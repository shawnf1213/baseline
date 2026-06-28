"""
Baseline Tennis Analytics - FastAPI backend.

Wraps all existing Python calculation logic in REST endpoints.
No logic is rewritten here - only imported and exposed via HTTP.

Endpoints:
  POST /api/search          - player search
  POST /api/player/stats    - surface stats + archetype
  POST /api/prop/calculate  - full prop projection
  POST /api/h2h             - head-to-head record + stats
"""

import asyncio
import sys
import time
import types
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock streamlit BEFORE any src imports.
# sofascore_client uses st.session_state as an in-memory cache dict.
# We provide a plain dict subclass that supports both [] and .get() access.
# ---------------------------------------------------------------------------
class _SessionState(dict):
    def __getattr__(self, key):
        return self.get(key)
    def __setattr__(self, key, value):
        self[key] = value

_st_mock = types.ModuleType("streamlit")
_st_mock.session_state = _SessionState()
_st_mock.spinner = lambda *a, **kw: __import__("contextlib").nullcontext()
sys.modules["streamlit"] = _st_mock
sys.modules["streamlit.runtime"] = types.ModuleType("streamlit.runtime")

# ---------------------------------------------------------------------------
# Normal imports (after mock)
# ---------------------------------------------------------------------------
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from src.api.sofascore_client import (
    init_session,
    search_players,
    get_player_stats_by_surface,
    get_player_surface_hold,
    peek_surface_hold,
    get_player_next_match,
    get_h2h_summary,
    get_h2h_stat_avg,
    SofascoreBlockedError,
)
from src.api.tennis_abstract import get_player_ta_stats, build_props_ta_view
from src.api.sackmann import (
    load_player_sackmann_data,
    aggregate_sackmann_stats,
    build_sackmann_chart_log,
    normalize_name_for_sackmann,
)
from src.unified_data import (
    normalize_sofascore_match,
    normalize_sackmann_match,
    merge_and_deduplicate,
    aggregate_unified_stats,
    build_unified_chart_log,
)
from src.calculations.archetypes import classify_archetype
from src.calculations.blended_stats import get_blended_stats
from src.calculations.confidence import calculate_confidence
from src.calculations.props import (
    project_aces,
    project_double_faults,
    project_total_games,
    project_break_points,
    project_player_games_won,
    generate_scouting_report,
    detect_environment,
    ENVIRONMENT_LABELS,
    GRAND_SLAMS,
)
from src.constants import (
    COURT_CPR, CPR_NEUTRAL, GENERIC_SURFACE_CPR,
    get_speed_tier, ST_PACE_PREVIOUS_YEAR, ST_YOY_THRESHOLD,
    resolve_court_name,
)
from src.api.string_tension import lookup_pace_index

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Baseline Tennis API", version="1.0.0")

_ALLOWED_ORIGINS = [
    "https://baseline-app-three.vercel.app",
    "http://localhost:5173",
    "http://localhost:5174",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    logger.info("Initialising Sofascore client / proxy session...")
    init_session()
    # Results tracker DB (Feature 1). Isolated: failure only disables the tracker.
    try:
        from src import database
        database.init_db()
    except Exception:  # noqa: BLE001
        logger.exception("Results DB init failed — tracker disabled")

    # Pre-warm today's slate cache in the background so /slate (Feature 4) hits a
    # warm cache instead of a slow cold full-day fetch right after each redeploy.
    def _warm_slate():
        import time as _t
        _t.sleep(8)
        try:
            from src import features
            n = features.get_slate("").get("count", 0)
            logger.info("slate pre-warm complete (%s matches)", n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("slate pre-warm failed: %s", exc)
    try:
        import threading
        threading.Thread(target=_warm_slate, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

    logger.info("Backend ready.")


# ════════════════════════════════════════════════════════════════════════════
# Feature 1 — Pick of the Day results tracker (durable Postgres-backed record)
# ════════════════════════════════════════════════════════════════════════════
class ResultLogRequest(BaseModel):
    player: str
    opponent: str = ""
    prop_type: str
    line: float | None = None
    model_projection: float | None = None
    lean: str = ""                      # OVER / UNDER
    confidence: float | None = None
    result: str = "PENDING"             # W / L / PENDING / NEEDS REVIEW
    original_line: float | None = None
    tournament: str = ""
    surface: str = ""


class ResultUpdateRequest(BaseModel):
    id: int
    result: str                          # W / L / PENDING / NEEDS REVIEW


@app.post("/api/results/log")
async def results_log(req: ResultLogRequest):
    """Insert one pick record into the durable results log."""
    from src import database
    rec = req.dict()
    if rec.get("original_line") is None:
        rec["original_line"] = rec.get("line")
    stored = database.log_pick(rec)
    if not stored:
        return {"ok": False, "error": "results DB unavailable"}
    return {"ok": True, "pick": stored}


@app.get("/api/results/health")
async def results_health():
    """Diagnostic: is the results DB connected? (does not expose the URL)."""
    from src import database
    return {"ready": database.is_ready(),
            "database_url_present": bool(database.DATABASE_URL)}


@app.get("/api/results/record")
async def results_record():
    """Full pick log plus aggregate record (W/L, win rate, avg confidence on
    winners vs losers, current streak). Public — used as a sales record."""
    from src import database
    return database.record_summary()


@app.get("/api/results/pending")
async def results_pending():
    """Picks still awaiting a result — used by the auto-resolution job."""
    from src import database
    return {"pending": database.pending_picks()}


@app.post("/api/results/update")
async def results_update(req: ResultUpdateRequest):
    """Set a pick's result (manual admin override or the auto-resolver)."""
    from src import database
    ok = database.update_result(req.id, req.result)
    return {"ok": ok}


@app.delete("/api/results/{pick_id}")
async def results_delete(pick_id: int):
    """Delete a pick row (admin cleanup)."""
    from src import database
    return {"ok": database.delete_pick(pick_id)}


class ResolveRequest(BaseModel):
    player: str
    opponent: str = ""
    prop_type: str
    line: float
    lean: str


@app.post("/api/results/resolve")
async def results_resolve(req: ResolveRequest):
    """Auto-resolve a pending pick from Sofascore's completed-match stats.
    Returns {result: W/L/NEEDS REVIEW, value, ...}. Used by the 11pm job."""
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(
            None, features.resolve_pick, req.player, req.opponent,
            req.prop_type, req.line, req.lean), timeout=90.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve endpoint error: %s", exc)
        return {"result": "NEEDS REVIEW", "reason": "resolver error"}


# ════════════════════════════════════════════════════════════════════════════
# Features 4-7 — slate, form, history, court report (read-only, isolated)
# ════════════════════════════════════════════════════════════════════════════
@app.get("/api/slate/today")
async def slate_today():
    """Feature 4 — today's ATP/WTA singles with tournament, surface, CPI."""
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, features.get_slate, ""), timeout=70.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slate endpoint error: %s", exc)
        return {"available": False, "atp": [], "wta": [], "count": 0}


@app.get("/api/player/form")
async def player_form(player_id: str = "", tour: str = "ATP"):
    """Feature 5 — last-15 form, streak, last-10 results, last5-vs-prev5 trend."""
    if not player_id:
        return {}
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, features.get_form, player_id, tour), timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("form endpoint error: %s", exc)
        return {}


@app.get("/api/player/streak")
async def player_streak(player_id: str = "", tour: str = "ATP"):
    """Feature 5 — lightweight streak only (for the midnight board scan)."""
    if not player_id:
        return {}
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, features.streak_only, player_id, tour), timeout=60.0)
    except Exception:  # noqa: BLE001
        return {}


@app.get("/api/history")
async def history(player_id: str = "", tour: str = "ATP", prop: str = "",
                  surface: str = "", line: float = 0.0):
    """Feature 6 — over/under counts vs line for a prop on a surface."""
    if not player_id or not prop:
        return {}
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(
            None, features.get_history, player_id, tour, prop, surface, line), timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("history endpoint error: %s", exc)
        return {}


@app.get("/api/courtreport")
async def courtreport(tournament: str = "", tour: str = "ATP", surface: str = ""):
    """Feature 7 — pre-tournament conditions summary for prop betting."""
    if not tournament:
        return {"available": False}
    from src import features
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(
            None, features.get_court_report, tournament, tour, surface), timeout=70.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("courtreport endpoint error: %s", exc)
        return {"available": False, "tournament": tournament}


@app.get("/")
async def root():
    return {"status": "ok", "service": "Baseline Tennis API"}


@app.get("/health")
async def health():
    from datetime import datetime
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat() + "Z"}


@app.get("/api/cache/clear")
async def cache_clear():
    """
    Flush all Sofascore player data from the in-process session_state cache.
    Clears ss_surface_v6_*, ss_events_v2_*, and ss_stats_* keys.
    After clearing, the next player request will trigger a fresh Sofascore fetch.
    """
    ss = _st_mock.session_state
    cleared_keys = [k for k in list(ss.keys()) if k.startswith(("ss_", "ss_stats_"))]
    for k in cleared_keys:
        del ss[k]
    logger.info("CACHE_CLEARED | removed %d keys", len(cleared_keys))
    return {
        "status": "cleared",
        "keys_removed": len(cleared_keys),
        "keys": cleared_keys,
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str
    tour: str = "ATP"


class PlayerStatsRequest(BaseModel):
    player_id: str
    player_name: str = ""
    tour: str = "ATP"


class PropRequest(BaseModel):
    player_id: str
    opponent_id: str
    player_name: str = ""
    opponent_name: str = ""
    tour: str = "ATP"
    surface: str = "Hard"
    court: str = ""
    prop_type: str = "Aces"
    prop_line: float = 0.0
    # Optional: client may send rankings to improve win-prob estimation
    player_rank: Optional[int] = None
    opponent_rank: Optional[int] = None
    # ATP Grand Slam qualifying rounds are best-of-3 (main draw is best-of-5).
    # Only meaningful for ATP Grand Slam courts; ignored otherwise.
    qualifying: bool = False


class H2HRequest(BaseModel):
    player1_id: str
    player2_id: str
    tour: str = "ATP"
    surface: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_matches(matches: list) -> list:
    """Strip non-JSON-serialisable values from match dicts."""
    safe = []
    for m in matches:
        safe.append({
            k: v for k, v in m.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        })
    return safe


def _resolve_lean(proj: float, line: float, model_lean: str) -> str:
    if line > 0:
        if proj == line:
            return "UNDER"
        return "OVER" if proj > line else "UNDER"
    if model_lean in ("OVER", "UNDER"):
        return model_lean
    return "UNDER"


def _edge_cap(confidence: int, proj: float, line: float) -> int:
    if line <= 0 or proj <= 0:
        return confidence
    edge_pct = abs(proj - line) / line * 100
    cap = 50 if edge_pct < 5 else 65 if edge_pct < 10 else 80 if edge_pct < 15 else 95
    return min(confidence, cap)


def _build_explanation(req: PropRequest, result: dict, lean: str,
                       proj: float, line: float, cpr: int,
                       sackmann_weight: float = 0.0,
                       sackmann_matches: int = 0,
                       ta_ss_matches: int = 0) -> str:
    parts = []
    pt = req.prop_type

    if pt == "Aces":
        sup = result.get("suppression_factor", 1.0)
        cf  = result.get("cpr_factor", 1.0)
        hf  = result.get("hand_factor", 1.0) or 1.0
        ag  = result.get("ace_against_factor", 1.0) or 1.0
        ph  = result.get("player_hand") or ""
        oh  = result.get("opp_hand") or ""
        hand_note = ""
        if ph and oh and ph != oh:
            direction = "reduces" if hf < 1.0 else "boosts"
            hand_note = f" Handedness ({ph} vs {oh}) {direction} ace projection times {hf:.2f}."
        parts.append(
            f"Surface CPR {cpr} applies times {cf:.2f} court-speed factor."
            f" Opponent return suppression times {sup:.2f}."
            f" Opponent ace-against factor times {ag:.2f}.{hand_note}"
        )
    elif pt == "Double Faults":
        pf = result.get("pressure_factor", 1.0)
        parts.append(
            f"Opponent return aggression factor times {pf:.2f} --"
            f" {'increases' if pf > 1 else 'reduces'} second-serve pressure."
        )
    elif pt == "Total Games":
        env = ENVIRONMENT_LABELS.get(result.get("environment", "STANDARD"), "Standard")
        gps = result.get("games_per_set", 0)
        sets = result.get("expected_sets", 2.3)
        ch   = result.get("combined_hold", 72)
        parts.append(
            f"{env} -- combined hold {ch:.0f}% gives {gps:.1f} games/set "
            f"over {sets:.1f} expected sets."
        )
    elif pt == "Break Points Won":
        env   = ENVIRONMENT_LABELS.get(result.get("environment", "STANDARD"), "Standard")
        conv  = result.get("conv_rate_pct") or 0
        faced = result.get("opp_bp_faced") or 0
        base  = result.get("base_proj") or (conv / 100) * faced
        ta_src = result.get("conv_rate_source", "")
        src_note = f" ({ta_src} data)" if ta_src else ""
        tour_avg_note = (
            " Opponent BP-faced data thin -- tour average used as baseline."
            if result.get("used_opp_tour_avg") else ""
        )
        parts.append(
            f"{env} -- {conv:.0f}% conversion rate times {faced:.1f} BPs opponent faces/match"
            f" = {base:.1f} base.{src_note}{tour_avg_note}"
        )
        # Opportunity scaling note — only shown when multiplier is meaningful (>5%)
        opp_mult = result.get("opp_scaling_factor", 1.0) or 1.0
        ret_factor = result.get("returner_factor", 1.0) or 1.0
        if opp_mult > 1.05:
            pct = round((opp_mult - 1.0) * 100)
            parts.append(f"Break-opportunity feedback adds {pct}% more chances.")
        if ret_factor > 1.03:
            pct = round((ret_factor - 1.0) * 100)
            parts.append(f"Return dominance creates {pct}% additional BP opportunities.")
        elif ret_factor < 0.97:
            pct = round((1.0 - ret_factor) * 100)
            parts.append(f"Return position reduces BP opportunities by {pct}%.")
        cpr_adj = result.get("cpr_adj_pct", 0)
        if cpr_adj:
            sign = "+" if cpr_adj >= 0 else ""
            parts.append(f"Surface CPR {cpr} applies {sign}{cpr_adj:.1f}%.")

    # Sackmann historical supplement note
    if sackmann_weight > 0.05 and sackmann_matches > 0:
        pct = round(sackmann_weight * 100)
        parts.append(
            f"Stats blended with {sackmann_matches}-match historical baseline "
            f"(2015-2020, {pct}% weight) to supplement recent data."
        )

    if line > 0:
        edge = proj - line
        sign = "+" if edge >= 0 else ""
        parts.append(
            f"Model projects {proj:.1f} vs book line {line:.1f} "
            f"(edge {sign}{edge:.1f}) -- {lean}."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# GET /api/search  -- GET avoids CORS preflight entirely
# POST /api/search -- kept for backwards compat
# ---------------------------------------------------------------------------
_SEARCH_UNAVAILABLE = JSONResponse(
    status_code=503,
    content={
        "error":   "data_source_unavailable",
        "message": "Unable to reach player database — try again in a few minutes",
    },
)


@app.get("/api/search/debug")
async def search_debug(q: str = "sinner"):
    """Raw Sofascore response — for diagnosing search filter issues."""
    from src.api.sofascore_client import _get, BASE_URL, SEARCH_BASE_URL, HEADERS
    loop = asyncio.get_event_loop()
    def _raw():
        www = _get(f"{BASE_URL}/search/all", {"q": q})
        api = _get(f"{SEARCH_BASE_URL}/search/all", {"q": q})
        www_items = www.get("results", [])
        api_items = api.get("results", [])
        return {
            "query": q,
            "www_count": len(www_items),
            "api_count": len(api_items),
            "www_first5": [
                {
                    "name":    (i.get("entity") or {}).get("name"),
                    "type":    (i.get("entity") or {}).get("type"),
                    "sport":   ((i.get("entity") or {}).get("sport") or {}).get("name"),
                    "sport_id":((i.get("entity") or {}).get("sport") or {}).get("id"),
                    "gender":  (i.get("entity") or {}).get("gender"),
                    "ranking": (i.get("entity") or {}).get("ranking"),
                    "country": (i.get("entity") or {}).get("country"),
                }
                for i in www_items[:5]
            ],
            "api_first5": [
                {
                    "name":    (i.get("entity") or {}).get("name"),
                    "type":    (i.get("entity") or {}).get("type"),
                    "sport":   ((i.get("entity") or {}).get("sport") or {}).get("name"),
                    "sport_id":((i.get("entity") or {}).get("sport") or {}).get("id"),
                    "gender":  (i.get("entity") or {}).get("gender"),
                    "ranking": (i.get("entity") or {}).get("ranking"),
                }
                for i in api_items[:5]
            ],
        }
    result = await loop.run_in_executor(None, _raw)
    return result


@app.get("/api/proxy/health")
async def proxy_health():
    """Test every Decodo port against the proxy's OWN ip-check endpoint
    (no Sofascore involved). Isolates a proxy account/credential failure
    (all ports fail to tunnel) from a Sofascore-side block (tunnel ok,
    Sofascore 403s)."""
    from src.api.sofascore_client import (
        _PROXY_PORTS, _PROXY_HOST, _PROXY_USER, _PROXY_PASS, _proxy_url,
    )
    loop = asyncio.get_event_loop()
    def _check():
        from curl_cffi import requests as cf
        out = []
        for port in _PROXY_PORTS:
            pu = _proxy_url(port)
            try:
                s = cf.Session(impersonate="chrome124")
                s.proxies = {"http": pu, "https": pu}
                r = s.get("https://ip.decodo.com/json", timeout=8)
                ip = ""
                try:
                    j = r.json()
                    ip = (j.get("proxy") or {}).get("ip") or j.get("ip") or ""
                except Exception:
                    pass
                out.append({"port": port, "status": r.status_code, "ext_ip": ip})
            except Exception as e:
                out.append({"port": port, "status": None, "error": f"{type(e).__name__}: {str(e)[:120]}"})
        return out
    results = await loop.run_in_executor(None, _check)
    return {
        "proxy_host": _PROXY_HOST,
        "username_set": bool(_PROXY_USER),
        "password_set": bool(_PROXY_PASS),
        "ports_tested": len(_PROXY_PORTS),
        "ports_ok": sum(1 for r in results if r.get("status") == 200),
        "results": results,
    }


@app.get("/api/player/next-match")
async def player_next_match(player_id: str = "", tour: str = "ATP"):
    """The player's next scheduled match (tournament + surface + opponent)."""
    if not player_id:
        return {}
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, get_player_next_match, player_id, tour),
            timeout=20.0,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("next-match endpoint error pid=%s: %s", player_id, e)
        return {}


@app.get("/api/proxy/usage")
async def proxy_usage():
    """Live proxy-vs-cache counts for the current day (STEP 7)."""
    from src.api.sofascore_client import proxy_usage_stats
    return proxy_usage_stats()


@app.get("/api/proxy/session-test")
async def proxy_session_test():
    """Proof that the Decodo sticky-session username format works (sticky +
    rotation + Sofascore 200) before switching the main path over."""
    from src.api.sofascore_client import run_session_format_test
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, run_session_format_test)


@app.get("/api/search/probe")
async def search_probe(q: str = "alcaraz"):
    """Raw HTTP status + body snippet from Sofascore — distinguishes a 403
    block / 407 proxy-auth failure from a genuine empty 200 response."""
    from src.api.sofascore_client import probe_request, BASE_URL, SEARCH_BASE_URL
    loop = asyncio.get_event_loop()
    def _probe():
        return {
            "query": q,
            "www": probe_request(f"{BASE_URL}/search/all", {"q": q}),
            "api": probe_request(f"{SEARCH_BASE_URL}/search/all", {"q": q}),
        }
    return await loop.run_in_executor(None, _probe)


@app.get("/api/search")
async def search_get(query: str = "", tour: str = "ATP"):
    q = query.strip()
    if len(q) < 3:
        return []
    t0 = time.time()
    loop = asyncio.get_event_loop()
    try:
        # search_players calls blocking requests.get() — must run in executor
        # so it never blocks the event loop and starves other endpoints.
        result = await asyncio.wait_for(
            loop.run_in_executor(None, search_players, q, tour),
            timeout=60.0,
        )
        elapsed = time.time() - t0
        logger.info("SEARCH | q=%r tour=%s t=%.2fs results=%d", q, tour, elapsed, len(result or []))
        return result or []
    except asyncio.TimeoutError:
        logger.warning("SEARCH TIMEOUT | q=%r tour=%s after 60s", q, tour)
        return []
    except SofascoreBlockedError as e:
        logger.error("SEARCH BLOCKED | q=%r tour=%s: %s", q, tour, e)
        return _SEARCH_UNAVAILABLE
    except Exception as e:
        logger.error("SEARCH ERROR | q=%r tour=%s err=%s", q, tour, e)
        return []

@app.post("/api/search")
async def search_post(req: SearchRequest):
    q = req.query.strip()
    if len(q) < 3:
        return []
    t0 = time.time()
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, search_players, q, req.tour),
            timeout=60.0,
        )
        elapsed = time.time() - t0
        logger.info("SEARCH | q=%r tour=%s t=%.2fs results=%d", q, req.tour, elapsed, len(result or []))
        return result or []
    except asyncio.TimeoutError:
        logger.warning("SEARCH TIMEOUT | q=%r tour=%s after 60s", q, req.tour)
        return []
    except SofascoreBlockedError as e:
        logger.error("SEARCH BLOCKED | q=%r tour=%s: %s", q, req.tour, e)
        return _SEARCH_UNAVAILABLE
    except Exception as e:
        logger.error("SEARCH ERROR | q=%r tour=%s err=%s", q, req.tour, e)
        return []


# ---------------------------------------------------------------------------
# POST /api/player/stats
# ---------------------------------------------------------------------------
@app.post("/api/player/stats")
async def player_stats(req: PlayerStatsRequest):
    try:
        _loop2 = asyncio.get_event_loop()

        # Fetch Sofascore stats and Tennis Abstract CONCURRENTLY (was sequential,
        # which made a cold /player ~17s Sofascore + up to 30s TA = ~33s+ and blew
        # the bot timeout). Now cold ~= max(Sofascore, TA); TA capped at 12s.
        async def _ta_safe():
            if not req.player_name:
                return None
            try:
                return await asyncio.wait_for(
                    get_player_ta_stats(req.player_name, req.tour), timeout=12.0
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("TA fetch failed for stats endpoint: %s", e)
                return None

        data, ta_data = await asyncio.gather(
            _loop2.run_in_executor(None, get_player_stats_by_surface, req.player_id, req.tour),
            _ta_safe(),
        )
        all_stats = data.get("All", {}) or {}
        archetype = classify_archetype(all_stats, req.tour)

        # Tournament titles (finals won) — reuses the event cache just warmed
        # above, so it's cheap. Isolated: failure just omits the section.
        try:
            from src.api.sofascore_client import get_player_titles
            titles = await _loop2.run_in_executor(
                None, get_player_titles, req.player_id, req.tour)
        except Exception as _te:  # noqa: BLE001
            logger.warning("titles fetch failed: %s", _te)
            titles = {}

        return {
            "titles": titles,
            "All":   data.get("All", {}),
            "Hard":  data.get("Hard", {}),
            "Clay":  data.get("Clay", {}),
            "Grass": data.get("Grass", {}),
            "form":  data.get("form", []),
            "archetype": archetype,
            "all_matches":   _safe_matches(data.get("all_matches", [])),
            "Hard_matches":  _safe_matches(data.get("Hard_matches", [])),
            "Clay_matches":  _safe_matches(data.get("Clay_matches", [])),
            "Grass_matches": _safe_matches(data.get("Grass_matches", [])),
            "ta_stats":      ta_data,
        }
    except Exception as e:
        logger.error("player/stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
async def _bp_quality_adjusted_generated(surface_matches, surface, tour, budget=8.0):
    """Quality-of-server-adjusted BP generated. Weight each surface match's
    break-points-generated by the OPPONENT's surface hold (service games won %):
    >80% → 1.3 (harder chances, more meaningful), <65% → 0.7 (weak server), else
    1.0. Cache-first; fetch misses in parallel under a time budget, leaving any
    stragglers running so they warm the cache for next time. Unresolved opponents
    default to weight 1.0. Returns (qadj_per_match, raw_per_match, resolved, total)."""
    pairs = [
        (m.get("opponent_id"), m.get("return_bp_opportunities"))
        for m in (surface_matches or [])
        if m.get("opponent_id") and m.get("return_bp_opportunities") is not None
    ]
    raw_vals = [bg for _, bg in pairs]
    raw_avg = round(sum(raw_vals) / len(raw_vals), 4) if raw_vals else None
    if not pairs:
        return None, raw_avg, 0, 0

    uniq = list({oid for oid, _ in pairs})
    holds, misses = {}, []
    for oid in uniq:
        h = peek_surface_hold(oid, surface)
        (holds.__setitem__(oid, h) if h is not None else misses.append(oid))

    if misses:
        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(5)

        async def _fetch(oid):
            async with sem:
                h = await loop.run_in_executor(
                    None, get_player_surface_hold, str(oid), surface, tour)
                return oid, h

        tasks = [asyncio.create_task(_fetch(oid)) for oid in misses]
        done, _pending = await asyncio.wait(tasks, timeout=budget)
        for d in done:
            try:
                oid, h = d.result()
                if h is not None:
                    holds[oid] = h
            except Exception:
                pass
        # `_pending` left running on purpose — they finish in the background and
        # populate _SURFACE_HOLD_CACHE so the next projection is fully weighted.

    num = den = 0.0
    for oid, bg in pairs:
        h = holds.get(oid)
        w = 1.3 if (h is not None and h > 80.0) else \
            0.7 if (h is not None and h < 65.0) else 1.0
        num += w * bg
        den += w
    qadj = round(num / den, 4) if den > 0 else raw_avg
    logger.info(
        "BP_QADJ | surf=%s | opps=%d uniq=%d resolved=%d | raw=%.2f -> qadj=%.2f",
        surface, len(pairs), len(uniq), len(holds), raw_avg or 0.0, qadj or 0.0,
    )
    return qadj, raw_avg, len(holds), len(uniq)


# POST /api/prop/calculate
# ---------------------------------------------------------------------------
@app.post("/api/prop/calculate")
async def prop_calculate(req: PropRequest):
    try:
        # All Sofascore calls are blocking sync — run in executor so they
        # never freeze the event loop and block search / other endpoints.
        _loop = asyncio.get_event_loop()
        p1_data, p2_data, h2h_summary, h2h_stats = await asyncio.gather(
            _loop.run_in_executor(None, get_player_stats_by_surface, req.player_id, req.tour),
            _loop.run_in_executor(None, get_player_stats_by_surface, req.opponent_id, req.tour),
            _loop.run_in_executor(None, get_h2h_summary, req.tour, req.player_id, req.opponent_id, req.surface),
            _loop.run_in_executor(None, get_h2h_stat_avg, req.tour, req.player_id, req.opponent_id, req.surface),
        )

        # Raw Sofascore surface stats (for fallback / return stats)
        p1_surface = p1_data.get(req.surface, {}) or {}
        p1_all     = p1_data.get("All", {}) or {}
        p2_surface = p2_data.get(req.surface, {}) or {}
        p2_all     = p2_data.get("All", {}) or {}
        p1_ss_raw  = p1_surface if p1_surface.get("matches_played", 0) else p1_all
        p2_ss_raw  = p2_surface if p2_surface.get("matches_played", 0) else p2_all

        # Sofascore surface logs (recent form — up to 10 stat-rich surface matches)
        p1_ss_log = p1_data.get(f"{req.surface}_surface_log", [])
        p2_ss_log = p2_data.get(f"{req.surface}_surface_log", [])

        h2h_ace_avg      = h2h_stats.get("ace")
        h2h_df_avg       = h2h_stats.get("df")
        h2h_bp_avg       = h2h_stats.get("bp")
        h2h_games_avg    = h2h_stats.get("games_avg")
        h2h_surf_matches = h2h_summary.get("surface_matches", 0)

        # Canonicalize a free-form court/tournament name (e.g. Sofascore's
        # "Bad Homburg, Germany") to a COURT_CPR key so the right ST Pace Index
        # is used. Exact keys (website, /prop) pass through unchanged.
        court_for_calc = "" if req.court in ("", "None") else resolve_court_name(req.court, req.tour)

        # ── ST Pace Index lookup — dynamic first, hardcoded fallback ─────────
        _fallback_cpr = COURT_CPR.get(court_for_calc,
                         GENERIC_SURFACE_CPR.get(req.surface, CPR_NEUTRAL))
        # lookup_pace_index is blocking; run in executor so it doesn't stall the loop
        _loop_cpr = asyncio.get_event_loop()
        _st_live_val, _st_source = await _loop_cpr.run_in_executor(
            None, lookup_pace_index, court_for_calc, _fallback_cpr
        )
        cpr = _st_live_val if _st_live_val is not None else _fallback_cpr
        _st_source_label = "st_live" if _st_source == "st_live" else "hardcoded"

        # Speed tier + year-over-year context
        _speed_tier = get_speed_tier(cpr)
        _yoy = ST_PACE_PREVIOUS_YEAR.get(court_for_calc)
        _yoy_note: Optional[str] = None
        if _yoy and abs(cpr - _yoy["prev"]) >= ST_YOY_THRESHOLD:
            direction = "faster" if cpr > _yoy["prev"] else "slower"
            _yoy_note = (
                f"{_yoy['prev_year'] + 1}: {cpr:.1f} vs {_yoy['prev_year']}: {_yoy['prev']:.1f} "
                f"— significantly {direction} this year"
            )

        logger.info(
            "CPR | court=%r surface=%s | fallback=%.1f | st_val=%.1f | "
            "source=%s | tier=%s | yoy=%s",
            court_for_calc, req.surface, _fallback_cpr, cpr,
            _st_source_label, _speed_tier, _yoy_note or "none",
        )

        # Fetch Tennis Abstract data for both players concurrently (30s timeout each)
        async def _ta_safe(name):
            if not name:
                return None
            try:
                return await asyncio.wait_for(
                    get_player_ta_stats(name, req.tour), timeout=30.0
                )
            except Exception as exc:
                logger.warning("TA fetch failed for '%s': %s", name, exc)
                return None

        # Start Sackmann CSV tasks immediately (run while TA fetches happen)
        # load_player_sackmann_data already has an internal 15s hard cap so
        # asyncio.to_thread will never block longer than that.
        def _sack_load(name: str) -> list:
            """Sync wrapper — safe to call from to_thread; never raises."""
            if not name:
                return []
            try:
                norm = normalize_name_for_sackmann(name)
                return load_player_sackmann_data(norm, req.tour)
            except Exception as exc:
                logger.warning("SACKMANN_FATAL | player=%s | %s", name, exc)
                return []

        sack_task_p1 = asyncio.create_task(
            asyncio.to_thread(_sack_load, req.player_name)
        )
        sack_task_p2 = asyncio.create_task(
            asyncio.to_thread(_sack_load, req.opponent_name)
        )

        # TA fetches run concurrently with the Sackmann tasks above
        player_ta, opponent_ta = await asyncio.gather(
            _ta_safe(req.player_name),
            _ta_safe(req.opponent_name),
        )

        # ── Build Prop-Projection-only TA views ─────────────────────────────
        # The Prop Projection tab must NOT use TA career data — players' levels
        # shift over years. Build views whose surface_stats are the last 52
        # weeks (with 2-yr fallback when sample is thin). All other tabs read
        # the full `player_ta` directly and are unaffected.
        player_ta_props,   p1_recent_meta = build_props_ta_view(player_ta,   req.surface)
        opponent_ta_props, p2_recent_meta = build_props_ta_view(opponent_ta, req.surface)
        logger.info(
            "TA_RECENT | p1=%s tier=%s n=%d (all_52w=%d) warn=%s | "
            "p2=%s tier=%s n=%d (all_52w=%d) warn=%s",
            req.player_name, p1_recent_meta["tier"], p1_recent_meta["surface_n"],
            p1_recent_meta["all_surfaces_n"], p1_recent_meta["warning"],
            req.opponent_name, p2_recent_meta["tier"], p2_recent_meta["surface_n"],
            p2_recent_meta["all_surfaces_n"], p2_recent_meta["warning"],
        )

        # Collect Sackmann results — give them up to 18s total from task creation.
        # If still running, shield cancels the wait but leaves the task alive for cache.
        try:
            p1_sack_matches = await asyncio.wait_for(
                asyncio.shield(sack_task_p1), timeout=18.0
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("SACKMANN_SKIPPED | player=%s | %s", req.player_name, exc)
            p1_sack_matches = []

        try:
            p2_sack_matches = await asyncio.wait_for(
                asyncio.shield(sack_task_p2), timeout=18.0
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("SACKMANN_SKIPPED | opponent=%s | %s", req.opponent_name, exc)
            p2_sack_matches = []

        # Aggregate Sackmann stats: surface-specific first, all-surface fallback
        p1_sack_surf = aggregate_sackmann_stats(p1_sack_matches, surface_filter=req.surface)
        p1_sack_all  = aggregate_sackmann_stats(p1_sack_matches, surface_filter=None)
        p2_sack_surf = aggregate_sackmann_stats(p2_sack_matches, surface_filter=req.surface)
        p2_sack_all  = aggregate_sackmann_stats(p2_sack_matches, surface_filter=None)

        # ── Unified match pool (Sofascore + Sackmann, deduplicated) ──────────
        # Normalise every source into a common schema, merge, then filter.
        # Used for: match count, win rate display, bar chart.
        # Projection stats still come from get_blended_stats (TA+SS+Sackmann blend).
        p1_ss_all = p1_data.get("all_matches", [])
        p2_ss_all = p2_data.get("all_matches", [])

        p1_unified = merge_and_deduplicate(
            [normalize_sofascore_match(m) for m in p1_ss_all],
            [normalize_sackmann_match(m) for m in p1_sack_matches],
        )
        p2_unified = merge_and_deduplicate(
            [normalize_sofascore_match(m) for m in p2_ss_all],
            [normalize_sackmann_match(m) for m in p2_sack_matches],
        )

        p1_unified_surf = aggregate_unified_stats(p1_unified, surface_filter=req.surface)
        p2_unified_surf = aggregate_unified_stats(p2_unified, surface_filter=req.surface)

        # Build blended stats — RECENCY-FOCUSED for the Prop Projection tab.
        # TA last-52-weeks 40% + SS 3yr 30% + SS last-20 20% + SS last-5 10%.
        # SS all-time (career) is dropped: career averages can mislead because
        # players' levels shift over years.  Sackmann supplements only when SS
        # data is thin.
        p1_blended = get_blended_stats(
            p1_data, p1_ss_log, req.surface, req.tour,
            player_ta=player_ta,
            sackmann_stats=p1_sack_surf, sackmann_all_stats=p1_sack_all,
            recency_focused=True,
            ta_recent_stats=p1_recent_meta["stats"],
        )
        p2_blended = get_blended_stats(
            p2_data, p2_ss_log, req.surface, req.tour,
            player_ta=opponent_ta,
            sackmann_stats=p2_sack_surf, sackmann_all_stats=p2_sack_all,
            recency_focused=True,
            ta_recent_stats=p2_recent_meta["stats"],
        )

        # Inject SS ace-against-per-match into blended dicts so project_aces
        # can use it directly from opponent_stats without TA dependency.
        p1_ss_ace_ag = p1_data.get(f"{req.surface}_ace_against_per_match")
        p2_ss_ace_ag = p2_data.get(f"{req.surface}_ace_against_per_match")
        if p2_ss_ace_ag is not None:
            p2_blended["ace_against_per_match"] = p2_ss_ace_ag

        # Merged stats: blended is the primary source; return stats already
        # include return stats from the SS tiers in get_blended_stats.
        def _merge_with_ss(blended: dict, ss_raw: dict) -> dict:
            merged = dict(blended)
            for k in ("return_first_serve_pts_won", "return_second_serve_pts_won",
                      "bp_faced_count"):
                if merged.get(k) is None:
                    merged[k] = ss_raw.get(k)
            return merged

        p1_s = _merge_with_ss(p1_blended, p1_ss_raw)
        p2_s = _merge_with_ss(p2_blended, p2_ss_raw)

        # Override matches_played with unified pool count when it's higher.
        # This ensures challenger players (whose SS stats API fails) show the
        # correct match count from all sources rather than 0 or undercount.
        if p1_unified_surf and p1_unified_surf["matches"] > p1_s.get("matches_played", 0):
            p1_s["matches_played"] = p1_unified_surf["matches"]
        if p2_unified_surf and p2_unified_surf["matches"] > p2_s.get("matches_played", 0):
            p2_s["matches_played"] = p2_unified_surf["matches"]

        # ── Backfill displayed surface SERVE stats from the 52-week TA view ───
        # Sofascore's surface stats can come back empty even for a player with a
        # real surface history (e.g. Sinner's grass: Sofascore returns 0 grass
        # matches in its window, so Aces/DFs/serve% render as "—"), while Tennis
        # Abstract DOES have the matches (the projection already uses them). Fill
        # the display from the SAME 52-week TA surface view project_aces reads,
        # so the panel shows real grass numbers instead of blanks. Percentages
        # map straight across; ace/DF counts convert from per-serve-point % using
        # the tour's average service points per match. Projection math is
        # unaffected — project_aces reads TA directly and prefers it already.
        def _backfill_serve_display(s: dict, ta_props: dict, all_stats: dict,
                                    surf: str, tour: str) -> None:
            # (1) Preferred: surface-specific TA serve stats. Gate on the actual
            # data being present (ace_pct), not a "matches" key — the recent-TA
            # view stores its sample size separately, so an over-strict gate
            # silently skipped real grass data.
            tsurf = (ta_props or {}).get("surface_stats", {}).get(surf) or {}
            if tsurf:
                sp = 80.0 if tour == "ATP" else 70.0
                ap, dp = tsurf.get("ace_pct"), tsurf.get("df_pct")
                if s.get("aces") is None and ap is not None:
                    s["aces"] = round((ap / 100.0) * sp, 1)
                if s.get("double_faults") is None and dp is not None:
                    s["double_faults"] = round((dp / 100.0) * sp, 1)
                for dst, src in (("first_serve_pct", "first_in_pct"),
                                 ("first_serve_pts_won", "first_won_pct"),
                                 ("second_serve_pts_won", "second_won_pct"),
                                 ("bp_converted", "bp_conv_pct"),
                                 ("bp_saved", "bp_saved_pct")):
                    if s.get(dst) is None and tsurf.get(src) is not None:
                        s[dst] = tsurf.get(src)
            # (2) Last-resort: the player's all-surface Sofascore stats, so the
            # panel NEVER shows blanks when the surface sample is empty (e.g.
            # Sofascore returns 0 recent grass matches). All-surface is a fair
            # stand-in and far better than "--"; the projection is unaffected.
            a = all_stats or {}
            for k in ("aces", "double_faults", "first_serve_pct",
                      "first_serve_pts_won", "second_serve_pts_won",
                      "bp_converted", "bp_saved"):
                if s.get(k) is None and a.get(k) is not None:
                    s[k] = a.get(k)

        _backfill_serve_display(p1_s, player_ta_props, p1_all, req.surface, req.tour)
        _backfill_serve_display(p2_s, opponent_ta_props, p2_all, req.surface, req.tour)

        # Inject identity, rank, and recent form into stats dicts so the
        # expected-sets win-prob estimator has everything it needs.
        p1_s["player_name"] = req.player_name or req.player_id
        p2_s["player_name"] = req.opponent_name or req.opponent_id
        if req.player_rank:
            p1_s["rank"] = int(req.player_rank)
        if req.opponent_rank:
            p2_s["rank"] = int(req.opponent_rank)
        # Recent form: derived from the unified surface match list (newest first)
        _p1_recent_matches = p1_data.get(f"{req.surface}_matches", []) or []
        _p2_recent_matches = p2_data.get(f"{req.surface}_matches", []) or []
        p1_s["form"] = [{"won": bool(m.get("won"))} for m in _p1_recent_matches[:10]]
        p2_s["form"] = [{"won": bool(m.get("won"))} for m in _p2_recent_matches[:10]]
        # All-surface stats — the win-prob estimator shrinks thin-sample surface
        # win rate AND serve/return toward these, so a few (or zero) grass matches
        # with corrupted stats can't invert the matchup.
        _p1_at = p1_data.get("All_all_time_stats") or {}
        _p2_at = p2_data.get("All_all_time_stats") or {}
        # Strength-of-schedule must reflect the player's CURRENT level, not their
        # whole career (a player who climbed through ITF/Challenger has a low
        # career tier but plays ATP now). Use the last-20-matches tier, falling
        # back to the 3-year then all-time average.
        _p1_recent = p1_data.get("All_last_20") or {}
        _p2_recent = p2_data.get("All_last_20") or {}
        _p1_3yr = p1_data.get("All_recent_3yr_stats") or {}
        _p2_3yr = p2_data.get("All_recent_3yr_stats") or {}
        for _s, _at, _rec, _3y in ((p1_s, _p1_at, _p1_recent, _p1_3yr),
                                   (p2_s, _p2_at, _p2_recent, _p2_3yr)):
            _s["overall_win_rate"]                    = _at.get("win_rate")
            _s["overall_first_serve_pts_won"]         = _at.get("first_serve_pts_won")
            _s["overall_second_serve_pts_won"]        = _at.get("second_serve_pts_won")
            _s["overall_return_first_serve_pts_won"]  = _at.get("return_first_serve_pts_won")
            _s["overall_return_second_serve_pts_won"] = _at.get("return_second_serve_pts_won")
            _s["competition_level"] = (_rec.get("competition_level")
                                       or _3y.get("competition_level")
                                       or _at.get("competition_level"))

        # All-surface ace rate + surface ace sample size — lets project_aces
        # regress a thin-surface ace base toward the player's broader rate so one
        # high-ace match can't define a clay player's grass projection.
        _now_ts = time.time()
        for _s, _pdata, _pall in ((p1_s, p1_data, p1_all), (p2_s, p2_data, p2_all)):
            _s["overall_aces"] = _pall.get("aces")
            _surf_log = _pdata.get(f"{req.surface}_matches", []) or []
            _s["ace_surface_n"] = sum(1 for _m in _surf_log
                                      if isinstance(_m.get("aces"), (int, float)))
            # Recency-weighted ace average (half-life 120d): recent / post-injury
            # form dominates and stale matches (e.g. last year's grass peak) decay,
            # so the base reflects how the player is serving NOW, not a year ago.
            _num = _den = 0.0
            for _m in _surf_log:
                _a, _ts = _m.get("aces"), _m.get("timestamp") or 0
                if not isinstance(_a, (int, float)) or not _ts:
                    continue
                _w = 0.5 ** (max(0.0, (_now_ts - _ts) / 86400.0) / 120.0)
                _num += _w * _a
                _den += _w
            _s["recency_weighted_aces"] = (_num / _den) if _den > 0 else None

        # ── Match format: strict rules, logged for every request ────────────────
        # ATP Grand Slam MAIN DRAW → best_of_5. ATP Grand Slam QUALIFYING →
        # best_of_3. ALL WTA events → best_of_3 (no exceptions; WTA Grand Slams
        # are NEVER BO5). All ATP non-GS → best_of_3.
        _is_atp_gs   = court_for_calc in GRAND_SLAMS and req.tour == "ATP"
        # Wimbledon ATP FINAL qualifying round (3rd round) is played best-of-5 —
        # treat it like a main-draw round, not best-of-3 like the earlier qual
        # rounds. Detected from the raw court/tournament string (which carries the
        # round, e.g. "Wimbledon, London, GB, Qualifying, 3rd Round").
        _raw_court_l = (req.court or "").lower()
        _wimb_final_qual = (req.tour == "ATP" and "wimbledon" in _raw_court_l
                            and "qualif" in _raw_court_l
                            and ("3rd round" in _raw_court_l or "final" in _raw_court_l))
        _qualifying  = bool(req.qualifying) and _is_atp_gs and not _wimb_final_qual
        match_fmt    = "best_of_5" if (_is_atp_gs and not _qualifying) else "best_of_3"
        # Human-readable label for the UI/bot so the format is confirmable.
        if _is_atp_gs:
            if _wimb_final_qual:
                _round_lbl = "Final Qualifying Round"
            else:
                _round_lbl = "Qualifying" if _qualifying else "Main Draw"
            _fmt_lbl = "Best of 5" if match_fmt == "best_of_5" else "Best of 3"
            match_format_label = f"{court_for_calc} — {_round_lbl} — {_fmt_lbl}"
        else:
            match_format_label = "Best of 3"
        logger.info(
            "MATCH_FORMAT | court=%s | tour=%s | is_atp_gs=%s | qualifying=%s | match_fmt=%s",
            court_for_calc or "generic", req.tour, _is_atp_gs, _qualifying, match_fmt,
        )

        # ── Total data-availability guard (ISSUE 1) ─────────────────────────
        # "SS 0 recent" means Sofascore returned zero matches (Varnish JS
        # challenge / 403). When the unified pool (Sofascore + Sackmann) AND
        # Tennis Abstract both return nothing for a player, every component
        # below falls back to tour-average defaults and manufactures a
        # projection from no data. Refuse to project instead of guessing.
        p1_total_data = len(p1_unified or []) + (p1_blended.get("_ta_career_matches", 0) or 0)
        p2_total_data = len(p2_unified or []) + (p2_blended.get("_ta_career_matches", 0) or 0)
        p1_stale = bool(p1_data.get("_stale_cache"))
        p2_stale = bool(p2_data.get("_stale_cache"))
        logger.info(
            "DATA_AVAIL | p1=%s total=%d stale=%s | p2=%s total=%d stale=%s",
            req.player_name, p1_total_data, p1_stale,
            req.opponent_name, p2_total_data, p2_stale,
        )
        _no_data = [
            n for n, t in (
                (req.player_name or "Player 1", p1_total_data),
                (req.opponent_name or "Player 2", p2_total_data),
            ) if t == 0
        ]
        if _no_data:
            who = " and ".join(_no_data)
            logger.error("DATA_GAP_REFUSE | no match data across any source for: %s", who)
            return {
                "model_projection": None,
                "lean": None,
                "confidence": 0,
                "note": (
                    f"Unable to load player match data — Sofascore temporarily "
                    f"unavailable ({who}). Please try again in a few minutes."
                ),
                "data_unavailable": True,
            }

        # ── Limited-surface-data threshold (ISSUE 2) ─────────────────────────
        # Fewer than 10 matches on the SELECTED surface (from the unified pool)
        # flags limited data, applied consistently to both players. 10+ → none.
        p1_surface_n = (p1_unified_surf or {}).get("matches", 0) or 0
        p2_surface_n = (p2_unified_surf or {}).get("matches", 0) or 0
        player_limited_data   = p1_surface_n < 10
        opponent_limited_data = p2_surface_n < 10
        logger.info(
            "SURFACE_DATA | %s n=%d limited=%s | %s n=%d limited=%s (threshold=10)",
            req.player_name, p1_surface_n, player_limited_data,
            req.opponent_name, p2_surface_n, opponent_limited_data,
        )

        # Run projection
        if req.prop_type == "Aces":
            result = project_aces(
                p1_s, p2_s, court_for_calc, h2h_ace_avg, cpr_override=cpr,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface,
                match_format=match_fmt,
            )
        elif req.prop_type == "Double Faults":
            result = project_double_faults(
                p1_s, p2_s, h2h_df_avg,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface,
                match_format=match_fmt,
                court=court_for_calc,
            )
        elif req.prop_type == "Total Games":
            result = project_total_games(
                p1_s, p2_s, req.surface, h2h_games_avg,
                tour=req.tour, court=court_for_calc,
                match_format=match_fmt,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
            )
        else:  # Break Points Won  OR  Player Total Games Won (both use the BP model)
            # All-surface player stats for Step 9 sanity check
            _p1_all_at = p1_data.get("All_all_time_stats") or {}
            p1_all_ref = {
                "bp_converted":                _p1_all_at.get("bp_converted"),
                "return_bp_opportunities":     _p1_all_at.get("return_bp_opportunities"),
                "return_bp_converted":         _p1_all_at.get("return_bp_converted"),
                "bp_faced_count":              _p1_all_at.get("bp_faced_count"),
                "return_first_serve_pts_won":  _p1_all_at.get("return_first_serve_pts_won"),
                "return_second_serve_pts_won": _p1_all_at.get("return_second_serve_pts_won"),
                "return_games_won_pct":        _p1_all_at.get("return_games_won_pct"),  # C2 career baseline
                "service_games_won_pct":       _p1_all_at.get("service_games_won_pct"),
            }
            if not p1_all_ref["bp_converted"] and player_ta:
                _ta_all = (player_ta.get("surface_stats") or {}).get("All") or {}
                p1_all_ref["bp_converted"] = _ta_all.get("bp_conv_pct")

            # All-surface opponent stats (for opponent opportunity blending)
            _p2_all_at = p2_data.get("All_all_time_stats") or {}
            p2_all_ref = {
                "bp_converted":                _p2_all_at.get("bp_converted"),
                "return_bp_opportunities":     _p2_all_at.get("return_bp_opportunities"),
                "bp_faced_count":              _p2_all_at.get("bp_faced_count"),
                "matches_played":              _p2_all_at.get("matches_played", 0),
                "return_first_serve_pts_won":  _p2_all_at.get("return_first_serve_pts_won"),
                "return_second_serve_pts_won": _p2_all_at.get("return_second_serve_pts_won"),
            }

            # ── BP quality-of-server adjustment (Break Points prop only) ──────
            # Gated on the BP prop so Player Total Games Won — which reuses
            # bp_result for its breaks component — is left exactly as before.
            is_bp_prop = (req.prop_type == "Break Points Won")
            bp_qadj = bp_raw_gen = None
            bp_forward_factor = 1.0
            if is_bp_prop:
                _bp_surf_matches = p1_data.get(f"{req.surface}_matches", []) or []
                bp_qadj, bp_raw_gen, _q_res, _q_tot = await _bp_quality_adjusted_generated(
                    _bp_surf_matches, req.surface, req.tour,
                )
                _opp_sgw = (p2_s or {}).get("service_games_won_pct")
                if _opp_sgw is None:
                    _opp_sgw = (p2_data.get("All") or {}).get("service_games_won_pct")
                if _opp_sgw is not None and _opp_sgw > 80.0:
                    bp_forward_factor = 0.85    # strong server → fewer chances created
                elif _opp_sgw is not None and _opp_sgw < 65.0:
                    bp_forward_factor = 1.10    # weak server → more chances created

            bp_result = project_break_points(
                p1_s, p2_s,
                player_all_stats=p1_all_ref,
                opponent_all_stats=p2_all_ref,
                h2h_bp_avg=h2h_bp_avg,
                cpr_override=cpr,
                h2h_match_count=h2h_surf_matches,
                player_ta=player_ta_props,
                opponent_ta=opponent_ta_props,
                surface=req.surface,
                tour=req.tour,
                opp_ss_matches=p2_blended.get("_ss_recent_matches", 0),
                match_format=match_fmt,
                court=court_for_calc,
                bp_prop_mode=is_bp_prop,
                bp_generated_quality_adj=bp_qadj,
                bp_generated_raw=bp_raw_gen,
                bp_forward_server_factor=bp_forward_factor,
            )

            if req.prop_type == "Player Total Games Won":
                # Combine the player's holds (from the combined Total Games
                # projection) with their breaks (the BP-won projection).
                tg_result = project_total_games(
                    p1_s, p2_s, req.surface, h2h_games_avg,
                    tour=req.tour, court=court_for_calc,
                    match_format=match_fmt,
                    player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                )
                result = project_player_games_won(
                    p1_s, p2_s, req.surface, cpr,
                    games_combined=tg_result.get("projection"),
                    bp_won=bp_result.get("projection"),
                    p1_win_prob=tg_result.get("p1_win_prob"),
                    p2_win_prob=tg_result.get("p2_win_prob"),
                    expected_sets=tg_result.get("expected_sets"),
                    tour=req.tour, match_format=match_fmt,
                )
            else:
                result = bp_result

        proj_val = result.get("projection")
        if proj_val is None:
            return {
                "model_projection": None,
                "lean": None,
                "confidence": 0,
                "note": result.get("note", "Insufficient data for this surface/prop combination."),
            }

        # Confidence — enhanced with TA match counts
        p1_surf_matches = p1_data.get(f"{req.surface}_matches", [])
        p2_surf_matches = p2_data.get(f"{req.surface}_matches", [])
        has_h2h_surface = h2h_summary.get("surface_matches", 0) > 0
        has_h2h_other   = (not has_h2h_surface) and h2h_summary.get("total", 0) > 0

        conf_result = calculate_confidence(
            player_surface_matches=p1_surf_matches,
            opponent_surface_matches=p2_surf_matches,
            prop_type=req.prop_type,
            has_h2h_surface=has_h2h_surface,
            has_h2h_other=has_h2h_other,
            court=court_for_calc,
            ta_career_surface_matches=p1_blended.get("_ta_career_matches", 0),
            ss_recent_surface_matches=p1_blended.get("_ss_recent_matches", 0),
            opp_ta_career_matches=p2_blended.get("_ta_career_matches", 0),
            p1_blended=p1_blended,
            p2_blended=p2_blended,
        )
        confidence = conf_result["confidence"]

        # ── BP opportunity-volume penalty (Part 1) ───────────────────────────
        # A conversion rate built on a thin opportunity sample is unreliable:
        # <3 BP generated/match → −10 and a disclosure note; ≥6 is a solid signal.
        if req.prop_type == "Break Points Won":
            _bp_gen = result.get("bp_generated_per_match")
            if _bp_gen is not None and _bp_gen < 3.0:
                confidence = max(25, confidence - 10)
                _bd = conf_result.get("breakdown")
                if isinstance(_bd, list):
                    _bd.append("Low opportunity volume — conversion rate based on limited chances")

        # Sackmann thin-data penalty (applied before sanity check)
        sack_penalty = p1_blended.get("_confidence_penalty", 0)
        if sack_penalty:
            confidence = max(25, confidence + sack_penalty)  # penalty is negative

        # ── Recent-data confidence penalty ───────────────────────────────────
        # Distinguish surface specialists from genuinely inactive players:
        #   Truly inactive  (<10 total in 52w)               → -20
        #   Middle ground   (10-19 total, <5 surface)        → -10
        #   Specialist      (≥20 total, <5 surface)          →  -5
        #   Healthy         (≥5 surface)                     →   0
        def _recent_penalty(meta):
            if not meta:
                return 0, None
            # TA match log not available — no penalty. The projection still
            # has Sofascore tiers behind it; we just can't compute recency.
            if meta.get("warning") == "ta_unavailable":
                return 0, None
            total_n   = meta.get("all_surfaces_n", 0) or 0
            surface_n = meta.get("surface_n", 0) or 0
            if meta.get("warning") == "insufficient" or total_n < 10:
                return -20, "insufficient"
            if surface_n >= 5:
                return 0, None
            if total_n >= 20:
                return -5, "specialist"
            return -10, "limited"

        p1_pen, p1_pen_kind = _recent_penalty(p1_recent_meta)
        p2_pen, p2_pen_kind = _recent_penalty(p2_recent_meta)
        total_recent_pen = p1_pen + p2_pen
        # Annotate the meta dicts so the frontend knows whether to draw amber
        # (limited / specialist) vs red (insufficient) warnings.
        if p1_recent_meta:
            p1_recent_meta["penalty"]      = p1_pen
            p1_recent_meta["penalty_kind"] = p1_pen_kind
        if p2_recent_meta:
            p2_recent_meta["penalty"]      = p2_pen
            p2_recent_meta["penalty_kind"] = p2_pen_kind
        if total_recent_pen:
            confidence = max(25, confidence + total_recent_pen)
            logger.warning(
                "RECENT_DATA_PENALTY | p1=%s(%+d) p2=%s(%+d) | total=%+d -> conf=%d",
                p1_pen_kind, p1_pen, p2_pen_kind, p2_pen, total_recent_pen, confidence,
            )

        # Sanity failure: projection fell outside realistic bounds → reduce confidence
        if result.get("sanity_failed"):
            confidence = max(25, confidence - 25)
            logger.warning(
                "Sanity check failed for %s %s — confidence reduced to %d",
                req.prop_type, proj_val, confidence,
            )

        lean = _resolve_lean(proj_val, req.prop_line, result.get("lean", ""))
        confidence = _edge_cap(confidence, proj_val, req.prop_line)

        # ── Feature 3 — data freshness / injury-withdrawal flag ──────────────
        # Advisory only — never blocks the projection. >21d → amber warning;
        # >45d → red warning AND a 15-point confidence reduction.
        try:
            from src import features as _feat
            _freshness = _feat.freshness_from_matches(p1_data.get("all_matches", []) or [])
        except Exception:  # noqa: BLE001
            _freshness = {"level": "", "message": "", "days_since_last": None, "confidence_penalty": 0}
        if _freshness.get("confidence_penalty"):
            confidence = max(15, confidence - _freshness["confidence_penalty"])

        # Archetypes
        p1_arch = classify_archetype(p1_all, req.tour)
        p2_arch = classify_archetype(p2_all, req.tour)

        # Resolve handedness from TA (may be None if TA unavailable)
        player_hand   = player_ta.get("handedness")   if player_ta   else None
        opponent_hand = opponent_ta.get("handedness") if opponent_ta else None

        # Handedness edge: True when one player is left-handed and the other right-handed
        handedness_edge = (
            bool(player_hand and opponent_hand and player_hand != opponent_hand)
        )

        # Opponent ace-against (aces the opponent concedes per match as a
        # returner ON THIS SURFACE). Sofascore is the real source — it's the
        # same value injected into p2_blended above and used by project_aces.
        # TA's ace_against_per_match is a placeholder that is always None
        # (TA match rows don't expose opponent aces), so reading TA first
        # left this field null and the "Aces Conceded/Match" row never
        # rendered in the opponent stat card.
        opponent_ace_against = (
            result.get("opp_ace_against")          # what the ace model actually used
            or p2_blended.get("ace_against_per_match")
            or p2_ss_ace_ag
            or (opponent_ta.get("ace_against_per_match") if opponent_ta else None)
        )

        # Data source transparency fields
        p1_ta_career   = p1_blended.get("_ta_career_matches", 0)
        p2_ta_career   = p2_blended.get("_ta_career_matches", 0)
        p1_ss_recent   = p1_blended.get("_ss_recent_matches", 0)
        p2_ss_recent   = p2_blended.get("_ss_recent_matches", 0)
        p1_fallback    = p1_blended.get("_surface_fallback", False)
        p2_fallback    = p2_blended.get("_surface_fallback", False)
        data_quality   = p1_blended.get("_data_quality", "moderate")
        p1_sack_count  = p1_blended.get("_sackmann_matches", 0)
        p2_sack_count  = p2_blended.get("_sackmann_matches", 0)
        p1_sack_weight = p1_blended.get("_sackmann_weight", 0.0)
        data_warning   = p1_blended.get("_data_warning")

        # ── Bar chart: last 5 matches overall (any surface) ─────────────────────
        # Shows the 5 most recent matches regardless of surface so the user can
        # see whether the prop line was met in recent form, not just on the
        # selected surface.  Matches with has_stats=False render as N/A gray bars.
        p1_chart_log = build_unified_chart_log(p1_unified)   # no surface filter
        chart_source = "sofascore" if any(
            m.get("source") == "sofascore" for m in p1_chart_log[:5]
        ) else "sackmann"

        logger.info(
            "CHART_UNIFIED | player=%s | chart_entries=%d | source=%s",
            req.player_name, len(p1_chart_log), chart_source,
        )

        # Build recent results strings for AI scouting context
        # Format: "W 6-3 6-4 vs Napolitano (Clay, Apr 13)"
        p1_recent = p1_data.get(f"{req.surface}_recent_results", [])
        p2_recent = p2_data.get(f"{req.surface}_recent_results", [])

        # AI scouting report
        h2h_total = h2h_summary.get("total", 0)
        scouting = generate_scouting_report(
            player_name=req.player_name or req.player_id,
            opponent_name=req.opponent_name or req.opponent_id,
            player_surface_stats=p1_s,
            opponent_surface_stats=p2_s,
            player_all_stats=p1_all,
            opponent_all_stats=p2_all,
            surface=req.surface,
            court=court_for_calc,
            prop_type=req.prop_type,
            projection={**result, "lean": lean, "confidence": confidence},
            player_arch=p1_arch,
            opponent_arch=p2_arch,
            h2h_summary=h2h_summary if h2h_total > 0 else None,
            player_hand=player_hand,
            opponent_hand=opponent_hand,
            player_recent_results=p1_recent or None,
            opponent_recent_results=p2_recent or None,
            ta_career_matches=p1_ta_career,
            data_quality=data_quality,
            player_recent_meta=p1_recent_meta,
            opponent_recent_meta=p2_recent_meta,
            prop_line=req.prop_line,
            player_surface_matches=p1_chart_log,
        )

        env_key   = result.get("environment") or detect_environment(p1_s, p2_s, surface=req.surface, tour=req.tour)
        env_label = ENVIRONMENT_LABELS.get(env_key, "Standard")

        # Serialise H2H DataFrames
        def _df_records(key):
            df = h2h_summary.get(key)
            if df is None or (hasattr(df, "empty") and df.empty):
                return []
            return df.to_dict(orient="records") if hasattr(df, "to_dict") else []

        return {
            "model_projection":     proj_val,
            "lean":                 lean,
            "confidence":           confidence,
            "confidence_breakdown": conf_result["breakdown"],
            # Feature 3 — data freshness / injury flag (advisory)
            "freshness_level":      _freshness.get("level", ""),
            "freshness_message":    _freshness.get("message", ""),
            "freshness_days":       _freshness.get("days_since_last"),
            "environment":          env_key,
            "environment_label":    env_label,
            "player_stats":         p1_s,
            "opponent_stats":       p2_s,
            "player_archetype":     p1_arch,
            "opponent_archetype":   p2_arch,
            # Handedness
            "player_handedness":    player_hand,
            "opponent_handedness":  opponent_hand,
            "handedness_edge":      handedness_edge,
            "opponent_ace_against": opponent_ace_against,
            # Data source transparency
            "ta_available":         bool(player_ta or opponent_ta),
            "player_ta_matches":    p1_ta_career,
            "opponent_ta_matches":  p2_ta_career,
            "player_ss_matches":    p1_ss_recent,
            "opponent_ss_matches":  p2_ss_recent,
            "player_surface_fallback":   p1_fallback,
            "opponent_surface_fallback": p2_fallback,
            "data_quality":         data_quality,
            # Limited-surface-data flags (ISSUE 2 — <10 surface matches)
            "player_limited_data":     player_limited_data,
            "opponent_limited_data":   opponent_limited_data,
            "player_surface_n":        p1_surface_n,
            "opponent_surface_n":      p2_surface_n,
            # Stale-cache freshness (ISSUE 1 — served from a prior snapshot)
            "data_stale":              p1_stale or p2_stale,
            # Projection quality flags
            "sanity_failed":        result.get("sanity_failed", False),
            "used_opp_tour_avg":    result.get("used_opp_tour_avg", False),
            "conv_rate_fallback":   result.get("conv_rate_fallback", False),
            "data_warning":         data_warning,
            # Break Points — surface vs overall breakdown + momentum (BP prop only)
            "bp_surf_conv_pct":      result.get("surf_conv_pct"),
            "bp_overall_conv_pct":   result.get("overall_conv_pct"),
            "bp_blended_conv_pct":   result.get("conv_rate_pct"),
            "bp_surf_opp_faced":     result.get("surf_opp_bp_faced"),
            "bp_overall_opp_faced":  result.get("overall_opp_bp_faced"),
            "bp_blended_opp_faced":  result.get("opp_bp_faced"),
            "bp_surf_conv_sample":   result.get("surf_conv_sample"),
            "bp_overall_conv_sample": result.get("overall_conv_sample"),
            "bp_opp_surf_sample":    result.get("opp_surf_sample"),
            "bp_surf_only_flag":     result.get("surf_only_flag", False),
            "bp_opp_projected":      result.get("opp_projected_bp_won"),
            "bp_momentum_bonus":     result.get("momentum_bonus"),
            "bp_surf_momentum_mult": result.get("surface_momentum_mult"),
            "bp_bo5_momentum_mult":  result.get("bo5_momentum_mult"),
            "bp_base_proj":          result.get("base_proj"),
            # Opponent serve-quality fields read directly (non-prefixed) by the
            # frontend BP stat comparison — these were previously not exposed,
            # leaving the Hold Rate and Serve Quality cells blank.
            "opp_hold_rate_pct":     result.get("opp_hold_rate_pct"),
            "opp_serve_tier":        result.get("opp_serve_tier"),
            # ── BP generated + games-won + server-quality badge (Parts 1/2/5) ──
            "bp_generated_per_match":     result.get("bp_generated_per_match"),
            "bp_generated_quality_adj":   result.get("bp_generated_quality_adj"),
            "bp_forward_server_factor":   result.get("forward_server_factor"),
            "player_service_games_won_pct": result.get("player_service_games_won_pct"),
            "player_return_games_won_pct":  result.get("player_return_games_won_pct"),
            "opp_service_games_won_pct":    result.get("opp_service_games_won_pct"),
            "opp_return_games_won_pct":     result.get("opp_return_games_won_pct"),
            "opp_server_quality_tier":      result.get("opp_server_quality_tier"),
            # Player return / blended conversion echoed for completeness
            "conv_rate_pct":         result.get("conv_rate_pct"),
            "player_bp_won_per_match":  result.get("player_bp_won_per_match"),
            "player_bp_opps_per_match": result.get("player_bp_opps_per_match"),
            "p1_ret":                result.get("p1_ret"),
            "p2_srv":                result.get("p2_srv"),
            "match_format":          result.get("match_format", match_fmt),
            "match_format_label":    match_format_label,
            "is_atp_gs":             _is_atp_gs,
            "qualifying":            _qualifying,
            # ── Expected-sets engine (all prop types expose these) ────────────
            "expected_sets":         result.get("expected_sets"),
            "competitiveness":       result.get("competitiveness"),
            "win_prob_gap":          result.get("win_prob_gap"),
            "p1_win_prob":           result.get("p1_win_prob"),
            "p2_win_prob":           result.get("p2_win_prob"),
            "avg_historical_sets":   result.get("avg_historical_sets"),
            "per_set_scale":         result.get("per_set_scale"),
            "is_bo5":                result.get("is_bo5", _is_atp_gs),
            "aces_per_set":          result.get("aces_per_set"),
            "df_per_set":            result.get("df_per_set"),
            "bp_won_per_set":        result.get("bp_won_per_set"),
            # ── Player Total Games Won breakdown (this prop only) ──────────────
            "games_held":            result.get("games_held"),
            "games_broken":          result.get("games_broken"),
            "player_hold_rate":      result.get("hold_rate"),
            "opp_hold_rate_g":       result.get("opp_hold_rate"),
            "player_break_rate":     result.get("break_rate"),
            "games_combined_ref":    result.get("games_combined"),
            # ── ST Pace Index / Surface Speed Tier ────────────────────────────
            "court_pace_index":      round(cpr, 1),
            "court_speed_tier":      _speed_tier,
            "court_st_source":       _st_source_label,   # 'st_live' | 'hardcoded'
            "court_yoy_note":        _yoy_note,
            # Reality-check flags (BP prop only)
            "bp_high_projection":    result.get("bp_high_projection", False),
            "bp_high_threshold":     result.get("bp_high_threshold"),
            "bp_momentum_capped":    result.get("bp_momentum_capped", False),
            "bp_momentum_cap":       result.get("bp_momentum_cap"),
            "bp_momentum_raw":       result.get("bp_momentum_raw"),
            # ── TA recent-window metadata (Prop Projection tab) ───────────────
            "player_ta_recent_tier":      p1_recent_meta["tier"],
            "player_ta_recent_matches":   p1_recent_meta["surface_n"],
            "player_ta_recent_all_n":     p1_recent_meta["all_surfaces_n"],
            "player_ta_recent_warning":   p1_recent_meta["warning"],
            "player_ta_recent_note":      p1_recent_meta["note"],
            "player_ta_penalty_kind":     p1_recent_meta.get("penalty_kind"),
            "player_ta_penalty":          p1_recent_meta.get("penalty", 0),
            "opponent_ta_recent_tier":    p2_recent_meta["tier"],
            "opponent_ta_recent_matches": p2_recent_meta["surface_n"],
            "opponent_ta_recent_all_n":   p2_recent_meta["all_surfaces_n"],
            "opponent_ta_recent_warning": p2_recent_meta["warning"],
            "opponent_ta_recent_note":    p2_recent_meta["note"],
            "opponent_ta_penalty_kind":   p2_recent_meta.get("penalty_kind"),
            "opponent_ta_penalty":        p2_recent_meta.get("penalty", 0),
            # Sofascore surface log (for bar chart; may fall back to Sackmann)
            "sofascore_surface_log": p1_chart_log,
            "chart_source":          chart_source,
            # Sackmann historical supplement metadata
            "player_sackmann_matches":   p1_sack_count,
            "opponent_sackmann_matches": p2_sack_count,
            "sackmann_weight":           p1_sack_weight,
            # Unified pool metadata (Sofascore + Sackmann merged)
            "player_unified_matches":    p1_unified_surf["matches"]    if p1_unified_surf else 0,
            "player_unified_win_rate":   p1_unified_surf["win_rate"]   if p1_unified_surf else None,
            "player_unified_sources":    p1_unified_surf["sources"]    if p1_unified_surf else [],
            "opponent_unified_matches":  p2_unified_surf["matches"]    if p2_unified_surf else 0,
            "opponent_unified_win_rate": p2_unified_surf["win_rate"]   if p2_unified_surf else None,
            "opponent_unified_sources":  p2_unified_surf["sources"]    if p2_unified_surf else [],
            "h2h_context": {
                "total":             h2h_total,
                "p1_wins":           h2h_summary.get("p1_wins", 0),
                "p2_wins":           h2h_summary.get("p2_wins", 0),
                "surface_matches":   h2h_surf_matches,
                "surface_p1_wins":   h2h_summary.get("surface_p1_wins", 0),
                "surface_p2_wins":   h2h_summary.get("surface_p2_wins", 0),
                "matches":           _df_records("matches"),
                "surface_matches_list": _df_records("surface_matches_df"),
                "ace_avg":           h2h_ace_avg,
                "df_avg":            h2h_df_avg,
                "bp_avg":            h2h_bp_avg,
                "games_avg":         h2h_games_avg,
                "date_range":        h2h_summary.get("date_range"),
                "surface_breakdown": h2h_summary.get("surface_breakdown", {}),
            },
            "plain_english_explanation": _build_explanation(
                req, result, lean, proj_val, req.prop_line, cpr,
                sackmann_weight=p1_sack_weight,
                sackmann_matches=p1_sack_count,
                ta_ss_matches=p1_ta_career,
            ),
            "ai_writeup": scouting,
            "raw_result": result,
            "player_surface_matches": _safe_matches(p1_surf_matches[:5]),
        }

    except Exception as e:
        logger.error("prop/calculate error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/h2h
# ---------------------------------------------------------------------------
@app.post("/api/h2h")
async def h2h_endpoint(req: H2HRequest):
    try:
        _h2h_loop = asyncio.get_event_loop()
        summary, stats = await asyncio.gather(
            _h2h_loop.run_in_executor(
                None, get_h2h_summary, req.tour, req.player1_id, req.player2_id, req.surface
            ),
            _h2h_loop.run_in_executor(
                None, get_h2h_stat_avg, req.tour, req.player1_id, req.player2_id, req.surface
            ),
        )

        def _df(key):
            df = summary.get(key)
            if df is None or (hasattr(df, "empty") and df.empty):
                return []
            return df.to_dict(orient="records") if hasattr(df, "to_dict") else []

        return {
            "total":                summary.get("total", 0),
            "p1_wins":              summary.get("p1_wins", 0),
            "p2_wins":              summary.get("p2_wins", 0),
            "surface_matches":      summary.get("surface_matches", 0),
            "surface_p1_wins":      summary.get("surface_p1_wins", 0),
            "surface_p2_wins":      summary.get("surface_p2_wins", 0),
            "h2h_rate":             summary.get("h2h_rate", 0.5),
            "matches":              _df("matches"),
            "surface_matches_list": _df("surface_matches_df"),
            "ace_avg":              stats.get("ace"),
            "df_avg":               stats.get("df"),
            "bp_avg":               stats.get("bp"),
            "games_avg":            stats.get("games_avg"),
            "date_range":           summary.get("date_range"),
            "surface_breakdown":    summary.get("surface_breakdown", {}),
        }
    except Exception as e:
        logger.error("h2h error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# (Board Optimizer removed)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
