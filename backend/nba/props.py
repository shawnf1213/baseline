"""
NBA player props — the core board.

    Points · Rebounds · Assists · 3-Pointers Made · Fantasy Score
    PRA · Pts+Rebs · Pts+Asts · Rebs+Asts

EVERY PROJECTION IS MINUTES x RATE, with minutes from the player's own recent
workload and the rate from his per-minute production, then adjusted by the game
in front of him:

    stat = projected_minutes
         x per_minute_rate
         x pace_factor          (volume side — how many possessions)
         x opponent_factor      (rate side — how efficiently they allow it)
         x usage_vacuum         (rate side — who is OUT tonight)
         x rest_factor          (small, and flagged)

PACE SCALES VOLUME, DEFENCE SCALES THE RATE, AND THEY ARE SEPARATELY CLAMPED.
This is the single most important guard against over-inflation here, and it is
the same rule nfl/props.py applies for the same reason: the two terms describe
different things, and applying a defensive rating to volume as well as rate
counts the opponent twice. See nba/ratings.py for the full account.

COMBOS ARE SUMS OF THE COMPONENT PROJECTIONS, NEVER MODELLED SEPARATELY.
A PRA projection is points + rebounds + assists, each from the model above, so
the card can never print a PRA that disagrees with the three numbers beside it.
Their SPREAD is a different matter and is measured from the player's own
per-game sums — see usage.COMBO_PARTS. Measured on Tyrese Maxey's 2025-26 log,
assuming independence would have understated PRA sigma by 7%, which inflates the
edge-to-variance ratio and hands a compounded prop confidence it has not earned.

THE PROJECTION IS THE MEAN; THE LEAN COMES FROM THE MEDIAN. Every stat on this
board is right-skewed (the fitted table in _ratios.py puts the median at 0.86 to
0.98 of the mean depending on stat and minutes), so a line between the median
and the mean is one where those two disagree. fair_line is the coin-flip point
and edge is measured from it, which makes edge, lean and P(over) agree by
construction. nfl/props.py learned this the hard way — "proj 50, UNDER 45" on a
card — and NBA has it from the first line of code.

Rule 2: returns {} on failure, never raises.
"""

import logging

log = logging.getLogger("baseline.nba.props")

# Single stats map 1:1 onto a game-log column; combos sum several.
SINGLE_PROPS = {
    "pts": "pts",
    "reb": "reb",
    "ast": "ast",
    "fg3m": "fg3m",
    "nba_fantasy_pts": "nba_fantasy_pts",
}
COMBO_PARTS = {
    "pra": ("pts", "reb", "ast"),
    "pr":  ("pts", "reb"),
    "pa":  ("pts", "ast"),
    "ra":  ("reb", "ast"),
}
SUPPORTED = tuple(SINGLE_PROPS) + tuple(COMBO_PARTS)

# Human labels, used by every embed and by the website.
PROP_LABEL = {
    "pts": "Points", "reb": "Rebounds", "ast": "Assists",
    "fg3m": "3-Pointers Made", "nba_fantasy_pts": "Fantasy Score",
    "pra": "Pts+Rebs+Asts", "pr": "Pts+Rebs", "pa": "Pts+Asts",
    "ra": "Rebs+Asts",
}

# ── REST ─────────────────────────────────────────────────────────────────────
# A second night of a back-to-back is a small negative and a VISIBLE FLAG,
# nothing more. The measured effect on counting stats is modest, and the honest
# version of that is a few per cent plus a label — not a dramatic adjustment
# implying we know more than we do. Home/away is a display flag only and is
# deliberately NOT fed into the number, matching the tennis HOME precedent of
# waiting until the ledger justifies it.
B2B_FACTOR = 0.97


def _combo_sigma(u: dict, prop: str, scale: float) -> float:
    """Measured combo sigma, rescaled to the projected mean.

    `scale` is projected / historical mean, so the spread travels with the
    projection rather than staying pinned to the player's season. Preserving the
    coefficient of variation is also what the empirical ratio table assumes —
    it is built on actual/mean ratios, so the two stay consistent.
    """
    sig = (u.get("sigma") or {}).get(prop)
    if isinstance(sig, (int, float)) and sig > 0:
        return float(sig) * max(0.25, min(4.0, scale))
    return 0.0


def project(player: str, prop: str, line: float = None, game: dict = None,
            season: int = None, as_of=None, inj: dict = None,
            position: str = None) -> dict:
    """Project one NBA player prop.

    `game` is a schedule row from client.upcoming() — it supplies the opponent,
    the pace matchup and which side the player is on. Without it the projection
    still runs on the player's own baseline and SAYS SO, rather than silently
    dropping the matchup term.

    Returns {} when usage is too thin, or {"skipped": True, "reason": ...} when
    the player is below the minutes the model was fitted on.
    """
    from . import usage as _usage, ratings as _rat, confidence as _conf
    from . import distributions as _dist, client as _c
    try:
        if prop not in SUPPORTED:
            return {}
        u = _usage.player_usage(player, season=season, as_of=as_of)
        if not u:
            return {}

        # ── THE MINUTES FLOOR ────────────────────────────────────────────────
        # Below this the SHAPE of every distribution is different, not merely
        # noisier — a 7-minute player's rebound total is a coin flip between 0
        # and 2, and the fitted bands start at 12. A prop under the floor is not
        # a low-confidence play, it is an unanswerable question.
        mins = float(u.get("minutes") or 0)
        if mins < _usage.MIN_MINUTES:
            # TWO DECIMALS, because one rounds 11.99 to "12.0" and the message
            # then reads "12.0 minutes is below the 12" — a reason that looks
            # like a bug to anyone reading the log.
            log.info("nba %s: %s projects %.2f minutes (< %.0f floor) — not priced",
                     prop, player, mins, _usage.MIN_MINUTES)
            return {"skipped": True, "player": u["player"], "prop": prop,
                    "reason": f"{mins:.2f} projected minutes is below the "
                              f"{_usage.MIN_MINUTES:.0f} the model was fitted on"}

        opponent = (game or {}).get("opponent_team")
        team = (game or {}).get("player_team") or u.get("team")
        # Venue, as a real input now rather than a badge. None when there is no
        # schedule row — home_factor then returns 1.000 and says "venue unknown"
        # instead of assuming a neutral court that does not exist.
        is_home = ((game or {}).get("home_abbr") == u.get("team")
                   if game and game.get("home_abbr") else None)
        # The player's listed position, for defence-vs-position. The board row
        # carries one (PrizePicks publishes it); the roster table is the
        # fallback, and "" means the positional layer is skipped and the
        # team-level rank used instead.
        pos = position or (_rat.player_positions(season) or {}).get(
            _c._norm_name(u["player"]), "")

        # ── THE GAME IN FRONT OF HIM ─────────────────────────────────────────
        pace = (_rat.pace_factor(opponent, season) if opponent
                else {"factor": 1.0, "basis": "no opponent supplied"})
        vac = _usage.usage_vacuum(team, player, season=season, inj=inj)
        rest = _usage.rest_state(team, (game or {}).get("tipoff"), season)
        rest_f = B2B_FACTOR if rest.get("back_to_back") else 1.0

        parts = COMBO_PARTS.get(prop)
        stats_for = parts if parts else (SINGLE_PROPS[prop],)

        mu, components, drivers = 0.0, {}, {}
        for st in stats_for:
            base = (u.get("stats") or {}).get(st)
            if not isinstance(base, (int, float)):
                return {}
            # EACH COMPONENT GETS ITS OWN DEFENSIVE AND VENUE MULTIPLIER, and
            # for a combo that is the whole point: a team can be top-5 against
            # guards' scoring and bottom-5 against their assists, so one blended
            # defence number on a PRA would be wrong in both directions at once.
            opp = (_rat.opponent_factor(opponent, st, season, position=pos)
                   if opponent else
                   {"factor": 1.0, "basis": "no opponent supplied",
                    "rank": None, "by_position": False})
            hf = _usage.home_factor(u, st, is_home)
            # minutes x per-minute rate, then the bounded adjustments. The
            # per-minute rate already embeds the player's own blended form, so
            # this scales HIS baseline to tonight's workload rather than handing
            # him a league-average role.
            rate = (u.get("per_min") or {}).get(st, 0.0)
            v = (mins * rate
                 * pace.get("factor", 1.0)
                 * opp.get("factor", 1.0)
                 * hf.get("factor", 1.0)
                 * vac.get("factor", 1.0)
                 * rest_f)
            components[st] = round(v, 2)
            drivers[st] = {
                "baseline": round(float(base), 2),
                "per_minute": round(float(rate), 5),
                "opponent_factor": opp.get("factor", 1.0),
                "opponent_basis": opp.get("basis"),
                "opponent_rank": opp.get("rank"),
                "opponent_by_position": opp.get("by_position"),
                "home_factor": hf.get("factor", 1.0),
                "home_basis": hf.get("basis"),
                "home_used": hf.get("used"),
                "home_avg": hf.get("home"), "away_avg": hf.get("away"),
                "home_games": hf.get("home_games"),
                "away_games": hf.get("away_games"),
            }
            mu += v

        # Historical mean on the SAME basis, to rescale the measured spread.
        hist = sum(float((u.get("stats") or {}).get(s) or 0) for s in stats_for)
        scale = (mu / hist) if hist > 0 else 1.0
        if parts:
            sd = _combo_sigma(u, prop, scale)
        else:
            s0 = (u.get("sigma") or {}).get(stats_for[0]) or 0.0
            sd = float(s0) * max(0.25, min(4.0, scale))

        out = {
            "sport": "nba", "prop": prop, "label": PROP_LABEL.get(prop, prop),
            "player": u["player"], "player_id": u.get("player_id"),
            "team": u.get("team"),
            "projection": round(mu, 2),
            "sd": round(sd, 2),
            "components": components if parts else None,
            "minutes": round(mins, 1),
            "minutes_cv": u.get("minutes_cv"),
            "rotation": u.get("rotation"),
            "games_in_window": u.get("games"),
            "window": u.get("window"),
            "drivers": drivers,
            "pace_factor": pace.get("factor", 1.0),
            "pace_basis": pace.get("basis"),
            "usage_vacuum": vac.get("factor", 1.0),
            "usage_vacuum_basis": vac.get("basis"),
            "usage_out": vac.get("out") or [],
            "opponent": opponent,
            "rest": rest,
            "back_to_back": bool(rest.get("back_to_back")),
            "home": is_home,
            "position": pos or None,
            # The headline defensive and venue numbers for the prop as a whole,
            # lifted from the component that names it (the first part of a
            # combo, or the single stat). Combos still apply each component's
            # OWN multiplier above; this is for display, which needs one number
            # it can caption honestly.
            "def_rank": (drivers.get(stats_for[0]) or {}).get("opponent_rank"),
            "def_basis": (drivers.get(stats_for[0]) or {}).get("opponent_basis"),
            "def_by_position": (drivers.get(stats_for[0]) or {}).get(
                "opponent_by_position"),
            "def_factor": (drivers.get(stats_for[0]) or {}).get(
                "opponent_factor"),
            "home_factor": (drivers.get(stats_for[0]) or {}).get("home_factor"),
            "home_basis": (drivers.get(stats_for[0]) or {}).get("home_basis"),
            "home_used": (drivers.get(stats_for[0]) or {}).get("home_used"),
            "split": ((u.get("splits") or {}).get(
                prop if parts else SINGLE_PROPS[prop]) or None),
            "traded": u.get("traded"),
            "stale": u.get("stale"),
            "days_since_last": u.get("days_since_last"),
            # Stated, never implied: a projection built without a schedule row
            # has no matchup behind it and is a weaker claim.
            "matchup_applied": bool(opponent),
            "prior_season_only": u.get("window") == "prior season only",
        }

        if isinstance(line, (int, float)):
            stat_key = prop if parts else SINGLE_PROPS[prop]
            e = _dist.p_over(stat_key if not parts else "pts",
                             mu, float(line), mins, sd=sd)
            if parts:
                # A combo has no fitted table of its own — its shape is the
                # convolution of three skewed distributions and is noticeably
                # more symmetric than any one of them. Using the points table
                # directly would overstate the skew, so the median ratio is
                # blended toward 1.0 in proportion to how many parts are summed.
                # Stated here rather than hidden: this is an approximation, and
                # it is the one place on this board where the shape is reasoned
                # rather than measured.
                mr = e.get("median_ratio") if e else None
                if isinstance(mr, (int, float)):
                    k = len(parts)
                    mr = 1.0 - (1.0 - float(mr)) / k
                    e = dict(e or {})
                    e["median_ratio"] = mr
                    e["basis"] = (f"points shape, skew damped /{k} for a "
                                  f"{k}-part combo")
            if e:
                _mr = e.get("median_ratio")
                fair = (round(mu * float(_mr), 2)
                        if isinstance(_mr, (int, float)) and _mr > 0 else None)
                # Recompute P(over) against the FAIR line so the probability and
                # the lean describe the same reference the edge does.
                out.update({
                    "line": float(line),
                    "p_over": round(e["p_over"], 4),
                    "p_under": round(e["p_under"], 4),
                    "p_push": round(e.get("p_push", 0.0), 4),
                    "lean": e["lean"],
                    "median_ratio": _mr,
                    "fair_line": fair,
                    "dist_basis": e.get("basis"),
                })
                ref = fair if isinstance(fair, (int, float)) else mu
                out["edge"] = round(ref - float(line), 2)
                out["edge_sd"] = (round(abs(out["edge"]) / sd, 3) if sd else None)
                out["edge_basis"] = ("fair line" if fair is not None
                                     else "mean (no empirical table)")

            # ── CONFIDENCE ───────────────────────────────────────────────────
            c = _conf.calculate_confidence(
                usage=u, stat=(prop if parts else SINGLE_PROPS[prop]),
                prop=prop, projection=mu, line=float(line), sigma=sd,
                opponent_known=bool(opponent), vacuum=vac, rest=rest)
            out.update({
                "confidence": c["confidence"],
                "confidence_breakdown": c["breakdown"],
                "data_ceiling": c["data_ceiling"],
                "cap_reason": c["cap_reason"],
                "cap_tag": c["cap_tag"],
                "evr": c["evr"], "evr_scaled": c["evr_scaled"],
                "coin_flip": c["coin_flip"],
            })
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba projection failed (%s %s): %s", player, prop, exc)
        return {}
