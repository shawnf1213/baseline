"""
NBA book lines — PrizePicks and Underdog, full-game player props only.

Mirrors nfl/lines.py deliberately: same function names, same return shape, same
"never silently fall back to the other book" rule. A line is always labelled
with the book it came from, because pooling two markets that price differently
is how a record stops meaning anything.

Satisfies Rule 6 (market anchor where odds exist): the projection is compared
against a real posted line, not an invented one.

LEAGUE FILTERING IS THE WHOLE GAME HERE
---------------------------------------
PrizePicks publishes several basketball leagues on one feed and they are
different products with different correct models. Observed live 2026-10-02:

    NBASZN   554 lines   season-long totals   <- an AVAILABILITY bet, not form
    WNBA     414 lines   a different league entirely
    NBA      194 lines   full-game player props   <- the only one we price
    WNBA2H    32 lines
    WNBA1Q     6 lines

The filter is an EXACT match on "NBA". A prefix match would sweep NBASZN in and
price a season-long points total with a single-game model — silently, and
catastrophically, since 554 season-long lines would outnumber the 194 real ones
nearly three to one. Never loosen this to startswith().

STANDARD LINES ONLY
-------------------
Of the 194 NBA lines live on 2026-10-02, only 35 (18%) were standard: 105 were
demons and 54 goblins. Those are different payout structures, not different
opinions about the same number, and the model prices neither. Same filter the
tennis and NFL boards apply. User instruction 2026-09-05: "no demons no goblins".

WHAT IS DELIBERATELY NOT MAPPED
-------------------------------
"Double-Double" and "Blocked Shots" are on the board and are not here. A
double-double is a joint threshold on two correlated counts — a different
question needing a bivariate model, not a mean and a spread. Blocked shots are
offered at 0.5-1.5 and are essentially "does it happen at all", dominated by
matchup luck on a handful of events. Both are counted as unmapped and logged, so
a prop we could start pricing shows up in the logs rather than being invisible.
"""

import logging
import re
import unicodedata

log = logging.getLogger("baseline.nba.lines")

# Underdog: see nfl/lines.py — `beta/v6` answers 426 and the live path is plain
# `v1`, unauthenticated, same payload shape.
BOARD_URL = "https://api.underdogfantasy.com/v1/over_under_lines"
PRIZEPICKS_URL = "https://partner-api.prizepicks.com/projections?per_page=1000"
TIMEOUT = 40

BOOKS = ("prizepicks", "underdog")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# The exact PrizePicks league name we price. Exact, not prefix — see docstring.
PP_LEAGUE = "NBA"

# PrizePicks stat_type -> canonical prop. Strings VERIFIED LIVE 2026-10-02
# against the feed; this is not a guess at what they might be called.
PP_PROP_MAP = {
    "Points":        "pts",
    "Rebounds":      "reb",
    "Assists":       "ast",
    "3-PT Made":     "fg3m",
    "Pts+Rebs+Asts": "pra",
    "Pts+Rebs":      "pr",
    "Pts+Asts":      "pa",
    "Rebs+Asts":     "ra",
    # NOT on the board on 2026-10-02 but carried seasonally. Mapped in advance
    # because an unmapped line is an invisible one, and the cost of a key that
    # never matches is nothing.
    "Fantasy Score": "nba_fantasy_pts",
}

# Underdog spells several of these differently. A separate map rather than a
# shared one, the same reason MLB and NFL each keep two: one book renaming a
# stat must not silently shrink the other book's board.
PROP_MAP = {
    "Points":            "pts",
    "Rebounds":          "reb",
    "Assists":           "ast",
    "3-Pointers Made":   "fg3m",
    "3-PT Made":         "fg3m",
    "Three Pointers Made": "fg3m",
    "Pts + Rebs + Asts": "pra",
    "Pts+Rebs+Asts":     "pra",
    "Points + Rebounds + Assists": "pra",
    "Pts + Rebs":        "pr",
    "Pts+Rebs":          "pr",
    "Pts + Asts":        "pa",
    "Pts+Asts":          "pa",
    "Rebs + Asts":       "ra",
    "Rebs+Asts":         "ra",
    "Fantasy Points":    "nba_fantasy_pts",
}

# What the engine can price. nba.props.SUPPORTED is the authority; this mirrors
# it so a line for a prop we cannot price is dropped at parse time.
ENABLED_PROPS = ("pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra",
                 "nba_fantasy_pts")


def _norm(s: str) -> str:
    """Normalise a player name for matching: strip accents, punctuation, case.

    NBA rosters carry accents the books do not agree on (Jokic/Jokic,
    Doncic/Doncic), suffixes (Jaren Jackson Jr.), and punctuation (De'Aaron
    Fox, Shai Gilgeous-Alexander). The suffix is stripped as a whole token so
    "Jackson Jr" and "Jackson" match.
    """
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks).strip()


def _is_straight(ln: dict) -> bool:
    """Level two-way over/under only.

    Underdog mixes multiplier lines (asymmetric payouts — a different bet) and
    one-sided lines into the same feed. The tennis and NFL boards reject both
    and so does this.
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
    """PrizePicks NBA lines -> {(normalised name, prop): {...}}.

    Standard lines only. {} on any failure — a missing feed means the board
    posts nothing, never a guessed number.
    """
    try:
        # PrizePicks sits behind Cloudflare and rate-limits hard. The residential
        # proxy spreads a board scan across addresses; without credentials this
        # is a direct call.
        from core import proxy as _px
        r = _px.get(PRIZEPICKS_URL, "nba", headers=_HEADERS, timeout=TIMEOUT)
        if r is None:
            log.warning("nba prizepicks: request failed (proxy and direct)")
            return {}
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba prizepicks fetch failed: %s", str(exc)[:160])
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
            # EXACT match. NBASZN is a different product — see module docstring.
            if lname.strip().upper() != PP_LEAGUE:
                if lname.strip().upper().startswith("NBA"):
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
            log.info("nba prizepicks: skipped %d non-standard (demon/goblin) line(s)",
                     skipped)
        if other_league:
            log.info("nba prizepicks: skipped %d line(s) from other NBA leagues "
                     "(NBASZN etc.)", other_league)
        if unmapped:
            log.info("nba prizepicks: %d unmapped stat_type(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("nba prizepicks: %d standard lines across %d prop type(s), "
                 "%d player(s)", len(out), len({p for _, p in out}),
                 len({n for n, _ in out}))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba prizepicks parse failed: %s", exc)
        return {}


def fetch_underdog_lines() -> dict:
    """Underdog straight two-way NBA lines -> {(normalised name, prop): {...}}."""
    try:
        from core import proxy as _px
        r = _px.get(BOARD_URL, "nba", headers=_HEADERS, timeout=TIMEOUT)
        if r is None:
            log.warning("nba underdog: request failed (proxy and direct)")
            return {}
        if r.status_code == 426:
            log.warning("nba underdog: upstream returned 426 upgrade_required — "
                        "the feed is version-gated. NBA Underdog board posts "
                        "nothing until the endpoint is updated.")
            return {}
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba underdog fetch failed: %s", str(exc)[:160])
        return {}
    try:
        players = {p.get("id"): p for p in (board.get("players") or [])
                   if str(p.get("sport_id", "")).upper() == "NBA"}
        apps = {a.get("id"): a for a in (board.get("appearances") or [])
                if a.get("player_id") in players}

        # UNDERDOG'S team_id IS A UUID, NOT AN ABBREVIATION — the trap that kept
        # the NFL Underdog board empty for weeks (nfl/lines.py has the full
        # account). The payload carries the translation itself: each game has
        # the two team UUIDs and an abbreviated_title of the form "AWAY @ HOME".
        team_abbr = {}
        for g in (board.get("games") or []):
            if str(g.get("sport_id", "")).upper() != "NBA":
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
            log.warning("nba underdog: could not build a team_id -> abbr map "
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
            log.warning("nba underdog: %d line(s) have no resolvable team — "
                        "the board will treat them as off-slate", no_team)
        if skipped_mult:
            log.info("nba underdog: skipped %d multiplier/one-sided line(s)",
                     skipped_mult)
        if unmapped:
            log.info("nba underdog: %d unmapped display_stat(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("nba underdog: %d straight lines across %d prop type(s)",
                 len(out), len({p for _, p in out}))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba underdog parse failed: %s", exc)
        return {}


def fetch_lines(book: str) -> dict:
    """Lines for ONE book. Never falls back to the other — see module docstring."""
    b = (book or "").strip().lower()
    if b == "prizepicks":
        return fetch_prizepicks_lines()
    if b == "underdog":
        return fetch_underdog_lines()
    log.warning("nba lines: unknown book %r (expected one of %s)", book, BOOKS)
    return {}
