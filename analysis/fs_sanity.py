"""FS shadow sanity on the 7/16 dog-vs-favorite spots. Confirms (a) the FS
distribution is bimodal (S1 win-straights FS far above S4 lose-straights FS), and
(b) the moneyline bound: at a LOW line every win clears FS, so P(over) >= P(win).
Reports plainly, null results included. Reuses the logged 7/16 matchups.
"""
import sys, time
sys.path.insert(0, "E:/baseline/discord-bot")
import logging; logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod
FS = "Fantasy Score"

# The 7/16 dog spots (from the tracker): player (the dog), opponent (favorite), surface.
SPOTS = [
    ("Gina Feistel", "Laura Samson", "Clay"),
    ("Jaime Faria", "Casper Ruud", "Clay"),
    ("Andrea Pellegrino", "Andrey Rublev", "Clay"),
    ("Nikoloz Basilashvili", "Thiago Agustin Tirante", "Clay"),
]


def wait_for_fs(tries=40):
    print("Waiting for the FS backend branch to deploy...")
    pr = pod._get("/api/search", {"query": "Sabalenka", "tour": "WTA"}, 20)
    o = pod._get("/api/search", {"query": "Gauff", "tour": "WTA"}, 20)
    for i in range(tries):
        try:
            d = pod._post("/api/prop/calculate", {
                "player_id": str(pr[0]["id"]), "opponent_id": str(o[0]["id"]),
                "player_name": "Sabalenka", "opponent_name": "Gauff",
                "tour": "WTA", "surface": "Hard", "court": "",
                "prop_type": FS, "prop_line": 20.5}, 180)
            if d and d.get("fs_p_over") is not None:
                print("  FS live (sample p_over=%.3f conf=%s)\n" % (
                    d["fs_p_over"], d.get("confidence")))
                return True
            print("  check %d: no fs_p_over yet" % (i + 1))
        except Exception as e:
            print("  check %d: %s" % (i + 1, str(e)[:70]))
        time.sleep(20)
    return False


def fs_call(player, opp, surface, line, debug=False):
    pr = pod._get("/api/search", {"query": player}, 20)
    orr = pod._get("/api/search", {"query": opp}, 20)
    if not pr or not orr:
        return None
    payload = {"player_id": str(pr[0]["id"]), "opponent_id": str(orr[0]["id"]),
               "player_name": player, "opponent_name": opp,
               "tour": pr[0].get("tour") or "ATP", "surface": surface, "court": "",
               "prop_type": FS, "prop_line": line}
    if debug:
        payload["debug"] = True
    return pod._post("/api/prop/calculate", payload, 180)


def main():
    if not wait_for_fs():
        print("FS deploy not detected — aborting."); return
    print("=== FS 7/16 dog-spot sanity ===\n")
    for player, opp, surf in SPOTS:
        # A mid line (near the mean) for shape, and a LOW line for the moneyline bound.
        d = fs_call(player, opp, surf, 12.5, debug=True)
        if not d:
            print("  %-20s — could not resolve/評価\n" % player); continue
        p_win = (d.get("p1_win_prob") or 0) / 100.0
        # Pull the per-scenario FS means from the FS_scenario_mixture trace step.
        s_mu = {}
        for step in (d.get("component_trace") or []):
            if step.get("name") == "FS_scenario_mixture":
                bd = (step.get("inputs") or {}).get("scenario_breakdown") or {}
                s_mu = {k: v.get("fs_mu") for k, v in bd.items()}
        # Low-line moneyline-bound test.
        low_line = 3.5
        d_low = fs_call(player, opp, surf, low_line)
        p_over_low = d_low.get("fs_p_over") if d_low else None
        bound_ok = (p_over_low is not None and p_win is not None
                    and p_over_low >= p_win - 0.02)
        print("  %-20s p_win=%.2f | mixture_mean=%.1f p_over(12.5)=%.3f conf=%s" % (
            player, p_win, d.get("model_projection") or 0, d.get("fs_p_over") or 0,
            d.get("confidence")))
        if s_mu:
            print("     bimodal: S1(win-2) FS=%.1f  S2(win-3)=%.1f  S3(lose-3)=%.1f  "
                  "S4(lose-2)=%.1f  [spread %.1f]" % (
                      s_mu.get("S1", 0), s_mu.get("S2", 0), s_mu.get("S3", 0),
                      s_mu.get("S4", 0), (s_mu.get("S1", 0) - s_mu.get("S4", 0))))
        print("     moneyline bound @line %.1f: p_over=%.3f vs p_win=%.3f -> %s" % (
            low_line, p_over_low or 0, p_win,
            "HOLDS (>=p_win)" if bound_ok else "below p_win (see note)"))
        print("     claim:", d.get("fs_implied_claim"), "\n")


main()
