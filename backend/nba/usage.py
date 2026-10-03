"""
NBA player usage — what a player actually does per game, and how reliably.

MINUTES ARE THE MASTER VARIABLE. Every counting stat on this board is minutes
times a per-minute rate, and the two fail in different ways: the rate is
reasonably stable for a given player, while minutes move with rotation, foul
trouble, blowouts and coaching whim. A projection that gets the rate right and
the minutes wrong is wrong. So minutes are projected explicitly, their
STABILITY is measured, and an unstable rotation is a confidence penalty rather
than a silently wider number.

This is the NBA equivalent of what expected sets does in tennis and what the
game-script volume mixture does in NFL: the one term everything else is scaled
by.

RECENCY IS WEIGHTED, NOT WINDOWED. A flat "last 10" throws away the other sixty
games and lurches when the tenth game ages out; a flat season average cannot see
a player who took over the offence in February. Both are available, and the
projection blends them — last 10 heaviest, season as the base — so a role change
shows up quickly without a two-game hot streak rewriting the number.

EVERY SERIES IS NEWEST-FIRST. stats.nba.com returns game logs in that order and
this module preserves it; a silent re-sort is how "last 10" becomes "first 10".

Rule 2 throughout: never raises, returns {} and says why.
"""

import logging
import math
import os
import statistics as _st

log = logging.getLogger("baseline.nba.usage")

# The per-game stats this module tracks. Combos are SUMS of these and are never
# modelled independently — see props.COMBO_PARTS for why.
BASE_STATS = ("pts", "reb", "ast", "fg3m", "nba_fantasy_pts")

# ── COMBO SPREAD IS MEASURED, NOT ASSUMED ────────────────────────────────────
# The combo PROJECTION is the sum of its component projections, so the card can
# never show a PRA that disagrees with the points, rebounds and assists printed
# beside it. Its SPREAD cannot be built the same way: sd(A+B) only equals
# sqrt(sd(A)^2 + sd(B)^2) when A and B are independent, and these are not —
# points and assists both rise when a player has the ball and the game is fast,
# while points and rebounds pull apart by role.
#
# Assuming independence would understate the spread of a positively-correlated
# combo, which is the dangerous direction: it inflates the edge-to-variance
# ratio and hands a compounded prop a confidence it has not earned. So the combo
# series is built PER GAME from the player's own log and its sigma measured
# directly, which carries the real correlation without having to estimate it.
COMBO_PARTS = {
    "pra": ("pts", "reb", "ast"),
    "pr":  ("pts", "reb"),
    "pa":  ("pts", "ast"),
    "ra":  ("reb", "ast"),
}

# ── RECENCY WEIGHTS ──────────────────────────────────────────────────────────
# Geometric decay over the newest games, then the season mean underneath. The
# half-life is deliberately gentle: NBA per-game output is noisy enough that an
# aggressive decay turns one 40-point night into a projection.
#
# RECENT_W is how much of the blend the weighted-recent term carries; the rest
# is the season mean. 0.65 was chosen so a player whose last 10 differ from his
# season by 4 points moves ~2.6 — visible, not whipsawed.
RECENT_N = 10
RECENT_HALFLIFE = 5.0
RECENT_W = float(os.getenv("NBA_RECENT_WEIGHT", "0.65") or 0.65)

# Sigma is measured over this many games — the spread the EVR grade reads.
SIGMA_N = 20

# ── ROTATION RISK ────────────────────────────────────────────────────────────
# Coefficient of variation of MINUTES over the recent window. Measured on the
# live 2025-26 season: a stable starter sits near 0.13 (Maxey 38.0 +/- 5.0), a
# genuine rotation player near 1.0 (Keshad Johnson 8.7 +/- 8.6). The bands below
# are set from that spread.
#
# This is a CONFIDENCE penalty, never a projection adjustment. The expected
# minutes are still the expected minutes; what rotation risk changes is how much
# the number should be trusted — the same treatment tennis gives a high-variance
# player.
ROTATION_STABLE = 0.25
ROTATION_RISKY = 0.45

# A player below this many minutes is not a prop, he is a lottery ticket. The
# board refuses rather than pricing him — same philosophy as nfl VOLUME_FLOOR:
# below the volume the model was fitted on the SHAPE is different, not merely
# noisier.
MIN_MINUTES = float(os.getenv("NBA_MIN_MINUTES", "12.0") or 12.0)

# Minimum games before a player can be priced at all.
MIN_GAMES = int(os.getenv("NBA_MIN_GAMES", "8") or 8)

# ── INACTIVITY ───────────────────────────────────────────────────────────────
# Days since last appearance. The tennis module treats a long gap as an amber
# then red warning with a confidence penalty, and the reason transfers exactly:
# a player returning from a month out is not the player the game log describes.
# In the NBA the scale is shorter because the schedule is denser — a healthy
# starter plays every 2-3 days, so 7 days is already unusual and 14 is a real
# absence.
STALE_AMBER_DAYS = 7
STALE_RED_DAYS = 14


def _weighted_recent(series: list) -> float:
    """Geometrically-decayed mean of the newest RECENT_N values."""
    vals = [v for v in series[:RECENT_N] if isinstance(v, (int, float))]
    if not vals:
        return 0.0
    lam = math.log(2) / RECENT_HALFLIFE
    ws = [math.exp(-lam * i) for i in range(len(vals))]
    return sum(v * w for v, w in zip(vals, ws)) / sum(ws)


def _series(rows: list, col: str) -> list:
    out = []
    for r in rows:
        v = r.get(col)
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out.append(float(v))
    return out


def player_usage(player: str, season: int = None, as_of=None) -> dict:
    """Per-game usage for one player. {} when there is not enough to price.

    Returns the blended per-game mean for every base stat, the sigma the EVR
    grade reads, projected minutes, rotation risk, rest state and the window the
    numbers came from.

    `as_of` (a date) restricts the log to games BEFORE it — used by the backtest
    and by the reproducibility check, so a trace can be re-run against the same
    information the live scan had.
    """
    from . import client as _c
    try:
        season = season or _c.current_season()
        df = _c.load("player_game_logs", season)
        window = "current season"
        # ── THE PRIOR-SEASON FALLBACK, LABELLED ─────────────────────────────
        # Before opening night the current season has no rows at all. NFL hit
        # exactly this in week 1 and the answer there is the answer here: price
        # on last season if asked, but SAY SO on the row, and let board.py
        # decide whether a labelled row is allowed to post. A projection built
        # on a player's role from eight months ago is not wrong, it is
        # uninformed, and the two must be distinguishable.
        if not len(df):
            df = _c.load("player_game_logs", season - 1)
            window = "prior season only"
        if not len(df) or "player_name" not in df.columns:
            return {}
        key = _c._norm_name(player)
        d = df[df["player_name"].astype(str).map(_c._norm_name) == key]
        if not len(d):
            return {}
        d = d.sort_values("game_date", ascending=False)
        if as_of is not None:
            import pandas as pd
            d = d[d["game_date"] < pd.Timestamp(as_of)]
            if not len(d):
                return {}
        rows = d.to_dict("records")
        if len(rows) < MIN_GAMES:
            log.info("nba usage: %s has %d game(s) (< %d) — not priced",
                     player, len(rows), MIN_GAMES)
            return {}

        mins = _series(rows, "min")
        if not mins:
            return {}
        proj_min = RECENT_W * _weighted_recent(mins) + (1 - RECENT_W) * (
            sum(mins) / len(mins))
        recent_min = mins[:RECENT_N]
        min_sd = _st.pstdev(recent_min) if len(recent_min) > 1 else 0.0
        min_mean = sum(recent_min) / len(recent_min)
        min_cv = (min_sd / min_mean) if min_mean > 0 else 0.0
        rotation = ("stable" if min_cv <= ROTATION_STABLE
                    else "variable" if min_cv <= ROTATION_RISKY else "volatile")

        stats, sigma, per_min = {}, {}, {}
        for col in BASE_STATS:
            s = _series(rows, col)
            if not s:
                continue
            blended = RECENT_W * _weighted_recent(s) + (1 - RECENT_W) * (
                sum(s) / len(s))
            stats[col] = blended
            win = s[:SIGMA_N]
            sigma[col] = _st.pstdev(win) if len(win) > 1 else 0.0
            # Per-minute rate, for scaling to projected minutes. Computed from
            # the same blended figure over the same blended minutes so the two
            # cannot describe different windows.
            per_min[col] = (blended / min_mean) if min_mean > 0 else 0.0

        # Combo sigma from the per-game SUM, carrying the real correlation —
        # see COMBO_PARTS. The combo MEAN is not taken from here: props.py sums
        # the component projections so the card cannot contradict itself.
        for combo, parts in COMBO_PARTS.items():
            series = []
            for r in rows[:SIGMA_N]:
                vals = [r.get(p) for p in parts]
                if all(isinstance(v, (int, float)) and not (
                        isinstance(v, float) and math.isnan(v)) for v in vals):
                    series.append(float(sum(vals)))
            if len(series) > 1:
                sigma[combo] = _st.pstdev(series)
                stats[combo] = sum(series) / len(series)

        # ── HOME / AWAY SPLITS ───────────────────────────────────────────────
        # stats.nba.com encodes the venue in `matchup`: "PHI vs. BOS" is home,
        # "PHI @ BOS" is away. Computed per stat so the projection can use the
        # player's OWN split rather than a league constant, and surfaced whole
        # (both averages and both counts) so the card can show the number the
        # adjustment is made of instead of applying it silently.
        splits = {}
        home_rows = [r for r in rows if " vs. " in str(r.get("matchup") or "")]
        away_rows = [r for r in rows if " @ " in str(r.get("matchup") or "")]
        for col in BASE_STATS:
            h = _series(home_rows, col)
            aw = _series(away_rows, col)
            if not h and not aw:
                continue
            splits[col] = {
                "home": round(sum(h) / len(h), 3) if h else None,
                "away": round(sum(aw) / len(aw), 3) if aw else None,
                "home_games": len(h), "away_games": len(aw),
            }
        for combo, parts in COMBO_PARTS.items():
            def _sum_series(rs):
                out = []
                for r in rs:
                    vals = [r.get(p) for p in parts]
                    if all(isinstance(v, (int, float)) and not (
                            isinstance(v, float) and math.isnan(v))
                           for v in vals):
                        out.append(float(sum(vals)))
                return out
            h, aw = _sum_series(home_rows), _sum_series(away_rows)
            if h or aw:
                splits[combo] = {
                    "home": round(sum(h) / len(h), 3) if h else None,
                    "away": round(sum(aw) / len(aw), 3) if aw else None,
                    "home_games": len(h), "away_games": len(aw),
                }

        # Days since the last appearance, for the inactivity gate.
        last_date = rows[0].get("game_date")
        days_since = None
        try:
            import datetime as _dt
            import pandas as pd
            ref = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp(
                _dt.date.today())
            days_since = int((ref - pd.Timestamp(last_date)).days)
        except Exception:  # noqa: BLE001
            pass
        stale = None
        if isinstance(days_since, int):
            if days_since >= STALE_RED_DAYS:
                stale = "red"
            elif days_since >= STALE_AMBER_DAYS:
                stale = "amber"

        team = str(rows[0].get("team_abbreviation") or "")
        # A player who changed team mid-season carries two roles in one log.
        # Flagged rather than corrected: the recency weighting already favours
        # the current one, and a hard cut to post-trade games only would leave
        # a freshly traded player with three games of sample.
        teams = {str(r.get("team_abbreviation") or "") for r in rows}

        return {
            "player": str(rows[0].get("player_name") or player),
            "player_id": rows[0].get("player_id"),
            "team": _c.normalize_team(team),
            "teams_this_season": sorted(_c.normalize_team(t) for t in teams if t),
            "traded": len(teams) > 1,
            "games": len(rows),
            "window": window,
            "minutes": round(proj_min, 2),
            "minutes_sd": round(min_sd, 2),
            "minutes_cv": round(min_cv, 3),
            "rotation": rotation,
            "stats": {k: round(v, 3) for k, v in stats.items()},
            "sigma": {k: round(v, 3) for k, v in sigma.items()},
            "per_min": {k: round(v, 5) for k, v in per_min.items()},
            "splits": splits,
            "days_since_last": days_since,
            "stale": stale,
            "last_game": (str(last_date)[:10] if last_date is not None else None),
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba usage failed for %s: %s", player, exc)
        return {}


def rest_state(team: str, game_date=None, season: int = None) -> dict:
    """Rest context for a team on a given date.

    {days_rest, back_to_back, games_in_7}

    BACK-TO-BACK IS A SMALL NEGATIVE AND A VISIBLE FLAG, nothing more. The
    measured effect on counting stats is real but modest, and the honest version
    of that is a few per cent plus a label on the card — not a dramatic
    adjustment that would imply we know more than we do. Same treatment the
    tennis HOME flag gets: shown, not fed into the number, until the ledger
    justifies more.
    """
    from . import client as _c
    out = {"days_rest": None, "back_to_back": False, "games_in_7": None}
    try:
        import datetime as _dt
        import pandas as pd
        dates = _c.last_game_dates(season).get(_c.normalize_team(team) or "")
        if not dates:
            return out
        ref = (pd.Timestamp(game_date).date() if game_date is not None
               else _dt.date.today())
        prior = [d for d in dates if d < ref]
        if not prior:
            return out
        last = max(prior)
        gap = (ref - last).days
        out["days_rest"] = gap
        out["back_to_back"] = gap == 1
        out["games_in_7"] = sum(1 for d in prior if (ref - d).days <= 7)
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba rest_state failed for %s: %s", team, str(exc)[:160])
        return out


def usage_vacuum(team: str, player: str, season: int = None,
                 inj: dict = None) -> dict:
    """How much team usage is OUT tonight, and what that is worth to this player.

    A high-usage teammate missing is the single largest knowable swing in an NBA
    prop — bigger than any opponent adjustment — because the possessions do not
    vanish, they are redistributed. This measures the absent share and hands
    back a bounded multiplier.

    BOUNDED HARD, for the same reason nfl MATCHUP_MIN/MAX is: this is built from
    an injury report that is often provisional and a redistribution assumption
    that is league-average rather than team-specific. It is a correction, not a
    driver.
    """
    from . import client as _c
    out = {"factor": 1.0, "out": [], "basis": "no injury data"}
    try:
        inj = _c.injuries() if inj is None else inj
        if not inj:
            return out
        df = _c.load("player_game_logs", season or _c.current_season())
        if not len(df):
            df = _c.load("player_game_logs",
                         (season or _c.current_season()) - 1)
        if not len(df) or "team_abbreviation" not in df.columns:
            return out
        tm = _c.normalize_team(team)
        d = df[df["team_abbreviation"].astype(str).map(_c.normalize_team) == tm]
        if not len(d):
            return out
        # Share of team minutes each player carries, as the redistribution base.
        tot = d.groupby("player_name")["min"].sum()
        grand = float(tot.sum()) or 1.0
        me = _c._norm_name(player)
        absent, absent_share = [], 0.0
        for nm, m in tot.items():
            k = _c._norm_name(str(nm))
            if k == me:
                continue
            st = (inj.get(k) or {}).get("status", "")
            if st and st.strip().lower() in ("out", "injured",
                                             "suspension", "suspended"):
                absent.append(str(nm))
                absent_share += float(m) / grand
        if not absent:
            out["basis"] = "no listed absences"
            return out
        # Redistribution: the player picks up a share of the vacated usage
        # proportional to his own share of the REMAINING minutes, damped. The
        # damping is because minutes are capped by stamina and rotation, so a
        # team missing 20% of its usage does not hand any one player 20% more.
        my_share = float(tot.get(
            next((n for n in tot.index if _c._norm_name(str(n)) == me), ""), 0.0)
        ) / grand
        remaining = max(1e-6, 1.0 - absent_share)
        gain = absent_share * (my_share / remaining) * VACUUM_DAMP
        factor = max(VACUUM_MIN, min(VACUUM_MAX, 1.0 + gain))
        out.update({"factor": round(factor, 4), "out": absent,
                    "absent_share": round(absent_share, 4),
                    "basis": f"{len(absent)} listed OUT "
                             f"({absent_share * 100:.0f}% of team minutes)"})
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba usage_vacuum failed for %s: %s", player, str(exc)[:160])
        return out


# Damping and bounds on the usage-vacuum term — see usage_vacuum.
VACUUM_DAMP = 0.60
VACUUM_MIN, VACUUM_MAX = 1.0, 1.18


# ── HOME / AWAY AS A CALCULATED INPUT (operator, 2026-10-03) ─────────────────
# Promoted from display flag to a term in the projection. Two layers:
#
#   LEAGUE BASELINE   a small, uniform home edge on scoring props, inverted on
#                     the road. Real and well established, but small.
#   PLAYER SPLIT      his OWN home and away averages for that stat, which can
#                     be much larger than the baseline in either direction.
#
# THE SPLIT IS ONLY TRUSTED WITH SAMPLE BEHIND IT. Under HOME_SPLIT_MIN_GAMES on
# either side the player split is discarded entirely and the baseline carries
# the whole adjustment — a 6-game home average is a number about six games, and
# blending it in at 60% would let a hot fortnight masquerade as a venue effect.
# When it does qualify it is blended 60/40 with the baseline rather than used
# raw, because even 10 games is thin for a per-venue split.
#
# Capped at +/-6% total, separately from the defensive and pace clamps.
HOME_BASELINE = float(os.getenv("NBA_HOME_BASELINE", "0.015") or 0.015)
HOME_SPLIT_MIN_GAMES = int(os.getenv("NBA_HOME_MIN_GAMES", "10") or 10)
HOME_SPLIT_WEIGHT = 0.60
HOME_CLAMP = float(os.getenv("NBA_HOME_CLAMP", "0.06") or 0.06)

# Which stats the league baseline applies to. Scoring props carry a real home
# edge; rebounds and assists are far weaker and are left to the player's own
# split alone rather than given a constant they have not earned.
_BASELINE_STATS = ("pts", "fg3m", "nba_fantasy_pts", "pra", "pr", "pa")


def home_factor(usage: dict, stat: str, is_home) -> dict:
    """The home/away multiplier for one stat. {factor, basis, ...}.

    `is_home` None means the venue is unknown — the factor is 1.000 and says so,
    rather than quietly assuming a neutral court that does not exist.
    """
    out = {"factor": 1.0, "basis": "venue unknown", "home": None, "away": None,
           "home_games": 0, "away_games": 0, "used": "none"}
    try:
        if is_home is None:
            return out
        sp = ((usage or {}).get("splits") or {}).get(stat) or {}
        out.update({"home": sp.get("home"), "away": sp.get("away"),
                    "home_games": sp.get("home_games") or 0,
                    "away_games": sp.get("away_games") or 0})

        base = HOME_BASELINE if stat in _BASELINE_STATS else 0.0
        baseline_adj = base if is_home else -base

        h, aw = sp.get("home"), sp.get("away")
        enough = (out["home_games"] >= HOME_SPLIT_MIN_GAMES
                  and out["away_games"] >= HOME_SPLIT_MIN_GAMES)
        if enough and isinstance(h, (int, float)) and isinstance(aw, (int, float)) \
                and h > 0 and aw > 0:
            mean = (h + aw) / 2.0
            # His own effect, expressed as a multiplier on his overall average.
            own = ((h if is_home else aw) / mean) - 1.0
            adj = HOME_SPLIT_WEIGHT * own + (1 - HOME_SPLIT_WEIGHT) * baseline_adj
            out["used"] = "player split 60/40 with baseline"
            out["basis"] = (f"{h:.1f} home / {aw:.1f} away over "
                            f"{out['home_games']}/{out['away_games']} games, "
                            f"60/40 blend")
        else:
            adj = baseline_adj
            why = ("no league baseline for this stat" if base == 0.0 else
                   "league baseline")
            if not enough:
                out["used"] = "league baseline (split too thin)"
                out["basis"] = (f"{why} — needs {HOME_SPLIT_MIN_GAMES}+ games "
                                f"each side, has {out['home_games']}/"
                                f"{out['away_games']}")
            else:
                out["used"] = "league baseline"
                out["basis"] = why
        adj = max(-HOME_CLAMP, min(HOME_CLAMP, adj))
        out["factor"] = round(1.0 + adj, 4)
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba home_factor failed (%s): %s", stat, str(exc)[:160])
        return out
