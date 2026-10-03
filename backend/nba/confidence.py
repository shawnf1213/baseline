"""
NBA confidence — the EVR framework, ported into this module rather than imported.

PORTED, NOT SHARED, AND DELIBERATELY SO. The tennis implementation in
src/calculations/confidence.py is built on tennis objects — surface match lists,
head-to-head depth, venue familiarity, per-tour sample standards — and every one
of its components reads a tennis-shaped record. Importing it would mean either
reshaping NBA data into tennis data or editing the tennis module, and both are
forbidden: nfl/ and mlb/ import nothing from src/calculations, and an NBA data
problem must never be able to change a tennis number.

So the SHAPE is identical and the CONTENT is NBA's:

    edge-to-variance grade      ->  the data ceiling
    component scores            ->  sample size, minutes stability, recency,
                                    opponent data, availability
    bonus stacking cap          ->  +15 combined
    penalty cap                 ->  -40 combined
    one clamp, applied once     ->  floor 25, ceiling 95
    cap-reason indicators       ->  why a number stopped where it did

WHAT VARIANCE MEANS HERE. Tennis computes a per-stat sigma from surface match
history; this computes it from the player's own game-to-game spread over the
last 20 games, which usage.py already measures. That is the honest denominator
for "how far is this projection from the line, in units of how much this player
actually moves".

THE CEILING IS 95, NOT 99. Tennis widened to 99 on 2026-09-29 because with its
edge cap removed the ceiling had become the binding clamp on a third of its
board. NBA has no such history and no graded record yet, so it starts at the
conservative number. Rule 4: a new sport does not inherit another sport's
hard-won allowances.
"""

import logging
import os

log = logging.getLogger("baseline.nba.confidence")

# The single confidence ceiling for this sport. See the module docstring for
# why it is 95 rather than tennis's 99.
_CONF_CEILING = int(os.getenv("NBA_CONF_CEILING", "95") or 95)

# ── PER-STAT EVR SCALING ─────────────────────────────────────────────────────
# The edge-to-variance ratio is scaled per stat so a strong play in a tight prop
# grades comparably with a strong play in a loose one. Derived from the fitted
# spreads in _ratios.py and the measured sigmas: on the 2025-26 season a typical
# rotation player's game-to-game sd is roughly
#
#     pts   5.4     reb   2.5     ast   2.8     fg3m  1.2     fantasy  11.4
#
# relative to means of ~25 / 4 / 6 / 2.4 / 42. Rebounds and assists therefore
# sit at a much HIGHER sd/mean than points, so an identical raw ratio means
# something different in each — these scale the ratio onto one grading curve.
#
# Tunable as real per-stat ratio distributions accumulate, exactly as the tennis
# PROP_EVR_SCALE comment says of its own numbers.
STAT_EVR_SCALE = {
    "pts":  1.0,
    "reb":  1.5,
    "ast":  1.5,
    "fg3m": 1.7,
    "nba_fantasy_pts": 1.2,
}

# Anchor points mapping a (stat-scaled) edge-to-variance ratio -> confidence
# ceiling. Interpolated between anchors so the grade is continuous, not stepped.
# Identical anchors to tennis: the curve is a statement about how much a given
# standardised edge is worth, which is not sport-specific, while the SCALING
# that feeds it is.
_EVR_ANCHORS = [(0.0, 55), (0.25, 64), (0.5, 72), (1.0, 80), (1.5, 85),
                (2.0, 88), (2.5, 89)]


def _evr_grade(x: float) -> int:
    """Piecewise-linear ceiling from a stat-scaled edge-to-variance ratio."""
    if x <= _EVR_ANCHORS[0][0]:
        return _EVR_ANCHORS[0][1]
    if x >= _EVR_ANCHORS[-1][0]:
        return _EVR_ANCHORS[-1][1]
    for (x0, y0), (x1, y1) in zip(_EVR_ANCHORS, _EVR_ANCHORS[1:]):
        if x0 <= x <= x1:
            return int(round(y0 + (y1 - y0) * (x - x0) / (x1 - x0)))
    return _EVR_ANCHORS[-1][1]


# ── PER-PROP CEILINGS ────────────────────────────────────────────────────────
# A COMBO IS COMPOUNDED FROM SEVERAL MODELS, so it carries compounded
# uncertainty and cannot reach the same ceiling as a directly-counted stat. This
# is the same treatment tennis gives Player Total Games Won and Total Games
# (both capped at 80) for exactly the same reason, and the spec asks for it
# explicitly: combos are the NBA's Total Games equivalent.
#
# 3PM is capped a little below the single-stat default as well — not because it
# is derived, but because it is the streakiest prop on the board. See
# VARIANCE_CAP below for the mechanism that usually binds first.
PROP_CONFIDENCE_CEILING = {
    "pra": 80, "pr": 80, "pa": 80, "ra": 80,
    "nba_fantasy_pts": 80,
    "fg3m": 88,
}

# ── THE VARIANCE CAP ─────────────────────────────────────────────────────────
# A stat whose own spread is enormous relative to its mean cannot support a high
# confidence however much data stands behind it — more games measuring a wild
# distribution tells you the distribution is wild, not that the next value is
# predictable. This is the NBA's version of the tennis ace-variance cap.
#
# Keyed on the coefficient of variation of the player's OWN last-20 games.
VARIANCE_CAP_CV = 0.60
VARIANCE_CAP = 82

# A player below this many stat-rich games cannot reach the top band. Tennis
# requires 15+ surface matches on both sides for 85+; the NBA equivalent is a
# meaningful slice of a season.
DEEP_SAMPLE_GAMES = 25
SHALLOW_CEILING = 84

# ── KNIFE EDGE ───────────────────────────────────────────────────────────────
# A projection sitting within one typical single-event increment of the line is
# a coin flip dressed as an edge, and the increment differs per stat: one made
# three is a whole unit of a 2.5 line, while one point is nothing on a 24.5
# points line. Values from the spec.
KNIFE_EDGE = {
    "fg3m": 0.5,
    "pts": 2.0,
    "reb": 1.0,
    "ast": 1.0,
    "nba_fantasy_pts": 3.0,
    "pra": 3.0, "pr": 2.5, "pa": 2.5, "ra": 1.5,
}
KNIFE_EDGE_PENALTY = -10


def finalize_confidence(total, prop: str = "", data_ceiling: int = None) -> int:
    """The SINGLE confidence floor/cap for NBA.

    Floor 25, ceiling 95, minus any per-prop ceiling for compounded props, minus
    the data_ceiling imposed by the EVR grade and data quality. This is the ONLY
    place NBA confidence is clamped; applied once as the final step after every
    modifier — the same discipline as tennis finalize_confidence, and for the
    same reason: a value clamped in two places is a value nobody can explain.
    """
    ceiling = min(_CONF_CEILING,
                  data_ceiling if isinstance(data_ceiling, (int, float))
                  else _CONF_CEILING)
    prop_ceiling = PROP_CONFIDENCE_CEILING.get(prop)
    if prop_ceiling is not None:
        ceiling = min(ceiling, prop_ceiling)
    try:
        t = round(total)
    except (TypeError, ValueError):
        t = 25
    return int(max(25, min(ceiling, t)))


# Which component scores are DISCRETIONARY bonuses, subject to the +15 stacking
# cap. Measured data-depth signals (sample_size, opponent_data) are excluded for
# the same reason tennis excludes h2h and opponent: capping them would gut
# legitimately strong, deep-sample picks.
_BONUS_KEYS = ("recency", "minutes_stability", "usage_vacuum")


def calculate_confidence(usage: dict, stat: str, prop: str, projection: float,
                         line: float, sigma: float, opponent_known: bool = True,
                         vacuum: dict = None, rest: dict = None) -> dict:
    """Confidence for one NBA prop, with the full component breakdown.

    Returns {confidence, breakdown, data_ceiling, cap_reason, cap_tag,
             evr, evr_scaled, coin_flip}.

    Never raises — a confidence failure must cost the number, never the board.
    """
    try:
        breakdown = {}

        # ── 1. Sample size — the primary data-depth signal ───────────────────
        games = int(usage.get("games") or 0)
        if games >= 50:
            ss, lbl = 55, f"{games} games — full-season sample"
        elif games >= DEEP_SAMPLE_GAMES:
            ss, lbl = 48, f"{games} games — solid sample"
        elif games >= 15:
            ss, lbl = 38, f"{games} games — workable sample"
        else:
            ss, lbl = 26, f"only {games} games — thin sample"
        breakdown["sample_size"] = {"score": ss, "label": lbl}

        # ── 2. Minutes stability ─────────────────────────────────────────────
        # Minutes are the master variable, so their reliability is a first-class
        # component rather than a footnote. A volatile rotation is the NBA's
        # high-variance-player penalty.
        cv = float(usage.get("minutes_cv") or 0.0)
        rot = usage.get("rotation") or "unknown"
        if rot == "stable":
            ms, mlbl = 8, f"minutes stable (cv {cv:.2f})"
        elif rot == "variable":
            ms, mlbl = 0, f"minutes move week to week (cv {cv:.2f})"
        else:
            ms, mlbl = -14, f"ROTATION RISK — minutes volatile (cv {cv:.2f})"
        breakdown["minutes_stability"] = {"score": ms, "label": mlbl}

        # ── 3. Recency alignment ─────────────────────────────────────────────
        # Does the player's recent form point the same way as the lean? Scored,
        # not gated: recency is already inside the projection via the weighted
        # blend, so using it again as a filter would double-count it — the same
        # correction nfl/pick_of_day made when it demoted its recent-form gate
        # to an info signal.
        lean_over = (projection or 0) >= (line or 0)
        s = (usage.get("stats") or {}).get(stat)
        recent_lbl, rs = "recency unavailable", 0
        if isinstance(s, (int, float)) and isinstance(line, (int, float)):
            agrees = (s >= line) == lean_over
            rs = 5 if agrees else -8
            recent_lbl = ("recent form agrees with the lean" if agrees
                          else "recent form points the other way")
        breakdown["recency"] = {"score": rs, "label": recent_lbl}

        # ── 4. Opponent data ─────────────────────────────────────────────────
        od = 7 if opponent_known else 0
        breakdown["opponent_data"] = {
            "score": od,
            "label": ("opponent defensive + pace splits available"
                      if opponent_known else "no opponent rating — league-neutral")}

        # ── 5. Availability ──────────────────────────────────────────────────
        # The tennis inactivity treatment, on the NBA's denser schedule.
        stale = usage.get("stale")
        days = usage.get("days_since_last")
        if stale == "red":
            av, albl = -22, f"has not played in {days} days — role unknown"
        elif stale == "amber":
            av, albl = -10, f"{days} days since last appearance"
        else:
            av, albl = 0, "active"
        breakdown["availability"] = {"score": av, "label": albl}

        # ── 6. Usage vacuum ──────────────────────────────────────────────────
        vac = (vacuum or {}).get("factor", 1.0)
        if isinstance(vac, (int, float)) and vac > 1.02:
            breakdown["usage_vacuum"] = {
                "score": 4,
                "label": f"usage up — {(vacuum or {}).get('basis', 'absences')}"}

        # ── 7. Rest ──────────────────────────────────────────────────────────
        # A small negative and a visible flag, nothing more — see usage.rest_state.
        if (rest or {}).get("back_to_back"):
            breakdown["rest"] = {
                "score": -5, "label": "second night of a back-to-back"}

        # ── 8. Traded mid-season ─────────────────────────────────────────────
        if usage.get("traded"):
            breakdown["role_change"] = {
                "score": -6,
                "label": "changed team this season — the log spans two roles"}

        # ── Bonus stacking cap, +15 combined ─────────────────────────────────
        bonus_sum = sum(max(0, breakdown[k]["score"])
                        for k in _BONUS_KEYS if k in breakdown)
        if bonus_sum > 15:
            overflow = bonus_sum - 15
            breakdown["bonus_cap"] = {
                "score": -overflow,
                "label": f"Bonus cap — combined bonuses limited to +15 "
                         f"(was +{round(bonus_sum)})"}

        # ── Penalty cap, -40 combined ────────────────────────────────────────
        penalty_sum = sum(min(0, v["score"]) for k, v in breakdown.items()
                          if k not in ("bonus_cap",))
        if penalty_sum < -40:
            breakdown["penalty_cap"] = {
                "score": -40 - penalty_sum,
                "label": f"Penalty cap applied — combined penalties limited to "
                         f"-40 (was {round(penalty_sum)})"}

        total = sum(v["score"] for v in breakdown.values())

        # ── THE EVR GRADE BECOMES THE DATA CEILING ───────────────────────────
        # How far the projection sits from the line, in units of how much this
        # player actually moves game to game.
        evr = evr_scaled = None
        data_ceiling, cap_reason, cap_tag = _CONF_CEILING, "", ""
        if isinstance(sigma, (int, float)) and sigma > 0 and \
                isinstance(projection, (int, float)) and \
                isinstance(line, (int, float)):
            evr = abs(float(projection) - float(line)) / float(sigma)
            evr_scaled = evr * STAT_EVR_SCALE.get(stat, 1.0)
            g = _evr_grade(evr_scaled)
            if g < data_ceiling:
                data_ceiling = g
                cap_reason = (f"edge is {evr:.2f} sigma of this player's own "
                              f"game-to-game spread")
                cap_tag = "evr-capped"

        # ── The variance cap ─────────────────────────────────────────────────
        mean = (usage.get("stats") or {}).get(stat)
        if isinstance(mean, (int, float)) and mean > 0 and \
                isinstance(sigma, (int, float)):
            stat_cv = float(sigma) / float(mean)
            if stat_cv >= VARIANCE_CAP_CV and data_ceiling > VARIANCE_CAP:
                data_ceiling = VARIANCE_CAP
                cap_reason = (f"{stat} swings {stat_cv:.0%} of its own mean "
                              f"game to game")
                cap_tag = "variance-capped"

        # ── Sample depth ─────────────────────────────────────────────────────
        if games < DEEP_SAMPLE_GAMES and data_ceiling > SHALLOW_CEILING:
            data_ceiling = SHALLOW_CEILING
            cap_reason = f"85+ needs {DEEP_SAMPLE_GAMES}+ games; this has {games}"
            cap_tag = "sample-capped"

        # ── Prior-season-only data ───────────────────────────────────────────
        if usage.get("window") == "prior season only" and data_ceiling > 65:
            data_ceiling = 65
            cap_reason = "priced on prior-season usage — no current-season games"
            cap_tag = "data-capped"

        # ── Knife edge ───────────────────────────────────────────────────────
        coin_flip = False
        ke = KNIFE_EDGE.get(prop) or KNIFE_EDGE.get(stat)
        if ke and isinstance(projection, (int, float)) and \
                isinstance(line, (int, float)):
            if abs(float(projection) - float(line)) < ke:
                coin_flip = True
                breakdown["knife_edge"] = {
                    "score": KNIFE_EDGE_PENALTY,
                    "label": f"projection is within {ke:g} of the line — "
                             f"effectively a coin flip"}
                total += KNIFE_EDGE_PENALTY

        conf = finalize_confidence(total, prop, data_ceiling)
        return {
            "confidence": conf,
            "raw_total": round(total, 1),
            "breakdown": breakdown,
            "data_ceiling": data_ceiling,
            "cap_reason": cap_reason,
            "cap_tag": cap_tag,
            "evr": round(evr, 3) if evr is not None else None,
            "evr_scaled": round(evr_scaled, 3) if evr_scaled is not None else None,
            "coin_flip": coin_flip,
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba confidence failed (%s %s): %s", stat, prop, exc)
        return {"confidence": 25, "breakdown": {}, "data_ceiling": _CONF_CEILING,
                "cap_reason": "confidence error", "cap_tag": "error",
                "evr": None, "evr_scaled": None, "coin_flip": False}
