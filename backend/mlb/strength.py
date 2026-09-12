"""
Strength of schedule — what a pitcher's numbers were earned AGAINST.

THE PROBLEM THIS SOLVES. A pitcher's rate is a fact about him and his opponents
together, and the model was reading it as a fact about him alone. Troy Melton
carried a 0.168 hits-per-batter-faced rate against a league average near 0.216 —
elite suppression on its face. Whether that is skill or schedule depends entirely
on who he faced, and nothing asked.

This is the same correction tennis makes with opponent quality: a player who
beats qualifiers does not own the same numbers as one who beats seeds, even when
the raw stat line matches.

HOW IT WORKS. For each start, the opponent's offensive rate relative to league
is known. Average those across his starts, weighted by batters faced, and the
result says how hard his schedule was. A pitcher who faced offences 6% below
league on hit rate has a rate that is 6% flattered, and the correction removes
exactly that much before the model treats it as ability.

    adjusted_rate = raw_rate / schedule_factor

WHAT IT IS NOT. It is not a second opponent adjustment. The opponent term in
pitcher_props prices the NEXT start against the team he is about to face; this
one corrects the BASELINE for the teams he has already faced. Applying only the
first would price a good matchup on an inflated baseline — the two answer
different questions and both are needed.

Shrunk toward 1.000 by batters faced, because a five-start schedule is a small
sample of opponents and an unshrunk factor would chase noise.
"""

import logging

log = logging.getLogger("baseline.mlb.strength")

# Prior in BATTERS FACED. A pitcher needs roughly this many before his schedule
# outweighs "average schedule" — about six starts.
PRIOR_BF = 140.0

# Nobody's schedule is 20% harder than the league's over a season.
CLIP = (0.88, 1.12)

# Which opponent-batting rate governs each pitcher prop.
RATE_FOR = {
    "strikeouts": "k_rate",
    "hits_allowed": "hit_rate",
    "walks_allowed": "bb_rate",
    "earned_runs": "run_rate",
    "pitching_outs": "run_rate",
    "pitcher_fantasy_score": "run_rate",
}


def _shrink(obs, n, k=PRIOR_BF):
    if obs is None or not n or n <= 0:
        return 1.0
    return ((obs * n) + (1.0 * k)) / (n + k)


def schedule_factor(rows: list, prop: str, team_batting: dict = None,
                    league: dict = None) -> dict:
    """How hard the schedule behind these starts was, for THIS prop's rate.

    Above 1.000 means he faced better offences than average, so his raw rate
    UNDERSTATES him. Below 1.000 means the reverse.

    Returns {factor, basis, opponents, bf}. factor 1.0 with a reason when the
    opponents are unknown — an unrecognised schedule must not silently become an
    average one.
    """
    from . import client
    field = RATE_FOR.get(prop)
    if not field or not rows:
        return {"factor": 1.0, "basis": "no schedule data"}
    try:
        tb = team_batting if team_batting is not None else client.get_team_batting()
        lg = league if league is not None else client.league_batting(tb)
        lg_rate = (lg or {}).get(field)
        if not tb or not lg_rate:
            return {"factor": 1.0, "basis": "league baseline unavailable"}
        num = den = 0.0
        seen = 0
        for r in rows:
            opp = tb.get(r.get("opponent_id"))
            bf = r.get("bf") or 0
            if not opp or not bf:
                continue
            rate = opp.get(field)
            if not rate:
                continue
            num += (rate / lg_rate) * bf
            den += bf
            seen += 1
        if not den or seen < 3:
            return {"factor": 1.0, "basis": "too few identified opponents"}
        raw = num / den
        f = max(CLIP[0], min(CLIP[1], _shrink(raw, den)))
        return {"factor": f, "raw": round(raw, 4), "opponents": seen,
                "bf": int(den),
                "basis": (f"faced offences at {raw:.3f}x league {field} "
                          f"over {seen} starts")}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb schedule_factor failed: %s", str(exc)[:140])
        return {"factor": 1.0, "basis": "schedule lookup failed"}


def rankings(prop: str = "hits_allowed", season: int = None,
             min_starts: int = 5) -> list:
    """Schedule-ADJUSTED pitcher rankings for a prop's rate.

    [{pitcher, raw_rate, schedule_factor, adjusted_rate, starts}] best-first.

    The point of the list is the gap between raw and adjusted: it names the
    pitchers whose numbers are a schedule artefact and the ones whose numbers are
    better than they look.
    """
    from . import client, pitcher_props as _pp
    import datetime as _dt
    field = RATE_FOR.get(prop)
    stat = {"hits_allowed": "h", "walks_allowed": "bb", "earned_runs": "er",
            "strikeouts": "k"}.get(prop)
    if not field or not stat:
        return []
    try:
        tb = client.get_team_batting()
        lg = client.league_batting(tb)
        pids, out = {}, []
        for off in range(0, 6):
            d = (_dt.date.today() - _dt.timedelta(days=off)).isoformat()
            for g in client.get_schedule(d):
                for side in ("away", "home"):
                    if g[side].get("pitcher_id"):
                        pids[g[side]["pitcher_id"]] = g[side].get("pitcher")
        for pid, name in pids.items():
            rows = _pp._start_rows(pid)
            if len(rows) < min_starts:
                continue
            bf = sum(r["bf"] for r in rows) or 1
            raw = sum(r.get(stat) or 0 for r in rows) / bf
            sf = schedule_factor(rows, prop, tb, lg)
            out.append({
                "pitcher": name, "pitcher_id": pid, "starts": len(rows),
                "raw_rate": round(raw, 4),
                "schedule_factor": round(sf["factor"], 4),
                "adjusted_rate": round(raw / sf["factor"], 4),
                "basis": sf.get("basis"),
            })
        out.sort(key=lambda x: x["adjusted_rate"])
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb strength rankings failed: %s", exc)
        return []
