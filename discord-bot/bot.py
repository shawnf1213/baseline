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
import json
import re
import time
import asyncio
import datetime
import logging

import requests
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import pick_of_day      # isolated Pick of the Day feature (own failure handling)
import results_tracker   # Feature 1 — durable results log (own failure handling)
import line_monitor      # Feature 2 — automated line-movement monitor (bot-only)

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

# Allow @everyone pings on the automatic broadcasts (POD, line alerts, slate).
# discord.py suppresses @everyone by default unless explicitly permitted.
EVERYONE_MENTION = discord.AllowedMentions(everyone=True)

# Embed colors — match the web app theme
COLOR_OVER = 0x00E676   # green  — OVER lean / positive edge
COLOR_UNDER = 0xFF4444  # red    — UNDER lean
COLOR_NEUTRAL = 0x0A0A0A  # dark — neutral / informational
COLOR_ERROR = 0xFF4444

FOOTER_TEXT = "Baseline — Data Driven. Optimizer Backed."
# Player stats and projections are built from a recency-focused window — signal
# that clearly so users know the data reflects current form, not career history.
FOOTER_52W = "Baseline — Data Driven. Optimizer Backed. • Last 52 weeks"
# Short by design. The old footer ("Baseline — Data Driven. Optimizer Backed. •
# Last 52 weeks • Model projections, not betting advice.") wrapped to TWO lines on
# a phone under every single embed — more vertical space than some of the plays it
# sat beneath. The disclaimer is the only part that has to be there; the slogan
# and the data-window note were noise repeated on every post.
FOOTER_PROJECTION = "Baseline · Model projections, not betting advice"

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
RESOLVE_TIMEOUT = 10   # submit-time name resolution
PROP_TIMEOUT = 45      # multi-source fetch
GENERIC_TIMEOUT = 30   # h2h / player-stats

# Cap concurrent backend calls so a traffic spike can't overwhelm Railway or
# spam the Sofascore proxy. A 6th command-initiated call waits for a slot rather
# than firing immediately — this IS the anti-spam guard (alongside Discord slow
# mode and the per-user cooldown), so longer timeouts are safe: at most 5 cold
# Sofascore fetches are ever in flight at once.
MAX_CONCURRENT_BACKEND_CALLS = 5

# ── Command request queue ───────────────────────────────────────────────────────
# Process at most REQUEST_LIMIT data commands (/prop /h2h /history /form) at once;
# extra requests queue (told "results coming shortly") and wait up to
# REQUEST_MAX_WAIT for a slot before being asked to retry. Keeps a burst of
# concurrent commands from all hitting the backend at the same instant.
REQUEST_LIMIT = 10
REQUEST_MAX_WAIT = 30          # seconds a queued request will wait for a slot
QUEUE_LOG_AT = 3               # log queue depth once it reaches this
_REQUEST_SEM = asyncio.Semaphore(REQUEST_LIMIT)
_in_flight = 0                 # requests currently holding or waiting for a slot


class _QueueBusy(Exception):
    """Raised when a queued request waited past REQUEST_MAX_WAIT."""


async def _enter_queue(interaction: "discord.Interaction"):
    """Defer the interaction and acquire a queue slot. If all REQUEST_LIMIT slots
    are busy, tell the user it's queued, then wait up to REQUEST_MAX_WAIT. Raises
    _QueueBusy on timeout (the caller should just return). On success the caller
    MUST call _leave_queue() in a finally."""
    global _in_flight
    _in_flight += 1
    if _in_flight >= QUEUE_LOG_AT:
        log.info("Request queue depth: %d (max concurrent %d)", _in_flight, REQUEST_LIMIT)
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True, ephemeral=True)
    if _REQUEST_SEM.locked():            # all slots busy → queued
        try:
            await interaction.followup.send(
                "⏳ Your request is queued — results coming shortly", ephemeral=True)
        except Exception:  # noqa: BLE001
            pass
    try:
        await asyncio.wait_for(_REQUEST_SEM.acquire(), timeout=REQUEST_MAX_WAIT)
    except asyncio.TimeoutError:
        _in_flight -= 1                  # never acquired a slot
        try:
            await interaction.followup.send(
                "⚠️ The server is busy right now — please try again in a moment",
                ephemeral=True)
        except Exception:  # noqa: BLE001
            pass
        raise _QueueBusy()


def _leave_queue():
    global _in_flight
    _REQUEST_SEM.release()
    _in_flight -= 1
API_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BACKEND_CALLS)

# ── Court lists per surface (display name → backend COURT_CPR key) ──────────────
# The backend owns the CPI values; the bot only sends a recognised court name and
# reads back court_pace_index. Names map 1:1 except the three noted exceptions.
COURTS_BY_SURFACE = {
    "Clay": [
        "Roland Garros", "Monte Carlo", "Madrid", "Barcelona", "Rome",
        "Hamburg", "Geneva", "Munich", "Lyon", "Gstaad", "Bastad", "Umag",
        "Kitzbuhel", "Estoril",
    ],
    "Hard": [
        "Australian Open", "US Open", "Indian Wells", "Miami", "Cincinnati",
        "Canadian Open", "Washington DC Open", "Los Cabos", "Winston-Salem",
        "Athens Open", "Paris Bercy", "Vienna", "Basel", "Rotterdam",
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


# Prop → the per-match stat field used for the last-5 OVER/UNDER-the-line signal.
_LAST5_STAT_KEY = {
    "Aces":                   "aces",
    "Double Faults":          "double_faults",
    "Total Games":            "total_match_games",
    "Break Points Won":       "bp_converted_count",   # breaks the player won
    "Player Total Games Won": "total_games_won",
}


def _last5_signal(matches, prop_type, line, limit=5) -> str:
    """Last-N dots showing whether the PROP'S STAT cleared the LINE in each recent
    match — 🟢 over · 🔴 under · ⚪ push/no-data — NOT win/loss. A player can clear
    an ace line in a loss (or miss it in a win), so win/loss was the wrong signal.
    Falls back to win/loss form dots only for an unsupported prop or missing line."""
    key = _LAST5_STAT_KEY.get(prop_type)
    if not key or not isinstance(line, (int, float)) or line <= 0:
        return _form_emojis(matches, limit)
    out = []
    for m in (matches or [])[:limit]:
        v = m.get(key) if isinstance(m, dict) else None
        if not isinstance(v, (int, float)):
            out.append("⚪")
        elif v > line:
            out.append("🟢")
        elif v < line:
            out.append("🔴")
        else:
            out.append("⚪")   # landed exactly on the line → push
    return " ".join(out) if out else "—"


def _form_divergence(matches, prop_type, line, lean) -> str:
    """Warn when the projection's lean contradicts recent same-surface form —
    e.g. it leans OVER but the player cleared the line in only a minority of
    recent matches (a bust risk the stats alone missed). Returns a note string,
    or '' when recent form aligns / there's too little data to judge."""
    key = _LAST5_STAT_KEY.get(prop_type)
    lean = (lean or "").upper()
    if not key or not isinstance(line, (int, float)) or lean not in ("OVER", "UNDER"):
        return ""
    over = under = 0
    for m in (matches or [])[:5]:
        v = m.get(key) if isinstance(m, dict) else None
        if not isinstance(v, (int, float)):
            continue
        if v > line:
            over += 1
        elif v < line:
            under += 1
    n = over + under
    if n < 3:
        return ""
    if lean == "OVER" and over < under:
        return f"Projection leans **OVER {line:g}** but only **{over} of last {n}** cleared it — recent form diverges from the stats."
    if lean == "UNDER" and under < over:
        return f"Projection leans **UNDER {line:g}** but **{over} of last {n}** cleared it — recent form diverges from the stats."
    return ""


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


def _prop_stat_blocks(prop_type, data, surface=None):
    """Return (player_block, opponent_block) — the stats most relevant to the
    selected prop, mirroring the web app's stat cards. ``surface`` (when given)
    is appended to the ace labels so it's explicit these are surface-filtered."""
    ps = data.get("player_stats") or {}
    os_ = data.get("opponent_stats") or {}
    # Explicit surface tag for ace stats (STEP 3) — these are the matchup-surface
    # figures the projection actually used, not an all-surface average.
    _sfx = f" ({surface})" if surface and surface != "All" else ""

    def block(lines, hand, arch):
        rows = [f"{lbl}: **{val}**" for lbl, val in lines]
        if arch:
            rows.append(f"_{arch}_")
        if hand:
            rows.append(f"✋ {hand}")
        return "\n".join(rows) if rows else "—"

    if prop_type == "Aces":
        p_lines = [
            (f"Aces/Match{_sfx}", _num(ps.get("aces"))),
            ("1st Serve %", _pct(ps.get("first_serve_pct"))),
            ("1st Srv Won", _pct(ps.get("first_serve_pts_won"))),
        ]
        o_lines = [
            (f"Aces Conceded/Match{_sfx}", _num(data.get("opponent_ace_against"))),
            ("Return 1st Won", _pct(os_.get("return_first_serve_pts_won"))),
            (f"Own Aces/Match{_sfx}", _num(os_.get("aces"))),
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
            ("BP Generated/Match", _num(data.get("bp_generated_per_match"))),
            ("BP Gen (Quality-Adj)", _num(data.get("bp_generated_quality_adj"))),
            ("BP Conversion", _pct(conv)),
            ("Service Games Won", _pct(ps.get("service_games_won_pct"))),
            ("Return Games Won", _pct(ps.get("return_games_won_pct"))),
        ]
        o_lines = [
            ("BP Faced/Match", _num(data.get("bp_blended_opp_faced"))),
            ("Service Games Won", _pct(os_.get("service_games_won_pct"))),
            ("Hold Rate", _pct(data.get("opp_hold_rate_pct"))),
            ("Server Quality",
             data.get("opp_server_quality_tier") or data.get("opp_serve_tier") or "—"),
            ("1st Srv Won", _pct(os_.get("first_serve_pts_won"))),
            ("2nd Srv Won", _pct(os_.get("second_serve_pts_won"))),
        ]
    elif prop_type == "Player Total Games Won":
        # Core drivers: player hold rate, opponent hold rate, player break rate,
        # and the held-vs-broken composition of the projection.
        p_lines = [
            ("Hold Rate", _pct(data.get("player_hold_rate"))),
            ("Break Rate vs Opp", _pct(data.get("player_break_rate"))),
            ("Games Held", _num(data.get("games_held"))),
            ("Games by Break", _num(data.get("games_broken"))),
        ]
        o_lines = [
            ("Hold Rate", _pct(data.get("opp_hold_rate_g"))),
            ("Win Rate", _pct(os_.get("win_rate"))),
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

    # NEW SIGNAL 3 — surface tiebreak rate in the comparison columns, with a
    # TIEBREAK SPECIALIST marker when the rate exceeds 35%.
    def _tb_cell(rate):
        if rate is None:
            return "—"
        return f"{rate:.0f}%" + ("  🎯 SPECIALIST" if rate > 35 else "")
    if data.get("player_tiebreak_rate") is not None:
        p_lines.append(("Tiebreak Rate", _tb_cell(data.get("player_tiebreak_rate"))))
    if data.get("opponent_tiebreak_rate") is not None:
        o_lines.append(("Tiebreak Rate", _tb_cell(data.get("opponent_tiebreak_rate"))))

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
    if data.get("indoor_court"):
        court_line += "  ·  🏟️ **INDOOR**"
    if data.get("altitude_court"):
        court_line += f"  ·  ⛰️ **ALTITUDE +{data.get('altitude_pct', 0):.0f}% aces**"
    if cpi is not None:
        court_line += f" · ST {cpi:g}" + (f" ({tier})" if tier else "")
    fmt_label = data.get("match_format_label") or "Best of 3"

    # ── Win probability + expected sets (context first — who's favored and how
    # long the match runs — before the projection itself). Star the favorite. ──
    p1wp, p2wp = data.get("p1_win_prob"), data.get("p2_win_prob")
    win_line = ""
    if p1wp is not None and p2wp is not None:
        pn, on = _last_name(player), _last_name(opponent)
        if p1wp >= p2wp:
            win_line = f"⭐ **{pn} {p1wp:.0f}%**  —  {on} {p2wp:.0f}%"
        else:
            win_line = f"{pn} {p1wp:.0f}%  —  ⭐ **{on} {p2wp:.0f}%**"
    exp_sets = data.get("expected_sets")
    sets_line = ""
    if isinstance(exp_sets, (int, float)):
        comp = data.get("competitiveness")
        sets_line = (f"Expected Sets **{exp_sets:.1f}** · {fmt_label}"
                     + (f" · {comp}" if comp else ""))

    # Verdict — grouped with blank lines for readability: context (win prob /
    # sets) first, then court, then the projection takeaway.
    g_context = "\n".join(x for x in (win_line, sets_line) if x)
    _cap = data.get("confidence_cap_reason")
    _cap_txt = f"  ·  _{_cap}_" if _cap else ""
    g_proj = (
        f"{dot} **{lean} {line:g}**  ·  Projection **{_num(proj)}**  ·  Edge **{edge_txt}**{star}\n"
        f"Confidence  {_conf_bar(conf)}{_cap_txt}"
    )
    # Player Total Games Won is player-specific — say whose games, and how the
    # projection breaks down into holds vs breaks.
    if prop_type == "Player Total Games Won":
        gh, gb = data.get("games_held"), data.get("games_broken")
        comp = (f"\n🎾 **{player}'s** games won  ·  {_num(gh)} held on serve + "
                f"{_num(gb)} by breaking") if gh is not None else f"\n🎾 **{player}'s** games won"
        g_proj += comp
    verdict = "\n\n".join(x for x in (g_context, court_line, g_proj) if x)

    e = discord.Embed(
        title=f"{prop_type} — {player} vs {opponent}",
        description=verdict[:4096],
        color=color,
    )
    e.set_thumbnail(url=LOGO_URL)

    # ── Feature 3 — data freshness / injury-withdrawal flag (amber/red) ──────
    _fresh_level = data.get("freshness_level")
    _fresh_msg = data.get("freshness_message")
    if _fresh_level and _fresh_msg:
        _icon = "🔴" if _fresh_level == "red" else "🟡"
        _suffix = " Confidence reduced 15 points." if _fresh_level == "red" else ""
        e.add_field(name=f"{_icon} Data Freshness",
                    value=f"{_fresh_msg}{_suffix}", inline=False)

    # Prop-relevant stat cards, side by side.
    p_block, o_block = _prop_stat_blocks(prop_type, data, surface)
    e.add_field(name=f"🎾 {player}", value=p_block[:1024], inline=True)
    e.add_field(name=f"🎾 {opponent}", value=o_block[:1024], inline=True)

    # Tour-average-estimate note when limited data forced a fallback on a
    # fundamental stat (so the numbers above aren't mistaken for measured data).
    if data.get("player_tour_avg_stats") or data.get("opponent_tour_avg_stats"):
        e.add_field(
            name="≈ Note",
            value="Some fundamental stats are tour-average estimates (limited match data).",
            inline=False,
        )

    # Handedness edge note (win prob + expected sets now live at the top).
    if data.get("handedness_edge"):
        e.add_field(name="Matchup", value="Handedness edge ✓", inline=False)

    e.add_field(
        name=f"Last 5 ({surface}) vs {line:g} — {_last_name(player)}  🟢 over · 🔴 under",
        value=_last5_signal(data.get("player_surface_matches"), prop_type, line),
        inline=False,
    )

    _div = _form_divergence(data.get("player_surface_matches"), prop_type, line, lean)
    if _div:
        e.add_field(name="⚠ Recent Form", value=_div, inline=False)

    # Quality-of-opposition + reliability context (Improvements 1, 3, 5).
    if data.get("stats_inflated"):
        e.add_field(name="⚠ Opposition Quality",
                    value="Stats inflated by weaker opposition — quality-adjusted figure used in projection.",
                    inline=False)
    if data.get("consistency_tier"):
        e.add_field(name="Consistency", value=data["consistency_tier"], inline=True)
    if data.get("retirement_risk"):
        _pc = data.get("pct_completed")
        e.add_field(name="⚠ Retirement Risk",
                    value=(f"2+ DNF in last 50 — {_pc:.0f}% completed (props may void)"
                           if isinstance(_pc, (int, float))
                           else "2+ retirements in last 50 matches (props may void)"),
                    inline=False)

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
    ta = data.get("ta_stats") or {}
    hand = _hand_label(ta.get("handedness"))

    # Form must match the selected surface (otherwise e.g. Sinner's Hard card
    # shows 100% win rate but a red from a clay loss). For a specific surface use
    # that surface's recent matches; for All, use the cross-surface form list.
    if surface == "All":
        form = data.get("form", [])
        form_label = "Last 10 Form"
    else:
        form = data.get(f"{surface}_matches", []) or []
        form_label = f"Last 10 Form ({surface})"

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

    e.add_field(name=form_label, value=_form_emojis(form, limit=10), inline=False)

    # Tournament Titles — only tournaments won at least once (missing = zero).
    # Omit the section entirely if the player has no recorded titles.
    titles = data.get("titles") or {}
    if titles:
        lines = [f"{t} 🏆 x{n}" for t, n in list(titles.items())[:18]]
        body = "\n".join(lines)
        if len(titles) > 18:
            body += f"\n_…+{len(titles) - 18} more_"
        e.add_field(name="Tournament Titles", value=body[:1024], inline=False)

    e.set_footer(text=FOOTER_52W)
    return e


# ── Discord client ──────────────────────────────────────────────────────────────
class BaselineBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Keep a global registration so the bot works in any server it's added to.
        await self.tree.sync()
        log.info("Global slash commands synced.")


client = BaselineBot()

PROP_CHOICES = [
    app_commands.Choice(name="Aces", value="Aces"),
    app_commands.Choice(name="Double Faults", value="Double Faults"),
    app_commands.Choice(name="Break Points Won", value="Break Points Won"),
    app_commands.Choice(name="Total Games", value="Total Games"),
    app_commands.Choice(name="Player Total Games Won", value="Player Total Games Won"),
]
SURFACE_CHOICES = [
    app_commands.Choice(name="Hard", value="Hard"),
    app_commands.Choice(name="Clay", value="Clay"),
    app_commands.Choice(name="Grass", value="Grass"),
]
# ATP Grand Slam round — only meaningful for an ATP Grand Slam court. Main draw
# is best-of-5, qualifying is best-of-3. Ignored for WTA / non-GS / non-ATP.
ROUND_CHOICES = [
    app_commands.Choice(name="Main Draw (best of 5)", value="main"),
    app_commands.Choice(name="Qualifying (best of 3)", value="qualifying"),
]
ATP_GRAND_SLAMS = {"Australian Open", "US Open", "Roland Garros", "Wimbledon"}
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
            # Surface not chosen — INTERLEAVE across surfaces so the 25-item cap
            # doesn't truncate whole surfaces (grass is last and was getting cut
            # to just Wimbledon). Round-robin one court per surface at a time.
            from itertools import zip_longest
            pool = [("None", None)]
            per_surface = [[(c, surf) for c in courts]
                           for surf, courts in COURTS_BY_SURFACE.items()]
            for group in zip_longest(*per_surface):
                for item in group:
                    if item:
                        pool.append(item)

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
    gs_round="ATP Grand Slam only: Main Draw (best of 5) or Qualifying (best of 3). Default Main Draw.",
)
@app_commands.choices(prop_type=PROP_CHOICES, surface=SURFACE_CHOICES, gs_round=ROUND_CHOICES)
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
    gs_round: app_commands.Choice[str] = None,
):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
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

        # ATP Grand Slam qualifying = best-of-3. Only applies for an ATP Grand
        # Slam court; ignored otherwise (default Main Draw / best-of-5 at a GS).
        is_atp_gs  = (court in ATP_GRAND_SLAMS) and (tour == "ATP")
        qualifying = is_atp_gs and gs_round is not None and gs_round.value == "qualifying"

        payload = {
            "player_id": p_id, "opponent_id": o_id,
            "player_name": p_name, "opponent_name": o_name,
            "tour": tour, "surface": surface_val,
            "court": court_key, "prop_type": prop_type.value,
            "prop_line": float(line),
            "qualifying": qualifying,
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
    finally:
        _leave_queue()


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
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
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
    finally:
        _leave_queue()


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
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
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
    finally:
        _leave_queue()


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
            "• **line** — the book line, e.g. 11.5\n"
            "• **gs_round** *(ATP Grand Slam only)* — Main Draw (best of 5) or "
            "Qualifying (best of 3). Defaults to Main Draw."
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


# ── Pick of the Day ─────────────────────────────────────────────────────────────
MEMBER_ROLE_NAME = os.getenv("BASELINE_MEMBER_ROLE", "Baseline Member")
POD_CHANNEL_ID = int(os.getenv("POD_CHANNEL_ID", "0") or "0")
# Daily auto-post local time. Defaults to midnight (00:00) US Eastern, which
# auto-handles EST/EDT via the zoneinfo database (no manual DST adjustment).
# Override POD_TZ (IANA name) and POD_HOUR/POD_MINUTE if needed.
try:
    from zoneinfo import ZoneInfo
    POD_TZINFO = ZoneInfo(os.getenv("POD_TZ", "America/New_York"))
except Exception:  # pragma: no cover — fall back to a fixed EST offset
    POD_TZINFO = datetime.timezone(datetime.timedelta(hours=-5))
# Daily auto-post trigger time (ET). The serialized generation run takes ~10
# min, so the post lands a bit after this. Default 21:00 ET → triggers at
# 9:00 PM ET. (Adjust POD_HOUR/POD_MINUTE if run time drifts. NOTE: Railway
# POD_HOUR/POD_MINUTE env vars OVERRIDE these defaults — clear them there if set.)
POD_HOUR = int(os.getenv("POD_HOUR", "16") or "16")
POD_MINUTE = int(os.getenv("POD_MINUTE", "50") or "50")
# Daily picks are PRE-GENERATED at 5:50 PM ET so the projections (~10 min) are
# ready to fire right after the 6 PM recap. The recap job then posts: recap →
# ranked list → 3x. (Env override PICKS_GEN_HOUR/MINUTE.)
# POTD trigger — the board eval starts here and the ranked list + 3x post when it
# finishes (~10 min later). Independent of the recap, which posts earlier.
PICKS_GEN_HOUR = int(os.getenv("PICKS_GEN_HOUR", "19") or "19")
PICKS_GEN_MINUTE = int(os.getenv("PICKS_GEN_MINUTE", "50") or "50")
# Ranked plays are delivered in pages of this many, each its own @everyone message
# (top-12 → two messages: 1-6 then 7-12).
# NOTE: RANKED_PAGE_SIZE (6-plays-per-message paging) was retired when the ⭐ got
# its own embed — the board is now one compact two-lines-per-play list that fits a
# single embed, and splits only at play boundaries if it ever outgrows one.
# One-off EXTRA run: on this ET date ONLY, re-run the ranked list + 3x at 11 PM ET
# (in addition to the normal daily run). The recurring schedule above is untouched;
# dedup in _log_picks_pending prevents any double-counting. Set to "" to disable —
# auto-reverts the next day.
POD_EXTRA_RUN_DATE = os.getenv("POD_EXTRA_RUN_DATE", "2026-07-14")
POD_EXTRA_RUN_HOUR = int(os.getenv("POD_EXTRA_RUN_HOUR", "22") or "22")
POD_EXTRA_RUN_MINUTE = int(os.getenv("POD_EXTRA_RUN_MINUTE", "40") or "40")
# NOTE: the `_daily_bundle` pre-generated-bundle mechanism was REMOVED on
# 2026-07-15. Nothing ever populated it, so its "reuse a bundle <40 min old" path
# was permanently dead and the board was always evaluated at trigger time — while
# the code read as though a pre-generation step existed. Cache warmth is now
# handled explicitly by daily_cache_prewarm 30 minutes before generation, and the
# board is evaluated exactly ONCE, at trigger time, against that warm cache.
# Optional one-shot post on startup for verifying a deploy (off by default).
POD_POST_ON_START = (os.getenv("POD_POST_ON_START", "0") or "0") not in ("0", "false", "False")
_pod_startup_done = False
# Optional one-shot post on startup for verifying a deploy (off by default).
POD_POST_ON_START = (os.getenv("POD_POST_ON_START", "0") or "0") not in ("0", "false", "False")
_pod_startup_done = False

# Feature 4 — daily Slate auto-post to the 📋・slate channel, at midnight ET
# alongside the Pick of the Day.
SLATE_CHANNEL_ID = int(os.getenv("SLATE_CHANNEL_ID", "1519546971344470027") or "0")
SLATE_HOUR = int(os.getenv("SLATE_HOUR", "0") or "0")      # 12:00 AM ET
SLATE_MINUTE = int(os.getenv("SLATE_MINUTE", "0") or "0")

# Daily win/loss record auto-post (the /results command is bot-only now).
# 11:45 PM ET by default — just before the Pick of the Day, after the day's
# picks have been graded by the resolver. Defaults to the POD channel.
RESULTS_CHANNEL_ID = int(os.getenv("RESULTS_CHANNEL_ID", str(POD_CHANNEL_ID or 0)) or "0")
# Minimum post-guard decided picks before the weekly calibration table means
# anything. All history up to 2026-07-14 is pre-guard (confidence possibly scored
# on a cache-poisoned snapshot), so the clean sample restarts from zero and the
# table stays suppressed until it rebuilds rather than reporting noise.
CALIBRATION_MIN_SAMPLE = 40

RESULTS_POST_HOUR = int(os.getenv("RESULTS_POST_HOUR", "19") or "19")
RESULTS_POST_MINUTE = int(os.getenv("RESULTS_POST_MINUTE", "45") or "45")

# ── One-off schedule override ────────────────────────────────────────────────
# On ONEOFF_SCHED_DATE only, the recap and the POTD run at the times below
# INSTEAD of their recurring slots. Each loop is registered at BOTH times and
# _slot_is_live() decides which firing actually runs, so a day never posts twice
# and no new task loop had to be wired up. Auto-reverts: on any other date the
# one-off slot no-ops and the normal 7:45 / 7:50 slots run as usual.
ONEOFF_SCHED_DATE = os.getenv("ONEOFF_SCHED_DATE", "2026-07-15")
ONEOFF_RECAP_HM   = (18, 15)    # recap  — 6:15 PM ET
ONEOFF_POTD_HM    = (18, 50)    # POTD   — 6:50 PM ET
ONEOFF_PREWARM_HM = (18, 20)    # cache pre-warm — 30 min before the one-off POTD

# ── Cache pre-warm ───────────────────────────────────────────────────────────
# Runs 30 minutes before the POTD generation and throws its results away. The
# ONLY thing it produces is a warm cache.
#
# Why it exists: the BP opponent-hold quality adjustment is CACHE-ONLY (see
# _bp_quality_adjusted_generated) — it never awaits a fetch, so its value is a
# pure function of cache state. That killed the timing race, but a COLD run still
# computes on a thin cache: measured 1/7 opponents resolved on the first run,
# climbing to 5/7 by the fifth as background warming landed, moving the BP
# projection 6.1 <-> 6.0. The generation job runs cold every day, so the picks
# that actually get posted were the ones computed on the thinnest cache.
#
# This fixes it at the SCHEDULING layer, not the math layer: warm first, then
# compute. No change to how any number is calculated — the same computation just
# runs against a full cache instead of an empty one.
#
# Proxy cost is ~neutral: these fetches already happened as background warming
# during the generation run. They are moved earlier, not added.
PREWARM_HOUR   = int(os.getenv("PREWARM_HOUR", "19") or "19")     # 30 min before
PREWARM_MINUTE = int(os.getenv("PREWARM_MINUTE", "20") or "20")   # the 7:50 POTD


def _slot_is_live(oneoff_hm: tuple) -> bool:
    """Should THIS firing run? True for the one-off slot on the override date, and
    for the normal slot on every other date.

    Matches on (hour, minute) with a couple of minutes' tolerance rather than
    exact equality — the loop wakes at the scheduled second, but a slow event loop
    could drift it past the minute boundary and silently skip the day's post.
    Safe because the one-off and normal slots are far apart (5:00 vs 7:45,
    6:50 vs 7:50)."""
    now = datetime.datetime.now(POD_TZINFO)
    mins_now = now.hour * 60 + now.minute
    mins_off = oneoff_hm[0] * 60 + oneoff_hm[1]
    is_oneoff_slot = abs(mins_now - mins_off) <= 2
    is_oneoff_date = now.strftime("%Y-%m-%d") == ONEOFF_SCHED_DATE
    live = is_oneoff_slot if is_oneoff_date else (not is_oneoff_slot)
    if not live:
        log.info("SLOT_SKIP | %s %02d:%02d | oneoff_date=%s (today=%s) oneoff_slot=%s "
                 "— this firing is not the live slot today",
                 "one-off" if is_oneoff_slot else "normal", now.hour, now.minute,
                 ONEOFF_SCHED_DATE, now.strftime("%Y-%m-%d"), is_oneoff_slot)
    return live
# One-off skip: don't post the daily recap on this ET date (it already posted
# earlier that day). Set to "" to disable. Resumes normally the next day.
RESULTS_SKIP_DATE = os.getenv("RESULTS_SKIP_DATE", "2026-06-30")
# One-off Pick of the Day skip: on this ET date the scans DON'T generate picks —
# the 4:50 scan posts a "no value, waiting for new tournaments" @everyone notice
# and the evening scan stays silent. Set to "" to disable. Resumes next day.
POD_SKIP_DATE = os.getenv("POD_SKIP_DATE", "2026-07-11")
MSG_POD_SKIP = (
    "🎾 **No Pick of the Day today.** There isn't enough value on the board "
    "right now — we're between tournaments. We'd rather sit out than force a "
    "weak play, so we're waiting for the new events to begin. Back with fresh "
    "plays soon. 🎾"
)
MSG_NO_PICK = (
    "No Pick of the Day right now — nothing on the board cleared the "
    "confidence threshold (or the board is unavailable). Try again later."
)
MSG_NO_PICK_DAILY = (
    "No qualifying plays today — nothing on the board cleared our quality bar "
    "(Aces / Break Points at 75%+ confidence, Total Games Won at 80%+, Total "
    "Games at 90%+). We'd rather sit out than force a weak play. Check back tomorrow. 🎾"
)


def _member_gate(interaction: discord.Interaction) -> bool:
    """Soft Baseline Member gate: if the guild HAS the role, require it;
    if the role doesn't exist (or it's a DM), allow — never hard-breaks."""
    guild = interaction.guild
    if guild is None:
        return True
    role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
    if role is None:
        return True
    member = interaction.user
    return isinstance(member, discord.Member) and role in member.roles


async def _fetch_streak(player_id, tour):
    if not player_id:
        return {}
    try:
        return await backend_get("/api/player/streak",
                                 {"player_id": player_id, "tour": tour or "ATP"}, GENERIC_TIMEOUT)
    except Exception:  # noqa: BLE001
        return {}


async def _annotate_form_alerts(picks: list):
    """Feature 5 — tag each pick whose player is on a 5+ win/loss streak so the
    top-3 output can show a small form-alert note. Best-effort; never raises."""
    try:
        streaks = await asyncio.gather(
            *[_fetch_streak(p.get("player_id"), p.get("tour", "ATP")) for p in picks],
            return_exceptions=True,
        )
    except Exception:  # noqa: BLE001
        streaks = [{} for _ in picks]
    for p, s in zip(picks, streaks):
        p["form_alert"] = ""
        if isinstance(s, dict) and (s.get("streak_len") or 0) >= 5:
            t = s.get("streak_type")
            icon = "🔥" if t == "W" else "❄️"
            p["form_alert"] = f"{icon} {t}{s.get('streak_len')} streak"


def _form_note(pick: dict) -> str:
    fa = pick.get("form_alert")
    return f"  ·  {fa}" if fa else ""


def _pick_line(pick: dict) -> str:
    """One compact line summarising a pick for the 'Also Today' list."""
    proj = pick.get("projection")
    lean = (pick.get("lean") or "").upper() or ("OVER" if (pick.get("edge") or 0) >= 0 else "UNDER")
    arrow = "🔼" if lean == "OVER" else "🔽"
    loc = pick.get("tournament") or f"{pick.get('surface','')} court"
    proj_txt = f"proj {proj:.1f}" if isinstance(proj, (int, float)) else "proj —"
    return (f"{arrow} **{pick['player']}** {lean} {pick['line']:g} {pick['prop_type']}{_form_note(pick)}  "
            f"· {proj_txt} · {pick.get('confidence', 0):.0f}% conf\n"
            f"┕ vs {pick['opponent']} · {loc}")


_POD_AUTHORS = ["🏆 Pick of the Day", "🥈 #2 Top Play", "🥉 #3 Top Play"]


def _single_pick_embed(pick: dict, author: str) -> discord.Embed:
    """A FULL /prop-style stat breakdown for one pick (so every listed play —
    not just #1 — shows its statistics). Tournament + surface come from the
    player's upcoming match on Sofascore."""
    court_display = pick.get("tournament") or f"{pick['surface']} court"
    e = prop_embed(
        pick["player"], pick["opponent"], pick["prop_type"],
        pick["surface"], court_display, pick["line"], pick["data"],
    )
    e.set_author(name=author)
    if pick.get("form_alert"):
        e.add_field(name="🔥 Form Alert",
                    value=f"**{pick['player']}** is on a {pick['form_alert']}.", inline=False)
    # STEP 5 — a Total Games pick cleared an elevated, prop-specific bar.
    if pick.get("prop_type") == "Total Games":
        e.add_field(
            name="📊 Elevated Threshold",
            value=(f"Total Games is held to a stricter **{pick_of_day.TOTAL_GAMES_MIN_CONF}%** "
                   f"confidence bar (vs {pick_of_day.STANDARD_MIN_CONF}% for the other props) "
                   f"— combined-player and match-length variance make it less predictable, so "
                   f"it only surfaces when the data strongly supports it."),
            inline=False)
    return e


def picks_embeds(picks: list) -> list:
    """One full stat embed per pick (#1 Pick of the Day, #2, #3) so statistics
    show for EVERY listed play. Sent together as a multi-embed message."""
    return [_single_pick_embed(p, _POD_AUTHORS[i] if i < len(_POD_AUTHORS) else f"#{i+1}")
            for i, p in enumerate(picks)]


async def _deliver_pod(picks: list, send, mention: bool = False) -> None:
    """Deliver the per-pick stat embeds via ``send`` (channel.send or
    interaction.followup.send). One multi-embed message if the combined size
    fits Discord's 6000-char cap, otherwise one message per pick. ``mention``
    pings @everyone (used for the automatic daily post, not the /command)."""
    embeds = picks_embeds(picks)
    content = "@everyone" if mention else None
    if sum(len(e) for e in embeds) <= 5900:
        await send(content=content, embeds=embeds, allowed_mentions=EVERYONE_MENTION)
    else:
        for i, e in enumerate(embeds):
            await send(content=(content if i == 0 else None), embed=e,
                       allowed_mentions=EVERYONE_MENTION)


def picks_embed(picks: list) -> discord.Embed:
    """Single combined embed (kept for the line-monitor/compat callers)."""
    return _single_pick_embed(picks[0], _POD_AUTHORS[0])


def pick_embed(pick: dict) -> discord.Embed:
    """Single-pick embed (kept for compatibility)."""
    return _single_pick_embed(pick, _POD_AUTHORS[0])


# Pick of the Day is bot-broadcast only (the automatic daily post) — no
# user-invokable command.


# ── Daily auto-post + results logging + line monitor ────────────────────────────
_line_monitor_task = None


def _pick_to_record(p: dict, group: str = "potd") -> dict:
    return {
        "player": p.get("player", ""), "opponent": p.get("opponent", ""),
        "prop_type": p.get("prop_type", ""), "line": p.get("line"),
        "model_projection": p.get("projection"), "lean": (p.get("lean") or "").upper(),
        "confidence": p.get("confidence"), "result": "PENDING",
        "original_line": p.get("original_line", p.get("line")),
        "tournament": p.get("tournament") or "", "surface": p.get("surface") or "",
        "pick_group": group,
        "confidence_breakdown": _breakdown_json(p),
    }


def _breakdown_json(p: dict) -> str:
    """Compact JSON of the confidence component breakdown (for later calibration).
    Empty string when unavailable. Truncated so a huge dict can't bloat a row."""
    try:
        bd = (p.get("data") or {}).get("confidence_breakdown")
        if not bd:
            return ""
        return json.dumps(bd, separators=(",", ":"))[:2000]
    except Exception:  # noqa: BLE001
        return ""


async def _log_picks_pending(picks: list, group: str = "potd"):
    """Feature 1 — log each pick to the durable results tracker as PENDING,
    tagged with its pick group ("potd" or "3x").

    DEDUP: a (player, prop_type, group) already logged in the last ~18h is NOT
    logged again — so a same-day re-run (e.g. an extra evening run) never creates
    a duplicate row that would count twice when the prop resolves the next day."""
    try:
        rec = await asyncio.to_thread(results_tracker.get_record)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=18)
        existing = set()
        for q in (rec or {}).get("picks", []):
            ga = q.get("generated_at")
            try:
                dt = datetime.datetime.fromisoformat((ga or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if dt >= cutoff:
                existing.add((pick_of_day._norm(q.get("player", "")),
                              q.get("prop_type"), (q.get("pick_group") or "potd")))
    except Exception:  # noqa: BLE001
        existing = set()

    logged = skipped = 0
    for p in picks:
        key = (pick_of_day._norm(p.get("player", "")), p.get("prop_type"), group)
        if key in existing:
            skipped += 1
            log.info("POD: skip duplicate log (already logged today): %s %s [%s]",
                     p.get("player"), p.get("prop_type"), group)
            continue
        rec = await asyncio.to_thread(results_tracker.log_pick, _pick_to_record(p, group))
        if rec:
            p["pick_id"] = rec.get("id")
            logged += 1
            existing.add(key)   # guard against duplicates within this batch too
    log.info("POD: logged %d %s picks (%d skipped as same-day duplicates)",
             logged, group, skipped)


def _start_line_monitor(channel, picks: list):
    """Feature 2 — start the bot-only line-movement monitor for these picks."""
    global _line_monitor_task
    try:
        if _line_monitor_task and not _line_monitor_task.done():
            _line_monitor_task.cancel()

        async def _post_alert(text):
            await channel.send(f"@everyone\n{text}", allowed_mentions=EVERYONE_MENTION)

        _line_monitor_task = asyncio.create_task(
            line_monitor.monitor(picks, pick_of_day.current_board_lines, _post_alert))
        log.info("POD: line monitor started for %d picks", len(picks))
    except Exception:  # noqa: BLE001
        log.exception("failed to start line monitor")


# ── Baseline 3x — two-pick slip (posts alongside the Pick of the Day) ────────
COLOR_THREEX = 0x9B59B6   # purple — distinct from the green/red POTD embeds


def threex_embed(legs: list) -> discord.Embed:
    """The Baseline 3x — two independent legs packaged as one slip. Distinct
    purple color so it's visually separable from the Pick of the Day at a glance.

    Just the legs. No preamble and no slip-strength block: the title says what
    this is, and restating the rules of a two-leg slip every single day is noise
    a returning subscriber reads past. Slip strength was derived from the two
    confidences already shown — it told them nothing they couldn't see."""
    # Dated by the SLATE (when the legs play), not by when this was generated.
    slate = _slate_date(legs)
    e = discord.Embed(title=f"🎟️ Baseline 3x — {slate.month}/{slate.day}",
                      color=COLOR_THREEX)
    lines = []
    for i, leg in enumerate(legs, 1):
        lean = _lean_of(leg)
        proj, conf = leg.get("projection"), leg.get("confidence")
        play = f"{lean} {leg['line']:g} {_short_prop(leg['prop_type'])}".upper()
        bits = [f"{LEAN_DOT.get(lean, '⚪')} **{play}**"]
        if isinstance(proj, (int, float)):
            bits.append(f"Proj {proj:.1f}")
        if isinstance(conf, (int, float)):
            bits.append(f"{conf:.0f}%")
        lines.append(f"**{i}. {leg['player']}** vs {_short_opp(leg.get('opponent'))}")
        lines.append(" · ".join(bits))
        if i < len(legs):
            lines.append("")
    e.description = "\n".join(lines)
    return _stamped_footer(e, when=slate)


# ── Ranked plays list (the daily post) ───────────────────────────────────────
def _ranked_stats(prop_type: str, data: dict) -> str:
    """Key player stats for the prop — same fields as the /prop stat card."""
    ps = data.get("player_stats") or {}
    if prop_type == "Aces":
        return (f"Ace rate **{_num(ps.get('aces'))}**/m · "
                f"Opp conceded **{_num(data.get('opponent_ace_against'))}**/m")
    if prop_type == "Break Points Won":
        conv = data.get("bp_blended_conv_pct") or ps.get("bp_converted")
        return (f"BP conv **{_pct(conv)}** · "
                f"Opp BP faced **{_num(data.get('bp_blended_opp_faced'))}**/m")
    if prop_type == "Player Total Games Won":
        return (f"Hold **{_pct(ps.get('service_games_won_pct'))}** · "
                f"Ret games won **{_pct(ps.get('return_games_won_pct'))}**")
    if prop_type == "Total Games":
        ch = data.get("combined_hold")
        return f"Combined hold **{_pct(ch)}**" if ch is not None else ""
    return ""


# ── Shared presentation helpers ──────────────────────────────────────────────
# One indicator per concept, never stacked:
#   lean     -> 🟢 OVER / 🔴 UNDER / ⚪ no lean
#   result   -> ✅ win / ❌ loss / ⚪ push / 🚫 void (DNP)
# Numbers: projections + edges to ONE decimal, confidence to a WHOLE percent.
LEAN_DOT = {"OVER": "🟢", "UNDER": "🔴"}

# Prop names, shortened for the list view. The full name is a big share of the
# line width on a phone ("Player Total Games Won" is 22 chars), and it was what
# pushed every play onto a third wrapped line.
PROP_SHORT = {
    "Break Points Won":       "BP Won",
    "Player Total Games Won": "Games Won",
    "Total Games":            "Total Games",
    "Double Faults":          "DFs",
    "Aces":                   "Aces",
}


def _short_prop(prop: str) -> str:
    return PROP_SHORT.get(prop, prop or "")


def _short_opp(name: str) -> str:
    """'Lola Radivojević' -> 'L. Radivojević'. Keeps the matchup identifiable
    while cutting the width that forced a line wrap."""
    parts = (name or "").split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else (name or "")


def _lean_of(pick: dict) -> str:
    return (pick.get("lean") or "").upper() or (
        "OVER" if (pick.get("edge") or 0) >= 0 else "UNDER")


def _lean_color(lean: str) -> int:
    return COLOR_OVER if lean == "OVER" else COLOR_UNDER if lean == "UNDER" else COLOR_NEUTRAL


def _edge_txt(edge) -> str:
    """Signed edge to one decimal, e.g. +4.4 / -2.3. Em-dash when unavailable."""
    return f"{edge:+.1f}" if isinstance(edge, (int, float)) else "—"


def _slate_date(picks) -> datetime.datetime:
    """The ET date the plays actually PLAY — not the date they were generated.

    Picks are selected by a 24-hour lookahead, so an evening trigger is always
    building TOMORROW's card: the 7/14 22:26 post covered 7/15 matches but was
    labelled 7/14, which made the next day's recap look like it was scoring a
    different day's plays. The slate date is a property of the matches, so read it
    from the matches — every pick carries start_timestamp.

    Uses the MOST COMMON match date among the plays (a late-evening board can
    straddle two dates; the bulk of the card is the card). Falls back to 'now'
    only when no pick carries a start time."""
    from collections import Counter
    dates = []
    for p in (picks or []):
        ts = p.get("start_timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            dates.append(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                         .astimezone(POD_TZINFO).date())
    if not dates:
        return datetime.datetime.now(POD_TZINFO)
    top = Counter(dates).most_common(1)[0][0]
    return datetime.datetime(top.year, top.month, top.day, tzinfo=POD_TZINFO)


def _stamped_footer(e: discord.Embed, text: str = FOOTER_PROJECTION,
                    when: datetime.datetime = None) -> discord.Embed:
    """Footer = the standing disclaimer + the date the post is ABOUT, so a
    screenshot carries its own date. Pass ``when`` (the slate date) for pick
    posts; results posts pass their own date; anything else falls back to now."""
    d = when or datetime.datetime.now(POD_TZINFO)
    e.set_footer(text=f"{text} • {d.month}/{d.day}")
    return e


def _play_headline(pick: dict, rank: int = None) -> str:
    """'2. Player vs Opponent — Break Points Won 3.5' (rank optional)."""
    prefix = f"**{rank}.** " if rank else ""
    return (f"{prefix}**{pick['player']}** vs {pick['opponent']} — "
            f"{pick['prop_type']} {pick['line']:g}")


def _play_statline(pick: dict) -> str:
    """The one-line stat row: '🔴 UNDER · Proj 4.2 · Edge -2.3 · 76%'.
    Fields that have no value are OMITTED rather than shown as blank/N-A."""
    lean = _lean_of(pick)
    bits = [f"{LEAN_DOT.get(lean, '⚪')} **{lean}**"]
    proj = pick.get("projection")
    if isinstance(proj, (int, float)):
        bits.append(f"Proj {proj:.1f}")
    edge = pick.get("edge")
    if isinstance(edge, (int, float)):
        bits.append(f"Edge {edge:+.1f}")
    conf = pick.get("confidence")
    if isinstance(conf, (int, float)):
        bits.append(f"**{conf:.0f}%**")
    return " · ".join(bits)


def _ranked_line(pick: dict, rank: int) -> str:
    """One ranked play in the LIST view — two SHORT lines that don't wrap on a
    phone. Carries exactly what a subscriber needs to act: the play, the lean,
    the projection, the confidence.

    Edge is deliberately omitted here: it's just projection minus line, so it's
    derivable from what's shown and was costing width that forced a third
    wrapped line. All depth (key stats, win prob, expected sets) lives in the ⭐
    embed only."""
    lean = _lean_of(pick)
    proj = pick.get("projection")
    conf = pick.get("confidence")
    l1 = f"**{rank}. {pick['player']}** vs {_short_opp(pick.get('opponent'))}"
    # THE PLAY IS THE HEADLINE — bold AND uppercase so it outranks everything
    # beside it. Projection and confidence are supporting numbers and are left
    # in plain weight; if everything is bold, nothing is.
    play = f"{lean} {pick['line']:g} {_short_prop(pick['prop_type'])}".upper()
    bits = [f"{LEAN_DOT.get(lean, '⚪')} **{play}**"]
    if isinstance(proj, (int, float)):
        bits.append(f"Proj {proj:.1f}")
    if isinstance(conf, (int, float)):
        bits.append(f"{conf:.0f}%")
    return l1 + "\n" + " · ".join(bits)


def potd_embed(pick: dict) -> discord.Embed:
    """The ⭐ Pick of the Day as its own dedicated embed, posted first.

    THE ONLY PLACE WITH DEPTH. Every other post carries just the play, lean,
    projection and confidence; the reasoning lives here.

    The stat row is ONE line, not three inline fields. Inline fields render
    three-across on desktop but STACK on a narrow phone, and each one costs a
    label line plus a value line — so three numbers ate six lines and buried the
    play. One dot-separated line reads identically on both clients."""
    data = pick.get("data") or {}
    lean = _lean_of(pick)
    # Titled with the SLATE date — the day this match PLAYS. A 10pm trigger builds
    # tomorrow's card, so "PICK OF THE DAY" without a date (or with the generation
    # date) tells a subscriber the wrong day.
    _sl = _slate_date([pick])
    e = discord.Embed(title=f"⭐ PICK OF THE DAY — {_sl.month}/{_sl.day}",
                      color=_lean_color(lean))

    loc = pick.get("tournament") or (f"{pick.get('surface')} court"
                                     if pick.get("surface") else None)
    proj, edge = pick.get("projection"), pick.get("edge")
    conf = pick.get("confidence")

    # The play, then the numbers, in two blocks — no field stacking.
    # THE PLAY IS THE HEADLINE — bold AND uppercase, on its own line, above the
    # supporting numbers which stay in plain weight.
    play = f"{lean} {pick['line']:g} {pick['prop_type']}".upper()
    head = [f"**{pick['player']}** vs **{pick['opponent']}**",
            f"{LEAN_DOT.get(lean, '⚪')} **{play}**"]
    row = []
    if isinstance(proj, (int, float)):
        row.append(f"Proj {proj:.1f}")
    if isinstance(edge, (int, float)):
        row.append(f"Edge {edge:+.1f}")
    if isinstance(conf, (int, float)):
        row.append(f"Conf {conf:.0f}%")
    if row:
        head.append(" · ".join(row))
    if loc:
        head.append(f"_{loc}_")
    e.description = "\n".join(head)

    stats = _ranked_stats(pick["prop_type"], data)
    if pick.get("coin_flip"):
        stats = ((stats + "\n") if stats else "") + \
            "⚠️ **Coin-flip zone** — line in the highest-variance band"
    cap = data.get("confidence_cap_reason")
    if cap:
        stats = ((stats + "\n") if stats else "") + f"_Capped: {cap}_"
    fa = pick.get("form_alert")
    if fa:
        stats = ((stats + "\n") if stats else "") + fa
    # Win prob / expected sets join Key Stats rather than taking a second field —
    # one depth block, not two.
    ctx = []
    p1wp, esets = data.get("p1_win_prob"), data.get("expected_sets")
    if isinstance(p1wp, (int, float)):
        ctx.append(f"Win prob **{p1wp:.0f}%**")
    if isinstance(esets, (int, float)):
        ctx.append(f"Exp sets **{esets:.1f}**")
    if ctx:
        stats = ((stats + "\n") if stats else "") + " · ".join(ctx)
    if stats:
        e.add_field(name="Key Stats", value=stats[:1024], inline=False)

    # Dated by the SLATE (when this match plays), not by when it was generated.
    return _stamped_footer(e, when=_slate_date([pick]))


_DESC_LIMIT = 3800        # Discord's description cap is 4096 — leave headroom


def ranked_embeds(ranked: list, start_rank: int = 1, total: int = None) -> list:
    """The RANKED BOARD — every qualifying play from ``start_rank`` on, two lines
    each, blank line between. The ⭐ is NOT here; it gets its own embed via
    potd_embed() posted above this one.

    Plays are joined into the description (not one field each) because Discord
    pads consecutive fields with uneven whitespace on mobile, which is exactly
    the run-together look this replaces. A blank line between blocks renders the
    same on both clients.

    Splits into further embeds ONLY at play boundaries — a play's two lines are
    never separated across embeds."""
    # Dated by the SLATE (when these plays play), not by when they were
    # generated — an evening trigger always builds TOMORROW's card.
    slate = _slate_date(ranked)
    total = total if total is not None else len(ranked) + start_rank - 1
    if not ranked:
        return []

    blocks = [_ranked_line(p, i) for i, p in enumerate(ranked, start_rank)]

    pages, cur = [], []
    for b in blocks:
        candidate = "\n\n".join(cur + [b])
        if cur and len(candidate) > _DESC_LIMIT:
            pages.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if cur:
        pages.append(cur)

    embeds = []
    rank_cursor = start_rank
    for idx, page in enumerate(pages):
        first, last = rank_cursor, rank_cursor + len(page) - 1
        rank_cursor = last + 1
        title = f"🎾 Ranked Board — {slate.month}/{slate.day}"
        if idx:
            title += " (cont.)"
        e = discord.Embed(title=title, color=COLOR_NEUTRAL)
        header = (f"Plays **{first}–{last}** of {total}\n\n"
                  if len(pages) > 1 else "")
        e.description = header + "\n\n".join(page)
        embeds.append(e)
    _stamped_footer(embeds[-1], when=slate)
    return embeds


def _recent_pick_keys(hours: int = 12) -> set:
    """(_norm(player), prop_type) for every pick logged in the last ``hours`` —
    so the evening scan skips whatever the afternoon scan already posted. Safe:
    returns an empty set on any error (evening scan then behaves like a normal run)."""
    try:
        rec = results_tracker.get_record()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        keys = set()
        for p in rec.get("picks", []):
            ga = p.get("generated_at")
            if not ga:
                continue
            try:
                dt = datetime.datetime.fromisoformat(ga.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if dt >= cutoff:
                keys.add((pick_of_day._norm(p.get("player", "")), p.get("prop_type")))
        return keys
    except Exception:  # noqa: BLE001
        return set()


async def _post_daily_picks(channel, track: bool = True) -> str:
    """Post the daily RANKED LIST (⭐ #1 = Pick of the Day, then 2..N of every
    qualifying play), then the Baseline 3x slip immediately after. Evaluates the
    board here, at trigger time. When ``track`` is set, logs every play (POTD
    group) + slip legs (3x group) and starts the line-movement monitor over ALL
    of them. Never raises.

    The old `_daily_bundle` "pre-generated bundle, reused if <40 min old" path is
    GONE. It was vestigial: nothing ever populated the dict, so `fresh` was always
    False and this always regenerated inline — the branch was dead code that read
    like a live optimisation, and it implied a pre-generation step that did not
    exist. Cache warmth is now handled honestly by daily_cache_prewarm 30 minutes
    ahead; the board is evaluated exactly ONCE here, against that warm cache."""
    bundle = await pick_of_day.generate_ranked_and_slip()
    ranked = bundle.get("ranked") or []
    slip = bundle.get("slip") or []
    log.info("daily picks: board evaluated at trigger time (%d ranked) — "
             "cache pre-warmed at %02d:%02d", len(ranked),
             ONEOFF_PREWARM_HM[0] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
             == ONEOFF_SCHED_DATE else PREWARM_HOUR,
             ONEOFF_PREWARM_HM[1] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
             == ONEOFF_SCHED_DATE else PREWARM_MINUTE)

    if not ranked:
        no_play = discord.Embed(description=MSG_NO_PICK_DAILY, color=COLOR_NEUTRAL)
        no_play.set_author(name="🎾 Baseline Ranked Plays")
        await channel.send(embed=no_play)
        return "no qualifying plays — posted no-play notice"

    await _annotate_form_alerts(ranked)

    # ⭐ Pick of the Day gets its OWN embed, posted first, then the ranked board
    # (plays 2..N) below it. Both ride one @everyone message so the headline play
    # and the board arrive together rather than as separate pings.
    post = [potd_embed(ranked[0])] + ranked_embeds(ranked[1:], start_rank=2,
                                                   total=len(ranked))
    await channel.send(
        content=("@everyone" if track else None),
        embeds=post[:10], allowed_mentions=EVERYONE_MENTION)
    # Overflow (a board so long it needed >9 board embeds) continues unpinged.
    for i in range(10, len(post), 10):
        await channel.send(embeds=post[i:i + 10])

    # ── LOG ONLY AFTER A SUCCESSFUL SEND ─────────────────────────────────────
    # This used to log BEFORE posting, so a board that was never published — or
    # was superseded by a later re-run — still entered the permanent record. On
    # 7/14 that put Parks Total Games and Sakkari Total Games into the ledger from
    # a 22:26 board, and 18 minutes later the Total Games bar moved 80 -> 85 and
    # both dropped off the card that actually posted. The recap then scored two
    # plays no subscriber was ever shown.
    # The record must contain what was PUBLISHED, nothing else. If the send above
    # raises, we never reach this line and nothing is logged — which is correct:
    # an unposted play is not a play.
    if track:
        await _log_picks_pending(ranked, group="potd")

    # Baseline 3x — a SEPARATE post right after the ranked list.
    if slip:
        if track:
            await _log_picks_pending(slip, group="3x")
        await channel.send(
            content=("@everyone" if track else None),
            embed=threex_embed(slip), allowed_mentions=EVERYONE_MENTION)

    if track:
        _start_line_monitor(channel, ranked + slip)   # monitor every play + both legs
    slip_note = (f" + 3x [{slip[0]['player']}, {slip[1]['player']}]"
                 if slip else " (no 3x — thin pool)")
    return f"posted {len(ranked)} ranked, ⭐ {ranked[0]['player']} {ranked[0]['prop_type']}{slip_note}"


# The EXACT plays posted at 9:18 PM on 7/13 (from that post; surfaces/courts
# confirmed against the logged rows). Hardcoded because the DB holds many duplicate
# runs from today's schedule changes and isn't a clean source. Re-scored with the
# current model at 11:40 PM — same plays, same order, updated confidence.
_REPOST_SPECS_0713 = [
    {"player": "Panna Udvardy",        "opponent": "Leyre Romero Gormaz", "prop_type": "Break Points Won", "line": 4.5, "surface": "Clay", "tournament": "Iasi"},
    {"player": "Aliaksandra Sasnovich", "opponent": "Anna Blinkova",       "prop_type": "Break Points Won", "line": 4.5, "surface": "Hard", "tournament": "Athens"},
    {"player": "Simona Waltert",       "opponent": "Katarzyna Kawa",       "prop_type": "Break Points Won", "line": 5.5, "surface": "Clay", "tournament": "Iasi"},
    {"player": "Laura Samson",         "opponent": "Laura Pigossi",        "prop_type": "Total Games",      "line": 19.5, "surface": "Clay", "tournament": "Kitzbuhel"},
    {"player": "Ignacio Buse",         "opponent": "Stefanos Tsitsipas",   "prop_type": "Break Points Won", "line": 2.0, "surface": "Clay", "tournament": "Gstaad"},
    {"player": "Martin Krumich",       "opponent": "Stefano Travaglia",    "prop_type": "Break Points Won", "line": 3.0, "surface": "Clay", "tournament": "Bastad"},
]
# 3x legs posted at 9:18: Sasnovich (Athens) + Samson (Kitzbuhel).
_REPOST_SLIP_0713 = [_REPOST_SPECS_0713[1], _REPOST_SPECS_0713[3]]


async def _repost_todays_plays(channel) -> str:
    """Re-post the EXACT 9:18 PM plays (fixed specs) re-scored with the current
    model — same plays, same order, updated confidence. Does NOT re-log, so
    nothing double-counts; the original rows remain the record of truth."""
    ranked = await pick_of_day.evaluate_fixed_props(_REPOST_SPECS_0713)  # order preserved
    slip = await pick_of_day.evaluate_fixed_props(_REPOST_SLIP_0713)
    if not ranked:
        log.info("REPOST: re-eval produced nothing")
        return "re-eval produced nothing"
    await _annotate_form_alerts(ranked)

    embeds = [potd_embed(ranked[0])] + ranked_embeds(ranked[1:], start_rank=2,
                                                     total=len(ranked))
    for i in range(0, len(embeds), 10):
        await channel.send(content=("@everyone" if i == 0 else None),
                           embeds=embeds[i:i + 10], allowed_mentions=EVERYONE_MENTION)
    if slip and len(slip) >= 2:
        await channel.send(content="@everyone", embed=threex_embed(slip[:2]),
                           allowed_mentions=EVERYONE_MENTION)
    return (f"re-posted {len(ranked)} plays (updated confidence)"
            + (" + 3x" if slip and len(slip) >= 2 else ""))


@tasks.loop(time=[
    datetime.time(hour=ONEOFF_PREWARM_HM[0], minute=ONEOFF_PREWARM_HM[1], tzinfo=POD_TZINFO),
    datetime.time(hour=PREWARM_HOUR, minute=PREWARM_MINUTE, tzinfo=POD_TZINFO),
])
async def daily_cache_prewarm():
    """Walk the day's board 30 minutes before generation and THROW THE RESULTS
    AWAY. The only product is a warm cache — see the block comment on
    ONEOFF_PREWARM_HM for why this is a scheduling fix, not a math one.

    Posts nothing. Never raises: a failed pre-warm must degrade to 'the
    generation runs cold', exactly as it does today, never to a missing POTD."""
    if not _slot_is_live(ONEOFF_PREWARM_HM):
        return
    try:
        t0 = time.time()
        bundle = await pick_of_day.generate_ranked_and_slip()
        n = len(bundle.get("ranked") or [])
        log.info(
            "PREWARM | board walked in %.1f min | %d qualifying plays (DISCARDED — "
            "this run exists only to warm the player-stats and opponent-hold "
            "caches so the %02d:%02d generation computes warm). Check BP_QADJ "
            "resolved-fraction on the next run: it should now sit near 1.0.",
            (time.time() - t0) / 60.0, n,
            ONEOFF_POTD_HM[0] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
            == ONEOFF_SCHED_DATE else PICKS_GEN_HOUR,
            ONEOFF_POTD_HM[1] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
            == ONEOFF_SCHED_DATE else PICKS_GEN_MINUTE,
        )
    except Exception:  # noqa: BLE001
        log.exception("PREWARM failed — generation will run cold (no worse than "
                      "before the pre-warm existed)")


@daily_cache_prewarm.before_loop
async def _before_cache_prewarm():
    await client.wait_until_ready()


@tasks.loop(time=[
    datetime.time(hour=ONEOFF_POTD_HM[0], minute=ONEOFF_POTD_HM[1], tzinfo=POD_TZINFO),
    datetime.time(hour=PICKS_GEN_HOUR, minute=PICKS_GEN_MINUTE, tzinfo=POD_TZINFO),
])
async def daily_picks_generate():
    """THE POTD TRIGGER — evaluates the board and posts the ⭐ Pick of the Day +
    Ranked Board (@everyone) + the 3x when the run finishes (~6-10 min).
    Independent of the recap, which posts earlier.

    Registered at BOTH the one-off slot (6:50 PM on ONEOFF_SCHED_DATE) and the
    recurring slot (7:50 PM); _slot_is_live picks which one actually runs, so the
    override date posts once at 6:50 and every other date once at 7:50."""
    if not _slot_is_live(ONEOFF_POTD_HM):
        return
    if not POD_CHANNEL_ID:
        return
    try:
        channel = client.get_channel(POD_CHANNEL_ID)
        if channel is None:
            log.warning("POTD trigger: channel %s not found", POD_CHANNEL_ID)
            return
        # One-off skip date — post the no-value notice instead of generating.
        if POD_SKIP_DATE and datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d") == POD_SKIP_DATE:
            e = discord.Embed(description=MSG_POD_SKIP, color=COLOR_NEUTRAL)
            e.set_author(name="🎾 Baseline Ranked Plays")
            await channel.send(content="@everyone", embed=e, allowed_mentions=EVERYONE_MENTION)
            log.info("POTD trigger: skip-date %s — posted no-value notice", POD_SKIP_DATE)
            return
        status = await _post_daily_picks(channel, track=True)
        log.info("POTD trigger: %s", status)
    except Exception:  # noqa: BLE001
        log.exception("POTD trigger failed")


@daily_picks_generate.before_loop
async def _before_picks_generate():
    await client.wait_until_ready()


@tasks.loop(time=datetime.time(hour=POD_EXTRA_RUN_HOUR, minute=POD_EXTRA_RUN_MINUTE,
                               tzinfo=POD_TZINFO))
async def extra_pod_run():
    """One-off EXTRA run: on POD_EXTRA_RUN_DATE only, a FRESH POTD scan + post at
    POD_EXTRA_RUN_HOUR:MINUTE ET (currently 10:10 PM on 2026-07-14). No-op on any
    other date, so the normal recurring 7:50 PM trigger is untouched.

    Fresh board — not the earlier re-post of fixed plays (that one-off is done).
    The 5:50 pre-generated bundle is discarded first so this re-scans rather than
    replaying a stale bundle from before tonight's confidence fixes.

    track=True, so plays are logged and line-monitored like any real post.
    _log_picks_pending de-dupes on (player, prop, group) within 18h, so anything
    the 7:50 run already logged won't double-count; genuinely new plays will log."""
    if not POD_CHANNEL_ID or not POD_EXTRA_RUN_DATE:
        return
    if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d") != POD_EXTRA_RUN_DATE:
        return
    try:
        channel = client.get_channel(POD_CHANNEL_ID)
        if channel is None:
            log.warning("POD extra run: channel %s not found", POD_CHANNEL_ID)
            return
        # (No bundle to clear — _post_daily_picks always evaluates the board at
        # trigger time now; the vestigial pre-generated-bundle path is gone.)
        status = await _post_daily_picks(channel, track=True)
        log.info("POD one-off %02d:%02d fresh run (%s): %s",
                 POD_EXTRA_RUN_HOUR, POD_EXTRA_RUN_MINUTE, POD_EXTRA_RUN_DATE, status)
    except Exception:  # noqa: BLE001
        log.exception("POD extra fresh run failed")


@extra_pod_run.before_loop
async def _before_extra_pod_run():
    await client.wait_until_ready()


# Pick of the Day is broadcast only via the scheduled daily auto-post — there is
# no manual /postpicks command (removed by request).


# ── Feature 4 — daily Slate auto-post (📋・slate channel) ─────────────────────────
async def _post_slate(channel) -> str:
    data = await backend_get("/api/slate/today", {}, 80)
    if not data or not data.get("available"):
        await asyncio.sleep(2)
        data = await backend_get("/api/slate/today", {}, 80)
    # Automatic daily slate pings @everyone (the /slate command does not).
    await channel.send(content="@everyone", embed=slate_embed(data),
                       allowed_mentions=EVERYONE_MENTION)
    return f"posted slate ({(data or {}).get('count', 0)} matches)"


@tasks.loop(time=datetime.time(hour=SLATE_HOUR, minute=SLATE_MINUTE, tzinfo=POD_TZINFO))
async def daily_slate():
    if not SLATE_CHANNEL_ID:
        return
    try:
        channel = client.get_channel(SLATE_CHANNEL_ID)
        if channel is None:
            log.warning("daily slate: channel %s not found", SLATE_CHANNEL_ID)
            return
        status = await _post_slate(channel)
        log.info("daily slate: %s", status)
    except Exception:  # noqa: BLE001
        log.exception("daily slate post failed")


@daily_slate.before_loop
async def _before_daily_slate():
    await client.wait_until_ready()


# ════════════════════════════════════════════════════════════════════════════
# Shared small helpers for the new commands
# ════════════════════════════════════════════════════════════════════════════
FOOTER_GENERIC = "Baseline — Data Driven. Optimizer Backed."


def _fmt_et(ts) -> str:
    if not ts:
        return "TBD"
    try:
        dt = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).astimezone(POD_TZINFO)
        return dt.strftime("%I:%M %p ET").lstrip("0")
    except Exception:  # noqa: BLE001
        return "TBD"


def _add_lines_field(e: discord.Embed, name: str, lines: list, limit: int = 1024):
    """Add ``lines`` as one or more fields, each under Discord's 1024 char cap."""
    buf, first = "", True
    for ln in lines:
        add = ("\n" if buf else "") + ln
        if len(buf) + len(add) > limit:
            e.add_field(name=name if first else f"{name} (cont.)", value=buf or "—", inline=False)
            first, buf = False, ln
        else:
            buf += add
    if buf:
        e.add_field(name=name if first else f"{name} (cont.)", value=buf, inline=False)


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


# ════════════════════════════════════════════════════════════════════════════
# Feature 1 — /results (public) and /results update (admin)
# ════════════════════════════════════════════════════════════════════════════
def _et_date_of(generated_at: str):
    """The ET calendar date ('YYYY-MM-DD') a pick was generated on."""
    if not generated_at:
        return None
    try:
        dt = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(POD_TZINFO).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def daily_recap_embed(rec: dict, target_date: str = None) -> discord.Embed:
    """Date-based daily recap (the auto-posted format). Header 'M/D Recap', that
    date's Pick-of-the-Day picks with W/L/PUSH indicators, a Today record + hit
    rate line, and the cumulative Overall line. ``target_date`` is an ET
    'YYYY-MM-DD'; defaults to today in ET. Emoji/colour/PUSH handling unchanged."""
    if target_date is None:
        target_date = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
    try:
        _d = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        header = f"{_d.month}/{_d.day} Recap"
    except Exception:  # noqa: BLE001
        header = "Recap"

    # Date-scoped by RESOLUTION date — the recap shows the picks whose results
    # came in on this date (only graded picks have a resolved_at), regardless of
    # when they were generated.
    picks = rec.get("picks", []) if rec else []
    graded = [p for p in picks
              if p.get("result") in ("W", "L", "PUSH", "VOID")
              and _et_date_of(p.get("resolved_at")) == target_date]
    today = graded

    # CASHED = W + PUSH — a push didn't miss, so it counts as cashed. The
    # denominator is every play that actually PLAYED (W + L + PUSH); VOID/DNP
    # (cancelled) never played, so it's excluded from both sides.
    t_w = sum(1 for p in graded if p["result"] == "W")
    t_l = sum(1 for p in graded if p["result"] == "L")
    t_p = sum(1 for p in graded if p["result"] == "PUSH")
    t_cash = t_w + t_p
    t_total = t_w + t_l + t_p
    t_rate = round(t_cash / t_total * 100) if t_total else 0

    o_w, o_l = rec.get("wins", 0), rec.get("losses", 0)
    o_p = rec.get("pushes", 0) or 0
    o_cash = o_w + o_p
    o_total = o_w + o_l + o_p
    o_rate = round(o_cash / o_total * 100) if o_total else 0

    color = COLOR_UNDER if (t_total and t_rate < 50) else COLOR_OVER
    e = discord.Embed(title=f"📊 {header}", color=color)

    # One indicator per concept: ✅ win · ❌ loss · ⚪ push · 🚫 void (DNP).
    icon = {"W": "✅", "L": "❌", "PUSH": "⚪", "VOID": "🚫"}
    if graded:
        rows = []
        for p in graded:
            # Result indicator LEADS, then player, prop, line, lean — one compact
            # line. Empty parts are dropped rather than rendering a gap.
            bits = [p["player"]]
            if p.get("prop_type"):
                bits.append(str(p["prop_type"]))
            if isinstance(p.get("line"), (int, float)):
                bits.append(f"{p['line']:g}")
            if p.get("lean"):
                bits.append(str(p["lean"]).upper())
            row = f"{icon.get(p['result'], '⚪')} **{bits[0]}** {' '.join(bits[1:])}".rstrip()
            if p["result"] == "VOID":
                row += " — **DNP** (cancelled)"
            rows.append(row)
        _add_lines_field(e, "Today's Picks", rows)
    else:
        e.description = "_No picks resolved today._"

    # Summary block, separated from the pick list. Discord already spaces fields
    # apart, so the separation is a leading newline inside this field rather than
    # an extra empty spacer field (no field is left blank).
    today_line = f"**Today:** {t_cash}/{t_total} cashed ({t_rate}%)"
    if t_p:
        today_line += f"  ·  incl. {t_p} push{'es' if t_p != 1 else ''}"
    e.add_field(name="📋 Record",
                value=f"{today_line}\n**Overall:** {o_cash}/{o_total} cashed ({o_rate}%)",
                inline=False)
    return _stamped_footer(e, FOOTER_GENERIC)


def results_embed(rec: dict) -> discord.Embed:
    if not rec or not rec.get("total"):
        e = discord.Embed(title="📊 Baseline Track Record", color=COLOR_NEUTRAL,
                           description="No graded picks yet — check back after today's plays resolve.")
        e.set_footer(text=FOOTER_GENERIC)
        return e
    wins, losses = rec.get("wins", 0), rec.get("losses", 0)
    win_rate = rec.get("win_rate", 0.0)
    color = COLOR_OVER if win_rate >= 50 else COLOR_UNDER
    # ON FIRE — only signal when the 5+ most-recent graded picks haven't been
    # missed (no loss). Otherwise show no streak line at all. (Replaces the old
    # streak calc.) Pending picks are transparent; a loss breaks the run.
    streak = 0
    for p in rec.get("picks", []):          # newest first
        r = p.get("result")
        if r == "L":
            break
        if r in ("W", "PUSH"):
            streak += 1
    on_fire = streak >= 5
    e = discord.Embed(title="📊 Baseline Track Record", color=color)
    e.description = (
        f"**Record:** {wins}-{losses}   ·   **Win rate:** {win_rate:g}%\n"
        + (f"🔥 **ON FIRE — {streak} in a row!**\n" if on_fire else "")
        + f"Total graded: {wins + losses}  ·  Pending: {rec.get('pending', 0)}"
        + (f"  ·  Pushes: {rec.get('pushes', 0)}" if rec.get("pushes") else "")
        + (f"  ·  Needs review: {rec.get('needs_review', 0)}" if rec.get("needs_review") else "")
    )
    last = [p for p in rec.get("picks", []) if p.get("result") in ("W", "L", "PUSH", "VOID")][:10]
    if last:
        # 🟢 W · 🔴 L · ⚪ PUSH · 🚫 VOID (cancelled / DNP).
        icon = {"W": "🟢", "L": "🔴", "PUSH": "⚪", "VOID": "🚫"}
        rows = [f"{icon.get(p['result'],'⚪')} **{p['player']}** {p.get('lean','')} "
                f"{p.get('line','')}{'' if p.get('line') is None else ''} {p['prop_type']}"
                + (" — DNP" if p['result'] == "VOID" else "")
                for p in last]
        _add_lines_field(e, "Last 10 (Pick of the Day)", rows)

    # 3x slip — tracked independently: the paired slip record (both legs must
    # hit) plus the individual-leg record for transparency.
    slips = rec.get("threex_slips") or {}
    legs = rec.get("threex_legs") or {}
    if (slips.get("slips") or 0) or (legs.get("total") or 0):
        sw, sl = slips.get("wins", 0), slips.get("losses", 0)
        lw, ll = legs.get("wins", 0), legs.get("losses", 0)
        val = (f"**Slip record:** {sw}-{sl}   ·   **Win rate:** {slips.get('win_rate', 0):g}%\n"
               f"_(both legs must hit — a slip wins only when neither leg misses)_\n"
               f"**Individual legs:** {lw}-{ll}"
               + (f"  ·  Pending: {legs.get('pending', 0)}" if legs.get("pending") else "")
               + (f"  ·  Pushes: {legs.get('pushes', 0)}" if legs.get("pushes") else ""))
        e.add_field(name="🎟️ Baseline 3x", value=val, inline=False)
    e.set_footer(text=FOOTER_GENERIC)
    return e


results_group = app_commands.Group(name="results",
                                   description="Baseline's automated public track record")


# The win/loss record is bot-broadcast only (daily auto-post) — no user 'show'
# command. The admin 'update' command below stays for correcting the record.
_RESULT_CHOICES = [
    app_commands.Choice(name="Win", value="W"),
    app_commands.Choice(name="Loss", value="L"),
    app_commands.Choice(name="Push", value="PUSH"),
    app_commands.Choice(name="Void / DNP (cancelled)", value="VOID"),
    app_commands.Choice(name="Pending", value="PENDING"),
    app_commands.Choice(name="Needs Review", value="NEEDS REVIEW"),
]


@results_group.command(name="update", description="Admin: manually set a pick's result")
@app_commands.describe(pick_id="The pick id (from the record)", result="Correct result")
@app_commands.choices(result=_RESULT_CHOICES)
async def results_update_cmd(interaction: discord.Interaction, pick_id: int,
                             result: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not _is_admin(interaction):
        await interaction.followup.send(embed=error_embed("Admins only."), ephemeral=True)
        return
    try:
        ok = await asyncio.to_thread(results_tracker.update_result, pick_id, result.value)
        msg = (f"✅ Pick #{pick_id} set to **{result.value}**." if ok
               else f"⚠️ Could not update pick #{pick_id}.")
        await interaction.followup.send(embed=discord.Embed(description=msg, color=COLOR_NEUTRAL),
                                        ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("/results update failed")
        await interaction.followup.send(embed=error_embed("Update failed."), ephemeral=True)


client.tree.add_command(results_group)


# ════════════════════════════════════════════════════════════════════════════
# Feature 4 — /slate (public)
# ════════════════════════════════════════════════════════════════════════════
def _slate_title(data: dict) -> str:
    ds = data.get("date") if isinstance(data, dict) else None
    if ds:
        try:
            d = datetime.datetime.strptime(ds, "%Y-%m-%d")
            return f"🎾 Slate — {d.strftime('%a %b %d')}"
        except Exception:  # noqa: BLE001
            pass
    return "🎾 Today's Slate"


def slate_embed(data: dict) -> discord.Embed:
    e = discord.Embed(title=_slate_title(data), color=COLOR_NEUTRAL)
    if not data or not data.get("available"):
        e.description = "Slate data unavailable — try again shortly."
        e.set_footer(text=FOOTER_GENERIC)
        return e
    if not data.get("count"):
        e.description = "No live or upcoming matches found."
        e.set_footer(text=FOOTER_GENERIC)
        return e
    legend = "🟢 Upcoming · 🔴 Live · ❌ Cancelled · ⏸️ Postponed"
    # When today's card is already done, we roll to the next day — say so.
    if data.get("is_today") is False:
        e.description = f"_Today's matches are finished — here's the next slate._\n{legend}"
    else:
        e.description = legend

    # A full day can be 130+ matches — well over Discord's 6000-char / 25-field
    # embed limit. Give each tour an equal slice of a conservative budget so both
    # ATP and WTA show, with compact one-line entries and a remainder note.
    PER_TOUR_CHARS, FIELD_BUDGET = 2500, 22

    def _flush(name, buf):
        e.add_field(name=name, value=buf, inline=False)

    for tour, label in (("atp", "🟦 ATP"), ("wta", "🟪 WTA")):
        rows = data.get(tour, [])
        if not rows or len(e.fields) >= FIELD_BUDGET:
            continue
        name = f"{label} ({len(rows)})"
        buf, first, shown, used = "", True, 0, 0
        for m in rows:
            st = (m.get("status") or "").lower()
            badge = ("❌" if "cancel" in st else
                     "⏸️" if "postpone" in st else
                     "🔴" if st in ("inprogress", "interrupted", "suspended") else
                     "🟢")     # notstarted / upcoming
            line = (f"{badge} `{_fmt_et(m.get('start_timestamp'))}` **{m['p1']}** vs **{m['p2']}** · "
                    f"{m.get('surface','')} {m['cpi']:g} · {(m.get('tournament','') or '')[:22]}")
            add = ("\n" if buf else "") + line
            if used + len(add) > PER_TOUR_CHARS or len(e.fields) >= FIELD_BUDGET:
                break
            if len(buf) + len(add) > 1024:
                _flush(name if first else f"{label} (cont.)", buf)
                first, buf = False, line
            else:
                buf += add
            used += len(add)
            shown += 1
        if buf and len(e.fields) < FIELD_BUDGET:
            _flush(name if first else f"{label} (cont.)", buf)
        if shown < len(rows) and len(e.fields) < FIELD_BUDGET:
            _flush("​", f"…and {len(rows) - shown} more — use /prop for any matchup")
    e.set_footer(text=FOOTER_GENERIC + " • Scheduled, EST")
    return e


# The slate is bot-broadcast only (the automatic daily post) — no user command.


# ════════════════════════════════════════════════════════════════════════════
# Feature 5 — /form (public)
# ════════════════════════════════════════════════════════════════════════════
def form_embed(name: str, data: dict) -> discord.Embed:
    if not data or not data.get("last10"):
        e = discord.Embed(title=f"📈 Form — {name}", color=COLOR_NEUTRAL,
                           description="Not enough recent match data.")
        e.set_footer(text=FOOTER_GENERIC)
        return e
    st_type, st_len = data.get("streak_type"), data.get("streak_len", 0)
    alert = data.get("form_alert")
    color = COLOR_OVER if st_type == "W" else COLOR_UNDER if st_type == "L" else COLOR_NEUTRAL
    e = discord.Embed(title=f"📈 Form — {name}", color=color)
    if alert:
        word = "WIN" if st_type == "W" else "LOSS"
        e.description = f"## {'🔥' if st_type=='W' else '🧊'} FORM ALERT — {st_len}-match {word} streak"
    else:
        e.description = (f"Current streak: **{st_len} {('win' if st_type=='W' else 'loss')}"
                         f"{'s' if st_len != 1 else ''}**" if st_type else "Current streak: —")

    icon = {True: "🟢", False: "🔴"}
    rows = [f"{icon[bool(m['won'])]} vs {m['opponent']} ({m['surface'] or '—'})"
            for m in data.get("last10", [])]
    _add_lines_field(e, "Last 10", rows)

    trend = data.get("trend", {})
    arrow = {"up": "🔼", "down": "🔽", "flat": "➡️"}
    tl = []
    for key, lbl in (("aces", "Aces"), ("break_points_won", "Break Points Won"),
                     ("double_faults", "Double Faults")):
        t = trend.get(key, {})
        r, p = t.get("recent5"), t.get("prev5")
        if r is None and p is None:
            continue
        tl.append(f"{arrow.get(t.get('direction','flat'),'➡️')} **{lbl}** "
                  f"{r if r is not None else '—'} (last 5) vs {p if p is not None else '—'} (prev 5)")
    if tl:
        e.add_field(name="Trend (last 5 vs previous 5)", value="\n".join(tl), inline=False)

    fr = data.get("freshness") or {}
    if fr.get("message"):
        e.add_field(name=("🔴" if fr.get("level") == "red" else "🟡") + " Data Freshness",
                    value=fr["message"], inline=False)
    e.set_footer(text=FOOTER_GENERIC + " • Last 15 matches")
    return e


@client.tree.command(name="form", description="A player's current form, streak and stat trend")
@app_commands.describe(player="Player name")
@app_commands.autocomplete(player=player_autocomplete)
async def form(interaction: discord.Interaction, player: str):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    try:
        pid, tour, name = await resolve_player(player)
        if not pid:
            await _send_error(interaction, "Couldn't find that player.")
            return
        data = await backend_get("/api/player/form", {"player_id": pid, "tour": tour}, PROP_TIMEOUT)
        await interaction.followup.send(embed=form_embed(name, data), ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("/form failed")
        await _send_error(interaction, "Unable to load form right now.")
    finally:
        _leave_queue()


# ════════════════════════════════════════════════════════════════════════════
# Feature 6 — /history (members only)
# ════════════════════════════════════════════════════════════════════════════
def history_embed(name: str, prop: str, surface: str, line: float, data: dict) -> discord.Embed:
    if not data or not data.get("player_matches"):
        e = discord.Embed(title=f"📚 {prop} History — {name}", color=COLOR_NEUTRAL,
                           description=f"No {surface} matches with {prop} data found.")
        e.set_footer(text=FOOTER_GENERIC)
        return e
    over, under = data.get("over", 0), data.get("under", 0)
    n = data.get("player_matches", 0)
    hit = data.get("hit_rate")
    e = discord.Embed(
        title=f"📚 {prop} History — {name}",
        color=COLOR_OVER if (hit or 0) >= 50 else COLOR_UNDER,
        description=(f"**{name}** has gone **OVER {line:g}** {prop} on **{surface or 'all surfaces'}** "
                     f"in **{over} of their last {n}** — **{hit:g}% hit rate**."),
    )
    e.add_field(name="Split", value=f"🔼 Over: **{over}**  ·  🔽 Under: **{under}**  ·  "
                                    f"Avg: **{data.get('average')}**", inline=False)
    last = data.get("last10", [])
    if last:
        rows = [f"{'🔼' if m['over'] else '🔽'} `{m.get('date','')}` vs {m.get('opponent','')}: "
                f"**{m.get('value')}**" for m in last]
        _add_lines_field(e, f"Last {len(last)} matches", rows)
    e.set_footer(text=FOOTER_GENERIC + " • Surface match log")
    return e


@client.tree.command(name="history", description="How often a player has gone over/under a prop line")
@app_commands.describe(player="Player", prop="Prop type", surface="Surface", line="The line to test")
@app_commands.choices(prop=PROP_CHOICES, surface=SURFACE_CHOICES)
@app_commands.autocomplete(player=player_autocomplete)
async def history(interaction: discord.Interaction, player: str,
                  prop: app_commands.Choice[str], surface: app_commands.Choice[str], line: float):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    try:
        if not _member_gate(interaction):
            await _send_error(interaction, f"This command is for **{MEMBER_ROLE_NAME}** members.")
            return
        pid, tour, name = await resolve_player(player)
        if not pid:
            await _send_error(interaction, "Couldn't find that player.")
            return
        data = await backend_get("/api/history", {
            "player_id": pid, "tour": tour, "prop": prop.value,
            "surface": surface.value, "line": line}, PROP_TIMEOUT)
        await interaction.followup.send(
            embed=history_embed(name, prop.value, surface.value, line, data), ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("/history failed")
        await _send_error(interaction, "Unable to load history right now.")
    finally:
        _leave_queue()


# ════════════════════════════════════════════════════════════════════════════
# Feature 7 — /courtreport (public) + Monday 9am auto-post
# ════════════════════════════════════════════════════════════════════════════
def courtreport_embed(data: dict) -> discord.Embed:
    name = data.get("tournament", "Tournament")
    if not data or not data.get("available"):
        e = discord.Embed(title=f"🏟️ Court Report — {name}", color=COLOR_NEUTRAL,
                           description="Court data unavailable for that tournament.")
        e.set_footer(text=FOOTER_GENERIC)
        return e
    cpi, tier = data.get("cpi"), data.get("speed_tier", "")
    e = discord.Embed(title=f"🏟️ Court Report — {name}", color=COLOR_NEUTRAL)
    cond = [f"**Surface:** {data.get('surface') or '—'}",
            f"**ST Pace Index:** {cpi:g} ({tier})" if isinstance(cpi, (int, float)) else "**ST Pace Index:** —"]
    yoy = data.get("yoy")
    if yoy:
        cond.append(f"**YoY change:** {yoy['previous']:g} → {yoy['current']:g} "
                    f"({'+' if yoy['delta'] >= 0 else ''}{yoy['delta']:g}, {yoy['direction']})")
    e.add_field(name="🎾 Surface Conditions", value="\n".join(cond), inline=False)
    outlook = [data.get("ace_note", ""), data.get("bp_note", "")]
    rel = data.get("reliable_props") or []
    if rel:
        outlook.append("**Most reliable here:** " + ", ".join(rel))
    e.add_field(name="📊 Prop Outlook", value="\n".join(x for x in outlook if x), inline=False)
    watch = data.get("players_to_watch") or []
    if watch:
        e.add_field(name="👀 Players to Watch", value="\n".join(f"• {w}" for w in watch), inline=False)
    e.set_footer(text=FOOTER_GENERIC)
    return e


@client.tree.command(name="courtreport", description="Pre-tournament conditions & prop outlook for an event")
@app_commands.describe(tournament="Tournament / court", tour="Tour (for WTA/ATP variants)")
@app_commands.autocomplete(tournament=court_autocomplete)
@app_commands.choices(tour=[app_commands.Choice(name="ATP", value="ATP"),
                            app_commands.Choice(name="WTA", value="WTA")])
async def courtreport(interaction: discord.Interaction, tournament: str,
                      tour: app_commands.Choice[str] = None):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    try:
        tval = tour.value if tour else "ATP"
        # The bot knows each autocompleted court's surface — pass it so the
        # report always shows a surface even for courts not in the backend map.
        shint = surface_for_court(tournament) or ""
        data = await backend_get("/api/courtreport",
                                 {"tournament": tournament, "tour": tval, "surface": shint}, 75)
        await interaction.followup.send(embed=courtreport_embed(data), ephemeral=True)
    except Exception:  # noqa: BLE001
        log.exception("/courtreport failed")
        await interaction.followup.send(embed=courtreport_embed({"tournament": tournament}), ephemeral=True)
    finally:
        _leave_queue()


# ── Feature 1 — 11pm EST auto-resolution job ─────────────────────────────────
RESOLVE_EVERY_HOURS = int(os.getenv("RESOLVE_EVERY_HOURS", "2") or "2")
RESOLVE_GIVEUP_HOURS = 36     # after this long unresolved → NEEDS REVIEW


def _pick_age_hours(pk: dict) -> float:
    """Hours since the pick was logged (generated_at). Large default if unknown."""
    raw = pk.get("generated_at")
    if not raw:
        return 0.0
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return 0.0


async def _resolve_all_pending() -> int:
    """Grade every pending pick against completed-match stats. Returns the number
    newly graded. A pick whose match hasn't finished stays PENDING; only after
    RESOLVE_GIVEUP_HOURS is it flagged NEEDS REVIEW. Shared by the periodic
    resolver loop and the pre-recap resolve so a recap never posts stale results."""
    pending = await asyncio.to_thread(results_tracker.get_pending)
    if not pending:
        log.info("POD resolve: nothing pending")
        return 0
    graded = 0
    for pk in pending:
        res = await asyncio.to_thread(results_tracker.resolve_pick, pk)
        outcome = (res.get("result") or "").upper()
        if outcome in ("W", "L", "PUSH", "VOID"):   # VOID = cancelled / DNP
            ok = await asyncio.to_thread(results_tracker.update_result, pk["id"], outcome)
            graded += 1 if ok else 0
            log.info("POD resolve: pick #%s %s %s -> %s (val=%s)",
                     pk.get("id"), pk.get("player"), pk.get("prop_type"),
                     outcome, res.get("value"))
        elif _pick_age_hours(pk) > RESOLVE_GIVEUP_HOURS:
            # Match still unresolved after a day and a half — flag for review.
            await asyncio.to_thread(results_tracker.update_result, pk["id"], "NEEDS REVIEW")
            log.info("POD resolve: pick #%s %s -> NEEDS REVIEW (stale, %s)",
                     pk.get("id"), pk.get("player"), res.get("reason"))
        else:
            # Match not finished yet — leave PENDING, retry next cycle.
            log.info("POD resolve: pick #%s %s still pending (%s)",
                     pk.get("id"), pk.get("player"), res.get("reason"))
    log.info("POD resolve: graded %d of %d pending", graded, len(pending))
    return graded


@tasks.loop(hours=RESOLVE_EVERY_HOURS)
async def daily_resolve_results():
    """Periodic grader (runs every couple hours + on startup)."""
    try:
        await _resolve_all_pending()
    except Exception:  # noqa: BLE001
        log.exception("daily_resolve_results failed")


@daily_resolve_results.before_loop
async def _before_resolve():
    await client.wait_until_ready()


# ── Feature 1 — daily win/loss record auto-post (replaces the /results command) ──
@tasks.loop(time=[
    datetime.time(hour=ONEOFF_RECAP_HM[0], minute=ONEOFF_RECAP_HM[1], tzinfo=POD_TZINFO),
    datetime.time(hour=RESULTS_POST_HOUR, minute=RESULTS_POST_MINUTE, tzinfo=POD_TZINFO),
])
async def daily_results_post():
    """The daily recap. Registered at BOTH the one-off slot (5:00 PM on
    ONEOFF_SCHED_DATE) and the recurring slot (7:45 PM); _slot_is_live picks which
    firing runs so the day never posts the recap twice."""
    if not _slot_is_live(ONEOFF_RECAP_HM):
        return
    chan_id = RESULTS_CHANNEL_ID or POD_CHANNEL_ID
    if not chan_id:
        return
    if RESULTS_SKIP_DATE and datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d") == RESULTS_SKIP_DATE:
        log.info("daily results: skipping %s — recap already posted earlier today", RESULTS_SKIP_DATE)
        return
    try:
        channel = client.get_channel(chan_id)
        if channel is None:
            log.warning("daily results: channel %s not found", chan_id)
            return
        today = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")

        # 1) RECAP — resolve today's pending picks first (so the recap reflects
        #    final results), then post the previous day's date-based recap.
        try:
            await _resolve_all_pending()
        except Exception:  # noqa: BLE001
            log.exception("pre-recap resolve failed (posting with current data)")
        rec = await asyncio.to_thread(results_tracker.get_record)
        if rec and rec.get("total"):
            await channel.send(content="@everyone", embed=daily_recap_embed(rec),
                               allowed_mentions=EVERYONE_MENTION)
            log.info("daily results: posted recap (overall %s-%s)",
                     rec.get("wins"), rec.get("losses"))
        else:
            log.info("daily results: no graded record yet — skipping recap")

        # The picks are NOT posted here — the POTD trigger is its own job
        # (daily_picks_generate) so the recap can land earlier, independently.
    except Exception:  # noqa: BLE001
        log.exception("daily results post failed")


@daily_results_post.before_loop
async def _before_results_post():
    await client.wait_until_ready()


# ── PART 5 — weekly confidence-calibration log (Railway logs only) ───────────
@tasks.loop(time=datetime.time(hour=9, minute=30, tzinfo=POD_TZINFO))
async def weekly_calibration_log():
    """Every Monday 9:30 AM ET, log the rolling calibration table — confidence
    bands vs actual hit rate over the last 30 days — so drift between stated
    confidence and real performance is visible in Railway logs without a manual
    query. Logs only; posts nothing to Discord."""
    if datetime.datetime.now(POD_TZINFO).weekday() != 0:   # Mondays only
        return
    try:
        rec = await asyncio.to_thread(results_tracker.get_record)
        picks = (rec or {}).get("picks", [])
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)

        def _recent(p):
            ra = p.get("resolved_at")
            if not ra:
                return False
            try:
                dt = datetime.datetime.fromisoformat(ra.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt >= cutoff
            except Exception:  # noqa: BLE001
                return False

        # POST-GUARD ONLY. Picks flagged pre_guard=1 had their confidence computed
        # before the degraded-fetch cache guard shipped (2026-07-14), so they may
        # have been scored against a poisoned Sofascore snapshot (events present,
        # per-match statistics missing → a player's usable match count collapsing
        # to ~0). Those numbers say nothing about whether the model is calibrated,
        # so every figure below — band hit rates, monotonicity, per-prop table —
        # computes exclusively from post-guard picks. The pick records themselves
        # are untouched and still count toward the public W/L record.
        _all_dec = [p for p in picks if p.get("result") in ("W", "L")
                    and isinstance(p.get("confidence"), (int, float)) and _recent(p)]
        dec = [p for p in _all_dec if not int(p.get("pre_guard") or 0)]
        _excluded = len(_all_dec) - len(dec)
        log.info("CALIBRATION | rolling 30d | %d decided post-guard picks "
                 "(%d pre-guard excluded — confidence may be cache-contaminated)",
                 len(dec), _excluded)
        if len(dec) < CALIBRATION_MIN_SAMPLE:
            log.info("CALIBRATION | SAMPLE TOO SMALL — %d/%d post-guard picks. "
                     "Bands/monotonicity suppressed until the clean sample rebuilds; "
                     "no conclusions should be drawn from the pre-guard history.",
                     len(dec), CALIBRATION_MIN_SAMPLE)
            return

        # Confidence-band hit rates.
        band_rates = []   # (label, hit%, n) for populated bands, low→high
        for lo, hi in ((70, 75), (75, 80), (80, 85), (85, 90), (90, 101)):
            b = [p for p in dec if lo <= p["confidence"] < hi]
            w = sum(1 for p in b if p["result"] == "W")
            hr = (w / len(b) * 100) if b else 0.0
            log.info("CALIBRATION |   %2d-%-3d | n=%2d | %d-%d | hit=%.0f%%",
                     lo, (hi if hi < 101 else 100), len(b), w, len(b) - w, hr)
            if b:
                band_rates.append(("%d-%d" % (lo, hi if hi < 101 else 100), hr, len(b)))

        # Monotonicity — each higher populated band should hit >= the band below.
        inversions = [(band_rates[i - 1], band_rates[i])
                      for i in range(1, len(band_rates))
                      if band_rates[i][1] < band_rates[i - 1][1]]
        if not band_rates:
            log.info("CALIBRATION | monotonicity: n/a (no populated bands)")
        elif not inversions:
            log.info("CALIBRATION | monotonicity: OK — bands are non-decreasing")
        else:
            for lower, higher in inversions:
                log.warning("CALIBRATION | ⚠ INVERSION | %s hits %.0f%% (n=%d) but higher band "
                            "%s hits only %.0f%% (n=%d)",
                            lower[0], lower[1], lower[2], higher[0], higher[1], higher[2])

        # Per-prop-type hit rate (spot a new failure pattern within a week).
        by_type = {}
        for p in dec:
            by_type.setdefault(p.get("prop_type") or "?", []).append(p)
        log.info("CALIBRATION | per prop type (30d):")
        for pt, ps in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            w = sum(1 for p in ps if p["result"] == "W")
            log.info("CALIBRATION |   %-22s | n=%2d | %d-%d | hit=%.0f%%",
                     pt[:22], len(ps), w, len(ps) - w, (w / len(ps) * 100) if ps else 0.0)
    except Exception:  # noqa: BLE001
        log.exception("weekly calibration log failed")


@weekly_calibration_log.before_loop
async def _before_calibration_log():
    await client.wait_until_ready()


# ── Feature 7 — Monday 9am EST court-report auto-post ────────────────────────
COURTREPORT_CHANNEL_ID = int(os.getenv("COURTREPORT_CHANNEL_ID", str(POD_CHANNEL_ID or 0)) or "0")


@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=POD_TZINFO))
async def weekly_court_report():
    # tasks.loop with a single time fires daily; gate to Mondays only.
    if datetime.datetime.now(POD_TZINFO).weekday() != 0:
        return
    chan_id = COURTREPORT_CHANNEL_ID or POD_CHANNEL_ID
    if not chan_id:
        return
    try:
        channel = client.get_channel(chan_id)
        if channel is None:
            log.warning("weekly court report: channel %s not found", chan_id)
            return
        slate = await backend_get("/api/slate/today", {}, GENERIC_TIMEOUT)
        seen, posted = set(), 0
        for m in (slate.get("atp", []) + slate.get("wta", [])):
            t = (m.get("tournament") or "").strip()
            tour = m.get("tour", "ATP")
            key = t.lower()
            if not t or key in seen:
                continue
            seen.add(key)
            data = await backend_get("/api/courtreport", {"tournament": t, "tour": tour}, GENERIC_TIMEOUT)
            if data and data.get("available"):
                await channel.send(embed=courtreport_embed(data))
                posted += 1
            if posted >= 4:                 # cap weekly volume
                break
        log.info("weekly court report: posted %d reports", posted)
    except Exception:  # noqa: BLE001
        log.exception("weekly_court_report failed")


@weekly_court_report.before_loop
async def _before_weekly_report():
    await client.wait_until_ready()


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


_guild_synced = False


@client.event
async def on_ready():
    global _guild_synced
    # Register commands to every guild the bot is in — guild commands propagate
    # INSTANTLY (global commands lag up to an hour and cause "This command is
    # outdated"). A guild command overrides the same-named global command, so no
    # duplicates appear. Done once per process.
    if not _guild_synced:
        _guild_synced = True
        try:
            # Push the current command set to each guild (instant availability).
            for g in client.guilds:
                client.tree.copy_global_to(guild=g)
                await client.tree.sync(guild=g)
            # CLEANUP: a stale GLOBAL command set (from an earlier global sync)
            # shows up alongside the guild copies as DUPLICATES. Clear the global
            # scope and push an empty global set so only the guild copies remain.
            # The decorators re-register all commands globally on the next restart,
            # so this is safe to run every startup.
            client.tree.clear_commands(guild=None)
            await client.tree.sync()
            log.info("Commands guild-synced to %d guild(s); global scope cleared (no dupes).",
                     len(client.guilds))
        except Exception:
            log.exception("guild command sync failed")
    # Daily picks are pre-generated at 5:50 PM ET; the recap job posts the ranked
    # list + 3x right after the 6 PM recap.
    if POD_CHANNEL_ID and not daily_picks_generate.is_running():
        try:
            daily_picks_generate.start()
            log.info("POTD trigger scheduled at %02d:%02d %s (one-off %02d:%02d on %s) "
                     "-> channel %s",
                     PICKS_GEN_HOUR, PICKS_GEN_MINUTE, POD_TZINFO,
                     ONEOFF_POTD_HM[0], ONEOFF_POTD_HM[1], ONEOFF_SCHED_DATE,
                     POD_CHANNEL_ID)
        except Exception:
            log.exception("failed to start daily picks generation loop")
    # Cache pre-warm — 30 min before generation. Started SEPARATELY from the POTD
    # trigger so a pre-warm failure can never stop the picks from being posted.
    if not daily_cache_prewarm.is_running():
        try:
            daily_cache_prewarm.start()
            log.info("Cache pre-warm scheduled at %02d:%02d %s (one-off %02d:%02d on %s)",
                     PREWARM_HOUR, PREWARM_MINUTE, POD_TZINFO,
                     ONEOFF_PREWARM_HM[0], ONEOFF_PREWARM_HM[1], ONEOFF_SCHED_DATE)
        except Exception:
            log.exception("failed to start cache pre-warm loop")
    # One-off extra run (date-gated; no-op on other days).
    if POD_CHANNEL_ID and POD_EXTRA_RUN_DATE and not extra_pod_run.is_running():
        try:
            extra_pod_run.start()
            log.info("One-off extra POTD run scheduled %02d:%02d %s on %s",
                     POD_EXTRA_RUN_HOUR, POD_EXTRA_RUN_MINUTE, POD_TZINFO, POD_EXTRA_RUN_DATE)
        except Exception:
            log.exception("failed to start extra POTD run loop")

    # Feature 1 — results auto-resolution (runs on startup + every few hours).
    if not daily_resolve_results.is_running():
        try:
            daily_resolve_results.start()
            log.info("Results auto-resolution running every %dh", RESOLVE_EVERY_HOURS)
        except Exception:
            log.exception("failed to start results resolution loop")

    # Feature 1 — daily win/loss record auto-post (the /results command is gone).
    if (RESULTS_CHANNEL_ID or POD_CHANNEL_ID) and not daily_results_post.is_running():
        try:
            daily_results_post.start()
            log.info("Daily results record auto-post scheduled at %02d:%02d %s -> channel %s",
                     RESULTS_POST_HOUR, RESULTS_POST_MINUTE, POD_TZINFO,
                     RESULTS_CHANNEL_ID or POD_CHANNEL_ID)
        except Exception:
            log.exception("failed to start daily results post loop")

    # Feature 4 — daily Slate auto-post to the 📋・slate channel.
    if SLATE_CHANNEL_ID and not daily_slate.is_running():
        try:
            daily_slate.start()
            log.info("Daily slate auto-post scheduled at %02d:%02d %s -> channel %s",
                     SLATE_HOUR, SLATE_MINUTE, POD_TZINFO, SLATE_CHANNEL_ID)
        except Exception:
            log.exception("failed to start daily slate loop")

    # Feature 7 — Monday 9am court-report auto-post (only if a channel is set).
    if (COURTREPORT_CHANNEL_ID or POD_CHANNEL_ID) and not weekly_court_report.is_running():
        try:
            weekly_court_report.start()
            log.info("Weekly court report scheduled Mon 09:00 %s -> channel %s",
                     POD_TZINFO, COURTREPORT_CHANNEL_ID or POD_CHANNEL_ID)
        except Exception:
            log.exception("failed to start weekly court report loop")

    # PART 5 — weekly confidence-calibration log (Railway logs only).
    if not weekly_calibration_log.is_running():
        try:
            weekly_calibration_log.start()
            log.info("Weekly calibration log scheduled Mon 09:30 %s", POD_TZINFO)
        except Exception:
            log.exception("failed to start weekly calibration log loop")

    # TEMPORARY: one-shot post on startup to verify the autonomous path end-to-end
    # without waiting for midnight. Remove once confirmed (set POD_POST_ON_START off).
    global _pod_startup_done
    if POD_POST_ON_START and POD_CHANNEL_ID and not _pod_startup_done:
        _pod_startup_done = True
        ch = client.get_channel(POD_CHANNEL_ID)
        if ch is None:
            log.warning("POD startup test: channel %s not found / not visible to bot", POD_CHANNEL_ID)
        else:
            try:
                status = await _post_daily_picks(ch, track=False)
                log.info("POD startup test post -> %s", status)
            except discord.Forbidden:
                log.error("POD startup test: missing Send Messages / Embed Links in channel %s",
                          POD_CHANNEL_ID)
            except Exception:
                log.exception("POD startup test post failed")

    log.info("Logged in as %s (id=%s) — API=%s", client.user, client.user.id, API_BASE)


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
