"""
MLB book lines — PrizePicks and Underdog, all prop types.

Both books are scanned and kept SEPARATE, mirroring how tennis runs a PrizePicks
board and an Underdog board with their own records. A line is always labelled with
the book it came from; fetch_lines() never silently falls back to the other book,
because a mislabelled line would pool two markets that must stay distinct.

Underdog publishes every sport on one unauthenticated endpoint — the same feed
discord-bot/underdog.py already reads for tennis. This module fetches it
independently rather than importing that one, for two reasons: `underdog.py`
lives in the tennis codebase and filters to `sport_id == TENNIS` by design, and
importing across into it would give the MLB module a dependency on tennis code,
which is the coupling direction Rule 2 exists to prevent. The duplicated fetch is
one cheap HTTP call on a shadow board.

Satisfies Rule 6 (market anchor where odds exist): the projection is compared
against a real posted line, not an invented one.

THE TWO BOOKS DO NOT AGREE ON PROP NAMES, so there are two maps, not one:

    canonical prop      PrizePicks stat_type      Underdog display_stat
    strikeouts          "Pitcher Strikeouts"      "Strikeouts"      <- ambiguous
    hitter_strikeouts   "Hitter Strikeouts"       (not offered)
    hits_runs_rbis      "Hits+Runs+RBIs"          "Hits + Runs + RBIs"
    walks               "Walks"                   "Batter Walks"

Mapping either book's strings onto the other would mislabel props. Observed live
2026-08-07.

ROLE, NOT STRING, DISAMBIGUATES A BARE "Strikeouts"
---------------------------------------------------
Underdog posts "Strikeouts" with no Pitcher/Hitter qualifier. Pricing a batter
with the pitcher model would be silent and catastrophic — the same failure mode
"Hitter Strikeouts" already nearly caused on PrizePicks.

It is tempting to infer from counts (2 "Strikeouts" lines against 18 of every
batter prop, so it must be pitchers). That is an inference from one small
snapshot, and small snapshots have been wrong repeatedly here. So the ambiguity
is resolved by ROLE instead: the schedule already names every probable starter,
and callers pass that set in. A name in it gets the pitcher prop; a name not in
it gets the hitter prop. No guessing, and it stays correct if Underdog adds a
hitter strikeout market tomorrow.

UNMAPPED STRINGS ARE LOGGED, NOT DROPPED SILENTLY. A book renaming a prop, or
adding one we could price, should show up in the logs rather than quietly
shrinking the board.
"""

import logging
import re
import unicodedata

import requests

log = logging.getLogger("baseline.mlb.lines")

BOARD_URL = "https://api.underdogfantasy.com/beta/v6/over_under_lines"
PRIZEPICKS_URL = "https://partner-api.prizepicks.com/projections?per_page=1000"
TIMEOUT = 40

BOOKS = ("prizepicks", "underdog")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# Sentinels resolved by role at fetch time — never returned to a caller.
_AMBIG_K = "_strikeouts_by_role"
_AMBIG_FS = "_fantasy_by_role"

# Underdog display_stat -> canonical prop. Observed live 2026-08-07.
PROP_MAP = {
    "Strikeouts":          _AMBIG_K,        # no Pitcher/Hitter qualifier
    "Fantasy Score":       _AMBIG_FS,
    "Fantasy Points":      _AMBIG_FS,
    "Pitching Outs":       "pitching_outs",
    "Hits Allowed":        "hits_allowed",
    "Earned Runs Allowed": "earned_runs",
    "Walks Allowed":       "walks_allowed",
    "Hits":                "hits",
    "Total Bases":         "total_bases",
    "Home Runs":           "home_runs",
    "Runs":                "runs",
    "RBIs":                "rbis",
    "Batter Walks":        "walks",
    "Stolen Bases":        "stolen_bases",
    "Doubles":             "doubles",
    "Triples":             "triples",
    "Singles":             "singles",
    "Hits + Runs + RBIs":  "hits_runs_rbis",
}

# PrizePicks stat_type -> canonical prop. Observed live 2026-08-07.
# CRITICAL: PrizePicks lists BOTH "Pitcher Strikeouts" and "Hitter Strikeouts"
# (a BATTER prop — how often a hitter strikes out). They are mapped to different
# canonical props by exact string; neither is ever matched by prefix.
PP_PROP_MAP = {
    "Pitcher Strikeouts":    "strikeouts",
    "Hitter Strikeouts":     "hitter_strikeouts",
    "Pitcher Fantasy Score": "pitcher_fantasy_score",
    "Hitter Fantasy Score":  "hitter_fantasy_score",
    "Pitching Outs":         "pitching_outs",
    "Hits Allowed":          "hits_allowed",
    "Earned Runs Allowed":   "earned_runs",
    "Walks Allowed":         "walks_allowed",
    "Hits":                  "hits",
    "Total Bases":           "total_bases",
    "Home Runs":             "home_runs",
    "Runs":                  "runs",
    "RBIs":                  "rbis",
    "Walks":                 "walks",
    "Stolen Bases":          "stolen_bases",
    "Doubles":               "doubles",
    "Triples":               "triples",
    "Singles":               "singles",
    "Hits+Runs+RBIs":        "hits_runs_rbis",
}

# Which engine prices each canonical prop. The board dispatches on this rather
# than on a string prefix, so a prop can never reach the wrong model.
PITCHER_PROPS = ("strikeouts", "pitching_outs", "hits_allowed",
                 "walks_allowed", "earned_runs", "pitcher_fantasy_score")
BATTER_PROPS = ("hits", "total_bases", "hitter_strikeouts", "walks",
                "home_runs", "doubles", "triples", "singles", "runs",
                "rbis", "stolen_bases", "hits_runs_rbis",
                "hitter_fantasy_score")


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


def _resolve(prop: str, norm_name: str, pitcher_names) -> str:
    """Turn an ambiguous prop into a concrete one using the player's ROLE.

    `pitcher_names` is the set of normalised probable starters for the slate.
    When it is None the caller did not supply the schedule, and a bare
    "Strikeouts" falls back to the pitcher prop — the historical behaviour that
    the working strikeout board relies on. That fallback is logged.
    """
    if prop == _AMBIG_K:
        if pitcher_names is None:
            return "strikeouts"
        return "strikeouts" if norm_name in pitcher_names else "hitter_strikeouts"
    if prop == _AMBIG_FS:
        if pitcher_names is None:
            return "hitter_fantasy_score"
        return ("pitcher_fantasy_score" if norm_name in pitcher_names
                else "hitter_fantasy_score")
    return prop


def _is_straight(ln: dict) -> bool:
    """Level two-way over/under only. Underdog mixes multiplier lines (asymmetric
    payouts, a different bet) and one-sided lines onto the same feed; the tennis
    board rejects both and so does this."""
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


def fetch_prizepicks_lines(pitcher_names=None) -> dict:
    """PrizePicks MLB lines -> {(normalised name, prop): {...}}.

    Standard lines only: a "demon"/"goblin" line is a different payout structure,
    the same reason the tennis board filters on odds_type. PrizePicks posts no
    two-way price on the pick'em board, so there is no de-vig here and
    market_p_over stays absent rather than being invented from a flat 50/50.
    """
    try:
        r = requests.get(PRIZEPICKS_URL, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb prizepicks fetch failed: %s", str(exc)[:160])
        return {}
    try:
        inc = {(i.get("type"), i.get("id")): i for i in (board.get("included") or [])}
        out, skipped, unmapped = {}, 0, {}
        for proj in (board.get("data") or []):
            a = proj.get("attributes") or {}
            rel = proj.get("relationships") or {}
            lref = (rel.get("league") or {}).get("data") or {}
            lname = ((inc.get((lref.get("type"), lref.get("id"))) or {})
                     .get("attributes", {}).get("name") or "")
            if lname.upper() != "MLB":
                continue
            stat = (a.get("stat_type") or "").strip()
            prop = PP_PROP_MAP.get(stat)
            if not prop:
                unmapped[stat] = unmapped.get(stat, 0) + 1
                continue
            if (a.get("odds_type") or "standard").lower() != "standard":
                skipped += 1
                continue
            if a.get("line_score") is None:
                continue
            pref = ((rel.get("new_player") or rel.get("player")) or {}).get("data") or {}
            name = ((inc.get((pref.get("type"), pref.get("id"))) or {})
                    .get("attributes", {}).get("name") or "")
            if not name:
                continue
            try:
                line = float(a["line_score"])
            except (TypeError, ValueError):
                continue
            nn = _norm(name)
            out[(nn, _resolve(prop, nn, pitcher_names))] = {
                "player": name, "line": line, "prop": prop,
                "over_price": None, "under_price": None, "book": "prizepicks"}
        if skipped:
            log.info("mlb prizepicks: skipped %d non-standard (demon/goblin) line(s)",
                     skipped)
        if unmapped:
            log.info("mlb prizepicks: %d unmapped stat_type(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("mlb prizepicks: %d standard lines across %d prop type(s)",
                 len(out), len({p for _, p in out}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb prizepicks parse failed: %s", exc)
        return {}


def fetch_underdog_lines(pitcher_names=None) -> dict:
    """Underdog straight two-way MLB lines -> {(normalised name, prop): {...}}.

    {} on any failure — a missing line feed means the board posts nothing for
    that book, never a guessed number.
    """
    try:
        r = requests.get(BOARD_URL, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        board = r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb lines fetch failed: %s", str(exc)[:160])
        return {}
    try:
        players = {p.get("id"): p for p in (board.get("players") or [])
                   if str(p.get("sport_id", "")).upper() == "MLB"}
        apps = {a.get("id"): a for a in (board.get("appearances") or [])
                if a.get("player_id") in players}
        out, skipped_mult, unmapped = {}, 0, {}
        for ln in (board.get("over_under_lines") or []):
            st = (ln.get("over_under") or {}).get("appearance_stat") or {}
            app = apps.get(st.get("appearance_id"))
            if not app:
                continue
            stat = st.get("display_stat") or ""
            prop = PROP_MAP.get(stat)
            if not prop:
                unmapped[stat] = unmapped.get(stat, 0) + 1
                continue
            if not _is_straight(ln):
                skipped_mult += 1
                continue
            if ln.get("live_event"):
                continue
            pl = players.get(app.get("player_id")) or {}
            name = f"{pl.get('first_name','')} {pl.get('last_name','')}".strip()
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
            nn = _norm(name)
            resolved = _resolve(prop, nn, pitcher_names)
            if prop in (_AMBIG_K, _AMBIG_FS) and pitcher_names is None:
                log.info("mlb underdog: %r for %s resolved to %s by FALLBACK "
                         "(no schedule supplied)", stat, name, resolved)
            out[(nn, resolved)] = {
                "player": name, "line": line, "prop": resolved,
                "over_price": over_px, "under_price": under_px,
                "book": "underdog"}
        if skipped_mult:
            log.info("mlb lines: skipped %d multiplier/one-sided line(s)",
                     skipped_mult)
        if unmapped:
            log.info("mlb underdog: %d unmapped display_stat(s): %s",
                     len(unmapped), dict(sorted(unmapped.items(),
                                                key=lambda kv: -kv[1])[:12]))
        log.info("mlb underdog: %d straight lines across %d prop type(s)",
                 len(out), len({p for _, p in out}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb lines parse failed: %s", exc)
        return {}


# Kept under its original name: the strikeout board and its tests call this.
fetch_strikeout_lines = fetch_underdog_lines


def fetch_lines(book: str, pitcher_names=None) -> dict:
    """Dispatch to a book. {} for an unknown book — never a silent fallback to
    the other one, which would mislabel where a line came from.

    `pitcher_names`: normalised names of the slate's probable starters, used to
    resolve props whose book string does not say whether it is a pitcher or a
    hitter market. See the module docstring.
    """
    if book == "underdog":
        return fetch_underdog_lines(pitcher_names)
    if book == "prizepicks":
        return fetch_prizepicks_lines(pitcher_names)
    log.warning("mlb lines: unknown book %r", book)
    return {}


def price(row: dict, match: dict) -> dict:
    """Attach one book line to one projection row and compute the market edge.

    Split out of attach() so the multi-prop board can price a row it built
    itself. The over/under probabilities already live on the row — each prop
    engine computes them with ITS OWN dispersion — so this never recomputes
    them with a strikeout dispersion, which is what the old single-prop path
    did and would now be wrong for four of the five pitcher props.
    """
    from core import odds as _odds
    row["line"] = match["line"]
    row["book"] = match.get("book")
    po, pu = _odds.devig_two_way(match.get("over_price"), match.get("under_price"))
    if po is not None:
        row["market_p_over"] = round(po, 4)
        row["market_p_under"] = round(pu, 4)
        # Model minus market, on the model's own side. The number that says
        # whether we disagree with the book and by how much.
        side = row.get("p_over") if row.get("lean") == "OVER" else row.get("p_under")
        mkt = po if row.get("lean") == "OVER" else pu
        if isinstance(side, (int, float)):
            row["edge_vs_market"] = round(side - mkt, 4)
    return row


def attach(rows: list, lines: dict = None, book: str = "underdog") -> list:
    """Attach book lines to STRIKEOUT projection rows, matched on player name.

    The legacy single-prop path, still used by the strikeouts-only scan. Rows
    carry no prop key, so this looks up the strikeout entry specifically. The
    multi-prop board uses price() instead.
    """
    lines = lines if lines is not None else fetch_lines(book)
    if not lines:
        return rows
    from . import strikeouts as _so
    hit = 0
    for r in rows:
        nn = _norm(r.get("pitcher") or "")
        m = lines.get((nn, "strikeouts"))
        if not m:
            continue
        hit += 1
        r.update(_so._over_under(r["projection"], m["line"]))
        price(r, m)
    log.info("mlb lines: %s matched %d/%d strikeout projections", book, hit,
             len(rows))
    return rows
