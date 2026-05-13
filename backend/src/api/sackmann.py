import pandas as pd
import requests
import streamlit as st
from io import StringIO
from datetime import datetime

ATP_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
WTA_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"


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
