"""
MLB results store — a SEPARATE table, never the tennis one.

WHY A SEPARATE TABLE RATHER THAN A `sport` COLUMN ON `picks`
------------------------------------------------------------
Rule 3 says records never pool across sports and prescribes a `sport` field on
every record. Adding that column to the live `picks` table means a migration on
tennis's production data and a sweep of every tennis record query — real risk to
a live product, for a sport that has not graded a single pick.

`mlb_picks` achieves Rule 3's INTENT more strongly than a shared column does:
tennis and MLB cannot pool because they are not in the same table. Every row
still carries `sport` explicitly, so if the two are ever consolidated the column
is already populated and the merge is a UNION, not a backfill.

Nothing here imports backend/src/database.py. That module is tennis's, and
importing it would give MLB a dependency on tennis code — the coupling direction
Rule 2 exists to prevent. This owns its own engine and its own metadata.

CONNECTION: reads MLB_DATABASE_URL only. There is deliberately no fallback to
DATABASE_URL — see the constant below.

DEGRADES, NEVER RAISES: with no MLB_DATABASE_URL every function returns an
empty/false result and logs once. An MLB storage outage must be invisible to
tennis.
"""

import os
import logging
import datetime as _dt

log = logging.getLogger("baseline.mlb.store")

# DEDICATED env var — deliberately NOT `DATABASE_URL`, and with NO fallback to it.
#
# `DATABASE_URL` is whatever Railway injects into the service, and on the backend
# that is the TENNIS database. Falling back to it would silently create mlb_picks
# inside tennis's Postgres — records still would not pool (different table), but
# it is not the isolation Shawn provisioned, and which DB won would depend on
# which service happened to run the code. An unset MLB_DATABASE_URL disables the
# store instead, which is loud and safe.
MLB_DATABASE_URL = (os.getenv("MLB_DATABASE_URL", "") or "").replace(
    "postgres://", "postgresql://", 1)

_engine = None
_Session = None
_MlbPick = None
_init_failed = False


def _init():
    """Lazy engine + table creation. Returns True when the store is usable."""
    global _engine, _Session, _MlbPick, _init_failed
    if _MlbPick is not None:
        return True
    if _init_failed or not MLB_DATABASE_URL:
        return False
    try:
        from sqlalchemy import (create_engine, Column, Integer, String, Float,
                                DateTime, func)
        from sqlalchemy.orm import declarative_base, sessionmaker

        Base = declarative_base()

        class MlbPick(Base):
            __tablename__ = "mlb_picks"          # NOT `picks` — see module docstring
            id = Column(Integer, primary_key=True, autoincrement=True)
            # Rule 3: carried explicitly even though the table is already
            # single-sport, so a future consolidation is a UNION not a backfill.
            sport = Column(String, default="mlb", nullable=False)
            book = Column(String, nullable=False)          # prizepicks | underdog
            slate_date = Column(String, nullable=False)     # ET YYYY-MM-DD
            pitcher = Column(String, nullable=False)
            pitcher_id = Column(Integer)
            opponent = Column(String, default="")
            prop_type = Column(String, default="strikeouts")
            line = Column(Float)
            projection = Column(Float)
            lean = Column(String)
            probability = Column(Float)      # model prob on its own side
            market_prob = Column(Float)      # de-vigged, None on PrizePicks
            edge_vs_market = Column(Float)
            is_potd = Column(Integer, default=0)
            result = Column(String, default="PENDING")   # W/L/PUSH/VOID/PENDING
            result_value = Column(Float)
            shadow = Column(Integer, default=1)
            game_pk = Column(Integer)
            posted_at = Column(DateTime(timezone=True), server_default=func.now())
            resolved_at = Column(DateTime(timezone=True), nullable=True)

        eng = create_engine(MLB_DATABASE_URL, pool_pre_ping=True,
                            pool_recycle=300)
        Base.metadata.create_all(eng)        # only creates mlb_picks
        _engine = eng
        _Session = sessionmaker(bind=eng)
        _MlbPick = MlbPick
        log.info("mlb store: mlb_picks ready")
        return True
    except Exception as exc:  # noqa: BLE001
        _init_failed = True
        log.warning("mlb store unavailable (%s) — MLB will run without "
                    "persistence; tennis is unaffected", str(exc)[:160])
        return False


def available() -> bool:
    return _init()


def _side_prob(row: dict):
    return row.get("p_over") if row.get("lean") == "OVER" else row.get("p_under")


def log_board(rows: list, book: str, slate_date: str, potd_key=None,
              shadow: bool = True) -> int:
    """Persist one book's posted board. Returns rows written (0 if unavailable).

    Only plays with a real line are stored: a projection with no market has
    nothing to grade against, and storing it would pad the denominator with
    picks that were never actionable.

    Idempotent per (book, slate_date, player, prop_type): re-posting a board
    updates the existing row rather than double-counting it, which is the failure
    that hit the tennis record when a board was posted twice.

    PROP_TYPE IS PART OF THE KEY, not decoration. One pitcher now carries up to
    five props on the same slate; keying on the player alone would make each
    prop overwrite the last and store one row where five were posted.
    """
    if not _init():
        return 0
    try:
        s = _Session()
        n = 0
        try:
            for r in rows:
                if r.get("line") is None:
                    continue
                # Batter rows carry `player`; the pitcher path carries `pitcher`.
                # The column is named `pitcher` for history and now holds either.
                who = r.get("player") or r.get("pitcher")
                prop = r.get("prop") or "strikeouts"
                existing = (s.query(_MlbPick)
                            .filter_by(book=book, slate_date=slate_date,
                                       pitcher=who, prop_type=prop)
                            .one_or_none())
                vals = dict(
                    sport="mlb", book=book, slate_date=slate_date,
                    pitcher=who,
                    pitcher_id=r.get("pitcher_id") or r.get("batter_id"),
                    opponent=r.get("opponent", ""), prop_type=prop,
                    line=r.get("line"), projection=r.get("projection"),
                    lean=r.get("lean"), probability=_side_prob(r),
                    market_prob=(r.get("market_p_over")
                                 if r.get("lean") == "OVER"
                                 else r.get("market_p_under")),
                    edge_vs_market=r.get("edge_vs_market"),
                    is_potd=1 if (potd_key and (who, prop) == potd_key) else 0,
                    shadow=1 if shadow else 0, game_pk=r.get("game_pk"),
                )
                if existing:
                    for k, v in vals.items():
                        setattr(existing, k, v)
                else:
                    s.add(_MlbPick(**vals))
                n += 1
            s.commit()
        finally:
            s.close()
        log.info("mlb store: wrote %d %s rows for %s", n, book, slate_date)
        return n
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store log_board failed: %s", exc)
        return 0


def pending(book: str = None) -> list:
    """Ungraded rows, optionally for one book."""
    if not _init():
        return []
    try:
        s = _Session()
        try:
            q = s.query(_MlbPick).filter(_MlbPick.result == "PENDING")
            if book:
                q = q.filter(_MlbPick.book == book)
            return [_to_dict(r) for r in q.all()]
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store pending failed: %s", exc)
        return []


def update_result(pick_id: int, result: str, value=None) -> bool:
    if not _init():
        return False
    try:
        s = _Session()
        try:
            row = s.query(_MlbPick).filter_by(id=int(pick_id)).one_or_none()
            if not row:
                return False
            row.result = result
            if value is not None:
                row.result_value = float(value)
            row.resolved_at = _dt.datetime.now(_dt.timezone.utc)
            s.commit()
            return True
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store update_result failed: %s", exc)
        return False


def board_for(book: str, slate_date: str) -> list:
    """Every stored row for one book's slate, best play first."""
    if not _init():
        return []
    try:
        s = _Session()
        try:
            rows = (s.query(_MlbPick)
                    .filter_by(book=book, slate_date=slate_date).all())
            out = [_to_dict(r) for r in rows]
            out.sort(key=lambda r: -(r.get("probability") or 0))
            return out
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store board_for failed: %s", exc)
        return []


def record(book: str, since_days: int = None) -> dict:
    """Aggregate record for ONE book. Never mixes books, never mixes sports.

    Cashed convention matches tennis: W+PUSH over W+L+PUSH, VOID excluded from
    both sides — so the two sports' rates mean the same thing even though they
    are stored apart.
    """
    if not _init():
        return {}
    try:
        s = _Session()
        try:
            q = s.query(_MlbPick).filter_by(book=book, sport="mlb")
            if since_days:
                cut = (_dt.date.today()
                       - _dt.timedelta(days=since_days)).isoformat()
                q = q.filter(_MlbPick.slate_date >= cut)
            rows = [_to_dict(r) for r in q.all()]
        finally:
            s.close()
        w = sum(1 for r in rows if r["result"] in ("W", "PUSH"))
        l = sum(1 for r in rows if r["result"] == "L")
        v = sum(1 for r in rows if r["result"] == "VOID")
        p = sum(1 for r in rows if r["result"] == "PENDING")
        dec = w + l
        return {"book": book, "sport": "mlb", "total": len(rows), "wins": w,
                "losses": l, "voids": v, "pending": p,
                "win_rate": round(w / dec * 100, 1) if dec else None,
                "picks": rows}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store record failed: %s", exc)
        return {}


def purge_slate(slate_date: str, book: str = None) -> int:
    """Delete every stored row for one slate. Returns rows removed.

    Exists to clear fabricated test data (see recap.force_resolve_all) before it
    can pollute a real record. Deliberately targets an EXPLICIT slate_date rather
    than "anything that looks like a test": a rule that guesses which rows are
    fake would eventually delete a real one.
    """
    if not _init():
        return 0
    try:
        s = _Session()
        try:
            q = s.query(_MlbPick).filter_by(slate_date=slate_date)
            if book:
                q = q.filter_by(book=book)
            n = q.count()
            q.delete(synchronize_session=False)
            s.commit()
            log.warning("mlb store: PURGED %d row(s) for slate %s%s",
                        n, slate_date, f" (book={book})" if book else "")
            return n
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store purge_slate failed: %s", exc)
        return 0


def _to_dict(r) -> dict:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}
