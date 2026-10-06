"""
Results tracker client (Feature 1) — isolated bot-side helper.

Thin wrapper over the backend's durable Postgres-backed /api/results/* endpoints.
Every call is wrapped so a backend/DB outage returns a safe default and can never
crash the bot or affect any other command. No discord imports.
"""

import os
import logging

import requests

import service_token as _service_token

log = logging.getLogger("baseline-bot.results")


def _svc():
    """Service-token header for every backend call (see service_token.py)."""
    return _service_token.headers()

API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")

LOG_TIMEOUT     = 15
RECORD_TIMEOUT  = 20
# ABOVE the server's own RESOLVE_TIMEOUT_S (240), or the client hangs up first
# and the grade the backend was about to return is thrown away. 95 paired with
# the old 90s server cap; both were smaller than the work resolve_pick asks for,
# which is what stalled every pick on the 2026-10-02 card.
RESOLVE_TIMEOUT = int(os.getenv("RESOLVE_TIMEOUT", "270") or "270")


def log_pick(rec: dict) -> dict:
    """Insert one pick as PENDING (or with a result). Returns the stored row
    (with its id) or {} on failure."""
    try:
        r = requests.post(f"{API_BASE}/api/results/log", json=rec, timeout=LOG_TIMEOUT,
                          headers=_svc())
        r.raise_for_status()
        data = r.json()
        return data.get("pick", {}) if data.get("ok") else {}
    except Exception as exc:  # noqa: BLE001
        log.warning("results log failed for %r: %s", rec.get("player"), exc)
        return {}


def get_record() -> dict:
    """Full log + aggregate record. Returns {} on failure."""
    try:
        r = requests.get(f"{API_BASE}/api/results/record", timeout=RECORD_TIMEOUT,
                         headers=_svc())
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("results record fetch failed: %s", exc)
        return {}


def get_pending() -> list:
    try:
        r = requests.get(f"{API_BASE}/api/results/pending", timeout=RECORD_TIMEOUT,
                         headers=_svc())
        r.raise_for_status()
        return (r.json() or {}).get("pending", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("results pending fetch failed: %s", exc)
        return []


def update_result(pick_id: int, result: str, value: float = None) -> bool:
    try:
        payload = {"id": int(pick_id), "result": result}
        if value is not None:
            payload["value"] = value
        r = requests.post(f"{API_BASE}/api/results/update",
                          json=payload, timeout=LOG_TIMEOUT, headers=_svc())
        r.raise_for_status()
        return bool((r.json() or {}).get("ok"))
    except Exception as exc:  # noqa: BLE001
        log.warning("results update failed id=%s: %s", pick_id, exc)
        return False


def resolve_pick(pick: dict) -> dict:
    """Ask the backend to auto-resolve a pending pick against the completed
    match. Returns {result: W/L/NEEDS REVIEW, ...}."""
    try:
        payload = {
            "player": pick.get("player", ""),
            "opponent": pick.get("opponent", ""),
            "prop_type": pick.get("prop_type", ""),
            "line": pick.get("line"),
            "lean": pick.get("lean", ""),
        }
        r = requests.post(f"{API_BASE}/api/results/resolve", json=payload, timeout=RESOLVE_TIMEOUT,
                          headers=_svc())
        r.raise_for_status()
        return r.json() or {"result": "NEEDS REVIEW"}
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve failed for %r: %s", pick.get("player"), exc)
        return {"result": "NEEDS REVIEW", "reason": "resolve error"}
