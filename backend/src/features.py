"""
Isolated feature logic for the Discord bot's new commands (Features 1, 4-7).

Everything here is read-only and self-contained: each function wraps its work
in try/except and returns an empty/typed result on failure, so a problem in one
feature can never crash the API or affect prop calculations. None of this code
touches prop math, archetypes, or court CPI values — it only READS existing
Sofascore helpers and the COURT_CPR dictionary.
"""

import re
import time
import logging
import unicodedata
from datetime import datetime, timezone

from src.api.sofascore_client import (
    search_players, get_player_stats_by_surface, get_scheduled_events,
)
from src.constants import (
    COURT_CPR, GENERIC_SURFACE_CPR, CPR_NEUTRAL, get_speed_tier,
    resolve_court_name, ST_PACE_PREVIOUS_YEAR,
)

logger = logging.getLogger("baseline.features")

# prop_type (canonical) -> per-match field name in all_matches records
PROP_FIELD = {
    "Aces":             "aces",
    "Double Faults":    "double_faults",
    "Break Points Won": "bp_converted_count",
    "Total Games":      "total_match_games",
}

FRESH_AMBER_DAYS = 21
FRESH_RED_DAYS   = 45
RED_CONF_PENALTY = 15


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def resolve_player(name: str, tour: str = "") -> dict:
    """Best-effort name -> {id, tour, name}. Searches the given tour, or both."""
    if not name:
        return {}
    tours = (tour.upper(),) if tour else ("ATP", "WTA")
    nnorm = _norm(name)
    last = nnorm.split()[-1] if nnorm.split() else nnorm
    best, best_score = None, 0.0
    for t in tours:
        try:
            res = search_players(last if len(last) >= 3 else nnorm, t) or []
        except Exception:  # noqa: BLE001
            res = []
        for p in res:
            cn = _norm(p.get("name", ""))
            score = 0.75 * _ratio(nnorm, cn) + 0.25 * _ratio(last, cn.split()[-1] if cn.split() else cn)
            if score > best_score:
                best_score, best = score, {**p, "tour": t}
    if best and best_score >= 0.80:
        return {"id": str(best["id"]), "tour": best.get("tour", "ATP"), "name": best.get("name", "")}
    return {}


def _val(m: dict, field: str):
    v = m.get(field)
    return float(v) if isinstance(v, (int, float)) else None


# ── Feature 3 — freshness (days since last match) ───────────────────────────
def freshness_from_matches(all_matches: list) -> dict:
    """Return {days_since_last, level, message, confidence_penalty}. level is one
    of '', 'amber', 'red'. Never blocks — purely advisory."""
    if not all_matches:
        return {"days_since_last": None, "level": "", "message": "", "confidence_penalty": 0}
    ts = max((m.get("timestamp", 0) or 0) for m in all_matches)
    if not ts:
        return {"days_since_last": None, "level": "", "message": "", "confidence_penalty": 0}
    days = int((time.time() - ts) // 86400)
    if days > FRESH_RED_DAYS:
        return {"days_since_last": days, "level": "red",
                "message": (f"Player may be inactive or injured — last match was {days} days ago. "
                            "Data may not reflect current form."),
                "confidence_penalty": RED_CONF_PENALTY}
    if days > FRESH_AMBER_DAYS:
        return {"days_since_last": days, "level": "amber",
                "message": (f"Player may be inactive or injured — last match was {days} days ago. "
                            "Data may not reflect current form."),
                "confidence_penalty": 0}
    return {"days_since_last": days, "level": "", "message": "", "confidence_penalty": 0}


# ── Feature 5 — player form ─────────────────────────────────────────────────
def get_form(player_id: str, tour: str = "ATP") -> dict:
    """Last-15 form: current streak, last-10 results, and a last-5-vs-prev-5
    trend for aces / break points won / double faults."""
    try:
        data = get_player_stats_by_surface(player_id, tour) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_form stats failed pid=%s: %s", player_id, exc)
        return {}
    matches = (data.get("all_matches") or [])[:15]
    if not matches:
        return {}

    # Current streak from the most recent match backward.
    streak_type, streak_len = None, 0
    for m in matches:
        won = bool(m.get("won"))
        t = "W" if won else "L"
        if streak_type is None:
            streak_type, streak_len = t, 1
        elif t == streak_type:
            streak_len += 1
        else:
            break

    last10 = [{
        "won": bool(m.get("won")),
        "opponent": m.get("opponent_name", ""),
        "surface": m.get("surface", ""),
        "date": m.get("date", ""),
    } for m in matches[:10]]

    def _avg_field(subset, field):
        vals = [_val(m, field) for m in subset]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    recent5, prev5 = matches[:5], matches[5:10]
    trend = {}
    for label, field in (("aces", "aces"), ("break_points_won", "bp_converted_count"),
                         ("double_faults", "double_faults")):
        r, p = _avg_field(recent5, field), _avg_field(prev5, field)
        direction = "flat"
        if r is not None and p is not None:
            direction = "up" if r > p else "down" if r < p else "flat"
        trend[label] = {"recent5": r, "prev5": p, "direction": direction}

    alert = streak_len >= 5
    return {
        "streak_type": streak_type, "streak_len": streak_len,
        "form_alert": alert,
        "last10": last10,
        "trend": trend,
        "match_count": len(matches),
        "freshness": freshness_from_matches(data.get("all_matches") or []),
    }


def streak_only(player_id: str, tour: str = "ATP") -> dict:
    """Lightweight {streak_type, streak_len} for the midnight board scan."""
    f = get_form(player_id, tour)
    return {"streak_type": f.get("streak_type"), "streak_len": f.get("streak_len", 0)}


# ── Feature 6 — historical prop lookup ──────────────────────────────────────
def get_history(player_id: str, tour: str, prop_type: str, surface: str, line: float) -> dict:
    """Over/under counts vs ``line`` for ``prop_type`` across the player's last
    20 matches on ``surface``, plus average and the last 10 individual results."""
    field = PROP_FIELD.get(prop_type)
    if not field:
        return {"error": f"unsupported prop_type {prop_type!r}"}
    try:
        data = get_player_stats_by_surface(player_id, tour) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_history stats failed pid=%s: %s", player_id, exc)
        return {}
    surf = (surface or "").title()
    pool = [m for m in (data.get("all_matches") or [])
            if (not surf or m.get("surface") == surf) and _val(m, field) is not None]
    pool = pool[:20]                      # most recent 20 on surface with the stat
    if not pool:
        return {"player_matches": 0, "over": 0, "under": 0, "average": None,
                "hit_rate": None, "line": line, "surface": surf,
                "prop_type": prop_type, "last10": []}

    vals = [_val(m, field) for m in pool]
    over = sum(1 for v in vals if v > line)
    under = sum(1 for v in vals if v < line)
    push = sum(1 for v in vals if v == line)
    avg = round(sum(vals) / len(vals), 1)
    hit_rate = round(over / len(pool) * 100, 0)

    last10 = [{
        "date": m.get("date", ""),
        "opponent": m.get("opponent_name", ""),
        "value": _val(m, field),
        "over": _val(m, field) > line,
    } for m in pool[:10]]

    return {
        "player_matches": len(pool), "over": over, "under": under, "push": push,
        "average": avg, "hit_rate": hit_rate, "line": line, "surface": surf,
        "prop_type": prop_type, "last10": last10,
    }


# ── Feature 1 — results auto-resolution ─────────────────────────────────────
def resolve_pick(player: str, opponent: str, prop_type: str,
                 line: float, lean: str) -> dict:
    """Find the player's most recent COMPLETED match vs ``opponent`` and decide
    whether ``lean`` over/under ``line`` for ``prop_type`` was correct.
    Returns {result: W/L/NEEDS REVIEW, value, opponent_matched, date}."""
    field = PROP_FIELD.get(prop_type)
    if not field:
        return {"result": "NEEDS REVIEW", "reason": "unsupported prop"}
    p = resolve_player(player)
    if not p:
        return {"result": "NEEDS REVIEW", "reason": "player not resolved"}
    try:
        data = get_player_stats_by_surface(p["id"], p["tour"]) or {}
    except Exception as exc:  # noqa: BLE001
        return {"result": "NEEDS REVIEW", "reason": f"stats error: {exc}"}

    opp_norm = _norm(opponent)
    now = time.time()
    best = None
    for m in (data.get("all_matches") or []):     # most recent first
        if not m.get("won") and not m.get("score"):
            continue
        # Match must be finished and recent (last ~3 days) and vs the right opp.
        ts = m.get("timestamp", 0) or 0
        if ts and (now - ts) > 3 * 86400:
            break                                  # too old; list is newest-first
        on = _norm(m.get("opponent_name", ""))
        if on and (opp_norm == on or opp_norm.split()[-1:] == on.split()[-1:]
                   or _ratio(opp_norm, on) >= 0.8):
            best = m
            break
    if best is None:
        return {"result": "NEEDS REVIEW", "reason": "completed match not found"}

    value = _val(best, field)
    if value is None:
        return {"result": "NEEDS REVIEW", "reason": "stat unavailable",
                "opponent_matched": best.get("opponent_name"), "date": best.get("date")}

    ln = (lean or "").upper()
    if ln == "OVER":
        won = value > line
    elif ln == "UNDER":
        won = value < line
    else:
        return {"result": "NEEDS REVIEW", "reason": "no lean", "value": value}
    return {"result": "W" if won else "L", "value": value,
            "line": line, "lean": ln,
            "opponent_matched": best.get("opponent_name"), "date": best.get("date")}


# ── Feature 4 — slate ───────────────────────────────────────────────────────
def _court_cpi(tournament: str, surface: str, tour: str):
    key = resolve_court_name(tournament, tour)
    cpi = COURT_CPR.get(key)
    if cpi is None:
        cpi = GENERIC_SURFACE_CPR.get((surface or "").title(), CPR_NEUTRAL)
    try:
        tier = get_speed_tier(cpi)
    except Exception:  # noqa: BLE001
        tier = ""
    return cpi, tier


def get_slate(date_str: str = "") -> dict:
    """All ATP/WTA singles scheduled for the day, grouped by tour with CPI."""
    try:
        events = get_scheduled_events(date_str) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_slate failed: %s", exc)
        return {"available": False, "atp": [], "wta": []}
    out = {"available": True, "atp": [], "wta": [], "count": 0}
    for e in events:
        cpi, tier = _court_cpi(e.get("tournament", ""), e.get("surface", ""), e.get("tour", ""))
        row = {
            "p1": e.get("p1_name", ""), "p2": e.get("p2_name", ""),
            "tournament": e.get("tournament", ""), "surface": e.get("surface", ""),
            "cpi": cpi, "speed_tier": tier,
            "start_timestamp": e.get("start_timestamp", 0),
            "tour": e.get("tour", ""),
        }
        (out["atp"] if e.get("tour") == "ATP" else out["wta"]).append(row)
    out["count"] = len(out["atp"]) + len(out["wta"])
    return out


# ── Feature 7 — court report ────────────────────────────────────────────────
def _speed_category(cpi: float) -> str:
    try:
        return get_speed_tier(cpi)
    except Exception:  # noqa: BLE001
        return "Average"


def get_court_report(tournament: str, tour: str = "ATP") -> dict:
    """Pre-tournament conditions summary built from COURT_CPR + YoY data +
    scheduled entrants. Never raises."""
    try:
        key = resolve_court_name(tournament, tour)
        cpi = COURT_CPR.get(key)
        # Infer surface from COURTS_BY_SURFACE membership when possible.
        from src.constants import COURTS_BY_SURFACE
        surface = ""
        for surf, courts in COURTS_BY_SURFACE.items():
            if key in courts:
                surface = surf
                break
        if cpi is None:
            cpi = GENERIC_SURFACE_CPR.get(surface or "Hard", CPR_NEUTRAL)
        tier = _speed_category(cpi)
        prev = ST_PACE_PREVIOUS_YEAR.get(key)
        yoy = None
        if isinstance(prev, (int, float)):
            delta = round(cpi - prev, 1)
            yoy = {"previous": prev, "current": cpi, "delta": delta,
                   "direction": "faster" if delta > 0 else "slower" if delta < 0 else "unchanged"}

        fast = cpi >= 39
        slow = cpi <= 32
        ace_note = ("Fast court — serve dominance amplified; ace lines trend OVER for big servers."
                    if fast else
                    "Slow court — serve advantage suppressed; ace lines trend UNDER."
                    if slow else
                    "Average pace — aces track close to each server's baseline.")
        bp_note = ("Returners get fewer looks; break points are scarcer — BP-won UNDER leans."
                   if fast else
                   "Returner-friendly — more break chances; BP-won OVER leans, more total games."
                   if slow else
                   "Balanced break-point environment.")
        if fast:
            reliable = ["Aces (OVER for big servers)", "Total Games (UNDER — quick holds)"]
        elif slow:
            reliable = ["Break Points Won (OVER)", "Total Games (OVER — long games)",
                        "Aces (UNDER)"]
        else:
            reliable = ["Total Games", "Break Points Won"]

        # Players to watch — entrants from today's slate at this tournament.
        watch = []
        try:
            events = get_scheduled_events() or []
            seen = set()
            tkey = _norm(tournament.split(",")[0])
            for e in events:
                if _norm(e.get("tournament", "").split(",")[0]) != tkey:
                    continue
                for nm in (e.get("p1_name"), e.get("p2_name")):
                    n = _norm(nm)
                    if nm and n not in seen:
                        seen.add(n)
                        watch.append(nm)
            watch = watch[:3]
        except Exception:  # noqa: BLE001
            watch = []

        return {
            "available": True, "tournament": tournament, "court_key": key,
            "cpi": cpi, "speed_tier": tier, "surface": surface,
            "yoy": yoy, "ace_note": ace_note, "bp_note": bp_note,
            "reliable_props": reliable, "players_to_watch": watch,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_court_report failed for %r: %s", tournament, exc)
        return {"available": False, "tournament": tournament}
