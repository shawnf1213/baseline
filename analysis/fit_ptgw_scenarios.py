"""Empirical scenario fit for the PTGW rebuild (Sackmann is dead — source is
Sofascore per-match records, the same source the approved games_per_set fit used).

Each match record carries, for the SELECTED player:
    won            -> True/False           (match result)
    sets_played    -> 2 / 3 (BO3) or 3/4/5 (BO5)
    total_games_won-> the player's OWN games won in the match   (the PTGW variable)
So every match is a clean labelled observation for the four scenarios:
    S1 win in straights | S2 win in a decider | S3 lose in a decider | S4 lose straights.

Outputs, PER TOUR (ATP/WTA) and format (BO3 here; BO5 gets a fallback table —
tour-level ATP/WTA slates are almost entirely BO3, GS BO5 is too sparse to fit):
    P(3 sets | win), P(3 sets | lose)
    per-scenario player-games-won mean & sd
Deduped by event_id so a match seen from both players counts once.
"""
import sys, statistics, collections
sys.path.insert(0, "E:/baseline/discord-bot")
import logging
logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod

ATP = ["Rublev", "Sonego", "Cobolli", "Darderi", "Etcheverry", "Navone", "Munar",
       "Tsitsipas", "Bublik", "Halys", "Basilashvili", "Tirante", "Pellegrino",
       "Collignon", "Vacherot", "Baez", "Tabilo", "Zverev", "Ruud", "Cerundolo",
       "Djokovic", "Medvedev", "Fritz", "Shelton", "Musetti", "Dimitrov"]
WTA = ["Sasnovich", "Jones", "Urgesi", "Feistel", "Samson", "Erjavec", "Barthel",
       "Valentova", "Sakkari", "Dart", "Badosa", "Bronzetti", "Parks", "Chiesa",
       "Andreeva", "Zheng", "Krejcikova", "Tauson", "Rus", "Grabher",
       "Swiatek", "Gauff", "Sabalenka", "Pegula", "Kalinskaya", "Vondrousova"]


def collect(tour, names):
    seen, rows = set(), []
    for q in names:
        try:
            res = pod._get("/api/search", {"query": q, "tour": tour}, 20)
            if not res:
                continue
            d = pod._post("/api/player/stats",
                          {"player_id": str(res[0]["id"]), "tour": tour,
                           "player_name": q}, 180)
        except Exception:
            continue
        for m in (d.get("all_matches") or []):
            eid = m.get("event_id")
            if eid in seen:
                continue
            gw = m.get("total_games_won")
            sp = m.get("sets_played")
            won = m.get("won")
            if not isinstance(gw, (int, float)) or gw <= 0:
                continue
            if sp not in (2, 3, 4, 5):
                continue
            if won is None:
                continue
            # BO5 detection: a match with 4-5 sets, or a 3-set match the LOSER won
            # a set in with a total that only makes sense at BO5, is rare in this
            # tour-level sample; treat sets_played>=4 as BO5, else BO3.
            fmt = "BO5" if sp >= 4 else "BO3"
            if fmt == "BO3" and sp not in (2, 3):
                continue
            # Drop retirement-corrupted rows: a completed BO3 has >=12 games total
            # for the pair; a single player winning <3 games in a non-swept loss is
            # a RET. Keep only plausible completed-match player totals.
            if not (2 <= gw <= 30):
                continue
            seen.add(eid)
            rows.append((bool(won), int(sp), float(gw), fmt))
    return rows


def scenario(won, sp, fmt):
    if fmt == "BO3":
        if won and sp == 2:  return "S1"
        if won and sp == 3:  return "S2"
        if not won and sp == 3: return "S3"
        if not won and sp == 2: return "S4"
    else:  # BO5
        if won and sp == 3:  return "S1"
        if won and sp >= 4:  return "S2"
        if not won and sp >= 4: return "S3"
        if not won and sp == 3: return "S4"
    return None


def summarize(rows, label):
    bo3 = [r for r in rows if r[3] == "BO3"]
    print("\n=== %s ===  n=%d  (BO3=%d BO5=%d)" % (
        label, len(rows), len(bo3), len(rows) - len(bo3)))
    if len(bo3) < 40:
        print("  NOT ENOUGH BO3 rows to fit")
        return None
    wins = [r for r in bo3 if r[0]]
    loss = [r for r in bo3 if not r[0]]
    p3_win  = sum(1 for r in wins if r[1] == 3) / len(wins) if wins else 0
    p3_lose = sum(1 for r in loss if r[1] == 3) / len(loss) if loss else 0
    print("  P(3 sets | win)  = %.3f   (n=%d)" % (p3_win, len(wins)))
    print("  P(3 sets | lose) = %.3f   (n=%d)" % (p3_lose, len(loss)))
    buckets = collections.defaultdict(list)
    for r in bo3:
        s = scenario(r[0], r[1], r[3])
        if s:
            buckets[s].append(r[2])
    out = {"p3_win": round(p3_win, 3), "p3_lose": round(p3_lose, 3), "scen": {}}
    print("  scenario   n     mean   sd     (player games won)")
    for s in ("S1", "S2", "S3", "S4"):
        v = buckets.get(s, [])
        if len(v) < 8:
            print("    %s      %-4d  (too few — fallback)" % (s, len(v)))
            continue
        mu = statistics.mean(v)
        sd = statistics.pstdev(v) if len(v) > 1 else 1.5
        out["scen"][s] = (round(mu, 2), round(sd, 2))
        print("    %s      %-4d  %-5.2f  %-5.2f" % (s, len(v), mu, sd))
    return out


import json, os
_CACHE = "C:/Users/shawn/AppData/Local/Temp/claude/E--claude-tennis-app/347ffff2-ae6e-4883-84c9-20005ba38489/scratchpad/ptgw_rows.json"
if os.path.exists(_CACHE):
    print("Loading cached rows...")
    _d = json.load(open(_CACHE))
    atp = [tuple(r) for r in _d["atp"]]
    wta = [tuple(r) for r in _d["wta"]]
else:
    print("Collecting ATP...")
    atp = collect("ATP", ATP)
    print("Collecting WTA...")
    wta = collect("WTA", WTA)
    json.dump({"atp": atp, "wta": wta}, open(_CACHE, "w"))
a = summarize(atp, "ATP")
w = summarize(wta, "WTA")
print("\n\nPROPOSED CONSTANTS (paste into props.py _PTGW_SCEN_FIT):")
print("ATP:", a)
print("WTA:", w)
