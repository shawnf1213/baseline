"""
MLB sport-module interface.

The common interface NORTH_STAR.md requires of every sport module:
`scan_board()`, `project()`, `resolve()`, `grade()`. Implementing it here is what
makes `mlb/` a SPORT MODULE rather than a loose directory of MLB code — a future
core dispatcher can call these four functions without knowing anything about
baseball.

The tennis side does not implement this interface yet; its logic still lives in
backend/src and discord-bot. That refactor is gated on capturing a byte-identical
tennis baseline fixture first (NORTH_STAR.md decisions log), so this module is
deliberately written to be called EITHER by a future dispatcher or standalone.

Rule 2 — every public function catches its own exceptions and returns an
empty/typed result. Nothing raised here can reach another sport.
Rule 4 — scan_board() returns projections only. It never posts, and it writes no
rows, so Rule 3's sport-column gate is satisfied by writing nothing at all.
"""

import logging
import datetime as _dt

from . import MLB_ENABLED, SPORT, client, strikeouts

log = logging.getLogger("baseline.mlb.board")

# Props this module can price, and which engine owns each.
#   strikeouts          -> mlb/strikeouts.py  (the tuned model: log5 opponent
#                          term, park, home/away split, lineup-aware basis)
#   other pitcher props -> mlb/pitcher_props.py
#   batter props        -> mlb/batter_props.py
# Strikeouts deliberately keeps its own engine rather than being folded into the
# generic pitcher path: it is the only prop with graded results behind it, and
# rerouting it would silently change a working board.
SUPPORTED_PROPS = ("strikeouts",)          # the schedule-driven legacy scan


def _pitcher_index(games: list) -> dict:
    """{normalised probable-starter name: matchup context} for a slate."""
    idx = {}
    for g in games:
        for side, opp in (("away", "home"), ("home", "away")):
            pid = g[side].get("pitcher_id")
            if not pid:
                continue          # probable not announced — a real state, skip
            from .lines import _norm
            idx[_norm(g[side].get("pitcher") or "")] = {
                "pitcher_id": pid,
                "pitcher": g[side].get("pitcher"),
                "team": g[side].get("team"),
                "opponent": g[opp].get("team"),
                "opponent_team_id": g[opp].get("team_id"),
                "game_pk": g.get("game_pk"),
                "game_date": g.get("game_date"),
                "is_home": side == "home",
                "home_team_id": (g.get("home") or {}).get("team_id"),
            }
    return idx


def _batter_index(games: list, date: str = None) -> dict:
    """{normalised name: context} for every batter in a CONFIRMED lineup.

    THE LINEUP IS THE GATE, AND IT IS STRUCTURAL. A batter reaches this index
    only by appearing in a posted lineup, so a board built from it physically
    cannot carry a player who turns out to be resting. That is deliberate: a
    rested regular does not go 0-for-4, the prop voids, and a board that showed
    him was wrong rather than unlucky.

    The cost is real and worth stating plainly — at a 9am board time NO game has
    a lineup, so this returns {} and the board carries pitcher props only.
    Moving batter props onto the board is a SCHEDULING decision (post later),
    not a modelling one.

    Uses the one-request slate fetch, not context.get_lineup() per game-side:
    the per-game path also resolves handedness for all ~18 hitters, which across
    a full slate is hundreds of sequential calls and timed the scan out. Only
    batters that survive to a projection get their handedness looked up.
    """
    from .lines import _norm
    lineups = client.get_slate_lineups(date)
    if not lineups:
        return {}
    idx = {}
    for g in games:
        sides = lineups.get(g.get("game_pk")) or {}
        for side, opp in (("away", "home"), ("home", "away")):
            for slot, b in enumerate(sides.get(side) or [], 1):
                idx[_norm(b.get("name") or "")] = {
                    "batter_id": b["id"], "player": b.get("name"),
                    "lineup_slot": slot,
                    "team": g[side].get("team"),
                    "opponent": g[opp].get("team"),
                    "opponent_team_id": g[opp].get("team_id"),
                    "opposing_pitcher_id": g[opp].get("pitcher_id"),
                    "opposing_pitcher": g[opp].get("pitcher"),
                    "game_pk": g.get("game_pk"),
                    "game_date": g.get("game_date"),
                    "is_home": side == "home",
                }
    return idx


def scan_all_props(date: str = None, book: str = "underdog",
                   only: str = None) -> list:
    """Every (player, prop) pair this BOOK prices that we can also project.

    LINES-DRIVEN, not schedule-driven. The legacy strikeout scan projects every
    announced starter and then looks for a line; with fifteen prop types that
    would mean hundreds of wasted API calls for markets the book never posted.
    Here the book's board is fetched first and only priced pairs are projected.

    Every row is priced by the engine that owns its prop, so each carries its own
    fitted dispersion — earned runs (1.63) and pitching outs (0.72) must not be
    evaluated with the strikeout dispersion.

    `only`: "pitcher" or "batter" to restrict the scan. This exists because the
    two prop families are ready at DIFFERENT TIMES OF DAY. Probable starters are
    known the night before, so pitcher props can post at 9am; lineups are not
    posted until a few hours before first pitch, so batter props cannot. Running
    one board per family lets each post when its inputs actually exist, instead
    of holding the pitcher board until the afternoon or posting a batter board
    that is structurally empty.

    Returns [] on any failure. Never raises, never posts, never writes.
    """
    from . import lines as _lines, pitcher_props as _pp, batter_props as _bp
    try:
        games = client.get_schedule(date)
        if not games:
            log.info("mlb scan_all_props: no games for %s", date or "today")
            return []

        # ── PRE-GAME ONLY ────────────────────────────────────────────────────
        # A started game must never reach the board. Both books leave lines up
        # after first pitch (PrizePicks was still showing 35 on a slate where
        # every game was Final or In Progress), and a projection built from
        # full-game history against a game already in the 6th is not a
        # prediction — it is a stale number wearing one. Gate on the coarse
        # abstract state, not detailedState, whose long tail of "Warmup" /
        # "Manager challenge" / "Delayed Start: Rain" an allow-list keeps
        # getting wrong.
        live = [g for g in games if g.get("abstract_state") not in (None, "Preview")]
        games = [g for g in games if g.get("abstract_state") in (None, "Preview")]
        if live:
            log.info("mlb scan_all_props: skipped %d game(s) already started or "
                     "final (%s)", len(live),
                     ", ".join(sorted({str(g.get("status")) for g in live})))
        if not games:
            log.info("mlb scan_all_props: every game on %s has started — "
                     "nothing to price", date or "today")
            return []

        pidx = _pitcher_index(games)
        book_lines = _lines.fetch_lines(book, pitcher_names=set(pidx))
        if not book_lines:
            log.info("mlb scan_all_props: %s posted no usable lines", book)
            return []

        if only in ("pitcher", "batter"):
            fam = _lines.PITCHER_PROPS if only == "pitcher" else _lines.BATTER_PROPS
            book_lines = {k: v for k, v in book_lines.items() if k[1] in fam}
            if not book_lines:
                log.info("mlb scan_all_props: %s posted no %s-prop lines",
                         book, only)
                return []

        # Only pay for lineups if the book actually prices a batter prop today.
        wants_batters = any(p in _lines.BATTER_PROPS for _, p in book_lines)
        bidx = _batter_index(games, date) if wants_batters else {}
        if wants_batters and not bidx:
            log.info("mlb scan_all_props: %s prices batter props but NO lineup "
                     "is posted yet — batter props withheld (see _batter_index)",
                     book)

        team_batting = client.get_team_batting()
        out, unmatched, no_lineup = [], 0, 0

        for (nn, prop), m in book_lines.items():
            row = {}
            if prop in _lines.PITCHER_PROPS:
                ctx = pidx.get(nn)
                if not ctx:
                    unmatched += 1     # priced, but not a starter we identified
                    continue
                if prop == "strikeouts":
                    row = strikeouts.project(
                        ctx["pitcher_id"], ctx["opponent_team_id"],
                        team_batting=team_batting, game_pk=ctx["game_pk"],
                        is_home=ctx["is_home"],
                        home_team_id=ctx["home_team_id"])
                else:
                    row = _pp.project(ctx["pitcher_id"], prop,
                                      opponent_team_id=ctx["opponent_team_id"])
                if not row or row.get("skipped"):
                    if row and row.get("opener_risk"):
                        log.info("mlb scan: skipping %s %s — %s",
                                 ctx.get("pitcher"), prop, row.get("reason"))
                    continue           # thin sample or wrong role; engine logged why
                row.update({k: ctx[k] for k in
                            ("pitcher", "team", "opponent", "game_pk",
                             "game_date")})
            elif prop in _lines.BATTER_PROPS:
                ctx = bidx.get(nn)
                if not ctx:
                    no_lineup += 1
                    continue
                row = _bp.project(ctx["batter_id"], prop,
                                  lineup_confirmed=True)
                if not row:
                    continue
                row.update({k: ctx[k] for k in
                            ("player", "team", "opponent", "game_pk",
                             "game_date", "lineup_slot", "opposing_pitcher")})
                row["pitcher"] = ctx["player"]     # display/store compatibility
                # Handedness only for batters that actually made it this far —
                # one call each, versus ~270 if the whole slate were resolved.
                from . import context as _ctx
                row["bat"] = (_ctx.handedness(ctx["batter_id"]) or {}).get("bat")
            else:
                continue

            # Price with the engine's OWN over/under, then attach the market.
            row["prop"] = prop
            row["sport"] = SPORT
            row["shadow"] = not MLB_ENABLED
            engine_ou = (_bp if prop in _lines.BATTER_PROPS else _pp) \
                if prop != "strikeouts" else strikeouts
            _reprice(row, m["line"], prop, engine_ou)
            _lines.price(row, m)
            out.append(row)

        log.info("mlb scan_all_props %s/%s: %d rows | %d priced names not "
                 "matched to a starter | %d batter lines with no lineup",
                 book, date or "today", len(out), unmatched, no_lineup)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb scan_all_props failed: %s", exc)
        return []


def _reprice(row: dict, line, prop: str, engine) -> None:
    """Recompute the row's over/under against the book's actual line.

    The engines are called without a line above (one projection, then price it)
    so this fills in p_over / p_under / lean using THAT PROP'S dispersion or
    empirical spread — never a shared one.
    """
    import math
    from core import odds as _odds
    mu = row.get("projection")
    if not isinstance(mu, (int, float)) or not isinstance(line, (int, float)):
        return
    sd = row.get("sd")
    if isinstance(sd, (int, float)) and sd > 0:
        # Composite (Fantasy Score, H+R+RBI): empirical spread, normal tail.
        p_over = 0.5 * math.erfc(((line - mu) / sd) / (2 ** 0.5))
        row.update({"p_over": round(p_over, 4),
                    "p_under": round(1 - p_over, 4),
                    "lean": "OVER" if p_over >= 0.5 else "UNDER"})
        return
    disp = row.get("dispersion")
    if not isinstance(disp, (int, float)):
        disp = getattr(engine, "DISPERSION", 1.0)
        if isinstance(disp, dict):
            disp = disp.get(prop, 1.0)
        if prop in getattr(engine, "COUNT_PROPS", {}):
            disp = engine.COUNT_PROPS[prop][1]
    r = _odds.count_over_under(mu, line, dispersion=disp)
    row.update({"p_over": round(r["p_over"], 4),
                "p_under": round(r["p_under"], 4),
                "p_push": round(r["p_push"], 4),
                "lean": r["lean"], "dispersion": disp})


def scan_board(date: str = None, line_map: dict = None) -> list:
    """Every projectable starter on a date, as neutral prop rows.

    `line_map` optionally supplies {pitcher_id: line} from a book; without it the
    rows carry a projection and no over/under, which is the correct shape for
    shadow review — we are checking the projection against actuals, not pricing
    a market we have not fetched.

    Returns [] on any failure. Never raises, never posts, never writes.
    """
    try:
        games = client.get_schedule(date)
        if not games:
            log.info("mlb scan_board: no games for %s", date or "today")
            return []
        team_batting = client.get_team_batting()
        out = []
        for g in games:
            for side, opp_side in (("away", "home"), ("home", "away")):
                pid = g[side].get("pitcher_id")
                if not pid:
                    continue          # probable not announced — a real state, skip
                line = (line_map or {}).get(pid)
                proj = strikeouts.project(
                    pid, g[opp_side].get("team_id"), line=line,
                    team_batting=team_batting,
                    game_pk=g.get("game_pk"),
                    is_home=(side == "home"),
                    home_team_id=(g.get("home") or {}).get("team_id"))
                if not proj:
                    continue          # thin sample; strikeouts.project logged why
                proj.update({
                    "sport": SPORT,
                    "game_pk": g.get("game_pk"),
                    "game_date": g.get("game_date"),
                    "pitcher": g[side].get("pitcher"),
                    "team": g[side].get("team"),
                    "opponent": g[opp_side].get("team"),
                    "shadow": not MLB_ENABLED,
                })
                out.append(proj)
        log.info("mlb scan_board %s: %d projections from %d games (shadow=%s)",
                 date or "today", len(out), len(games), not MLB_ENABLED)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb scan_board failed: %s", exc)
        return []


def run_daily(book: str, date: str = None, shadow: bool = None,
              post_it: bool = True, only: str = None,
              exclude_posted: bool = True, additional: bool = False) -> dict:
    """Full daily cycle for ONE book: scan -> attach that book's lines -> post ->
    persist.

    Persisting is what makes a recap possible: without a stored board there is
    nothing to grade and nothing to summarise. Rows land in mlb_picks, never the
    tennis `picks` table, and only plays that matched a real line are stored —
    a projection with no market has nothing to be graded against.

    Books run independently. One book failing does not stop the other, and
    neither can reach tennis. Never raises.
    """
    from . import post as _post, store as _store, recap as _recap, SHADOW
    # None means "ask the flag" — callers that pass a literal here are how a
    # board kept printing SHADOW after MLB went live.
    shadow = SHADOW if shadow is None else shadow
    out = {"book": book, "projections": 0, "priced": 0,
           "posted": False, "stored": 0, "shadow": shadow}
    try:
        rows = scan_all_props(date, book=book, only=only)
        out["only"] = only
        out["projections"] = len(rows)
        rows.sort(key=lambda r: -((r.get("p_over") if r.get("lean") == "OVER"
                                   else r.get("p_under")) or 0))
        out["priced"] = sum(1 for r in rows if r.get("line") is not None)
        out["props"] = sorted({r.get("prop") for r in rows if r.get("prop")})

        slate = date or _recap.et_today()

        # A LATER SCAN ON THE SAME CARD POSTS ONLY WHAT THE EARLIER ONE COULD
        # NOT. Two boards run per slate (11:30 PM and 9 AM); without this the
        # morning one repeats most of the night one — the store would dedupe the
        # rows, but Discord would show every play twice. Same rule tennis
        # applies: a play already live is never re-posted.
        state = _store.board_state(book, slate) if exclude_posted else {}
        if state.get("players"):
            before = len(rows)
            # EXCLUDE BY PLAYER, not (player, prop). A pitcher already on the
            # board must not come back on a second prop — his strikeouts and his
            # earned runs are the same six innings, and boarding both is the
            # thing one-prop-per-player forbids.
            rows = [r for r in rows
                    if (r.get("player") or r.get("pitcher")) not in state["players"]]
            out["skipped_already_posted"] = before - len(rows)
            log.info("mlb run_daily (%s): %d play(s) whose player is already on "
                     "the %s board — posting the %d that are new", book,
                     before - len(rows), slate, len(rows))
        if not rows:
            out["post_reason"] = "every qualifying play is already posted"
            return out
        label = f"{int(slate[5:7])}/{int(slate[8:10])}"
        # POTD is chosen from what will actually be POSTED, so the star can never
        # name a play the board dropped in dedupe — the bug that put Zizou up as
        # tennis POTD when he was not on the Underdog board at all.
        # Seeded with what the board already holds, so both dedupe rules span
        # the day's two runs rather than resetting on each.
        posted_rows = _post.postable(rows,
                                     seen_players=state.get("players"),
                                     game_counts=state.get("games"))
        if additional:
            # A top-up posts at most SECOND_MAX and carries no star, so the
            # stored rows must match — otherwise the record would contain plays
            # the board never showed, and a POTD nobody saw.
            posted_rows = posted_rows[:_post.SECOND_MAX]
        potd = {} if additional else _post.select_potd(posted_rows)

        if post_it:
            res = _post.post_board(rows, label, book=book, shadow=shadow,
                                   additional=additional)
            out["posted"] = bool(res.get("ok"))
            out["post_reason"] = res.get("reason")
            # Log ONLY after a successful send, the same rule the tennis board
            # follows: an unposted play is not a play.
            if not out["posted"]:
                return out
        out["stored"] = _store.log_board(
            posted_rows, book, slate,
            potd_key=((potd.get("player") or potd.get("pitcher"),
                       potd.get("prop")) if potd else None),
            shadow=shadow)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb run_daily (%s) failed: %s", book, exc)
        out["error"] = str(exc)[:200]
        return out


def project(subject_id, opponent_id, prop: str = "strikeouts", line=None) -> dict:
    """Single projection by prop name. {} for an unsupported prop or any failure."""
    try:
        if prop not in SUPPORTED_PROPS:
            log.info("mlb project: unsupported prop %r", prop)
            return {}
        return strikeouts.project(subject_id, opponent_id, line=line)
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb project failed: %s", exc)
        return {}


def resolve(pitcher_id, game_pk=None, game_date: str = None,
            prop: str = "strikeouts") -> dict:
    """Actual settled value for one prop from a COMPLETED game.

    Mirrors the tennis resolver's contract: return a typed result or NEEDS
    REVIEW, never a guess. A start that does not appear as completed is NEEDS
    REVIEW rather than a zero — the tennis side learned that the hard way, where
    treating an absent match as a real result produced four misgrades in a week.

    A BATTER WHO DID NOT PLAY IS NEEDS REVIEW, NOT AN 0-FOR. This is the same
    rule and the reason batter props are gated on a posted lineup upstream: a
    scratched hitter's prop voids at the book, and grading it as a loss would
    invent a result the market never settled.
    """
    try:
        if not pitcher_id:
            return {"result": "NEEDS REVIEW", "reason": "no player id"}
        season = _dt.date.today().year
        if game_date:
            try:
                season = int(str(game_date)[:4])
            except (TypeError, ValueError):
                pass

        from . import lines as _lines
        if prop in _lines.BATTER_PROPS:
            return _resolve_batter(pitcher_id, prop, season, game_date,
                                   game_pk=game_pk)

        from . import pitcher_props as _pp
        for r in _pp._start_rows(pitcher_id, _dt.date(season, 7, 1)):
            if not _same_game(r, game_pk, game_date):
                continue
            field = {"strikeouts": "k", "pitching_outs": "outs",
                     "hits_allowed": "h", "walks_allowed": "bb",
                     "earned_runs": "er"}.get(prop)
            val = _pp._fs_of(r) if prop == "pitcher_fantasy_score" else (
                r.get(field) if field else None)
            if val is None:
                return {"result": "NEEDS REVIEW",
                        "reason": f"{prop} unavailable"}
            return {"result": "OK", "value": float(val), "date": r.get("date"),
                    "bf": r.get("bf"), "prop": prop, "sport": SPORT}
        return {"result": "NEEDS REVIEW", "reason": "completed start not found"}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb resolve failed: %s", exc)
        return {"result": "NEEDS REVIEW", "reason": "resolver error"}


def _same_game(row: dict, game_pk, game_date) -> bool:
    """Does this game-log row belong to the game we are settling?

    GAME_PK WINS WHEN BOTH SIDES HAVE ONE. Matching on the date alone is wrong in
    two real cases: a doubleheader puts two games on one date, and MLB's calendar
    date for a late game can differ from the slate the board filed it under —
    Zac Thornton's 8/12 start was logged under a date the 8/11 board would not
    have matched. The date is only a fallback for rows stored before game_pk was
    captured.
    """
    if game_pk and row.get("game_pk"):
        return row["game_pk"] == game_pk
    if game_date:
        return str(row.get("date"))[:10] == str(game_date)[:10]
    return True


def _resolve_batter(batter_id, prop: str, season: int, game_date: str,
                    game_pk=None) -> dict:
    """Settled value for one batter prop from a completed game."""
    from . import batter_props as _bp
    for r in _bp._game_rows(batter_id, season):
        if not _same_game(r, game_pk, game_date):
            continue
        if prop == "hitter_fantasy_score":
            val = _bp._fs_of(r)
        elif prop == "hits_runs_rbis":
            val = r["h"] + r["r"] + r["rbi"]
        else:
            fld = _bp.COUNT_PROPS.get(prop, (None,))[0]
            val = r.get(fld) if fld else None
        if val is None:
            return {"result": "NEEDS REVIEW", "reason": f"{prop} unavailable"}
        return {"result": "OK", "value": float(val), "date": r.get("date"),
                "pa": r.get("pa"), "prop": prop, "sport": SPORT}
    # No game log entry: scratched, rested, or the game has not finalised.
    # Never an 0-for — see resolve()'s docstring.
    return {"result": "NEEDS REVIEW",
            "reason": "no completed game for this batter (scratched or unplayed)"}


def grade(lean: str, line, value) -> str:
    """W / L / PUSH from a settled value. Whole-number lines can push; the caller
    must not fold a push into either side."""
    try:
        if value is None or line is None:
            return "NEEDS REVIEW"
        v, ln = float(value), float(line)
        if v == ln:
            return "PUSH"
        over = v > ln
        return "W" if ((lean or "").upper() == "OVER") == over else "L"
    except Exception:  # noqa: BLE001
        return "NEEDS REVIEW"
