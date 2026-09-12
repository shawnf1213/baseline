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

# THE single source of truth for whether posted output is labelled as shadow.
# Callers must derive from this rather than passing a literal — the bot and the
# CLI both hardcoded shadow=True, so flipping MLB_ENABLED changed nothing and
# every board still carried a SHADOW banner while claiming to be live.
SHADOW = not MLB_ENABLED

SPORT = "mlb"

# ── The common sport-module interface (NORTH_STAR.md) ────────────────────────
# scan_board / project / resolve / grade re-exported at package level so a future
# core dispatcher can treat this module as a black box: `mlb.scan_board(date)`
# with no knowledge of baseball. Imported lazily inside the functions below so
# importing `mlb` never triggers a network client import as a side effect.

def scan_board(*a, **kw):
    """See mlb.board.scan_board."""
    from .board import scan_board as _f
    return _f(*a, **kw)


def project(*a, **kw):
    """See mlb.board.project."""
    from .board import project as _f
    return _f(*a, **kw)


def resolve(*a, **kw):
    """See mlb.board.resolve."""
    from .board import resolve as _f
    return _f(*a, **kw)


def grade(*a, **kw):
    """See mlb.board.grade."""
    from .board import grade as _f
    return _f(*a, **kw)


__all__ = ["MLB_ENABLED", "MLB_SHADOW_LOG", "SPORT",
           "scan_board", "project", "resolve", "grade"]
