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

# Props this module can currently price. Strikeouts first, per the North Star.
SUPPORTED_PROPS = ("strikeouts",)


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
                    team_batting=team_batting)
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


def resolve(pitcher_id, game_pk=None, game_date: str = None) -> dict:
    """Actual strikeouts from a COMPLETED start.

    Mirrors the tennis resolver's contract: return a typed result or NEEDS
    REVIEW, never a guess. A start that does not appear as completed is NEEDS
    REVIEW rather than a zero — the tennis side learned that the hard way, where
    treating an absent match as a real result produced four misgrades in a week.
    """
    try:
        if not pitcher_id:
            return {"result": "NEEDS REVIEW", "reason": "no pitcher id"}
        season = _dt.date.today().year
        if game_date:
            try:
                season = int(str(game_date)[:4])
            except (TypeError, ValueError):
                pass
        for r in client.get_pitcher_game_log(pitcher_id, season):
            if not r.get("is_start"):
                continue
            if game_date and str(r.get("date"))[:10] != str(game_date)[:10]:
                continue
            k = r.get("k")
            if k is None:
                return {"result": "NEEDS REVIEW", "reason": "strikeouts unavailable"}
            return {"result": "OK", "value": float(k), "date": r.get("date"),
                    "bf": r.get("bf"), "ip": r.get("ip"), "sport": SPORT}
        return {"result": "NEEDS REVIEW", "reason": "completed start not found"}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb resolve failed: %s", exc)
        return {"result": "NEEDS REVIEW", "reason": "resolver error"}


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
