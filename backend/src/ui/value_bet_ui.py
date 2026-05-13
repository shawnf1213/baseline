import streamlit as st
from src.api.sofascore_client import get_player_stats_by_surface, get_h2h_summary
from src.calculations.archetypes import classify_archetype
from src.calculations.value_bet import (
    calculate_win_probability,
    implied_from_american,
    calculate_value_bet,
)
from src.ui.components import (
    render_player_search, section_header, no_data, empty_prompt, prob_row,
    lean_badge, confidence_bar,
)


def _fmt(val, decimals=1, suffix=""):
    if val is None:
        return "—"
    return f"{val:.{decimals}f}{suffix}"


def render(tour: str) -> None:
    p1_id = st.session_state.get("main_id")
    p1_name = st.session_state.get("main_name")

    st.markdown('<div class="section-header" style="margin-top:0">Player (Your Pick)</div>', unsafe_allow_html=True)
    if p1_id:
        st.markdown(
            f'<div class="player-chip"><div class="player-chip-dot"></div>'
            f'<div class="player-chip-name">{p1_name}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        render_player_search("main", "Player — search above", tour, scope="value_bet")

    section_header("Opponent")
    render_player_search("vb_p2", "Search opponent…", tour, scope="value_bet")

    p2_id = st.session_state.get("vb_p2_id")
    p2_name = st.session_state.get("vb_p2_name")

    if not p1_id or not p2_id:
        if not p1_id:
            empty_prompt("Select a player using the search above")
        elif not p2_id:
            empty_prompt("Select an opponent to run value bet analysis")
        return

    # Surface and odds inputs
    section_header("Match Settings")
    col_surf, col_odds = st.columns(2)
    with col_surf:
        surface = st.selectbox(
            "Surface",
            ["Hard", "Clay", "Grass"],
            key="vb_surface",
        )
    with col_odds:
        odds_input = st.text_input(
            "Sportsbook Odds (American, e.g. -130 or +115)",
            key="vb_odds",
            placeholder="-130",
        )

    if not odds_input:
        st.info("Enter sportsbook odds for your player to calculate edge.")
        return

    # Load data
    with st.spinner("Loading player data…"):
        p1_data = get_player_stats_by_surface(p1_id, tour)
        p2_data = get_player_stats_by_surface(p2_id, tour)
        # H2H via matchstat using player IDs
        h2h = get_h2h_summary(tour, str(p1_id), str(p2_id), surface=surface)

    p1_arch = classify_archetype(p1_data.get("All", {}), tour)
    p2_arch = classify_archetype(p2_data.get("All", {}), tour)

    win_prob_result = calculate_win_probability(p1_data, p2_data, h2h, surface)
    model_prob = win_prob_result["model_probability"]
    implied_prob = implied_from_american(odds_input)

    recent_edge_strong = abs(model_prob - 0.5) > 0.18
    vb = calculate_value_bet(model_prob, implied_prob, p1_arch, p2_arch, surface, recent_edge_strong)

    # ── Matchup header ────────────────────────────────────────────────────────
    section_header(f"Value Analysis — {p1_name} vs {p2_name} on {surface}")

    c1, c2, c3 = st.columns([2, 1, 2])
    with c1:
        p1_wr = p1_data.get(surface, {}).get("win_rate") or p1_data.get("All", {}).get("win_rate") or 50
        p1_mp = p1_data.get(surface, {}).get("matches_played") or 0
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">{p1_name}</div>'
            f'<div class="stat-value" style="color:#00E676">{p1_wr:.0f}%</div>'
            f'<div class="stat-sub">{surface} win rate · {p1_mp} matches</div>'
            f'<div style="margin-top:8px;font-size:12px;color:#AAAAAA">{p1_arch}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            '<div style="text-align:center;padding:20px 0;font-size:26px;font-weight:900;color:#333">vs</div>',
            unsafe_allow_html=True,
        )
    with c3:
        p2_wr = p2_data.get(surface, {}).get("win_rate") or p2_data.get("All", {}).get("win_rate") or 50
        p2_mp = p2_data.get(surface, {}).get("matches_played") or 0
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">{p2_name}</div>'
            f'<div class="stat-value" style="color:#AAAAAA">{p2_wr:.0f}%</div>'
            f'<div class="stat-sub">{surface} win rate · {p2_mp} matches</div>'
            f'<div style="margin-top:8px;font-size:12px;color:#AAAAAA">{p2_arch}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Model breakdown ───────────────────────────────────────────────────────
    section_header("Model Probability Breakdown")
    components = win_prob_result.get("components", {})
    bars = ""
    for label, val in components.items():
        bars += prob_row(label, val)
    st.markdown(bars, unsafe_allow_html=True)

    # ── Value Bet output ──────────────────────────────────────────────────────
    section_header("Value Bet Signal")

    lean = vb["lean"]
    model_pct = vb["model_probability"]
    implied_pct = vb["implied_probability"]
    edge = vb["edge"]
    confidence = vb["confidence"]
    fair_odds = vb["fair_odds"]
    modifier = vb["modifier_pct"]

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Model Win Prob</div>'
            f'<div class="stat-value" style="color:#FFFFFF">{model_pct}%</div>'
            f'<div class="stat-sub">Fair odds: {fair_odds}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Implied (Book)</div>'
            f'<div class="stat-value" style="color:#AAAAAA">{implied_pct}%</div>'
            f'<div class="stat-sub">Input: {odds_input}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c3:
        edge_color = "#00E676" if edge > 0 else "#FF4444" if edge < 0 else "#AAAAAA"
        st.markdown(
            f'<div class="stat-card" style="text-align:center">'
            f'<div class="stat-label">Edge</div>'
            f'<div class="stat-value" style="color:{edge_color}">{edge:+.1f}%</div>'
            f'<div class="stat-sub">Archetype modifier: {modifier:+.1f}%</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Lean badge + confidence
    st.markdown('<div style="text-align:center;margin:24px 0 8px">', unsafe_allow_html=True)
    st.markdown(lean_badge(lean), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown(confidence_bar(confidence, f"{confidence}% model confidence"), unsafe_allow_html=True)

    lean_explain = {
        "OVER": f"Model gives {p1_name} a {model_pct}% win probability vs {implied_pct}% implied — positive edge of {edge:+.1f}%.",
        "UNDER": f"Model gives {p1_name} only {model_pct}% win probability vs {implied_pct}% implied — negative edge of {edge:+.1f}%. Consider fading.",
        "NEUTRAL": f"Model probability ({model_pct}%) is close to implied ({implied_pct}%). No clear edge detected.",
    }.get(lean, "")
    if lean_explain:
        st.markdown(
            f'<div style="font-size:13px;color:#AAAAAA;text-align:center;margin-top:8px">{lean_explain}</div>',
            unsafe_allow_html=True,
        )

    # ── H2H context ───────────────────────────────────────────────────────────
    h2h_total = h2h.get("total", 0)
    if h2h_total > 0:
        section_header("H2H Context")
        h2h_p1w = h2h.get("p1_wins", 0)
        h2h_p2w = h2h.get("p2_wins", 0)
        surf_total = h2h.get("surface_matches", 0)
        surf_p1w = h2h.get("surface_p1_wins", 0)
        st.markdown(
            f'<div class="stat-card">'
            f'<div style="font-size:13px;color:#AAAAAA">Overall: <span style="color:#FFFFFF">{h2h_p1w}-{h2h_p2w}</span> in favor of {p1_name if h2h_p1w >= h2h_p2w else p2_name}</div>'
            + (f'<div style="font-size:13px;color:#AAAAAA;margin-top:6px">On {surface}: <span style="color:#FFFFFF">{surf_p1w}-{surf_total - surf_p1w}</span></div>' if surf_total > 0 else "")
            + f'<div style="font-size:11px;color:#555;margin-top:8px">H2H win rate ({surface}): {win_prob_result["h2h_rate"]*100:.0f}%</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Archetype vs Surface note ──────────────────────────────────────────────
    section_header("Archetype × Surface Analysis")
    arch_notes = {
        ("Big Server", "Grass"): f"{p1_name} is a Big Server — Grass is their best surface. Strong advantage.",
        ("Big Server", "Clay"): f"Big servers typically struggle on Clay. {p1_name}'s serve advantage is reduced.",
        ("Counterpuncher", "Clay"): f"{p1_name} is a Counterpuncher — Clay extends rallies and maximizes their strength.",
        ("Serve and Volleyer", "Grass"): f"Serve-and-volley on Grass is lethal. {p1_name} gets a surface bonus.",
        ("Solid Baseliner", "Hard"): f"{p1_name}'s Solid Baseliner profile is neutral on Hard — relies on consistency.",
    }
    note = arch_notes.get((p1_arch, surface), f"{p1_name} ({p1_arch}) vs {p2_name} ({p2_arch}) on {surface}. Archetype modifier applied: {modifier:+.1f}%.")
    st.markdown(
        f'<div style="font-size:13px;color:#AAAAAA;padding:12px 16px;background:#111111;'
        f'border:1px solid #1e1e1e;border-radius:8px">{note}</div>',
        unsafe_allow_html=True,
    )
