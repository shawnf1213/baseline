"""
NFL board — turn a book's posted lines into a ranked list of plays.

Mirrors mlb/board.py: scan one book, price every line the engine supports, rank
by the model's confidence on the side it likes, and hand back rows. Books are
scanned INDEPENDENTLY and never pooled, for the same reason the MLB board keeps
them apart — two markets that price differently produce one meaningless record
if you merge them.

WHAT MAKES A ROW
----------------
A row needs three things and is dropped if any is missing:

    a posted line      from nfl.lines (standard only, exact-NFL league)
    a game             from client.upcoming_week() — supplies spread and total
    a projection       from nfl.props.project()

The GAME is not optional in spirit even though the engine tolerates its absence.
Without a spread there is no game-script mixture, and the projection degrades to
a league-neutral volume estimate that says "script_applied: false". Those rows
are kept but ranked below every scripted row, because a play whose volume term
is guesswork should never outrank one whose is priced. See _rank_key.

ONE PROP PER PLAYER
-------------------
A receiver's receiving yards and his receptions are the same targets seen twice;
a quarterback's pass yards and attempts likewise. Boarding both doubles the
exposure to a single outcome while looking like diversification. The board keeps
only that player's single best-ranked prop — the same rule the MLB board applies
to a pitcher's strikeouts and earned runs.
"""

import logging
import os

log = logging.getLogger("baseline.nfl.board")

# A play must beat the line by at least this much to be worth posting. Below it
# the projection and the market disagree by less than rounding, and the "edge"
# is noise. Yardage props and count props live on different scales, so the
# threshold is expressed in STANDARD DEVIATIONS, not yards — 4 yards on a 90-yard
# receiving line is nothing, 4 receptions on a 4.5 line is everything.
MIN_EDGE_SD = 0.20

# Confidence floor on the side the model likes. 0.50 is a coin flip by
# definition, so anything near it is not a play regardless of edge.
MIN_PROB = 0.53

# ── THE DATA-SUFFICIENCY GATE ────────────────────────────────────────────────
# A row whose usage window is "prior season only" is priced entirely on LAST
# season, because nflverse publishes no performance data until games are played.
# Measured on the live 2026-09-08 board, those rows carry +25.3% mean relative
# error against the market, concentrated exactly where roles change — while the
# SAME model backtests at -2.16 yards of bias in-season (2025, n=1552, beating
# both baselines). The model is not wrong; it is being asked a question it has
# no evidence for.
#
# So the gate is on EVIDENCE, not on a calendar date: a player is priced once he
# has current-season games, whenever that happens. In practice the board is
# empty in week 1, thin in weeks 2-3, and full from about week 4 — and it fills
# itself without anyone flipping a switch.
#
# Deliberately NOT solved by anchoring the projection to the posted line. That
# would drive error-vs-market to zero by construction while proving nothing:
# with no 2026 games played there are no outcomes to validate against, so the
# only available yardstick would be the very thing being fitted to. The edge has
# to come from the model seeing something the book missed, which requires the
# model to have seen something at all.
#
# NFL_ALLOW_PRIOR_SEASON=1 lifts the gate for research and one-off scans. Rows
# are still LABELLED either way, so a lifted gate is visible on the board rather
# than silent.
REQUIRE_CURRENT_SEASON = os.getenv(
    "NFL_ALLOW_PRIOR_SEASON", "0").strip() not in ("1", "true", "True", "yes", "on")


def _team_index(games: list) -> dict:
    """team abbr -> (game, is_home). Both sides of every upcoming game."""
    from .client import normalize_team
    idx = {}
    for g in games or []:
        h, a = g.get("home_abbr"), g.get("away_abbr")
        if h:
            idx[normalize_team(h)] = (g, True)
        if a:
            idx[normalize_team(a)] = (g, False)
    return idx


def _game_for(team: str, idx: dict) -> dict:
    """Build the `game` dict nfl.props.project() expects, from this player's side.

    The spread is stated FROM THE PLAYER'S SIDE — the schedule carries
    spread_home, so the away side is its negation. Getting this backwards would
    invert every game script on the board: favourites would be priced as
    underdogs, run-heavy scripts as pass-heavy. It is the single most
    consequential sign in this module.
    """
    from .client import normalize_team
    if not team:
        return {}
    hit = idx.get(normalize_team(team))
    if not hit:
        return {}
    g, is_home = hit
    sh = g.get("spread_home")
    spread = None
    if isinstance(sh, (int, float)):
        spread = float(sh) if is_home else -float(sh)
    return {
        "player_team": normalize_team(team),
        "opponent_team": normalize_team(g.get("away_abbr") if is_home
                                        else g.get("home_abbr")),
        "player_spread": spread,
        "total": g.get("total"),
        "game_id": g.get("game_id"),
        "kickoff": g.get("kickoff"),
        "matchup": g.get("name"),
    }


def _win_prob(r: dict):
    """The model's probability on the side it actually likes."""
    lean = (r.get("lean") or "").upper()
    if lean == "OVER":
        return r.get("p_over")
    if lean == "UNDER":
        return r.get("p_under")
    return None


def _rank_key(r: dict):
    """Sort key: scripted rows first, then by confidence, then by edge in SDs.

    Scripted-first is a correctness rule, not a cosmetic one — see module
    docstring. Within each group the model's own probability is the ranking, the
    same quantity the MLB board sorts on.
    """
    return (0 if r.get("script_applied") else 1,
            -(_win_prob(r) or 0.0),
            -(r.get("edge_sd") or 0.0))


def scan_board(book: str = "prizepicks", season: int = None,
               one_per_player: bool = True) -> list:
    """Price every supported line on ONE book. Returns ranked rows; [] on failure.

    Never raises — Rule 2. A book that fails returns an empty board and says so
    in the log, rather than taking the other book or the tennis bot down.
    """
    from . import lines as _lines, props as _props, client as _client
    try:
        posted = _lines.fetch_lines(book)
        if not posted:
            log.warning("nfl board (%s): no lines fetched — nothing to price", book)
            return []
        try:
            games = _client.upcoming_week()
        except Exception:  # noqa: BLE001 — Rule 2
            log.exception("nfl board (%s): schedule unavailable; pricing without "
                          "game script", book)
            games = []
        idx = _team_index(games)
        if not idx:
            log.warning("nfl board (%s): no upcoming games — every row will be "
                        "unscripted", book)

        rows, skipped, no_usage = [], 0, 0
        for (_nkey, prop), ln in posted.items():
            game = _game_for(ln.get("team"), idx)
            r = _props.project(ln["player"], prop, line=ln["line"],
                               game=game or None, season=season)
            if not r:
                no_usage += 1
                continue
            if r.get("skipped"):
                skipped += 1
                continue
            p = _win_prob(r)
            sd = r.get("sd") or 0.0
            edge = (r.get("projection") or 0.0) - float(ln["line"])
            r.update({
                "book": book,
                "line": ln["line"],
                "edge": round(edge, 2),
                "edge_sd": round(abs(edge) / sd, 3) if sd else None,
                "team": ln.get("team"),
                "board_position": ln.get("position"),
                "matchup": game.get("matchup"),
                "kickoff": game.get("kickoff"),
                "game_id": game.get("game_id"),
                "win_prob": p,
                # Stated on the row, never inferred at render time — a board
                # that shows a number must be able to say what it knew.
                "prior_season_only": r.get("window") == "prior season only",
                "role_change": r.get("role_change"),
            })
            rows.append(r)

        log.info("nfl board (%s): priced %d of %d line(s) — %d no usage, "
                 "%d below volume floor", book, len(rows), len(posted),
                 no_usage, skipped)

        # Filters applied AFTER pricing so the log above reports true coverage.
        # Evidence gate FIRST — see REQUIRE_CURRENT_SEASON. A play with no
        # current-season data behind it is not a weak play, it is an uninformed
        # one, and it should not compete for a board slot on a confidence number
        # derived from last year's role.
        stale = [r for r in rows if r.get("window") == "prior season only"]
        if stale:
            if REQUIRE_CURRENT_SEASON:
                rows = [r for r in rows if r.get("window") != "prior season only"]
                log.warning("nfl board (%s): %d play(s) held back — priced on "
                            "prior-season usage only, which measured +25%% "
                            "relative error on the 2026-09-08 board. They post "
                            "once these players have current-season games "
                            "(NFL_ALLOW_PRIOR_SEASON=1 to override).",
                            book, len(stale))
            else:
                log.warning("nfl board (%s): %d play(s) priced on PRIOR-SEASON "
                            "usage only (gate lifted) — rows are labelled",
                            book, len(stale))
        keep = [r for r in rows
                if (r.get("win_prob") or 0) >= MIN_PROB
                and (r.get("edge_sd") or 0) >= MIN_EDGE_SD]
        log.info("nfl board (%s): %d play(s) clear p>=%.2f and edge>=%.2f sd",
                 book, len(keep), MIN_PROB, MIN_EDGE_SD)
        keep.sort(key=_rank_key)

        if one_per_player:
            seen, dedup = set(), []
            for r in keep:
                nm = r.get("player")
                if nm in seen:
                    continue
                seen.add(nm)
                dedup.append(r)
            if len(dedup) != len(keep):
                log.info("nfl board (%s): %d row(s) dropped by one-prop-per-player",
                         book, len(keep) - len(dedup))
            keep = dedup
        return keep
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl board (%s) failed: %s", book, exc)
        return []
