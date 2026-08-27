"""
Sportsbook odds for tennis, scoped to matches already on a DFS board.

WHY THIS EXISTS: PrizePicks and Underdog are pick'em, not a market. Their lines
sit at .5 with a fixed payout, chosen to split action — they are not prices, and
they carry no probability. A sportsbook posts a flat number with juice on both
sides, and de-vigging that gives a real market probability to anchor against.
Every prop we model is currently compared to a pick'em line as if it were a
market estimate; this module is what makes a genuine comparison possible.

FANDUEL, DIRECT, NO PROXY. The July research recorded FanDuel as rejecting the
TLS handshake outright. That is no longer true with curl_cffi browser
impersonation — and the residential proxy makes it WORSE (exit IPs get 403 while
direct gets 200), so this deliberately does not use the proxy pool that
sofascore_client routes through.

THE PARAMS ARE THE UNLOCK, not the host. The obvious
`?page=CUSTOM&customPageId=tennis&_ak=...` form returns {"error":true} from every
US state host, which is exactly what made this look blocked. The full parameter
set below works, and eventTypeId=2 is tennis (FanDuel inherits Betfair's
taxonomy via Flutter; 6423 is American Football).

SCOPED TO THE DFS BOARD ON PURPOSE. The sport-level page returns only ONE
featured market per event, so reading a match's real market set costs a call per
event. Walking all ~86 tennis events every scan would be ~86 calls for props we
never price. Callers pass the players already on the PrizePicks/Underdog board,
so the fan-out is bounded by the board, not by the tour.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_AK = os.getenv("FANDUEL_AK", "FhMFpcPWXMeyZxOx").strip()
_REGION = os.getenv("FANDUEL_REGION", "nj").strip().lower()
_BASE = f"https://sbapi.{_REGION}.sportsbook.fanduel.com/api"
_TENNIS_EVENT_TYPE = 2

_PARAMS = (
    "betexRegion=GBR&capiJurisdiction=intl&currencyCode=USD&exchangeLocale=en_US"
    "&includePrices=true&language=en&priceHistory=1&regionCode=" + _REGION.upper()
    + "&_ak=" + _AK + "&timezone=America%2FNew_York"
)
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Brand": "FANDUEL",
    "X-Currency": "USD",
    "X-Application": "FDBSportsbook",
    "Referer": "https://sportsbook.fanduel.com/",
}

BOARD_TTL = 300      # the board moves slowly; a scan re-reads it many times
EVENT_TTL = 120      # per-match prices move faster
_cache: dict = {}

ENABLED = os.getenv("BOOK_ODDS_ENABLED", "1").strip() not in ("0", "false", "False")


def _cached(key: str, ttl: int):
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _store(key: str, value):
    _cache[key] = (time.time(), value)
    return value


def _get(url: str, timeout: int = 25) -> Optional[dict]:
    """One GET with browser TLS impersonation. Returns None on any failure —
    book odds are an ENRICHMENT, never a dependency: a projection that already
    works without them must not start failing because a book is unreachable."""
    try:
        from curl_cffi import requests as cf
    except Exception:  # noqa: BLE001
        logger.warning("book_odds: curl_cffi unavailable")
        return None
    try:
        r = cf.get(url, headers=_HEADERS, impersonate="chrome124", timeout=timeout)
        if r.status_code != 200:
            logger.info("book_odds: HTTP %s for %s", r.status_code, url.split("?")[0])
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        logger.info("book_odds: fetch failed for %s", url.split("?")[0], exc_info=False)
        return None


def _norm(s: str) -> str:
    """Reuse the canonical normalizer rather than add another. Four near-identical
    ones already exist in this codebase and they drift; this is not a fifth."""
    from ..features import _norm as canonical
    return canonical(s or "")


def _last(s: str) -> str:
    parts = _norm(s).split()
    return parts[-1] if parts else ""


def fetch_board() -> list:
    """Every tennis event FanDuel has up, with its featured market only."""
    if not ENABLED:
        return []
    c = _cached("board", BOARD_TTL)
    if c is not None:
        return c
    d = _get(f"{_BASE}/content-managed-page?page=SPORT"
             f"&eventTypeId={_TENNIS_EVENT_TYPE}&{_PARAMS}", timeout=30)
    events = list(((d or {}).get("attachments") or {}).get("events", {}).values())
    logger.info("book_odds: board has %d tennis events", len(events))
    return _store("board", events)


def find_event(player: str, opponent: str) -> Optional[dict]:
    """Match our two players to a FanDuel event.

    Joined on BOTH surnames, unordered. FanDuel names events "A v B" but which
    player is A is theirs to decide, and a single-surname match would happily
    pair the wrong Fernandez. Doubles events carry a "/" and are skipped — we
    never price them, and their names collide with singles surnames.
    """
    if not player or not opponent:
        return None
    pl, ol = _last(player), _last(opponent)
    if not pl or not ol:
        return None
    for e in fetch_board():
        name = str(e.get("name") or "")
        if "/" in name:
            continue
        n = _norm(name)
        if pl in n and ol in n:
            return e
    return None


def _american(decimal_price) -> Optional[int]:
    """Decimal -> American, because that is how a book line is read."""
    try:
        d = float(decimal_price)
    except (TypeError, ValueError):
        return None
    if d <= 1.0:
        return None
    return int(round((d - 1.0) * 100)) if d >= 2.0 else int(round(-100.0 / (d - 1.0)))


def markets_for(player: str, opponent: str) -> dict:
    """Every market FanDuel prices for this matchup, normalized.

    Returns {} rather than raising when the match is not up, the book is
    unreachable, or odds are disabled — see _get().
    """
    out = {"found": False, "source": "fanduel"}
    if not ENABLED:
        return out
    ev = find_event(player, opponent)
    if not ev:
        return out
    eid = ev.get("eventId")
    key = f"ev:{eid}"
    cached = _cached(key, EVENT_TTL)
    if cached is None:
        d = _get(f"{_BASE}/event-page?eventId={eid}&{_PARAMS}", timeout=30)
        cached = _store(key, ((d or {}).get("attachments") or {}).get("markets") or {})
    markets = []
    for m in (cached or {}).values():
        runners = []
        for r in (m.get("runners") or []):
            price = ((r.get("winRunnerOdds") or {}).get("trueOdds") or {}) \
                .get("decimalOdds", {}) or {}
            dec = price.get("decimalOdds")
            runners.append({
                "name": r.get("runnerName"),
                "handicap": r.get("handicap"),
                "decimal": dec,
                "american": _american(dec),
            })
        markets.append({
            "marketName": m.get("marketName"),
            "marketType": m.get("marketType"),
            "runners": runners,
        })
    out.update({
        "found": True,
        "event_id": eid,
        "event_name": ev.get("name"),
        "competition": ev.get("competitionName"),
        "market_count": len(markets),
        "markets": markets,
    })
    return out


def devig_two_way(a_dec, b_dec) -> Optional[float]:
    """Proportional de-vig of a two-way price -> P(a). Same method the Sofascore
    moneyline anchor already uses, so the two are directly comparable."""
    try:
        ia, ib = 1.0 / float(a_dec), 1.0 / float(b_dec)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    tot = ia + ib
    return (ia / tot) if tot > 0 else None


def summary_for(player: str, opponent: str) -> dict:
    """The book markets that map onto props we actually model.

    win_at_least_one_set is the interesting one: we DERIVE that from the
    scenario mixture (P = 1 - S4) and have never had anything to check it
    against. A priced line is both an anchor and a scoreboard for the mixture.
    """
    raw = markets_for(player, opponent)
    if not raw.get("found"):
        return raw
    pl = _last(player)
    out = {k: raw[k] for k in ("found", "source", "event_id", "event_name",
                               "competition", "market_count")}
    for m in raw["markets"]:
        nm = (m.get("marketName") or "")
        low = nm.lower()
        runners = m.get("runners") or []
        if m.get("marketType") == "MATCH_BETTING" and len(runners) == 2:
            p = devig_two_way(runners[0]["decimal"], runners[1]["decimal"])
            if p is not None:
                mine = pl in _norm(runners[0]["name"] or "")
                out["moneyline_p"] = round(p if mine else 1.0 - p, 4)
                out["moneyline_american"] = (runners[0] if mine else runners[1])["american"]
        elif "at least 1 set" in low and pl in _norm(nm) and len(runners) == 2:
            p = devig_two_way(runners[0]["decimal"], runners[1]["decimal"])
            if p is not None:
                yes = "yes" in _norm(runners[0]["name"] or "")
                out["win_at_least_one_set_p"] = round(p if yes else 1.0 - p, 4)
        elif "most aces" in low:
            out["most_aces"] = [{"name": r["name"], "american": r["american"]}
                                for r in runners]
        elif "ace" in low:
            out.setdefault("ace_markets", []).append(
                {"market": nm, "runners": [{"name": r["name"],
                                            "handicap": r["handicap"],
                                            "american": r["american"]}
                                           for r in runners]})
        elif "break" in low:
            out.setdefault("break_markets", []).append(
                {"market": nm, "runners": [{"name": r["name"],
                                            "handicap": r["handicap"],
                                            "american": r["american"]}
                                           for r in runners]})
    return out
