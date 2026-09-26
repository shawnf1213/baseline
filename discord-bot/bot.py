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
import underdog         # Underdog Fantasy board client (own failure handling)
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
# RAISED 45 -> 150 (2026-09-26). A COLD projection genuinely takes ~105s:
# it walks both players' event history page by page and then fetches per-match
# statistics, which is hundreds of Sofascore requests. At 45s the bot gave up
# on work the backend was still doing correctly and told subscribers "Unable to
# reach Baseline servers" — which is false and reads like an outage. Measured
# 2026-09-26: cold 105s, and 200s when Sofascore was throttling.
#
# Safe to raise: the command defers the interaction first, so Discord allows up
# to 15 minutes, and MAX_CONCURRENT_BACKEND_CALLS still caps how many cold
# fetches are ever in flight. A REPEAT of the same prop is now ~0.2s off the
# backend projection cache, so this only ever applies to a genuinely cold one.
PROP_TIMEOUT = int(os.getenv("PROP_TIMEOUT", "150") or 150)
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

# ── Court lists, split by tour and surface ─────────────────────────────
# THE BACKEND OWNS THIS LIST. It used to be maintained here by hand and it went
# stale: the bot offered 43 tournaments while the web app offered 118, so a
# court a user could pick on the site was not offered in Discord at all and that
# projection silently fell back to generic surface pace. Same matchup, two
# numbers. It is fetched once at startup from /api/courts and cached; the
# snapshot below is only the fallback for a cold or unreachable backend.
#
# Autocomplete NEVER touches the network — Discord gives it about three seconds
# and player_autocomplete already spends that budget on a search. This is read
# from the cache, in memory, always.
_COURTS_FALLBACK = {
    "ATP": {
        "Hard": [
            "Australian Open", "US Open", "Indian Wells Masters",
            "Miami Open", "Cincinnati Masters", "Canadian Open",
            "Vienna Open", "Swiss Indoors Basel", "Rotterdam Open",
            "Qatar Open Doha", "Dubai Duty Free Championships",
            "ATP Finals Turin", "Paris Masters", "Dallas Open",
            "Delray Beach Open", "Adelaide International", "Auckland Open",
            "Acapulco Open", "Washington DC Open", "Los Cabos Open",
            "Winston-Salem Open", "Athens Open", "Tokyo Japan Open",
            "Shanghai Masters", "Stockholm Open", "Antwerp European Open",
            "Challenger Hard (Generic)",
        ],
        "Clay": [
            "Roland Garros", "Monte Carlo Masters", "Madrid Open",
            "Barcelona Open", "Italian Open Rome", "Hamburg Open",
            "Munich Open", "Geneva Open", "Lyon Open", "Buenos Aires Open",
            "Rio Open", "Santiago Open", "Houston Clay", "Estoril Open",
            "Marrakech Open", "Bastad Open", "Umag Open", "Gstaad Open",
            "Kitzbuhel Open", "Challenger Clay Europe (Generic)",
            "Challenger Clay South America (Generic)", "Bordeaux Challenger",
            "Braunschweig Challenger", "Valencia Challenger",
            "Monza Challenger", "Aix-en-Provence Challenger",
            "Sanremo Challenger", "Geneva Challenger",
        ],
        "Grass": [
            "Wimbledon", "Stuttgart", "Halle", "Queens Club Championships",
            "s-Hertogenbosch", "Mallorca", "Eastbourne International",
            "Birmingham", "Nottingham",
        ],
    },
    "WTA": {
        "Hard": [
            "Australian Open WTA", "US Open WTA", "Indian Wells WTA",
            "Miami Open WTA", "Cincinnati WTA", "Canadian Open WTA",
            "Wuhan Open", "China Open Beijing", "WTA Finals", "Dubai WTA",
            "Doha WTA", "Adelaide WTA", "Auckland WTA", "Acapulco WTA",
            "San Jose WTA", "Washington WTA", "Tokyo Pan Pacific",
            "Osaka WTA", "Linz WTA", "Guadalajara WTA", "Monterrey WTA",
            "Cleveland WTA", "Athens Open WTA", "WTA 125 Hard (Generic)",
            "Austin WTA 125", "Jiangxi Open WTA 125",
        ],
        "Clay": [
            "Roland Garros WTA", "Madrid Open WTA", "Italian Open WTA Rome",
            "Stuttgart WTA", "Hamburg WTA", "Prague Open WTA", "Rabat WTA",
            "Strasbourg WTA", "Warsaw WTA", "Budapest WTA", "Bastad WTA",
            "Palermo WTA", "San Jose Clay WTA", "Bogota WTA",
            "Trophee Clarins Paris WTA 125", "Catalonia Open WTA 125",
            "Huzhou Open WTA 125 Clay", "Emilia-Romagna WTA 125 Clay",
            "WTA 125 Clay (Generic)",
        ],
        "Grass": [
            "Wimbledon WTA", "Queens Club WTA", "Bad Homburg WTA",
            "s-Hertogenbosch WTA", "Mallorca WTA", "Eastbourne WTA",
            "Birmingham WTA", "Nottingham WTA", "Berlin WTA",
        ],
    },
}

TOURNAMENTS_BY_TOUR = _COURTS_FALLBACK
COURT_CPI: dict = {}


def _derive_by_surface(by_tour: dict) -> dict:
    """Surface → every tournament on it, both tours, first-seen order."""
    out = {}
    for surf in ("Clay", "Hard", "Grass"):
        seen = []
        for tour in by_tour.values():
            for name in tour.get(surf, []):
                if name not in seen:
                    seen.append(name)
        out[surf] = seen
    return out


COURTS_BY_SURFACE = _derive_by_surface(TOURNAMENTS_BY_TOUR)


def refresh_courts(payload: dict) -> int:
    """Adopt the backend's court list. Returns the number of tournaments."""
    global TOURNAMENTS_BY_TOUR, COURTS_BY_SURFACE, COURT_CPI
    tours = (payload or {}).get("tours") or {}
    by_tour, cpi = {}, {}
    for tour, surfaces in tours.items():
        if not isinstance(surfaces, dict):
            continue
        by_tour[tour] = {}
        for surf, entries in surfaces.items():
            names = []
            for e in entries or []:
                name = (e or {}).get("name") if isinstance(e, dict) else e
                if not name:
                    continue
                names.append(name)
                if isinstance(e, dict) and e.get("cpr") is not None:
                    cpi[name] = e["cpr"]
            by_tour[tour][surf] = names
    total = sum(len(v) for d in by_tour.values() for v in d.values())
    if total < len(_flatten(_COURTS_FALLBACK)) // 2:
        # A truncated answer is worse than the snapshot we shipped with.
        log.warning("courts refresh returned only %d entries — keeping snapshot", total)
        return 0
    TOURNAMENTS_BY_TOUR = by_tour
    COURTS_BY_SURFACE = _derive_by_surface(by_tour)
    COURT_CPI = cpi
    return total


def _flatten(by_tour: dict) -> list:
    return [n for d in by_tour.values() for v in d.values() for n in v]


# Names the bot used to offer, mapped to the canonical COURT_CPR key. Kept
# because the court field accepts free text — a user who types "Basel", or whose
# client replays a stale choice, still gets Swiss Indoors Basel's pace instead of
# an "isn't a recognised tournament" error. Not offered in autocomplete.
COURT_KEY_OVERRIDES = {
    "Monte Carlo": "Monte Carlo Masters",
    "Madrid": "Madrid Open",
    "Barcelona": "Barcelona Open",
    "Rome": "Italian Open Rome",
    "Hamburg": "Hamburg Open",
    "Geneva": "Geneva Open",
    "Munich": "Munich Open",
    "Lyon": "Lyon Open",
    "Gstaad": "Gstaad Open",
    "Bastad": "Bastad Open",
    "Umag": "Umag Open",
    "Kitzbuhel": "Kitzbuhel Open",
    "Estoril": "Estoril Open",
    "Indian Wells": "Indian Wells Masters",
    "Miami": "Miami Open",
    "Cincinnati": "Cincinnati Masters",
    "Los Cabos": "Los Cabos Open",
    "Winston-Salem": "Winston-Salem Open",
    "Paris Bercy": "Paris Masters",
    "Vienna": "Vienna Open",
    "Basel": "Swiss Indoors Basel",
    "Rotterdam": "Rotterdam Open",
    "Doha": "Qatar Open Doha",
    "Dubai": "Dubai Duty Free Championships",
    "Shanghai": "Shanghai Masters",
    "ATP Finals": "ATP Finals Turin",
    "Queens Club": "Queens Club Championships",
    "Eastbourne": "Eastbourne International",
    "Berlin": "Berlin WTA",
    "Bad Homburg": "Bad Homburg WTA",
}


def backend_court_key(display: str) -> str:
    """Canonical COURT_CPR key for a court the user picked or typed."""
    if display in _flatten(TOURNAMENTS_BY_TOUR):
        return display
    return COURT_KEY_OVERRIDES.get(display, display)


def courts_for(surface: str, tour: str = None) -> list:
    """The tournaments to offer. Tour-filtered when we know it, union when not."""
    if tour and tour in TOURNAMENTS_BY_TOUR:
        return TOURNAMENTS_BY_TOUR[tour].get(surface, [])
    return COURTS_BY_SURFACE.get(surface, [])


def surface_for_court(display: str):
    key = backend_court_key(display)
    for surf, courts in COURTS_BY_SURFACE.items():
        if key in courts:
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

    THAT SHORT DEADLINE ALSO PICKS THE BACKEND'S FETCH STRATEGY. Autocomplete
    (guard=False) asks for fast=1, which fails out of a blocked Sofascore fetch
    instead of rotating proxy ports for ~19 seconds — an answer that arrives
    after Discord's 3s limit is not an answer. Command submit (guard=True) has
    no such limit and keeps the thorough path, because there it is worth waiting
    to actually resolve the player.
    """
    _params = {"query": query, "tour": tour}
    if not guard:
        _params["fast"] = 1
    try:
        if guard:
            data = await backend_get("/api/search", _params, timeout)
        else:
            data = await asyncio.to_thread(_get, "/api/search", _params, timeout)
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

    def block(lines, hand, arch, serve_profile=None):
        rows = [f"{lbl}: **{val}**" for lbl, val in lines]
        if serve_profile:
            rows.append(f"🎾 {serve_profile}")
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
    elif prop_type == "Break Points Saved":
        # The projection is (games broken) x (save rate), so the stats shown are
        # the ones that actually drive it: the SERVER's holding and save rate on
        # one side, the RETURNER's break-point generation on the other.
        # Serve-point splits and win rate are deliberately absent — neither feeds
        # this number, and win rate especially invites reading a match-outcome
        # signal into a serve stat.
        p_lines = [
            ("Service Games Won", _pct(ps.get("service_games_won_pct"))),
            ("Hold vs This Opp", _pct(data.get("bps_effective_hold"))),
            ("BP Saved", _pct(data.get("bps_save_rate") or ps.get("bp_saved"))),
            ("BP Faced/Match", _num(ps.get("bp_faced_count"))),
            ("Proj. BP Faced", _num(data.get("bps_faced_proj"))),
        ]
        o_lines = [
            ("Return Games Won", _pct(os_.get("return_games_won_pct"))),
            ("BP Created/Match", _num(os_.get("return_bp_opportunities"))),
            ("BP Conversion", _pct(os_.get("bp_converted"))),
            ("Service Games Won", _pct(os_.get("service_games_won_pct"))),
        ]
    elif prop_type == "Player Total Games Won":
        # Core drivers: player hold rate vs opponent hold rate, plus player break rate.
        # The held-vs-broken decomposition was removed from the display 2026-07-26
        # (user): a games-won total isn't a holds/breaks scenario, so the breakdown was
        # dropped from BOTH the stat table and the summary line below.
        p_lines = [
            ("Hold Rate", _pct(data.get("player_hold_rate"))),
            ("Break Rate vs Opp", _pct(data.get("player_break_rate"))),
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
    # Suppressed for Break Points Saved: tiebreak rate says a set reached 6-6,
    # which is a match-length signal and tells you nothing about how often this
    # server faces or saves a break point. The block already carries hold rate,
    # which is the serve-dominance measure that actually drives this projection.
    if prop_type != "Break Points Saved":
        if data.get("player_tiebreak_rate") is not None:
            p_lines.append(("Tiebreak Rate", _tb_cell(data.get("player_tiebreak_rate"))))
        if data.get("opponent_tiebreak_rate") is not None:
            o_lines.append(("Tiebreak Rate", _tb_cell(data.get("opponent_tiebreak_rate"))))

    p_block = block(p_lines, _hand_label(data.get("player_handedness")), data.get("player_archetype"),
                    data.get("player_serve_profile"))
    o_block = block(o_lines, _hand_label(data.get("opponent_handedness")), data.get("opponent_archetype"),
                    data.get("opponent_serve_profile"))
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
    # Fantasy Score folds expected sets into its own drivers line (alongside aces + DFs)
    # below, so skip the standalone sets line for FS to avoid showing the count twice.
    if isinstance(exp_sets, (int, float)) and prop_type != "Fantasy Score":
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
    # Fantasy Score is a COMPOSITE — surface the drivers that compose the number so the
    # projection is legible: the player's projected aces + double faults (each scored
    # ±0.5) and the match's expected set count (each set won/lost swings FS by 3). Order
    # follows the user's ask: aces, then double faults, then expected sets.
    if prop_type == "Fantasy Score":
        _fa, _fd = data.get("fs_ace_proj"), data.get("fs_df_proj")
        _fs_sets, _fs_comp = data.get("expected_sets"), data.get("competitiveness")
        _drv = []
        if _fa is not None:
            _drv.append(f"**{_num(_fa)}** aces")
        if _fd is not None:
            _drv.append(f"**{_num(_fd)}** double faults")
        if isinstance(_fs_sets, (int, float)):
            _drv.append(f"**{_fs_sets:.1f}** sets" + (f" ({_fs_comp})" if _fs_comp else ""))
        if _drv:
            g_proj += "\n🎾 Projected  ·  " + "  ·  ".join(_drv)
    # Player Total Games Won renders like any other prop (2026-07-26, user): both the
    # games-won breakdown (held on serve + by breaking) and the implied match-outcome
    # claim were removed. A games-won total isn't a win/lose scenario, and P(over) is
    # already the confidence bar.
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
    app_commands.Choice(name="Break Points Saved", value="Break Points Saved"),
    app_commands.Choice(name="Total Games", value="Total Games"),
    app_commands.Choice(name="Player Total Games Won", value="Player Total Games Won"),
    app_commands.Choice(name="Fantasy Score", value="Fantasy Score"),
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

        # THE TOUR, FOR FREE. The player field's autocomplete value is the
        # encoded "id|tour|name", so by the time someone reaches the court field
        # we already know whether this is an ATP or a WTA matchup — no network
        # call, no guess. A WTA user was being offered Vienna and the ATP Finals;
        # the web app filtered these out long ago and this brings Discord level.
        # Free text in the player field decodes to None, and then we show both.
        tour = decode_player(getattr(interaction.namespace, "player", "") or "")[1]

        if surface and surface in COURTS_BY_SURFACE:
            pool = [("None", None)] + [(c, surface) for c in courts_for(surface, tour)]
        else:
            # Surface not chosen — INTERLEAVE across surfaces so the 25-item cap
            # doesn't truncate whole surfaces (grass is last and was getting cut
            # to just Wimbledon). Round-robin one court per surface at a time.
            from itertools import zip_longest
            pool = [("None", None)]
            per_surface = [[(c, surf) for c in courts_for(surf, tour)]
                           for surf in COURTS_BY_SURFACE]
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
    # PTGW is under a structural rebuild (scenario-mixture model). Until it is
    # verified and re-enabled, /prop does not serve it. The new chain still runs
    # in the daily board's shadow log; it just isn't offered on demand here.
    if prop_type.value == "Player Total Games Won" and not pick_of_day.PTGW_ENABLED:
        log.info("CMD /prop | PTGW requested while disabled — returning rebuild notice")
        await _send_error(interaction,
            "**Player Total Games Won is under rebuild.** The projection method for "
            "this prop is being reworked (bimodal scenario model) and is temporarily "
            "unavailable. Other props are unaffected.")
        return
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


# ── /match — match-outcome projections ──────────────────────────────────────────
# Every outcome below comes from the SAME four-scenario mixture the props ride on,
# from the selected player's perspective:
#   S1 win in straights · S2 win in a decider · S3 lose in a decider · S4 lose in straights
# so P(win >= 1 set) = 1 - S4, P(straights) = S1, P(goes the distance) = S2 + S3.
# The scenario split is LINE-INDEPENDENT (verified: identical S1-S4 across lines
# 6.5/10.5/14.5), so we ask for the mixture with a neutral line and ignore p_over.
# That is why this needs no new backend endpoint.
_MATCH_NEUTRAL_LINE = 10.5


def match_outcomes_embed(p_name: str, o_name: str, surface: str, court_display: str,
                         data: dict) -> discord.Embed:
    """Match-outcome projection: win a set / win the match / straights / distance,
    plus the supporting match profile. Projection only — no book price, no edge."""
    sp = data.get("ptgw_scenario_probs") or {}
    s1 = sp.get("S1") or 0.0
    s2 = sp.get("S2") or 0.0
    s3 = sp.get("S3") or 0.0
    s4 = sp.get("S4") or 0.0
    p_win = data.get("ptgw_p_win_match")
    if not isinstance(p_win, (int, float)):
        _wp = data.get("p1_win_prob")
        p_win = (_wp / 100.0) if isinstance(_wp, (int, float)) else (s1 + s2)

    p_set   = 1.0 - s4          # win at least one set
    p_str   = s1                # win in straight sets
    p_dist  = s2 + s3           # match goes the distance
    p_opp_set = 1.0 - s1        # opponent wins at least one set

    is_bo5 = bool(data.get("is_bo5"))
    dist_label = "Match goes 4+ sets" if is_bo5 else "Match goes 3 sets"

    e = discord.Embed(title="🎾 Match Outlook",
                      color=COLOR_OVER if p_win >= 0.5 else COLOR_UNDER)
    loc = court_display if court_display and court_display != "Generic surface" else f"{surface} court"
    e.description = (f"**{p_name}** vs **{o_name}**\n_{loc}_"
                     + (f"  ·  _{data.get('match_format_label')}_"
                        if data.get("match_format_label") else ""))

    # Headline block — win-a-set first, since that's the market this was built for.
    rows = [
        f"🎯 **Wins at least one set**  ·  **{p_set*100:.0f}%**",
        f"🏆 Wins the match  ·  **{p_win*100:.0f}%**",
        f"⚡ Wins in straight sets  ·  **{p_str*100:.0f}%**",
        f"⏳ {dist_label}  ·  **{p_dist*100:.0f}%**",
        f"↩️ {o_name} wins at least one set  ·  **{p_opp_set*100:.0f}%**",
    ]
    e.add_field(name=f"Outcome probabilities — {p_name}", value="\n".join(rows), inline=False)

    # Match profile — the context behind the numbers.
    prof = []
    if isinstance(data.get("expected_sets"), (int, float)):
        prof.append(f"Expected sets **{data['expected_sets']:.1f}**")
    if data.get("competitiveness"):
        prof.append(str(data["competitiveness"]))
    if data.get("environment_label"):
        prof.append(str(data["environment_label"]))
    if data.get("court_speed_tier"):
        _cpi = data.get("court_pace_index")
        prof.append(f"{data['court_speed_tier']} court"
                    + (f" (CPI {_cpi:g})" if isinstance(_cpi, (int, float)) else ""))
    if prof:
        e.add_field(name="Match profile", value=" · ".join(prof), inline=False)

    # Serve/return context — tiebreak rate is the serve-dominance read the mixture uses.
    serve = []
    for nm, key in ((p_name, "player_tiebreak_rate"), (o_name, "opponent_tiebreak_rate")):
        v = data.get(key)
        if isinstance(v, (int, float)):
            serve.append(f"{nm} **{v:.0f}%**")
    if serve:
        e.add_field(name="Tiebreak rate (serve dominance)", value="  ·  ".join(serve), inline=False)

    # H2H — only when they've actually met.
    h = data.get("h2h_context") or {}
    if (h.get("total") or 0) > 0:
        line = f"**{h.get('p1_wins', 0)}–{h.get('p2_wins', 0)}** in {h['total']} meeting" \
               f"{'s' if h['total'] != 1 else ''}"
        if h.get("surface_matches"):
            line += (f"  ·  on {surface}: {h.get('surface_p1_wins', 0)}–"
                     f"{h.get('surface_p2_wins', 0)} of {h['surface_matches']}")
        e.add_field(name=f"Head-to-head ({p_name} first)", value=line, inline=False)

    # NOTE (2026-07-30, user): the "Basis" provenance field (market-anchored flag,
    # data quality, surface sample sizes) was REMOVED from the embed along with the
    # caveat below. Those signals are still computed and available in the response
    # (ptgw_anchored / data_quality / *_surface_n) — they just aren't printed.
    #
    # NOTE (2026-07-30, user): the "read this as an estimate" caveat field was
    # REMOVED from the embed. The limitation still stands — these set splits are a
    # by-product of the games-won fit, are not separately calibrated against set
    # results, and have no graded track record — it just isn't printed on the post.
    return _stamped_footer(e, FOOTER_GENERIC)


@client.tree.command(name="match",
                     description="Match outlook: win a set, win the match, straight sets, distance")
@app_commands.describe(
    player="Player the projection is for — type to search",
    opponent="Opponent — type to search",
    surface="Court surface",
    court="Tournament (optional) — choose one matching the surface, or None for generic",
    gs_round="ATP Grand Slam only: Main Draw (best of 5) or Qualifying (best of 3). Default Main Draw.",
)
@app_commands.choices(surface=SURFACE_CHOICES, gs_round=ROUND_CHOICES)
@app_commands.autocomplete(player=player_autocomplete, opponent=player_autocomplete,
                           court=court_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def match_outlook(
    interaction: discord.Interaction,
    player: str,
    opponent: str,
    surface: app_commands.Choice[str],
    court: str = "None",
    gs_round: app_commands.Choice[str] = None,
):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    log.info("CMD /match | user=%s | %s vs %s | %s | court=%s",
             interaction.user.id, player, opponent, surface.value, court)
    try:
        surface_val = surface.value
        court = (court or "None").strip()

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

        try:
            p_id, p_tour, p_name = await resolve_player(player)
            o_id, o_tour, o_name = await resolve_player(opponent)
        except NETWORK_ERRORS:
            log.warning("match resolve: backend unreachable")
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
        is_atp_gs = (court in ATP_GRAND_SLAMS) and (tour == "ATP")
        qualifying = is_atp_gs and gs_round is not None and gs_round.value == "qualifying"

        payload = {
            "player_id": p_id, "opponent_id": o_id,
            "player_name": p_name, "opponent_name": o_name,
            "tour": tour, "surface": surface_val, "court": court_key,
            # The scenario mixture rides on the PTGW path; the line is irrelevant to
            # the set split (verified line-independent) and p_over is discarded.
            "prop_type": "Player Total Games Won",
            "prop_line": _MATCH_NEUTRAL_LINE,
            "qualifying": qualifying,
        }

        try:
            data = await backend_post("/api/prop/calculate", payload, PROP_TIMEOUT)
        except NETWORK_ERRORS:
            log.warning("match calc: backend timeout/unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if _is_block_response(data):
            await _send_error(interaction, MSG_BLOCK)
            return

        if not (data.get("ptgw_scenario_probs") or {}):
            await _send_error(interaction,
                "No match outlook available for this matchup — the scenario model "
                "didn't return set probabilities. Check the players and surface.")
            return

        await interaction.followup.send(
            embed=match_outcomes_embed(p_name, o_name, surface_val, court_display, data),
            ephemeral=True,
        )
    except Exception:  # noqa: BLE001 — never let a command crash the process
        log.exception("UNHANDLED /match error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


# ── /spread — game handicap ─────────────────────────────────────────────────────
def spread_embed(p_name: str, o_name: str, surface: str, court_display: str,
                 spread: float, data: dict) -> discord.Embed:
    """Game-spread (games handicap) projection. Settles on the GAME MARGIN across
    every set, regardless of who wins the match."""
    p_cov = data.get("spread_p_cover")
    margin = data.get("spread_margin_proj")
    if not isinstance(p_cov, (int, float)):
        p_cov = 0.0
    other = 1.0 - p_cov

    e = discord.Embed(title="🎾 Game Spread",
                      color=COLOR_OVER if p_cov >= 0.5 else COLOR_UNDER)
    loc = court_display if court_display and court_display != "Generic surface" else f"{surface} court"
    e.description = (f"**{p_name}** {spread:+g} games  vs  **{o_name}**\n_{loc}_"
                     + (f"  ·  _{data.get('match_format_label')}_"
                        if data.get("match_format_label") else ""))

    # Spell out what clearing the handicap actually REQUIRES in games, rather than
    # leaving the reader to do the margin arithmetic.
    if spread < 0:                                   # laying games
        _need_txt = f"winning by {int(abs(spread)) + 1}+ total games"
    else:                                            # receiving games
        _max_loss = (int(spread) - 1) if float(spread).is_integer() else int(spread)
        _need_txt = (f"losing by {_max_loss} or fewer games, or winning outright"
                     if _max_loss > 0 else "winning outright")

    rows = [f"🎯 **{p_cov*100:.0f}% chance {p_name} clears {spread:+g}**",
            f"↩️ {other*100:.0f}% chance {o_name} clears {-spread:+g}"]
    if isinstance(margin, (int, float)):
        _verb = "wins by" if margin >= 0 else "loses by"
        rows.append(f"📐 Projected margin  ·  **{p_name} {_verb} {abs(margin):.1f} games**")
    # Whole-number handicaps can land exactly on the number — that's a push (stake
    # back), neither cleared nor lost. Half-point spreads can't push, so say nothing.
    _push_txt = ""
    if float(spread).is_integer():
        _push_txt = (f"  A {int(abs(spread))}-game margin is a push."
                     if abs(spread) > 0 else "")
    rows.append(f"_Clearing {spread:+g} means {_need_txt}.{_push_txt}_")
    e.add_field(name=f"Chance to clear {spread:+g}", value="\n".join(rows), inline=False)

    # Where the cover actually comes from — the same four scenarios as /match.
    scen = data.get("spread_scenarios") or {}
    if scen:
        is_bo5 = bool(data.get("is_bo5"))
        labels = {"S1": "Wins in straights", "S2": "Wins in a decider",
                  "S3": "Loses in a decider", "S4": "Loses in straights"}
        lines = []
        for s in ("S1", "S2", "S3", "S4"):
            d_s = scen.get(s)
            if not d_s:
                continue
            # Two DIFFERENT percentages, so say which is which: how likely the
            # scenario is, then — within that scenario only — how often it clears.
            lines.append(f"**{labels[s]}** · {d_s['p']*100:.0f}% likely · "
                         f"avg margin {d_s['margin']:+.1f} · "
                         f"clears {spread:+g} in **{d_s['p_cover']*100:.0f}%** of those")
        if lines:
            e.add_field(name=f"Where the {p_cov*100:.0f}% comes from",
                        value="\n".join(lines), inline=False)

    prof = []
    if isinstance(data.get("expected_sets"), (int, float)):
        prof.append(f"Expected sets **{data['expected_sets']:.1f}**")
    if data.get("competitiveness"):
        prof.append(str(data["competitiveness"]))
    if isinstance(data.get("p1_win_prob"), (int, float)):
        prof.append(f"{p_name} wins **{data['p1_win_prob']:.0f}%**")
    if data.get("court_speed_tier"):
        prof.append(f"{data['court_speed_tier']} court")
    if prof:
        e.add_field(name="Match profile", value=" · ".join(prof), inline=False)

    h = data.get("h2h_context") or {}
    if (h.get("total") or 0) > 0:
        line = (f"**{h.get('p1_wins', 0)}–{h.get('p2_wins', 0)}** in {h['total']} "
                f"meeting{'s' if h['total'] != 1 else ''}")
        if h.get("surface_matches"):
            line += (f"  ·  on {surface}: {h.get('surface_p1_wins', 0)}–"
                     f"{h.get('surface_p2_wins', 0)} of {h['surface_matches']}")
        e.add_field(name=f"Head-to-head ({p_name} first)", value=line, inline=False)
    return _stamped_footer(e, FOOTER_GENERIC)


@client.tree.command(name="spread",
                     description="Game spread (games handicap) — chance a player covers")
@app_commands.describe(
    player="Player the spread is for — type to search",
    opponent="Opponent — type to search",
    spread="Games handicap for that player, e.g. -4.5 (laying) or 4.5 (receiving)",
    surface="Court surface",
    court="Tournament (optional) — choose one matching the surface, or None for generic",
    gs_round="ATP Grand Slam only: Main Draw (best of 5) or Qualifying (best of 3). Default Main Draw.",
)
@app_commands.choices(surface=SURFACE_CHOICES, gs_round=ROUND_CHOICES)
@app_commands.autocomplete(player=player_autocomplete, opponent=player_autocomplete,
                           court=court_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def spread_cmd(
    interaction: discord.Interaction,
    player: str,
    opponent: str,
    spread: float,
    surface: app_commands.Choice[str],
    court: str = "None",
    gs_round: app_commands.Choice[str] = None,
):
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    log.info("CMD /spread | user=%s | %s %+g vs %s | %s | court=%s",
             interaction.user.id, player, spread, opponent, surface.value, court)
    try:
        if spread == 0 or abs(spread) > 15:
            await _send_error(interaction,
                "Give a game handicap between -15 and 15 (not 0) — e.g. `-4.5` to lay "
                "4.5 games or `4.5` to receive them.")
            return

        surface_val = surface.value
        court = (court or "None").strip()
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

        try:
            p_id, p_tour, p_name = await resolve_player(player)
            o_id, o_tour, o_name = await resolve_player(opponent)
        except NETWORK_ERRORS:
            log.warning("spread resolve: backend unreachable")
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
        is_atp_gs = (court in ATP_GRAND_SLAMS) and (tour == "ATP")
        qualifying = is_atp_gs and gs_round is not None and gs_round.value == "qualifying"

        payload = {
            "player_id": p_id, "opponent_id": o_id,
            "player_name": p_name, "opponent_name": o_name,
            "tour": tour, "surface": surface_val, "court": court_key,
            "prop_type": "Player Total Games Won",
            "prop_line": _MATCH_NEUTRAL_LINE,
            "spread": float(spread),
            "qualifying": qualifying,
        }
        try:
            data = await backend_post("/api/prop/calculate", payload, PROP_TIMEOUT)
        except NETWORK_ERRORS:
            log.warning("spread calc: backend timeout/unreachable")
            await _send_error(interaction, MSG_UNREACHABLE)
            return

        if _is_block_response(data):
            await _send_error(interaction, MSG_BLOCK)
            return
        if data.get("spread_p_cover") is None:
            await _send_error(interaction,
                "No spread projection available for this matchup — the scenario model "
                "didn't return a margin. Check the players and surface.")
            return

        await interaction.followup.send(
            embed=spread_embed(p_name, o_name, surface_val, court_display, float(spread), data),
            ephemeral=True,
        )
    except Exception:  # noqa: BLE001 — never let a command crash the process
        log.exception("UNHANDLED /spread error")
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
        name="/match",
        value=(
            "Match outlook — set and match outcome probabilities, no line needed.\n"
            "`/match player:Sinner opponent:Alcaraz surface:Hard`\n"
            "Shows **wins at least one set**, wins the match, wins in straight sets, "
            "and whether it goes the distance — plus the match profile, tiebreak "
            "rates and H2H behind those numbers."
        ),
        inline=False,
    )
    e.add_field(
        name="/spread",
        value=(
            "Game spread (games handicap) — chance a player covers.\n"
            "`/spread player:Sinner opponent:Alcaraz spread:-4.5 surface:Hard`\n"
            "Settles on the **game margin** across every set, so it counts every "
            "game won regardless of who takes the match. Shows the cover chance "
            "for both sides, the projected margin, and how the cover happens."
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
# MASTER SWITCH for automated posting to the picks channel — user requested OFF on
# 2026-07-24, until further notice. When off: NO automated Pick of the Day board and
# AUTO-POST RE-ENABLED 2026-07-26 (explicit user go-ahead). Daily POTD board warms at
# 19:30 ET (PREWARM_HOUR/MINUTE) and posts at 20:00 ET (PICKS_GEN_HOUR/MINUTE). NOTE:
# this same master switch also gates the daily results RECAP post. Default flipped to
# "true"; set AUTOPOST_ENABLED=false in the Railway env to force it back off with no
# code change.
AUTOPOST_ENABLED = os.getenv("AUTOPOST_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on")

# ── SECOND SCANS ARE OFF (operator, 2026-09-26) ──────────────────────────────
# One switch for every post that adds plays to a card already published: the
# morning/extension second wave, the 8 PM evening rescan, the date-gated extra
# run, and the Underdog top-up. They are the same idea posted at four times of
# day, and measured together they lose.
#
# Since the board's prop mix settled (2026-09-12 -> 09-25), against a 57.74%
# breakeven:
#
#     Discord board (potd)   44-32   57.9%   <- the board itself is fine
#     second-wave             7-12   36.8%
#     3x                      3-6    33.3%
#     evening-rescan          2-4    33.3%
#     underdog                2-3    40.0%
#
# Over the full record the same pattern held: second-wave 105-97 (52.0%) against
# the board's 310-261. The board is not short of value — the additions around it
# were cancelling the value it found. This is also what the evening_rescan
# comment already said from the earlier removal (100-97, 50.8%, dragging the
# published record from 55.0% to 53.8%); it came back on 2026-09-22 and the
# result was the same.
#
# Gated in ONE place rather than by deleting four schedulers, so re-enabling is
# SECOND_SCANS_ENABLED=true and the measurement above stays attached to the
# decision.
SECOND_SCANS_ENABLED = os.getenv("SECOND_SCANS_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on")
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
PICKS_GEN_HOUR = int(os.getenv("PICKS_GEN_HOUR", "22") or "22")     # 10:00 PM POTD (2026-07-29 user: 8 PM -> 10 PM)
PICKS_GEN_MINUTE = int(os.getenv("PICKS_GEN_MINUTE", "0") or "0")
# THE SEPARATE "SECOND WAVE" SCAN IS GONE (2026-09-21, user: "not needed any
# more with the new schedule"). It began as an 11 PM run, moved to 8 AM, then to
# midnight — each time to catch a board the previous drop was too early to see.
# The 4 PM board plus the 8 PM rescan cover that between them now, so a third
# scan only re-ranked the same card a third time.
#
# SECOND_WAVE_MAX SURVIVES because it is not really about that loop: it is the
# default cap on any "additional plays" post, and both the Underdog board and
# _post_second_wave still read it.
SECOND_WAVE_MAX    = int(os.getenv("SECOND_WAVE_MAX", "2") or "2")      # cap on additional plays
# Ranked plays are delivered in pages of this many, each its own @everyone message
# (top-12 → two messages: 1-6 then 7-12).
# NOTE: RANKED_PAGE_SIZE (6-plays-per-message paging) was retired when the ⭐ got
# its own embed — the board is now one compact two-lines-per-play list that fits a
# single embed, and splits only at play boundaries if it ever outgrows one.
# One-off EXTRA run: on this ET date ONLY, re-run the ranked list + 3x at 11 PM ET
# (in addition to the normal daily run). The recurring schedule above is untouched;
# dedup in _log_picks_pending prevents any double-counting. Set to "" to disable —
# auto-reverts the next day.
# Default "" = DISARMED, which is what the comment above already documents as
# the off value. It previously defaulted to a one-off date that is now long
# past. Behaviour is unchanged either way — a past date can never equal today,
# and every consumer is truthiness-guarded — but a stale date READS as armed,
# and a one-off override that looks live is exactly the class of thing that
# gets misread during an incident.
POD_EXTRA_RUN_DATE = os.getenv("POD_EXTRA_RUN_DATE", "")
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

# Underdog equivalent, for reposting its board after a channel change without
# waiting for 10:30 PM. Off by default; unset it again once it has fired.
UNDERDOG_POST_ON_START = (os.getenv("UNDERDOG_POST_ON_START", "0")
                          or "0") not in ("0", "false", "False")
_ud_startup_done = False

# Feature 4 — daily Slate auto-post to the 📋・slate channel, at midnight ET
# alongside the Pick of the Day.
SLATE_CHANNEL_ID = int(os.getenv("SLATE_CHANNEL_ID", "1519546971344470027") or "0")
SLATE_HOUR = int(os.getenv("SLATE_HOUR", "0") or "0")      # 12:00 AM ET
SLATE_MINUTE = int(os.getenv("SLATE_MINUTE", "0") or "0")

# Daily win/loss record auto-post (the /results command is bot-only now).
# 11:45 PM ET by default — just before the Pick of the Day, after the day's
# picks have been graded by the resolver. Defaults to the POD channel.
RESULTS_CHANNEL_ID = int(os.getenv("RESULTS_CHANNEL_ID", str(POD_CHANNEL_ID or 0)) or "0")
# Public track-record channel — the daily RECAP posts here with @everyone (once a
# day, so the ping is fine), separate from the premium POTD channel where the
# board/3x keep their own @everyone. Hardcoded by request (2026-07-29): no env override.
TRACK_RECORD_CHANNEL_ID = 1532142615435284721
# Line-movement alerts post here (🆘・line-changes) instead of the POTD board
# channel — they're informational, fire repeatedly, and cluttered the board feed.
# Hardcoded by request (2026-07-29): no env override. Never pinged.
LINE_ALERT_CHANNEL_ID = 1532229544503672853
# Minimum post-guard decided picks before the weekly calibration table means
# anything. All history up to 2026-07-14 is pre-guard (confidence possibly scored
# on a cache-poisoned snapshot), so the clean sample restarts from zero and the
# table stays suppressed until it rebuilds rather than reporting noise.
CALIBRATION_MIN_SAMPLE = 40

# ── Calibration baseline ─────────────────────────────────────────────────────
# Picks generated BEFORE this instant were scored by a materially different model
# and must never be pooled with what comes after. Two breaks landed on 2026-07-15:
#   • the data-integrity fixes (cache-poisoning guard, deterministic event
#     selection, stat-rich standardisation) — see picks.pre_guard; and
#   • the games_per_set per-tour fit (FREEZE_LOG entry 2), which moves EVERY
#     Total Games and Player Total Games Won projection by 1-2 games, plus the
#     Total Games confidence ceiling that followed from it.
# A hit rate computed across that boundary measures two different models averaged
# together, which is worse than no number at all. This supersedes the pre_guard
# flag as the cutoff — pre_guard stays as the historical marker of the first break.
# Stored as a UTC instant: tonight's 8:20 PM ET run is 00:20 UTC on 7/16, so it is
# the FIRST run on the new model and the first to count.
CALIBRATION_BASELINE_UTC = os.getenv("CALIBRATION_BASELINE_UTC", "2026-07-16T00:00:00")

# Daily recap posts at 8:45 AM ET (2026-07-30, user; was 12:05 AM, before that
# 5 PM). It still recaps the JUST-COMPLETED day — moving it later only makes that
# recap MORE complete, since matches finishing after midnight have graded by then.
# Matches that slid to the next day grade the next day and land in that day's
# recap (resolution-date scoped, 6 AM→6 AM window). Env vars still override — if
# RESULTS_POST_HOUR/MINUTE are set in Railway they win, so keep them unset (or set
# to 8 / 45) for this 8:45 AM schedule to take effect.
# KEEP THIS OFF THE HOUR of any posting loop. It used to sit 45 minutes after an
# 8:00 AM second wave because at 8:00 sharp the two collided — two @everyone
# posts in the same minute, both doing heavy backend work (the recap resolves
# every pending pick, a scan walks the whole board for ~6-10 min). That scan is
# gone, but the rule it taught is not: when retiming anything, do not land it on
# the same minute as another drop.
RESULTS_POST_HOUR = int(os.getenv("RESULTS_POST_HOUR", "8") or "8")
RESULTS_POST_MINUTE = int(os.getenv("RESULTS_POST_MINUTE", "45") or "45")

# ── One-off schedule override (DISABLED by default) ─────────────────────────
# The recurring schedule now governs, driven by the Railway env vars
# (PICKS_GEN_HOUR/MINUTE, PREWARM_HOUR/MINUTE) — POTD 8:00 PM, pre-warm 7:30 PM.
# The one-off override is OFF unless ONEOFF_SCHED_DATE is explicitly set in the
# env to a real date; the empty default never matches today, so on every date
# _slot_is_live() picks the recurring slot and the one-off times below are dormant
# (they only fire on a date that equals ONEOFF_SCHED_DATE, which is never "").
# One-off override, OFF. The 2026-08-02 11:30 PM rain-delay scan is spent — it
# never fired because the duplicate guard below compared generation dates instead
# of slate dates (fixed 2026-08-03), and the date has since passed. Set
# ONEOFF_SCHED_DATE to a real date to re-arm; the empty default never matches, so
# _slot_is_live() always picks the recurring slot.
ONEOFF_SCHED_DATE = os.getenv("ONEOFF_SCHED_DATE", "")
ONEOFF_RECAP_HM   = (3, 0)      # dormant
ONEOFF_POTD_HM    = (23, 30)    # dormant (was the 8/2 one-off slot)
ONEOFF_PREWARM_HM = (23, 0)     # dormant
# Extension scan PARKED for today: user asked only for the 5 PM recap + 7 PM POTD.
# A past time-of-day means its next firing is tomorrow, which isn't the one-off
# date, so the loop body no-ops — no unrequested 10:15 PM additions post.
ONEOFF_EXT_HM     = (12, 0)     # parked (no extension today)

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
PREWARM_HOUR   = int(os.getenv("PREWARM_HOUR", "21") or "21")     # 9:30 PM — 30 min
PREWARM_MINUTE = int(os.getenv("PREWARM_MINUTE", "30") or "30")   # before the 10 PM POTD


def _sched_times(oneoff_hm: tuple, hour: int, minute: int) -> list:
    """The times a slot-guarded loop should actually wake at.

    THE ONE-OFF TIME IS ONLY REGISTERED WHEN AN OVERRIDE DATE IS SET. It used to
    be on every one of these loops unconditionally and then rejected at run time
    by _slot_is_live, so the bot woke at 23:00 and 23:30 every night to decide it
    had nothing to do — a scheduled job for a date long past, showing up in the
    logs as SLOT_SKIP (operator, 2026-09-20: "the 1130 pm one off needs to be
    shut off").

    The mechanism is intact rather than deleted: set ONEOFF_SCHED_DATE and the
    one-off slot is registered again exactly as before.
    """
    times = [datetime.time(hour=hour, minute=minute, tzinfo=POD_TZINFO)]
    if ONEOFF_SCHED_DATE:
        times.insert(0, datetime.time(hour=oneoff_hm[0], minute=oneoff_hm[1],
                                      tzinfo=POD_TZINFO))
    return times


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
RESULTS_SKIP_DATE = os.getenv("RESULTS_SKIP_DATE", "")   # "" = disarmed
# One-off Pick of the Day skip: on this ET date the scans DON'T generate picks —
# the 4:50 scan posts a "no value, waiting for new tournaments" @everyone notice
# and the evening scan stays silent. Set to "" to disable. Resumes next day.
POD_SKIP_DATE = os.getenv("POD_SKIP_DATE", "")           # "" = disarmed
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
# The floor is READ, not typed. It said "65%" while the constant moved to 70,
# and a public message quoting a bar the board no longer uses is a wrong claim.
MSG_NO_PICK_DAILY = (
    f"No qualifying plays today — nothing on the board cleared our "
    f"{pick_of_day.BOARD_MIN_CONF}% board floor. We'd rather sit out than force "
    f"a weak play. Check back tomorrow. 🎾"
)
# v2 no-POTD fallback: the board HAS plays, but none cleared the 80% Pick-of-the-
# Day bar. The ranked board still posts; this rides in place of the ⭐ embed.
MSG_NO_POTD_HAS_BOARD = (
    "**No Pick of the Day today — no play qualified for the ⭐ slot. Board below.**"
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


def _pick_to_record(p: dict, group: str = "potd", excluded: bool = False) -> dict:
    return {
        # 1 for shadow props: graded normally, never counted in the record.
        "excluded_from_record": 1 if excluded else 0,
        "player": p.get("player", ""), "opponent": p.get("opponent", ""),
        "prop_type": p.get("prop_type", ""), "line": p.get("line"),
        "model_projection": p.get("projection"), "lean": (p.get("lean") or "").upper(),
        "confidence": p.get("confidence"), "result": "PENDING",
        "original_line": p.get("original_line", p.get("line")),
        "tournament": p.get("tournament") or "", "surface": p.get("surface") or "",
        "pick_group": group,
        # THE STAR, RECORDED. pick_group says only "this came off the board";
        # every board pick carries it, so the record could never answer "how has
        # the Pick of the Day done" — the question the ⭐ actually makes.
        "is_potd": 1 if p.get("is_potd") else 0,
        "confidence_breakdown": _breakdown_json(p),
        # standard vs demon — so the results tracker / recaps / hit rates segment.
        "odds_type": (p.get("odds_type") or "standard"),
        # What the model actually used. See _model_inputs_json.
        "model_inputs": _model_inputs_json(p),
        # ── THE CONFIDENCE/EDGE SPLIT, IN SHADOW (2026-09-24) ────────────────
        # Recorded so the split can be GRADED on picks that were actually
        # posted, rather than reconstructed later against code that has moved.
        # Neither of these selects anything yet — `confidence` above is still
        # the number every gate reads.
        #   edge_sigma            |projection − line| / σ — the value signal,
        #                         and the one that survived a temporal holdout.
        #   confidence_data_only  evidence with no line-derived ceiling, i.e.
        #                         confidence as it is meant to read on the board.
        "edge_sigma": p.get("edge_sigma"),
        "confidence_data_only": p.get("confidence_data_only"),
        # ── TOUR LEVEL (2026-09-24) ──────────────────────────────────────────
        # The tournament string cannot answer this. Sofascore returns a bare
        # city, so 'Tolentino, Italy' (ITF) and 'Cincinnati, USA' (Masters) look
        # identical — searching the record for 'challenger'/'itf' matches ZERO
        # rows and reads as "we never post lower-tier matches", which is wrong.
        # The bot has carried these three all along; they were just never
        # persisted, so the question could not be asked of the record.
        "player_rank": p.get("player_rank"),
        "opponent_rank": p.get("opponent_rank"),
        "both_challenger_level": (1 if p.get("both_challenger_level") else 0),
    }


# Props whose stored model_projection is a FAIR LINE (the 50/50 point of a
# scenario mixture), not an expected value. Recorded per pick because this
# changed silently at the A2 rebuild and nothing marked the boundary: a
# retrospective error metric that mixes the two is comparing a mean against a
# median and will report noise as a broken model.
#
# FANTASY SCORE ADDED 2026-09-24. It has published a fair line all along —
# props.py sets `projection = fs_fair_line(...)`, because for a bimodal prop
# "the mean lands in the empty valley between the win and loss bands, a score
# that almost never occurs". This set said otherwise, so every FS pick was
# stamped projection_kind="mean" and any analysis trusting that field was
# comparing actual counts against a median while believing it had a mean —
# the precise error this field exists to prevent. Mirrors FAIR_LINE_PROPS in
# backend/src/database.py, which excludes both from bias correction.
_FAIR_LINE_PROPS = {"Break Points Won", "Fantasy Score"}


def _model_inputs_json(p: dict) -> str:
    """Compact JSON of WHAT THE MODEL USED, for later measurement.

    Not diagnostics — these are the inputs a future question will need and which
    CANNOT be recovered afterwards. Re-running the model later re-runs today's
    code against today's statistics, which is a different pick: the stats have
    moved, the model has changed, and a market anchor present at pick time may
    be gone by the time anyone looks.

    Everything here is read from the calculate response the pick was actually
    built from. Empty string when unavailable; truncated so one row cannot bloat.
    """
    try:
        d = (p.get("data") or {})
        if not d:
            return ""
        prop = p.get("prop_type") or ""
        out = {
            # THE FIELD THAT MATTERS MOST: what kind of number model_projection is.
            "projection_kind": "fair_line" if prop in _FAIR_LINE_PROPS else "mean",
            # The outcome anchor — which drives every scenario mixture.
            "p_sel": (d.get("bp_blended_wp") or d.get("fs_blended_wp")
                      or d.get("ptgw_blended_wp")),
            "market_wp": (d.get("bp_market_wp") or d.get("fs_market_wp")
                          or d.get("ptgw_market_wp")),
            "model_wp": (d.get("bp_model_wp") or d.get("fs_model_wp")
                         or d.get("ptgw_model_wp")),
            "anchored": (d.get("bp_anchored") if prop == "Break Points Won"
                         else d.get("fs_anchored")),
            "expected_sets": d.get("expected_sets"),
            # Serve inputs — Fantasy Score consumes both at face value.
            "ace_proj": d.get("fs_ace_proj"),
            "df_proj": d.get("fs_df_proj"),
            # Hold rates: the games-margin identity is built from these.
            "player_hold": d.get("player_hold_rate_pct") or d.get("player_hold_rate"),
            "opp_hold": d.get("opp_hold_rate_pct"),
            # BP internals — bp_base_proj is the C1–C6 SUB-TOTAL, which is NOT the
            # value the FS games margin consumes. Both are stored so the next
            # analysis cannot confuse them again.
            "bp_base_proj": d.get("bp_base_proj"),
            "bp_mixture_mean": d.get("bp_mixture_mean"),
            "bp_fair_line": d.get("bp_fair_line"),
            "bp_p_over": d.get("bp_p_over"),
            # ── THE C-CHAIN, COMPONENT BY COMPONENT ──────────────────────────
            # base = C1 × C2 × (C3/100) × C4 × C5 × C6. Storing only the
            # sub-total meant a projection that moved could not be attributed:
            # on 9/18 a base went 6.49 -> 4.33 on an unchanged matchup and a
            # quarter of the move sat in C2/C4/C5/C6, which nothing recorded.
            # These cost a few bytes and they are not recoverable later — the
            # stats move, the model changes, and the opponent plays again.
            "c1_opp_faced": d.get("bp_c1_opp_faced"),
            "c1_tour_avg_used": d.get("bp_c1_tour_avg_used"),
            "c1_opp_surf_sample": d.get("bp_opp_surf_sample"),
            "c2_returner_mult": d.get("bp_c2_returner_mult"),
            "c3_conv_pct": d.get("bp_c3_conv_pct"),
            "c4_serve_quality": d.get("bp_c4_serve_quality"),
            "c5_surface_adj": d.get("bp_c5_surface_adj"),
            "c6_cpr_mod": d.get("bp_c6_cpr_mod"),
            # C7 is ADDITIVE and has been pinned at its cap on every play we
            # looked at, so whether the cap bound is the thing to record.
            "c7_momentum": d.get("bp_momentum_bonus"),
            "c7_momentum_raw": d.get("bp_momentum_raw"),
            "c7_momentum_capped": d.get("bp_momentum_capped"),
            # What the bias correction moved the published number by.
            "bias_correction": d.get("bias_correction"),
            # Sample depth behind the opponent's numbers. `data_quality` counts
            # events AVAILABLE, not statistics FETCHED, so it reads "rich" on a
            # five-match stat sample — record the count, not the adjective.
            "opp_ss_matches": d.get("opponent_ss_matches"),
            "opp_rank": d.get("opponent_rank"),
            "cpi": d.get("court_pace_index"),
            # INDOOR, RECORDED BEFORE IT MATTERS. Across 517 graded picks there
            # is not one indoor-hard result — the record starts in June and the
            # indoor swing is October/November — so the aces +6.5% and break
            # points -4% adjustments have never been tested against an outcome,
            # and there is nothing to calibrate the other four props from.
            # Storing the flag now is what makes that answerable in December
            # rather than another argument.
            "indoor": d.get("indoor_venue"),
            "indoor_applied": d.get("indoor_court"),
            "indoor_source": d.get("indoor_source"),
            # Total Games market blend.
            "tg_book_line": d.get("tg_book_line"),
            "tg_model_proj": d.get("tg_model_proj"),
            "tg_blended_proj": d.get("tg_blended_proj"),
            "match_format": d.get("match_format_label"),
        }
        out = {k: v for k, v in out.items() if v is not None}
        # TRUNCATE BY DROPPING FIELDS, NOT BY SLICING THE STRING. A raw [:2000]
        # can cut mid-token and store JSON that will not parse — which is worse
        # than storing less, because the row looks present and fails silently
        # the day someone reads it. Now the payload stays valid at every size.
        blob = json.dumps(out, separators=(",", ":"))
        while len(blob) > 2000 and out:
            out.pop(next(reversed(out)))
            blob = json.dumps(out, separators=(",", ":"))
        return blob
    except Exception:  # noqa: BLE001
        return ""


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


async def _log_picks_pending(picks: list, group: str = "potd", excluded: bool = False):
    """Feature 1 — log each pick to the durable results tracker as PENDING,
    tagged with its pick group ("potd" or "3x").

    DEDUP: a (player, prop_type, group) already logged in the last ~18h is NOT
    logged again — so a same-day re-run (e.g. an extra evening run) never creates
    a duplicate row that would count twice when the prop resolves the next day."""
    try:
        rec = await asyncio.to_thread(results_tracker.get_record)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=18)
        existing = set()
        # record["picks"] is POTD-only — the 3x legs live in threex_legs["picks"].
        # Dedup MUST see both, or a re-posted 3x leg is never recognised as a
        # duplicate and gets logged again (the McNally FS 3x double on 2026-07-29).
        _dedup_src = (list((rec or {}).get("picks", []))
                      + list(((rec or {}).get("threex_legs") or {}).get("picks", [])))
        for q in _dedup_src:
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
        rec = await asyncio.to_thread(results_tracker.log_pick,
                                      _pick_to_record(p, group, excluded=excluded))
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

        # Line-change alerts go to their OWN channel (🆘・line-changes), not the
        # board channel — they're informational, fire repeatedly, and were cluttering
        # the POTD feed (2026-07-29, user). Falls back to the board channel only if
        # that channel can't be resolved, so an alert is never silently dropped.
        _alert_channel = client.get_channel(LINE_ALERT_CHANNEL_ID) or channel
        if _alert_channel is not channel:
            log.info("line monitor: alerts -> #%s (%s)",
                     getattr(_alert_channel, "name", "?"), LINE_ALERT_CHANNEL_ID)
        else:
            log.warning("line monitor: channel %s not found — alerts fall back to the "
                        "board channel", LINE_ALERT_CHANNEL_ID)

        async def _post_alert(payload):
            # Informational and repeat-firing — post WITHOUT an @everyone ping.
            # Mentions are suppressed entirely so a stray @everyone in the text
            # can't ping either.
            #
            # Accepts BOTH shapes on purpose. The monitor now sends a dict so the
            # alert renders as the same card NFL uses (core.alerts, so the two
            # cannot drift), but a plain string still works — and if anything at
            # all goes wrong building the embed we fall back to the text the dict
            # carries. An alert that fails to render must still be delivered;
            # silence here looks exactly like "the line never moved".
            none = discord.AllowedMentions.none()
            if isinstance(payload, dict):
                text = payload.get("text") or ""
                try:
                    from core import alerts as _al
                    parts = _al.line_alert(
                        player=payload.get("player"),
                        prop_label=payload.get("prop_label"),
                        old_line=payload.get("old_line"),
                        new_line=payload.get("new_line"),
                        projection=payload.get("projection"),
                        old_lean=payload.get("old_lean"),
                        new_lean=payload.get("new_lean"),
                        book=payload.get("book"), sport=payload.get("sport"),
                        note=payload.get("note"))
                    await _alert_channel.send(embed=_al.to_embed(parts, discord),
                                              allowed_mentions=none)
                    return
                except Exception:  # noqa: BLE001
                    log.exception("line alert embed failed — posting as text")
                    await _alert_channel.send(text, allowed_mentions=none)
                    return
            await _alert_channel.send(payload, allowed_mentions=none)

        _line_monitor_task = asyncio.create_task(
            line_monitor.monitor(picks, pick_of_day.current_board_lines, _post_alert))
        log.info("POD: line monitor started for %d picks", len(picks))
    except Exception:  # noqa: BLE001
        log.exception("failed to start line monitor")


async def _resume_line_monitor_on_startup():
    """Rebuild the in-memory line monitor after a bot restart.

    The monitor is an in-process task: a redeploy (or a crash/reconnect that
    restarts the process) kills it, and it otherwise only (re)starts when a NEW
    board is posted — so a restart in the hours between the daily post and the
    matches silently ends line-movement alerts for the day. On startup, re-arm it
    from TODAY's still-PENDING picks. The picks DB carries no PrizePicks name or
    match start time, so the monitor key falls back to the resolved player name
    (matches the board for all but trailing-patronymic names) and the match-started
    drop relies on the 20h safety cap. No-op when nothing is pending. Never raises."""
    global _line_monitor_task
    try:
        if not POD_CHANNEL_ID:
            return
        if _line_monitor_task and not _line_monitor_task.done():
            return   # a monitor is already running (plain reconnect) — leave it be
        channel = client.get_channel(POD_CHANNEL_ID)
        if channel is None:
            return
        pending = await asyncio.to_thread(results_tracker.get_pending) or []
        today = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
        picks = []
        for p in pending:
            if not str(p.get("generated_at") or "").startswith(today):
                continue   # only today's board — don't resurrect stale picks
            orig = p.get("original_line")
            orig = orig if orig is not None else p.get("line")
            if orig is None:
                continue
            picks.append({
                "player": p.get("player"),
                "pp_player": p.get("player"),      # DB has no PP name; resolved name usually matches
                "prop_type": p.get("prop_type"),
                "original_line": orig,
                "projection": p.get("model_projection"),
                "lean": p.get("lean"),
                # The MATCH, so the monitor can tell a line move from this
                # player's NEXT match reappearing under the same board key.
                "opponent": p.get("opponent"),
                "start_timestamp": None,           # unknown post-restart -> runs to the safety cap
            })
        if picks:
            _start_line_monitor(channel, picks)
            log.info("Line monitor RESUMED after restart for %d of today's pending picks",
                     len(picks))
        else:
            log.info("Line monitor resume: no pending picks for today — nothing to re-arm")
    except Exception:  # noqa: BLE001
        log.exception("line monitor resume-on-startup failed")


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
        _demon = "😈 DEMON " if leg.get("odds_type") == "demon" else ""
        bits = [f"{LEAN_DOT.get(lean, '⚪')} {_demon}**{play}**"]
        if isinstance(proj, (int, float)):
            bits.append(f"Proj {proj:.1f}")
        conf = _shown_conf(leg)
        if isinstance(conf, (int, float)):
            bits.append(f"{conf:.0f}%")
        lines.append(f"**{i}. {leg['player']}** vs {_short_opp(leg.get('opponent'))}")
        lines.append(" · ".join(bits))
        if leg.get("odds_type") == "demon":
            _std = leg.get("standard_line")
            _ctx = (f" (standard {_std:g})" if isinstance(_std, (int, float)) else "")
            lines.append(f"😈 _Boosted demon line {leg['line']:g}{_ctx} — over-only_")
        if i < len(legs):
            lines.append("")
    e.description = "\n".join(lines)
    return _stamped_footer(e, when=slate)


# ── Ranked plays list (the daily post) ───────────────────────────────────────
# Minimum service/return GAMES behind a displayed hold/return rate. ~40 games is
# roughly 3 matches — enough that the rate reflects a player rather than an
# afternoon. Below it the rate is shown as thin rather than as fact.
MIN_GAMES_FOR_RATE = 40


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
        # SAMPLE-GATED. These rates divide by SERVICE GAMES, not matches, and the
        # two populations diverge wildly: Gina Feistel had 36 stat-rich clay
        # matches but only TWO carrying service_games, so "Hold 94%" was 15/16
        # games from two ITF matches — displayed beside a 36-match count, and
        # flatly contradicting the 26% win probability on the same card. A reader
        # can only calibrate a rate against its denominator, so below the minimum
        # the stat is DROPPED entirely.
        #
        # It used to print the rate with an "(only N games — thin)" caveat, which
        # on the 9/3 Pick of the Day rendered as "Hold 85% (only 0 games — thin)"
        # twice — a made-up number and an apology for it, on the most prominent
        # card we post. Showing the rate anyway is the worse half of that: a
        # reader takes "Hold 85%" and skips the parenthetical. If the denominator
        # is too small to trust the number, there is no number to show.
        sg_n = ps.get("service_games_n")
        rg_n = ps.get("return_games_n")
        parts = []
        if isinstance(sg_n, (int, float)) and sg_n >= MIN_GAMES_FOR_RATE:
            parts.append(f"Hold **{_pct(ps.get('service_games_won_pct'))}**")
        if isinstance(rg_n, (int, float)) and rg_n >= MIN_GAMES_FOR_RATE:
            parts.append(f"Ret games won **{_pct(ps.get('return_games_won_pct'))}**")
        return " · ".join(parts)
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


def _shown_conf(pick: dict):
    """The confidence a subscriber sees — THE RAW MODEL SCORE.

    ONE NUMBER, THE ONE THAT ACTUALLY CHOSE THE PLAY (operator, 2026-09-21:
    "we should just show raw data if that's how the optimizer calculated it").
    Until now the board selected on the raw score and printed a calibrated one
    derived only from relative edge, and the two disagreed about which play was
    best. On the 9/21 board the ⭐ printed 58.8 and ranked FIFTH of nine by the
    number on screen, and Vekić and Kenin printed an identical 58.8 off the same
    line and projection despite raw scores of 78 and 75 — because the displayed
    figure is a function of (projection, line) alone and discards everything
    else the model knows.

    WHAT THIS COSTS, recorded honestly because it is real and measured on 702
    graded picks. The raw score does not order outcomes:

        65-70  52.8%    75-80  47.2%    85+  41.3%
        70-75  59.0%    80-85  59.5%

    The most confident band is the worst one, so a printed 85 describes a play
    that has won 41% of the time. The calibrated figure it replaces was at least
    rising at the top (66-71 -> 61.6%). This is a deliberate trade of that
    against internal consistency, and the right resolution is to re-fit the
    calibration on the number that drives selection rather than to keep two.

    `confidence_calibrated` is still attached to every pick and still written to
    the record, so nothing is lost and the comparison stays measurable.
    """
    c = pick.get("confidence")
    return c if isinstance(c, (int, float)) else pick.get("confidence_calibrated")


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
    conf = _shown_conf(pick)
    if isinstance(conf, (int, float)):
        bits.append(f"**{conf:.0f}%**")
    return " · ".join(bits)


def _ranked_line(pick: dict, rank: int, suppress_correlation_note: bool = False) -> str:
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
    # No "vs opponent" — the player + prop is enough; the opponent is visible on
    # PrizePicks when the prop is actually played, so it's redundant width here.
    l1 = f"**{rank}. {pick['player']}**"
    # THE PLAY IS THE HEADLINE — bold AND uppercase so it outranks everything
    # beside it. Projection and confidence are supporting numbers and are left
    # in plain weight; if everything is bold, nothing is.
    play = f"{lean} {pick['line']:g} {_short_prop(pick['prop_type'])}".upper()
    _demon = "😈 DEMON " if pick.get("odds_type") == "demon" else ""
    bits = [f"{LEAN_DOT.get(lean, '⚪')} {_demon}**{play}**"]
    # Every prop — including Player Total Games Won and Fantasy Score — renders the
    # same plain "Proj X · NN%" on the list (2026-07-27, user). PTGW previously got a
    # "Fair line X vs book Y · P(over) Z%" treatment, but that repeated the book line
    # (already shown in the "OVER 5.5 …" headline) and read differently from every
    # other play. The projection is still the median internally; only the label is
    # plain. PTGW projections are whole numbers, so drop the trailing ".0".
    if isinstance(proj, (int, float)):
        _is_ptgw = pick.get("prop_type") == "Player Total Games Won"
        bits.append(f"Proj {proj:g}" if _is_ptgw else f"Proj {proj:.1f}")
    conf = _shown_conf(pick)
    if isinstance(conf, (int, float)):
        bits.append(f"{conf:.0f}%")
    out = l1 + "\n" + " · ".join(bits)
    # Demon: show the boosted line against its standard-line context so nobody
    # mistakes a demon for a normal prop.
    if pick.get("odds_type") == "demon":
        _std = pick.get("standard_line")
        _ctx = (f" (standard {_std:g})" if isinstance(_std, (int, float)) else "")
        out += f"\n😈 _Boosted demon line {pick['line']:g}{_ctx} — over-only_"
    # PTGW: implied match-outcome claim removed 2026-07-26 (user). The slate-
    # correlation caution is REMOVED too (2026-07-29, user) — board rows carry NO
    # ⚠️ cautions. Correlation is still detected and capped internally
    # (PTGW_MAX_PER_BOARD + the ptgw_correlated flag, logged for audits).
    # Total Games projection is anchored to the sharp Sofascore total, so its edge
    # (proj − line) is already a clean "PrizePicks vs book" read. The old
    # "model X vs book Y — anchored to book" divergence caution is REMOVED
    # (2026-07-29, user): it exposed internal model-vs-book plumbing on a
    # member-facing row without changing the play. Divergence is still computed
    # and available (tg_divergent / tg_book_line / tg_model_proj) for logs+audits.
    # Fantasy Score shows NO implied-claim line — it reads like any other prop.
    return out


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
    conf = _shown_conf(pick)
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

    # ── FRESHNESS (2026-09-24, operator) ─────────────────────────────────────
    # Court load, not injury history: how much tennis this player has already
    # played and how hard. A finalist arriving on one day's rest off back-to-back
    # three-setters is a different player from a first-round entrant, and the
    # card never said so.
    #
    # Shown ONLY when it is actually saying something (below 90, i.e. a real
    # grind) or when the opponent is materially more tired — otherwise it is a
    # line reading "100" on every card, which trains people to ignore it.
    # None means we could not judge, and prints nothing rather than "fresh".
    _h, _ho = data.get("health_score"), data.get("opponent_health_score")
    _hb = data.get("health_basis")
    if isinstance(_h, (int, float)):
        _gap = (_h - _ho) if isinstance(_ho, (int, float)) else 0
        if _h < 90 or abs(_gap) >= 15:
            _icon = "🟢" if _h >= 85 else ("🟡" if _h >= 65 else "🔴")
            _line = f"{_icon} Freshness **{_h}**"
            if isinstance(_ho, (int, float)):
                _line += f" · opp {_ho}"
            if _hb:
                _line += f"\n_{_hb}_"
            stats = ((stats + "\n") if stats else "") + _line
    if stats:
        e.add_field(name="Key Stats", value=stats[:1024], inline=False)

    # Dated by the SLATE (when this match plays), not by when it was generated.
    return _stamped_footer(e, when=_slate_date([pick]))


_DESC_LIMIT = 3800        # Discord's description cap is 4096 — leave headroom


def ranked_embeds(ranked: list, start_rank: int = 1, total: int = None,
                  title_override: str = None, suppress_correlation_note: bool = False) -> list:
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

    # v2 tier break: a "— Volume plays —" divider between the conviction tier
    # (>= 80, Pick-of-the-Day-grade) and the volume tier (65–79). Inserted once, at
    # the first sub-80 play, and only when this embed set actually spans both tiers
    # (the ranking is confidence-desc, so all sub-80 plays are contiguous at the
    # bottom). Lets members read conviction vs coverage at a glance.
    _thresh = pick_of_day.POTD_THRESHOLD
    _has_conviction = any(isinstance(p.get("confidence"), (int, float))
                          and p["confidence"] >= _thresh for p in ranked)
    blocks, _divider_placed = [], False
    for i, p in enumerate(ranked, start_rank):
        c = p.get("confidence")
        if (_has_conviction and not _divider_placed
                and isinstance(c, (int, float)) and c < _thresh):
            blocks.append("**— Volume plays (65–79%) —**")
            _divider_placed = True
        blocks.append(_ranked_line(p, i, suppress_correlation_note))

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
        # Named to match the Underdog board exactly ("🎾 M/D Underdog Board") so the
        # two books read as the same kind of post rather than two different things.
        title = title_override or f"🎾 {slate.month}/{slate.day} PrizePicks Board"
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
    if not AUTOPOST_ENABLED:
        # MASTER SWITCH backstop — covers every board-posting caller (daily trigger,
        # extra run, on-start post). No board is generated or posted while off.
        log.info("_post_daily_picks: automated posting DISABLED (AUTOPOST_ENABLED off) "
                 "— not posting the board")
        return "autopost disabled"
    bundle = await pick_of_day.generate_ranked_and_slip()
    ranked = bundle.get("ranked") or []
    slip = bundle.get("slip") or []
    thin_slate = bool(bundle.get("thin_slate"))
    has_star = bool(bundle.get("has_star"))
    log.info("daily picks: board evaluated at trigger time (%d ranked) — "
             "cache pre-warmed at %02d:%02d", len(ranked),
             ONEOFF_PREWARM_HM[0] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
             == ONEOFF_SCHED_DATE else PREWARM_HOUR,
             ONEOFF_PREWARM_HM[1] if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
             == ONEOFF_SCHED_DATE else PREWARM_MINUTE)

    # Never re-post a play that is already live and ungraded (2026-08-02). The
    # 18h dedup in _log_picks_pending stopped a pick being LOGGED twice, but the
    # board could still DISPLAY it again, presenting an open play as new. This
    # bites hardest when matches are postponed: a rain-delayed match slides onto
    # the next card while its pick is still pending. Dropped plays are logged by
    # name so a short board is explainable.
    _open = await _pending_pick_keys()
    if _open:
        _before = len(ranked) + len(slip)
        _drop = [p for p in ranked
                 if (pick_of_day._norm(p.get("player")), p.get("prop_type")) in _open]
        ranked = [p for p in ranked
                  if (pick_of_day._norm(p.get("player")), p.get("prop_type")) not in _open]
        slip = [p for p in slip
                if (pick_of_day._norm(p.get("player")), p.get("prop_type")) not in _open]
        if _drop:
            log.info("daily picks: dropped %d play(s) still awaiting a result: %s "
                     "(%d -> %d posted)", len(_drop),
                     ", ".join(f"{p.get('player')} {p.get('prop_type')}" for p in _drop),
                     _before, len(ranked) + len(slip))
        if ranked:
            ranked, has_star = pick_of_day._promote_star(ranked)
        # A slip is a PAIR. If the filter above took out one leg, what's left is
        # not a 3x — on 9/3 a single leg (Trungelliti) posted under the 3x header.
        # Re-cut from the full evaluated pool, minus the stale plays and minus the
        # ⭐ and its match, exactly as the original cut did. Only if that still
        # can't find two independent legs does the 3x drop for the day.
        if len(slip) < 2:
            _pool = [p for p in (bundle.get("pool") or [])
                     if (pick_of_day._norm(p.get("player")), p.get("prop_type")) not in _open]
            slip = pick_of_day._select_slip(
                _pool, ranked[:1] if (ranked and has_star) else [])
            log.info("daily picks: slip lost a leg to the pending filter — re-cut "
                     "from %d remaining plays -> %d leg(s)", len(_pool), len(slip))

    if not ranked:
        no_play = discord.Embed(description=MSG_NO_PICK_DAILY, color=COLOR_NEUTRAL)
        no_play.set_author(name="🎾 Baseline Ranked Plays")
        await channel.send(embed=no_play)
        return "no qualifying plays — posted no-play notice"

    await _annotate_form_alerts(ranked)

    # ⭐ Pick of the Day gets its OWN embed, posted first, then the ranked board
    # (plays 2..N) below it. Both ride one @everyone message so the headline play
    # and the board arrive together rather than as separate pings.
    # No ⭐ when nothing on the board can carry it (see _promote_star): post the
    # ranked board alone rather than badge a play that failed the eligibility
    # rules. A Pick of the Day is a claim; some boards don't support one.
    # v2 no-POTD fallback: the slot NEVER silently vanishes. When nothing clears
    # the 80% bar we still post the full ranked board, with a standard message in
    # place of the ⭐ embed, and log the miss (date + how close the best star-
    # eligible play came).
    _no_potd_line = None
    if has_star:
        post = [potd_embed(ranked[0])] + ranked_embeds(ranked[1:], start_rank=2,
                                                       total=len(ranked))
    else:
        post = ranked_embeds(ranked, start_rank=1, total=len(ranked))
        _no_potd_line = MSG_NO_POTD_HAS_BOARD
        # "Highest star-eligible confidence" = best non-DF play (DF can't star).
        _star_confs = [p.get("confidence") for p in ranked
                       if p.get("prop_type") not in pick_of_day.POD_STAR_EXCLUDE_PROPS
                       and isinstance(p.get("confidence"), (int, float))]
        _hi = max(_star_confs) if _star_confs else None
        log.info("POD_NO_POTD | %s | no play met the %d%% bar | highest star-eligible "
                 "confidence=%s | board still posted (%d plays)",
                 datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d"),
                 pick_of_day.POTD_THRESHOLD,
                 ("%.0f" % _hi) if _hi is not None else "n/a", len(ranked))
    # Message content: @everyone (when tracking), the no-POTD line, and the thin-
    # slate note — each on its own line, in the order read.
    _content = "@everyone" if track else None
    if _no_potd_line:
        _content = ((_content + "\n") if _content else "") + _no_potd_line
    if thin_slate:
        _content = ((_content + "\n") if _content else "") + pick_of_day.THIN_SLATE_NOTE
    await channel.send(
        content=_content,
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
        # _promote_star put the ⭐ play at ranked[0] when the board has one.
        # Mark it before logging: the record is written once and cannot be
        # reconstructed afterwards, because which play carried the star depends
        # on the whole board as it stood at post time.
        if has_star and ranked:
            ranked[0]["is_potd"] = True
        await _log_picks_pending(ranked, group="potd")

    # SHADOW PROPS — logged, never posted. _rank_board collected these while
    # evaluating the same board, so they cost no extra Sofascore work. They go in
    # as excluded_from_record, so the resolver grades them and record_summary
    # ignores them; after ~40-50 graded picks each, Aces and Double Faults can be
    # judged on out-of-sample evidence instead of the in-sample point estimate
    # that today's walk-forward produced. See SHADOW_PROPS in pick_of_day.
    _shadow = list(getattr(pick_of_day, "LAST_SHADOW_PICKS", []) or [])
    if _shadow:
        log.info("SHADOW | logging %d qualified play(s) not posted: %s",
                 len(_shadow),
                 ", ".join("%s %s" % (s.get("player"), s.get("prop_type"))
                           for s in _shadow[:6]))
        await _log_picks_pending(_shadow, group="shadow", excluded=True)

    # Baseline 3x — a SEPARATE post right after the ranked list.
    # TWO LEGS OR NOTHING. `if slip:` posted a one-leg slip on 9/3 under the 3x
    # header, then raised IndexError on slip[1] in the return line below — so the
    # malformed embed shipped and the handler died after it. A one-leg 3x is not
    # a 3x; if the re-cut above could not find a pair, there is no slip today.
    if len(slip) < 2:
        slip = []
    if slip:
        if track:
            await _log_picks_pending(slip, group="3x")
        await channel.send(
            content=("@everyone" if track else None),
            embed=threex_embed(slip), allowed_mentions=EVERYONE_MENTION)

    if track:
        _start_line_monitor(channel, ranked + slip)   # monitor every play + both legs
    slip_note = (f" + 3x [{', '.join(p['player'] for p in slip)}]"
                 if slip else " (no 3x — thin pool)")
    return f"posted {len(ranked)} ranked, ⭐ {ranked[0]['player']} {ranked[0]['prop_type']}{slip_note}"


async def _todays_posted_keys() -> set:
    """(_norm(player), prop_type) for every pick logged in the last ~18h — the prior
    night's 8 PM board + 3x slip — so the morning second wave never repeats a play
    already on the list. The ~18h window comfortably spans the 8 PM → next-8 AM gap."""
    keys = set()
    try:
        rec = await asyncio.to_thread(results_tracker.get_record)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=18)
        for q in (rec or {}).get("picks", []):
            try:
                dt = datetime.datetime.fromisoformat((q.get("generated_at") or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if dt >= cutoff:
                keys.add((pick_of_day._norm(q.get("player", "")), q.get("prop_type")))
    except Exception:  # noqa: BLE001
        log.exception("second wave: failed to read today's posted keys")
    return keys


# ── Underdog board ──────────────────────────────────────────────────────────
# A SECOND book, scanned and posted on its own schedule at 10:30 PM ET and
# scored separately (pick_group "underdog" -> its own record block). It runs
# 30 minutes after the PrizePicks board so the two posts don't land together.
#
# It reuses pick_of_day._rank_board(props=...) rather than a parallel pipeline,
# so every gate — per-prop confidence bars, thin-slate handling, per-prop board
# caps, tier-aware per-player dedupe, the ranking rule, star eligibility — is
# literally the same code the PrizePicks board runs. The two cannot drift.
UNDERDOG_HOUR = int(os.getenv("UNDERDOG_HOUR", "22") or "22")     # 10:30 PM ET
UNDERDOG_MINUTE = int(os.getenv("UNDERDOG_MINUTE", "30") or "30")

# Underdog pre-warm — 10:15 PM ET, 15 minutes before its board (2026-08-05, user).
# The 9:30 PM daily_cache_prewarm walks the PRIZEPICKS board only, so the Underdog
# board was the one scan that always ran cold: its own prop mix (Break Points
# Saved especially) and any player PrizePicks doesn't list were uncached, so
# generation took the full walk and the post straggled well past 10:30 instead of
# landing with the main drop. This warms the same board it is about to build.
UNDERDOG_PREWARM_HOUR = int(os.getenv("UNDERDOG_PREWARM_HOUR", "22") or "22")
UNDERDOG_PREWARM_MINUTE = int(os.getenv("UNDERDOG_PREWARM_MINUTE", "15") or "15")

# ── Underdog gets its OWN channel (2026-08-09, user) ─────────────────────────
# PrizePicks keeps POD_CHANNEL_ID; Underdog posts here. Hardcoded with an env
# override, the same pattern TRACK_RECORD_CHANNEL_ID uses.
#
# Falls back to POD_CHANNEL_ID if this is ever cleared, so a blanked variable
# degrades to "both books in one channel" rather than to silence — an Underdog
# board that posts nowhere looks identical to a scan that found no plays.
UNDERDOG_CHANNEL_ID = int(os.getenv("UNDERDOG_CHANNEL_ID",
                                    "1536206878856585236") or 0) or POD_CHANNEL_ID

# Second Underdog drop, 7:30 AM ET (2026-08-09, user). The 10:30 PM board is
# built off the lines posted the night before; by morning Underdog has put up the
# rest of the day's card, so a morning scan reaches props that did not exist at
# 10:30 PM. Mirrors the PrizePicks second wave at 8:00 AM.
UNDERDOG_AM_HOUR = int(os.getenv("UNDERDOG_AM_HOUR", "7") or "7")
UNDERDOG_AM_MINUTE = int(os.getenv("UNDERDOG_AM_MINUTE", "30") or "30")


async def _post_underdog_board(channel, track: bool = True,
                               additional: bool = False) -> str:
    """Scan Underdog's board and post it. Never raises.

    `additional` makes it a TOP-UP rather than a second full board — capped at
    SECOND_WAVE_MAX, no ⭐, titled "Underdog Additional Plays". Exactly what
    _post_second_wave does for PrizePicks. Without it the 7:30 AM run posted a
    twelve-play board titled "M/D Underdog Board" with its own star, so one card
    produced two competing boards."""
    if not AUTOPOST_ENABLED:
        log.info("underdog board: automated posting DISABLED (AUTOPOST_ENABLED off)")
        return "autopost disabled"
    # The top-up is the Underdog copy of the PrizePicks second wave, so it falls
    # under the same switch. The FIRST Underdog board of the day is untouched —
    # only the additional-plays pass is suppressed.
    if additional and not SECOND_SCANS_ENABLED:
        log.info("underdog top-up: DISABLED (SECOND_SCANS_ENABLED off)")
        return "second scans disabled"
    props = await asyncio.to_thread(underdog.to_board_props)
    if not props:
        log.info("underdog board: no straight two-way props on the board")
        return "no underdog props"
    ordered, thin = await pick_of_day._rank_board(props=props)
    if not ordered:
        log.info("underdog board: nothing cleared the gating")
        return "no qualifying plays"

    # Same rule as the PrizePicks board: never re-post a play that is already
    # live and ungraded, whichever book it came from.
    _open = await _pending_pick_keys()
    if _open:
        _drop = [p for p in ordered
                 if (pick_of_day._norm(p.get("player")), p.get("prop_type")) in _open]
        ordered = [p for p in ordered
                   if (pick_of_day._norm(p.get("player")), p.get("prop_type")) not in _open]
        if _drop:
            log.info("underdog board: dropped %d play(s) still awaiting a result: %s",
                     len(_drop),
                     ", ".join(f"{p.get('player')} {p.get('prop_type')}" for p in _drop))
    if not ordered:
        log.info("underdog board: every qualifying play is already live")
        return "all plays already open"

    if additional:
        # Top-up: same cap and shape as the PrizePicks second wave. No star —
        # the day has ONE Pick of the Day, from the 10:30 PM board.
        ranked = ordered[:SECOND_WAVE_MAX]
        has_star = False
        await _annotate_form_alerts(ranked)
        embeds = ranked_embeds(ranked, start_rank=1, total=len(ranked),
                               title_override="🎾 Underdog Additional Plays",
                               suppress_correlation_note=True)
    else:
        ranked = ordered[:pick_of_day.MAX_RANKED_PLAYS]
        ranked, has_star = pick_of_day._promote_star(ranked)
        await _annotate_form_alerts(ranked)

        slate = _slate_date(ranked)
        title = f"🎾 {slate.month}/{slate.day} Underdog Board"
        if has_star:
            embeds = [potd_embed(ranked[0])] + ranked_embeds(
                ranked[1:], start_rank=2, total=len(ranked), title_override=title)
        else:
            embeds = ranked_embeds(ranked, start_rank=1, total=len(ranked),
                                   title_override=title)
    await channel.send(content=("@everyone" if track else None),
                       embeds=embeds[:10], allowed_mentions=EVERYONE_MENTION)
    for i in range(10, len(embeds), 10):
        await channel.send(embeds=embeds[i:i + 10])

    # LOG ONLY AFTER A SUCCESSFUL SEND, same rule as the PrizePicks board — an
    # unposted play is not a play. pick_group "underdog" keeps this book's record
    # entirely separate from PrizePicks (see database.pick_source).
    if track:
        if has_star and ranked:
            ranked[0]["is_potd"] = True
        await _log_picks_pending(ranked, group="underdog")
    return "posted %d underdog %s%s" % (
        len(ranked), "additional plays" if additional else "plays",
        " (with ⭐)" if has_star else "")


# ══════════════════════════════════════════════════════════════════════════════
# MLB — a SEPARATE SPORT, fully isolated (NORTH_STAR rules 1 & 2)
# ══════════════════════════════════════════════════════════════════════════════
# These tasks are ADDITIVE. They do not read, call or modify a single tennis
# function, and every one of them imports `mlb` lazily INSIDE its own try/except
# so that even an ImportError cannot escape into the tennis loops. If MLB breaks
# entirely, tennis does not notice.
#
# MLB posts to its own hidden channels and writes to its own Postgres
# (MLB_DATABASE_URL, a different database in a different Railway project from
# tennis). It never touches the tennis record.
MLB_TASKS_ENABLED = os.getenv("MLB_TASKS_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on")


def _mlb_import(module_name: str):
    """Import an `mlb.*` submodule, locating the package first.

    The container layout is NOT guaranteed. start.sh handles both
    /app/discord-bot/bot.py (build root = repo root) and /app/bot.py (build root =
    discord-bot/), and in the second case the repo-root `mlb` and `core` packages
    are not deployed with the bot at all. The first attempt at this assumed the
    former and produced ModuleNotFoundError, swallowed by the caller's error
    boundary — MLB silently never ran.

    So: try every plausible root, and if the package genuinely is not on disk say
    so LOUDLY with the paths searched, because "not deployed" and "wrong path" need
    different fixes and are indistinguishable from a bare ImportError.
    """
    import sys
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # backend/ FIRST — the sport packages live there now, so both the bot
        # and the API import one copy instead of the backend being unable to
        # reach them at all.
        os.path.join(os.path.dirname(here), "backend"),
        os.path.dirname(here),          # /app  when bot.py is /app/discord-bot/bot.py
        here,                           # /app  when bot.py is /app/bot.py
        os.path.join(os.getcwd(), "backend"),
        os.getcwd(),
        "/app/backend",
        "/app",
    ]
    seen, found = [], None
    for root in candidates:
        if not root or root in seen:
            continue
        seen.append(root)
        if os.path.isdir(os.path.join(root, "mlb")):
            found = root
            break
    if found:
        if found not in sys.path:
            sys.path.insert(0, found)
    else:
        log.error("MLB package NOT FOUND on disk. Searched: %s. Contents of %s: %s. "
                  "This means mlb/ was not deployed with the bot — check the Railway "
                  "service Root Directory (it must be the repo root, not discord-bot/).",
                  seen, here, sorted(os.listdir(here))[:25])
    return importlib.import_module(module_name)
# ── TWO MLB BOARDS PER CARD (2026-08-09, user) ───────────────────────────────
# 11:30 PM ET is the primary: both books put the next day's lines up late in the
# evening, so this is the first moment a full card is priced. It necessarily
# boards TOMORROW — every game "today" is already final at 11:30 PM — which is
# why both tasks go through _mlb_target_slate() instead of defaulting to today.
#
# 9:00 AM ET is the follow-up, and it posts ONLY what the night scan could not:
# starters announced overnight and lines the books had not put up yet. run_daily
# excludes anything already on that slate's board, so the two never duplicate.
MLB_BOARD_HOUR = int(os.getenv("MLB_BOARD_HOUR", "23") or "23")
MLB_BOARD_MINUTE = int(os.getenv("MLB_BOARD_MINUTE", "30") or "30")
MLB_BOARD2_HOUR = int(os.getenv("MLB_BOARD2_HOUR", "9") or "9")
MLB_BOARD2_MINUTE = int(os.getenv("MLB_BOARD2_MINUTE", "0") or "0")

MLB_RESOLVE_EVERY_HOURS = int(os.getenv("MLB_RESOLVE_EVERY_HOURS", "2") or "2")

# ── TWO BOARDS, BECAUSE THE INPUTS ARRIVE AT DIFFERENT TIMES ─────────────────
# Pitcher props need only the probable starters, which are announced the night
# before — so they post in the morning.
#
# Batter props need a POSTED LINEUP, and lineups do not exist at 9am. A batter
# board at the morning slot is not merely weaker, it is empty: the scan withholds
# every batter prop because an unconfirmed hitter may be rested, and a rested
# hitter's prop voids rather than losing. Teams post lineups roughly three hours
# before first pitch, so the afternoon slot is the earliest one that has them for
# the evening games that make up most of a slate.
MLB_BATTER_BOARD_HOUR = int(os.getenv("MLB_BATTER_BOARD_HOUR", "16") or "16")
MLB_BATTER_BOARD_MINUTE = int(os.getenv("MLB_BATTER_BOARD_MINUTE", "30") or "30")
# Off by default: batter props are new and unproven, and Rule 4 says a new prop
# ships dark until it has been reviewed. Set MLB_BATTER_BOARD=true to enable.
MLB_BATTER_BOARD = os.getenv("MLB_BATTER_BOARD", "false").strip().lower() in (
    "1", "true", "yes", "on")


async def _mlb_run_boards(label: str, additional: bool = False) -> None:
    """Post both books' PITCHER boards for the right slate. Never raises.

    Shared by the 11:30 PM and 9:00 AM triggers so the two can never drift in
    what they do — only in when they run and, with `additional`, in whether the
    result is a full board or a top-up.
    """
    if not MLB_TASKS_ENABLED:
        return
    try:
        mlb_board = _mlb_import("mlb.board")
        slate = _mlb_target_slate()
        for book in ("prizepicks", "underdog"):
            try:
                res = await asyncio.to_thread(mlb_board.run_daily, book,
                                              slate, None, True, "pitcher",
                                              True, additional)
                log.info("MLB %s board (%s) slate=%s: %s", label, book, slate, res)
            except Exception:  # noqa: BLE001 — one book must not stop the other
                log.exception("MLB %s board failed for %s", label, book)
    except Exception:  # noqa: BLE001 — MLB must never reach tennis
        log.exception("MLB %s board task failed entirely (tennis unaffected)",
                      label)


@tasks.loop(time=[datetime.time(hour=MLB_BOARD_HOUR, minute=MLB_BOARD_MINUTE,
                                tzinfo=POD_TZINFO)])
async def mlb_daily_boards():
    """Primary MLB board — 11:30 PM ET, on the next day's card."""
    await _mlb_run_boards("primary")


@mlb_daily_boards.before_loop
async def _before_mlb_boards():
    await client.wait_until_ready()


# ── MLB line movement — one channel, BOTH books, every alert labelled ────────
# PrizePicks and Underdog post different lines on the same pitcher and share
# this channel, so an unlabelled alert is a real number attached to a market it
# may not belong to. mlb/line_monitor.py groups every alert under its book.
MLB_LINE_CHECK_MINUTES = int(os.getenv("MLB_LINE_CHECK_MINUTES", "30") or "30")


@tasks.loop(minutes=MLB_LINE_CHECK_MINUTES)
async def mlb_line_watch():
    """Re-check both books' lines against every unsettled MLB play."""
    if not MLB_TASKS_ENABLED:
        return
    try:
        mlb_lines = _mlb_import("mlb.line_monitor")
        res = await asyncio.to_thread(mlb_lines.check_and_post)
        if res.get("moves"):
            log.info("MLB line movement: %s", res)
    except Exception:  # noqa: BLE001 — must never reach tennis
        log.exception("MLB line watch failed (tennis unaffected)")


@mlb_line_watch.before_loop
async def _before_mlb_line_watch():
    await client.wait_until_ready()


# ══════════════════════════════════════════════════════════════════════════════
# MLB slash commands — the /prop equivalent, in the MLB projections channel
# ══════════════════════════════════════════════════════════════════════════════
# Same shape as the tennis commands: choices for the prop, autocomplete on the
# player, ephemeral reply, cooldown, and every failure returned as a specific
# message rather than a generic error. Every MLB import is lazy and inside a
# try/except, so a broken MLB module cannot take a tennis command down with it.
MLB_PROJECTIONS_CHANNEL_ID = int(
    os.getenv("MLB_PROJECTIONS_CHANNEL_ID", "1536401640180154448") or 0)

# Built once at import from the MLB module's own list, so adding a prop engine
# there puts it in the picker without touching this file. Falls back to an empty
# list if MLB is unavailable — the command then reports that plainly instead of
# failing to register and taking the whole command tree with it.
try:
    _mlb_proj_mod = _mlb_import("mlb.projections")
    MLB_PROP_CHOICES = [app_commands.Choice(name=label, value=value)
                        for value, label in _mlb_proj_mod.ALL_PROPS][:25]
except Exception:  # noqa: BLE001
    log.exception("MLB projections module unavailable — /mlbprop will report it")
    MLB_PROP_CHOICES = [app_commands.Choice(name="Pitcher Strikeouts",
                                            value="strikeouts")]


async def mlb_player_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest MLB players by name. Never raises — an autocomplete that throws
    leaves the user staring at a spinner."""
    try:
        if len((current or "").strip()) < 2:
            return []
        mod = _mlb_import("mlb.projections")
        hits = await asyncio.to_thread(mod.search_players, current, 20)
        out = []
        for h in hits[:25]:
            if not h.get("name"):
                continue
            # Position and team disambiguate the several players who share a
            # name — there are two Luis Garcías, and picking the wrong one
            # silently projects the wrong player.
            extra = " · ".join(x for x in (h.get("position"), h.get("team")) if x)
            label = f"{h['name']}" + (f" ({extra})" if extra else "")
            out.append(app_commands.Choice(name=label[:100], value=h["name"][:100]))
        return out
    except Exception:  # noqa: BLE001
        log.exception("mlb autocomplete failed")
        return []


def _mlb_prop_embed(r: dict) -> discord.Embed:
    """Result embed for /mlbprop. Mirrors the MLB board's own wording so a
    command answer and a posted play read the same."""
    lean = (r.get("lean") or "NEUTRAL").upper()
    side = r.get("p_over") if lean == "OVER" else r.get("p_under")
    color = (COLOR_OVER if lean == "OVER"
             else COLOR_UNDER if lean == "UNDER" else COLOR_NEUTRAL)
    dot = "🟢" if lean == "OVER" else "🔴" if lean == "UNDER" else "⚪"
    proj = r.get("projection")
    line = r.get("line")

    head = f"**{r.get('player')}**"
    if r.get("opponent"):
        head += f" vs {r['opponent']}"
    parts = [head]
    if line is not None:
        parts.append(f"{dot} **{lean} {line:g} {r.get('prop_label','').upper()}**")
        conf = f" · **{side * 100:.0f}%** confidence" if isinstance(side, (int, float)) else ""
        parts.append(f"Projected **{proj:.2f}**{conf}")
    else:
        parts.append(f"Projected **{proj:.2f}** {r.get('prop_label')}")
        parts.append("_No line given — add one for a lean and a confidence._")

    why = []
    if isinstance(r.get("expected_bf"), (int, float)):
        why.append(f"{r['expected_bf']:.0f} batters faced/start")
    if isinstance(r.get("pa_per_game"), (int, float)):
        why.append(f"{r['pa_per_game']:.1f} PA/game")
    n = r.get("starts_in_window") or r.get("games_in_window")
    if n:
        why.append(f"{n} {'starts' if r.get('starts_in_window') else 'games'}")
    if why:
        parts.append("_" + " · ".join(why) + "_")

    # Say when a term is MISSING rather than letting the number imply it was
    # included. Only meaningful for pitcher props — the batter engine has no
    # opponent term at all, so flagging it there would invent a caveat.
    if r.get("is_pitcher_prop") and not r.get("opponent_known"):
        parts.append("⚠️ _No scheduled start found in the next two days — "
                     "projected without an opponent adjustment._")
    if r.get("teammate_dependent"):
        parts.append("⚠️ _Runs and RBIs depend heavily on the teammates around "
                     "him, so this is a weaker estimate than a hit rate._")

    e = discord.Embed(description="\n".join(parts), color=color)
    e.set_footer(text=FOOTER_PROJECTION)
    return e


@client.tree.command(name="mlbprop",
                     description="Get a Baseline MLB prop projection")
@app_commands.describe(
    player="Pitcher or batter — type to search",
    prop="Which prop to project",
    line="The book line (e.g. 5.5). Optional — omit for a raw projection.",
)
@app_commands.choices(prop=MLB_PROP_CHOICES)
@app_commands.autocomplete(player=mlb_player_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def mlbprop(interaction: discord.Interaction, player: str,
                  prop: app_commands.Choice[str], line: float = None):
    if (MLB_PROJECTIONS_CHANNEL_ID
            and interaction.channel_id != MLB_PROJECTIONS_CHANNEL_ID):
        await _send_error(interaction,
                          f"Use MLB commands in <#{MLB_PROJECTIONS_CHANNEL_ID}>.")
        return
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    log.info("CMD /mlbprop | user=%s | %s | %s | line=%s",
             interaction.user.id, player, prop.value, line)
    try:
        mod = _mlb_import("mlb.projections")
        r = await asyncio.to_thread(mod.project, player, prop.value, line)
        if not r.get("ok"):
            await _send_error(interaction, r.get("error") or MSG_GENERIC)
            return
        await interaction.followup.send(embed=_mlb_prop_embed(r), ephemeral=True)
    except Exception:  # noqa: BLE001 — never let a command crash the process
        log.exception("UNHANDLED /mlbprop error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@tasks.loop(time=[datetime.time(hour=MLB_BATTER_BOARD_HOUR,
                                minute=MLB_BATTER_BOARD_MINUTE,
                                tzinfo=POD_TZINFO)])
async def mlb_batter_boards():
    """Post both books' BATTER boards once lineups are out. Shadow channels.

    Separate loop rather than a second call inside mlb_daily_boards: the two
    boards run at different times, and a failure in one must not affect the
    other. Games whose lineup still is not posted are simply absent — the scan
    withholds them rather than guessing that a regular is playing.
    """
    if not (MLB_TASKS_ENABLED and MLB_BATTER_BOARD):
        return
    try:
        mlb_board = _mlb_import("mlb.board")
        for book in ("prizepicks", "underdog"):
            try:
                res = await asyncio.to_thread(mlb_board.run_daily, book,
                                              None, None, True, "batter")
                log.info("MLB batter board (%s): %s", book, res)
            except Exception:  # noqa: BLE001
                log.exception("MLB batter board failed for %s", book)
    except Exception:  # noqa: BLE001
        log.exception("MLB batter board task failed entirely (tennis unaffected)")


@mlb_batter_boards.before_loop
async def _before_mlb_batter_boards():
    await client.wait_until_ready()


@tasks.loop(hours=MLB_RESOLVE_EVERY_HOURS)
async def mlb_resolve_and_recap():
    """Grade MLB picks, then post each book's recap once its slate is settled.

    Entirely separate from daily_resolve_results — different store, different
    channels, different resolver. A tennis recap cannot be affected by this.
    """
    if not MLB_TASKS_ENABLED:
        return
    try:
        mlb_recap = _mlb_import("mlb.recap")
        for book in ("prizepicks", "underdog"):
            try:
                graded = await asyncio.to_thread(mlb_recap.resolve_pending, book)
                log.info("MLB resolve (%s): %s", book, graded)
                # post_ready_recap, NOT post_recap: the latter defaults to
                # TODAY's slate, so a recap held past midnight became
                # unreachable — the board it wanted was yesterday's and "today"
                # had no board at all. This picks the oldest settled, unposted
                # slate in the last few days.
                res = await asyncio.to_thread(mlb_recap.post_ready_recap, book)
                if res.get("ok"):
                    log.info("MLB recap posted (%s)", book)
                else:
                    log.info("MLB recap held (%s): %s", book, res.get("reason"))
            except Exception:  # noqa: BLE001
                log.exception("MLB resolve/recap failed for %s", book)
    except Exception:  # noqa: BLE001
        log.exception("MLB resolve task failed entirely (tennis unaffected)")


@mlb_resolve_and_recap.before_loop
async def _before_mlb_resolve():
    await client.wait_until_ready()


# ── ONE-SHOT TEST PATH (env-gated, off by default) ───────────────────────────
# MLB_TEST_RUN=1 makes the bot, ONCE at startup: post both boards, force-grade
# every pending MLB pick with MLB_TEST_VALUE, and post both recaps. It exists to
# exercise store -> resolve -> recap before real games settle.
#
# The results it writes are FABRICATED. It is off unless explicitly set, it only
# ever touches the shadow MLB database, and it logs at WARNING so it can never be
# mistaken for a real grading pass. Unset MLB_TEST_RUN after testing.
MLB_TEST_RUN = os.getenv("MLB_TEST_RUN", "").strip().lower() in (
    "1", "true", "yes", "on")
MLB_TEST_VALUE = float(os.getenv("MLB_TEST_VALUE", "1") or 1)

# MLB_PURGE_SLATE=YYYY-MM-DD deletes every stored MLB row for that slate, ONCE at
# startup. It exists to clear fabricated test data before it can pollute a real
# record. Targets an explicit date rather than guessing which rows look fake —
# a heuristic would eventually delete a real pick. Unset it after use.
MLB_PURGE_SLATE = (os.getenv("MLB_PURGE_SLATE", "") or "").strip()

# ── MLB_RUN_NOW: post the boards once, right now ─────────────────────────────
# Distinct from MLB_TEST_RUN, which force-grades every pending pick with a
# FABRICATED value and posts recaps. This one only scans and posts a board —
# no grading, no recap, no invented results. It is what you want when the books
# have just put a slate up and you want to see the board before the 9am loop.
#
# MLB_RUN_NOW_ONLY=pitcher|batter restricts the prop family.
# MLB_RUN_NOW_DATE=YYYY-MM-DD targets an explicit slate.
MLB_RUN_NOW = os.getenv("MLB_RUN_NOW", "").strip().lower() in (
    "1", "true", "yes", "on")
MLB_RUN_NOW_ONLY = (os.getenv("MLB_RUN_NOW_ONLY", "") or "").strip() or None
MLB_RUN_NOW_DATE = (os.getenv("MLB_RUN_NOW_DATE", "") or "").strip() or None


def _mlb_target_slate():
    """Which slate a manual run should board.

    Rolls to TOMORROW when every game today has already started. Both books put
    the next day's lines up late in the evening, so a manual run at 11pm means
    "board the slate that just went up", not "board the fifteen games that are
    already final". The scheduled 9am loop is unaffected — at 9am today's games
    have not started, so this returns today.
    """
    try:
        mlb_client = _mlb_import("mlb.client")
        import datetime as _d
        et = _d.datetime.now(POD_TZINFO)
        today = et.strftime("%Y-%m-%d")
        games = mlb_client.get_schedule(today)
        if games and not any(g.get("abstract_state") in (None, "Preview")
                             for g in games):
            tomorrow = (et + _d.timedelta(days=1)).strftime("%Y-%m-%d")
            log.warning("MLB RUN NOW: all %d game(s) on %s have started — "
                        "boarding %s instead", len(games), today, tomorrow)
            return tomorrow
        return today
    except Exception:  # noqa: BLE001
        log.exception("MLB slate selection failed; using default")
        return None


async def _mlb_run_now():
    """Runs once at startup when MLB_RUN_NOW is set. Boards only. Never raises."""
    try:
        mlb_board = _mlb_import("mlb.board")
        slate = MLB_RUN_NOW_DATE or _mlb_target_slate()
        log.warning("MLB RUN NOW: boarding slate=%s only=%s (no grading, "
                    "no recap)", slate, MLB_RUN_NOW_ONLY or "all props")
        for book in ("prizepicks", "underdog"):
            try:
                res = await asyncio.to_thread(mlb_board.run_daily, book, slate,
                                              None, True, MLB_RUN_NOW_ONLY)
                log.warning("MLB RUN NOW board (%s): %s", book, res)
            except Exception:  # noqa: BLE001 — one book must not stop the other
                log.exception("MLB RUN NOW failed for %s", book)
    except Exception:  # noqa: BLE001
        log.exception("MLB RUN NOW failed entirely (tennis unaffected)")


async def _mlb_purge_once():
    """Runs once at startup when MLB_PURGE_SLATE is set. Never raises."""
    try:
        mlb_store = _mlb_import("mlb.store")
        n = await asyncio.to_thread(mlb_store.purge_slate, MLB_PURGE_SLATE)
        log.warning("MLB PURGE: removed %d row(s) for slate %s", n, MLB_PURGE_SLATE)
    except Exception:  # noqa: BLE001
        log.exception("MLB purge failed (tennis unaffected)")


# MLB_PURGE_ALL wipes the ENTIRE MLB record — every board, every graded result,
# and the rolling 30-day figures derived from them. Irreversible, so it takes the
# literal phrase rather than a truthy value: a stray "true" on the wrong variable
# should not be able to erase a season. Unset it immediately after it fires, or
# every restart re-wipes.
MLB_PURGE_ALL = (os.getenv("MLB_PURGE_ALL", "") or "").strip()


# MLB_STORE_NOW persists the current board WITHOUT posting it.
#
# Exists because `railway run` executes LOCALLY with injected variables: it can
# post (Discord is a public API) but cannot reach postgres.railway.internal, so
# a manual board lands in the channel with stored=0 — visible, ungradeable, and
# absent from every future recap. This runs the same scan inside Railway, where
# the internal host resolves, and writes the rows the board is showing.
#
# Set to a slate date (YYYY-MM-DD) or "1" for the auto-selected slate. It
# re-scans, so lines may have moved slightly since the post. Unset after use.
MLB_STORE_NOW = (os.getenv("MLB_STORE_NOW", "") or "").strip()

# MLB_REGRADE_SLATE=YYYY-MM-DD[,YYYY-MM-DD...] clears the RESULT on every graded
# row for those slates so they re-settle against final stats. The picks are
# untouched — only W/L/PUSH/VOID and the settled value are cleared.
#
# For repairing results graded before the finality gate existed, when a pick
# checked mid-game was settled on a partial line that looked exactly like a
# final one. Unset after use.
MLB_REGRADE_SLATE = (os.getenv("MLB_REGRADE_SLATE", "") or "").strip()

# MLB_DEDUPE_RECORD removes stored plays the current dedupe rules would never
# have posted — the same pitcher on two props across the night board and the
# morning top-up. One start counted twice inflates the sample and correlates the
# record with itself.
#   "dry"    report what would go, change nothing
#   "apply"  actually delete
MLB_DEDUPE_RECORD = (os.getenv("MLB_DEDUPE_RECORD", "") or "").strip().lower()


async def _mlb_dedupe_record_once():
    """Runs once at startup when MLB_DEDUPE_RECORD is set. Never raises."""
    try:
        mlb_store = _mlb_import("mlb.store")
        apply = MLB_DEDUPE_RECORD in ("apply", "1", "true", "yes", "on")
        res = await asyncio.to_thread(mlb_store.dedupe_record, None, None,
                                      not apply)
        for line in (res.get("details") or []):
            log.warning("MLB DEDUPE %s | %s", "REMOVE" if apply else "would remove",
                        line)
        log.warning("MLB DEDUPE RECORD (%s): examined=%s removed=%s kept=%s",
                    "APPLIED" if apply else "DRY RUN",
                    res.get("examined"), res.get("removed"), res.get("kept"))
        if apply:
            for book in ("prizepicks", "underdog"):
                rec = await asyncio.to_thread(mlb_store.record, book, 30)
                log.warning("MLB DEDUPE record after (%s): %s", book, rec)
    except Exception:  # noqa: BLE001
        log.exception("MLB dedupe-record failed (tennis unaffected)")


async def _mlb_regrade_once():
    """Runs once at startup when MLB_REGRADE_SLATE is set. Never raises."""
    try:
        mlb_store = _mlb_import("mlb.store")
        mlb_recap = _mlb_import("mlb.recap")
        for slate in [d.strip() for d in MLB_REGRADE_SLATE.split(",") if d.strip()]:
            n = await asyncio.to_thread(mlb_store.reset_slate, slate)
            log.warning("MLB REGRADE: reset %d graded row(s) on %s", n, slate)
        for book in ("prizepicks", "underdog"):
            res = await asyncio.to_thread(mlb_recap.resolve_pending, book)
            log.warning("MLB REGRADE re-settle (%s): %s", book, res)
        # Report the corrected record per book AND per slate, so the effect of
        # the re-grade is visible rather than having to be inferred from counts.
        for book in ("prizepicks", "underdog"):
            rec = await asyncio.to_thread(mlb_store.record, book, 30)
            log.warning("MLB REGRADE record (%s): %s", book, rec)
        summ = await asyncio.to_thread(mlb_store.summary)
        log.warning("MLB REGRADE summary: %s", summ)
    except Exception:  # noqa: BLE001
        log.exception("MLB regrade failed (tennis unaffected)")


async def _mlb_store_now_once():
    """Runs once at startup when MLB_STORE_NOW is set. Stores, never posts."""
    try:
        mlb_board = _mlb_import("mlb.board")
        slate = (MLB_STORE_NOW if MLB_STORE_NOW not in ("1", "true", "yes", "on")
                 else _mlb_target_slate())
        log.warning("MLB STORE NOW: persisting slate=%s without posting", slate)
        for book in ("prizepicks", "underdog"):
            try:
                # post_it=False -> run_daily skips the send and goes straight to
                # log_board. exclude_posted=False so it stores the whole board,
                # not just what a previous run missed.
                res = await asyncio.to_thread(
                    mlb_board.run_daily, book, slate, None, False, "pitcher",
                    False)
                log.warning("MLB STORE NOW (%s): %s", book, res)
            except Exception:  # noqa: BLE001
                log.exception("MLB STORE NOW failed for %s", book)
    except Exception:  # noqa: BLE001
        log.exception("MLB STORE NOW failed entirely (tennis unaffected)")


async def _mlb_purge_all_once():
    """Runs once at startup when MLB_PURGE_ALL holds the confirmation phrase.

    Logs the record's contents BEFORE deleting, so the wipe leaves evidence of
    what it removed rather than only a count.
    """
    try:
        mlb_store = _mlb_import("mlb.store")
        before = await asyncio.to_thread(mlb_store.summary)
        log.warning("MLB PURGE ALL: record before wipe = %s", before)
        n = await asyncio.to_thread(mlb_store.purge_all, MLB_PURGE_ALL)
        after = await asyncio.to_thread(mlb_store.summary)
        log.warning("MLB PURGE ALL: deleted %d row(s); record after = %s",
                    n, after)
    except Exception:  # noqa: BLE001
        log.exception("MLB purge-all failed (tennis unaffected)")


async def _mlb_one_shot_test():
    """Runs once at startup when MLB_TEST_RUN is set. Never raises."""
    try:
        mlb_board = _mlb_import("mlb.board")
        mlb_recap = _mlb_import("mlb.recap")
        log.warning("MLB TEST RUN starting — results will be FABRICATED "
                    "(value=%s), shadow database only", MLB_TEST_VALUE)
        for book in ("prizepicks", "underdog"):
            try:
                res = await asyncio.to_thread(mlb_board.run_daily, book)
                log.warning("MLB TEST board (%s): %s", book, res)
                forced = await asyncio.to_thread(
                    mlb_recap.force_resolve_all, book, MLB_TEST_VALUE)
                log.warning("MLB TEST force-resolve (%s): %s", book, forced)
                rec = await asyncio.to_thread(mlb_recap.post_recap, book)
                log.warning("MLB TEST recap (%s): %s", book, rec)
            except Exception:  # noqa: BLE001
                log.exception("MLB TEST failed for %s", book)
        # Report the outcome INTO the recap channel. Without this a store failure
        # is only visible in Railway logs, and "nothing posted" is indistinguish-
        # able from "never ran".
        try:
            mlb_store = _mlb_import("mlb.store")
            mlb_post = _mlb_import("mlb.post")
            import requests as _rq
            status = ("store CONNECTED" if mlb_store.available()
                      else "store UNAVAILABLE (MLB_DATABASE_URL not resolving)")
            for book in ("prizepicks", "underdog"):
                cid = mlb_post.channel_for(book, "recap")
                if not cid:
                    continue
                _rq.post(
                    f"https://discord.com/api/v10/channels/{cid}/messages",
                    headers={"Authorization":
                             f"Bot {os.getenv('DISCORD_BOT_TOKEN','')}",
                             "Content-Type": "application/json"},
                    json={"content": f"🔧 MLB test diagnostic — {status}",
                          "allowed_mentions": {"parse": []}}, timeout=20)
        except Exception:  # noqa: BLE001
            log.exception("MLB TEST diagnostic post failed")
    except Exception:  # noqa: BLE001
        log.exception("MLB TEST run failed entirely (tennis unaffected)")


# ══════════════════════════════════════════════════════════════════════════════
# NFL — boards, projections channel, line alerts
# ══════════════════════════════════════════════════════════════════════════════
# Wired 2026-09-08 to the four channels the user supplied. Mirrors the MLB block
# above in every structural way: lazy imports behind a locator, every task in its
# own error boundary, and nothing here can reach tennis.
#
# READ THIS BEFORE FLIPPING NFL_ENABLED
# -------------------------------------
# The projection engine is sound IN-SEASON — the 2025 walk-forward backtest puts
# it at -2.16 yards of bias on receiving yards (n=1552) and it beats both the
# season-average and last-4-game baselines on every prop. But it is BLIND in
# week 1, when nflverse has published no current-season performance data and
# every projection runs on last season's usage. Measured against the live
# 2026-09-08 PrizePicks board that produced +25.3% mean relative error,
# concentrated entirely on players whose role changed (Isaiah Williams, WR4 on
# the 2026 Jets, projected 31.0 receiving yards into an 8.5 line).
#
# nfl.board therefore holds back any play with no current-season games behind
# it, so in week 1 these boards post NOTHING and say why in the log. That is the
# intended behaviour, not a failure to configure. The board fills itself as real
# games are played — no switch to flip.
NFL_TASKS_ENABLED = os.getenv("NFL_TASKS_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on")
NFL_BOARD_HOUR = int(os.getenv("NFL_BOARD_HOUR", "22") or "22")
NFL_BOARD_MINUTE = int(os.getenv("NFL_BOARD_MINUTE", "0") or "0")

# ── THE NFL BOARD POSTS THE WAY THE TENNIS BOARD POSTS ─────────────
# (user, 2026-09-19, before the board went public.) Four things were different,
# and all four were visible to a reader:
#
#   1. TWO @everyone pings per drop — the ⭐ and the board went out as separate
#      messages, each mentioning everyone. Tennis carries both embeds in ONE
#      message so a drop is one notification.
#   2. NO BAR ON THE ⭐. rows[0] was badged Pick of the Day whatever it scored,
#      including a play sitting on the 0.52 board floor. Tennis stars nothing
#      below POTD_THRESHOLD and says so when no play clears it.
#   3. NINE PLAYS (⭐ + 8) against tennis's five.
#   4. NO MASTER SWITCH. AUTOPOST_ENABLED silenced tennis and the recap but not
#      this, so turning posting off left the NFL board talking.
NFL_MAX_PLAYS = int(os.getenv("NFL_MAX_PLAYS", "5") or "5")
# The same 80 tennis uses, on the same 0-100 scale. Reachable: the 9/19
# prizepicks scan had nine rows at or above it, topping out at 0.872.
NFL_POTD_MIN_PROB = float(os.getenv("NFL_POTD_MIN_PROB", "0.80") or "0.80")


def _nfl_win_prob(row: dict):
    """The model's probability for the side we lean, or None.

    `win_prob` is what nfl.board filters on; p_over/p_under are what the embeds
    read. Both are present on a scanned row — prefer the first and fall back, so
    a row shaped by an older scan still gets gated rather than waved through.
    """
    v = row.get("win_prob")
    if not isinstance(v, (int, float)):
        lean = (row.get("lean") or "").upper()
        v = row.get("p_over") if lean == "OVER" else row.get("p_under")
    return v if isinstance(v, (int, float)) else None

# ── THE NIGHT BEFORE, NOT THE MORNING OF ───────────────────────────
# Each board posts at NFL_BOARD_HOUR ET on the EVE of its slate (operator,
# 2026-09-18), so the card is up the night before people need it:
#
#     Wednesday 22:00 ET  ->  Thursday's game    titled "9/25 TNF"
#     Saturday  22:00 ET  ->  Sunday's slate     titled "9/28 Sunday Slate"
#     Sunday    22:00 ET  ->  Monday's game      titled "9/29 MNF"
#
# The map is POSTING weekday -> the tag its target carries; the target date is
# always the following day, so nothing has to name a slate or count a gap.
# Mon=0 ... Sun=6.
_NFL_EVE_SCHEDULE = {
    2: "TNF",             # Wednesday night -> Thursday
    5: "Sunday Slate",    # Saturday night  -> Sunday
    6: "MNF",             # Sunday night    -> Monday
}


def _nfl_eve_schedule() -> dict:
    """{posting weekday: title tag}. NFL_BOARD_EVES overrides, as "2:TNF,6:MNF"."""
    raw = (os.getenv("NFL_BOARD_EVES", "") or "").strip()
    if not raw:
        return dict(_NFL_EVE_SCHEDULE)
    out = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        d, _, tag = part.partition(":")
        d, tag = d.strip(), tag.strip()
        if d.isdigit() and 0 <= int(d) <= 6 and tag:
            out[int(d)] = tag
    return out or dict(_NFL_EVE_SCHEDULE)


NFL_BOARD_EVES = _nfl_eve_schedule()
NFL_LINE_CHECK_MINUTES = int(os.getenv("NFL_LINE_CHECK_MINUTES", "30") or "30")
# One-shot intro post to the projections channel. Off by default so a redeploy
# never re-pings the channel; set NFL_POST_INTRO=1 for the single announcement,
# then unset it.
NFL_POST_INTRO = (os.getenv("NFL_POST_INTRO", "0") or "0") not in ("0", "false", "False")
_nfl_intro_done = False

# Rows currently being watched for line movement, per book. Written by the board
# task, read by the line watch — so an alert can only ever be about a play that
# was actually posted.
_NFL_WATCH = {}


def _nfl_import(module_name: str):
    """Import an `nfl.*` submodule, locating the package first.

    Identical problem and identical fix to _mlb_import — the container layout is
    not guaranteed, and a bare ImportError swallowed by an error boundary is how
    a sport silently never runs.
    """
    import sys
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    _repo = os.path.dirname(here)
    for root in (os.path.join(_repo, "backend"), _repo, here,
                 os.path.join(os.getcwd(), "backend"), os.getcwd(),
                 "/app/backend", "/app"):
        if root and os.path.isdir(os.path.join(root, "nfl")):
            if root not in sys.path:
                sys.path.insert(0, root)
            break
    else:
        log.error("NFL package NOT FOUND on disk (searched backend/, %s, %s, "
                  "%s, /app) — NFL tasks will report this rather than failing "
                  "silently", _repo, here, os.getcwd())
    return importlib.import_module(module_name)


async def _nfl_post_board(book: str, day=None, window_days: int = None,
                          label: str = "", title_tag: str = "") -> None:
    """Scan one book for a SLATE and post it. Never raises.

    `day` is the ET game date this board is about, which is not the day it is
    posted: the Friday board is about Sunday and the Sunday-night board is
    about Monday. Passing it explicitly is what keeps those straight — and what
    lets the repeat guard know a Sunday play was already shown on Friday.
    """
    if not AUTOPOST_ENABLED:
        # MASTER SWITCH backstop, the same one _post_daily_picks carries. It
        # used to cover tennis and the recap but not this, so flipping posting
        # off left the NFL board announcing itself to the server.
        log.info("NFL board (%s): automated posting DISABLED "
                 "(AUTOPOST_ENABLED off) — not scanning or posting", book)
        return
    try:
        nb = _nfl_import("nfl.board")
        npost = _nfl_import("nfl.post")
        shadow = not getattr(_nfl_import("nfl"), "NFL_ENABLED", False)
        day = day or nb.slate_date()
        rows = await asyncio.to_thread(nb.scan_board, book, None, True, True,
                                       day, window_days)
        if not rows:
            log.warning("NFL %sboard (%s): nothing cleared the filters for %s "
                        "— not posting. Early in the season this is expected: "
                        "every play is held back for having no current-season "
                        "data.", label, book, day)
            return
        cid = npost.channel_for("board", book)
        if not cid:
            log.error("NFL board (%s): no channel configured", book)
            return
        ch = client.get_channel(cid)
        if ch is None:
            log.error("NFL board (%s): channel %s not visible to the bot", book, cid)
            return
        # ⭐ POTD first, board from #2 — see nfl.post.build_potd_embed.
        # TITLED BY THE GAMES, NOT BY THE CLOCK. Both embeds defaulted to
        # datetime.now(), which was harmless while boards posted on the morning
        # of — and wrong the moment they moved to the night before, when "now"
        # is the previous day. `day` is the slate this board is about, and
        # title_tag says which kind of game it is (MNF / TNF / the Sunday card).
        _dl = f"{day.month}/{day.day}" + (f" {title_tag}" if title_tag else "")

        # THE BOARD IS FIVE PLAYS, NOT NINE. rows arrives ranked; everything
        # past NFL_MAX_PLAYS is simply not this board's business.
        shown = rows[:NFL_MAX_PLAYS]

        # ⭐ ONLY IF IT IS EARNED. The top row was badged Pick of the Day no
        # matter what it scored — on a board whose floor is 0.52 that is a
        # headline claim the number does not support. When nothing clears the
        # bar the board still posts, in full, from #1, with a line saying why
        # the slot is empty. The slot never silently vanishes; see the tennis
        # twin in _post_daily_picks.
        _p0 = _nfl_win_prob(shown[0]) if shown else None
        has_star = isinstance(_p0, (int, float)) and _p0 >= NFL_POTD_MIN_PROB
        _no_potd_line = None
        if has_star:
            potd = npost.build_potd_embed(shown[0], when=day, date_label=_dl)
            embed = npost.build_board_embed(shown[1:], book, shadow=shadow,
                                            start_rank=2, when=day,
                                            date_label=_dl,
                                            max_plays=NFL_MAX_PLAYS)
        else:
            potd = None
            embed = npost.build_board_embed(shown, book, shadow=shadow,
                                            start_rank=1, when=day,
                                            date_label=_dl,
                                            max_plays=NFL_MAX_PLAYS)
            _no_potd_line = MSG_NO_POTD_HAS_BOARD
            log.warning("NFL_NO_POTD | %s | %s | best play %.3f < %.2f bar | "
                        "board still posted (%d plays)", day, book,
                        _p0 if isinstance(_p0, (int, float)) else float("nan"),
                        NFL_POTD_MIN_PROB, len(shown))
        embeds = [e for e in (potd, embed) if e is not None]
        if not embeds:
            return

        # ONE PING FOR ONE DROP. The ⭐ and the board used to go out as two
        # messages, each mentioning everyone — two notifications for one board,
        # which is how a server gets muted. Tennis sends both embeds in a single
        # @everyone message and these now read as one drop.
        # LINE ALERTS ARE THE EXCEPTION and stay silent: they fire repeatedly
        # through the day, and a ping per line move trains people to mute.
        _content = "@everyone" + (f"\n{_no_potd_line}" if _no_potd_line else "")
        await ch.send(content=_content, embeds=embeds,
                      allowed_mentions=discord.AllowedMentions(everyone=True))
        log.warning("NFL %sboard (%s) posted %d of %d play(s) for %s to %s "
                    "(star=%s, shadow=%s)", label, book, len(shown), len(rows),
                    day, cid, has_star, shadow)
        # Hand the POSTED rows to the line watch so alerts track what readers
        # were actually shown, and record those — recording all 168 scanned rows
        # would blacklist the whole slate from any later scan.
        _NFL_WATCH[book] = shown
        await asyncio.to_thread(nb.record_posted, shown, str(day))
        # Persist for the recap. NOT posted anywhere — the recap is built but
        # deliberately not wired to the track-record channel yet (user).
        try:
            nstore = _nfl_import("nfl.store")
            nrecap = _nfl_import("nfl.recap")
            # WHAT WAS PUBLISHED, not what was scanned — the same rule the
            # tennis ledger follows. Logging all 168 priced rows would score a
            # record against plays no subscriber was ever shown. The POTD name
            # is None when no play earned the ⭐.
            await asyncio.to_thread(
                nstore.log_board, shown, book, str(day),
                shown[0]["player"] if has_star else None, shadow)
        except Exception:  # noqa: BLE001 — never cost the post
            log.exception("NFL store logging failed (board already posted)")
    except Exception:  # noqa: BLE001 — Rule 2, and tennis must never be reached
        log.exception("NFL board (%s) failed entirely (tennis unaffected)", book)


@tasks.loop(time=[datetime.time(hour=NFL_BOARD_HOUR, minute=NFL_BOARD_MINUTE,
                                tzinfo=POD_TZINFO)])
async def nfl_daily_boards():
    """Both books' NFL boards, posted the NIGHT BEFORE each slate.

    Wednesday, Saturday and Sunday at NFL_BOARD_HOUR ET (operator, 2026-09-18),
    each board covering the FOLLOWING day's games and titled for them:

        Wed 22:00 -> Thursday  "9/25 TNF"
        Sat 22:00 -> Sunday    "9/28 Sunday Slate"
        Sun 22:00 -> Monday    "9/29 MNF"

    The target is always tomorrow, so unlike the boards this replaced nothing
    has to name a slate or count a gap of one day against two.

    The loop still fires daily because discord.py has no weekday filter, so the
    guard is here. One book failing must not stop the other.
    """
    if not NFL_TASKS_ENABLED:
        return
    try:
        import datetime as _dt
        nb = _nfl_import("nfl.board")
        today = nb.slate_date()
    except Exception:  # noqa: BLE001 — Rule 2; without a date there is no board
        log.exception("NFL board: could not resolve the slate date")
        return
    tag = NFL_BOARD_EVES.get(today.weekday())
    if tag is None:
        log.info("NFL board: %s is not an eve night (posting on %s) — skipping",
                 today, sorted(NFL_BOARD_EVES))
        return
    slate = today + _dt.timedelta(days=1)
    log.warning("NFL board: posting tonight for the %s slate (%s)", slate, tag)
    for book in ("prizepicks", "underdog"):
        # window_days=0 pins the board to that ONE slate. Without it the scan
        # takes a default window and rakes in games belonging to another card.
        await _nfl_post_board(book, day=slate, window_days=0,
                              label=f"{tag} ", title_tag=tag)


@nfl_daily_boards.before_loop
async def _before_nfl_boards():
    await client.wait_until_ready()


# ── THE NFL WEEK, NOT A DAILY BOARD ───────────────────────────────
# A daily board is the wrong shape for this sport. Football plays on three days
# and nothing happens on the other four, so a board that fired every morning was
# empty most of the week.
#
# The schedule now posts on the MORNING OF EACH GAME DAY (operator, 2026-09-18):
#
#     Monday    NFL_BOARD_HOUR ET  ->  that night's Monday game
#     Thursday  NFL_BOARD_HOUR ET  ->  that night's Thursday game
#     Sunday    NFL_BOARD_HOUR ET  ->  that day's Sunday slate
#
# ONE LOOP DOES ALL THREE (see nfl_daily_boards, which guards on the weekday and
# pins the board to that day). It replaced three loops: a daily board plus a
# Friday board that looked ahead to Sunday and a Sunday-night board that looked
# ahead to Monday. Those existed to get lines up early, but they also meant the
# channel saw seven posts a week for three days of football, and the two
# look-ahead boards had to name their target slate explicitly because the gap
# differed. Posting the morning of removes the gap, so nothing has to guess.
#
# The repeat guard is keyed on the SLATE, so a board cannot double-post a game
# day even if the loop is restarted or run manually.


# ── WEBSITE BOARD REFRESH ────────────────────────────────────────────────────
# The site shows the FULL scanned market, the way the tennis board does — not
# the eight plays that went to Discord. The model cannot run in the web backend
# (nfl/ ships with this bot, and pricing there would mean pyarrow plus a
# few-hundred-megabyte parquet on the service that has to cold-start for the
# app), so the bot scans and publishes, and the backend serves what it is given.
#
# Every NFL_BOARD_REFRESH_MINUTES, because lines move all day and a board that
# only refreshed when Discord was posted would be stale between drops.
NFL_BOARD_REFRESH_MINUTES = int(
    os.getenv("NFL_BOARD_REFRESH_MINUTES", "45") or "45")


@tasks.loop(minutes=NFL_BOARD_REFRESH_MINUTES)
async def nfl_board_refresh():
    """Scan and publish the full board for the site. Posts nothing to Discord."""
    if not NFL_TASKS_ENABLED:
        return
    try:
        npub = _nfl_import("nfl.publish")
        nb = _nfl_import("nfl.board")
        day = nb.slate_date()
        for book in ("prizepicks", "underdog"):
            try:
                n = await asyncio.to_thread(npub.publish_scan, book, day)
                if n:
                    log.warning("NFL website board: %d row(s) published (%s %s)",
                                n, book, day)
            except Exception:  # noqa: BLE001 — one book must not stop the other
                log.exception("NFL website board publish failed for %s", book)
    except Exception:  # noqa: BLE001 — Rule 2
        log.exception("NFL website board refresh failed (tennis unaffected)")


@nfl_board_refresh.before_loop
async def _before_nfl_board_refresh():
    await client.wait_until_ready()


@tasks.loop(minutes=NFL_LINE_CHECK_MINUTES)
async def nfl_line_watch():
    """Re-check the books for movement on the plays we posted."""
    if not NFL_TASKS_ENABLED or not _NFL_WATCH:
        return
    try:
        lm = _nfl_import("nfl.line_monitor")
        npost = _nfl_import("nfl.post")
        nl = _nfl_import("nfl.lines")
        cid = npost.channel_for("lines")
        ch = client.get_channel(cid) if cid else None
        if ch is None:
            log.error("NFL line watch: channel %s not visible", cid)
            return
        for book, rows in list(_NFL_WATCH.items()):
            try:
                current = await asyncio.to_thread(nl.fetch_lines, book)
                if not current:
                    continue
                watched = [{
                    "player": r["player"], "prop": r.get("prop"),
                    "line": r.get("line"), "projection": r.get("projection"),
                    "lean": r.get("lean"), "sd": r.get("sd"), "book": book,
                    "_key": (nl._norm(r["player"]), r.get("prop")),
                } for r in rows if r.get("line") is not None]
                for a in lm.detect(watched, current):
                    # Adopt the new line FIRST, so a move is announced once
                    # rather than on every pass after it — the tennis monitor's
                    # hard-won rule.
                    for r in rows:
                        if (r.get("player") == a["player"]
                                and r.get("prop") == a["prop"]):
                            r["line"] = a["new_line"]
                            r["lean"] = a["new_lean"]
                    await ch.send(embed=npost.build_line_alert_embed(a))
            except Exception:  # noqa: BLE001 — one book must not stop the other
                log.exception("NFL line watch failed for %s", book)
    except Exception:  # noqa: BLE001
        log.exception("NFL line watch failed entirely (tennis unaffected)")


@nfl_line_watch.before_loop
async def _before_nfl_line_watch():
    await client.wait_until_ready()


async def _nfl_selfcheck() -> None:
    """Prove at startup that the NFL data layer actually works IN THIS CONTAINER.

    Every NFL command answered "no game log found" in production while passing on
    a dev machine, because nflverse ships parquet and the container had no
    parquet engine installed. pandas returned an empty frame, the empty frame
    looked exactly like "this player has no games", and the real error sat in a
    log line nobody was reading.

    So: read one real dataset, look up one real player, and say plainly whether
    it worked. A dependency that only breaks in production must announce itself
    on the way up, not on a member's first command.
    """
    if not NFL_TASKS_ENABLED:
        return
    try:
        def _probe():
            q = _nfl_import("nfl.queries")
            df, yr = q._weekly()
            engine = None
            try:
                import pyarrow  # noqa: F401
                engine = "pyarrow"
            except ImportError:
                try:
                    import fastparquet  # noqa: F401
                    engine = "fastparquet"
                except ImportError:
                    engine = None
            form = q.recent_form("Lamar Jackson", 1)
            # Warm the autocomplete index HERE, not on a member's first
            # keystroke. Building it takes ~5s and Discord allows an
            # autocomplete callback about 3, so a cold index would have shown
            # "Loading options failed" to whoever typed first.
            n_idx = len(q._search_index())
            try:
                from core import proxy as _px
                pxs = _px.status()
            except Exception:  # noqa: BLE001
                pxs = {"configured": "import failed"}
            return engine, len(df), yr, bool(form.get("player")), pxs, n_idx

        engine, rows, yr, ok, pxs, n_idx = await asyncio.to_thread(_probe)
        log.warning("NFL proxy: configured=%s ports=%s nfl_enabled=%s",
                    pxs.get("configured"), pxs.get("ports"),
                    pxs.get("nfl_enabled"))
        if ok and rows:
            log.warning("NFL selfcheck OK — parquet engine=%s, %d weekly rows "
                        "(%s), player lookup works, %d players indexed for "
                        "autocomplete", engine, rows, yr, n_idx)
        else:
            log.error("NFL SELFCHECK FAILED — parquet engine=%s, weekly rows=%d "
                      "(%s), player lookup=%s. Every NFL command will report "
                      "'no game log found' until this is fixed. If engine is "
                      "None, pyarrow is missing from discord-bot/requirements.txt.",
                      engine, rows, yr, ok)
    except Exception:  # noqa: BLE001 — a self-check must never take the bot down
        log.exception("NFL selfcheck errored (tennis unaffected)")


async def _nfl_post_intro() -> None:
    """One-shot 'how this works' post in the projections channel."""
    global _nfl_intro_done
    if _nfl_intro_done or not NFL_POST_INTRO:
        return
    _nfl_intro_done = True
    try:
        npost = _nfl_import("nfl.post")
        cid = npost.channel_for("projections")
        ch = client.get_channel(cid) if cid else None
        if ch is None:
            log.error("NFL intro: projections channel %s not visible", cid)
            return
        await ch.send(content="@everyone", embed=npost.build_intro_embed(),
                      allowed_mentions=discord.AllowedMentions(everyone=True))
        log.warning("NFL intro posted to %s", cid)
    except Exception:  # noqa: BLE001
        log.exception("NFL intro post failed (tennis unaffected)")


NFL_PROJECTIONS_CHANNEL_ID = int(
    os.getenv("NFL_PROJECTIONS_CHANNEL_ID", "1546942501812834414") or 0)

async def nfl_player_autocomplete(interaction: discord.Interaction, current: str):
    """Player picker for the NFL commands.

    Tennis has had one all along; NFL asked people to type a name exactly, so a
    typo ("lamarr jackson") and a real miss produced the same "no game log
    found" and there was no way to tell which had happened. You cannot mistype
    a name you selected from a list.

    Entirely local — the weekly frame is already in memory — so it answers in
    under a millisecond and cannot hit Discord's ~3s deadline. Never raises: an
    autocomplete that throws shows the user "Loading options failed".
    """
    try:
        cur = (current or "").strip()
        if len(cur) < 2:
            return []
        q = _nfl_import("nfl.queries")
        hits = await asyncio.to_thread(q.search_players, cur, 25)
        out = []
        for h in hits:
            bits = [b for b in (h.get("position"), h.get("team")) if b]
            label = h["name"] + (f" ({' '.join(bits)})" if bits else "")
            out.append(app_commands.Choice(name=label[:100], value=h["name"][:100]))
        return out[:25]
    except Exception:  # noqa: BLE001
        log.exception("nfl player autocomplete failed")
        return []


async def nfl_team_autocomplete(interaction: discord.Interaction, current: str):
    """Team picker. Matches the abbreviation OR the club name, because nobody
    remembers that nflverse calls the Rams LA and the Commanders WAS."""
    try:
        q = _nfl_import("nfl.queries")
        hits = await asyncio.to_thread(q.search_teams, (current or "").strip(), 25)
        return [app_commands.Choice(name=f"{h['name']} ({h['abbr']})"[:100],
                                    value=h["abbr"])
                for h in hits][:25]
    except Exception:  # noqa: BLE001
        log.exception("nfl team autocomplete failed")
        return []


NFL_PROP_CHOICES = [
    app_commands.Choice(name="Pass Yards", value="pass_yards"),
    app_commands.Choice(name="Rush Yards", value="rush_yards"),
    app_commands.Choice(name="Receiving Yards", value="receiving_yards"),
    app_commands.Choice(name="Receptions", value="receptions"),
]


@client.tree.command(name="nflprop",
                     description="Get a Baseline NFL prop projection")
@app_commands.describe(
    player="Player name",
    prop="Which prop to project",
    line="The book line, e.g. 62.5",
)
@app_commands.choices(prop=NFL_PROP_CHOICES)
@app_commands.autocomplete(player=nfl_player_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflprop(interaction: discord.Interaction, player: str,
                  prop: app_commands.Choice[str], line: float = None):
    if (NFL_PROJECTIONS_CHANNEL_ID
            and interaction.channel_id != NFL_PROJECTIONS_CHANNEL_ID):
        await _send_error(interaction,
                          f"Use NFL commands in <#{NFL_PROJECTIONS_CHANNEL_ID}>.")
        return
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return
    log.info("CMD /nflprop | user=%s | %s | %s | line=%s",
             interaction.user.id, player, prop.value, line)
    try:
        nprops = _nfl_import("nfl.props")
        npost = _nfl_import("nfl.post")
        nb = _nfl_import("nfl.board")
        nclient = _nfl_import("nfl.client")
        nl = _nfl_import("nfl.lines")
        # Attach the game so the script mixture applies — without it the answer
        # is a league-neutral volume estimate and materially weaker. The team
        # comes from the book's own board entry, which is the only place we
        # learn who a player currently plays for.
        game = None
        try:
            posted = await asyncio.to_thread(nl.fetch_lines, "prizepicks")
            row = posted.get((nl._norm(player), prop.value))
            if row and row.get("team"):
                games = await asyncio.to_thread(nclient.upcoming_week)
                game = nb._game_for(row["team"], nb._team_index(games)) or None
        except Exception:  # noqa: BLE001 — a missing game is a weaker answer, not an error
            log.warning("/nflprop: could not attach a game for %s", player)
        r = await asyncio.to_thread(nprops.project, player, prop.value, line, game)
        if not r:
            await _send_error(
                interaction,
                f"No usable game log for **{player}**. Check the spelling — or "
                f"he may not have enough games for the model to project.")
            return
        await interaction.followup.send(embed=npost.build_prop_embed(r),
                                        ephemeral=True)
    except Exception:  # noqa: BLE001 — never let a command crash the process
        log.exception("UNHANDLED /nflprop error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


async def _nfl_guard(interaction) -> bool:
    """Channel check + queue admission, shared by every NFL command."""
    if (NFL_PROJECTIONS_CHANNEL_ID
            and interaction.channel_id != NFL_PROJECTIONS_CHANNEL_ID):
        await _send_error(interaction,
                          f"Use NFL commands in <#{NFL_PROJECTIONS_CHANNEL_ID}>.")
        return False
    try:
        await _enter_queue(interaction)
    except _QueueBusy:
        return False
    return True


async def _nfl_reply(interaction, embed, empty_msg: str) -> None:
    if embed is None:
        await _send_error(interaction, empty_msg)
        return
    await interaction.followup.send(embed=embed, ephemeral=True)


# /nflboard was REMOVED (user, 2026-09-08). The scheduled boards already post
# to the two book channels, so a command that reprints the same list added a
# second way to ask the same question — and the projections channel is for
# asking about a player, not for re-reading the board. nfl.board.scan_board
# stays: the scheduled tasks are its real caller.


@client.tree.command(name="nflplayer",
                     description="Role, usage and efficiency profile")
@app_commands.describe(player="Player name")
@app_commands.autocomplete(player=nfl_player_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflplayer(interaction: discord.Interaction, player: str):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflplayer | user=%s | %s", interaction.user.id, player)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.player_profile, player)
        await _nfl_reply(interaction, npost.build_player_embed(r),
                         f"No usable game log for **{player}**. Check the "
                         f"spelling, or he may not have enough games yet.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflplayer error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@client.tree.command(name="nflform", description="Recent game-by-game trend")
@app_commands.describe(player="Player name",
                       games="How many recent games (default 6)")
@app_commands.autocomplete(player=nfl_player_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflform(interaction: discord.Interaction, player: str,
                  games: int = 6):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflform | user=%s | %s | n=%s", interaction.user.id, player, games)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.recent_form, player, max(1, min(17, games)))
        await _nfl_reply(interaction, npost.build_form_embed(r),
                         f"No game log found for **{player}**.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflform error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@client.tree.command(name="nflhistory",
                     description="How often he has cleared a number")
@app_commands.describe(player="Player name", prop="Which prop",
                       line="The number to test against")
@app_commands.choices(prop=NFL_PROP_CHOICES)
@app_commands.autocomplete(player=nfl_player_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflhistory(interaction: discord.Interaction, player: str,
                     prop: app_commands.Choice[str], line: float):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflhistory | user=%s | %s | %s %s",
             interaction.user.id, player, prop.value, line)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.line_history, player, prop.value, line)
        await _nfl_reply(interaction, npost.build_history_embed(r),
                         f"No game log found for **{player}**.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflhistory error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@client.tree.command(name="nflgame", description="Win probability and projected score")
@app_commands.describe(team="Team")
@app_commands.autocomplete(team=nfl_team_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflgame(interaction: discord.Interaction, team: str):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflgame | user=%s | %s", interaction.user.id, team)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.game_outcome, team)
        await _nfl_reply(interaction, npost.build_game_embed(r),
                         f"No upcoming game found for **{team}**. Use the team "
                         f"abbreviation, e.g. `SEA`, `KC`, `PHI`.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflgame error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@client.tree.command(name="nflspread", description="Chance a team covers a handicap")
@app_commands.describe(team="Team abbreviation, e.g. SEA",
                       handicap="The handicap, e.g. -3.5")
@app_commands.autocomplete(team=nfl_team_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflspread(interaction: discord.Interaction, team: str,
                    handicap: float):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflspread | user=%s | %s %s", interaction.user.id, team, handicap)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.spread_cover, team, handicap)
        await _nfl_reply(interaction, npost.build_spread_embed(r),
                         f"No upcoming game with a posted spread for **{team}**.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflspread error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@client.tree.command(name="nflh2h", description="Head-to-head record")
@app_commands.describe(team1="Team",
                       team2="Team")
@app_commands.autocomplete(team1=nfl_team_autocomplete, team2=nfl_team_autocomplete)
@app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
async def nflh2h(interaction: discord.Interaction, team1: str, team2: str):
    if not await _nfl_guard(interaction):
        return
    log.info("CMD /nflh2h | user=%s | %s vs %s", interaction.user.id, team1, team2)
    try:
        q = _nfl_import("nfl.queries")
        npost = _nfl_import("nfl.post")
        r = await asyncio.to_thread(q.head_to_head, team1, team2)
        await _nfl_reply(interaction, npost.build_h2h_embed(r),
                         f"No meetings found for **{team1}** vs **{team2}**. Use "
                         f"team abbreviations, e.g. `SEA` and `NE`.")
    except Exception:  # noqa: BLE001
        log.exception("UNHANDLED /nflh2h error")
        await _send_error(interaction, MSG_GENERIC)
    finally:
        _leave_queue()


@tasks.loop(time=[datetime.time(hour=UNDERDOG_PREWARM_HOUR,
                                minute=UNDERDOG_PREWARM_MINUTE, tzinfo=POD_TZINFO)])
async def underdog_cache_prewarm():
    """Walk the Underdog board 15 minutes before it generates and THROW THE
    RESULT AWAY — the only product is a warm player-stats cache, exactly like
    daily_cache_prewarm does for PrizePicks.

    Posts nothing, logs nothing to the record, and never raises: a failed
    pre-warm degrades to 'the 10:30 generation runs cold', which is simply the
    behaviour before this existed."""
    try:
        t0 = time.time()
        props = await asyncio.to_thread(underdog.to_board_props)
        if not props:
            log.info("UD_PREWARM | no straight two-way props on the board — nothing to warm")
            return
        ordered, _thin = await pick_of_day._rank_board(props=props)
        log.info("UD_PREWARM | board walked in %.1f min | %d props scanned, %d qualifying "
                 "(DISCARDED — this run exists only to warm the caches so the "
                 "%02d:%02d Underdog generation computes warm)",
                 (time.time() - t0) / 60.0, len(props), len(ordered or []),
                 UNDERDOG_HOUR, UNDERDOG_MINUTE)
    except Exception:  # noqa: BLE001
        log.exception("UD_PREWARM failed — the Underdog board will run cold "
                      "(no worse than before the pre-warm existed)")


@underdog_cache_prewarm.before_loop
async def _before_underdog_prewarm():
    await client.wait_until_ready()


@tasks.loop(time=[datetime.time(hour=UNDERDOG_HOUR, minute=UNDERDOG_MINUTE,
                                tzinfo=POD_TZINFO)])
async def daily_underdog_board():
    """The Underdog board trigger — 10:30 PM ET, half an hour after PrizePicks."""
    if not UNDERDOG_CHANNEL_ID:
        return
    try:
        channel = client.get_channel(UNDERDOG_CHANNEL_ID)
        if channel is None:
            log.warning("underdog board: channel %s not found",
                        UNDERDOG_CHANNEL_ID)
            return
        # Same duplicate guard as the PrizePicks board, scoped to THIS book: one
        # Underdog board per card, compared on slate date rather than calendar day.
        try:
            _rec_u = await asyncio.to_thread(results_tracker.get_record)
            _now_u = datetime.datetime.now(POD_TZINFO)
            _target = ((_now_u + datetime.timedelta(days=1)) if _now_u.hour >= 12
                       else _now_u).strftime("%Y-%m-%d")
            for _q in ((_rec_u or {}).get("underdog") or {}).get("picks", []):
                if _q.get("excluded_from_record"):
                    continue
                if _slate_date_of(_q) == _target:
                    log.info("underdog board: a board for the %s card was already "
                             "posted — skipping", _target)
                    return
        except Exception:  # noqa: BLE001
            pass
        status = await _post_underdog_board(channel, track=True)
        log.info("underdog board: %s", status)
    except Exception:  # noqa: BLE001
        log.exception("underdog board trigger failed")


@daily_underdog_board.before_loop
async def _before_underdog_board():
    await client.wait_until_ready()


async def _pending_pick_keys() -> set:
    """(_norm(player), prop_type) for every pick still UNRESOLVED.

    A play that is already logged and awaiting a result must not be posted again
    on a later board: the 18h dedup in _log_picks_pending stops it being logged
    twice, but nothing stopped it being DISPLAYED twice, so members saw a live
    play presented as new. That matters most when matches are postponed — a
    rain-delayed match slides onto the next card while its pick is still open."""
    keys = set()
    try:
        pending = await asyncio.to_thread(results_tracker.get_pending) or []
        for q in pending:
            # A pick flagged excluded_from_record is not a live play — it was
            # superseded or its post was pulled. It must not block that player's
            # prop from a future board, or an excluded row (which may never be
            # graded) would lock the matchup out permanently.
            if q.get("excluded_from_record"):
                continue
            keys.add((pick_of_day._norm(q.get("player", "")), q.get("prop_type")))
    except Exception:  # noqa: BLE001
        log.exception("board: failed to read pending pick keys (posting unfiltered)")
    return keys


async def _post_second_wave(channel, track: bool = True, max_plays: int = None,
                            min_conf: int = 0,
                            title: str = "🎾 Additional Plays",
                            group: str = "second-wave", label: str = "second wave") -> str:
    """Post ADDITIONAL plays not already on an earlier board (excluded by
    player+prop_type), ping @everyone, and log them. Never raises.

    PARAMETERISED BECAUSE THERE ARE NOW TWO OF THESE and they want different
    shapes. The midnight scan is the wide one: whatever is left on the card,
    capped at SECOND_WAVE_MAX. The 8 PM rescan is the narrow one — two or three
    plays at 70+ confidence — and exists to add to the day without diluting it.

    min_conf GATES, ROOM ORDERS. Within a set already held to a high confidence
    bar, the play worth adding is the one with the most distance between our
    number and the book's, so the shortlist is re-sorted by relative edge. That
    is the opposite of how the main board ranks (confidence first, see
    pick_of_day._rank_key) and deliberately so: the board is ordering plays that
    only had to clear 65, while this is choosing among plays that already
    cleared 70 and can afford to be picked on margin."""
    if not AUTOPOST_ENABLED:
        log.info("second wave: automated posting DISABLED (AUTOPOST_ENABLED off)")
        return "autopost disabled"
    # Checked HERE as well as at the schedulers: every additional-plays post goes
    # through this function, so one guard covers the evening rescan, the extension
    # scan and any manual call, and no future caller can reintroduce them by
    # accident.
    if not SECOND_SCANS_ENABLED:
        log.info("%s: DISABLED (SECOND_SCANS_ENABLED off) — the board stands on "
                 "its own; additions measured 36.8%% since 2026-09-12", label)
        return "second scans disabled"
    # Exclude BOTH: anything logged in the last 18h (the normal same-cycle repeat)
    # AND anything still awaiting a result, however old. The 18h window alone is
    # time-based, so a POSTPONED play ages out of it and gets posted again as new
    # — that is exactly what happened on 8/3, when Putintseva's 8/2 pick (logged
    # ~31h earlier, still pending after a rain delay) led the Additional Plays and
    # was logged a second time. Pending state, not elapsed time, is the real test.
    exclude = await _todays_posted_keys()
    exclude |= await _pending_pick_keys()
    ordered, _thin = await pick_of_day._rank_board()
    if not ordered:
        log.info("second wave: no qualifying board — nothing to add")
        return "no board"
    cands = [p for p in ordered
             if (pick_of_day._norm(p.get("player", "")), p.get("prop_type")) not in exclude]
    if min_conf:
        _before = len(cands)
        cands = [p for p in cands if (p.get("confidence") or 0) >= min_conf]
        cands.sort(key=lambda p: -(pick_of_day._rel_edge(p) or 0))
        log.info("%s: %d of %d candidate(s) clear %d%% confidence; ordered by room",
                 label, len(cands), _before, min_conf)
    adds = cands[:(max_plays or SECOND_WAVE_MAX)]
    if not adds:
        log.info("%s: no additional plays beyond the %d already posted%s", label,
                 len(exclude),
                 (" that also clear %d%%" % min_conf) if min_conf else "")
        return "no additional plays"

    await _annotate_form_alerts(adds)
    # One embed titled "Additional Plays" — no separate header/description blurb
    # (2026-07-27, user: far too much wording); the slate date rides the board footer.
    # Correlation caution suppressed here (kept on the main board).
    embeds = ranked_embeds(adds, start_rank=1, total=len(adds),
                           title_override=title,
                           suppress_correlation_note=True)
    # @everyone — a real second daily drop (2026-07-27, user), matching the 8 PM board's ping.
    _content = "@everyone" if track else None
    await channel.send(content=_content, embeds=embeds[:10], allowed_mentions=EVERYONE_MENTION)

    if track:
        await _log_picks_pending(adds, group=group)
        # _start_line_monitor REPLACES the running monitor, so re-arm over ALL of today's
        # pending picks (8 PM board + 3x + these adds) — never drop the 8 PM set.
        try:
            pending = await asyncio.to_thread(results_tracker.get_pending) or []
            today = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
            mon = []
            for p in pending:
                if not str(p.get("generated_at") or "").startswith(today):
                    continue
                orig = p.get("original_line")
                orig = orig if orig is not None else p.get("line")
                if orig is None:
                    continue
                mon.append({"player": p.get("player"), "pp_player": p.get("player"),
                            "prop_type": p.get("prop_type"), "original_line": orig,
                            "projection": p.get("model_projection"), "lean": p.get("lean"),
                            "opponent": p.get("opponent"),
                            "start_timestamp": None})
            if mon:
                _start_line_monitor(channel, mon)
        except Exception:  # noqa: BLE001
            log.exception("second wave: monitor re-arm failed")
    return "posted %d additional plays: %s" % (
        len(adds), ", ".join("%s %s" % (p.get("player"), p.get("prop_type")) for p in adds))


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


@tasks.loop(time=_sched_times(ONEOFF_PREWARM_HM, PREWARM_HOUR, PREWARM_MINUTE))
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


@tasks.loop(time=[datetime.time(hour=ONEOFF_EXT_HM[0], minute=ONEOFF_EXT_HM[1],
                                tzinfo=POD_TZINFO)])
async def extension_pod_run():
    """EXTENSION scan — re-walk the board and post ONLY plays that were not in
    the earlier post. Additions, not a replacement.

    PrizePicks keeps posting props through the evening, so a board scanned at
    8:20 misses lines that appear at 9. This continues the SAME list rather than
    reposting it: numbering picks up where the earlier post stopped, and anything
    already published is filtered out by (player, prop) against the last 18h of
    logged picks — the same key _log_picks_pending de-dupes on, so a play cannot
    be posted twice or logged twice.

    No ⭐ and no 3x: both were settled by the earlier post, and re-crowning a
    headline or re-cutting a slip would contradict what subscribers already have.
    Date-gated to ONEOFF_SCHED_DATE; no-op on any other day. Never raises."""
    if datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d") != ONEOFF_SCHED_DATE:
        return
    if not POD_CHANNEL_ID:
        return
    try:
        channel = client.get_channel(POD_CHANNEL_ID)
        if channel is None:
            log.warning("EXTENSION: channel %s not found", POD_CHANNEL_ID)
            return
        already = _recent_pick_keys(hours=18)
        bundle = await pick_of_day.generate_ranked_and_slip()
        ranked = bundle.get("ranked") or []
        new = [p for p in ranked
               if (pick_of_day._norm(p.get("player", "")), p.get("prop_type"))
               not in already]
        log.info("EXTENSION | board re-scanned: %d qualifying, %d already posted "
                 "earlier, %d NEW", len(ranked), len(ranked) - len(new), len(new))
        if not new:
            log.info("EXTENSION | nothing new on the board — posting nothing "
                     "(silence beats a message that says 'no change')")
            return
        offset = len(already)               # continue the earlier list's numbering
        slate = _slate_date(new)
        embeds = ranked_embeds(
            new, start_rank=offset + 1, total=offset + len(new),
            title_override="➕ Added Plays — %d/%d" % (slate.month, slate.day))
        content = "@everyone"
        if bundle.get("thin_slate"):
            content += "\n" + pick_of_day.THIN_SLATE_NOTE
        await channel.send(content=content, embeds=embeds[:10],
                           allowed_mentions=EVERYONE_MENTION)
        for i in range(10, len(embeds), 10):
            await channel.send(embeds=embeds[i:i + 10])
        # Log AFTER a successful send, same rule as the main post. Dedup inside
        # _log_picks_pending is a second belt on top of the `already` filter.
        await _log_picks_pending(new, group="potd")
        # Restart the monitor over the FULL re-scanned board, not just the new
        # plays: _start_line_monitor cancels the running task, so passing only the
        # additions would silently stop watching the earlier 7. `ranked` still
        # contains those (they re-qualified), so this covers old + new.
        _start_line_monitor(channel, ranked)
    except Exception:  # noqa: BLE001
        log.exception("EXTENSION run failed")


@extension_pod_run.before_loop
async def _before_extension_pod_run():
    await client.wait_until_ready()


@tasks.loop(time=_sched_times(ONEOFF_POTD_HM, PICKS_GEN_HOUR, PICKS_GEN_MINUTE))
async def daily_picks_generate():
    """THE POTD TRIGGER — evaluates the board and posts the ⭐ Pick of the Day +
    PrizePicks Board (@everyone) + the 3x when the run finishes (~6-10 min).
    Independent of the recap, which posts earlier.

    Registered at BOTH the one-off slot (6:50 PM on ONEOFF_SCHED_DATE) and the
    recurring slot (7:50 PM); _slot_is_live picks which one actually runs, so the
    override date posts once at 6:50 and every other date once at 7:50."""
    if not _slot_is_live(ONEOFF_POTD_HM):
        return
    if not POD_CHANNEL_ID:
        return
    if not AUTOPOST_ENABLED:
        log.info("POTD trigger: automated posting DISABLED (AUTOPOST_ENABLED off) — "
                 "not generating or posting the board")
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
        # Duplicate-board guard (2026-07-29, rewritten 2026-08-03). Never post two
        # boards for the same CARD — but compare SLATE dates, not generation dates.
        #
        # The original compared generation date and broke the 8/2 one-off: a board
        # posted 01:25 AM built the 8/2 card, so when the 11:30 PM run (building the
        # 8/3 card) fired, the guard saw "already posted today" and silently
        # returned. Two boards on one calendar day are perfectly legitimate when
        # they target different cards; two boards for the SAME card are not.
        try:
            _rec_g = await asyncio.to_thread(results_tracker.get_record)
            _now_et = datetime.datetime.now(POD_TZINFO)
            _target_slate = ((_now_et + datetime.timedelta(days=1)) if _now_et.hour >= 12
                             else _now_et).strftime("%Y-%m-%d")
            for _q in (_rec_g or {}).get("picks", []):
                if (_q.get("pick_group") or "potd") != "potd":
                    continue
                if _q.get("excluded_from_record"):
                    continue
                if _slate_date_of(_q) == _target_slate:
                    log.info("POTD trigger: a board for the %s card was already posted "
                             "— skipping to avoid a duplicate", _target_slate)
                    return
        except Exception:  # noqa: BLE001
            pass
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


# ── THE 8 PM RESCAN, ONE DAY AT A TIME ──────────────────────────────────────
# Brought back 2026-09-22 (operator: "add back in the 8pm rescan for today").
#
# DATE-GATED ON PURPOSE. "For today" is a one-day decision, and the way to keep
# it one day is to make the code enforce that rather than rely on someone
# remembering to take it out again. EVENING_RESCAN_DATE is a single ET date; on
# any other day the loop wakes, sees the date does not match and returns. Empty
# — the default — means it never runs at all, so a redeploy cannot silently
# revive it. Same shape as POD_EXTRA_RUN_DATE.
#
# WHY IT WAS REMOVED, kept here because it is the reason to be careful about
# leaving it on. Across 710 graded plays the additional-plays scans ran 100-97
# (50.8%) against the board's 282-231 (55.0%), and dragged the published record
# from 55.0% to 53.8%. The gap was not significant on its own (p=0.31) but it
# never pointed the other way.
EVENING_RESCAN_DATE = os.getenv("EVENING_RESCAN_DATE", "").strip()
EVENING_RESCAN_HOUR = int(os.getenv("EVENING_RESCAN_HOUR", "20") or "20")
EVENING_RESCAN_MINUTE = int(os.getenv("EVENING_RESCAN_MINUTE", "0") or "0")
EVENING_RESCAN_MAX = int(os.getenv("EVENING_RESCAN_MAX", "3") or "3")
EVENING_RESCAN_MIN_CONF = int(os.getenv("EVENING_RESCAN_MIN_CONF", "70") or "70")


@tasks.loop(time=[
    datetime.time(hour=EVENING_RESCAN_HOUR, minute=EVENING_RESCAN_MINUTE,
                  tzinfo=POD_TZINFO),
])
async def evening_rescan():
    """Up to EVENING_RESCAN_MAX plays at EVENING_RESCAN_MIN_CONF+, on ONE date.

    min_conf gates and room orders — see _post_second_wave. No-op unless today
    is EVENING_RESCAN_DATE.
    """
    if not POD_CHANNEL_ID or not EVENING_RESCAN_DATE:
        return
    _today = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
    if _today != EVENING_RESCAN_DATE:
        log.info("evening rescan: armed for %s, today is %s — not running",
                 EVENING_RESCAN_DATE, _today)
        return
    if not AUTOPOST_ENABLED:
        log.info("evening rescan: DISABLED (AUTOPOST_ENABLED off)")
        return
    try:
        channel = client.get_channel(POD_CHANNEL_ID)
        if channel is None:
            log.warning("evening rescan: channel %s not found", POD_CHANNEL_ID)
            return
        if POD_SKIP_DATE and _today == POD_SKIP_DATE:
            log.info("evening rescan: skip-date %s — not posting", POD_SKIP_DATE)
            return
        status = await _post_second_wave(
            channel, track=True,
            max_plays=EVENING_RESCAN_MAX,
            min_conf=EVENING_RESCAN_MIN_CONF,
            title="🎾 Late Value",
            group="evening-rescan",
            label="evening rescan")
        log.info("evening rescan (%s): %s", EVENING_RESCAN_DATE, status)
    except Exception:  # noqa: BLE001 — Rule 2, never take the bot down
        log.exception("evening rescan failed")


@evening_rescan.before_loop
async def _before_evening_rescan():
    await client.wait_until_ready()


# ── ADDITIONAL-PLAYS SCANS ARE GONE, IN EVERY SPORT ─────────────────────────
# Removed 2026-09-22 (operator). Tennis had two of them at different times, the
# Underdog book had a 7:30 AM top-up and MLB a 9:00 AM one. They all did the
# same thing: re-rank the same card hours after the board and post what was
# left.
#
# Measured over 710 graded plays, that is what "what was left" was worth:
#
#     main board   282-231   55.0%   [50.6, 59.2]
#     additional   100-97    50.8%   [44.3, 58.2]
#
# Barely above break-even, four points under the board, and enough volume to
# pull the published record from 55.0% down to 53.8%. The gap is not
# statistically significant on its own (p=0.31) — it is 197 picks — but it has
# been in the same direction the whole time, and a top-up that cannot beat the
# board it tops up is only adding volume.
#
# The board scans are untouched. This removes only the second bite.

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
def _et_date_of(generated_at: str, shift_hours: float = 0):
    """The ET calendar date ('YYYY-MM-DD') a timestamp falls on. ``shift_hours``
    offsets the timestamp before taking the date — the midnight recap uses -6 so a
    match graded just after midnight still counts for the day it was PLAYED (the recap
    "day" runs 6 AM→6 AM). The earliest next-day match starts ~7 AM, so this never
    pulls a genuinely-next-day match into the prior day's recap."""
    if not generated_at:
        return None
    try:
        dt = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        if shift_hours:
            dt = dt + datetime.timedelta(hours=shift_hours)
        return dt.astimezone(POD_TZINFO).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _slate_date_of(p: dict) -> str:
    """The ET date a pick's match is actually PLAYED, from when the list was built —
    the same rule the board uses: a list generated from noon onward is building
    TOMORROW's card, anything earlier is today's.
      board 7/29 8 PM -> 7/30 · wave 7/30 8 AM -> 7/30 · board 7/30 10 PM -> 7/31
    Returns None when generated_at is missing/unparseable."""
    try:
        _g = datetime.datetime.fromisoformat(
            (p.get("generated_at") or "").replace("Z", "+00:00")).astimezone(POD_TZINFO)
    except Exception:  # noqa: BLE001
        return None
    _d = _g.date() + datetime.timedelta(days=1) if _g.hour >= 12 else _g.date()
    return _d.strftime("%Y-%m-%d")


def daily_recap_embed(rec: dict, target_date: str = None,
                      source: str = "prizepicks") -> discord.Embed:
    """Date-based daily recap. Header 'M/D PrizePicks Recap', that date's picks with
    W/L/PUSH indicators, a Today rate and a rolling 30-day rate. ``target_date`` is
    an ET 'YYYY-MM-DD'; defaults to today in ET.

    ``source`` selects WHICH book's record to render — "prizepicks" reads the
    top-level record, "underdog" reads rec["underdog"], which the backend scores
    separately. The two are never mixed: a second book has its own lines and must
    earn its own track record."""
    if source == "underdog":
        rec = (rec or {}).get("underdog") or {}
    if target_date is None:
        target_date = datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
    try:
        _d = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        header = (f"{_d.month}/{_d.day} Underdog Recap" if source == "underdog"
                  else f"{_d.month}/{_d.day} PrizePicks Recap")
    except Exception:  # noqa: BLE001
        header = "Underdog Recap" if source == "underdog" else "PrizePicks Recap"

    # Date-scoped by RESOLUTION date, on a 6 AM→6 AM "day" (shift_hours=-6) so a
    # match that finished late and graded just after midnight still counts for the
    # day it was played — while genuinely-next-day matches (earliest ~7 AM) stay out.
    # Only graded picks have a resolved_at; generation date is irrelevant here.
    picks = rec.get("picks", []) if rec else []
    # Scoped by SLATE DATE — the day the match was played — NOT by when the pick
    # happened to resolve (2026-08-02). The old 6 AM→6 AM resolution window filed
    # a pick under whatever day it graded on, so Shapovalov (8/1 card, graded
    # 4:54 AM on 8/1) landed in the 7/31 recap and was missing from 8/1's.
    #
    # Resolution-scoping existed so a late grader still appeared SOMEWHERE. That
    # is no longer needed: a day's recap now waits until every pick on that card
    # is settled, so nothing can be orphaned. This also makes the pick list agree
    # with the readiness check and the carryover line, which are both slate-based.
    graded = [p for p in picks
              if p.get("result") in ("W", "L", "PUSH", "VOID")
              and _slate_date_of(p) == target_date]
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

    # ROLLING 30 DAYS instead of all-time (2026-08-03, user). The lifetime total
    # spans a stretch where earlier plays were still being corrected — regraded,
    # voided, superseded — so it isn't a number to publish. A month-to-date
    # window covers only settled recent history and resets cleanly.
    # THE BOARD'S MONTHLY LINE IS GONE; THE ⭐'s STAYS (operator, 2026-09-19).
    # "September: 79/158 cashed (50%)" described every play the board posted,
    # which is not what the product leads with and not what the record is asked
    # about. The ⭐'s month is the number that means something, so that is the
    # one the recap carries — and the pass over the pick log that computed the
    # board figure is gone with it.
    #
    # Counted on SLATE date, like the pick list, so a play belongs to the day it
    # was played, and scoped to the CALENDAR MONTH so a post headed "September"
    # reports September rather than a rolling window that is mostly August.
    try:
        _target_dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        _win_start = _target_dt.replace(day=1).strftime("%Y-%m-%d")
        _win_label = _target_dt.strftime("%B")
    except Exception:  # noqa: BLE001
        _win_start = None
        _win_label = "This month"
    s_w = s_l = s_p = 0
    if _win_start:
        for p in picks:
            if p.get("excluded_from_record") or not p.get("is_potd"):
                continue
            _sd = _slate_date_of(p)
            if not _sd or not (_win_start <= _sd <= target_date):
                continue
            _r = p.get("result")
            if _r == "W":
                s_w += 1
            elif _r == "L":
                s_l += 1
            elif _r == "PUSH":
                s_p += 1               # VOID/DNP never played — excluded both sides
    s_cash = s_w + s_p
    s_total = s_w + s_l + s_p
    s_rate = round(s_cash / s_total * 100) if s_total else 0
    # Today's star, if the card had one. Read off the same graded list the pick
    # rows come from, so it can never disagree with them.
    _star_today = next((p for p in graded if p.get("is_potd")), None)

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
            # Show the ORIGINAL posted line, not the live/bumped one — the play was
            # graded at the line members actually played. A later PrizePicks line
            # move must not change what the recap shows.
            _ln = p.get("original_line")
            if not isinstance(_ln, (int, float)):
                _ln = p.get("line")
            if isinstance(_ln, (int, float)):
                bits.append(f"{_ln:g}")
            if p.get("lean"):
                bits.append(str(p["lean"]).upper())
            row = f"{icon.get(p['result'], '⚪')} **{bits[0]}** {' '.join(bits[1:])}".rstrip()
            if p["result"] == "VOID":
                row += " — **DNP** (cancelled)"
            else:
                # Actual stat the player recorded — shown next to the line so the
                # result is verifiable at a glance (e.g. "Aces 13.5 OVER  →  18").
                _rv = p.get("result_value")
                if isinstance(_rv, (int, float)):
                    row += f"  →  **{_rv:g}**"
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
    record_val = today_line
    # Today's ⭐, then the ⭐'s month. The board's own month line used to sit
    # between them and has been removed: the running record people care about is
    # the star's, and the board's would only invite comparison with a number
    # that answers a different question.
    if _star_today is not None:
        _si = {"W": "✅", "L": "❌", "PUSH": "⚪", "VOID": "🚫"}
        record_val += (f"\n⭐ **Pick of the Day:** "
                       f"{_si.get(_star_today.get('result'), '⚪')} "
                       f"{_star_today.get('player', '')}")
    if s_total:
        record_val += (f"\n⭐ **{_win_label} POTD:** {s_cash}/{s_total} "
                       f"cashed ({s_rate}%)")
    # Rough-day note — included ONLY when the day's cashed rate is under 60% and at
    # least one play actually resolved. Deliberately conditional so it never reads
    # as canned: good days (>=60%) and empty days show nothing extra.
    if t_total and t_rate < 60:
        record_val += "\n\n**Bad beats today, but we move.**"
    e.add_field(name="📋 Record", value=record_val, inline=False)

    # ── 🎟️ Baseline 3x — the day's slip, graded ─────────────────────────────
    # The 3x legs live in their OWN record block (threex_legs), not in `picks`,
    # so the pick list above never showed them and the slip result was invisible
    # in the recap. Scope them by the SAME 6 AM→6 AM resolution window as the
    # main list, then grade the slip by the house rule: both legs must hit; a
    # PUSH/VOID leg drops out and the slip is graded on what remains; any miss
    # loses. Omitted entirely on days with no resolved slip.
    try:
        _legs_all = [p for p in (((rec.get("threex_legs") or {}).get("picks") or []) if rec else [])
                     if not p.get("excluded_from_record")]
        # A slip is a PAIR and must be graded as one unit, on the day it was PLAYED.
        # Attribute it by SLATE date, exactly like the pick list above.
        #
        # This previously keyed off the day the slip's LAST leg resolved, which
        # broke as soon as a leg was postponed: on 8/3 the Eala leg from the 8/2
        # slip finally graded, so that whole slip migrated onto the 8/3 recap,
        # merged with 8/3's own slip, and rendered as a single three-leg "MISSED"
        # — while 8/2 lost its 3x block entirely. Slate date can't drift like that,
        # and since a day only posts once every pick is settled, the slip is always
        # complete by the time its recap goes out.
        _legs = [p for p in _legs_all if _slate_date_of(p) == target_date]
        if any(p.get("result") not in ("W", "L", "PUSH", "VOID") for p in _legs):
            _legs = []                        # slip still has a live leg — hold it
        if _legs:
            _decided = [p["result"] for p in _legs if p["result"] in ("W", "L")]
            if not _decided:
                _slip_txt, _slip_icon = "PUSH", "⚪"
            elif "L" in _decided:
                _slip_txt, _slip_icon = "MISSED", "❌"
            else:
                _slip_txt, _slip_icon = "CASHED", "✅"
            _rows = []
            for p in _legs:
                _ln = p.get("original_line")
                if not isinstance(_ln, (int, float)):
                    _ln = p.get("line")
                _bits = [p.get("player") or "?"]
                if p.get("prop_type"):
                    _bits.append(str(p["prop_type"]))
                if isinstance(_ln, (int, float)):
                    _bits.append(f"{_ln:g}")
                if p.get("lean"):
                    _bits.append(str(p["lean"]).upper())
                _row = f"{icon.get(p['result'], '⚪')} **{_bits[0]}** {' '.join(_bits[1:])}".rstrip()
                if p["result"] == "VOID":
                    _row += " — **DNP** (cancelled)"
                else:
                    _rv = p.get("result_value")
                    if isinstance(_rv, (int, float)):
                        _row += f"  →  **{_rv:g}**"
                _rows.append(_row)
            # The cumulative slip record line was REMOVED from the recap
            # (2026-07-30, user). The day's slip result still shows; the running
            # 3x win/loss total does not. It is still tracked in the backend
            # (record["threex_slips"]) for anyone who needs it.
            e.add_field(name=f"🎟️ Baseline 3x — {_slip_icon} {_slip_txt}",
                        value="\n".join(_rows), inline=False)
    except Exception:  # noqa: BLE001
        pass

    # "Some props play tomorrow" — when plays from this cycle haven't resolved yet
    # (a match moved or started late). Names the next calendar date so members know
    # those carry over to the next recap. Only shown when there ARE such plays.
    try:
        # Scope by the pick's SLATE DATE — the day its match is actually played —
        # using the same rule the board itself uses: a list generated from noon
        # onward is building TOMORROW's card, anything earlier is today's.
        #   board 7/29 8 PM  -> slate 7/30      wave 7/30 8 AM -> slate 7/30
        #   board 7/30 10 PM -> slate 7/31 (NOT this recap)
        #
        # This replaces a batch-matching heuristic that scoped to whichever batches
        # produced a graded pick. That broke once the recap moved to the morning:
        # on 2026-07-30 a SINGLE pick from the 10 PM board (a 7/31 list) resolved
        # before the 6 AM cutoff, which pulled its entire batch into scope and
        # reported 10 of the NEXT day's plays as "didn't finish today".
        _pending = [p for p in picks
                    if p.get("result") not in ("W", "L", "PUSH", "VOID")
                    and not p.get("excluded_from_record")
                    and _slate_date_of(p) == target_date]
        if _pending:
            _nd = datetime.datetime.strptime(target_date, "%Y-%m-%d") + datetime.timedelta(days=1)
            _names = ", ".join(sorted({(p.get("player") or "").split()[-1] for p in _pending}))
            e.add_field(
                name=f"🗓️ Some props play tomorrow · {_nd.month}/{_nd.day}",
                value=f"{len(_pending)} play{'s' if len(_pending) != 1 else ''} didn't finish today — "
                      f"recapped {_nd.month}/{_nd.day}: {_names}",
                inline=False)
    except Exception:  # noqa: BLE001
        pass
    # Footer carries the date the recap is ABOUT, not the date it happens to be
    # rendered. An 8/1 recap posted after midnight was stamping "8/2", which
    # contradicted its own title.
    try:
        _fd = datetime.datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=POD_TZINFO)
    except Exception:  # noqa: BLE001
        _fd = None
    return _stamped_footer(e, FOOTER_GENERIC, when=_fd)


def results_embed(rec: dict) -> discord.Embed:
    if not rec or not rec.get("total"):
        e = discord.Embed(title="📊 Baseline Track Record", color=COLOR_NEUTRAL,
                           description="No graded picks yet — check back after today's plays resolve.")
        e.set_footer(text=FOOTER_GENERIC)
        return e
    # PUSH counts as a win (policy) — fold pushes into the win column so the shown
    # record agrees with the pushes-as-wins win_rate from the backend.
    pushes = rec.get("pushes", 0) or 0
    wins, losses = rec.get("wins", 0) + pushes, rec.get("losses", 0)
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
    # THE HEADLINE IS THE ⭐, FOR THE MONTH. It used to be every play the board
    # posted — five to eighteen a day — which is a BOARD record, not a Pick of
    # the Day record. Those answer different questions, and the one the ⭐
    # promises is how the play we LED with did.
    pm = rec.get("potd_month") or {}
    pm_w = pm.get("wins", 0) or 0
    pm_l = pm.get("losses", 0) or 0
    pm_rate = pm.get("win_rate")
    pm_tracked = pm.get("tracked", 0) or 0
    try:
        _mn = datetime.datetime.strptime(str(pm.get("month") or ""), "%Y-%m").strftime("%B")
    except Exception:  # noqa: BLE001
        _mn = "This month"
    if pm_rate is not None:
        color = COLOR_OVER if pm_rate >= 50 else COLOR_UNDER
    e = discord.Embed(title="📊 Baseline Track Record", color=color)
    if pm_tracked:
        head = (f"**⭐ Pick of the Day — {_mn}:** {pm_w}-{pm_l}"
                f"   ·   **Win rate:** {pm_rate:g}%\n"
                f"Starred plays graded: {pm_tracked}"
                + (f"  ·  Pending: {pm['pending']}" if pm.get("pending") else "")
                + "\n")
    else:
        # THE BAR IS NOT ONE NUMBER. Break Points Won clears the ⭐ at
        # STAR_BP_MIN_CONF (70) rather than the uniform POTD_THRESHOLD (80) —
        # since the A2 rebuild its confidence is half a scenario-mixture P(side)
        # and does not share a scale with the other props' data-quality
        # composite. Saying "the 80 bar" here was simply wrong for the prop that
        # carries almost every star, so this states the rule instead of a value.
        head = (f"**⭐ Pick of the Day — {_mn}:** no graded star yet\n"
                f"_The ⭐ only goes on a play that clears its prop's conviction "
                f"bar, so some days carry none._\n")
    e.description = (
        head
        + (f"🔥 **ON FIRE — {streak} in a row!**\n" if on_fire else "")
        # The whole posted board, kept as context rather than as the headline —
        # a small-sample number standing entirely alone is worse than both.
        + f"_Full board, all time: {wins}-{losses} · {win_rate:g}%_"
        + (f"  ·  Pending: {rec.get('pending', 0)}" if rec.get("pending") else "")
        + (f"  ·  incl. {pushes} push{'es' if pushes != 1 else ''}" if pushes else "")
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
        _add_lines_field(e, "Last 10 posted", rows)

    # 3x slip — tracked independently: the paired slip record (both legs must
    # hit) plus the individual-leg record for transparency.
    slips = rec.get("threex_slips") or {}
    legs = rec.get("threex_legs") or {}
    if (slips.get("slips") or 0) or (legs.get("total") or 0):
        # PUSH counts as a win (policy) — fold pushes into the win column for both
        # the slip record and the individual-leg record.
        _slip_push = slips.get("pushes", 0) or 0
        _leg_push = legs.get("pushes", 0) or 0
        sw, sl = slips.get("wins", 0) + _slip_push, slips.get("losses", 0)
        lw, ll = legs.get("wins", 0) + _leg_push, legs.get("losses", 0)
        val = (f"**Slip record:** {sw}-{sl}   ·   **Win rate:** {slips.get('win_rate', 0):g}%\n"
               f"_(both legs must hit — a slip wins only when neither leg misses)_\n"
               f"**Individual legs:** {lw}-{ll}"
               + (f"  ·  Pending: {legs.get('pending', 0)}" if legs.get("pending") else "")
               + (f"  ·  incl. {_leg_push} push{'es' if _leg_push != 1 else ''}" if _leg_push else ""))
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
            ok = await asyncio.to_thread(results_tracker.update_result, pk["id"],
                                         outcome, res.get("value"))
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


# ── Event-driven recap (2026-08-02, user) ───────────────────────────────────
# The recap no longer waits for a clock slot: the resolver runs every
# RESOLVE_EVERY_HOURS (2) and, as soon as a slate day has NOTHING left pending,
# that day's recap posts. The 8:45 AM task below stays as a BACKSTOP.
#
# EVERY pick must be settled — there is no age-based escape hatch. An earlier
# version let a pick that had been pending 12h stop blocking its day; that
# posted an incomplete recap (2026-08-02, user), so the rule is now simply:
# nothing pending, or the day does not post. A play whose match is postponed
# therefore holds its recap until it grades or is voided — void it manually
# (VOID = DNP) to release the day.
_GRADED_RESULTS = ("W", "L", "PUSH", "VOID")

# ONE-OFF (2026-08-03, user): hold these days and release them TOGETHER. 8/2 was
# fully settled while 8/3 still had matches running, and the user wants both to
# land at once rather than 8/2 going out alone hours earlier. While any listed
# day is still unsettled NOTHING posts; once every listed day is ready they post
# in order on the same pass.
#
# Self-clearing: once all of them are in the channel the duplicate check no-ops
# them and normal day-by-day posting resumes.
#
# EMPTY by default as of 2026-08-05 — the 8/2 and 8/3 backfill it was added for
# posted days ago, so it was doing a redundant channel read every recap cycle.
# Set the RECAP_BATCH_DATES env var to re-arm it for a future batch.
RECAP_BATCH_DATES = [d.strip() for d in
                     os.getenv("RECAP_BATCH_DATES", "").split(",")
                     if d.strip()]


async def _recap_already_posted(channel, date_str: str,
                                source: str = "prizepicks") -> bool:
    """True if this day's recap for THIS source is already in the channel. Reads
    the channel rather than trusting in-memory state, so a restart (or a manual
    post) can never produce a second recap for the same day. On any read failure
    it returns True — refusing to post is the safe direction."""
    try:
        _d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        # PrizePicks accepts the LEGACY "Premium List" title as well. Recaps posted
        # before the 2026-08-05 rename are still in the channel, and failing to
        # recognise one would publish a SECOND recap for a day already covered —
        # the exact duplicate this guard exists to prevent.
        if source == "underdog":
            wants = [f"{_d.month}/{_d.day} Underdog Recap"]
        else:
            wants = [f"{_d.month}/{_d.day} PrizePicks Recap",
                     f"{_d.month}/{_d.day} Premium List"]
        async for msg in channel.history(limit=40):
            for emb in (msg.embeds or []):
                if emb.title and any(w in emb.title for w in wants):
                    return True
        return False
    except Exception:  # noqa: BLE001
        log.exception("recap duplicate-check failed for %s — not posting", date_str)
        return True


async def _post_recap_for(channel, date_str: str, why: str,
                          source: str = "prizepicks") -> bool:
    """Post one day's recap for one SOURCE, guarded against duplicates.
    Returns True if sent."""
    if not AUTOPOST_ENABLED:
        log.info("recap: posting DISABLED (AUTOPOST_ENABLED off) — %s %s %s",
                 source, date_str, why)
        return False
    if await _recap_already_posted(channel, date_str, source):
        log.info("recap: %s %s already posted — skipping (%s)", source, date_str, why)
        return False
    rec = await asyncio.to_thread(results_tracker.get_record)
    _book = (rec or {}).get("underdog") if source == "underdog" else rec
    if not (_book and _book.get("total")):
        log.info("recap: no graded %s record yet — nothing to post for %s",
                 source, date_str)
        return False
    await channel.send(content="@everyone",
                       embed=daily_recap_embed(rec, target_date=date_str, source=source),
                       allowed_mentions=EVERYONE_MENTION)
    log.info("recap: posted %s %s -> track-record (%s)", source, date_str, why)
    return True


# ── THE NFL RECAP ────────────────────────────────────────────────────────────
# NFL picks were never graded at all: 407 rows sat PENDING across seven slates
# because nothing in the bot ever called nfl.recap.resolve. The recap module was
# written and then deliberately left unwired, and it stayed that way.
#
# WHAT WAS POSTED IS READ BACK OUT OF DISCORD, not out of the results table.
# That table holds every row the scanner priced — 131 for 2026-09-20 against the
# 8 that went out — and nfl.board.record_posted writes to a file on the bot's own
# container that does not survive a redeploy. The message in the channel is the
# only durable record of what subscribers were actually shown, so a recap that
# claims to describe the board has to read the board.
NFL_RECAP_LOOKBACK_DAYS = int(os.getenv("NFL_RECAP_LOOKBACK_DAYS", "3") or "3")

# "🔴 **UNDER 48.5 RUSH YARDS** · Proj 25.9 · 87%" and the bolded name above it.
_NFL_NAME_RE = re.compile(r"\*\*(?:\d+\.\s*)?([A-Za-z.'\- ]+?)\*\*")
_NFL_SIDE_RE = re.compile(r"\*\*(OVER|UNDER)\s+([\d.]+)")


def _nfl_plays_in_embed(embed) -> list:
    """[(player, lean, line)] from one posted board embed."""
    out, name = [], None
    chunks = [embed.description or ""] + [f.value or "" for f in (embed.fields or [])]
    for chunk in chunks:
        for line in chunk.split("\n"):
            side = _NFL_SIDE_RE.search(line)
            if side and name:
                out.append((name, side.group(1), float(side.group(2))))
                name = None
                continue
            m = _NFL_NAME_RE.search(line)
            if m and not side:
                nm = m.group(1).strip()
                if len(nm) > 3:
                    name = nm
    return out


async def _nfl_posted_plays(slate: str) -> dict:
    """{book: [(player, lean, line)]} for the board posted for this slate."""
    from datetime import date as _date
    try:
        y, m, d = (int(x) for x in slate.split("-"))
        tag = f"{_date(y, m, d).month}/{_date(y, m, d).day}"
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    npost = _nfl_import("nfl.post")
    for book in ("prizepicks", "underdog"):
        cid = npost.channel_for("board", book)
        ch = client.get_channel(cid) if cid else None
        if ch is None:
            continue
        plays = []
        async for msg in ch.history(limit=40):
            if msg.author.id != client.user.id or not msg.embeds:
                continue
            if tag not in (msg.embeds[0].title or ""):
                continue
            for e in msg.embeds:
                plays += [p for p in _nfl_plays_in_embed(e) if p not in plays]
            if plays:
                break
        if plays:
            out[book] = plays
    return out


def _nfl_match_rows(plays: list, picks: list, book: str) -> list:
    """The stored row for each posted play, matched on the NEAREST line.

    Not the exact line: what the embed shows is the line AT POST TIME, and the
    stored row can carry a later one because the results table dedupes on
    (book, slate, player, prop) and a subsequent write updates it. On 2026-09-20
    Garrett Wilson went out at receptions UNDER 6.0 and is stored at 5.5, and
    Chris Olave at OVER 5.5 stored at 6.0 — both moved inside the half hour
    between the scan and the post, and exact matching reported 3-1 instead of
    5-1. The lean must still agree, and the nearest line is what separates
    Jefferson's receptions 6.5 from his receiving yards 74.5.
    """
    rows = []
    for name, lean, line in plays:
        cands = [p for p in picks
                 if p.get("book") == book and p.get("player") == name
                 and (p.get("lean") or "").upper() == lean]
        if cands:
            rows.append(min(cands,
                            key=lambda p: abs(float(p.get("line") or 0) - line)))
    return rows


NFL_BOOK_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}


def nfl_recap_embed(slate: str, book: str, rows: list, w: int, lo: int):
    """ONE BOOK, ONE RECAP — the NFL twin of daily_recap_embed.

    Separate per book exactly as tennis is (see _post_recap_for, which takes a
    `source` and posts once for each). The two books price different lines off
    different menus, so a combined record describes a card nobody was shown —
    on 2026-09-20 PrizePicks went 5-1 and Underdog 3-2, and "8-3" is not a thing
    either subscriber saw. Keeping them apart also means a late game on one book
    cannot hold the other book's recap back.
    """
    dec = w + lo
    pct = f"{w / dec * 100:.0f}%" if dec else "—"
    label = NFL_BOOK_LABEL.get(book, book.title())
    e = discord.Embed(
        title=f"🏈 NFL {label} Recap — {slate}",
        colour=(COLOR_OVER if w > lo else
                COLOR_UNDER if lo > w else COLOR_NEUTRAL),
        description=f"**{w}-{lo}**  ·  {pct}  ·  {len(rows)} play(s)")
    lines = []
    for p in rows:
        res = p.get("result") or "PENDING"
        mark = {"W": "✅", "L": "❌", "PUSH": "➖", "VOID": "⚪"}.get(res, "•")
        av = p.get("result_value")
        prop = (p.get("prop_type") or "").replace("_", " ").title()
        lines.append(
            f"{mark} **{p.get('player')}** {p.get('lean')} {p.get('line'):g} "
            f"{prop}" + (f" — {av:g}" if isinstance(av, (int, float)) else ""))
    if lines:
        e.add_field(name="Plays", value="\n".join(lines)[:1024], inline=False)
    e.set_footer(text=FOOTER_GENERIC)
    return e


async def _maybe_post_nfl_recap():
    """Post an NFL recap for any recent slate whose POSTED plays have all settled.

    Same rule the tennis recap follows: one pick still pending holds the whole
    day back, because an incomplete recap is worse than a late one.
    """
    if not TRACK_RECORD_CHANNEL_ID or not AUTOPOST_ENABLED or not NFL_TASKS_ENABLED:
        return
    channel = client.get_channel(TRACK_RECORD_CHANNEL_ID)
    if channel is None:
        return
    nrecap = _nfl_import("nfl.recap")
    today = datetime.datetime.now(POD_TZINFO).date()
    for back in range(1, NFL_RECAP_LOOKBACK_DAYS + 1):
        slate = str(today - datetime.timedelta(days=back - 1))
        try:
            # Grade first — nothing else in the bot ever calls this.
            await asyncio.to_thread(nrecap.resolve, slate, None, None, None, True)
            plays = await _nfl_posted_plays(slate)
            if not plays:
                continue
            rec = await asyncio.to_thread(nrecap.record, slate)
            picks = (rec or {}).get("picks") or []
            # EACH BOOK STANDS ALONE, decided and posted on its own — a late
            # game on one must not hold the other's recap.
            for book, pl in plays.items():
                rows = _nfl_match_rows(pl, picks, book)
                if not rows:
                    continue
                pending = sum(1 for p in rows
                              if (p.get("result") or "PENDING") == "PENDING")
                if pending:
                    log.info("NFL recap %s %s: %d of %d posted play(s) still "
                             "pending — holding", slate, book, pending, len(rows))
                    continue
                if await _nfl_recap_already_posted(channel, slate, book):
                    continue
                w = sum(1 for p in rows if p.get("result") == "W")
                lo = sum(1 for p in rows if p.get("result") == "L")
                await channel.send(
                    content="@everyone",
                    embed=nfl_recap_embed(slate, book, rows, w, lo),
                    allowed_mentions=EVERYONE_MENTION)
                log.warning("NFL recap posted for %s %s (%d-%d) -> track-record",
                            slate, book, w, lo)
        except Exception:  # noqa: BLE001 — one slate must not stop the others
            log.exception("NFL recap failed for %s", slate)


async def _nfl_recap_already_posted(channel, slate: str, book: str) -> bool:
    """True when THIS BOOK's recap for this day is already in the channel.

    Keyed on book as well as date: the two books post separately, so one being
    up must not suppress the other.
    """
    want = f"🏈 NFL {NFL_BOOK_LABEL.get(book, book.title())} Recap — {slate}"
    try:
        async for msg in channel.history(limit=60):
            for e in (msg.embeds or []):
                if (e.title or "").strip() == want:
                    return True
    except Exception:  # noqa: BLE001 — never block a post on a history read
        log.exception("NFL recap dedupe check failed")
    return False


async def _maybe_post_ready_recap():
    """After a resolve pass: post the recap for any slate day that is DONE.

    A day is done ONLY when every pick on that day's card is settled (W/L/PUSH/
    VOID). Any pick still pending holds the whole day back, however old it is —
    an incomplete recap is worse than a late one. Looks back three days
    (oldest first) so a day held up by a late match still posts once it lands."""
    chan_id = TRACK_RECORD_CHANNEL_ID
    if not chan_id or not AUTOPOST_ENABLED:
        return
    # RESULTS_SKIP_DATE is checked HERE, not only in daily_results_post, because
    # this function is the one that actually posts and it has a second caller:
    # daily_resolve_results runs every 2 hours AND on startup. Guarding only the
    # midnight job meant setting the switch did not stop a recap — the resolve
    # loop posted it anyway a couple of hours later. The name promises "no recap
    # today"; now every path honours that.
    if (RESULTS_SKIP_DATE
            and datetime.datetime.now(POD_TZINFO).strftime("%Y-%m-%d")
            == RESULTS_SKIP_DATE):
        log.info("recap: skip-date %s — not posting any book's recap today",
                 RESULTS_SKIP_DATE)
        return
    channel = client.get_channel(chan_id)
    if channel is None:
        log.warning("recap: channel %s not found", chan_id)
        return
    rec = await asyncio.to_thread(results_tracker.get_record)
    now = datetime.datetime.now(POD_TZINFO)
    today = now.strftime("%Y-%m-%d")

    # Each BOOK is settled and posted independently: Underdog's board finishing
    # must not hold PrizePicks' recap, or vice versa. Sources are evaluated in
    # order, and at most one recap posts per pass.
    _books = [("prizepicks", [p for p in ((rec or {}).get("picks") or [])
                              if not p.get("excluded_from_record")]),
              ("underdog", [p for p in (((rec or {}).get("underdog") or {}).get("picks") or [])
                            if not p.get("excluded_from_record")])]

    picks = _books[0][1]                      # batch hold below is PrizePicks-only

    def _day_ready(d):
        dp = [p for p in picks if _slate_date_of(p) == d]
        return bool(dp) and not [p for p in dp if p.get("result") not in _GRADED_RESULTS]

    # ONE-OFF BATCH HOLD — release the listed days together or not at all.
    # PRIZEPICKS ONLY (2026-08-04): it must not gate another book. Underdog
    # settles on its own schedule and its recap should go out as soon as its day
    # is complete, so when the batch is holding we SKIP prizepicks rather than
    # returning, and the loop below still reaches the other books.
    _skip_prizepicks = False
    if RECAP_BATCH_DATES:
        _unposted = [d for d in RECAP_BATCH_DATES
                     if not await _recap_already_posted(channel, d)]
        if _unposted:
            _not_ready = [d for d in _unposted if not _day_ready(d)]
            if _not_ready:
                log.info("recap: BATCH HOLD %s — waiting on %s (prizepicks held; "
                         "other books unaffected)",
                         ",".join(RECAP_BATCH_DATES), ",".join(_not_ready))
                _skip_prizepicks = True
            else:
                for d in sorted(_unposted):
                    await _post_recap_for(channel, d, "batched release")
                return

    for _src, _src_picks in _books:
        if _src == "prizepicks" and _skip_prizepicks:
            continue
        if not _src_picks:
            continue
        for _off in (3, 2, 1, 0):               # oldest first, TODAY included
            day = (now - datetime.timedelta(days=_off)).strftime("%Y-%m-%d")
            # TODAY may post once its card is complete (2026-08-04). "All picks
            # settled" is the real test of a finished day, and waiting for the
            # calendar to roll over just delays a finished recap by hours.
            #
            # THE 8 AM WAIT IS GONE WITH THE WAVE THAT CAUSED IT.
            # This used to hold a finished PrizePicks card until 9 AM, because a
            # second wave landed at 8 AM and posting before it could publish a
            # recap the wave would immediately orphan. The additional-plays
            # scans were removed on 2026-09-22, so nothing can add to today's
            # card any more — and the guard outlived its reason, sitting on a
            # complete 9/23 card for hours after the last match settled at
            # ~4:30 AM. A recap that waits for a scan that cannot run is just a
            # late recap.
            #
            # If an additional-plays scan is ever brought back on a schedule
            # (not the one-off EVENING_RESCAN_DATE, which lands at 8 PM and so
            # cannot orphan a morning recap), this wait has to come back with
            # it — keyed to that scan's hour, not hardcoded to 9.
            day_picks = [p for p in _src_picks if _slate_date_of(p) == day]
            if not day_picks:
                continue
            blocking = [p for p in day_picks if p.get("result") not in _GRADED_RESULTS]
            if blocking:
                log.info("recap: %s %s NOT ready — %d pick(s) still pending: %s",
                         _src, day, len(blocking),
                         ", ".join((p.get("player") or "?") for p in blocking[:5]))
                continue
            if await _post_recap_for(channel, day,
                                     f"all {len(day_picks)} plays settled", _src):
                return                          # one recap per pass


@tasks.loop(hours=RESOLVE_EVERY_HOURS)
async def daily_resolve_results():
    """Periodic grader — every RESOLVE_EVERY_HOURS (2) and on startup. After each
    pass it posts the recap for any slate day that has finished settling."""
    try:
        await _resolve_all_pending()
    except Exception:  # noqa: BLE001
        log.exception("daily_resolve_results failed")
    try:
        await _maybe_post_ready_recap()
    except Exception:  # noqa: BLE001
        log.exception("event-driven recap check failed")
    try:
        await _maybe_post_nfl_recap()
    except Exception:  # noqa: BLE001 — Rule 2, NFL must never reach tennis
        log.exception("NFL recap check failed (tennis unaffected)")


@daily_resolve_results.before_loop
async def _before_resolve():
    await client.wait_until_ready()


# ── Feature 1 — daily win/loss record auto-post (replaces the /results command) ──
@tasks.loop(time=_sched_times(ONEOFF_RECAP_HM, RESULTS_POST_HOUR, RESULTS_POST_MINUTE))
async def daily_results_post():
    """The daily recap. Registered at BOTH the one-off slot (3:00 AM on
    ONEOFF_SCHED_DATE, dormant) and the recurring slot (5:00 PM ET); _slot_is_live
    picks which firing runs so the day never posts the recap twice."""
    if not _slot_is_live(ONEOFF_RECAP_HM):
        return
    # Recap posts to the PUBLIC track-record channel ONLY (2026-07-29) — never the
    # premium POTD channel. The board / ⭐ / 3x stay in POTD via daily_picks_generate
    # (untouched). channel-not-found below skips safely if the bot can't see this id.
    chan_id = TRACK_RECORD_CHANNEL_ID
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
        # 1) Resolve first, so anything that finished overnight is graded.
        try:
            await _resolve_all_pending()
        except Exception:  # noqa: BLE001
            log.exception("pre-recap resolve failed")

        # 2) BACKSTOP (2026-08-02, corrected 2026-08-03). This slot used to FORCE a
        # post of yesterday's date, which bypassed the all-settled rule entirely —
        # on 8/3 it published the 8/2 recap at 08:45 with SEVEN picks still pending
        # (rain-delayed Toronto matches now playing today). A backstop must not be
        # able to publish a half-finished day.
        #
        # It now runs the SAME readiness check as the 2-hourly resolver: post only
        # a day whose every pick is settled. That keeps its real purpose — catching
        # a day the event path somehow missed — without inventing a second, weaker
        # rule for when a recap may go out.
        if not AUTOPOST_ENABLED:
            log.info("daily results: recap posting DISABLED (AUTOPOST_ENABLED off) — "
                     "resolved pending, not posting the recap")
            return
        await _maybe_post_ready_recap()

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
        def _post_baseline(p):
            """Generated on/after the calibration baseline — i.e. scored by the
            CURRENT model. Anything earlier came from a different one."""
            ga = p.get("generated_at") or ""
            return bool(ga) and ga[:19] >= CALIBRATION_BASELINE_UTC

        _all_dec = [p for p in picks if p.get("result") in ("W", "L")
                    and isinstance(p.get("confidence"), (int, float)) and _recent(p)]
        dec = [p for p in _all_dec
               if _post_baseline(p) and not int(p.get("pre_guard") or 0)]
        _excluded = len(_all_dec) - len(dec)
        log.info("CALIBRATION | rolling 30d | %d decided picks on the CURRENT model "
                 "(%d excluded: generated before the %s baseline, or flagged "
                 "pre_guard). Two model breaks landed 2026-07-15 — data-integrity "
                 "fixes and the games_per_set per-tour fit — and a hit rate pooled "
                 "across them measures two models averaged together.",
                 len(dec), _excluded, CALIBRATION_BASELINE_UTC)
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


# ── STRIPE -> DISCORD ROLE SYNC ──────────────────────────────────────────────
# Someone who subscribes on the web is not in the Discord yet, or is in it
# without the premium role. This grants it so a paying subscriber gets the
# channels too, and takes it back when they stop paying.
#
# IT ONLY EVER TOUCHES PEOPLE WITH A STRIPE RECORD. The backend's revoke list
# contains only ids whose subscription has LAPSED — never someone it has no
# record of. The premium role is also handed out by Discord's own server
# subscriptions, by comps and by hand, and a sync that removed the role from
# "everyone not currently paying us through Stripe" would strip every one of
# those members the first time it ran. That is the failure this shape exists to
# make impossible.
# 3 minutes, not 15: a failed payment must cost the role promptly, and the
# sync is one cheap HTTP call plus a role edit only when something actually
# changed — it does not walk the member list.
SUB_SYNC_MINUTES = int(os.getenv("SUB_SYNC_MINUTES", "3") or "3")
SUB_SYNC_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
SUB_SYNC_ROLE_ID = int(
    (os.getenv("DISCORD_PREMIUM_ROLE_IDS", "").split(",")[0] or "0").strip() or 0)
BILLING_SYNC_TOKEN = os.getenv("BILLING_SYNC_TOKEN", "").strip()

# ── COMPS BEAT STRIPE (2026-09-24, operator) ─────────────────────────────────
# The revoke list is already narrow — database.subscription_role_sets only ever
# returns people with a LAPSED Stripe record, so someone comped who never paid
# is invisible to it and safe. THE GAP is the person who paid ONCE and was
# comped LATER: they carry a lapsed record, so the sync revokes a role the
# operator granted by hand, every 3 minutes, forever.
#
# That is what happened here. The only way to stop it was to drop the bot below
# the premium role in the hierarchy, which breaks the sync for EVERYONE — real
# lapsed subscribers keep their role too, and the log fills with 403s.
#
# Two independent ways to mark a comp, because they suit different moments:
#
#   SUB_SYNC_COMP_ROLE_ID — a second Discord role meaning "comped". The better
#     one: granted in the same UI where the premium role is granted, visible to
#     anyone looking at the member, and adding a comp needs no deploy.
#   SUB_SYNC_COMP_IDS — a comma-separated id allowlist, for when a role does not
#     exist yet or a one-off is faster than server admin.
#
# Both only ever SKIP a revoke. Neither can grant anything, so a mistake here
# cannot hand out access — it can only decline to take it away.
SUB_SYNC_COMP_ROLE_ID = int(os.getenv("SUB_SYNC_COMP_ROLE_ID", "0") or 0)
SUB_SYNC_COMP_IDS = {s.strip() for s in
                     os.getenv("SUB_SYNC_COMP_IDS", "").split(",") if s.strip()}


@tasks.loop(minutes=SUB_SYNC_MINUTES)
async def subscription_role_sync():
    if not (BILLING_SYNC_TOKEN and SUB_SYNC_GUILD_ID and SUB_SYNC_ROLE_ID):
        return
    try:
        r = await asyncio.to_thread(
            requests.get, f"{API_BASE}/api/billing/subscribers",
            params={"token": BILLING_SYNC_TOKEN}, timeout=30)
        if r.status_code != 200:
            log.warning("sub role sync: backend HTTP %s", r.status_code)
            return
        data = r.json() or {}
        grant = [str(x) for x in (data.get("grant") or [])]
        revoke = [str(x) for x in (data.get("revoke") or [])]
    except Exception:
        log.exception("sub role sync: could not reach backend")
        return
    if not grant and not revoke:
        return

    guild = client.get_guild(SUB_SYNC_GUILD_ID)
    if guild is None:
        log.warning("sub role sync: guild %s not visible to the bot", SUB_SYNC_GUILD_ID)
        return
    role = guild.get_role(SUB_SYNC_ROLE_ID)
    if role is None:
        log.warning("sub role sync: role %s not found", SUB_SYNC_ROLE_ID)
        return

    added = removed = 0
    for did in grant:
        try:
            m = guild.get_member(int(did)) or await guild.fetch_member(int(did))
        except Exception:
            continue          # not in the server — nothing to grant, not an error
        if m and role not in m.roles:
            try:
                await m.add_roles(role, reason="Baseline: active Stripe subscription")
                added += 1
            except Exception:
                log.exception("sub role sync: add failed for %s", did)
    comp_role = guild.get_role(SUB_SYNC_COMP_ROLE_ID) if SUB_SYNC_COMP_ROLE_ID else None
    if SUB_SYNC_COMP_ROLE_ID and comp_role is None:
        log.warning("sub role sync: comp role %s not found — comps by role are "
                    "NOT protected this cycle", SUB_SYNC_COMP_ROLE_ID)
    skipped = 0
    for did in revoke:
        try:
            m = guild.get_member(int(did)) or await guild.fetch_member(int(did))
        except Exception:
            continue
        if not (m and role in m.roles):
            continue
        # A COMP OVERRIDES A LAPSED STRIPE RECORD. Checked before the removal,
        # never after — see the SUB_SYNC_COMP_* block. This is the only thing
        # standing between a hand-granted role and a sync that would strip it
        # every 3 minutes.
        if str(did) in SUB_SYNC_COMP_IDS:
            skipped += 1
            log.info("sub role sync: %s is on the comp id list — keeping the role", did)
            continue
        if comp_role is not None and comp_role in m.roles:
            skipped += 1
            log.info("sub role sync: %s holds %s — comped, keeping the role",
                     did, comp_role.name)
            continue
        try:
            await m.remove_roles(role, reason="Baseline: subscription lapsed")
            removed += 1
        except discord.Forbidden:
            # The bot sits at or below the premium role in the hierarchy, so it
            # cannot edit it. Logged as a one-line WARNING rather than a full
            # traceback: it repeats every cycle and the stack says nothing the
            # message does not. Fix is a Discord hierarchy change, not code.
            log.warning("sub role sync: cannot remove %s from %s — bot role is "
                        "not above %s in the hierarchy", role.name, did, role.name)
        except Exception:
            log.exception("sub role sync: remove failed for %s", did)
    if added or removed or skipped:
        log.info("sub role sync: +%d role(s), -%d role(s), %d comp(s) kept",
                 added, removed, skipped)


@subscription_role_sync.before_loop
async def _before_subscription_role_sync():
    await client.wait_until_ready()


@client.event
async def on_ready():
    global _guild_synced

    # Adopt the backend's court list before any autocomplete can run. Best
    # effort: a failure leaves the shipped snapshot in place, which is the same
    # list, just frozen at deploy time. Never fatal — the bot must come up.
    try:
        _payload = await backend_get("/api/courts", {}, 10)
        _n = refresh_courts(_payload)
        if _n:
            log.info("courts refreshed from backend: %d tournaments", _n)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("courts refresh failed (%s) — using shipped snapshot of %d",
                    exc, len(_flatten(TOURNAMENTS_BY_TOUR)))

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
    # The 8 PM rescan — ARMED FOR ONE DATE ONLY (EVENING_RESCAN_DATE). Its own
    # error boundary so a failure here cannot touch the 5 PM board.
    # DIAGNOSTIC (2026-09-24): this guard evaluated False on three consecutive
    # restarts while every term looked true from outside the container —
    # EVENING_RESCAN_DATE was set and agreed between the CLI and the container,
    # POD_CHANNEL_ID appears in the POTD trigger log one block above, and
    # start() never raised (no "failed to start" line, no traceback). The 8 PM
    # rescan silently never armed and the operator's rescan did not go out.
    # Printing the three terms is the only way left to see which one is lying;
    # it costs one line per restart.
    log.warning("evening rescan gate | POD_CHANNEL_ID=%s | EVENING_RESCAN_DATE=%r "
                "| is_running=%s", POD_CHANNEL_ID, EVENING_RESCAN_DATE,
                evening_rescan.is_running())
    if (SECOND_SCANS_ENABLED and POD_CHANNEL_ID and EVENING_RESCAN_DATE
            and not evening_rescan.is_running()):
        try:
            evening_rescan.start()
            log.warning("Evening rescan ARMED for %s at %02d:%02d %s "
                        "(max %d, >=%d%% confidence, ordered by room) -> channel %s",
                        EVENING_RESCAN_DATE, EVENING_RESCAN_HOUR,
                        EVENING_RESCAN_MINUTE, POD_TZINFO, EVENING_RESCAN_MAX,
                        EVENING_RESCAN_MIN_CONF, POD_CHANNEL_ID)
        except Exception:  # noqa: BLE001
            log.exception("failed to start evening rescan loop")
    # Underdog board — a SECOND book on its own 10:30 PM schedule, scored
    # separately. Started independently so a failure here can never affect the
    # PrizePicks board or its record.
    if UNDERDOG_CHANNEL_ID and not daily_underdog_board.is_running():
        try:
            daily_underdog_board.start()
            log.info("Underdog board scheduled at %02d:%02d %s -> channel %s",
                     UNDERDOG_HOUR, UNDERDOG_MINUTE, POD_TZINFO,
                     UNDERDOG_CHANNEL_ID)
        except Exception:
            log.exception("failed to start underdog board loop")
    # Underdog pre-warm — 15 min before the Underdog board. Started separately
    # from the board itself so a pre-warm failure can never stop the board.
    if POD_CHANNEL_ID and not underdog_cache_prewarm.is_running():
        try:
            underdog_cache_prewarm.start()
            log.info("Underdog pre-warm scheduled at %02d:%02d %s (board at %02d:%02d)",
                     UNDERDOG_PREWARM_HOUR, UNDERDOG_PREWARM_MINUTE, POD_TZINFO,
                     UNDERDOG_HOUR, UNDERDOG_MINUTE)
        except Exception:
            log.exception("failed to start underdog pre-warm loop")
    # Stripe -> Discord role sync. Wrapped so a failure here cannot stop the
    # tennis loops from starting.
    try:
        if BILLING_SYNC_TOKEN and SUB_SYNC_GUILD_ID and SUB_SYNC_ROLE_ID:
            if not subscription_role_sync.is_running():
                subscription_role_sync.start()
                log.info("Subscription role sync every %dm -> role %s",
                         SUB_SYNC_MINUTES, SUB_SYNC_ROLE_ID)
        else:
            log.info("Subscription role sync OFF (needs BILLING_SYNC_TOKEN, "
                     "DISCORD_GUILD_ID and DISCORD_PREMIUM_ROLE_IDS)")
    except Exception:
        log.exception("failed to start subscription role sync")

    # MLB — separate sport, separate channels, separate database. Wrapped so a
    # failure to start MLB can never prevent a tennis loop from starting.
    if MLB_TASKS_ENABLED:
        log.warning("MLB startup: tasks enabled, test_run=%s", MLB_TEST_RUN)
        # Each start gets its OWN boundary. Previously one try wrapped all three,
        # so a failure in the first .start() aborted the block and the test run
        # never fired — a silent skip that looked identical to "not configured".
        try:
            if not mlb_daily_boards.is_running():
                mlb_daily_boards.start()
                log.warning("MLB boards scheduled at %02d:%02d %s",
                            MLB_BOARD_HOUR, MLB_BOARD_MINUTE, POD_TZINFO)
        except Exception:
            log.exception("failed to start MLB board loop (tennis unaffected)")
        try:
            if not mlb_line_watch.is_running():
                mlb_line_watch.start()
                log.warning("MLB line watch every %dm -> channel %s",
                            MLB_LINE_CHECK_MINUTES,
                            os.getenv("MLB_LINE_CHANGE_CHANNEL_ID",
                                      "1536214940288024587"))
        except Exception:
            log.exception("failed to start MLB line watch (tennis unaffected)")
        try:
            if MLB_BATTER_BOARD and not mlb_batter_boards.is_running():
                mlb_batter_boards.start()
                log.warning("MLB batter board scheduled at %02d:%02d %s",
                            MLB_BATTER_BOARD_HOUR, MLB_BATTER_BOARD_MINUTE,
                            POD_TZINFO)
            elif not MLB_BATTER_BOARD:
                log.warning("MLB batter board OFF (set MLB_BATTER_BOARD=true; "
                            "needs posted lineups, so it runs in the afternoon)")
        except Exception:
            log.exception("failed to start MLB batter loop (tennis unaffected)")
        try:
            if not mlb_resolve_and_recap.is_running():
                mlb_resolve_and_recap.start()
                log.warning("MLB resolve/recap every %dh", MLB_RESOLVE_EVERY_HOURS)
        except Exception:
            log.exception("failed to start MLB resolve loop (tennis unaffected)")
        try:
            if MLB_PURGE_SLATE:
                asyncio.create_task(_mlb_purge_once())
                log.warning("MLB_PURGE_SLATE=%s — one-shot purge scheduled",
                            MLB_PURGE_SLATE)
        except Exception:
            log.exception("failed to schedule MLB purge (tennis unaffected)")
        try:
            if MLB_DEDUPE_RECORD:
                asyncio.create_task(_mlb_dedupe_record_once())
                log.warning("MLB_DEDUPE_RECORD=%s — one-shot record dedupe "
                            "scheduled", MLB_DEDUPE_RECORD)
        except Exception:
            log.exception("failed to schedule MLB dedupe-record (tennis unaffected)")
        try:
            if MLB_REGRADE_SLATE:
                asyncio.create_task(_mlb_regrade_once())
                log.warning("MLB_REGRADE_SLATE=%s — one-shot re-grade scheduled",
                            MLB_REGRADE_SLATE)
        except Exception:
            log.exception("failed to schedule MLB regrade (tennis unaffected)")
        try:
            if MLB_STORE_NOW:
                asyncio.create_task(_mlb_store_now_once())
                log.warning("MLB_STORE_NOW=%s — one-shot persist scheduled "
                            "(no posting)", MLB_STORE_NOW)
        except Exception:
            log.exception("failed to schedule MLB store-now (tennis unaffected)")
        try:
            if MLB_PURGE_ALL:
                asyncio.create_task(_mlb_purge_all_once())
                log.warning("MLB_PURGE_ALL set — FULL record wipe scheduled")
        except Exception:
            log.exception("failed to schedule MLB purge-all (tennis unaffected)")
        try:
            if MLB_RUN_NOW:
                asyncio.create_task(_mlb_run_now())
                log.warning("MLB_RUN_NOW set — one-shot board scheduled "
                            "(boards only, no grading, no recap)")
        except Exception:
            log.exception("failed to schedule MLB run-now (tennis unaffected)")
        try:
            if MLB_TEST_RUN:
                # asyncio.create_task, NOT client.loop.create_task: in discord.py
                # 2.x Client.loop can be the MISSING sentinel and attribute access
                # on it raises — which the old single boundary swallowed.
                asyncio.create_task(_mlb_one_shot_test())
                log.warning("MLB_TEST_RUN set — one-shot test scheduled")
        except Exception:
            log.exception("failed to schedule MLB test run (tennis unaffected)")
    else:
        log.warning("MLB startup: MLB_TASKS_ENABLED is FALSE — no MLB tasks")

    # ── NFL ──────────────────────────────────────────────────────────────
    # DELIBERATELY OUTSIDE the MLB_TASKS_ENABLED block. The first cut of this
    # nested it there and NFL silently never started, because MLB is currently
    # switched off — one sport's kill switch must never gate another's. Each
    # start below is its own error boundary, and a broken NFL module can reach
    # neither the other sports nor tennis.
    try:
        if NFL_TASKS_ENABLED and not nfl_daily_boards.is_running():
            nfl_daily_boards.start()
            _N = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            _days = ", ".join(f"{_N[d]}->{_N[(d + 1) % 7]} {tag}"
                              for d, tag in sorted(NFL_BOARD_EVES.items()))
            log.warning("NFL boards scheduled %s at %02d:%02d %s -> pp=%s ud=%s",
                        _days, NFL_BOARD_HOUR, NFL_BOARD_MINUTE, POD_TZINFO,
                        os.getenv("NFL_PP_CHANNEL_ID", "1546942099268706417"),
                        os.getenv("NFL_UD_CHANNEL_ID", "1546942176259346482"))
        elif not NFL_TASKS_ENABLED:
            log.warning("NFL tasks OFF (set NFL_TASKS_ENABLED=true)")
    except Exception:  # noqa: BLE001
        log.exception("failed to start NFL board loop (tennis unaffected)")
    # The Friday->Sunday and Sunday-night->Monday loops were removed on
    # 2026-09-18: nfl_daily_boards now covers all three game days itself.
    try:
        if NFL_TASKS_ENABLED and not nfl_board_refresh.is_running():
            nfl_board_refresh.start()
            log.warning("NFL website board refresh every %dm",
                        NFL_BOARD_REFRESH_MINUTES)
    except Exception:  # noqa: BLE001
        log.exception("failed to start NFL board refresh (tennis unaffected)")
    try:
        if NFL_TASKS_ENABLED and not nfl_line_watch.is_running():
            nfl_line_watch.start()
            log.warning("NFL line watch every %dm -> channel %s",
                        NFL_LINE_CHECK_MINUTES,
                        os.getenv("NFL_LINE_CHANGE_CHANNEL_ID",
                                  "1546943210574708848"))
    except Exception:  # noqa: BLE001
        log.exception("failed to start NFL line watch (tennis unaffected)")
    # Prove the data layer works in THIS container before anyone asks it a
    # question — see _nfl_selfcheck.
    try:
        asyncio.create_task(_nfl_selfcheck())
    except Exception:  # noqa: BLE001
        log.exception("failed to schedule NFL selfcheck (tennis unaffected)")
    try:
        if NFL_POST_INTRO:
            asyncio.create_task(_nfl_post_intro())
            log.warning("NFL_POST_INTRO=1 — one-shot intro post scheduled")
    except Exception:  # noqa: BLE001
        log.exception("failed to schedule NFL intro (tennis unaffected)")
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
    if (SECOND_SCANS_ENABLED and POD_CHANNEL_ID and POD_EXTRA_RUN_DATE
            and not extra_pod_run.is_running()):
        try:
            extra_pod_run.start()
            log.info("One-off extra POTD run scheduled %02d:%02d %s on %s",
                     POD_EXTRA_RUN_HOUR, POD_EXTRA_RUN_MINUTE, POD_TZINFO, POD_EXTRA_RUN_DATE)
        except Exception:
            log.exception("failed to start extra POTD run loop")

    # One-off extension scan: re-scan the board and post ONLY plays that were not
    # already posted today. Date-gated; no-op on other days.
    if SECOND_SCANS_ENABLED and POD_CHANNEL_ID and not extension_pod_run.is_running():
        try:
            extension_pod_run.start()
            log.info("One-off extension scan scheduled %02d:%02d %s on %s",
                     ONEOFF_EXT_HM[0], ONEOFF_EXT_HM[1], POD_TZINFO, ONEOFF_SCHED_DATE)
        except Exception:
            log.exception("failed to start extension scan loop")

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

    # PART 5 — weekly confidence-calibration log (Railway logs only).
    if not weekly_calibration_log.is_running():
        try:
            weekly_calibration_log.start()
            log.info("Weekly calibration log scheduled Mon 09:30 %s", POD_TZINFO)
        except Exception:
            log.exception("failed to start weekly calibration log loop")

    # Feature 2 — resume the line-movement monitor after a restart. A redeploy kills
    # the in-memory monitor task; without this it stays dead until the next board
    # post, silently ending line alerts for the day. No-op when nothing is pending.
    await _resume_line_monitor_on_startup()

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

    # One-shot Underdog board on startup — the Underdog equivalent of
    # POD_POST_ON_START, for reposting after a channel change without waiting
    # for 10:30 PM. track=True because this IS the day's board, not a test: an
    # untracked post would show plays that never enter the record and can never
    # be graded. Unset UNDERDOG_POST_ON_START after it fires, or every restart
    # re-posts.
    global _ud_startup_done
    if UNDERDOG_POST_ON_START and UNDERDOG_CHANNEL_ID and not _ud_startup_done:
        _ud_startup_done = True
        ch = client.get_channel(UNDERDOG_CHANNEL_ID)
        if ch is None:
            log.warning("UD startup post: channel %s not found / not visible "
                        "to the bot", UNDERDOG_CHANNEL_ID)
        else:
            try:
                status = await _post_underdog_board(ch, track=True)
                log.warning("UD startup post -> %s (channel %s)", status,
                            UNDERDOG_CHANNEL_ID)
            except discord.Forbidden:
                log.error("UD startup post: missing Send Messages / Embed Links "
                          "in channel %s", UNDERDOG_CHANNEL_ID)
            except Exception:
                log.exception("UD startup post failed")

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
