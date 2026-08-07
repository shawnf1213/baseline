"""
MLB pitcher strikeouts — projection model.

SHADOW ONLY (North Star rule 4). Nothing here posts or writes.

WHY THIS IS NOT A SCENARIO MIXTURE
----------------------------------
Rule 5 mandates scenario mixtures for bimodal props and names MLB strikeouts as
one. Measured on 416 real starts (2026-08-07, every announced starter on that
slate), strikeouts are NOT bimodal:

    0 K   2 | 1 K  18 | 2 K  40 | 3 K  48 | 4 K  71 | 5 K  62 | 6 K  62
    7 K  47 | 8 K  22 | 9 K  26 | 10 K 14 | 11+ K  4
    mean 5.14  median 5  sd 2.41  skew +0.42  kurtosis 3.03
    variance/mean = 1.13   (Poisson would be 1.00)

Single peak, mild right skew, near-normal kurtosis, 13% overdispersed.

The tennis rationale does not transfer. Tennis match-outcome props are bimodal
because they split on a genuine binary — a player who WINS takes at least 12
games, one who LOSES takes fewer — so the mean falls in an empty valley between
two bands and misprices. Strikeouts have no such binary: start length is
concentrated (batters faced mean 22.7, sd 3.5), short hooks are only 3.4% of
starts, and K is a count process on top of it. There is no valley.

So this uses an OVERDISPERSED COUNT model, which is what the data is. Shawn
approved the deviation 2026-08-07; logged in NORTH_STAR.md as a scoped exception
per the working rule that model-philosophy changes get recorded. The scenario
machinery stays available for MLB props that genuinely are bimodal (a pitcher
win, or anything conditioned on game outcome).

LOOKBACK WINDOW — CURRENT SEASON, not 52 weeks
----------------------------------------------
Tennis uses a 52-week rolling window because tennis plays ~48 weeks a year, so 52
weeks is one competitive cycle. MLB's regular season is ~27 weeks (2026: Mar 25 ->
Sep 27, 186 days), so a 52-week window necessarily spans the offseason and drags
in the previous campaign.

An initial holdout on 21 pitchers from one slate favoured 52 weeks (3.52pp vs
3.72pp mean absolute error on K rate) and this module shipped that way. The
multi-season fit REVERSED it. On 59 qualified starters:

    season-only   3.861 pp
    52-week       4.020 pp        52wk better on only 11/59 pitchers (19%)

The first result was small-sample noise. Pitchers change materially between
seasons — role, health, pitch mix — and last year's tail is stale rather than
extra signal. Season-only it is.

EARLY-SEASON FALLBACK: in April a season-only window holds two or three starts,
which is not a rate. Below MIN_STARTS the window extends into the prior season —
accepting stale data is better than projecting off a sample too thin to speak,
and the extension is reported in the output so it is never silent.
"""

import logging
import math
import datetime as _dt

from . import client

log = logging.getLogger("baseline.mlb.strikeouts")

MIN_STARTS = 5                    # below this the rate is a rumour, not a stat

# ── Fitted on 6,092 starts, all qualified starters 2023-2026 ─────────────────
# Variance/mean for a pitcher's K count. 1.00 would be pure Poisson; the excess
# is real start-to-start variation in length, opponent and pitch count that a
# point estimate cannot see.
#
# NOT perfectly stable across seasons — 2023 1.131, 2024 1.029, 2025 1.134,
# 2026 1.120 (spread 0.105, with 2024 the outlier at near-Poisson). Pooled 1.105
# is used rather than the latest season so one anomalous year cannot swing it.
DISPERSION = 1.105

# Population anchors from the same pooled sample. Fallbacks only, so a data
# outage degrades to a league-average starter rather than a divide-by-zero.
LEAGUE_BF_PER_START = 23.73       # was 22.7 from a single slate — undercounted
LEAGUE_K_RATE = 0.2348            # K per batter faced, pitching side
LEAGUE_TEAM_K_RATE = 0.221        # K per plate appearance, batting side

# How hard the opponent's contact profile moves the projection. 1.0 = fully
# proportional (a team that strikes out 10% more relative to league lifts the
# projection 10%). Held at 1.0 until there is a fitted value — an invented
# damping constant is exactly the kind of unfitted number that put _BP_BASE_POP
# 11% off its own model on the tennis side.
OPP_WEIGHT = 1.0


def pitcher_form(pitcher_id, as_of: _dt.date = None) -> dict:
    """Current-season starting line for one pitcher (see module docstring on
    why this is season-only rather than a 52-week roll).

    Rates are sum/sum — total strikeouts over total batters faced — never a mean
    of per-start rates. A mean-of-rates over-weights short starts, which is the
    same error the tennis side avoids everywhere it computes a rate.

    Returns {} when the sample is too thin to speak, rather than a number with no
    denominator behind it.
    """
    as_of = as_of or _dt.date.today()

    def _starts(season):
        return [r for r in client.get_pitcher_game_log(pitcher_id, season)
                if r.get("is_start")
                and isinstance(r.get("bf"), (int, float)) and r["bf"]]

    rows = _starts(as_of.year)
    extended = False
    if len(rows) < MIN_STARTS:
        # Early-season fallback: a two-start sample is not a rate. Reach back
        # rather than project off nothing — and SAY SO in the output.
        prior = _starts(as_of.year - 1)
        if prior:
            rows = rows + prior
            extended = True
    if len(rows) < MIN_STARTS:
        log.info("mlb strikeouts: pitcher %s has %d starts (< %d) — no projection",
                 pitcher_id, len(rows), MIN_STARTS)
        return {}
    bf = sum(r["bf"] for r in rows)
    k = sum(r.get("k") or 0 for r in rows)
    ip = sum(r.get("ip") or 0.0 for r in rows)
    return {
        "pitcher_id": pitcher_id,
        "starts": len(rows),
        "batters_faced": bf,
        "strikeouts": k,
        "innings": round(ip, 1),
        "k_rate": k / bf,
        "bf_per_start": bf / len(rows),
        "window": ("current season + prior (extended: thin sample)" if extended
                   else "current season"),
        "window_extended": extended,
    }


def project(pitcher_id, opponent_team_id, line=None, as_of: _dt.date = None,
            team_batting: dict = None) -> dict:
    """Project a starter's strikeouts against one opponent.

        expected K = expected batters faced x k_rate x opponent factor

    The opponent factor is the batting side's K-per-plate-appearance RELATIVE to
    league, so the model stays stable when the run environment shifts year to
    year — an absolute rate would drift with the league.

    Returns {} when the pitcher's sample is too thin. With `line`, adds p_over /
    p_under from a negative binomial with the measured dispersion. Never raises.
    """
    try:
        form = pitcher_form(pitcher_id, as_of=as_of)
        if not form:
            return {}
        tb = team_batting if team_batting is not None else client.get_team_batting()
        lg = client.league_k_rate(tb) if tb else LEAGUE_TEAM_K_RATE
        opp = (tb or {}).get(opponent_team_id) or {}
        opp_k = opp.get("k_rate")
        if isinstance(opp_k, (int, float)) and lg > 0:
            opp_factor = 1.0 + OPP_WEIGHT * ((opp_k / lg) - 1.0)
        else:
            opp_factor = 1.0            # unknown opponent -> league-neutral

        exp_bf = form["bf_per_start"]
        mu = exp_bf * form["k_rate"] * opp_factor

        out = {
            "sport": "mlb",
            "prop": "strikeouts",
            "pitcher_id": pitcher_id,
            "opponent_team_id": opponent_team_id,
            "projection": round(mu, 2),
            "expected_bf": round(exp_bf, 1),
            "k_rate": round(form["k_rate"], 4),
            "opponent_k_rate": round(opp_k, 4) if isinstance(opp_k, (int, float)) else None,
            "league_k_rate": round(lg, 4),
            "opponent_factor": round(opp_factor, 3),
            "starts_in_window": form["starts"],
            "window": form["window"],
            "window_extended": form["window_extended"],
            "model": "negative-binomial (overdispersed count), NOT a scenario mixture",
        }
        if isinstance(line, (int, float)):
            out.update(_over_under(mu, line))
            out["line"] = line
        return out
    except Exception as exc:  # noqa: BLE001
        # Rule 2: an MLB failure is caught here and can never reach another sport.
        log.exception("mlb strikeouts projection failed (pitcher=%s): %s",
                      pitcher_id, exc)
        return {}


def _nb_params(mu: float, dispersion: float = DISPERSION):
    """Negative binomial (r, p) from a mean and a variance/mean ratio.

    var = mu * dispersion, and for NB var = mu + mu^2/r, so r = mu/(dispersion-1).
    At dispersion <= 1 the distribution is Poisson or under-dispersed and NB has
    no valid r — callers fall back to Poisson.
    """
    if dispersion <= 1.0 or mu <= 0:
        return None, None
    r = mu / (dispersion - 1.0)
    p = r / (r + mu)
    return r, p


def _pmf(k: int, mu: float) -> float:
    """P(K = k). Negative binomial at the measured dispersion, Poisson if the
    dispersion degenerates."""
    r, p = _nb_params(mu)
    if r is None:
        return math.exp(-mu) * mu ** k / math.factorial(k)
    return (math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1))
            * (p ** r) * ((1 - p) ** k))


def _over_under(mu: float, line: float) -> dict:
    """P(over) / P(under) for a strikeout line.

    Lines are almost always X.5, so no continuity correction is needed; a whole
    -number line would push, which the caller must handle rather than this
    function silently folding into one side.
    """
    floor = int(math.floor(line))
    p_at_or_under = sum(_pmf(k, mu) for k in range(0, floor + 1))
    p_at_or_under = max(0.0, min(1.0, p_at_or_under))
    return {
        "p_over": round(1.0 - p_at_or_under, 4),
        "p_under": round(p_at_or_under, 4),
        "lean": "OVER" if (1.0 - p_at_or_under) >= 0.5 else "UNDER",
        "dispersion": DISPERSION,
    }
