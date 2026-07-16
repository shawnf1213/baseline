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

    _SQLALCHEMY_OK = True
except Exception as exc:  # pragma: no cover — missing dep shouldn't crash the app
    logger.warning("SQLAlchemy unavailable — results DB disabled: %s", exc)
    _SQLALCHEMY_OK = False
    Pick = None  # type: ignore
    CacheEntry = None  # type: ignore


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
            )
            s.add(row)
            s.flush()
            return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("log_pick failed: %s", exc)
        return {}


def update_result(pick_id: int, result: str) -> bool:
    """Set the result (W/L/PENDING/NEEDS REVIEW) and resolved_at. Returns success."""
    if not _READY:
        return False
    try:
        with _session() as s:
            row = s.get(Pick, int(pick_id))
            if row is None:
                return False
            row.result = (result or "").upper()
            row.resolved_at = datetime.now(timezone.utc)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("update_result failed: %s", exc)
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
    # PUSH and VOID (cancelled / DNP) are neither a win nor a loss — excluded from
    # the win-rate denominator (decided = W + L only), but still shown in the log.
    decided = wins + losses
    win_rate = round(len(wins) / len(decided) * 100, 1) if decided else 0.0

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
    decided = w + l
    return {
        "slips": len(groups),
        "wins": w,
        "losses": l,
        "pushes": push,
        "pending": pending,
        "win_rate": round(w / decided * 100, 1) if decided else 0.0,
    }


def record_summary() -> dict:
    """Aggregate record, split by pick group. Top-level fields describe the
    Pick of the Day (the headline product; legacy NULL-group rows count here);
    ``threex_legs`` is the individual-leg record and ``threex_slips`` is the
    paired slip record for the 3x."""
    picks = all_picks()  # most recent first

    def _grp(p):
        return (p.get("pick_group") or "potd").lower()

    potd = [p for p in picks if _grp(p) != "3x"]
    threex = [p for p in picks if _grp(p) == "3x"]

    summary = _summarize(potd)
    summary["threex_legs"] = _summarize(threex)
    summary["threex_slips"] = _slip_record(threex)
    return summary
