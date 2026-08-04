"""
Underdog Fantasy board client — isolated, no discord imports.

Underdog publishes its whole board on ONE unauthenticated JSON endpoint, the
same shape of integration as the PrizePicks partner API, so this needs no proxy
and no credentials. Every call is wrapped: a failure returns an empty board and
can never crash a caller.

Two things Underdog gives us that PrizePicks does not:
  • a PRICE per side (american/decimal), so a projection can be compared to an
    implied probability rather than only to a flat pick'em line
  • SET markets (Sets Won / Sets Played), which the scenario mixture already
    models via the same S1-S4 split that drives /match

Structure of the payload:
  players       id -> {first_name, last_name, sport_id}
  appearances   id -> {player_id, match_id, match_type}
  solo_games    id -> {home_player_id, away_player_id, ..., scheduled_at, status}
  over_under_lines[].over_under.appearance_stat -> {appearance_id, display_stat}
Opponent resolution therefore runs appearance -> solo_game -> the other side.
"""

import logging
import re

import requests

log = logging.getLogger("baseline-bot.underdog")

BOARD_URL = "https://api.underdogfantasy.com/beta/v6/over_under_lines"
TIMEOUT = 40
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}

# Underdog display_stat -> Baseline prop_type.
# The five on the left project through the EXISTING chain unchanged. Sets Won /
# Sets Played are Underdog-only markets served by the scenario mixture.
PROP_MAP = {
    "Aces":            "Aces",
    "Double Faults":   "Double Faults",
    "Breakpoints Won": "Break Points Won",
    "Games Won":       "Player Total Games Won",
    "Games Played":    "Total Games",          # match total, both players
    "Sets Won":        "Sets Won",
    "Sets Played":     "Sets Played",
}
# Markets we deliberately do NOT carry: 1st Set Games Won/Played, Tiebreakers
# Played, and the serve-point splits (First Serve Points Won, First Serves In,
# Break Points Saved, Serve/Second Serve Points Won, Second Serve Attempts,
# Points Won). Each would need a model we have not built or graded.
SET_PROPS = ("Sets Won", "Sets Played")

_RANK_PREFIX = re.compile(r"^\(\s*\d+\s*\)\s*")     # "(1) Aryna Sabalenka"


def _clean_name(s: str) -> str:
    """Strip Underdog's seed prefix so names match the resolver everywhere else."""
    return _RANK_PREFIX.sub("", (s or "").strip()).strip()


def fetch_board() -> dict:
    """Raw board payload, or {} on any failure."""
    try:
        r = requests.get(BOARD_URL, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("underdog board fetch failed: %s", str(exc)[:160])
        return {}


def parse_tennis(board: dict = None) -> list:
    """Tennis props as [{player, opponent, prop_type, line, ...}], mirroring the
    shape pick_of_day._parse_board returns so the projection path is shared.

    Only lines whose stat is in PROP_MAP are returned; everything else is dropped
    with a count logged, so an unfamiliar market is visible rather than silent.
    """
    board = board if board is not None else fetch_board()
    if not board:
        return []
    try:
        players = {p.get("id"): p for p in (board.get("players") or [])
                   if (p.get("sport_id") or "").upper() == "TENNIS"}
        apps = {a.get("id"): a for a in (board.get("appearances") or [])
                if a.get("player_id") in players}
        solo = {g.get("id"): g for g in (board.get("solo_games") or [])}

        out, skipped = [], {}
        for ln in (board.get("over_under_lines") or []):
            ou = ln.get("over_under") or {}
            st = ou.get("appearance_stat") or {}
            app = apps.get(st.get("appearance_id"))
            if not app:
                continue
            disp = st.get("display_stat")
            prop = PROP_MAP.get(disp)
            if not prop:
                skipped[disp] = skipped.get(disp, 0) + 1
                continue
            game = solo.get(app.get("match_id")) or {}
            pid = app.get("player_id")
            home_id, away_id = game.get("home_player_id"), game.get("away_player_id")
            if pid == home_id:
                opp_name = game.get("away_player_name")
            elif pid == away_id:
                opp_name = game.get("home_player_name")
            else:
                opp_name = None
            pl = players.get(pid) or {}
            name = _clean_name(f"{pl.get('first_name','')} {pl.get('last_name','')}")
            try:
                line = float(ln.get("stat_value"))
            except (TypeError, ValueError):
                continue
            # Prices, when present — the reason Underdog is worth carrying.
            over_px = under_px = None
            for o in (ln.get("options") or []):
                if o.get("choice") == "higher":
                    over_px = o.get("american_price")
                elif o.get("choice") == "lower":
                    under_px = o.get("american_price")
            out.append({
                "player": name,
                "opponent": _clean_name(opp_name),
                "prop_type": prop,
                "line": line,
                "source": "underdog",
                "ud_stat": disp,
                "over_price": over_px,
                "under_price": under_px,
                "line_type": ln.get("line_type"),
                "live": bool(ln.get("live_event")),
                "match_id": app.get("match_id"),
                "scheduled_at": game.get("scheduled_at"),
                "status": game.get("status"),
                "round": ((game.get("pre_game_data") or {}).get("round_display")),
            })
        if skipped:
            log.info("underdog: skipped %d line(s) in unmapped markets: %s",
                     sum(skipped.values()),
                     ", ".join(f"{k}={v}" for k, v in sorted(skipped.items(),
                                                             key=lambda x: -x[1])[:6]))
        log.info("underdog: parsed %d tennis props across %d players",
                 len(out), len({p['player'] for p in out}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("underdog parse failed: %s", exc)
        return []


def implied_prob(american) -> float:
    """American price -> implied probability (with vig). None when unparseable."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig_two_way(over_american, under_american) -> tuple:
    """(p_over, p_under) de-vigged proportionally, matching how the moneyline
    anchor treats a two-way market. (None, None) if either side is unusable."""
    po, pu = implied_prob(over_american), implied_prob(under_american)
    if po is None or pu is None:
        return (None, None)
    tot = po + pu
    if tot <= 0:
        return (None, None)
    return (round(po / tot, 4), round(pu / tot, 4))
