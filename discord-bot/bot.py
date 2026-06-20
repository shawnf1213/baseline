"""
Baseline Discord bot — a thin client over the existing Baseline FastAPI backend.

It does NOT contain any projection/calculation logic. Every number it shows comes
straight from the backend endpoints:
    GET  /api/search          — player search (autocomplete)
    POST /api/prop/calculate  — prop projection
    POST /api/h2h             — head-to-head
    POST /api/player/stats    — player stats

Slash commands: /prop  /h2h  /player  /help
"""

import os
import re
import asyncio
import logging

import requests
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("baseline-bot")

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")

LOGO_URL = "https://baseline-app-three.vercel.app/baseline-logo.png"

# Embed colors — match the web app theme
COLOR_OVER = 0x00E676   # green  — OVER lean / positive edge
COLOR_UNDER = 0xFF4444  # red    — UNDER lean
COLOR_NEUTRAL = 0x0A0A0A  # dark — neutral / informational
COLOR_ERROR = 0xFF4444

FOOTER_TEXT = "Baseline — Data Driven. Optimizer Backed."
FOOTER_PROJECTION = (
    "Baseline — Data Driven. Optimizer Backed. • Model projections, not betting advice."
)

# Per-request network timeouts (seconds). These are sized to the backend's
# COLD-fetch latency, not the warm-cache case. The backend caches per player per
# 2-hour bucket, so only the FIRST request for a given matchup/player pays the
# Sofascore event-pagination cost; everything after is ~0.5s. Measured cold:
# search ~6-12s, player/h2h ~15-20s (event pagination), prop ~22-40s (both
# players + Tennis Abstract + Sackmann). The slash command is deferred (15-min
# Discord window), so a longer wait just shows "thinking…" and never hangs
# Discord. Timeouts that are too short cause users to retry, which is what
# actually spams Sofascore — so we give the first call room to finish once.
SEARCH_TIMEOUT = 8     # autocomplete uses a much shorter deadline (see below)
RESOLVE_TIMEOUT = 15   # submit-time name resolution (cold search)
# Cold fetches now include inter-player spacing (1.5-3.5s) and a possible 5-10s
# 403 backoff, so give them more room. The interaction is deferred (15-min
# window) — the "thinking…" state persists, so a longer wait never hangs Discord.
PROP_TIMEOUT = 90      # cold multi-source fetch + spacing + backoff headroom
GENERIC_TIMEOUT = 60   # h2h / player-stats cold event pagination + spacing

# Cap concurrent backend calls so a traffic spike can't overwhelm Railway or
# spam the Sofascore proxy. A 6th command-initiated call waits for a slot rather
# than firing immediately — this IS the anti-spam guard (alongside Discord slow
# mode and the per-user cooldown), so longer timeouts are safe: at most 5 cold
# Sofascore fetches are ever in flight at once.
MAX_CONCURRENT_BACKEND_CALLS = 5
API_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BACKEND_CALLS)

# ── Court lists per surface (display name → backend COURT_CPR key) ──────────────
# The backend owns the CPI values; the bot only sends a recognised court name and
# reads back court_pace_index. Names map 1:1 except the three noted exceptions.
COURTS_BY_SURFACE = {
    "Clay": [
        "Roland Garros", "Monte Carlo", "Madrid", "Barcelona", "Rome",
        "Hamburg", "Geneva", "Munich", "Lyon",
    ],
    "Hard": [
        "Australian Open", "US Open", "Indian Wells", "Miami", "Cincinnati",
        "Canada Montreal", "Paris Bercy", "Vienna", "Basel", "Rotterdam",
        "Doha", "Dubai", "Shanghai", "ATP Finals",
    ],
    "Grass": [
        "Wimbledon", "Queens Club", "Halle", "Stuttgart", "s-Hertogenbosch",
        "Birmingham", "Nottingham", "Mallorca", "Eastbourne", "Berlin",
        "Bad Homburg",
    ],
}
# Display names whose backend COURT_CPR key differs from the display name.
COURT_KEY_OVERRIDES = {
    "Shanghai": "Shanghai Masters",
    "Berlin": "Berlin WTA",
    "Bad Homburg": "Bad Homburg WTA",
}


def backend_court_key(display: str) -> str:
    return COURT_KEY_OVERRIDES.get(display, display)


def surface_for_court(display: str):
    for surf, courts in COURTS_BY_SURFACE.items():
        if display in courts:
            return surf
    return None


# ── HTTP helpers (run blocking requests off the event loop) ─────────────────────
class BackendError(Exception):
    """Generic backend failure (timeout, connection, 5xx)."""


class DataUnavailable(Exception):
    """Player data source (Sofascore) temporarily unavailable."""


def _get(path: str, params: dict, timeout: int):
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict, timeout: int):
    r = requests.post(f"{API_BASE}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


async def backend_get(path: str, params: dict, timeout: int):
    """Semaphore-guarded GET — counts against the global concurrency cap."""
    async with API_SEMAPHORE:
        return await asyncio.to_thread(_get, path, params, timeout)


async def backend_post(path: str, payload: dict, timeout: int):
    """Semaphore-guarded POST — counts against the global concurrency cap."""
    async with API_SEMAPHORE:
        return await asyncio.to_thread(_post, path, payload, timeout)


async def search_players(query: str, tour: str, timeout: int = SEARCH_TIMEOUT,
                         guard: bool = False):
    """Resolve a query to a list of player dicts via the backend search endpoint.

    guard=True routes through the concurrency semaphore (used at command submit
    time). Autocomplete passes guard=False so frequent keystroke searches never
    block command traffic — they're already bounded by a short deadline.
    """
    try:
        if guard:
            data = await backend_get("/api/search", {"query": query, "tour": tour}, timeout)
        else:
            data = await asyncio.to_thread(
                _get, "/api/search", {"query": query, "tour": tour}, timeout
            )
    except Exception as exc:  # noqa: BLE001 — autocomplete must never raise
        log.warning("search failed q=%r tour=%s: %s", query, tour, exc)
        return []
    if isinstance(data, dict):  # backend returns a dict only on the block path
        return []
    return data or []


async def search_both_tours(query: str, timeout: int = SEARCH_TIMEOUT,
                            guard: bool = False):
    """Search ATP and WTA concurrently and merge (men + women)."""
    atp, wta = await asyncio.gather(
        search_players(query, "ATP", timeout, guard=guard),
        search_players(query, "WTA", timeout, guard=guard),
    )
    out = []
    for tour, players in (("ATP", atp), ("WTA", wta)):
        for p in players:
            out.append({**p, "tour": tour})
    return out


# Discord gives autocomplete callbacks a hard ~3s deadline. The backend search
# (Sofascore via proxy) is frequently slower than that, so the autocomplete must
# bound its own wait and degrade gracefully — returning [] (Discord shows "no
# options") instead of letting Discord time out with "Loading options failed".
# The user can still type a full name; resolve_player re-searches on submit with
# the full latency budget.
# A live backend search is ~2s (Sofascore via proxy); the backend now caches
# searches for 15 min so repeats are instant. Give the call ~2.7s under Discord's
# hard 3s autocomplete limit so a ~2s search reliably lands instead of timing out.
AUTOCOMPLETE_DEADLINE = 2.7


async def search_both_tours_fast(query: str):
    try:
        return await asyncio.wait_for(
            search_both_tours(query, timeout=2.6), timeout=AUTOCOMPLETE_DEADLINE
        )
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — never raise to Discord
        return []


def _is_block_response(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("data_unavailable"):
        return True
    note = (data.get("note") or "").lower()
    return "temporarily unavailable" in note or "unable to load player match data" in note


# ── Player reference encoding for autocomplete values ───────────────────────────
# Discord autocomplete Choice.value carries the resolved selection so we don't have
# to re-search on submit. Format: "id|tour|name" (≤100 chars).
def encode_player(p: dict) -> str:
    val = f"{p['id']}|{p.get('tour', 'ATP')}|{p.get('name', '')}"
    return val[:100]


def decode_player(value: str):
    """Return (id, tour, name) from an encoded value, or (None, None, raw)."""
    if value and "|" in value:
        parts = value.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            return parts[0], parts[1], parts[2]
    return None, None, value


async def resolve_player(value: str):
    """Resolve an autocomplete value (or free text) to (id, tour, name)."""
    pid, tour, name = decode_player(value)
    if pid:
        return pid, tour, name
    # Free text — search both tours and take the best match. Guarded by the
    # concurrency semaphore (command-initiated) and capped at RESOLVE_TIMEOUT.
    results = await search_both_tours(value, timeout=RESOLVE_TIMEOUT, guard=True)
    if results:
        top = results[0]
        return str(top["id"]), top.get("tour", "ATP"), top.get("name", value)
    return None, None, value


# ── Embed builders ──────────────────────────────────────────────────────────────
def error_embed(message: str) -> discord.Embed:
    e = discord.Embed(title="⚠️ Error", description=message, color=COLOR_ERROR)
    e.set_footer(text=FOOTER_TEXT)
    return e


def _form_emojis(matches, limit=5) -> str:
    out = []
    for m in (matches or [])[:limit]:
        if isinstance(m, dict):
            won = m.get("won")
        else:
            won = bool(m)
        out.append("🟢" if won else "🔴")
    return " ".join(out) if out else "—"


def _shorten(text: str, n: int = 400) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


# ── Formatting helpers ──────────────────────────────────────────────────────────
def _num(v, d=1):
    return f"{float(v):.{d}f}" if isinstance(v, (int, float)) else "—"


def _pct(v):
    return f"{float(v):.0f}%" if isinstance(v, (int, float)) else "—"


def _conf_bar(pct):
    """5-segment confidence bar with a Low/Medium/High label."""
    if not isinstance(pct, (int, float)):
        return "—"
    filled = max(0, min(5, round(pct / 20)))
    bar = "▰" * filled + "▱" * (5 - filled)
    label = "Low" if pct < 50 else "Medium" if pct < 70 else "High"
    return f"{bar}  {pct:.0f}% · {label}"


def _hand_label(h):
    if not h:
        return None
    h = str(h).upper()
    if h.startswith("L"):
        return "Left-handed"
    if h.startswith("R"):
        return "Right-handed"
    return None


def _clean_explanation(text: str) -> str:
    """Strip internal model jargon so the read rens like a human scouting note."""
    if not text:
        return ""
    t = text
    t = re.sub(r"\((?:SS|TA)[^)]*\)", "", t)        # (SS:surface_only data)
    t = re.sub(r"\bC\d+\b", "", t)                   # C8 / C1 component tokens
    t = t.replace(" -- ", " — ").replace("--", "—")
    t = re.sub(r"\s{2,}", " ", t).strip()
    return _shorten(t, 400)


def _last_name(full: str) -> str:
    return (full or "").split()[-1] if full else full


def _prop_stat_blocks(prop_type, data):
    """Return (player_block, opponent_block) — the stats most relevant to the
    selected prop, mirroring the web app's stat cards."""
    ps = data.get("player_stats") or {}
    os_ = data.get("opponent_stats") or {}

    def block(lines, hand, arch):
        rows = [f"{lbl}: **{val}**" for lbl, val in lines]
        if arch:
            rows.append(f"_{arch}_")
        if hand:
            rows.append(f"✋ {hand}")
        return "\n".join(rows) if rows else "—"

    if prop_type == "Aces":
        p_lines = [
            ("Aces/Match", _num(ps.get("aces"))),
            ("1st Serve %", _pct(ps.get("first_serve_pct"))),
            ("1st Srv Won", _pct(ps.get("first_serve_pts_won"))),
        ]
        o_lines = [
            ("Aces Conceded/Match", _num(data.get("opponent_ace_against"))),
            ("Return 1st Won", _pct(os_.get("return_first_serve_pts_won"))),
            ("Own Aces/Match", _num(os_.get("aces"))),
        ]
    elif prop_type == "Double Faults":
        p_lines = [
            ("DFs/Match", _num(ps.get("double_faults"))),
            ("2nd Srv Won", _pct(ps.get("second_serve_pts_won"))),
            ("1st Serve %", _pct(ps.get("first_serve_pct"))),
        ]
        o_lines = [
            ("Return 2nd Won", _pct(os_.get("return_second_serve_pts_won"))),
            ("DFs/Match", _num(os_.get("double_faults"))),
        ]
    elif prop_type == "Break Points Won":
        conv = data.get("bp_blended_conv_pct") or ps.get("bp_converted")
        p_lines = [
            ("BP Conversion", _pct(conv)),
            ("Return 1st Won", _pct(ps.get("return_first_serve_pts_won"))),
            ("Return 2nd Won", _pct(ps.get("return_second_serve_pts_won"))),
        ]
        o_lines = [
            ("BP Faced/Match", _num(data.get("bp_blended_opp_faced"))),
            ("Hold Rate", _pct(data.get("opp_hold_rate_pct"))),
            ("Serve Quality", data.get("opp_serve_tier") or "—"),
        ]
    else:  # Total Games
        p_lines = [
            ("1st Srv Won", _pct(ps.get("first_serve_pts_won"))),
            ("2nd Srv Won", _pct(ps.get("second_serve_pts_won"))),
            ("Win Rate", _pct(ps.get("win_rate"))),
        ]
        o_lines = [
            ("1st Srv Won", _pct(os_.get("first_serve_pts_won"))),
            ("2nd Srv Won", _pct(os_.get("second_serve_pts_won"))),
            ("Win Rate", _pct(os_.get("win_rate"))),
        ]

    p_block = block(p_lines, _hand_label(data.get("player_handedness")), data.get("player_archetype"))
    o_block = block(o_lines, _hand_label(data.get("opponent_handedness")), data.get("opponent_archetype"))
    return p_block, o_block


def prop_embed(player, opponent, prop_type, surface, court_display, line, data) -> discord.Embed:
    proj = data.get("model_projection")
    lean = (data.get("lean") or "NEUTRAL").upper()
    conf = data.get("confidence")
    cpi = data.get("court_pace_index")
    tier = data.get("court_speed_tier")
    edge = (proj - line) if (proj is not None and line is not None) else None

    color = COLOR_OVER if lean == "OVER" else COLOR_UNDER if lean == "UNDER" else COLOR_NEUTRAL
    dot = "🟢" if lean == "OVER" else "🔴" if lean == "UNDER" else "⚪"
    edge_txt = f"{'+' if edge >= 0 else ''}{edge:.1f}" if edge is not None else "—"

    # Strong-lean emphasis: confident AND a meaningful edge.
    strong = bool(conf and conf >= 70 and edge is not None and abs(edge) >= 1.0 and lean in ("OVER", "UNDER"))
    star = "  ⭐ **Strong lean**" if strong else ""

    court_line = f"**{surface}** · {court_display}"
    if cpi is not None:
        court_line += f" · ST {cpi:g}" + (f" ({tier})" if tier else "")

    # Verdict-led description — the takeaway reads instantly.
    verdict = (
        f"{dot} **{lean} {line:g}**  ·  Projection **{_num(proj)}**  ·  Edge **{edge_txt}**{star}\n"
        f"Confidence  {_conf_bar(conf)}\n"
        f"{court_line}"
    )

    e = discord.Embed(
        title=f"{prop_type} — {player} vs {opponent}",
        description=verdict[:4096],
        color=color,
    )
    e.set_thumbnail(url=LOGO_URL)

    # Prop-relevant stat cards, side by side.
    p_block, o_block = _prop_stat_blocks(prop_type, data)
    e.add_field(name=f"🎾 {player}", value=p_block[:1024], inline=True)
    e.add_field(name=f"🎾 {opponent}", value=o_block[:1024], inline=True)

    # Matchup context line.
    mp = []
    if data.get("p1_win_prob") is not None and data.get("p2_win_prob") is not None:
        mp.append(f"Win prob: {_last_name(player)} {data['p1_win_prob']:.0f}% / "
                  f"{_last_name(opponent)} {data['p2_win_prob']:.0f}%")
    if data.get("competitiveness"):
        es = data.get("expected_sets")
        mp.append(data["competitiveness"] + (f" · ~{es:.1f} sets" if isinstance(es, (int, float)) else ""))
    if data.get("handedness_edge"):
        mp.append("Handedness edge ✓")
    if mp:
        e.add_field(name="Matchup", value="\n".join(mp), inline=False)

    e.add_field(
        name=f"Last 5 ({surface}) — {_last_name(player)}",
        value=_form_emojis(data.get("player_surface_matches")),
        inline=False,
    )

    explanation = _clean_explanation(data.get("plain_english_explanation", ""))
    if explanation:
        e.add_field(name="Read", value=explanation, inline=False)

    # Limited / stale data disclosure (mirrors the web app warning).
    notes = []
    if data.get("player_limited_data"):
        notes.append(f"{_last_name(player)}: limited surface data")
    if data.get("opponent_limited_data"):
        notes.append(f"{_last_name(opponent)}: limited surface data")
    if data.get("data_stale"):
        notes.append("served from cached snapshot")
    if notes:
        e.add_field(name="⚠️ Data note", value=" · ".join(notes), inline=False)

    e.set_footer(text=FOOTER_PROJECTION)
    return e


def h2h_embed(p1, p2, surface, data) -> discord.Embed:
    total = data.get("total", 0)
    p1w = data.get("p1_wins", 0)
    p2w = data.get("p2_wins", 0)

    # Accent color by who leads the rivalry.
    color = COLOR_OVER if p1w > p2w else COLOR_UNDER if p2w > p1w else COLOR_NEUTRAL
    leader = p1 if p1w > p2w else p2 if p2w > p1w else None
    headline = f"**{p1}  {p1w} – {p2w}  {p2}**"
    if leader:
        headline += f"\n{_last_name(leader)} leads · {total} meetings"
    else:
        headline += f"\n{total} meetings"

    e = discord.Embed(
        title=f"Head-to-Head — {p1} vs {p2}",
        description=headline,
        color=color,
    )
    e.set_thumbnail(url=LOGO_URL)

    if data.get("surface_matches"):
        e.add_field(
            name=f"On {surface}",
            value=f"**{data.get('surface_p1_wins', 0)} – {data.get('surface_p2_wins', 0)}** "
                  f"({data.get('surface_matches')} meetings)",
            inline=True,
        )

    avgs = []
    if data.get("games_avg") is not None:
        avgs.append(f"Games {data['games_avg']:.1f}")
    if data.get("ace_avg") is not None:
        avgs.append(f"Aces {data['ace_avg']:.1f}")
    if data.get("bp_avg") is not None:
        avgs.append(f"BP won {data['bp_avg']:.1f}")
    if avgs:
        e.add_field(name="H2H Averages", value=" · ".join(avgs), inline=True)

    lines = []
    for m in (data.get("matches") or [])[:6]:
        if not isinstance(m, dict):
            continue
        date = m.get("date") or m.get("Date") or ""
        tourn = m.get("tournament") or m.get("Tournament") or m.get("event") or ""
        score = m.get("score") or m.get("Score") or ""
        winner = m.get("winner") or m.get("Winner") or ""
        piece = " · ".join(x for x in (date, tourn) if x)
        detail = " · ".join(x for x in (winner, score) if x)
        lines.append(f"• {piece}{(' — ' + detail) if detail else ''}".strip())
    if lines:
        e.add_field(name="Recent Meetings", value="\n".join(lines)[:1024], inline=False)
    elif total == 0:
        e.add_field(name="Recent Meetings", value="No tour-level meetings found.", inline=False)

    e.set_footer(text=FOOTER_TEXT)
    return e


def player_embed(name, surface, data) -> discord.Embed:
    arch = data.get("archetype") or "—"
    surf = data.get(surface, {}) or {}
    form = data.get("form", [])
    ta = data.get("ta_stats") or {}
    hand = _hand_label(ta.get("handedness"))

    surf_label = "All surfaces" if surface == "All" else f"{surface} court"
    desc = f"**{arch}**  ·  {surf_label}"
    if hand:
        desc += f"  ·  ✋ {hand}"

    e = discord.Embed(
        title=f"{name} — Player Profile",
        description=desc,
        color=COLOR_NEUTRAL,
    )
    e.set_thumbnail(url=LOGO_URL)

    e.add_field(name="Matches", value=str(surf.get("matches_played") or "—"), inline=True)
    e.add_field(name="Win Rate", value=_pct(surf.get("win_rate")), inline=True)
    e.add_field(name="BP Converted", value=_pct(surf.get("bp_converted")), inline=True)
    e.add_field(name="Aces/Match", value=_num(surf.get("aces")), inline=True)
    e.add_field(name="DFs/Match", value=_num(surf.get("double_faults")), inline=True)
    e.add_field(name="1st Serve %", value=_pct(surf.get("first_serve_pct")), inline=True)
    e.add_field(name="1st Srv Won", value=_pct(surf.get("first_serve_pts_won")), inline=True)
    e.add_field(name="2nd Srv Won", value=_pct(surf.get("second_serve_pts_won")), inline=True)
    e.add_field(name="Return 1st Won", value=_pct(surf.get("return_first_serve_pts_won")), inline=True)

    e.add_field(name="Last 10 Form", value=_form_emojis(form, limit=10), inline=False)
    e.set_footer(text=FOOTER_TEXT)
    return e


# ── Discord client ──────────────────────────────────────────────────────────────
class BaselineBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        log.info("Slash commands synced.")


client = BaselineBot()

PROP_CHOICES = [
    app_commands.Choice(name="Aces", value="Aces"),
    app_commands.Choice(name="Double Faults", value="Double Faults"),
    app_commands.Choice(name="Break Points Won", value="Break Points Won"),
    app_commands.Choice(name="Total Games", value="Total Games"),
]
SURFACE_CHOICES = [
    app_commands.Choice(name="Hard", value="Hard"),
    app_commands.Choice(name="Clay", value="Clay"),
    app_commands.Choice(name="Grass", value="Grass"),
]
# /player can also show overall (all-surface) stats. "All" only makes sense here,
# not for /prop or /h2h which must be tied to a specific surface.
PLAYER_SURFACE_CHOICES = [
    app_commands.Choice(name="All (overall)", value="All"),
    app_commands.Choice(name="Hard", value="Hard"),
    app_commands.Choice(name="Clay", value="Clay"),
    app_commands.Choice(name="Grass", value="Grass"),
]

# Standard user-facing messages.
MSG_UNREACHABLE = "Unable to reach Baseline servers right now — try again shortly."
MSG_GENERIC = "Something went wrong — please try again."
MSG_BLOCK = "Player data source temporarily unavailable. Please try again in a few minutes."

# Network failures that mean "backend unreachable / timed out" (vs a bug).
NETWORK_ERRORS = (requests.Timeout, requests.ConnectionError)


async def _send_error(interaction: discord.Interaction, message: str):
    """Send an error embed regardless of whether the interaction was deferred."""
    embed = error_embed(message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("failed to deliver error embed to user")


# ── Autocomplete callbacks ──────────────────────────────────────────────────────
async def player_autocomplete(interaction: discord.Interaction, current: str):
    current = (current or "").strip()
    if len(current) < 3:
        return []
    results = await search_both_tours_fast(current)
    choices = []
    seen = set()
    for p in results[:25]:
        key = (p["id"], p.get("tour"))
        if key in seen:
            continue
        seen.add(key)
        label = f"{p.get('name', '?')} ({p.get('tour')})"
        rank = p.get("currentRank")
        if rank:
            label += f" · #{rank}"
        choices.append(app_commands.Choice(name=label[:100], value=encode_player(p)))
    return choices[:25]


async def court_autocomplete(interaction: discord.Interaction, current: str):
    # Pure-local, no network — but wrap defensively so it can never raise to
    # Discord (which would surface as "Loading options failed").
    try:
        current = (current or "").lower().strip()
        surface = getattr(interaction.namespace, "surface", None)

        if surface and surface in COURTS_BY_SURFACE:
            pool = [("None", None)] + [(c, surface) for c in COURTS_BY_SURFACE[surface]]
        else:
            # Surface not chosen yet — show all, grouped/labelled by surface.
            pool = [("None", None)]
            for surf, courts in COURTS_BY_SURFACE.items():
                pool += [(c, surf) for c in courts]

        out = []
        for display, surf in pool:
            label = f"{display} ({surf})" if (surf and not surface) else display
            if current and current not in display.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=display))
        return out[:25]
    except Exception:  # noqa: BLE001
        return [app_commands.Choice(name="None", value="None")]


# ── /prop ─────────────────────────────────────────────────────────────────────
@client.tree.command(name="prop", description="Get a Baseline prop projection for a matchup")
@app_commands.describe(
    player="Player (the one the prop is for) — type to search",
    opponent="Opponent — type to search",
    prop_type="Which prop to project",
    surface="Court surface",
    court="Tournament (optional) — choose one matching the surface, or None for generic",
    line="The book line (e.g. 1.5)",
)
@app_commands.choices(prop_type=PROP_CHOICES, surface=SURFACE_CHOICES)
@app_commands.autocomplete(player=player_autocomplete, opponent=player_autocomplete, court=court_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def prop(
    interaction: discord.Interaction,
    player: str,
    opponent: str,
    prop_type: app_commands.Choice[str],
    surface: app_commands.Choice[str],
    line: float,
    court: str = "None",
):
    await interaction.response.defer(thinking=True, ephemeral=True)
    log.info("CMD /prop | user=%s | %s vs %s | %s | %s | court=%s | line=%s",
             interaction.user.id, player, opponent, prop_type.value, surface.value, court, line)
    try:
        surface_val = surface.value
        court = (court or "None").strip()

        # Validate court matches surface
        if court and court != "None":
            court_surf = surface_for_court(court)
            if court_surf is None:
                await _send_error(interaction,
                    f"`{court}` isn't a recognised tournament. Pick one from the court list or use None.")
                return
            if court_surf != surface_val:
                await _send_error(interaction,
                    f"`{court}` is a **{court_surf}** event but you selected **{surface_val}**. "
                    f"Pick a court matching the surface, or use None.")
                return

        # Resolve players (network) — distinguish unreachable from not-found.
        try:
            p_id, p_tour, p_name = await resolve_player(player)
            o_id, o_tour, o_name = await resolve_player(opponent)
        except NETWORK_ERRORS:
            log.warning("prop resolve: backend unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if not p_id or not o_id:
            missing = player if not p_id else opponent
            await _send_error(interaction,
                f"Couldn't find a player matching `{missing}`. Try the autocomplete suggestions.")
            return

        tour = p_tour or "ATP"
        court_key = "" if court == "None" else backend_court_key(court)
        court_display = "Generic surface" if court == "None" else court

        payload = {
            "player_id": p_id, "opponent_id": o_id,
            "player_name": p_name, "opponent_name": o_name,
            "tour": tour, "surface": surface_val,
            "court": court_key, "prop_type": prop_type.value,
            "prop_line": float(line),
        }

        try:
            data = await backend_post("/api/prop/calculate", payload, PROP_TIMEOUT)
        except NETWORK_ERRORS:
            log.warning("prop calc: backend timeout/unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if _is_block_response(data):
            await _send_error(interaction, MSG_BLOCK)
            return

        if data.get("model_projection") is None:
            await _send_error(interaction,
                data.get("note") or "No projection available for this matchup/prop.")
            return

        await interaction.followup.send(
            embed=prop_embed(p_name, o_name, prop_type.value, surface_val, court_display, float(line), data),
            ephemeral=True,
        )
    except Exception:  # noqa: BLE001 — never let a command crash the process
        log.exception("UNHANDLED /prop error")
        await _send_error(interaction, MSG_GENERIC)


# ── /h2h ────────────────────────────────────────────────────────────────────────
@client.tree.command(name="h2h", description="Head-to-head record between two players")
@app_commands.describe(
    player1="First player — type to search",
    player2="Second player — type to search",
    surface="Optional surface filter",
)
@app_commands.choices(surface=SURFACE_CHOICES)
@app_commands.autocomplete(player1=player_autocomplete, player2=player_autocomplete)
async def h2h(
    interaction: discord.Interaction,
    player1: str,
    player2: str,
    surface: app_commands.Choice[str] = None,
):
    await interaction.response.defer(thinking=True, ephemeral=True)
    log.info("CMD /h2h | user=%s | %s vs %s | surface=%s",
             interaction.user.id, player1, player2, surface.value if surface else "All")
    try:
        surface_val = surface.value if surface else None

        try:
            p1_id, p1_tour, p1_name = await resolve_player(player1)
            p2_id, p2_tour, p2_name = await resolve_player(player2)
        except NETWORK_ERRORS:
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if not p1_id or not p2_id:
            await _send_error(interaction,
                "Couldn't resolve both players. Use the autocomplete suggestions.")
            return

        payload = {
            "player1_id": p1_id, "player2_id": p2_id,
            "tour": p1_tour or "ATP", "surface": surface_val,
        }
        try:
            data = await backend_post("/api/h2h", payload, GENERIC_TIMEOUT)
        except NETWORK_ERRORS:
            log.warning("h2h: backend timeout/unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        await interaction.followup.send(embed=h2h_embed(p1_name, p2_name, surface_val, data), ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /h2h error")
        await _send_error(interaction, MSG_GENERIC)


# ── /player ───────────────────────────────────────────────────────────────────
@client.tree.command(name="player", description="Player profile, surface stats and recent form")
@app_commands.describe(name="Player — type to search", surface="Surface (or All for overall)")
@app_commands.choices(surface=PLAYER_SURFACE_CHOICES)
@app_commands.autocomplete(name=player_autocomplete)
async def player_cmd(
    interaction: discord.Interaction,
    name: str,
    surface: app_commands.Choice[str],
):
    await interaction.response.defer(thinking=True, ephemeral=True)
    log.info("CMD /player | user=%s | name=%s | surface=%s",
             interaction.user.id, name, surface.value)
    try:
        try:
            p_id, p_tour, p_name = await resolve_player(name)
        except NETWORK_ERRORS:
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if not p_id:
            await _send_error(interaction, f"Couldn't find a player matching `{name}`.")
            return

        payload = {"player_id": p_id, "player_name": p_name, "tour": p_tour or "ATP"}
        try:
            data = await backend_post("/api/player/stats", payload, GENERIC_TIMEOUT)
        except NETWORK_ERRORS:
            log.warning("player stats: backend timeout/unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if not (data.get(surface.value) or {}).get("matches_played") and not data.get("form"):
            await _send_error(interaction, MSG_BLOCK)
            return

        await interaction.followup.send(embed=player_embed(p_name, surface.value, data), ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /player error")
        await _send_error(interaction, MSG_GENERIC)


# ── /help ───────────────────────────────────────────────────────────────────────
@client.tree.command(name="help", description="How to use the Baseline bot")
async def help_cmd(interaction: discord.Interaction):
    e = discord.Embed(
        title="Baseline Bot — Commands",
        description="Tennis prop projections straight from the Baseline model.",
        color=COLOR_OVER,
    )
    e.set_thumbnail(url=LOGO_URL)
    e.add_field(
        name="/prop",
        value=(
            "Project a prop for a matchup.\n"
            "`/prop player:Sinner opponent:Alcaraz prop_type:Aces surface:Hard line:11.5`\n"
            "• **player / opponent** — start typing, pick from autocomplete\n"
            "• **prop_type** — Aces · Double Faults · Break Points Won · Total Games\n"
            "• **surface** — Hard · Clay · Grass\n"
            "• **court** *(optional)* — pick the tournament; the autocomplete only "
            "shows events matching your chosen surface (e.g. Grass → Wimbledon, Halle). "
            "Leave as None for the generic surface speed.\n"
            "• **line** — the book line, e.g. 11.5"
        ),
        inline=False,
    )
    e.add_field(
        name="/h2h",
        value="Head-to-head record + recent meetings.\n`/h2h player1:Djokovic player2:Nadal surface:Clay`",
        inline=False,
    )
    e.add_field(
        name="/player",
        value="Profile, surface stats and recent form.\n`/player name:Gauff surface:Grass`",
        inline=False,
    )
    e.add_field(name="/help", value="This message.", inline=False)
    e.set_footer(text=FOOTER_PROJECTION)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ── Global error handling — nothing should ever crash the process ───────────────
@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Catch-all for slash-command errors not handled inside a command body
    (notably the per-user cooldown, which fires before the handler runs)."""
    if isinstance(error, app_commands.CommandOnCooldown):
        await _send_error(interaction, f"⏳ Slow down — try again in {error.retry_after:.1f}s.")
        return
    # Unwrap the original exception for clearer logs.
    orig = getattr(error, "original", error)
    log.exception("APP_COMMAND_ERROR: %r", orig)
    if isinstance(orig, NETWORK_ERRORS):
        await _send_error(interaction, MSG_UNREACHABLE)
    else:
        await _send_error(interaction, MSG_GENERIC)


@client.event
async def on_error(event_method: str, *args, **kwargs):
    """Global event-loop error handler. Any uncaught exception in any event is
    logged with a full traceback and swallowed so the bot keeps running."""
    log.exception("UNCAUGHT ERROR in event %s — bot continues running", event_method)


@client.event
async def on_ready():
    log.info("Logged in as %s (id=%s) — API=%s | max_concurrent=%d",
             client.user, client.user.id, API_BASE, MAX_CONCURRENT_BACKEND_CALLS)


# ── Connection lifecycle logging (reconnect handled by discord.py itself) ───────
@client.event
async def on_connect():
    log.info("Gateway connected.")


@client.event
async def on_disconnect():
    # discord.py auto-reconnects (client.run(reconnect=True), the default). We
    # only log here — we never call close() or override the reconnect loop.
    log.warning("Gateway disconnected — discord.py will auto-reconnect.")


@client.event
async def on_resumed():
    log.info("Gateway session resumed after reconnect.")


def main():
    if not DISCORD_BOT_TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Add it to discord-bot/.env "
            "(or the Railway service variables) before starting the bot."
        )
    # reconnect=True is the default; stated explicitly so it is never removed.
    client.run(DISCORD_BOT_TOKEN, reconnect=True)


if __name__ == "__main__":
    main()
