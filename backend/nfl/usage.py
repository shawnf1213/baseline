"""
NFL player usage — the share of his team's volume a player actually gets.

THIS IS THE HARD PART OF FOOTBALL, and it is what makes NFL props different from
MLB. A starting pitcher faces about 22 batters whatever happens; a receiver's
targets swing with the depth chart, the script and who else is healthy. So the
volume term is itself uncertain, and pretending otherwise is the main way an NFL
projection goes wrong.

SHRINKAGE IS NOT OPTIONAL AT 17 GAMES. MLB's MIN_STARTS=5 is 3% of a season;
five NFL games is nearly a third of one. A target share off four games is mostly
noise, so every rate here is blended toward the player's prior season and then
toward a position baseline, with the weight set by how much we have actually
seen. Same empirical-Bayes construction the tennis side uses, just with a much
heavier prior because the samples are so much smaller.

RECENCY MATTERS MORE THAN IN OTHER SPORTS. A role change — a trade, an injury
ahead of him, a coordinator change — makes early-season usage actively
misleading rather than merely noisy. Recent games are weighted up, and the
window is reported so a caller can see how thin it is.
"""

import logging
import os

log = logging.getLogger("baseline.nfl.usage")

# Games below this and the rate is a rumour. Kept low because the NFL season is
# short and the alternative is projecting nobody — but the shrinkage below does
# the real work of keeping a thin sample honest.
MIN_GAMES = 4

# Empirical-Bayes prior strength, in games.
#
# Set to 4, not 8, and the difference matters. The position prior is the ALL-WR
# average, which includes every WR3 on the field for twelve snaps — so it is a
# poor prior for a number-one receiver, and a heavy k drags every star toward a
# role he does not have. At k=4 a full 17-game season keeps ~81% of its own
# signal, which is the right balance when the prior itself is that crude.
#
# The first pass used k=8 AND passed the recency-weight sum (~6.5 for a full
# season) as the sample size, so the prior actually outweighed seventeen games:
# Jaxon Smith-Njigba's measured 0.360 target share came out at 0.254. Shrinkage
# should temper a thin sample, not overrule a complete one.
PRIOR_GAMES = 4.0

# Half-life for recency weighting, in games. A game 6 back counts half as much
# as last week's.
RECENCY_HALFLIFE = 6.0

# ── THE SEASON BOUNDARY IS NOT JUST SIX MORE GAMES ───────────────────────────
# Recency weighting above is SEASON-BLIND: it asks how many games back a row is
# and never which year it came from. Two weeks into a season that is close to
# ignoring the new year entirely. Measured on the 2026-09-20 board:
#
#     Mark Andrews        1 game in 2026, 17 in 2025  ->  12% of the weight 2026
#     Justin Jefferson    1 / 17                      ->  12%
#     Chris Olave         1 / 16                      ->  13%
#
# One current game carries weight 1.0 against a prior-season tail summing to
# ~6.5, so the projection the board posts in September is mostly last season.
# That is the real source of the +25% week-1 error the evidence gate was put in
# to paper over: a trade, a new depth chart or a coordinator change makes last
# year's role actively wrong, not merely stale.
#
# This multiplier discounts a prior-season game ON TOP OF recency, so the model
# uses both years with the current one weighing more. It self-corrects: by the
# time the current season has six games it dominates at any setting.
#
# DEFAULT 1.0 IS EXACTLY TODAY'S BEHAVIOUR. Nothing changes until this is set,
# which is deliberate — the value belongs to the backtest, not to taste.
PRIOR_SEASON_WEIGHT = float(
    os.getenv("NFL_PRIOR_SEASON_WEIGHT", "1.0") or 1.0)

# ── SNAP-SHARE ROLE ADJUSTMENT ───────────────────────────────────────────────
# A season target share lags a role change: when the man ahead of a receiver
# goes down, his snaps jump this week and his target share only catches up in
# the average weeks later. Recent snap share is the leading indicator.
#
# DAMPED TO HALF, and that is measured rather than cautious. Tested on 2,644
# player-weeks:
#     season target share alone   MAE 0.05208
#     x full snap ratio                0.05153
#     x HALF snap ratio                0.05114   <- best
# Extra snaps convert only partly into extra targets — a receiver on the field
# more is not targeted proportionally more — so the full ratio over-corrects.
# 45% of player-weeks show a real role change, and on those the gain is larger
# (0.04633 -> 0.04527).
SNAP_DAMP = 0.5
SNAP_RECENT_GAMES = 2
SNAP_RATIO_CLIP = (0.70, 1.40)

# Position baselines, used as the prior when a player has no history. Measured
# from 2023-2025 regular season, players with >= 8 games.
# ── ROLE-TIERED PRIORS (2026-09-06) ──────────────────────────────────────────
# The single POSITION_PRIOR below is the ALL-position average, and shrinking
# toward it is the largest remaining source of bias. Every player the board
# posts a line for is a STARTER, and the average WR is not: the population
# includes WR3s, WR4s and practice-squad callups.
#
# Measured on 2025 (players with 6+ games, joined to the depth chart):
#
#            rank 1    rank 2    rank 3    rank 4    ALL      prior in use
#   WR       0.2449    0.1736    0.1026    0.0696    0.1188   0.150
#   TE       0.1655    0.0758    0.0482    0.0319    0.0968   0.115
#   RB       0.1041    0.0592    0.0258    0.0328    0.0602   0.075
#
# So a WR1 was shrunk toward 0.150 when his real prior is 0.2449 — 39% low —
# and the eligible board is almost entirely rank 1-2. That is the -2.07 yard
# receiving bias: targets came in 3.5% light and yards per target was already
# exact (ratio 0.9912 vs 0.9653), so the error was volume, not efficiency.
#
# This is what a person reading the game does instinctively — a WR1 is not an
# "average WR" — and it DOES remove the bias: receiving went -2.16 -> -1.22.
#
# BUT IT MAKES MAE WORSE, so it ships OFF:
#
#                      flat prior   role prior
#   2025 receiving       +1.46%       +0.99%
#   2025 receptions      +1.21%       +0.61%
#   2024 receiving       +0.76%       -0.25%
#
# A bias-variance trade-off, and the measurement says the flat prior's
# OVER-shrinkage was paying for itself. Moving the prior next to the observed
# value means it barely pulls, so the estimate tracks a noisier raw share: less
# bias, more variance, worse net error. The remaining -1.22 yards is a price
# worth paying for the variance reduction.
#
# Kept, computed and reported (depth_rank / target_share_prior are on every
# usage dict) because the ROLE is real and useful to read — and because a
# better-fitted version might yet win. Two things would need to change: fit the
# tiers on BOTH seasons rather than 32-37 players from 2025, and try a PARTIAL
# blend toward the role tier instead of replacing the prior outright.
# Set NFL_ROLE_PRIOR=1 to enable.
ROLE_PRIOR_ENABLED = os.getenv("NFL_ROLE_PRIOR", "0").strip() in ("1", "true", "True")

ROLE_PRIOR_TARGET_SHARE = {
    "WR": {1: 0.2449, 2: 0.1736, 3: 0.1026, 4: 0.0696},
    "TE": {1: 0.1655, 2: 0.0758, 3: 0.0482, 4: 0.0319},
    "RB": {1: 0.1041, 2: 0.0592, 3: 0.0258, 4: 0.0328},
}

# Backfield carry share by depth rank. Receiving props run off target share
# above; a running back's carries do not, and using the target table for them
# would price a lead back's ground work off his role in the passing game.
ROLE_CARRY_SHARE = {"RB": {1: 0.480, 2: 0.220, 3: 0.100, 4: 0.050}}


# ── ROLE-CHANGE RESCALE (week 1 blindness) ───────────────────────────────────
# THE PROBLEM THIS SOLVES, measured against the live 2026-09-08 PrizePicks
# board (374 standard lines, 260 priced):
#
#     mean relative error vs the market      +25.3%
#     low-line receiving yards               +84.6%
#     low-line rush yards                    +77.0%
#     high lines (stars)                     -6.6% / +4.7%
#
# The same model backtests at -2.16 yards of bias in-season (2025, 1552 player
# weeks, beating both the season-average and last-4 baselines). It is not
# mis-specified; it is BLIND. In week 1 there is no current-season data at all,
# so every projection runs on last season's usage. A player who was a starter in
# 2025 and is WR4 in 2026 keeps his old volume, and the board fills with false
# overs on exactly the players whose role changed. Isaiah Williams — WR4 on the
# 2026 Jets — projected 31.0 receiving yards against an 8.5 line.
#
# The depth charts needed to see this ARE published before week 1 (nflverse
# depth_charts_2026 exists and is timestamped daily; only the PERFORMANCE
# datasets 404 until games are played), and depth_rank() already reads them.
# The information was sitting there unconsumed.
#
# DEMOTIONS ONLY — the asymmetry is the whole result. Measured on the same 107
# board rows that carry a depth rank in both seasons:
#
#     no rescale                     MAE 42.9%   bias +25.3%
#     symmetric (up and down)        MAE 50.8%   bias +31.7%   <- WORSE
#     demotions only                 MAE 40.5%   bias +18.3%   <- kept
#     promotions only                MAE 53.2%   bias +38.7%   <- much worse
#
# Scaling promotions UP is what breaks it: Xavier Hutchinson (WR4 -> WR2) went
# from +86% to +364%. That is football-sensible rather than a fitting artefact.
# A player who loses his role will not repeat last year's volume — the snaps are
# simply gone. A player who gains one does not inherit the departed starter's
# touches wholesale; he has to earn them, and his efficiency at the new role is
# unproven. So the factor is capped at 1.0 and can only ever cut.
#
# VOLUME ONLY, never efficiency. A demoted receiver sees fewer targets; he does
# not become worse at catching them. Scaling yards-per-target would be inventing
# a skill decline the depth chart says nothing about.
#
# This is a PARTIAL fix and is documented as one: it removes roughly a third of
# the week-1 bias, leaving +18.3%. It is not a licence to post — see
# board.REQUIRE_CURRENT_SEASON, which is what actually keeps the board honest
# until real 2026 usage exists.
ROLE_RESCALE_ENABLED = os.getenv("NFL_ROLE_RESCALE", "1").strip() in (
    "1", "true", "True", "yes", "on")


def role_change_factor(player: str, season: int = None) -> dict:
    """How much of last season's VOLUME this player's current role still supports.

    Returns {"factor": float <= 1.0, "from": rank, "to": rank, "basis": str}.
    factor 1.0 means "no demotion detected" — same rank, a promotion, or one of
    the two ranks unknown. Never raises, never returns > 1.0.
    """
    out = {"factor": 1.0, "from": None, "to": None, "basis": "no depth data"}
    try:
        if not ROLE_RESCALE_ENABLED:
            out["basis"] = "role rescale disabled"
            return out
        from .client import current_season
        cur = season or current_season()
        pos_now, rank_now = depth_rank(player, season=cur)
        pos_then, rank_then = depth_rank(player, season=cur - 1)
        if not (rank_now and rank_then):
            return out
        pos = pos_now or pos_then
        tbl = ROLE_PRIOR_TARGET_SHARE.get(pos)
        if not tbl:
            return out
        a, b = tbl.get(int(rank_then)), tbl.get(int(rank_now))
        if not (a and b):
            return out
        out.update({"from": int(rank_then), "to": int(rank_now)})
        if int(rank_now) <= int(rank_then):
            out["basis"] = (f"{pos}{rank_then} -> {pos}{rank_now}: no demotion, "
                            f"volume left as measured")
            return out
        out["factor"] = round(min(b / a, 1.0), 4)
        out["basis"] = (f"{pos}{rank_then} -> {pos}{rank_now}: prior-season volume "
                        f"scaled to {out['factor']:.0%} of measured")
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl role_change_factor(%r) failed: %s", player, str(exc)[:120])
        return out

POSITION_PRIOR = {
    "WR":  {"target_share": 0.150, "catch_rate": 0.640, "yards_per_target": 8.10,
            "carry_share": 0.010, "yards_per_carry": 6.20},
    "TE":  {"target_share": 0.115, "catch_rate": 0.700, "yards_per_target": 7.40,
            "carry_share": 0.005, "yards_per_carry": 3.50},
    "RB":  {"target_share": 0.075, "catch_rate": 0.760, "yards_per_target": 6.20,
            "carry_share": 0.480, "yards_per_carry": 4.30},
    "QB":  {"target_share": 0.000, "catch_rate": 0.000, "yards_per_target": 0.00,
            "carry_share": 0.090, "yards_per_carry": 4.60},
}


_DEPTH_CACHE = {}


# gsis_id -> ESPN id, filled as a side effect of building the depth-chart cache.
# Module level, so it survives across players the way _DEPTH_CACHE does.
_ESPN_ID = {}


def espn_id(player: str, season: int = None) -> str:
    """ESPN's id for a player, or "". The only key that reaches a headshot.

    Depends on depth_rank having populated the cache, so it calls it first —
    cheap after the first player, since the chart is cached per season.
    """
    try:
        from . import client
        season = season or client.current_season()
        depth_rank(player, season=season)
        gid = _name_to_gsis(season).get(_norm_player(player))
        return _ESPN_ID.get(gid, "") if gid else ""
    except Exception:  # noqa: BLE001 — a missing photo must not cost a profile
        return ""


def _season_span(hist) -> str:
    """"2025", or "2025-26" when the sample straddles two seasons. "" if unknown.

    Never raises — a missing season column must cost a label, not a profile.
    """
    try:
        if "season" not in getattr(hist, "columns", []):
            return ""
        ys = sorted({int(y) for y in hist["season"].dropna().unique()})
        if not ys:
            return ""
        return (str(ys[0]) if len(ys) == 1
                else f"{ys[0]}-{str(ys[-1])[-2:]}")
    except Exception:  # noqa: BLE001
        return ""


def depth_rank(player: str, season: int = None, before_week: int = None):
    """(position abbreviation, depth rank) from the depth chart, or (None, None).

    Dated, so the backtest can ask what the chart said BEFORE a given week
    rather than what it says now — a week-6 projection must not know that a
    player was promoted in week 12.
    """
    from . import client
    import pandas as pd
    season = season or client.current_season()
    key = (season, before_week)
    if key not in _DEPTH_CACHE:
        try:
            dc = client.load("depth_charts", season)
            if not len(dc):
                # Preseason: the current year has no chart yet, so use last
                # year's rather than losing the role entirely.
                dc = client.load("depth_charts", season - 1)
            if not len(dc):
                _DEPTH_CACHE[key] = {}
            else:
                d = dc.copy()
                # TWO SCHEMAS. nflverse changed the depth-chart format: 2024 and
                # earlier carry season/week/depth_team/position, 2025 onward
                # carry dt/pos_rank/pos_abb. Handling only the newer one made
                # every pre-2025 backtest silently fall back to the flat prior,
                # which is exactly the bias this feature exists to remove.
                if "pos_rank" in d.columns:                       # 2025+ schema
                    d["dt"] = pd.to_datetime(d["dt"], errors="coerce", utc=True)
                    if before_week:
                        cut = pd.Timestamp(client._week_start(season, before_week), tz="UTC")
                        d = d[d["dt"] <= cut]
                    # ONE PLAYER, SEVERAL POSITIONS, ONE TIMESTAMP. The chart
                    # lists a starting back at RB1 and AGAIN at KR3 and PR3,
                    # every row stamped with the same dt — so sorting by dt
                    # alone left the winner to file order, and 183 players took
                    # a special-teams slot as their role. Chuba Hubbard, RB1,
                    # came back "KR3".
                    #
                    # That is not merely a wrong badge. depth_rank keys the role
                    # priors in player_usage, so an RB1 was being priced with
                    # whatever prior a kick returner has — i.e. none.
                    #
                    # Keep the latest snapshot per player, then prefer offence
                    # or defence over special teams, and the best rank within
                    # that. A genuine kicker, punter or long snapper has nothing
                    # but special-teams rows and keeps his.
                    d = d[d["dt"] == d.groupby("gsis_id")["dt"].transform("max")]
                    if "pos_grp" in d.columns:
                        d = d.assign(
                            _st=(d["pos_grp"].astype(str)
                                 == "Special Teams").astype(int),
                            _rk=pd.to_numeric(d["pos_rank"], errors="coerce")
                                  .fillna(99))
                        d = (d.sort_values(["_st", "_rk"])
                              .groupby("gsis_id").head(1))
                    else:
                        d = d.groupby("gsis_id").tail(1)
                    rank_col, pos_col = "pos_rank", "pos_abb"
                else:
                    # <=2024 SCHEMA: NOT USABLE AS A ROLE RANK, and returning it
                    # anyway is worse than returning nothing.
                    #
                    # depth_team takes only '1','2','3', and 150 distinct
                    # receivers carry depth_team == '1' across 2024 — about 4.7
                    # per team. It marks "first string" at several WR spots, not
                    # the WR1. 2025's pos_rank is far finer: 53 distinct players
                    # at WR/rank 1, ~1.6 per team.
                    #
                    # Mixing them collapsed the fitted WR1 target-share prior
                    # from 0.2449 (2025 alone) to 0.1684 (both seasons) — not
                    # sampling noise, just two different definitions of "1"
                    # averaged together. It is also why role priors measured
                    # WORST on 2024: those ranks were nearly random.
                    #
                    # So the coarse schema yields no rank. A caller gets None
                    # and falls back to the flat prior, which is honest.
                    log.info("nfl depth_rank: %s uses the coarse pre-2025 "
                             "depth-chart schema — no usable role rank", season)
                    _DEPTH_CACHE[key] = {}
                    return (None, None)
                if not rank_col or not pos_col or not len(d):
                    _DEPTH_CACHE[key] = {}
                else:
                    tbl = {}
                    for _, r in d.iterrows():
                        gid = r.get("gsis_id")
                        if not gid:
                            continue
                        try:
                            rk = int(r.get(rank_col))
                        except (TypeError, ValueError):
                            continue
                        tbl[gid] = (r.get(pos_col), rk)
                        # The ESPN id travels on the same row, and it is the
                        # only identifier that reaches a headshot. Harvested
                        # here rather than in a second pass over 500k rows.
                        eid = r.get("espn_id")
                        if eid is not None and str(eid) not in ("", "nan"):
                            try:
                                _ESPN_ID[gid] = str(int(float(eid)))
                            except (TypeError, ValueError):
                                pass
                    _DEPTH_CACHE[key] = tbl
        except Exception:  # noqa: BLE001
            log.exception("nfl depth_rank failed")
            _DEPTH_CACHE[key] = {}
    table = _DEPTH_CACHE[key]
    if not table:
        return (None, None)
    # players are keyed by gsis_id; resolve the name once per season
    from . import client as _c
    ids = _name_to_gsis(season)
    gid = ids.get(_norm_player(player))
    return table.get(gid, (None, None)) if gid else (None, None)


_NAME_ID_CACHE = {}


def _name_to_gsis(season: int) -> dict:
    if season in _NAME_ID_CACHE:
        return _NAME_ID_CACHE[season]
    from . import client
    out = {}
    try:
        # BOTH SEASONS, current winning on a clash. Loading only the current
        # frame and falling back when it is ENTIRELY empty is the wrong test
        # once a season starts: in week 1 of 2026 it held two teams, so every
        # player outside NE and SEA was missing from this map and depth_rank
        # returned (None, None) for almost the league.
        import pandas as _pd
        cur = client.load("stats_player_week", season)
        prev = client.load("stats_player_week", season - 1)
        frames = [f for f in (prev, cur) if len(f)]   # cur last => cur wins
        wk = _pd.concat(frames, ignore_index=True) if frames else cur
        if len(wk):
            # Keyed on the NORMALISED name so depth_rank resolves whatever the
            # caller typed — the same folding player_usage does.
            out = {_norm_player(n): i
                   for n, i in zip(wk["player_display_name"], wk["player_id"])}
    except Exception:  # noqa: BLE001
        log.exception("nfl name->gsis failed")
    _NAME_ID_CACHE[season] = out
    return out


def _shrink(obs, n, prior, k=PRIOR_GAMES):
    """Empirical-Bayes blend of an observed rate toward a prior."""
    if obs is None or not n or n <= 0:
        return prior
    return ((obs * n) + (prior * k)) / (n + k)


def _weighted(vals, weights):
    tot = sum(weights)
    return (sum(v * w for v, w in zip(vals, weights)) / tot) if tot else None



def _norm_player(s) -> str:
    """Fold a player name for matching: accents, punctuation, case, suffixes.

    Deliberately the same rules as nfl.lines._norm and nfl.queries._norm_name,
    so a name that resolves on the board resolves in a command too.
    """
    import re
    import unicodedata
    if s is None:
        return ""
    s = str(s)
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    toks = [t for t in s.split()
            if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks).strip()


def player_usage(player: str, season: int = None, position: str = None,
                 before_week: int = None) -> dict:
    """A player's usage and efficiency rates, shrunk and recency-weighted.

    Returns {} when there is not enough history to say anything. Never raises.

    The returned `games` and `window` are not decoration — a caller showing a
    projection built on five games should be able to say so.

    `before_week` restricts the CURRENT season to weeks strictly before it. It
    exists for backtesting: a projection evaluated against week 9 must be built
    only from weeks 1-8, or the test measures nothing but hindsight. Prior
    seasons are always fully available, which is what a real Sunday looks like.
    """
    from . import client
    import pandas as pd
    try:
        season = season or client.current_season()
        frames = []
        # Current season first, then the prior one. In August the current season
        # has not started, so the prior season IS the sample — that is normal,
        # not a fallback, and it is reported in `window`.
        for yr, tag in ((season, "current"), (season - 1, "prior")):
            df = client.load("stats_player_week", yr)
            if not len(df):
                continue
            # NORMALISED match, not an exact string compare. `== player` meant
            # "lamar jackson" found nothing while "Lamar Jackson" worked, and
            # the user-facing message for that was "no usable game log" — which
            # reads as "this player does not exist" rather than "check your
            # capitalisation". Nobody types a slash command in title case.
            d = df[(df.get("season_type") == "REG")
                   & (df["player_display_name"].astype(str).map(_norm_player)
                      == _norm_player(player))]
            if before_week and tag == "current":
                d = d[d["week"] < before_week]
            if len(d):
                frames.append(d.assign(_src=tag, _season=yr))
        if not frames:
            log.info("nfl usage: no rows for %r", player)
            return {}
        hist = pd.concat(frames, ignore_index=True)
        hist = hist.sort_values(["_season", "week"])
        n = len(hist)
        if n < MIN_GAMES:
            log.info("nfl usage: %s has %d game(s) (< %d)", player, n, MIN_GAMES)
            return {}

        pos = position or (hist["position"].dropna().iloc[-1]
                           if hist["position"].notna().any() else "WR")
        prior = dict(POSITION_PRIOR.get(pos, POSITION_PRIOR["WR"]))
        # ROLE-TIERED TARGET-SHARE PRIOR. The flat position prior is the
        # ALL-position average and every player the board posts is a starter,
        # so shrinking a WR1 toward the all-WR mean drags him ~39% low. See
        # ROLE_PRIOR_TARGET_SHARE for the measured tiers.
        _dpos, _drank = depth_rank(player, season=season, before_week=before_week)
        _tier = ROLE_PRIOR_TARGET_SHARE.get(pos, {}).get(int(_drank)) if _drank else None
        if _tier and ROLE_PRIOR_ENABLED:
            prior["target_share"] = _tier

        # Recency weights: most recent game weight 1, halving every
        # RECENCY_HALFLIFE games back, then a prior-season game discounted
        # again by PRIOR_SEASON_WEIGHT — see the note on that constant.
        order = list(range(n - 1, -1, -1))          # 0 = most recent
        _src = hist["_src"].tolist()
        w = [0.5 ** (i / RECENCY_HALFLIFE)
             * (1.0 if s == "current" else PRIOR_SEASON_WEIGHT)
             for i, s in zip(order, _src)]

        def wsum(col):
            if col not in hist:
                return 0.0
            vals = hist[col].fillna(0).tolist()
            return sum(v * ww for v, ww in zip(vals, w))

        wn = sum(w)
        tgt, rec = wsum("targets"), wsum("receptions")
        rec_yds = wsum("receiving_yards")
        car, rush_yds = wsum("carries"), wsum("rushing_yards")
        att, pass_yds = wsum("attempts"), wsum("passing_yards")
        cmp_ = wsum("completions")
        pass_tds, ints = wsum("passing_tds"), wsum("passing_interceptions")

        # target_share is precomputed per game by nflverse; average it the same
        # recency-weighted way rather than recomputing from team totals.
        ts_vals = hist.get("target_share")
        ts = (_weighted(ts_vals.fillna(0).tolist(), w)
              if ts_vals is not None else None)

        # ── ROLE WITHIN THE OFFENCE ─────────────────────────────────────────
        # Target share alone says how OFTEN the ball goes his way; it says
        # nothing about WHERE. A slot receiver and a field-stretcher can share
        # 22% of targets and project to very different yardage, and the
        # difference shows up in air yards rather than in target count.
        #
        #   aDOT             air yards per target — his route depth
        #   air_yards_share  his share of the team's total air yards
        #   wopr             1.5*target_share + 0.7*air_yards_share, the
        #                    standard composite of volume AND depth
        #   racr             receiving yards per air yard — efficiency at
        #                    converting depth into production
        #
        # All four are already computed per game by nflverse, so this reads
        # them rather than re-deriving from play-by-play. Recency-weighted like
        # every other rate here. REPORTED, not yet consumed by the projection —
        # whether they improve it is a backtest question, not an assumption.
        ays_vals = hist.get("air_yards_share")
        ays = (_weighted(ays_vals.fillna(0).tolist(), w)
               if ays_vals is not None else None)
        rec_air = wsum("receiving_air_yards")
        adot = (rec_air / tgt) if tgt else None
        racr_vals = hist.get("racr")
        racr = (_weighted(racr_vals.fillna(0).tolist(), w)
                if racr_vals is not None else None)
        wopr_vals = hist.get("wopr")
        wopr = (_weighted(wopr_vals.fillna(0).tolist(), w)
                if wopr_vals is not None else None)

        out = {
            # The CANONICAL name from the data, not what the caller typed.
            # Returning the input meant every downstream lookup keyed off
            # "lamar jackson" and missed — depth_rank included, so /nflplayer
            # showed a player with no role.
            "player": (str(hist["player_display_name"].iloc[-1])
                       if "player_display_name" in hist.columns else player),
            "position": pos,
            "games": n,
            "window": ("current season" if (hist["_src"] == "current").all()
                       else "current + prior season"
                       if (hist["_src"] == "current").any()
                       else "prior season only"),
            # The YEARS the sample actually came from. "prior season only" is
            # the right label for the model — it gates the evidence check below
            # — but it tells a reader nothing they can check. The website shows
            # this instead: a season, which is a fact, not a relative phrase
            # whose meaning moves every September.
            "seasons": _season_span(hist),
            "effective_n": round(wn, 1),
            # Raw, before shrinkage — shown so the shrink is visible.
            "raw": {
                "targets_per_game": round(tgt / wn, 2) if wn else None,
                "catch_rate": round(rec / tgt, 4) if tgt else None,
                "yards_per_target": round(rec_yds / tgt, 3) if tgt else None,
                "carries_per_game": round(car / wn, 2) if wn else None,
                "yards_per_carry": round(rush_yds / car, 3) if car else None,
                "pass_att_per_game": round(att / wn, 2) if wn else None,
                "yards_per_attempt": round(pass_yds / att, 3) if att else None,
                "completion_pct": round(cmp_ / att, 4) if att else None,
                "yards_per_completion": round(pass_yds / cmp_, 3) if cmp_ else None,
                "target_share": round(ts, 4) if ts else None,
            },
        }
        # ── snap-share role adjustment ──────────────────────────────────
        snap = _snap_ratio(player, season, before_week)
        if snap.get("ratio"):
            # REPORTING the snap numbers was gated on target share, so an
            # early-down back who is never thrown to had no snap share at all —
            # and snaps have nothing to do with targets. The ADJUSTMENT still
            # is: it damps a target share, so it needs one to damp.
            out["snap_ratio"] = round(snap["ratio"], 3)
            out["snap_recent"] = round(snap["recent"], 3)
            out["snap_season"] = round(snap["season"], 3)
            if ts:
                ts = ts * (1.0 + SNAP_DAMP * (snap["ratio"] - 1.0))
                out["raw"]["target_share_pre_snap"] = out["raw"].get("target_share")

        # Shrunk rates — these are what the projection uses.
        # n here is REAL GAMES, not the recency-weight sum. The weights decide
        # how the average is computed; they must not also shrink the sample.
        out["target_share"] = round(
            _shrink(ts, n, prior["target_share"]), 4)
        out["catch_rate"] = round(
            _shrink(rec / tgt if tgt else None, tgt, prior["catch_rate"],
                    k=25.0), 4)              # k in TARGETS, not games
        out["yards_per_target"] = round(
            _shrink(rec_yds / tgt if tgt else None, tgt,
                    prior["yards_per_target"], k=25.0), 3)
        out["yards_per_carry"] = round(
            _shrink(rush_yds / car if car else None, car,
                    prior["yards_per_carry"], k=40.0), 3)   # k in CARRIES
        out["carries_per_game"] = round(car / wn, 2) if wn else 0.0
        out["targets_per_game"] = round(tgt / wn, 2) if wn else 0.0
        out["pass_att_per_game"] = round(att / wn, 2) if wn else 0.0
        out["yards_per_attempt"] = round(
            _shrink(pass_yds / att if att else None, att, 7.05, k=120.0), 3)
        # QB accuracy, kept SEPARATE from yards per attempt on purpose.
        # Completion % is far more stable week to week than Y/A — the yardage
        # swings on a couple of deep shots, the accuracy does not — and a
        # defence suppresses the two differently. Splitting them lets the
        # opponent adjustment hit the term it actually moves.
        out["completion_pct"] = round(
            _shrink(cmp_ / att if att else None, att, 0.650, k=120.0), 4)
        out["yards_per_completion"] = round(
            _shrink(pass_yds / cmp_ if cmp_ else None, cmp_, 10.85, k=80.0), 3)
        out["pass_td_rate"] = round(pass_tds / att, 4) if att else 0.0
        out["int_rate"] = round(ints / att, 4) if att else 0.0

        # Role block — see the aDOT comment above. Shrunk where a rate, raw
        # where a share, and None rather than 0.0 when the player has no
        # receiving role at all (a QB's aDOT is not zero, it is undefined).
        out["depth_rank"] = int(_drank) if _drank else None
        out["target_share_prior"] = round(prior["target_share"], 4)
        out["role"] = {
            "adot": round(adot, 2) if adot else None,
            "air_yards_share": round(ays, 4) if ays else None,
            "wopr": round(wopr, 4) if wopr else None,
            # RACR is receiving yards / air yards, so it is only meaningful
            # when air yards are positive and non-trivial. A running back
            # catching screens behind the line has an aDOT near zero and can go
            # NEGATIVE — Achane came out at -3.439, which is not an efficiency,
            # it is a division artefact. Gated to a real downfield role.
            "racr": (round(racr, 3) if (racr and adot and adot >= 1.0) else None),
            "air_yards_per_game": round(rec_air / wn, 1) if wn and rec_air else None,
        }

        # ── role-change rescale ─────────────────────────────────────────────
        # ONLY when every game in the window is from a PRIOR season. Once the
        # player has current-season games, those games ARE the evidence about
        # his current role and the depth chart adds nothing — applying it then
        # would cut volume the player has already demonstrably produced. This
        # is also what keeps the backtest untouched: it runs with before_week
        # inside a single season, so the window is never prior-season-only and
        # this branch cannot fire. See role_change_factor for the measurements.
        if out["window"] == "prior season only":
            rc = role_change_factor(player, season=season)
            f = rc.get("factor", 1.0)
            out["role_change"] = rc
            if f < 1.0:
                for k in ("target_share", "targets_per_game", "carries_per_game",
                          "pass_att_per_game"):
                    if isinstance(out.get(k), (int, float)):
                        out[k] = round(out[k] * f, 4)
                log.info("nfl usage: %s %s", player, rc["basis"])
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl usage failed for %r: %s", player, exc)
        return {}


# ── A KNOWN-WRONG INPUT, DELIBERATELY LEFT WRONG (2026-09-06) ────────────────
# team_tendency counts plays as attempts + carries. Pass ATTEMPTS EXCLUDE SACKS,
# and a sack is a dropback, so every team's play count is ~2.3 low. Measured
# against play-by-play: MIN 52.59 vs 55.5, KC 59.65 vs 61.6, BUF 61.29 vs 63.3 —
# a uniform -2.3, exactly the league sack rate. Setting the flag also fixes the
# pass RATE, which excluded sacks from the numerator while the module's own
# NEUTRAL comment defines pass rate as including them.
#
# RESOLVED 2026-09-06 — the counterpart error was found, so this is now ON.
# Correcting it ALONE made the model worse:
#
#                    MAE 2025      MAE 2024      bias 2025
#   receiving_yards  -1.64%        -1.52%        +0.70 -> +2.61
#   receptions       -1.82%        -1.79%        +0.09 -> +0.27
#   rush_yards       +0.04%        +0.08%        unchanged
#
# The model was unbiased BECAUSE two errors cancelled: volume ~4% low, and
# something in the receiving chain ~4% high. Fixing one unmasks the other, and
# the projection then runs 2.6 yards hot. The compensating term has not been
# found — target_share shrinkage and yards_per_target are the candidates.
#
# The counterpart was TARGETS_PER_DROPBACK in volume.py: the receiving chain
# multiplied target_share by pass ATTEMPTS as though every attempt produced a
# target. It does not — throwaways, spikes and batted balls do not. Measured on
# 2025: 30.84 targets per 32.25 attempts (0.9563) per 34.64 dropbacks (0.8904).
# So targets were 4.4% high, cancelling the 4% low play count. With both fixed
# the two stop hiding each other, and this flag defaults ON.
COUNT_SACKS_AS_PLAYS = os.getenv("NFL_COUNT_SACKS", "1").strip() in ("1", "true", "True")


def team_tendency(team: str, season: int = None) -> dict:
    """A team's own pace and pass rate, for feeding volume.team_volume().

    Falls back to league-neutral when the team has no rows — a new season before
    week 1 is the normal case for that, not an error.
    """
    from . import client
    try:
        season = season or client.current_season()
        for yr in (season, season - 1):
            df = client.load("stats_team_week", yr)
            if not len(df):
                continue
            d = df[(df.get("season_type") == "REG") & (df.get("team") == team)]
            if not len(d):
                continue
            att = float(d.get("attempts").fillna(0).sum())
            car = float(d.get("carries").fillna(0).sum())
            # SACKS ARE PASS PLAYS. `attempts` excludes them, so plays computed
            # as attempts+carries omitted ~2.3 dropbacks a game and every team's
            # volume came out ~4% low. Measured against play-by-play: model MIN
            # 52.59 vs truth 55.5, KC 59.65 vs 61.6, BUF 61.29 vs 63.3 — a
            # uniform -2.3, which is exactly the league sack rate.
            #
            # It skewed the pass RATE the same way, and inconsistently with the
            # module's own NEUTRAL, whose comment says pass rate is the "share of
            # plays that are pass attempts (incl. sacks)". The two halves of the
            # volume model were using different definitions of a pass play.
            # OFF BY DEFAULT — see COUNT_SACKS_AS_PLAYS below. The fix is
            # correct; shipping it alone measurably degrades the model.
            sk = (float(d.get("sacks_suffered").fillna(0).sum())
                  if (COUNT_SACKS_AS_PLAYS and "sacks_suffered" in d) else 0.0)
            g = len(d)
            plays = att + car + sk
            if not g or plays <= 0:
                continue
            return {"team": team, "season": yr, "games": g,
                    "plays_per_game": round(plays / g, 2),
                    "pass_rate": round((att + sk) / plays, 4),
                    "sacks_per_game": round(sk / g, 2)}
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl team_tendency failed for %r: %s", team, str(exc)[:140])
        return {}


def _snap_ratio(player: str, season: int = None, before_week: int = None) -> dict:
    """Recent snap share against this player's own season snap share.

    Above 1.0 means his role has grown lately — the man ahead is hurt, or the
    offence has moved him up. Below 1.0 means the reverse.

    Compared against HIS OWN baseline, not the league's, so a rotational back
    who is always at 45% reads as 1.0 rather than as permanently diminished.
    Clipped, because a player returning from injury can show a ratio of 6 that
    means "he missed a game", not "his role sextupled".
    """
    from . import client
    try:
        season = season or client.current_season()
        rows = []
        for yr in (season, season - 1):
            sc = client.load("snap_counts", yr, ext="csv")
            if not len(sc):
                continue
            d = sc[(sc.get("game_type") == "REG") & (sc.get("player") == player)]
            if before_week is not None and yr == season:
                d = d[d["week"] < before_week]
            if len(d):
                rows.append(d.sort_values("week"))
            if rows and len(rows[0]) >= 4:
                break            # current season is enough
        if not rows:
            return {}
        import pandas as pd
        d = pd.concat(rows, ignore_index=True)
        pct = d["offense_pct"].dropna()
        if len(pct) < 3:
            return {}
        season_pct = float(pct.mean())
        recent = float(pct.tail(SNAP_RECENT_GAMES).mean())
        if season_pct <= 0.05:
            return {}
        ratio = max(SNAP_RATIO_CLIP[0], min(SNAP_RATIO_CLIP[1], recent / season_pct))
        return {"ratio": ratio, "recent": recent, "season": season_pct,
                "games": int(len(pct))}
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl snap ratio failed for %r: %s", player, str(exc)[:140])
        return {}


# ── FEATURE 2: PRODUCTION BY DEFENCE QUALITY ─────────────────────────────────
# A season average hides WHO it was earned against. Two receivers at 60 yards a
# game are not the same bet if one did it against bottom-ten pass defences and
# the other against top-ten. This splits a player's own game log by the quality
# of the defence he faced, so the projection's opponent adjustment can be
# checked against what he has actually done rather than assumed to apply evenly.
#
# THE RATING USED IS THE ONE KNOWABLE BEFORE THAT GAME — the PRIOR season's,
# same discipline as the backtest. Rating a week-3 game with the defence's
# finished season would be scoring it with information nobody had at kickoff.
#
# Tier edges are on the league-relative rating, where 1.000 is average and ABOVE
# 1.000 means the defence ALLOWS MORE (i.e. is worse):
DEF_TIER_TOUGH = 0.97      # allows 3%+ less than average
DEF_TIER_SOFT = 1.03       # allows 3%+ more

# Which defensive rating governs which prop.
_PROP_DEF_FIELD = {
    "receiving_yards": "pass_yds_per_att",
    "receptions": "completion_pct",
    "rush_yards": "rush_yds_per_att",
    "pass_yards": "pass_yds_per_att",
}
# Per-game production column and its volume driver, so the split separates
# "he saw fewer targets" from "he did less with them".
_PROP_COLS = {
    "receiving_yards": ("receiving_yards", "targets"),
    "receptions": ("receptions", "targets"),
    "rush_yards": ("rushing_yards", "carries"),
    "pass_yards": ("passing_yards", "attempts"),
}


def defense_splits(player: str, prop: str = "receiving_yards",
                   season: int = None, before_week: int = None) -> dict:
    """A player's production against tough / average / soft defences.

    Returns {} when the player has no usable history. Never raises.

    `resilience` is the headline: his RATE against tough defences divided by his
    rate against everyone. Below 1.0 means his production is padded by soft
    matchups; near 1.0 means it holds up. It is a rate, not a per-game total, so
    it is not contaminated by his team simply throwing less in hard games.
    """
    from . import client, ratings as _rat
    import pandas as pd
    try:
        season = season or client.current_season()
        prod_col, vol_col = _PROP_COLS.get(prop, (None, None))
        field = _PROP_DEF_FIELD.get(prop)
        if not prod_col or not field:
            return {}
        rows = []
        for yr in (season, season - 1):
            df = client.load("stats_player_week", yr)
            if not len(df):
                continue
            d = df[(df.get("season_type") == "REG")
                   & (df["player_display_name"].astype(str).map(_norm_player)
                      == _norm_player(player))]
            if before_week and yr == season:
                d = d[d["week"] < before_week]
            if not len(d):
                continue
            # Ratings as they were KNOWABLE before this season started.
            rt = _rat.team_ratings([yr - 1])
            for _, r in d.iterrows():
                opp = r.get("opponent_team")
                rec = (rt.get(opp) or {}).get("defense") if opp else None
                if not rec:
                    continue
                rows.append({
                    "opp": opp,
                    "def_rating": float(rec.get(field, 1.0)),
                    "prod": float(r.get(prod_col) or 0.0),
                    "vol": float(r.get(vol_col) or 0.0) if vol_col else 0.0,
                })
        if len(rows) < 4:
            return {}
        f = pd.DataFrame(rows)

        def agg(sel, label):
            g = f[sel]
            if not len(g):
                return None
            v = float(g["vol"].sum())
            return {"games": int(len(g)),
                    "per_game": round(float(g["prod"].mean()), 2),
                    "volume_per_game": round(float(g["vol"].mean()), 2),
                    "per_unit": round(float(g["prod"].sum()) / v, 3) if v else None,
                    "mean_def_rating": round(float(g["def_rating"].mean()), 4)}

        tough = agg(f["def_rating"] <= DEF_TIER_TOUGH, "tough")
        avg = agg((f["def_rating"] > DEF_TIER_TOUGH) & (f["def_rating"] < DEF_TIER_SOFT), "average")
        soft = agg(f["def_rating"] >= DEF_TIER_SOFT, "soft")
        all_v = float(f["vol"].sum())
        overall_rate = (float(f["prod"].sum()) / all_v) if all_v else None
        resilience = (tough["per_unit"] / overall_rate
                      if (tough and tough.get("per_unit") and overall_rate) else None)
        # SHRUNK TOWARD 1.0 BY SAMPLE SIZE, and this is the value to consume.
        # A tough-defence tier holds 2-5 games in a season and a half, so the raw
        # ratio swings wildly: Chase came out at 1.548 on five games and Kupp at
        # 0.522 on two. Neither is a stable property of the player, and feeding
        # the raw number into a projection would inject precisely the noise the
        # rest of this module guards against.
        #
        # k IS NOT FITTED. 8 games is a stated prior, chosen so a 4-game tier
        # carries a third of its raw signal, and it needs a proper fit against
        # rest-of-season outcomes before anything CONSUMES this. Both values are
        # returned so the fit can be run without changing callers.
        RESILIENCE_PRIOR_GAMES = 8.0
        n_tough = (tough or {}).get("games") or 0
        resilience_shrunk = (
            1.0 + (resilience - 1.0) * (n_tough / (n_tough + RESILIENCE_PRIOR_GAMES))
            if resilience is not None else None)
        return {"player": player, "prop": prop, "games": int(len(f)),
                "overall_per_unit": round(overall_rate, 3) if overall_rate else None,
                "tough": tough, "average": avg, "soft": soft,
                "resilience_raw": round(resilience, 3) if resilience is not None else None,
                "resilience": (round(resilience_shrunk, 3)
                               if resilience_shrunk is not None else None),
                "resilience_n": n_tough}
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl defense_splits failed for %r: %s", player, exc)
        return {}


# ── FEATURES 3-4: COVERAGE SPLITS ────────────────────────────────────────────
# WHAT THE DATA SUPPORTS, AND WHAT IT DOES NOT.
#
# pbp_participation gives the 11 defenders on every play, and man/zone on 99.9%
# of targets (17,570 of 17,582 in 2025). It does NOT give coverage ASSIGNMENT —
# there is no field saying which defender covered which receiver. So a true
# shadow model ("their CB1 travels with our WR1") would rest on an assumption,
# not an observation, and is deliberately not built here.
#
# What IS observable is the effect that assumption is a proxy for. Shadowing
# matters in MAN coverage; in zone a receiver is passed between defenders and
# the individual matchup largely dissolves. So a receiver's man-vs-zone split,
# crossed with how much man a defence actually plays, captures the same signal
# from measured quantities:
#
#     league 2025: 12,154 zone targets (69%) vs 5,416 man (31%)
#     a top receiver carries 170-208 labelled targets, so both tiers are real
#
# A receiver who is materially worse against man, facing a man-heavy defence, is
# the matchup you were reaching for — and it is checkable rather than assumed.
_COVERAGE_MIN_TARGETS = 12

# Joined targets-with-coverage, cached per (season, before_week). The merge is
# 45k participation rows against 17k targets and it does NOT change within a
# call — recomputing it per player made a board scan and the backtest
# unusable (~10,000 merges). Keyed on before_week too, so the backtest's
# week-gated views stay separate from the full-season one.
_COV_CACHE = {}


def _coverage_frame(season: int, before_week: int = None):
    """Targets joined to man/zone, cached. Empty frame on any failure."""
    from . import client
    import pandas as pd
    key = (season, before_week)
    if key in _COV_CACHE:
        return _COV_CACHE[key]
    try:
        pbp = client.load("play_by_play", season)
        part = client.load("pbp_participation", season)
        if not len(pbp) or not len(part):
            _COV_CACHE[key] = pd.DataFrame()
            return _COV_CACHE[key]
        if before_week and "week" in pbp.columns:
            pbp = pbp[pbp["week"] < before_week]
        if not len(pbp):
            _COV_CACHE[key] = pd.DataFrame()
            return _COV_CACHE[key]
        tg = pbp[pbp["play_type"] == "pass"][
            ["game_id", "play_id", "receiver_player_name", "yards_gained",
             "complete_pass", "air_yards", "defteam"]]
        pt = part[["nflverse_game_id", "play_id", "defense_man_zone_type"]]
        m = tg.merge(pt, left_on=["game_id", "play_id"],
                     right_on=["nflverse_game_id", "play_id"], how="inner")
        _COV_CACHE[key] = m
        return m
    except Exception:  # noqa: BLE001
        log.exception("nfl coverage frame failed")
        _COV_CACHE[key] = pd.DataFrame()
        return _COV_CACHE[key]


def coverage_splits(player: str, season: int = None,
                    before_week: int = None) -> dict:
    """A receiver's production against MAN vs ZONE coverage.

    Joins play-by-play targets to pbp_participation on (game_id, play_id).
    Returns {} when the player has too few labelled targets. Never raises.
    """
    from . import client
    import pandas as pd
    try:
        season = season or client.current_season()
        m = _coverage_frame(season, before_week)
        if not len(m):
            return {}
        # nflverse abbreviates the receiver name in play-by-play ("J.Chase"),
        # so match on last name + first initial rather than the display name.
        parts = str(player).split()
        if len(parts) < 2:
            return {}
        want = f"{parts[0][0]}.{parts[-1]}"
        d = m[m["receiver_player_name"] == want]
        if len(d) < _COVERAGE_MIN_TARGETS:
            return {}

        def agg(sel):
            g = d[sel]
            n = len(g)
            if not n:
                return None
            return {"targets": int(n),
                    "yards_per_target": round(float(g["yards_gained"].sum()) / n, 3),
                    "catch_rate": round(float(g["complete_pass"].fillna(0).mean()), 4),
                    "adot": round(float(g["air_yards"].mean()), 2)
                            if g["air_yards"].notna().any() else None}

        man = agg(d["defense_man_zone_type"] == "MAN_COVERAGE")
        zone = agg(d["defense_man_zone_type"] == "ZONE_COVERAGE")
        overall = agg(d["defense_man_zone_type"].notna())
        # man_delta: his yards per target against MAN relative to his own
        # overall. Below 1.0 means man coverage suppresses him.
        man_delta = (round(man["yards_per_target"] / overall["yards_per_target"], 3)
                     if (man and overall and overall["yards_per_target"]) else None)
        return {"player": player, "season": season,
                "man": man, "zone": zone, "overall": overall,
                "man_delta": man_delta,
                "man_share_faced": (round(man["targets"] / overall["targets"], 3)
                                    if (man and overall) else None)}
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl coverage_splits failed for %r: %s", player, exc)
        return {}


def team_coverage_tendency(team: str, season: int = None,
                           before_week: int = None) -> dict:
    """How much MAN coverage a defence plays, relative to the league.

    The other half of the matchup: a receiver's man weakness only matters
    against a defence that actually plays it. League 2025 is ~31% man.
    """
    from . import client
    try:
        season = season or client.current_season()
        m = _coverage_frame(season, before_week)
        if not len(m):
            return {}
        m = m[m["defense_man_zone_type"].notna()]
        if not len(m):
            return {}
        lg = float((m["defense_man_zone_type"] == "MAN_COVERAGE").mean())
        d = m[m["defteam"] == team]
        if len(d) < 100:
            return {"team": team, "man_rate": None, "league_man_rate": round(lg, 4),
                    "basis": "insufficient plays"}
        mr = float((d["defense_man_zone_type"] == "MAN_COVERAGE").mean())
        return {"team": team, "plays": int(len(d)),
                "man_rate": round(mr, 4),
                "league_man_rate": round(lg, 4),
                "man_rate_rel": round(mr / lg, 3) if lg else None}
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl team_coverage_tendency failed for %r: %s", team, exc)
        return {}
