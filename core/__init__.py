"""
Baseline — sport-agnostic CORE.

NORTH_STAR.md: "A sport-agnostic CORE handles board scanning, Discord posting,
results tracking, recaps, the calibration ledger, the market-anchor de-vig math,
the scenario-mixture engine, and the confidence->probability mapping. Each SPORT
is a module implementing a common interface."

WHAT IS ACTUALLY HERE, AND WHAT IS NOT
--------------------------------------
Here now:
  - the sport REGISTRY and dispatcher (below)
  - the common interface every sport module must satisfy (interfaces.py)
  - sport-agnostic odds/rate math: de-vig, log5, overdispersed counts (odds.py)

NOT here yet: Discord posting, results tracking, recaps, the calibration ledger,
the confidence->probability mapping. Those all exist today as TENNIS code in
backend/src and discord-bot, and Rule 1 forbids moving them until a cached tennis
slate has been snapshotted to a fixture and can be verified byte-identical across
the move. This package was therefore written FRESH rather than by cutting tennis
apart — nothing in it changes tennis behaviour, because nothing in tennis imports
it yet.

Migration order when the fixture exists: de-vig first (tennis already has an
equivalent, so it is the cleanest byte-identical check), then the scenario-mixture
engine, then posting/recaps last since those are the most entangled with Discord
state.
"""

import logging

log = logging.getLogger("baseline.core")

# ── Sport registry ───────────────────────────────────────────────────────────
# Populated lazily so importing core never drags in a sport's network clients.
_REGISTRY = {}
DEFAULT_SPORT = "tennis"          # tennis-forward, per the North Star


def register(sport: str, module) -> None:
    """Register a sport module. It must satisfy the SportModule interface —
    validated on registration rather than at first call, so a malformed module
    fails loudly at startup instead of silently mid-slate."""
    from .interfaces import validate_sport_module
    missing = validate_sport_module(module)
    if missing:
        raise TypeError(
            f"sport module {sport!r} is missing required interface: "
            f"{', '.join(missing)}")
    _REGISTRY[sport] = module
    log.info("core: registered sport %r", sport)


def get_sport(sport: str = None):
    """Look up a registered sport module. Auto-registers the built-ins on first
    use. Returns None for an unknown sport rather than raising — Rule 2 says one
    sport's absence must never break another's pipeline."""
    sport = (sport or DEFAULT_SPORT).lower()
    if sport not in _REGISTRY:
        _autoregister(sport)
    return _REGISTRY.get(sport)


def _autoregister(sport: str) -> None:
    """Import and register a known sport on demand. Failure is logged, never
    raised: an MLB import error must not take a tennis scan down."""
    if sport == "mlb":
        try:
            import mlb
            register("mlb", mlb)
        except Exception as exc:  # noqa: BLE001
            log.warning("core: mlb module unavailable: %s", exc)
    elif sport == "tennis":
        # No tennis MODULE exists yet — its logic still lives in backend/src and
        # discord-bot and is called directly. Deliberately not faked with a
        # shim: a registry entry that does not actually route tennis would be
        # worse than an honest absence.
        log.info("core: tennis is not yet a registered module — callers still "
                 "invoke backend/src + discord-bot directly")


def available_sports() -> list:
    """Sports with a registered, interface-satisfying module."""
    for s in ("mlb", "tennis"):
        if s not in _REGISTRY:
            _autoregister(s)
    return sorted(_REGISTRY)


# ── Dispatch ─────────────────────────────────────────────────────────────────
def scan_board(sport: str = None, **kw) -> list:
    """Dispatch a board scan to a sport module. [] for an unknown sport or any
    failure inside the module — the error boundary Rule 2 requires."""
    mod = get_sport(sport)
    if mod is None:
        log.warning("core.scan_board: no module for sport %r", sport)
        return []
    try:
        return mod.scan_board(**kw) or []
    except Exception as exc:  # noqa: BLE001
        log.exception("core.scan_board: %r failed: %s", sport, exc)
        return []


def project(sport: str = None, **kw) -> dict:
    """Dispatch a single projection. {} on unknown sport or module failure."""
    mod = get_sport(sport)
    if mod is None:
        return {}
    try:
        return mod.project(**kw) or {}
    except Exception as exc:  # noqa: BLE001
        log.exception("core.project: %r failed: %s", sport, exc)
        return {}


def resolve(sport: str = None, **kw) -> dict:
    """Dispatch a resolve. NEEDS REVIEW on unknown sport or module failure —
    never a fabricated result."""
    mod = get_sport(sport)
    if mod is None:
        return {"result": "NEEDS REVIEW", "reason": f"no module for sport {sport!r}"}
    try:
        return mod.resolve(**kw) or {"result": "NEEDS REVIEW"}
    except Exception as exc:  # noqa: BLE001
        log.exception("core.resolve: %r failed: %s", sport, exc)
        return {"result": "NEEDS REVIEW", "reason": "resolver error"}


def grade(sport: str = None, **kw) -> str:
    """Dispatch a grade. NEEDS REVIEW on unknown sport or module failure."""
    mod = get_sport(sport)
    if mod is None:
        return "NEEDS REVIEW"
    try:
        return mod.grade(**kw)
    except Exception as exc:  # noqa: BLE001
        log.exception("core.grade: %r failed: %s", sport, exc)
        return "NEEDS REVIEW"


__all__ = ["register", "get_sport", "available_sports", "DEFAULT_SPORT",
           "scan_board", "project", "resolve", "grade"]
