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
    "Aces":              "Aces",
    "Double Faults":     "Double Faults",
    "Breakpoints Won":   "Break Points Won",
    # "Break Points Saved" is REVERTED off the board (2026-08-04). The projector
    # exists but its VOLUME input does not survive a consistency check: break
    # points lost must equal games broken within a match, and the two stats
    # disagree by 6-40% across players (Draper 1.40x, Kostyuk 1.29x, Swiatek
    # 1.25x). Until the volume side is anchored to the hold rate this market must
    # not reach a board. See project_break_points_saved.
    "Games Won":         "Player Total Games Won",
    "Games Played":      "Total Games",         # match total, both players
    "Sets Won":          "Sets Won",
    "Sets Played":       "Sets Played",
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


def _is_straight(ln: dict) -> bool:
    """True only for a clean two-way over/under at level (1.0x) payout.

    Underdog mixes three things on one endpoint (2026-08-03, user: scan only the
    straight ones):
      • level two-way lines — payout 1.0x on both sides            <- the 262 we keep
      • MULTIPLIER lines — asymmetric payouts (0.91/1.05 … 0.73/1.37, up to 3.5x)
        that are a different bet: the payout, not just the line, carries the edge
      • ONE-SIDED lines — only 'higher' or only 'lower' offered, so there is no
        two-way market to take a side of
    Anything but the first is rejected here.
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


def parse_tennis(board: dict = None, straight_only: bool = True) -> list:
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
        n_mult = 0
        for ln in (board.get("over_under_lines") or []):
            ou = ln.get("over_under") or {}
            st = ou.get("appearance_stat") or {}
            app = apps.get(st.get("appearance_id"))
            if not app:
                continue
            if straight_only and not _is_straight(ln):
                n_mult += 1
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
        if n_mult:
            log.info("underdog: skipped %d multiplier / one-sided line(s) — only "
                     "level two-way over/under is scanned", n_mult)
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


def to_board_props(props: list = None) -> list:
    """Underdog props in the exact shape pick_of_day._parse_board returns, so they
    can be fed straight into _rank_board(props=...) and inherit ALL of the
    PrizePicks gating rather than a parallel copy of it.

    SET MARKETS ARE EXCLUDED HERE. Every gate in that pipeline is expressed in
    CONFIDENCE (board floor 65, POTD bar 80, per-prop caps, tier-aware dedupe),
    and Sets Won / Sets Played produce a probability, not a confidence score.
    Feeding them in would mean inventing a confidence number for them, so they
    stay out of the gated board until they have a scoring basis of their own.
    Doubles entries are dropped for the same reason PrizePicks drops them.
    """
    src = props if props is not None else parse_tennis()
    out = []
    for p in src:
        if p.get("prop_type") in SET_PROPS:
            continue
        if p.get("live"):
            continue
        pl, op = p.get("player") or "", p.get("opponent") or ""
        if not pl or not op or "/" in pl or "/" in op:
            continue
        out.append({"player": pl, "opponent": op,
                    "prop_type": p["prop_type"], "line": p["line"],
                    "odds_type": "standard",          # Underdog has no demon concept
                    "source": "underdog",
                    "over_price": p.get("over_price"),
                    "under_price": p.get("under_price")})
    return out


# ── Set markets ──────────────────────────────────────────────────────────────
# Sets Won / Sets Played need NO new model: in best-of-3 the four scenarios map
# to exact set counts, so the probability is a plain sum, not a distribution.
#
#            S1 win-straights  S2 win-decider  S3 lose-decider  S4 lose-straights
#  sets won        2                 2                1                 0
#  sets played     2                 3                3                 2
#
# So P(Sets Won > 1.5) = S1+S2 = P(win match), P(Sets Won > 0.5) = 1 - S4, and
# P(Sets Played > 2.5) = S2+S3 = P(it goes the distance). Same S1-S4 the props
# and /match already ride on, so a set line can never disagree with them.
#
# Best-of-5 is NOT supported: there S2/S3 cover both 4- and 5-set matches, so a
# 4.5 sets-played line is genuinely ambiguous. Tour tennis on Underdog is
# best-of-3, and returning None is better than guessing.
_SETS_BO3 = {
    "Sets Won":    {"S1": 2, "S2": 2, "S3": 1, "S4": 0},
    "Sets Played": {"S1": 2, "S2": 3, "S3": 3, "S4": 2},
}


def sets_market_prob(scenario_probs: dict, prop_type: str, line: float,
                     is_bo5: bool = False) -> dict:
    """P(over) / P(under) for a Sets Won or Sets Played line.

    scenario_probs is the {S1..S4} already computed for the match, so this
    inherits the market-anchored win prob. Returns None when the market isn't
    supported (best-of-5) or the inputs are missing — never a guess.
    """
    outcomes = _SETS_BO3.get(prop_type)
    if not outcomes or not scenario_probs or is_bo5:
        return None
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None
    p_over = 0.0
    exp = 0.0
    total = 0.0
    for s, sets in outcomes.items():
        p = scenario_probs.get(s)
        if not isinstance(p, (int, float)):
            continue
        total += p
        exp += p * sets
        if sets > line:
            p_over += p
    if total <= 0:
        return None
    return {
        "p_over": round(p_over / total, 4),
        "p_under": round(1.0 - (p_over / total), 4),
        "projection": round(exp / total, 2),
        "line": line,
    }


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
