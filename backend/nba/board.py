"""
The NBA board — price every supported line on one book, qualify, rank.

SCOPED TO THE SLATE BEFORE ANYTHING IS PRICED. The NBA plays most nights, so a
week-wide window would mix tonight's slate with five others and a board titled
for today would carry games three days out. The default window is ONE day,
unlike NFL's, which is a weekly sport with a two-day drop.

ONE PROP PER PLAYER
-------------------
A player's points and his Pts+Rebs+Asts are the same night seen twice; so are
his rebounds and his Rebs+Asts. Boarding both doubles the exposure to a single
outcome while looking like diversification. The board keeps only that player's
single best-ranked prop — the same rule the NFL board applies to a receiver's
yards and receptions, and the MLB board to a pitcher's strikeouts and earned
runs.

COMBOS ARE HELD TO A HIGHER BAR AND CANNOT BE THE STAR
------------------------------------------------------
A PRA is compounded from three projections, so its errors compound too. This is
the NBA's Total Games: tennis blocks Total Games and Player Total Games Won from
the star slot and caps their confidence at 80 for exactly this reason, and the
same treatment applies here. Single-stat props — points, rebounds, assists,
threes — are star-eligible; combos are board-eligible only, and only at a
stricter bar.

Rule 2: never raises. A book that fails returns an empty board and says so in
the log, rather than taking the other book or the tennis bot down.
"""

import logging
import os

log = logging.getLogger("baseline.nba.board")

# ── QUALIFICATION ────────────────────────────────────────────────────────────
# The standard bar MATCHES THE NFL PIPELINE'S, per spec: a model probability
# floor and an edge expressed in standard deviations rather than raw units,
# because 2 points on a 24.5 line and 2 rebounds on a 4.5 line are not the same
# claim.
MIN_PROB = float(os.getenv("NBA_MIN_PROB", "0.52") or 0.52)
MIN_EDGE_SD = float(os.getenv("NBA_MIN_EDGE_SD", "0.10") or 0.10)

# Confidence floor. Separate from MIN_PROB: the probability says which side the
# model likes, the confidence says how much evidence stands behind it, and a
# board needs both.
MIN_CONF = float(os.getenv("NBA_MIN_CONF", "60") or 60)

# ── THE COMBO BAR ────────────────────────────────────────────────────────────
# Stricter on every axis, because a combo's error is the sum of three. These are
# ADDITIVE to the standard bar, not replacements, so loosening the standard bar
# cannot accidentally loosen the combo one.
COMBO_PROPS = ("pra", "pr", "pa", "ra")
COMBO_MIN_PROB = float(os.getenv("NBA_COMBO_MIN_PROB", "0.56") or 0.56)
COMBO_MIN_EDGE_SD = float(os.getenv("NBA_COMBO_MIN_EDGE_SD", "0.20") or 0.20)
COMBO_MIN_CONF = float(os.getenv("NBA_COMBO_MIN_CONF", "68") or 68)

# Props that may hold the star slot. Combos are deliberately absent — see the
# module docstring.
STAR_ELIGIBLE = ("pts", "reb", "ast", "fg3m")

# ── THE DATA-SUFFICIENCY GATE ────────────────────────────────────────────────
# A row whose usage window is "prior season only" is priced entirely on LAST
# season, because the current one has published no games yet. NFL measured
# exactly this at +25.3% relative error against the market, concentrated where
# roles change — and an NBA offseason moves roles at least as much as an NFL
# one.
#
# So the gate is on EVIDENCE, not on a calendar date: a player is priced once he
# has current-season games, whenever that happens. In practice the board is
# empty on opening night, thin for a week, and full from about game 10 — and it
# fills itself without anyone flipping a switch.
#
# NBA_ALLOW_PRIOR_SEASON=1 lifts the gate for research and one-off scans. Rows
# are still LABELLED either way, so a lifted gate is visible rather than silent.
REQUIRE_CURRENT_SEASON = os.getenv(
    "NBA_ALLOW_PRIOR_SEASON", "0").strip() not in ("1", "true", "True", "yes", "on")


def _et_today() -> str:
    import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = datetime.timezone(datetime.timedelta(hours=-5))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _et_date(tipoff: str):
    """The ET calendar date a tip-off falls on. None when unparseable."""
    if not tipoff:
        return None
    try:
        import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("America/New_York")
        except Exception:  # noqa: BLE001
            tz = datetime.timezone(datetime.timedelta(hours=-5))
        dt = datetime.datetime.fromisoformat(str(tipoff).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(tz).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def slate_date(when=None) -> str:
    """Which slate a scan is building.

    A scan run in the EVENING is building tomorrow's card, because tonight's
    games have tipped. Before the evening it is today's. The cut is 20:00 ET —
    after the last NBA tip-off of a normal night.
    """
    import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = datetime.timezone(datetime.timedelta(hours=-5))
    now = when or datetime.datetime.now(tz)
    d = now.date()
    if now.hour >= 20:
        d = d + datetime.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def games_on(games: list, day: str = None, window_days: int = 0) -> list:
    """Games tipping off on `day`, optionally plus `window_days` after it."""
    import datetime
    day = day or slate_date()
    try:
        d0 = datetime.date.fromisoformat(day)
    except Exception:  # noqa: BLE001
        return games
    out = []
    for g in games:
        gd = _et_date(g.get("tipoff"))
        if not gd:
            continue
        try:
            d = datetime.date.fromisoformat(gd)
        except Exception:  # noqa: BLE001
            continue
        if 0 <= (d - d0).days <= max(0, int(window_days)):
            out.append(g)
    return out


def _team_index(games: list) -> dict:
    """{team abbr: the game row}, both sides of every fixture."""
    from .client import normalize_team
    idx = {}
    for g in games:
        for side, other in (("home_abbr", "away_abbr"), ("away_abbr", "home_abbr")):
            tm = normalize_team(g.get(side) or "")
            if not tm:
                continue
            idx[tm] = dict(g, player_team=tm,
                           opponent_team=normalize_team(g.get(other) or ""),
                           matchup=f"{g.get('away_abbr')} @ {g.get('home_abbr')}")
    return idx


def _game_for(team: str, idx: dict) -> dict:
    from .client import normalize_team
    return idx.get(normalize_team(team or "")) or {}


def _win_prob(r: dict):
    """The model's probability on the side it actually likes."""
    lean = (r.get("lean") or "").upper()
    if lean == "OVER":
        return r.get("p_over")
    if lean == "UNDER":
        return r.get("p_under")
    return None


def _rank_key(r: dict):
    """Sort key (ascending sort, so terms are negated): confidence, then edge.

    CONFIDENCE FIRST, EDGE AS THE TIEBREAK — the ranking the tennis board uses.
    A large edge computed from thin evidence is not a large edge, and ordering
    purely on edge surfaces precisely the plays where the projection is least
    supported.

    Deliberately TWO terms and no more. The tennis board carried a third,
    strength-of-field, as the LEAD term, and because tuple comparison is
    lexicographic it outranked confidence and edge together — one bit decided
    the order and buried plays that had earned their place. That was removed on
    2026-10-02 and is not reproduced here.
    """
    return (-(r.get("confidence") or 0), -(r.get("edge_sd") or 0.0))


def _passes(r: dict) -> bool:
    """Does this row clear its bar? Combos face a stricter one."""
    p = _win_prob(r) or 0.0
    esd = r.get("edge_sd") or 0.0
    conf = r.get("confidence") or 0
    if r.get("prop") in COMBO_PROPS:
        return (p >= COMBO_MIN_PROB and esd >= COMBO_MIN_EDGE_SD
                and conf >= COMBO_MIN_CONF)
    return p >= MIN_PROB and esd >= MIN_EDGE_SD and conf >= MIN_CONF


def star_eligible(r: dict) -> bool:
    """May this row hold the star slot? Combos may not — see module docstring."""
    return (r.get("prop") in STAR_ELIGIBLE
            and not r.get("coin_flip")
            and not r.get("prior_season_only"))


def scan_board(book: str = "prizepicks", season: int = None,
               one_per_player: bool = True, day=None,
               window_days: int = None) -> list:
    """Price every supported line on ONE book. Returns ranked rows; [] on failure."""
    from . import lines as _lines, props as _props, client as _client
    try:
        posted = _lines.fetch_lines(book)
        if not posted:
            log.warning("nba board (%s): no lines fetched — nothing to price", book)
            return []
        try:
            games = _client.upcoming(days=3)
        except Exception:  # noqa: BLE001 — Rule 2
            log.exception("nba board (%s): schedule unavailable; pricing without "
                          "a matchup", book)
            games = []
        _win = (int(os.getenv("NBA_SLATE_WINDOW_DAYS", "0") or 0)
                if window_days is None else window_days)
        _day = day or slate_date()
        allg = games
        games = games_on(games, _day, _win)
        log.info("nba board (%s): slate %s (+%dd) -> %d of %d upcoming game(s)",
                 book, _day, _win, len(games), len(allg))
        if not games and allg:
            log.warning("nba board (%s): no games on %s — nothing to price. "
                        "The next tip-off is %s.", book, _day,
                        min((_et_date(g.get("tipoff")) for g in allg
                             if _et_date(g.get("tipoff"))), default="unknown"))
        idx = _team_index(games)
        if not idx:
            log.warning("nba board (%s): no upcoming games — every row would be "
                        "unmatched", book)

        # ONE INJURY FETCH FOR THE WHOLE SCAN. usage_vacuum is called per line
        # and would otherwise re-fetch the report for each of them.
        try:
            inj = _client.injuries()
        except Exception:  # noqa: BLE001 — Rule 2
            inj = {}

        rows, skipped, no_usage, off_slate = [], 0, 0, 0
        for (_nkey, prop), ln in posted.items():
            game = _game_for(ln.get("team"), idx)
            if not game:
                # Not playing on this slate. A player who is not playing today
                # does not belong on today's board at all.
                off_slate += 1
                continue
            r = _props.project(ln["player"], prop, line=ln["line"],
                               game=game, season=season, inj=inj)
            if not r:
                no_usage += 1
                continue
            if r.get("skipped"):
                skipped += 1
                continue
            r.update({
                "book": book,
                "team": ln.get("team"),
                "board_position": ln.get("position"),
                "matchup": game.get("matchup"),
                "away_abbr": game.get("away_abbr"),
                "home_abbr": game.get("home_abbr"),
                "tipoff": game.get("tipoff"),
                "game_id": game.get("game_id"),
                "win_prob": _win_prob(r),
                "slate_date": _day,
            })
            rows.append(r)

        log.info("nba board (%s): priced %d of %d line(s) — %d not on this "
                 "slate, %d no usage, %d below the minutes floor", book,
                 len(rows), len(posted), off_slate, no_usage, skipped)

        # Filters applied AFTER pricing so the log above reports true coverage.
        stale = [r for r in rows if r.get("prior_season_only")]
        if stale:
            if REQUIRE_CURRENT_SEASON:
                rows = [r for r in rows if not r.get("prior_season_only")]
                log.warning("nba board (%s): %d play(s) held back — priced on "
                            "prior-season usage only. They post once these "
                            "players have current-season games "
                            "(NBA_ALLOW_PRIOR_SEASON=1 to override).",
                            book, len(stale))
            else:
                log.warning("nba board (%s): %d play(s) priced on PRIOR-SEASON "
                            "usage only (gate lifted) — rows are labelled",
                            book, len(stale))

        keep = [r for r in rows if _passes(r)]
        _combo_kept = sum(1 for r in keep if r.get("prop") in COMBO_PROPS)
        log.info("nba board (%s): %d play(s) qualified — singles need p>=%.2f / "
                 "edge>=%.2f sd / conf>=%.0f, combos need %.2f / %.2f / %.0f "
                 "(%d combo(s) kept)", book, len(keep), MIN_PROB, MIN_EDGE_SD,
                 MIN_CONF, COMBO_MIN_PROB, COMBO_MIN_EDGE_SD, COMBO_MIN_CONF,
                 _combo_kept)
        keep.sort(key=_rank_key)

        if one_per_player:
            seen, dedup = set(), []
            for r in keep:
                nm = r.get("player")
                if nm in seen:
                    continue
                seen.add(nm)
                dedup.append(r)
            if len(dedup) != len(keep):
                log.info("nba board (%s): %d row(s) dropped by one-prop-per-player",
                         book, len(keep) - len(dedup))
            keep = dedup
        return keep
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba board (%s) failed: %s", book, exc)
        return []


def pick_star(rows: list):
    """The highest-ranked STAR-ELIGIBLE row, or None.

    Returns None rather than falling back to a combo: if nothing single-stat
    qualifies, the card has no star that day. A compounded prop must not inherit
    the slot by default — that is precisely the failure mode the tennis board
    guards against with Total Games.
    """
    for r in rows:
        if star_eligible(r):
            return r
    return None
