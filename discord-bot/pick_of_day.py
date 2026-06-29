"""
Pick of the Day — isolated Discord-bot feature.

Fetches the PrizePicks tennis board, fuzzy-matches players to Baseline, runs the
existing /api/prop/calculate projections, and returns the single best edge.

Fully self-contained and failure-isolated: every external call is wrapped so a
PrizePicks outage, a backend timeout, or zero matches returns None — it can
never crash the bot or affect any other command. Contains NO discord imports
(the command handler in bot.py builds the embed), so it stays decoupled.
"""

import os
import re
import asyncio
import logging
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone

import requests

log = logging.getLogger("baseline-bot.pickoftheday")

API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")

PRIZEPICKS_URL = "https://partner-api.prizepicks.com/projections?per_page=1000"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36"
)

# PrizePicks stat_type (lowercased) -> Baseline prop_type. Only these four.
# NOTE: PrizePicks has BOTH "Total Games" (the match total, what Baseline's
# "Total Games" projects) and "Total Games Won" (a single player's games won) —
# these are different stats. Baseline has no per-player games-won model, so
# "Total Games Won" is deliberately NOT mapped and is skipped.
PROP_MAP = {
    "aces":             "Aces",
    "double faults":    "Double Faults",
    "double fault":     "Double Faults",
    "break points won": "Break Points Won",
    "total games":      "Total Games",   # match total only — NOT "Total Games Won"
}

MIN_CONFIDENCE  = 60      # don't force a weak pick below this
MAX_CONCURRENT  = 1       # serialize backend calcs — the heavy prop calc 502s under
                          # concurrent load; one-at-a-time also warms its cache
MATCH_THRESHOLD = 0.80    # fuzzy name-match threshold
MAX_PROPS       = 25      # cap evaluations so the command stays responsive
MAX_LOOKAHEAD_HOURS = 24  # only pick matches that play within this many hours
# Every Pick of the Day must be at least 90% confidence. Raised from the old
# 65% Aces/BP bar after high-projection misses (e.g. Cilic Aces projected 15.2,
# 80% conf, finished with 2 in a 0-3 blowout). Below 90% we post no pick.
ACES_BP_MIN_CONF = 90     # Aces / Break Points Won: minimum confidence to use
TG_DF_MIN_CONF   = 90     # Total Games / Double Faults: minimum confidence to use

SEARCH_TIMEOUT = 10
CALC_TIMEOUT   = 90       # backend prop calc can be slow on a cold proxy cache
CALC_RETRIES   = 3        # retry timeouts + 5xx; first try also warms the backend cache


# ── small helpers ───────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


# Players excluded from Pick of the Day — known injured / off-form cases the data
# can't see (e.g. an injury "not on record"). POD_EXCLUDE env (comma-separated
# names) appends more without a code change. Matched as a normalised substring,
# so a surname is enough.
_POD_EXCLUDE = {"raducanu"}
_env_excl = os.getenv("POD_EXCLUDE", "")
if _env_excl.strip():
    _POD_EXCLUDE |= {_norm(x) for x in _env_excl.split(",") if x.strip()}

# Prop types never used for Pick of the Day (excluded by request).
_POD_EXCLUDE_PROPS = {"Double Faults"}


def _is_excluded(name: str) -> bool:
    n = _norm(name)
    return bool(n) and any(ex and ex in n for ex in _POD_EXCLUDE)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _season_surface() -> str:
    """Approximate current-tour surface from the calendar month. PrizePicks
    props don't carry a surface, so this is the default passed to the calc."""
    m = datetime.now(timezone.utc).month
    if m in (4, 5):
        return "Clay"
    if m in (6, 7):
        return "Grass"
    return "Hard"


def _get(path: str, params: dict, timeout: int):
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict, timeout: int):
    r = requests.post(f"{API_BASE}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── STEP 1: fetch the PrizePicks board ──────────────────────────────────────
def _fetch_board():
    try:
        r = requests.get(
            PRIZEPICKS_URL,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=8,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 — never raise out of this feature
        log.warning("PrizePicks board fetch failed: %s", exc)
        return None


# ── STEP 2: filter to eligible tennis props ─────────────────────────────────
def _parse_board(board: dict) -> list:
    """Eligible tennis props: [{player, opponent, prop_type, line}]."""
    if not board or not isinstance(board, dict):
        return []
    included = {(i.get("type"), i.get("id")): i for i in board.get("included", [])}
    out = []
    for proj in board.get("data", []):
        attr = proj.get("attributes", {}) or {}
        prop_type = PROP_MAP.get((attr.get("stat_type") or "").strip().lower())
        if not prop_type or prop_type in _POD_EXCLUDE_PROPS:
            continue
        # Only ever use the STANDARD line. PrizePicks also lists "demon" (boosted,
        # higher line) and "goblin" (reduced, lower line) variants — never pick
        # those, and never fabricate a line when no standard one exists; just skip.
        if (attr.get("odds_type") or "standard").lower() != "standard":
            continue
        line = attr.get("line_score")
        if line is None:
            continue
        rel = proj.get("relationships", {}) or {}

        # League → tennis only
        lref = (rel.get("league") or {}).get("data") or {}
        league = included.get((lref.get("type"), lref.get("id")), {})
        league_name = ((league.get("attributes") or {}).get("name") or "").lower()
        if "tennis" not in league_name:
            continue

        # Player
        pref = (rel.get("new_player") or rel.get("player") or {}).get("data") or {}
        player = included.get((pref.get("type"), pref.get("id")), {})
        pname = (player.get("attributes") or {}).get("name") or ""
        if not pname:
            continue
        if _is_excluded(pname):          # injured / off-form exclude list
            log.info("POD: excluding %s (exclude list)", pname)
            continue

        opponent = (attr.get("description") or "").strip()  # tennis: opponent name
        # Skip doubles (combo entries like "Hsieh / Wang") — no single player.
        if "/" in pname or "/" in opponent or not opponent:
            continue
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            continue
        out.append({"player": pname, "opponent": opponent,
                    "prop_type": prop_type, "line": line_f})
    return out


# ── STEP 3: fuzzy match + projections (max 3 concurrent) ────────────────────
async def _resolve(name: str, tours: tuple = ("ATP", "WTA")):
    """Fuzzy-match a PrizePicks name to a Baseline player (>=0.8). Returns
    (id, tour, name) or None.

    ``tours`` restricts the search — pass a single tour for an opponent so a
    WTA player never resolves to a same-surname ATP player (and vice-versa),
    since both halves of a tennis prop are always on the same tour.

    Scoring weights the FULL-name similarity over the last-name match so that
    e.g. 'Xinyu Wang' beats 'Aoran Wang' instead of every 'Wang' tying at 1.0.
    """
    if not name:
        return None
    nnorm = _norm(name)
    parts = nnorm.split()
    last = parts[-1] if parts else nnorm
    query = last if len(last) >= 3 else nnorm
    candidates = []
    for tour in tours:
        try:
            res = await asyncio.to_thread(_get, "/api/search",
                                          {"query": query, "tour": tour}, SEARCH_TIMEOUT)
        except Exception:  # noqa: BLE001
            res = []
        if isinstance(res, list):
            for p in res:
                candidates.append({**p, "tour": tour})

    best, best_score = None, 0.0
    for c in candidates:
        cn = _norm(c.get("name", ""))
        c_last = cn.split()[-1] if cn.split() else cn
        # Full name dominates; last-name agreement only breaks near-ties. This
        # stops same-surname players from all tying at a perfect last-name 1.0.
        score = 0.75 * _ratio(nnorm, cn) + 0.25 * _ratio(last, c_last)
        if score > best_score:
            best_score, best = score, c
    if best and best_score >= MATCH_THRESHOLD:
        return str(best["id"]), best.get("tour", "ATP"), best.get("name", "")
    return None


async def _next_match(player_id: str, tour: str) -> dict:
    """The player's next scheduled match (tournament + surface) from Sofascore."""
    try:
        nm = await asyncio.to_thread(_get, "/api/player/next-match",
                                     {"player_id": player_id, "tour": tour}, SEARCH_TIMEOUT)
        return nm if isinstance(nm, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def _evaluate(prop: dict, sem: asyncio.Semaphore):
    async with sem:
        try:
            p = await _resolve(prop["player"])
            if not p:
                log.info("POD skip (no player match): %r", prop["player"])
                return None
            p_id, tour, p_name = p
            # Opponent is in the SAME match, so the SAME tour — restrict the
            # search to avoid resolving a WTA player's opponent to a same-surname
            # ATP player (e.g. 'Xinyu Wang' -> 'Aoran Wang').
            o = await _resolve(prop["opponent"], tours=(tour,)) if prop["opponent"] else None
            if not o:
                log.info("POD skip (no opponent match): %r vs %r on %s",
                         prop["player"], prop["opponent"], tour)
                return None
            o_id, _, o_name = o

            # Real surface + tournament from the player's UPCOMING match (so we
            # don't guess the surface or show a stale completed event).
            nm = await _next_match(p_id, tour)

            # Only consider matches that actually play within the next 24h. A
            # prop sitting on the board for a match 2-3 days out isn't a "play of
            # the day". Requires a known scheduled start time within the window.
            start = nm.get("start_timestamp")
            now = datetime.now(timezone.utc).timestamp()
            if not start or start > now + MAX_LOOKAHEAD_HOURS * 3600 or start < now - 6 * 3600:
                log.info("POD skip (match not within %dh): %r vs %r start=%s",
                         MAX_LOOKAHEAD_HOURS, p_name, o_name, start)
                return None

            surface = nm.get("surface") or _season_surface()
            tournament = nm.get("tournament") or None

            # Pass the real tournament so the backend uses that court's ST Pace
            # Index (e.g. Bad Homburg = 36, not the generic grass 34). Flag
            # qualifying so a Grand Slam quallie stays best-of-3, not best-of-5.
            is_qualifying = bool(tournament) and "qualif" in tournament.lower()

            payload = {
                "player_id": p_id, "opponent_id": o_id,
                "player_name": p_name, "opponent_name": o_name,
                "tour": tour, "surface": surface,
                "court": tournament or "", "qualifying": is_qualifying,
                "prop_type": prop["prop_type"], "prop_line": prop["line"],
            }
            data = None
            for attempt in range(CALC_RETRIES):
                try:
                    data = await asyncio.to_thread(_post, "/api/prop/calculate", payload, CALC_TIMEOUT)
                    break
                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.HTTPError) as exc:
                    # Retry transient failures only: timeouts, connection drops,
                    # and 5xx (e.g. a 502 while the backend is busy/restarting).
                    # A 4xx is a real client error — don't retry. The aborted
                    # attempt also warms the backend's player cache, so retries
                    # usually return quickly.
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status is not None and status < 500:
                        raise
                    if attempt == CALC_RETRIES - 1:
                        raise
                    log.info("POD calc %s for %r — retrying (%d/%d)",
                             status or type(exc).__name__, p_name, attempt + 2, CALC_RETRIES)
                    await asyncio.sleep(2.0 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            log.warning("POD evaluate failed for %r: %s", prop.get("player"), exc)
            return None

        proj = data.get("model_projection")
        if proj is None:
            return None
        conf = data.get("confidence") or 0
        line = prop["line"]
        edge = proj - line
        return {
            "player": p_name, "opponent": o_name,
            "player_id": p_id, "tour": tour,
            "pp_player": prop["player"],              # original PrizePicks name (board matching)
            "prop_type": prop["prop_type"], "line": line, "original_line": line,
            "surface": payload["surface"], "tournament": tournament,
            "start_timestamp": nm.get("start_timestamp"),
            "projection": proj, "edge": edge, "edge_mag": abs(edge),
            "confidence": conf, "lean": data.get("lean"),
            "p1_win_prob": data.get("p1_win_prob"), "p2_win_prob": data.get("p2_win_prob"),
            "explanation": data.get("plain_english_explanation"),
            "score": conf * abs(edge),
            "data": data,
        }


def current_board_lines() -> dict:
    """Re-fetch the PrizePicks board and return {(norm_player, prop_type): line}
    for the standard lines only. Used by the line-movement monitor. Empty on
    failure — never raises."""
    try:
        board = _fetch_board()
        out = {}
        for pr in _parse_board(board):
            out[(_norm(pr["player"]), pr["prop_type"])] = pr["line"]
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("current_board_lines failed: %s", exc)
        return {}


def _lean_dir(pk: dict) -> str:
    """OVER/UNDER direction of a pick (from the model lean, else the edge sign)."""
    ln = (pk.get("lean") or "").upper()
    if ln in ("OVER", "UNDER"):
        return ln
    return "OVER" if (pk.get("edge") or 0) >= 0 else "UNDER"


def _passes_quality(pk: dict) -> bool:
    """Quality gate: every Pick of the Day must be at least 90% confidence,
    regardless of prop type. Below that we'd rather post no pick than a
    coin-flip — high projections can still bust on a blowout / off-serve day."""
    conf = pk.get("confidence") or 0
    if pk.get("prop_type") in ("Aces", "Break Points Won"):
        return conf >= ACES_BP_MIN_CONF
    return conf >= TG_DF_MIN_CONF


# Per-match stat field per prop, for the recent-form-vs-line check.
_POD_STAT_KEY = {
    "Aces":                   "aces",
    "Break Points Won":       "bp_converted_count",
    "Total Games":            "total_match_games",
    "Player Total Games Won": "total_games_won",
}


def _recent_supports_lean(pk: dict, lookback: int = 5, min_n: int = 3) -> bool:
    """True if the player's RECENT same-surface form supports the pick's lean —
    the majority of the last ``lookback`` matches landed on the lean's side of
    the line. Catches projections that contradict recent reality (e.g. Cilic
    Aces projected OVER 10.5 while he'd cleared it in only 2 of his last 5 grass
    matches, then finished with 2). Returns True when there's too little form
    data to judge, so we don't over-filter thin-history players."""
    lean = (pk.get("lean") or "").upper()
    line = pk.get("line")
    key = _POD_STAT_KEY.get(pk.get("prop_type"))
    ms = (pk.get("data") or {}).get("player_surface_matches") or []
    if lean not in ("OVER", "UNDER") or not key or not isinstance(line, (int, float)):
        return True
    over = under = 0
    for m in ms[:lookback]:
        v = m.get(key) if isinstance(m, dict) else None
        if not isinstance(v, (int, float)):
            continue
        if v > line:
            over += 1
        elif v < line:
            under += 1
    if over + under < min_n:        # too few stat-bearing matches → don't filter
        return True
    supports = (over >= under) if lean == "OVER" else (under >= over)
    if not supports:
        log.info("POD: %s %s %s — recent form diverges (over=%d under=%d, lean=%s), excluding",
                 pk.get("player"), pk.get("prop_type"), line, over, under, lean)
    return supports


# ── STEPS 4 + 7: select the best picks, fully isolated ──────────────────────
async def generate_picks(n: int = 3):
    """Return up to ``n`` picks ranked best-first (list, possibly empty). Each
    is the same dict shape as ``generate_pick``. Never raises."""
    try:
        board = await asyncio.to_thread(_fetch_board)
        props = _parse_board(board)
        if not props:
            log.info("POD: no eligible tennis props on the board")
            return None

        # One evaluation per (player, prop) — the projection is line-independent.
        seen, by_type = set(), {}
        for pr in props:
            k = (_norm(pr["player"]), pr["prop_type"])
            if k in seen:
                continue
            seen.add(k)
            by_type.setdefault(pr["prop_type"], []).append(pr)

        # Balance the capped sample across ALL prop types (round-robin) so Total
        # Games doesn't crowd out Aces / Double Faults / Break Points Won.
        uniq, lists = [], [v for v in by_type.values()]
        while len(uniq) < MAX_PROPS and any(lists):
            for lst in lists:
                if lst:
                    uniq.append(lst.pop(0))
                    if len(uniq) >= MAX_PROPS:
                        break

        sem = asyncio.Semaphore(MAX_CONCURRENT)
        results = await asyncio.gather(*[_evaluate(pr, sem) for pr in uniq],
                                       return_exceptions=True)
        picks = [r for r in results
                 if isinstance(r, dict) and (r.get("confidence") or 0) >= MIN_CONFIDENCE
                 and _passes_quality(r) and _recent_supports_lean(r)]
        log.info("POD: evaluated=%d eligible=%d", len(uniq), len(picks))
        picks.sort(key=lambda x: x["score"], reverse=True)
        # Keep only each player's single best-scoring play so the top N are N
        # different players (no same player twice for different props).
        best_per_player, ordered = set(), []
        for pk in picks:
            key = _norm(pk["player"])
            if key in best_per_player:
                continue
            best_per_player.add(key)
            ordered.append(pk)

        top = ordered[:max(1, n)]
        # Direction diversity — don't surface all OVERs or all UNDERs. If the top
        # N are all one direction and a qualifying opposite-direction pick exists
        # further down, swap it in for the weakest of the top (best picks kept).
        if n >= 2 and len(top) >= 2 and len(ordered) > len(top):
            dirs = {_lean_dir(p) for p in top}
            if len(dirs) == 1:
                only = dirs.pop()
                opp = next((p for p in ordered[len(top):] if _lean_dir(p) != only), None)
                if opp:
                    log.info("POD: injecting %s pick for direction balance (top were all %s)",
                             _lean_dir(opp), only)
                    top[-1] = opp
        return top
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_picks failed: %s", exc)
        return []


async def generate_pick():
    """Return the single best pick dict (or None). Never raises.
    Backwards-compatible wrapper around generate_picks()."""
    picks = await generate_picks(1)
    return picks[0] if picks else None
