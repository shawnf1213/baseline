"""
NBA opponent context — what a defence allows, and how fast it plays.

TWO SEPARATE THINGS, AND CONFLATING THEM IS THE CLASSIC NBA PROJECTION ERROR.

    PACE       how many possessions the game will have. Inflates or suppresses
               EVERY counting stat equally — points, rebounds, assists, threes.
               This is the NBA's court-speed term, the direct analogue of the
               tennis ST Pace Index.
    DEFENCE    how efficiently the opponent allows production PER POSSESSION,
               and it differs sharply by stat: a team can smother the three and
               surrender the rim, or wall off the paint and give up open looks.

A fast bad defence and a slow good one can post identical points-allowed-per-
game and are opposite matchups. Collapsing them into one "points allowed"
number is how a projection ends up backwards against exactly the teams that
matter most.

SO: PACE SCALES VOLUME, DEFENCE SCALES THE RATE. Two bounded multipliers with
separate clamps, never one global budget — the same containment philosophy as
the tennis CPR and the NFL opponent factor. Per-component clamps mean a bad
matchup on both axes cannot compound into a number no evidence supports.

RATINGS ARE LEAGUE-RELATIVE AND SHRUNK, so an average opponent multiplies by
exactly 1.000 and the whole adjustment is a no-op. A team is never "unknown but
probably average" silently — an unrecognised opponent returns 1.000 WITH a
basis that says so.

Rule 2 throughout.
"""

import logging
import os

log = logging.getLogger("baseline.nba.ratings")

# ── BOUNDS ───────────────────────────────────────────────────────────────────
# Defence and pace are clamped SEPARATELY and deliberately tightly. These are
# built from one season of team-level splits, they are corrections rather than
# drivers, and the compounding risk is real: a +12% defence on a +6% pace is
# already a 19% swing before the player's own form is considered.
DEF_MIN, DEF_MAX = 0.88, 1.12
PACE_MIN, PACE_MAX = 0.94, 1.06

# How hard to shrink a team's split toward the league mean. 0.75 keeps three
# quarters of a measured 82-game effect — enough to matter, not enough for a
# small-sample team to dominate a projection.
SHRINK = float(os.getenv("NBA_RATING_SHRINK", "0.75") or 0.75)

# Which defensive split governs each base stat. Kept as ONE map so the board,
# the projection and the website can never disagree about which matchup number
# was used — the same reason nfl/ratings.py keeps PROP_METRIC beside
# opponent_factor's own mapping.
STAT_ALLOWED = {
    "pts":  "pts_allowed",
    "reb":  "reb_allowed",
    "ast":  "ast_allowed",
    "fg3m": "fg3m_allowed",
    "nba_fantasy_pts": "pts_allowed",   # fantasy is dominated by scoring
}

_cache = {}


def _possessions(row) -> float:
    """Standard possessions estimate for one team-game."""
    try:
        return (float(row.get("fga") or 0) + 0.44 * float(row.get("fta") or 0)
                - float(row.get("oreb") or 0) + float(row.get("tov") or 0))
    except (TypeError, ValueError):
        return 0.0


def defense_table(season: int = None) -> dict:
    """{team: {pts_allowed, reb_allowed, ast_allowed, fg3m_allowed, pace, ...}}

    Built by self-joining the team log on game_id: the two rows sharing a
    game_id are the two sides of it, so what a team ALLOWED is simply what its
    opponent recorded. {} on failure.
    """
    from . import client as _c
    season = season or _c.current_season()
    if season in _cache:
        return _cache[season]
    try:
        # WHICH SEASON THE TABLE IS ACTUALLY BUILT FROM, recorded. Before
        # opening night the current season has no rows and this falls back to
        # the last one — which is right, but the table must SAY so. The first
        # cut logged "season 2027" while holding 2026 numbers, and a table that
        # misreports its own vintage is how a stale rating gets trusted.
        used = season
        df = _c.load("team_game_logs", season)
        if not len(df):
            used = season - 1
            df = _c.load("team_game_logs", used)
        if not len(df) or "game_id" not in df.columns:
            return {}
        rows = df.to_dict("records")
        by_game = {}
        for r in rows:
            by_game.setdefault(r.get("game_id"), []).append(r)

        agg = {}
        for gid, pair in by_game.items():
            if len(pair) != 2:
                continue                      # a half-written game; skip it
            for me, opp in ((pair[0], pair[1]), (pair[1], pair[0])):
                tm = _c.normalize_team(str(me.get("team_abbreviation") or ""))
                if not tm:
                    continue
                a = agg.setdefault(tm, {"n": 0, "pts": 0.0, "reb": 0.0,
                                        "ast": 0.0, "fg3m": 0.0, "poss": 0.0})
                a["n"] += 1
                # What the OPPONENT recorded = what this team allowed.
                for k in ("pts", "reb", "ast", "fg3m"):
                    try:
                        a[k] += float(opp.get(k) or 0)
                    except (TypeError, ValueError):
                        pass
                # Pace is a property of the GAME, so both teams get the same
                # figure: the average of the two possession estimates.
                a["poss"] += (_possessions(me) + _possessions(opp)) / 2.0

        table = {}
        for tm, a in agg.items():
            n = a["n"] or 1
            table[tm] = {
                "games": a["n"],
                "pts_allowed": a["pts"] / n,
                "reb_allowed": a["reb"] / n,
                "ast_allowed": a["ast"] / n,
                "fg3m_allowed": a["fg3m"] / n,
                "pace": a["poss"] / n,
            }
        if not table:
            return {}
        # League means, for the relative figures every factor below is built on.
        keys = ("pts_allowed", "reb_allowed", "ast_allowed", "fg3m_allowed",
                "pace")
        league = {k: sum(v[k] for v in table.values()) / len(table)
                  for k in keys}
        for tm, v in table.items():
            for k in keys:
                v[f"{k}_rel"] = (v[k] / league[k]) if league[k] else 1.0
            # Rank among the thirty. Published because "x1.0043" tells a reader
            # nothing on its own — it needs to say which defence that is and
            # what it actually allows. Rank 1 = allows the LEAST.
            pass
        for k in keys:
            order = sorted(table, key=lambda t: table[t][k])
            for i, tm in enumerate(order, 1):
                table[tm][f"{k}_rank"] = i
        table["_league"] = league
        table["_season"] = used
        _cache[season] = table
        log.info("nba ratings: built defence table for %d team(s) from season "
                 "%d%s (league pace %.1f, pts allowed %.1f)",
                 len(table) - 2, used,
                 f" (asked for {season} — no rows yet)" if used != season else "",
                 league["pace"], league["pts_allowed"])
        return table
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba defense_table failed: %s", exc)
        return {}


def _shrunk(rel: float, lo: float, hi: float) -> float:
    """Shrink a league-relative ratio toward 1.0 and clamp it."""
    f = 1.0 + (float(rel) - 1.0) * SHRINK
    return max(lo, min(hi, f))


def opponent_factor(opponent: str, stat: str, season: int = None) -> dict:
    """The RATE multiplier for facing this defence on this stat.

    Returns {"factor": float, "basis": str, "rank": int|None}. 1.000 with basis
    "unknown opponent" when the team has no rating — an unrecognised opponent
    must not silently become an average one without saying so.

    APPLIED TO THE RATE ONLY, never to minutes or possessions. Pace already
    carries the volume side, and multiplying both is the double-count that
    inflates NBA projections.
    """
    from . import client as _c
    try:
        field = STAT_ALLOWED.get(stat)
        if not field:
            return {"factor": 1.0, "basis": "unmapped stat", "rank": None}
        t = defense_table(season)
        r = (t or {}).get(_c.normalize_team(opponent or "")) or {}
        rel = r.get(f"{field}_rel")
        if not isinstance(rel, (int, float)):
            return {"factor": 1.0, "basis": "unknown opponent", "rank": None}
        return {
            "factor": round(_shrunk(rel, DEF_MIN, DEF_MAX), 4),
            "basis": f"{field.replace('_', ' ')} {r.get(field, 0):.1f}/g "
                     f"(rank {r.get(f'{field}_rank')} of 30)",
            "rank": r.get(f"{field}_rank"),
            "allowed": round(float(r.get(field, 0)), 2),
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba opponent_factor failed (%s %s): %s", opponent, stat,
                    str(exc)[:160])
        return {"factor": 1.0, "basis": "error", "rank": None}


def pace_factor(opponent: str, season: int = None) -> dict:
    """The VOLUME multiplier for the pace this opponent plays at.

    Separate from opponent_factor and separately clamped — see the module
    docstring. This is the term that makes a fast opponent lift every counting
    stat at once, which is exactly what pace does and what a per-stat defensive
    split cannot express.
    """
    from . import client as _c
    try:
        t = defense_table(season)
        r = (t or {}).get(_c.normalize_team(opponent or "")) or {}
        rel = r.get("pace_rel")
        if not isinstance(rel, (int, float)):
            return {"factor": 1.0, "basis": "unknown opponent", "rank": None}
        return {
            "factor": round(_shrunk(rel, PACE_MIN, PACE_MAX), 4),
            "basis": f"pace {r.get('pace', 0):.1f} poss/g "
                     f"(rank {r.get('pace_rank')} of 30, "
                     f"{'fast' if rel > 1.01 else 'slow' if rel < 0.99 else 'average'})",
            "rank": r.get("pace_rank"),
            "pace": round(float(r.get("pace", 0)), 2),
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba pace_factor failed (%s): %s", opponent, str(exc)[:160])
        return {"factor": 1.0, "basis": "error", "rank": None}
