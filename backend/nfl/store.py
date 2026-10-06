"""
NFL pick persistence — a thin client over the backend's durable results API.

STORED THE SAME WAY AS TENNIS, IN A SEPARATE TABLE (operator, 2026-09-10).
Tennis writes picks through the backend's Postgres-backed /api/results/*
endpoints via discord-bot/results_tracker.py; this is the same arrangement
against /api/nfl/results/*, which reads and writes `nfl_picks` in the SAME
database. No second Postgres to provision, and no `sport` column on the tennis
table — one shared table is how a filter gets forgotten and an NFL row lands in
the public tennis record. MLB drew the line in the same place with mlb_picks.

WHY THIS REPLACED A JSON FILE. The first cut wrote a committed file, because
Railway's Postgres is published on postgres.railway.internal and that name only
resolves inside a Railway container — so the deployed bot could reach it and a
laptop could not. Going through the backend's HTTPS API removes the problem
entirely: it is reachable from a container, a laptop, and anything else that can
make a request, and it is the mechanism tennis already trusts.

DEGRADES, NEVER RAISES. With the backend unreachable every function here returns
an empty result and the board still scans and still posts. A missing record must
cost the recap, never the board.
"""

import json as _json
import logging
import os

log = logging.getLogger("baseline.nfl.store")

API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")

LOG_TIMEOUT = 20
READ_TIMEOUT = 25


def _svc_headers() -> dict:
    """X-Service-Token for every backend call — see core/service_token.py."""
    t = (os.getenv("BASELINE_SERVICE_TOKEN") or "").strip()
    return {"X-Service-Token": t} if t else {}


def _post(path: str, payload: dict, timeout: int = LOG_TIMEOUT):
    import requests
    try:
        r = requests.post(f"{API_BASE}{path}", json=payload, timeout=timeout,
                          headers=_svc_headers())
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl store POST %s failed: %s", path, str(exc)[:160])
        return {}


def _get(path: str, params: dict = None, timeout: int = READ_TIMEOUT):
    import requests
    try:
        r = requests.get(f"{API_BASE}{path}", params=params or None,
                         timeout=timeout, headers=_svc_headers())
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl store GET %s failed: %s", path, str(exc)[:160])
        return {}


def available() -> bool:
    """Is the durable store reachable AND connected to its database?"""
    d = _get("/api/nfl/results/record", {"since_days": 1})
    return bool(d.get("ready"))


def log_board(rows: list, book: str, slate_date: str, potd_player=None,
              shadow: bool = True, season=None, week=None) -> int:
    """Persist a posted board. Returns rows written; 0 when unavailable.

    The backend dedupes on (book, slate_date, player, prop_type), so a retry or
    a second post of the same board cannot double-count a play into the record.
    """
    if not rows:
        return 0
    try:
        from . import client as _c
        season = season or _c.current_season()
        week = week or _c.current_week()
        payload = [{
            "book": book, "slate_date": slate_date,
            "player": r.get("player"), "team": r.get("team") or "",
            "opponent": r.get("opponent") or "",
            "prop_type": r.get("prop"), "line": r.get("line"),
            "model_projection": r.get("projection"), "lean": r.get("lean"),
            "confidence": r.get("win_prob"), "edge": r.get("edge"),
            "is_potd": 1 if r.get("player") == potd_player else 0,
            "usage_window": r.get("window") or "",
            "prior_season_only": 1 if r.get("prior_season_only") else 0,
            "shadow": 1 if shadow else 0,
            "season": season, "week": week,
            # THE MODEL'S INPUTS, KEPT. The projector already returns these
            # (carries / carry_share / yards_per_carry, targets / catch_rate)
            # and post.py reads them to render the card — then they were dropped
            # on the floor. Measured 2026-09-27 over 142 graded picks,
            # rush_yards over-projects by 31% of the line while receptions
            # under-projects by 20%; those point in opposite directions, so the
            # question is whether the volume term or the efficiency term is
            # wrong, and only the drivers can answer it. json.dumps rather than a
            # column each: the keys differ per prop and this is diagnostic data,
            # not something queried in a hot path.
            "drivers": _json.dumps(r.get("drivers") or {}, default=str)[:4000],
        } for r in rows if r.get("player")]
        d = _post("/api/nfl/results/log", {"picks": payload})
        n = int(d.get("written") or 0)
        log.info("nfl store: logged %d of %d pick(s) for %s %s",
                 n, len(payload), book, slate_date)
        return n
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl store log_board failed: %s", exc)
        return 0


def pending(book: str = None, slate_date: str = None) -> list:
    rows = _get("/api/nfl/results/pending").get("pending") or []
    if book:
        rows = [r for r in rows if r.get("book") == book]
    if slate_date:
        rows = [r for r in rows if r.get("slate_date") == slate_date]
    return rows


def update_result(pick_id, result: str, value=None) -> bool:
    d = _post("/api/nfl/results/update",
              {"pick_id": pick_id, "result": result, "value": value})
    return bool(d.get("ok"))


def picks_for(slate_date: str = None, book: str = None,
              since_days: int = None) -> list:
    params = {}
    if slate_date:
        params["slate_date"] = slate_date
    if book:
        params["book"] = book
    if since_days:
        params["since_days"] = since_days
    return _get("/api/nfl/results/record", params).get("picks") or []


def summary() -> dict:
    d = _get("/api/nfl/results/record")
    picks = d.get("picks") or []
    out = {"available": bool(d.get("ready")), "api": API_BASE,
           "total": len(picks)}
    for res in ("PENDING", "W", "L", "PUSH", "VOID"):
        out[res] = sum(1 for p in picks if (p.get("result") or "PENDING") == res)
    return out
