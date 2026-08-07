"""
The contract every sport module must satisfy.

Deliberately a duck-typed check rather than an ABC: sport modules are Python
PACKAGES (`mlb`, and eventually `tennis`), not classes, so there is nothing to
subclass. validate_sport_module() is called at registration so a malformed module
fails at startup rather than silently mid-slate.
"""

REQUIRED = ("scan_board", "project", "resolve", "grade")

# Not required, but a module that defines them gets richer core behaviour later
# (calibration bucketing, per-sport board caps). Absence is fine.
OPTIONAL = ("SPORT", "SUPPORTED_PROPS", "MLB_ENABLED")


def validate_sport_module(module) -> list:
    """Return the names of REQUIRED members the module is missing. Empty list
    means it satisfies the interface."""
    return [name for name in REQUIRED
            if not callable(getattr(module, name, None))]


def describe(module) -> dict:
    """What a sport module offers — used for diagnostics and by any future
    /sports endpoint. Never raises on a partial module."""
    return {
        "sport": getattr(module, "SPORT", None),
        "satisfies_interface": not validate_sport_module(module),
        "missing": validate_sport_module(module),
        "supported_props": tuple(getattr(module, "SUPPORTED_PROPS", ()) or ()),
        "enabled": bool(getattr(module, "MLB_ENABLED", False))
                   if getattr(module, "SPORT", None) == "mlb" else None,
    }
