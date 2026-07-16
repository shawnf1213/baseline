"""VERIFY board policy v2 vs v1, side by side, on a representative cached slate.
Confidence values are inputs here and are NEVER modified by selection — so "zero
confidence changed" is structural. We check: (a) picks ADDED by v2, (b) NONE
removed (every v1-qualifier still qualifies), (c) star selection old vs new,
(d) the no-POTD fallback path.
"""
import sys
sys.path.insert(0, "E:/baseline/discord-bot")
import logging; logging.basicConfig(level=logging.CRITICAL)
import pick_of_day as p

# A representative scored slate (confidence is the model's output — untouched).
# win_prob on 'data' lets us evaluate v1's TG-90 star rule faithfully.
def cand(player, prop, conf, line=10.5, wp=70.0, lean="OVER"):
    return {"player": player, "prop_type": prop, "confidence": conf, "line": line,
            "lean": lean, "edge": 1.0, "edge_mag": 1.0,
            "data": {"p1_win_prob": wp, "p2_win_prob": 100 - wp,
                     "player_stats": {"service_games_n": 60}}}

slate = [
    cand("A_aces_hi",  "Aces", 82),
    cand("B_aces_mid", "Aces", 68),
    cand("C_bp_mid",   "Break Points Won", 72),
    cand("D_tg_82",    "Total Games", 82, wp=88),
    cand("E_tg_86",    "Total Games", 86, wp=88),
    cand("F_df_hi",    "Double Faults", 90),
    cand("G_aces_lo",  "Aces", 60),
    cand("H_tg_91fav", "Total Games", 84, wp=91),
]

# ── v1 predicates (reconstructed for the comparison ONLY) ────────────────────
def v1_board_qual(c):
    if c["prop_type"] == "Double Faults":     # v1 excluded DF from the board
        return False
    return c["confidence"] >= p._v1_min_conf_for(c["prop_type"])

def v1_star(c):                                # v1 star rules
    prop = c["prop_type"]
    if prop == "Total Games":
        return c["data"]["p1_win_prob"] >= 90.0 or c["data"]["p2_win_prob"] >= 90.0
    if prop == "Player Total Games Won":
        return False   # bespoke; not in this slate
    return True         # Aces / BP always star-eligible under v1 (any conf)

# ── run both ─────────────────────────────────────────────────────────────────
v1_q = [c["player"] for c in slate if v1_board_qual(c)]
v2_q = [c["player"] for c in slate if p._passes_quality(c)]
added   = [x for x in v2_q if x not in v1_q]
removed = [x for x in v1_q if x not in v2_q]

print("=== BOARD QUALIFICATION ===")
for c in slate:
    print("  %-12s %-18s conf=%-3d | v1=%-3s v2=%-3s%s" % (
        c["player"], c["prop_type"], c["confidence"],
        "Y" if v1_board_qual(c) else "n", "Y" if p._passes_quality(c) else "n",
        "   <== ADDED by v2" if (c["player"] in added) else ""))
print("\nADDED by v2:  ", added)
print("REMOVED by v2:", removed, "  (must be empty)")
assert not removed, "REGRESSION: v2 removed a v1-qualifying pick!"

# ── star selection: v1 vs v2 (board-qualified, conf-desc) ────────────────────
print("\n=== PICK OF THE DAY (star) ===")
board_v2 = sorted([c for c in slate if p._passes_quality(c)],
                  key=lambda c: c["confidence"], reverse=True)
board_v1 = sorted([c for c in slate if v1_board_qual(c)],
                  key=lambda c: c["confidence"], reverse=True)
v1_star_pick = next((c["player"] for c in board_v1 if v1_star(c)), None)
ordered_v2, has_star = p._promote_star(list(board_v2))
v2_star_pick = ordered_v2[0]["player"] if has_star else None
print("  v1 ⭐:", v1_star_pick)
print("  v2 ⭐:", v2_star_pick, "(has_star=%s)" % has_star)

# ── no-POTD fallback path ────────────────────────────────────────────────────
print("\n=== NO-POTD FALLBACK (nothing star-eligible clears 80) ===")
thin = [cand("X_aces78", "Aces", 78), cand("Y_bp76", "Break Points Won", 76),
        cand("Z_df95", "Double Faults", 95)]
ordered, has_star2 = p._promote_star(sorted(thin, key=lambda c: c["confidence"],
                                            reverse=True))
star_confs = [c["confidence"] for c in ordered
              if c["prop_type"] not in p.POD_STAR_EXCLUDE_PROPS]
print("  board qualifies (all >=65):", [c["player"] for c in ordered
                                        if p._passes_quality(c)])
print("  has_star:", has_star2, "(expect False)")
print("  highest star-eligible conf:", max(star_confs), "(DF 95 correctly ignored)")
assert has_star2 is False, "no-POTD path should yield has_star=False"
assert max(star_confs) == 78
print("\nALL ASSERTIONS PASS.")
