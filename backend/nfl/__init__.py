"""
NFL sport module — game props only.

Mirrors mlb/ deliberately: same flag names, same shadow semantics, same
interface, same Rule 2 error boundaries. A new sport should look like the last
one, not like a new design.

SCOPE IS THE REGULAR-SEASON GAME BOARD (`NFL` on PrizePicks), NOT the whole tab.
The tab carries three products and they are different problems:

    NFLP     preseason game props   — starters play one series and depth charts
                                      are fiction. There is no honest projection.
    NFLSZN   season-long totals     — dominated by AVAILABILITY, not performance;
                                      a 1,150-yard rushing line is mostly a bet on
                                      17 healthy games. Needs an injury-survival
                                      model this module does not have.
    NFL      game props             — what this module prices.

Rule 4: NFL_ENABLED defaults FALSE. Nothing here posts until the projections have
been reviewed against real results.
"""

import os

# ── Rule 4: shadow flag, default FALSE ───────────────────────────────────────
NFL_ENABLED = os.getenv("NFL_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on")

# Single source of truth for whether output is labelled shadow. Derived, never
# passed as a literal — hardcoding it is how the MLB boards kept printing SHADOW
# after the flag was flipped.
SHADOW = not NFL_ENABLED

SPORT = "nfl"
