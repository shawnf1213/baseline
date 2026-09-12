"""
Sport-agnostic odds and rate math.

Nothing in this module knows what sport it is serving. Everything here is a
mathematical identity that holds for tennis, baseball or anything else, which is
the test for whether a function belongs in core/ rather than in a sport module.

Per NORTH_STAR rule 6, the market anchor is shared: de-vig lives here and is
reused per sport, never rebuilt.
"""

import math

_SQRT2 = math.sqrt(2.0)


# ── Implied probability / de-vig ─────────────────────────────────────────────
def implied_prob(american) -> float:
    """American price -> implied probability, vig included. None if unparseable."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig_two_way(over_american, under_american) -> tuple:
    """(p_over, p_under), vig removed proportionally.

    Proportional (a.k.a. "normalise the overround") is the method the tennis side
    already uses for moneylines, kept identical here so a market probability means
    the same thing in every sport. (None, None) when either side is unusable —
    a one-sided price is not a market.
    """
    po, pu = implied_prob(over_american), implied_prob(under_american)
    if po is None or pu is None:
        return (None, None)
    tot = po + pu
    if tot <= 0:
        return (None, None)
    return (po / tot, pu / tot)


def overround(over_american, under_american) -> float:
    """How much vig the book is holding, as a fraction. 0.045 = a 4.5% hold.
    Useful as a data-quality signal: an implausible overround usually means a
    stale or mismatched price rather than a generous book."""
    po, pu = implied_prob(over_american), implied_prob(under_american)
    if po is None or pu is None:
        return None
    return (po + pu) - 1.0


# ── Rate combination ─────────────────────────────────────────────────────────
def log5(rate_a: float, rate_b: float, league: float) -> float:
    """Bill James' log5 / odds ratio — combine two rates against a league baseline.

        expected = (A*B/L) / ( (A*B/L) + ((1-A)(1-B)/(1-L)) )

    Sport-agnostic by construction: it is a statement about combining two
    independent propensities relative to a population, with no free parameter.
    MLB uses it for pitcher-K% vs team-K%; the same identity applies to any
    matchup of two rates on a common baseline.

    Returns rate_a unchanged on degenerate input, so a bad league value can never
    invert a matchup.
    """
    if not (0 < league < 1) or not (0 < rate_a < 1) or not (0 < rate_b < 1):
        return rate_a
    num = rate_a * rate_b / league
    den = num + ((1 - rate_a) * (1 - rate_b) / (1 - league))
    return (num / den) if den else rate_a


# ── Count distributions ──────────────────────────────────────────────────────
def nb_pmf(k: int, mu: float, dispersion: float) -> float:
    """P(K = k) for an overdispersed count.

    Negative binomial parameterised by mean and variance/mean ratio, which is how
    the dispersion is actually measured from data. Falls back to Poisson at
    dispersion <= 1, where NB has no valid shape parameter.
    """
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    if dispersion <= 1.0:
        return math.exp(-mu) * mu ** k / math.factorial(k)
    r = mu / (dispersion - 1.0)
    p = r / (r + mu)
    return (math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1))
            * (p ** r) * ((1 - p) ** k))


def count_over_under(mu: float, line: float, dispersion: float = 1.0,
                     max_k: int = 60) -> dict:
    """P(over) / P(under) for a count prop at a line.

    Whole-number lines PUSH; this returns p_push separately rather than folding it
    into a side, because silently folding a push into a loss is a real way to
    misstate a record.
    """
    floor = int(math.floor(line))
    is_whole = float(line) == float(floor)
    p_at_or_under = sum(nb_pmf(k, mu, dispersion) for k in range(0, floor + 1))
    p_push = nb_pmf(floor, mu, dispersion) if is_whole else 0.0
    p_under = max(0.0, min(1.0, p_at_or_under - p_push))
    p_over = max(0.0, min(1.0, 1.0 - p_at_or_under))
    return {"p_over": p_over, "p_under": p_under, "p_push": p_push,
            "lean": "OVER" if p_over >= p_under else "UNDER"}


# ── Normal helpers ───────────────────────────────────────────────────────────
# Four hand-rolled copies of these existed across mlb/ and nfl/, and they did
# NOT all compute the same thing: three were survival functions written as
# erfc(+z/root2) and one was a CDF written as erfc(-x/root2). Both forms are
# correct in their own context and they are trivially easy to confuse, because
# the only visible difference is a minus sign inside an erfc call.
#
# Naming them separately is the point. normal_sf answers "how likely is MORE
# than this", normal_cdf answers "how likely is LESS than this", and neither can
# be mistaken for the other at a call site.

def normal_sf(x: float, mu: float, sd: float) -> float:
    """P(X > x) for a normal with this mean and standard deviation.

    The survival function — what an OVER is. Returns 0.0/1.0 rather than
    dividing by zero when sd is non-positive, since a degenerate distribution
    still has a well-defined answer either side of its point mass.
    """
    if sd is None or sd <= 0:
        return 1.0 if mu > x else 0.0
    return 0.5 * math.erfc(((x - mu) / sd) / _SQRT2)


def normal_cdf(z: float) -> float:
    """Phi(z) — P(Z < z) for the STANDARD normal.

    Takes an already-standardised value, unlike normal_sf which takes raw
    x/mu/sd. Used where the input is a z-score by construction, such as a
    margin expressed in standard deviations.
    """
    return 0.5 * math.erfc(-z / _SQRT2)
