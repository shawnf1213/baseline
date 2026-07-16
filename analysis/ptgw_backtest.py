"""PTGW backtest v2 — logged Pick records re-run through the NEW scenario-mixture
chain. Uses the full 86-pick log (first list in record_summary). Reports old
posted confidence/lean vs new P(over)/confidence/lean, with the null-result check:
does any losing pick still post at >=80 under the new chain? No tuning to pass.
Waits for the lean-fix redeploy by checking a known OVER stays OVER.
"""
import sys, time
sys.path.insert(0, "E:/baseline/discord-bot")
import logging; logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod
PTGW = "Player Total Games Won"
FOUR = {"feistel", "basilashvili", "pellegrino", "faria"}
PTGW_BAR = 80


def _last(n): return (n or "").strip().split()[-1].lower() if n else ""


def wait_for_leanfix(tries=40):
    print("Waiting for the lean-fix redeploy (Sabalenka/Gauff must read OVER)...")
    pr = pod._get("/api/search", {"query": "Sabalenka", "tour": "WTA"}, 20)
    o = pod._get("/api/search", {"query": "Gauff", "tour": "WTA"}, 20)
    for i in range(tries):
        try:
            d = pod._post("/api/prop/calculate", {
                "player_id": str(pr[0]["id"]), "opponent_id": str(o[0]["id"]),
                "player_name": "Sabalenka", "opponent_name": "Gauff",
                "tour": "WTA", "surface": "Hard", "court": "",
                "prop_type": PTGW, "prop_line": 11.5}, 180)
            if d and d.get("ptgw_p_over") is not None and d.get("lean") == "OVER":
                print("  lean-fix live (p_over=%.3f lean=%s conf=%s)\n"
                      % (d["ptgw_p_over"], d["lean"], d.get("confidence")))
                return True
            print("  check %d: lean=%s (waiting)" % (i + 1, d.get("lean") if d else "?"))
        except Exception as e:
            print("  check %d: %s" % (i + 1, str(e)[:70]))
        time.sleep(20)
    return False


def rerun(p):
    try:
        pr = pod._get("/api/search", {"query": p.get("player")}, 20)
        orr = pod._get("/api/search", {"query": p.get("opponent")}, 20)
        if not pr or not orr:
            return None
        return pod._post("/api/prop/calculate", {
            "player_id": str(pr[0]["id"]), "opponent_id": str(orr[0]["id"]),
            "player_name": p.get("player"), "opponent_name": p.get("opponent"),
            "tour": pr[0].get("tour") or "ATP",
            "surface": p.get("surface") or "Hard", "court": p.get("tournament") or "",
            "prop_type": PTGW, "prop_line": p.get("line")}, 180)
    except Exception as e:
        print("   rerun failed %s: %s" % (p.get("player"), str(e)[:70]))
        return None


def main():
    if not wait_for_leanfix():
        print("lean-fix not detected — aborting."); return
    rec = pod._get("/api/results/record", {}, 60)
    picks = []
    for k in (rec or {}):
        if isinstance(rec[k], list) and len(rec[k]) > len(picks):
            picks = rec[k]
    ptgw = [p for p in picks if p.get("prop_type") == PTGW]
    print("PTGW picks in tracker: %d (all statuses)\n" % len(ptgw))

    print("%-15s %-5s %-5s %-4s %-4s | %-6s %-4s %-5s %-4s %-5s %-5s" % (
        "player", "lean", "line", "oCf", "res", "p_over", "nCf", "nLn", "flip",
        "oPost", "nPost"))
    old_post = old_hit = new_post = new_hit = 0
    still_bad = []
    for p in ptgw:
        d = rerun(p)
        if not d:
            continue
        old_c, old_l = p.get("confidence"), (p.get("lean") or "").upper()
        res = (p.get("result") or "").upper()
        nov, nc, nl = d.get("ptgw_p_over"), d.get("confidence"), d.get("lean")
        won = res == "W"
        # 3 of 4 named 7/16 losers resolved as L; Faria still PENDING but factually
        # lost (Faria U10.5 -> 13). Treat the named four as losers for the null check.
        is_loser = (res == "L") or (_last(p.get("player")) in FOUR)
        flip = (nl or "") != old_l
        old_would = isinstance(old_c, (int, float)) and old_c >= PTGW_BAR
        new_would = isinstance(nc, (int, float)) and nc >= PTGW_BAR
        if res in ("W", "L"):
            if old_would:
                old_post += 1; old_hit += int(won)
            if new_would and not flip:
                new_post += 1; new_hit += int(won)
        if new_would and is_loser and not flip:
            still_bad.append((p.get("player"), old_l, p.get("line"), nc, nov))
        flag = "  <-7/16" if _last(p.get("player")) in FOUR else ""
        print("%-15s %-5s %-5s %-4s %-4s | %-6s %-4s %-5s %-4s %-5s %-5s%s" % (
            (p.get("player") or "")[:15], old_l, p.get("line"),
            "%.0f" % old_c if isinstance(old_c, (int, float)) else "?", res,
            "%.3f" % nov if isinstance(nov, (int, float)) else "?",
            "%.0f" % nc if isinstance(nc, (int, float)) else "?", nl,
            "YES" if flip else "-", "Y" if old_would else "n",
            "Y" if new_would else "n", flag))

    print("\n=== SUMMARY (resolved W/L only) ===")
    print("OLD: posted %d, won %d  (%s)" % (old_post, old_hit,
          "%.0f%%" % (100 * old_hit / old_post) if old_post else "n/a"))
    print("NEW: would-post %d, won %d  (%s)" % (new_post, new_hit,
          "%.0f%%" % (100 * new_hit / new_post) if new_post else "n/a"))
    print("\nNULL-RESULT CHECK — any losing side still posting at >=%d under the new"
          " chain:" % PTGW_BAR)
    if not still_bad:
        print("  NONE. No loser survives the new chain at the 80 bar.")
    else:
        for b in still_bad:
            print("  STILL POSTS LOSER: %s %s%.1f  new_conf=%.0f p_over=%.3f" % b)


main()
