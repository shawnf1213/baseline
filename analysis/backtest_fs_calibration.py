"""C3 gate: OUT-OF-SAMPLE calibration of the Fantasy Score scenario-mixture prop.

Same data limitation as the BP backtest: the picks table persists no FS projection
inputs and match records carry no odds, so a "resolved FS picks" sweep is not
runnable. Instead, test the SHIPPED model (props.project_fantasy_score) against the
REALIZED FS every resolved match provides. FS is fully reconstructable from the
Sofascore per-match record:
    FS = 10 + (games_won − games_lost) + 3·(sets_won − sets_lost)
         + 0.5·aces − 0.5·double_faults
so each match is a labelled FS observation.

METHOD (out-of-sample, disclosed proxy):
  • Per player, split matches 50/50 by time. TRAIN half → the model inputs
    (mean aces, mean df, mean sets, WIN RATE as the p_sel proxy). TEST half →
    evaluation. Level/rate from train only = no leakage.
  • For each TEST match: call project_fantasy_score with the train inputs at a grid
    of lines around the realized FS; record predicted P(over) vs realized 1{FS>line}.
  • Report a calibration table (predicted-P(over) bucket → realized over-rate, the
    diagonal = perfect), plus Brier and lean accuracy.

DISCLOSED APPROXIMATION: p_sel here is the player's TRAIN win rate, NOT a market-
anchored moneyline (unavailable historically). The live prop anchors p_sel to the
de-vigged moneyline (0.7 market / 0.3 model), so live calibration is expected to be
at least as good as this. This backtest gates the SCENARIO-MIXTURE SHAPE, not the
anchor. A well-calibrated table here is necessary, not sufficient, for star-
eligibility; the anchor quality is validated separately (it is shared with PTGW).
"""
import sys, statistics, collections, json, os
sys.path.insert(0, "E:/baseline/discord-bot")
sys.path.insert(0, "E:/baseline/backend")
import logging
logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod
from src.calculations.props import project_fantasy_score

ATP = ["Rublev", "Sonego", "Cobolli", "Darderi", "Etcheverry", "Navone", "Munar",
       "Tsitsipas", "Bublik", "Halys", "Basilashvili", "Tirante", "Pellegrino",
       "Collignon", "Vacherot", "Baez", "Tabilo", "Zverev", "Ruud", "Cerundolo",
       "Djokovic", "Medvedev", "Fritz", "Shelton", "Musetti", "Dimitrov"]
WTA = ["Sasnovich", "Jones", "Urgesi", "Feistel", "Samson", "Erjavec", "Barthel",
       "Valentova", "Sakkari", "Dart", "Badosa", "Bronzetti", "Parks", "Chiesa",
       "Andreeva", "Zheng", "Krejcikova", "Tauson", "Rus", "Grabher",
       "Swiatek", "Gauff", "Sabalenka", "Pegula", "Kalinskaya", "Vondrousova"]
_CACHE = ("C:/Users/shawn/AppData/Local/Temp/claude/E--claude-tennis-app/"
          "347ffff2-ae6e-4883-84c9-20005ba38489/scratchpad/fs_cal_rows.json")


def realized_fs(won, sp, gw, tmg, aces, df):
    games_lost = max(0, tmg - gw)
    if won:
        sets_won, sets_lost = 2, sp - 2
    else:
        sets_won, sets_lost = sp - 2, 2
    return 10 + (gw - games_lost) + 3 * (sets_won - sets_lost) + 0.5 * aces - 0.5 * df


def collect(tour, names):
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
            won, sp = m.get("won"), m.get("sets_played")
            gw, tmg = m.get("total_games_won"), m.get("total_match_games")
            ac, df = m.get("aces"), m.get("double_faults")
            ts = m.get("timestamp") or 0
            if won is None or sp not in (2, 3) or m.get("player_retired"):
                continue
            if not all(isinstance(x, (int, float)) for x in (gw, tmg, ac, df)):
                continue
            if tmg < gw or tmg <= 0 or tmg > 60:
                continue
            seen.add(eid)
            rows.append((ts, bool(won), int(sp), float(gw), float(tmg), float(ac), float(df)))
        rows.sort(key=lambda r: r[0])
        if len(rows) >= 8:
            out[q] = rows
    return out


if os.path.exists(_CACHE):
    print("Loading cached rows...")
    _d = json.load(open(_CACHE)); data = {"ATP": _d["ATP"], "WTA": _d["WTA"]}
else:
    print("Collecting ATP..."); atp = collect("ATP", ATP)
    print("Collecting WTA..."); wta = collect("WTA", WTA)
    data = {"ATP": atp, "WTA": wta}
    json.dump({"ATP": atp, "WTA": wta}, open(_CACHE, "w"))


def run(tour):
    obs = []          # (pred_p_over, realized_over, pred_mean, realized_fs)
    n_players = 0
    for player, rows in data[tour].items():
        if len(rows) < 8:
            continue
        n_players += 1
        half = len(rows) // 2
        train, test = rows[:half], rows[half:]
        win_rate = statistics.mean([1.0 if r[1] else 0.0 for r in train])
        ace_proj = statistics.mean([r[5] for r in train])
        df_proj = statistics.mean([r[6] for r in train])
        exp_sets = statistics.mean([r[2] for r in train])
        p_sel = max(0.05, min(0.95, win_rate))
        for (_ts, won, sp, gw, tmg, ac, df) in test:
            rfs = realized_fs(won, sp, gw, tmg, ac, df)
            # grid of lines around the realized value so we probe both sides
            for L in (rfs - 6, rfs - 3, rfs, rfs + 3, rfs + 6):
                try:
                    r = project_fantasy_score(
                        p_sel=p_sel, ace_proj=ace_proj, df_proj=df_proj,
                        expected_sets=exp_sets, prop_line=L, tour=tour,
                        match_format="best_of_3", player_name=player, trace=None)
                except Exception:
                    continue
                po = r.get("fs_p_over")
                if po is None:
                    continue
                obs.append((po, 1.0 if rfs > L else 0.0, r.get("fs_mixture_mean"), rfs))
    if not obs:
        print("\n=== %s ===  no observations" % tour); return
    brier = statistics.mean([(po - ro) ** 2 for po, ro, _, _ in obs])
    acc = statistics.mean([1.0 if (po > 0.5) == (ro > 0.5) else 0.0 for po, ro, _, _ in obs])
    means = [(pm, rf) for _, _, pm, rf in obs if isinstance(pm, (int, float))]
    mae = statistics.mean([abs(pm - rf) for pm, rf in means]) if means else float("nan")
    print("\n=== %s ===  players=%d  test (match×line) obs=%d" % (tour, n_players, len(obs)))
    print("  predicted-P(over) bucket -> realized over-rate  (diagonal = calibrated)")
    buckets = collections.defaultdict(list)
    for po, ro, _, _ in obs:
        buckets[min(9, int(po * 10))].append(ro)
    for b in range(10):
        v = buckets.get(b, [])
        if v:
            lo, hi = b / 10.0, (b + 1) / 10.0
            print("    %.1f-%.1f  n=%-4d  realized_over=%.3f" % (lo, hi, len(v), statistics.mean(v)))
    print("  Brier=%.3f  lean_acc=%.3f  point-MAE(mean vs realized FS)=%.2f" % (brier, acc, mae))


print("\n" + "=" * 74)
run("ATP")
run("WTA")
print("\n" + "=" * 74)
print("Reads as calibrated if realized-over-rate rises monotonically with the")
print("predicted-P(over) bucket and tracks the diagonal. p_sel = TRAIN win rate")
print("(disclosed proxy for the live de-vigged moneyline anchor).")
