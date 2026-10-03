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

# ── THE RANK-BAND SCHEDULE (operator, 2026-10-03) ────────────────────────────
# The multiplier is set by WHERE a defence ranks on that stat, not by the raw
# size of its split. Rank 1 allows the LEAST, rank 30 the most, so the sign
# follows: facing a top-5 defence suppresses, facing a bottom-5 inflates.
#
#     rank  1-5    -6% .. -10%     best five, hardest matchup
#     rank  6-10   -3% ..  -5%
#     rank 11-20    neutral        the middle third does nothing
#     rank 21-25   +3% ..  +5%
#     rank 26-30   +6% .. +10%     bottom five, softest matchup
#
# INTERPOLATED INSIDE EACH BAND rather than flat, so rank 26 and rank 30 are
# not the same matchup. A flat band would put a cliff between rank 25 (+5%) and
# rank 26 (+6%) and then nothing at all across the next four places, which is
# exactly backwards — the extremes are where the real difference lives.
_RANK_BANDS = [
    (1, 5, -0.10, -0.06),      # (lo, hi, factor at lo, factor at hi)
    (6, 10, -0.05, -0.03),
    (11, 20, 0.0, 0.0),
    (21, 25, 0.03, 0.05),
    (26, 30, 0.06, 0.10),
]

# Total defensive adjustment per prop, after any positional layer. Per-component
# and separate from the pace clamp — the containment philosophy everywhere else
# in this codebase, and the reason a bad matchup on several axes cannot compound
# into a number no evidence supports.
DEF_TOTAL_CLAMP = float(os.getenv("NBA_DEF_CLAMP", "0.12") or 0.12)

# Minimum opposing players of a position a team must have faced before its
# positional split is trusted over the team-level one. A team that has seen four
# centres all season has a positional number that is noise, and a rank built on
# noise is worse than the team-level rank it would replace.
DVP_MIN_GAMES = int(os.getenv("NBA_DVP_MIN_GAMES", "40") or 40)


def _rank_adjust(rank) -> float:
    """The multiplier for a 1-30 defensive rank, interpolated within its band."""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.0
    r = max(1, min(30, r))
    for lo, hi, a, b in _RANK_BANDS:
        if lo <= r <= hi:
            if hi == lo:
                return a
            t = (r - lo) / (hi - lo)
            return a + (b - a) * t
    return 0.0

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

# 24-HOUR CACHE, NOT A PERMANENT ONE. The first cut memoised the defence table
# for the life of the process, and the bot runs for weeks — so a table built on
# opening night would still have been serving in December. Same class of bug as
# the nflverse in-process memo that pinned a week-1 frame all season.
CACHE_TTL = int(os.getenv("NBA_RATINGS_TTL", str(24 * 3600)) or 24 * 3600)
_cache = {}
_cache_at = {}
_pos_cache = {}
_pos_cache_at = {}

# Roster positions come back as G / F / C, plus hyphenated swings (C-F, F-G).
# NOT PG/SG/SF/PF/C — stats.nba.com's roster endpoint does not carry that
# granularity, and neither do its bio or player-index endpoints (checked
# 2026-10-03: 582 rows each, no POSITION column at all).
#
# G/F/C is therefore the honest resolution for defence-vs-position, and it is
# not a token one: perimeter versus interior is the axis that matters for this
# adjustment, and it is exactly the case the whole idea is for — a team that
# walls off the rim while surrendering open looks defends centres and guards
# very differently. A hyphenated position takes its FIRST letter, because that
# is the primary listing.
POSITION_GROUPS = ("G", "F", "C")


def position_group(pos: str) -> str:
    """G / F / C from a roster position string, or '' when unknown."""
    p = (pos or "").strip().upper()
    if not p:
        return ""
    first = p[0]
    return first if first in POSITION_GROUPS else ""


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
    import time as _t
    season = season or _c.current_season()
    if season in _cache and (_t.time() - _cache_at.get(season, 0)) < CACHE_TTL:
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
        _cache_at[season] = _t.time()
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


def player_positions(season: int = None) -> dict:
    """{normalised player name: 'G'|'F'|'C'} from the 30 team rosters.

    Thirty small calls, cached for CACHE_TTL. stats.nba.com has no league-wide
    position feed — leaguedashplayerbiostats and commonallplayers both return
    582 rows with no POSITION column — so the rosters are the source.

    {} on failure, which callers must read as "no positional data" and fall back
    to the team-level rank, never as "everyone is a guard".
    """
    from . import client as _c
    import time as _t
    season = season or _c.current_season()
    key = ("pos", season)
    if key in _pos_cache and (_t.time() - _pos_cache_at.get(key, 0)) < CACHE_TTL:
        return _pos_cache[key]
    out = {}
    try:
        df = _c.load("team_game_logs", season)
        if not len(df):
            df = _c.load("team_game_logs", season - 1)
        if not len(df) or "team_id" not in df.columns:
            return {}
        team_ids = sorted({int(t) for t in df["team_id"].dropna().unique()})
        for tid in team_ids:
            hdr, rows = _c._stats_get("commonteamroster", {
                "LeagueID": "00", "Season": _c.season_str(season),
                "TeamID": str(tid)})
            if not rows or not hdr:
                continue
            try:
                ip, inm = hdr.index("POSITION"), hdr.index("PLAYER")
            except ValueError:
                continue
            for r in rows:
                g = position_group(r[ip])
                if g:
                    out[_c._norm_name(str(r[inm]))] = g
        _pos_cache[key] = out
        _pos_cache_at[key] = _t.time()
        log.info("nba ratings: roster positions for %d player(s) across %d team(s)",
                 len(out), len(team_ids))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba player_positions failed: %s", str(exc)[:160])
        return out


def defense_vs_position(season: int = None) -> dict:
    """{team: {position: {stat: allowed_per_game, stat_rank: n}}}

    What each team allows to GUARDS, FORWARDS and CENTRES separately, built by
    joining the player game log to roster positions and reading off what the
    opposing side recorded. This is the number that separates "top-5 defence"
    from "top-5 defence AGAINST THE PLAYER IN FRONT OF US".

    {} when positions are unavailable — the caller then uses the team-level
    rank and says so.
    """
    from . import client as _c
    import time as _t
    season = season or _c.current_season()
    key = ("dvp", season)
    if key in _pos_cache and (_t.time() - _pos_cache_at.get(key, 0)) < CACHE_TTL:
        return _pos_cache[key]
    try:
        pos = player_positions(season)
        if not pos:
            return {}
        df = _c.load("player_game_logs", season)
        if not len(df):
            df = _c.load("player_game_logs", season - 1)
        if not len(df) or "matchup" not in df.columns:
            return {}
        agg = {}
        for r in df.to_dict("records"):
            # "LAL @ BOS" / "LAL vs. BOS" -> the player's OPPONENT is the other
            # side, which is who allowed what he recorded.
            m = str(r.get("matchup") or "")
            opp = ""
            if " @ " in m:
                opp = m.split(" @ ", 1)[1].strip()
            elif " vs. " in m:
                opp = m.split(" vs. ", 1)[1].strip()
            opp = _c.normalize_team(opp)
            g = pos.get(_c._norm_name(str(r.get("player_name") or "")))
            if not opp or not g:
                continue
            a = agg.setdefault(opp, {}).setdefault(
                g, {"n": 0, "pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0})
            a["n"] += 1
            for k in ("pts", "reb", "ast", "fg3m"):
                try:
                    a[k] += float(r.get(k) or 0)
                except (TypeError, ValueError):
                    pass
        if not agg:
            return {}
        table = {t: {g: {k: v[k] / (v["n"] or 1) for k in
                         ("pts", "reb", "ast", "fg3m")} | {"n": v["n"]}
                     for g, v in gs.items()} for t, gs in agg.items()}
        # Rank within each position group, per stat. Rank 1 allows the least.
        for g in POSITION_GROUPS:
            teams = [t for t in table if g in table[t]]
            for k in ("pts", "reb", "ast", "fg3m"):
                for i, t in enumerate(sorted(teams,
                                             key=lambda x: table[x][g][k]), 1):
                    table[t][g][f"{k}_rank"] = i
        _pos_cache[key] = table
        _pos_cache_at[key] = _t.time()
        log.info("nba ratings: defence-vs-position built for %d team(s) x %s",
                 len(table), "/".join(POSITION_GROUPS))
        return table
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba defense_vs_position failed: %s", str(exc)[:160])
        return {}


def opponent_factor(opponent: str, stat: str, season: int = None,
                    position: str = None) -> dict:
    """The RATE multiplier for facing this defence on this stat.

    Returns {factor, basis, rank, allowed, by_position}. 1.000 with basis
    "unknown opponent" when the team has no rating — an unrecognised opponent
    must not silently become an average one without saying so.

    POSITIONAL FIRST, TEAM-LEVEL AS THE FALLBACK. When the player's position is
    known and that team has enough games against it, the rank used is how they
    defend THAT position on THAT stat. Otherwise the team-level stat rank. The
    basis string always says which, because a projection that cannot explain
    which matchup number it used cannot be audited.

    APPLIED TO THE RATE ONLY, never to minutes or possessions. Pace already
    carries the volume side, and multiplying both is the double-count that
    inflates NBA projections.
    """
    from . import client as _c
    try:
        field = STAT_ALLOWED.get(stat)
        if not field:
            return {"factor": 1.0, "basis": "unmapped stat", "rank": None,
                    "by_position": False}
        tm = _c.normalize_team(opponent or "")
        t = defense_table(season)
        r = (t or {}).get(tm) or {}
        if not r:
            return {"factor": 1.0, "basis": "unknown opponent", "rank": None,
                    "by_position": False}

        # The stat the positional table is keyed on — fantasy maps onto points
        # the same way STAT_ALLOWED does.
        _pstat = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "fg3m",
                  "nba_fantasy_pts": "pts"}.get(stat)
        grp = position_group(position or "")
        rank, basis, by_pos = None, "", False
        if grp and _pstat:
            dvp = defense_vs_position(season)
            cell = ((dvp.get(tm) or {}).get(grp) or {})
            # MINIMUM SAMPLE. A team that has faced four centres all season has
            # a positional number that is noise, and a rank built on it is worse
            # than the team-level one it would replace.
            if cell.get(f"{_pstat}_rank") and (cell.get("n") or 0) >= DVP_MIN_GAMES:
                rank = cell[f"{_pstat}_rank"]
                basis = (f"{grp} {_pstat} allowed {cell[_pstat]:.1f}/g "
                         f"(rank {rank} of 30 vs {grp}, n={cell['n']})")
                by_pos = True
        if rank is None:
            rank = r.get(f"{field}_rank")
            if rank is None:
                return {"factor": 1.0, "basis": "unknown opponent", "rank": None,
                        "by_position": False}
            basis = (f"{field.replace('_', ' ')} {r.get(field, 0):.1f}/g "
                     f"(rank {rank} of 30, team-level)")

        adj = _rank_adjust(rank)
        adj = max(-DEF_TOTAL_CLAMP, min(DEF_TOTAL_CLAMP, adj))
        return {
            "factor": round(1.0 + adj, 4),
            "basis": basis,
            "rank": rank,
            "allowed": round(float(r.get(field, 0)), 2),
            "by_position": by_pos,
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba opponent_factor failed (%s %s): %s", opponent, stat,
                    str(exc)[:160])
        return {"factor": 1.0, "basis": "error", "rank": None,
                "by_position": False}


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
