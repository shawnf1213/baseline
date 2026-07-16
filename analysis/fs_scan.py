"""PART 2 — FS-ONLY shadow scan through the corrected chain (anchor + median).
Filters the PrizePicks board to Fantasy Score only, evaluates every FS line,
returns the full table (including passes), old_P vs new_P, flags, and the worked
Ruud-equivalent decomposition pre-anchor vs post-anchor. Writes to a shadow log.
Nothing posts. FS_ENABLED stays false."""
import sys, time, asyncio, json
sys.path.insert(0, "E:/baseline/discord-bot")
sys.path.insert(0, "E:/baseline/backend")
sys.path.insert(0, "E:/baseline/backend/src")
import logging; logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as pod
import src.calculations.props as props

FS = "Fantasy Score"
LOG = "E:/baseline/analysis/fs_shadow_scan.log"


def scen_probs(wp, tour, fmt):
    """Scenario probabilities at a given win prob — replicates the mixture's own
    computation so we can produce the OLD (model-only/unanchored) P alongside."""
    fit = props._PTGW_SCEN_BO5 if fmt == "best_of_5" else props._PTGW_SCEN_FIT.get(tour, props._PTGW_SCEN_FIT["ATP"])
    gap = wp - 0.5
    p3w = max(props._PTGW_P3_MIN, min(props._PTGW_P3_MAX, fit["p3_win"] - props._PTGW_GAP_K * gap))
    p3l = max(props._PTGW_P3_MIN, min(props._PTGW_P3_MAX, fit["p3_lose"] + props._PTGW_GAP_K * gap))
    return {"S1": wp * (1 - p3w), "S2": wp * p3w, "S3": (1 - wp) * p3l, "S4": (1 - wp) * (1 - p3l)}


def p_over_at(wp, breakdown, tour, fmt):
    sp = scen_probs(wp, tour, fmt)
    return sum(sp[s] * breakdown[s]["p_over"] for s in ("S1", "S2", "S3", "S4"))


def wait_deploy(tries=40):
    print("Waiting for Part 1 (anchor+median) to deploy...")
    pr = pod._get("/api/search", {"query": "Ruud", "tour": "ATP"}, 20)
    o = pod._get("/api/search", {"query": "Cerundolo", "tour": "ATP"}, 20)
    for i in range(tries):
        try:
            d = pod._post("/api/prop/calculate", {
                "player_id": str(pr[0]["id"]), "opponent_id": str(o[0]["id"]),
                "player_name": "Ruud", "opponent_name": "Cerundolo", "tour": "ATP",
                "surface": "Clay", "court": "", "prop_type": FS, "prop_line": 21.0}, 150)
            if d and d.get("fs_fair_line") is not None and "fs_market_wp" in d:
                print("  live (sample: fair_line=%s market_wp=%s anchored=%s)\n" % (
                    d.get("fs_fair_line"), d.get("fs_market_wp"), d.get("fs_anchored")))
                return True
        except Exception as e:
            print("  check %d: %s" % (i + 1, str(e)[:50]))
        time.sleep(20)
    return False


async def main():
    if not wait_deploy():
        print("deploy not detected — aborting."); return
    board = await asyncio.to_thread(pod._fetch_board)
    props_all = pod._parse_board(board)
    fs_props = [p for p in props_all if p.get("prop_type") == FS]
    print("FS lines on the current board: %d\n" % len(fs_props))
    if not fs_props:
        print("NULL RESULT: no Fantasy Score lines on the board right now.")
        open(LOG, "w").write("FS scan: no FS lines on board\n")
        return

    sem = asyncio.Semaphore(4)
    cands = await asyncio.gather(*[pod._evaluate(p, sem) for p in fs_props],
                                 return_exceptions=True)

    rows = []
    unpriced = []
    for prop, c in zip(fs_props, cands):
        if not isinstance(c, dict):
            unpriced.append((prop, str(c)[:80])); continue
        d = c.get("data") or {}
        bd = d.get("fs_scenario_breakdown")
        if d.get("fs_p_over") is None or not bd:
            unpriced.append((prop, "chain returned no FS probability")); continue
        tour = c.get("tour") or "ATP"
        fmt = "best_of_5" if (prop.get("standard_line") and False) else "best_of_3"
        lean = (d.get("lean") or "").upper()
        p_over_new = d.get("fs_p_over")
        model_wp = d.get("fs_model_wp"); blended_wp = d.get("fs_blended_wp")
        # OLD P = unanchored (model-only win prob), same per-scenario FS bands.
        p_over_old = p_over_at(model_wp, bd, tour, fmt) if model_wp is not None else p_over_new
        side = lambda po: (po if lean == "OVER" else 1 - po)
        flags = []
        if d.get("fs_divergent"): flags.append("divergence")
        if d.get("fs_anchored") is False: flags.append("unanchored")
        if prop.get("odds_type") == "demon": flags.append("demon(unpostable)")
        # thin-data: either side under 15 stat-rich (from guard note if present)
        gn = d.get("fs_guard_note") or ""
        conf = d.get("confidence") or 0
        rows.append({
            "player": prop["player"], "opp": prop["opponent"], "line": prop["line"],
            "lean": lean, "fair": d.get("fs_fair_line"), "mean": d.get("fs_mixture_mean"),
            "p_hit": round(side(p_over_new), 3), "old_p_hit": round(side(p_over_old), 3),
            "conf": conf, "mwp": model_wp, "mkwp": d.get("fs_market_wp"), "bwp": blended_wp,
            "claim": d.get("fs_implied_claim"), "pos": d.get("fs_line_position"),
            "flags": ",".join(flags) or "-",
            "q_v2": conf >= 65, "q_old": round(side(p_over_old), 3) * 100 >= 80,
            "odds_type": prop.get("odds_type", "standard"),
        })

    rows.sort(key=lambda r: -r["conf"])
    out = []
    out.append("=== FS-ONLY SHADOW SCAN (%d lines) — corrected chain, nothing posted ===" % len(rows))
    for r in rows:
        out.append(
            "%-18s vs %-16s | book %-5s %-5s | fair %-5s (mean %-5s) | P(hit) %.2f (old %.2f) | "
            "conf %-3d | wp m/mk/b %.2f/%s/%.2f | v2q=%s oldq=%s | %s"
            % (r["player"][:18], r["opp"][:16], r["line"], r["lean"], r["fair"], r["mean"],
               r["p_hit"], r["old_p_hit"], r["conf"], r["mwp"],
               ("%.2f" % r["mkwp"]) if r["mkwp"] is not None else "N/A", r["bwp"],
               "Y" if r["q_v2"] else "n", "Y" if r["q_old"] else "n", r["flags"]))
        out.append("      claim: %s  |  %s" % (r["claim"], r["pos"]))
    if unpriced:
        out.append("\nUNPRICED / errored FS lines (reason):")
        for prop, why in unpriced:
            out.append("  %s vs %s line %s — %s" % (prop["player"], prop["opponent"], prop["line"], why))

    # Worked example: highest-line favorite (Ruud-equivalent) decomposition pre/post anchor.
    ex = next((r for r in rows if r["mkwp"] is not None), rows[0] if rows else None)
    if ex:
        pr = pod._get("/api/search", {"query": ex["player"]}, 20)
        orr = pod._get("/api/search", {"query": ex["opp"]}, 20)
        d = pod._post("/api/prop/calculate", {
            "player_id": str(pr[0]["id"]), "opponent_id": str(orr[0]["id"]),
            "player_name": ex["player"], "opponent_name": ex["opp"],
            "tour": pr[0].get("tour") or "ATP", "surface": "Clay", "court": "",
            "prop_type": FS, "prop_line": ex["line"], "debug": True}, 150)
        bd = d.get("fs_scenario_breakdown") or {}
        tour = pr[0].get("tour") or "ATP"
        mwp, bwp = d.get("fs_model_wp"), d.get("fs_blended_wp")
        out.append("\n=== WORKED EXAMPLE: %s %s %s (decomposition pre vs post anchor) ===" % (
            ex["player"], ex["lean"], ex["line"]))
        out.append("  win prob: model %.3f -> market %s -> blended %.3f" % (
            mwp, d.get("fs_market_wp"), bwp))
        sp_old = scen_probs(mwp, tour, "best_of_3")
        sp_new = scen_probs(bwp, tour, "best_of_3")
        out.append("  scen  FS_mean  P(FS>line)  P(scen)_PRE  P(scen)_POST")
        for s in ("S1", "S2", "S3", "S4"):
            out.append("  %-4s  %6.1f   %.3f       %.3f        %.3f" % (
                s, bd[s]["fs_mu"], bd[s]["p_over"], sp_old[s], sp_new[s]))
        po_old = sum(sp_old[s] * bd[s]["p_over"] for s in bd)
        po_new = sum(sp_new[s] * bd[s]["p_over"] for s in bd)
        out.append("  P(over): PRE(unanchored) %.3f  ->  POST(anchored) %.3f" % (po_old, po_new))
        out.append("  fair line: %.1f (median) | divergent=%s | conf=%s" % (
            d.get("fs_fair_line"), d.get("fs_divergent"), d.get("confidence")))

    txt = "\n".join(out)
    print(txt)
    open(LOG, "w", encoding="utf-8").write(txt + "\n")
    print("\n[written to shadow log: %s]" % LOG)


asyncio.run(main())
