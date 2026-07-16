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
# ("Total Games" and "Player Total Games Won"), both held to a stricter 80% bar
# (see PROP_MIN_CONF).
PROP_MAP = {
    "aces":             "Aces",
    "double faults":    "Double Faults",
    "double fault":     "Double Faults",
    "break points won": "Break Points Won",
    "total games":      "Total Games",              # match total
    "total games won":  "Player Total Games Won",   # a single player's games won
    "fantasy score":    "Fantasy Score",            # composite (games/sets/aces/DF)
}

MAX_CONCURRENT  = 4       # parallelise backend calcs so the full board (100+ props)
                          # evaluates inside the pre-gen window. CALC_RETRIES with
                          # backoff absorbs the occasional 502 under light concurrency.
MATCH_THRESHOLD = 0.80    # fuzzy name-match threshold
MAX_RANKED_PLAYS = 12     # post the top-12 ranked plays (delivered as two pages of
                          # 6 by the bot). The 3x still draws its legs from the
                          # full evaluated pool.
MAX_PROPS       = 130     # evaluate (nearly) the whole board — the ranked list
                          # must show EVERY qualifying play, and the daily run is a
                          # pre-generated 10-min job, so a low cap would silently
                          # drop strong plays on a big board (e.g. 100+ props).
MAX_LOOKAHEAD_HOURS = 24  # only pick matches that play within this many hours
# Per-prop-type minimum confidence to qualify for the ranked list.
#   STANDARD (75): Aces / Break Points Won / Double Faults. The ranked list shows
#   every qualifying play, so the bar is set to keep the list to genuinely strong
#   plays (>=75% confidence).
#   HIGH BAR (80): Total Games (match total) AND Player Total Games Won. Both are
#   derived, higher-variance stats — the match total depends on BOTH players'
#   combined performance plus match-length variance, and per-player games won is
#   compounded from holds + breaks + win-prob share — so they carry a bar above
#   standard and only surface when the data strongly supports them.
#   Total Games sat at 90 until 2026-07-14, when a full-board audit showed the
#   model does not produce 90+ on that prop: all 33 Total Games candidates that
#   night scored <=80, so the bar wasn't gating the category, it was silently
#   excluding it. 80 keeps it a rare, genuinely-strong play instead of dead
#   weight. Total Games still cannot take the ⭐ slot without a 90% favourite
#   (see _star_eligible) — that gate is unchanged.
STANDARD_MIN_CONF    = 75   # Aces / Break Points Won (Double Faults is excluded)
# Total Games moved 80 -> 85 on 2026-07-14. After the data-integrity fixes (cache
# guard, deterministic event selection, stat-rich standardisation) the cleaner
# samples raised measured variance on Aces/BP Won — which the EVR grade correctly
# reads as less certainty, dropping those scores — while Total Games, a
# match-level aggregate of both players, held steady. That inverted the intended
# prop preference: TG started filling the board at 80-82 while the props the model
# is actually built around got squeezed out. The bar restores the hierarchy at the
# QUALIFICATION layer rather than by touching any score: TG must now be genuinely
# strong (85+) to make the list at all, and still needs a 90%+ favourite to take
# the ⭐ (see _star_eligible). Nothing about how TG is scored changed.
# 85 -> 80 on 2026-07-15, forced by the games_per_set fit (FREEZE_LOG entry 2):
# Total Games now carries an 80 CONFIDENCE CEILING, because the fit measured that
# combined hold explains only R^2 0.09-0.16 of games-per-set variance. An 85 bar
# above an 80 ceiling would make the prop unqualifiable — the exact degenerate
# ceiling==bar trap already found on PTGW. 80/80 matches how PTGW is treated: a
# derived, compounded stat qualifies only when it maxes out its ceiling. That is
# acceptable strictness for a prop the model demonstrably predicts poorly.
TOTAL_GAMES_MIN_CONF = 80   # Total Games (match total) — at its ceiling
PLAYER_TGW_MIN_CONF  = 80   # Player Total Games Won — high bar (bespoke paths below)
# Per-prop overrides; anything not listed uses STANDARD_MIN_CONF.
PROP_MIN_CONF = {
    "Total Games":             TOTAL_GAMES_MIN_CONF,   # 85
    "Player Total Games Won":  PLAYER_TGW_MIN_CONF,    # 80
}

# ── PTGW verification gate (structural rebuild — see FREEZE_LOG.md) ───────────
# The PTGW chain was rebuilt from a mean-vs-line EVR grade to a scenario-mixture
# P(over) model after every 7/16 PTGW pick lost. Until Shawn reviews the live
# shadow output and flips this flag, PTGW is EXCLUDED from the posted board, the
# 3x slip, and POTD eligibility, and /prop returns "PTGW under rebuild". The new
# chain STILL runs on every board evaluation and logs its projection in shadow
# mode (POD_PTGW_SHADOW), so it can be judged on live slates without posting.
# DO NOT default this to true — it stays false until explicitly enabled.
PTGW_ENABLED = os.getenv("PTGW_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on")
# Fantasy Score gate. ENABLED (2026-07-16) after the shadow review + fixes: market
# win-prob anchor, median "fair line" display, derived claim, and the divergence
# guard (which caps FS at 70 whenever model & book disagree on the outcome — so FS
# favourites post as flagged volume plays, never as the ⭐). Set FS_ENABLED=false to
# return it to shadow. FS reuses the PTGW scenario mixture.
FS_ENABLED = os.getenv("FS_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on")
# Slate-correlation guard (Part 3): at most this many PTGW picks per board, and a
# flag when multiple share the same implied direction. Only enforced once enabled.
PTGW_MAX_PER_BOARD = 2
# Total Games (match total) floods the board: it's a match-level stat with many
# qualifying lines and, since the games_per_set fit, a low-information prop capped
# at 80. Left uncapped it crowds out the props the model is actually built around.
# Keep only the highest-ranked few per board.
TOTAL_GAMES_MAX_PER_BOARD = 3


# ── Thin-slate mode ──────────────────────────────────────────────────────────
# Some days the board simply has nothing to analyse. Observed 2026-07-15: 1000
# tennis props listed but only 101 STANDARD — Break Points Won had ZERO standard
# lines (all 7 were goblins/demons, which are never played), and Aces had ONE. The
# eligible board was 1 Aces + 30 PTGW + 35 Total Games — i.e. entirely the two
# props carrying the HIGHEST bars (80 and 85), with the 75-bar props absent.
#
# The normal bars assume a full board where being selective costs nothing because
# something else always qualifies. On a thin slate that assumption inverts: the
# bars stop selecting the best plays and start selecting NOTHING. Holding a 85 bar
# against a board that can only offer Total Games isn't discipline, it's just
# silence.
#
# So when there is little to analyse, every gate drops to 70 and the ranking's
# existing confidence-first / edge-tiebreak ordering does the discriminating —
# with confidences compressed into a narrow band, the edge differential is what
# actually separates the plays. The ranking rule itself is UNCHANGED: confidence
# still outranks edge absolutely; edge just does more work when confidence ties.
#
# Gated on candidates actually SCORED (post 24h-window, post-resolution) — that is
# the real "props to analyse" count, not the raw listing.
THIN_SLATE_SCORED_MAX = 25   # fewer scored candidates than this => thin slate
THIN_SLATE_MIN_CONF   = 70   # (v1) every gate dropped to this when thin — moot under v2's 65 floor
THIN_SLATE_NOTE = "⚠️ Play lightly — slate not very full today."


# ══ BOARD QUALIFICATION POLICY v2 (2026-07-16) ═══════════════════════════════
# Selection policy ONLY — no projection, confidence, or guard math changes here.
#   • Board + 3x eligibility: ANY prop qualifies at confidence >= 65. This single
#     floor replaces every v1 per-prop bar (standard 70/75, Total Games 85,
#     PTGW 80, and the PTGW blowout-UNDER 75 exception — now moot).
#   • 3x slip legs must be >= 70 (one notch above the floor — don't build slips
#     from floor picks).
#   • Pick of the Day: a uniform 80 across ALL prop types. Double Faults is the
#     ONLY prop permanently blocked from the ⭐ slot (it still populates the board
#     and 3x normally). See _star_eligible.
# What is deliberately UNCHANGED: all confidence computation, knife-edge checks,
# the PTGW structural guards, depth ceilings (they gate via confidence.py exactly
# as before), and the ranking rule (confidence DESC, edge magnitude tiebreaker).
BOARD_MIN_CONF = 65   # uniform board + 3x-pool floor
SLIP_MIN_CONF  = 70   # a 3x leg must clear this (above the board floor)
POTD_THRESHOLD = 80   # uniform Pick-of-the-Day bar, every eligible prop
# The ⭐ exclusions: Double Faults never leads the card, and neither does any DEMON
# (its boosted payout structure is not part of the standard public POTD record).
POD_STAR_EXCLUDE_PROPS = {"Double Faults"}

# ── Demon props (boosted alternate lines, over-only) ─────────────────────────
# Demons are evaluated through the normal projection chain but held to ELEVATED
# bars — most are traps; only a few are mispriced our way. Config values:
DEMON_MIN_CONF = 85    # a demon must clear this confidence (above the 65 board floor)
DEMON_MIN_EDGE = 0.9   # AND the projection must clear the boosted line by this many
                       # units (absolute edge in the prop's own units)
# Note: the backend data ceiling already requires BOTH players 15+ stat-rich for
# any 85+ confidence, so a demon at 85 implicitly rests on deep data — we do NOT
# weaken that ceiling for demons.
# One week from the v2 cutover, log picks that qualify under v2 but would have been
# excluded under v1 (so we can see exactly what the looser floor lets in).
V2_CUTOVER_DATE   = "2026-07-16"
V2_DIFF_LOG_UNTIL = "2026-07-23"


def _min_conf_for(prop_type: str, thin: bool = False) -> int:
    """v2: the board qualification floor is a UNIFORM 65 for every prop type. The
    old per-prop bars and the thin-slate drop are gone — 65 is already below the
    old thin floor of 70, so a thin slate no longer needs its own (lower-would-be)
    bar. ``thin`` is accepted for signature stability but no longer changes the
    floor. Confidence itself is untouched; this is purely which picks make the list."""
    return BOARD_MIN_CONF


def _v1_min_conf_for(prop_type: str) -> int:
    """v1 bar for a prop, for the one-week v2-vs-v1 delta log ONLY. Not used for
    selection. Mirrors the retired per-prop bars (Total Games 85→ its 80 ceiling,
    PTGW 80, else 75)."""
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

# Prop types excluded from the BOARD entirely. v2: this is now EMPTY. Double
# Faults used to be board-excluded; under v2 it populates the ranked board and 3x
# normally and is blocked ONLY from the ⭐ slot (POD_STAR_EXCLUDE_PROPS). No prop
# is barred from the board itself.
_POD_EXCLUDE_PROPS = set()


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
        # PrizePicks lists "standard", "demon" (boosted, higher line, over-only,
        # modified payout) and "goblin" (reduced, lower line) variants. v2+demon:
        # standard and demon are BOTH evaluated (demons under stricter bars, see
        # _demon_qualifies); goblins remain excluded entirely; any other exotic
        # type is skipped. Never fabricate a line when none exists.
        _ot = (attr.get("odds_type") or "standard").lower()
        if _ot not in ("standard", "demon"):
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
                    "prop_type": prop_type, "line": line_f, "odds_type": _ot})
    # Attach the STANDARD-line context to each demon (same player + prop), so the
    # display can show members "the boosted line vs the normal one". None when the
    # board carries no standard variant for that prop.
    std_lines = {(e["player"], e["prop_type"]): e["line"]
                 for e in out if e["odds_type"] == "standard"}
    for e in out:
        if e["odds_type"] == "demon":
            e["standard_line"] = std_lines.get((e["player"], e["prop_type"]))
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
            "odds_type": prop.get("odds_type", "standard"),
            "standard_line": prop.get("standard_line"),   # demon's normal-line context
            "surface": payload["surface"], "tournament": tournament,
            "start_timestamp": nm.get("start_timestamp"),
            "projection": proj, "edge": edge, "edge_mag": abs(edge),
            "confidence": conf, "lean": data.get("lean"),
            "p1_win_prob": data.get("p1_win_prob"), "p2_win_prob": data.get("p2_win_prob"),
            # PTGW scenario-mixture surface (None for other props) — used by the
            # shadow log, the correlation guard, and the implied-claim display.
            "ptgw_p_over": data.get("ptgw_p_over"),
            "ptgw_p_win_match": data.get("ptgw_p_win_match"),
            "ptgw_implied_claim": data.get("ptgw_implied_claim"),
            "ptgw_knife_edge": data.get("ptgw_knife_edge"),
            "fs_p_over": data.get("fs_p_over"),
            "fs_implied_claim": data.get("fs_implied_claim"),
            "fs_knife_edge": data.get("fs_knife_edge"),
            "explanation": data.get("plain_english_explanation"),
            # Legacy combined score — kept for logging/diagnostics ONLY. It is NOT
            # the ranking key; see _rank_key(), which orders confidence-first with
            # edge as a pure tiebreaker. This additive blend still let a big edge
            # overcome a higher confidence (an 80-conf/6.0-edge play outscored an
            # 81-conf/4.4-edge one), which is exactly what the ranking rule forbids.
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
# DEPTH GOVERNS. The depth test runs FIRST and outranks every exception below:
#   • SHALLOW (either side < 15 stat-rich surface matches): bar is the standard 80
#     with NO exceptions. The backend independently caps such plays at 76 (see
#     _PTGW_SHALLOW_CEILING in confidence.py), so a shallow PTGW play can never
#     qualify — blowout or not.
#     WHY the blowout exception cannot rescue a shallow play: the exception is
#     justified by a large win-probability gap, but that gap is ITSELF computed
#     from the same shallow data. Letting it relax the bar would let a thin-data
#     play borrow credibility from its own uncertain conclusion — the evidence for
#     the exception is exactly as unreliable as the play it would be excusing.
#     So the relaxation is only available to plays that already passed the depth
#     test, i.e. the same deep-data condition that permits the full 80 ceiling.
#
# Only for plays PASSING the depth test, the set-count rules apply (PTGW only):
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
PLAYER_TGW_BLOWOUT_UNDER_BAR = 75     # relaxed bar for a blowout UNDER — DEEP DATA ONLY
PLAYER_TGW_OVER_MIN_EDGE     = 1.5    # blowout OVER must clear the line by >= this
PLAYER_TGW_KNIFE_EDGE        = 0.7    # |proj - line| <= this → coin-flip zone
PLAYER_TGW_KNIFE_PENALTY     = 10     # confidence subtracted in the coin-flip zone
# Stat-rich surface matches required on BOTH sides for a PTGW play to reach the
# 80 ceiling AND to be eligible for the blowout-under relaxation. Mirrors the
# backend's own depth test so the bar and the ceiling agree on "deep".
PLAYER_TGW_DEEP_MIN_MATCHES  = 15


def _ptgw_depth_ok(pk: dict):
    """(deep, p1_n, p2_n) — do BOTH players clear the stat-rich depth bar?

    Prefers the backend's ``player_deep``/``opponent_deep`` flags, which are the
    SINGLE SOURCE OF TRUTH: they already carry the 7-day depth hysteresis, so this
    gate and the backend's own ceilings can't disagree about who is deep. A raw
    count read here would flap independently of the ceiling that capped the score.

    Falls back to the raw counts only if the flags are absent (older backend).
    Unknown depth is treated as SHALLOW (conservative): a missing signal can't
    demonstrate depth, and this prop compounds several models."""
    d = pk.get("data") or {}
    p1, p2 = d.get("player_ta_matches"), d.get("opponent_ta_matches")
    d1, d2 = d.get("player_deep"), d.get("opponent_deep")
    if isinstance(d1, bool) and isinstance(d2, bool):
        return (d1 and d2), p1, p2
    if not isinstance(p1, (int, float)) or not isinstance(p2, (int, float)):
        return False, p1, p2
    return (p1 >= PLAYER_TGW_DEEP_MIN_MATCHES
            and p2 >= PLAYER_TGW_DEEP_MIN_MATCHES), p1, p2


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


def _ptgw_qualify(pk: dict, thin: bool = False):
    """(qualifies, bar, path) for a Player Total Games Won candidate, using its
    current (post-knife-edge) confidence. path ∈ {'shallow-standard-80',
    'standard-80', 'blowout-under-75', 'blowout-over-strict'}.

    DEPTH FIRST — see the block comment above. A shallow play gets the standard
    bar and no exception; the backend has already capped it at 76.

    ``thin`` drops the base bar to THIN_SLATE_MIN_CONF (70). Note the blowout-under
    relaxation is then clamped to the base too: at 70 the normal 75 relaxation
    would be STRICTER than the standard bar, which would invert the exception into
    a penalty. An exception must never make a play harder to qualify."""
    conf = pk.get("confidence") or 0
    proj, line = pk.get("projection"), pk.get("line")
    lean = _lean_dir(pk)
    gap = _win_prob_gap(pk)
    base = _min_conf_for("Player Total Games Won", thin=thin)   # 80, or 70 if thin
    blowout_under_bar = min(PLAYER_TGW_BLOWOUT_UNDER_BAR, base)
    blowout = gap is not None and gap > PLAYER_TGW_BLOWOUT_WPGAP

    deep, p1_n, p2_n = _ptgw_depth_ok(pk)
    if not deep:
        # Log ONLY when the depth rule actually overrode an exception the play
        # would otherwise have received — that's the interaction worth watching.
        if blowout and lean == "UNDER":
            log.info(
                "POD_PTGW_DEPTH_BLOCK | %-22s p1=%s p2=%s stat-rich (need %d both) | "
                "gap=%.0fpp UNDER would have relaxed the bar to %d — WITHHELD, bar stays "
                "%d (the gap is computed from the same shallow data) | conf=%.0f -> %s",
                (pk.get("player") or "")[:22], p1_n, p2_n, PLAYER_TGW_DEEP_MIN_MATCHES,
                gap or 0.0, PLAYER_TGW_BLOWOUT_UNDER_BAR, base, conf,
                "still qualifies" if conf >= base else "blocked",
            )
        return conf >= base, base, "shallow-standard-80"

    if blowout and lean == "UNDER":
        return (conf >= blowout_under_bar, blowout_under_bar,
                "blowout-under-%d" % blowout_under_bar)
    if blowout and lean == "OVER":
        edge_ok = (isinstance(proj, (int, float)) and isinstance(line, (int, float))
                   and (proj - line) >= PLAYER_TGW_OVER_MIN_EDGE)
        return (conf >= base and edge_ok), base, "blowout-over-strict"
    return conf >= base, base, "standard-80"


def _rank_key(pk: dict) -> tuple:
    """Ranking key (sort DESCENDING). Two explicit levels, confidence first:

      1. confidence  — the primary and dominant term
      2. edge_mag    — tiebreaker ONLY, among plays of equal confidence

    A play with genuinely higher confidence therefore ALWAYS outranks a lower-
    confidence one no matter how large the latter's projected edge. This replaces
    the old additive ``conf + abs(edge)`` score, under which a ceiling-pinned
    80-conf play with a 6.0 edge (86) jumped ahead of an 81-conf play with a 4.4
    edge (85.4) — inverting the rule that confidence outweighs edge. Props with a
    hard confidence ceiling (Player Total Games Won caps at 80) all tie at the
    ceiling and now order by edge among THEMSELVES, at the bottom of their band,
    instead of leapfrogging higher-confidence plays.
    """
    return (pk.get("confidence") or 0, pk.get("edge_mag") or abs(pk.get("edge") or 0))


def _passes_quality(pk: dict) -> bool:
    """v2 board quality gate — a single uniform floor for EVERY prop type. The
    PTGW bespoke bar logic is retired; PTGW's depth ceiling and structural guards
    still shape its confidence upstream (in confidence.py / main.py), and here it
    clears the same 65 floor as everything else. Demons use _demon_qualifies."""
    if pk.get("odds_type") == "demon":
        return _demon_qualifies(pk)
    return (pk.get("confidence") or 0) >= BOARD_MIN_CONF


def _demon_qualifies(pk: dict) -> bool:
    """A demon qualifies for the board/3x ONLY when it clears the elevated demon
    bars AND points OVER (demons are over-only by platform rule). Every rejection
    is logged so the bars can be reviewed against what they filter. Returns False
    (never posts) unless all three hold: OVER lean, conf >= 85, edge >= 0.9."""
    conf = pk.get("confidence") or 0
    proj = pk.get("projection")
    line = pk.get("line")
    edge = (proj - line) if isinstance(proj, (int, float)) and isinstance(line, (int, float)) else None
    who = (pk.get("player") or "")[:22]
    # Over-only: a demon whose model edge points UNDER is not a play, ever.
    if _lean_dir(pk) != "OVER":
        log.info("POD_DEMON_REJECT | demon_under_no_play | %-22s %-18s line=%-5s "
                 "proj=%-6s conf=%-3.0f edge=%s — demons are over-only, discarded",
                 who, (pk.get("prop_type") or "")[:18], line,
                 "%.2f" % proj if isinstance(proj, (int, float)) else "?", conf,
                 "%+.2f" % edge if edge is not None else "?")
        return False
    conf_ok = conf >= DEMON_MIN_CONF
    edge_ok = edge is not None and edge >= DEMON_MIN_EDGE
    if conf_ok and edge_ok:
        log.info("POD_DEMON_OK | %-22s %-18s line=%-5s proj=%-6.2f conf=%-3.0f edge=%+.2f "
                 "(bars %d/%.1f) -> QUALIFIES",
                 who, (pk.get("prop_type") or "")[:18], line, proj, conf, edge,
                 DEMON_MIN_CONF, DEMON_MIN_EDGE)
        return True
    _why = []
    if not conf_ok:
        _why.append("conf %.0f < %d" % (conf, DEMON_MIN_CONF))
    if not edge_ok:
        _why.append("edge %s < %.1f" % (("%+.2f" % edge) if edge is not None else "?", DEMON_MIN_EDGE))
    log.info("POD_DEMON_REJECT | %-22s %-18s line=%-5s proj=%-6s conf=%-3.0f edge=%s -> "
             "below bars (%s)",
             who, (pk.get("prop_type") or "")[:18], line,
             "%.2f" % proj if isinstance(proj, (int, float)) else "?", conf,
             "%+.2f" % edge if edge is not None else "?", "; ".join(_why))
    return False


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

    # One evaluation per (player, prop, odds_type) — the projection is line-
    # independent, but a demon carries a DIFFERENT (boosted) line than the
    # standard, so both variants must be evaluated separately against their lines.
    seen, by_type = set(), {}
    for pr in props:
        k = (_norm(pr["player"]), pr["prop_type"], pr.get("odds_type", "standard"))
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
    by_type_qual = {}   # qualifying count per prop type
    by_type_eval = {}   # SCORED count per prop type — the composition denominator

    # THIN-SLATE CHECK — decided BEFORE any gating, from the candidates that
    # actually scored. Everything is scored regardless; only the BAR changes, so
    # this costs nothing and never re-evaluates the board.
    scored = [r for r in results if isinstance(r, dict)]
    thin_slate = len(scored) < THIN_SLATE_SCORED_MAX
    if thin_slate:
        log.info("POD_THIN_SLATE | only %d candidates scored (<%d) — dropping EVERY "
                 "confidence gate to %d. Normal bars (75/80/85) select nothing on a "
                 "board this thin; edge differential does the separating via the "
                 "existing confidence-first / edge-tiebreak ranking.",
                 len(scored), THIN_SLATE_SCORED_MAX, THIN_SLATE_MIN_CONF)

    for r in results:
        if not isinstance(r, dict):
            log.info("POD_CAND | EVAL_FAILED | %s", str(r)[:120])
            continue
        ptype = r.get("prop_type")
        by_type_eval[ptype] = by_type_eval.get(ptype, 0) + 1
        # PTGW: apply the knife-edge penalty (mutates conf + coin_flip) FIRST — a
        # confidence adjustment, UNCHANGED by v2 — then, while the rebuild is
        # disabled, record its shadow projection and exclude it from the board.
        if ptype == "Player Total Games Won":
            _apply_ptgw_knife_edge(r)
            if not PTGW_ENABLED:
                log.info("POD_PTGW_SHADOW | %-22s conf=%-3.0f p_over=%-5s line=%-5s lean=%-5s "
                         "claim=%s — EXCLUDED (PTGW_ENABLED=false, rebuild)",
                         (r.get("player") or "")[:22], r.get("confidence") or 0,
                         r.get("ptgw_p_over"), r.get("line"), _lean_dir(r),
                         r.get("ptgw_implied_claim") or "?")
                continue
        # Fantasy Score: shadow mode until FS_ENABLED flips. Log the projection so
        # it can be judged on live slates, then exclude from the board. An FS demon
        # is structurally impossible (FS ceiling 80 < DEMON_MIN_CONF 85) — log any
        # that would otherwise qualify, per spec.
        if ptype == "Fantasy Score":
            if r.get("odds_type") == "demon":
                log.info("POD_FS_DEMON | %-22s conf=%-3.0f line=%-5s — FS demon "
                         "(impossible: FS ceiling 80 < demon bar %d), logged",
                         (r.get("player") or "")[:22], r.get("confidence") or 0,
                         r.get("line"), DEMON_MIN_CONF)
            if not FS_ENABLED:
                log.info("POD_FS_SHADOW | %-22s conf=%-3.0f p_over=%-5s line=%-5s lean=%-5s "
                         "claim=%s — EXCLUDED (FS_ENABLED=false, shadow)",
                         (r.get("player") or "")[:22], r.get("confidence") or 0,
                         r.get("fs_p_over"), r.get("line"), _lean_dir(r),
                         r.get("fs_implied_claim") or "?")
                continue
        # Demons: elevated bars, over-only (see _demon_qualifies, which logs its
        # own accept/reject line). Standard props: the uniform v2 65 floor.
        conf = r.get("confidence") or 0
        if r.get("odds_type") == "demon":
            bar = DEMON_MIN_CONF
            ok = _demon_qualifies(r)
        else:
            bar = _min_conf_for(ptype)
            ok = conf >= bar
            log.info("POD_CAND | %-22s %-18s line=%-5s conf=%-3.0f proj=%-6.2f edge=%+5.2f "
                     "recent_ok=%-5s bar=%d -> %s",
                     (r.get("player") or "")[:22], (ptype or "")[:18],
                     r.get("line"), conf, r.get("projection") or 0.0, r.get("edge") or 0.0,
                     _recent_supports_lean(r), bar,
                     "QUALIFIES" if ok else ("below v2 floor %d" % bar))
        if ok:
            picks.append(r)
            by_type_qual[ptype] = by_type_qual.get(ptype, 0) + 1
            # One-week v2-vs-v1 delta: would this pick have been EXCLUDED under v1?
            if datetime.now(_ET).strftime("%Y-%m-%d") <= V2_DIFF_LOG_UNTIL:
                _is_demon = r.get("odds_type") == "demon"
                _v1_excluded_df = ptype == "Double Faults"   # v1 barred DF from the board
                # v1 evaluated neither DF (board-excluded) nor demons (odds_type filtered).
                _v1_ok = (not _v1_excluded_df) and (not _is_demon) and conf >= _v1_min_conf_for(ptype)
                if not _v1_ok:
                    _why = ("evaluated demons (odds_type filtered)" if _is_demon
                            else "excluded DF from board" if _v1_excluded_df
                            else "bar was %d" % _v1_min_conf_for(ptype))
                    log.info("POD_V2_DIFF | %-22s %-18s conf=%-3.0f line=%-5s -> ADDED by v2 "
                             "(v1 never %s)",
                             (r.get("player") or "")[:22], (ptype or "")[:18], conf,
                             r.get("line"), _why)
    log.info("POD: evaluated=%d eligible=%d (v2 uniform board floor=%d, POTD bar=%d)",
             len(uniq), len(picks), BOARD_MIN_CONF, POTD_THRESHOLD)

    # ── BOARD COMPOSITION ────────────────────────────────────────────────────
    # One line per prop type: how many were scored vs how many qualified, and what
    # SHARE of the final list each type holds. Composition drift is the thing that
    # gets noticed by eye far too late — Total Games quietly filling the board at
    # 80-82 after the data fixes changed measured variance is exactly the pattern
    # this makes visible daily. A type trending toward a majority share is the
    # signal to look at its bar, not at the individual plays.
    _total_q = len(picks)
    for _pt in sorted(set(list(by_type_eval.keys()) + list(by_type_qual.keys()))):
        _ev, _q = by_type_eval.get(_pt, 0), by_type_qual.get(_pt, 0)
        log.info("POD_COMPOSITION | %-22s scored=%-3d qualified=%-3d (%4.1f%% pass) "
                 "| %4.1f%% of list | bar=%d",
                 _pt or "?", _ev, _q, (_q / _ev * 100) if _ev else 0.0,
                 (_q / _total_q * 100) if _total_q else 0.0, _min_conf_for(_pt))
    log.info("POD_COMPOSITION | TOTAL qualifying=%d | by type: %s",
             _total_q, dict(by_type_qual) or "none")
    picks.sort(key=_rank_key, reverse=True)
    # Keep only each player's single best-scoring play so the ranking has one
    # entry per player (no same player twice for different props).
    best_per_player, ordered = set(), []
    for pk in picks:
        key = _norm(pk["player"])
        if key in best_per_player:
            continue
        best_per_player.add(key)
        ordered.append(pk)

    # ── Part 3 slate-correlation guard (only meaningful once PTGW_ENABLED) ────
    # Cap PTGW at PTGW_MAX_PER_BOARD (keep the highest-ranked), and flag when the
    # surviving PTGW picks all imply the same match direction (e.g. all "favourite
    # wins in straights") — a correlated cluster that is really one bet repeated.
    if PTGW_ENABLED:
        _ptgw = [pk for pk in ordered if pk.get("prop_type") == "Player Total Games Won"]
        if len(_ptgw) > PTGW_MAX_PER_BOARD:
            _drop = set(id(pk) for pk in _ptgw[PTGW_MAX_PER_BOARD:])
            log.info("POD_PTGW_CORR | %d PTGW picks > cap %d — dropping %d lowest-ranked",
                     len(_ptgw), PTGW_MAX_PER_BOARD, len(_drop))
            ordered = [pk for pk in ordered if id(pk) not in _drop]
            _ptgw = _ptgw[:PTGW_MAX_PER_BOARD]
        if len(_ptgw) >= 2:
            _dirs = {_lean_dir(pk) for pk in _ptgw}
            if len(_dirs) == 1:
                for pk in _ptgw:
                    pk["ptgw_correlated"] = True
                log.info("POD_PTGW_CORR | %d PTGW picks ALL %s — correlated cluster flagged",
                         len(_ptgw), next(iter(_dirs)))

    # ── Total Games board cap ────────────────────────────────────────────────
    # Keep only the highest-ranked TOTAL_GAMES_MAX_PER_BOARD Total Games plays; the
    # rest are dropped so the board isn't dominated by a low-information prop. The
    # ⭐ can still be a Total Games play if it's the strongest overall.
    _tg = [pk for pk in ordered if pk.get("prop_type") == "Total Games"]
    if len(_tg) > TOTAL_GAMES_MAX_PER_BOARD:
        _drop = set(id(pk) for pk in _tg[TOTAL_GAMES_MAX_PER_BOARD:])
        log.info("POD_TG_CAP | %d Total Games picks > cap %d — dropping %d lowest-ranked",
                 len(_tg), TOTAL_GAMES_MAX_PER_BOARD, len(_drop))
        ordered = [pk for pk in ordered if id(pk) not in _drop]
    return ordered, thin_slate


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
    STEP 3: only return a slip if TWO candidates clear the 3x leg bar (v2:
            SLIP_MIN_CONF = 70, one notch above the board floor — don't build
            slips from floor picks). Never force a weak second leg — return [].
    """
    if not ordered:
        return []
    potd_keys = {(_norm(p["player"]), p["prop_type"]) for p in (potd or [])}
    # Correlation avoidance also covers the POTD: a 3x leg from the SAME match as
    # a Pick of the Day (e.g. the other server's aces) is correlated with it and
    # undercuts the "distinct value from each post" goal, so exclude those whole
    # matches — not just the exact (player, prop_type) already picked.
    potd_matches = {_match_key(p) for p in (potd or [])}
    # ``ordered`` already contains board-qualifying picks (>= 65). 3x legs must
    # additionally clear the higher SLIP_MIN_CONF (70) bar.
    pool = [c for c in ordered
            if (_norm(c["player"]), c["prop_type"]) not in potd_keys
            and _match_key(c) not in potd_matches
            and (c.get("confidence") or 0) >= SLIP_MIN_CONF]
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
        ordered, _thin = await _rank_board()
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


# Props that may NEVER hold the ⭐ Pick-of-the-Day slot, however well they score.
# Player Total Games Won is hard-capped at 80 confidence (it's derived from several
# compounding models), so every strong one pins to exactly 80 and they tie; letting
# a ceiling-pinned play lead the card would make the ⭐ an edge-magnitude contest.
# It still ranks anywhere in the list — it just can't be the headline play.
_STAR_INELIGIBLE_PROPS = set()      # nothing is banned outright; see _star_eligible

# A PTGW UNDER may hold the ⭐ ONLY when the structure carries it, not the stats.
# The 7/15 18:50 post is the case this exists for: Gina Feistel UNDER 11.5 went out
# as ⭐ on "Hold 94%" that was 15/16 service games from TWO ITF matches, against an
# opponent who was only a 73% favourite. A games-won UNDER is a bet that the match
# is short and one-sided. What makes that true is the OPPONENT overwhelming the
# player — not a thin hold rate on the player's own side. So:
#   • the opponent must be overwhelmingly dominant (>= 85% win prob), and
#   • the player's hold/return rates must rest on a real sample of GAMES.
# Neither alone is enough. Below either, the play can still rank — it just can't
# be the headline.
STAR_PTGW_MIN_OPP_WP    = 85.0   # opponent win prob for a PTGW UNDER to lead
STAR_PTGW_MIN_RATE_GAMES = 40    # service games behind the player's hold rate


def _star_eligible(pk: dict) -> bool:
    """v2: a play may hold the ⭐ Pick-of-the-Day slot iff it clears the uniform
    80 threshold AND its prop is not permanently star-blocked.

    ONE exclusion mechanism, ONE entry: Double Faults may never be the Pick of the
    Day, however high it scores (it still populates the board and 3x). Every other
    prop — Aces, Break Points Won, Total Games, Player Total Games Won — is
    star-eligible at >= 80. The old TG-90%-favourite bar and the PTGW-UNDER
    structural requirement are RETIRED; those confidence-shaping stories now live
    entirely inside the projection/guard chain, not in a second selection gate.

    (PTGW is theoretical here until PTGW_ENABLED flips: its confidence ceiling is
    80/76, so it can only ever star at exactly its ceiling — no special handling.)

    Demons are NEVER star-eligible: the boosted-payout structure is not part of the
    standard public POTD record, which stays standard-only."""
    if pk.get("odds_type") == "demon":
        return False
    if pk.get("prop_type") in POD_STAR_EXCLUDE_PROPS:
        return False
    return (pk.get("confidence") or 0) >= POTD_THRESHOLD


def _promote_star(ordered: list):
    """(ordered, has_star). Puts a ⭐-eligible play at ordered[0] when one exists.

    Returns has_star=False when NOTHING on the board can hold the ⭐ — and the
    caller then posts a ranked board with NO Pick of the Day.

    This used to fall back to "keep the ineligible play as ⭐ rather than post no
    ⭐", which quietly defeated the eligibility rules the moment a board was
    single-prop. On 7/15 every qualifying play was PTGW, so nothing was eligible,
    the fallback fired, and Gina Feistel's thin-data UNDER led the card — exactly
    the play the rules were written to keep out of that slot. A ⭐ is a claim that
    one play is the best on the board; when no play can carry that claim, the
    honest output is no ⭐, not the least-bad one wearing the badge."""
    if not ordered:
        return ordered, False
    if _star_eligible(ordered[0]):
        return ordered, True
    blocked = ordered[0]
    idx = next((i for i, p in enumerate(ordered) if _star_eligible(p)), None)
    if idx is None:
        log.info("POD_NO_STAR | top play (%s %s) can't hold the ⭐ and NO play on "
                 "the board is ⭐-eligible — posting the ranked board with no Pick "
                 "of the Day rather than promoting an ineligible play",
                 blocked.get("player"), blocked.get("prop_type"))
        return ordered, False
    star = ordered.pop(idx)
    log.info("POD: %s %s is not ⭐-eligible — demoted; promoting %s %s (conf %s) "
             "to Pick of the Day",
             blocked.get("player"), blocked.get("prop_type"),
             star.get("player"), star.get("prop_type"), star.get("confidence"))
    return [star] + ordered, True


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
        ordered, thin_slate = await _rank_board()
        if ordered is None:
            return {"ranked": None, "slip": [], "thin_slate": False, "has_star": False}
        if not ordered:
            return {"ranked": [], "slip": [], "thin_slate": thin_slate, "has_star": False}
        # ⭐ gate. has_star=False -> the board is posted with NO Pick of the Day.
        ordered, has_star = _promote_star(ordered)
        # One-off (today only): hold specific players out of the ⭐ slot.
        ordered = _apply_star_exclusions(ordered)
        # 3x excludes only the ⭐ POTD (ordered[0]) and its match — drawn from the
        # FULL evaluated pool (not just the posted top-6). With NO ⭐ there is
        # nothing to exclude, so the slip may draw from the whole board.
        slip = _select_slip(ordered, ordered[:1] if has_star else [])
        # Post only the top-N plays (⭐ + the next best), even though the whole
        # board was evaluated.
        return {"ranked": ordered[:MAX_RANKED_PLAYS], "slip": slip,
                "thin_slate": thin_slate, "has_star": has_star}
    except Exception as exc:  # noqa: BLE001 — total isolation
        log.exception("POD generate_ranked_and_slip failed: %s", exc)
        return {"ranked": [], "slip": [], "thin_slate": False, "has_star": False}


async def evaluate_fixed_props(specs: list) -> list:
    """Re-score a FIXED, already-known set of plays with the CURRENT model —
    bypassing the board fetch and the match-window gate. Used to re-post an
    earlier slate with refreshed confidence. Each spec needs: player, opponent,
    prop_type, line, surface, tournament. Returns pick dicts (same shape as
    ``_evaluate``) for those that evaluate, in the SAME order. Never raises."""
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _ev(spec):
        async with sem:
            try:
                p = await _resolve(spec.get("player", ""))
                if not p:
                    log.info("REPOST skip (no player match): %r", spec.get("player"))
                    return None
                p_id, tour, p_name = p
                o = await _resolve(spec.get("opponent", ""), tours=(tour,))
                if not o:
                    log.info("REPOST skip (no opponent match): %r", spec.get("opponent"))
                    return None
                o_id, _, o_name = o
                surface = spec.get("surface") or _season_surface()
                court = spec.get("tournament") or ""
                payload = {
                    "player_id": p_id, "opponent_id": o_id,
                    "player_name": p_name, "opponent_name": o_name,
                    "tour": tour, "surface": surface, "court": court,
                    "qualifying": bool(court) and "qualif" in court.lower(),
                    "prop_type": spec.get("prop_type"), "prop_line": spec.get("line"),
                }
                data = None
                for attempt in range(CALC_RETRIES):
                    try:
                        data = await asyncio.to_thread(_post, "/api/prop/calculate", payload, CALC_TIMEOUT)
                        break
                    except (requests.exceptions.Timeout,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.HTTPError) as exc:
                        status = getattr(getattr(exc, "response", None), "status_code", None)
                        if status is not None and status < 500:
                            raise
                        if attempt == CALC_RETRIES - 1:
                            raise
                        await asyncio.sleep(2.0 * (attempt + 1))
                proj = data.get("model_projection")
                if proj is None:
                    return None
                conf = data.get("confidence") or 0
                line = spec.get("line")
                edge = proj - line
                return {
                    "player": p_name, "opponent": o_name, "player_id": p_id, "tour": tour,
                    "pp_player": spec.get("player"), "prop_type": spec.get("prop_type"),
                    "line": line, "original_line": line, "surface": surface,
                    "tournament": court or None, "start_timestamp": None,
                    "projection": proj, "edge": edge, "edge_mag": abs(edge),
                    "confidence": conf, "lean": data.get("lean"),
                    "p1_win_prob": data.get("p1_win_prob"), "p2_win_prob": data.get("p2_win_prob"),
                    "explanation": data.get("plain_english_explanation"),
                    "score": conf + abs(edge), "data": data,
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("REPOST eval failed for %r: %s", spec.get("player"), exc)
                return None

    results = await asyncio.gather(*[_ev(s) for s in specs])
    return [r for r in results if r]


async def generate_picks(n: int = 3):
    """Return up to ``n`` Pick-of-the-Day picks ranked best-first (list, possibly
    empty; None when the board has no eligible props). Never raises.
    Backwards-compatible wrapper around the shared evaluation pass."""
    try:
        ordered, _thin = await _rank_board()
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
