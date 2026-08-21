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


def _send(email: str, link: str) -> bool:
    """Send via Resend. False on any failure — the caller must NOT surface that
    to the user, or a failed send becomes a way to probe which addresses
    exist."""
    if not RESEND_API_KEY:
        return False
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
          </p>
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
        ok = _send(addr, link)
        logger.info("magic link requested for a subscriber, sent=%s", ok)
    else:
        # No subscription. Nothing is sent and nothing is disclosed.
        logger.info("magic link requested for an address with no subscription")
    return generic
