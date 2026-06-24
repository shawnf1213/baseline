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
MAX_CONCURRENT  = 3       # concurrent backend prop calcs
MATCH_THRESHOLD = 0.80    # fuzzy name-match threshold
MAX_PROPS       = 25      # cap evaluations so the command stays responsive

SEARCH_TIMEOUT = 10
CALC_TIMEOUT   = 60


# ── small helpers ───────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


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
        if not prop_type:
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
async def _resolve(name: str):
    """Fuzzy-match a PrizePicks name to a Baseline player (>=0.8). Returns
    (id, tour, name) or None. Searches both tours by last name."""
    if not name:
        return None
    nnorm = _norm(name)
    parts = nnorm.split()
    last = parts[-1] if parts else nnorm
    query = last if len(last) >= 3 else nnorm
    candidates = []
    for tour in ("ATP", "WTA"):
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
        score = max(_ratio(nnorm, cn), _ratio(last, c_last))
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
            o = await _resolve(prop["opponent"]) if prop["opponent"] else None
            if not p or not o:
                log.info("POD skip (no match): %r vs %r", prop["player"], prop["opponent"])
                return None
            p_id, tour, p_name = p
            o_id, _, o_name = o

            # Real surface + tournament from the player's UPCOMING match (so we
            # don't guess the surface or show a stale completed event). Falls back
            # to the calendar-season surface if Sofascore has no scheduled match.
            nm = await _next_match(p_id, tour)
            surface = nm.get("surface") or _season_surface()
            tournament = nm.get("tournament") or None

            payload = {
                "player_id": p_id, "opponent_id": o_id,
                "player_name": p_name, "opponent_name": o_name,
                "tour": tour, "surface": surface, "court": "",
                "prop_type": prop["prop_type"], "prop_line": prop["line"],
            }
            data = await asyncio.to_thread(_post, "/api/prop/calculate", payload, CALC_TIMEOUT)
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
            "prop_type": prop["prop_type"], "line": line,
            "surface": payload["surface"], "tournament": tournament,
            "projection": proj, "edge": edge, "edge_mag": abs(edge),
            "confidence": conf, "lean": data.get("lean"),
            "p1_win_prob": data.get("p1_win_prob"), "p2_win_prob": data.get("p2_win_prob"),
            "explanation": data.get("plain_english_explanation"),
            "score": conf * abs(edge),
            "data": data,
        }


# ── STEPS 4 + 7: select the single best pick, fully isolated ────────────────
async def generate_pick():
    """Return the best pick dict (or None). Never raises."""
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
                 if isinstance(r, dict) and (r.get("confidence") or 0) >= MIN_CONFIDENCE]
        log.info("POD: evaluated=%d eligible=%d", len(uniq), len(picks))
        if not picks:
            return None
        return max(picks, key=lambda x: x["score"])
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_pick failed: %s", exc)
        return None
