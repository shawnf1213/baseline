"""
Baseline — MLB sport module.

One of the per-sport modules described in NORTH_STAR.md. The sport-agnostic core
owns board scanning, Discord posting, results tracking, recaps, the calibration
ledger, the market-anchor de-vig math and the scenario-mixture MACHINERY; this
module owns MLB's scenario DEFINITIONS and its data access.

STATUS: shadow only. Per North Star rule 4 every new prop ships behind an ENABLED
flag defaulting to FALSE — computing and logging, never posting — until reviewed
against actuals. MLB_ENABLED is that flag and it is false. Nothing here posts to
Discord and nothing here writes to the picks/results tables.

Rule 3 (records never pool across sports) is not yet satisfiable: the picks schema
has no `sport` column. That migration is a hard gate before MLB writes its first
row. Until it lands this module is read-and-project only, which keeps the rule
intact by writing nothing at all.

Rule 2 (hard sport isolation) is why every public entry point here is wrapped: an
exception in MLB must be caught and logged and can never reach the tennis
pipeline.
"""

import os

# ── Rule 4: shadow flag, default FALSE ───────────────────────────────────────
MLB_ENABLED = os.getenv("MLB_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on")

# Shadow logging is independent of MLB_ENABLED: we WANT projections computed and
# recorded while the prop is unposted, because that shadow output vs actuals is
# exactly what the review in rule 4 is based on.
MLB_SHADOW_LOG = os.getenv("MLB_SHADOW_LOG", "true").strip().lower() in (
    "1", "true", "yes", "on")

SPORT = "mlb"

__all__ = ["MLB_ENABLED", "MLB_SHADOW_LOG", "SPORT"]
