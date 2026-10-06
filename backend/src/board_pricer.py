"""Prices the live tennis board on the backend, on a schedule, one row at a
time — so phones read priced rows instead of pricing them (operator ruling,
2026-10-06: per-user client pricing multiplied proxy calls and recreated the
burst pattern that exhausted the proxies).

WHAT IT DOES, per pass (every BOARD_PRICER_INTERVAL_S):
  1. read the live PrizePicks and Underdog tennis rows (src/live_board,
     already cached) and order them soonest-start first;
  2. for each row not yet priced, priced too long ago, or whose line moved:
       name -> Sofascore id        (search_players, cached in-process)
       id   -> scheduled opponent  (get_player_next_match, cached)
       the SAME projection call /api/prop/calculate answers, so the number
       the board shows and the number Project shows cannot differ, and the
       backend's own projection cache is shared;
  3. sleep BOARD_PRICER_GAP_S between rows. Strictly sequential: this is the
     throttled path the bot already uses, taken one step at a time.

NOTHING ABOUT THE MODEL CHANGES HERE. This module decides WHEN a row is
priced, never HOW; it calls the existing endpoint function and stores what
comes back. The priced table is persisted to the durable cache so a redeploy
does not empty the board.
"""
import asyncio
import logging
import os
import time
import unicodedata

logger = logging.getLogger("baseline.board_pricer")

ENABLED = (os.getenv("BOARD_PRICER_ENABLED", "1") or "1").lower() not in ("0", "false", "no", "off")
INTERVAL_S = int(os.getenv("BOARD_PRICER_INTERVAL_S", "600") or 600)
GAP_S = float(os.getenv("BOARD_PRICER_GAP_S", "2") or 2)
PRICE_TTL_S = int(os.getenv("BOARD_PRICER_TTL_S", "7200") or 7200)       # re-price after
FAIL_RETRY_S = int(os.getenv("BOARD_PRICER_FAIL_RETRY_S", "3600") or 3600)
MAX_PER_PASS = int(os.getenv("BOARD_PRICER_MAX_PER_PASS", "400") or 400)
START_DELAY_S = 25
PERSIST_KEY = "board_pricer:priced:v1"
PERSIST_TTL_S = 12 * 3600

_priced: dict = {}      # key -> {projection, confidence, lean, edge, player_id, opponent_id,
                        #         surface, tour, line, priced_at, status: priced|failed}
_players: dict = {}     # norm name -> (at, {id, tour, rank} | None)
_ctx: dict = {}         # player id -> (at, {opponent_id, surface, tournament} | None)
_status = {"enabled": ENABLED, "running": False, "last_pass_started": None,
           "last_pass_finished": None, "last_pass_rows": 0, "last_pass_priced": 0,
           "last_pass_failed": 0, "priced_total": 0}
_lock = asyncio.Lock()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()


def key_of(row: dict) -> str:
    return f"{_norm(row.get('player'))}|{row.get('prop_type')}|{row.get('line')}"


# ── persistence ──────────────────────────────────────────────────────────────
def restore() -> int:
    try:
        from . import database
        saved = database.cache_get(PERSIST_KEY)
        if isinstance(saved, dict):
            now = time.time()
            for k, v in saved.items():
                if isinstance(v, dict) and (now - float(v.get("priced_at") or 0)) < PERSIST_TTL_S:
                    _priced[k] = v
        _status["priced_total"] = sum(1 for v in _priced.values() if v.get("status") == "priced")
        return len(_priced)
    except Exception as exc:  # noqa: BLE001
        logger.warning("board pricer restore failed: %s", exc)
        return 0


def persist() -> None:
    try:
        from . import database
        database.cache_set(PERSIST_KEY, _priced, ttl_seconds=PERSIST_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("board pricer persist failed: %s", exc)


# ── lookups (cached, sequential) ─────────────────────────────────────────────
async def _resolve(name: str, tour_hint: str, tours=None) -> dict | None:
    """name -> {id, tour, rank}. Exact normalised match preferred; the hinted
    tour searched first; a WTA name never locks onto a fuzzy ATP match."""
    nk = _norm(name)
    hit = _players.get(nk)
    if hit and (time.time() - hit[0]) < 7 * 24 * 3600:
        return hit[1]
    from .api.sofascore_client import search_players
    loop = asyncio.get_event_loop()
    order = list(tours) if tours else (["WTA", "ATP"] if tour_hint == "WTA" else ["ATP", "WTA"])
    found = None
    all_rows: list = []
    parts = nk.split()
    queries = [name.strip()]
    if parts and len(parts[-1]) >= 3 and parts[-1] != name.strip().lower():
        queries.append(parts[-1])
    for q in queries:
        for t in order:
            try:
                res = await loop.run_in_executor(None, search_players, q, t, False)
            except Exception:  # noqa: BLE001
                res = []
            if isinstance(res, list):
                all_rows.extend({**r, "_tour": t} for r in res if isinstance(r, dict))
            if any(_norm(r.get("name")) == nk for r in all_rows):
                break
        if any(_norm(r.get("name")) == nk for r in all_rows):
            break
    m = next((r for r in all_rows if _norm(r.get("name")) == nk), None) or (all_rows[0] if all_rows else None)
    if m and m.get("id"):
        g = (m.get("gender") or "").upper()
        tour = "WTA" if g == "F" else "ATP" if g == "M" else m.get("_tour") or tour_hint or "ATP"
        found = {"id": str(m["id"]), "tour": tour, "rank": m.get("currentRank")}
    _players[nk] = (time.time(), found)
    return found


async def _context(pid: str, tour: str) -> dict | None:
    hit = _ctx.get(pid)
    if hit and (time.time() - hit[0]) < 2 * 3600:
        return hit[1]
    from .api.sofascore_client import get_player_next_match
    loop = asyncio.get_event_loop()
    ctx = None
    try:
        nm = await loop.run_in_executor(None, get_player_next_match, pid, tour)
        if isinstance(nm, dict) and nm.get("opponent_id"):
            ctx = {"opponent_id": str(nm["opponent_id"]), "surface": nm.get("surface") or "",
                   "tournament": nm.get("tournament") or ""}
    except Exception:  # noqa: BLE001
        ctx = None
    _ctx[pid] = (time.time(), ctx)
    return ctx


# ── one row ──────────────────────────────────────────────────────────────────
async def price_row(row: dict) -> dict:
    """Price one board row through the existing projection endpoint function.
    Returns the record stored in _priced (status priced|failed)."""
    key = key_of(row)
    rec = {"line": row.get("line"), "priced_at": time.time(), "status": "failed"}
    try:
        p = await _resolve(row.get("player", ""), row.get("tour") or "")
        if not p:
            rec["reason"] = "player not found"
            _priced[key] = rec
            return rec
        ctx = await _context(p["id"], p["tour"])
        opponent_id = ctx.get("opponent_id") if ctx else None
        surface = (ctx or {}).get("surface") or row.get("surface") or "Hard"
        court = row.get("tournament") or (ctx or {}).get("tournament") or ""
        if not opponent_id:
            o = await _resolve(row.get("opponent", ""), p["tour"], tours=[p["tour"]])
            opponent_id = o["id"] if o else None
        if not opponent_id:
            rec["reason"] = "opponent not found"
            _priced[key] = rec
            return rec
        # The endpoint function itself — same cache, same model, same answer.
        from main import prop_calculate, PropRequest
        req = PropRequest(player_id=p["id"], opponent_id=str(opponent_id),
                          player_name=row.get("player", ""), opponent_name=row.get("opponent", ""),
                          tour=p["tour"], surface=surface, court=court,
                          qualifying=("qualif" in court.lower()),
                          prop_type=row.get("prop_type", ""), prop_line=float(row.get("line") or 0))
        data = await prop_calculate(req)
        if not isinstance(data, dict) or data.get("data_unavailable"):
            rec["reason"] = "data unavailable"
            rec["transient"] = True
            _priced[key] = rec
            return rec
        proj = data.get("model_projection")
        if not isinstance(proj, (int, float)):
            rec["reason"] = "no projection"
            _priced[key] = rec
            return rec
        line = float(row.get("line") or 0)
        edge = round(float(proj) - line, 1)
        lean = str(data.get("lean") or "").upper()
        if lean not in ("OVER", "UNDER"):
            lean = "OVER" if edge > 0 else "UNDER" if edge < 0 else ""
        rec.update({
            "status": "priced",
            "projection": round(float(proj), 2),
            "confidence": data.get("confidence") if isinstance(data.get("confidence"), (int, float)) else None,
            "lean": lean, "edge": edge,
            "player_id": p["id"], "opponent_id": str(opponent_id),
            "surface": surface, "tour": p["tour"], "court": court,
        })
        rec.pop("reason", None)
        _priced[key] = rec
        return rec
    except Exception as exc:  # noqa: BLE001 — one row must never stop the pass
        rec["reason"] = str(exc)[:120]
        rec["transient"] = True
        _priced[key] = rec
        return rec


def _due(row: dict, now: float) -> bool:
    rec = _priced.get(key_of(row))
    if not rec:
        return True
    age = now - float(rec.get("priced_at") or 0)
    if rec.get("status") == "priced":
        return age > PRICE_TTL_S
    return age > (600 if rec.get("transient") else FAIL_RETRY_S)


# ── a pass over both books ───────────────────────────────────────────────────
async def run_pass() -> dict:
    if _lock.locked():
        return {"skipped": "pass already running"}
    async with _lock:
        from . import live_board
        loop = asyncio.get_event_loop()
        _status["running"] = True
        _status["last_pass_started"] = time.time()
        rows: list = []
        seen = set()
        for book in ("prizepicks", "underdog"):
            try:
                b = await loop.run_in_executor(None, live_board.live_board, book)
            except Exception as exc:  # noqa: BLE001
                logger.warning("board pricer: %s board unavailable: %s", book, exc)
                continue
            for r in b.get("rows") or []:
                k = key_of(r)
                if k in seen:
                    continue
                seen.add(k)
                rows.append(r)
        now = time.time()
        # Soonest first; a match already under way keeps whatever price it has.
        rows.sort(key=lambda r: (r.get("start_timestamp") or 1e12))
        todo = [r for r in rows if _due(r, now)
                and not ((r.get("start_timestamp") or 0) and (r["start_timestamp"] + 600) < now)]
        todo = todo[:MAX_PER_PASS]
        _status["last_pass_rows"] = len(rows)
        priced = failed = 0
        logger.info("board pricer: pass start — %d rows on the board, %d to price", len(rows), len(todo))
        for i, r in enumerate(todo):
            rec = await price_row(r)
            if rec.get("status") == "priced":
                priced += 1
            else:
                failed += 1
            if i % 20 == 19:
                persist()
            await asyncio.sleep(GAP_S)
        persist()
        _status.update({"running": False, "last_pass_finished": time.time(),
                        "last_pass_priced": priced, "last_pass_failed": failed,
                        "priced_total": sum(1 for v in _priced.values() if v.get("status") == "priced")})
        logger.info("board pricer: pass done — priced %d, failed %d, total priced %d",
                    priced, failed, _status["priced_total"])
        return dict(_status)


async def loop_forever() -> None:
    await asyncio.sleep(START_DELAY_S)
    restored = restore()
    logger.info("board pricer: started (interval %ss, gap %ss, restored %d)", INTERVAL_S, GAP_S, restored)
    while True:
        try:
            await run_pass()
        except Exception as exc:  # noqa: BLE001
            logger.exception("board pricer: pass crashed: %s", exc)
            _status["running"] = False
        await asyncio.sleep(INTERVAL_S)


def start() -> None:
    if not ENABLED:
        logger.info("board pricer: disabled (BOARD_PRICER_ENABLED=0)")
        return
    try:
        asyncio.get_event_loop().create_task(loop_forever())
    except Exception as exc:  # noqa: BLE001
        logger.warning("board pricer: could not start: %s", exc)


# ── what the app reads ───────────────────────────────────────────────────────
def merge(rows: list) -> list:
    """Attach the stored price to each live row. `pricing` is one of
    priced | pending | failed | started (match under way, never priced)."""
    now = time.time()
    for r in rows:
        rec = _priced.get(key_of(r))
        started = bool(r.get("start_timestamp")) and (r["start_timestamp"] + 600) < now
        if rec and rec.get("status") == "priced":
            r.update({"projection": rec.get("projection"), "confidence": rec.get("confidence"),
                      "lean": rec.get("lean"), "edge": rec.get("edge"),
                      "player_id": rec.get("player_id"), "opponent_id": rec.get("opponent_id"),
                      "surface_used": rec.get("surface"), "tour": r.get("tour") or rec.get("tour"),
                      "priced_at": rec.get("priced_at"), "pricing": "priced"})
        else:
            r.update({"projection": None, "confidence": None, "lean": "", "edge": None,
                      "player_id": None, "opponent_id": None, "surface_used": None, "priced_at": None,
                      "pricing": "failed" if (rec and not rec.get("transient")) else ("started" if started else "pending")})
    return rows


def status() -> dict:
    return dict(_status)
