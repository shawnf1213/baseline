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


def _shrink(obs, n, prior, k=PRIOR_GAMES):
    """Empirical-Bayes blend of an observed rate toward a prior."""
    if obs is None or not n or n <= 0:
        return prior
    return ((obs * n) + (prior * k)) / (n + k)


def _weighted(vals, weights):
    tot = sum(weights)
    return (sum(v * w for v, w in zip(vals, weights)) / tot) if tot else None


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
            d = df[(df.get("season_type") == "REG")
                   & (df.get("player_display_name") == player)]
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
        prior = POSITION_PRIOR.get(pos, POSITION_PRIOR["WR"])

        # Recency weights: most recent game weight 1, halving every
        # RECENCY_HALFLIFE games back.
        order = list(range(n - 1, -1, -1))          # 0 = most recent
        w = [0.5 ** (i / RECENCY_HALFLIFE) for i in order]

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
            "player": player, "position": pos,
            "games": n,
            "window": ("current season" if (hist["_src"] == "current").all()
                       else "current + prior season"
                       if (hist["_src"] == "current").any()
                       else "prior season only"),
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
        if snap.get("ratio") and ts:
            ts = ts * (1.0 + SNAP_DAMP * (snap["ratio"] - 1.0))
            out["snap_ratio"] = round(snap["ratio"], 3)
            out["snap_recent"] = round(snap["recent"], 3)
            out["snap_season"] = round(snap["season"], 3)
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
# CORRECTING IT MADE THE MODEL WORSE, and that is the finding worth keeping:
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
# So this stays OFF until the second error is located, rather than shipping a
# change that is right in principle and worse in practice. NEUTRAL["plays"] in
# volume.py was corrected 63.0 -> 60.71 in the same pass and is NOT gated: it is
# the week-1 fallback, it was measured directly, and it moves the opposite way.
COUNT_SACKS_AS_PLAYS = os.getenv("NFL_COUNT_SACKS", "0").strip() in ("1", "true", "True")


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
                   & (df.get("player_display_name") == player)]
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
