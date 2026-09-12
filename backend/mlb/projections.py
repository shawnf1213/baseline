"""
On-demand MLB projections — the engine behind the /mlb* slash commands.

Mirrors what tennis's /prop does through the backend's /api/prop/calculate:
resolve the player, run the SAME model the daily board runs, and return a
projection with a lean against the user's line. Kept out of discord-bot/bot.py so
the command there is a thin wrapper and nothing MLB can raise into a tennis path.

THE SAME ENGINES THE BOARD USES — deliberately. If /mlbprop had its own
projection path it would drift from the board, and a user comparing a command
answer against a posted play would get two different numbers for one pitcher.
strikeouts.py, pitcher_props.py and batter_props.py are called directly.

OPPONENT IS RESOLVED FROM THE SCHEDULE, not asked for. A pitcher starts against
exactly one team on a given day, so making the user type it invites a mismatch
between the opponent they name and the game the model prices. Looked up from
today's and tomorrow's cards; if the pitcher is not on either, the projection
still runs without the opponent adjustment and SAYS SO rather than silently
dropping a term.
"""

import datetime as _dt
import logging

log = logging.getLogger("baseline.mlb.projections")

# What a user can ask for, in the order they should appear in the picker.
# Value is the canonical prop; label is what Discord shows. Only props with a
# real engine behind them are listed — offering one we cannot price would return
# an error after the user filled in a whole command.
PITCHER_PROPS = [
    ("strikeouts", "Pitcher Strikeouts"),
    ("pitching_outs", "Pitching Outs"),
    ("hits_allowed", "Hits Allowed"),
    ("walks_allowed", "Walks Allowed"),
    ("earned_runs", "Earned Runs"),
    ("pitcher_fantasy_score", "Pitcher Fantasy Score"),
]
BATTER_PROPS = [
    ("hits", "Hits"),
    ("total_bases", "Total Bases"),
    ("hits_runs_rbis", "Hits + Runs + RBIs"),
    ("home_runs", "Home Runs"),
    ("doubles", "Doubles"),
    ("triples", "Triples"),
    ("singles", "Singles"),
    ("runs", "Runs"),
    ("rbis", "RBIs"),
    ("walks", "Walks"),
    ("hitter_strikeouts", "Hitter Strikeouts"),
    ("stolen_bases", "Stolen Bases"),
    ("hitter_fantasy_score", "Hitter Fantasy Score"),
]
ALL_PROPS = PITCHER_PROPS + BATTER_PROPS
PROP_LABELS = dict(ALL_PROPS)
_PITCHER_SET = {v for v, _ in PITCHER_PROPS}


def search_players(query: str, limit: int = 20) -> list:
    """[{id, name, position, team}] for an autocomplete query. [] on failure."""
    from . import client
    q = (query or "").strip()
    if len(q) < 2:
        return []
    try:
        d = client._get("/people/search", names=q, limit=limit)
        out = []
        for p in (d.get("people") or [])[:limit]:
            out.append({
                "id": p.get("id"),
                "name": p.get("fullName"),
                "position": ((p.get("primaryPosition") or {}).get("abbreviation")),
                "team": ((p.get("currentTeam") or {}).get("name")),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb player search failed (%s): %s", q, str(exc)[:120])
        return []


def _find_matchup(player_id):
    """The player's next scheduled game across today and tomorrow.

    Returns {opponent, opponent_team_id, game_pk, is_home, home_team_id, date,
    role} or {}. `role` is "pitcher" when they are the announced starter.
    """
    from . import client
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-4)))
    for offset in (0, 1):
        date = (now + _dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            games = client.get_schedule(date)
        except Exception:  # noqa: BLE001
            continue
        for g in games:
            # Only games that have not started — a finished game is not a
            # matchup to project, same gate the board uses.
            if g.get("abstract_state") not in (None, "Preview"):
                continue
            for side, opp in (("away", "home"), ("home", "away")):
                if g[side].get("pitcher_id") == player_id:
                    return {"opponent": g[opp].get("team"),
                            "opponent_team_id": g[opp].get("team_id"),
                            "game_pk": g.get("game_pk"),
                            "is_home": side == "home",
                            "home_team_id": (g.get("home") or {}).get("team_id"),
                            "date": date, "role": "pitcher"}
    return {}


def project(player_name: str, prop: str, line: float = None) -> dict:
    """Project one MLB prop for one player.

    Returns a dict with at least {ok}. On failure {ok: False, error: str} so the
    command layer can show a specific reason rather than a generic error — a
    thin sample and an unknown name are different problems for the user.

    Never raises.
    """
    try:
        if prop not in PROP_LABELS:
            return {"ok": False, "error": f"`{prop}` is not a prop I can project."}

        hits = search_players(player_name, limit=5)
        if not hits:
            return {"ok": False,
                    "error": f"No MLB player found for **{player_name}**."}
        who = hits[0]
        pid = who["id"]

        is_pitcher_prop = prop in _PITCHER_SET
        matchup = _find_matchup(pid) if is_pitcher_prop else {}

        if is_pitcher_prop:
            if prop == "strikeouts":
                from . import strikeouts as _so
                row = _so.project(pid, matchup.get("opponent_team_id"),
                                  line=line,
                                  game_pk=matchup.get("game_pk"),
                                  is_home=matchup.get("is_home"),
                                  home_team_id=matchup.get("home_team_id"))
            else:
                from . import pitcher_props as _pp
                row = _pp.project(pid, prop,
                                  opponent_team_id=matchup.get("opponent_team_id"),
                                  line=line)
            if not row:
                return {"ok": False,
                        "error": (f"**{who['name']}** does not have enough "
                                  f"starts this season to project "
                                  f"{PROP_LABELS[prop]}. The model needs at "
                                  f"least 5.")}
        else:
            from . import batter_props as _bp
            row = _bp.project(pid, prop, line=line)
            if not row:
                return {"ok": False,
                        "error": (f"**{who['name']}** does not have enough "
                                  f"games to project {PROP_LABELS[prop]}. The "
                                  f"model needs at least 20.")}

        row.update({
            "ok": True,
            "player": who["name"],
            "player_id": pid,
            "position": who.get("position"),
            "prop_label": PROP_LABELS[prop],
            "line": line,
            "opponent": matchup.get("opponent"),
            "game_date": matchup.get("date"),
            # Stated on the embed so a projection is never quietly missing the
            # opponent term without the reader knowing.
            "opponent_known": bool(matchup.get("opponent_team_id")),
            "is_pitcher_prop": is_pitcher_prop,
        })
        return row
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb on-demand projection failed (%s %s): %s",
                      player_name, prop, exc)
        return {"ok": False, "error": "Projection failed — try again shortly."}
