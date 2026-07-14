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
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = timezone(timedelta(hours=-4))

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

# PrizePicks stat_type (lowercased) -> Baseline prop_type.
# NOTE: PrizePicks has BOTH "Total Games" (the match total) and "Total Games Won"
# (a single player's games won) — different stats, both modelled by Baseline
# ("Total Games" and "Player Total Games Won"), held to stricter bars — 90% and
# 80% respectively (see PROP_MIN_CONF).
PROP_MAP = {
    "aces":             "Aces",
    "double faults":    "Double Faults",
    "double fault":     "Double Faults",
    "break points won": "Break Points Won",
    "total games":      "Total Games",              # match total
    "total games won":  "Player Total Games Won",   # a single player's games won
}

MAX_CONCURRENT  = 4       # parallelise backend calcs so the full board (100+ props)
                          # evaluates inside the pre-gen window. CALC_RETRIES with
                          # backoff absorbs the occasional 502 under light concurrency.
MATCH_THRESHOLD = 0.80    # fuzzy name-match threshold
MAX_RANKED_PLAYS = 6      # post only the top-6 ranked plays (the 3x still draws
                          # its legs from the full evaluated pool)
MAX_PROPS       = 130     # evaluate (nearly) the whole board — the ranked list
                          # must show EVERY qualifying play, and the daily run is a
                          # pre-generated 10-min job, so a low cap would silently
                          # drop strong plays on a big board (e.g. 100+ props).
MAX_LOOKAHEAD_HOURS = 24  # only pick matches that play within this many hours
# Per-prop-type minimum confidence to qualify for the ranked list.
#   STANDARD (75): Aces / Break Points Won / Double Faults. The ranked list shows
#   every qualifying play, so the bar is set to keep the list to genuinely strong
#   plays (>=75% confidence).
#   HIGH BAR (90): Total Games (match total) AND Player Total Games Won. Both are
#   derived, higher-variance stats — the match total depends on BOTH players'
#   combined performance plus match-length variance, and per-player games won is
#   compounded from holds + breaks + win-prob share — so they only surface when
#   the data strongly supports them.
STANDARD_MIN_CONF    = 75   # Aces / Break Points Won / Double Faults
TOTAL_GAMES_MIN_CONF = 90   # Total Games (match total) — highest bar
PLAYER_TGW_MIN_CONF  = 80   # Player Total Games Won — mid bar
# Per-prop overrides; anything not listed uses STANDARD_MIN_CONF.
PROP_MIN_CONF = {
    "Total Games":             TOTAL_GAMES_MIN_CONF,   # 90
    "Player Total Games Won":  PLAYER_TGW_MIN_CONF,    # 80
}


def _min_conf_for(prop_type: str) -> int:
    """The minimum confidence a candidate of this prop type must clear to qualify.
    Total Games (match total) → 90, Player Total Games Won → 80, everything else
    (Aces / Break Points) → 75."""
    return PROP_MIN_CONF.get(prop_type, STANDARD_MIN_CONF)

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
_POD_EXCLUDE_PROPS = {"Double Faults", "Player Total Games Won"}


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
            # Ranking score — CONFIDENCE-DOMINANT. Confidence is the primary term;
            # edge magnitude only nudges within/near a confidence tier. This is an
            # additive blend (was multiplicative conf*edge, which let a big
            # projected edge outrank a more-confident play). Now a few points of
            # extra confidence beats a larger projected edge — e.g. Ann Li's
            # conf-95 BP play outranks her conf-91 Total Games play.
            "score": conf + abs(edge),
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


# ── Player Total Games Won — bespoke qualification ───────────────────────────
# The prop's biggest variance source is SET COUNT. Rules (PTGW only):
#   • BLOWOUT-UNDER (75): win-prob gap > 35pp AND lean UNDER. A projected blowout
#     near-locks the set count (2 sets), removing that variance and making an
#     UNDER meaningfully safer — so it may qualify at 75 instead of 80.
#   • BLOWOUT-OVER (strict): win-prob gap > 35pp AND lean OVER gets NO relief and
#     EXTRA scrutiny — dominant wins compress games toward ~12 vs a typical 12.5
#     line, so it needs the standard 80 AND the projection must clear the line by
#     >= 1.5 games.
#   • STANDARD (80): everything else.
#   • KNIFE-EDGE (any win prob): if |projection - line| <= 0.7 the line sits in the
#     highest-variance band (straight-sets scorelines cluster at 12-14 games, so
#     12.5/13 lines are coin-flips) — subtract 10 confidence and flag it.
PLAYER_TGW_BLOWOUT_WPGAP     = 35.0   # win-prob gap (pp) above which blowout logic applies
PLAYER_TGW_BLOWOUT_UNDER_BAR = 75     # relaxed bar for a blowout UNDER
PLAYER_TGW_OVER_MIN_EDGE     = 1.5    # blowout OVER must clear the line by >= this
PLAYER_TGW_KNIFE_EDGE        = 0.7    # |proj - line| <= this → coin-flip zone
PLAYER_TGW_KNIFE_PENALTY     = 10     # confidence subtracted in the coin-flip zone


def _win_prob_gap(pk: dict):
    """Absolute win-probability gap (percentage points) between the two players,
    or None when unavailable."""
    d = pk.get("data") or {}
    g = d.get("win_prob_gap")
    if isinstance(g, (int, float)):
        return abs(g)
    w1, w2 = d.get("p1_win_prob"), d.get("p2_win_prob")
    if isinstance(w1, (int, float)) and isinstance(w2, (int, float)):
        return abs(w1 - w2)
    return None


def _apply_ptgw_knife_edge(pk: dict) -> None:
    """Knife-edge coin-flip check for Player Total Games Won — subtract 10
    confidence and set pk['coin_flip'] when the projection sits within 0.7 games
    of the line. Mutates pk once (idempotent via the _ptgw_adjusted guard)."""
    if pk.get("prop_type") != "Player Total Games Won" or pk.get("_ptgw_adjusted"):
        return
    pk["_ptgw_adjusted"] = True
    proj, line = pk.get("projection"), pk.get("line")
    if (isinstance(proj, (int, float)) and isinstance(line, (int, float))
            and abs(proj - line) <= PLAYER_TGW_KNIFE_EDGE):
        pk["coin_flip"] = True
        pk["confidence"] = (pk.get("confidence") or 0) - PLAYER_TGW_KNIFE_PENALTY
        log.info("POD_KNIFE_EDGE | %-22s Player Total Games Won proj=%.1f line=%.1f "
                 "(|Δ|<=%.1f) -> -%d conf (coin-flip zone)",
                 (pk.get("player") or "")[:22], proj, line,
                 PLAYER_TGW_KNIFE_EDGE, PLAYER_TGW_KNIFE_PENALTY)


def _ptgw_qualify(pk: dict):
    """(qualifies, bar, path) for a Player Total Games Won candidate, using its
    current (post-knife-edge) confidence. path ∈ {'standard-80',
    'blowout-under-75', 'blowout-over-strict'}."""
    conf = pk.get("confidence") or 0
    proj, line = pk.get("projection"), pk.get("line")
    lean = _lean_dir(pk)
    gap = _win_prob_gap(pk)
    base = _min_conf_for("Player Total Games Won")   # 80
    blowout = gap is not None and gap > PLAYER_TGW_BLOWOUT_WPGAP
    if blowout and lean == "UNDER":
        return conf >= PLAYER_TGW_BLOWOUT_UNDER_BAR, PLAYER_TGW_BLOWOUT_UNDER_BAR, "blowout-under-75"
    if blowout and lean == "OVER":
        edge_ok = (isinstance(proj, (int, float)) and isinstance(line, (int, float))
                   and (proj - line) >= PLAYER_TGW_OVER_MIN_EDGE)
        return (conf >= base and edge_ok), base, "blowout-over-strict"
    return conf >= base, base, "standard-80"


def _passes_quality(pk: dict) -> bool:
    """Quality gate. Player Total Games Won uses the bespoke path logic above (75
    blowout-under / strict blowout-over / 80 standard); every other prop clears a
    flat bar (75 standard, 90 Total Games)."""
    if pk.get("prop_type") == "Player Total Games Won":
        return _ptgw_qualify(pk)[0]
    return (pk.get("confidence") or 0) >= _min_conf_for(pk.get("prop_type"))


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
async def _rank_board():
    """Evaluate the whole board ONCE and return the qualifying candidates,
    deduped to each player's single best play, sorted best-first.

    Returns None when the board has no eligible props (so callers can tell
    "nothing on the board" apart from "nothing qualified" = []). Raises only on
    unexpected errors — callers wrap. This is the shared evaluation pass behind
    both the Pick of the Day and the 3x slip, so the heavy serialized backend
    calc runs a single time per trigger."""
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
    # STEP 1 — log EVERY evaluated candidate + why it passed/failed, so a
    # zero-pick day is fully debuggable from the Railway logs.
    # NOTE: the recent-form HARD gate (_recent_supports_lean) is no longer a
    # filter — recent form is already folded into the projection by the
    # opponent-weighted recent-form pull, so excluding on it again
    # double-counted it. It's still computed + logged as an info signal.
    picks = []
    by_type_qual = {}   # STEP 4 — qualifying count per prop type
    for r in results:
        if not isinstance(r, dict):
            log.info("POD_CAND | EVAL_FAILED | %s", str(r)[:120])
            continue
        ptype = r.get("prop_type")
        # Player Total Games Won: apply the knife-edge penalty (mutates conf +
        # coin_flip) FIRST, then resolve its bespoke qualification path.
        path = ""
        if ptype == "Player Total Games Won":
            _apply_ptgw_knife_edge(r)
            ok, bar, path = _ptgw_qualify(r)
            log.info("POD_PTGW | %-22s conf=%-3.0f proj=%-6.2f line=%-5s lean=%-5s gap=%-3.0f "
                     "coin_flip=%-5s -> path=%s bar=%d -> %s",
                     (r.get("player") or "")[:22], r.get("confidence") or 0,
                     r.get("projection") or 0.0, r.get("line"), _lean_dir(r),
                     _win_prob_gap(r) or 0.0, bool(r.get("coin_flip")), path, bar,
                     "QUALIFIES" if ok else "below")
        else:
            bar = _min_conf_for(ptype)
            ok = (r.get("confidence") or 0) >= bar
        conf = r.get("confidence") or 0
        log.info("POD_CAND | %-22s %-18s line=%-5s conf=%-3.0f proj=%-6.2f edge=%+5.2f "
                 "recent_ok=%-5s bar=%d%s -> %s",
                 (r.get("player") or "")[:22], (ptype or "")[:18],
                 r.get("line"), conf, r.get("projection") or 0.0, r.get("edge") or 0.0,
                 _recent_supports_lean(r), bar, (" [%s]" % path) if path else "",
                 "QUALIFIES" if ok else ("below %s bar %d" % (ptype, bar)))
        if ok:
            picks.append(r)
            by_type_qual[ptype] = by_type_qual.get(ptype, 0) + 1
    log.info("POD: evaluated=%d eligible=%d (bars: standard=%d, Total Games=%d) | "
             "qualifying per prop type: %s",
             len(uniq), len(picks), STANDARD_MIN_CONF, TOTAL_GAMES_MIN_CONF,
             dict(by_type_qual) or "none")
    picks.sort(key=lambda x: x["score"], reverse=True)
    # Keep only each player's single best-scoring play so the ranking has one
    # entry per player (no same player twice for different props).
    best_per_player, ordered = set(), []
    for pk in picks:
        key = _norm(pk["player"])
        if key in best_per_player:
            continue
        best_per_player.add(key)
        ordered.append(pk)
    return ordered


def _select_potd(ordered: list, n: int = 3) -> list:
    """The Pick of the Day selection — top-N of the ranking with direction
    diversity. Pure (no I/O); operates on the output of ``_rank_board``.
    UNCHANGED POTD logic (just factored out of the old generate_picks)."""
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


def _match_key(pk: dict) -> frozenset:
    """Unordered {player, opponent} identity of a prop's match. Two props share
    a match iff their keys are equal — this catches the reversed case where one
    prop is on player A vs B and the other is on player B vs A."""
    return frozenset({_norm(pk.get("player", "")), _norm(pk.get("opponent", ""))})


# Confidence window (points) inside which the 3x prefers prop-type diversity
# over a marginally higher-scoring same-prop leg (STEP 2).
SLIP_DIVERSITY_WINDOW = 5


def _select_slip(ordered: list, potd: list) -> list:
    """Build the 3x — two independent legs packaged as one slip (STEP 1-3).

    STEP 1: exclude anything already in the POTD, keyed by (player, prop_type),
            so the two posts never overlap.
    STEP 2: take the two highest-scoring remaining candidates by the same
            combined score used for the POTD, with correlation avoidance (the
            two legs must come from two DIFFERENT matches) and a prop-diversity
            preference (within SLIP_DIVERSITY_WINDOW confidence points, prefer
            two different prop types).
    STEP 3: only return a slip if TWO candidates clear their standard quality
            bar. Never force a weak second leg — return [] and log a thin pool.
    """
    if not ordered:
        return []
    potd_keys = {(_norm(p["player"]), p["prop_type"]) for p in (potd or [])}
    # Correlation avoidance also covers the POTD: a 3x leg from the SAME match as
    # a Pick of the Day (e.g. the other server's aces) is correlated with it and
    # undercuts the "distinct value from each post" goal, so exclude those whole
    # matches — not just the exact (player, prop_type) already picked.
    potd_matches = {_match_key(p) for p in (potd or [])}
    # ``ordered`` already contains only qualifying picks (past the prop-type bar
    # and past its prop-type bar); re-check _passes_quality defensively.
    pool = [c for c in ordered
            if (_norm(c["player"]), c["prop_type"]) not in potd_keys
            and _match_key(c) not in potd_matches
            and _passes_quality(c)]
    if len(pool) < 2:
        log.info("3x: pool too thin after POTD exclusion (%d qualifying) — no slip today",
                 len(pool))
        return []

    leg1 = pool[0]
    m1 = _match_key(leg1)
    # Correlation avoidance — leg2 must be from a different match than leg1.
    rest = [c for c in pool[1:] if _match_key(c) != m1]
    if not rest:
        log.info("3x: only one independent match qualifies after exclusion — no slip today")
        return []
    leg2 = rest[0]

    # Prop diversity preference — if leg2 repeats leg1's prop type, swap in the
    # best different-prop candidate that scores within the confidence window.
    if leg2["prop_type"] == leg1["prop_type"]:
        alt = next(
            (c for c in rest
             if c["prop_type"] != leg1["prop_type"]
             and (leg2.get("confidence", 0) - c.get("confidence", 0)) <= SLIP_DIVERSITY_WINDOW),
            None)
        if alt:
            log.info("3x: swapping leg2 -> %s %s for prop diversity (was another %s)",
                     alt["player"], alt["prop_type"], leg1["prop_type"])
            leg2 = alt

    log.info("3x: slip legs = [%s %s @%s | %s %s @%s]",
             leg1["player"], leg1["prop_type"], leg1["line"],
             leg2["player"], leg2["prop_type"], leg2["line"])
    return [leg1, leg2]


async def generate_potd_and_slip(n: int = 3, exclude_keys: set = None) -> dict:
    """Single board evaluation → the Pick of the Day picks AND the 3x slip legs.
    Returns {"potd": [...] | None, "slip": [...]}. ``potd`` is None only when the
    board had no eligible props; ``slip`` is [] whenever fewer than two
    independent candidates remain after POTD exclusion. ``exclude_keys`` is an
    optional set of (norm_player, prop_type) tuples to drop before selection —
    used by the evening scan so it never re-posts the afternoon's plays. Never
    raises."""
    try:
        ordered = await _rank_board()
        if ordered is None:
            return {"potd": None, "slip": []}
        if exclude_keys:
            ordered = [c for c in ordered
                       if (_norm(c["player"]), c["prop_type"]) not in exclude_keys]
        if not ordered:
            return {"potd": [], "slip": []}
        potd = _select_potd(ordered, n)
        slip = _select_slip(ordered, potd)
        return {"potd": potd, "slip": slip}
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_potd_and_slip failed: %s", exc)
        return {"potd": [], "slip": []}


# A Total Games (match total) play may only be the ⭐ Pick of the Day when one
# player is at least a 90% favorite — the set count is then near-locked and the
# total is a very clear win condition. Below that it can appear in the list but
# must not lead.
STAR_TOTAL_GAMES_MIN_WP = 90.0


def _star_eligible(pk: dict) -> bool:
    """True if this play may occupy the ⭐ Pick-of-the-Day slot. Any prop other
    than Total Games is always eligible; a Total Games play is eligible only when
    the stronger player's win probability is >= 90%."""
    if pk.get("prop_type") != "Total Games":
        return True
    d = pk.get("data") or {}
    wps = [w for w in (d.get("p1_win_prob"), d.get("p2_win_prob"))
           if isinstance(w, (int, float))]
    return bool(wps) and max(wps) >= STAR_TOTAL_GAMES_MIN_WP


def _promote_star(ordered: list) -> list:
    """Ensure ordered[0] (the ⭐) isn't a Total Games play without a 90%+ favorite.
    If it is, promote the highest-scoring ⭐-eligible play to the front; the rest
    keep their score order. No-op when the top play is already eligible or when
    nothing else qualifies for the star."""
    if not ordered or _star_eligible(ordered[0]):
        return ordered
    idx = next((i for i, p in enumerate(ordered) if _star_eligible(p)), None)
    if idx is None or idx == 0:
        # Nothing else is star-eligible (e.g. board is only sub-90% Total Games) —
        # leave as-is rather than post no ⭐.
        log.info("POD: top play is a sub-90%% Total Games and no other star-eligible "
                 "play exists — keeping it as ⭐")
        return ordered
    star = ordered.pop(idx)
    log.info("POD: %s Total Games has no 90%%+ favorite — demoted from ⭐; "
             "promoting %s %s to Pick of the Day",
             ordered[0].get("player") if ordered else "?", star.get("player"), star.get("prop_type"))
    return [star] + ordered


# One-off: on this ET date, keep these players OUT of the ⭐ Pick-of-the-Day slot
# (they stay in the ranked list). Auto-reverts the next day.
STAR_EXCLUDE_DATE    = "2026-07-13"
STAR_EXCLUDE_PLAYERS = {"ann li"}     # normalised (see _norm)


def _apply_star_exclusions(ordered: list) -> list:
    """One-off, date-gated: if today (ET) is STAR_EXCLUDE_DATE and the ⭐ is an
    excluded player, promote the next star-eligible non-excluded play to #1. The
    excluded player stays in the list, just not as Pick of the Day."""
    if not ordered or datetime.now(_ET).strftime("%Y-%m-%d") != STAR_EXCLUDE_DATE:
        return ordered
    if _norm(ordered[0].get("player", "")) not in STAR_EXCLUDE_PLAYERS:
        return ordered
    idx = next((i for i, p in enumerate(ordered)
                if _star_eligible(p) and _norm(p.get("player", "")) not in STAR_EXCLUDE_PLAYERS), None)
    if idx is None or idx == 0:
        return ordered
    excluded_name = ordered[0].get("player")
    star = ordered.pop(idx)
    log.info("POD: one-off %s exclusion — %s held out of ⭐ today (stays in list); "
             "promoting %s %s to Pick of the Day",
             STAR_EXCLUDE_DATE, excluded_name, star.get("player"), star.get("prop_type"))
    return [star] + ordered


async def generate_ranked_and_slip() -> dict:
    """Single board evaluation → the FULL ranked list of qualifying plays plus the
    3x slip. Returns {"ranked": [...] | None, "slip": [...]}:
      * ``ranked``  — every qualifying play, best-first by the combined score
                      (confidence × edge magnitude), one entry per player.
                      ``ranked[0]`` is the ⭐ Pick of the Day. None only when the
                      board had no eligible props; [] when nothing qualified.
      * ``slip``    — the 3x legs, drawn from the ranked plays but excluding ONLY
                      the ⭐ Pick of the Day (and its match) — correlation
                      avoidance + the two-legs-or-nothing quality bar as before.
    Never raises."""
    try:
        ordered = await _rank_board()
        if ordered is None:
            return {"ranked": None, "slip": []}
        if not ordered:
            return {"ranked": [], "slip": []}
        # ⭐ gate: a Total Games play can't lead unless one player is a 90%+ favorite.
        ordered = _promote_star(ordered)
        # One-off (today only): hold specific players out of the ⭐ slot.
        ordered = _apply_star_exclusions(ordered)
        # 3x excludes only the ⭐ POTD (ordered[0]) and its match — drawn from the
        # FULL evaluated pool (not just the posted top-6).
        slip = _select_slip(ordered, ordered[:1])
        # Post only the top-N plays (⭐ + the next best), even though the whole
        # board was evaluated.
        return {"ranked": ordered[:MAX_RANKED_PLAYS], "slip": slip}
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_ranked_and_slip failed: %s", exc)
        return {"ranked": [], "slip": []}


async def generate_picks(n: int = 3):
    """Return up to ``n`` Pick-of-the-Day picks ranked best-first (list, possibly
    empty; None when the board has no eligible props). Never raises.
    Backwards-compatible wrapper around the shared evaluation pass."""
    try:
        ordered = await _rank_board()
        if ordered is None:
            return None
        return _select_potd(ordered, n)
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_picks failed: %s", exc)
        return []


async def generate_pick():
    """Return the single best pick dict (or None). Never raises.
    Backwards-compatible wrapper around generate_picks()."""
    picks = await generate_picks(1)
    return picks[0] if picks else None
