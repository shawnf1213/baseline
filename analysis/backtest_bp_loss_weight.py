"""A2 gate: OUT-OF-SAMPLE sweep of BP_LOSS_MATCHUP_WEIGHT.

WHY NOT "resolved picks": the picks table (backend/src/database.py) stores only
line / model_projection / lean / result — NOT the mixture inputs (base_proj, the
market-anchored win prob, tour). And Sofascore match records carry no rankings or
odds. So the literal "sweep the weight on resolved BP picks" cannot be run: the
per-pick inputs the mixture needs were never persisted. Reported plainly, not
worked around silently.

WHAT IS runnable, and tests EXACTLY the parameter: the loss weight governs how much
the matchup LEVEL (base_proj / base_pop) carries into the break count WHEN THE
PLAYER LOSES. Every resolved match is a labelled BP situation with the ground truth
the picks lack — realized breaks + realized outcome. Conditioning on the REALIZED
outcome removes the win-prob term entirely (the win/lose mix is PTGW machinery,
already validated), leaving a clean test of the scenario means × loss scale.

METHOD (out-of-sample):
  • Per player, split matches 50/50 by time. TRAIN half -> the player's LEVEL
    (mean breaks). TEST half -> evaluation. Level from train only = no leakage.
  • For each TEST match: scenario S1..S4 from (won, sets, fmt); base_scale =
    clamp(level/base_pop, 0.5, 2.0); for each candidate weight w, predicted mean =
    scen_mean[S] × scale, scale = base_scale (win S1/S2) or 1+(base_scale−1)·w
    (loss S3/S4). Win-scenario predictions are identical across w, so the sweep is
    differentiated PURELY by loss scenarios — the parameter's exact domain.
  • Metrics per w (report LOSS-subset prominently — that is where w acts):
      MAE   mean |predicted_mean − realized breaks|
      Brier over a realistic line grid, mean (P(over) − 1{breaks>line})²
      ACC   lean accuracy: 1{P(over)>0.5} == 1{breaks>line}
  The w minimising out-of-sample loss-subset MAE/Brier is the DATA's choice.
"""
import sys, statistics, collections, json, os
sys.path.insert(0, "E:/baseline/discord-bot")
sys.path.insert(0, "E:/baseline/backend")
import logging
logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod
from src.calculations.props import (
    _BP_SCEN_FIT, _BP_BASE_POP, _norm_sf, _BP_SCALE_LO, _BP_SCALE_HI)

ATP = ["Rublev", "Sonego", "Cobolli", "Darderi", "Etcheverry", "Navone", "Munar",
       "Tsitsipas", "Bublik", "Halys", "Basilashvili", "Tirante", "Pellegrino",
       "Collignon", "Vacherot", "Baez", "Tabilo", "Zverev", "Ruud", "Cerundolo",
       "Djokovic", "Medvedev", "Fritz", "Shelton", "Musetti", "Dimitrov"]
WTA = ["Sasnovich", "Jones", "Urgesi", "Feistel", "Samson", "Erjavec", "Barthel",
       "Valentova", "Sakkari", "Dart", "Badosa", "Bronzetti", "Parks", "Chiesa",
       "Andreeva", "Zheng", "Krejcikova", "Tauson", "Rus", "Grabher",
       "Swiatek", "Gauff", "Sabalenka", "Pegula", "Kalinskaya", "Vondrousova"]

WEIGHTS = [0.0, 0.25, 0.35, 0.5, 1.0]
LINE_GRID = {"ATP": [1.5, 2.5, 3.5, 4.5], "WTA": [2.5, 3.5, 4.5, 5.5, 6.5]}
_CACHE = ("C:/Users/shawn/AppData/Local/Temp/claude/E--claude-tennis-app/"
          "347ffff2-ae6e-4883-84c9-20005ba38489/scratchpad/bp_bt_rows.json")


def scenario(won, sp, fmt):
    if fmt == "BO3":
        return {(True, 2): "S1", (True, 3): "S2", (False, 3): "S3", (False, 2): "S4"}.get((won, sp))
    return None  # BO5 excluded — too few for a stable per-player split


def collect(tour, names):
    """Per player -> chronological list of (won, sets, breaks). BO3 only, RET dropped."""
    out = {}
    for q in names:
        try:
            res = pod._get("/api/search", {"query": q, "tour": tour}, 20)
            if not res:
                continue
            d = pod._post("/api/player/stats",
                          {"player_id": str(res[0]["id"]), "tour": tour, "player_name": q}, 180)
        except Exception:
            continue
        seen, rows = set(), []
        for m in (d.get("all_matches") or []):
            eid = m.get("event_id")
            if eid in seen:
                continue
            brk, sp, won, ts = (m.get("bp_converted_count"), m.get("sets_played"),
                                m.get("won"), m.get("timestamp") or 0)
            if not isinstance(brk, (int, float)) or sp not in (2, 3) or won is None:
                continue
            if m.get("player_retired") or not (0.0 <= brk <= 15.0):
                continue
            seen.add(eid)
            rows.append((ts, bool(won), int(sp), float(brk)))
        rows.sort(key=lambda r: r[0])           # chronological
        if len(rows) >= 8:
            out[q] = rows
    return out


if os.path.exists(_CACHE):
    print("Loading cached rows...")
    _d = json.load(open(_CACHE))
    data = {"ATP": _d["ATP"], "WTA": _d["WTA"]}
else:
    print("Collecting ATP..."); atp = collect("ATP", ATP)
    print("Collecting WTA..."); wta = collect("WTA", WTA)
    data = {"ATP": atp, "WTA": wta}
    json.dump({"ATP": atp, "WTA": wta}, open(_CACHE, "w"))


def run(tour):
    base_pop = _BP_BASE_POP[tour]
    sfit = _BP_SCEN_FIT[tour]["scen"]
    lines = LINE_GRID[tour]
    # Build out-of-sample TEST observations: (scenario, base_scale, realized_breaks)
    test = []
    n_players = 0
    for player, rows in data[tour].items():
        if len(rows) < 8:
            continue
        n_players += 1
        half = len(rows) // 2
        train, tst = rows[:half], rows[half:]
        level = statistics.mean([r[3] for r in train])     # mean breaks, TRAIN only
        base_scale = max(_BP_SCALE_LO, min(_BP_SCALE_HI, level / base_pop)) if base_pop else 1.0
        for (_ts, won, sp, brk) in tst:
            s = scenario(won, sp, "BO3")
            if s:
                test.append((s, base_scale, brk, won))
    n_win = sum(1 for t in test if t[3])
    n_loss = len(test) - n_win
    print("\n=== %s ===  players=%d  test_obs=%d  (wins=%d losses=%d)"
          % (tour, n_players, len(test), n_win, n_loss))
    print("  loss-subset is where the weight acts; win-subset is identical across w (shown as control)\n")
    print("  weight |  LOSS MAE  LOSS Brier  LOSS acc | ALL MAE  ALL Brier  ALL acc")
    best = None
    for w in WEIGHTS:
        agg = {"loss": {"ae": [], "brier": [], "acc": []},
               "all":  {"ae": [], "brier": [], "acc": []}}
        for (s, base_scale, brk, won) in test:
            mu, sd = sfit[s]
            scale = base_scale if s in ("S1", "S2") else 1.0 + (base_scale - 1.0) * w
            pmu, psd = mu * scale, sd * scale
            ae = abs(pmu - brk)
            for L in lines:
                po = _norm_sf(L, pmu, psd)
                realized = 1.0 if brk > L else 0.0
                brier = (po - realized) ** 2
                acc = 1.0 if (po > 0.5) == (realized > 0.5) else 0.0
                agg["all"]["brier"].append(brier); agg["all"]["acc"].append(acc)
                if not won:
                    agg["loss"]["brier"].append(brier); agg["loss"]["acc"].append(acc)
            agg["all"]["ae"].append(ae)
            if not won:
                agg["loss"]["ae"].append(ae)
        lm = statistics.mean(agg["loss"]["ae"]); lb = statistics.mean(agg["loss"]["brier"])
        la = statistics.mean(agg["loss"]["acc"])
        am = statistics.mean(agg["all"]["ae"]); ab = statistics.mean(agg["all"]["brier"])
        aa = statistics.mean(agg["all"]["acc"])
        star = ""
        if best is None or lm < best[1]:
            best = (w, lm)
        print("   %.2f   |   %.3f     %.3f      %.3f  |  %.3f    %.3f     %.3f"
              % (w, lm, lb, la, am, ab, aa))
    print("  -> min out-of-sample LOSS MAE at weight = %.2f" % best[0])
    return best


print("\n" + "=" * 74)
ra = run("ATP")
rw = run("WTA")
print("\n" + "=" * 74)
print("DATA'S CHOICE (min out-of-sample loss-subset MAE):  ATP w=%.2f  WTA w=%.2f"
      % (ra[0], rw[0]))
print("Shipped default BP_LOSS_MATCHUP_WEIGHT = 0.35 (initial). Adjust from this table.")
