“””
Baseline Tennis Analytics - FastAPI backend.

Wraps all existing Python calculation logic in REST endpoints.
No logic is rewritten here - only imported and exposed via HTTP.

Endpoints:
  POST /api/search          - player search
  POST /api/player/stats    - surface stats + archetype
  POST /api/prop/calculate  - full prop projection
  POST /api/h2h             - head-to-head record + stats
“””

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
from src.api.tennis_abstract import get_player_ta_stats
from src.calculations.archetypes import classify_archetype
from src.calculations.confidence import calculate_confidence
from src.calculations.props import (
    project_aces,
    project_double_faults,
    project_total_games,
    project_break_points,
    generate_scouting_report,
    detect_environment,
    ENVIRONMENT_LABELS,
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
                       proj: float, line: float, cpr: int) -> str:
    parts = []
    pt = req.prop_type

    if pt == “Aces”:
        sup = result.get(“suppression_factor”, 1.0)
        cf  = result.get(“cpr_factor”, 1.0)
        hf  = result.get(“hand_factor”, 1.0) or 1.0
        ag  = result.get(“ace_against_factor”, 1.0) or 1.0
        ph  = result.get(“player_hand”) or “”
        oh  = result.get(“opp_hand”) or “”
        hand_note = “”
        if ph and oh and ph != oh:
            direction = “reduces” if hf < 1.0 else “boosts”
            hand_note = f” Handedness ({ph} vs {oh}) {direction} ace projection x{hf:.2f}.”
        parts.append(
            f”Surface CPR {cpr} applies x{cf:.2f} court-speed factor.”
            f” Opponent return suppression x{sup:.2f}.”
            f” Opponent ace-against factor x{ag:.2f}.{hand_note}”
        )
    elif pt == “Double Faults”:
        pf = result.get(“pressure_factor”, 1.0)
        parts.append(
            f”Opponent return aggression factor x{pf:.2f} -”
            f” {‘increases’ if pf > 1 else ‘reduces’} second-serve pressure.”
        )
    elif pt == “Total Games”:
        env = ENVIRONMENT_LABELS.get(result.get(“environment”, “STANDARD”), “Standard”)
        gps = result.get(“games_per_set”, 0)
        sets = result.get(“expected_sets”, 2.3)
        ch   = result.get(“combined_hold”, 72)
        parts.append(
            f”{env} - combined hold {ch:.0f}% -> {gps:.1f} games/set “
            f”over {sets:.1f} expected sets.”
        )
    elif pt == “Break Points Won”:
        env   = ENVIRONMENT_LABELS.get(result.get(“environment”, “STANDARD”), “Standard”)
        conv  = result.get(“conv_rate_pct”) or 0
        faced = result.get(“opp_bp_faced”) or 0
        base  = (conv / 100) * faced
        ta_src = result.get(“conv_rate_source”, “”)
        src_note = f” (TA {ta_src} data)” if ta_src else “”
        parts.append(
            f”{env} - {conv:.0f}% conversion x {faced:.1f} BPs opponent faces/match”
            f” = {base:.1f} base projection.{src_note}”
        )
        cpr_adj = result.get(“cpr_adj_pct”, 0)
        if cpr_adj:
            sign = “+” if cpr_adj >= 0 else “”
            parts.append(f”Surface CPR {cpr} applies {sign}{cpr_adj:.1f}%.”)

    if line > 0:
        edge = proj - line
        sign = “+” if edge >= 0 else “”
        parts.append(
            f”Model projects {proj:.1f} vs book line {line:.1f} “
            f”(edge {sign}{edge:.1f}) -> {lean}.”
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# GET /api/search  — GET avoids CORS preflight entirely
# POST /api/search — kept for backwards compat
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
        data     = get_player_stats_by_surface(req.player_id, req.tour)
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
        # Fetch data
        p1_data = get_player_stats_by_surface(req.player_id, req.tour)
        p2_data = get_player_stats_by_surface(req.opponent_id, req.tour)
        h2h_summary = get_h2h_summary(
            req.tour, req.player_id, req.opponent_id, surface=req.surface
        )
        h2h_stats = get_h2h_stat_avg(
            req.tour, req.player_id, req.opponent_id, surface=req.surface
        )

        # Surface-first stats, fall back to All
        p1_surface = p1_data.get(req.surface, {}) or {}
        p1_all     = p1_data.get("All", {}) or {}
        p2_surface = p2_data.get(req.surface, {}) or {}
        p2_all     = p2_data.get("All", {}) or {}
        p1_s = p1_surface if p1_surface.get("matches_played", 0) else p1_all
        p2_s = p2_surface if p2_surface.get("matches_played", 0) else p2_all

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

        player_ta, opponent_ta = await asyncio.gather(
            _ta_safe(req.player_name),
            _ta_safe(req.opponent_name),
        )

        # Run projection
        if req.prop_type == "Aces":
            result = project_aces(
                p1_s, p2_s, court_for_calc, h2h_ace_avg, cpr_override=cpr,
                player_ta=player_ta, opponent_ta=opponent_ta,
                tour=req.tour, surface=req.surface,
            )
        elif req.prop_type == "Double Faults":
            result = project_double_faults(p1_s, p2_s, h2h_df_avg)
        elif req.prop_type == "Total Games":
            result = project_total_games(
                p1_s, p2_s, req.surface, h2h_games_avg,
                tour=req.tour, court=court_for_calc
            )
        else:  # Break Points Won
            result = project_break_points(
                p1_s, p2_s,
                h2h_bp_avg=h2h_bp_avg,
                cpr_override=cpr,
                h2h_match_count=h2h_surf_matches,
                player_ta=player_ta,
                opponent_ta=opponent_ta,
                surface=req.surface,
            )

        proj_val = result.get("projection")
        if proj_val is None:
            return {
                "model_projection": None,
                "lean": None,
                "confidence": 0,
                "note": result.get("note", "Insufficient data for this surface/prop combination."),
            }

        # Confidence
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
        )
        confidence = conf_result["confidence"]

        lean = _resolve_lean(proj_val, req.prop_line, result.get("lean", ""))
        confidence = _edge_cap(confidence, proj_val, req.prop_line)

        # Archetypes
        p1_arch = classify_archetype(p1_all, req.tour)
        p2_arch = classify_archetype(p2_all, req.tour)

        # Resolve handedness from TA (may be None if TA unavailable)
        player_hand   = player_ta.get("handedness")   if player_ta   else None
        opponent_hand = opponent_ta.get("handedness") if opponent_ta else None

        # Opponent ace-against (aces the opponent concedes per match as a returner)
        opponent_ace_against = opponent_ta.get("ace_against_per_match") if opponent_ta else None

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
        )

        env_key   = result.get("environment") or detect_environment(p1_s, p2_s)
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
            # Tennis Abstract enrichment (None when TA unavailable)
            "player_handedness":    player_hand,
            "opponent_handedness":  opponent_hand,
            "opponent_ace_against": opponent_ace_against,
            "ta_available":         bool(player_ta or opponent_ta),
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
            },
            "plain_english_explanation": _build_explanation(
                req, result, lean, proj_val, req.prop_line, cpr
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
        }
    except Exception as e:
        logger.error("h2h error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

