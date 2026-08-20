"""
Stripe subscriptions — weekly and monthly plans.

CHECKOUT IS STRIPE-HOSTED. We create a Checkout Session and redirect; the card
is entered on Stripe's page, on Stripe's domain. No card number, CVC or expiry
ever reaches this server, which keeps the whole application out of PCI scope.
There is deliberately no card form anywhere in this codebase and none should be
added.

ENTITLEMENT IS READ LOCALLY, WRITTEN BY WEBHOOK. Stripe is the source of truth,
but checking it over the network on every gated request would put their uptime
in front of ours. Webhooks write to the subscriptions table; reads hit Postgres.

EVERY WEBHOOK IS SIGNATURE-VERIFIED and unverified payloads are rejected without
being parsed. The endpoint is public by necessity, so without verification
anyone who found the URL could POST themselves a lifetime subscription. This is
the single most important line in the file.

NOTHING HERE ACTIVATES WITHOUT KEYS. Absent STRIPE_SECRET_KEY the module reports
unconfigured and every route degrades to "billing unavailable" rather than
erroring — the app must run normally for existing users while billing is still
being set up.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("baseline.billing")

# ── Configuration (env only — never hardcode a key, not even a test one) ─────
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
PRICE_WEEKLY          = os.getenv("STRIPE_PRICE_WEEKLY", "").strip()
PRICE_MONTHLY         = os.getenv("STRIPE_PRICE_MONTHLY", "").strip()
SUCCESS_URL           = os.getenv("STRIPE_SUCCESS_URL",
                                  "https://baseline-app-three.vercel.app/?sub=ok").strip()
CANCEL_URL            = os.getenv("STRIPE_CANCEL_URL",
                                  "https://baseline-app-three.vercel.app/?sub=cancel").strip()

PLANS = {"weekly": PRICE_WEEKLY, "monthly": PRICE_MONTHLY}

# Statuses that grant access. past_due is deliberately INCLUDED: Stripe retries a
# failed payment for days, and cutting a paying subscriber off the moment a card
# blips — often a bank's fraud hold, not a real failure — is a worse outcome than
# a few days of grace. unpaid/canceled are excluded; by then Stripe has given up.
ACTIVE_STATUSES = {"active", "trialing", "past_due"}

try:
    import stripe as _stripe
    if STRIPE_SECRET_KEY:
        _stripe.api_key = STRIPE_SECRET_KEY
    _LIB_OK = True
except Exception as exc:  # noqa: BLE001
    logger.warning("stripe library unavailable: %s", exc)
    _stripe = None
    _LIB_OK = False


# ── OWNER BYPASS ─────────────────────────────────────────────────────────────
# The owner must be able to use the product without paying for it. There are two
# mechanisms here and the difference between them is the whole security of this
# feature.
#
# BILLING_OWNER_TOKEN — a long random secret, for the APP. The browser sends it
#   as a header and the server compares it to the env var. Unforgeable without
#   knowing the secret.
#
# BILLING_OWNER_DISCORD_IDS — for the BOT ONLY, where the Discord user id comes
#   from a signed interaction payload and the caller cannot choose it.
#
# WHY NOT JUST TRUST A DISCORD ID FROM THE APP: /api/billing/status takes
# discord_id as a query parameter, which is caller-supplied. If an id on the
# owner list granted access there, anyone who learned the owner's Discord id —
# which is public in any server they post in — could type it into a URL and take
# the product for free. Owner ids are therefore honoured ONLY when the caller has
# already proven it is the bot, via the same BILLING_SYNC_TOKEN shared secret.
OWNER_TOKEN = os.getenv("BILLING_OWNER_TOKEN", "").strip()
OWNER_DISCORD_IDS = {
    x.strip() for x in os.getenv("BILLING_OWNER_DISCORD_IDS", "").split(",")
    if x.strip()
}


def is_owner_token(token: str) -> bool:
    """Constant-time compare against the owner token. Empty token never matches,
    so leaving the env var unset disables the bypass entirely rather than
    granting everyone access."""
    if not OWNER_TOKEN or not token:
        return False
    import hmac
    return hmac.compare_digest(token, OWNER_TOKEN)


def is_owner_discord(discord_id: str, trusted: bool = False) -> bool:
    """Owner check for a Discord id.

    `trusted` MUST be True and is only set by a caller that has proven it is the
    bot. A caller-supplied id is never enough — see the note above.
    """
    return bool(trusted and discord_id and discord_id in OWNER_DISCORD_IDS)


def is_configured() -> bool:
    """True when a checkout could actually be created."""
    return bool(_LIB_OK and STRIPE_SECRET_KEY and (PRICE_WEEKLY or PRICE_MONTHLY))


def config_status() -> dict:
    """What is missing, for an operator — never exposes key VALUES, only whether
    each one is present. A status endpoint that echoed a secret back would be a
    way to exfiltrate it."""
    return {
        "library_installed": _LIB_OK,
        "secret_key_set": bool(STRIPE_SECRET_KEY),
        "webhook_secret_set": bool(STRIPE_WEBHOOK_SECRET),
        "weekly_price_set": bool(PRICE_WEEKLY),
        "monthly_price_set": bool(PRICE_MONTHLY),
        "test_mode": STRIPE_SECRET_KEY.startswith("sk_test_") if STRIPE_SECRET_KEY else None,
        "ready": is_configured(),
    }


def create_checkout_session(plan: str, discord_id: str = "",
                            email: str = "") -> dict:
    """Create a Stripe-hosted Checkout Session. Returns {url} or {error}.

    discord_id rides along in metadata so the webhook can link the resulting
    subscription to a Discord account without us having to ask the buyer who
    they are afterwards — the moment they pay, we already know who to grant.
    """
    if not is_configured():
        return {"error": "billing not configured"}
    price = PLANS.get((plan or "").lower())
    if not price:
        return {"error": f"unknown plan '{plan}'"}
    try:
        sess = _stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            # {CHECKOUT_SESSION_ID} is substituted by Stripe. The quickstart
            # uses it so the success page can VERIFY the session instead of
            # trusting that a redirect happened — a URL anyone can visit.
            success_url=(SUCCESS_URL +
                         ('&' if '?' in SUCCESS_URL else '?') +
                         'session_id={CHECKOUT_SESSION_ID}'),
            cancel_url=CANCEL_URL,
            allow_promotion_codes=True,
            # Metadata lands on BOTH the session and the subscription, so the
            # link survives even if we only ever see the subscription event.
            metadata={"discord_id": discord_id or "", "plan": plan},
            subscription_data={"metadata": {"discord_id": discord_id or "",
                                            "plan": plan}},
            **({"customer_email": email} if email else {}),
        )
        logger.info("checkout session created plan=%s discord=%s", plan,
                    discord_id or "-")
        return {"url": sess.url, "session_id": sess.id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("checkout session failed")
        return {"error": str(exc)[:200]}


def verify_webhook(payload: bytes, sig_header: str):
    """Verify a webhook signature and return the event, or None.

    RETURNS None RATHER THAN PARSING ON FAILURE. The endpoint is public, so an
    unverified body is hostile input by default — it is never JSON-decoded, let
    alone acted on. Without a configured webhook secret we reject everything,
    because accepting unsigned events would be strictly worse than having no
    billing at all.
    """
    if not (_LIB_OK and STRIPE_WEBHOOK_SECRET):
        logger.warning("webhook received but STRIPE_WEBHOOK_SECRET is not set — rejected")
        return None
    try:
        return _stripe.Webhook.construct_event(payload, sig_header,
                                               STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # noqa: BLE001 — includes SignatureVerificationError
        logger.warning("webhook signature rejected: %s", str(exc)[:160])
        return None


def _ts(v) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None
    except (TypeError, ValueError, OSError):
        return None


def apply_event(event) -> dict:
    """Fold one verified Stripe event into the subscriptions table.

    Only subscription lifecycle events are handled; everything else is
    acknowledged and ignored. Stripe retries on a non-2xx, so silently ignoring
    an event we do not model is correct — raising would make Stripe retry
    forever on an event that will never matter to us.
    """
    from . import database as db
    etype = getattr(event, "type", None) or event.get("type")
    obj = (getattr(event, "data", None) or event.get("data", {})).get("object", {})

    if etype in ("customer.subscription.created",
                 "customer.subscription.updated",
                 "customer.subscription.deleted"):
        md = obj.get("metadata") or {}
        rec = {
            "stripe_customer_id": obj.get("customer") or "",
            "stripe_sub_id": obj.get("id") or "",
            # A deleted subscription is 'canceled' regardless of the status
            # Stripe last reported on the object.
            "status": ("canceled" if etype.endswith("deleted")
                       else (obj.get("status") or "incomplete")),
            "plan": md.get("plan") or "",
            "discord_id": md.get("discord_id") or "",
            "current_period_end": _ts(obj.get("current_period_end")),
            "cancel_at_period_end": 1 if obj.get("cancel_at_period_end") else 0,
        }
        if not rec["stripe_sub_id"]:
            return {"ok": False, "reason": "event carried no subscription id"}
        saved = db.upsert_subscription(rec)
        logger.info("subscription %s -> %s (discord=%s plan=%s)",
                    rec["stripe_sub_id"], rec["status"],
                    rec["discord_id"] or "-", rec["plan"] or "-")
        return {"ok": bool(saved), "handled": etype, "status": rec["status"]}

    return {"ok": True, "handled": None, "ignored": etype}


def has_access(discord_id: str = "", email: str = "",
               owner_token: str = "", trusted_discord: bool = False) -> dict:
    """Is this person entitled right now? {active, status, plan, period_end}.

    Grants through current_period_end even when cancel_at_period_end is set: the
    period is paid for. Fails CLOSED — an unreadable database answers "no
    access" rather than letting everyone in, because the failure mode of the
    opposite is giving the product away.
    """
    from . import database as db
    # Owner bypass, checked before any subscription lookup so it still works
    # when Stripe is unconfigured or the database is down — the owner must never
    # be locked out of their own product by a billing outage.
    if is_owner_token(owner_token):
        return {"active": True, "status": "owner", "plan": "owner",
                "period_end": None, "cancels_at_period_end": False}
    if is_owner_discord(discord_id, trusted=trusted_discord):
        return {"active": True, "status": "owner", "plan": "owner",
                "period_end": None, "cancels_at_period_end": False}
    try:
        row = db.find_subscription(discord_id=discord_id, email=email)
    except Exception:  # noqa: BLE001
        logger.exception("entitlement lookup failed — denying")
        return {"active": False, "status": "lookup_failed"}
    if not row:
        return {"active": False, "status": "none"}
    status = (row.get("status") or "").lower()
    end = row.get("current_period_end")
    if isinstance(end, str):
        try:
            end = datetime.fromisoformat(end)
        except ValueError:
            end = None
    not_expired = True
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        not_expired = end > datetime.now(timezone.utc)
    return {
        "active": status in ACTIVE_STATUSES and not_expired,
        "status": status,
        "plan": row.get("plan") or "",
        "period_end": end.isoformat() if end else None,
        "cancels_at_period_end": bool(row.get("cancel_at_period_end")),
    }


def billing_portal_url(customer_id: str, return_url: str = "") -> dict:
    """A Stripe-hosted portal session so a subscriber can update their card or
    cancel without us building any of that, and without card data touching us."""
    if not is_configured() or not customer_id:
        return {"error": "billing not configured"}
    try:
        s = _stripe.billing_portal.Session.create(
            customer=customer_id, return_url=return_url or SUCCESS_URL)
        return {"url": s.url}
    except Exception as exc:  # noqa: BLE001
        logger.exception("portal session failed")
        return {"error": str(exc)[:200]}
