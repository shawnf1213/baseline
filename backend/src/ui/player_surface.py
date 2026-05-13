import streamlit as st
from src.api.sofascore_client import get_player_stats_by_surface, ts_to_date_str
from src.calculations.archetypes import classify_archetype, get_archetype_color, get_archetype_description
from src.ui.components import (
    stat_card, surface_badge, form_dots, section_header, no_data, empty_prompt,
)
from src.constants import ARCHETYPE_COLORS


def _fmt(val, decimals=1, suffix=""):
    if val is None:
        return "—"
    return f"{val:.{decimals}f}{suffix}"


def _stat_row(cols, label, all_val, hard_val, clay_val, grass_val, unit="", accent_fn=None):
    items = [all_val, hard_val, clay_val, grass_val]
    for col, val in zip(cols, items):
        with col:
            v_str = _fmt(val, 1, unit) if val is not None else "—"
            accent = accent_fn(val) if (accent_fn and val is not None) else False
            color = "#00E676" if accent else "#FF4444" if (unit == "%" and val is not None and val < 40) else "#FFFFFF"
            st.markdown(
                f'<div style="font-size:16px;font-weight:700;color:{color};font-variant-numeric:tabular-nums">{v_str}</div>',
                unsafe_allow_html=True,
            )


def render(tour: str) -> None:
    player_id = st.session_state.get("main_id")
    player_name = st.session_state.get("main_name")

    if not player_id:
        empty_prompt(
            "Search for a player above",
            "Type a player's name to load their surface analytics",
        )
        return

    with st.spinner(f"Loading stats for {player_name}…"):
        data = get_player_stats_by_surface(player_id, tour)

    if not data or data.get("All", {}).get("matches_played", 0) == 0:
        no_data(
            "No match data found",
            "Matchstat may not have recent events for this player. Try a different player or tour.",
        )
        return

    all_s = data.get("All", {})
    hard_s = data.get("Hard", {})
    clay_s = data.get("Clay", {})
    grass_s = data.get("Grass", {})
    form = data.get("form", [])

    archetype = classify_archetype(all_s, tour)
    arch_color = ARCHETYPE_COLORS.get(archetype, "#AAAAAA")
    arch_desc = get_archetype_description(archetype)

    # ── Header ──────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:20px">'
        f'<div style="font-size:20px;font-weight:800">{player_name}</div>'
        f'<span class="archetype-badge" style="background:{arch_color}20;color:{arch_color};'
        f'border:1px solid {arch_color}40">{archetype}</span>'
        f'</div>'
        f'<div style="font-size:13px;color:#888;margin-bottom:4px">{arch_desc}</div>',
        unsafe_allow_html=True,
    )

    # ── Last 10 form ─────────────────────────────────────────────────────────
    if form:
        section_header("Last 10 Match Form")
        wins = sum(1 for m in form if m.get("won"))
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:4px">'
            f'{form_dots(form)}'
            f'<span style="color:#AAAAAA;font-size:12px">{wins}W – {len(form)-wins}L last {len(form)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Surface Win Rate ─────────────────────────────────────────────────────
    section_header("Win Rate by Surface")
    c1, c2, c3, c4 = st.columns(4)
    for col, label, sdata in [
        (c1, "All Surfaces", all_s),
        (c2, "Hard", hard_s),
        (c3, "Clay", clay_s),
        (c4, "Grass", grass_s),
    ]:
        mp = sdata.get("matches_played", 0) or 0
        wr = sdata.get("win_rate")
        wr_str = _fmt(wr, 0, "%") if wr is not None else "—"
        color = "#00E676" if (wr or 0) >= 55 else "#FF4444" if (wr or 0) < 45 else "#FFFFFF"
        with col:
            st.markdown(
                f'<div class="stat-card">'
                f'<div class="stat-label">{label}</div>'
                f'<div class="stat-value" style="color:{color}">{wr_str}</div>'
                f'<div class="stat-sub">{mp} matches</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Stats grid ───────────────────────────────────────────────────────────
    section_header("Surface Stats Breakdown")

    headers = ["Stat", "All", "Hard", "Clay", "Grass"]
    h_cols = st.columns([2.2, 1, 1, 1, 1])
    for col, h in zip(h_cols, headers):
        with col:
            st.markdown(
                f'<div style="font-size:11px;color:#555;text-transform:uppercase;'
                f'letter-spacing:.06em;padding:4px 0;border-bottom:1px solid #1e1e1e">{h}</div>',
                unsafe_allow_html=True,
            )

    STATS = [
        ("Aces / Match", "aces", 1, ""),
        ("Double Faults / Match", "double_faults", 1, ""),
        ("1st Serve %", "first_serve_pct", 0, "%"),
        ("1st Serve Pts Won", "first_serve_pts_won", 0, "%"),
        ("2nd Serve Pts Won", "second_serve_pts_won", 0, "%"),
        ("Ret Pts Won (1st Srv)", "return_first_serve_pts_won", 0, "%"),
        ("Ret Pts Won (2nd Srv)", "return_second_serve_pts_won", 0, "%"),
        ("BP Converted", "bp_converted", 0, "%"),
        ("BP Saved", "bp_saved", 0, "%"),
    ]

    for stat_label, key, decimals, unit in STATS:
        row_cols = st.columns([2.2, 1, 1, 1, 1])
        with row_cols[0]:
            st.markdown(
                f'<div style="font-size:12px;color:#AAAAAA;padding:8px 0;'
                f'border-bottom:1px solid #111">{stat_label}</div>',
                unsafe_allow_html=True,
            )
        for col, sdata in zip(row_cols[1:], [all_s, hard_s, clay_s, grass_s]):
            val = sdata.get(key)
            val_str = _fmt(val, decimals, unit) if val is not None else "—"
            if val is not None and unit == "%":
                color = "#00E676" if val >= 60 else "#FF4444" if val < 40 else "#CCCCCC"
            else:
                color = "#FFFFFF"
            with col:
                st.markdown(
                    f'<div style="font-size:14px;font-weight:600;color:{color};'
                    f'padding:8px 0;border-bottom:1px solid #111;'
                    f'font-variant-numeric:tabular-nums">{val_str}</div>',
                    unsafe_allow_html=True,
                )

    # ── Archetype detail cards ────────────────────────────────────────────────
    section_header("Serve Profile")
    c1, c2, c3 = st.columns(3)
    with c1:
        aces_val = all_s.get("aces")
        st.markdown(stat_card("Aces / Match", _fmt(aces_val, 1), accent=bool(aces_val and aces_val > 8)), unsafe_allow_html=True)
    with c2:
        df_val = all_s.get("double_faults")
        st.markdown(stat_card("Double Faults / Match", _fmt(df_val, 1), warn=bool(df_val and df_val > 5)), unsafe_allow_html=True)
    with c3:
        fs_val = all_s.get("first_serve_pts_won")
        st.markdown(stat_card("1st Serve Pts Won", _fmt(fs_val, 0), unit="%", accent=bool(fs_val and fs_val > 75)), unsafe_allow_html=True)

    section_header("Return Profile")
    c1, c2, c3 = st.columns(3)
    with c1:
        r1 = all_s.get("return_first_serve_pts_won")
        st.markdown(stat_card("Ret Pts Won (1st)", _fmt(r1, 0), unit="%", accent=bool(r1 and r1 > 35)), unsafe_allow_html=True)
    with c2:
        r2 = all_s.get("return_second_serve_pts_won")
        st.markdown(stat_card("Ret Pts Won (2nd)", _fmt(r2, 0), unit="%", accent=bool(r2 and r2 > 50)), unsafe_allow_html=True)
    with c3:
        bpc = all_s.get("bp_converted")
        st.markdown(stat_card("BP Converted", _fmt(bpc, 0), unit="%", accent=bool(bpc and bpc > 45)), unsafe_allow_html=True)

    # ── Match schedule by surface ─────────────────────────────────────────────
    surface_sel = st.selectbox(
        "View match history on surface",
        ["All", "Hard", "Clay", "Grass"],
        key="surface_schedule_select",
    )

    if surface_sel == "All":
        matches = data.get("all_matches", [])
    else:
        matches = data.get(f"{surface_sel}_matches", [])

    if matches:
        section_header(f"Match History — {surface_sel} (Last 3 Years)")
        wins = sum(1 for m in matches if m.get("won"))
        total = len(matches)
        rows_html = ""
        for m in matches[:50]:
            won = m.get("won", False)
            pill = '<span class="pill-w">W</span>' if won else '<span class="pill-l">L</span>'
            date_str = m.get("date") or ts_to_date_str(m.get("timestamp", 0))
            surf = m.get("surface", "—")
            surf_cls = {"Hard": "sb-hard", "Clay": "sb-clay", "Grass": "sb-grass"}.get(surf, "sb-unknown")
            score = m.get("score", "—") or "—"
            rows_html += (
                f"<tr>"
                f"<td>{date_str}</td>"
                f"<td>{m.get('tournament','—')}</td>"
                f"<td><span class='surface-badge {surf_cls}'>{surf}</span></td>"
                f"<td>{pill}</td>"
                f"<td>{m.get('opponent_name','—')}</td>"
                f"<td style='font-variant-numeric:tabular-nums;color:#AAAAAA'>{score}</td>"
                f"</tr>"
            )
        st.markdown(
            f'<div style="font-size:12px;color:#AAAAAA;margin-bottom:6px">'
            f'{wins}W – {total - wins}L · {total} matches tracked</div>'
            f'<div class="h2h-table-wrap"><table class="h2h-table">'
            f'<thead><tr><th>Date</th><th>Tournament</th><th>Surface</th>'
            f'<th>Result</th><th>Opponent</th><th>Score</th></tr></thead>'
            f'<tbody>{rows_html}</tbody></table></div>',
            unsafe_allow_html=True,
        )
