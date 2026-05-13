import streamlit as st
from src.api.sofascore_client import search_players


def render_player_search(
    prefix: str,
    label: str,
    tour: str,
    compact: bool = False,
    scope: str = "",
) -> None:
    """
    Renders a player search input + result buttons.

    State keys (stable, used by consumers): {prefix}_id, {prefix}_name, {prefix}_results
    Widget keys (unique per call site):      built from tour + scope + prefix so Streamlit
                                             never sees duplicate element keys across tabs.
    """
    tour_slug = tour.lower()
    wk = f"{tour_slug}_{scope}_{prefix}" if scope else f"{tour_slug}_{prefix}"

    def _on_change():
        q = st.session_state.get(f"{wk}_query", "")
        if len(q) >= 3:
            results = search_players(q, tour)
            st.session_state[f"{prefix}_results"] = results
        else:
            st.session_state[f"{prefix}_results"] = []

    selected_name = st.session_state.get(f"{prefix}_name")

    if selected_name:
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(
                f'<div class="player-chip">'
                f'<div class="player-chip-dot"></div>'
                f'<div class="player-chip-name">{selected_name}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col2:
            if st.button("✕", key=f"{wk}_clear", help="Clear selection"):
                st.session_state[f"{prefix}_id"] = None
                st.session_state[f"{prefix}_name"] = None
                st.session_state[f"{prefix}_results"] = []
                st.rerun()
        return

    st.text_input(
        label,
        key=f"{wk}_query",
        placeholder="Type player name (3+ chars)…",
        on_change=_on_change,
        label_visibility="collapsed" if compact else "visible",
    )

    results = st.session_state.get(f"{prefix}_results", [])
    for player in results:
        pid = player.get("id")
        pname = player.get("name", "Unknown")
        gender = player.get("gender", "")
        rank = player.get("currentRank")
        rank_tag = f" · #{rank}" if rank else ""
        gender_tag = " · ♀" if gender == "F" else " · ♂" if gender == "M" else ""
        btn_label = f"{pname}{rank_tag}{gender_tag}"

        if st.button(btn_label, key=f"{wk}_pick_{pid}", use_container_width=True):
            st.session_state[f"{prefix}_id"] = pid
            st.session_state[f"{prefix}_name"] = pname
            st.session_state[f"{prefix}_results"] = []
            st.rerun()


def stat_card(label: str, value, unit: str = "", accent: bool = False, warn: bool = False, sub: str = "") -> str:
    value_class = "stat-accent" if accent else "stat-warn" if warn else ""
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    sub_html = f'<div class="stat-sub">{sub}</div>' if sub else ""
    val_str = f"{value:.1f}" if isinstance(value, float) else str(value) if value is not None else "—"
    return (
        f'<div class="stat-card">'
        f'<div class="stat-label">{label}</div>'
        f'<div class="stat-value {value_class}">{val_str}{unit_html}</div>'
        f'{sub_html}'
        f'</div>'
    )


def surface_badge(surface: str) -> str:
    cls = {"Hard": "sb-hard", "Clay": "sb-clay", "Grass": "sb-grass"}.get(surface, "sb-unknown")
    return f'<span class="surface-badge {cls}">{surface}</span>'


def form_dots(form_list: list) -> str:
    dots = ""
    for m in form_list[:10]:
        cls = "form-w" if m.get("won") else "form-l"
        dots += f'<span class="form-dot {cls}" title="{m.get("tournament","")}" ></span>'
    return f'<div class="form-row">{dots}</div>'


def lean_badge(lean: str) -> str:
    cls = {"OVER": "lean-over", "UNDER": "lean-under"}.get(lean, "lean-neutral")
    return f'<span class="lean-badge {cls}">{lean}</span>'


def confidence_bar(pct: int, label: str = "") -> str:
    label_html = f'<div class="conf-label">{label or f"{pct}% confidence"}</div>'
    return (
        f'<div class="conf-bar-wrap"><div class="conf-bar" style="width:{pct}%"></div></div>'
        f'{label_html}'
    )


def section_header(title: str) -> None:
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def no_data(msg: str = "No data available", sub: str = "") -> None:
    sub_html = f'<div class="msg">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="no-data"><div class="icon">📭</div>'
        f'<div>{msg}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def empty_prompt(title: str, sub: str = "") -> None:
    sub_html = f'<div class="ep-sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="empty-prompt">'
        f'<div class="ep-icon">🎾</div>'
        f'<div class="ep-title">{title}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def prob_row(label: str, pct: float, color: str = "#00E676") -> str:
    w = min(100, max(0, pct))
    return (
        f'<div class="prob-row">'
        f'<div class="prob-label">{label}</div>'
        f'<div class="prob-track"><div class="prob-fill" style="width:{w}%;background:{color}"></div></div>'
        f'<div class="prob-val">{pct:.1f}%</div>'
        f'</div>'
    )
