"""
NFL read-only queries — the /nflplayer, /nflform, /nflhistory, /nflgame,
/nflspread and /nflh2h answers.

These mirror the TENNIS command set one for one, because members have already
learned that vocabulary and a second sport should not invent a second one:

    tennis      nfl            what it answers
    /prop       /nflprop       project a player prop            (nfl.props)
    /match      /nflgame       who wins, and by how much
    /spread     /nflspread     chance a team covers the handicap
    /h2h        /nflh2h        past meetings
    /player     /nflplayer     role, usage and efficiency profile
    /form       /nflform       recent game-by-game trend
    /history    /nflhistory    how often he has cleared a number

WHERE THE NUMBERS COME FROM
---------------------------
Every one of these is measured, not assumed. The win-probability and cover
curves are FITTED on nflverse `games.csv` — 5,431 completed games, 2006-2025 —
rather than borrowed from a rule of thumb:

    P(win) = 1 / (1 + exp(-(-0.0523 + 0.1454 * points_favoured_by)))

    favoured by 7 -> 0.724      underdog by 7 -> 0.255
    favoured by 3 -> 0.595      underdog by 3 -> 0.380

which tracks the raw buckets closely (home dogs of 3.5-7 measured 0.317, home
favourites of 7-10 measured 0.778).

MIND THE SIGN. games.csv states the spread as POSITIVE-means-home-favoured;
ESPN, this codebase and every bettor state it as NEGATIVE-means-favoured
(nfl/volume.py: "negative means favoured"). The fit above was measured in
games.csv's convention and is consumed in ours, so win_prob() negates on the way
in. The first cut of this did not, and returned 0.38 for a team favoured by 3 at
home — an inverted curve that looks perfectly plausible until you read it.

The residual margin-minus-spread has mean -0.04 and sd 13.19, so the
market is unbiased and MARGIN_SD is a measurement rather than a guess. Home
teams covered 47.65% and totals went over 49.05% — both near the coin flip a
liquid market should produce, which is the check that says these inputs are
priced honestly and we are not going to beat them by arithmetic alone.

THAT LAST POINT MATTERS. /nflgame and /nflspread convert the market's own number
into a probability. They are a READING AID, not an edge, and they say so — the
edge in this product lives in the player props, where our volume x rate estimate
can disagree with the book. Presenting a de-vigged spread as a model opinion
would be dressing up the market as insight.
"""

import logging
import math

log = logging.getLogger("baseline.nfl.queries")

GAMES_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")

# Fitted on games.csv, 2006-2025, n=5431. See module docstring.
WP_B0, WP_B1 = -0.0523, 0.1454
MARGIN_SD = 13.19

# canonical prop -> the weekly-log column that measures it
PROP_COL = {
    "pass_yards": "passing_yards",
    "rush_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
}

_games_cache = {}


def _games():
    """Completed-game history with lines. Cached — it is a 2 MB download."""
    if "df" in _games_cache:
        return _games_cache["df"]
    import pandas as pd
    try:
        df = pd.read_csv(GAMES_URL, low_memory=False)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl queries: games.csv unavailable: %s", str(exc)[:140])
        df = pd.DataFrame()
    _games_cache["df"] = df
    return df


def win_prob(spread) -> float:
    """P(this team wins) from ITS OWN line, negative = favoured.

    Fitted, not assumed — and note the negation: see MIND THE SIGN above.
    """
    if not isinstance(spread, (int, float)):
        return 0.5
    favoured_by = -float(spread)
    return 1.0 / (1.0 + math.exp(-(WP_B0 + WP_B1 * favoured_by)))


def _norm_name(s) -> str:
    # str() first, unconditionally: these columns are Arrow-backed and a missing
    # name arrives as float NaN, which unicodedata.normalize refuses outright.
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
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks).strip()


def _weekly(season: int = None):
    """Player weekly logs for the season, falling back to the prior one.

    Returns (frame, season_used). In week 1 the current season's file does not
    exist yet, and a query that silently returned nothing would look like "this
    player does not exist" rather than "the season has not started".
    """
    from . import client as _c
    cur = season or _c.current_season()
    df = _c.load("stats_player_week", cur)
    if len(df):
        return df, cur
    df = _c.load("stats_player_week", cur - 1)
    return df, (cur - 1 if len(df) else cur)


def _player_rows(name: str, season: int = None):
    df, yr = _weekly(season)
    if not len(df):
        return None, yr
    col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    key = _norm_name(name)
    m = df[df[col].astype(str).map(_norm_name) == key]
    if not len(m):
        # forgiving surname match, so "Jefferson" finds Justin Jefferson when
        # only one player matches — but never when several do.
        cand = df[df[col].astype(str).map(
            lambda s: key in _norm_name(s) or _norm_name(s).endswith(" " + key))]
        names = sorted(set(cand[col].astype(str)))
        if len(names) == 1:
            m = cand
        elif len(names) > 1:
            return {"ambiguous": names[:8]}, yr
    if not len(m):
        return None, yr
    if "week" in m.columns:
        m = m.sort_values("week")
    return m, yr


# ── /nflplayer ───────────────────────────────────────────────────────────────
def player_profile(name: str, season: int = None) -> dict:
    """Role, usage and efficiency — the NFL answer to tennis's /player.

    Tennis shows surface splits and serve/return numbers. The NFL equivalent of
    "what kind of player is this" is his ROLE: where he sits on the depth chart,
    how much of the offence runs through him, and how efficient he is with it.
    """
    from . import usage as _usage
    try:
        u = _usage.player_usage(name, season=season)
        if not u:
            return {}
        pos, rank = _usage.depth_rank(u["player"], season=season)
        rc = u.get("role_change") or {}
        return {
            "player": u["player"], "position": u.get("position"),
            "depth_pos": pos, "depth_rank": rank,
            "games": u.get("games"), "window": u.get("window"),
            "target_share": u.get("target_share"),
            "targets_per_game": u.get("targets_per_game"),
            "carries_per_game": u.get("carries_per_game"),
            "pass_att_per_game": u.get("pass_att_per_game"),
            "catch_rate": u.get("catch_rate"),
            "yards_per_target": u.get("yards_per_target"),
            "yards_per_carry": u.get("yards_per_carry"),
            "yards_per_attempt": u.get("yards_per_attempt"),
            "completion_pct": u.get("completion_pct"),
            "snap_ratio": u.get("snap_ratio"),
            "role": u.get("role") or {},
            "role_change": rc or None,
            # The depth a reader actually asks for — who he struggles against,
            # and who he shares the ball with. Each is independently guarded, so
            # one failing does not cost the profile.
            "splits": opponent_splits(u["player"], season, u.get("position")),
            "targets": favourite_targets(u["player"], season),
            "competition": target_competition(u["player"], season),
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl player_profile(%r) failed: %s", name, exc)
        return {}


# ── /nflform ─────────────────────────────────────────────────────────────────
def recent_form(name: str, n: int = 6, season: int = None) -> dict:
    """Last n games, game by game — the NFL answer to tennis's /form."""
    try:
        rows, yr = _player_rows(name, season)
        if isinstance(rows, dict) and rows.get("ambiguous"):
            return rows
        if rows is None or not len(rows):
            return {}
        keep = ["week", "opponent_team", "targets", "receptions",
                "receiving_yards", "carries", "rushing_yards", "attempts",
                "completions", "passing_yards"]
        have = [c for c in keep if c in rows.columns]
        tail = rows[have].tail(n)
        col = ("player_display_name" if "player_display_name" in rows.columns
               else "player_name")
        return {
            "player": str(rows[col].iloc[-1]),
            "season": yr,
            "games": [{k: (None if _isna(v) else v) for k, v in r.items()}
                      for r in tail.to_dict("records")],
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl recent_form(%r) failed: %s", name, exc)
        return {}


def _isna(v):
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except Exception:  # noqa: BLE001
        return v is None


# ── /nflhistory ──────────────────────────────────────────────────────────────
def line_history(name: str, prop: str, line: float, season: int = None) -> dict:
    """How often he cleared this number — the NFL answer to tennis's /history.

    Counts over/under/push across the window and the last 5, and returns the
    per-game values so the caller can show the actual games rather than only a
    ratio. A hit rate with no games behind it is the easiest number in this
    product to over-read.
    """
    try:
        col = PROP_COL.get(prop)
        if not col:
            return {}
        rows, yr = _player_rows(name, season)
        if isinstance(rows, dict) and rows.get("ambiguous"):
            return rows
        if rows is None or not len(rows) or col not in rows.columns:
            return {}
        vals = [float(v) for v in rows[col].fillna(0).tolist()]
        namecol = ("player_display_name" if "player_display_name" in rows.columns
                   else "player_name")

        def tally(vs):
            o = sum(1 for v in vs if v > line)
            u = sum(1 for v in vs if v < line)
            return {"over": o, "under": u, "push": len(vs) - o - u, "n": len(vs)}

        return {
            "player": str(rows[namecol].iloc[-1]), "prop": prop, "line": line,
            "season": yr, "values": vals,
            "all": tally(vals), "last5": tally(vals[-5:]),
            "mean": round(sum(vals) / len(vals), 2) if vals else None,
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl line_history(%r) failed: %s", name, exc)
        return {}


# ── /nflgame ─────────────────────────────────────────────────────────────────
def game_outcome(team: str, season: int = None) -> dict:
    """Win probability and projected score for this team's next game.

    The NFL answer to tennis's /match. Derived from the market's spread and
    total, so it is a reading aid rather than an edge — see the module docstring.
    """
    from . import client as _c
    try:
        from .board import _team_index, _game_for
        games = _c.upcoming_week()
        g = _game_for(team, _team_index(games))
        if not g:
            return {}
        spread = g.get("player_spread")      # from THIS team's side
        total = g.get("total")
        p = win_prob(spread) if spread is not None else None
        proj_for = proj_against = None
        if isinstance(total, (int, float)) and isinstance(spread, (int, float)):
            # total = for + against, and margin = for - against = -spread
            # (negative spread means favoured). Solving the pair:
            proj_for = (float(total) - float(spread)) / 2.0
            proj_against = (float(total) + float(spread)) / 2.0
        return {
            "team": g.get("player_team"), "opponent": g.get("opponent_team"),
            "matchup": g.get("matchup"), "kickoff": g.get("kickoff"),
            "spread": spread, "total": total, "win_prob": p,
            "proj_for": proj_for, "proj_against": proj_against,
        }
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl game_outcome(%r) failed: %s", team, exc)
        return {}


# ── /nflspread ───────────────────────────────────────────────────────────────
def spread_cover(team: str, handicap: float, season: int = None) -> dict:
    """P(this team covers `handicap`) — the NFL answer to tennis's /spread.

    handicap is stated the way a bettor says it: -4.5 means "wins by 5+".
    Normal around the market's own margin with the measured sd of 13.19, so at
    the posted number this returns ~50% BY CONSTRUCTION. That is the honest
    answer, and the embed says so rather than dressing it up.
    """
    try:
        g = game_outcome(team, season)
        if not g or g.get("spread") is None:
            return {}
        exp_margin = -float(g["spread"])         # negative spread = favoured
        need = -float(handicap)                  # cover means margin > -handicap
        z = (exp_margin - need) / MARGIN_SD
        p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        return {**g, "handicap": handicap, "cover_prob": p,
                "expected_margin": exp_margin, "margin_sd": MARGIN_SD}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl spread_cover(%r, %r) failed: %s", team, handicap, exc)
        return {}


# ── /nflh2h ──────────────────────────────────────────────────────────────────
def head_to_head(team_a: str, team_b: str, limit: int = 8) -> dict:
    """Past meetings — the NFL answer to tennis's /h2h."""
    from .client import normalize_team
    try:
        df = _games()
        if not len(df):
            return {}
        a, b = normalize_team(team_a), normalize_team(team_b)
        d = df[df.home_score.notna()]
        m = d[((d.home_team == a) & (d.away_team == b))
              | ((d.home_team == b) & (d.away_team == a))]
        if not len(m):
            return {"team_a": a, "team_b": b, "meetings": [], "a_wins": 0,
                    "b_wins": 0, "ties": 0}
        m = m.sort_values(["season", "week"])
        out, aw, bw, ties = [], 0, 0, 0
        for _, r in m.iterrows():
            hs, as_ = float(r.home_score), float(r.away_score)
            if hs == as_:
                ties += 1
                win = None
            else:
                win = r.home_team if hs > as_ else r.away_team
                if win == a:
                    aw += 1
                else:
                    bw += 1
            out.append({"season": int(r.season), "week": int(r.week),
                        "home": r.home_team, "away": r.away_team,
                        "home_score": int(hs), "away_score": int(as_),
                        "winner": win})
        return {"team_a": a, "team_b": b, "a_wins": aw, "b_wins": bw,
                "ties": ties, "total": len(out), "meetings": out[-limit:]}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl head_to_head(%r,%r) failed: %s", team_a, team_b, exc)
        return {}


# ── DEPTH: the questions a person actually asks about a player ───────────────
# "Who does he struggle against", "who does he throw to". A role table alone
# answers neither, and those are the two things a reader reaches for first.
#
# EVERY SPLIT CARRIES ITS SAMPLE SIZE, and small ones are dropped rather than
# shown small. One 12-yard game against a defence is not a matchup problem, and
# rendering it as "worst vs BUF" would invent a pattern out of a single Sunday.
# A 17-game season means most opponents are faced ONCE, so requiring two
# meetings leaves only divisional rivals — and with three qualifying opponents
# "best" and "worst" returned the same three teams in opposite order, which is
# not a split, it is a sorted list wearing two hats. So single meetings count,
# every entry carries its n, and best/worst are forced DISJOINT: if there are
# not enough distinct opponents to populate both ends, the lists shrink rather
# than overlap.
MIN_SPLIT_GAMES = 1

# The stat that IS the player, by position — what "a good game" means for him.
PRIMARY_STAT = {
    "QB": ("passing_yards", "pass yds"),
    "RB": ("rushing_yards", "rush yds"),
    "WR": ("receiving_yards", "rec yds"),
    "TE": ("receiving_yards", "rec yds"),
    "FB": ("rushing_yards", "rush yds"),
}


def opponent_splits(name: str, season: int = None, position: str = None):
    """Best and worst opponents by the player's own primary stat.

    Returns {"stat", "label", "best": [...], "worst": [...], "mean"} where each
    entry is {opp, mean, n}. Opponents faced fewer than MIN_SPLIT_GAMES times
    are excluded — see the note above.
    """
    try:
        rows, yr = _player_rows(name, season)
        if isinstance(rows, dict) or rows is None or not len(rows):
            return {}
        pos = position
        if not pos and "position" in rows.columns:
            vals = rows["position"].dropna()
            pos = str(vals.iloc[-1]) if len(vals) else None
        col, label = PRIMARY_STAT.get((pos or "WR").upper(),
                                      ("receiving_yards", "rec yds"))
        if col not in rows.columns or "opponent_team" not in rows.columns:
            return {}
        g = (rows[["opponent_team", col]].dropna()
             .groupby("opponent_team")[col].agg(["mean", "count"]))
        g = g[g["count"] >= MIN_SPLIT_GAMES]
        if not len(g):
            return {}
        g = g.sort_values("mean")
        # Split the ranking down the middle before taking the ends, so the same
        # opponent can never appear as both a strength and a weakness.
        k = min(3, len(g) // 2)
        if k < 1:
            return {}
        worst = [{"opp": i, "mean": round(float(r["mean"]), 1),
                  "n": int(r["count"])} for i, r in g.head(k).iterrows()]
        best = [{"opp": i, "mean": round(float(r["mean"]), 1),
                 "n": int(r["count"])} for i, r in g.tail(k).iloc[::-1].iterrows()]
        return {"stat": col, "label": label, "season": yr,
                "best": best, "worst": worst,
                "mean": round(float(rows[col].dropna().mean()), 1)}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl opponent_splits(%r) failed: %s", name, exc)
        return {}


def favourite_targets(name: str, season: int = None, limit: int = 4):
    """Who this QB actually throws to — his receivers ranked by target share.

    Only meaningful for a passer, so it returns {} for anyone else rather than
    inventing a receiving corps for a running back. Built from the same weekly
    logs: take the QB's team and weeks, then rank everyone who caught a pass for
    that team in those weeks.
    """
    try:
        rows, yr = _player_rows(name, season)
        if isinstance(rows, dict) or rows is None or not len(rows):
            return {}
        if "team" not in rows.columns or "attempts" not in rows.columns:
            return {}
        # A passer, judged by what he did rather than by a position label — a
        # label can be stale, four hundred attempts cannot.
        if float(rows["attempts"].fillna(0).sum()) < 50:
            return {}
        team = str(rows["team"].dropna().iloc[-1])
        weeks = set(int(w) for w in rows["week"].dropna().tolist())
        df, _ = _weekly(season)
        col = ("player_display_name" if "player_display_name" in df.columns
               else "player_name")
        mates = df[(df["team"] == team) & (df["week"].isin(weeks))
                   & (df.get("season_type") == "REG")]
        if "targets" not in mates.columns or not len(mates):
            return {}
        g = (mates.groupby(col)
             .agg(targets=("targets", "sum"),
                  rec=("receptions", "sum"),
                  yds=("receiving_yards", "sum"),
                  games=("week", "nunique")))
        g = g[g["targets"] > 0].sort_values("targets", ascending=False)
        total = float(g["targets"].sum()) or 1.0
        out = []
        for nm, r in g.head(limit).iterrows():
            out.append({"player": str(nm), "targets": int(r["targets"]),
                        "share": round(float(r["targets"]) / total, 3),
                        "rec": int(r["rec"]), "yards": int(r["yds"]),
                        "games": int(r["games"])})
        return {"team": team, "season": yr, "targets": out}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl favourite_targets(%r) failed: %s", name, exc)
        return {}


def target_competition(name: str, season: int = None, limit: int = 4):
    """Who this pass-catcher shares the ball with — the mirror of the above.

    A receiver's ceiling is set as much by who else is on the field as by his
    own ability, and "third in the pecking order" is the single most useful
    thing to know about a WR3 that a target share alone does not say.
    """
    try:
        rows, yr = _player_rows(name, season)
        if isinstance(rows, dict) or rows is None or not len(rows):
            return {}
        if "team" not in rows.columns or "targets" not in rows.columns:
            return {}
        if float(rows["targets"].fillna(0).sum()) <= 0:
            return {}
        team = str(rows["team"].dropna().iloc[-1])
        weeks = set(int(w) for w in rows["week"].dropna().tolist())
        df, _ = _weekly(season)
        col = ("player_display_name" if "player_display_name" in df.columns
               else "player_name")
        mates = df[(df["team"] == team) & (df["week"].isin(weeks))
                   & (df.get("season_type") == "REG")]
        g = (mates.groupby(col).agg(targets=("targets", "sum"))
             .sort_values("targets", ascending=False))
        g = g[g["targets"] > 0]
        total = float(g["targets"].sum()) or 1.0
        me = _norm_name(str(rows[col].iloc[-1]))
        out, rank = [], None
        for i, (nm, r) in enumerate(g.head(limit).iterrows(), 1):
            is_me = _norm_name(str(nm)) == me
            out.append({"player": str(nm), "targets": int(r["targets"]),
                        "share": round(float(r["targets"]) / total, 3),
                        "is_player": is_me})
        for i, (nm, _r) in enumerate(g.iterrows(), 1):
            if _norm_name(str(nm)) == me:
                rank = i
                break
        return {"team": team, "season": yr, "rank": rank,
                "of": int(len(g)), "targets": out}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl target_competition(%r) failed: %s", name, exc)
        return {}
