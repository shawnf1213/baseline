"""
Durable storage for the Pick of the Day results tracker (Feature 1).

Uses the Railway-provisioned PostgreSQL database via the DATABASE_URL env var.
Completely self-contained: if DATABASE_URL is missing or the DB is unreachable,
every helper degrades to a no-op / empty result and logs a warning, so a DB
problem can never crash the API or affect any prop calculation.

Records survive Railway redeploys, restarts and new deployments because they
live in Postgres, not in memory or the (ephemeral) container filesystem.
"""

import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("baseline.database")

# Railway/Heroku historically hand out "postgres://"; SQLAlchemy needs
# "postgresql://". Normalise so either form works.
_RAW_URL = os.getenv("DATABASE_URL", "") or ""
if _RAW_URL.startswith("postgres://"):
    _RAW_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _RAW_URL

# Model-generation marker stamped on every new pick (Fix D, 2026-07-23). Bump this
# whenever a material projection-model change ships so calibration can segment
# results by generation instead of pooling incompatible models. Current value marks
# the BP four-scenario outcome-conditioning rebuild (A2): BP picks BEFORE this
# (old C1–C8 chain) carry the backfilled "pre-a2" and must not be pooled with
# post-fix BP results. Same pattern as board_policy_version.
MODEL_VERSION = "2026.07.23-bp-a2"

_engine = None
_Session = None
_READY = False

try:
    from sqlalchemy import (
        create_engine, Column, Integer, String, Float, DateTime, func,
    )
    from sqlalchemy.orm import declarative_base, sessionmaker
    Base = declarative_base()

    class Pick(Base):
        __tablename__ = "picks"
        id               = Column(Integer, primary_key=True, autoincrement=True)
        player           = Column(String, nullable=False)
        opponent         = Column(String, default="")
        prop_type        = Column(String, nullable=False)
        line             = Column(Float)
        model_projection = Column(Float)
        lean             = Column(String)          # OVER / UNDER
        confidence       = Column(Float)
        result           = Column(String, default="PENDING")  # W/L/PUSH/PENDING/NEEDS REVIEW
        generated_at     = Column(DateTime(timezone=True), server_default=func.now())
        resolved_at      = Column(DateTime(timezone=True), nullable=True)
        original_line    = Column(Float)
        tournament       = Column(String, default="")
        surface          = Column(String, default="")
        # "potd" (Pick of the Day) or "3x" (two-leg slip). Legacy rows are NULL
        # and treated as "potd" everywhere they're read.
        pick_group       = Column(String, default="potd")
        # JSON snapshot of the confidence component breakdown at pick time, so a
        # faithful calibration recompute is possible later. NULL on legacy rows.
        confidence_breakdown = Column(String)
        # 1 = this pick's confidence was computed BEFORE the degraded-fetch cache
        # guard shipped (2026-07-14), so it may have been scored against a poisoned
        # Sofascore snapshot (events present, per-match statistics missing — a
        # player's usable match count collapsing to ~0). Those scores are not
        # trustworthy calibration inputs. The pick RECORD stands as posted and is
        # never altered; this flag only excludes it from calibration maths.
        pre_guard = Column(Integer, default=0)
        # Board qualification policy in force when this pick was selected:
        #   v1 = per-prop bars (standard 70/75, Total Games 85, PTGW 80, blowout
        #        exception; DF board-excluded; TG-90 / PTGW star gates)
        #   v2 = uniform 65 board floor, uniform 80 POTD bar, DF star-blocked only
        # Existing rows are backfilled to v1; new picks default to v2. Calibration
        # can report per-prop hit rates split by policy version without a reset.
        board_policy_version = Column(String, default="v2")
        # PrizePicks odds_type: "standard" or "demon" (goblins are never posted).
        # Lets the tracker / recaps / hit-rate reports segment standard vs demon.
        # Existing rows are backfilled to "standard".
        odds_type = Column(String, default="standard")
        # 1 = this record is a superseded / earlier-generation / duplicate pick that
        # must NOT count toward the public record or appear in recaps. Kept in the
        # DB for the reproducibility audit, never deleted. Default 0 = counts.
        excluded_from_record = Column(Integer, default=0)
        # Projection-model generation in force when this pick was made (Fix D).
        # Existing rows are backfilled to "pre-a2" (old BP C1–C8 chain); new picks
        # default to MODEL_VERSION. Lets calibration split BP hit-rates by the A2
        # outcome-conditioning boundary instead of pooling incompatible models.
        model_version = Column(String, default=MODEL_VERSION)
        # Actual stat the player recorded when the pick resolved (e.g. 18 aces,
        # 21 total games). Captured by the resolver at grade time so the recap can
        # show the final number next to each prop. NULL = not recorded (older rows
        # or manual grades from matches the resolver couldn't fetch).
        result_value = Column(Float, nullable=True)
        # WHAT THE MODEL ACTUALLY USED, captured at pick time.
        #
        # Every retrospective analysis before this had to re-run today's code
        # against today's statistics to reconstruct a pick made days ago — which
        # is not the pick. The stats have moved, the model has changed, and an
        # anchor present then may be absent now. That is how a whole evening of
        # diagnostics went wrong: `model_projection` silently changed meaning
        # (a mean before the A2 rebuild, the fair line after), so regressing
        # outcomes against it compared two different quantities; and a games
        # margin was measured against `bp_base_proj` when the code consumed
        # `project_break_points()["projection"]`.
        #
        # projection_kind is the field that fixes the first of those: it records
        # WHAT KIND OF NUMBER model_projection is, so nobody has to infer it from
        # a date. The rest record the inputs a later question will want and which
        # cannot be recovered afterwards.
        model_inputs = Column(String)

        def to_dict(self) -> dict:
            return {
                "id": self.id,
                "player": self.player,
                "opponent": self.opponent,
                "prop_type": self.prop_type,
                "line": self.line,
                "model_projection": self.model_projection,
                "lean": self.lean,
                "confidence": self.confidence,
                "result": self.result,
                "generated_at": self.generated_at.isoformat() if self.generated_at else None,
                "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
                "original_line": self.original_line,
                "tournament": self.tournament,
                "surface": self.surface,
                "pick_group": (self.pick_group or "potd"),
                "confidence_breakdown": self.confidence_breakdown,
                "pre_guard": int(self.pre_guard or 0),
                "board_policy_version": (self.board_policy_version or "v1"),
                "odds_type": (self.odds_type or "standard"),
                "excluded_from_record": int(self.excluded_from_record or 0),
                "model_version": (self.model_version or "pre-a2"),
                "result_value": self.result_value,
                "model_inputs": self.model_inputs,
            }

    class CacheEntry(Base):
        """Durable key-value cache — the DURABILITY layer behind the in-process
        caches, not a per-read dependency.

        Why: every cache in this app lived only in the process. A Railway deploy
        wipes them, so the opponent-hold cache measured 5/7 opponents resolved,
        then 0/7 immediately after a push — and the BP quality adjustment (a pure
        function of cache state) moved with it. Cache warmth was being destroyed
        by the act of shipping, which also silently reset the stat-rich counts and
        made cross-deploy reproducibility impossible to observe.

        Design: memory stays the hot path. Postgres is read ONCE per key on the
        first miss (lazy hydrate — no bulk load at boot) and written through on
        every set. Warm reads never touch Postgres, so there is no latency change.
        """
        __tablename__ = "cache_entries"
        cache_key   = Column(String, primary_key=True)
        value       = Column(String, nullable=False)      # JSON
        written_at  = Column(DateTime(timezone=True), server_default=func.now())
        ttl_seconds = Column(Integer)                     # NULL = never expires

    class Subscription(Base):
        """One row per PAYING CUSTOMER, keyed by their Stripe customer id.

        WHY A LOCAL TABLE AND NOT "ASK STRIPE": entitlement is checked on every
        gated request, and a network call to Stripe per request would put their
        uptime in front of ours. Stripe remains the source of truth; webhooks
        write here, and this is the fast read.

        NO CARD DATA IS EVER STORED, and none is available to store — checkout is
        Stripe-hosted, so the card never touches this server. The only payment
        identifiers here are Stripe's own opaque ids.

        discord_id links a subscription to a Discord account so the bot can grant
        and revoke the role; app_email links it to an app login. Either may be
        NULL: someone can pay before connecting Discord, and the link is made
        later without disturbing the subscription itself.
        """
        __tablename__ = "subscriptions"
        id                  = Column(Integer, primary_key=True, autoincrement=True)
        stripe_customer_id  = Column(String, nullable=False, index=True)
        stripe_sub_id       = Column(String, nullable=False, unique=True, index=True)
        # active | trialing | past_due | canceled | unpaid | incomplete
        status              = Column(String, nullable=False, default="incomplete")
        plan                = Column(String, default="")        # weekly | monthly
        discord_id          = Column(String, default="", index=True)
        app_email           = Column(String, default="", index=True)
        # When the paid period ends. Access is granted up to this instant even
        # after a cancellation — a cancel is "do not renew", not "cut them off
        # mid-period", and treating it as the latter would be taking money for
        # time not served.
        current_period_end  = Column(DateTime(timezone=True), nullable=True)
        cancel_at_period_end = Column(Integer, default=0)        # 0/1
        created_at          = Column(DateTime(timezone=True), server_default=func.now())
        updated_at          = Column(DateTime(timezone=True), server_default=func.now())
        # The IP the trial was started from, kept RAW and deliberately so: the
        # question it answers is "has this address already taken a free trial
        # under a different email", and a hash cannot be eyeballed during an
        # abuse review. Personal data under GDPR — it needs a line in the privacy
        # policy and should not outlive its purpose.
        signup_ip           = Column(String, default="", index=True)

    class PreviewSession(Base):
        """One row per anonymous visitor's free look at the app.

        WHY SERVER-SIDE: a timer in the browser is a suggestion. Anything held in
        localStorage or a cookie dies with a refresh at best and an incognito
        window at worst, which is exactly the bypass this is meant to close. The
        clock therefore lives here, and the browser only ever asks how much is
        left.

        KEYED ON A HASHED IP, NOT A COOKIE. A private window carries no cookies
        and no storage, so the only thing that survives it is the address the
        request came from. Hashed because this table only needs to recognise a
        repeat visitor, never to identify one — unlike Subscription.signup_ip
        above, which exists to be read by a human.

        WHAT THIS CANNOT DO, stated plainly so nobody assumes otherwise: an IP is
        not a person. Everyone behind one office NAT or one mobile carrier CGNAT
        shares a window, and anyone who flips on a VPN or switches to cell data
        gets a fresh one. This raises the cost of a bypass; it does not make it
        impossible, and no client-side scheme does better.
        """
        __tablename__ = "preview_sessions"
        id          = Column(Integer, primary_key=True, autoincrement=True)
        visitor_key = Column(String, nullable=False, unique=True, index=True)
        first_seen  = Column(DateTime(timezone=True), server_default=func.now())
        last_seen   = Column(DateTime(timezone=True), server_default=func.now())
        hits        = Column(Integer, default=0)

    _SQLALCHEMY_OK = True
except Exception as exc:  # pragma: no cover — missing dep shouldn't crash the app
    logger.warning("SQLAlchemy unavailable — results DB disabled: %s", exc)
    _SQLALCHEMY_OK = False
    Pick = None  # type: ignore
    CacheEntry = None  # type: ignore
    Subscription = None  # type: ignore
    PreviewSession = None  # type: ignore


def init_db() -> None:
    """Create the engine and ensure the picks table exists. Never drops data.
    Safe to call once on startup; failures are logged and leave the DB disabled."""
    global _engine, _Session, _READY
    if not _SQLALCHEMY_OK:
        return
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — results tracker DB disabled.")
        return
    try:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)   # CREATE TABLE IF NOT EXISTS — never drops
        # Lightweight migration: create_all won't ALTER an existing table, so
        # add columns introduced after the table was first created. IF NOT
        # EXISTS makes this idempotent and safe on every boot.
        try:
            from sqlalchemy import text
            with _engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS pick_group "
                    "VARCHAR DEFAULT 'potd'"))
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS confidence_breakdown VARCHAR"))
                # signup_ip: the subscriptions table already exists in production,
                # so create_all above will not add this. Existing rows stay blank —
                # the IP was never captured for them and inventing one would be
                # worse than an honest gap in the abuse history.
                conn.execute(text(
                    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS signup_ip VARCHAR"))
                # Existing rows stay NULL: the inputs were never captured for them
                # and inventing values would be worse than an honest gap.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS model_inputs VARCHAR"))
                # pre_guard: every row that already exists when this column is
                # first created predates the degraded-fetch cache guard, so it is
                # backfilled to 1 exactly once. NULL is the "never seen" marker —
                # after this UPDATE no row is NULL, so a redeploy can't reflag
                # post-guard picks. New picks default to 0 via the column default.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS pre_guard INTEGER"))
                _bf = conn.execute(text(
                    "UPDATE picks SET pre_guard = 1 WHERE pre_guard IS NULL"))
                if getattr(_bf, "rowcount", 0):
                    logger.info("picks pre_guard backfill: %d existing rows marked "
                                "pre-cache-guard (excluded from calibration maths)",
                                _bf.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN pre_guard SET DEFAULT 0"))
                # board_policy_version: every row existing when this column is
                # first added predates the v2 policy, so backfill NULL -> 'v1'
                # exactly once, then set the column default to 'v2' so new picks
                # are v2 automatically. log_pick also passes 'v2' explicitly.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "board_policy_version VARCHAR"))
                _bfp = conn.execute(text(
                    "UPDATE picks SET board_policy_version = 'v1' "
                    "WHERE board_policy_version IS NULL"))
                if getattr(_bfp, "rowcount", 0):
                    logger.info("picks board_policy_version backfill: %d existing "
                                "rows marked v1", _bfp.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN board_policy_version "
                    "SET DEFAULT 'v2'"))
                # odds_type: existing rows predate demon evaluation -> "standard".
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS odds_type VARCHAR"))
                conn.execute(text(
                    "UPDATE picks SET odds_type = 'standard' WHERE odds_type IS NULL"))
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN odds_type SET DEFAULT 'standard'"))
                # excluded_from_record: superseded / duplicate picks flagged out of
                # the record + recaps but retained for audit. Existing rows -> 0.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "excluded_from_record INTEGER"))
                conn.execute(text(
                    "UPDATE picks SET excluded_from_record = 0 "
                    "WHERE excluded_from_record IS NULL"))
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN excluded_from_record SET DEFAULT 0"))
                # model_version (Fix D): every row existing when this column is first
                # added predates the BP A2 outcome-conditioning rebuild, so backfill
                # NULL -> 'pre-a2' exactly once, then set the column default to the
                # current MODEL_VERSION so new picks are stamped automatically.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS model_version VARCHAR"))
                _bmv = conn.execute(text(
                    "UPDATE picks SET model_version = 'pre-a2' "
                    "WHERE model_version IS NULL"))
                if getattr(_bmv, "rowcount", 0):
                    logger.info("picks model_version backfill: %d existing rows marked "
                                "pre-a2 (old BP C1-C8 chain)", _bmv.rowcount)
                conn.execute(text(
                    "ALTER TABLE picks ALTER COLUMN model_version SET DEFAULT '%s'"
                    % MODEL_VERSION))
                # result_value: the actual stat recorded at resolution (aces, games,
                # etc.) so the recap can show the final number. Nullable, no backfill
                # — existing graded rows simply have no stored value.
                conn.execute(text(
                    "ALTER TABLE picks ADD COLUMN IF NOT EXISTS "
                    "result_value DOUBLE PRECISION"))
        except Exception as mexc:  # noqa: BLE001 — non-fatal; column may already exist
            logger.warning("picks pick_group migration skipped: %s", mexc)
        _READY = True
        logger.info("Results DB ready (picks table ensured).")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Results DB init failed — tracker disabled: %s", exc)
        _READY = False


def is_ready() -> bool:
    return _READY


@contextmanager
def _session():
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ── CRUD helpers (all degrade gracefully when the DB is disabled) ────────────
def log_pick(rec: dict) -> dict:
    """Insert one pick record. Returns the stored row as a dict, or {} on failure."""
    if not _READY:
        return {}
    try:
        with _session() as s:
            row = Pick(
                player=rec.get("player", ""),
                opponent=rec.get("opponent", ""),
                prop_type=rec.get("prop_type", ""),
                line=rec.get("line"),
                model_projection=rec.get("model_projection"),
                lean=(rec.get("lean") or "").upper(),
                confidence=rec.get("confidence"),
                result=(rec.get("result") or "PENDING").upper(),
                original_line=rec.get("original_line", rec.get("line")),
                tournament=rec.get("tournament", ""),
                surface=rec.get("surface", ""),
                pick_group=(rec.get("pick_group") or "potd"),
                confidence_breakdown=rec.get("confidence_breakdown"),
                board_policy_version=(rec.get("board_policy_version") or "v2"),
                odds_type=(rec.get("odds_type") or "standard"),
                model_version=(rec.get("model_version") or MODEL_VERSION),
                model_inputs=(rec.get("model_inputs") or None),
            )
            s.add(row)
            s.flush()
            return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("log_pick failed: %s", exc)
        return {}


def update_result(pick_id: int, result: str, value: float = None) -> bool:
    """Set the result (W/L/PENDING/NEEDS REVIEW) and resolved_at. Returns success.
    ``value`` is the actual stat the player recorded (aces, games, …); when given
    it's stored so the recap can show the final number. None leaves it untouched."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            row.result = (result or "").upper()
            row.resolved_at = datetime.now(timezone.utc)
            if value is not None:
                try:
                    row.result_value = float(value)
                except (TypeError, ValueError):
                    pass
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("update_result failed: %s", exc)
        return False


def set_excluded(ids: list, excluded: bool = True) -> int:
    """Flag (or unflag) pick rows as excluded_from_record. Retains the rows; only
    the flag changes. Returns the number of rows updated."""
    if not _READY or not ids:
        return 0
    try:
        n = 0
        with _session() as s:
            for pid in ids:
                row = s.get(Pick, int(pid))
                if row is None:
                    continue
                row.excluded_from_record = 1 if excluded else 0
                n += 1
        return n
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_excluded failed: %s", exc)
        return 0


def set_line(pick_id: int, line=None, original_line=None) -> bool:
    """Correct a pick's line / original_line (admin). Used when a PrizePicks line
    moved between posting and logging, so the stored line no longer matches what
    members played. Returns success."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            if isinstance(line, (int, float)):
                row.line = line
            if isinstance(original_line, (int, float)):
                row.original_line = original_line
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_line failed: %s", exc)
        return False


def delete_pick(pick_id: int) -> bool:
    """Delete one pick row (admin cleanup / removing a bad entry)."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            s.delete(row)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_pick failed: %s", exc)
        return False


def all_picks() -> list:
    """All pick rows as dicts, most recent first."""
    if not _READY:
        return []
    try:
        with _session() as s:
            rows = s.query(Pick).order_by(Pick.generated_at.desc(), Pick.id.desc()).all()
            return [r.to_dict() for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.exception("all_picks failed: %s", exc)
        return []


def pending_picks() -> list:
    """Pick rows still awaiting a result (PENDING), oldest first."""
    if not _READY:
        return []
    try:
        with _session() as s:
            rows = (s.query(Pick)
                    .filter(Pick.result == "PENDING")
                    .order_by(Pick.generated_at.asc()).all())
            return [r.to_dict() for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.exception("pending_picks failed: %s", exc)
        return []


# ── Durable cache (see CacheEntry) ──────────────────────────────────────────
# Every helper degrades to a no-op / miss when the DB is unavailable, so a
# Postgres problem costs cache warmth and NOTHING else — the callers still have
# their in-memory layer and their network fallback.
# ── Subscriptions ────────────────────────────────────────────────────────────
def upsert_subscription(rec: dict) -> dict:
    """Insert or update one subscription, keyed on stripe_sub_id.

    UPSERT, NOT INSERT: Stripe sends created -> updated -> deleted for the same
    subscription and re-delivers any event whose response it did not see. An
    insert-only path would fan one subscription into duplicate rows and leave
    entitlement depending on which row a query happened to find first.
    """
    if not _READY or Subscription is None:
        return {}
    try:
        with _session() as s:
            row = (s.query(Subscription)
                    .filter(Subscription.stripe_sub_id == rec["stripe_sub_id"])
                    .one_or_none())
            if row is None:
                row = Subscription(stripe_sub_id=rec["stripe_sub_id"])
                s.add(row)
            for f in ("stripe_customer_id", "status", "plan",
                      "current_period_end", "cancel_at_period_end"):
                if rec.get(f) is not None:
                    setattr(row, f, rec[f])
            # Never blank an existing link: a later event may carry no metadata,
            # and overwriting a known discord_id with "" would silently strip a
            # paying subscriber of their role.
            if rec.get("discord_id"):
                row.discord_id = rec["discord_id"]
            if rec.get("app_email"):
                row.app_email = rec["app_email"]
            row.updated_at = datetime.now(timezone.utc)
            s.flush()
            return {"id": row.id, "stripe_sub_id": row.stripe_sub_id,
                    "status": row.status}
    except Exception:
        logger.exception("upsert_subscription failed")
    return {}


def find_subscription(discord_id: str = "", email: str = "") -> dict:
    """Best current subscription for a person, or {}. Prefers an ACTIVE row —
    someone who cancelled and resubscribed has two, and the live one is the
    answer."""
    if not _READY or Subscription is None:
        return {}
    if not discord_id and not email:
        return {}
    try:
        with _session() as s:
            q = s.query(Subscription)
            q = (q.filter(Subscription.discord_id == discord_id) if discord_id
                 else q.filter(Subscription.app_email == email))
            rows = q.all()
            if not rows:
                return {}
            rows.sort(key=lambda r: (
                0 if (r.status or "") in ("active", "trialing") else 1,
                -(r.current_period_end.timestamp() if r.current_period_end else 0),
            ))
            r = rows[0]
            return {"id": r.id, "stripe_customer_id": r.stripe_customer_id,
                    "stripe_sub_id": r.stripe_sub_id, "status": r.status,
                    "plan": r.plan, "discord_id": r.discord_id,
                    "app_email": r.app_email,
                    "current_period_end": r.current_period_end,
                    "cancel_at_period_end": r.cancel_at_period_end}
    except Exception:
        logger.exception("find_subscription failed")
    return {}


def subscriptions_debug() -> dict:
    """Row count and a redacted sample. Never exposed without the sync token."""
    if not _READY or Subscription is None:
        return {"ready": bool(_READY), "model": Subscription is not None,
                "note": "db disabled or model missing"}
    out = {"ready": True, "rows": 0, "sample": []}
    with _session() as s:
        rows = s.query(Subscription).all()
        out["rows"] = len(rows)
        for r in rows[:5]:
            out["sample"].append({
                "sub": (r.stripe_sub_id or "")[-8:],
                "status": r.status,
                "has_email": bool(r.app_email),
                "has_discord": bool(r.discord_id),
                "period_end": str(r.current_period_end)[:19] if r.current_period_end else None,
            })
        return out
    return out


def subscription_role_sets() -> dict:
    """{"grant": [...], "revoke": [...]} of Discord ids for role syncing.

    THE REVOKE LIST IS DELIBERATELY NARROW: it contains only people who have a
    Stripe subscription record that has LAPSED. Someone with no record at all
    never appears in either list.

    That distinction is the whole safety of this feature. The premium role is
    also granted by Discord's own server subscriptions, by comps and by hand,
    and a sync that revoked "everyone with the role who is not currently paying
    us through Stripe" would strip the role from every one of those people the
    first time it ran.
    """
    if not _READY or Subscription is None:
        return {"grant": [], "revoke": []}
    try:
        now = datetime.now(timezone.utc)
        grant, lapsed = set(), set()
        with _session() as s:
            for r in s.query(Subscription).all():
                did = (r.discord_id or "").strip()
                if not did:
                    continue
                end = r.current_period_end
                if end is not None and end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                # Must match billing.ACTIVE_STATUSES. past_due is NOT
                # entitled: a failed payment removes the role on the next sync.
                live = ((r.status or "") in ("active", "trialing")
                        and (end is None or end > now))
                (grant if live else lapsed).add(did)
        # Someone who resubscribed has both an old dead row and a live one.
        # Active always wins, so they are never revoked on the strength of a
        # superseded record.
        return {"grant": sorted(grant), "revoke": sorted(lapsed - grant)}
    except Exception:
        logger.exception("subscription_role_sets failed")
    return {"grant": [], "revoke": []}


def link_subscription(stripe_sub_id: str, discord_id: str = "",
                      email: str = "") -> bool:
    """Attach a Discord id or app email to an existing subscription — someone
    who paid before connecting either one."""
    if not _READY or Subscription is None or not stripe_sub_id:
        return False
    try:
        with _session() as s:
            row = (s.query(Subscription)
                    .filter(Subscription.stripe_sub_id == stripe_sub_id)
                    .one_or_none())
            if row is None:
                return False
            if discord_id:
                row.discord_id = discord_id
            if email:
                row.app_email = email
            row.updated_at = datetime.now(timezone.utc)
            return True
    except Exception:
        logger.exception("link_subscription failed")
    return False


def active_subscriber_discord_ids() -> list:
    """Discord ids entitled right now — what the bot syncs roles against."""
    if not _READY or Subscription is None:
        return []
    try:
        now = datetime.now(timezone.utc)
        out = []
        with _session() as s:
            for r in s.query(Subscription).all():
                if not r.discord_id:
                    continue
                if (r.status or "") not in ("active", "trialing"):
                    continue
                end = r.current_period_end
                if end is not None:
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    if end <= now:
                        continue
                out.append(r.discord_id)
        return sorted(set(out))
    except Exception:
        logger.exception("active_subscriber_discord_ids failed")
    return []


def cache_get(key: str):
    """Value for ``key``, or None on miss/expiry/DB-unavailable. TTL is enforced
    HERE on read: an expired row is a miss and the caller refetches, so a stale
    value can never be served just because it survived a restart."""
    if not _READY:
        return None
    try:
        with _session() as s:
            row = s.get(CacheEntry, key)
            if row is None:
                return None
            if row.ttl_seconds:
                age = (datetime.now(timezone.utc) - row.written_at).total_seconds()
                if age > row.ttl_seconds:
                    return None          # expired -> treat as a miss
            import json as _json
            return _json.loads(row.value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_get(%s) failed — treating as miss: %s", key, str(exc)[:120])
        return None


def cache_set(key: str, value, ttl_seconds: int = None) -> bool:
    """Write-through upsert. ttl_seconds=None means NEVER expires — correct for
    immutable data (a completed match's statistics cannot change).

    NOTE FOR CALLERS: this does not know whether ``value`` is trustworthy. The
    degraded-fetch guard must run BEFORE calling this — a degraded fetch must
    never overwrite a healthy row, exactly as it must never overwrite a healthy
    in-memory entry."""
    if not _READY:
        return False
    try:
        import json as _json
        payload = _json.dumps(value)
        with _session() as s:
            row = s.get(CacheEntry, key)
            if row is None:
                s.add(CacheEntry(cache_key=key, value=payload,
                                 ttl_seconds=ttl_seconds,
                                 written_at=datetime.now(timezone.utc)))
            else:
                row.value = payload
                row.ttl_seconds = ttl_seconds
                row.written_at = datetime.now(timezone.utc)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_set(%s) failed — memory-only this run: %s", key, str(exc)[:120])
        return False


def cache_stats() -> dict:
    """Row count + oldest/newest write — for verifying the layer is actually
    persisting rather than silently no-opping."""
    if not _READY:
        return {"ready": False, "rows": 0}
    try:
        with _session() as s:
            n = s.query(CacheEntry).count()
            return {"ready": True, "rows": n}
    except Exception:  # noqa: BLE001
        return {"ready": False, "rows": 0}


def _avg(vals: list):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _summarize(picks: list) -> dict:
    """Aggregate a set of pick rows (most-recent-first) into a record block."""
    wins = [p for p in picks if p["result"] == "W"]
    losses = [p for p in picks if p["result"] == "L"]
    pushes = [p for p in picks if p["result"] == "PUSH"]
    voids = [p for p in picks if p["result"] == "VOID"]
    # PUSH counts as a WIN (policy) — folded into the win-rate numerator AND the
    # denominator. VOID (cancelled / DNP) never played, so it stays out of both.
    # Denominator = W + L + PUSH.
    decided = wins + losses + pushes
    win_rate = round((len(wins) + len(pushes)) / len(decided) * 100, 1) if decided else 0.0

    # Current streak: walk decided picks newest→oldest, count consecutive sames.
    streak_type, streak_len = None, 0
    for p in picks:
        if p["result"] not in ("W", "L"):
            continue
        if streak_type is None:
            streak_type, streak_len = p["result"], 1
        elif p["result"] == streak_type:
            streak_len += 1
        else:
            break

    return {
        "picks": picks,
        "total": len(picks),
        "wins": len(wins),
        "losses": len(losses),
        "pushes": len(pushes),
        "voids": len(voids),
        "pending": len([p for p in picks if p["result"] == "PENDING"]),
        "needs_review": len([p for p in picks if p["result"] == "NEEDS REVIEW"]),
        "win_rate": win_rate,
        "avg_confidence_wins": _avg([p["confidence"] for p in wins]),
        "avg_confidence_losses": _avg([p["confidence"] for p in losses]),
        "streak_type": streak_type,
        "streak_len": streak_len,
    }


def _slip_record(threex_picks: list) -> dict:
    """Grade the 3x SLIP record from its individual legs. Both legs of a day's
    slip are logged together, so we group by generated_at date (one slip per
    day) and grade the pair:

      • both legs W        -> slip W
      • any leg L          -> slip L
      • a leg PUSHes       -> it drops out; the slip reduces to the remaining
                              leg(s) and is graded on those alone
      • all legs PUSH      -> slip PUSH
      • any leg unresolved -> slip still pending (not counted)
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for p in threex_picks:
        day = (p.get("generated_at") or "")[:10]
        groups[day].append(p)

    w = l = push = pending = 0
    for _day, legs in groups.items():
        results = [p["result"] for p in legs]
        if any(r in ("PENDING", "NEEDS REVIEW") for r in results):
            pending += 1
            continue
        graded = [r for r in results if r in ("W", "L")]  # PUSH / VOID legs drop out
        if not graded:
            push += 1                     # every leg pushed
        elif any(r == "L" for r in graded):
            l += 1                        # both legs must hit — one miss = loss
        else:
            w += 1
    # PUSH counts as a WIN (policy): an all-push slip is a win, and pushes sit in
    # both the numerator and the denominator. Raw w / l / push counts are kept for
    # display; only the rate folds pushes in.
    decided = w + l + push
    return {
        "slips": len(groups),
        "wins": w,
        "losses": l,
        "pushes": push,
        "pending": pending,
        "win_rate": round((w + push) / decided * 100, 1) if decided else 0.0,
    }


# A pick's SOURCE is the book its board came from, derived from pick_group.
# Everything that is not explicitly another book is PrizePicks, so legacy rows
# (potd / second-wave / 3x / NULL) keep counting exactly where they always have.
# Underdog groups are namespaced with an "underdog" prefix ("underdog",
# "underdog-wave", "underdog-3x", ...) so a new Underdog product needs no change
# here. Sources are scored SEPARATELY: mixing two books' hit rates into one
# number would make both meaningless.
SOURCE_PREFIXES = {"underdog": "underdog"}


def pick_source(p: dict) -> str:
    g = (p.get("pick_group") or "potd").lower()
    for prefix, src in SOURCE_PREFIXES.items():
        if g.startswith(prefix):
            return src
    return "prizepicks"


def record_summary() -> dict:
    """Aggregate record, split by SOURCE then by pick group.

    Top-level fields describe the PrizePicks Pick of the Day (the headline
    product; legacy NULL-group rows count here); ``threex_legs`` is the
    individual-leg record and ``threex_slips`` the paired slip record for the 3x.

    ``underdog`` carries the Underdog board's own record in the same shape as the
    top level (picks / wins / losses / win_rate / ...). It is deliberately NOT
    folded into the headline numbers — a second book has its own lines, its own
    market, and must earn its own track record before it can be quoted alongside
    the first."""
    # EXCLUDE superseded / duplicate records (excluded_from_record=1) from every
    # record computation and from the recap's pick list. They remain in the DB
    # (all_picks) for the audit, just invisible to the public record.
    picks = [p for p in all_picks() if not p.get("excluded_from_record")]

    def _grp(p):
        return (p.get("pick_group") or "potd").lower()

    pp = [p for p in picks if pick_source(p) == "prizepicks"]
    ud = [p for p in picks if pick_source(p) == "underdog"]

    potd = [p for p in pp if _grp(p) != "3x"]
    threex = [p for p in pp if _grp(p) == "3x"]

    summary = _summarize(potd)
    summary["threex_legs"] = _summarize(threex)
    summary["threex_slips"] = _slip_record(threex)
    # Underdog, scored on its own. Same shape as the top level so any caller that
    # can render the PrizePicks record can render this one unchanged.
    summary["underdog"] = _summarize([p for p in ud if _grp(p) != "underdog-3x"])
    summary["underdog"]["threex_legs"] = _summarize(
        [p for p in ud if _grp(p) == "underdog-3x"])

    # Standard vs demon segmentation (weekly report line). Compact — counts and
    # win rate only, not the full pick lists, so the payload stays small.
    def _seg(rows):
        s = _summarize(rows)
        return {"total": s["total"], "wins": s["wins"], "losses": s["losses"],
                "win_rate": s["win_rate"], "avg_confidence_wins": s["avg_confidence_wins"]}
    # Scoped to PrizePicks: "demon" is a PrizePicks concept and Underdog carries
    # no equivalent, so mixing its rows in would quietly dilute the standard bucket.
    summary["by_odds_type"] = {
        "standard": _seg([p for p in pp if (p.get("odds_type") or "standard") == "standard"]),
        "demon":    _seg([p for p in pp if (p.get("odds_type") or "standard") == "demon"]),
    }
    return summary


# ── Anonymous preview window ────────────────────────────────────────────────
def preview_reset(visitor_key: str) -> bool:
    """Clear one visitor's preview clock. Support use: someone who genuinely lost
    their window to a shared office IP has no other way back."""
    if not _READY or PreviewSession is None:
        return False
    try:
        with _session() as s:
            row = (s.query(PreviewSession)
                     .filter(PreviewSession.visitor_key == visitor_key).one_or_none())
            if row is None:
                return False
            s.delete(row)
            return True
    except Exception:  # noqa: BLE001
        logger.exception("preview_reset failed")
        return False


def preview_touch(visitor_key: str, window_seconds: int,
                  reset_hours: float = 0.0) -> dict:
    """Start or read an anonymous visitor's free-look clock.

    THE CLOCK STARTS ON FIRST CONTACT AND NEVER RESTARTS. first_seen is written
    once and only read afterwards, so a refresh, a new tab, a private window and
    a cleared browser all land on the same row and the same deadline. That is the
    entire point: a browser-held timer resets in all four cases.

    Returns remaining seconds and whether the window is still open. Fails OPEN
    (grants the window) when the DB is unavailable — a database outage should
    not lock every visitor out of the marketing preview.
    """
    from datetime import datetime, timezone
    if not _READY or PreviewSession is None:
        return {"ok": False, "allowed": True, "remaining": window_seconds,
                "reason": "preview store unavailable — failing open"}
    try:
        with _session() as s:
            row = (s.query(PreviewSession)
                     .filter(PreviewSession.visitor_key == visitor_key).one_or_none())
            now = datetime.now(timezone.utc)
            if row is None:
                row = PreviewSession(visitor_key=visitor_key, hits=1)
                s.add(row)
                s.flush()
                started = row.first_seen or now
            else:
                started = row.first_seen or now
                row.hits = (row.hits or 0) + 1
                row.last_seen = now
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
            # A LAPSED WINDOW MAY REOPEN. reset_hours=0 means the free look is
            # once ever, which is the strictest reading and also punishes the
            # person who browsed for 40 seconds, got interrupted, and came back
            # next week to a wall. Above 0, the clock restarts once that long has
            # passed since it began.
            if reset_hours > 0 and elapsed > reset_hours * 3600.0:
                row.first_seen = now
                row.hits = 1
                started, elapsed = now, 0.0
            remaining = max(0.0, window_seconds - elapsed)
            return {"ok": True, "allowed": remaining > 0,
                    "remaining": int(remaining), "elapsed": int(elapsed),
                    "hits": row.hits or 1}
    except Exception:  # noqa: BLE001
        logger.exception("preview_touch failed")
        return {"ok": False, "allowed": True, "remaining": window_seconds,
                "reason": "preview lookup failed — failing open"}


def trials_from_ip(ip: str) -> list:
    """Every subscription already started from this address.

    The anti-abuse read: a second free trial from an address that has one is the
    signal, and email alone cannot see it because a new address is free to make.
    """
    if not _READY or Subscription is None or not ip:
        return []
    try:
        with _session() as s:
            rows = (s.query(Subscription)
                      .filter(Subscription.signup_ip == ip)
                      .order_by(Subscription.created_at.desc()).all())
            return [{"stripe_sub_id": r.stripe_sub_id, "status": r.status,
                     "app_email": r.app_email, "discord_id": r.discord_id,
                     "created_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]
    except Exception:  # noqa: BLE001
        logger.exception("trials_from_ip failed")
        return []


def set_signup_ip(stripe_sub_id: str, ip: str) -> bool:
    """Stamp the signup IP onto a subscription once it exists.

    Separate from upsert_subscription because the IP is known at CHECKOUT time
    (the browser is talking to us) while the subscription id only exists after
    Stripe's webhook — the two facts arrive from different directions.
    """
    if not _READY or Subscription is None or not (stripe_sub_id and ip):
        return False
    try:
        with _session() as s:
            row = (s.query(Subscription)
                     .filter(Subscription.stripe_sub_id == stripe_sub_id).one_or_none())
            if row is None:
                return False
            if not (row.signup_ip or ""):
                row.signup_ip = ip[:64]
            return True
    except Exception:  # noqa: BLE001
        logger.exception("set_signup_ip failed")
        return False
