"""
MLB book lines — PrizePicks and Underdog.

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

# Underdog display_stat -> Baseline MLB prop. Strikeouts first, per the North Star.
PROP_MAP = {"Strikeouts": "strikeouts"}

# PrizePicks stat_type -> Baseline MLB prop.
# CRITICAL: PrizePicks lists BOTH "Pitcher Strikeouts" (159 lines, the pitcher
# prop we model) and "Hitter Strikeouts" (333 lines, a BATTER prop — how often a
# hitter strikes out). Mapping the wrong one would silently price batters with a
# pitcher model. Only the pitcher prop is mapped, by exact string.
PP_PROP_MAP = {"Pitcher Strikeouts": "strikeouts"}


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


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


def fetch_prizepicks_lines() -> dict:
    """PrizePicks Pitcher Strikeouts -> {normalised name: {line, ...}}.

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
        out, skipped = {}, 0
        for proj in (board.get("data") or []):
            a = proj.get("attributes") or {}
            rel = proj.get("relationships") or {}
            lref = (rel.get("league") or {}).get("data") or {}
            lname = ((inc.get((lref.get("type"), lref.get("id"))) or {})
                     .get("attributes", {}).get("name") or "")
            if lname.upper() != "MLB":
                continue
            if PP_PROP_MAP.get((a.get("stat_type") or "").strip()) != "strikeouts":
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
            out[_norm(name)] = {"pitcher": name, "line": line,
                                "over_price": None, "under_price": None,
                                "book": "prizepicks"}
        if skipped:
            log.info("mlb prizepicks: skipped %d non-standard (demon/goblin) line(s)",
                     skipped)
        log.info("mlb prizepicks: %d standard pitcher-strikeout lines", len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb prizepicks parse failed: %s", exc)
        return {}


def fetch_lines(book: str) -> dict:
    """Dispatch to a book. {} for an unknown book — never a silent fallback to
    the other one, which would mislabel where a line came from."""
    if book == "underdog":
        return fetch_strikeout_lines()
    if book == "prizepicks":
        return fetch_prizepicks_lines()
    log.warning("mlb lines: unknown book %r", book)
    return {}


def fetch_strikeout_lines() -> dict:
    """Underdog straight two-way strikeout lines.
    {normalised pitcher name: {line, over_price, under_price}}.

    {} on any failure — a missing line feed means the board posts
    projection-only, never a guessed number.
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
        out = {}
        skipped_mult = 0
        for ln in (board.get("over_under_lines") or []):
            st = (ln.get("over_under") or {}).get("appearance_stat") or {}
            app = apps.get(st.get("appearance_id"))
            if not app:
                continue
            if PROP_MAP.get(st.get("display_stat")) != "strikeouts":
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
            out[_norm(name)] = {"pitcher": name, "line": line,
                                "over_price": over_px, "under_price": under_px,
                                "book": "underdog"}
        if skipped_mult:
            log.info("mlb lines: skipped %d multiplier/one-sided strikeout line(s)",
                     skipped_mult)
        log.info("mlb lines: %d straight strikeout lines", len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb lines parse failed: %s", exc)
        return {}


def attach(rows: list, lines: dict = None, book: str = "underdog") -> list:
    """Attach book lines to projection rows, matched on normalised pitcher name.

    A row with no matching line keeps `line=None` and posts projection-only. The
    market probability is de-vigged through core so an MLB market probability
    means exactly what a tennis one does.
    """
    lines = lines if lines is not None else fetch_lines(book)
    if not lines:
        return rows
    from core import odds as _odds
    from . import strikeouts as _so
    hit = 0
    for r in rows:
        m = lines.get(_norm(r.get("pitcher") or ""))
        if not m:
            continue
        hit += 1
        r["line"] = m["line"]
        r["book"] = m.get("book", book)
        r.update(_so._over_under(r["projection"], m["line"]))
        po, pu = _odds.devig_two_way(m.get("over_price"), m.get("under_price"))
        if po is not None:
            r["market_p_over"] = round(po, 4)
            r["market_p_under"] = round(pu, 4)
            # Model minus market, on the model's own side. The number that says
            # whether we disagree with the book and by how much.
            side = r.get("p_over") if r.get("lean") == "OVER" else r.get("p_under")
            mkt = po if r.get("lean") == "OVER" else pu
            if isinstance(side, (int, float)):
                r["edge_vs_market"] = round(side - mkt, 4)
    log.info("mlb lines: %s matched %d/%d projections", book, hit, len(rows))
    return rows
