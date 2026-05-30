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
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from src.api.sofascore_client import (
    init_session,
    search_players,
    get_player_stats_by_surface,
    get_h2h_summary,
    get_h2h_stat_avg,
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
    generate_scouting_report,
    detect_environment,
    ENVIRONMENT_LABELS,
    GRAND_SLAMS,
)
from src.constants import COURT_CPR, CPR_NEUTRAL, GENERIC_SURFACE_CPR

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
    logger.info("Backend ready.")


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
@app.get("/api/search")
async def search_get(query: str = "", tour: str = "ATP"):
    if len(query.strip()) < 3:
        return []
    try:
        return search_players(query.strip(), tour)
    except Exception as e:
        logger.error("search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/search")
async def search_post(req: SearchRequest):
    if len(req.query.strip()) < 3:
        return []
    try:
        return search_players(req.query.strip(), req.tour)
    except Exception as e:
        logger.error("search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/player/stats
# ---------------------------------------------------------------------------
@app.post("/api/player/stats")
async def player_stats(req: PlayerStatsRequest):
    try:
        data      = get_player_stats_by_surface(req.player_id, req.tour)
        all_stats = data.get("All", {}) or {}
        archetype = classify_archetype(all_stats, req.tour)

        # Fetch Tennis Abstract data if player name is provided
        ta_data = None
        if req.player_name:
            try:
                ta_data = await asyncio.wait_for(
                    get_player_ta_stats(req.player_name, req.tour),
                    timeout=30.0,
                )
            except Exception as e:
                logger.warning("TA fetch failed for stats endpoint: %s", e)

        return {
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
# POST /api/prop/calculate
# ---------------------------------------------------------------------------
@app.post("/api/prop/calculate")
async def prop_calculate(req: PropRequest):
    try:
        # Fetch Sofascore data
        p1_data = get_player_stats_by_surface(req.player_id, req.tour)
        p2_data = get_player_stats_by_surface(req.opponent_id, req.tour)
        h2h_summary = get_h2h_summary(
            req.tour, req.player_id, req.opponent_id, surface=req.surface
        )
        h2h_stats = get_h2h_stat_avg(
            req.tour, req.player_id, req.opponent_id, surface=req.surface
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

        court_for_calc = "" if req.court in ("", "None") else req.court
        cpr = COURT_CPR.get(court_for_calc,
              GENERIC_SURFACE_CPR.get(req.surface, CPR_NEUTRAL))

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

        # ── Match format: strict rules, logged for every request ────────────────
        # ATP Grand Slams only → best_of_5. ALL WTA events → best_of_3 (no
        # exceptions; WTA Grand Slams are NEVER BO5).  All ATP non-GS → best_of_3.
        _is_atp_gs = court_for_calc in GRAND_SLAMS and req.tour == "ATP"
        match_fmt  = "best_of_5" if _is_atp_gs else "best_of_3"
        logger.info(
            "MATCH_FORMAT | court=%s | tour=%s | is_atp_gs=%s | match_fmt=%s",
            court_for_calc or "generic", req.tour, _is_atp_gs, match_fmt,
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
                player_ta=player_ta_props, opponent_ta=opponent_ta_props,
            )
        else:  # Break Points Won
            # All-surface player stats for Step 9 sanity check
            _p1_all_at = p1_data.get("All_all_time_stats") or {}
            p1_all_ref = {
                "bp_converted":                _p1_all_at.get("bp_converted"),
                "return_bp_opportunities":     _p1_all_at.get("return_bp_opportunities"),
                "return_bp_converted":         _p1_all_at.get("return_bp_converted"),
                "bp_faced_count":              _p1_all_at.get("bp_faced_count"),
                "return_first_serve_pts_won":  _p1_all_at.get("return_first_serve_pts_won"),
                "return_second_serve_pts_won": _p1_all_at.get("return_second_serve_pts_won"),
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

            result = project_break_points(
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
            )

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

        # Sackmann thin-data penalty (applied before sanity check)
        sack_penalty = p1_blended.get("_confidence_penalty", 0)
        if sack_penalty:
            confidence = max(15, confidence + sack_penalty)  # penalty is negative

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
            confidence = max(15, confidence + total_recent_pen)
            logger.warning(
                "RECENT_DATA_PENALTY | p1=%s(%+d) p2=%s(%+d) | total=%+d -> conf=%d",
                p1_pen_kind, p1_pen, p2_pen_kind, p2_pen, total_recent_pen, confidence,
            )

        # Sanity failure: projection fell outside realistic bounds → reduce confidence
        if result.get("sanity_failed"):
            confidence = max(15, confidence - 25)
            logger.warning(
                "Sanity check failed for %s %s — confidence reduced to %d",
                req.prop_type, proj_val, confidence,
            )

        lean = _resolve_lean(proj_val, req.prop_line, result.get("lean", ""))
        confidence = _edge_cap(confidence, proj_val, req.prop_line)

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

        # Opponent ace-against (aces the opponent concedes per match as a returner)
        opponent_ace_against = opponent_ta.get("ace_against_per_match") if opponent_ta else None

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

        env_key   = result.get("environment") or detect_environment(p1_s, p2_s, surface=req.surface)
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
            # Projection quality flags
            "sanity_failed":        result.get("sanity_failed", False),
            "used_opp_tour_avg":    result.get("used_opp_tour_avg", False),
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
            "match_format":          result.get("match_format", match_fmt),
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
        summary = get_h2h_summary(
            req.tour, req.player1_id, req.player2_id, surface=req.surface
        )
        stats = get_h2h_stat_avg(
            req.tour, req.player1_id, req.player2_id, surface=req.surface
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


# ---------------------------------------------------------------------------
# POST /api/board/scrape and /analyze — PrizePicks Board Optimizer
# ---------------------------------------------------------------------------
from src.api.prizepicks_scraper import scrape_board, clear_cache as _pp_clear_cache
from src.api.player_matcher import match_player
from src.constants import COURT_CPR, COURTS_BY_SURFACE


# Reverse index: lowercase tournament name → (surface, court_key)
_TOURNAMENT_INDEX: dict = {}
for _surf, _courts in COURTS_BY_SURFACE.items():
    for _c in _courts:
        _TOURNAMENT_INDEX[_c.lower()] = (_surf, _c)
# Add common short forms / aliases users may see on PrizePicks
_TOURNAMENT_ALIASES = {
    "french open":          ("Clay",  "Roland Garros"),
    "roland-garros":        ("Clay",  "Roland Garros"),
    "rome masters":         ("Clay",  "Italian Open Rome"),
    "madrid masters":       ("Clay",  "Madrid Open"),
    "monte-carlo":          ("Clay",  "Monte Carlo Masters"),
    "ao":                   ("Hard",  "Australian Open"),
    "us open":              ("Hard",  "US Open"),
    "wimbledon":            ("Grass", "Wimbledon"),
}
_TOURNAMENT_INDEX.update({k: v for k, v in _TOURNAMENT_ALIASES.items()})


def _resolve_surface_and_court(tournament: Optional[str],
                               description: Optional[str] = "") -> tuple:
    """
    Pick (surface, court) from a tournament-ish string. Falls back to ('Hard', '')
    when nothing matches — Hard is the most common surface on tour year-round.
    """
    haystack = " ".join(filter(None, [tournament or "", description or ""])).lower()
    if not haystack.strip():
        return "Hard", ""
    # First try exact match on the index keys (longest first to avoid partial wins)
    for key in sorted(_TOURNAMENT_INDEX.keys(), key=len, reverse=True):
        if key in haystack:
            return _TOURNAMENT_INDEX[key]
    return "Hard", ""


class BoardScrapeRequest(BaseModel):
    force_refresh: bool = False


class BoardAnalyzeRequest(BaseModel):
    """Caller passes the already-scraped board to keep the two steps separate."""
    props: list = []
    tour_filter: Optional[str] = None   # 'ATP' | 'WTA' | None (both)


@app.post("/api/board/scrape")
async def board_scrape(req: BoardScrapeRequest = None):
    """
    Scrape PrizePicks tennis board. Returns the eligible-prop list along with
    scrape metadata. Heavy work runs in a thread so we don't block the loop.
    """
    force = bool(req and req.force_refresh)
    if force:
        _pp_clear_cache()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, scrape_board, force)
        return result
    except Exception as exc:
        logger.exception("board/scrape failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


async def _analyze_one_prop(pp_prop: dict, tour: str) -> dict:
    """
    Match a single PrizePicks prop to Sofascore players and run the full
    Baseline projection. Returns a normalised analyzed-prop dict that the
    frontend renders directly.
    """
    out = {
        "pp_prop":           pp_prop,
        "tour":              tour,
        "matched":           False,
        "match_note":        None,
        "model_projection":  None,
        "lean":              None,
        "confidence":        0,
        "edge":              None,
        "surface":           None,
        "court":             None,
        "player":            None,
        "opponent":          None,
        "result":            None,   # full /api/prop/calculate payload when matched
        "error":             None,
    }

    try:
        # Resolve surface + court from tournament
        surface, court = _resolve_surface_and_court(
            pp_prop.get("tournament"), pp_prop.get("description"),
        )
        out["surface"], out["court"] = surface, court

        # Match the selected player
        player = match_player(pp_prop.get("player_name") or "", tour=tour)
        if not player:
            out["match_note"] = "Player data unavailable"
            return out
        out["player"] = player

        # Match the opponent if PrizePicks supplied one. If not, we can't run
        # the model — but we still return the matched player + a clear note.
        opp_name = pp_prop.get("opponent_name")
        opponent = match_player(opp_name, tour=tour) if opp_name else None
        if not opponent:
            out["match_note"] = "Opponent not listed by PrizePicks"
            return out
        out["opponent"] = opponent
        out["matched"]  = True

        # Run the existing prop projection endpoint inline. Builds the same
        # PropRequest the user would have sent from the Prop Projection tab.
        req = PropRequest(
            player_id=str(player["id"]),
            opponent_id=str(opponent["id"]),
            player_name=player.get("name") or pp_prop.get("player_name") or "",
            opponent_name=opponent.get("name") or opp_name or "",
            tour=tour,
            surface=surface,
            court=court or "",
            prop_type=pp_prop["prop_type"],
            prop_line=float(pp_prop["prop_line"]),
            player_rank=player.get("currentRank"),
            opponent_rank=opponent.get("currentRank"),
        )
        result = await prop_calculate(req)
        out["result"]           = result
        out["model_projection"] = result.get("model_projection")
        out["lean"]             = result.get("lean")
        out["confidence"]       = result.get("confidence") or 0
        if out["model_projection"] is not None:
            out["edge"] = round(float(out["model_projection"]) - float(pp_prop["prop_line"]), 2)
    except HTTPException as exc:
        out["error"] = f"projection failed: {exc.detail}"
    except Exception as exc:
        logger.exception("[Board] analyze one prop failed: %s", exc)
        out["error"] = str(exc)
    return out


@app.post("/api/board/analyze")
async def board_analyze(req: BoardAnalyzeRequest):
    """
    Run the full Baseline projection for each prop in `req.props`. Players
    and opponents are matched by fuzzy name match. Heavy parallelism is
    capped via a semaphore so we don't hammer Sofascore.
    """
    if not req.props:
        return {"analyzed": [], "n_total": 0, "n_matched": 0}

    # Heuristic tour selection: WTA leagues from PP carry the literal string
    # 'WTA' in the league name. Everything else maps to ATP.
    def _tour_for_league(name: str) -> str:
        return "WTA" if name and "wta" in str(name).lower() else "ATP"

    sem = asyncio.Semaphore(5)

    async def _run(pp_prop):
        tour = _tour_for_league(pp_prop.get("league"))
        if req.tour_filter and tour != req.tour_filter.upper():
            return None
        async with sem:
            return await _analyze_one_prop(pp_prop, tour)

    raw_results = await asyncio.gather(*(_run(p) for p in req.props))
    analyzed = [r for r in raw_results if r is not None]

    return {
        "analyzed":   analyzed,
        "n_total":    len(req.props),
        "n_matched":  sum(1 for r in analyzed if r.get("matched")),
        "n_projected": sum(1 for r in analyzed
                            if r.get("model_projection") is not None),
    }


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
