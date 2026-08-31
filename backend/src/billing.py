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

# ── PAYMENT LINKS ────────────────────────────────────────────────────────────
# A Stripe-hosted Payment Link, used INSTEAD of building a Checkout Session for
# a plan when one is configured. The reason to prefer it is that trial terms and
# promotions can be configured on the link in the dashboard without a deploy —
# the weekly link carries the free trial.
#
# THE COST OF A PAYMENT LINK IS THAT IT CARRIES NO METADATA OF OUR OWN. A
# session we build attaches discord_id directly to the subscription, which is
# how the webhook knows whose role to grant. A link cannot, so the id rides in
# `client_reference_id` on the URL instead, and the checkout.session.completed
# handler reads it back and links the subscription. Without that a subscriber
# pays, Stripe records it, and they get no access — the exact failure this
# indirection exists to prevent.
PAYMENT_LINKS = {
    "weekly": os.getenv("STRIPE_LINK_WEEKLY", "").strip(),
    "monthly": os.getenv("STRIPE_LINK_MONTHLY", "").strip(),
}

# Statuses that grant access. NO GRACE PERIOD, by explicit decision: the moment
# Stripe reports a failed payment the subscription stops entitling anything.
#
# past_due used to be included, on the reasoning that Stripe retries for days and
# a bank's fraud hold is not a real cancellation. That is a real cost — a
# subscriber whose card blips loses access and the Discord role until they fix
# it, and Stripe's own retry would have recovered many of them silently. It is
# the owner's call and the call is strict enforcement.
#
# Payment recovers -> Stripe flips the subscription back to active, the webhook
# writes it, and the next role sync restores the role. Nothing is permanent.
ACTIVE_STATUSES = {"active", "trialing"}

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
    """True when a checkout could actually be started.

    A configured Payment Link counts: it needs no API call to hand out, so a
    plan can be sold on a link alone even before a price id is set.
    """
    return bool(_LIB_OK and STRIPE_SECRET_KEY
                and (PRICE_WEEKLY or PRICE_MONTHLY or any(PAYMENT_LINKS.values())))


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
        "weekly_link_set": bool(PAYMENT_LINKS["weekly"]),
        "monthly_link_set": bool(PAYMENT_LINKS["monthly"]),
        "test_mode": STRIPE_SECRET_KEY.startswith("sk_test_") if STRIPE_SECRET_KEY else None,
        "ready": is_configured(),
    }


# Checkout starts (browser -> us, we see the real IP) and the webhook that
# confirms them (Stripe -> us, we see Stripe's IP) arrive from opposite
# directions minutes apart. A Payment Link cannot carry metadata, so the address
# is parked here and claimed when the subscription appears. Bounded and in-memory
# on purpose: losing it on a redeploy costs one unlogged IP, while persisting it
# would mean storing addresses for people who never subscribe at all.
_PENDING_IPS: dict = {}
_PENDING_MAX = 500


def _remember_pending_ip(discord_id: str = "", email: str = "", ip: str = "") -> None:
    if not ip:
        return
    for key in (f"d:{discord_id}" if discord_id else "", f"e:{(email or '').lower()}" if email else ""):
        if key:
            _PENDING_IPS[key] = ip
    if len(_PENDING_IPS) > _PENDING_MAX:
        for k in list(_PENDING_IPS)[:len(_PENDING_IPS) - _PENDING_MAX]:
            _PENDING_IPS.pop(k, None)


def claim_pending_ip(discord_id: str = "", email: str = "") -> str:
    """The address a checkout was started from, if we parked one."""
    for key in (f"d:{discord_id}" if discord_id else "", f"e:{(email or '').lower()}" if email else ""):
        if key and key in _PENDING_IPS:
            return _PENDING_IPS.get(key) or ""
    return ""


def create_checkout_session(plan: str, discord_id: str = "",
                            email: str = "", signup_ip: str = "") -> dict:
    """Create a Stripe-hosted Checkout Session. Returns {url} or {error}.

    discord_id rides along in metadata so the webhook can link the resulting
    subscription to a Discord account without us having to ask the buyer who
    they are afterwards — the moment they pay, we already know who to grant.
    """
    if not is_configured():
        return {"error": "billing not configured"}
    plan_key = (plan or "").lower()

    # A configured Payment Link wins over building a session, because that is
    # where the trial and promo terms live.
    link = PAYMENT_LINKS.get(plan_key)
    if link:
        if discord_id:
            sep = "&" if "?" in link else "?"
            # Stripe echoes client_reference_id back on
            # checkout.session.completed, which is the only thread tying this
            # payment to a Discord account.
            link = f"{link}{sep}client_reference_id={discord_id}"
        # No discord_id is fine and expected: someone can buy without ever
        # touching Discord. They are identified by the EMAIL Stripe collects,
        # and the checkout session id hands them a logged-in app session on the
        # way back (see claim_session).
        # A Payment Link cannot carry arbitrary metadata, so the IP is recorded
        # HERE against the email/discord we have, and reconciled onto the
        # subscription when its webhook lands. Weaker than the session path, but
        # the trial link is a Payment Link and it is the one that gets abused.
        _remember_pending_ip(discord_id=discord_id, email=email, ip=signup_ip)
        logger.info("payment link issued plan=%s discord=%s ip=%s", plan_key,
                    discord_id or "-", signup_ip or "-")
        return {"url": link, "via": "payment_link"}

    price = PLANS.get(plan_key)
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
            # signup_ip rides in SUBSCRIPTION metadata, not just the session's:
            # the session is transient and we may only ever see a subscription
            # event, and the address is the one fact the webhook cannot recover
            # on its own (a webhook arrives from STRIPE's address, never the
            # buyer's). Without this the trial-abuse check has nothing to read.
            metadata={"discord_id": discord_id or "", "plan": plan,
                      "signup_ip": signup_ip or ""},
            subscription_data={"metadata": {"discord_id": discord_id or "",
                                            "plan": plan,
                                            "signup_ip": signup_ip or ""}},
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



def _period_end(sub) -> Optional[datetime]:
    """When the paid period ends.

    CURRENT STRIPE API VERSIONS DO NOT PUT THIS AT THE TOP LEVEL. It lives on
    each subscription ITEM, and sub["current_period_end"] comes back None — so
    every expiry check silently compared against nothing and only the status
    field was doing any work. Falls back to trial_end, which is the meaningful
    boundary for a subscription still in trial.
    """
    v = sub.get("current_period_end")
    if not v:
        items = (sub.get("items") or {}).get("data") or []
        for it in items:
            v = it.get("current_period_end")
            if v:
                break
    if not v:
        v = sub.get("trial_end")
    return _ts(v)


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

    # ── THE LINK BETWEEN A PAYMENT LINK PURCHASE AND A DISCORD ACCOUNT ──────
    # A Payment Link cannot carry our metadata, so the buyer's Discord id rides
    # in client_reference_id and comes back HERE and nowhere else. Without this
    # branch a link purchase creates a subscription with no discord_id, the role
    # sync never sees them, and someone who paid gets nothing.
    #
    # Ordering is not guaranteed: this event can arrive before or after
    # customer.subscription.created. Both paths therefore upsert on
    # stripe_sub_id, and link_subscription never blanks a value it already has,
    # so whichever lands second completes the record rather than overwriting it.
    if etype == "checkout.session.completed":
        ref = (obj.get("client_reference_id") or "").strip()
        sub_id = obj.get("subscription") or ""
        email = ((obj.get("customer_details") or {}).get("email")
                 or obj.get("customer_email") or "")
        if not sub_id:
            return {"ok": True, "handled": etype, "note": "not a subscription"}
        if ref or email:
            linked = db.link_subscription(sub_id, discord_id=ref, email=email)
            if not linked:
                # The subscription row does not exist yet. Create the shell so
                # the id is not lost; the subscription.* event fills in status
                # and period end when it arrives.
                db.upsert_subscription({
                    "stripe_customer_id": obj.get("customer") or "",
                    "stripe_sub_id": sub_id,
                    "status": "incomplete",
                    "discord_id": ref,
                    "app_email": email,
                })
            logger.info("checkout completed sub=%s discord=%s email=%s",
                        sub_id, ref or "-", "yes" if email else "-")
        else:
            logger.warning("checkout completed for %s with NO client_reference_id "
                           "and no email — cannot be linked to an account", sub_id)
        return {"ok": True, "handled": etype}

    # ── A FAILED PAYMENT, ACTED ON IMMEDIATELY ──────────────────────────────
    # customer.subscription.updated normally carries the status flip to
    # past_due, but invoice.payment_failed is the event Stripe emits FIRST and
    # it is the one that cannot be missed. Writing the status here means a
    # failure is recorded even if the subscription update is delayed, and the
    # next role sync removes the role. Belt and braces on the one transition
    # the owner asked to be strict about.
    if etype == "invoice.payment_failed":
        sub_id = obj.get("subscription") or ""
        if sub_id:
            db.upsert_subscription({
                "stripe_customer_id": obj.get("customer") or "",
                "stripe_sub_id": sub_id,
                "status": "past_due",
            })
            logger.info("payment failed for %s -> past_due (access revoked)", sub_id)
        return {"ok": True, "handled": etype}

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
            "current_period_end": _period_end(obj),
            "cancel_at_period_end": 1 if obj.get("cancel_at_period_end") else 0,
        }
        if not rec["stripe_sub_id"]:
            return {"ok": False, "reason": "event carried no subscription id"}
        saved = db.upsert_subscription(rec)
        # Signup IP, stamped AFTER the row exists. Prefer the address that rode in
        # on subscription metadata (the session path); fall back to whatever the
        # Payment Link path parked, since a Payment Link cannot carry metadata.
        # Written once and never overwritten — the FIRST address is the one that
        # answers "where was this trial started from".
        _ip = (md.get("signup_ip") or "").strip()
        if not _ip:
            _ip = claim_pending_ip(discord_id=rec["discord_id"])
        if _ip:
            try:
                db.set_signup_ip(rec["stripe_sub_id"], _ip)
            except Exception:  # noqa: BLE001
                logger.exception("could not stamp signup_ip")
        logger.info("subscription %s -> %s (discord=%s plan=%s ip=%s)",
                    rec["stripe_sub_id"], rec["status"],
                    rec["discord_id"] or "-", rec["plan"] or "-", _ip or "-")
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

def claim_session(session_id: str) -> dict:
    """Turn a completed Checkout Session id into a verified identity.

    THIS IS WHAT LETS SOMEONE BUY WITHOUT A DISCORD ACCOUNT AND STILL GET IN.
    Stripe appends the session id to our success_url, we retrieve that session
    from Stripe — server-to-server, with our secret key — and read the email and
    subscription off it. The id is only useful to whoever just completed the
    payment, and it is verified against Stripe rather than trusted from the URL,
    so it cannot be forged by editing the address bar.

    Only a PAID session is accepted. An abandoned one carries the same id and
    would otherwise hand out access to somebody who reached the payment page and
    never paid.
    """
    if not is_configured() or not session_id:
        return {"ok": False, "error": "not configured"}
    try:
        sess = _stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("claim: could not retrieve %s: %s", session_id, str(exc)[:120])
        return {"ok": False, "error": "unknown session"}

    paid = (sess.get("payment_status") in ("paid", "no_payment_required")
            or sess.get("status") == "complete")
    if not paid:
        # no_payment_required covers a 100%-off trial, where nothing is charged
        # today but the subscription is real.
        return {"ok": False, "error": "session not paid"}

    email = ((sess.get("customer_details") or {}).get("email")
             or sess.get("customer_email") or "")
    sub_id = sess.get("subscription") or ""
    ref = (sess.get("client_reference_id") or "").strip()
    if sub_id and (email or ref):
        from . import database as db
        if not db.link_subscription(sub_id, discord_id=ref, email=email):
            db.upsert_subscription({
                "stripe_customer_id": sess.get("customer") or "",
                "stripe_sub_id": sub_id, "status": "incomplete",
                "discord_id": ref, "app_email": email,
            })
    logger.info("claim ok sub=%s email=%s discord=%s", sub_id or "-",
                "yes" if email else "-", ref or "-")
    return {"ok": True, "email": email, "discord_id": ref,
            "stripe_sub_id": sub_id, "customer_id": sess.get("customer") or ""}


def resync_from_stripe(limit: int = 100) -> dict:
    """Rebuild the subscriptions table from Stripe.

    Stripe is the source of truth; this table is a local read cache written by
    webhooks. Anything that stops a webhook landing — a deploy mid-delivery, an
    outage, or a bug like the one that made every write silently no-op — leaves
    real paying customers invisible to entitlement and the role sync.

    This is the repair. It is idempotent (upsert on stripe_sub_id) and safe to
    run any time, so it doubles as a way to verify that what we hold matches
    what Stripe holds.

    The customer's email is fetched per subscription because the subscription
    object does not carry it, and the email is what identifies a buyer who never
    connected Discord.
    """
    if not is_configured():
        return {"ok": False, "error": "billing not configured"}
    from . import database as db
    seen, wrote, errors = 0, 0, 0
    try:
        subs = _stripe.Subscription.list(limit=min(100, limit), status="all")
    except Exception as exc:  # noqa: BLE001
        logger.exception("resync: could not list subscriptions")
        return {"ok": False, "error": str(exc)[:200]}

    for sub in subs.auto_paging_iter():
        seen += 1
        if seen > limit:
            break
        try:
            email = ""
            cust_id = sub.get("customer")
            if cust_id:
                try:
                    cust = _stripe.Customer.retrieve(cust_id)
                    email = (cust.get("email") or "") if not cust.get("deleted") else ""
                except Exception:  # noqa: BLE001
                    pass
            md = sub.get("metadata") or {}
            rec = {
                "stripe_customer_id": cust_id or "",
                "stripe_sub_id": sub.get("id") or "",
                "status": sub.get("status") or "incomplete",
                "plan": md.get("plan") or "",
                "discord_id": md.get("discord_id") or "",
                "app_email": email,
                "current_period_end": _period_end(sub),
                "cancel_at_period_end": 1 if sub.get("cancel_at_period_end") else 0,
            }
            if rec["stripe_sub_id"] and db.upsert_subscription(rec):
                wrote += 1
        except Exception:  # noqa: BLE001
            errors += 1
            logger.exception("resync: failed on one subscription")
    logger.info("resync: %d seen, %d written, %d errors", seen, wrote, errors)
    return {"ok": True, "seen": seen, "written": wrote, "errors": errors}
