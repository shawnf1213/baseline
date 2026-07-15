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


def _parse_port_list(spec: str) -> list:
    """Accept a comma list AND/OR 'start-end' ranges, so all 50 Decodo endpoint
    ports can be named with a single env value, e.g. PROXY_PORT_LIST=10001-10050
    (or '10001,10002,10010-10020'). Each port is a sticky-1min rotating IP, so
    more ports = more IP diversity = far fewer Sofascore blocks."""
    out: list = []
    for tok in (spec or "").replace(" ", "").split(","):
        if not tok:
            continue
        if "-" in tok:
            a, _, b = tok.partition("-")
            if a.isdigit() and b.isdigit():
                out.extend(range(int(a), int(b) + 1))
        elif tok.isdigit():
            out.append(int(tok))
    return out


_PROXY_PORTS = _parse_port_list(os.getenv("PROXY_PORT_LIST", ""))

# One port + one Session for the lifetime of a player search session.
# Only rotated when: new search starts, 407 received, or 403 persists.
current_proxy_port: Optional[int] = None
_current_session_id: str = ""
_used_ports: list = []
_bad_ports:  dict = {}          # port -> timestamp marked bad
_proxy_session   = None         # curl_cffi Session — reused across all requests

# Decodo sticky-SESSION usernames ({user}-session-{id}) give a fresh residential
# IP per session, so the bot rotates across many IPs instead of being stuck on
# the ~7 static port IPs (which Sofascore then blocks wholesale). Requires a
# Decodo plan with session support; if the plan rejects it (407), we auto-fall
# back to the plain username on the first failure. Opt out with PROXY_SESSIONS=0.
_session_mode = os.getenv("PROXY_SESSIONS", "0").strip() not in ("0", "false", "False")

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

# ── Degraded-fetch cache guard (see get_player_stats_by_surface) ─────────────
# A proxy/stats-API outage can return every EVENT successfully while returning no
# per-match STATISTICS. The resulting records look structurally fine (score,
# opponent, timestamp) but carry no aces/DF/BP, so every stat-driven confidence
# guard reads the player as near-empty. Caching that poisons the whole 6h bucket.
_DEGRADED_MIN_REQUESTED  = 20    # don't judge yield on a tiny request set
_DEGRADED_MIN_YIELD      = 0.10  # absolute floor, ONLY when no prior snapshot exists
                                 # (kept low: genuine ITF-only players legitimately
                                 #  have near-zero stat coverage on Sofascore)
_DEGRADED_VS_PRIOR_RATIO = 0.50  # <50% of a prior healthy snapshot = collapse

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


def _proxy_url(port: int, session_id: Optional[str] = None) -> str:
    # With session mode on (and a session id available) use Decodo's sticky-
    # session username so each session gets its own rotating residential IP.
    user = (f"{_PROXY_USER}-session-{session_id}"
            if (_session_mode and session_id) else _PROXY_USER)
    return f"http://{user}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"


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
        pu = _proxy_url(current_proxy_port, _current_session_id)
        s.proxies = {"http": pu, "https": pu}
    _proxy_session = s
    logger.info("New session: port=%s sid=%s profile=%s sessions=%s",
                current_proxy_port, _current_session_id, profile, _session_mode)


def _maybe_disable_sessions() -> bool:
    """A 407 while using a Decodo session username means the plan does not
    support sticky sessions — fall back to the plain username (which works, just
    without IP rotation) and rebuild the session on the SAME port. Returns True
    if it just switched, so the caller retries instead of marking the port bad."""
    global _session_mode
    if _session_mode:
        _session_mode = False
        logger.warning("PROXY_SESSIONS_UNSUPPORTED | 407 on session username — "
                       "falling back to plain username (set PROXY_SESSIONS=0 to silence)")
        _new_session(force_port=False)
        return True
    return False


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
                if _maybe_disable_sessions():
                    continue
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
                # A 407 here usually means the Decodo plan rejected the sticky-
                # session username — fall back to plain and retry the same port
                # before treating the port as dead.
                if "407" in msg and _maybe_disable_sessions():
                    continue
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


def _ground_str_to_surface(s: str):
    """Map a Sofascore groundType string to a surface, handling COMPOUND names.
    Sofascore reports e.g. 'Red clay indoor', 'Green clay', 'Hardcourt outdoor'
    — exact-match misses these, so fall back to substring matching on the
    authoritative groundType BEFORE the tournament-name keyword guess. Fixes
    e.g. WTA Stuttgart (indoor clay) being mislabeled grass via the 'stuttgart'
    grass keyword."""
    if not s:
        return None
    s = s.lower()
    exact = _GROUND_TYPE_MAP.get(s)
    if exact:
        return exact
    if "grass" in s:
        return "Grass"
    if "clay" in s:          # red clay, green clay, clay indoor, terre battue
        return "Clay"
    if "hard" in s or "carpet" in s:
        return "Hard"
    return None


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
            mapped = _ground_str_to_surface(gt_raw)
            if mapped:
                return mapped
        elif isinstance(gt_raw, dict):
            # Some API versions nest as {"name": "clay"}
            mapped = _ground_str_to_surface(gt_raw.get("name") or "")
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
    # opponent aces per match = this player's ACES ALLOWED (return/defensive stat)
    "opp_aces",
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

    # ── Service / Return games won % (sum/sum, like bp_converted) ────────────
    # Cleanest serve/return dominance measures: holds and breaks as a share of
    # service / return games actually played. Sum/sum avoids per-match rate bias.
    _sgw_pairs = [
        (m["service_games_won"], m["service_games"]) for m in stat_m
        if m.get("service_games_won") is not None and m.get("service_games")
    ]
    # DENOMINATORS ARE EXPOSED alongside the percentages. Without them a caller
    # cannot tell a 300-game hold rate from a 16-game one, and they look identical.
    # Real case (Gina Feistel, clay): 122 clay matches, 36 stat-rich, but only TWO
    # carried service_games — so "Hold 93.75%" was 15/16 service games across two
    # ITF matches, displayed next to matches_played=36 as if that were the sample.
    # A rate without its denominator is not a statistic, it's a rumour.
    if _sgw_pairs:
        _tw = sum(w for w, _ in _sgw_pairs)
        _tp = sum(p for _, p in _sgw_pairs)
        result["service_games_won_pct"] = round(_tw / _tp * 100, 2) if _tp > 0 else None
        result["service_games_n"] = _tp          # service games behind the pct
        result["service_games_matches_n"] = len(_sgw_pairs)
    _rgw_pairs = [
        (m["return_games_won"], m["return_games"]) for m in stat_m
        if m.get("return_games_won") is not None and m.get("return_games")
    ]
    if _rgw_pairs:
        _tw = sum(w for w, _ in _rgw_pairs)
        _tp = sum(p for _, p in _rgw_pairs)
        result["return_games_won_pct"] = round(_tw / _tp * 100, 2) if _tp > 0 else None
        result["return_games_n"] = _tp           # return games behind the pct
        result["return_games_matches_n"] = len(_rgw_pairs)

    # BP generated per match = opportunities the player creates as a returner.
    # (return_bp_opportunities is already aggregated above; expose it explicitly
    # under the name the projection + display use.)
    if result.get("return_bp_opportunities") is not None:
        result["bp_generated_per_match"] = result["return_bp_opportunities"]

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


def _count_sets_and_tiebreaks(event: dict) -> tuple:
    """(sets_played, tiebreak_sets) from the per-set game scores. A set reached
    a tiebreak when it finished 7-6 / 6-7 (NEW SIGNAL 3 — serve dominance)."""
    home_sc = event.get("homeScore", {}) or {}
    away_sc = event.get("awayScore", {}) or {}
    sets_played = tb = 0
    for key in ("period1", "period2", "period3", "period4", "period5"):
        h = home_sc.get(key)
        a = away_sc.get(key)
        if h is None or a is None:
            break
        try:
            if {int(h), int(a)} == {7, 6}:
                tb += 1
            sets_played += 1
        except (TypeError, ValueError):
            break
    return sets_played, tb


_RANKINGS_CACHE = {"data": None, "ts": 0.0}
_RANKINGS_TTL = 7 * 24 * 3600   # rankings update weekly


def get_current_rankings() -> dict:
    """Current ATP (rankings/type/5) + WTA (type/6) singles rankings as a
    {sofascore_player_id: ranking} lookup. Cached 7 days. Returns {} on total
    failure (callers degrade to the tournament-tier weight)."""
    now = time.time()
    cached = _RANKINGS_CACHE["data"]
    if cached is not None and (now - _RANKINGS_CACHE["ts"]) < _RANKINGS_TTL:
        return cached
    out = {}
    for rtype in (5, 6):   # 5 = ATP singles, 6 = WTA singles (confirmed live)
        try:
            d = _get(f"{BASE_URL}/rankings/type/{rtype}")
            for row in (d.get("rankings") or []):
                tid = (row.get("team") or {}).get("id")
                rk = row.get("ranking")
                if tid is not None and rk is not None:
                    out[int(tid)] = int(rk)
        except Exception as e:   # noqa: BLE001
            logger.warning("RANKINGS_FETCH_FAILED | type=%d | %s", rtype, str(e)[:120])
    if out:
        _RANKINGS_CACHE["data"] = out
        _RANKINGS_CACHE["ts"] = now
        logger.info("RANKINGS_LOADED | %d ranked players (ATP+WTA)", len(out))
        return out
    return cached or {}


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

    # Retirement / walkover (Imp 5): Sofascore status code 91 = Walkover,
    # 92 = Retired, 93 = Disqualified (description corroborates). The player who
    # did NOT win is the one who retired/withdrew → their own injury signal.
    _st = event.get("status", {}) or {}
    _st_desc = (_st.get("description") or "").lower()
    _is_dnf = (_st.get("code") in (91, 92, 93)
               or any(t in _st_desc for t in ("retir", "walkover", "default")))
    player_retired = bool(_is_dnf and not won)

    opp_team = event.get("awayTeam", {}) if side == "home" else event.get("homeTeam", {})

    # NOTE: surface intentionally omitted — caller (get_player_stats_by_surface)
    # sets surface via _infer_surface_from_event (groundType-aware) and we must
    # not overwrite it here with the weaker keyword-only inference.
    _sets_played, _tb_sets = _count_sets_and_tiebreaks(event)
    result = {
        "won":           won,
        "player_retired": player_retired,
        "sets_played":   _sets_played,
        "tiebreak_sets": _tb_sets,
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

    # ── Per-match service/return GAMES won (holds & breaks) ──────────────────
    # return games won = breaks = break points converted (winning a break point
    # wins the return game). service games won = total games won − breaks. These
    # feed the cleanest serve/return dominance measures (service/return games
    # won %), aggregated sum/sum in _agg_split / _agg.
    _tgw    = result.get("total_games_won")
    _sg     = result.get("service_games")
    _rg     = result.get("return_games")
    _breaks = result.get("bp_converted_count")
    if (_tgw is not None and _sg is not None and _rg is not None
            and _breaks is not None and _sg > 0 and _rg > 0):
        _rgw = max(0.0, min(_breaks, _rg))      # breaks can't exceed return games
        _sgw = _tgw - _rgw                       # holds = total games won − breaks
        if 0.0 <= _sgw <= _sg:                   # identity sanity check
            result["return_games_won"]  = _rgw
            result["service_games_won"] = _sgw

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


# ── Durable cache helpers (scopes 2/3 and 3/3) ───────────────────────────────
# Postgres is the durability layer behind the in-process caches; memory stays the
# hot path. Each helper swallows every DB error — a Postgres problem must cost
# cache warmth and nothing else, never a request.
#
# A note on SIZE, because these two scopes differ sharply:
#   • event statistics — small per row, IMMUTABLE, no TTL. Rows accumulate, which
#     is the whole point: a match played last month should never be refetched.
#   • player surface aggregates — LARGE (all_matches can run to hundreds of
#     entries plus per-surface copies). The memory key carries a 6h bucket, but
#     the DB key deliberately does NOT: a bucketed DB key would mint a new row
#     every 6 hours per player and never reread the old ones, growing without
#     bound. One stable row per player, overwritten, with a 6h TTL gives the same
#     freshness semantics and a bounded table.
_PLAYER_SURFACE_DB_MAX_BYTES = 4 * 1024 * 1024   # skip absurd payloads


def _event_stats_from_db(eid):
    try:
        from src import database
        return database.cache_get(f"ss_stats_{eid}")
    except Exception:  # noqa: BLE001
        return None


def _event_stats_to_db(eid, data) -> None:
    try:
        from src import database
        database.cache_set(f"ss_stats_{eid}", data, ttl_seconds=None)   # immutable
    except Exception:  # noqa: BLE001
        pass


def _surface_db_key(pid: int) -> str:
    return f"ss_surface_v6_{pid}"      # NO bucket — see the note above


def _player_surface_from_db(pid: int):
    try:
        from src import database
        return database.cache_get(_surface_db_key(pid))
    except Exception:  # noqa: BLE001
        return None


def _player_surface_to_db(pid: int, surfaces: dict) -> None:
    try:
        import json as _json
        from src import database
        _size = len(_json.dumps(surfaces))
        if _size > _PLAYER_SURFACE_DB_MAX_BYTES:
            logger.info("SURFACE_DB_SKIP | pid=%d | payload %.1fMB exceeds the "
                        "%.0fMB cap — memory-only", pid, _size / 1e6,
                        _PLAYER_SURFACE_DB_MAX_BYTES / 1e6)
            return
        database.cache_set(_surface_db_key(pid), surfaces,
                           ttl_seconds=_CACHE_BUCKET_SECS)
    except Exception:  # noqa: BLE001
        pass


def _fetch_stats_parallel(event_ids: list) -> dict:
    """
    Fetch event statistics for multiple events concurrently (up to 10 at once).
    Returns {event_id: stats_dict}. Checks st.session_state cache before any request.
    Each thread uses its own curl_cffi Session sharing the current sticky proxy port.
    Results are collected in the main thread before updating session state.
    """
    results: dict = {}
    uncached: list = []

    # Cache check before any network activity.
    # TRUTHY, not `is not None`: a failed fetch used to be stored as {} and {} is
    # not None, so one transient proxy failure permanently removed that event from
    # the stat-rich set for the life of the session — the count could only ever go
    # DOWN. A completed match's statistics are immutable, so a previously-fetched
    # event stat must never be lost to a later failure; only real payloads are
    # cached (see the write below) and anything else is retried.
    for eid in event_ids:
        cached = st.session_state.get(f"ss_stats_{eid}")
        if cached:
            results[eid] = cached
            continue
        # Durable layer (scope 3/3). A COMPLETED match's statistics are immutable,
        # so these rows have NO TTL — once fetched, correct forever. This is the
        # highest-value cache to persist: event stats are what stat_matches is
        # built from, which drives the stat-rich counts, which drive n / o_n /
        # p2_n and deep status. Losing them on deploy is what made those counts
        # reset and made cross-deploy reproducibility unobservable.
        durable = _event_stats_from_db(eid)
        if durable:
            results[eid] = durable
            st.session_state[f"ss_stats_{eid}"] = durable   # hydrate memory
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

    _ok = _fail = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for eid, data in ex.map(_fetch_one, uncached):
            results[eid] = data
            # Update session state from main thread (map blocks until all done).
            # ONLY cache a real payload. Caching {} on failure is what made the
            # stat-rich count drift downward and never recover; leaving a failure
            # uncached lets the next run retry it, so the count converges UP toward
            # the true value instead of oscillating around it.
            if data and data.get("statistics"):
                st.session_state[f"ss_stats_{eid}"] = data
                # GUARD BEFORE WRITE-THROUGH: only a payload with real statistics
                # reaches here — a failed/empty fetch takes the else branch and is
                # written NOWHERE, so it can overwrite neither memory nor a healthy
                # Postgres row. Same rule as the in-memory guard, applied durably.
                _event_stats_to_db(eid, data)
                _ok += 1
            else:
                _fail += 1
    if _fail:
        logger.info(
            "STATS_FETCH | %d/%d newly fetched OK, %d failed (left UNCACHED for retry "
            "— cached events are kept, so the stat-rich count only converges upward)",
            _ok, len(uncached), _fail,
        )
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
        "opp_aces",   # opponent aces per match = this player's ACES ALLOWED
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

    # Service / Return games won % (sum/sum) + BP generated per match — so the
    # "All" (career) and per-surface basic dicts carry the dominance measures.
    _sgw = [(m["service_games_won"], m["service_games"]) for m in matches
            if m.get("service_games_won") is not None and m.get("service_games")]
    if _sgw:
        _w, _p = sum(w for w, _ in _sgw), sum(p for _, p in _sgw)
        agg["service_games_won_pct"] = round(_w / _p * 100, 2) if _p > 0 else None
    _rgw = [(m["return_games_won"], m["return_games"]) for m in matches
            if m.get("return_games_won") is not None and m.get("return_games")]
    if _rgw:
        _w, _p = sum(w for w, _ in _rgw), sum(p for _, p in _rgw)
        agg["return_games_won_pct"] = round(_w / _p * 100, 2) if _p > 0 else None
    _bpg = [m["return_bp_opportunities"] for m in matches
            if m.get("return_bp_opportunities") is not None]
    if _bpg:
        agg["bp_generated_per_match"] = round(sum(_bpg) / len(_bpg), 4)
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

    # NOTE: do NOT short-circuit search on _search_blocked. That global flag is
    # also tripped by player-stats 403s, so honoring it here kills search even
    # when 50 fresh IPs are available to retry. Search retries through blocks
    # itself (fast=False below), rotating ports on each 403.

    # New search = new sticky proxy session + fresh Decodo session ID
    _new_session(force_port=True)

    # Warmup removed: x-requested-with header handles auth; cookie warmup
    # was eating 1-3s of the 10s frontend budget for no benefit.

    # Light throttle to avoid rate detection — keep it short so slow proxy
    # responses don't push the total past the frontend's 10s wall-clock limit.
    _search_throttle()

    logger.info("SEARCH_SOFASCORE | query=%r tour=%s", query, tour)
    # fast=False so search retries + rotates ports through Sofascore's
    # intermittent 403s (same way the player-stats fetch gets through), instead
    # of giving up on the first block. Slower, but it actually resolves players.
    data = _get(f"{BASE_URL}/search/all", {"q": query}, fast=False)

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


# ── Per-opponent surface hold cache (BP quality-of-server weighting) ──────────
# (player_id, surface) -> (hold_pct, fetched_ts). Hold ≈ service-games-won % on
# the surface — a stable trait, so a long TTL keeps the BP quality adjustment
# cheap after the first warm-up.
_SURFACE_HOLD_CACHE: dict = {}
_SURFACE_HOLD_TTL = 7 * 24 * 3600   # 7 days


def _hold_db_key(pid: int, surface: str) -> str:
    return f"surface_hold_v1_{pid}_{surface}"


def peek_surface_hold(player_id, surface: str):
    """Cached surface hold % for a player, or None if not cached/fresh. NEVER
    triggers a network fetch — for cache-first batch lookups.

    Two layers: memory (hot path) then Postgres (durability). Postgres is read
    ONLY on a memory miss — lazily, per key, no bulk load at boot — and a hit
    hydrates memory so subsequent reads never touch the DB.

    Why the DB layer exists: this cache used to live only in the process, so every
    deploy wiped it. Measured across a push: opponents resolved went 5/7 -> 0/7,
    and the BP quality adjustment (a pure function of cache state) moved with it.
    Cache warmth was being destroyed by the act of shipping."""
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return None
    key = (pid, surface)
    hit = _SURFACE_HOLD_CACHE.get(key)
    if hit and (time.time() - hit[1]) < _SURFACE_HOLD_TTL:
        return hit[0]
    # Memory miss -> try the durable layer once, then hydrate memory from it.
    try:
        from src import database
        val = database.cache_get(_hold_db_key(pid, surface))
    except Exception:  # noqa: BLE001 — durability must never break a read
        val = None
    if val is not None:
        _SURFACE_HOLD_CACHE[key] = (val, time.time())
        return val
    return None


def get_player_surface_hold(player_id, surface: str, tour: str = "ATP"):
    """Opponent serve quality ≈ their service-games-won % on the surface. Cached
    7 days; falls back surface → All. Returns float % or None. On a cache MISS
    this fetches full history (heavy) — callers budget/parallelise the misses."""
    cached = peek_surface_hold(player_id, surface)
    if cached is not None:
        return cached
    try:
        data = get_player_stats_by_surface(str(player_id), tour) or {}
    except Exception as e:
        logger.info("SURF_HOLD_FETCH_FAIL | pid=%s surf=%s err=%s", player_id, surface, e)
        return None
    hold = (data.get(surface) or {}).get("service_games_won_pct")
    if hold is None:
        hold = (data.get("All") or {}).get("service_games_won_pct")
    # GUARD BEFORE WRITE-THROUGH. `hold is not None` IS the guard for this cache:
    # a degraded fetch yields None (no stats parsed -> no service_games_won_pct on
    # either the surface or the All fallback), and None is never written. So a
    # degraded fetch can overwrite neither memory nor a healthy Postgres row — the
    # same rule the surface-stats guard enforces, applied to the durable layer.
    if hold is not None:
        try:
            pid = int(player_id)
        except (TypeError, ValueError):
            return hold
        _SURFACE_HOLD_CACHE[(pid, surface)] = (hold, time.time())
        try:
            from src import database
            database.cache_set(_hold_db_key(pid, surface), hold,
                               ttl_seconds=_SURFACE_HOLD_TTL)
        except Exception:  # noqa: BLE001 — a DB problem costs warmth, nothing else
            pass
    return hold


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

    # Durable layer (scope 2/3): read ONCE on a memory miss, then hydrate memory
    # so the rest of this bucket never touches the DB again. TTL is enforced
    # inside cache_get, so a row that merely survived a restart cannot be served
    # stale — an expired row is a miss and we refetch below.
    _durable = _player_surface_from_db(pid)
    if _durable:
        logger.info("SURFACE_DB_HIT | pid=%s | hydrated from Postgres (survived "
                    "restart) — no refetch", pid)
        st.session_state[cache_key] = _durable
        record_cache_hit()
        return _durable

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

    # DETERMINISTIC SELECTION — sort newest-first BEFORE the [:50] cap so the 50
    # events we attempt are a pure function of the player's match history, not of
    # the order the paginated API happened to return them in. Without this, two
    # runs can attempt two different 50-event subsets of the same history and
    # legitimately produce different stat-rich counts. Ties break on event id so
    # the order is total (same-timestamp events can't reshuffle between runs).
    valid.sort(key=lambda e: ((e.get("startTimestamp") or 0), (e.get("id") or 0)),
               reverse=True)
    logger.info("[STATS_FLOW] VALID_EVENTS | valid=%d (finished singles, sorted "
                "newest-first before the %d-event stats cap)", len(valid), 50)

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
            "opponent_id":       opp.get("id"),   # for per-opponent hold-quality weighting
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

    # ── DEGRADED-FETCH CACHE GUARD ───────────────────────────────────────────
    # Events can succeed (hundreds of matches found) while the per-match
    # statistics calls come back empty — a transient stats-API / proxy failure.
    # The records then carry score/opponent/timestamp but NO aces/DF/BP, so every
    # stat-driven guard downstream sees a near-empty player.
    #
    # The old guard was `if stat_matches:` — a truthiness test that only rejected
    # ZERO. A Decodo outage produced 1 stat-rich match out of 50 requested for a
    # player who really has ~38; that 1 passed the test and poisoned the cache for
    # the whole bucket, and every confidence score computed against it was wrong.
    #
    # So compare the stat-rich yield against what we actually ASKED for, and
    # additionally never let a degraded fetch overwrite a healthy prior snapshot.
    _requested = len(event_ids)
    _got = len(stat_matches)
    _yield = (_got / _requested) if _requested else 0.0

    # Best prior snapshot for this player, from any earlier bucket.
    healthy, healthy_bucket = None, -1
    _prefix = f"ss_surface_v6_{pid}_"
    for k in list(st.session_state.keys()):
        if not k.startswith(_prefix) or k == cache_key:
            continue
        try:
            b = int(k.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            continue
        cv = st.session_state.get(k) or {}
        if (cv.get("_stat_match_count") or 0) > _got and b > healthy_bucket:
            healthy, healthy_bucket = cv, b
    _prior_n = (healthy.get("_stat_match_count") or 0) if healthy is not None else None

    # Two independent triggers, in confidence order:
    #  1. COLLAPSE vs a prior healthy snapshot — the strongest signal. The player
    #     demonstrably had coverage before, so a sudden drop is our failure, not
    #     the player's history changing.
    #  2. ABSOLUTE floor — only used when there's no prior snapshot to compare
    #     against. Deliberately low: a genuine ITF-only player really can have
    #     almost no stats on Sofascore, and treating that as degraded would mean
    #     never caching them and re-fetching (burning proxy) on every request.
    _degraded, _why = False, ""
    if _requested >= _DEGRADED_MIN_REQUESTED:
        if _prior_n is not None and _got < _prior_n * _DEGRADED_VS_PRIOR_RATIO:
            _degraded = True
            _why = ("coverage collapsed vs prior healthy snapshot (%d -> %d stat-rich)"
                    % (_prior_n, _got))
        elif _prior_n is None and _yield < _DEGRADED_MIN_YIELD:
            _degraded = True
            _why = ("stat yield %.0f%% below %.0f%% floor and no prior snapshot"
                    % (_yield * 100, _DEGRADED_MIN_YIELD * 100))

    if _degraded:
        logger.error(
            "SURFACE_DEGRADED_FETCH | pid=%d | stat-rich=%d/%d requested | %d events | "
            "%s | NOT caching as authoritative | %s",
            pid, _got, _requested, len(all_match_stats), _why,
            ("serving healthy bucket %d (%d stat-rich)" % (healthy_bucket, _prior_n))
            if healthy is not None else "no healthy prior snapshot — returning degraded UNCACHED",
        )
        if healthy is not None:
            healthy = dict(healthy)
            healthy["_stale_cache"] = True
            healthy["_degraded_refetch"] = True
            return healthy
        # No prior snapshot: return the degraded result so the caller still gets
        # SOMETHING, but do NOT cache it — the next request re-fetches and can
        # recover as soon as the proxy/stats API is healthy again.
        surfaces["_degraded_fetch"] = True
        return surfaces

    # Reached ONLY past the degraded-fetch guard above — every degraded branch
    # returns early, so a degraded snapshot can overwrite neither memory nor a
    # healthy Postgres row. Guard first, write-through second.
    st.session_state[cache_key] = surfaces
    _player_surface_to_db(pid, surfaces)
    return surfaces


def get_player_titles(player_id, tour: str = "ATP") -> dict:
    """Career tournament titles, derived from FINALS WON in the player's event
    history (Sofascore has no direct titles endpoint). Returns
    {tournament_name: times_won} sorted by count desc — only tournaments won at
    least once. Cached 48h (title counts change rarely). {} on failure.
    """
    pid = int(player_id)
    cache_key = f"ss_titles_{pid}_{int(time.time()) // (48 * 3600)}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    titles: dict = {}
    try:
        events = _get_player_recent_events(pid)   # full history, cached 6h
        for e in events:
            if (e.get("status") or {}).get("type", "") not in ("finished", "ended"):
                continue
            ri = e.get("roundInfo") or {}
            if (ri.get("slug") or "").lower() != "final":     # championship match only
                continue
            ht, at = (e.get("homeTeam") or {}), (e.get("awayTeam") or {})
            hn, an = ht.get("name", ""), at.get("name", "")
            if "/" in hn or "/" in an:                        # skip doubles
                continue
            # Tour-level titles only (ATP / WTA / Grand Slam = tier 3.0); drop
            # Challenger / ITF Futures / exhibitions so the section means "titles"
            # in the headline sense. A player with only lower-tier wins shows none.
            if _competition_tier(e) < 2.5:
                continue
            tname = (e.get("tournament") or {}).get("name", "")
            ut = ((e.get("tournament") or {}).get("uniqueTournament", {}) or {}).get("name") \
                or (e.get("uniqueTournament") or {}).get("name") or tname
            if not ut:
                continue
            if "qualif" in (f"{tname} {ut} {ri.get('name','')}").lower():  # not a qual final
                continue
            # Did THIS player win the final? winnerCode 1=home, 2=away.
            side = "home" if ht.get("id") == pid else "away"
            wc = e.get("winnerCode")
            if (wc == 1 and side == "home") or (wc == 2 and side == "away"):
                titles[ut] = titles.get(ut, 0) + 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_player_titles failed pid=%s: %s", pid, exc)
        titles = {}

    sorted_titles = dict(sorted(titles.items(), key=lambda kv: (-kv[1], kv[0])))
    st.session_state[cache_key] = sorted_titles
    return sorted_titles


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
        # team/{id}/events/next/0 404s for tennis players whose upcoming match
        # isn't yet in the team "next" feed, but near-events returns it reliably
        # as `nextEvent` (alongside the most recent `previousEvent`).
        data = _get(f"{BASE_URL}/team/{pid}/near-events")
        ev = (data or {}).get("nextEvent") or {}
        now = time.time()
        ts = ev.get("startTimestamp", 0) or 0
        ht = (ev.get("homeTeam") or {}).get("name", "")
        at = (ev.get("awayTeam") or {}).get("name", "")
        is_doubles = "/" in ht or "/" in at
        if ev and ts >= now - 6 * 3600 and not is_doubles:
            home_id = (ev.get("homeTeam") or {}).get("id")
            opp = ev.get("awayTeam" if home_id == pid else "homeTeam", {}) or {}
            result = {
                "tournament": (ev.get("tournament") or {}).get("name", ""),
                "surface": _infer_surface_from_event(ev),
                "opponent_name": opp.get("name", ""),
                "opponent_id": opp.get("id"),
                "start_timestamp": ts,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("next-match fetch failed for pid=%s: %s", pid, exc)
        result = {}

    if result.get("tournament"):
        st.session_state[cache_key] = result
    return result


# Sofascore tennis CATEGORY ids — the per-tour scheduled-events endpoints
# (category/{id}/scheduled-events/{date}) still work where the sport-level one
# now 404s. ATP main tour = 3, WTA main tour = 6.
_TENNIS_CATEGORY_IDS = {"ATP": 3, "WTA": 6}

# Last successful scheduled-events result per date — survives the hourly cache
# bucket so a slow/failed refresh can serve stale data instead of an empty slate.
_SCHED_LAST_GOOD: dict = {}


def get_scheduled_events(date_str: str = "", tours=("ATP", "WTA")) -> list:
    """Today's (or a given date's) scheduled ATP/WTA SINGLES matches from
    Sofascore. Returns a list of normalized dicts:
        {tour, tournament, surface, p1_name, p1_id, p2_name, p2_id,
         start_timestamp, status}
    Cached 1h (scheduled matches barely change intraday). Empty list on failure.
    Used by the slate (Feature 4) and court report (Feature 7) — never raises.
    """
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    # 5-minute cache (was 1h): which matches exist barely changes, but their
    # STATUS (upcoming → live → finished) does, and the slate must reflect the
    # current state when it's run rather than a stale snapshot.
    cache_key = f"ss_sched_{date_str}_{int(time.time()) // 300}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    out: list = []
    _last_good = _SCHED_LAST_GOOD.get(date_str)
    try:
        # NOTE: the sport-level /sport/tennis/scheduled-events/{date} endpoint now
        # 404s — fetch per-tour via the CATEGORY endpoints instead, which return
        # exactly that tour's main-draw events (ATP=3, WTA=6).
        for tname in tours:
            cid = _TENNIS_CATEGORY_IDS.get(tname.upper())
            if not cid:
                continue
            data = _get(f"{BASE_URL}/category/{cid}/scheduled-events/{date_str}")
            for e in (data.get("events", []) or []):
                ht = (e.get("homeTeam") or {})
                at = (e.get("awayTeam") or {})
                hn, an = ht.get("name", ""), at.get("name", "")
                if not hn or not an or "/" in hn or "/" in an:   # need both, singles only
                    continue
                out.append({
                    "tour": tname.upper(),
                    "tournament": (e.get("tournament") or {}).get("name", ""),
                    "surface": _infer_surface_from_event(e),
                    "p1_name": hn, "p1_id": ht.get("id"),
                    "p2_name": an, "p2_id": at.get("id"),
                    "start_timestamp": e.get("startTimestamp", 0) or 0,
                    "status": ((e.get("status") or {}).get("type") or ""),
                })
        out.sort(key=lambda m: m.get("start_timestamp", 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduled-events fetch failed for %s: %s", date_str, exc)
        out = []

    if out:
        st.session_state[cache_key] = out
        _SCHED_LAST_GOOD[date_str] = out
    elif _last_good:
        # A cold/slow fetch returned nothing — serve the last good result rather
        # than an empty "unavailable" slate.
        logger.info("scheduled-events: serving last-good for %s (%d events)",
                    date_str, len(_last_good))
        out = _last_good
    return out


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


def find_void_match(player_id, opponent_name: str, days: int = 4) -> dict:
    """Detect a recent match vs ``opponent_name`` that was CANCELLED or WALKED
    OVER — i.e. definitively never going to be played — within ``days`` of now.
    Used to void (DNP) a pick whose match didn't happen instead of leaving it to
    hang as NEEDS REVIEW. Returns {'date', 'status'} or {}. Never raises.

    Sofascore status: code 60 = Canceled, 91 = Walkover; the status.type /
    description corroborate. POSTPONED (code 70) is deliberately NOT voided — a
    postponed match is only rescheduled, so the pick stays pending until it
    actually plays. We check both the last (past) and next (upcoming) event
    feeds since a cancellation can sit in either."""
    try:
        opp = re.sub(r"[^a-z ]", " ", (opponent_name or "").lower()).strip()
        opp_last = opp.split()[-1] if opp else ""
        if not opp_last:
            return {}
        now = time.time()
        events: list = []
        for feed, pages in (("last", (0, 1)), ("next", (0,))):
            for page in pages:
                try:
                    d = _get(f"{BASE_URL}/team/{player_id}/events/{feed}/{page}")
                    events += (d or {}).get("events", [])
                except Exception:  # noqa: BLE001
                    break
        for e in events:
            ts = e.get("startTimestamp", 0) or 0
            if ts and abs(now - ts) > days * 86400:
                continue
            stt = e.get("status") or {}
            code = stt.get("code")
            stype = (stt.get("type") or "").lower()
            desc = (stt.get("description") or "").lower()
            # Canceled / walkover only — NOT postponed (which is just rescheduled).
            is_void = (code in (60, 91)
                       or stype in ("canceled", "cancelled")
                       or any(k in desc for k in ("cancel", "walkover", "w/o", "walk over")))
            if not is_void:
                continue
            names = (((e.get("homeTeam") or {}).get("name") or "") + " "
                     + ((e.get("awayTeam") or {}).get("name") or "")).lower()
            if opp_last in names:
                logger.info("VOID_DETECT | pid=%s vs %r | status=%r code=%s",
                            player_id, opponent_name, stype or desc, code)
                return {"date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
                        "status": stype or desc or "cancelled"}
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("find_void_match failed: %s", exc)
        return {}


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
    # stat_n / games_n are the MEETING COUNTS behind the averages. They were
    # computed but not returned, so no caller could sample-gate the H2H blends —
    # a single sparse meeting carried the same weight as five (see the H2H gates
    # in props.py). Returning them is what makes gating possible.
    empty = {"ace": None, "df": None, "games_avg": None, "stat_n": 0, "games_n": 0}
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

    logger.info(
        "H2H_STAT_AVG | %s vs %s | surface=%s | meetings=%d | stat-rich=%d "
        "(ace/df/bp basis) | with-games=%d | ace=%s df=%s bp=%s games=%s",
        p1, p2, surface or "any", len(h2h), n, games_n,
        round(ace_sum / n, 2) if n else None,
        round(df_sum / n, 2) if n else None,
        round(bp_sum / n, 2) if n else None,
        round(games_sum / games_n, 1) if games_n else None,
    )
    return {
        "ace":       round(ace_sum  / n,       2) if n       else None,
        "df":        round(df_sum   / n,       2) if n       else None,
        "bp":        round(bp_sum   / n,       2) if n       else None,
        "games_avg": round(games_sum / games_n, 1) if games_n else None,
        # Meeting counts behind each average — the sample-gate inputs.
        # stat_n  : meetings with PARSED statistics (ace/df/bp basis)
        # games_n : meetings with a total-games count (score-derived; needs no
        #           statistics call, so it is usually >= stat_n)
        "stat_n":    n,
        "games_n":   games_n,
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
