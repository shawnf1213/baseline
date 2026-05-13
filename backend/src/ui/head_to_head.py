import streamlit as st
import pandas as pd
from src.api.sofascore_client import get_h2h_summary, format_h2h_table
from src.ui.components import render_player_search, section_header, no_data, empty_prompt, surface_badge


def _win_rate_bar(wins: int, total: int, color: str) -> str:
    if total == 0:
        return '<div style="color:#444;font-size:12px">No matches</div>'
    return (
        f'<div style="font-size:22px;font-weight:800;color:{color}">{wins}</div>'
        f'<div style="font-size:11px;color:#AAAAAA">of {total}</div>'
    )


def _result_pill(result: str) -> str:
    if result == "W":
        return '<span class="pill-w">W</span>'
    return '<span class="pill-l">L</span>'


def _surface_badge_html(surface: str) -> str:
    cls = {"Hard": "sb-hard", "Clay": "sb-clay", "Grass": "sb-grass"}.get(surface, "sb-unknown")
    return f'<span class="surface-badge {cls}">{surface}</span>'


def render(tour: str) -> None:
    p1_id = st.session_state.get("main_id")
    p1_name = st.session_state.get("main_name")

    st.markdown('<div class="section-header" style="margin-top:0">Player 1</div>', unsafe_allow_html=True)
    if p1_id:
        st.markdown(
            f'<div class="player-chip"><div class="player-chip-dot"></div>'
            f'<div class="player-chip-name">{p1_name}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        render_player_search("main", "Player 1 — search above", tour, scope="h2h")

    section_header("Player 2 (Opponent)")
    render_player_search("h2h_p2", "Search opponent…", tour, scope="h2h")

    p2_id = st.session_state.get("h2h_p2_id")
    p2_name = st.session_state.get("h2h_p2_name")

    if not p1_id or not p2_id:
        if not p1_id:
            empty_prompt("Select Player 1 using the search above")
        elif not p2_id:
            empty_prompt("Select an opponent to load H2H data")
        return

    # Surface filter
    section_header("Filters")
    surface_filter = st.selectbox(
        "Surface",
        ["All Surfaces", "Hard", "Clay", "Grass"],
        key="h2h_surface_filter",
        label_visibility="collapsed",
    )
    surface = None if surface_filter == "All Surfaces" else surface_filter

    # Use player IDs directly (Matchstat works on IDs; names used as fallback)
    with st.spinner("Loading H2H data…"):
        summary = get_h2h_summary(tour, str(p1_id), str(p2_id), surface=surface)

    total = summary.get("total", 0)
    p1w = summary.get("p1_wins", 0)
    p2w = summary.get("p2_wins", 0)
    surf_total = summary.get("surface_matches", 0)
    surf_p1w = summary.get("surface_p1_wins", 0)
    surf_p2w = summary.get("surface_p2_wins", 0)

    # ── H2H summary cards ────────────────────────────────────────────────────
    section_header("Head-to-Head Record")
    c1, c2, c3 = st.columns([2, 1, 2])

    with c1:
        color1 = "#00E676" if p1w >= p2w else "#FF4444"
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">{p1_name}</div>'
            f'<div class="stat-value" style="color:{color1};font-size:40px">{p1w}</div>'
            f'<div class="stat-sub">Overall wins</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            f'<div style="text-align:center;padding:24px 0">'
            f'<div style="font-size:28px;font-weight:900;color:#333">vs</div>'
            f'<div style="font-size:11px;color:#444;margin-top:4px">{total} meetings</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with c3:
        color2 = "#00E676" if p2w >= p1w else "#FF4444"
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">{p2_name}</div>'
            f'<div class="stat-value" style="color:{color2};font-size:40px">{p2w}</div>'
            f'<div class="stat-sub">Overall wins</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if total == 0:
        no_data(
            f"No H2H matches found between {p1_name} and {p2_name}",
            "Data sourced from Matchstat Tennis API.",
        )
        return

    # Surface-specific record
    if surface and surf_total > 0:
        section_header(f"On {surface}")
        c1, c2, c3 = st.columns([2, 1, 2])
        with c1:
            sc1 = "#00E676" if surf_p1w >= surf_p2w else "#FF4444"
            st.markdown(
                f'<div class="stat-card" style="text-align:center">'
                f'<div class="stat-label">{p1_name} on {surface}</div>'
                f'<div class="stat-value" style="color:{sc1};font-size:36px">{surf_p1w}</div>'
                f'<div class="stat-sub">{surf_total} surface meetings</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f'<div style="text-align:center;padding:24px 0">'
                f'<div style="font-size:24px;font-weight:900;color:#333">vs</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c3:
            sc2 = "#00E676" if surf_p2w >= surf_p1w else "#FF4444"
            st.markdown(
                f'<div class="stat-card" style="text-align:center">'
                f'<div class="stat-label">{p2_name} on {surface}</div>'
                f'<div class="stat-value" style="color:{sc2};font-size:36px">{surf_p2w}</div>'
                f'<div class="stat-sub">{surf_total} surface meetings</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    elif surface and surf_total == 0:
        st.info(f"No H2H matches found on {surface}. Showing all-surface history.")

    # ── Match history table ──────────────────────────────────────────────────
    section_header("Match History")

    df_to_show = summary.get("surface_matches_df" if surface else "matches", pd.DataFrame())
    if df_to_show is None or df_to_show.empty:
        df_to_show = summary.get("matches", pd.DataFrame())

    if df_to_show is None or df_to_show.empty:
        no_data("No match detail rows available")
        return

    table_df = format_h2h_table(df_to_show, p1_name)
    if table_df is None or table_df.empty:
        no_data("Could not parse match history")
        return

    # Render as HTML table for styled surface badges and result pills
    rows_html = ""
    for _, row in table_df.iterrows():
        surface_cell = _surface_badge_html(row.get("Surface", "Unknown"))
        result_cell = _result_pill(row.get("Result", ""))
        rows_html += (
            f"<tr>"
            f"<td>{row.get('Match Date','')}</td>"
            f"<td>{row.get('Tournament','')}</td>"
            f"<td>{surface_cell}</td>"
            f"<td>{result_cell}</td>"
            f"<td>{row.get('Opponent','')}</td>"
            f"<td style='font-variant-numeric:tabular-nums;color:#AAAAAA'>{row.get('Score','')}</td>"
            f"</tr>"
        )

    table_html = (
        f'<div class="h2h-table-wrap"><table class="h2h-table">'
        f'<thead><tr>'
        f'<th>Match Date</th><th>Tournament</th><th>Surface</th>'
        f'<th>Result</th><th>Opponent</th><th>Score</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)
