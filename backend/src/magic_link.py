"""
Email magic-link sign-in, for subscribers who have no Discord account.

WHY THIS EXISTS: someone can now subscribe without touching Discord, and the
checkout hands them a session on the way back. That covers the browser they paid
in. A new device, a cleared browser or an expired session leaves them with a
live subscription and no way back in — and "type your email to get access" is
not an option, because an email address is a claim. Anyone who guessed a
subscriber's address would inherit their subscription. Proving the address means
sending something to it.

THE LINK IS SIGNED, NOT STORED. An HMAC over {email, issued-at} with the app
secret needs no table and cannot be forged without the secret. The tradeoff is
that it is replayable until it expires, so the window is deliberately short —
fifteen minutes, versus thirty days for the session it produces.

IT NEVER REVEALS WHETHER AN EMAIL IS A SUBSCRIBER. request_link() returns the
same response either way. An endpoint that said "no such subscriber" would let
anyone test addresses against your customer list, which is a privacy leak about
your users rather than about the app.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from typing import Optional

import requests

logger = logging.getLogger("baseline.magic")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "Baseline <onboarding@resend.dev>").strip()
APP_URL = os.getenv("APP_URL", "https://baselineev.vercel.app").strip()
SECRET = os.getenv("APP_SESSION_SECRET", "").strip()

LINK_TTL = 15 * 60             # 15 minutes
_THROTTLE_SECONDS = 60         # one send per address per minute

# ── THE 6-DIGIT CODE (operator, 2026-10-05, for the iOS app) ────────────────
# The link works on the website because the browser that opened the email is
# the browser that holds the session. On a phone the email is read in Mail and
# the sign-in happens in the app, so a link that lands on baselineev.com in
# Safari helps nobody. The same email now also carries a code the user can
# type into the app. THE LINK IS UNCHANGED and still signs the website in.
#
#   10 minutes     shorter than the link's 15, because a six-digit code is
#                  guessable in a way a signed token is not
#   single use     the stored record is killed the moment a code succeeds
#   5 attempts     then the record is killed too, and a new email is needed —
#                  which also re-arms the 60s per-address throttle
#
# Stored HASHED in the durable cache table (database.cache_set with a TTL),
# keyed by the address, so a restart between send and verify does not strand
# the user and the plaintext code never sits in a database row.
CODE_TTL = 10 * 60
CODE_MAX_ATTEMPTS = 5
_CODE_KEY = "magic_code:{}"

# ── THE APP STORE REVIEWER'S FIXED CODE (operator, 2026-10-05) ──────────────
# Apple's reviewer cannot reach the operator's Discord or inbox, so the emailed
# code alone would fail review. For the ONE address in APP_REVIEWER_EMAILS, a
# fixed code held as a backend secret is accepted as well as the emailed one.
#
# Three things keep this from being a back door:
#   - it is checked ONLY when the address is the designated reviewer, so for
#     every other address this branch does not exist;
#   - it is refused unless the secret is at least REVIEWER_CODE_MIN_LEN
#     characters, so a short or empty value disables it rather than weakening
#     it;
#   - what it unlocks is the reviewer flag (active, never owner, never admin),
#     because access_for_email resolves the same address to exactly that.
# The value is set in Railway and never appears in this repo or in logs.
REVIEWER_CODE = os.getenv("APP_REVIEWER_CODE", "").strip()
REVIEWER_CODE_MIN_LEN = 12
_last_sent: dict = {}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def is_configured() -> bool:
    return bool(RESEND_API_KEY and SECRET)


def config_status() -> dict:
    """Presence only, never values."""
    return {
        "resend_key_set": bool(RESEND_API_KEY),
        "mail_from": MAIL_FROM if MAIL_FROM else None,
        "app_url": APP_URL,
        "secret_set": bool(SECRET),
        "ready": is_configured(),
    }


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(email: str) -> str:
    payload = {"e": email.lower().strip(), "iat": int(time.time())}
    raw = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).digest()
    return f"{raw}.{_b64e(sig)}"


def read_token(token: str) -> Optional[str]:
    """Verified email, or None. Constant-time signature comparison."""
    if not token or not SECRET or "." not in token:
        return None
    raw, _, sig = token.partition(".")
    try:
        expect = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig), expect):
            return None
        data = json.loads(_b64d(raw))
    except Exception:  # noqa: BLE001
        return None
    if int(time.time()) - int(data.get("iat", 0)) > LINK_TTL:
        return None
    return (data.get("e") or "").strip() or None


def _send(email: str, link: str, code: str = "") -> bool:
    """Send via Resend. False on any failure — the caller must NOT surface that
    to the user, or a failed send becomes a way to probe which addresses
    exist."""
    if not RESEND_API_KEY:
        return False
    code_block = f"""
          <p style="font-size:12.5px;line-height:1.6;color:#7a7a7a;margin-top:26px">
            Signing in on the Baseline iPhone app? Enter this code instead:
          </p>
          <div style="font-size:30px;font-weight:800;letter-spacing:8px;
                      color:#ffffff;margin:6px 0 4px">{code}</div>
          <p style="font-size:12px;color:#7a7a7a">
            The code works once, for the next 10 minutes.
          </p>""" if code else ""
    html = f"""
      <div style="font-family:-apple-system,Segoe UI,sans-serif;background:#0a0a0a;
                  padding:32px;color:#eaeaea">
        <div style="max-width:460px;margin:0 auto">
          <div style="font-size:22px;font-weight:800;letter-spacing:3px;
                      text-transform:uppercase;margin-bottom:18px">
            BASE<span style="color:#00E676">LINE</span>
          </div>
          <p style="font-size:15px;line-height:1.6;color:#bdbdbd">
            Tap below to sign in. The link works once, for the next 15 minutes.
          </p>
          <a href="{link}" style="display:inline-block;margin:18px 0;padding:14px 26px;
             background:#00E676;color:#052e16;text-decoration:none;border-radius:12px;
             font-weight:800;letter-spacing:1px;text-transform:uppercase">
            Sign in to Baseline
          </a>
          <p style="font-size:12.5px;line-height:1.6;color:#7a7a7a">
            If you didn't request this, ignore it — nothing happens until the
            link is opened, and it expires on its own.
          </p>{code_block}
        </div>
      </div>"""
    try:
        r = requests.post(
            "https://api.resend.com/emails", timeout=20,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                     "Content-Type": "application/json"},
            json={"from": MAIL_FROM, "to": [email],
                  "subject": "Your Baseline sign-in link", "html": html})
        if r.status_code in (200, 201):
            return True
        logger.warning("resend send failed %s: %s", r.status_code, r.text[:180])
    except Exception:  # noqa: BLE001
        logger.exception("resend send errored")
    return False


def request_link(email: str) -> dict:
    """Send a sign-in link IF that address has a live subscription.

    THE RESPONSE IS IDENTICAL EITHER WAY, on purpose. Reporting "no subscription
    for that email" would turn this into an oracle for testing addresses against
    your customer list. The caller shows the same "check your inbox" message
    regardless, and the truth only ever reaches the real inbox owner.
    """
    addr = (email or "").strip().lower()
    generic = {"ok": True, "sent": None}          # `sent` deliberately unknown
    if not addr or not _EMAIL_RE.match(addr):
        return {"ok": False, "error": "enter a valid email"}
    if not is_configured():
        return {"ok": False, "error": "email sign-in not configured"}

    # Throttle per address so this cannot be used to bomb somebody's inbox.
    now = time.time()
    if now - _last_sent.get(addr, 0) < _THROTTLE_SECONDS:
        return generic                             # silently succeed; no send
    _last_sent[addr] = now

    try:
        from . import billing
        entitled = billing.has_access(email=addr).get("active")
    except Exception:  # noqa: BLE001
        logger.exception("magic link entitlement check failed")
        entitled = False

    if entitled:
        link = f"{APP_URL}{'&' if '?' in APP_URL else '?'}magic={make_token(addr)}"
        code = _issue_code(addr)
        ok = _send(addr, link, code)
        logger.info("magic link requested for a subscriber, sent=%s code=%s",
                    ok, bool(code))
    else:
        # No subscription. Nothing is sent and nothing is disclosed.
        logger.info("magic link requested for an address with no subscription")
    return generic


# ── Codes ────────────────────────────────────────────────────────────────────
def _code_hash(addr: str, code: str) -> str:
    """HMAC of address+code under the app secret. Keyed to the address so a
    code issued to one inbox cannot be replayed against another, and keyed
    under the secret so a leaked table row is not a usable code."""
    return hmac.new(SECRET.encode(), f"{addr}\x00{code}".encode(),
                    hashlib.sha256).hexdigest()


def _issue_code(addr: str) -> str:
    """Mint a fresh 6-digit code for `addr` and store its hash. "" if the
    durable store is unavailable — the LINK still goes out in that case, so
    an outage of the code path degrades to the old behaviour, not to no
    sign-in at all."""
    try:
        from . import database
        code = f"{secrets.randbelow(10 ** 6):06d}"
        ok = database.cache_set(_CODE_KEY.format(addr),
                                {"h": _code_hash(addr, code), "n": 0},
                                ttl_seconds=CODE_TTL)
        return code if ok else ""
    except Exception:  # noqa: BLE001
        logger.exception("magic code issue failed — email goes out link-only")
        return ""


def verify_code(email: str, code: str) -> dict:
    """{"email": addr} on success, else {"error": reason}.

    Every failure path kills nothing EXCEPT the lockout: a wrong digit counts
    an attempt, the fifth wrong one burns the record, and a correct code burns
    it too. There is no path through here that leaves a usable code behind
    after it has been used."""
    addr = (email or "").strip().lower()
    if not addr or not _EMAIL_RE.match(addr):
        return {"error": "enter the 6-digit code from the email"}
    # Reviewer first, and only for the reviewer. Compared constant-time, on
    # the raw (trimmed) input — the fixed code is not six digits.
    try:
        from . import discord_auth
        is_reviewer = addr in discord_auth.REVIEWER_EMAILS
    except Exception:  # noqa: BLE001
        is_reviewer = False
    if (is_reviewer and len(REVIEWER_CODE) >= REVIEWER_CODE_MIN_LEN
            and hmac.compare_digest(str(code or "").strip(), REVIEWER_CODE)):
        logger.info("reviewer signed in with the fixed code")
        return {"email": addr}
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) != 6:
        return {"error": "enter the 6-digit code from the email"}
    try:
        from . import database
        key = _CODE_KEY.format(addr)
        rec = database.cache_get(key)
        if not isinstance(rec, dict) or not rec.get("h"):
            return {"error": "code expired or not found — request a new email"}
        n = int(rec.get("n") or 0) + 1
        if hmac.compare_digest(rec["h"], _code_hash(addr, digits)):
            # SINGLE USE: the record is replaced by a dead marker that can never
            # match, and expires with the original TTL.
            database.cache_set(key, {"h": "", "n": CODE_MAX_ATTEMPTS},
                               ttl_seconds=60)
            return {"email": addr}
        if n >= CODE_MAX_ATTEMPTS:
            database.cache_set(key, {"h": "", "n": n}, ttl_seconds=60)
            logger.warning("magic code locked after %d attempts", n)
            return {"error": "too many attempts — request a new email"}
        database.cache_set(key, {"h": rec["h"], "n": n}, ttl_seconds=CODE_TTL)
        return {"error": f"incorrect code ({CODE_MAX_ATTEMPTS - n} attempts left)"}
    except Exception:  # noqa: BLE001
        logger.exception("magic code verify failed")
        return {"error": "sign-in unavailable right now"}
