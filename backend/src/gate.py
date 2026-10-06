"""The member gate — who may call what (operator ruling, 2026-10-06).

Three classes of route:

  PUBLIC      no credentials: the landing/welcome data (results summary),
              billing config + checkout + webhook, auth, sports config,
              health, docs, and routes that verify a session themselves
              (billing portal/status, account delete).
  RESTRICTED  machines only: the bot's result/board ingest and resolve
              routes, and every admin/diagnostic route. Allowed with the
              service token (core/service_token) or the admin token. Never a
              member session.
  PREMIUM     everything else under /api — the member product. Allowed with
              the service token, or with a Bearer session whose membership is
              active (the same check /api/auth/me makes).

THE SWITCH. Enforcement is OFF unless PREMIUM_ENFORCE=1, and can be flipped
instantly, without a deploy, through POST /api/admin/enforce (admin token):
the override lives in the durable cache and is re-read every 20 seconds.
With enforcement off nothing changes for anyone — the gate only records.

The website sends its session on every call (utils/api.js interceptor), the
app attaches it in lib/api.ts, and every bot/publisher call carries the
service token — so turning this on is a change of posture, not of behaviour,
for every legitimate caller.
"""
import hashlib
import hmac
import logging
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("baseline.gate")

ENV_FLAG = "PREMIUM_ENFORCE"
OVERRIDE_KEY = "premium_enforce_override"
OVERRIDE_TTL_S = 20

PUBLIC_EXACT = {
    "/", "/health", "/api/health",
    "/api/results/summary", "/api/results/health",
    "/api/sports",
    "/api/billing/config", "/api/billing/checkout", "/api/billing/webhook",
    "/api/billing/claim", "/api/billing/trial-check", "/api/billing/status",
    "/api/billing/portal",            # verifies its own session
    "/api/account/delete",            # verifies its own session
    "/api/player/image",              # a picture, same-origin for the website
    "/api/admin/enforce",             # admin-token checked in the handler
}
PUBLIC_PREFIX = ("/api/auth/", "/docs", "/openapi", "/redoc")

RESTRICTED_PREFIX = (
    "/api/results/audit", "/api/results/bias", "/api/results/exclude", "/api/results/setline",
    "/api/results/log", "/api/results/update", "/api/results/resolve", "/api/results/pending",
    "/api/results/observe-line",
    "/api/nfl/results/log", "/api/nfl/results/pending", "/api/nfl/results/update",
    "/api/nba/results/log", "/api/nba/results/pending", "/api/nba/results/update",
    "/api/nba/results/exclude",
    "/api/billing/subscribers", "/api/billing/ip-history", "/api/billing/resync",
    "/api/search/debug", "/api/search/probe", "/api/proxy/", "/api/cache/clear",
    "/api/nba/diag", "/api/preview/", "/api/odds/", "/api/admin/", "/api/push/send",
)
# Method-specific restricted routes: the bot's board/profile pushes share a
# path with the member-facing GETs.
RESTRICTED_METHOD = (
    ("POST", "/api/nfl/board"), ("POST", "/api/nfl/players"), ("POST", "/api/nba/board"),
    ("DELETE", "/api/results/"),
)


def classify(method: str, path: str) -> str:
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
        return "public"
    for m, p in RESTRICTED_METHOD:
        if method == m and path.startswith(p):
            return "restricted"
    if path.startswith(RESTRICTED_PREFIX):
        return "restricted"
    if path.startswith("/api/"):
        return "premium"
    return "public"


# ── the switch ───────────────────────────────────────────────────────────────
_override = {"at": 0.0, "val": None}


def _env_on() -> bool:
    return (os.getenv(ENV_FLAG, "0") or "0").strip().lower() in ("1", "true", "yes", "on")


def enforce_on() -> bool:
    now = time.time()
    if now - _override["at"] > OVERRIDE_TTL_S:
        try:
            from . import database
            v = database.cache_get(OVERRIDE_KEY)
            _override["val"] = (v.get("on") if isinstance(v, dict) else None)
        except Exception:  # noqa: BLE001
            _override["val"] = None
        _override["at"] = now
    if isinstance(_override["val"], bool):
        return _override["val"]
    return _env_on()


def set_override(on) -> bool:
    """None clears the override (back to the env default)."""
    try:
        from . import database
        if on is None:
            database.cache_set(OVERRIDE_KEY, {"on": None}, ttl_seconds=1)
            _override["val"] = None
        else:
            database.cache_set(OVERRIDE_KEY, {"on": bool(on)})
            _override["val"] = bool(on)
        _override["at"] = time.time()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate: could not set override: %s", exc)
        return False


def status() -> dict:
    return {"enforcing": enforce_on(), "env_default": _env_on(),
            "override": _override["val"], "service_token_set": bool(os.getenv("BASELINE_SERVICE_TOKEN"))}


# ── credentials ──────────────────────────────────────────────────────────────
def is_service(req: Request) -> bool:
    expected = (os.getenv("BASELINE_SERVICE_TOKEN") or "").strip()
    supplied = req.headers.get("x-service-token", "")
    return bool(expected) and hmac.compare_digest(supplied, expected)


def is_admin(req: Request) -> bool:
    for env, header in (("ADMIN_TOKEN", "x-admin-token"), ("OWNER_TOKEN", "x-owner-token")):
        expected = (os.getenv(env) or "").strip()
        supplied = req.headers.get(header, "")
        if expected and supplied and hmac.compare_digest(supplied, expected):
            return True
    return False


# Membership per session token, cached a minute: the role lookup behind it has
# its own five-minute cache, this just keeps a board scroll from re-asking.
_members: dict = {}
MEMBER_TTL_S = 60


def member_active(req: Request) -> tuple:
    """(authenticated, active) for the Bearer session on this request."""
    tok = req.headers.get("authorization", "").replace("Bearer ", "").strip()
    if not tok:
        return (False, False)
    h = hashlib.sha256(tok.encode()).hexdigest()
    hit = _members.get(h)
    if hit and (time.time() - hit[0]) < MEMBER_TTL_S:
        return hit[1]
    try:
        from . import discord_auth
        data = discord_auth.read_session(tok)
        if not data or data.get("sub") in (None, "", "state"):
            res = (False, False)
        elif (data.get("k") or "discord") == "email":
            res = (True, bool(discord_auth.access_for_email(str(data["sub"])).get("active")))
        else:
            res = (True, bool(discord_auth.access_for(str(data["sub"]), data.get("u", "")).get("active")))
    except Exception as exc:  # noqa: BLE001 — a lookup failure must not open the gate
        logger.warning("gate: membership check failed: %s", exc)
        res = (False, False)
    # Trim occasionally so the map cannot grow without bound.
    if len(_members) > 5000:
        _members.clear()
    _members[h] = (time.time(), res)
    return res


def forget(tok: str) -> None:
    try:
        _members.pop(hashlib.sha256(tok.encode()).hexdigest(), None)
    except Exception:  # noqa: BLE001
        pass


# ── the middleware ───────────────────────────────────────────────────────────
async def middleware(req: Request, call_next):
    if req.method == "OPTIONS":
        return await call_next(req)
    cls = classify(req.method, req.url.path)
    if cls == "public" or is_service(req):
        return await call_next(req)
    if cls == "restricted":
        if is_admin(req):
            return await call_next(req)
        if not enforce_on():
            return await call_next(req)
        return JSONResponse({"detail": "service or admin token required"}, status_code=401)
    # premium
    if not enforce_on():
        return await call_next(req)
    authed, active = member_active(req)
    if not authed:
        return JSONResponse({"detail": "sign in required"}, status_code=401)
    if not active:
        return JSONResponse({"detail": "membership required"}, status_code=403)
    return await call_next(req)
