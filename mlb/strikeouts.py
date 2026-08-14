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

from . import client, context as _ctx
from core import odds as _odds

log = logging.getLogger("baseline.mlb.strikeouts")

MIN_STARTS = 5                    # below this the rate is a rumour, not a stat

# ── OPENER / SHORT-ROLE DETECTION ────────────────────────────────────────────
# A starter faces ~23 batters. An OPENER is announced as the starter and is
# pulled after one or two innings by design — a bullpen game wearing a starter's
# label. Projecting him on a starter's workload is not slightly wrong, it is
# several times wrong.
#
# Sean Newcomb, 2026-08-14: pulled in the FIRST, 4 batters faced. He had exactly
# two 2026 starts (9 BF and 4 BF), which is below MIN_STARTS, so the early-season
# fallback reached into 2025 — when he really was a starter at 18-24 BF — and
# averaged the two into 17.0 BF/start. The model then projected him as a
# conventional starter for a game his team planned to open.
#
# THE FALLBACK ASSUMES PRIOR-SEASON DATA IS STALE. Sometimes it is INVALID: a
# role change makes last year's workload a description of a different job. So
# the current season now gets to veto the extension.
OPENER_BF = 12.0            # a start under this is not a starter's workload
ROLE_CHANGE_RATIO = 0.65    # current season this far below prior = role change

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

# ── OPPONENT ADJUSTMENT: log5 / odds ratio ───────────────────────────────────
# Bill James' log5, the standard method for combining two rates against a league
# baseline. Sport-agnostic — the same identity is used for NBA/NFL matchup rates —
# so it belongs in the shared core rather than here once that exists.
#
#     expected = (P*B/L) / ( (P*B/L) + ((1-P)(1-B)/(1-L)) )
#
# Chosen over the linear factor this module shipped with (proj * (1 + w*(B/L - 1)))
# NOT because it measured better — on 92 held-out starts the two are
# indistinguishable, 8.787pp vs 8.773pp mean absolute error, log5 ahead on 41% —
# but because it has NO FREE PARAMETER. The linear form needs a weight, and the
# weight was sitting at an invented 1.0; fitting it on this sample would be
# fitting noise, since per-start K rate error is ~8.8pp against a ~0.4pp
# adjustment gain. log5 removes the question. It is also bounded on [0,1], which
# the linear form is not.
#
# Both beat no adjustment at all (9.141pp), so the opponent term earns its place.
# See FanGraphs, "Better Match-Up Data: Forecasting Strikeout Rate".


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
    role_note = None
    cur_bf = (sum(r["bf"] for r in rows) / len(rows)) if rows else None

    # A pitcher his team is currently using in short outings gets NO extension,
    # however thin the sample. Two starts of 9 and 4 batters is a small sample
    # about his ROLE, and it is not improved by adding last season's data about a
    # different one.
    if rows and len(rows) >= 2 and cur_bf is not None and cur_bf < OPENER_BF:
        log.info("mlb strikeouts: pitcher %s averaging %.1f BF over %d start(s) "
                 "this season — opener/short role, not extending to prior year",
                 pitcher_id, cur_bf, len(rows))
        return {
            "pitcher_id": pitcher_id, "starts": len(rows),
            "opener_risk": True, "bf_per_start": round(cur_bf, 1),
            "role_note": (f"{cur_bf:.1f} batters faced per start this season — "
                          f"used as an opener or in short relief"),
        }

    if len(rows) < MIN_STARTS:
        # Early-season fallback: a two-start sample is not a rate. Reach back
        # rather than project off nothing — and SAY SO in the output.
        prior = _starts(as_of.year - 1)
        if prior:
            prior_bf = sum(r["bf"] for r in prior) / len(prior)
            # Veto: if this season's workload is far below last season's, the
            # role changed and the prior year describes a different pitcher.
            if (cur_bf is not None and len(rows) >= 2 and prior_bf > 0
                    and cur_bf / prior_bf < ROLE_CHANGE_RATIO):
                log.info("mlb strikeouts: pitcher %s at %.1f BF/start vs %.1f "
                         "last season — role change, refusing the extension",
                         pitcher_id, cur_bf, prior_bf)
                return {
                    "pitcher_id": pitcher_id, "starts": len(rows),
                    "opener_risk": True, "bf_per_start": round(cur_bf, 1),
                    "role_note": (f"{cur_bf:.1f} batters faced per start this "
                                  f"season against {prior_bf:.1f} last season — "
                                  f"role has changed"),
                }
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
            team_batting: dict = None, game_pk=None, is_home: bool = None,
            home_team_id=None, use_context: bool = True) -> dict:
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
        # AN OPENER IS NOT A THIN SAMPLE, IT IS THE WRONG QUESTION. The model
        # prices ~23 batters faced; a pitcher being pulled after one inning by
        # design is several times off, not slightly. Refuse with a reason rather
        # than return a number nobody can act on.
        if form.get("opener_risk"):
            return {"skipped": True, "sport": "mlb", "prop": "strikeouts",
                    "pitcher_id": pitcher_id, "opener_risk": True,
                    "reason": form.get("role_note")}
        tb = team_batting if team_batting is not None else client.get_team_batting()
        lg = client.league_k_rate(tb) if tb else LEAGUE_TEAM_K_RATE
        opp = (tb or {}).get(opponent_team_id) or {}
        opp_k = opp.get("k_rate")
        p_rate = form["k_rate"]
        basis, ctx_detail, park, ha_factor = "team", {}, 1.0, 1.0

        if use_context:
            # ── OPPONENT: lineup > platoon > team (context.opponent_k_rate) ──
            oc = _ctx.opponent_k_rate(pitcher_id, opponent_team_id,
                                      game_pk=game_pk, is_home=is_home,
                                      team_batting=tb, league_rate=lg)
            if oc.get("opponent_rate") is not None:
                opp_k = oc["opponent_rate"]
            basis = oc.get("basis", "team")
            ctx_detail = oc.get("detail") or {}

            # ── PITCHER SIDE: home/away split, shrunk toward his overall rate ──
            sp = _ctx.pitcher_splits(pitcher_id)
            side = "home" if is_home else "away"
            if is_home is not None and sp.get(side) is not None:
                shrunk_side = _ctx._shrink(sp[side], sp.get(side + "_bf"),
                                           p_rate, _ctx.SPLIT_PRIOR_BF)
                ha_factor = (shrunk_side / p_rate) if p_rate else 1.0
                ctx_detail["home_away"] = {"side": side,
                                           "split_rate": round(sp[side], 4),
                                           "factor": round(ha_factor, 3)}

            # ── PARK: the venue is the HOME team's ──────────────────────────
            pf = _ctx.park_factors()
            venue_team = home_team_id if home_team_id is not None else (
                opponent_team_id if is_home is False else None)
            if venue_team is not None and venue_team in pf:
                park = pf[venue_team]
                ctx_detail["park_factor"] = round(park, 3)

        if isinstance(opp_k, (int, float)) and 0 < lg < 1:
            adj_rate = log5(p_rate, opp_k, lg)
        else:
            adj_rate = p_rate           # unknown opponent -> pitcher's own rate
        # Environment multipliers apply to the RATE, not the volume: a K-friendly
        # park changes how often a batter faced becomes a strikeout, it does not
        # make the manager leave the starter in longer.
        adj_rate = max(0.01, min(0.60, adj_rate * ha_factor * park))
        opp_factor = (adj_rate / p_rate) if p_rate else 1.0

        exp_bf = form["bf_per_start"]
        mu = exp_bf * adj_rate

        out = {
            "sport": "mlb",
            "prop": "strikeouts",
            "pitcher_id": pitcher_id,
            "opponent_team_id": opponent_team_id,
            "projection": round(mu, 2),
            "expected_bf": round(exp_bf, 1),
            "k_rate": round(form["k_rate"], 4),
            "adjusted_k_rate": round(adj_rate, 4),
            "opponent_k_rate": round(opp_k, 4) if isinstance(opp_k, (int, float)) else None,
            "league_k_rate": round(lg, 4),
            "opponent_factor": round(opp_factor, 3),
            "starts_in_window": form["starts"],
            "window": form["window"],
            "window_extended": form["window_extended"],
            "model": "negative-binomial (overdispersed count), NOT a scenario mixture",
            "opponent_basis": basis,
            "context": ctx_detail,
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


# log5 is a sport-agnostic identity, so it lives in core/odds.py and is aliased
# here rather than duplicated. Re-exported so mlb.strikeouts.log5 still resolves.
log5 = _odds.log5


def _pmf(k: int, mu: float) -> float:
    """P(K = k) at the measured MLB dispersion. Thin wrapper over the shared
    count distribution so the MLB constant lives with the MLB model."""
    return _odds.nb_pmf(k, mu, DISPERSION)


def _over_under(mu: float, line: float) -> dict:
    """P(over) / P(under) / P(push) for a strikeout line, via the shared count
    helper. Whole-number lines push and are reported separately — folding a push
    into a side is how a record gets misstated."""
    r = _odds.count_over_under(mu, line, dispersion=DISPERSION)
    return {
        "p_over": round(r["p_over"], 4),
        "p_under": round(r["p_under"], 4),
        "p_push": round(r["p_push"], 4),
        "lean": r["lean"],
        "dispersion": DISPERSION,
    }
