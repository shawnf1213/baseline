"""The live tennis prop market, parsed server-side for the app.

The website fetches PrizePicks and Underdog in the browser (through Vercel
rewrites) and parses them client-side in frontend/src/mobile/data.js. A phone
should not pull Underdog's ~16 MB all-sports payload over cellular to find a
few hundred tennis lines, so the same parse runs here and the app receives
the slim result.

THE PARSE MIRRORS THE BOT — discord-bot/pick_of_day._parse_board for
PrizePicks and discord-bot/underdog.parse_tennis for Underdog: standard lines
only (no demon/goblin), singles only, Underdog straight two-way lines only,
live lines dropped. One deliberate difference: the bot's per-book EXCLUDED
props are not applied, because this is the market view the website's Board
shows, not the board that gets posted.

NOTHING IS PRICED HERE. Rows carry names, the line, and what the slate knows
(tour, surface, tournament, start time); the app prices each row on demand
through /api/prop/calculate, exactly as the website does.
"""
import logging
import threading
import time
import unicodedata
from datetime import datetime

import requests

logger = logging.getLogger("baseline.live_board")

# league_id=5 is PrizePicks' tennis league; the league name is still checked
# per row so a renumbering fails closed (zero rows) rather than serving
# another sport as tennis.
PRIZEPICKS_URL = ("https://partner-api.prizepicks.com/projections"
                  "?per_page=1000&league_id=5")
UNDERDOG_URL = "https://api.underdogfantasy.com/v1/over_under_lines"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}
TTL_SECONDS = 180          # a book's board, once parsed
SLATE_TTL_SECONDS = 600    # the slate join; Sofascore is the slow part
FETCH_TIMEOUT = 40

# PrizePicks stat_type (lowercased) -> Baseline prop type (bot PROP_MAP).
PP_PROP_MAP = {
    "aces": "Aces",
    "double faults": "Double Faults", "double fault": "Double Faults",
    "break points won": "Break Points Won",
    "total games": "Total Games",
    "total games won": "Player Total Games Won",
    "fantasy score": "Fantasy Score",
}
# Underdog display_stat -> Baseline prop type. Sets markets are left out, as
# the website does: /api/prop/calculate has no branch for them.
UD_PROP_MAP = {
    "Aces": "Aces",
    "Double Faults": "Double Faults",
    "Breakpoints Won": "Break Points Won",
    "Break Points Saved": "Break Points Saved",
    "Games Won": "Player Total Games Won",
    "Games Played": "Total Games",
}

_lock = threading.Lock()
_cache: dict = {}          # key -> (expires_at, value)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()


def _last(s: str) -> str:
    parts = _norm(s).split()
    return parts[-1] if parts else ""


def _get_cached(key: str):
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def _set_cached(key: str, value, ttl: int):
    with _lock:
        _cache[key] = (time.time() + ttl, value)


# ── slate join ───────────────────────────────────────────────────────────────
def _slate_maps() -> dict:
    """name -> {tour, surface, tournament, start_timestamp}, keyed on the full
    normalised name and, as a fallback, the last name. Cached; an empty dict
    on failure so the board still serves without enrichment."""
    hit = _get_cached("slate")
    if hit is not None:
        return hit
    maps: dict = {"full": {}, "last": {}, "date": None}
    try:
        from . import features
        slate = features.get_slate("") or {}
        maps["date"] = slate.get("date")
        for tour_key in ("atp", "wta"):
            for r in slate.get(tour_key) or []:
                info = {"tour": r.get("tour") or tour_key.upper(),
                        "surface": r.get("surface") or "",
                        "tournament": r.get("tournament") or "",
                        "start_timestamp": r.get("start_timestamp") or 0}
                for name in (r.get("p1"), r.get("p2")):
                    if not name:
                        continue
                    maps["full"][_norm(name)] = info
                    maps["last"].setdefault(_last(name), info)
    except Exception as exc:  # noqa: BLE001 — never let the join break the board
        logger.warning("live board: slate join unavailable: %s", exc)
    # A failed join is cached briefly too, so a Sofascore outage does not turn
    # every board request into a 60-second wait.
    _set_cached("slate", maps, SLATE_TTL_SECONDS if maps["full"] else 120)
    return maps


def _epoch(iso):
    """ISO-8601 from either book ("2026-10-06T00:00:00.000-04:00",
    "2026-10-07T04:00:00Z") -> epoch seconds, or None."""
    if not iso:
        return None
    try:
        s = str(iso).strip().replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:  # noqa: BLE001
        return None


def _enrich(rows: list, maps: dict) -> list:
    full, last = maps.get("full") or {}, maps.get("last") or {}
    for r in rows:
        info = full.get(_norm(r["player"])) or last.get(_last(r["player"])) or {}
        r["tour"] = info.get("tour") or ""
        r["surface"] = info.get("surface") or ""
        r["tournament"] = info.get("tournament") or ""
        # The slate's start time when the name joined; otherwise the book's
        # own start time, so the board can still sort soonest-first after the
        # day's slate has rolled over.
        r["start_timestamp"] = info.get("start_timestamp") or _epoch(r.get("starts_at"))
    return rows


# ── PrizePicks ───────────────────────────────────────────────────────────────
def _prizepicks_rows() -> list:
    r = requests.get(PRIZEPICKS_URL, headers=_HEADERS, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    board = r.json() or {}
    included = {(i.get("type"), i.get("id")): i for i in board.get("included", [])}
    out, seen = [], set()
    for proj in board.get("data", []) or []:
        attr = proj.get("attributes", {}) or {}
        prop_type = PP_PROP_MAP.get((attr.get("stat_type") or "").strip().lower())
        if not prop_type:
            continue
        if (attr.get("odds_type") or "standard").lower() != "standard":
            continue
        line = attr.get("line_score")
        if line is None:
            continue
        rel = proj.get("relationships", {}) or {}
        lref = (rel.get("league") or {}).get("data") or {}
        league = included.get((lref.get("type"), lref.get("id")), {})
        if "tennis" not in ((league.get("attributes") or {}).get("name") or "").lower():
            continue
        pref = (rel.get("new_player") or rel.get("player") or {}).get("data") or {}
        player = included.get((pref.get("type"), pref.get("id")), {})
        pname = (player.get("attributes") or {}).get("name") or ""
        opponent = (attr.get("description") or "").strip()
        if not pname or not opponent or "/" in pname or "/" in opponent:
            continue
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            continue
        key = (pname, prop_type, line_f)
        if key in seen:
            continue
        seen.add(key)
        out.append({"player": pname, "opponent": opponent, "prop_type": prop_type,
                    "line": line_f, "odds_type": "standard",
                    "starts_at": attr.get("start_time")})
    return out


# ── Underdog ─────────────────────────────────────────────────────────────────
def _is_straight(ln: dict) -> bool:
    opts = ln.get("options") or []
    if len(opts) < 2:
        return False
    if {o.get("choice") for o in opts} != {"higher", "lower"}:
        return False
    for o in opts:
        try:
            if abs(float(o.get("payout_multiplier")) - 1.0) > 1e-9:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _clean_ud(s: str) -> str:
    import re
    return re.sub(r"^\(\s*\d+\s*\)\s*", "", (s or "").strip()).strip()


def _underdog_rows() -> list:
    r = requests.get(UNDERDOG_URL, headers=_HEADERS, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    board = r.json() or {}
    players = {p.get("id"): p for p in (board.get("players") or [])
               if (p.get("sport_id") or "").upper() == "TENNIS"}
    apps = {a.get("id"): a for a in (board.get("appearances") or [])
            if a.get("player_id") in players}
    solo = {g.get("id"): g for g in (board.get("solo_games") or [])}
    out, seen = [], set()
    for ln in board.get("over_under_lines") or []:
        st = (ln.get("over_under") or {}).get("appearance_stat") or {}
        app = apps.get(st.get("appearance_id"))
        if not app or not _is_straight(ln) or ln.get("live_event"):
            continue
        prop = UD_PROP_MAP.get(st.get("display_stat"))
        if not prop:
            continue
        game = solo.get(app.get("match_id")) or {}
        pid = app.get("player_id")
        if pid == game.get("home_player_id"):
            opp = game.get("away_player_name")
        elif pid == game.get("away_player_id"):
            opp = game.get("home_player_name")
        else:
            opp = None
        pl = players.get(pid) or {}
        name = _clean_ud(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
        opponent = _clean_ud(opp)
        try:
            line = float(ln.get("stat_value"))
        except (TypeError, ValueError):
            continue
        if not name or not opponent or "/" in name or "/" in opponent:
            continue
        key = (name, prop, line)
        if key in seen:
            continue
        seen.add(key)
        # American prices arrive as strings ("-114"); the app wants numbers.
        over_px = under_px = None
        for o in ln.get("options") or []:
            try:
                px = int(float(o.get("american_price")))
            except (TypeError, ValueError):
                px = None
            if o.get("choice") == "higher":
                over_px = px
            elif o.get("choice") == "lower":
                under_px = px
        out.append({"player": name, "opponent": opponent, "prop_type": prop,
                    "line": line, "odds_type": "standard",
                    "over_price": over_px, "under_price": under_px,
                    "starts_at": game.get("scheduled_at")})
    return out


# ── the endpoint's answer ────────────────────────────────────────────────────
def live_board(book: str) -> dict:
    """{book, rows, count, available, slate_date, fetched_at}. Cached per book
    for TTL_SECONDS. `available` False means the feed could not be read — the
    app says so instead of showing an empty board as if nothing were listed."""
    book = (book or "prizepicks").lower()
    hit = _get_cached(f"board:{book}")
    if hit is not None:
        return hit
    try:
        rows = _underdog_rows() if book == "underdog" else _prizepicks_rows()
        available = True
    except Exception as exc:  # noqa: BLE001 — Rule 2: never raise at the edge
        logger.warning("live board fetch failed (%s): %s", book, str(exc)[:160])
        rows, available = [], False
    maps = _slate_maps()
    rows = _enrich(rows, maps)
    rows.sort(key=lambda r: (r.get("start_timestamp") or 1e12, r["player"], r["prop_type"]))
    out = {"book": book, "rows": rows, "count": len(rows), "available": available,
           "slate_date": maps.get("date"),
           "fetched_at": datetime.utcnow().isoformat() + "Z"}
    # A failed fetch is cached only briefly, so the next reader retries soon.
    _set_cached(f"board:{book}", out, TTL_SECONDS if available else 30)
    return out
