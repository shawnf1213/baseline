"""
NBA sport module — regular-season player props only.

Mirrors nfl/ deliberately, which mirrors mlb/: same flag names, same shadow
semantics, same interface, same Rule 2 error boundaries. A new sport should look
like the last one, not like a new design.

SCOPE IS THE REGULAR-SEASON PLAYER BOARD (`NBA` on PrizePicks), NOT the whole
tab. PrizePicks publishes several basketball leagues on one feed and they are
different products with different correct models:

    NBA      full-game player props   <- the only one this module prices
    NBA1H    first half               — half the sample, different rotations
    NBA1Q    first quarter            — starters only, a different question
    NBASZN   season-long totals       — an availability bet, not a form bet

The filter is an EXACT match on "NBA", for the same reason nfl/lines.py refuses
to prefix-match: a prefix sweeps all four in and prices a season-long points
total with a single-game model, silently.

Rule 4: NBA_ENABLED defaults FALSE. Nothing here posts until the projections
have been reviewed against real results.
"""

import os

# ── Rule 4: shadow flag, default FALSE ───────────────────────────────────────
NBA_ENABLED = os.getenv("NBA_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on")

# Single source of truth for whether output is labelled shadow. Derived, never
# passed as a literal — hardcoding it is how the MLB boards kept printing SHADOW
# after the flag was flipped.
SHADOW = not NBA_ENABLED

SPORT = "nba"
