"""
Discord sign-in and role-based app entitlement.

THE SERVER ROLE IS THE ENTITLEMENT. A member holding the premium role in the
Discord guild has app access; when the role goes away, so does access. Discord's
own server subscriptions grant and revoke a role automatically, so checking the
role covers native subscriptions, manually granted comps and Stripe-driven
grants without needing to know which produced it.

WHY OAUTH RATHER THAN "TELL US YOUR DISCORD ID": an id typed into a form is a
claim, not proof. The OAuth code exchange happens server-to-server against
Discord with our client secret, so the id that comes back is verified. That is
what makes the owner bypass safe here — the same bypass keyed on a
caller-supplied id would be a hole.

THIS REPLACES A CLIENT-SIDE PASSWORD CHECK. The old gate compared against
VITE_APP_PASSWORD in the browser, which ships the password inside the JS bundle
for anyone who opens devtools. It gated nothing; this actually does.

ROLES ARE RE-CHECKED, NOT TRUSTED FOR THE LIFE OF A SESSION. "Premium runs out →
access ends" only holds if we look again. The session cookie proves WHO you are;
entitlement is re-read from Discord on a short cache, so a lapse takes effect in
minutes rather than whenever a long-lived token happens to expire.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger("baseline.discord_auth")

CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "").strip()
REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "").strip()
APP_URL       = os.getenv("APP_URL", "https://baseline-app-three.vercel.app").strip()
SESSION_SECRET = os.getenv("APP_SESSION_SECRET", "").strip()

PREMIUM_ROLE_IDS = {r.strip() for r in
                    os.getenv("DISCORD_PREMIUM_ROLE_IDS", "").split(",") if r.strip()}
OWNER_DISCORD_IDS = {r.strip() for r in
                     os.getenv("BILLING_OWNER_DISCORD_IDS", "").split(",") if r.strip()}

SESSION_TTL = int(os.getenv("APP_SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
_ROLE_CACHE_TTL = 300          # 5 minutes — see the re-check note above
_API = "https://discord.com/api/v10"

_role_cache: dict = {}         # discord_id -> (checked_at, roles tuple)


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and BOT_TOKEN and GUILD_ID
                and SESSION_SECRET and REDIRECT_URI)


def config_status() -> dict:
    """Presence only — never the values."""
    return {
        "client_id_set": bool(CLIENT_ID),
        "client_secret_set": bool(CLIENT_SECRET),
        "bot_token_set": bool(BOT_TOKEN),
        "guild_id_set": bool(GUILD_ID),
        "redirect_uri_set": bool(REDIRECT_URI),
        "session_secret_set": bool(SESSION_SECRET),
        "premium_roles_configured": len(PREMIUM_ROLE_IDS),
        "owner_ids_configured": len(OWNER_DISCORD_IDS),
        "ready": is_configured(),
    }


# ── Signed sessions (no extra dependency, no server-side session store) ──────
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(discord_id: str, username: str = "") -> str:
    """HMAC-signed token: payload.signature.

    Signed, NOT encrypted — it carries no secret, only a user id the holder
    already knows. The signature is what matters: without the server secret a
    payload cannot be altered, so nobody can re-issue themselves someone else's
    session or extend their own expiry.
    """
    payload = {"sub": discord_id, "u": username, "iat": int(time.time())}
    raw = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).digest()
    return f"{raw}.{_b64e(sig)}"


def read_session(token: str) -> Optional[dict]:
    """Verify and decode a session token, or None. Constant-time comparison."""
    if not token or not SESSION_SECRET or "." not in token:
        return None
    raw, _, sig = token.partition(".")
    try:
        expect = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig), expect):
            return None
        data = json.loads(_b64d(raw))
    except Exception:  # noqa: BLE001
        return None
    if int(time.time()) - int(data.get("iat", 0)) > SESSION_TTL:
        return None
    return data


# ── OAuth ────────────────────────────────────────────────────────────────────
def login_url(state: str = "") -> str:
    """Discord authorize URL. `identify` and `guilds.members.read` only — we ask
    for the minimum needed to know who you are and whether you hold the role,
    and never for message or email scopes we have no use for."""
    return f"{_API}/oauth2/authorize?" + urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state or "",
        "prompt": "none",
    })


def exchange_code(code: str) -> Optional[dict]:
    """Trade an OAuth code for the user's identity. None on failure.

    Server-to-server with the client secret — this is the step that turns a
    claimed identity into a verified one.
    """
    if not is_configured():
        return None
    try:
        tok = requests.post(f"{_API}/oauth2/token", timeout=20, data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT_URI,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if tok.status_code != 200:
            logger.warning("oauth token exchange failed: %s", tok.status_code)
            return None
        access = (tok.json() or {}).get("access_token")
        if not access:
            return None
        me = requests.get(f"{_API}/users/@me", timeout=20,
                          headers={"Authorization": f"Bearer {access}"})
        if me.status_code != 200:
            return None
        u = me.json() or {}
        return {"id": str(u.get("id") or ""),
                "username": u.get("global_name") or u.get("username") or ""}
    except Exception:  # noqa: BLE001
        logger.exception("oauth exchange failed")
        return None


# ── Role lookup ──────────────────────────────────────────────────────────────
def _member_roles(discord_id: str) -> Optional[tuple]:
    """Roles this user holds in the guild. None means "could not determine".

    Read with the BOT token rather than the user's OAuth token: the bot sees the
    member object regardless of the user's own privacy settings, and it keeps
    working without the user having to re-authorise.

    None and () mean different things and callers must not conflate them: () is
    "in the guild, holds nothing", None is "we could not ask". A 404 is a real
    answer — not a member — and is returned as ().
    """
    now = time.time()
    hit = _role_cache.get(discord_id)
    if hit and now - hit[0] < _ROLE_CACHE_TTL:
        return hit[1]
    if not (BOT_TOKEN and GUILD_ID):
        return None
    try:
        r = requests.get(f"{_API}/guilds/{GUILD_ID}/members/{discord_id}",
                         timeout=20,
                         headers={"Authorization": f"Bot {BOT_TOKEN}"})
        if r.status_code == 404:
            roles = ()                      # not in the server at all
        elif r.status_code == 200:
            roles = tuple(str(x) for x in (r.json() or {}).get("roles", []))
        else:
            logger.warning("guild member lookup HTTP %s", r.status_code)
            return None
        _role_cache[discord_id] = (now, roles)
        return roles
    except Exception:  # noqa: BLE001
        logger.exception("guild member lookup failed")
        return None


def access_for(discord_id: str, username: str = "") -> dict:
    """Entitlement for a VERIFIED Discord id.

    Only ever call this with an id that came from exchange_code or a signed
    session. Passing in a user-supplied id would make the owner check forgeable.

    Resolution order, first match wins:
      1. owner            — always, unconditionally
      2. premium role in the guild
      3. active Stripe subscription (for people not in the server)
      4. otherwise denied

    Owner is checked first so the owner keeps access even if Discord's API is
    unreachable or the bot has been removed from the guild — being locked out of
    your own product by someone else's outage is not acceptable.
    """
    if discord_id and discord_id in OWNER_DISCORD_IDS:
        return {"active": True, "reason": "owner", "owner": True,
                "discord_id": discord_id, "username": username}
    roles = _member_roles(discord_id)
    if roles is None:
        # FAIL CLOSED for non-owners. An outage must not open the paywall.
        return {"active": False, "reason": "role_check_unavailable",
                "owner": False, "discord_id": discord_id, "username": username}
    has = bool(PREMIUM_ROLE_IDS and (set(roles) & PREMIUM_ROLE_IDS))
    if has:
        return {"active": True, "reason": "premium_role", "owner": False,
                "source": "discord", "discord_id": discord_id,
                "username": username}

    # NOT IN THE SERVER, OR IN IT WITHOUT THE ROLE -> a Stripe subscription is
    # the other way in. Everyone signs in with Discord regardless, because
    # having a Discord account is not the same as being in the guild, and one
    # identity system means a subscriber who later joins the server is the same
    # person to us rather than a duplicate.
    #
    # trusted_discord=True is CORRECT AND LOAD-BEARING here: this id came from
    # an OAuth code exchange, not from a caller. Passing True with an
    # unverified id would make the owner check forgeable, which is the whole
    # reason that flag exists.
    try:
        from . import billing
        sub = billing.has_access(discord_id=discord_id, trusted_discord=True)
    except Exception:  # noqa: BLE001
        logger.exception("subscription lookup failed — denying")
        sub = {"active": False, "status": "lookup_failed"}

    if sub.get("active"):
        return {"active": True, "reason": "stripe_subscription", "owner": False,
                "source": "stripe", "plan": sub.get("plan") or "",
                "period_end": sub.get("period_end"),
                "cancels_at_period_end": sub.get("cancels_at_period_end", False),
                "discord_id": discord_id, "username": username}

    return {
        "active": False,
        # The reason drives what the paywall SAYS. Someone sitting in the server
        # without the role needs "get premium in Discord"; someone outside it
        # needs "subscribe". Telling either one the other's story is the
        # difference between a sale and a confused user.
        "reason": ("in_guild_no_role" if roles else "not_in_guild"),
        "owner": False, "source": None,
        "subscription_status": sub.get("status") or "none",
        "discord_id": discord_id, "username": username,
    }


def invalidate(discord_id: str) -> None:
    """Drop a cached role result — used after a role change so the next check is
    live rather than waiting out the cache."""
    _role_cache.pop(discord_id, None)
