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
        result           = Column(String, default="PENDING")  # W/L/PENDING/NEEDS REVIEW
        generated_at     = Column(DateTime(timezone=True), server_default=func.now())
        resolved_at      = Column(DateTime(timezone=True), nullable=True)
        original_line    = Column(Float)
        tournament       = Column(String, default="")
        surface          = Column(String, default="")

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
            }

    _SQLALCHEMY_OK = True
except Exception as exc:  # pragma: no cover — missing dep shouldn't crash the app
    logger.warning("SQLAlchemy unavailable — results DB disabled: %s", exc)
    _SQLALCHEMY_OK = False
    Pick = None  # type: ignore


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


def _avg(vals: list):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def record_summary() -> dict:
    """Full log plus aggregate record: wins/losses/win-rate, avg confidence on
    winners vs losers, and current streak."""
    picks = all_picks()  # most recent first
    wins = [p for p in picks if p["result"] == "W"]
    losses = [p for p in picks if p["result"] == "L"]
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
        "pending": len([p for p in picks if p["result"] == "PENDING"]),
        "needs_review": len([p for p in picks if p["result"] == "NEEDS REVIEW"]),
        "win_rate": win_rate,
        "avg_confidence_wins": _avg([p["confidence"] for p in wins]),
        "avg_confidence_losses": _avg([p["confidence"] for p in losses]),
        "streak_type": streak_type,
        "streak_len": streak_len,
    }
