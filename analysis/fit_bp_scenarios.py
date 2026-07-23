"""Empirical breaks-by-scenario fit for the A2 Break Points Won rebuild.

SAME source + method as the approved PTGW games fit (analysis/fit_ptgw_scenarios.py)
— Sofascore per-match records. Each match record carries, for the SELECTED player:
    won                 -> True/False           (match result)
    sets_played         -> 2/3 (BO3) or 3/4/5 (BO5)
    bp_converted_count  -> the player's BREAKS won in the match  (the BP variable)
So every match is a labelled observation for the four scenarios:
    S1 win-in-2 | S2 win-in-3 | S3 lose-in-3 | S4 lose-in-2.

Outputs per tour (ATP/WTA), BO3: per-scenario BREAKS mean & sd. The scenario
PROBABILITIES (p3_win/p3_lose) are a property of the match, not the prop, so the
BP mixture reuses _PTGW_SCEN_FIT's — we only need the per-scenario break
distributions here. Deduped by event_id. Retirements dropped.
"""
import sys, statistics, collections, json, os
sys.path.insert(0, "E:/baseline/discord-bot")
import logging
logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod

# Same rosters as the PTGW fit for comparability.
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
            brk = m.get("bp_converted_count")
            sp = m.get("sets_played")
            won = m.get("won")
            if not isinstance(brk, (int, float)):
                continue
            if sp not in (2, 3, 4, 5) or won is None:
                continue
            if m.get("player_retired"):          # RET corrupts the break count
                continue
            fmt = "BO5" if sp >= 4 else "BO3"
            if fmt == "BO3" and sp not in (2, 3):
                continue
            # Plausible completed-match break count (a full match rarely exceeds ~12).
            if not (0.0 <= brk <= 15.0):
                continue
            seen.add(eid)
            rows.append((bool(won), int(sp), float(brk), fmt))
    return rows


def scenario(won, sp, fmt):
    if fmt == "BO3":
        if won and sp == 2:      return "S1"
        if won and sp == 3:      return "S2"
        if not won and sp == 3:  return "S3"
        if not won and sp == 2:  return "S4"
    else:  # BO5
        if won and sp == 3:      return "S1"
        if won and sp >= 4:      return "S2"
        if not won and sp >= 4:  return "S3"
        if not won and sp == 3:  return "S4"
    return None


def summarize(rows, label):
    bo3 = [r for r in rows if r[3] == "BO3"]
    print("\n=== %s ===  n=%d  (BO3=%d BO5=%d)" % (
        label, len(rows), len(bo3), len(rows) - len(bo3)))
    if len(bo3) < 40:
        print("  NOT ENOUGH BO3 rows to fit")
        return None
    buckets = collections.defaultdict(list)
    for r in bo3:
        s = scenario(r[0], r[1], r[3])
        if s:
            buckets[s].append(r[2])
    out = {"scen": {}}
    print("  scenario   n     mean   sd     (player BREAKS won)")
    for s in ("S1", "S2", "S3", "S4"):
        v = buckets.get(s, [])
        if len(v) < 8:
            print("    %s      %-4d  (too few — fallback)" % (s, len(v)))
            continue
        mu = statistics.mean(v)
        sd = statistics.pstdev(v) if len(v) > 1 else 1.5
        out["scen"][s] = (round(mu, 2), round(max(sd, 0.5), 2))
        print("    %s      %-4d  %-5.2f  %-5.2f" % (s, len(v), mu, sd))
    return out


_CACHE = ("C:/Users/shawn/AppData/Local/Temp/claude/E--claude-tennis-app/"
          "347ffff2-ae6e-4883-84c9-20005ba38489/scratchpad/bp_rows.json")
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
print("\n\nPROPOSED CONSTANTS (paste into props.py _BP_SCEN_FIT):")
print("ATP:", a)
print("WTA:", w)
