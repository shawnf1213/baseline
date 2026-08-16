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
                            .filter_by(sport="mlb", book=book,
                                       slate_date=slate_date,
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
            q = s.query(_MlbPick).filter(_MlbPick.sport == "mlb",
                                         _MlbPick.result == "PENDING")
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
                    .filter_by(sport="mlb", book=book,
                               slate_date=slate_date).all())
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
            q = s.query(_MlbPick).filter_by(sport="mlb", slate_date=slate_date)
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


def board_state(book: str, slate_date: str) -> dict:
    """What this book's board ALREADY contains for a slate.

    {"players": {name, ...}, "games": {game_pk: count}}

    KEYED ON THE PLAYER, NOT (player, prop). Keying on the pair was the bug that
    filled the 8/10 recap with plays nobody saw on a board: the 11:30 PM run
    boarded Andrew Painter's earned runs, and because ("Painter","earned_runs")
    was taken but "Painter" was not, the 9 AM run boarded his Fantasy Score as
    though it were a different play. It is not — it is the same six innings
    priced twice, which is exactly what the one-prop-per-player rule exists to
    stop. Across two runs a 13-play card became 32 stored rows.

    Game counts come back too, so the two-players-per-game cap survives across
    runs instead of resetting each time.
    """
    out = {"players": set(), "games": {}}
    if not _init():
        return out
    try:
        s = _Session()
        try:
            for r in (s.query(_MlbPick)
                       .filter_by(sport="mlb", book=book,
                                  slate_date=slate_date).all()):
                if r.pitcher:
                    out["players"].add(r.pitcher)
                if r.game_pk:
                    out["games"][r.game_pk] = out["games"].get(r.game_pk, 0) + 1
            return out
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store board_state failed: %s", exc)
        return out


def dedupe_record(slate_date: str = None, book: str = None,
                  dry_run: bool = True) -> dict:
    """Remove stored plays that the CURRENT dedupe rules would never have posted.

    Boards written before the cross-run fix could carry the same pitcher twice on
    one card — his strikeouts from the 11:30 PM board and his earned runs from
    the 9 AM top-up. Those are not two results, they are one start counted twice,
    and leaving them in inflates the sample and correlates the record with itself.

    KEEPS THE STRONGEST AND DROPS THE REST, per (book, slate):
      - one row per PLAYER, the one with the highest model probability
      - at most MAX_PER_GAME players per game

    Deliberately does NOT cap at MAX_PLAYS. A slate legitimately holds a 12-play
    primary board plus a 6-play top-up; trimming to 12 would delete plays that
    really were posted.

    dry_run=True by default — it reports what it would remove and changes
    nothing. Deleting graded history is not something to do on a typo.
    """
    from .post import MAX_PER_GAME
    out = {"examined": 0, "removed": 0, "kept": 0, "dry_run": dry_run,
           "details": []}
    if not _init():
        return out
    try:
        s = _Session()
        try:
            q = s.query(_MlbPick).filter_by(sport="mlb")
            if slate_date:
                q = q.filter_by(slate_date=slate_date)
            if book:
                q = q.filter_by(book=book)
            rows = q.all()
            out["examined"] = len(rows)

            groups = {}
            for r in rows:
                groups.setdefault((r.book, r.slate_date), []).append(r)

            doomed = []
            for (bk, slate), grp in sorted(groups.items()):
                # Best-first, exactly how the board ranked them. A row with no
                # probability sorts last rather than winning by accident.
                grp.sort(key=lambda r: -(r.probability or 0))
                seen_players, per_game = set(), {}
                for r in grp:
                    who = r.pitcher
                    key = r.game_pk or ("solo", who)
                    if who in seen_players:
                        doomed.append((r, f"{who} already kept on another prop"))
                        continue
                    if per_game.get(key, 0) >= MAX_PER_GAME:
                        doomed.append((r, f"game {r.game_pk} already has "
                                          f"{MAX_PER_GAME} players"))
                        continue
                    seen_players.add(who)
                    per_game[key] = per_game.get(key, 0) + 1
                    out["kept"] += 1

            for r, why in doomed:
                out["details"].append(
                    f"{r.book} {r.slate_date} {r.pitcher} {r.prop_type} "
                    f"{r.lean} {r.line} [{r.result}] — {why}")
            out["removed"] = len(doomed)

            if not dry_run and doomed:
                for r, _ in doomed:
                    s.delete(r)
                s.commit()
                log.warning("mlb store: DEDUPED RECORD — removed %d duplicate "
                            "row(s), kept %d", out["removed"], out["kept"])
            else:
                log.info("mlb store: dedupe_record DRY RUN — would remove %d, "
                         "keep %d", out["removed"], out["kept"])
            return out
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store dedupe_record failed: %s", exc)
        return out


def reset_slate(slate_date: str, book: str = None) -> int:
    """Set every graded row on a slate back to PENDING so it re-settles.

    A repair, not a purge: the picks stay exactly as posted — player, prop, line,
    lean, projection — and only the RESULT is cleared. The next resolve pass then
    grades them against final stats.

    Needed because results graded before the finality gate may have been settled
    on a live, partial game log: Zac Thornton was recorded a WIN on 2 earned runs
    in the 6th and finished with 3. Those rows carry real-looking numbers, so
    there is no way to spot them by inspection — the whole slate has to re-settle.
    """
    if not _init():
        return 0
    try:
        s = _Session()
        try:
            q = s.query(_MlbPick).filter_by(sport="mlb", slate_date=slate_date)
            if book:
                q = q.filter_by(book=book)
            rows = [r for r in q.all()
                    if (r.result or "PENDING") != "PENDING"]
            for r in rows:
                r.result = "PENDING"
                r.result_value = None
                r.resolved_at = None
            s.commit()
            log.warning("mlb store: RESET %d graded row(s) on %s%s — they will "
                        "re-settle against final stats", len(rows), slate_date,
                        f" (book={book})" if book else "")
            return len(rows)
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store reset_slate failed: %s", exc)
        return 0


def purge_all(confirm: str = "") -> int:
    """Delete EVERY row in mlb_picks. Returns rows removed.

    Wipes the whole MLB record — board history, graded results, and the rolling
    30-day figures that are computed from them. Irreversible.

    Requires confirm="DELETE ALL MLB HISTORY" so it cannot fire from a stray call
    or a mistyped env var. purge_slate() remains the right tool for clearing one
    day; this exists for resetting the record before a real launch, when the
    stored history is test data and its win rate is meaningless.

    ONLY TOUCHES mlb_picks. It is a table-scoped delete on the MLB model, so it
    cannot reach a tennis table even though it is unfiltered — and MLB_DATABASE_URL
    points at MLB's own database in any case.
    """
    if confirm != "DELETE ALL MLB HISTORY":
        log.warning("mlb store: purge_all called without confirmation — refusing")
        return 0
    if not _init():
        return 0
    try:
        s = _Session()
        try:
            # Scoped to sport even though the table is MLB-only. This is the
            # one destructive path in the module; an unscoped DELETE is the
            # wrong default to leave lying around if the table ever grows.
            n = s.query(_MlbPick).filter_by(sport="mlb").count()
            (s.query(_MlbPick).filter_by(sport="mlb")
              .delete(synchronize_session=False))
            s.commit()
            log.warning("mlb store: PURGED ALL — %d row(s) deleted from "
                        "mlb_picks; the MLB record is now empty", n)
            return n
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store purge_all failed: %s", exc)
        return 0


def summary() -> dict:
    """Row counts by book and result — for confirming what a purge would remove
    BEFORE removing it, and what it removed after."""
    if not _init():
        return {}
    try:
        s = _Session()
        try:
            rows = s.query(_MlbPick).filter_by(sport="mlb").all()
            out = {"total": len(rows)}
            for r in rows:
                b = r.book or "?"
                out.setdefault(b, {})
                k = (r.result or "PENDING")
                out[b][k] = out[b].get(k, 0) + 1
            slates = sorted({r.slate_date for r in rows if r.slate_date})
            out["slates"] = slates
            return out
        finally:
            s.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb store summary failed: %s", exc)
        return {}


def _to_dict(r) -> dict:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}
