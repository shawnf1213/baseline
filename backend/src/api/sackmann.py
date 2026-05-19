import re
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)

# ── Original H2H URL templates (kept for backward compat) ────────────────────
ATP_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
WTA_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"

# ── Historical player-stats URL templates (2015-2020 only) ───────────────────
ATP_TOUR_URL  = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
ATP_CHALL_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_qual_chall_{year}.csv"
WTA_TOUR_URL  = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"
WTA_ITF_URL   = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_qual_itf_{year}.csv"

SACKMANN_MIN_YEAR = 2015
SACKMANN_MAX_YEAR = 2020
YEARS_TO_FETCH    = list(range(SACKMANN_MIN_YEAR, SACKMANN_MAX_YEAR + 1))

# Module-level cache: {key: (fetched_at_ts, data)}
# Historical data never changes so 7-day TTL is conservative.
_PLAYER_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 7 * 24 * 3600   # 7 days

# Sofascore → Sackmann name overrides (add as discovered from logs)
_NAME_OVERRIDES: Dict[str, str] = {}


# ── Name normalisation ────────────────────────────────────────────────────────
def normalize_name_for_sackmann(sofascore_name: str) -> str:
    """Return the Sackmann CSV name for a Sofascore display name."""
    return _NAME_OVERRIDES.get(sofascore_name, sofascore_name)


# ── Low-level CSV fetch ───────────────────────────────────────────────────────
def _fetch_csv(url: str) -> Optional[pd.DataFrame]:
    """Fetch a single Sackmann CSV. Returns None on any error."""
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return pd.read_csv(StringIO(r.text), low_memory=False)
        logger.debug("SACKMANN_404 | url=%s | status=%d", url, r.status_code)
    except Exception as e:
        logger.debug("SACKMANN_FETCH_ERR | url=%s | %s", url, e)
    return None


# ── Score parser ──────────────────────────────────────────────────────────────
def _parse_score_to_games(score_str: str) -> Optional[int]:
    """Sum all set games from a score like '6-3 7-6(4) 3-6'. Returns None if unparseable."""
    try:
        total = 0
        sets  = re.findall(r"(\d+)-(\d+)", str(score_str))
        for w, l in sets:
            total += int(w) + int(l)
        return total if total > 0 else None
    except Exception:
        return None


# ── Row parser ────────────────────────────────────────────────────────────────
def _parse_sackmann_row(row: "pd.Series", player_name: str, result: str) -> Optional[dict]:
    """
    Convert one Sackmann CSV row to the internal match dict.
    All percentages are stored as 0-100 scale to match TA/SS conventions.
    Returns None if the row is missing key serve data (svpt == 0).
    """
    try:
        is_winner = result == "W"
        px  = "w_" if is_winner else "l_"
        opx = "l_" if is_winner else "w_"

        def _f(col, fallback=0.0):
            v = row.get(col)
            try:
                return float(v) if v is not None and str(v).strip() not in ("", "nan") else fallback
            except (ValueError, TypeError):
                return fallback

        svpt      = _f(f"{px}svpt")
        first_in  = _f(f"{px}1stIn")
        first_won = _f(f"{px}1stWon")
        second_won= _f(f"{px}2ndWon")
        sv_gms    = _f(f"{px}SvGms")
        bp_saved  = _f(f"{px}bpSaved")
        bp_faced  = _f(f"{px}bpFaced")
        aces      = _f(f"{px}ace")       # Sackmann column is 'w_ace' not 'w_aces'
        dfs       = _f(f"{px}df")

        opp_svpt      = _f(f"{opx}svpt")
        opp_first_in  = _f(f"{opx}1stIn")
        opp_first_won = _f(f"{opx}1stWon")
        opp_second_won= _f(f"{opx}2ndWon")
        opp_bp_faced  = _f(f"{opx}bpFaced")
        opp_bp_saved  = _f(f"{opx}bpSaved")

        # Skip rows with no serve data
        if svpt == 0:
            return None

        second_in     = max(svpt - first_in, 0.0)
        opp_second_in = max(opp_svpt - opp_first_in, 0.0)

        # Player serve stats (percentages × 100)
        first_serve_pct    = (first_in  / svpt      * 100) if svpt > 0       else None
        first_serve_won_pct= (first_won / first_in  * 100) if first_in > 0   else None
        second_serve_won_pct=(second_won/ second_in * 100) if second_in > 0  else None
        bp_saved_pct       = (bp_saved  / bp_faced  * 100) if bp_faced > 0   else None

        # BP won by this player = opponent's BPs they failed to save
        bp_won          = opp_bp_faced - opp_bp_saved
        bp_conv_pct     = (bp_won / opp_bp_faced * 100) if opp_bp_faced > 0  else None

        # Return stats (player's return pts won against opponent's serve)
        ret_1st_pct = (1 - opp_first_won / opp_first_in) * 100  if opp_first_in  > 0 else None
        ret_2nd_pct = (1 - opp_second_won/ opp_second_in)* 100  if opp_second_in > 0 else None

        score      = str(row.get("score", ""))
        total_games= _parse_score_to_games(score)

        surface_raw = str(row.get("surface", "")).lower()
        if   "clay"   in surface_raw:                       surface = "Clay"
        elif "grass"  in surface_raw:                       surface = "Grass"
        elif "hard"   in surface_raw or "carpet" in surface_raw: surface = "Hard"
        else:                                               surface = None

        if surface is None:
            return None

        date_raw = str(row.get("tourney_date", ""))
        try:
            dt = datetime.strptime(date_raw[:8], "%Y%m%d")
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = date_raw

        return {
            "date":                 date_str,
            "tournament":           str(row.get("tourney_name", "")),
            "surface":              surface,
            "result":               result,
            "opponent":             str(row.get("loser_name" if is_winner else "winner_name", "")),
            "score":                score,
            "aces":                 aces,
            "double_faults":        dfs,
            "svpt":                 svpt,
            "first_serve_pct":      first_serve_pct,
            "first_serve_won_pct":  first_serve_won_pct,
            "second_serve_won_pct": second_serve_won_pct,
            "bp_faced":             bp_faced,
            "bp_saved":             bp_saved,
            "bp_saved_pct":         bp_saved_pct,
            "bp_won":               bp_won,
            "bp_conv_pct":          bp_conv_pct,
            "opp_bp_faced":         opp_bp_faced,
            "ret_pts_won_1st":      ret_1st_pct,
            "ret_pts_won_2nd":      ret_2nd_pct,
            "total_games":          total_games,
            "sv_gms":               sv_gms,
            "source":               "sackmann",
        }

    except Exception as e:
        logger.debug("SACKMANN_ROW_PARSE_ERR | player=%s | %s", player_name, e)
        return None


# ── Player data loader ────────────────────────────────────────────────────────
def load_player_sackmann_data(player_name: str, tour: str = "ATP") -> List[dict]:
    """
    Load all Sackmann CSV matches for player_name from 2015-2020.
    Searches both main-tour and challenger/ITF files in parallel.
    Result is cached in memory for 7 days (data never changes).

    Returns a list of match dicts sorted newest first.
    """
    sackmann_name = normalize_name_for_sackmann(player_name)
    cache_key     = f"{sackmann_name.lower().replace(' ','_')}_{tour.upper()}"

    # Check cache
    cached = _PLAYER_CACHE.get(cache_key)
    if cached:
        ts, data = cached
        if time.time() - ts < _CACHE_TTL:
            return data

    tour_up = tour.upper()
    if tour_up == "ATP":
        url_pairs = [(ATP_TOUR_URL.format(year=y), ATP_CHALL_URL.format(year=y))
                     for y in YEARS_TO_FETCH]
    else:
        url_pairs = [(WTA_TOUR_URL.format(year=y), WTA_ITF_URL.format(year=y))
                     for y in YEARS_TO_FETCH]

    all_urls = [u for pair in url_pairs for u in pair]   # flat list

    # Fetch all CSVs in parallel (up to 12 concurrent requests)
    dfs: Dict[str, Optional[pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=min(12, len(all_urls))) as ex:
        fut_map = {ex.submit(_fetch_csv, url): url for url in all_urls}
        for fut in as_completed(fut_map):
            dfs[fut_map[fut]] = fut.result()

    matches: List[dict] = []
    name_lower = sackmann_name.lower()

    for url, df in dfs.items():
        if df is None or df.empty:
            continue
        if "winner_name" not in df.columns or "loser_name" not in df.columns:
            continue

        won_mask  = df["winner_name"].str.lower().str.contains(name_lower, na=False, regex=False)
        lost_mask = df["loser_name" ].str.lower().str.contains(name_lower, na=False, regex=False)

        for _, row in df[won_mask ].iterrows():
            m = _parse_sackmann_row(row, sackmann_name, "W")
            if m:
                matches.append(m)

        for _, row in df[lost_mask].iterrows():
            m = _parse_sackmann_row(row, sackmann_name, "L")
            if m:
                matches.append(m)

    # Deduplicate (same match can appear in both tour + chall files for some rounds)
    seen: set = set()
    unique: List[dict] = []
    for m in matches:
        key = (m["date"], m["opponent"], m["score"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    unique.sort(key=lambda x: x["date"], reverse=True)

    if not unique:
        logger.warning(
            "SACKMANN_NO_MATCHES | player=%r | tour=%s | searched_name=%r",
            player_name, tour, sackmann_name,
        )
    else:
        logger.info(
            "SACKMANN_LOADED | player=%r | tour=%s | total=%d | years=2015-2020",
            sackmann_name, tour, len(unique),
        )

    _PLAYER_CACHE[cache_key] = (time.time(), unique)
    return unique


# ── Surface adjustment factors (all-surface → specific surface) ───────────────
_SURFACE_ADJ: Dict[str, Dict[str, float]] = {
    "Clay":  {"aces":       0.75, "double_faults": 1.10, "bp_conv_pct": 1.05},
    "Grass": {"aces":       1.35, "double_faults": 0.95, "bp_conv_pct": 0.95},
    "Hard":  {},
}


def apply_surface_adjustments(stats: Optional[dict], surface: str) -> Optional[dict]:
    """Apply surface-specific multipliers to all-surface Sackmann stats."""
    if not stats:
        return None
    out = dict(stats)
    for field, factor in _SURFACE_ADJ.get(surface, {}).items():
        if out.get(field) is not None:
            out[field] = round(out[field] * factor, 4)
    return out


# ── Aggregation ───────────────────────────────────────────────────────────────
def aggregate_sackmann_stats(
    matches: List[dict],
    surface_filter: Optional[str] = None,
) -> Optional[dict]:
    """
    Aggregate a list of Sackmann match dicts into a single stats dict.
    All percentage values are on the 0-100 scale to match TA/SS conventions.
    Returns None if no matches survive the surface filter.
    """
    filtered = [m for m in matches if m["surface"] == surface_filter] \
               if surface_filter else list(matches)
    if not filtered:
        return None

    def _avg(vals):
        clean = [v for v in vals if v is not None]
        return round(sum(clean) / len(clean), 4) if clean else None

    wins = sum(1 for m in filtered if m["result"] == "W")
    win_rate_pct = round(wins / len(filtered) * 100, 2) if filtered else 0.0

    result = {
        "matches":              len(filtered),
        "win_rate":             win_rate_pct,   # 0-100 scale
        "aces":                 _avg([m["aces"]               for m in filtered]),
        "double_faults":        _avg([m["double_faults"]       for m in filtered]),
        "first_serve_pct":      _avg([m["first_serve_pct"]     for m in filtered]),
        "first_serve_pts_won":  _avg([m["first_serve_won_pct"] for m in filtered]),
        "second_serve_pts_won": _avg([m["second_serve_won_pct"]for m in filtered]),
        "bp_converted":         _avg([m["bp_conv_pct"]         for m in filtered]),
        "bp_saved":             _avg([m["bp_saved_pct"]         for m in filtered]),
        "bp_faced_per_match":   _avg([m["opp_bp_faced"]         for m in filtered]),
        "bp_won_per_match":     _avg([m["bp_won"]               for m in filtered]),
        "return_first_serve_pts_won":  _avg([m["ret_pts_won_1st"] for m in filtered]),
        "return_second_serve_pts_won": _avg([m["ret_pts_won_2nd"] for m in filtered]),
        "total_games_per_match":       _avg([m["total_games"]     for m in filtered]),
        "source": "sackmann_historical",
    }

    # WIN_RATE_DEBUG — catch 0% win rate for players with substantial match counts
    if result["win_rate"] == 0.0 and len(filtered) > 5:
        logger.warning(
            "WIN_RATE_DEBUG | sackmann | wins=0 | total=%d | surface=%s | "
            "POSSIBLE_PARSE_ERROR",
            len(filtered), surface_filter or "All",
        )

    return result


# ── Chart log builder (bar chart fallback) ────────────────────────────────────
def build_sackmann_chart_log(
    matches: List[dict],
    surface: str,
    limit: int = 10,
) -> List[dict]:
    """
    Build a chart-compatible log from Sackmann historical matches.
    Format mirrors the Sofascore surface_log so the frontend can render it directly.
    """
    surf_matches = [m for m in matches if m["surface"] == surface]
    out = []
    for m in surf_matches[:limit]:
        opp_parts = m["opponent"].split()
        out.append({
            "date":                 m["date"],
            "date_ts":              0,
            "tournament":           m["tournament"],
            "surface":              m["surface"],
            "opponent":             m["opponent"],
            "opponent_abbr":        opp_parts[-1] if opp_parts else m["opponent"],
            "won":                  m["result"] == "W",
            "score":                m["score"],
            "total_match_games":    m["total_games"],
            "aces":                 m["aces"],
            "double_faults":        m["double_faults"],
            "bp_converted_count":   m["bp_won"],
            "bp_converted":         m["bp_conv_pct"],
            "bp_faced_count":       m["opp_bp_faced"],
            "first_serve_pts_won":  m["first_serve_won_pct"],
            "second_serve_pts_won": m["second_serve_won_pct"],
            "source":               "sackmann_historical",
        })
    return out


def _fetch_year(url_template: str, year: int) -> pd.DataFrame:
    try:
        resp = requests.get(url_template.format(year=year), timeout=15)
        if resp.status_code == 200:
            return pd.read_csv(StringIO(resp.text), low_memory=False)
    except Exception:
        pass
    return pd.DataFrame()


def fetch_matches_df(tour: str, years: list) -> pd.DataFrame:
    cache_key = f"sackmann_{tour}_{min(years)}_{max(years)}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    url_template = ATP_URL if tour == "ATP" else WTA_URL
    dfs = [_fetch_year(url_template, y) for y in years]
    dfs = [d for d in dfs if not d.empty]

    if not dfs:
        st.session_state[cache_key] = pd.DataFrame()
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    st.session_state[cache_key] = combined
    return combined


def _name_matches(series: pd.Series, player_name: str) -> pd.Series:
    last = player_name.strip().split()[-1].lower()
    return series.str.lower().str.contains(last, na=False)


def get_h2h_matches(tour: str, p1_name: str, p2_name: str, years_back: int = 8) -> pd.DataFrame:
    current_year = datetime.now().year
    years = list(range(current_year - years_back, current_year + 1))
    df = fetch_matches_df(tour, years)
    if df.empty:
        return pd.DataFrame()

    mask = (
        (_name_matches(df["winner_name"], p1_name) & _name_matches(df["loser_name"], p2_name)) |
        (_name_matches(df["winner_name"], p2_name) & _name_matches(df["loser_name"], p1_name))
    )
    h2h = df[mask].copy()
    if h2h.empty:
        return pd.DataFrame()

    if "tourney_date" in h2h.columns:
        h2h["match_date"] = pd.to_datetime(h2h["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
        h2h = h2h.sort_values("match_date", ascending=False)

    return h2h


def get_h2h_summary(tour: str, p1_name: str, p2_name: str, surface: str = None) -> dict:
    df = get_h2h_matches(tour, p1_name, p2_name)
    if df.empty:
        return {"total": 0, "p1_wins": 0, "p2_wins": 0, "surface_matches": 0,
                "surface_p1_wins": 0, "surface_p2_wins": 0, "matches": pd.DataFrame()}

    p1_last = p1_name.strip().split()[-1].lower()

    df_surface = df[df["surface"] == surface].copy() if surface else df.copy()

    total = len(df)
    p1_wins = _name_matches(df["winner_name"], p1_name).sum()
    p2_wins = total - p1_wins

    surf_total = len(df_surface)
    surf_p1_wins = _name_matches(df_surface["winner_name"], p1_name).sum() if surf_total > 0 else 0
    surf_p2_wins = surf_total - surf_p1_wins

    return {
        "total": total,
        "p1_wins": int(p1_wins),
        "p2_wins": int(p2_wins),
        "surface_matches": surf_total,
        "surface_p1_wins": int(surf_p1_wins),
        "surface_p2_wins": int(surf_p2_wins),
        "matches": df,
        "surface_matches_df": df_surface,
    }


def get_h2h_stat_avg(tour: str, p1_name: str, p2_name: str, surface: str = None) -> dict:
    summary = get_h2h_summary(tour, p1_name, p2_name, surface)
    df = summary.get("surface_matches_df" if surface else "matches", pd.DataFrame())
    if df.empty or len(df) < 1:
        return {}

    stat_cols = ["w_ace", "l_ace", "w_df", "l_df", "w_svpt", "l_svpt",
                 "w_1stIn", "l_1stIn", "w_1stWon", "l_1stWon",
                 "w_2ndWon", "l_2ndWon", "w_bpSaved", "l_bpSaved",
                 "w_bpFaced", "l_bpFaced"]

    avail = [c for c in stat_cols if c in df.columns]
    if not avail:
        return {"games_avg": _calc_avg_games(df)}

    p1_last = p1_name.strip().split()[-1].lower()
    p1_winner_mask = df["winner_name"].str.lower().str.contains(p1_last, na=False)

    avgs = {}
    for _, row in df.iterrows():
        is_p1_winner = p1_last in str(row.get("winner_name", "")).lower()
        prefix = "w" if is_p1_winner else "l"

        if f"{prefix}_ace" in df.columns:
            avgs.setdefault("ace", []).append(row.get(f"{prefix}_ace", 0) or 0)
        if f"{prefix}_df" in df.columns:
            avgs.setdefault("df", []).append(row.get(f"{prefix}_df", 0) or 0)

    result = {k: sum(v) / len(v) for k, v in avgs.items() if v}
    result["games_avg"] = _calc_avg_games(df)
    return result


def _calc_avg_games(df: pd.DataFrame) -> float:
    if "score" not in df.columns or df.empty:
        return 0.0
    total_games = []
    for score in df["score"].dropna():
        try:
            sets = str(score).split()
            games = 0
            for s in sets:
                parts = s.replace("(", " ").split()
                if "-" in parts[0]:
                    a, b = parts[0].split("-")[:2]
                    games += int(a) + int(b)
            if games > 0:
                total_games.append(games)
        except Exception:
            pass
    return sum(total_games) / len(total_games) if total_games else 0.0


def format_h2h_table(df: pd.DataFrame, p1_name: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    p1_last = p1_name.strip().split()[-1].lower()
    rows = []
    for _, row in df.iterrows():
        is_p1_winner = p1_last in str(row.get("winner_name", "")).lower()
        winner = row.get("winner_name", "")
        loser = row.get("loser_name", "")
        opponent = loser if is_p1_winner else winner

        date_raw = row.get("match_date", None) or row.get("tourney_date", None)
        try:
            if hasattr(date_raw, "strftime"):
                date_str = date_raw.strftime("%b %d %Y")
            else:
                dt = pd.to_datetime(str(date_raw), format="%Y%m%d", errors="coerce")
                date_str = dt.strftime("%b %d %Y") if not pd.isna(dt) else str(date_raw)
        except Exception:
            date_str = str(date_raw)

        surface = row.get("surface", "Unknown")
        score = row.get("score", "")
        tourney = row.get("tourney_name", "Unknown")

        rows.append({
            "Match Date": date_str,
            "Tournament": tourney,
            "Surface": surface,
            "Result": "W" if is_p1_winner else "L",
            "Opponent": opponent,
            "Score": score,
        })

    return pd.DataFrame(rows)
