"""
NFL book lines — PrizePicks and Underdog, game props only.

Mirrors mlb/lines.py deliberately: same function names, same return shape, same
"never silently fall back to the other book" rule. A line is always labelled with
the book it came from, because pooling two markets that price differently is how
a record stops meaning anything.

Satisfies Rule 6 (market anchor where odds exist): the projection is compared
against a real posted line, not an invented one.

LEAGUE FILTERING IS THE WHOLE GAME HERE
---------------------------------------
PrizePicks publishes FIVE different NFL leagues on one feed, and they are
different products with different correct models. Observed live 2026-09-08:

    NFL       7182 lines   full-game props        <- the only one we price
    NFLSZN    1332 lines   season-long totals     <- availability bet, not form
    NFL1H      846 lines   first half
    NFL1Q      367 lines   first quarter
    NFLP         -         preseason

The filter is an EXACT match on "NFL". A prefix match would sweep all five in
and price a season-long rushing total with a single-game model — silently, and
catastrophically. Never loosen this to startswith().

STANDARD LINES ONLY
-------------------
Of the 7182 NFL lines live on 2026-09-08, only 1496 (21%) were standard:
3706 were demons and 1980 goblins. Those are different payout structures, not
different opinions about the same number, and the model prices neither. Same
reason the tennis board filters on odds_type. User instruction 2026-09-05:
"no demons no goblins".

UNDERDOG IS RETURNING 426 AS OF 2026-09-08
-------------------------------------------
`api.underdogfantasy.com/v1/over_under_lines` — the URL tennis, MLB and
this module all use — now answers `426 upgrade_required` for every sport. v5
does the same; v7 and v8 do not exist. The one live endpoint, /v2, requires a
`product` parameter whose accepted values are not public.

The fetcher below is written and wired anyway, against the same interface as the
PrizePicks one, so restoring the feed is a one-line URL change rather than a
build. Until then it returns {} and says why, exactly once per call — a dead
upstream must be loud in the logs and invisible on the board, never a guessed
number.
"""

import logging
import re
import unicodedata

import requests

log = logging.getLogger("baseline.nfl.lines")

# UNDERDOG MOVED THE BOARD, it did not close it. `beta/v6` answers 426
# "a new version is required to continue" to everyone now — which reads
# like a deliberate block and is not one: they shipped a new client on
# app.underdogsports.com and retired the beta prefix. The current path is
# plain `v1`, it is unauthenticated, needs no headers, and returns the same
# payload shape (over_under_lines / appearances / players / games) — 9,064
# lines across NFL, MLB and tennis when this was changed.
BOARD_URL = "https://api.underdogfantasy.com/v1/over_under_lines"
PRIZEPICKS_URL = "https://partner-api.prizepicks.com/projections?per_page=1000"
TIMEOUT = 40

BOOKS = ("prizepicks", "underdog")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# The exact PrizePicks league name we price. Exact, not prefix — see module docstring.
PP_LEAGUE = "NFL"

# PrizePicks stat_type -> canonical prop. Observed live 2026-09-08.
# Only the four the engine actually prices appear here; everything else on the
# NFL board (Longest Reception, Player Touchdowns, Kicking Points, Sacks, the
# Combo and "Quarters with N+" markets) is counted as unmapped and logged, so a
# prop we could start pricing shows up in the logs rather than being invisible.
PP_PROP_MAP = {
    "Pass Yards":      "pass_yards",
    "Rush Yards":      "rush_yards",
    "Receiving Yards": "receiving_yards",
    "Receptions":      "receptions",
}

# Underdog display_stat -> canonical prop. Underdog spells the yardage props out
# ("Passing"/"Rushing") where PrizePicks abbreviates them, so this is a separate
# map rather than a shared one — the same reason MLB keeps two. Both spellings
# are accepted because the feed has been dark since before this module was
# written and the exact strings could not be re-confirmed live; an extra key
# costs nothing, a missing one silently shrinks the board.
PROP_MAP = {
    "Passing Yards":   "pass_yards",
    "Pass Yards":      "pass_yards",
    "Rushing Yards":   "rush_yards",
    "Rush Yards":      "rush_yards",
    "Receiving Yards": "receiving_yards",
    "Receptions":      "receptions",
}

# What the engine can price. nfl.props.SUPPORTED is the authority; this mirrors
# it so a line for a prop we cannot price is dropped at parse time.
ENABLED_PROPS = ("pass_yards", "rush_yards", "receiving_yards", "receptions")


def _norm(s: str) -> str:
    """Normalise a player name for matching: strip accents, punctuation, case.

    NFL rosters are full of suffixes and punctuation the two books do not agree
    on — "A.J. Brown" vs "AJ Brown", "Kevin Byard III", "Ja'Marr Chase". The
    suffix is stripped as a whole token so "Brown III" and "Brown" match.
    """
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks).strip()


def _is_straight(ln: dict) -> bool:
    """Level two-way over/under only.

    Underdog mixes multiplier lines (asymmetric payouts — a different bet) and
    one-sided lines into the same feed. The tennis board rejects both and so
    does this.
    """
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


def fetch_prizepicks_lines() -> dict:
    """PrizePicks NFL lines -> {(normalised name, prop): {...}}.

    Standard lines only. PrizePicks posts no two-way price on the pick'em board,
    so there is no de-vig here and market_p_over stays absent rather than being
    invented from a flat 50/50.

    {} on any failure — a missing feed means the board posts nothing, never a
    guessed number.
    """
    try:
        # PrizePicks sits behind Cloudflare and rate-limits hard (1015 after a
        # handful of calls from one IP). The residential proxy spreads a board
        # scan across addresses; without credentials this is a direct call.
        from core import proxy as _px
        r = _px.get(PRIZEPICKS_URL, "nfl", headers=_HEADERS, timeout=TIMEOUT)
        if r is None:
            log.warning("nfl prizepicks: request failed (proxy and direct)")
            return {}
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl prizepicks fetch failed: %s", str(exc)[:160])
        return {}
    try:
        inc = {(i.get("type"), i.get("id")): i
               for i in (board.get("included") or [])}
        out, skipped, unmapped, other_league = {}, 0, {}, 0
        for proj in (board.get("data") or []):
            a = proj.get("attributes") or {}
            rel = proj.get("relationships") or {}
            lref = (rel.get("league") or {}).get("data") or {}
            lname = ((inc.get((lref.get("type"), lref.get("id"))) or {})
                     .get("attributes", {}).get("name") or "")
            # EXACT match. NFLSZN / NFL1H / NFL1Q / NFLP are different products.
            if lname.strip().upper() != PP_LEAGUE:
                if lname.strip().upper().startswith("NFL"):
                    other_league += 1
                continue
            stat = (a.get("stat_type") or "").strip()
            prop = PP_PROP_MAP.get(stat)
            if not prop:
                unmapped[stat] = unmapped.get(stat, 0) + 1
                continue
            if prop not in ENABLED_PROPS:
                continue
            if (a.get("odds_type") or "standard").lower() != "standard":
                skipped += 1
                continue
            if a.get("line_score") is None:
                continue
            pref = ((rel.get("new_player") or rel.get("player")) or {}).get("data") or {}
            pattrs = ((inc.get((pref.get("type"), pref.get("id"))) or {})
                      .get("attributes", {}))
            name = pattrs.get("name") or ""
            if not name:
                continue
            try:
                line = float(a["line_score"])
            except (TypeError, ValueError):
                continue
            out[(_norm(name), prop)] = {
                "player": name, "line": line, "prop": prop,
                "team": (pattrs.get("team") or "").strip() or None,
                "position": (pattrs.get("position") or "").strip() or None,
                "over_price": None, "under_price": None, "book": "prizepicks"}
        if skipped:
            log.info("nfl prizepicks: skipped %d non-standard (demon/goblin) line(s)",
                     skipped)
        if other_league:
            log.info("nfl prizepicks: skipped %d line(s) from other NFL leagues "
                     "(NFLSZN/NFL1H/NFL1Q/NFLP)", other_league)
        if unmapped:
            log.info("nfl prizepicks: %d unmapped stat_type(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("nfl prizepicks: %d standard lines across %d prop type(s), "
                 "%d player(s)", len(out), len({p for _, p in out}),
                 len({n for n, _ in out}))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl prizepicks parse failed: %s", exc)
        return {}


def fetch_underdog_lines() -> dict:
    """Underdog straight two-way NFL lines -> {(normalised name, prop): {...}}.

    Returns {} while the upstream is dark (see module docstring — 426 as of
    2026-09-08). The parse below is the real one and runs unchanged the moment
    the feed answers again.
    """
    try:
        from core import proxy as _px
        r = _px.get(BOARD_URL, "nfl", headers=_HEADERS, timeout=TIMEOUT)
        if r is None:
            log.warning("nfl underdog: request failed (proxy and direct)")
            return {}
        if r.status_code == 426:
            log.warning("nfl underdog: upstream returned 426 upgrade_required — "
                        "the v6 feed is version-gated. NFL Underdog board posts "
                        "nothing until the endpoint is updated. (This affects "
                        "tennis and MLB identically; same URL.)")
            return {}
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl underdog fetch failed: %s", str(exc)[:160])
        return {}
    try:
        players = {p.get("id"): p for p in (board.get("players") or [])
                   if str(p.get("sport_id", "")).upper() == "NFL"}
        apps = {a.get("id"): a for a in (board.get("appearances") or [])
                if a.get("player_id") in players}

        # UNDERDOG'S team_id IS A UUID, NOT AN ABBREVIATION.
        # It was being written straight into the row's "team", where the board
        # then asked normalize_team("01bfe9d5-f671-57ed-aa60-249fcca9267c") to
        # find a fixture. It never could, so _game_for returned {} for every
        # line and all of them were discarded as "not on this slate" — on
        # 2026-09-19 that was 333 of 333, which is why the Underdog NFL board
        # has been posting nothing while PrizePicks priced 168.
        # The payload carries the translation itself: each game has the two
        # team UUIDs and an abbreviated_title of the form "AWAY @ HOME".
        team_abbr = {}
        for g in (board.get("games") or []):
            if str(g.get("sport_id", "")).upper() != "NFL":
                continue
            title = (g.get("abbreviated_title") or "")
            if " @ " not in title:
                continue
            away, home = (x.strip() for x in title.split(" @ ", 1))
            if g.get("away_team_id") and away:
                team_abbr[g["away_team_id"]] = away
            if g.get("home_team_id") and home:
                team_abbr[g["home_team_id"]] = home
        if not team_abbr:
            log.warning("nfl underdog: could not build a team_id -> abbr map "
                        "from %d game(s) — rows will carry no team and the "
                        "board will drop them as off-slate",
                        len(board.get("games") or []))

        out, skipped_mult, unmapped, no_team = {}, 0, {}, 0
        for ln in (board.get("over_under_lines") or []):
            st = (ln.get("over_under") or {}).get("appearance_stat") or {}
            app = apps.get(st.get("appearance_id"))
            if not app:
                continue
            stat = (st.get("display_stat") or "").strip()
            prop = PROP_MAP.get(stat)
            if not prop:
                unmapped[stat] = unmapped.get(stat, 0) + 1
                continue
            if prop not in ENABLED_PROPS:
                continue
            if not _is_straight(ln):
                skipped_mult += 1
                continue
            if ln.get("live_event"):
                continue
            pl = players.get(app.get("player_id")) or {}
            name = f"{pl.get('first_name','')} {pl.get('last_name','')}".strip()
            if not name:
                continue
            try:
                line = float(ln.get("stat_value"))
            except (TypeError, ValueError):
                continue
            over_px = under_px = None
            for o in (ln.get("options") or []):
                if o.get("choice") == "higher":
                    over_px = o.get("american_price")
                elif o.get("choice") == "lower":
                    under_px = o.get("american_price")
            abbr = team_abbr.get(pl.get("team_id"))
            if not abbr:
                no_team += 1
            out[(_norm(name), prop)] = {
                "player": name, "line": line, "prop": prop,
                "team": abbr,
                "position": (pl.get("position_id") or None),
                "over_price": over_px, "under_price": under_px,
                "book": "underdog"}
        if no_team:
            log.warning("nfl underdog: %d line(s) have no resolvable team — "
                        "the board will treat them as off-slate", no_team)
        if skipped_mult:
            log.info("nfl underdog: skipped %d multiplier/one-sided line(s)",
                     skipped_mult)
        if unmapped:
            log.info("nfl underdog: %d unmapped display_stat(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("nfl underdog: %d straight lines across %d prop type(s)",
                 len(out), len({p for _, p in out}))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl underdog parse failed: %s", exc)
        return {}


def fetch_lines(book: str) -> dict:
    """Lines for ONE book. Never falls back to the other — see module docstring."""
    b = (book or "").strip().lower()
    if b == "prizepicks":
        return fetch_prizepicks_lines()
    if b == "underdog":
        return fetch_underdog_lines()
    log.warning("nfl lines: unknown book %r (expected one of %s)", book, BOOKS)
    return {}
