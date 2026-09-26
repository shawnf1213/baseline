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

# Venue clock for the card-date rule — see CARD_START_LOCAL_HOUR and the block
# in _price_one. Never fatal: without it every card falls back to the ET cutoff,
# which is the behaviour that shipped before it.
try:
    import venue_tz
except Exception:  # noqa: BLE001 — Rule 2, a missing helper must not stop a board
    venue_tz = None

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
# CUT 8 -> 5 (2026-09-18, user: "way too many out everyday"). Env-overridable so
# this can be tuned without a deploy. NOTE what the September numbers actually
# say about the reason given — see BOARD_MIN_CONF below: volume is not what is
# holding the hit rate down, so this cut buys a tighter card, not a better one.
# Matches starting at or after this ET hour tonight belong to TOMORROW's card,
# not today's. See the Asia-swing note in the slate filter below: a Singapore or
# Tokyo day opens around 22:00-23:00 ET and runs through the following ET
# morning. 21 is late enough that a US or European card is finished by then, so
# this only ever picks up an Asia-swing opener.
ASIA_SLATE_CUTOFF_HOUR = int(os.getenv("ASIA_SLATE_CUTOFF_HOUR", "21") or "21")

# The hour, IN THE VENUE'S OWN TIME, before which the next card is still today's
# and after which it is tomorrow's. A tennis card opens late morning local
# everywhere on tour, so this is a fact about the sport rather than about a
# region — unlike the ET cutoff above, it does not need revisiting when the tour
# moves. See the card-date block in _price_one.
CARD_START_LOCAL_HOUR = int(os.getenv("CARD_START_LOCAL_HOUR", "11") or "11")

MAX_RANKED_PLAYS = int(os.getenv("MAX_RANKED_PLAYS", "5") or "5")
                          # post the top-5 ranked plays (2026-09-03: 12 -> 8;
                          # 2026-09-18: 8 -> 5).
                          # The bot pages at 6, so a full board is one page of 6
                          # plus a short second page. The 3x still draws its legs
                          # from the full evaluated pool.
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

# ── PTGW gate (structural rebuild — see FREEZE_LOG.md) ───────────────────────
# PTGW was rebuilt from a mean-vs-EVR grade to a scenario-mixture P(over) model
# after every 7/16 PTGW pick lost. ENABLED (2026-07-22) after the shadow review +
# the market win-prob anchor (main.py PTGW branch): the mixture now keys off the
# de-vigged moneyline, not the model's underdog-skewed win prob — the same footing
# as FS. Set PTGW_ENABLED=false to return it to shadow (POD_PTGW_SHADOW logs the
# projection without posting; /prop returns "under rebuild").
PTGW_ENABLED = os.getenv("PTGW_ENABLED", "true").strip().lower() in (
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
# Raised 2 -> 3 (2026-07-29, user): the per-prop caps, not MAX_RANKED_PLAYS, were
# capping the board at ~6 plays. Loosened so a full 12-play board can fill while no
# single prop type dominates it.
# ── UNIFORM PER-PROP BOARD CAP (2026-09-03, user) ────────────────────────────
# At most BOARD_MAX_PER_PROP of any one prop type on a board, so no single prop
# can fill the list. Replaces four separately hand-tuned caps (PTGW 3, Total
# Games 4, Fantasy Score 4, Double Faults 2) that had drifted apart and left
# Aces uncapped entirely.
#
# BREAK POINTS WON IS EXEMPT. It is the prop the model is built around, carries
# the tightest absolute projection error we measure, and is the only prop
# allowed to lead the card below the uniform bar (STAR_BP_MIN_CONF). Capping it
# at two would throw away the plays with the most edge to make room for props
# that have less.
BOARD_MAX_PER_PROP = 2
BOARD_PROP_CAP_EXEMPT = {"Break Points Won"}

# (PTGW_MAX_PER_BOARD / TOTAL_GAMES_MAX_PER_BOARD / FS_MAX_PER_BOARD /
#  DF_MAX_PER_BOARD were retired here — BOARD_MAX_PER_PROP replaces all four.
#  The PTGW block below still runs, but only to FLAG a correlated cluster.)


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
# BACK TO 65 (2026-09-18, same day). Raised to 70 that morning to cut volume;
# put back within the day because it was the wrong lever and the board showed it
# — that night only 3 of 9 candidates cleared 70, and at 75 the board was EMPTY.
# It was dropping plays on a score that does not rank: measured over 677 graded
# picks the confidence AUC is 0.489 and a flat prior beats it on Brier, with the
# cashed rate by band running 65-69 44%, 70-74 59%, 75-79 44%, 80-84 54%. A
# floor on a number that does not separate winners from losers only shrinks the
# card; MAX_RANKED_PLAYS is the lever that actually controls volume, and it does
# it without pretending the cut was about quality.
# Qualifying-draw matches are excluded from the board — see the full evidence at
# the skip site in _price_one. Env-tunable so it reverts without a deploy.
SKIP_QUALIFYING = (os.getenv("SKIP_QUALIFYING", "1") or "1") not in ("0", "false", "False")

BOARD_MIN_CONF = int(os.getenv("BOARD_MIN_CONF", "65") or "65")
                      # uniform board + 3x-pool floor
SLIP_MIN_CONF  = 70   # a 3x leg must clear this (above the board floor)
POTD_THRESHOLD = 80   # uniform Pick-of-the-Day bar, every eligible prop
# The ⭐ exclusions: Double Faults never leads the card, and neither does any DEMON
# (its boosted payout structure is not part of the standard public POTD record).
#
# Player Total Games Won joins the permanent block (2026-09-03). It is hard-capped
# at 80 confidence and built from several compounding models, so every strong one
# pins to exactly the bar and they tie — which makes the ⭐ an edge-magnitude
# contest rather than a confidence one. The 9/3 card is the case: Schoolkate UNDER
# 15.5 led at conf 80 on a 13% win probability with a hold and return rate that
# both rested on ZERO service games. It still populates the board and the 3x at
# full weight; it just cannot be the headline play.
# EMPTIED 2026-09-13 (operator): any prop may hold the ⭐.
#
# The two entries were Double Faults and Player Total Games Won. Both were
# blocked on structural worries rather than on results, and the graded record
# does not support either: PTGW is the BEST prop on the board at 40-24 (62.5%),
# and Double Faults runs 39-33 (54.2%). Blocking the strongest prop from the
# headline was costing the ⭐ its best candidates.
#
# The set is kept rather than deleted so a prop can be blocked again by adding
# one string, and so the mechanism stays visible instead of being rediscovered.
POD_STAR_EXCLUDE_PROPS = set()
# PROBATION (Fix C3, 2026-07-23): Fantasy Score is a composite scenario-mixture prop
# that has NOT been out-of-sample backtested. It stays enabled and board/3x eligible,
# but cannot hold the ⭐ until a calibration backtest certifies it (projected P(over)
# vs realized). Distinct from the PERMANENT DF block above — remove this set entry
# once FS passes analysis/backtest_fs_calibration.py.
# Break Points Saved joins Fantasy Score on probation (2026-08-04): brand new,
# never graded against a result, and its first version had to be reverted for a
# volume error. It can populate a board but must not headline one until it has a
# track record.
# EMPTIED 2026-09-13 (operator): probation lifted with the exclusions above.
#
# Fantasy Score now HAS a record — 70-47 (59.8%), the second-best prop we post,
# and the least biased of any (-1.7%). The probation was written when it had
# none. Break Points Saved is still never posted (it is not in PROP_TYPES for
# the board), so its entry was moot.
POD_STAR_PROBATION_PROPS = set()

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

# Prop types excluded from the BOARD entirely. Filtered at the earliest point
# (the candidate loop below), so an excluded prop can never reach the ranked
# board, the 3x, or the ⭐ slot — this is a stronger bar than
# POD_STAR_EXCLUDE_PROPS, which only blocks the headline.
#
# Double Faults is OUT again (2026-09-03, user) pending a fix. It was
# board-excluded under v1, restored under v2, and is now withdrawn: it is the
# highest-variance prop we carry (Tier 3, never ⭐, capped confidence) and its
# projections have not been re-validated since the serve/return tour averages
# were found to be on the wrong scale — DF depends on second-serve and
# service-game rates that draw from the same stats.
#
# Re-enable by removing it here once the projection is fixed and backtested;
# nothing else needs to change.
# ── PROPS THE BOARD DOES NOT POST ────────────────────────────────────────────
# Measured on the full graded record (2026-09-13), not impressions:
#
#     Aces                118 picks   51-67   43.2%   projections -19.5% biased
#     Total Games         124 picks   62-62   50.0%
#     Double Faults        72 picks   39-33   54.2%   projections +32.3% biased
#     Break Points Won    143 picks   83-60   58.0%
#     Fantasy Score       117 picks   70-47   59.8%
#     Player Total Games   64 picks   40-24   62.5%
#
# ACES is a losing prop and a structurally broken one. The model over-projects
# by ~20%, and the bias is NOT match length — best-of-5 is LESS biased (-11.5%)
# than best-of-3 (-24.3%), which rules out expected_sets. It is also not
# uniform: the 12+ line bucket (genuine bombers) is nearly unbiased at -3% and
# is the only winning bucket. That is the signature of a returner's GLOBAL
# ace-conceded rate being applied to every server — a weak returner earns that
# rate against bombers, so reusing it for a mid-tier server double-counts. The
# fix is to condition ace-against on server type; until that exists the prop
# does not belong on the board.
#
# TOTAL GAMES is exactly break-even over 124 picks. A 50% prop is not a small
# edge, it is no edge, and posting it costs the vig while diluting a board whose
# other props run 58-62%.
#
# Removing both takes the posted record from 345-293 (54.1%) to 232-164 (58.6%).
#
# Both remain fully implemented and projectable — /prop still prices them, and
# the website still shows them. This is a BOARD policy, not a model deletion.
_POD_EXCLUDE_PROPS = {"Double Faults", "Aces", "Total Games"}


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
        # ONLY standard lines are evaluated. PrizePicks also lists "demon" (boosted,
        # higher line) and "goblin" (reduced, lower line) variants — BOTH are
        # excluded entirely here, at the earliest point, so they can never enter the
        # candidate pool, qualification, ranking, POTD, or the 3x. Demon evaluation
        # was reverted: a demon has no placeable UNDER and must never reach POTD, and
        # rather than special-case it downstream we simply never acknowledge it.
        _ot = (attr.get("odds_type") or "standard").lower()
        if _ot != "standard":
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

            # Each list targets exactly ONE ET calendar date (2026-07-28, user): the
            # evening board (>= noon ET) is TOMORROW's slate, a morning scan (second
            # wave) is TODAY's. The old rolling [now-6h, now+24h] window spilled
            # tonight's in-progress matches (e.g. a 9 PM start) onto the next-day
            # board, and double-logged next-day matches across two consecutive boards.
            # Filtering strictly by the match's ET date fixes both.
            start = nm.get("start_timestamp")
            if not start:
                log.info("POD skip (no scheduled start): %r vs %r", p_name, o_name)
                return None
            _now_et = datetime.now(_ET)
            _evening = _now_et.hour >= 12
            _target_date = (_now_et + timedelta(days=1)).date() if _evening else _now_et.date()
            _start_et = datetime.fromtimestamp(start, timezone.utc).astimezone(_ET)
            _start_et_date = _start_et.date()

            # ── THE ASIA SWING DOES NOT FIT IN AN ET CALENDAR DAY ────────────
            # A tournament day in Singapore, Tokyo or Beijing OPENS late on one
            # ET date and finishes on the next. Singapore WTA, 2026-09-20:
            #
            #     Kasatkina  v Sasnovich   23:00 ET  Sun 09-20
            #     Mladenovic v Hibino      00:30 ET  Mon 09-21
            #     Gibson     v Ferro       02:00 ET  Mon 09-21
            #     Okamura    v Fernandez   06:30 ET  Mon 09-21
            #     Hunter     v Garland     08:00 ET  Mon 09-21
            #
            # One card, one lineup, two ET dates. Sunday's evening board targets
            # Monday and so collected every match from 00:30 on while silently
            # dropping the 23:00 opener — a play that is on the board the reader
            # is looking at, released the same day, and part of the same lineup.
            # Strict date equality cannot express that.
            #
            # A CARD IS DATED IN ITS OWN TIME ZONE. The venue clock comes from
            # the tournament name (venue_tz), so the board follows the tour
            # wherever it goes without anything being retuned per swing:
            #
            #   Singapore  board 20:00 ET Sun = 08:00 Mon local, card = Mon
            #              -> Kasatkina 23:00 ET Sun IS 11:00 Mon local. Kept.
            #   Melbourne  board 20:00 ET Sun = 12:00 Mon local; Monday's card
            #              opened at 11:00 and is under way, so card = Tue.
            #   New York   board 20:00 ET Sun = 20:00 Sun local, card = Mon, so
            #              tonight's 20:00 night session is NOT tomorrow's card.
            #
            # CARD_START_LOCAL_HOUR is the one judgement left, and it is a fact
            # about tennis rather than about a region: a card opens late morning
            # local, so before it the next card is today's and after it the next
            # card is tomorrow's.
            # Read straight off `nm`: the `tournament` local below is assigned
            # after this block, and binding it here would shadow that.
            _tz = (venue_tz.zone_for(nm.get("tournament") or "")
                   if venue_tz else None)
            if _tz is not None:
                _local_now = _now_et.astimezone(_tz)
                _started = _local_now.hour >= CARD_START_LOCAL_HOUR
                _next_card = (_local_now.date() + timedelta(days=1) if _started
                              else _local_now.date())
                _this_card = (_local_now.date() if _started
                              else _local_now.date() - timedelta(days=1))
                _card_date = _start_et.astimezone(_tz).date()
                _basis = str(_tz)
            else:
                # No venue clock (Davis Cup ties carry no city). Fall back to the
                # ET cutoff, which is what shipped before this and is no worse
                # than the behaviour it replaced. venue_tz logs the miss.
                _next_card = _target_date
                _this_card = _now_et.date()
                _card_date = (_start_et_date + timedelta(days=1)
                              if _start_et.hour >= ASIA_SLATE_CUTOFF_HOUR
                              else _start_et_date)
                _basis = "ET fallback"

            # ── WHICH CARD THIS SCAN IS FOR ──────────────────────────────────
            # The 8pm board is about the NEXT card — the one that has not begun.
            #
            # The additional scan is not. It runs at midnight ET, by which point
            # an Asia card is halfway through its own afternoon, and its job is
            # to catch what is STILL PLAYABLE — matches on the card already in
            # flight as well as the one after it. Restricting it to the next
            # card would have made it skip the rest of tonight's lineup, which
            # is exactly the board the reader is looking at. In a US or European
            # week this_card is already finished and the union collapses back to
            # the next card on its own, so nothing needs to know which swing the
            # tour is on.
            _targets = ({_next_card} if _evening else {_this_card, _next_card})

            # A match already under way is never a candidate for any board — it
            # cannot be bet — so the start must also still be ahead of us.
            if _card_date not in _targets or _start_et <= _now_et:
                log.info("POD skip (plays %s %02d:%02d ET -> %s card [%s], "
                         "targets %s): %r vs %r", _start_et_date, _start_et.hour,
                         _start_et.minute, _card_date, _basis,
                         sorted(str(x) for x in _targets), p_name, o_name)
                return None
            if _card_date != _start_et_date:
                log.info("POD keep (%02d:%02d ET on %s is the %s card at %s): "
                         "%r vs %r", _start_et.hour, _start_et.minute,
                         _start_et_date, _card_date, _basis, p_name, o_name)

            surface = nm.get("surface") or _season_surface()
            tournament = nm.get("tournament") or None

            # Pass the real tournament so the backend uses that court's ST Pace
            # Index (e.g. Bad Homburg = 36, not the generic grass 34). Flag
            # qualifying so a Grand Slam quallie stays best-of-3, not best-of-5.
            is_qualifying = bool(tournament) and "qualif" in tournament.lower()

            # ── QUALIFYING DRAWS ARE OFF THE BOARD (operator, 2026-09-24) ────
            # 30-43, 41.1% over 73 graded picks, against 55.0% on main draws
            # (z = -2.26, p = 0.024). Projection error is 62% higher there —
            # mean |actual − projection| of 2.405σ vs 1.488σ — which is the
            # mechanism: qualifying fields are thin-data players whose recent
            # form is mostly matches we have no statistics for.
            #
            # AND A HIGHER BAR MAKES IT WORSE, which is what rules out simply
            # gating it harder:
            #     conf >= 65  30-43  41%      conf >= 75  12-24  33%
            #     conf >= 70  22-33  40%      conf >= 80   7-15  32%
            # Confidence is inverted on this population, the same signature
            # that retired Aces and Total Games from the 3x. There is no
            # threshold that rescues it, so it comes off the board entirely.
            #
            # Costs ~10% of board volume (8.6 -> 7.7 graded picks per slate)
            # and lifts the record 53.6% -> 55.0%. No Pick of the Day has ever
            # come from a qualifying draw (0-0), so the star is untouched.
            #
            # Skipped HERE, before the projection call, so it also saves the
            # API round-trip rather than pricing a play we will discard.
            if is_qualifying and SKIP_QUALIFYING:
                log.info("POD: skipping %s %s — qualifying draw (%s)",
                         p_name, prop.get("prop_type"), tournament)
                return None

            payload = {
                "player_id": p_id, "opponent_id": o_id,
                "player_name": p_name, "opponent_name": o_name,
                "tour": tour, "surface": surface,
                "court": tournament or "", "qualifying": is_qualifying,
                "prop_type": prop["prop_type"], "prop_line": prop["line"],
            }
            # INDOOR IS ADDITIVE ONLY — sent when Sofascore says TRUE, omitted
            # otherwise so the backend falls back to INDOOR_TOURNAMENTS.
            #
            # The first version sent the boolean whichever way it came out, and
            # the 2026-09-21 17:00 board caught the problem immediately: the
            # Singapore WTA event reported groundType OUTDOOR, so an explicit
            # False overrode the hand-verified list entry and the ⭐ (Vekić,
            # Break Points Won) priced as an outdoor court again — the exact bug
            # the derivation was written to fix, reintroduced by the fix.
            #
            # The two sources are not symmetric. A True from Sofascore is new
            # information about a venue nobody has listed yet. A False is not
            # evidence of anything: the event-level field is frequently a
            # default, and the list is short, hand-checked and was right here.
            # So True adds, False defers. A venue wrongly ON the list stays
            # wrong, which is the status quo and visible in one place.
            if nm.get("indoor") is True:
                payload["indoor"] = True
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
            # ── THE CONFIDENCE/EDGE SPLIT, IN SHADOW (2026-09-24) ────────────
            # Carried so _pick_to_record can persist them. `confidence` above is
            # still the only number any gate, rank or display reads.
            #   edge_sigma            |projection − line| / σ — how far we are
            #                         from the book in units of the player's own
            #                         spread. The "are we beating the book"
            #                         number, and the one that held on holdout.
            #   confidence_data_only  evidence with no line-derived ceiling —
            #                         confidence as it is meant to read.
            "edge_sigma": data.get("edge_sigma"),
            "confidence_data_only": data.get("confidence_data_only"),
            "p1_win_prob": data.get("p1_win_prob"), "p2_win_prob": data.get("p2_win_prob"),
            # Strength-of-field: both players' current ATP/WTA ranks and the
            # both-challenger-level flag — deprioritizes challenger-vs-challenger
            # matchups below tour-level plays in _rank_key.
            "player_rank": data.get("player_rank"),
            "opponent_rank": data.get("opponent_rank"),
            "both_challenger_level": data.get("both_challenger_level"),
            # PTGW scenario-mixture surface (None for other props) — used by the
            # shadow log, the correlation guard, and the implied-claim display.
            "ptgw_p_over": data.get("ptgw_p_over"),
            "ptgw_p_win_match": data.get("ptgw_p_win_match"),
            "ptgw_implied_claim": data.get("ptgw_implied_claim"),
            "ptgw_knife_edge": data.get("ptgw_knife_edge"),
            "fs_p_over": data.get("fs_p_over"),
            "fs_implied_claim": data.get("fs_implied_claim"),
            "fs_knife_edge": data.get("fs_knife_edge"),
            # BP A2 scenario-mixture display (parity with PTGW/FS): P(over), the
            # market-anchor status/blend, the implied match claim, and the scenario
            # probabilities — so a posted BP pick shows its A2 context.
            "bp_p_over": data.get("bp_p_over"),
            "bp_anchored": data.get("bp_anchored"),
            "bp_implied_claim": data.get("bp_implied_claim"),
            "bp_blended_wp": data.get("bp_blended_wp"),
            "bp_scenario_probs": data.get("bp_scenario_probs"),
            # Total Games sportsbook anchor (None for other props). The projection
            # above is already the book-blended number; these carry the book line,
            # the edge vs the PrizePicks line, and the divergence flag for display
            # + shadow logging.
            "tg_book_line": data.get("tg_book_line"),
            "tg_model_proj": data.get("tg_model_proj"),
            "tg_book_edge": data.get("tg_book_edge"),
            "tg_anchored": data.get("tg_anchored"),
            "tg_divergent": data.get("tg_divergent"),
            # A1 interim: BP outcome-inversion guard (block from the board).
            "bp_suspended": data.get("bp_suspended"),
            "bp_suspend_reason": data.get("bp_suspend_reason"),
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
    """Re-fetch the PrizePicks board and return
    {(norm_player, prop_type): {"line": float, "opponent": norm_opponent}}
    for the standard lines only. Used by the line-movement monitor. Empty on
    failure — never raises.

    THE OPPONENT IS PART OF THE ANSWER, NOT DECORATION. This used to return the
    bare line, so a line was identified by player and prop alone. A player
    appears on the board again the moment their NEXT match is posted, under
    exactly the same key, and the monitor read that as movement on the pick it
    was already watching. Swiatek's match had already been played when the
    monitor compared her Fantasy Score line for TOMORROW's match against
    today's projection and reported it as holding. Carrying the opponent lets
    the monitor tell "the line moved" apart from "this is a different match".
    """
    try:
        board = _fetch_board()
        out = {}
        for pr in _parse_board(board):
            out[(_norm(pr["player"]), pr["prop_type"])] = {
                "line": pr["line"],
                "opponent": _norm(pr.get("opponent") or ""),
            }
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


def _rel_edge(pk: dict) -> float:
    """|projection - line| / line — edge as a FRACTION of the number bet on.

    Raw edge is not comparable across props. A 2.0 edge on a 4.5 break-points
    line is enormous; the same 2.0 on a 24.5 fantasy line is noise. Ranking on
    raw edge therefore sorts by prop SCALE, not by value: measured on the graded
    record it pushed Fantasy Score to 101 of 237 board slots and cut Break
    Points from 91 to 80.
    """
    line = pk.get("line")
    edge = pk.get("edge_mag") or abs(pk.get("edge") or 0)
    if not isinstance(line, (int, float)) or not line:
        return 0.0
    return edge / abs(line)



# ── CONFIDENCE RECALIBRATION ─────────────────────────────────────────────────
# The stated confidence was not a probability. Measured on 640 graded picks it
# overstated its own hit rate at EVERY band, by 12 to 31 points, and it did not
# even order outcomes — the 70-74 band (59.2%) beat the 80+ band (55.1%), and
# 75-79 came in at 46.0%. An "82% confidence" play that hits 55% is a number
# making a promise it cannot keep, and it is shown to subscribers.
#
# What DOES order outcomes is relative edge, which is now the ranking key. So
# the displayed number is derived from the same quantity the board sorts on,
# fitted to what actually happened. On the props we post (BP / FS / PTGW),
# 324 graded picks, a logistic on a play's rel-edge PERCENTILE within its slate:
#
#     P(win) = 1 / (1 + exp(-(0.0425 + 0.6977 * pct)))
#
#     pct 0.00 -> 51.1%      observed bottom third   51.9%
#     pct 0.50 -> 59.7%      observed middle third   61.1%
#     pct 1.00 -> 67.7%      observed top third      65.7%
#
# Percentile rather than raw rel_edge on purpose: rel_edge is heavy-tailed, and
# a logistic fitted on it directly spans only 57-61% — too flat to say anything.
# The percentile version is monotone and tracks the observed thirds closely.
#
# THE RANGE IS NARROW BECAUSE THE TRUTH IS NARROW. 51-68% is the honest spread
# of a board whose base rate is 59.6%; the old 65-95% display was inventing
# precision the record does not support. A smaller number that is true is worth
# more than a large one that is not, and it is the number a subscriber will
# check against the track record.
#
# GATING IS UNAFFECTED. BOARD_MIN_CONF still reads the RAW confidence, so this
# cannot quietly empty the board — a 51-68 scale against a 65 floor would reject
# almost everything. Raw decides eligibility; calibrated is what gets shown.
CALIB_B0 = 0.0425
CALIB_B1 = 0.6977
CALIB_MIN, CALIB_MAX = 50.0, 70.0

# ── THE PERCENTILE IS MEASURED AGAINST A FIXED DISTRIBUTION ──────────────────
# It used to be the percentile WITHIN THIS POOL, and the website used the
# percentile within its own visible board — the same formula over two different
# populations. So one play carried two different "confidence" numbers depending
# on whether you read it in Discord or on the site, and the site's moved whenever
# a filter changed what was visible. Reported by the operator on the Avanesyan
# O4.5 Break Points Won card: 65 on the website, a different number in the bot.
#
# A percentile only needs SOME distribution to measure against; it does not have
# to be the caller's own pool. This is the relative-edge distribution of the
# graded record (707 picks, every 5th percentile), so the number is a property of
# the PLAY — anything holding a projection and a line computes the same value.
#
# MUST STAY IN SYNC with data.js REL_EDGE_Q — that is the whole point.
REL_EDGE_Q = [
    0.0, 0.073171, 0.102326, 0.120837, 0.135273, 0.145644, 0.158852, 0.171429,
    0.186295, 0.2, 0.222222, 0.24, 0.260279, 0.288106, 0.315429, 0.368421,
    0.424242, 0.457582, 0.529412, 0.691282, 2.8,
]


def rel_edge_pct(rel: float) -> float:
    """Where `rel` sits in the graded record, 0-1, linearly interpolated."""
    try:
        r = float(rel)
    except (TypeError, ValueError):
        return 0.0
    q, last = REL_EDGE_Q, len(REL_EDGE_Q) - 1
    if r <= q[0]:
        return 0.0
    if r >= q[last]:
        return 1.0
    for i in range(last):
        if q[i] <= r <= q[i + 1]:
            span = q[i + 1] - q[i]
            return (i + ((r - q[i]) / span if span else 0.0)) / last
    return 1.0


def calibrated_confidence(pct: float) -> float:
    """Honest hit-rate estimate for a play at rel-edge percentile `pct` (0-1)."""
    import math
    try:
        q = min(1.0, max(0.0, float(pct)))
        p = 1.0 / (1.0 + math.exp(-(CALIB_B0 + CALIB_B1 * q))) * 100.0
        return round(min(CALIB_MAX, max(CALIB_MIN, p)), 1)
    except Exception:  # noqa: BLE001
        return 59.6      # the measured base rate — never a guess dressed as one


def confidence_for(projection, line) -> float:
    """The ONE displayed-confidence function, matching data.js confidenceFrom."""
    try:
        ln, proj = float(line), float(projection)
    except (TypeError, ValueError):
        return None
    if not ln:
        return None
    return calibrated_confidence(rel_edge_pct(abs(proj - ln) / abs(ln)))


def attach_calibrated_confidence(picks: list) -> None:
    """Set `confidence_calibrated` on every pick from its OWN relative edge.
    Mutates in place; never raises."""
    try:
        for p in (picks or []):
            if not isinstance(p, dict):
                continue
            c = confidence_for(p.get("projection"), p.get("line"))
            p["confidence_calibrated"] = c if c is not None else 59.6
    except Exception:  # noqa: BLE001 — a display number must never cost the board
        log.exception("calibrated confidence failed")


def _rank_key(pk: dict) -> tuple:
    """Ranking key (sort DESCENDING). Three levels, strength-of-field first:

      0. tour_level  — tour matches (1) ALWAYS rank above challenger-vs-
                       challenger matchups (0). Deprioritize-only.
      1. confidence  — the PRIMARY term
      2. rel_edge    — |projection - line| / line, tiebreaker among plays of
                       equal confidence

    REVERTED TO CONFIDENCE-FIRST 2026-09-20 (operator), after an edge-first
    board went out reading as a list of low-confidence plays. The reasoning,
    and it is the right reasoning: a large edge computed from thin evidence is
    not a large edge. Confidence is the model's own statement about how much
    data stands behind the projection, so ordering purely on edge surfaces
    precisely the plays where the projection is least supported — the edge is
    biggest exactly where the number is least trustworthy.

    THE EVIDENCE FOR EDGE-FIRST IS KEPT BELOW, because it is real and it was
    measured, and whoever revisits this should see both sides rather than
    rediscover it. Note its own caveat: it is IN-SAMPLE. The rule was chosen by
    testing on the picks it was then scored on, and a board ranked that way
    looked wrong in production on the first slate it shipped.

    Measured on 640 graded picks, confidence does not order outcomes — it is
    NON-MONOTONIC:

        conf 65-69   86-72   54.4%      conf 75-79   63-74   46.0%
        conf 70-74   87-60   59.2%      conf 80+    109-89   55.1%

    The 70-74 band beats the 80+ band. Every band also overstates its own hit
    rate by 12-31 points, so the number was never a probability. Meanwhile
    relative edge DOES order them, and the difference compounds through a
    board that only has eight slots: on the props we post (BP / FS / PTGW),
    37 slates, 269 picks —

        rank by confidence   top3 57.7%   top5 59.2%   top8 59.1%
        rank by rel_edge     top3 64.0%   top5 64.2%   top8 61.2%
        POTD (#1 only)       62.2%  ->  73.0%

    RELATIVE rather than raw, deliberately. Raw edge scores better on the single
    POTD (83.8%) but gets there by picking Fantasy Score 25 times in 37 — the
    prop with the biggest line values. That is scale, not skill, and it is
    fragile. Relative edge surfaces the MOST Break Points (93 vs 91) and lifts
    their hit rate from 53% to 57%, which is the value that was being cut by a
    confidence sort in the first place.

    Confidence has not been discarded: BOARD_MIN_CONF still decides what is
    ELIGIBLE. It answers "is this trustworthy enough to post"; relative edge
    answers "how good is it". Those are different questions and were being
    answered by the same number.

    CAVEAT, stated because it should shape expectations: the comparison above is
    in-sample — the rule was chosen by testing on the same picks it is scored
    on. The direction holds at every board size and on two different prop
    subsets, and it matches an independent finding (confidence bands are
    non-monotonic, edge quartiles are not), but the live number should be
    expected to land below the backtest.
    """
    return (0 if pk.get("both_challenger_level") else 1,
            pk.get("confidence") or 0,
            _rel_edge(pk))


# ── Prop-reliability tiers for the per-player dedupe (7/23 audit, Fix C1) ─────
# When a player has several qualifying props, which one REPRESENTS them on the
# board should weight how reliably the model handles that PROP TYPE, not raw
# confidence alone — otherwise a fragile prop's noisy 70 displaces a robust prop's
# 69. Lower tier number = more reliable = preferred. SELECTION ONLY: this changes
# which prop is kept per player, never the displayed confidence, and never the
# global board ORDER (that stays _rank_key / confidence).
#   Tier 1  Break Points Won, Aces        (discrete, directly-counted)
#   Tier 2  Total Games, Player Total Games Won, Fantasy Score  (scenario/derived)
#   Tier 3  Double Faults                 (rare event, highest relative variance)
# PROVISIONAL (A3, 2026-07-23): Break Points Won was just rebuilt (A2) and A3
# surfaced a base_proj underestimation vs weak/challenger opponents — its Tier-1
# rank is NOT yet certified. If the board shows BP displacing stronger props,
# demote it to 2 here (one-line change); the mechanism is independent of the map.
DEDUPE_TIER_OVERRIDE_MARGIN = 8   # a lower-tier prop must LEAD by >= this to win
_PROP_TIER = {
    "Break Points Won": 1, "Aces": 1,
    "Total Games": 2, "Player Total Games Won": 2, "Fantasy Score": 2,
    # Derived from hold rate x save rate rather than counted directly, and
    # ungraded — tier 2 until it earns better.
    "Break Points Saved": 2,
    "Double Faults": 3,
}


def _prop_tier(ptype: str) -> int:
    return _PROP_TIER.get(ptype, 2)          # unknown/demon -> middle tier


def _dedupe_preferred(cand: dict, cur: dict) -> bool:
    """True if cand should REPLACE cur as a player's single board entry.

    Higher reliability tier (lower number) wins UNLESS the lower-tier pick leads by
    >= DEDUPE_TIER_OVERRIDE_MARGIN in displayed confidence. Within one tier: higher
    _rank_key (confidence, then edge). Symmetric in the arguments; confidences are
    never modified — this is selection only."""
    tc, tu = _prop_tier(cand.get("prop_type")), _prop_tier(cur.get("prop_type"))
    if tc != tu:
        hi, lo = (cand, cur) if tc < tu else (cur, cand)     # hi = better (lower) tier
        lead = (lo.get("confidence") or 0) - (hi.get("confidence") or 0)
        winner = lo if lead >= DEDUPE_TIER_OVERRIDE_MARGIN else hi
        return winner is cand
    return _rank_key(cand) > _rank_key(cur)                  # same tier: confidence-first


def _passes_quality(pk: dict) -> bool:
    """v2 board quality gate — a single uniform floor for EVERY prop type. The
    PTGW bespoke bar logic is retired; PTGW's depth ceiling and structural guards
    still shape its confidence upstream (in confidence.py / main.py), and here it
    clears the same 65 floor as everything else. Demons use _demon_qualifies."""
    if pk.get("odds_type") == "demon":
        return _demon_qualifies(pk)
    # BOARD_MIN_EDGE_SIGMA is 0 (off) by default, so this is a no-op unless the
    # operator sets it — the record does not support a board-wide edge floor.
    # See the bucket table at SLIP_MIN_EDGE_SIGMA: sub-0.3σ picks went 45-33.
    if not _edge_sigma_ok(pk, BOARD_MIN_EDGE_SIGMA):
        return False
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
async def _rank_board(props: list = None):
    """Evaluate the whole board ONCE and return the qualifying candidates,
    deduped to each player's single best play, sorted best-first.

    Returns None when the board has no eligible props (so callers can tell
    "nothing on the board" apart from "nothing qualified" = []). Raises only on
    unexpected errors — callers wrap. This is the shared evaluation pass behind
    both the Pick of the Day and the 3x slip, so the heavy serialized backend
    calc runs a single time per trigger."""
    # `props` lets a DIFFERENT book feed the same machine (2026-08-03). Underdog
    # passes its own parsed board here so it inherits EVERY gate — per-prop
    # confidence bars, the thin-slate drop, per-prop board caps, the tier-aware
    # per-player dedupe, the ranking rule and star eligibility — rather than
    # reimplementing them and letting the two boards drift apart. None = the
    # normal PrizePicks path, unchanged.
    if props is None:
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
        # A1 interim (7/23 audit): the projector suspends Break Points Won picks in
        # the outcome-inversion zone (lopsided win prob / >=4 breaks at <35% win).
        # Block them from the board entirely — the projection is real but the lean
        # can invert until the A2 scenario rebuild lands.
        if r.get("bp_suspended"):
            log.info("POD_BP_SUSPENDED | %-22s conf=%-3.0f proj=%-6s line=%-5s — %s",
                     (r.get("player") or "")[:22], r.get("confidence") or 0,
                     r.get("projection"), r.get("line"),
                     r.get("bp_suspend_reason") or "outcome-inversion guard")
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
    # Per-player selection: normally ONE best play per player, tier-aware (Fix C1) —
    # the more reliably-modelled prop TYPE represents the player unless a lower-tier
    # prop leads by >= DEDUPE_TIER_OVERRIDE_MARGIN. EXCEPTION (2026-07-29, user):
    # when a player has 2+ props each at >= POTD_THRESHOLD (80) confidence, show ALL
    # of those high-confidence props — a genuinely strong second play on the same
    # player is worth surfacing, not hiding. Below that, still just the single best.
    # SELECTION ONLY — displayed confidence is untouched; global ORDER stays _rank_key.
    _props_by_player: dict = {}
    for pk in picks:
        _props_by_player.setdefault(_norm(pk["player"]), []).append(pk)
    _selected: list = []
    for _plist in _props_by_player.values():
        _high = [p for p in _plist if (p.get("confidence") or 0) >= POTD_THRESHOLD]
        if len(_high) >= 2:
            _selected.extend(_high)      # multiple 80%+ props on one player -> keep all
            log.info("POD_DEDUPE_MULTI | %-22s kept %d plays (all >=%d): %s",
                     (_plist[0].get("player") or "")[:22], len(_high), POTD_THRESHOLD,
                     ", ".join("%s(%.0f)" % (p.get("prop_type"), p.get("confidence") or 0) for p in _high))
            continue
        best = _plist[0]                 # picks is rank-sorted, so [0] is the top play
        for cand in _plist[1:]:
            if _dedupe_preferred(cand, best):
                best = cand
        if len(_plist) > 1:
            log.info("POD_DEDUPE_TIER | %-22s kept %s(t%d,%.0f) as single best of %d props",
                     (best.get("player") or "")[:22], best.get("prop_type"),
                     _prop_tier(best.get("prop_type")), best.get("confidence") or 0, len(_plist))
        _selected.append(best)
    ordered = sorted(_selected, key=_rank_key, reverse=True)
    # Honest hit-rate estimate for display — see attach_calibrated_confidence.
    attach_calibrated_confidence(ordered)

    # ── Per-MATCH dedupe (2026-08-05, user) ──────────────────────────────────
    # The pass above is per-PLAYER, so it cannot see that two survivors are the
    # two sides of ONE match — Bouzkova OVER 4.5 BPS (84) and Townsend OVER 4.5
    # BPS (76) both reached the 8/5 board off the same fixture. That is one match
    # represented twice, and for the mirrored props (BP saved/won, total games)
    # the two sides are close to the same bet stated from opposite ends.
    #
    # ONE play per match, keeping the highest-ranked. `ordered` is already
    # rank-sorted, so the first sighting of a match key is the best of it.
    # The key is the UNORDERED pair, so it matches whichever side is listed
    # first. Any pick missing an opponent falls back to its own name and is
    # therefore never merged with another player.
    _seen_match: dict = {}
    _match_kept: list = []
    for pk in ordered:
        _p, _o = _norm(pk.get("player") or ""), _norm(pk.get("opponent") or "")
        _key = tuple(sorted((_p, _o))) if _o else (_p,)
        _prev = _seen_match.get(_key)
        if _prev is None:
            _seen_match[_key] = pk
            _match_kept.append(pk)
            continue
        log.info("POD_DEDUPE_MATCH | %s vs %s — dropped %s %s(%.0f), kept %s %s(%.0f)",
                 (pk.get("player") or "")[:20], (pk.get("opponent") or "")[:20],
                 pk.get("player"), pk.get("prop_type"), pk.get("confidence") or 0,
                 _prev.get("player"), _prev.get("prop_type"), _prev.get("confidence") or 0)
    if len(_match_kept) != len(ordered):
        log.info("POD_DEDUPE_MATCH | %d plays -> %d after one-per-match",
                 len(ordered), len(_match_kept))
    ordered = _match_kept

    # ── Part 3 slate-correlation guard (only meaningful once PTGW_ENABLED) ────
    # Flag when the
    # surviving PTGW picks all imply the same match direction (e.g. all "favourite
    # wins in straights") — a correlated cluster that is really one bet repeated.
    if PTGW_ENABLED:
        _ptgw = [pk for pk in ordered if pk.get("prop_type") == "Player Total Games Won"]
        if len(_ptgw) >= 2:
            _dirs = {_lean_dir(pk) for pk in _ptgw}
            if len(_dirs) == 1:
                for pk in _ptgw:
                    pk["ptgw_correlated"] = True
                log.info("POD_PTGW_CORR | %d PTGW picks ALL %s — correlated cluster flagged",
                         len(_ptgw), next(iter(_dirs)))

    # ── UNIFORM PER-PROP CAP ─────────────────────────────────────────────────
    # At most BOARD_MAX_PER_PROP of any one prop type, Break Points Won exempt.
    # `ordered` is already ranked, so walking it in order keeps the best of each
    # and drops the tail. One pass replaces the four per-prop blocks that used to
    # live here (PTGW 3 / Total Games 4 / Fantasy Score 4 / Double Faults 2).
    _counts, _drop = {}, set()
    for pk in ordered:
        _pt = pk.get("prop_type")
        if _pt in BOARD_PROP_CAP_EXEMPT:
            continue
        _counts[_pt] = _counts.get(_pt, 0) + 1
        if _counts[_pt] > BOARD_MAX_PER_PROP:
            _drop.add(id(pk))
    if _drop:
        _over = {k: v for k, v in _counts.items() if v > BOARD_MAX_PER_PROP}
        log.info("POD_PROP_CAP | cap %d/prop (exempt: %s) — dropping %d play(s); over cap: %s",
                 BOARD_MAX_PER_PROP, ", ".join(sorted(BOARD_PROP_CAP_EXEMPT)),
                 len(_drop), ", ".join(f"{k} x{v}" for k, v in sorted(_over.items())))
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

# PER-PROP 3x FLOORS (operator, 2026-09-24). A prop listed here uses its own
# confidence bar instead of the blanket SLIP_MIN_CONF; anything not listed keeps
# the old behaviour (Tier 1/2 at SLIP_MIN_CONF, Tier 3 barred outright).
#
# Double Faults was previously banned from slips entirely as Tier 3 (7/23 audit,
# highest variance). It is now allowed at a raised bar rather than blocked.
# Total Games was already eligible at the blanket 70 and is now held to 80.
#
# WHAT THE RECORD SAYS ABOUT THESE TWO, so the next person to touch this does not
# have to re-derive it (722 graded in-record picks, pulled 2026-09-24):
#
#   Double Faults   65-69  4-2  67%   70-74 16-11 59%   75-79 11-17 39%   80+  8-3  73%
#   Total Games     65-69 23-23 50%   70-74  1-4  20%   75-79 14-13 52%   80+ 24-22 52%
#
# A two-leg 3x needs ~57.7% PER LEG to break even at 3x (3 * p^2 = 1), which is
# the number these bars should be judged against — not 50%. On that test the
# 75-79 band is a dead zone for BOTH (39% and 52%), and it is a dead zone for
# every other prop too (BPW 55%, FS 43%, Aces 42%, PTGW 47%).
#
# DF was asked for at 75 and moved to 80 by the operator once the bands were on
# the table: 75+ is 19-20 (48.7%) because it swallows that 11-17 band, while 80+
# is the only DF slice clearing breakeven. n=11 there, so it is a small-sample
# bet and worth re-checking after ~20 more graded DF picks.
#
# Total Games stays at 80 as asked. Being straight about it: 52% is still under
# the 57.7% breakeven, and no TG band has ever cleared it, so this bar limits
# the damage rather than making TG a good leg. Player Total Games Won is a
# SEPARATE prop and deliberately not listed — it keeps the blanket 70.
# Total Games briefly sat here at 80 (2026-09-24) before the band data showed it
# does not clear breakeven at any bar; it moved to SLIP_PROP_EXCLUDED below.
SLIP_PROP_MIN_CONF = {
    "Double Faults": 80,
}

# NEVER a 3x leg, at any confidence (operator, 2026-09-24).
#
# A two-leg 3x needs ~57.7% PER LEG to break even at 3x (3 * p^2 = 1). These two
# do not reach it at ANY confidence, and unlike the rest they get WORSE when a
# quality filter is applied — so there is no bar that rescues them. They are not
# mis-ranked, they are bad, and the 3x is the one post where a weak leg takes a
# good leg down with it. Both still populate the board and can hold the star.
#
#   Aces         all 51-69 42.5% | conf>=70 48.4% | conf>=80 37.0% | +edge gate 46%
#   Total Games  all 62-62 50.0% | conf>=70 50.0% | conf>=80 52.2% | +edge gate 44%
#
# Aces is the single biggest drag in the whole record and is bad in every month
# at every confidence level. Total Games is intrinsically near-coin-flip — see
# PROP_CONFIDENCE_CEILING in backend/src/calculations/confidence.py, where the
# games_per_set fit found combined hold explains only R^2 0.09-0.16 of the
# variance. That is also why books price it -120/-120 both ways.
SLIP_PROP_EXCLUDED = {"Aces", "Total Games"}

# ── EDGE FLOOR FOR 3x LEGS, IN σ (operator, 2026-09-24) ──────────────────────
# "I don't want book-aligned plays." This is that rule — but applied ONLY to the
# 3x, and measured in σ rather than in percent of the line.
#
# WHY σ AND NOT PERCENT: main._edge_cap gates on |proj − line| / line, which is
# the wrong ruler. A 34% edge is ~1.5 double faults on a 4.5 line (inside the
# noise) and ~4.3 games on a 12.5 line (a real gap). edge_sigma divides by the
# player's own spread, so the two are comparable.
#
# WHY ONLY THE 3x, AND NOT THE BOARD. The record does not support a board-wide
# floor. Per bucket, over 642 graded in-record picks with a recoverable σ:
#
#   0.00-0.20  n= 24  14-10  58.3%     0.60-0.80  n=106  62-44  58.5%
#   0.20-0.30  n= 54  31-23  57.4%     0.80-1.00  n= 76  40-36  52.6%
#   0.30-0.40  n= 75  26-49  34.7%     1.00-1.50  n= 71  42-29  59.2%
#   0.40-0.50  n= 73  38-35  52.1%     1.50+      n= 89  55-34  61.8%
#   0.50-0.60  n= 74  36-38  48.6%
#
# It is NOT monotone. The MOST book-aligned picks (< 0.3σ) went 45-33, 57.7% —
# better than the 53.6% board average. A floor would cut a winning segment. The
# cumulative version of this table looks clean only because a floor straddles
# the 34.7% hole at 0.30-0.40, and carving out one band from nine retrospective
# buckets is curve-fitting. So BOARD_MIN_EDGE_SIGMA defaults OFF.
#
# The 3x is different: it needs ~57.7% PER LEG to break even at 3x, and the
# current slip-eligible pool sits at 55.8% (n=362) — under water. Applying this
# floor lifts it to 58.0% (n=264) at a 27% volume cost. That is a gate paying
# for itself, not a band carved to fit.
#
# HONEST CAVEAT, on the record: applied to the 2026-09-23 board it would have
# cut Majchrzak (0.235σ), the ONLY winner on a 1-5-1 night. A gate on edge
# cannot rescue a night when the projections are wrong — it concentrates the
# damage, because high edge means high conviction in a wrong number.
#
# SCOPE CORRECTED SAME DAY: this floor applies ONLY to the projection-lean
# props. It was briefly applied to everything, which was wrong — see
# MIXTURE_PROPS below. After the Aces/Total Games exclusions above, the only
# slip-eligible prop it still governs is Double Faults, which is the intended
# footprint: DF is the one remaining slip-eligible prop whose side comes from
# the projection rather than from a mixture probability.
SLIP_MIN_EDGE_SIGMA = float(os.getenv("SLIP_MIN_EDGE_SIGMA", "0.6") or 0.6)

# Board-wide edge floor. DEFAULT 0.0 = OFF, deliberately — see the bucket table
# above. Set the env var to enable it if the operator decides to override the
# record; nothing else needs to change.
BOARD_MIN_EDGE_SIGMA = float(os.getenv("BOARD_MIN_EDGE_SIGMA", "0") or 0)


# Props whose SIDE comes from a scenario-mixture P(over), NOT from
# sign(projection − line). Mirrors `_prob_base` in main.py (which is what skips
# _edge_cap for them) and the EVR skip list in confidence.py.
#
# THE EDGE FLOOR MUST NOT APPLY TO THESE, and the reason is measurable rather
# than theoretical. For these props confidence ALREADY IS the probability edge:
# across 76 BPW picks carrying a stored mixture probability, confidence tracks
# 100 x P(chosen side) with a mean offset of +6.0. Confidence 72 means P(side)
# ~ 0.66 — a 16-point edge over a coin-flip line.
#
# σ mismeasures exactly that. A Break Points Won projection of 2.0 against a 2.5
# line is 0.30σ — which reads as "basically the book's number" — while the
# probability mass sitting below 2.5 is large, because the stat is a low, skewed
# integer count and the line is a half-integer. The σ ruler assumes a symmetric
# spread around the mean; these distributions are bimodal by construction, which
# is the same reason they were pulled out of EVR grading in the first place.
#
# So the two families get two rulers, and the confidence floor is the right gate
# here: SLIP_MIN_CONF 70 already means P(side) ~ 0.64, which IS a beat-the-book
# bar. The σ floor governs the projection-lean props, where confidence is the
# blended min(evidence, f(relative edge)) number and cannot be trusted as edge.
MIXTURE_PROPS = {"Break Points Won", "Fantasy Score", "Player Total Games Won"}


def _edge_sigma_ok(pk: dict, floor: float) -> bool:
    """True if this pick clears an edge floor measured in σ.

    SKIPS the mixture props entirely — see MIXTURE_PROPS above; σ is the wrong
    instrument for them and their confidence floor is already an edge gate.

    FAILS OPEN otherwise. A pick with no recoverable σ (too few matches for a
    variance read) returns True: an unmeasurable edge is not a small one, and
    dropping those would silently bias the board toward players with long
    histories — the opposite of finding value the book has not priced.
    """
    if floor <= 0:
        return True
    if (pk.get("prop_type") or "") in MIXTURE_PROPS:
        return True
    es = pk.get("edge_sigma")
    if not isinstance(es, (int, float)) or es != es:
        return True
    return es >= floor


def _slip_floor(ptype: str) -> float:
    """The confidence a prop needs to be a 3x leg.

    Excluded props are unreachable at any confidence. Otherwise an explicit
    per-prop floor wins, Tier 1/2 use SLIP_MIN_CONF, and Tier 3 stays barred —
    an unlisted high-variance prop is still not something to staple to another
    leg just because it scored well once.
    """
    if ptype in SLIP_PROP_EXCLUDED:
        return float("inf")
    if ptype in SLIP_PROP_MIN_CONF:
        return SLIP_PROP_MIN_CONF[ptype]
    if _prop_tier(ptype) >= 3:
        return float("inf")                  # unreachable -> not slip-eligible
    return SLIP_MIN_CONF


def _select_slip(ordered: list, potd: list) -> list:
    """Build the 3x — two independent legs packaged as one slip (STEP 1-3).

    STEP 1: exclude anything already in the POTD, keyed by (player, prop_type),
            so the two posts never overlap.
    STEP 2: take the two highest-scoring remaining candidates by the same
            combined score used for the POTD, with correlation avoidance (the
            two legs must come from two DIFFERENT matches) and a prop-diversity
            preference (within SLIP_DIVERSITY_WINDOW confidence points, prefer
            two different prop types).
    STEP 3: only return a slip if TWO candidates clear their own 3x leg bar —
            SLIP_PROP_MIN_CONF for the props with a specific one (Double Faults
            80), otherwise SLIP_MIN_CONF = 70, one notch above the board floor so
            slips are never built from floor picks. SLIP_PROP_EXCLUDED props (Aces,
            Total Games) and any unlisted Tier 3 prop are barred at any confidence.
            Fewer than two qualifying legs → NO slip, never a weaker filler.
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
    # additionally clear their own bar — see _slip_floor: SLIP_MIN_CONF (70) for
    # most Tier 1/2 props, a per-prop floor for the ones in SLIP_PROP_MIN_CONF,
    # and unreachable for any other Tier 3 prop.
    pool = [c for c in ordered
            if (_norm(c["player"]), c["prop_type"]) not in potd_keys
            and _match_key(c) not in potd_matches
            and (c.get("confidence") or 0) >= _slip_floor(c.get("prop_type"))
            and _edge_sigma_ok(c, SLIP_MIN_EDGE_SIGMA)]
    if len(pool) < 2:
        _conf_ok = [c for c in ordered
                    if (_norm(c["player"]), c["prop_type"]) not in potd_keys
                    and _match_key(c) not in potd_matches
                    and (c.get("confidence") or 0) >= _slip_floor(c.get("prop_type"))]
        log.info("3x: fewer than 2 legs qualify after POTD exclusion "
                 "(%d clear confidence, %d also clear the %.2fσ edge floor; "
                 "base conf %d, per-prop %s, excluded %s) — no slip today",
                 len(_conf_ok), len(pool), SLIP_MIN_EDGE_SIGMA, SLIP_MIN_CONF,
                 SLIP_PROP_MIN_CONF, sorted(SLIP_PROP_EXCLUDED))
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
# player is at least a 90% favorite AND the total is market-anchored (a real book
# line exists) — the set count is then near-locked and the total is a very clear,
# market-backed win condition. Below 90%, or with no book total, it can appear in
# the list but must not lead. Enforced in _star_eligible.
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

# ── BREAK POINTS WON: the one prop that may lead below the uniform bar ───────
# 70, not 80, and NOTHING else gets this. BP confidence stopped meaning what the
# other props' confidence means when A2 made it half scenario-mixture P(side):
# P(side) rarely clears 0.85 on a real prop, while a data-quality composite sits
# in the high 80s/90s whenever the data is good. Ranked against each other on one
# 80 bar, BP could not compete — its board volume fell 69% per slate day after A2
# while every other prop rose 45-230%, and no BP play has led the card since.
#
# This is a scale correction, not a lowered standard: a BP play at 70 carries the
# same strength of claim as another prop at 80, measured on a different ruler.
# BP also owns the tightest absolute projection error of any prop we run (1.56).
# 65, NOT 70, AND IT IS THE SAME 65 AS BOARD_MIN_CONF — operator's rule, stated
# more than once: Break Points Won always clears the ⭐ gate provided its
# confidence is 65 or better. Tying the two together is the point of it. If a BP
# play is trustworthy enough to be ON the board it is trustworthy enough to LEAD
# it, so there is no band where BP qualifies for the card but is quietly barred
# from the headline. 70 created exactly that band, and it cost the 2026-09-20
# board its Pick of the Day: Kasatkina BP at 69 was one point short, so a six-
# play card went out with no ⭐ at all.
STAR_BP_MIN_CONF = float(os.getenv("STAR_BP_MIN_CONF", "65") or "65")


def _star_eligible(pk: dict) -> bool:
    """v2: a play may hold the ⭐ Pick-of-the-Day slot iff it clears the uniform
    80 threshold AND its prop is not permanently star-blocked.

    Two prop-level exclusions: Double Faults may NEVER be the Pick of the Day
    (permanent), and Fantasy Score may not until an out-of-sample calibration
    backtest certifies it (PROBATION, Fix C3) — both still populate the board and
    3x. Every other prop — Aces, Break Points Won, Total Games, Player Total Games
    Won — is star-eligible at >= 80, EXCEPT Total Games, which carries an extra gate
    (RESTORED 2026-07-29): it may lead only on a >=90% favourite AND an anchored
    total (see the TG block below). The PTGW-UNDER structural requirement stays
    retired; that story lives inside the projection/guard chain.

    (PTGW is theoretical here until PTGW_ENABLED flips: its confidence ceiling is
    80/76, so it can only ever star at exactly its ceiling — no special handling.)

    Demons are NEVER star-eligible: the boosted-payout structure is not part of the
    standard public POTD record, which stays standard-only."""
    if pk.get("odds_type") == "demon":
        return False
    if pk.get("prop_type") in POD_STAR_EXCLUDE_PROPS:
        return False
    if pk.get("prop_type") in POD_STAR_PROBATION_PROPS:   # Fix C3: FS not star-eligible until backtested
        return False
    # Total Games ⭐ gate (RESTORED 2026-07-29 after a conf-80 unanchored ITF TG led
    # the card). TG is the model's worst-fit prop (R^2 0.09-0.16, 80 conf ceiling),
    # so it may hold the ⭐ ONLY when the match is near-locked AND market-anchored:
    #   • one player a >= STAR_TOTAL_GAMES_MIN_WP (90%) favourite — the set count is
    #     near-certain, making the total a clear win condition, AND
    #   • tg_anchored — a real book total sits behind the projection (unanchored TG
    #     is pure model on a prop the model predicts poorly).
    # It still ranks on the board and in the 3x; it just can't be the headline.
    if pk.get("prop_type") == "Total Games":
        _fav_wp = max(pk.get("p1_win_prob") or 0, pk.get("p2_win_prob") or 0)
        if _fav_wp < STAR_TOTAL_GAMES_MIN_WP or not pk.get("tg_anchored"):
            return False
    # Break Points Won clears at a LOWER bar than every other prop, and is the only
    # prop that does. Its confidence is half scenario-mixture P(side) since the A2
    # rebuild, and a probability rarely clears 0.85 on a real prop, while every
    # other prop's confidence is a data-quality composite that sits in the high
    # 80s/90s on good data. Held to a common 80 the two scales are not comparable,
    # and BP simply stopped reaching the top: board volume fell 69% per slate day
    # after A2 while every other prop rose 45-230%. A BP play at 70 is not a weaker
    # play than an Aces play at 80 — it is the same claim measured differently.
    if pk.get("prop_type") == "Break Points Won":
        return (pk.get("confidence") or 0) >= STAR_BP_MIN_CONF
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
    # BREAK POINTS WON GETS FIRST REFUSAL ON THE ⭐, ahead of higher-confidence
    # plays. Confidence is not a common currency across props — BP's is half
    # P(side) while the rest are data-quality composites — so "highest number
    # leads" was silently ranking BP last on a ruler it cannot win on. Ordering
    # only: no confidence is changed, and an ineligible BP play still cannot lead.
    _bp_idx = next((i for i, p in enumerate(ordered)
                    if p.get("prop_type") == "Break Points Won" and _star_eligible(p)), None)
    if _bp_idx is not None:
        if _bp_idx != 0:
            _bp = ordered.pop(_bp_idx)
            log.info("POD_BP_PRIORITY | %s Break Points Won (conf %s) promoted over "
                     "%s %s (conf %s) — BP leads the card whenever it is ⭐-eligible",
                     _bp.get("player"), _bp.get("confidence"),
                     ordered[0].get("player"), ordered[0].get("prop_type"),
                     ordered[0].get("confidence"))
            ordered = [_bp] + ordered
        return ordered, True
    if _star_eligible(ordered[0]):
        return ordered, True
    blocked = ordered[0]
    idx = next((i for i, p in enumerate(ordered) if _star_eligible(p)), None)
    if idx is None:
        # Headline guard (2026-07-23): even with NO ⭐, Double Faults — Tier 3 and
        # permanently star-blocked — must never visually LEAD the ranked board. If it
        # would, float the best non-DF play into the top slot. Ordering only: still
        # no ⭐, no confidence change. DF is the sole target (only prop this low in
        # trust). When a ⭐ exists this can't arise — the ⭐ (never DF) already leads.
        if ordered[0].get("prop_type") == "Double Faults":
            j = next((i for i, p in enumerate(ordered)
                      if p.get("prop_type") != "Double Faults"), None)
            if j is not None:
                head = ordered.pop(j)
                ordered = [head] + ordered
                log.info("POD_DF_HEADLINE | %s DF would lead the no-⭐ board — floated "
                         "%s %s (conf %s) to the top slot (ordering only, still no ⭐)",
                         blocked.get("player"), head.get("player"),
                         head.get("prop_type"), head.get("confidence"))
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
# "" = disarmed. Was a one-off date now long past; the guard below compares
# for equality with today, so a stale date and "" behave identically. Made
# explicit so it does not read as an active exclusion.
STAR_EXCLUDE_DATE    = os.getenv("STAR_EXCLUDE_DATE", "")
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
        #
        # ``pool`` is the FULL evaluated board. The caller drops plays that are
        # still awaiting a result, and that filter can take out one of the two
        # slip legs — on 9/3 it did, and the survivor posted alone as a one-leg
        # "3x". A slip is a PAIR by definition, so the caller has to be able to
        # re-cut it, and _select_slip draws from the whole board rather than the
        # top-N. Handing back only ``ranked`` left it nothing to re-cut from.
        return {"ranked": ordered[:MAX_RANKED_PLAYS], "slip": slip,
                "pool": ordered, "thin_slate": thin_slate, "has_star": has_star}
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
