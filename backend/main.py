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
import datetime
import os
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
    get_current_rankings,
    get_player_stats_by_surface,
    get_player_surface_hold,
    peek_surface_hold,
    get_player_next_match,
    get_match_moneyline_prob,
    get_match_total_games_line,
    get_h2h_summary,
    get_h2h_stat_avg,
    SofascoreBlockedError,
)

# FS market anchor (1A): weight on the de-vigged market win prob when blending
# with the model prob. blended = w·market + (1−w)·model.
WINPROB_MARKET_WEIGHT = 0.7
# Total Games market anchor: weight on the sportsbook's "Total games won" O/U line
# when blending with the model's total-games projection.
# blended = w·book_line + (1−w)·model_proj. |model − book| > this many games flags
# a divergence (the model disagrees materially with the sharp market).
TG_MARKET_WEIGHT = float(os.getenv("TG_MARKET_WEIGHT", "0.7") or "0.7")
TG_DIVERGENCE_GAMES = 3.0
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
from src.calculations.confidence import calculate_confidence, finalize_confidence
from src.calculations.props import (
    project_aces,
    project_double_faults,
    project_total_games,
    project_break_points,
    bp_scenario_mixture,
    bp_fair_line,
    project_player_games_won,
    project_fantasy_score,
    FS_DIVERGENCE_CONF_CAP,
    surface_affinity,
    generate_scouting_report,
    detect_environment,
    _server_quality_tier_sgw,
    ENVIRONMENT_LABELS,
    GRAND_SLAMS,
)
from src.constants import (
    COURT_CPR, CPR_NEUTRAL, GENERIC_SURFACE_CPR,
    get_speed_tier, ST_PACE_PREVIOUS_YEAR, ST_YOY_THRESHOLD,
    resolve_court_name, opponent_quality_weight, tier_proxy_weight, _norm_court,
    is_indoor_court, altitude_ace_factor,
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
    pick_group: str = "potd"             # "potd" or "3x"
    confidence_breakdown: str = ""       # JSON snapshot of the confidence components
    odds_type: str = "standard"          # "standard" or "demon"
    board_policy_version: str = "v2"     # board qualification policy in force


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


@app.get("/api/results/audit")
async def results_audit():
    """Read-only record-integrity forensic: EVERY pick row INCLUDING those
    excluded_from_record (which record_summary hides), with compact status fields.
    Lets an auditor reconcile excluded vs graded vs needs-review and detect any
    ID gaps that correspond to DELETED (vanished) rows rather than excluded ones.
    No writes."""
    from src import database
    picks = database.all_picks()
    return {
        "total": len(picks),
        "picks": [{
            "id": p.get("id"), "player": p.get("player"),
            "opponent": p.get("opponent"), "prop_type": p.get("prop_type"),
            "result": p.get("result"),
            "excluded": int(p.get("excluded_from_record") or 0),
            "group": p.get("pick_group"),
            "generated_at": p.get("generated_at"), "resolved_at": p.get("resolved_at"),
        } for p in picks],
    }


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


class ExcludeRequest(BaseModel):
    ids: list[int]
    excluded: bool = True


@app.post("/api/results/exclude")
async def results_exclude(req: ExcludeRequest):
    """Flag (or unflag) pick rows as excluded_from_record — superseded / duplicate
    picks kept in the DB for audit but removed from the public record + recaps."""
    from src import database
    return {"updated": database.set_excluded(req.ids, req.excluded)}


class SetLineRequest(BaseModel):
    id: int
    line: float | None = None
    original_line: float | None = None


@app.post("/api/results/setline")
async def results_setline(req: SetLineRequest):
    """Correct a pick's line / original_line when it moved between post and log."""
    from src import database
    return {"ok": database.set_line(req.id, req.line, req.original_line)}


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
    # ADMIN DIAGNOSTIC ONLY — when true the response carries `component_trace`,
    # every step of the calculation chain in order with its inputs, its own value
    # and the running result. Opt-in and off by default: no bot or frontend caller
    # sets it, so it can never reach a member-facing post. Permanent
    # instrumentation — future "is the projection right?" audits call this instead
    # of doing log archaeology.
    debug: bool = False


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

    # NEW SIGNALS — indoor (1), H2H psychological edge (2), tiebreak rate (3).
    for _sig_key in ("_indoor_note", "_h2h_psych_note", "_tiebreak_note"):
        if result.get(_sig_key):
            parts.append(result[_sig_key])

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
_BP_WARM_TASKS: set = set()   # strong refs to background cache-warming fetches


_BP_WARM_MAX_PER_REQUEST = 15   # cap background warm-ups queued per request


async def _bp_quality_adjusted_generated(surface_matches, surface, tour, budget=None):
    """Quality-of-server-adjusted BP generated. Weight each surface match's
    break-points-generated by the OPPONENT's surface hold (service games won %):
    >80% → 1.3 (harder chances, more meaningful), <65% → 0.7 (weak server), else
    1.0. Unresolved opponents take the neutral 1.0 weight.
    Returns (qadj_per_match, raw_per_match, resolved, total).

    CACHE-ONLY. This computation reads ONLY already-cached opponent holds. It
    never awaits a fetch, so its result is a pure function of cache state — not
    of how many network calls happened to land inside a timeout.

    Why: this used to await misses under an 8-second budget and take whatever
    resolved in time. That made the projection depend on fetch timing — the same
    class of defect as the stat-rich count drift. Measured: bp_generated_quality_adj
    read 8.5455 on a cold run and 8.5806 warm, moving a BP projection 6.1 -> 6.0
    between consecutive runs.

    Why not resolve-all (drop the budget and await everything): the POD scan makes
    ~130 of these, each fanning out opponent-hold fetches. Unbounded on a cold
    cache that is precisely the burst that exhausted the Decodo proxy and drew a
    Cloudflare ban on 2026-07-14. Determinism must not be bought with an outage.

    The rule this follows, established by the stat-rich fix: A NETWORK RACE NEVER
    DECIDES A NUMBER. A fetch may improve tomorrow's number by warming the cache;
    it can never change today's mid-computation.

    Misses are still queued for background warming, so the adjustment converges
    over days. `budget` is retained as an accepted-and-ignored kwarg for callers.
    """
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
        # Queue background warming and MOVE ON — the result of these fetches is
        # deliberately NOT read into this computation, no matter how fast they
        # land. They exist only to make the NEXT run's cache better. Capped per
        # request to bound proxy load; concurrency kept low for the same reason.
        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(3)

        async def _warm(oid):
            async with sem:
                try:
                    await loop.run_in_executor(
                        None, get_player_surface_hold, str(oid), surface, tour)
                except Exception:  # noqa: BLE001 — warming must never break a request
                    pass

        # A strong reference is required — asyncio only holds a weak ref, so
        # without _BP_WARM_TASKS the loop could GC these mid-flight and the cache
        # would never warm.
        for oid in misses[:_BP_WARM_MAX_PER_REQUEST]:
            t = asyncio.create_task(_warm(oid))
            _BP_WARM_TASKS.add(t)
            t.add_done_callback(_BP_WARM_TASKS.discard)

    num = den = 0.0
    for oid, bg in pairs:
        h = holds.get(oid)
        w = 1.3 if (h is not None and h > 80.0) else \
            0.7 if (h is not None and h < 65.0) else 1.0
        num += w * bg
        den += w
    qadj = round(num / den, 4) if den > 0 else raw_avg
    # Resolved fraction is the convergence metric: it should climb toward 1.0 over
    # days as background warming fills the cache. A persistently low fraction
    # means the adjustment is running mostly on neutral weights and is barely
    # adjusting anything — which should be VISIBLE, not silent.
    _frac = (len(holds) / len(uniq)) if uniq else 0.0
    logger.info(
        "BP_QADJ | surf=%s | opps=%d uniq_opponents=%d resolved=%d (%.0f%% of cache) "
        "| queued_for_warming=%d | raw=%.2f -> qadj=%.2f | CACHE-ONLY (no fetch awaited)",
        surface, len(pairs), len(uniq), len(holds), _frac * 100,
        min(len(misses), _BP_WARM_MAX_PER_REQUEST) if misses else 0,
        raw_avg or 0.0, qadj or 0.0,
    )
    return qadj, raw_avg, len(holds), len(uniq)


# ── Minimum-viable stat fallback (data-extraction reliability) ───────────────
# Tour-average estimates for the fundamental stats a player must always show.
# Used only as a last resort when surface + all-surface data can't produce a
# realistic value (e.g. a player with few stat-bearing matches whose Sofascore
# break-point detail is sparse, yielding a broken 0%).
_TOUR_AVG_MIN_VIABLE = {
    "ATP": {"service_games_won_pct": 80.0, "return_games_won_pct": 19.0,
            "bp_converted": 40.0, "first_serve_pct": 61.0,
            "return_bp_opportunities": 5.5},
    "WTA": {"service_games_won_pct": 67.0, "return_games_won_pct": 33.0,
            "bp_converted": 45.0, "first_serve_pct": 61.0,
            "return_bp_opportunities": 7.5},
}
# Below these a value is implausible for any professional → treat as extraction
# failure and fall back rather than display it.
_MIN_VIABLE_FLOOR = {
    "service_games_won_pct": 50.0, "return_games_won_pct": 3.0,
    "bp_converted": 8.0, "first_serve_pct": 45.0,
    "return_bp_opportunities": 0.5,
}


def _cascade_blend(surf_v, all_v, surf_n):
    """Weighted surface→all-surface blend by surface sample size (Step 3):
    10+ → 100% surface · 5-9 → 60/40 · 3-4 → 40/60 · 1-2 → 20/80 · 0 → all."""
    if surf_v is not None and surf_n >= 10:
        return surf_v
    ws = 0.6 if surf_n >= 5 else 0.4 if surf_n >= 3 else 0.2 if surf_n >= 1 else 0.0
    if surf_v is None:
        return all_v
    if all_v is None:
        return surf_v
    return ws * surf_v + (1.0 - ws) * all_v


def _fill_min_viable(s: dict, all_s: dict, surf_n: int, tour: str) -> list:
    """Cascade-blend the min-viable stats surface→all-surface, then floor to the
    tour average when still null / 0 / implausible. Mutates `s`; returns the list
    of stat keys that fell back to a tour-average estimate (for a UI indicator)."""
    total = max(int(s.get("matches_played", 0) or 0),
                int((all_s or {}).get("matches_played", 0) or 0))
    if total < 5:
        return []   # genuinely too little data — don't fabricate
    avgs = _TOUR_AVG_MIN_VIABLE.get((tour or "ATP").upper(), _TOUR_AVG_MIN_VIABLE["ATP"])
    fell_back = []
    for key, floor in _MIN_VIABLE_FLOOR.items():
        blended = _cascade_blend(s.get(key), (all_s or {}).get(key), surf_n)
        if blended is None or blended < floor:
            blended = avgs[key]
            fell_back.append(key)
        s[key] = round(blended, 2)
    return fell_back


# ── Depth hysteresis (stat-rich count noise guard) ───────────────────────────
# A player's completed match history cannot shrink, so their true stat-rich count
# on a surface is monotonically non-decreasing. Any READING that goes down is
# measurement error (a degraded/partial stats fetch), not new information.
#
# Deterministic event selection + never caching failed stat fetches (see
# sofascore_client) should make the counts reproducible on their own. This is the
# safety net for whatever residual noise survives: once a player has measured
# 15+ stat-rich matches on a surface, they KEEP deep status for that surface for
# 7 days even if a later fetch reads lower. Deep status is gained from any single
# healthy measurement and can only be lost by 7 days of persistently low reads,
# never by one bad fetch.
#
# Deliberately gates DEEP STATUS only, not the raw count — the count still feeds
# sample_size honestly; this just stops a threshold from flapping on noise.
# Underdog games-won UNDER penalty — see the application site near the lean
# resolution. Two tiers rather than a scale: the signal is directional, and a
# smooth ramp would imply a precision the affinity estimate doesn't have.
def _surf_stat_block(matches: list) -> dict:
    """win_rate / service+return games won % over a list of stat-rich match
    records. Rates are SUM/SUM (not a mean of per-match rates), so a match with
    more games carries proportionally more weight — and so a pooled multi-surface
    reference is automatically weighted by each surface's match count, which is
    what makes a 40-hard/6-grass player's reference hard-dominated."""
    if not matches:
        return {}
    wins = sum(1 for m in matches if m.get("won"))
    out = {"win_rate": round(wins / len(matches) * 100, 2)}
    for key, nf, df in (("service_games_won_pct", "service_games_won", "service_games"),
                        ("return_games_won_pct", "return_games_won", "return_games")):
        n = sum(m.get(nf) or 0 for m in matches if isinstance(m.get(nf), (int, float)))
        d = sum(m.get(df) or 0 for m in matches if isinstance(m.get(df), (int, float)))
        out[key] = round(n / d * 100, 2) if d > 0 else None
    return out


def _surface_ranking(pdata: dict, name: str) -> list:
    """Held-out affinity for EVERY surface, best to worst.

    Each surface is measured against the player's OTHER surfaces only — the
    surface being measured never appears in its own reference. One blended number
    hides the shape: 'clay is Jones's second surface at +1.2 while grass is her
    worst at -6.4' is the readable form, and it's what makes an affinity claim
    checkable rather than asserted."""
    rich = [m for m in (pdata.get("all_matches") or [])
            if m.get("surface") and isinstance(m.get("aces"), (int, float))]
    rank = []
    for surf in ("Hard", "Clay", "Grass"):
        same = [m for m in rich if m["surface"] == surf]
        held = [m for m in rich if m["surface"] != surf]
        d = {"player_name": name,
             "surface_stat_n": len(same), "heldout_stat_n": len(held)}
        d.update(_surf_stat_block(same))
        for k, v in _surf_stat_block(held).items():
            d["heldout_" + k] = v
        rank.append({"surface": surf, "affinity": surface_affinity(d),
                     "stat_n": len(same)})
    # Best first; unmeasurable surfaces sort last.
    rank.sort(key=lambda r: (r["affinity"] is None, -(r["affinity"] or 0.0)))
    return rank


UNDERDOG_AFFINITY_MIN_GAP    = 4.0    # meaningful affinity edge to the underdog
UNDERDOG_AFFINITY_STRONG_GAP = 8.0    # strong edge -> the larger penalty
UNDERDOG_UNDER_PENALTY_MIN   = 8
UNDERDOG_UNDER_PENALTY_MAX   = 12

_DEEP_MIN_MATCHES = 15
_DEEP_STATUS_TTL  = 7 * 24 * 3600
_DEEP_STATUS: dict = {}          # (player_id, surface) -> ts last measured deep


def _deep_with_hysteresis(pid, surface: str, measured_n, who: str = "") -> bool:
    """Deep status for (player, surface), with 7-day retention. Logs a retention
    so a play surviving on a remembered measurement is never silent."""
    key = (str(pid), (surface or "").lower())
    now = time.time()
    if isinstance(measured_n, (int, float)) and measured_n >= _DEEP_MIN_MATCHES:
        _DEEP_STATUS[key] = now
        return True
    ts = _DEEP_STATUS.get(key)
    if ts is not None and (now - ts) < _DEEP_STATUS_TTL:
        logger.info(
            "DEPTH_HYSTERESIS | %s pid=%s surface=%s | measured=%s (<%d) but measured "
            "deep %.1fh ago — RETAINING deep status (a completed match history cannot "
            "shrink, so the low read is a degraded fetch, not new information)",
            who, pid, surface, measured_n, _DEEP_MIN_MATCHES, (now - ts) / 3600.0,
        )
        return True
    return False


# ── Opponent-quality weighting (Improvement 1) ───────────────────────────────
_QW_COUNT_STATS = {                       # weighted MEAN of per-match value
    "aces": "aces", "double_faults": "double_faults",
    "return_bp_opportunities": "return_bp_opportunities",   # BP generated
}
_QW_RATE_STATS = {                        # weighted SUM/SUM: (numerator, denominator)
    "bp_converted":          ("bp_converted_count", "return_bp_opportunities"),
    "service_games_won_pct": ("service_games_won", "service_games"),
    "return_games_won_pct":  ("return_games_won", "return_games"),
}


def _date_pm1(d):
    """The date and ±1 day as YYYY-MM-DD strings (Sofascore↔Sackmann match window)."""
    try:
        base = datetime.datetime.strptime(str(d)[:10], "%Y-%m-%d")
        return [(base + datetime.timedelta(days=k)).strftime("%Y-%m-%d") for k in (0, -1, 1)]
    except Exception:  # noqa: BLE001
        return [str(d)[:10]] if d else []


def _opp_quality_weighted(surf_matches, rankings):
    """Stamp each surface match with the opponent's CURRENT Sofascore rank
    (looked up by opponent_id; tournament-tier weight when the opponent isn't in
    the rankings list — retired/unranked/ID mismatch) and compute raw +
    opponent-quality-weighted averages for the key stats.
    Returns ({stat: {raw, weighted}}, ranking_match_rate_pct).

    RANK SOURCE — opponent rank weighting sources ONLY from the Sofascore current-
    rankings cache (get_current_rankings: ATP + WTA singles, ranks 1-500 per tour,
    matched on Sofascore player ID, refreshed weekly), with tier_proxy_weight() as
    the fallback for anyone outside it. Jeff Sackmann's rank CSVs are NOT a source
    here and never fire: load_player_sackmann_data() is a hard no-op since the
    tennis_atp/tennis_wta repos went private (every CSV 404s), so any
    *_sackmann_matches=0 in the logs/response is that dead path, not a lookup miss.
    The proxy is expected on ITF/challenger boards, where opponents rank past 500
    and Sofascore simply doesn't publish a number — that is acceptable degradation,
    not a bug to chase."""
    ms = [m for m in (surf_matches or []) if isinstance(m, dict)]
    if len(ms) < 5:                       # too few matches → weighting is noise
        return {}, 0.0
    rankings = rankings or {}
    rows, ranked = [], 0
    for m in ms:
        oid = m.get("opponent_id")
        rank = None
        if oid is not None:
            try:
                rank = rankings.get(int(oid))
            except (TypeError, ValueError):
                rank = None
        if rank is not None:
            w = opponent_quality_weight(rank)        # rank tier (1-20→1.4 … >150→0.65)
            ranked += 1
        else:
            w = tier_proxy_weight(m.get("comp_tier"), m.get("tournament"))  # fallback
        # _opp_rank / _opp_weight reused by the recent-form pull (Imp 4)
        m["_opp_rank"] = rank if rank is not None else 999
        m["_opp_weight"] = w
        rows.append((m, w))
    # Coverage is reported over the matches the weighting ACTUALLY aggregates —
    # i.e. those carrying a numeric stat field. Stat-poor matches are skipped by
    # the isinstance() guards in the loops below, so counting them in the
    # denominator understates coverage badly: a player with 414 raw clay matches
    # but 39 stat-rich ones reported 37.4% when real coverage on the matches that
    # contribute was 61.5%. Fall back to the full list if no match carries stats,
    # so the figure is never divided by zero.
    _contrib = [m for m, _w in rows
                if any(isinstance(m.get(f), (int, float)) for f in _QW_COUNT_STATS.values())]
    _denom = _contrib or ms
    _ranked_contrib = sum(
        1 for m in _denom
        if m.get("_opp_rank") is not None and m.get("_opp_rank") != 999
    )
    match_rate = round(_ranked_contrib / len(_denom) * 100, 1)
    logger.info(
        "QW_COVERAGE | %d stat-rich of %d raw matches | %.0f%% via current ranking, "
        "rest via tournament-tier fallback",
        len(_contrib), len(ms), match_rate,
    )
    out = {}
    for key, fld in _QW_COUNT_STATS.items():
        wn = wd = rn = rd = 0.0
        for m, w in rows:
            v = m.get(fld)
            if isinstance(v, (int, float)):
                wn += v * w; wd += w; rn += v; rd += 1
        if wd > 0:
            out[key] = {"raw": round(rn / rd, 2), "weighted": round(wn / wd, 2)}
    for key, (nf, dnf) in _QW_RATE_STATS.items():
        wn = wd = rn = rd = 0.0
        for m, w in rows:
            n, d = m.get(nf), m.get(dnf)
            if isinstance(n, (int, float)) and isinstance(d, (int, float)) and d > 0:
                wn += n * w; wd += d * w; rn += n; rd += d
        if wd > 0 and rd > 0:
            out[key] = {"raw": round(rn / rd * 100, 1), "weighted": round(wn / wd * 100, 1)}
    return out, match_rate


def _apply_quality_weighting(s, qw):
    """Scale the blended stat by the opponent-quality ratio (weighted/raw),
    capped ±20% so the multi-tier blend stays the base. Stores *_raw_avg and
    *_weighted_avg for display; the projection then reads the adjusted value."""
    for key, vals in (qw or {}).items():
        raw, wt = vals.get("raw"), vals.get("weighted")
        s[f"{key}_raw_avg"] = raw
        s[f"{key}_weighted_avg"] = wt
        if raw and wt and raw > 0 and isinstance(s.get(key), (int, float)):
            ratio = max(0.80, min(1.20, wt / raw))
            s[key] = round(s[key] * ratio, 2)


def _pstdev(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    mu = sum(vals) / n
    return (sum((x - mu) ** 2 for x in vals) / n) ** 0.5


def _retirement_risk(matches):
    """Improvement 5 — retirement/walkover pattern (Sofascore status 91/92/93).
    RECENCY-DECAYED: a DNF in the last 10 matches counts fully, 11-30 back at
    half weight, >30 back not at all — 2 DNFs spread across 50 matches for a
    veteran is normal wear, not a pattern. Flag when the weighted count >= 2.
    Returns (is_risk, base_confidence_penalty, pct_completed). The caller scales
    the penalty by how early the prop resolves."""
    ms = (matches or [])[:50]
    if len(ms) < 10:
        return False, 0, None
    weighted = raw = 0.0
    for i, m in enumerate(ms):           # newest first
        if m.get("player_retired"):
            raw += 1
            if i < 10:
                weighted += 1.0
            elif i < 30:
                weighted += 0.5
            # index >= 30: does not count toward the flag
    pct = round((1 - raw / len(ms)) * 100, 1)
    flag = weighted >= 2.0
    return flag, (-10 if flag else 0), pct


def _tiebreak_rate(surf_log):
    """Surface tiebreak rate (NEW SIGNAL 3) — % of sets across the player's
    matches on this surface that reached a tiebreak. A high rate (>30%) means
    the player consistently holds even under pressure (serve dominance).
    Returns None when there are too few sets for a stable rate."""
    sp = tb = 0
    for m in (surf_log or []):
        if not isinstance(m, dict):
            continue
        _s, _t = m.get("sets_played"), m.get("tiebreak_sets")
        if isinstance(_s, (int, float)) and isinstance(_t, (int, float)):
            sp += _s
            tb += _t
    if sp < 6:
        return None
    return round(tb / sp * 100, 1)


def _in_tournament_blend(s, surf_log, current_tournament, weight=0.35):
    """Improvement 2 — current-tournament form is the highest-weight signal
    (exact same courts/conditions). When the player has ≥2 matches in the
    upcoming event (same tournament + year), blend their in-tournament averages
    into the stat at 35%. Returns True if applied."""
    if not current_tournament:
        return False
    ct = _norm_court(current_tournament)
    cyear = str(datetime.datetime.now().year)
    it = [m for m in (surf_log or [])
          if isinstance(m, dict) and ct and ct in _norm_court(m.get("tournament", ""))
          and str(m.get("date", ""))[:4] == cyear]
    if len(it) < 2:
        return False
    for key, fld in _QW_COUNT_STATS.items():
        vals = [m.get(fld) for m in it if isinstance(m.get(fld), (int, float))]
        if vals and isinstance(s.get(key), (int, float)):
            s[key] = round((1 - weight) * s[key] + weight * (sum(vals) / len(vals)), 2)
    for key, (nf, dnf) in _QW_RATE_STATS.items():
        n = d = 0.0
        for m in it:
            a, b = m.get(nf), m.get(dnf)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > 0:
                n += a; d += b
        if d > 0 and isinstance(s.get(key), (int, float)):
            s[key] = round((1 - weight) * s[key] + weight * (n / d * 100), 1)
    return True


_RECENT_FORM_STAT = {                     # prop → per-match stat for the form pull
    "Aces": "aces", "Double Faults": "double_faults",
    "Total Games": "total_match_games", "Break Points Won": "bp_converted_count",
    "Player Total Games Won": "total_games_won",
}


def _recent_form_pull(proj_val, surf_matches, prop_type, weight=0.30):
    """Pull the projection toward the player's recent same-surface average for
    the prop's stat (Improvement 4), with each of the last ~5 weighted by the
    opponent's rank (`_opp_rank`, stamped by _opp_quality_weighted): 3 unders vs
    elite returners pull harder than 3 vs weak ones. Returns (tempered, avg)."""
    key = _RECENT_FORM_STAT.get(prop_type)
    if not key or not isinstance(proj_val, (int, float)):
        return proj_val, None
    wn = wd = 0.0
    for m in (surf_matches or [])[:5]:
        v = m.get(key) if isinstance(m, dict) else None
        if not isinstance(v, (int, float)):
            continue
        w = m.get("_opp_weight")
        if not isinstance(w, (int, float)):
            w = opponent_quality_weight(m.get("_opp_rank"))
        wn += v * w; wd += w
    if wd <= 0:
        return proj_val, None
    recent_avg = wn / wd
    return round((1 - weight) * proj_val + weight * recent_avg, 1), round(recent_avg, 1)


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
        # Meeting counts behind those averages — the H2H sample-gate inputs.
        # stat_n backs ace/df (needs parsed statistics); games_n backs the total-
        # games average (score-derived, so usually the larger sample).
        h2h_stat_n       = h2h_stats.get("stat_n", 0) or 0
        h2h_games_n      = h2h_stats.get("games_n", 0) or 0
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

        # Inject SS ace-against-per-match (a player's ACES ALLOWED) into the
        # blended dicts so project_aces can read it directly from the stats
        # without a TA dependency. Prefer the surface-specific figure; fall back
        # to the All-surface figure when the surface pool is thin (otherwise the
        # value silently drops to a tour-average default and understates a
        # strong/weak returner). Injected for BOTH players so each side's own
        # aces-allowed is available for display and either projection direction.
        p1_ss_ace_ag = (p1_data.get(f"{req.surface}_ace_against_per_match")
                        or p1_data.get("All_ace_against_per_match"))
        p2_ss_ace_ag = (p2_data.get(f"{req.surface}_ace_against_per_match")
                        or p2_data.get("All_ace_against_per_match"))
        if p1_ss_ace_ag is not None:
            p1_blended["ace_against_per_match"] = p1_ss_ace_ag
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

        # ── Min-viable stat fallback (Steps 3 + 5): cascade surface→all-surface
        # then tour-average floor, so a fundamental stat (e.g. return games won)
        # never displays as a broken 0% for a player with real history. Runs
        # before the projection so corrected inputs flow into it too.
        _p1_surf_n = int((p1_data.get(req.surface) or {}).get("matches_played", 0) or 0)
        _p2_surf_n = int((p2_data.get(req.surface) or {}).get("matches_played", 0) or 0)
        p1_gw_est = _fill_min_viable(p1_s, p1_all, _p1_surf_n, req.tour)
        p2_gw_est = _fill_min_viable(p2_s, p2_all, _p2_surf_n, req.tour)
        if p1_gw_est or p2_gw_est:
            logger.info("MIN_VIABLE_FALLBACK | p1=%s est=%s | p2=%s est=%s",
                        req.player_name, p1_gw_est, req.opponent_name, p2_gw_est)

        # ── Opponent-quality weighting (Improvement 1): discount stats padded
        # vs weak fields. Adjusts the blended stat by the weighted/raw ratio so
        # the projection reads a quality-aware figure; raw kept for display.
        _p1_surf_log = p1_data.get(f"{req.surface}_matches", []) or []
        _p2_surf_log = p2_data.get(f"{req.surface}_matches", []) or []
        try:
            _rankings = get_current_rankings()        # {player_id: rank}, cached 7d
        except Exception:  # noqa: BLE001
            _rankings = {}
        _p1_qw, p1_qw_match_rate = _opp_quality_weighted(_p1_surf_log, _rankings)
        _p2_qw, p2_qw_match_rate = _opp_quality_weighted(_p2_surf_log, _rankings)
        _apply_quality_weighting(p1_s, _p1_qw)
        _apply_quality_weighting(p2_s, _p2_qw)
        logger.info("QUALITY_WEIGHT | p1=%s ranking_rate=%.0f%% | p2=%s rate=%.0f%%",
                    req.player_name, p1_qw_match_rate, req.opponent_name, p2_qw_match_rate)

        def _qw_inflated(s):
            for _k in ("return_bp_opportunities", "bp_converted"):
                _raw, _wt = s.get(f"{_k}_raw_avg"), s.get(f"{_k}_weighted_avg")
                if _raw and _wt and _raw > 0 and _wt < _raw * 0.85:
                    return True
            return False
        p1_stats_inflated = _qw_inflated(p1_s)

        # ── In-tournament form tier (Improvement 2): same courts/conditions ──
        if _in_tournament_blend(p1_s, _p1_surf_log, court_for_calc):
            logger.info("IN_TOURNAMENT | %s blended current-event form", req.player_name)
        _in_tournament_blend(p2_s, _p2_surf_log, court_for_calc)

        # ════════════════════════════════════════════════════════════════════
        # NEW SIGNALS — indoor/outdoor (1), H2H psychological edge (2),
        # tiebreak-rate serve dominance (3). Additive layers; computed here so
        # every downstream consumer (forward factor, projection, displays) sees
        # them. All three log their evaluation for verification.
        # ════════════════════════════════════════════════════════════════════
        # Signal 1 — indoor hard plays faster (no wind, truer bounce) → servers.
        is_indoor_hard = (req.surface == "Hard" and
                          is_indoor_court(court_for_calc or req.court or ""))
        logger.info("SIGNAL1_INDOOR | court=%r surface=%s -> indoor_hard=%s",
                    court_for_calc or req.court, req.surface, is_indoor_hard)
        # Altitude — thin air, faster serves → higher ace projection (aces only).
        # Additive ace modifier; does NOT change the CPI. Applies on any surface.
        alt_factor, alt_pct = altitude_ace_factor(court_for_calc or req.court or "")
        is_altitude = alt_pct > 0
        logger.info("ALTITUDE | court=%r -> altitude=%s (+%d%% aces)",
                    court_for_calc or req.court, is_altitude, alt_pct)

        # Signal 3 — surface tiebreak rate (serve dominance) for both players.
        p1_tb_rate = _tiebreak_rate(_p1_surf_log)
        p2_tb_rate = _tiebreak_rate(_p2_surf_log)
        logger.info("SIGNAL3_TIEBREAK | %s tb_rate=%s%% | %s tb_rate=%s%%",
                    req.player_name, p1_tb_rate, req.opponent_name, p2_tb_rate)

        # Signal 2 — H2H psychological edge on THIS surface (selected = p1).
        _h2h_surf_n = h2h_summary.get("surface_matches", 0) or 0
        _h2h_gap    = ((h2h_summary.get("surface_p1_wins", 0) or 0)
                       - (h2h_summary.get("surface_p2_wins", 0) or 0))
        h2h_psych_mult = 1.0
        h2h_psych_dir  = None
        if _h2h_surf_n >= 4 and abs(_h2h_gap) >= 3:
            _boost = 0.04 if abs(_h2h_gap) <= 5 else 0.07
            if _h2h_gap >= 3:      # p1 owns the matchup → converts better
                h2h_psych_mult, h2h_psych_dir = 1.0 + _boost, "leads"
            else:                  # p2 owns the matchup → p1 creates fewer chances
                h2h_psych_mult, h2h_psych_dir = 1.0 - _boost, "trails"
        logger.info("SIGNAL2_H2H_PSYCH | %s surface H2H gap=%+d (n=%d) -> dir=%s BP_mult=x%.2f",
                    req.player_name, _h2h_gap, _h2h_surf_n, h2h_psych_dir, h2h_psych_mult)

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
            # Surface-affinity inputs: each surface stat needs its all-surface
            # twin so affinity can measure the player against THEIR OWN baseline
            # (see surface_affinity in props.py). Without these the affinity score
            # silently falls back to win rate alone.
            _s["overall_service_games_won_pct"]       = _at.get("service_games_won_pct")
            _s["overall_return_games_won_pct"]        = _at.get("return_games_won_pct")
            _s["overall_first_serve_pts_won"]         = _at.get("first_serve_pts_won")
            _s["overall_second_serve_pts_won"]        = _at.get("second_serve_pts_won")
            _s["overall_return_first_serve_pts_won"]  = _at.get("return_first_serve_pts_won")
            _s["overall_return_second_serve_pts_won"] = _at.get("return_second_serve_pts_won")
            _s["competition_level"] = (_rec.get("competition_level")
                                       or _3y.get("competition_level")
                                       or _at.get("competition_level"))

        # ── HELD-OUT surface baseline (affinity reference) ───────────────────
        # Affinity asks "is this surface better FOR THIS PLAYER than their norm".
        # Measuring against overall_* is circular: overall INCLUDES the surface
        # being measured, so a clay specialist's clay results inflate the very
        # baseline they're compared to and every affinity shrinks toward zero.
        # The honest reference is the player's OTHER surfaces, held out.
        # Built from the raw per-match records (stat-rich only — a match with no
        # parsed statistics tells us nothing about how the surface suits them).
        for _s, _pdata in ((p1_s, p1_data), (p2_s, p2_data)):
            _held = [m for m in (_pdata.get("all_matches") or [])
                     if m.get("surface") and m.get("surface") != req.surface
                     and isinstance(m.get("aces"), (int, float))]
            _same = [m for m in (_pdata.get("all_matches") or [])
                     if m.get("surface") == req.surface
                     and isinstance(m.get("aces"), (int, float))]
            _s["surface_stat_n"] = len(_same)
            _s["heldout_stat_n"] = len(_held)
            if _held:
                _wins = sum(1 for m in _held if m.get("won"))
                _s["heldout_win_rate"] = round(_wins / len(_held) * 100, 2)
                for _pct_key, _num_f, _den_f in (
                        ("heldout_service_games_won_pct", "service_games_won", "service_games"),
                        ("heldout_return_games_won_pct",  "return_games_won",  "return_games")):
                    _n = sum(m.get(_num_f) or 0 for m in _held
                             if isinstance(m.get(_num_f), (int, float)))
                    _d = sum(m.get(_den_f) or 0 for m in _held
                             if isinstance(m.get(_den_f), (int, float)))
                    _s[_pct_key] = round(_n / _d * 100, 2) if _d > 0 else None
            # Full best-to-worst surface ranking (CHANGE 2) — each surface held
            # out of its own reference. Stored so it's auditable, not just logged.
            _s["surface_ranking"] = _surface_ranking(_pdata, _s.get("player_name", "?"))
            # SINGLE SOURCE for affinity. The ranking computes both sides of every
            # delta from RAW match records; _s's own surface stats have been
            # through quality-weighting, so letting the differential re-derive
            # affinity from _s compared a quality-weighted surface figure against
            # a raw held-out reference — apples to oranges, and it produced a
            # different number than the ranking for the same player+surface
            # (Urgesi clay: ranking -2.10, differential -15.09). Quality weighting
            # is deliberately excluded from affinity: it adjusts for OPPONENT
            # strength, which is exactly the thing affinity must not absorb — a
            # surface preference is about the player, not who they happened to face.
            _s["surface_affinity_precomputed"] = next(
                (r["affinity"] for r in _s["surface_ranking"]
                 if r["surface"] == req.surface), None)
            _rank_txt = " > ".join(
                "%s %s%s" % (r["surface"],
                             ("%+.1f" % r["affinity"]) if r["affinity"] is not None else "n/a",
                             "(n=%d)" % r["stat_n"])
                for r in _s["surface_ranking"])
            logger.info(
                "AFFINITY_BASELINE | %s | measuring %s: stat-rich=%d, held-out "
                "other-surface stat-rich=%d | held-out ref WR=%s SGW=%s RGW=%s | "
                "surface ranking (best->worst): %s",
                _s.get("player_name", "?"), req.surface, len(_same), len(_held),
                _s.get("heldout_win_rate"), _s.get("heldout_service_games_won_pct"),
                _s.get("heldout_return_games_won_pct"), _rank_txt,
            )

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

        # Component trace — populated only when req.debug is set (admin
        # diagnostic). None on every normal call, which makes each _trace() call
        # inside the projectors a no-op, so this costs nothing in production.
        _ctrace = [] if req.debug else None

        # Run projection
        if req.prop_type == "Aces":
            result = project_aces(
                p1_s, p2_s, court_for_calc, h2h_ace_avg, cpr_override=cpr,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface,
                match_format=match_fmt,
                trace=_ctrace,
                h2h_stat_n=h2h_stat_n,
            )
        elif req.prop_type == "Double Faults":
            result = project_double_faults(
                p1_s, p2_s, h2h_df_avg,
                h2h_stat_n=h2h_stat_n,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface,
                match_format=match_fmt,
                court=court_for_calc,
            )
        elif req.prop_type == "Total Games":
            result = project_total_games(
                p1_s, p2_s, req.surface, h2h_games_avg,
                h2h_games_n=h2h_games_n,
                tour=req.tour, court=court_for_calc,
                match_format=match_fmt,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
            )
            # ── SPORTSBOOK TOTAL-GAMES ANCHOR ────────────────────────────────
            # Sofascore's "Total games won" O/U is the sharp market's expected
            # match total. Blend the model projection toward the book line
            # (blended = w·book + (1−w)·model) so the number tracks the market
            # while still voicing a model edge. No book market -> model-only,
            # flagged unanchored. A gap of > TG_DIVERGENCE_GAMES between model
            # and book raises a divergence flag (surfaced, not auto-suppressed).
            _tg_model_proj = float(result.get("projection") or 0.0)
            _tg_line = await asyncio.to_thread(
                get_match_total_games_line,
                req.player_id, req.opponent_id, req.tour)
            _tg_book = (_tg_line or {}).get("book_line")
            if _tg_book is not None and _tg_model_proj > 0:
                _tg_blended = (TG_MARKET_WEIGHT * float(_tg_book)
                               + (1.0 - TG_MARKET_WEIGHT) * _tg_model_proj)
                result["projection"] = round(_tg_blended, 1)
                result["tg_book_line"] = float(_tg_book)
                result["tg_model_proj"] = round(_tg_model_proj, 1)
                result["tg_blended_proj"] = round(_tg_blended, 1)
                result["tg_book_over_prob"] = _tg_line.get("over_prob")
                result["tg_book_under_prob"] = _tg_line.get("under_prob")
                result["tg_anchored"] = True
                # Edge vs the PrizePicks line the user is actually betting.
                if req.prop_line is not None:
                    result["tg_book_edge"] = round(_tg_blended - float(req.prop_line), 2)
                result["tg_divergent"] = abs(_tg_model_proj - float(_tg_book)) > TG_DIVERGENCE_GAMES
            else:
                result["tg_anchored"] = False
                result["tg_divergent"] = False
        elif req.prop_type == "Fantasy Score":
            # FS is a COMPOSITE: it needs the player's ace + DF projections and the
            # match set structure (win prob + expected sets), then runs the
            # scenario mixture. The component projectors are called here purely to
            # feed FS — none of their own math changes.
            _ace_r = project_aces(
                p1_s, p2_s, court_for_calc, h2h_ace_avg, cpr_override=cpr,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface, match_format=match_fmt,
                h2h_stat_n=h2h_stat_n,
            )
            _df_r = project_double_faults(
                p1_s, p2_s, h2h_df_avg, h2h_stat_n=h2h_stat_n,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                tour=req.tour, surface=req.surface, match_format=match_fmt,
                court=court_for_calc,
            )
            _tg_r = project_total_games(
                p1_s, p2_s, req.surface, h2h_games_avg, h2h_games_n=h2h_games_n,
                tour=req.tour, court=court_for_calc, match_format=match_fmt,
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
            )
            # ── MARKET WIN-PROB ANCHOR (1A) ──────────────────────────────────
            # The model systematically underrates favourites. De-vig the two-way
            # moneyline of the upcoming Sofascore event into a market win prob and
            # blend: blended = w·market + (1−w)·model. No moneyline -> model-only,
            # flagged 'unanchored' (confidence capped at 70 downstream). The blended
            # prob feeds the mixture, so P(3 sets|win/lose) and the whole scenario
            # distribution shift consistently.
            _model_wp = (_tg_r.get("p1_win_prob") or 50.0) / 100.0
            _ml = await asyncio.to_thread(
                get_match_moneyline_prob,
                req.player_id, req.opponent_id, req.tour)
            _mkt_wp = _ml.get("market_p1") if isinstance(_ml, dict) else None
            _anchored = isinstance(_mkt_wp, (int, float))
            if _anchored:
                _blended_wp = (WINPROB_MARKET_WEIGHT * _mkt_wp
                               + (1.0 - WINPROB_MARKET_WEIGHT) * _model_wp)
            else:
                _blended_wp = _model_wp
            logger.info("FS_WINPROB | %s | model=%.3f market=%s blended=%.3f anchored=%s (%s)",
                        req.player_name or "player", _model_wp,
                        ("%.3f" % _mkt_wp) if _anchored else "None",
                        _blended_wp, _anchored,
                        (_ml.get("reason") if isinstance(_ml, dict) else "no data") or "ok")
            result = project_fantasy_score(
                p_sel=max(0.02, min(0.98, _blended_wp)),
                ace_proj=_ace_r.get("projection"),
                df_proj=_df_r.get("projection"),
                expected_sets=_tg_r.get("expected_sets"),
                prop_line=req.prop_line,
                tour=req.tour, match_format=match_fmt,
                player_name=req.player_name or "player",
                trace=_ctrace,
            )
            # Carry win prob forward for the guard/display, like PTGW does.
            for _k in ("p1_win_prob", "p2_win_prob"):
                result.setdefault(_k, _tg_r.get(_k))
            result["fs_model_wp"] = round(_model_wp, 4)
            result["fs_market_wp"] = round(_mkt_wp, 4) if _anchored else None
            result["fs_blended_wp"] = round(_blended_wp, 4)
            result["fs_anchored"] = _anchored
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
            _q_res = _q_tot = 0
            bp_forward_factor = 1.0
            if is_bp_prop:
                _bp_surf_matches = p1_data.get(f"{req.surface}_matches", []) or []
                bp_qadj, bp_raw_gen, _q_res, _q_tot = await _bp_quality_adjusted_generated(
                    _bp_surf_matches, req.surface, req.tour,
                )
                # Cache depth behind the quality adjustment — surfaced so a
                # thin-cache computation is visible in the trace rather than
                # silently running on neutral weights.
                if _ctrace is not None:
                    _ctrace.append({
                        "step": len(_ctrace) + 1, "name": "quality_adj_cache_depth",
                        "inputs": {"opponents_resolved": _q_res,
                                   "opponents_total": _q_tot,
                                   "raw_bp_generated": bp_raw_gen},
                        "value": bp_qadj, "running": bp_qadj,
                        "note": ("CACHE-ONLY: %d/%d opponents resolved from cache "
                                 "(%.0f%%); the rest take a neutral 1.0 weight. No "
                                 "fetch is awaited, so this is a pure function of "
                                 "cache state, never of fetch timing. Feeds C1."
                                 % (_q_res, _q_tot,
                                    (_q_res / _q_tot * 100) if _q_tot else 0.0)),
                    })
                _opp_sgw = (p2_s or {}).get("service_games_won_pct")
                if _opp_sgw is None:
                    _opp_sgw = (p2_data.get("All") or {}).get("service_games_won_pct")
                if _opp_sgw is not None and _opp_sgw > 80.0:
                    bp_forward_factor = 0.85    # strong server → fewer chances created
                elif _opp_sgw is not None and _opp_sgw < 65.0:
                    bp_forward_factor = 1.10    # weak server → more chances created
                # Imp 1 forward factor: harder to create chances vs elite
                # opponents. Prefer the request rank; fall back to current rankings.
                _cur_opp_rank = req.opponent_rank
                if _cur_opp_rank is None and req.opponent_id:
                    try:
                        _cur_opp_rank = _rankings.get(int(req.opponent_id))
                    except (TypeError, ValueError):
                        _cur_opp_rank = None
                if _cur_opp_rank is not None and _cur_opp_rank <= 20:
                    bp_forward_factor *= 0.90
                elif _cur_opp_rank is not None and _cur_opp_rank > 100:
                    bp_forward_factor *= 1.10

                # Signal 3 — opponent tiebreak rate as a BP-opportunity signal.
                # A very low tiebreak rate (<15%) means the opponent's sets end
                # decisively: cross-reference hold % to tell vulnerable from
                # dominant. Low TB + low hold → broken often → more chances;
                # low TB + high hold → crushing on serve → fewer chances.
                if p2_tb_rate is not None and p2_tb_rate < 15.0 and _opp_sgw is not None:
                    if _opp_sgw < 65.0:
                        bp_forward_factor *= 1.08
                        logger.info("SIGNAL3_BP_OPP | opp tb=%.0f%% + low hold %.0f%% -> vulnerable, +8%% opps", p2_tb_rate, _opp_sgw)
                    elif _opp_sgw > 80.0:
                        bp_forward_factor *= 0.92
                        logger.info("SIGNAL3_BP_OPP | opp tb=%.0f%% + high hold %.0f%% -> dominant, -8%% opps", p2_tb_rate, _opp_sgw)

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
                trace=_ctrace,
            )

            if req.prop_type == "Player Total Games Won":
                # Combine the player's holds (from the combined Total Games
                # projection) with their breaks (the BP-won projection).
                tg_result = project_total_games(
                    p1_s, p2_s, req.surface, h2h_games_avg,
                    h2h_games_n=h2h_games_n,
                    tour=req.tour, court=court_for_calc,
                    match_format=match_fmt,
                    player_ta=player_ta_props, opponent_ta=opponent_ta_props,
                )
                # ── MARKET WIN-PROB ANCHOR (same as FS) ───────────────────────
                # De-vig the upcoming match moneyline and blend it into the model
                # win prob so the scenario mixture keys off the market, not the
                # model's underdog-skewed read. No moneyline -> model-only.
                _ptgw_model_wp = (tg_result.get("p1_win_prob") or 50.0) / 100.0
                _ptgw_ml = await asyncio.to_thread(
                    get_match_moneyline_prob, req.player_id, req.opponent_id, req.tour)
                _ptgw_mkt_wp = _ptgw_ml.get("market_p1") if isinstance(_ptgw_ml, dict) else None
                _ptgw_anchored = isinstance(_ptgw_mkt_wp, (int, float))
                if _ptgw_anchored:
                    _ptgw_blended = (WINPROB_MARKET_WEIGHT * _ptgw_mkt_wp
                                     + (1.0 - WINPROB_MARKET_WEIGHT) * _ptgw_model_wp)
                else:
                    _ptgw_blended = _ptgw_model_wp
                logger.info("PTGW_WINPROB | %s | model=%.3f market=%s blended=%.3f anchored=%s",
                            req.player_name or "player", _ptgw_model_wp,
                            ("%.3f" % _ptgw_mkt_wp) if _ptgw_anchored else "None",
                            _ptgw_blended, _ptgw_anchored)
                result = project_player_games_won(
                    p1_s, p2_s, req.surface, cpr,
                    games_combined=tg_result.get("projection"),
                    bp_won=bp_result.get("projection"),
                    p1_win_prob=max(2.0, min(98.0, _ptgw_blended * 100.0)),
                    p2_win_prob=tg_result.get("p2_win_prob"),
                    expected_sets=tg_result.get("expected_sets"),
                    tour=req.tour, match_format=match_fmt,
                    prop_line=req.prop_line,
                    trace=_ctrace,
                )
                result["ptgw_model_wp"] = round(_ptgw_model_wp, 4)
                result["ptgw_market_wp"] = round(_ptgw_mkt_wp, 4) if _ptgw_anchored else None
                result["ptgw_blended_wp"] = round(_ptgw_blended, 4)
                result["ptgw_anchored"] = _ptgw_anchored
                # Carry the affinity differential + win prob forward. PTGW's own
                # projector doesn't compute them — it consumes the Total Games
                # projection's — but the underdog games-won confidence penalty
                # below needs both.
                for _k in ("p_affinity", "o_affinity", "affinity_gap",
                           "affinity_shift", "p1_win_prob", "p2_win_prob"):
                    result.setdefault(_k, tg_result.get(_k))
            else:
                result = bp_result
                # ── A2: BP FOUR-SCENARIO OUTCOME MIXTURE (7/23 audit) ─────────
                # The C1–C7 chain yields an outcome-BLIND breaks LEVEL (base_proj).
                # The SAME level implies very different P(over) by outcome: breaks-
                # in-a-win are matchup-wide, breaks-in-a-loss are floor-compressed.
                # Recondition base_proj on the market-anchored win prob via the same
                # scenario mixture as PTGW/FS (winners full matchup scale, losers
                # damped by BP_LOSS_MATCHUP_WEIGHT). Supersedes C8. Aces/DF live in a
                # different branch entirely and are byte-for-byte untouched.
                _bp_base = bp_result.get("base_proj")
                if isinstance(_bp_base, (int, float)) and _bp_base > 0 and req.prop_line is not None:
                    _bp_model_wp = (bp_result.get("p1_win_prob") or 50.0) / 100.0
                    _bp_ml = await asyncio.to_thread(
                        get_match_moneyline_prob, req.player_id, req.opponent_id, req.tour)
                    _bp_mkt_wp = _bp_ml.get("market_p1") if isinstance(_bp_ml, dict) else None
                    _bp_anchored = isinstance(_bp_mkt_wp, (int, float))
                    if _bp_anchored:
                        _bp_blended = (WINPROB_MARKET_WEIGHT * _bp_mkt_wp
                                       + (1.0 - WINPROB_MARKET_WEIGHT) * _bp_model_wp)
                    else:
                        _bp_blended = _bp_model_wp
                    _bp_pw = max(0.02, min(0.98, _bp_blended))
                    _bp_mix = bp_scenario_mixture(
                        _bp_pw, req.prop_line, _bp_base, req.tour, match_fmt)
                    _bp_fair = bp_fair_line(_bp_pw, _bp_base, req.tour, match_fmt)
                    result["projection"] = round(_bp_fair, 1)
                    result["bp_p_over"] = round(_bp_mix["p_over"], 4)
                    result["bp_scenario_probs"] = _bp_mix["scenario_probs"]
                    result["bp_scaled_means"] = _bp_mix["scaled_scenario_means"]
                    result["bp_mixture_mean"] = round(_bp_mix["mixture_mean"], 3)
                    result["bp_fair_line"] = round(_bp_fair, 2)
                    result["bp_base_proj"] = round(_bp_base, 3)
                    result["bp_model_wp"] = round(_bp_model_wp, 4)
                    result["bp_market_wp"] = round(_bp_mkt_wp, 4) if _bp_anchored else None
                    result["bp_blended_wp"] = round(_bp_blended, 4)
                    result["bp_anchored"] = _bp_anchored
                    # A2 upgrade of the A1 guard: re-key the lopsided/contradiction
                    # suspension off the MARKET-ANCHORED win prob (props.py set it off
                    # the model prob). Non-anchored -> blended==model -> unchanged.
                    _bp_wp_pct = _bp_blended * 100.0
                    _bp_lop = _bp_wp_pct < 30.0 or _bp_wp_pct > 70.0
                    _bp_contra = _bp_fair >= 4.0 and _bp_wp_pct < 35.0
                    result["bp_suspended"] = bool(_bp_lop or _bp_contra)
                    result["bp_suspend_reason"] = (
                        "lopsided win prob %.0f%% (outside 30-70)" % _bp_wp_pct if _bp_lop
                        else "contradiction: %.1f breaks at win prob %.0f%%" % (_bp_fair, _bp_wp_pct)
                        if _bp_contra else None)
                    logger.info("BP_WINPROB | %s | model=%.3f market=%s blended=%.3f "
                                "anchored=%s | base=%.2f fair=%.2f p_over=%.3f susp=%s",
                                req.player_name or "player", _bp_model_wp,
                                ("%.3f" % _bp_mkt_wp) if _bp_anchored else "None",
                                _bp_blended, _bp_anchored, _bp_base, _bp_fair,
                                _bp_mix["p_over"], result["bp_suspended"])
                    if _ctrace is not None:
                        _ctrace.append({
                            "step": len(_ctrace) + 1, "name": "bp_scenario_mixture",
                            "inputs": {"base_proj": round(_bp_base, 3),
                                       "p_win_anchored": round(_bp_blended, 4),
                                       "scenario_probs": _bp_mix["scenario_probs"],
                                       "scaled_means": _bp_mix["scaled_scenario_means"]},
                            "value": round(_bp_fair, 2), "running": round(_bp_fair, 2),
                            "note": ("A2 outcome conditioning: base_proj %.2f (C1–C7 blind "
                                     "level) reshaped by win/lose scenario mixture at "
                                     "p_win=%.3f -> P(over %.1f)=%.3f, fair line %.2f. "
                                     "Supersedes C8." % (_bp_base, _bp_blended,
                                     req.prop_line, _bp_mix["p_over"], _bp_fair))})

        proj_val = result.get("projection")
        if proj_val is None:
            return {
                "model_projection": None,
                "lean": None,
                "confidence": 0,
                "note": result.get("note", "Insufficient data for this surface/prop combination."),
            }

        # ── Recent-form pull REMOVED — it double-counted recent form ─────────
        # Recent form is ALREADY inside the blended stat: get_blended_stats has an
        # "SS last-5 on surface = 15%" tier. This post-calc step applied a SECOND
        # recent-form adjustment (up to ~30% toward the last-5 avg), stacking on
        # the blend and dragging projections ~8% below book lines systematically
        # (measured: WITH pull 5-above/7-below lines; WITHOUT, 7-above/5-below —
        # a natural spread). Recent form now moves the NUMBER in exactly one place
        # (the blend); the divergence confidence penalty and warning flag — which
        # adjust TRUST and INFORM the user — are unchanged. recent_form_avg is
        # still computed here for display/warning, but is NOT applied to proj_val.
        proj_val_premodel = proj_val
        # ⚠️ THE PULLED VALUE IS DELIBERATELY DISCARDED — recent form does NOT move
        # the projection. Read that again before assuming otherwise: the name below
        # is _DISCARDED_recent_form_pull precisely so nobody concludes from a
        # live-looking call that this feeds the number. It does not.
        #
        # Why it's disabled (kept, not deleted, so the reasoning survives): the
        # pull dragged projections ~8% under book lines systematically — measured
        # 5-above/7-below WITH the pull vs 7-above/5-below WITHOUT, i.e. the pull
        # turned a natural spread into a one-sided bias. Recent form already moves
        # the NUMBER in exactly one place (the blended-stats recency layer);
        # pulling again here double-counted it.
        #
        # What recent_form_avg IS still used for: the divergence confidence penalty
        # and the warning flag — those adjust TRUST and INFORM the reader. Neither
        # touches proj_val. It is also returned in the response purely for display.
        #
        # Consequently it is NOT a component_trace step, and must never become one
        # unless it is genuinely re-applied to the projection.
        _DISCARDED_recent_form_pull, recent_form_avg = _recent_form_pull(
            proj_val, p1_data.get(f"{req.surface}_matches", []) or [], req.prop_type)
        del _DISCARDED_recent_form_pull      # make the discard explicit, not incidental

        # ════ NEW SIGNALS applied as the top additive projection layer ═══════
        # THESE ARE PART OF THE PROJECTION CHAIN. They live outside the projector
        # function, which meant every audit that read the projector's output was
        # reading an intermediate value, not the number served. Each one is now a
        # labelled trace step so the chain is auditable end to end.
        _trace_pv = (lambda name, inputs, value, running, note:
                     _ctrace.append({"step": len(_ctrace) + 1, "name": name,
                                     "inputs": inputs, "value": value,
                                     "running": running, "note": note})
                     if _ctrace is not None else None)
        if isinstance(proj_val, (int, float)):
            # Signal 1 — indoor hard: faster, server-favouring conditions.
            if is_indoor_hard and req.prop_type == "Aces":
                _pre = proj_val
                proj_val = round(proj_val * 1.065, 1)
                result["_indoor_note"] = "Indoor hard court (faster, no wind) boosts ace projection +6.5%."
                logger.info("SIGNAL1_INDOOR_ACE | %.1f -> %.1f (+6.5%%)", _pre, proj_val)
                _trace_pv("post_indoor_hard", {"in": _pre, "is_indoor_hard": True},
                          1.065, proj_val, "indoor hard boosts aces +6.5%")
            elif is_indoor_hard and req.prop_type == "Break Points Won":
                _pre = proj_val
                proj_val = round(proj_val * 0.96, 1)
                result["_indoor_note"] = "Indoor hard court favours servers -- break-point conversion trimmed -4%."
                logger.info("SIGNAL1_INDOOR_BP | %.1f -> %.1f (-4%%)", _pre, proj_val)
                _trace_pv("post_indoor_hard", {"in": _pre, "is_indoor_hard": True},
                          0.96, proj_val, "indoor hard favours servers, BP -4%")
            # Altitude — thin air boosts aces only (does not change the CPI).
            if is_altitude and req.prop_type == "Aces":
                _pre = proj_val
                proj_val = round(proj_val * alt_factor, 1)
                result["_altitude_note"] = (
                    f"High-altitude venue (thin air, faster serves) boosts ace projection +{alt_pct}%.")
                logger.info("ALTITUDE_ACE | %.1f -> %.1f (+%d%%)", _pre, proj_val, alt_pct)
                _trace_pv("post_altitude", {"in": _pre, "venue": court_for_calc,
                                            "altitude_pct": alt_pct},
                          alt_factor, proj_val,
                          "high-altitude venue: thin air, faster serves -> aces +%d%%" % alt_pct)
            # Signal 2 — H2H psychological edge (BP prop).
            if req.prop_type == "Break Points Won" and h2h_psych_mult != 1.0:
                _pre = proj_val
                proj_val = round(proj_val * h2h_psych_mult, 1)
                _pct = round((h2h_psych_mult - 1.0) * 100)
                result["_h2h_psych_note"] = (
                    f"H2H psychological edge: {req.player_name} {h2h_psych_dir} the surface H2H "
                    f"by {abs(_h2h_gap)} of {_h2h_surf_n} -> BP {'+' if _pct >= 0 else ''}{_pct}%."
                )
                logger.info("SIGNAL2_H2H_APPLIED | %.1f -> %.1f (x%.2f)", _pre, proj_val, h2h_psych_mult)
                _trace_pv("post_h2h_psych", {"in": _pre, "surface_h2h": _h2h_surf_n,
                                             "gap": _h2h_gap, "direction": h2h_psych_dir},
                          h2h_psych_mult, proj_val,
                          "H2H psychological edge on the BP prop")

        # Signal 3 — tiebreak note (all props) + tiebreak-supplemented opponent
        # serve tier (BP prop). p1 = selected player, p2 = opponent.
        if p1_tb_rate is not None or p2_tb_rate is not None:
            result["_tiebreak_note"] = (
                f"Tiebreak rate (serve dominance): {req.player_name} "
                f"{('%.0f%%' % p1_tb_rate) if p1_tb_rate is not None else 'n/a'}, "
                f"{req.opponent_name} {('%.0f%%' % p2_tb_rate) if p2_tb_rate is not None else 'n/a'} of sets."
            )
        if req.prop_type == "Break Points Won":
            _opp_sgw_disp = ((p2_s or {}).get("service_games_won_pct")
                             or (p2_data.get("All") or {}).get("service_games_won_pct"))
            _up_tier = _server_quality_tier_sgw(_opp_sgw_disp, req.tour, p2_tb_rate)
            if _up_tier and result.get("opp_server_quality_tier") != _up_tier:
                logger.info("SIGNAL3_SERVE_TIER | opp tier %s -> %s (tb=%s sgw=%s)",
                            result.get("opp_server_quality_tier"), _up_tier, p2_tb_rate, _opp_sgw_disp)
                result["opp_server_quality_tier"] = _up_tier

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
            projection=proj_val,
            prop_line=req.prop_line,
            p1_deep=_deep_with_hysteresis(req.player_id, req.surface,
                                          p1_blended.get("_ta_career_matches", 0),
                                          req.player_name or "p1"),
            p2_deep=_deep_with_hysteresis(req.opponent_id, req.surface,
                                          p2_blended.get("_ta_career_matches", 0),
                                          req.opponent_name or "p2"),
        )
        # Start from the RAW (unclamped) base total. Every modifier below is
        # additive; the floor/cap is applied EXACTLY ONCE via finalize_confidence
        # at the very end — there is NO intermediate re-clamping in this file.
        confidence = conf_result["raw_total"]
        # Data-quality / variance ceiling (Fixes B/C + ace variance) — passed to
        # the single finalize step so it caps AFTER all modifiers.
        _data_ceiling = conf_result.get("data_ceiling", 95)

        # ══ PTGW: probability base, not the EVR/component grade (FREEZE exception) ══
        # PTGW confidence maps DIRECTLY from the scenario-mixture P(over) computed in
        # props.py. An "80% confidence" now literally means the modelled side hits
        # 80% of the time. The lean is set from P(over) here (not proj-vs-line), and
        # the mean-edge instruments — the +8 dominant bonus, the affinity underdog
        # penalty, and _edge_cap — are ALL skipped for PTGW below, because each is a
        # disguised comparison of the bimodal mean to the line.
        _ptgw_prob_base = False
        _ptgw_p_over = (result.get("ptgw_p_over")
                        if req.prop_type == "Player Total Games Won" else None)
        _ptgw_p_win = None          # selected player's match-win prob (0-1)
        _ptgw_implied_claim = None
        _ptgw_knife_edge = False
        if _ptgw_p_over is not None:
            _ptgw_lean = "OVER" if _ptgw_p_over >= 0.5 else "UNDER"
            result["lean"] = _ptgw_lean            # authoritative for _resolve_lean
            _p_side = _ptgw_p_over if _ptgw_lean == "OVER" else (1.0 - _ptgw_p_over)
            confidence = 100.0 * _p_side
            _ptgw_prob_base = True
            _pw = result.get("ptgw_p_win_match")
            _ptgw_p_win = _pw if isinstance(_pw, (int, float)) else (
                (result.get("p1_win_prob") or 50.0) / 100.0)
            _ptgw_knife_edge = 0.45 <= _ptgw_p_over <= 0.55
            # Required output field: the implied MATCH claim behind the pick.
            _loses = _ptgw_lean == "UNDER"
            _straight = (result.get("ptgw_scenario_probs") or {})
            _who = req.player_name or "player"
            if _loses:
                _ptgw_implied_claim = (
                    "%s U%.1f ⇒ %s loses, likely in straight sets"
                    % (_who, req.prop_line or 0, _who))
            else:
                _ptgw_implied_claim = (
                    "%s O%.1f ⇒ %s wins, or loses a competitive 3-setter"
                    % (_who, req.prop_line or 0, _who))
            logger.info("PTGW_PROB_BASE | %s line=%.1f p_over=%.3f p_win=%.3f "
                        "lean=%s base_conf=%.1f knife_edge=%s | %s",
                        _who, req.prop_line or 0, _ptgw_p_over, _ptgw_p_win,
                        _ptgw_lean, confidence, _ptgw_knife_edge, _ptgw_implied_claim)

        # ══ FANTASY SCORE: same probability base as PTGW (its own FREEZE prop) ══
        # FS confidence maps directly from the scenario-mixture P(over); the same
        # mean-edge instruments (EVR, _edge_cap, dominant bonus) are skipped. FS
        # does NOT get the PTGW structural guards — those are PTGW-specific — but it
        # shares the probability base and the FS_CONF_CEILING (80) applied downstream.
        _fs_prob_base = False
        _fs_p_over = (result.get("fs_p_over")
                      if req.prop_type == "Fantasy Score" else None)
        _fs_implied_claim = None
        _fs_line_position = None
        _fs_divergent = False
        _fs_guard_note = None
        _fs_knife_edge = False
        if _fs_p_over is not None:
            _fs_lean = "OVER" if _fs_p_over >= 0.5 else "UNDER"
            result["lean"] = _fs_lean
            _fs_side = _fs_p_over if _fs_lean == "OVER" else (1.0 - _fs_p_over)
            confidence = 100.0 * _fs_side
            _fs_prob_base = True
            _fs_knife_edge = 0.45 <= _fs_p_over <= 0.55
            _fs_implied_claim = result.get("fs_implied_claim")
            _fs_line_position = result.get("fs_line_position")
            # ── DIVERGENCE GUARD (point 4) ───────────────────────────────────
            # When the model and the book disagree on WHICH OUTCOME is expected
            # (proj band != line band — e.g. a three-set read against a dominant-
            # win line), the large P(over) is presumed model error, NOT a real
            # edge. Composite props on the platform's own scoring are rarely
            # mispriced by a full outcome band. Cap at 70 and flag; the Badosa
            # absolute-override does NOT extend to FS.
            # UNANCHORED cap (1A): no market moneyline available -> model-only ->
            # cap confidence at 70 (same ceiling as divergence).
            if not result.get("fs_anchored", True) and confidence > FS_DIVERGENCE_CONF_CAP:
                confidence = FS_DIVERGENCE_CONF_CAP
                logger.info("FS_UNANCHORED | %s line=%.1f -> cap %d (no market moneyline)",
                            req.player_name or "player", req.prop_line or 0,
                            FS_DIVERGENCE_CONF_CAP)
            _fs_divergent = bool(result.get("fs_divergent"))
            _fs_guard_note = None
            if _fs_divergent:
                _p1n = p1_blended.get("_ta_career_matches", 0)
                _p2n = p2_blended.get("_ta_career_matches", 0)
                _fs_guard_note = ("model/book scenario disagreement — verify inputs "
                                  "(proj band %s vs line band %s; stat-rich p1=%s p2=%s)"
                                  % (result.get("fs_proj_band"), result.get("fs_line_band"),
                                     _p1n, _p2n))
                if confidence > FS_DIVERGENCE_CONF_CAP:
                    confidence = FS_DIVERGENCE_CONF_CAP
                logger.info("FS_DIVERGENCE | %s line=%.1f proj_band=%s line_band=%s -> "
                            "cap %d | stat-rich p1=%s p2=%s",
                            req.player_name or "player", req.prop_line or 0,
                            result.get("fs_proj_band"), result.get("fs_line_band"),
                            FS_DIVERGENCE_CONF_CAP, _p1n, _p2n)
            logger.info("FS_PROB_BASE | %s line=%.1f p_over=%.3f lean=%s conf=%.1f "
                        "knife_edge=%s divergent=%s | %s | %s",
                        req.player_name or "player", req.prop_line or 0, _fs_p_over,
                        _fs_lean, confidence, _fs_knife_edge, _fs_divergent,
                        _fs_line_position or "?", _fs_implied_claim or "?")
        # ══ BREAK POINTS WON: same probability base (A2 outcome mixture) ═════
        # BP confidence maps from the scenario-mixture P(over) exactly like PTGW/FS.
        # The mean-edge instruments (EVR, _edge_cap, dominant bonus) are skipped —
        # each compares the outcome-BLIND mean to the line, the fallacy A2 removed.
        # Unanchored (no market moneyline) caps at 70, same ceiling as FS.
        _bp_prob_base = False
        _bp_p_over = (result.get("bp_p_over")
                      if req.prop_type == "Break Points Won" else None)
        _bp_implied_claim = None
        _bp_knife_edge = False
        if _bp_p_over is not None:
            _bp_lean = "OVER" if _bp_p_over >= 0.5 else "UNDER"
            result["lean"] = _bp_lean
            _bp_side = _bp_p_over if _bp_lean == "OVER" else (1.0 - _bp_p_over)
            confidence = 100.0 * _bp_side
            _bp_prob_base = True
            _bp_knife_edge = 0.45 <= _bp_p_over <= 0.55
            _who = req.player_name or "player"
            _bp_implied_claim = (
                "%s O%.1f BP ⇒ %s breaks repeatedly / wins comfortably"
                % (_who, req.prop_line or 0, _who) if _bp_lean == "OVER" else
                "%s U%.1f BP ⇒ %s rarely breaks / loses without converting"
                % (_who, req.prop_line or 0, _who))
            result["bp_implied_claim"] = _bp_implied_claim
            if not result.get("bp_anchored", True) and confidence > FS_DIVERGENCE_CONF_CAP:
                confidence = FS_DIVERGENCE_CONF_CAP
                logger.info("BP_UNANCHORED | %s line=%.1f -> cap %d (no market moneyline)",
                            _who, req.prop_line or 0, FS_DIVERGENCE_CONF_CAP)
            logger.info("BP_PROB_BASE | %s line=%.1f p_over=%.3f lean=%s conf=%.1f "
                        "knife_edge=%s anchored=%s | %s",
                        _who, req.prop_line or 0, _bp_p_over, _bp_lean, confidence,
                        _bp_knife_edge, result.get("bp_anchored"), _bp_implied_claim)
        # A single flag for the mean-edge skips below (all prob-base props).
        _prob_base = _ptgw_prob_base or _fs_prob_base or _bp_prob_base
        # Consistency tier for display comes straight from the confidence
        # breakdown — consistency is now scored ONCE, in confidence.py. There is
        # no separate main.py consistency penalty.
        _cons_bd = conf_result.get("breakdown")
        consistency_tier = ((_cons_bd.get("consistency") or {}).get("tier")
                            if isinstance(_cons_bd, dict) else None)

        # ── BP opportunity-volume penalty (Part 1) ───────────────────────────
        # A conversion rate built on a thin opportunity sample is unreliable:
        # <3 BP generated/match → −10 and a disclosure note; ≥6 is a solid signal.
        if req.prop_type == "Break Points Won":
            _bp_gen = result.get("bp_generated_per_match")
            if _bp_gen is not None and _bp_gen < 3.0:
                confidence -= 10
                _bd = conf_result.get("breakdown")
                if isinstance(_bd, list):
                    _bd.append("Low opportunity volume — conversion rate based on limited chances")

        # Retirement risk (Imp 5) — the flag/display always stays, but the PENALTY
        # scales by how early the prop resolves. A DNF only voids a prop if the
        # match ends before it resolves; a low line an elite returner clears in
        # the first set and a half barely cares about a late retirement. So when
        # the projection clears the line by 2x or more the prop resolves early →
        # soften -10 to -3; high lines that need deep sets keep the full penalty.
        retirement_risk, ret_pen, pct_completed = _retirement_risk(p1_ss_all)
        if retirement_risk:
            _line = req.prop_line or 0
            if isinstance(proj_val, (int, float)) and _line and proj_val >= 2 * _line:
                ret_pen = -3
            confidence += ret_pen
            logger.info("RETIREMENT | flag=True pen=%d (proj=%s line=%s)",
                        ret_pen, proj_val, req.prop_line)

        # Dominant matchup bonus (+8) — recognise overwhelming edges so the model
        # can express conviction instead of compressing everything into 60-80.
        # Fires only when the projection blows out the line (>1.75x) AND the
        # win-probability gap is lopsided (>30pp) AND the sample is solid (>=15).
        _wp_gap = result.get("win_prob_gap")
        if not isinstance(_wp_gap, (int, float)):
            _w1, _w2 = result.get("p1_win_prob"), result.get("p2_win_prob")
            _wp_gap = ((_w1 - _w2) if isinstance(_w1, (int, float)) and isinstance(_w2, (int, float))
                       else 0)
        if (not _prob_base
                and isinstance(proj_val, (int, float)) and req.prop_line
                and proj_val > req.prop_line * 1.75 and _wp_gap > 30 and _p1_surf_n >= 15):
            confidence += 8
            logger.info("DOMINANT_BONUS | +8 | proj=%.1f > 1.75x line=%.1f | wp_gap=%.0f | n=%d",
                        proj_val, req.prop_line, _wp_gap, _p1_surf_n)

        # Sackmann thin-data penalty (additive; negative). No clamp here.
        sack_penalty = p1_blended.get("_confidence_penalty", 0)
        if sack_penalty:
            confidence += sack_penalty

        # ── Consolidated inactivity penalty (SINGLE mechanism) ────────────────
        # Replaces BOTH the old freshness −15 AND the old per-player recent-data
        # penalty. Per player by days since last match: ≤21 none · 21–45 −5 ·
        # >45 −12. Summed across both players, combined cap −20 for the matchup.
        try:
            from src import features as _feat
            _fresh_p1 = _feat.freshness_from_matches(p1_data.get("all_matches", []) or [])
            _fresh_p2 = _feat.freshness_from_matches(p2_data.get("all_matches", []) or [])
        except Exception:  # noqa: BLE001
            _fresh_p1 = {"level": "", "message": "", "days_since_last": None}
            _fresh_p2 = {"level": "", "message": "", "days_since_last": None}
        _freshness = _fresh_p1   # p1 drives the advisory display flag (unchanged)

        def _inactivity_pen(days):
            if not isinstance(days, (int, float)):
                return 0
            if days <= 21:
                return 0
            if days <= 45:
                return -5
            return -12

        inactivity_pen = max(-20, _inactivity_pen(_fresh_p1.get("days_since_last"))
                                  + _inactivity_pen(_fresh_p2.get("days_since_last")))
        if inactivity_pen:
            confidence += inactivity_pen
            logger.info("INACTIVITY | p1_days=%s p2_days=%s -> combined %+d (cap -20)",
                        _fresh_p1.get("days_since_last"), _fresh_p2.get("days_since_last"),
                        inactivity_pen)

        # Recent-data meta annotations — DISPLAY ONLY (frontend amber/red tiers).
        # These no longer contribute to confidence; the inactivity component above
        # is the sole staleness penalty. Kind classification is unchanged so the
        # visible warnings render exactly as before.
        def _recent_kind(meta):
            if not meta or meta.get("warning") == "ta_unavailable":
                return None
            total_n   = meta.get("all_surfaces_n", 0) or 0
            surface_n = meta.get("surface_n", 0) or 0
            if meta.get("warning") == "insufficient" or total_n < 10:
                return "insufficient"
            if surface_n >= 5:
                return None
            if total_n >= 20:
                return "specialist"
            return "limited"

        for _meta in (p1_recent_meta, p2_recent_meta):
            if _meta:
                _meta["penalty_kind"] = _recent_kind(_meta)
                _meta["penalty"]      = 0   # display metadata only (no confidence effect)

        # Sanity failure: projection fell outside realistic bounds.
        if result.get("sanity_failed"):
            confidence -= 25
            logger.warning("Sanity check failed for %s %s — confidence reduced",
                           req.prop_type, proj_val)

        # PTGW takes its lean from the scenario-mixture P(over), NOT proj-vs-line:
        # the bimodal mean routinely lands ON the line (e.g. mean 11.5 vs line 11.5),
        # where _resolve_lean would tie to UNDER and flip a genuine OVER. For every
        # other prop, _resolve_lean is unchanged.
        if _ptgw_prob_base:
            lean = _ptgw_lean
        elif _fs_prob_base:
            lean = _fs_lean
        elif _bp_prob_base:
            lean = _bp_lean            # from P(over), not the median-vs-line tie
        else:
            lean = _resolve_lean(proj_val, req.prop_line, result.get("lean", ""))

        # ── Underdog games-won UNDER penalty (surface-affinity differential) ──
        # When the underdog is on their best surface against a favourite on their
        # worst, the scoreline that actually shows up is the competitive loss —
        # 4-6 6-7, 6-7 5-7 — which clears a games-won line the level gap said it
        # wouldn't. An UNDER on the underdog's games won is precisely the bet that
        # loses in those matches, so it earns a penalty rather than a bonus.
        # OVERs are untouched: they're the side the affinity argument supports.
        _aff_gap = result.get("affinity_gap")
        _p1wp = result.get("p1_win_prob")
        if (req.prop_type == "Player Total Games Won"
                and not _ptgw_prob_base                # replaced by the mixture + guards
                and lean == "UNDER"
                and isinstance(_aff_gap, (int, float))
                and isinstance(_p1wp, (int, float))
                and _p1wp < 50.0                       # the player IS the underdog
                and _aff_gap >= UNDERDOG_AFFINITY_MIN_GAP):
            _pen = (UNDERDOG_UNDER_PENALTY_MAX
                    if _aff_gap >= UNDERDOG_AFFINITY_STRONG_GAP
                    else UNDERDOG_UNDER_PENALTY_MIN)
            confidence -= _pen
            logger.info(
                "AFFINITY_UNDERDOG_PENALTY | %s %s UNDER | win_prob=%.1f (underdog) | "
                "affinity gap=%+.1f in the underdog's favour -> -%d confidence "
                "(competitive-loss scorelines beat this line)",
                req.player_name, req.prop_type, _p1wp, _aff_gap, _pen,
            )

        # Edge-based ceiling — a SEPARATE rule (caps confidence when the
        # projection barely clears the line), not the floor/cap. Applied before
        # the single finalize step below. SKIPPED for PTGW: |proj − line| / line is
        # the same bimodal-mean-vs-line fallacy the rebuild removed — a high-P(over)
        # PTGW pick can have a tiny mean edge, and edge_cap would wrongly gut it.
        if not _prob_base:
            confidence = _edge_cap(confidence, proj_val, req.prop_line)

        # ══ PART 3 — HARD STRUCTURAL GUARDS (cheap invariants, model-independent) ══
        # These hold even if the mixture has a bug. Applied after all modifiers,
        # before the single finalize/clamp.
        _ptgw_guard_note = None
        if _ptgw_prob_base:
            _is_bo5 = result.get("is_bo5") or (match_fmt == "best_of_5")
            _line = req.prop_line or 0
            _p_lose = 1.0 - (_ptgw_p_win if isinstance(_ptgw_p_win, (int, float)) else 0.5)
            # Guard 1 — an UNDER on a game-total line at/above the winner's floor+ can
            # only win when the player LOSES: cap UNDER confidence at 100·P(lose).
            #   BO3 line ≥ 11.5  ·  BO5 line ≥ 17.5
            _guard_line = 17.5 if _is_bo5 else 11.5
            if lean == "UNDER" and _line >= _guard_line:
                _cap = 100.0 * _p_lose
                if confidence > _cap:
                    _ptgw_guard_note = (
                        "UNDER capped at 100·P(lose)=%.0f (line %.1f ≥ %.1f: this "
                        "wins only if the player loses)" % (_cap, _line, _guard_line))
                    logger.info("PTGW_GUARD_1 | conf %.1f -> %.1f | %s",
                                confidence, _cap, _ptgw_guard_note)
                    confidence = min(confidence, _cap)
            # Guard 2 — block ANY PTGW UNDER when the model's own win prob for the
            # player exceeds 40%. Such a player clears the winner's-floor line the
            # majority of the time; an UNDER is structurally a bad bet.
            if lean == "UNDER" and isinstance(_ptgw_p_win, (int, float)) and _ptgw_p_win > 0.40:
                _ptgw_guard_note = (
                    "UNDER BLOCKED — model win prob %.0f%% > 40%%; player clears the "
                    "line more often than not" % (_ptgw_p_win * 100))
                logger.info("PTGW_GUARD_2 | conf %.1f -> 25 (blocked) | %s",
                            confidence, _ptgw_guard_note)
                confidence = min(confidence, 25)   # below every qualification bar

        # ── SINGLE floor/cap — the final confidence step, in one place ────────
        # floor 25 / cap 95 (minus any per-prop ceiling, minus the data-quality /
        # variance ceiling), applied exactly once after every bonus and penalty.
        _pre_cap = round(confidence) if isinstance(confidence, (int, float)) else None
        confidence = finalize_confidence(confidence, req.prop_type, _data_ceiling)
        # A cap indicator is shown ONLY when a structural ceiling actually pulled
        # the score down (the pre-cap value exceeded the data ceiling).
        confidence_cap_reason = (
            conf_result.get("cap_tag")
            if (_data_ceiling < 95 and _pre_cap is not None
                and _pre_cap > confidence and confidence == _data_ceiling)
            else None
        )

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
        # ⚠️ Despite the "_ta_" / "ta_matches" naming these are SOFASCORE stat-rich
        # surface match counts, NOT Tennis Abstract. See the note in
        # blended_stats.py. They are capped by the valid[:50] stats fetch and span
        # ~52 weeks — they are NOT a career-depth sample, and the response's
        # player_ta_matches / opponent_ta_matches fields inherit the same misnomer.
        p1_ta_career   = p1_blended.get("_ta_career_matches", 0)   # stat-rich SS surface n
        p2_ta_career   = p2_blended.get("_ta_career_matches", 0)   # stat-rich SS surface n
        p1_ss_recent   = p1_blended.get("_ss_recent_matches", 0)
        p2_ss_recent   = p2_blended.get("_ss_recent_matches", 0)
        p1_fallback    = p1_blended.get("_surface_fallback", False)
        p2_fallback    = p2_blended.get("_surface_fallback", False)
        # data_quality is the PLAYER's (p1). The opponent's is exposed separately
        # as opponent_data_quality — the confidence ceilings in confidence.py gate
        # on BOTH sides, so an audit that can only see p1 is blind to half of what
        # actually caps a score.
        data_quality     = p1_blended.get("_data_quality", "moderate")
        opp_data_quality = p2_blended.get("_data_quality", "moderate")
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

        # ── Trace FINAL — assert-equal to what the response actually carries ──
        # "FINAL" previously meant "the projector stopped here", while main.py went
        # on to move the number — so the trace disagreed with model_projection and
        # quietly misreported the chain. FINAL now means RESPONSE-EQUAL, and the
        # assertion is what keeps it honest: in debug mode a mismatch is an ERROR,
        # not a trace that lies. If a future adjustment is added without a trace
        # step, this fails loudly on the next audit instead of hiding.
        if _ctrace is not None:
            _ctrace.append({
                "step": len(_ctrace) + 1, "name": "FINAL",
                "inputs": {"response_model_projection": proj_val},
                "value": proj_val, "running": proj_val,
                "note": "response-equal by assertion — this IS the served number",
            })
            _proj_steps = [e for e in _ctrace if e["name"] == "projector_output"]
            if _proj_steps and _proj_steps[0]["running"] != proj_val:
                logger.info(
                    "TRACE_CHAIN | projector_output=%s -> FINAL=%s (post-projector "
                    "adjustments moved the number; each is a labelled step)",
                    _proj_steps[0]["running"], proj_val)
            if isinstance(proj_val, (int, float)):
                _last_running = None
                for _e in reversed(_ctrace[:-1]):
                    if isinstance(_e.get("running"), (int, float)):
                        _last_running = _e["running"]
                        break
                if _last_running is not None and abs(_last_running - proj_val) > 0.05:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "component_trace is INCOMPLETE: last traced running value "
                            f"{_last_running} != served model_projection {proj_val}. "
                            "An adjustment is mutating the projection without a trace "
                            "step. Refusing to serve a trace that misreports the chain."
                        ),
                    )

        # Serialise H2H DataFrames
        def _df_records(key):
            df = h2h_summary.get(key)
            if df is None or (hasattr(df, "empty") and df.empty):
                return []
            return df.to_dict(orient="records") if hasattr(df, "to_dict") else []

        return {
            "model_projection":     proj_val,
            "model_projection_premull": round(proj_val_premodel, 1) if isinstance(proj_val_premodel, (int, float)) else None,
            "recent_form_avg":      recent_form_avg,
            "consistency_tier":     consistency_tier,
            "retirement_risk":      retirement_risk,
            "pct_completed":        pct_completed,
            # NEW SIGNALS — indoor flag (1) + surface tiebreak rates (3)
            "indoor_court":         is_indoor_hard,
            "altitude_court":        is_altitude,
            "altitude_pct":          alt_pct,
            "player_tiebreak_rate":   p1_tb_rate,
            "opponent_tiebreak_rate": p2_tb_rate,
            "lean":                 lean,
            "confidence":           confidence,
            "confidence_cap_reason": confidence_cap_reason,
            # A1 interim BP outcome-inversion guard (Break Points Won only; None
            # for other props). Projection/confidence are unchanged — the bot
            # excludes suspended BP picks from the board.
            "bp_suspended":         result.get("bp_suspended"),
            "bp_suspend_reason":    result.get("bp_suspend_reason"),
            "confidence_breakdown": conf_result["breakdown"],
            # ── PTGW scenario-mixture surface (None for other props) ──────────────
            "ptgw_p_over":          _ptgw_p_over,
            "ptgw_p_win_match":     (round(_ptgw_p_win, 4)
                                     if isinstance(_ptgw_p_win, (int, float)) else None),
            "ptgw_scenario_probs":  result.get("ptgw_scenario_probs"),
            "ptgw_implied_claim":   _ptgw_implied_claim,
            "ptgw_knife_edge":      _ptgw_knife_edge,
            "ptgw_guard_note":      _ptgw_guard_note,
            # Win-prob anchor (model / de-vigged market / blended) + anchored flag.
            "ptgw_model_wp":        result.get("ptgw_model_wp"),
            "ptgw_market_wp":       result.get("ptgw_market_wp"),
            "ptgw_blended_wp":      result.get("ptgw_blended_wp"),
            "ptgw_anchored":        result.get("ptgw_anchored"),
            # ── Fantasy Score scenario-mixture surface (None for other props) ─────
            "fs_p_over":            _fs_p_over,
            "fs_scenario_probs":    result.get("fs_scenario_probs"),
            "fs_scenario_breakdown": result.get("fs_scenario_breakdown"),
            "fs_fair_line":         result.get("fs_fair_line"),
            "fs_mixture_mean":      result.get("fs_mixture_mean"),
            "fs_implied_claim":     _fs_implied_claim,
            "fs_line_position":     _fs_line_position,
            "fs_divergent":         _fs_divergent,
            "fs_guard_note":        _fs_guard_note,
            "fs_knife_edge":        _fs_knife_edge,
            # Win-prob anchor (model / de-vigged market / blended) + anchored flag.
            "fs_model_wp":          result.get("fs_model_wp"),
            "fs_market_wp":         result.get("fs_market_wp"),
            "fs_blended_wp":        result.get("fs_blended_wp"),
            "fs_anchored":          result.get("fs_anchored"),
            # ── Break Points Won scenario-mixture surface (A2; None for others) ───
            "bp_p_over":            _bp_p_over,
            "bp_scenario_probs":    result.get("bp_scenario_probs"),
            "bp_scaled_means":      result.get("bp_scaled_means"),
            "bp_fair_line":         result.get("bp_fair_line"),
            "bp_base_proj":         result.get("bp_base_proj"),
            "bp_mixture_mean":      result.get("bp_mixture_mean"),
            "bp_implied_claim":     _bp_implied_claim,
            "bp_knife_edge":        _bp_knife_edge,
            # Win-prob anchor (model / de-vigged market / blended) + anchored flag.
            "bp_model_wp":          result.get("bp_model_wp"),
            "bp_market_wp":         result.get("bp_market_wp"),
            "bp_blended_wp":        result.get("bp_blended_wp"),
            "bp_anchored":          result.get("bp_anchored"),
            # ── Total Games sportsbook anchor (None for other props) ──────────────
            # model proj / de-vigged book O-U line / blended proj + edge vs the
            # PrizePicks line and a divergence flag (|model − book| > 3 games).
            "tg_book_line":         result.get("tg_book_line"),
            "tg_model_proj":        result.get("tg_model_proj"),
            "tg_blended_proj":      result.get("tg_blended_proj"),
            "tg_book_over_prob":    result.get("tg_book_over_prob"),
            "tg_book_under_prob":   result.get("tg_book_under_prob"),
            "tg_book_edge":         result.get("tg_book_edge"),
            "tg_anchored":          result.get("tg_anchored"),
            "tg_divergent":         result.get("tg_divergent"),
            # Feature 3 — data freshness / injury flag (advisory)
            "freshness_level":      _freshness.get("level", ""),
            "freshness_message":    _freshness.get("message", ""),
            "freshness_days":       _freshness.get("days_since_last"),
            "environment":          env_key,
            "environment_label":    env_label,
            "player_stats":         p1_s,
            "opponent_stats":       p2_s,
            # Min-viable stats that fell back to a tour-average estimate (UI shows ~est)
            "player_tour_avg_stats":   p1_gw_est,
            "opponent_tour_avg_stats": p2_gw_est,
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
            # ADMIN DIAGNOSTIC — present ONLY when the caller sent debug=true.
            # No bot or frontend caller does, so it never reaches a member-facing
            # post. `None` on every normal request.
            "component_trace":        _ctrace,
            "data_quality":           data_quality,       # player (p1)
            "opponent_data_quality":  opp_data_quality,   # opponent (p2)
            # Surface-affinity differential (positive = this surface suits them
            # relative to their OWN all-surface baseline).
            "player_surface_affinity":   result.get("p_affinity"),
            "opponent_surface_affinity": result.get("o_affinity"),
            "surface_affinity_gap":      result.get("affinity_gap"),
            "surface_affinity_shift":    result.get("affinity_shift"),
            # Full best->worst held-out surface ranking per player.
            "player_surface_ranking":    p1_s.get("surface_ranking"),
            "opponent_surface_ranking":  p2_s.get("surface_ranking"),
            # Depth status AFTER hysteresis — the single source of truth for every
            # depth gate (the backend's own ceilings and the bot's PTGW bar), so a
            # noisy count can't make them disagree.
            "player_deep":            _deep_with_hysteresis(
                req.player_id, req.surface, p1_ta_career, req.player_name or "p1"),
            "opponent_deep":          _deep_with_hysteresis(
                req.opponent_id, req.surface, p2_ta_career, req.opponent_name or "p2"),
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
            # ── Opponent-quality-weighted averages (Improvement 1) ──
            "bp_generated_raw_avg":       p1_s.get("return_bp_opportunities_raw_avg"),
            "bp_generated_weighted_avg":  p1_s.get("return_bp_opportunities_weighted_avg"),
            "bp_converted_raw_avg":       p1_s.get("bp_converted_raw_avg"),
            "bp_converted_weighted_avg":  p1_s.get("bp_converted_weighted_avg"),
            "quality_match_rate":         p1_qw_match_rate,
            "stats_inflated":             p1_stats_inflated,
            # ── BP generated + games-won + server-quality badge (Parts 1/2/5) ──
            "bp_generated_per_match":     result.get("bp_generated_per_match"),
            "bp_generated_quality_adj":   result.get("bp_generated_quality_adj"),
            "bp_forward_server_factor":   result.get("forward_server_factor"),
            "player_service_games_won_pct": result.get("player_service_games_won_pct"),
            "player_return_games_won_pct":  result.get("player_return_games_won_pct"),
            "opp_service_games_won_pct":    result.get("opp_service_games_won_pct"),
            "opp_return_games_won_pct":     result.get("opp_return_games_won_pct"),
            # Tour-relative badge (Step 7): append the tour so the tier is
            # clearly judged vs WTA/ATP peers, e.g. "Elite Server · WTA".
            "opp_server_quality_tier":      (f"{result.get('opp_server_quality_tier')} · {req.tour}"
                                             if result.get("opp_server_quality_tier") else None),
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
