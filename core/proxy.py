"""
Decodo residential proxy — shared plumbing, opt-in per sport.

WHY A SPORT NEEDS THIS AT ALL
-----------------------------
Public sports APIs block datacenter IP ranges. The symptom is the worst kind:
everything works on a laptop and fails only in the container, so it survives
every local test and reaches production intact. NFL hit it twice —

    ESPN scoreboard    403 Forbidden from Railway, 200 from a laptop
    PrizePicks         429 Cloudflare 1015 after a handful of calls

— and the tennis side solved the same problem for Sofascore years ago with a
Decodo account that is already paid for and already running.

WHAT THIS IS NOT
----------------
It is not jurisdiction spoofing and it is not authentication bypass. Every
endpoint routed through here is a PUBLIC, read-only, unauthenticated feed that
we are already entitled to read; the proxy exists because the host declines
datacenter ranges, not because it declines us. Nothing here defeats a paywall,
a login, or a geographic licensing control, and it must not be extended to.

WIRED FOR: nfl/ (client + lines). DELIBERATELY NOT MLB — the user asked to leave
it out for now, and MLB currently reaches its feeds without help. Nothing here
imports a sport module, so adding MLB later is one call site, not a refactor.

CONFIG — the SAME environment variables the tennis client already uses, so one
Decodo account and one set of credentials serve both:

    PROXY_HOST       gate.decodo.com
    PROXY_USERNAME   account user
    PROXY_PASSWORD   account password
    PROXY_PORT_LIST  "10001-10050" or "10001,10002,10010-10020"

DISABLED BY DEFAULT IN THE SENSE THAT MATTERS: with no credentials configured,
every function here returns None and callers fall through to a direct request.
A missing proxy must degrade to the current behaviour, never to an exception.
"""

import logging
import os
import random
import time

log = logging.getLogger("baseline.core.proxy")

HOST = os.getenv("PROXY_HOST", "gate.decodo.com")
USER = os.getenv("PROXY_USERNAME", "")
PASS = os.getenv("PROXY_PASSWORD", "")

# Per-sport master switch. NFL defaults ON because two of its feeds are known to
# block datacenter IPs; anything else must opt in explicitly.
NFL_ENABLED = os.getenv("NFL_USE_PROXY", "1").strip().lower() in (
    "1", "true", "yes", "on")

# How long a port that returned 407/403 is avoided.
BAD_PORT_COOLDOWN = 600
_bad_ports = {}


def _parse_port_list(spec: str) -> list:
    """Comma list AND/OR 'start-end' ranges — same parser as the tennis client,
    so one PROXY_PORT_LIST value configures both."""
    out = []
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


PORTS = _parse_port_list(os.getenv("PROXY_PORT_LIST", ""))


def configured() -> bool:
    """True when there is enough config to attempt a proxied request."""
    return bool(USER and PASS and PORTS)


def _choose_port():
    if not PORTS:
        return None
    now = time.time()
    for p in [k for k, t in list(_bad_ports.items())
              if now - t > BAD_PORT_COOLDOWN]:
        _bad_ports.pop(p, None)
    live = [p for p in PORTS if p not in _bad_ports] or list(PORTS)
    return random.choice(live)


def mark_bad(port) -> None:
    """Take a port out of rotation for a while (407, or a persistent block)."""
    if port:
        _bad_ports[port] = time.time()


def proxy_url(port=None, session_id: str = None):
    """A proxies-dict value, or None when the proxy is not configured.

    `session_id` uses Decodo's sticky-session username so each caller gets its
    own rotating residential IP rather than sharing the port's static one.
    """
    if not configured():
        return None
    port = port or _choose_port()
    if not port:
        return None
    user = f"{USER}-session-{session_id}" if session_id else USER
    return f"http://{user}:{PASS}@{HOST}:{port}", port


def proxies_for(sport: str, session_id: str = None):
    """({"http": url, "https": url}, port) for a sport, or (None, None).

    Callers pass the result straight to requests. (None, None) means "go
    direct" — every call site must treat that as normal, because it is the
    state on any machine without proxy credentials.
    """
    if sport == "nfl" and not NFL_ENABLED:
        return None, None
    if sport == "mlb":
        # Left out on purpose — see the module docstring.
        return None, None
    got = proxy_url(session_id=session_id)
    if not got:
        return None, None
    url, port = got
    return {"http": url, "https": url}, port


def get(url, sport: str, session_id: str = None, retries: int = 2, **kw):
    """requests.get through the sport's proxy, falling back to DIRECT.

    Tries the proxy first when configured, rotating ports on failure, and then
    makes one direct attempt. The direct attempt is not a formality: it is what
    keeps a dev machine (no credentials) and a proxy outage both working.

    Returns a Response, or None when every attempt failed. Never raises.
    """
    import requests
    last = None
    for attempt in range(max(1, retries)):
        px, port = proxies_for(sport, session_id=session_id)
        if not px:
            break
        try:
            r = requests.get(url, proxies=px, **kw)
            if r.status_code in (403, 407, 429):
                log.warning("proxy port %s got %s for %s — rotating",
                            port, r.status_code, url[:80])
                mark_bad(port)
                last = r
                continue
            return r
        except Exception as exc:  # noqa: BLE001
            log.warning("proxy port %s failed (%s) — rotating", port,
                        str(exc)[:120])
            mark_bad(port)
    try:
        return requests.get(url, **kw)
    except Exception as exc:  # noqa: BLE001
        log.warning("direct request failed for %s: %s", url[:80], str(exc)[:120])
        return last


def status() -> dict:
    """What is configured, for a health check. Never returns the password."""
    return {"host": HOST, "username_set": bool(USER), "password_set": bool(PASS),
            "ports": len(PORTS), "configured": configured(),
            "nfl_enabled": NFL_ENABLED,
            "bad_ports": sorted(_bad_ports.keys())}
