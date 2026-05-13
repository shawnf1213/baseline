import streamlit as st
from src.api.sofascore_client import (
    get_player_stats_by_surface,
    get_h2h_summary,
    get_h2h_stat_avg,
    get_tournament_record_modifier,
    ts_to_date_str,
)
from src.calculations.archetypes import classify_archetype
from src.calculations.props import (
    project_aces,
    project_double_faults,
    project_total_games,
    project_break_points,
    generate_scouting_report,
    detect_environment,
    ENVIRONMENT_LABELS,
)
from src.ui.components import (
    render_player_search, section_header, no_data, empty_prompt,
    lean_badge, confidence_bar, stat_card,
)
from src.constants import COURTS_BY_SURFACE, COURT_CPR, CPR_NEUTRAL, GENERIC_SURFACE_CPR, GENERIC_TIER_LABEL
from src.calculations.confidence import calculate_confidence, SECTION_LABELS


def _fmt(val, decimals=1, suffix=""):
    if val is None:
        return "—"
    return f"{val:.{decimals}f}{suffix}"


def _stat_mini(label: str, val, unit: str = "", accent: bool = False) -> str:
    color = "#00E676" if accent else "#FFFFFF"
    val_str = _fmt(val, 0 if unit == "%" else 1, unit) if val is not None else "—"
    return (
        f'<div style="padding:10px 0;border-bottom:1px solid #151515">'
        f'<div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.05em">{label}</div>'
        f'<div style="font-size:15px;font-weight:700;color:{color};font-variant-numeric:tabular-nums">{val_str}</div>'
        f'</div>'
    )


def _render_match_schedule(player_data: dict, surface: str, player_name: str) -> None:
    matches = player_data.get(f"{surface}_matches", [])
    if not matches:
        st.markdown(
            f'<div style="font-size:13px;color:#444;padding:10px 0">No {surface} matches tracked in last 3 years.</div>',
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for m in matches[:30]:
        won = m.get("won", False)
        result_pill = '<span class="pill-w">W</span>' if won else '<span class="pill-l">L</span>'
        date_str = m.get("date") or ts_to_date_str(m.get("timestamp", 0))
        tournament = m.get("tournament", "—")
        opponent = m.get("opponent_name", "—")
        score = m.get("score", "—") or "—"
        surf = m.get("surface", surface)
        surf_cls = {"Hard": "sb-hard", "Clay": "sb-clay", "Grass": "sb-grass"}.get(surf, "sb-unknown")
        rows_html += (
            f"<tr>"
            f"<td>{date_str}</td>"
            f"<td>{tournament}</td>"
            f"<td><span class='surface-badge {surf_cls}'>{surf}</span></td>"
            f"<td>{result_pill}</td>"
            f"<td>{opponent}</td>"
            f"<td style='font-variant-numeric:tabular-nums;color:#AAAAAA'>{score}</td>"
            f"</tr>"
        )

    total = len(matches)
    wins = sum(1 for m in matches if m.get("won"))
    st.markdown(
        f'<div style="font-size:12px;color:#AAAAAA;margin-bottom:6px">'
        f'{wins}W – {total - wins}L on {surface} (last 3 yrs, {total} matches tracked)</div>'
        f'<div class="h2h-table-wrap"><table class="h2h-table">'
        f'<thead><tr><th>Date</th><th>Tournament</th><th>Surface</th>'
        f'<th>Result</th><th>Opponent</th><th>Score</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _confidence_color_label(confidence: int) -> tuple:
    if confidence < 40:
        return "#FF4444", "Low Confidence"
    elif confidence < 66:
        return "#FFA726", "Moderate Confidence"
    elif confidence <= 80:
        return "#00E676", "Good Confidence"
    else:
        return "#69FF47", "High Confidence"


def _confidence_card_html(confidence: int) -> str:
    color, label = _confidence_color_label(confidence)
    return (
        f'<div style="margin-top:8px">'
        f'<div style="font-size:24px;font-weight:800;color:{color};line-height:1">{confidence}%</div>'
        f'<div style="font-size:11px;color:{color};opacity:.85;margin-top:3px;letter-spacing:.03em">{label}</div>'
        f'<div style="margin-top:8px;height:4px;background:#1a1a1a;border-radius:2px">'
        f'<div style="width:{confidence}%;height:100%;background:{color};border-radius:2px;'
        f'transition:width .4s ease"></div></div>'
        f'</div>'
    )


def _render_confidence_breakdown(breakdown: dict, confidence: int) -> None:
    raw_total = sum(info["score"] for info in breakdown.values())
    rows_html = ""
    for key, info in breakdown.items():
        score = info["score"]
        max_s = info["max"]
        lbl = SECTION_LABELS.get(key, key.replace("_", " ").title())
        if score > 0:
            score_color = "#00E676"
            icon = "✓"
        elif score < 0:
            score_color = "#FF4444"
            icon = "↓"
        else:
            score_color = "#555555"
            icon = "—"
        sign = "+" if score > 0 else ""
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'padding:7px 0;border-bottom:1px solid #151515;gap:12px">'
            f'<div style="display:flex;gap:8px;align-items:flex-start">'
            f'<span style="color:{score_color};font-size:12px;min-width:12px">{icon}</span>'
            f'<div>'
            f'<div style="font-size:12px;color:#CCCCCC;font-weight:600">{lbl}</div>'
            f'<div style="font-size:11px;color:#555;margin-top:2px">{info["label"]}</div>'
            f'</div></div>'
            f'<div style="font-size:13px;font-weight:700;color:{score_color};'
            f'white-space:nowrap;min-width:55px;text-align:right">'
            f'{sign}{score}<span style="color:#333;font-weight:400">/{max_s}</span></div>'
            f'</div>'
        )
    clamped_note = ""
    if raw_total != confidence:
        clamped_note = (
            f'<div style="font-size:11px;color:#444;margin-top:8px">'
            f'Raw score {raw_total} → clamped to {confidence}% (range 15–95)</div>'
        )
    st.markdown(
        f'<div style="padding:4px 0">{rows_html}{clamped_note}</div>',
        unsafe_allow_html=True,
    )


_ENV_COLORS = {
    "HIGH_BREAK":  "#FF9800",
    "SERVE_DOM":   "#42A5F5",
    "RET_EDGE":    "#00E676",
    "WEAK_SERVE":  "#EF5350",
    "STANDARD":    "#888888",
}

_ENV_DESC = {
    "HIGH_BREAK": "Both players return well and neither holds comfortably — high break rate both ways. Sets will be competitive, service games won't be automatic.",
    "SERVE_DOM":  "Both players hold serve easily and return poorly — sets frequently go deep or reach tiebreaks. Break points are rare and hard to convert.",
    "RET_EDGE":   "Selected player returns well but faces a strong server — creates opportunities but converts at a reduced rate. Opponent serve limits the opportunity pool.",
    "WEAK_SERVE": "Opponent serves poorly so the opportunity pool is large, but selected player's return efficiency is below average — volume compensates for lower conversion.",
    "STANDARD":   "Balanced serve/return dynamics — neither player dominates on serve or return.",
}


def _env_badge(env_key: str) -> str:
    label = ENVIRONMENT_LABELS.get(env_key, "Standard")
    color = _ENV_COLORS.get(env_key, "#888888")
    return (
        f'<span style="display:inline-block;padding:4px 12px;border-radius:14px;'
        f'font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;'
        f'background:{color}22;color:{color};border:1px solid {color}55">'
        f'{label}</span>'
    )


def _srv_ret_comparison(p1_name: str, p2_name: str, p1_stats: dict, p2_stats: dict) -> str:
    def _pct(v):
        return f"{v:.0f}%" if v is not None else "—"

    p1_srv = p1_stats.get("first_serve_pts_won")
    p2_srv = p2_stats.get("first_serve_pts_won")

    p1_r1 = p1_stats.get("return_first_serve_pts_won")
    p1_r2 = p1_stats.get("return_second_serve_pts_won")
    p2_r1 = p2_stats.get("return_first_serve_pts_won")
    p2_r2 = p2_stats.get("return_second_serve_pts_won")

    p1_ret = (p1_r1 + p1_r2) / 2 if p1_r1 is not None and p1_r2 is not None else p1_r1 or p1_r2
    p2_ret = (p2_r1 + p2_r2) / 2 if p2_r1 is not None and p2_r2 is not None else p2_r1 or p2_r2

    return (
        f'<div style="display:grid;grid-template-columns:140px 1fr 1fr;gap:4px 12px;'
        f'margin:10px 0 4px 0;font-size:12px;align-items:center">'
        f'<div></div>'
        f'<div style="color:#888;text-align:center;font-weight:600">{p1_name}</div>'
        f'<div style="color:#888;text-align:center;font-weight:600">{p2_name}</div>'
        f'<div style="color:#555;padding:3px 0">1st Srv Pts Won</div>'
        f'<div style="color:#FFF;text-align:center;font-weight:700">{_pct(p1_srv)}</div>'
        f'<div style="color:#AAA;text-align:center;font-weight:700">{_pct(p2_srv)}</div>'
        f'<div style="color:#555;padding:3px 0">Ret Pts Won (avg)</div>'
        f'<div style="color:#FFF;text-align:center;font-weight:700">{_pct(p1_ret)}</div>'
        f'<div style="color:#AAA;text-align:center;font-weight:700">{_pct(p2_ret)}</div>'
        f'</div>'
    )


def _speed_tier_label(cpr: int) -> str:
    if cpr >= 42:
        return "Fast"
    elif cpr >= 38:
        return "Medium-Fast"
    elif cpr >= 32:
        return "Medium"
    elif cpr >= 27:
        return "Medium-Slow"
    else:
        return "Slow"


def _resolve_lean(effective_proj: float, prop_line: float, model_lean: str) -> str:
    """Always return OVER or UNDER — never NEUTRAL."""
    if prop_line > 0:
        if effective_proj == prop_line:
            return "UNDER"   # tie-break: books shade lines above true expectation
        return "OVER" if effective_proj > prop_line else "UNDER"
    # No line entered — use model lean, but still never Neutral
    if model_lean in ("OVER", "UNDER"):
        return model_lean
    return "UNDER"


def _edge_confidence_cap(confidence: int, effective_proj: float, prop_line: float) -> int:
    """
    Cap confidence based on edge size vs the book line.
    A small projection edge means moderate confidence regardless of data quality.
    90%+ only appears when edge > 15% or data quality is independently very high.
    """
    if prop_line <= 0 or effective_proj <= 0:
        return confidence
    edge_pct = abs(effective_proj - prop_line) / prop_line * 100
    if edge_pct < 5:
        cap = 50
    elif edge_pct < 10:
        cap = 65
    elif edge_pct < 15:
        cap = 80
    else:
        cap = 95   # large edge — let data quality determine confidence fully
    return min(confidence, cap)


def render(tour: str) -> None:
    # ── Player search — always visible ───────────────────────────────────────
    section_header("Players")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        st.markdown(
            '<div style="font-size:12px;color:#AAAAAA;margin-bottom:6px;'
            'text-transform:uppercase;letter-spacing:.06em">Selected Player</div>',
            unsafe_allow_html=True,
        )
        render_player_search("prop_p1", "Search player…", tour, compact=True, scope="props")
    with col_p2:
        st.markdown(
            '<div style="font-size:12px;color:#AAAAAA;margin-bottom:6px;'
            'text-transform:uppercase;letter-spacing:.06em">Opponent</div>',
            unsafe_allow_html=True,
        )
        render_player_search("prop_p2", "Search opponent…", tour, compact=True, scope="props")

    p1_id   = st.session_state.get("prop_p1_id")
    p1_name = st.session_state.get("prop_p1_name", "Player")
    p2_id   = st.session_state.get("prop_p2_id")
    p2_name = st.session_state.get("prop_p2_name", "Opponent")

    if not p1_id or not p2_id:
        st.markdown('<div style="margin-top:16px"></div>', unsafe_allow_html=True)
        if not p1_id:
            empty_prompt("Search for a player", "Select both a player and opponent to generate prop projections")
        else:
            empty_prompt("Search for an opponent", "Select both a player and opponent to generate prop projections")
        return

    # ── Surface + Court — always visible ─────────────────────────────────────
    section_header("Match Setup")
    col_s, col_c = st.columns(2)
    with col_s:
        surface = st.selectbox("Surface", ["Hard", "Clay", "Grass"], key="prop_surface")
    with col_c:
        courts = ["None"] + COURTS_BY_SURFACE.get(surface, [])
        court  = st.selectbox("Court / Tournament", courts, key="prop_court")

    if court == "None":
        cpr         = GENERIC_SURFACE_CPR.get(surface, CPR_NEUTRAL)
        speed_label = GENERIC_TIER_LABEL.get(surface, "Medium")
        speed_card_title = f"{surface} — {speed_label}"
    else:
        cpr         = COURT_CPR.get(court, CPR_NEUTRAL)
        speed_label = _speed_tier_label(cpr)
        speed_card_title = f"{court} — {surface} / {speed_label}"

    st.markdown(
        f'<div class="speed-card">'
        f'<div class="speed-card-label">Surface Speed Tier</div>'
        f'<div class="speed-card-value">{speed_card_title}</div>'
        f'<div class="speed-card-cpr">CPR {cpr}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Prop Type — always visible ────────────────────────────────────────────
    section_header("Prop Type")
    prop_type = st.radio(
        "Select prop",
        ["Aces", "Double Faults", "Total Games", "Break Points Won"],
        horizontal=True,
        key="prop_type_select",
        label_visibility="collapsed",
    )

    # ── Prop Line — always visible ────────────────────────────────────────────
    section_header("Prop Line")
    col_line, col_spacer = st.columns([2, 4])
    with col_line:
        prop_line = st.number_input(
            f"{prop_type} line",
            min_value=0.0, max_value=100.0, value=0.0, step=0.5,
            key="prop_line_input",
            label_visibility="collapsed",
            format="%.1f",
            help="Enter the sportsbook line to compare against the projection",
        )

    # ── Run button ────────────────────────────────────────────────────────────
    st.markdown('<div style="margin-top:8px"></div>', unsafe_allow_html=True)
    run_clicked = st.button(
        "Run Prop Estimate",
        key="prop_run_btn",
        type="primary",
        use_container_width=False,
    )

    # Track which player pair the last run was for; reset if players changed
    current_pair = (p1_id, p2_id)
    if st.session_state.get("prop_run_for") != current_pair:
        st.session_state.prop_run_for = None
        st.session_state.prop_run_requested = False

    if run_clicked:
        st.session_state.prop_run_requested = True
        st.session_state.prop_run_for = current_pair

    if not st.session_state.get("prop_run_requested"):
        return

    # ── Fetch all data — only after Run is clicked ────────────────────────────
    with st.spinner("Analyzing matchup — this takes a few seconds"):
        p1_all_data = get_player_stats_by_surface(str(p1_id), tour)
        p2_all_data = get_player_stats_by_surface(str(p2_id), tour)
        h2h_summary = get_h2h_summary(tour, str(p1_id), str(p2_id), surface=surface)
        h2h_stats   = get_h2h_stat_avg(tour, str(p1_id), str(p2_id), surface=surface)

    p1_surface_stats = p1_all_data.get(surface, {}) or {}
    p1_all_stats     = p1_all_data.get("All", {}) or {}
    p2_surface_stats = p2_all_data.get(surface, {}) or {}
    p2_all_stats     = p2_all_data.get("All", {}) or {}

    p1_arch = classify_archetype(p1_all_stats, tour)
    p2_arch = classify_archetype(p2_all_stats, tour)

    h2h_ace_avg      = h2h_stats.get("ace")
    h2h_df_avg       = h2h_stats.get("df")
    h2h_bp_avg       = h2h_stats.get("bp")
    h2h_games_avg    = h2h_stats.get("games_avg")
    h2h_surf_matches = h2h_summary.get("surface_matches", 0)

    p1_surf_matches = p1_all_data.get(f"{surface}_matches", [])
    p2_surf_matches = p2_all_data.get(f"{surface}_matches", [])
    _has_h2h_surface = h2h_summary.get("surface_matches", 0) > 0
    _has_h2h_other   = (not _has_h2h_surface) and h2h_summary.get("total", 0) > 0

    # ── Surface Stats Comparison ──────────────────────────────────────────────
    section_header("Surface Stats Comparison")
    c1, c2 = st.columns(2)

    with c1:
        st.markdown(
            f'<div style="font-size:13px;font-weight:600;color:#FFFFFF;margin-bottom:10px">'
            f'🎾 {p1_name} — {surface}</div>',
            unsafe_allow_html=True,
        )
        p1_mp = p1_surface_stats.get("matches_played", 0) or 0
        st.markdown(
            f'<div style="font-size:11px;color:#555;margin-bottom:8px">{p1_mp} matches tracked on {surface}</div>',
            unsafe_allow_html=True,
        )
        stats_html = ""
        stats_html += _stat_mini("Aces / Match", p1_surface_stats.get("aces"), accent=bool((p1_surface_stats.get("aces") or 0) > 6))
        stats_html += _stat_mini("Double Faults / Match", p1_surface_stats.get("double_faults"))
        stats_html += _stat_mini("1st Serve %", p1_surface_stats.get("first_serve_pct"), "%")
        stats_html += _stat_mini("1st Serve Pts Won", p1_surface_stats.get("first_serve_pts_won"), "%", accent=bool((p1_surface_stats.get("first_serve_pts_won") or 0) > 75))
        stats_html += _stat_mini("2nd Serve Pts Won", p1_surface_stats.get("second_serve_pts_won"), "%")
        stats_html += _stat_mini("Ret Pts Won (1st Srv)", p1_surface_stats.get("return_first_serve_pts_won"), "%")
        stats_html += _stat_mini("Ret Pts Won (2nd Srv)", p1_surface_stats.get("return_second_serve_pts_won"), "%")
        stats_html += _stat_mini("BP Converted", p1_surface_stats.get("bp_converted"), "%", accent=bool((p1_surface_stats.get("bp_converted") or 0) > 45))
        stats_html += _stat_mini("Win Rate", p1_surface_stats.get("win_rate"), "%")
        st.markdown(stats_html, unsafe_allow_html=True)

    with c2:
        st.markdown(
            f'<div style="font-size:13px;font-weight:600;color:#AAAAAA;margin-bottom:10px">'
            f'🎾 {p2_name} — {surface}</div>',
            unsafe_allow_html=True,
        )
        p2_mp = p2_surface_stats.get("matches_played", 0) or 0
        st.markdown(
            f'<div style="font-size:11px;color:#555;margin-bottom:8px">{p2_mp} matches tracked on {surface}</div>',
            unsafe_allow_html=True,
        )
        stats_html2 = ""
        stats_html2 += _stat_mini("Aces / Match", p2_surface_stats.get("aces"))
        stats_html2 += _stat_mini("Double Faults / Match", p2_surface_stats.get("double_faults"))
        stats_html2 += _stat_mini("1st Serve %", p2_surface_stats.get("first_serve_pct"), "%")
        stats_html2 += _stat_mini("1st Serve Pts Won", p2_surface_stats.get("first_serve_pts_won"), "%")
        stats_html2 += _stat_mini("2nd Serve Pts Won", p2_surface_stats.get("second_serve_pts_won"), "%")
        stats_html2 += _stat_mini("Ret Pts Won (1st Srv)", p2_surface_stats.get("return_first_serve_pts_won"), "%", accent=bool((p2_surface_stats.get("return_first_serve_pts_won") or 0) > 35))
        stats_html2 += _stat_mini("Ret Pts Won (2nd Srv)", p2_surface_stats.get("return_second_serve_pts_won"), "%")
        stats_html2 += _stat_mini("BP Converted", p2_surface_stats.get("bp_converted"), "%")
        stats_html2 += _stat_mini("Win Rate", p2_surface_stats.get("win_rate"), "%")
        st.markdown(stats_html2, unsafe_allow_html=True)

    # ── H2H context ───────────────────────────────────────────────────────────
    h2h_total  = h2h_summary.get("total", 0)
    surf_h2h   = h2h_summary.get("surface_matches", 0)
    if surf_h2h > 0:
        section_header(f"H2H Context on {surface}")
        sp1w = h2h_summary.get("surface_p1_wins", 0)
        sp2w = h2h_summary.get("surface_p2_wins", 0)
        h2h_cells = (
            f'<div style="display:flex;gap:16px;align-items:center">'
            f'<div><div style="font-size:11px;color:#555">{p1_name}</div>'
            f'<div style="font-size:24px;font-weight:800;color:#00E676">{sp1w}</div></div>'
            f'<div style="font-size:18px;color:#333">–</div>'
            f'<div><div style="font-size:11px;color:#555">{p2_name}</div>'
            f'<div style="font-size:24px;font-weight:800;color:#AAAAAA">{sp2w}</div></div>'
            f'<div style="font-size:13px;color:#444;margin-left:12px">{surf_h2h} meetings on {surface}</div>'
            f'</div>'
        )
        if h2h_ace_avg:
            h2h_cells += f'<div style="font-size:12px;color:#AAAAAA;margin-top:6px">Avg aces by {p1_name} in H2H ({surface}): {h2h_ace_avg:.1f}</div>'
        if h2h_games_avg and h2h_games_avg > 0:
            h2h_cells += f'<div style="font-size:12px;color:#AAAAAA">Avg total games in H2H ({surface}): {h2h_games_avg:.1f}</div>'
        st.markdown(f'<div class="stat-card">{h2h_cells}</div>', unsafe_allow_html=True)
    elif h2h_total > 0:
        section_header("H2H Context")
        p1w = h2h_summary.get("p1_wins", 0)
        p2w = h2h_summary.get("p2_wins", 0)
        st.markdown(
            f'<div style="font-size:13px;color:#AAAAAA;padding:10px 14px;background:#111;'
            f'border:1px solid #1e1e1e;border-radius:8px">'
            f'Overall H2H: {p1_name} {p1w}–{p2w} {p2_name} ({h2h_total} meetings). '
            f'No matches found on {surface}.</div>',
            unsafe_allow_html=True,
        )

    # ── Compute projection ────────────────────────────────────────────────────
    section_header("Prop Projection")

    p1_s = p1_surface_stats if p1_surface_stats.get("matches_played", 0) else p1_all_stats
    p2_s = p2_surface_stats if p2_surface_stats.get("matches_played", 0) else p2_all_stats
    court_for_calc = "" if court == "None" else court

    if prop_type == "Aces":
        result = project_aces(p1_s, p2_s, court_for_calc, h2h_ace_avg, cpr_override=cpr)
        matchup_note = (
            f"{p1_name} averages {_fmt(p1_s.get('aces'), 1)} aces/match on {surface}. "
            f"{p2_name} wins {_fmt(p2_s.get('return_first_serve_pts_won'), 0)}% of points on 1st serve "
            f"(suppression factor: ×{result.get('suppression_factor', 1.0):.2f}). "
            f"Court speed CPR {cpr} applies ×{result.get('cpr_factor', 1.0):.2f} multiplier."
        )
    elif prop_type == "Double Faults":
        result = project_double_faults(p1_s, p2_s, h2h_df_avg)
        matchup_note = (
            f"{p1_name} averages {_fmt(p1_s.get('double_faults'), 1)} DFs/match on {surface}. "
            f"Opponent return aggression factor: ×{result.get('pressure_factor', 1.0):.2f}. "
            f"Stronger returners create more second-serve pressure."
        )
    elif prop_type == "Total Games":
        result = project_total_games(p1_s, p2_s, surface, h2h_games_avg, tour=tour, court=court_for_calc)
        _env_key  = result.get("environment", "STANDARD")
        _env_lbl  = ENVIRONMENT_LABELS.get(_env_key, "Standard")
        _gps      = result.get("games_per_set", 0)
        _sets     = result.get("expected_sets", 2.3)
        _ch       = result.get("combined_hold", 72)
        _fmt_str  = result.get("format", "Best of 3")
        _p1s      = result.get("p1_srv", p1_s.get("first_serve_pts_won") or 72)
        _p2s      = result.get("p2_srv", p2_s.get("first_serve_pts_won") or 72)
        _h2h_line = f" H2H avg on {surface}: {_fmt(h2h_games_avg, 1)} games." if h2h_games_avg else ""
        matchup_note = (
            f"{_env_lbl} — {p1_name} holds at {_p1s:.0f}%, {p2_name} at {_p2s:.0f}% on {surface}. "
            f"Combined hold rate {_ch:.0f}% → {_gps:.1f} games/set over {_sets:.1f} expected sets "
            f"({_fmt_str}).{_h2h_line}"
        )
    else:  # Break Points Won
        result = project_break_points(
            p1_s, p2_s,
            h2h_bp_avg=h2h_bp_avg,
            cpr_override=cpr,
            h2h_match_count=h2h_surf_matches,
        )
        _env_key  = result.get("environment", "STANDARD")
        _env_lbl  = ENVIRONMENT_LABELS.get(_env_key, "Standard")
        _conv     = result.get("conv_rate_pct", 0)
        _faced    = result.get("opp_bp_faced", 0)
        _h2h_bp   = result.get("h2h_bp_avg")
        _cpr_adj  = result.get("cpr_adj_pct", 0)
        _cpr_val  = result.get("cpr", cpr)
        _proj     = result.get("projection")
        _adj_sign = "+" if (_cpr_adj or 0) >= 0 else ""
        _h2h_line = (
            f" H2H avg on {surface}: {_h2h_bp:.1f} BPs (blended 30%)."
            if _h2h_bp is not None else ""
        )
        _cpr_line = (
            f" Surface CPR {_cpr_val} adjustment: {_adj_sign}{_cpr_adj:.1f}% → final {_fmt(_proj, 1)}."
            if _cpr_adj else ""
        )
        matchup_note = (
            f"{_env_lbl} — {p1_name} converts {_fmt(_conv, 0)}% of break points on {surface}. "
            f"{p2_name} faces {_fmt(_faced, 1)} BPs/match on their serve. "
            f"Base projection: {_fmt(_conv, 0)}% × {_fmt(_faced, 1)} = {_fmt((_conv or 0) / 100 * (_faced or 0), 1)}.{_h2h_line}{_cpr_line}"
        )

    # Data-quality confidence
    conf_result = calculate_confidence(
        player_surface_matches=p1_surf_matches,
        opponent_surface_matches=p2_surf_matches,
        prop_type=prop_type,
        has_h2h_surface=_has_h2h_surface,
        has_h2h_other=_has_h2h_other,
        court=court_for_calc,
    )
    confidence   = conf_result["confidence"]
    conf_breakdown = conf_result["breakdown"]

    proj_val = result.get("projection")
    note     = result.get("note", "")

    if proj_val is None:
        no_data("Projection unavailable", note or "Insufficient surface data for this player/prop combination.")
        return

    # Tournament history modifier
    tour_mod = 0.0
    if court_for_calc:
        tour_mod = get_tournament_record_modifier(str(p1_id), court_for_calc, tour)

    if tour_mod != 0.0:
        mod_color = "#00E676" if tour_mod > 0 else "#FF4444"
        sign = "+" if tour_mod > 0 else ""
        st.markdown(
            f'<div style="font-size:13px;padding:8px 14px;background:#111;'
            f'border:1px solid #1e1e1e;border-radius:8px;margin-bottom:10px">'
            f'<span style="color:#AAAAAA">Tournament History Modifier: </span>'
            f'<span style="color:{mod_color};font-weight:700">{sign}{tour_mod:.1f}%</span>'
            f' — {p1_name}\'s historical record at {court_for_calc}</div>',
            unsafe_allow_html=True,
        )

    effective_proj = proj_val + (proj_val * tour_mod / 100) if tour_mod != 0.0 else proj_val

    # Issue 1 — lean is always OVER or UNDER, never NEUTRAL
    lean = _resolve_lean(effective_proj, prop_line, result.get("lean", ""))
    edge = effective_proj - prop_line if prop_line > 0 else None

    # Confidence capped by edge size — small edge = moderate confidence regardless of data quality
    confidence = _edge_confidence_cap(confidence, effective_proj, prop_line)

    # ── Projection display ────────────────────────────────────────────────────
    col_proj, col_line_card, col_lean = st.columns(3)
    with col_proj:
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Model Projection</div>'
            f'<div class="proj-number">{round(effective_proj, 1)}</div>'
            f'<div class="proj-label">{p1_name}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_line_card:
        line_display = f"{prop_line:.1f}" if prop_line > 0 else "—"
        edge_html = ""
        if edge is not None:
            edge_color = "#00E676" if lean == "OVER" else "#FF4444"
            edge_sign  = "+" if edge > 0 else ""
            edge_html  = f'<div style="font-size:13px;color:{edge_color};margin-top:4px">edge {edge_sign}{edge:.1f}</div>'
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Book Line</div>'
            f'<div class="proj-number" style="color:#AAAAAA">{line_display}</div>'
            f'{edge_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_lean:
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Lean</div>'
            f'<div style="margin:12px 0">{lean_badge(lean)}</div>'
            f'{_confidence_card_html(confidence)}'
            f'</div>',
            unsafe_allow_html=True,
        )

    with st.expander("Confidence Breakdown", expanded=False):
        _render_confidence_breakdown(conf_breakdown, confidence)

    # Environment badge + serve/return context for environment-driven props
    if prop_type in ("Total Games", "Break Points Won"):
        _env_key_display = result.get("environment", "STANDARD")
        _env_desc_text   = _ENV_DESC.get(_env_key_display, "")
        _comp_html = _srv_ret_comparison(p1_name, p2_name, p1_s, p2_s)
        st.markdown(
            f'<div style="margin:12px 0;padding:14px 16px;background:#111111;'
            f'border:1px solid #1e1e1e;border-radius:10px">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
            f'<div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.06em">Match Environment</div>'
            f'{_env_badge(_env_key_display)}'
            f'</div>'
            f'{_comp_html}'
            f'<div style="font-size:12px;color:#555;margin-top:8px;line-height:1.5">{_env_desc_text}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="margin:12px 0;padding:14px 16px;background:#111111;'
        f'border:1px solid #1e1e1e;border-radius:10px;font-size:13px;color:#AAAAAA;line-height:1.65">'
        f'{matchup_note}</div>',
        unsafe_allow_html=True,
    )

    section_header(f"Recent {surface} Matches — {p1_name}")
    _render_match_schedule(p1_all_data, surface, p1_name)

    section_header(f"Recent {surface} Matches — {p2_name}")
    _render_match_schedule(p2_all_data, surface, p2_name)

    section_header("Baseline Edge AI Scouting Report")
    report = generate_scouting_report(
        player_name=p1_name,
        opponent_name=p2_name,
        player_surface_stats=p1_s,
        opponent_surface_stats=p2_s,
        player_all_stats=p1_all_stats,
        opponent_all_stats=p2_all_stats,
        surface=surface,
        court=court_for_calc,
        prop_type=prop_type,
        projection={**result, "lean": lean, "confidence": confidence},
        player_arch=p1_arch,
        opponent_arch=p2_arch,
        h2h_summary=h2h_summary if h2h_total > 0 else None,
    )

    st.markdown(
        f'<div class="ai-card">'
        f'<div class="ai-card-label">BASELINE EDGE AI</div>'
        f'<div class="ai-card-text">{report}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
