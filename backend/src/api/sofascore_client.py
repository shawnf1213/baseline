"""
Sofascore tennis data client.

Core logic is identical to the pre-flag stable version (sofascore.py).
All requests route through Decodo rotating residential proxies via curl_cffi.
No Playwright. No file caching — uses st.session_state exactly as the original.

Public interface:
  init_session, run_connection_test, load_player_lists,
  search_players, get_player_stats_by_surface,
  get_h2h_summary, get_h2h_stat_avg,
  get_tournament_record_modifier,
  ts_to_date_str, format_h2h_table.
"""

import os
import re
import random
import string
import time
import logging
import threading
import unicodedata


def _strip_accents(s: str) -> str:
    """Fold diacritics so 'molcan' matches 'Molčan' (č), 'cilic' matches
    'Čilić', etc. Used by search scoring so accented names aren't down-ranked."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
BASE_URL        = "https://www.sofascore.com/api/v1"
# Search was confirmed working on api.sofascore.com before the www switch.
# Use it as a fallback when www returns 0 tennis entities.
SEARCH_BASE_URL = "https://api.sofascore.com/api/v1"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "x-requested-with": "dd30ae",
}

# Rotate through recent Chrome profiles and matching User-Agent strings.
# IMPORTANT: every profile must be supported by the INSTALLED curl_cffi build.
# curl_cffi 0.15.0 does NOT support "chrome125" — picking it raised
# ImpersonateError on every request for that session, returning empty results
# that then got cached and looked like a persistent player-specific outage.
# We filter the candidate list against what the installed build actually
# supports so a version mismatch can never silently break fetching again.
_CHROME_PROFILE_CANDIDATES = ["chrome124", "chrome123", "chrome120", "chrome131", "chrome119"]


def _supported_chrome_profiles() -> list:
    """Intersect our candidate profiles with what the installed curl_cffi
    build supports. Falls back to chrome124 (proven working) if introspection
    fails or yields nothing."""
    try:
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        import typing
        supported = set(typing.get_args(BrowserTypeLiteral))
        usable = [p for p in _CHROME_PROFILE_CANDIDATES if p in supported]
        if usable:
            return usable
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not introspect curl_cffi profiles: %s", exc)
    return ["chrome124"]


_CHROME_PROFILES = _supported_chrome_profiles()
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.141 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.155 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.76 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------
_PROXY_HOST  = os.getenv("PROXY_HOST",     "gate.decodo.com")
_PROXY_USER  = os.getenv("PROXY_USERNAME", "")
_PROXY_PASS  = os.getenv("PROXY_PASSWORD", "")
_PROXY_PORTS = [
    int(p.strip())
    for p in os.getenv("PROXY_PORT_LIST", "").split(",")
    if p.strip().isdigit()
]

# One port + one Session for the lifetime of a player search session.
# Only rotated when: new search starts, 407 received, or 403 persists.
current_proxy_port: Optional[int] = None
_current_session_id: str = ""
_used_ports: list = []
_bad_ports:  dict = {}          # port -> timestamp marked bad
_proxy_session   = None         # curl_cffi Session — reused across all requests

# Request throttle — enforces minimum gap between Sofascore calls
_last_sofascore_request: float = 0.0
_throttle_lock = threading.Lock()

# Inter-player session spacing (STEP 2): new player lookups are spaced 1.5-3.5s
# apart so one IP isn't seen firing many unrelated lookups back-to-back.
_last_player_session_ts: float = 0.0
_player_session_lock = threading.Lock()

# Cache bucket window. Decodo block risk is driven by live-request volume, so we
# cache aggressively: a player's events/surface stats are reused for 6h (props
# evaluate matches that don't change minute-to-minute).
_CACHE_BUCKET_SECS = 6 * 3600

# Proxy-usage / cache counters (STEP 7) — logged daily so the cache hit rate and
# live proxy volume are visible in Railway without reproducing issues live.
_counter_lock = threading.Lock()
_proxy_live_count: int = 0
_cache_served_count: int = 0
_counter_day: Optional[str] = None

# 403 block state — set when all retry attempts exhaust on 403
_search_blocked: bool = False
_search_blocked_ts: float = 0.0


class SofascoreBlockedError(Exception):
    """Raised when Sofascore returns 403 across all retry attempts during search."""


def _proxy_ok() -> bool:
    return bool(_PROXY_PORTS and _PROXY_USER and _PROXY_HOST)


def _fresh_session_id() -> str:
    """Generate a random 8-char alphanumeric string for Decodo sticky-session rotation."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _proxy_url(port: int) -> str:
    return f"http://{_PROXY_USER}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"


def _choose_port() -> Optional[int]:
    """Pick a port that is not recently used and not currently bad."""
    if not _proxy_ok():
        return None
    now = time.time()
    for p in [k for k, t in list(_bad_ports.items()) if now - t > 600]:
        del _bad_ports[p]
    avoid      = set(_used_ports[-2:]) | set(_bad_ports)
    candidates = ([p for p in _PROXY_PORTS if p not in avoid] or
                  [p for p in _PROXY_PORTS if p not in _bad_ports] or
                  list(_PROXY_PORTS))
    return random.choice(candidates)


def _new_session(force_port: bool = True) -> None:
    """
    Create a new curl_cffi Session bound to a proxy port.
    Uses a fresh Decodo session ID on every call to force a new residential IP.
    Called once per player search, and on 407 / persistent 403.
    """
    global current_proxy_port, _used_ports, _proxy_session, _current_session_id
    if force_port or current_proxy_port is None:
        port = _choose_port()
        current_proxy_port = port
        if port is not None:
            _used_ports = (_used_ports + [port])[-4:]
    _current_session_id = _fresh_session_id()
    profile = random.choice(_CHROME_PROFILES)
    ua      = random.choice(_UA_POOL)
    from curl_cffi import requests as cf
    s = cf.Session(impersonate=profile)
    s.headers.update({**HEADERS, "User-Agent": ua})
    if current_proxy_port and _proxy_ok():
        pu = _proxy_url(current_proxy_port)
        s.proxies = {"http": pu, "https": pu}
    _proxy_session = s
    logger.info("New session: port=%s sid=%s profile=%s", current_proxy_port, _current_session_id, profile)


def _do_warmup() -> None:
    """
    GET the Sofascore homepage so the session has valid cookies before API calls.
    Only called from search_players(), not from _new_session(), to avoid blocking
    stats/H2H parallel fetches that don't need a warmed-up session.
    Capped at 5s so a slow proxy response never stalls the search.
    """
    if _proxy_session is None:
        return
    try:
        _proxy_session.get("https://www.sofascore.com", timeout=3)
        logger.debug("Sofascore warm-up OK (port=%s sid=%s)", current_proxy_port, _current_session_id)
    except Exception as e:
        logger.debug("Sofascore warm-up failed (non-fatal): %s", e)


def _search_throttle() -> None:
    """
    Light rate-limit between search requests.  Keep short (≤0.3s) so that
    the warmup removal savings aren't eaten back by a long sleep.
    """
    global _last_sofascore_request
    with _throttle_lock:
        now = time.time()
        gap = now - _last_sofascore_request
        min_gap = 0.2 + random.uniform(0, 0.1)   # 0.2–0.3s — light, keeps it fast
        if gap < min_gap:
            time.sleep(min_gap - gap)
        _last_sofascore_request = time.time()


def _mark_bad(port: int) -> None:
    """Mark a port bad for 10 min, then immediately rotate to a new session."""
    logger.warning("Port %d marked bad — rotating", port)
    _bad_ports[port] = time.time()
    _new_session(force_port=True)


# ---------------------------------------------------------------------------
# Proxy-usage counters (STEP 7) + cache hit/miss visibility (STEP 3)
# ---------------------------------------------------------------------------
def _roll_counter_day_locked() -> None:
    global _counter_day, _proxy_live_count, _cache_served_count
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _counter_day != today:
        if _counter_day is not None:
            logger.info(
                "PROXY_DAILY_SUMMARY | day=%s | live_proxy_requests=%d | cache_served=%d",
                _counter_day, _proxy_live_count, _cache_served_count,
            )
        _counter_day = today
        _proxy_live_count = 0
        _cache_served_count = 0


def record_live_fetch(n: int = 1) -> None:
    """One live proxy request actually sent to Sofascore."""
    global _proxy_live_count
    with _counter_lock:
        _roll_counter_day_locked()
        _proxy_live_count += n


def record_cache_hit(n: int = 1) -> None:
    """A player lookup served entirely from cache (no proxy request)."""
    global _cache_served_count
    with _counter_lock:
        _roll_counter_day_locked()
        _cache_served_count += n


def proxy_usage_stats() -> dict:
    with _counter_lock:
        _roll_counter_day_locked()
        total = _proxy_live_count + _cache_served_count
        return {
            "day": _counter_day,
            "live_proxy_requests": _proxy_live_count,
            "cache_served": _cache_served_count,
            "cache_hit_rate_pct": round(_cache_served_count / total * 100, 1) if total else 0.0,
        }


def begin_player_session() -> None:
    """Start a fresh sticky session for a NEW player lookup.

    On this Decodo plan each PORT maps to a distinct sticky residential IP
    (verified via /api/proxy/session-test: same port -> same IP, different port
    -> different IP; the username "-session-{id}" format is NOT supported and
    407s). So rotating the port per player gives each player its own IP and keeps
    one IP from being seen firing many unrelated lookups. New player sessions are
    spaced 1.5-3.5s apart so the pattern reads as spaced-out human browsing.
    Only called on a cache MISS, so cached lookups incur no delay.
    """
    # Simplified: just rotate to a fresh port (new sticky IP) per player. No
    # artificial inter-player delay — that was slowing lookups for no clear gain.
    _new_session(force_port=True)
    logger.info("PLAYER_SESSION_START | port=%s", current_proxy_port)


# ---------------------------------------------------------------------------
# Core HTTP fetch
# ---------------------------------------------------------------------------
def probe_request(url: str, params: dict = None) -> dict:
    """
    Diagnostic-only: perform a single proxied GET and report the raw HTTP
    status, a body snippet, and the proxy port used — WITHOUT the silent
    {}-on-failure swallowing that _get does. Used to tell a 403 challenge /
    407 proxy-auth failure apart from a genuine 200-with-empty response.
    """
    global _proxy_session
    if _proxy_session is None:
        _new_session(force_port=True)
    try:
        r = _proxy_session.get(url, params=params, timeout=8)
        body = r.text or ""
        ctype = r.headers.get("content-type", "")
        n_results = None
        if "json" in ctype.lower():
            try:
                n_results = len(r.json().get("results", []))
            except Exception:
                n_results = None
        return {
            "ok": r.status_code == 200,
            "status": r.status_code,
            "proxy_port": current_proxy_port,
            "content_type": ctype,
            "n_results": n_results,
            "body_snippet": body[:400],
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "proxy_port": current_proxy_port,
            "error": f"{type(e).__name__}: {e}",
        }


def run_session_format_test() -> dict:
    """Isolated, read-only proof that the Decodo sticky-session username format
    works BEFORE switching the main fetch path over. Tests, on one port:
      - same session id twice  -> should return the SAME residential IP (sticky)
      - a different session id  -> should return a DIFFERENT IP (rotation works)
      - the plain username      -> control
      - a real Sofascore call via a session username -> confirms not 403
    Returns raw status/IP/body so the format can be confirmed from the logs.
    """
    from curl_cffi import requests as cf
    if not _proxy_ok():
        return {"error": "proxy not configured"}
    port = _PROXY_PORTS[0]

    def _fetch_ip(session_username: str) -> dict:
        pu = f"http://{session_username}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"
        try:
            s = cf.Session(impersonate="chrome124")
            s.proxies = {"http": pu, "https": pu}
            r = s.get("https://ip.decodo.com/json", timeout=10)
            ip = ""
            try:
                j = r.json()
                ip = (j.get("proxy") or {}).get("ip") or j.get("ip") or ""
            except Exception:
                pass
            return {"status": r.status_code, "ip": ip}
        except Exception as e:
            return {"status": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    sid1, sid2 = _fresh_session_id(), _fresh_session_id()
    u1 = f"{_PROXY_USER}-session-{sid1}"
    u2 = f"{_PROXY_USER}-session-{sid2}"
    call1 = _fetch_ip(u1)
    call2 = _fetch_ip(u1)   # same session -> expect same IP
    call3 = _fetch_ip(u2)   # different session -> expect different IP
    control = _fetch_ip(_PROXY_USER)

    # Port behaviour: 3 back-to-back plain-username calls on the SAME port —
    # identical IPs => port is sticky; differing IPs => rotates per request.
    same_port_ips = [_fetch_ip(_PROXY_USER).get("ip") for _ in range(3)]
    # One call on each of up to 3 different ports.
    diff_port = {}
    for p in _PROXY_PORTS[:3]:
        pu = f"http://{_PROXY_USER}:{_PROXY_PASS}@{_PROXY_HOST}:{p}"
        try:
            s = cf.Session(impersonate="chrome124")
            s.proxies = {"http": pu, "https": pu}
            r = s.get("https://ip.decodo.com/json", timeout=10)
            ip = ""
            try:
                j = r.json(); ip = (j.get("proxy") or {}).get("ip") or j.get("ip") or ""
            except Exception:
                pass
            diff_port[str(p)] = ip
        except Exception as e:
            diff_port[str(p)] = f"ERR {type(e).__name__}"

    # Real Sofascore call through a session username (the 403/200 proof).
    sofa: dict
    try:
        pu = f"http://{u1}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"
        s = cf.Session(impersonate="chrome124")
        s.headers.update(HEADERS)
        s.proxies = {"http": pu, "https": pu}
        r = s.get(f"{BASE_URL}/search/all", params={"q": "sinner"}, timeout=12)
        n = None
        try:
            n = len(r.json().get("results", []))
        except Exception:
            pass
        sofa = {"status": r.status_code, "n_results": n, "body_snippet": (r.text or "")[:160]}
    except Exception as e:
        sofa = {"error": f"{type(e).__name__}: {str(e)[:150]}"}

    return {
        "session_username_format": f"{_PROXY_USER}-session-<id>",
        "port_tested": port,
        "session1_call1": call1,
        "session1_call2_same_id": call2,
        "session2_diff_id": call3,
        "plain_username_control": control,
        "username_session_supported": bool(call1.get("status") == 200),
        "sticky_ok": bool(call1.get("ip") and call1.get("ip") == call2.get("ip")),
        "rotation_ok": bool(call1.get("ip") and call3.get("ip")
                            and call1.get("ip") != call3.get("ip")),
        "same_port_repeated_ips": same_port_ips,
        "same_port_is_sticky": len(set(i for i in same_port_ips if i)) == 1 and bool(same_port_ips[0]),
        "different_port_ips": diff_port,
        "sofascore_via_session": sofa,
    }


def _get(url: str, params: dict = None, fast: bool = False) -> dict:
    """fast=True is for latency-critical search/autocomplete calls (Discord has a
    hard 3s autocomplete limit): on a 403 it fails immediately instead of doing
    the 5-10s anti-block backoff, which would blow the autocomplete deadline."""
    global _proxy_session, _search_blocked, _search_blocked_ts

    if _proxy_session is None:
        _new_session(force_port=True)

    # Simple, fast retry: on 403 or a dead-port error, rotate to a fresh port and
    # retry quickly (no long sleeps). Search (fast=True) just fails on 403.
    for attempt in range(3):
        try:
            record_live_fetch()
            r = _proxy_session.get(url, params=params, timeout=8)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 407:
                _mark_bad(current_proxy_port)
                continue
            if r.status_code == 403:
                logger.warning("403 %s port=%s fast=%s", url, current_proxy_port, fast)
                if fast:
                    return {}
                _new_session(force_port=True)   # rotate port and retry
                if attempt >= 2:
                    _search_blocked = True
                    _search_blocked_ts = time.time()
                    logger.error("SOFASCORE BLOCKED: 403 on all attempts for %s", url)
                    return {}
                continue
            logger.debug("HTTP %d %s", r.status_code, url)
            return {}
        except Exception as e:
            msg = str(e).lower()
            if any(x in msg for x in ("proxy", "tunnel", "connect", "407")):
                # Dead proxy port (e.g. Decodo 502 CONNECT tunnel failure).
                # Mark it bad AND rotate to a fresh port — without this the
                # next attempt reuses the same dead port and all 3 retries
                # fail even when other ports are healthy.
                _mark_bad(current_proxy_port)
                logger.warning("PROXY_ERR %s attempt=%d port=%s — rotating: %s",
                               url, attempt + 1, current_proxy_port, str(e)[:120])
                _new_session(force_port=True)
            else:
                logger.debug("Error %s: %s", url, e)
            if attempt == 2:
                return {}
    return {}


# ---------------------------------------------------------------------------
# Session init / connection test
# ---------------------------------------------------------------------------
def _check_proxy_health() -> None:
    """
    Test all proxy ports and log health. Runs in a background thread at startup
    so it never delays the server becoming ready.
    """
    if not _proxy_ok():
        return
    logger.info("Proxy health check starting (%d ports)...", len(_PROXY_PORTS))
    healthy = 0
    unhealthy = 0
    try:
        from curl_cffi import requests as cf
        for port in _PROXY_PORTS:
            pu  = _proxy_url(port)
            try:
                s = cf.Session(impersonate="chrome124")
                s.proxies = {"http": pu, "https": pu}
                r = s.get("https://ip.decodo.com/json", timeout=8)
                if r.status_code == 200:
                    info    = r.json()
                    ext_ip  = (info.get("proxy") or {}).get("ip") or info.get("ip") or "?"
                    country = (info.get("country") or {}).get("name") or ""
                    logger.info("  Port %d OK -> %s (%s)", port, ext_ip, country)
                    healthy += 1
                elif r.status_code == 407:
                    logger.error("  Port %d: 407 proxy auth failed", port)
                    unhealthy += 1
                else:
                    logger.warning("  Port %d: HTTP %d", port, r.status_code)
                    unhealthy += 1
            except Exception as e:
                logger.warning("  Port %d: error — %s", port, e)
                unhealthy += 1
    except Exception as e:
        logger.warning("Proxy health check error: %s", e)

    if unhealthy > 4:
        logger.warning(
            "PROXY WARNING: %d/%d ports unhealthy — rotation may be impaired",
            unhealthy, len(_PROXY_PORTS),
        )
    else:
        logger.info("Proxy health: %d/%d ports OK", healthy, len(_PROXY_PORTS))


def _proxy_health_once() -> None:
    """One scheduled pass (STEP 4): test up to 3 random ports against a
    lightweight Sofascore endpoint and log OK/403. Warn loudly if >=3 sampled
    ports return 403 so a brewing block is visible early. Also emits the daily
    proxy-usage summary so cache effectiveness is visible in Railway logs."""
    if not _proxy_ok():
        return
    from curl_cffi import requests as cf
    sample = random.sample(_PROXY_PORTS, min(3, len(_PROXY_PORTS)))
    ok = blocked = 0
    for p in sample:
        pu = _proxy_url(p)
        try:
            s = cf.Session(impersonate="chrome124")
            s.headers.update(HEADERS)
            s.proxies = {"http": pu, "https": pu}
            r = s.get(f"{BASE_URL}/search/all", params={"q": "sinner"}, timeout=10)
            if r.status_code == 200:
                ok += 1
                logger.info("PROXY_HEALTH | port=%d OK", p)
            elif r.status_code == 403:
                blocked += 1
                logger.warning("PROXY_HEALTH | port=%d 403", p)
            else:
                logger.warning("PROXY_HEALTH | port=%d HTTP %d", p, r.status_code)
        except Exception as e:
            logger.warning("PROXY_HEALTH | port=%d err %s", p, str(e)[:80])
        time.sleep(random.uniform(1.5, 3.5))   # space the checks too
    if blocked >= 3:
        logger.warning(
            "PROXY_HEALTH_ALERT | %d/%d sampled ports returning 403 — possible "
            "brewing Sofascore block, investigate before users hit 'no players found'",
            blocked, len(sample),
        )
    logger.info("PROXY_HEALTH_SUMMARY | ok=%d blocked=%d | usage=%s",
                ok, blocked, proxy_usage_stats())


def _proxy_health_scheduler() -> None:
    """Run a health pass every 30 minutes, independent of user traffic."""
    while True:
        time.sleep(1800)
        try:
            _proxy_health_once()
        except Exception as e:  # noqa: BLE001 — a monitor must never crash
            logger.warning("proxy health scheduler error: %s", e)


_health_scheduler_started = False


def init_session() -> None:
    """
    Pick initial proxy port and create the shared Session.
    Proxy health check runs in a background thread so it doesn't delay startup.
    """
    global _health_scheduler_started
    _new_session(force_port=True)
    if not _proxy_ok():
        logger.warning("No proxy configured — using direct connection")
    else:
        # Non-blocking startup health check — logs arrive ~30s after startup
        threading.Thread(target=_check_proxy_health, daemon=True).start()
        # Recurring 30-min health monitor (STEP 4) — start once
        if not _health_scheduler_started:
            _health_scheduler_started = True
            threading.Thread(target=_proxy_health_scheduler, daemon=True).start()
            logger.info("Proxy health scheduler started (every 30 min)")
    logger.info("Sofascore client ready (port=%s)", current_proxy_port)


def run_connection_test() -> None:
    logger.info("=== connection test ===")
    resp = _get(f"{BASE_URL}/search/all", {"q": "Sinner"})
    results = resp.get("results", [])
    tennis  = [r for r in results
               if r.get("entity", {}).get("sport", {}).get("name", "").lower() == "tennis"]
    logger.info("tennis players found: %d", len(tennis))
    for t in tennis[:2]:
        e = t.get("entity", {})
        logger.info("  id=%-8s  %s", e.get("id"), e.get("name"))


def load_player_lists() -> None:
    logger.info("Sofascore: no startup player list needed")


# ---------------------------------------------------------------------------
# Surface inference (keyword-based — stable, works from tournament name)
# ---------------------------------------------------------------------------
HARD_KEYWORDS = [
    "us open", "australian open", "indian wells", "miami", "cincinnati",
    "canada", "montreal", "toronto", "vienna", "basel", "rotterdam",
    "doha", "dubai", "atp finals", "nitto", "paris masters",
    "washington", "beijing", "shanghai", "astana", "metz", "antwerp",
    "stockholm", "moscow", "sofia", "memphis", "delray", "hard", "indoor",
]
CLAY_KEYWORDS = [
    "roland garros", "french open", "monte carlo", "barcelona", "rome",
    "hamburg", "geneva", "lyon", "budapest", "estoril", "munich",
    "madrid", "rio", "buenos aires", "sao paulo", "marrakech",
    "bastad", "gstaad", "umag", "kitzbuhel", "bucharest",
    "clay", "terre battue",
    # Challenger & ITF clay venues
    "mexico city", "bogota", "lima", "santiago", "cordoba", "cherbourg",
    "oeiras", "prostejov", "poznan", "braunschweig", "salzburg",
    "tampere", "istanbul", "casablanca", "tunis", "cairo",
    "perugia", "parma", "banja luka", "santa fe", "morelos",
    # Additional Challenger clay venues
    "valencia", "biella", "maia", "braga", "bagnoles", "andrezieux",
    "leon", "guadalajara", "concepcion", "ortisei", "bergamo",
    "lugano", "olbia", "savona", "porto", "lagos", "tlemcen",
    "hammamet", "sfax", "monastir", "rabat", "fes", "agadir",
    "marrakech", "algier", "cairo", "sharm", "luxor",
    "szczecin", "bydgoszcz", "wroclaw", "krakow", "lodz",
    "norrkoping", "bastad", "manerbio", "como", "piacenza",
]
GRASS_KEYWORDS = [
    "wimbledon", "queen", "queens", "halle", "stuttgart",
    "eastbourne", "nottingham", "newport", "'s-hertogenbosch", "rosmalen",
    "grass",
]


def _infer_surface(tournament_name: str) -> str:
    name = tournament_name.lower()
    for kw in GRASS_KEYWORDS:
        if kw in name:
            return "Grass"
    for kw in CLAY_KEYWORDS:
        if kw in name:
            return "Clay"
    for kw in HARD_KEYWORDS:
        if kw in name:
            return "Hard"
    return "Hard"


# Sofascore groundType numeric codes → surface name
_GROUND_TYPE_MAP: dict = {
    1: "Hard",   # outdoor hard
    2: "Clay",
    3: "Grass",
    4: "Hard",   # carpet (treat as hard)
    5: "Hard",   # indoor hard
    "hard": "Hard",
    "clay": "Clay",
    "grass": "Grass",
    "carpet": "Hard",
    "indoor": "Hard",
}


def _infer_surface_from_event(event: dict, log_missing: bool = False) -> str:
    """
    Try Sofascore native groundType fields first (numeric or string),
    then fall back to keyword matching on the tournament name.

    Checks multiple field paths including Challenger-specific locations:
      event.groundType
      event.tournament.groundType
      event.tournament.uniqueTournament.groundType
      event.uniqueTournament.groundType           ← top-level (Challenger events)
      event.tournament.category.groundType        ← category level
      event.uniqueTournament.groundTypeEnum       ← enum string (some Challenger events)
      event.tournament.uniqueTournament.groundTypeEnum
    """
    tournament = event.get("tournament") or {}
    unique_t   = tournament.get("uniqueTournament") or {}
    top_unique = event.get("uniqueTournament") or {}
    category   = tournament.get("category") or {}

    candidates = (
        event.get("groundType"),
        tournament.get("groundType"),
        unique_t.get("groundType"),
        top_unique.get("groundType"),
        category.get("groundType"),
        # groundTypeEnum — additional field path used in some Challenger events
        unique_t.get("groundTypeEnum"),
        top_unique.get("groundTypeEnum"),
    )

    for gt_raw in candidates:
        if gt_raw is None:
            continue
        if isinstance(gt_raw, int):
            mapped = _GROUND_TYPE_MAP.get(gt_raw)
            if mapped:
                return mapped
        elif isinstance(gt_raw, str):
            # Try integer parse first — Sofascore sometimes sends "1"/"2"/"3" as strings
            try:
                mapped = _GROUND_TYPE_MAP.get(int(gt_raw))
                if mapped:
                    return mapped
            except (ValueError, TypeError):
                pass
            mapped = _GROUND_TYPE_MAP.get(gt_raw.lower())
            if mapped:
                return mapped
        elif isinstance(gt_raw, dict):
            # Some API versions nest as {"name": "clay"}
            name_val = (gt_raw.get("name") or "").lower()
            mapped = _GROUND_TYPE_MAP.get(name_val)
            if mapped:
                return mapped

    # Fell through — keyword matching on all available name fields
    tourn_name = " ".join(filter(None, [
        tournament.get("name", ""),
        unique_t.get("name", ""),
        top_unique.get("name", ""),
    ]))
    surface = _infer_surface(tourn_name)
    if log_missing:
        logger.debug(
            "SURFACE_FALLBACK | event_id=%s | tourn=%r | "
            "gt_candidates=%s | inferred=%s",
            event.get("id"), tourn_name,
            [c for c in candidates if c is not None],
            surface,
        )
    return surface


# ---------------------------------------------------------------------------
# Stat parsing helpers
# ---------------------------------------------------------------------------
def _parse_fraction_pct(value_str: str) -> Optional[float]:
    """Parse percentage from '56/94 (60%)' or '75%' or plain number."""
    if value_str is None:
        return None
    s = str(value_str).strip()
    m = re.search(r'\((\d+(?:\.\d+)?)%\)', s)
    if m:
        return float(m.group(1))
    m = re.search(r'^(\d+(?:\.\d+)?)%$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^(\d+)/(\d+)$', s)
    if m:
        n, d = float(m.group(1)), float(m.group(2))
        return (n / d * 100) if d > 0 else None
    try:
        return float(s)
    except ValueError:
        return None


# Sofascore stat label -> (internal_key, is_fraction_pct)
STAT_MAP = {
    "aces":                       ("aces",                        False),
    "double faults":               ("double_faults",               False),
    "first serve":                 ("first_serve_pct",             True),
    "first serve points":          ("first_serve_pts_won",         True),
    "second serve points":         ("second_serve_pts_won",        True),
    "first serve return points":   ("return_first_serve_pts_won",  True),
    "second serve return points":  ("return_second_serve_pts_won", True),
    "break points converted":      ("bp_converted_count",          False),
    "break points saved":          ("bp_saved",                    True),
    "service games played":        ("service_games",               False),
    "return games played":         ("return_games",                False),
    "service points won":          ("service_pts_won",             False),
    "receiver points won":         ("return_pts_won_count",        False),
    "total won":                   ("total_games_won",             False),
    "tiebreaks":                   ("tiebreaks",                   False),
}

THREE_YEARS_SECS = 3 * 365 * 24 * 3600

# Years considered "recent 3-year window" — updated each calendar year
_RECENT_YEARS = {2023, 2024, 2025, 2026}

def _year_from_ts(ts: int) -> int:
    """Extract 4-digit year from Unix timestamp, or 0 if unknown."""
    if not ts:
        return 0
    try:
        return datetime.utcfromtimestamp(ts).year
    except Exception:
        return 0


# Stat keys used for per-match averages in _agg_split.
# NOTE: bp_converted is NOT averaged here — _agg_split overrides it with a
# proper sum/sum calculation from return_bp_converted / return_bp_opportunities.
_SPLIT_NUMERIC_KEYS = [
    "aces", "double_faults", "first_serve_pct",
    "first_serve_pts_won", "second_serve_pts_won",
    "return_first_serve_pts_won", "return_second_serve_pts_won",
    "bp_saved", "total_match_games",
    "bp_converted_count", "bp_faced_count",
    # return-side raw counts (stored per match; aggregated via sum/sum in _agg_split)
    "return_bp_opportunities", "return_bp_converted",
]


def _agg_split(all_m: list, stat_m: list) -> dict:
    """
    Split aggregation: win_rate uses all_m (all finished matches including
    stat-poor ones), stat averages use stat_m (only stat-rich matches).

    This ensures win rate reflects real match outcomes even for Challenger
    events where Sofascore's stats API returns empty data.

    IMPORTANT — bp_converted is calculated via sum/sum (total BPs the player
    converted as returner ÷ total BP opportunities the player created as
    returner), NOT as an average of per-match rates.  This prevents bias from
    matches with very few opportunities.
    """
    if not all_m and not stat_m:
        return {"matches_played": 0, "stat_matches": 0}
    wins = sum(1 for m in all_m if m.get("won", False))
    result: dict = {
        "matches_played": len(all_m),
        "stat_matches":   len(stat_m),
        "wins":           wins,
        "win_rate":       round(wins / len(all_m) * 100, 2) if all_m else 0,
    }
    for key in _SPLIT_NUMERIC_KEYS:
        vals = [m[key] for m in stat_m if key in m and m[key] is not None]
        result[key] = round(sum(vals) / len(vals), 4) if vals else None

    # ── bp_converted: sum/sum from raw return-side counts ────────────────────
    # Pair matches that have BOTH return_bp_converted and return_bp_opportunities.
    # This gives the true career conversion rate on the surface, not an average
    # of per-match rates (which is biased by match-to-match opportunity variance).
    _bp_pairs = [
        (m["return_bp_converted"], m["return_bp_opportunities"])
        for m in stat_m
        if m.get("return_bp_converted") is not None
        and m.get("return_bp_opportunities") is not None
        and m["return_bp_opportunities"] > 0
    ]
    if _bp_pairs:
        _total_conv = sum(c for c, _ in _bp_pairs)
        _total_opps = sum(o for _, o in _bp_pairs)
        if _total_opps > 0:
            result["bp_converted"]          = round(_total_conv / _total_opps * 100, 4)
            result["return_bp_converted"]   = round(_total_conv / len(_bp_pairs), 4)
            result["return_bp_opportunities"] = round(_total_opps / len(_bp_pairs), 4)
            result["bp_converted_count"]    = round(_total_conv / len(_bp_pairs), 4)

    # Fallback: if the raw return-BP counts weren't parsed for these matches
    # (so sum/sum couldn't run), average the per-match bp_converted rates instead
    # of leaving it blank. Less precise than sum/sum, but avoids an empty
    # "Conv (Overall)" when per-match rates are present.
    if result.get("bp_converted") is None:
        _conv_vals = [m["bp_converted"] for m in stat_m if m.get("bp_converted") is not None]
        if _conv_vals:
            result["bp_converted"] = round(sum(_conv_vals) / len(_conv_vals), 4)

    # Strength-of-schedule: average competition tier across ALL matches (win_rate
    # uses all_m, so schedule strength should too). Feeds the win-prob estimator.
    _tiers = [m["comp_tier"] for m in all_m if m.get("comp_tier") is not None]
    result["competition_level"] = round(sum(_tiers) / len(_tiers), 3) if _tiers else None

    return result


def _build_score_str(event: dict) -> str:
    home_sc = event.get("homeScore", {})
    away_sc = event.get("awayScore", {})
    sets = []
    for key in ["period1", "period2", "period3", "period4", "period5"]:
        h = home_sc.get(key)
        a = away_sc.get(key)
        if h is not None and a is not None:
            sets.append(f"{h}-{a}")
        else:
            break
    return " ".join(sets)


def _competition_tier(event: dict) -> float:
    """Strength-of-field proxy from the Sofascore category/tournament name.
    Tour (ATP/WTA) = 3, Challenger = 2, ITF / qualifying / exhibition = 1.
    Higher = tougher opponents. Used so a player's stats earned against weak
    fields (Challengers) aren't treated as equal to stats vs the main tour."""
    tournament = event.get("tournament") or {}
    category   = tournament.get("category") or {}
    blob = " ".join([
        (category.get("slug") or ""),
        (category.get("name") or ""),
        (tournament.get("name") or ""),
    ]).lower()
    if any(x in blob for x in ("itf", "exhibition", "liga pro", "utr", "futures")):
        return 1.0
    # Qualifying (even at an ATP/WTA event) is played against Challenger-level
    # opposition, so it must NOT count as full main-tour strength.
    if "qualif" in blob:
        return 2.0
    if "challenger" in blob:
        return 2.0
    if "atp" in blob or "wta" in blob or "grand slam" in blob:
        return 3.0
    return 2.5   # unknown — neutral, between challenger and tour


def _calc_total_match_games(event: dict) -> Optional[int]:
    """
    Sum games played by both players across all sets.
    e.g. 6-3 6-4 → (6+3) + (6+4) = 19
         7-6 6-4 → (7+6) + (6+4) = 23   (tiebreak = 1 game, so 7+6=13 ✓)
    Returns None if no period scores are present.
    """
    home_sc = event.get("homeScore", {})
    away_sc = event.get("awayScore", {})
    total = 0
    found_any = False
    for key in ["period1", "period2", "period3", "period4", "period5"]:
        h = home_sc.get(key)
        a = away_sc.get(key)
        if h is not None and a is not None:
            total += int(h) + int(a)
            found_any = True
        else:
            break
    return total if found_any else None


def _parse_match_stats(stats_data: dict, event: dict, player_id: int) -> Optional[dict]:
    statistics = stats_data.get("statistics", [])
    if not statistics:
        return None

    all_period = next((p for p in statistics if p.get("period") == "ALL"), None)
    if not all_period:
        all_period = statistics[0]

    home_id = event.get("homeTeam", {}).get("id")
    side     = "home" if home_id == player_id else "away"
    opp_side = "away" if side == "home" else "home"

    home_score = event.get("homeScore", {}).get("current", 0) or 0
    away_score = event.get("awayScore", {}).get("current", 0) or 0
    won = (home_score > away_score) if side == "home" else (away_score > home_score)

    opp_team = event.get("awayTeam", {}) if side == "home" else event.get("homeTeam", {})

    # NOTE: surface intentionally omitted — caller (get_player_stats_by_surface)
    # sets surface via _infer_surface_from_event (groundType-aware) and we must
    # not overwrite it here with the weaker keyword-only inference.
    result = {
        "won":           won,
        "tournament":    event.get("tournament", {}).get("name", "Unknown"),
        "timestamp":     event.get("startTimestamp", 0),
        "event_id":      event.get("id"),
        "opponent_name": opp_team.get("name", "Unknown"),
    }

    opp_bp_faced = None

    # ── Collect stat items — handle both nested (groups) and flat structures ──
    # ATP/WTA: statistics[0].groups[n].statisticsItems
    # Some Challenger events: statistics[0].statisticsItems  (no groups level)
    all_items: list = []
    for group in all_period.get("groups", []):
        all_items.extend(group.get("statisticsItems", []))
    if not all_items:
        # Flat structure fallback (Challenger / some tournament levels)
        all_items = all_period.get("statisticsItems", [])
        if all_items:
            logger.debug(
                "FLAT_STATS | event_id=%s | tourn=%r | items=%d",
                event.get("id"),
                event.get("tournament", {}).get("name", ""),
                len(all_items),
            )

    for item in all_items:
        name_lower = item.get("name", "").lower().strip()
        if name_lower not in STAT_MAP:
            continue
        internal_key, is_pct = STAT_MAP[name_lower]

        if is_pct:
            raw_str = item.get(side, "")
            val = _parse_fraction_pct(raw_str)
            if internal_key == "bp_saved":
                # Also store how many BPs the player faced on their serve (denominator)
                frac_m = re.match(r"(\d+)/(\d+)", str(raw_str))
                if frac_m:
                    result["bp_faced_count"] = float(frac_m.group(2))
        elif internal_key == "bp_converted_count":
            # Sofascore "Break Points Converted" for the player's side:
            #   "3/5 (60%)"  →  group(1)=3  converted as RETURNER  (attacking stat)
            #                    group(2)=5  opportunities created as RETURNER
            #
            # These are RETURN stats.  Never mix with bp_faced_count which is
            # a SERVE stat (BPs the player faces on their own serve).
            raw_str = item.get(side, "")
            frac_m = re.match(r"(\d+)/(\d+)", str(raw_str))
            if frac_m:
                val = float(frac_m.group(1))
                # Store raw return counts separately so _agg_split can sum them.
                result["return_bp_converted"]     = float(frac_m.group(1))
                result["return_bp_opportunities"] = float(frac_m.group(2))
            else:
                try:
                    val = float(str(raw_str).strip()) if raw_str else None
                except (ValueError, TypeError):
                    val = None

            # Secondary serve-side bp_faced_count extraction:
            # "Break Points Converted" OPPONENT denominator = BPs opponent had on
            # return = BPs the PLAYER faced on their OWN serve.  This is a serve
            # stat and must never be used as the conversion-rate denominator.
            # It covers matches where "Break Points Saved" is a plain "%" string.
            if "bp_faced_count" not in result:
                opp_conv_raw = item.get(opp_side, "")
                opp_frac = re.match(r"(\d+)/(\d+)", str(opp_conv_raw))
                if opp_frac:
                    result["bp_faced_count"] = float(opp_frac.group(2))
        else:
            val = item.get(f"{side}Value")
            if val is not None:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    val = None

        if val is not None:
            result[internal_key] = val

        # Capture opponent aces so we can compute ace-against-per-match from SS data
        if name_lower == "aces":
            opp_ace_val = item.get(f"{opp_side}Value")
            if opp_ace_val is not None:
                try:
                    result["opp_aces"] = float(opp_ace_val)
                except (ValueError, TypeError):
                    pass

        if name_lower == "break points saved":
            opp_raw = item.get(opp_side, "")
            m = re.match(r"(\d+)/(\d+)", str(opp_raw))
            if m:
                opp_bp_faced = float(m.group(2))

    # If no stat items were parsed at all, return None — caller will skip this match
    stat_keys = {"aces", "double_faults", "bp_converted_count", "first_serve_pts_won"}
    if not any(k in result for k in stat_keys):
        logger.warning(
            "STATS_NO_KEYS | event_id=%s | tourn=%r | all_items=%d | "
            "returning None (match excluded from surface log)",
            event.get("id"),
            event.get("tournament", {}).get("name", ""),
            len(all_items),
        )
        return None

    # ── Per-match BP conversion rate (return stat) ───────────────────────────
    # Denominator priority:
    #   1. return_bp_opportunities  — extracted directly from player's "Break
    #      Points Converted" fraction denominator (most accurate).
    #   2. opp_bp_faced             — from opponent's "Break Points Saved"
    #      denominator; numerically identical to (1) in a consistent match.
    #
    # NEVER divide by bp_faced_count (player's serve stat) or return_games
    # (number of return service games).  Those are completely different stats
    # and using them as the denominator would produce a meaningless rate.
    bp_conv_count = result.get("bp_converted_count")
    ret_opps      = result.get("return_bp_opportunities")
    denom         = ret_opps if (ret_opps is not None and ret_opps > 0) \
                    else (opp_bp_faced if (opp_bp_faced and opp_bp_faced > 0) else None)

    if bp_conv_count is not None and denom is not None:
        result["bp_converted"] = bp_conv_count / denom * 100
        # Ensure return_bp_opportunities is stored even when extracted via opp_bp_faced
        if ret_opps is None:
            result["return_bp_opportunities"] = denom
        logger.debug(
            "BP_CONV_RATE | event_id=%s | converted=%.0f | opps=%.0f | rate=%.1f%%",
            result.get("event_id"), bp_conv_count, denom,
            result["bp_converted"],
        )
    else:
        logger.debug(
            "BP_CONV_RATE_SKIP | event_id=%s | bp_conv_count=%s | ret_opps=%s | opp_bp_faced=%s",
            result.get("event_id"), bp_conv_count, ret_opps, opp_bp_faced,
        )

    return result


# ---------------------------------------------------------------------------
# Event fetching
# ---------------------------------------------------------------------------
def _fetch_event_page(player_id: int, page: int) -> list:
    """Fetch one page of a player's event history. Returns list (may be empty)."""
    data = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
    return data.get("events", [])


MAX_PAGES_DEFAULT = 50    # fetch up to 50 pages (~500 events) — covers full career history


def _get_player_recent_events(player_id: int, max_pages: int = MAX_PAGES_DEFAULT) -> list:
    """
    Fetch ALL available pages of a player's event history in parallel batches of 5.

    Strategy:
    - Fetch pages in batches of 5 concurrently.
    - Stop only when a batch returns no events (end of history) or max_pages reached.
    - No early-stop on surface match count — we want the full career history so
      all-time surface aggregations are accurate.
    - Cache key is bucketed to a 2-hour window so stale data never persists longer
      than 2 hours without a fresh Sofascore fetch.
    - PAGE_SCAN lines logged per batch so Railway shows pagination progress.
    """
    # 6-hour cache bucket (STEP 3): match history doesn't change minute-to-minute,
    # so reusing it for 6h sharply cuts live proxy volume.
    _bucket = int(time.time()) // _CACHE_BUCKET_SECS
    cache_key = f"ss_events_v2_{player_id}_{_bucket}"
    if cache_key in st.session_state:
        logger.info("EVENTS_CACHE_HIT | player_id=%s | bucket=%s", player_id, _bucket)
        record_cache_hit()
        return st.session_state[cache_key]

    # Cache miss → fresh sticky session for THIS player (new port = new IP),
    # spaced 1.5-3.5s from the previous player's lookup (STEP 1+2).
    logger.info("EVENTS_CACHE_MISS | player_id=%s | bucket=%s | starting player session", player_id, _bucket)
    begin_player_session()

    all_events: list = []
    now = time.time()
    page = 0
    batch_num = 0
    first_batch_retries = 0   # rotate-and-retry budget when page 0 comes back empty

    while page < max_pages:
        batch = list(range(page, min(page + 5, max_pages)))
        if not batch:
            break

        # Fetch batch in parallel
        page_results: dict = {}
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            fut_map = {ex.submit(_fetch_event_page, player_id, p): p for p in batch}
            for fut in as_completed(fut_map):
                page_results[fut_map[fut]] = fut.result()

        got_any = False
        for p in sorted(batch):
            evts = page_results.get(p, [])
            surface_this_page: dict = {}
            for e in evts:
                ts = e.get("startTimestamp", 0) or 0
                if ts and ts > now:
                    continue
                if e.get("status", {}).get("type", "") not in ("finished", "ended"):
                    continue
                ht = e.get("homeTeam", {}).get("name", "")
                at = e.get("awayTeam", {}).get("name", "")
                if "/" in ht or "/" in at:
                    continue
                surf = _infer_surface_from_event(e)
                surface_this_page[surf] = surface_this_page.get(surf, 0) + 1

            logger.info(
                "PAGE_SCAN | player_id=%s | page=%d | total_events=%d | surface_counts=%s",
                player_id, p, len(evts), surface_this_page,
            )
            all_events.extend(evts)
            if evts:
                got_any = True

        page += len(batch)
        batch_num += 1

        if not got_any:
            # An empty FIRST batch (nothing accumulated yet) for a real player
            # almost always means the proxy port failed mid-fetch, not that the
            # player has zero matches. Rotate to a fresh port and retry page 0
            # a few times before concluding it's genuinely the end of history.
            if not all_events and first_batch_retries < 3:
                first_batch_retries += 1
                logger.warning(
                    "PAGE_SCAN | player_id=%s | first batch empty — likely proxy "
                    "failure, rotating port and retrying (attempt %d/3)",
                    player_id, first_batch_retries,
                )
                _new_session(force_port=True)
                page = 0
                batch_num = 0
                continue
            logger.info(
                "PAGE_SCAN | player_id=%s | empty batch at batch=%d page=%d — end of history",
                player_id, batch_num, page,
            )
            break   # end of history

        logger.info(
            "PAGE_SCAN | player_id=%s | cumulative_events=%d | batches_fetched=%d",
            player_id, len(all_events), batch_num,
        )

    # Diagnostic: log the most recent match date found and total events fetched.
    finished_events = [
        e for e in all_events
        if e.get("status", {}).get("type", "") in ("finished", "ended")
        and e.get("startTimestamp", 0)
    ]
    if finished_events:
        most_recent_ts = max(e.get("startTimestamp", 0) for e in finished_events)
        try:
            most_recent_date = datetime.utcfromtimestamp(most_recent_ts).strftime("%Y-%m-%d")
        except Exception:
            most_recent_date = "unknown"
        logger.info(
            "EVENTS_FETCHED | player_id=%s | total_events=%d | finished=%d | most_recent_date=%s | bucket=%s",
            player_id, len(all_events), len(finished_events), most_recent_date, _bucket,
        )
    else:
        logger.warning("EVENTS_FETCHED | player_id=%s | no finished events found | total=%d", player_id, len(all_events))

    # Never cache an empty fetch — caching empty would lock in a transient
    # proxy failure for the whole 2-hour bucket, making it look player-specific
    # and persistent (e.g. one bad Gauff fetch poisoning every later request).
    # Leaving it uncached lets the next request re-fetch with a fresh port.
    if all_events:
        st.session_state[cache_key] = all_events
    else:
        logger.warning(
            "EVENTS_EMPTY | player_id=%s | NOT caching empty result so the next "
            "request retries with a fresh proxy port", player_id,
        )
    return all_events


def _get_event_statistics(event_id: int) -> dict:
    cache_key = f"ss_stats_{event_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    data = _get(f"{BASE_URL}/event/{event_id}/statistics")
    # Don't cache an empty/failed stat fetch — let it retry next time.
    if data:
        st.session_state[cache_key] = data
    return data


def _fetch_stats_parallel(event_ids: list) -> dict:
    """
    Fetch event statistics for multiple events concurrently (up to 10 at once).
    Returns {event_id: stats_dict}. Checks st.session_state cache before any request.
    Each thread uses its own curl_cffi Session sharing the current sticky proxy port.
    Results are collected in the main thread before updating session state.
    """
    results: dict = {}
    uncached: list = []

    # Fix 4 — cache check before any network activity
    for eid in event_ids:
        cached = st.session_state.get(f"ss_stats_{eid}")
        if cached is not None:
            results[eid] = cached
        else:
            uncached.append(eid)

    if not uncached:
        return results

    port = current_proxy_port  # read once — all threads share same sticky port

    def _fetch_one(event_id: int):
        from curl_cffi import requests as cf
        s = cf.Session(impersonate="chrome120")
        s.headers.update(HEADERS)
        if port and _proxy_ok():
            pu = _proxy_url(port)
            s.proxies = {"http": pu, "https": pu}
        url = f"{BASE_URL}/event/{event_id}/statistics"
        for attempt in range(2):
            try:
                r = s.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    # Challenger events sometimes return empty statistics on first hit —
                    # retry once after 500 ms before giving up.
                    if data.get("statistics"):
                        return event_id, data
            except Exception:
                pass
            if attempt == 0:
                time.sleep(0.5)
        return event_id, {}

    with ThreadPoolExecutor(max_workers=10) as ex:
        for eid, data in ex.map(_fetch_one, uncached):
            results[eid] = data
            # Update session state from main thread (map blocks until all done)
            st.session_state[f"ss_stats_{eid}"] = data

    return results


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------
def _agg(matches: list) -> dict:
    if not matches:
        return {"matches_played": 0}
    numeric_keys = [
        "aces", "double_faults", "first_serve_pct",
        "first_serve_pts_won", "second_serve_pts_won",
        "return_first_serve_pts_won", "return_second_serve_pts_won",
        "bp_converted", "bp_saved", "total_games_won", "total_match_games",
        "service_pts_won", "return_pts_won_count",
        "bp_converted_count", "bp_faced_count",
    ]
    wins = sum(1 for m in matches if m.get("won", False))
    agg = {
        "matches_played": len(matches),
        "wins":           wins,
        "win_rate":       wins / len(matches) * 100 if matches else 0,
    }
    for key in numeric_keys:
        vals = [m[key] for m in matches if key in m and m[key] is not None]
        agg[key] = sum(vals) / len(vals) if vals else None
    return agg


# ---------------------------------------------------------------------------
# IOC/Olympic country code → ISO alpha-2 mapping
# Sofascore uses IOC codes (e.g. GER, SUI, GRE) which differ from ISO alpha-3
# (DEU, CHE, GRC). This table converts them to alpha-2 for flag emoji rendering.
# ---------------------------------------------------------------------------
_IOC_TO_ALPHA2 = {
    "ALG": "DZ", "ARG": "AR", "ARM": "AM", "AUS": "AU", "AUT": "AT",
    "AZE": "AZ", "BAH": "BS", "BEL": "BE", "BIH": "BA", "BLR": "BY",
    "BOL": "BO", "BRA": "BR", "BUL": "BG", "CAN": "CA", "CHI": "CL",
    "CHN": "CN", "COL": "CO", "CRO": "HR", "CYP": "CY", "CZE": "CZ",
    "DEN": "DK", "ECU": "EC", "EGY": "EG", "ESP": "ES", "EST": "EE",
    "FIN": "FI", "FRA": "FR", "GBR": "GB", "GEO": "GE", "GER": "DE",
    "GRE": "GR", "HKG": "HK", "HUN": "HU", "INA": "ID", "IND": "IN",
    "IRL": "IE", "ISR": "IL", "ITA": "IT", "JPN": "JP", "KAZ": "KZ",
    "KOR": "KR", "KSA": "SA", "LAT": "LV", "LTU": "LT", "LUX": "LU",
    "MAR": "MA", "MDA": "MD", "MEX": "MX", "MON": "MC", "MNE": "ME",
    "NED": "NL", "NOR": "NO", "NZL": "NZ", "PAR": "PY", "PER": "PE",
    "PHI": "PH", "POL": "PL", "POR": "PT", "QAT": "QA", "ROU": "RO",
    "RSA": "ZA", "RUS": "RU", "SLO": "SI", "SRB": "RS", "SUI": "CH",
    "SVK": "SK", "SWE": "SE", "THA": "TH", "TPE": "TW", "TUN": "TN",
    "TUR": "TR", "UAE": "AE", "UKR": "UA", "URU": "UY", "USA": "US",
    "UZB": "UZ", "VEN": "VE",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def search_players(query: str, tour: str = "ATP") -> list:
    global _search_blocked, _search_blocked_ts
    if len(query) < 3:
        return []

    # Short search cache (15-min bucket): autocomplete fires the same popular
    # prefixes/names repeatedly across users, so caching makes those instant and
    # avoids a live proxy request entirely. Cache-first before any block check.
    _q_norm = query.strip().lower()
    _scache_key = f"ss_search_{tour}_{_q_norm}_{int(time.time()) // 900}"
    _cached = st.session_state.get(_scache_key)
    if _cached is not None:
        logger.info("SEARCH_CACHE_HIT | query=%r tour=%s", query, tour)
        record_cache_hit()
        return _cached

    # Clear stale block flag (2-minute cooldown before retrying)
    if _search_blocked and (time.time() - _search_blocked_ts) > 120:
        _search_blocked = False
        logger.info("SOFASCORE: block cooldown expired, resuming")

    if _search_blocked:
        raise SofascoreBlockedError(
            "Sofascore search blocked — proxy IPs flagged, retrying too soon"
        )

    # New search = new sticky proxy session + fresh Decodo session ID
    _new_session(force_port=True)

    # Warmup removed: x-requested-with header handles auth; cookie warmup
    # was eating 1-3s of the 10s frontend budget for no benefit.

    # Light throttle to avoid rate detection — keep it short so slow proxy
    # responses don't push the total past the frontend's 10s wall-clock limit.
    _search_throttle()

    logger.info("SEARCH_SOFASCORE | query=%r tour=%s", query, tour)
    data = _get(f"{BASE_URL}/search/all", {"q": query}, fast=True)

    # Raise if _get() set the block flag during this call
    if _search_blocked:
        raise SofascoreBlockedError(
            "Sofascore returned 403 on all retry attempts — proxy IPs blocked"
        )

    raw_results = data.get("results", [])
    logger.info("SEARCH_RAW | query=%r www_results=%d data_keys=%s",
                query, len(raw_results), list(data.keys()))

    # Log first 5 items so we can see exactly what Sofascore returned
    for i, item in enumerate(raw_results[:5]):
        e = item.get("entity") or {}
        sp = e.get("sport") or {}
        logger.info(
            "SEARCH_RAW_ITEM[%d] | type=%s sport_id=%s sport_name=%r gender=%r name=%r ranking=%s",
            i, e.get("type"), sp.get("id"), sp.get("name"), e.get("gender"),
            e.get("name"), e.get("ranking"),
        )

    # Fallback to api.sofascore.com if www returned nothing.
    # Search was verified working on the api. subdomain before the BASE_URL switch.
    if not raw_results:
        logger.info("SEARCH_FALLBACK_URL | query=%r www returned 0 — trying api.sofascore.com", query)
        data2 = _get(f"{SEARCH_BASE_URL}/search/all", {"q": query}, fast=True)
        raw_results2 = data2.get("results", [])
        logger.info("SEARCH_FALLBACK_URL_RAW | query=%r api_results=%d", query, len(raw_results2))
        if raw_results2:
            raw_results = raw_results2
            for i, item in enumerate(raw_results[:5]):
                e = item.get("entity") or {}
                sp = e.get("sport") or {}
                logger.info(
                    "SEARCH_API_ITEM[%d] | type=%s sport_id=%s sport_name=%r gender=%r name=%r",
                    i, e.get("type"), sp.get("id"), sp.get("name"), e.get("gender"), e.get("name"),
                )

    gender_pref = "F" if tour.upper() == "WTA" else "M"
    opposite_gender = "F" if gender_pref == "M" else "M"

    def _is_tennis_player(item: dict) -> bool:
        """
        Accept a result item if it looks like a tennis player.
        Sofascore tennis sport IDs: 5 (confirmed). Type 1 = individual athlete.
        We accept type == 1 OR sport_id == 5 — either is sufficient.
        Gender is not checked (partial-name queries often omit it).
        """
        e = item.get("entity") or {}
        sp = e.get("sport") or {}
        sport_id   = sp.get("id")
        sport_name = (sp.get("name") or "").lower()
        etype      = e.get("type")
        gender     = e.get("gender")

        is_tennis = (sport_id == 5 or "tennis" in sport_name)
        is_person = (etype == 1)
        is_wrong_gender = (gender == opposite_gender)

        if not is_tennis:
            return False
        if not is_person:
            # Accept anyway if sport is definitely tennis — some responses omit type
            pass
        if is_wrong_gender:
            return False
        return True

    entities = [item.get("entity") or {} for item in raw_results if _is_tennis_player(item)]
    logger.info("SEARCH_FILTERED | query=%r tennis_players=%d", query, len(entities))

    # Fallback for multi-word queries: try each word individually and merge
    if not entities:
        seen_ids: set = set()
        words = [w for w in query.strip().split() if len(w) >= 3 and w.lower() != query.strip().lower()]
        for word in words:
            word_data = _get(f"{BASE_URL}/search/all", {"q": word}, fast=True)
            for item in word_data.get("results", []):
                if _is_tennis_player(item):
                    e = item.get("entity") or {}
                    eid = e.get("id")
                    if eid and eid not in seen_ids:
                        entities.append(e)
                        seen_ids.add(eid)
        if words:
            logger.info("SEARCH_FALLBACK | words=%s entities_after=%d", words, len(entities))

    # Score-based sort: exact last name=100, starts-with=80, contains=60, first-name=40.
    # Accent-insensitive: a query "molcan" must exact-match "Molčan" — otherwise
    # the č broke the exact match and an unrelated "Molcan" outranked the real
    # accented player.
    query_lower = _strip_accents(query.strip().lower())

    def _score(e: dict) -> float:
        name_parts = [_strip_accents(p) for p in (e.get("name") or "").lower().split()]
        last = name_parts[-1] if name_parts else ""
        rank_penalty = (e.get("ranking") or e.get("teamRank") or 500) / 1000
        if last == query_lower:
            return 100 - rank_penalty
        if last.startswith(query_lower):
            return 80 - rank_penalty
        if query_lower in last:
            return 60 - rank_penalty
        if any(query_lower in p for p in name_parts):
            return 40 - rank_penalty
        return 0 - rank_penalty

    entities.sort(key=_score, reverse=True)

    out = []
    seen_final: set = set()
    for e in entities:
        eid = e.get("id")
        if eid in seen_final:
            continue
        seen_final.add(eid)
        if len(out) >= 5:
            break
        c = e.get("country") or {}
        alpha3 = (c.get("alpha3") or "") if isinstance(c, dict) else ""
        alpha2_raw = (c.get("alpha2") or "") if isinstance(c, dict) else ""
        # Resolve alpha2: use API value first, then convert via IOC→alpha2 table
        alpha2 = alpha2_raw.upper() or _IOC_TO_ALPHA2.get(alpha3.upper(), "")
        country_display = alpha3 or (c.get("name") or "" if isinstance(c, dict) else "")
        out.append({
            "id":          eid,
            "name":        e.get("name") or e.get("shortName") or "",
            "currentRank": e.get("ranking") or e.get("teamRank"),
            "countryAcr":  country_display,
            "countryCode": alpha2,
            "gender":      e.get("gender") or "",
        })
    logger.info("SEARCH_RESULT | query=%r out=%s", query, [x["name"] for x in out])
    # Cache non-empty results for the 15-min window (don't cache empties — a
    # transient block shouldn't be remembered as "no such player").
    if out:
        st.session_state[_scache_key] = out
    return out


def get_player_stats_by_surface(player_id, tour: str = "ATP") -> dict:
    pid = int(player_id)
    # v6: full-history pagination, SS aggregation tiers, ace-against extraction.
    # Cache key uses the same 6h bucket as the events cache (STEP 3) so surface
    # stats refresh together and live proxy volume stays low.
    _bucket = int(time.time()) // _CACHE_BUCKET_SECS
    cache_key = f"ss_surface_v6_{pid}_{_bucket}"
    if cache_key in st.session_state:
        logger.info("SURFACE_CACHE_HIT | pid=%s | bucket=%s", pid, _bucket)
        record_cache_hit()
        return st.session_state[cache_key]

    logger.info("[STATS_FLOW] START | player_id=%d tour=%s", pid, tour)

    now = time.time()
    events = _get_player_recent_events(pid, max_pages=MAX_PAGES_DEFAULT)
    logger.info("[STATS_FLOW] EVENTS_RAW | fetched=%d", len(events))

    # ── Stale-cache fallback when the live fetch comes back empty ─────────────
    # An empty events list almost always means Sofascore served the Varnish JS
    # challenge / 403'd every retry (see _get logging) rather than the player
    # genuinely having no matches. Rather than caching an empty result for the
    # next 2 hours and projecting on zero data, reuse the most recent non-empty
    # snapshot from any PRIOR 2-hour bucket, flagged stale so the caller can
    # surface a freshness warning.
    if not events:
        logger.warning(
            "[STATS_FLOW] EMPTY_FETCH | pid=%d | likely Sofascore block (403/challenge) "
            "— searching prior cache buckets for a stale snapshot", pid,
        )
        stale = None
        stale_bucket = -1
        _prefix = f"ss_surface_v6_{pid}_"
        for k in list(st.session_state.keys()):
            if not k.startswith(_prefix):
                continue
            try:
                b = int(k.rsplit("_", 1)[1])
            except (ValueError, IndexError):
                continue
            cached_val = st.session_state.get(k) or {}
            if cached_val.get("all_matches") and b > stale_bucket:
                stale, stale_bucket = cached_val, b
        if stale is not None:
            logger.warning(
                "[STATS_FLOW] STALE_CACHE_USED | pid=%d | bucket=%d (current=%d) | matches=%d",
                pid, stale_bucket, _bucket, len(stale.get("all_matches", [])),
            )
            stale = dict(stale)
            stale["_stale_cache"] = True
            stale["_stat_match_count"] = len([
                m for m in stale.get("all_matches", []) if m.get("has_stats")
            ])
            return stale
        logger.error(
            "[STATS_FLOW] NO_DATA | pid=%d | empty fetch AND no prior cache — "
            "projection caller should refuse rather than use tour-average defaults", pid,
        )

    # Filter to finished singles events; log surface detection for debugging
    valid: list = []
    for event in events:
        ts = event.get("startTimestamp", 0) or 0
        if ts and ts > now:
            continue
        if event.get("status", {}).get("type", "") not in ("finished", "ended"):
            continue
        ht = event.get("homeTeam", {}).get("name", "")
        at = event.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        valid.append(event)

    # EVENT_DEBUG: log tournament → surface mapping for first 20 events so we
    # can confirm Challenger events are detected with the right surface.
    for event in valid[:20]:
        tournament  = event.get("tournament") or {}
        unique_t    = tournament.get("uniqueTournament") or {}
        top_unique  = event.get("uniqueTournament") or {}
        category    = tournament.get("category") or {}
        surf_detected = _infer_surface_from_event(event, log_missing=False)
        logger.info(
            "EVENT_DEBUG | id=%s | name=%r | "
            "gt_event=%s | gt_tourn=%s | gt_uniq=%s | gt_top_uniq=%s | gt_cat=%s | "
            "category_id=%s | surface=%s",
            event.get("id"),
            tournament.get("name", ""),
            event.get("groundType"),
            tournament.get("groundType"),
            unique_t.get("groundType"),
            top_unique.get("groundType"),
            category.get("groundType"),
            category.get("id"),
            surf_detected,
        )

    logger.info("[STATS_FLOW] VALID_EVENTS | valid=%d (finished singles)", len(valid))

    # Fetch stats for the most recent 50 matches in parallel
    event_ids = [e.get("id", 0) for e in valid[:50]]
    stats_map = _fetch_stats_parallel(event_ids)
    events_with_stats = sum(1 for eid in event_ids if stats_map.get(eid, {}).get("statistics"))
    logger.info("[STATS_FLOW] STATS_FETCHED | requested=%d got_statistics=%d", len(event_ids), events_with_stats)

    # Build per-match records.
    # all_match_stats  — every finished single (for form/display/win-rate).
    # stat_matches     — only matches where statistics parsed successfully;
    #                    these drive the stat averages and "Matches" count shown in cards.
    all_match_stats: list = []
    stat_matches:    list = []
    _logged_first = False

    for event in valid:
        ts      = event.get("startTimestamp", 0) or 0
        home_id = event.get("homeTeam", {}).get("id")
        side    = "home" if home_id == pid else "away"
        opp     = event.get("awayTeam" if side == "home" else "homeTeam", {})
        hs      = event.get("homeScore", {}).get("current", 0) or 0
        aws     = event.get("awayScore", {}).get("current", 0) or 0
        won     = (hs > aws) if side == "home" else (aws > hs)

        score_str = _build_score_str(event)
        tmg = _calc_total_match_games(event)
        # Use native groundType field first; fall back to keyword matching
        surface_val = _infer_surface_from_event(event)

        base = {
            "won":               won,
            "surface":           surface_val,
            "tournament":        event.get("tournament", {}).get("name", "Unknown"),
            "timestamp":         ts,
            "date":              datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
            "event_id":          event.get("id"),
            "opponent_name":     opp.get("name", "Unknown"),
            "score":             score_str,
            "total_match_games": tmg,
            "comp_tier":         _competition_tier(event),   # strength-of-field
        }

        # Diagnostic: log first match's raw score/side fields to confirm win detection
        if not _logged_first:
            logger.info(
                "[WIN_DETECT] first event: id=%s side=%s homeScore=%s awayScore=%s won=%s surface=%s tourn=%r",
                event.get("id"), side, hs, aws, won, surface_val,
                event.get("tournament", {}).get("name", ""),
            )
            _logged_first = True

        has_stats = False
        stats_data = stats_map.get(event.get("id", 0)) or {}
        if stats_data:
            parsed = _parse_match_stats(stats_data, event, pid)
            if parsed:
                # _parse_match_stats intentionally excludes "surface" from its
                # return dict to avoid overwriting the groundType-aware detection
                # already stored in base["surface"] via _infer_surface_from_event.
                # Any remaining overlap (won, tournament, etc.) is idempotent.
                base.update(parsed)
                has_stats = True

        all_match_stats.append(base)
        if has_stats:
            # Only stat-parsed matches contribute to the aggregated averages
            stat_matches.append(base)

    # Aggregate stats using ONLY stat-parsed matches so the "Matches" count
    # reflects real data rows, not matches that returned empty statistics.
    def _agg_surface(surf):
        subset = (
            [m for m in stat_matches if m.get("surface") == surf]
            if surf else stat_matches
        )
        return surf or "All", _agg(subset)

    surfaces: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for label, result in ex.map(_agg_surface, [None, "Hard", "Clay", "Grass"]):
            surfaces[label] = result

    sorted_m = sorted(all_match_stats, key=lambda x: x.get("timestamp", 0), reverse=True)

    # Diagnostic: log first 3 matches for total_match_games verification
    for i, m in enumerate(sorted_m[:3]):
        logger.info(
            "[TOTAL_GAMES] match %d: score=%r  total_match_games=%s  tournament=%r",
            i + 1, m.get("score"), m.get("total_match_games"), m.get("tournament")
        )

    surfaces["form"] = [
        {
            "won":        m.get("won", False),
            "tournament": m.get("tournament", ""),
            "surface":    m.get("surface", ""),
            "opponent":   m.get("opponent_name", ""),
        }
        for m in sorted_m[:10]
    ]
    surfaces["all_matches"] = sorted_m
    for surf in ("Hard", "Clay", "Grass"):
        surfaces[f"{surf}_matches"] = [m for m in sorted_m if m.get("surface") == surf]

    # Build recent-results strings per surface for AI scouting report context.
    # Format: "W 6-3 6-4 vs Napolitano (Clay, Apr 13)"
    def _recent_result_str(m: dict) -> str:
        result_ch = "W" if m.get("won") else "L"
        score     = m.get("score", "")
        opp       = m.get("opponent_name", "Unknown")
        date_str  = ""
        ts_val    = m.get("timestamp", 0) or 0
        if ts_val:
            try:
                dt       = datetime.utcfromtimestamp(ts_val)
                cur_year = datetime.utcnow().year
                date_str = dt.strftime("%b") + " " + str(dt.day)
                if dt.year != cur_year:
                    date_str += f" '{str(dt.year)[2:]}"   # e.g. "May 26 '25"
            except Exception:
                date_str = m.get("date", "")
        tourn = m.get("tournament", "")
        return f"{result_ch} {score} vs {opp} ({tourn}, {date_str})" if date_str else f"{result_ch} {score} vs {opp} ({tourn})"

    for surf in ("Hard", "Clay", "Grass"):
        surf_matches = surfaces[f"{surf}_matches"]
        surfaces[f"{surf}_recent_results"] = [
            _recent_result_str(m) for m in surf_matches[:5]
        ]

    # Build sofascore_surface_log: last 10 stat-rich matches per surface.
    # These are the matches with full parsed statistics (aces, DFs, BP, total_games).
    # Used as the "recent form" layer in blended_stats.
    def _ss_log_entry(m: dict) -> dict:
        ts_val = m.get("timestamp", 0) or 0
        try:
            dt       = datetime.utcfromtimestamp(ts_val)
            cur_year = datetime.utcnow().year
            date_str = dt.strftime("%b") + " " + str(dt.day)
            if dt.year != cur_year:
                date_str += f" '{str(dt.year)[2:]}"   # e.g. "May 26 '25"
        except Exception:
            date_str = m.get("date", "")
        opp_parts = (m.get("opponent_name") or "Unknown").split()
        opp_abbr = opp_parts[-1] if opp_parts else "Unknown"
        return {
            "date":            date_str,
            "date_ts":         ts_val,
            "tournament":      m.get("tournament", ""),
            "surface":         m.get("surface", ""),
            "opponent":        m.get("opponent_name", "Unknown"),
            "opponent_abbr":   opp_abbr,
            "won":             m.get("won", False),
            "score":           m.get("score", ""),
            "total_match_games": m.get("total_match_games"),
            "aces":            m.get("aces"),
            "double_faults":   m.get("double_faults"),
            "bp_converted_count":    m.get("bp_converted_count"),
            "bp_converted":          m.get("bp_converted"),
            # Return-side raw counts (NOT serve stats):
            "return_bp_converted":    m.get("return_bp_converted"),    # BPs won as returner
            "return_bp_opportunities": m.get("return_bp_opportunities"), # BPs created as returner
            # Serve-side raw count (NEVER use as conversion-rate denominator):
            "bp_faced_count":        m.get("bp_faced_count"),   # BPs faced on own serve
            "first_serve_pts_won":   m.get("first_serve_pts_won"),
            "second_serve_pts_won":  m.get("second_serve_pts_won"),
        }

    for surf in ("Hard", "Clay", "Grass"):
        # Strict inclusion: only add matches where the stats API returned at least
        # aces AND bp_converted_count.  This excludes matches where the stats fetch
        # failed (aces would be None) and matches with only partial data.
        # A genuine 0 value (player hit 0 aces / won 0 BPs) is still included.
        surf_stat_matches = [
            m for m in sorted_m
            if m.get("surface") == surf
            and m.get("aces") is not None
            and m.get("bp_converted_count") is not None
        ]
        skipped = sum(
            1 for m in sorted_m
            if m.get("surface") == surf
            and not (m.get("aces") is not None and m.get("bp_converted_count") is not None)
        )
        if skipped:
            logger.info(
                "SURFACE_LOG | surface=%s | included=%d | skipped_no_stats=%d",
                surf, len(surf_stat_matches), skipped,
            )
        surfaces[f"{surf}_surface_log"] = [_ss_log_entry(m) for m in surf_stat_matches[:10]]

    # Per-surface chart log: ALL matches (stat-rich AND stat-poor) for bar chart.
    # Distinct from {surf}_surface_log which only contains stat-rich matches used
    # for blended stats.  For challenger players (Sofascore stats API fails),
    # this ensures the bar chart can still display match results (won/score/date)
    # even when individual stat values are None.
    for surf in ("Hard", "Clay", "Grass"):
        surf_all = [m for m in sorted_m if m.get("surface") == surf]
        surfaces[f"{surf}_chart_log"] = [_ss_log_entry(m) for m in surf_all[:10]]

    # All-surface chart log: most recent 10 matches across all surfaces combined.
    # Used as final Sofascore fallback when surface-specific chart log is also empty.
    surfaces["all_surface_chart_log"] = [_ss_log_entry(m) for m in sorted_m[:10]]

    # ── New SS aggregation tiers (for blended_stats) ─────────────────────────
    # Build all_time, recent_3yr, and last_20 tiers for each surface (and All).
    # Uses sorted_m (newest-first) so that stat_m[:20] captures the most recent
    # 20 stat-rich matches rather than the oldest 20 from the raw iteration order.
    _stat_ids = {id(m) for m in stat_matches}   # O(1) lookup — same objects in sorted_m

    for surf_label in (None, "Hard", "Clay", "Grass"):
        label = surf_label or "All"
        all_m  = [m for m in sorted_m
                  if surf_label is None or m.get("surface") == surf_label]
        stat_m = [m for m in sorted_m
                  if (surf_label is None or m.get("surface") == surf_label)
                  and id(m) in _stat_ids]

        # 3-year window: 2023-present
        all_m_3yr  = [m for m in all_m  if _year_from_ts(m.get("timestamp", 0)) in _RECENT_YEARS]
        stat_m_3yr = [m for m in stat_m if _year_from_ts(m.get("timestamp", 0)) in _RECENT_YEARS]

        # Last 20 stat-rich matches on this surface (newest-first = most recent 20)
        stat_m_20 = stat_m[:20]

        surfaces[f"{label}_all_time_stats"]  = _agg_split(all_m, stat_m)
        surfaces[f"{label}_recent_3yr_stats"] = _agg_split(all_m_3yr, stat_m_3yr)
        surfaces[f"{label}_last_20"]          = _agg_split(stat_m_20, stat_m_20)

        logger.info(
            "SS_TIERS | surface=%s | all_time=%d/%d | 3yr=%d/%d | last20=%d",
            label,
            len(all_m), len(stat_m),
            len(all_m_3yr), len(stat_m_3yr),
            len(stat_m_20),
        )

        # Ace-against: average of opponent aces per match (from opp_aces field)
        ace_ag_vals = [m["opp_aces"] for m in stat_m if m.get("opp_aces") is not None]
        surfaces[f"{label}_ace_against_per_match"] = (
            round(sum(ace_ag_vals) / len(ace_ag_vals), 2) if ace_ag_vals else None
        )

    # Summary log: confirms tier keys were built and shows match counts per surface.
    # If Railway shows all zeros here, the surface detection or event fetch failed.
    tier_summary = {
        lbl: surfaces.get(f"{lbl}_all_time_stats", {}).get("matches_played", 0)
        for lbl in ("All", "Hard", "Clay", "Grass")
    }
    logger.info("SS_TIERS_BUILT | pid=%d | all_time_matches=%s", pid, tier_summary)

    logger.info(
        "[STATS_FLOW] AVERAGES | Hard: n=%s aces=%s | Clay: n=%s aces=%s | Grass: n=%s aces=%s | All: n=%s",
        surfaces.get("Hard", {}).get("matches_played"),
        surfaces.get("Hard", {}).get("aces"),
        surfaces.get("Clay", {}).get("matches_played"),
        surfaces.get("Clay", {}).get("aces"),
        surfaces.get("Grass", {}).get("matches_played"),
        surfaces.get("Grass", {}).get("aces"),
        surfaces.get("All", {}).get("matches_played"),
    )

    # Tag freshness + the real stat-row count so the projection caller can
    # detect a total data gap and refuse rather than project on tour-averages.
    surfaces["_stale_cache"] = False
    surfaces["_stat_match_count"] = len(stat_matches)

    # Only cache when we actually got STAT-RICH matches. Events can succeed
    # (e.g. 42 matches found) while the per-match statistics calls all come back
    # empty (transient stats-API/proxy failure) — that yields N/A serve stats.
    # Caching that would serve blank serve/return stats for the whole 6h window;
    # leaving it uncached lets the next request re-fetch and recover.
    if stat_matches:
        st.session_state[cache_key] = surfaces
    else:
        logger.warning(
            "SURFACE_NO_STATS | pid=%d | %d events but 0 stat-rich matches — "
            "NOT caching (would serve N/A serve stats); next request retries",
            pid, len(all_match_stats),
        )
    return surfaces


def get_player_next_match(player_id, tour: str = "ATP") -> dict:
    """Return the player's NEXT scheduled singles match from Sofascore:
    {tournament, surface, opponent_name, start_timestamp}. Used so a feature can
    show the upcoming event (e.g. 'Bad Homburg') and its surface rather than the
    most recent completed match. Returns {} if none / on failure. Cached 1h."""
    pid = int(player_id)
    cache_key = f"ss_next_{pid}_{int(time.time()) // 3600}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    result: dict = {}
    try:
        _probe = probe_request(f"{BASE_URL}/team/{pid}/events/next/0")
        result["_debug_probe"] = {"status": _probe.get("status"),
                                  "body": (_probe.get("body_snippet") or _probe.get("error") or "")[:200]}
        data = _get(f"{BASE_URL}/team/{pid}/events/next/0")
        result["_debug_keys"] = list(data.keys()) if isinstance(data, dict) else str(type(data))
        _raw = data.get("events", []) or []
        result["_debug_raw_count"] = len(_raw)
        if _raw:
            _e0 = _raw[0]
            result["_debug_first"] = {
                "tournament": (_e0.get("tournament") or {}).get("name"),
                "ts": _e0.get("startTimestamp"),
                "status": (_e0.get("status") or {}).get("type"),
                "home": (_e0.get("homeTeam") or {}).get("name"),
                "away": (_e0.get("awayTeam") or {}).get("name"),
            }
        now = time.time()
        upcoming = []
        for e in _raw:
            ts = e.get("startTimestamp", 0) or 0
            if ts < now:
                continue
            ht = e.get("homeTeam", {}).get("name", "")
            at = e.get("awayTeam", {}).get("name", "")
            if "/" in ht or "/" in at:   # doubles
                continue
            upcoming.append(e)
        upcoming.sort(key=lambda e: e.get("startTimestamp", 0) or 0)
        if upcoming:
            ev = upcoming[0]
            home_id = ev.get("homeTeam", {}).get("id")
            side = "home" if home_id == pid else "away"
            opp = ev.get("awayTeam" if side == "home" else "homeTeam", {})
            result = {
                "tournament": ev.get("tournament", {}).get("name", ""),
                "surface": _infer_surface_from_event(ev),
                "opponent_name": opp.get("name", ""),
                "opponent_id": opp.get("id"),
                "start_timestamp": ev.get("startTimestamp"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("next-match fetch failed for pid=%s: %s", pid, exc)
        result = {}

    st.session_state[cache_key] = result
    return result


def _get_player_events_paged(player_id: int, max_pages: int = 10) -> list:
    """
    Fetch up to max_pages pages of recent events for a player.
    Stops early if a page returns fewer than 10 events or total > 200.
    Uses a separate cache key from the stats cache to allow deeper pagination.
    """
    cache_key = f"ss_events_h2h_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    all_events = []
    for page in range(max_pages):
        data   = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
        events = data.get("events", [])
        if not events:
            break
        all_events.extend(events)
        if len(events) < 10 or len(all_events) > 200:
            break

    st.session_state[cache_key] = all_events
    return all_events


def get_h2h_summary(tour: str, p1: str, p2: str,
                    surface: Optional[str] = None) -> dict:
    empty = {
        "total": 0, "p1_wins": 0, "p2_wins": 0,
        "surface_matches": 0, "surface_p1_wins": 0, "surface_p2_wins": 0,
        "h2h_rate": 0.5,
        "matches": pd.DataFrame(), "surface_matches_df": pd.DataFrame(),
        "date_range": None, "surface_breakdown": {},
    }
    p1_id = int(p1)
    p2_id = int(p2)

    # Fetch both players' event histories and cross-reference for matches
    p1_events = _get_player_events_paged(p1_id, max_pages=10)
    p2_events = _get_player_events_paged(p2_id, max_pages=10)

    # Build a set of event IDs from p2's history for fast lookup
    p2_event_ids = {e.get("id") for e in p2_events if e.get("id")}

    # Find events appearing in BOTH players' lists where they faced each other
    h2h = []
    seen_ids: set = set()
    for e in p1_events:
        eid = e.get("id")
        if not eid or eid in seen_ids:
            continue
        home_id = e.get("homeTeam", {}).get("id")
        away_id = e.get("awayTeam", {}).get("id")
        if {home_id, away_id} != {p1_id, p2_id}:
            continue
        status = e.get("status", {}).get("type", "")
        if status not in ("finished", "ended"):
            continue
        ht = e.get("homeTeam", {}).get("name", "")
        at = e.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        # Prefer events confirmed in p2's history too, but don't discard if missing
        seen_ids.add(eid)
        h2h.append(e)

    # Also check p2's events for any matches not yet found via p1
    for e in p2_events:
        eid = e.get("id")
        if not eid or eid in seen_ids:
            continue
        home_id = e.get("homeTeam", {}).get("id")
        away_id = e.get("awayTeam", {}).get("id")
        if {home_id, away_id} != {p1_id, p2_id}:
            continue
        status = e.get("status", {}).get("type", "")
        if status not in ("finished", "ended"):
            continue
        ht = e.get("homeTeam", {}).get("name", "")
        at = e.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        seen_ids.add(eid)
        h2h.append(e)

    if not h2h:
        return empty

    rows = []
    timestamps = []
    for e in h2h:
        is_p1home = e.get("homeTeam", {}).get("id") == p1_id
        hs  = e.get("homeScore", {}).get("current", 0) or 0
        aws = e.get("awayScore", {}).get("current", 0) or 0
        p1w = (is_p1home and hs > aws) or (not is_p1home and aws > hs)
        ts  = e.get("startTimestamp", 0)
        dt  = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
        if ts:
            timestamps.append(ts)
        opp = e.get("awayTeam", {}) if is_p1home else e.get("homeTeam", {})
        rows.append({
            "Match Date":  dt,
            "Tournament":  e.get("tournament", {}).get("name", ""),
            "Surface":     _infer_surface(e.get("tournament", {}).get("name", "")),
            "Result":      "W" if p1w else "L",
            "Opponent":    opp.get("name", "Unknown"),
            "Score":       _build_score_str(e),
        })

    total   = len(rows)
    p1_wins = sum(1 for r in rows if r["Result"] == "W")
    surf_rows  = [r for r in rows if r["Surface"] == surface] if surface else []
    surf_total = len(surf_rows)
    surf_p1w   = sum(1 for r in surf_rows if r["Result"] == "W")
    h2h_rate   = (surf_p1w / surf_total if surf_total
                  else p1_wins / total if total else 0.5)

    # Build date_range from earliest to latest year
    date_range = None
    if timestamps:
        years = [datetime.utcfromtimestamp(ts).year for ts in timestamps]
        if min(years) == max(years):
            date_range = str(min(years))
        else:
            date_range = f"{min(years)}–{max(years)}"

    # Build surface breakdown dict: {surface: count}
    surface_breakdown: dict = {}
    for r in rows:
        surf = r["Surface"]
        surface_breakdown[surf] = surface_breakdown.get(surf, 0) + 1

    return {
        "total":               total,
        "p1_wins":             p1_wins,
        "p2_wins":             total - p1_wins,
        "surface_matches":     surf_total,
        "surface_p1_wins":     surf_p1w,
        "surface_p2_wins":     surf_total - surf_p1w,
        "h2h_rate":            h2h_rate,
        "matches":             pd.DataFrame(rows),
        "surface_matches_df":  pd.DataFrame(surf_rows) if surf_rows else pd.DataFrame(),
        "date_range":          date_range,
        "surface_breakdown":   surface_breakdown,
    }


def get_h2h_stat_avg(tour: str, p1: str, p2: str,
                     surface: Optional[str] = None) -> dict:
    empty = {"ace": None, "df": None, "games_avg": None}
    p1_id = int(p1)
    p2_id = int(p2)

    events = _get_player_recent_events(p1_id)
    h2h = [
        e for e in events
        if {e.get("homeTeam", {}).get("id"), e.get("awayTeam", {}).get("id")} == {p1_id, p2_id}
        and e.get("status", {}).get("type", "") in ("finished", "ended")
        and (not surface or _infer_surface(e.get("tournament", {}).get("name", "")) == surface)
    ]

    if not h2h:
        return empty

    ace_sum = df_sum = bp_sum = n = 0
    games_sum = games_n = 0
    for e in h2h:
        # Total match games from period scores — no stats request needed
        tmg = _calc_total_match_games(e)
        if tmg is not None and tmg > 0:
            games_sum += tmg
            games_n   += 1

        stats  = _get_event_statistics(e.get("id", 0))
        parsed = _parse_match_stats(stats, e, p1_id) if stats else None
        if parsed:
            if parsed.get("aces") is not None:
                ace_sum += parsed["aces"]
            if parsed.get("double_faults") is not None:
                df_sum += parsed["double_faults"]
            if parsed.get("bp_converted_count") is not None:
                bp_sum += parsed["bp_converted_count"]
            n += 1

    return {
        "ace":       round(ace_sum  / n,       2) if n       else None,
        "df":        round(df_sum   / n,       2) if n       else None,
        "bp":        round(bp_sum   / n,       2) if n       else None,
        "games_avg": round(games_sum / games_n, 1) if games_n else None,
    }


def get_tournament_record_modifier(player_id: str, tournament_id: str,
                                   tour: str = "ATP") -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Compatibility helpers
# ---------------------------------------------------------------------------
def ts_to_date_str(ts) -> str:
    if not ts:
        return "-"
    try:
        if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-":
            return ts[:10]
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d %Y")
    except Exception:
        return str(ts)[:10]


def format_h2h_table(df: pd.DataFrame, p1_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    expected = {"Match Date", "Tournament", "Surface", "Result", "Opponent", "Score"}
    if expected.issubset(set(df.columns)):
        return df[list(expected)].copy()
    return df.copy()
