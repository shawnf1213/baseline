"""
NFL pick persistence — what was posted, and how it turned out.

Mirrors mlb/store.py: same dedicated-env-var rule, same degrade-never-raise
contract, its own table.

WHY A DATABASE AND NOT THE JSON FILE
-------------------------------------
nfl/board.py already keeps posted.json so a second board does not repeat the
first. That file lives in the container's cache directory, which Railway keeps
only for the life of a deploy — fine for "did I already post this today",
useless for a record. A recap has to survive a redeploy or it is not a record of
anything, and this project redeploys several times an hour on an active day.

CONNECTION: reads NFL_DATABASE_URL only, with NO fallback to DATABASE_URL — the
same reasoning mlb/store.py sets out. DATABASE_URL is whatever Railway injects
into whichever service happens to run the code, so a fallback would silently
write picks to different databases depending on the caller. Unset disables the
store entirely and every function here returns an empty result.

DEGRADES, NEVER RAISES. With no store configured the board still scans, still
posts, and still hides repeats via posted.json; it simply keeps no record. A
missing database must cost the recap, never the board.

TWO BACKENDS, AND THE FILE IS THE DEFAULT
------------------------------------------
Railway's Postgres is published on postgres.railway.internal, which resolves
ONLY inside a Railway container and has no TCP proxy enabled. So the deployed
bot can reach it and this machine cannot — and the operator asked to be able to
run a recap on request and read it here, which happens on this machine.

A database nobody can query from where the question gets asked is not a store.
So the primary backend is a JSON file COMMITTED TO THE REPO, which both sides
can read: boards posted from a laptop write it directly, and the container gets
it on the next deploy. Postgres is used IN ADDITION when it is reachable, so the
scheduled in-container board is not lost either.

The file's real limitation, stated rather than buried: a board posted BY THE
CONTAINER writes to Postgres, and this machine will not see those rows until
the Postgres host is reachable from outside (a Railway TCP proxy would fix it).
Boards posted from here are always visible.
"""

import logging
import os

log = logging.getLogger("baseline.nfl.store")

# Dedicated var, no fallback. See the module docstring.
NFL_DATABASE_URL = (os.getenv("NFL_DATABASE_URL", "") or "").replace(
    "postgres://", "postgresql://", 1)

# Committed, not cached: this is the record, and it has to survive a redeploy
# and be readable from a laptop. ~200 bytes a pick.
FILE_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_data", "picks.json")

_engine = None
_Session = None
_NflPick = None
_init_failed = False


def _init() -> bool:
    """Lazy engine + table creation. True when the store is usable."""
    global _engine, _Session, _NflPick, _init_failed
    if _NflPick is not None:
        return True
    if _init_failed or not NFL_DATABASE_URL:
        return False
    try:
        from sqlalchemy import (create_engine, Column, Integer, String, Float,
                                DateTime, func)
        from sqlalchemy.orm import declarative_base, sessionmaker

        Base = declarative_base()

        class NflPick(Base):
            __tablename__ = "nfl_picks"
            id = Column(Integer, primary_key=True, autoincrement=True)
            # Carried explicitly even though the table is single-sport, so a
            # future consolidation is a UNION rather than a backfill (Rule 3).
            sport = Column(String, default="nfl", nullable=False)
            book = Column(String, nullable=False)
            slate_date = Column(String, nullable=False)      # ET YYYY-MM-DD
            player = Column(String, nullable=False)
            team = Column(String, default="")
            opponent = Column(String, default="")
            prop_type = Column(String, nullable=False)
            line = Column(Float)
            projection = Column(Float)
            lean = Column(String)
            probability = Column(Float)
            edge = Column(Float)
            is_potd = Column(Integer, default=0)
            result = Column(String, default="PENDING")   # W/L/PUSH/VOID/PENDING
            result_value = Column(Float)
            # WHAT THE MODEL KNEW WHEN IT PRICED THIS. A record that cannot say
            # whether a pick was made on prior-season usage is a record that
            # cannot answer the only question worth asking of this board.
            window = Column(String, default="")
            prior_season_only = Column(Integer, default=0)
            shadow = Column(Integer, default=1)
            season = Column(Integer)
            week = Column(Integer)
            posted_at = Column(DateTime(timezone=True), server_default=func.now())
            resolved_at = Column(DateTime(timezone=True), nullable=True)

        eng = create_engine(NFL_DATABASE_URL, pool_pre_ping=True,
                            pool_recycle=300)
        Base.metadata.create_all(eng)        # only creates nfl_picks
        _engine, _Session, _NflPick = eng, sessionmaker(bind=eng), NflPick
        log.info("nfl store: nfl_picks ready")
        return True
    except Exception as exc:  # noqa: BLE001 — Rule 2
        _init_failed = True
        log.warning("nfl store unavailable (%s) — NFL runs without persistence; "
                    "tennis and MLB are unaffected", str(exc)[:160])
        return False


def available() -> bool:
    """True when SOMETHING can record a pick. The file always can."""
    return True


# ── FILE BACKEND ─────────────────────────────────────────────────────────────
def _file_load() -> list:
    import json
    try:
        with open(FILE_STORE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else (d.get("picks") or [])
    except Exception:  # noqa: BLE001 — missing or corrupt is not an error
        return []


def _file_save(rows: list) -> bool:
    import json
    try:
        os.makedirs(os.path.dirname(FILE_STORE), exist_ok=True)
        tmp = FILE_STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, default=str)
        os.replace(tmp, FILE_STORE)      # atomic; a killed write never truncates
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl store: could not write %s: %s", FILE_STORE, str(exc)[:120])
        return False


def _next_id(rows: list) -> int:
    return (max((int(r.get("id") or 0) for r in rows), default=0) + 1)


def log_board(rows: list, book: str, slate_date: str, potd_player=None,
              shadow: bool = True, season=None, week=None) -> int:
    """Persist a posted board to the file, and to Postgres when reachable.

    DEDUPES on (book, slate_date, player, prop_type). Re-posting or a retry must
    not double-count a play into the record — the easiest way there is to make a
    win rate look better than it was.
    """
    if not rows:
        return 0
    written = 0
    try:
        from . import client as _c
        season = season or _c.current_season()
        week = week or _c.current_week()
        have = _file_load()
        seen = {(r.get("book"), r.get("slate_date"), r.get("player"),
                 r.get("prop_type")) for r in have}
        nid = _next_id(have)
        for r in rows:
            key = (book, slate_date, r.get("player"), r.get("prop"))
            if not key[2] or key in seen:
                continue
            seen.add(key)
            have.append({
                "id": nid, "sport": "nfl", "book": book,
                "slate_date": slate_date, "player": r.get("player"),
                "team": r.get("team") or "", "opponent": r.get("opponent") or "",
                "prop_type": r.get("prop"), "line": r.get("line"),
                "projection": r.get("projection"), "lean": r.get("lean"),
                "probability": r.get("win_prob"), "edge": r.get("edge"),
                "is_potd": 1 if r.get("player") == potd_player else 0,
                "result": "PENDING", "result_value": None,
                "window": r.get("window") or "",
                "prior_season_only": 1 if r.get("prior_season_only") else 0,
                "shadow": 1 if shadow else 0,
                "season": season, "week": week,
                "posted_at": _now_iso(), "resolved_at": None,
            })
            nid += 1
            written += 1
        if written:
            _file_save(have)
        log.info("nfl store: logged %d pick(s) for %s %s (file)",
                 written, book, slate_date)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl store log_board (file) failed: %s", exc)

    # Postgres too, when it is reachable — so the scheduled in-container board
    # is recorded even though this machine cannot read that database.
    if _init():
        try:
            from . import client as _c
            with _Session() as s:
                existing = {(r.player, r.prop_type) for r in
                            s.query(_NflPick).filter(
                                _NflPick.book == book,
                                _NflPick.slate_date == slate_date)}
                for r in rows:
                    k = (r.get("player"), r.get("prop"))
                    if not k[0] or k in existing:
                        continue
                    existing.add(k)
                    s.add(_NflPick(
                        book=book, slate_date=slate_date, player=r.get("player"),
                        team=r.get("team") or "", opponent=r.get("opponent") or "",
                        prop_type=r.get("prop"), line=r.get("line"),
                        projection=r.get("projection"), lean=r.get("lean"),
                        probability=r.get("win_prob"), edge=r.get("edge"),
                        is_potd=1 if r.get("player") == potd_player else 0,
                        window=r.get("window") or "",
                        prior_season_only=1 if r.get("prior_season_only") else 0,
                        shadow=1 if shadow else 0,
                        season=season or _c.current_season(),
                        week=week or _c.current_week()))
                s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("nfl store: postgres mirror failed (%s) — the file "
                        "record is unaffected", str(exc)[:120])
    return written


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def pending(book: str = None, slate_date: str = None) -> list:
    rows = [r for r in _file_load() if (r.get("result") or "PENDING") == "PENDING"]
    if book:
        rows = [r for r in rows if r.get("book") == book]
    if slate_date:
        rows = [r for r in rows if r.get("slate_date") == slate_date]
    return sorted(rows, key=lambda r: int(r.get("id") or 0))


def update_result(pick_id, result: str, value=None) -> bool:
    rows = _file_load()
    hit = False
    for r in rows:
        if int(r.get("id") or -1) == int(pick_id):
            r["result"] = result
            r["result_value"] = value
            r["resolved_at"] = _now_iso()
            hit = True
            break
    if hit:
        _file_save(rows)
    return hit


def picks_for(slate_date: str = None, book: str = None,
              since_days: int = None) -> list:
    import datetime
    rows = _file_load()
    if slate_date:
        rows = [r for r in rows if r.get("slate_date") == slate_date]
    if book:
        rows = [r for r in rows if r.get("book") == book]
    if since_days:
        cut = (datetime.date.today()
               - datetime.timedelta(days=int(since_days))).isoformat()
        rows = [r for r in rows if str(r.get("slate_date") or "") >= cut]
    return sorted(rows, key=lambda r: (str(r.get("slate_date")),
                                       int(r.get("id") or 0)))


def summary() -> dict:
    rows = _file_load()
    out = {"available": True, "file": FILE_STORE, "postgres": _init(),
           "total": len(rows)}
    for res in ("PENDING", "W", "L", "PUSH", "VOID"):
        out[res] = sum(1 for r in rows if (r.get("result") or "PENDING") == res)
    return out


def _to_dict(r) -> dict:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}
