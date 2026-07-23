# Model Freeze Log

Records changes shipped to the projection / confidence stack while a change
freeze was in effect, so post-freeze calibration can evaluate each one's effect
separately rather than against a moving baseline.

This file was created on 2026-07-15. No such log existed before — the freeze had
been a conversational constraint only, so entries prior to this date are not
recorded here.

---

## Freeze window: opened ~2026-07-13, still open

**Standing constraint:** do not change prop calculation formulas, qualification
thresholds, prop ceilings, court CPI values, archetype logic, or the recent-form
recalibration.

### Entry 1 — Surface-affinity differential (2026-07-15)

**The one model change shipped during the freeze window.**

Scope: the **win probability and expected sets layer**, which the standing freeze
does not cover. No prop formula, prop ceiling, CPI value or qualification
threshold was touched by this change.

**What it does.** The model compared players' raw surface stats and their overall
level, but never asked which player the surface *relatively* favours. Affinity
measures each player against **their own** all-surface baseline (win rate,
service games won, return games won deltas, weighted 50/25/25, damped by surface
sample size), so absolute level cancels out. The differential
(`player affinity − opponent affinity`) narrows the win-probability gap when the
**underdog** holds a meaningful positive affinity edge, capped at 15pp of gap,
never letting the favourite stop being the favourite. Expected sets rise from the
narrowed gap through the existing `_expected_sets_from_gap` path.

An affinity edge for the *favourite* is deliberately ignored — widening the gap on
this signal would compound the level estimate rather than correct it.

**Justification / motivating case:** Urgesi vs Jones (Rome, clay). Jones is the
better player overall but clay is her worst surface; Urgesi is lower-level but
clay is her best. The pre-change model read this as a comfortable Jones win and
projected Urgesi's games won near 5, leaning UNDER 8.5. The affinity differential
is what that read was missing.

**Knock-on (same entry):** a confidence penalty of 8–12 points on **UNDER** leans
for the **underdog's** Player Total Games Won when the affinity gap favours them
— competitive-loss scorelines (4-6 6-7) are exactly what beats those lines. OVERs
untouched.

**Deliberately NOT shipped:** home-country advantage. Real but small, and
unmeasured here; it is display-only (a HOME flag) so it can be evaluated against
the results ledger later rather than guessed at now.

**Calibration note:** this change alters win probability and expected sets for
every prop, so it moves projections board-wide. It shipped alongside the
2026-07-14 data-integrity fixes (cache-poisoning guard, deterministic event
selection, stat-rich count standardisation), which independently moved
projections. Post-freeze calibration should treat **2026-07-15 as a baseline
break** and not pool results across it. See also `picks.pre_guard`, which marks
picks scored before the cache guard shipped.

### Entry 1a — Correction to the affinity MEASUREMENT (2026-07-15)

Not a new model change: same feature, same freeze window. Entry 1's mechanism is
unchanged; what it measures was wrong.

**The bug.** Affinity compared a surface against `overall_*` — but "overall"
INCLUDES the surface being measured, so every player was partly compared against
themselves. The dilution is worst exactly where the signal matters most: a player
whose matches are mostly clay has a nearly-all-clay "overall", so their true clay
affinity all but vanishes. Every affinity was pulled toward zero, and the
differential under-fired.

**The fix (held-out baseline).** Surface S is now measured against the player's
OTHER surfaces only — clay vs hard+grass, hard vs clay+grass. The measured surface
never appears in its own reference. The pooled reference is rate-weighted SUM/SUM
over the held-out matches, so it is automatically weighted by each surface's
match count (40 hard + 6 grass produces a hard-dominated reference).

**Minimum sample guard.** Affinity requires >= 5 stat-rich matches on the surface
AND >= 8 stat-rich across the others. Below either, affinity is null, logged as
insufficient, and the differential does not fire — no adjustment from
unmeasurable affinity. Both sides of a difference need support; a baseline built
from 2 matches is not a baseline.

**Surface ranking.** Each player now carries an explicit best->worst affinity
ranking across hard/clay/grass, stored in the player data, exposed in the API and
printed in the SURFACE_AFFINITY log. One blended number hid the shape.

**Temporary instrumentation (remove after 2026-07-22).** SURFACE_AFFINITY logs the
held-out and diluted values side by side, so live data shows how often the
correction changes the picture and whether the 3.0 trigger threshold still holds
once affinities are measured honestly rather than shrunk toward zero.

**Dual-window affinity: NOT BUILDABLE (investigated 2026-07-15, not shipped).**
The plan was to blend a 52-week affinity with a career one (40/60) and flag
divergence. Both premises failed against the data:

* There is only ONE window. The `valid[:50]` stats fetch means only the ~50 most
  recent matches ever get statistics — for Jones that is 414 matches -> 50
  stat-rich spanning exactly 12.0 months. The "52-week window" and the "career
  log" are the same rows, so a blend would average a number against itself and
  the divergence flag could never fire.
* Tennis Abstract cannot supply the career side. `ta_stats.surface_stats` carries
  ace_pct / df_pct / first_in_pct / first_won_pct / second_won_pct / bp_saved_pct
  / bp_conv_pct — but NO win rate, NO service games won, NO return games won,
  i.e. none of the three affinity inputs (win rate alone is 50% of the score).
  It is also null on all surfaces for Urgesi, one of the two players in the
  motivating case, and its counts (Jones: 22/16/17 = 55) are no deeper than
  Sofascore's.

Consequence: the shipped affinity IS a 52-week figure and should be read as
recent form, not career identity. Jones's +12.93 clay affinity is measured over
one year; whether it is her career identity is NOT answerable from current data.

Raising the valid[:50] cap would create a real career window but multiplies proxy
volume on every cold fetch across every player — a deliberate infrastructure
decision (cost + rate limits; see the 2026-07-14 Decodo exhaustion and Discord
Cloudflare ban), explicitly NOT taken as a fallback inside this task.

**~~Known defect~~ — FIXED 2026-07-15 (commit 96957a5).** The differential's
affinity and the per-surface ranking disagreed (Urgesi clay: ranking -2.10,
differential -15.09) because the ranking computed both sides of every delta from
raw match records while the differential re-derived affinity from `p1_s`, whose
surface stats have been through quality-weighting — a quality-weighted surface
figure measured against a raw held-out reference.

The ranking is now the SINGLE SOURCE: main.py precomputes the measured surface's
affinity from it and `surface_affinity()` returns that value directly. Quality
weighting is excluded from affinity by design — it adjusts for OPPONENT strength,
which is exactly what affinity must not absorb, since a surface preference is a
property of the player, not of who they happened to face.

Verified live: Urgesi clay reads -2.10 in both the ranking and the differential,
Jones +12.93 in both; unit profiles unchanged (+12.00 / -13.00 / 0.00, both
sample guards fire); the asymmetric favourite-ignore rule is intact (underdog
edge narrows the gap 26.7 -> 11.7, hitting the 15pp cap; favourite edge moves it
by +0.0). Case 1 reproduces identically across two consecutive runs.

---

## Verification status (as of 2026-07-15)

The model is **NOT yet certified frozen**. A four-case end-to-end verification was
specified; only Case 1 has been run.

* **Case 1 — Urgesi vs Jones, clay, PTGW — PASSES.** Stat-rich 26/25 (both clear
  the 5/8 guards); affinity Urgesi -2.10 / Jones +12.93 from the single source;
  gap -15.03 points at the FAVOURITE so the differential correctly does not fire;
  win prob 16.9/83.1; expected sets 2.1; projection 7.9 vs line 8.5; UNDER at
  confidence 65 — below the 80 PTGW bar, so it does not qualify. Identical across
  two consecutive runs.
* **Cases 2, 3, 4 — NOT RUN.** ATP aces chain, WTA break-points chain, and the
  thin-data guard case. Most of their components (65/35 opponent ace blend, CPR
  modifier, handedness, returner-creation multiplier, 60/40 conversion blend, C8
  cap, momentum bonus) are not exposed in the API response, so verifying them
  needs log inspection or added instrumentation — not response diffs.
* **Also not run:** the modifier-budget (<=15% from base) check and the
  no-raw-count audit.
* **Already verified, do not redo:** deterministic newest-50 event selection,
  cached-event-stat merge on fetch failure, and run-to-run reproducibility — all
  passed the three-run test (Chiesa 30/26, Sakkari 33/23, Penickova 26/29,
  identical across three runs).

**Do not mark this baseline verified until Cases 2-4 pass.** The post-guard
calibration sample measures against whatever is certified here, so certifying an
unverified baseline would silently corrupt every calibration number downstream.

### Affinity measurability limit (found 2026-07-15, Case 4)

The held-out method needs BOTH >=5 stat-rich on the measured surface AND >=8
across the OTHER surfaces. But the stat-rich window is only the newest ~50
matches (valid[:50]), and mid-swing that window is dominated by ONE surface — so
the held-out reference starves and affinity returns None.

Observed: Sonego vs Collignon on clay — affinity None for BOTH players, guards
firing correctly, differential unable to fire. Urgesi (26 clay + 11 hard) and
Jones (25 clay + 21 hard + 4 grass) clear it; players whose last 50 matches are
almost all clay do not.

This is the guard working as designed (no adjustment from unmeasurable affinity),
not a bug — but it means the affinity differential is expected to be INACTIVE for
a meaningful share of a single-surface-swing board. Its real coverage is an open
question; the SURFACE_AFFINITY log line reports None vs measured per projection,
so a week of boards will quantify it.

Interacts with the valid[:50] cap: raising it would widen the held-out reference
as well as create a career window. Still an infrastructure decision, still not
taken here.

#### Known remedy for the affinity seasonal blind spot (designed, NOT built)

Parked deliberately as a post-certification follow-up so the week of
None-vs-measured coverage logging lands on a prepared decision rather than an
open question.

**The blind spot.** The held-out reference dies exactly when the signal is most
bettable: during a surface swing the newest-50 stat-rich window is nearly all one
surface, so the "other surfaces" side of every delta starves and affinity returns
None for a large share of the board (observed: Sonego vs Collignon, both None).
Clay season is precisely when surface-form edges matter most and precisely when
the current method goes dark.

**The remedy: let the REFERENCE reach further back than the projection window.**
The two do not need the same depth. The projection needs stat-rich serve/return
detail and is rightly limited to the newest-50 fetch. The reference only needs to
answer "how does this player do on their other surfaces" — and WIN RATE alone
answers that from the raw match log, which carries `won` and `surface` on every
match going back years (Jones: 414 matches vs 50 stat-rich). A win-rate-only
held-out reference drawn from the full log keeps the reference alive when the
recent window is single-surface.

**Design notes for whoever builds it:**
* Reference and measured side must stay commensurable — a win-rate-only reference
  can only be differenced against a win-rate measured value, so this likely means
  affinity degrades to its win-rate component (weight 0.50) when the serve/return
  components have no reference, rather than mixing a deep win-rate reference with
  a shallow serve/return one. Do NOT average across incommensurable bases (that
  was the Entry 1a bug in another costume).
* Flag which basis produced each affinity (full-fidelity vs win-rate-only) so the
  differential can weight or gate on it, and so the calibration can separate them.
* The deep reference is NOT quality-weighted and NOT stat-rich-gated; its own
  sample guard should be on raw match count, not stat-rich count.
* This does not need the valid[:50] cap raised — that is the point of it.

---

## DECIDED (2026-07-15): raise the stats-fetch window 50 -> 150, AFTER certification

Taken deliberately as an infrastructure decision, not as a side effect of a
modelling task — it was correctly refused twice as a fallback (once inside the
affinity work, once inside the dual-window investigation).

**Rationale.** 150 stat-rich matches is ~3 years of texture: a real career
reference for the affinity remedy, honest variance estimates, and blend layers
that finally average over genuinely different windows instead of the same 50
matches wearing different labels.

**Sequencing: PHASE 1 certification first, PHASE 2 cap second.** Certify the
current model, change the cap, re-verify, and only then does the freeze clock
start for real — one model, one window, one uninterrupted ledger.

**Guardrails agreed:**
1. Backfill through the EVENT-LEVEL cache — completed-match stats are immutable,
   so fetch only the ~100 additional events per player and never refetch what is
   cached. NOTE: this is only safe because of the 2026-07-14 fix that stopped
   caching failures as `{}` — without it, extending the window would re-poison.
2. Warm gradually — board players first on their next natural fetch, no mass
   overnight backfill. Log daily proxy call volume before/after so the Decodo
   cost is measured, not guessed.
3. Deterministic newest-N selection and every guard stay unchanged. The window
   widens; the rules governing it do not.
4. Rerun all four certification cases after the change. Record the date: a
   SECOND baseline break the calibration ledger must not pool across, same as
   2026-07-15.
5. The affinity career reference + dual-window blend become buildable on the
   wider window — build them LAST, per the recorded remedy, degrading to
   win-rate basis with the flag rather than diluting deep against shallow.

**⚠️ CORRECTION — guardrail 2 assumes a throttle that DOES NOT EXIST.**
`_search_throttle()` (min-gap under a lock) is wired ONLY into the search path.
The stats path (`_fetch_stats_parallel`) has NO throttle: up to 10 concurrent
calls through a ThreadPoolExecutor with no inter-request spacing; the only
`sleep(0.5)` is a per-event retry backoff. At 150 that is 150 unpaced calls per
cold player — the exact burst shape behind this week's Decodo exhaustion and the
Discord Cloudflare 1015 ban. **A stats-path throttle must be BUILT as part of
Phase 2, before the cap is raised.** Do not treat it as existing.

**Accepted cost:** every player's stats shift again when their window triples, so
projections/affinities/confidences all move once more and the clean post-guard
sample restarts. Accepted knowingly: better to break the baseline once more this
week than to accumulate three weeks of sample and discard it in August. Note this
interacts with CALIBRATION_MIN_SAMPLE=40 — the weekly table stays suppressed until
40 post-change picks accumulate, roughly two weeks from the cap change, not from
2026-07-15.

### Entry 2 — games_per_set: per-tour empirical fit (2026-07-15)

**A PROJECTION-FORMULA change, shipped during the freeze.** Entry 1 was scoped to
the win-prob/expected-sets layer, which the freeze does not cover. This one DOES
touch a prop formula, deliberately, because the formula was measurably wrong.

**The bug.** `games_per_set` was:
    >75 : 9.5 + (ch-75)/15 | >=65 : 8.5 + (ch-65)/10 | else max(7.5, 7.5+(ch-50)/15)
calibrated for ATP hold levels and applied to BOTH tours. ATP sits at a mean
combined hold of 79.9% where it was roughly right (+/-0.3). WTA sits at 64.5% —
where it was wrong by -0.5 to -2.3 games/set across its ENTIRE operating range.
Its low end claimed a 50%-hold set averages 7.5 games; a set is FIRST TO 6, so
6-1 is already 7.

**How it surfaced.** The 7/16 board posted four PTGW UNDERs. Book had both live
matches at a 20.5 total priced -120/-120 BOTH ways; we projected 18.9 and 18.0.
PTGW splits that combined total between the players, so every games-won projection
inherited the shortfall (Feistel 7.9 vs a book-implied 8.5). The UNDERs were still
correct — PrizePicks was hanging 10.5/11.5 against a book-implied 8.5 — but we
were right for the wrong reason, and the same bias flips us onto the WRONG side on
a tight line while showing 80% confidence.

**The fit.** 1,233 completed matches (ATP 603 / WTA 630), deduped by event. Each
match supplies both variables from itself: combined_hold from its own
service/return games, games_per_set from its own total games / sets.
    ATP: games_per_set = 5.8218 + 0.05061 * combined_hold   (R^2 0.157)
    WTA: games_per_set = 7.2399 + 0.03294 * combined_hold   (R^2 0.093)
Clamped to the observed support [8.3, 11.0]. Both live matches now land within ~1
game of the book (was -1.6 / -2.5).

**The finding that outlives the fix: R^2 is 0.09-0.16.** Combined hold explains
only ~10-15% of games-per-set variance; residual sd ~1.2 games/set => ~+/-2.8
games on a 2.3-set total. TOTAL GAMES IS INTRINSICALLY NEAR-COIN-FLIP — which is
precisely why books price it -120/-120 both ways. The 85 confidence bar on that
prop is claiming a precision the statistic cannot support, and should be revisited
against this number rather than against intuition.

**Known approximation.** x in the fit is the IN-MATCH combined hold; the model
feeds a SEASON-AVERAGE hold, which is less dispersed. Feeding a less-variable x
into a curve fitted on a more-variable one under-disperses the output slightly.
Accepted versus a curve that was simply wrong, but it is not free.

**BASELINE BREAK.** This moves EVERY Total Games and Player Total Games Won
projection up ~1-2 games. Calibration must not pool across 2026-07-15. Some
existing UNDERs weaken or flip; that is the fix working.

#### Entry 2a — Total Games confidence ceiling + calibration re-baseline (2026-07-15)

Follows directly from entry 2's R^2, and is the honest consequence of it.

**The finding forced this.** PROP_EVR_SCALE gave Total Games **1.9 — the HIGHEST
amplification of any prop**. So the prop the model explains LEAST (R^2 0.09-0.16)
was receiving the MOST generous confidence grading. Exactly backwards.

**Ceiling.** Total Games now caps at 80, same as PTGW — both are derived,
compounded stats. Residual sd ~1.2 games/set means ~+/-2.8 games on a 2.3-set
total; a 90+ confidence on a statistic we explain 15% of is a claim the data
cannot support. Books price this prop -120/-120 on BOTH sides and PrizePicks leans
on it precisely because it is intrinsically near-coin-flip.

**Bar 85 -> 80.** Forced: an 85 bar above an 80 ceiling makes the prop
unqualifiable — the degenerate ceiling==bar trap already found on PTGW. 80/80
means Total Games qualifies only when it maxes out its ceiling. Acceptable
strictness for a prop the model demonstrably predicts poorly, and consistent with
how PTGW is already treated.

**Calibration re-baseline: CALIBRATION_BASELINE_UTC = 2026-07-16T00:00:00.**
Two model breaks landed on 2026-07-15 — the data-integrity fixes (cache-poisoning
guard, deterministic event selection, stat-rich standardisation) and the
games_per_set per-tour fit plus this ceiling. A hit rate pooled across that
boundary averages two different models together, which is worse than no number.
The weekly log now counts only picks GENERATED on/after the baseline; pre_guard
remains as the historical marker of the first break. Tonight's 8:20 PM ET run is
00:20 UTC on 7/16 — the first run on the new model, and the first to count.

The clean sample therefore restarts from tonight, and the weekly table stays
suppressed until 40 post-baseline picks accumulate (~2 days at current volume).

#### Entry 2b — expected_sets exceeded its format's mathematical ceiling (2026-07-15)

Found from a user-reported 24.1 total-games projection. Not a tuning problem — the
values were outside what the format can physically produce.

**The maths.** BO3: exp_sets = 2 + P(3 sets), and P(3 sets) = 2q(1-q), which MAXES
at 0.5 when q (per-set win prob) = 0.5. So exp_sets <= 2.5, ALWAYS. BO5 at q=0.5:
P(3-0)=0.250 -> 3 sets, P(3-1)=0.375 -> 4, P(3-2)=0.375 -> 5, giving E[sets] =
4.125. So exp_sets <= 4.125, ALWAYS.

**The bug.** Even-matchup values were BO3 2.6 and BO5 4.4 — both ABOVE their
ceiling. 2.6 implies P(3 sets) = 60%; the maximum between two coin-flip players is
50%, and the measured rate across 409 WTA matches is 32.8%. The model was claiming
more sets than the format can produce.

**Fix.** BO3 even 2.6 -> 2.5, slight 2.5 -> 2.45. BO5 even 4.4 -> 4.1, slight
4.1 -> 4.0. Verified every bucket now sits inside its ceiling.

**Verification.** Even WTA at ch=61: was 9.25 x 2.60 = 24.05. Now 9.25 x 2.50 =
23.12. Measured E[TMG | even] from 409 matches = 0.5*18.00 + 0.5*28.25 = 23.13.

**This error PREDATES entry 2.** Raising games_per_set to its correct level simply
made it visible — at the old gps of 8.23 the same match produced 21.4, which
looked unremarkable. Two compensating errors were cancelling: a gps that was too
LOW and an exp_sets that was too HIGH. Fixing one exposed the other, which is the
normal way compensating errors surface, and a reminder that a projection landing
near the book is not evidence that its components are right.

**Also confirmed sound:** proj = E[gps] x E[sets] is valid here — mean(gps) x
mean(sets) = 21.27 vs actual mean TMG 21.36 across 409 matches (error -0.09), so
the independence assumption behind the multiplication holds well enough.

---

## Entry 3 — PTGW projection chain rebuild (scoped exception, 2026-07-16)

**Scope of the exception.** PTGW projection chain rebuild — a STRUCTURAL math
error (a bimodal distribution treated as a point estimate). This is the only prop
touched. Aces / Double Faults / Break Points Won / Total Games are UNCHANGED —
proven below, not asserted.

**What was wrong (audit).** Every PTGW pick on 7/16 lost (Feistel U11.5→12,
Basilashvili U11.5→15, Pellegrino U10.5→16, Faria U10.5→13). Not variance — the
market falsified the outputs. Feistel was +162 (P(win)≈36% de-vigged), and any
BO3 winner scores ≥12 games (two sets × 6), so P(over 11.5) ≥ 36% before counting
3-set losses. An 80% UNDER confidence was mathematically impossible.
The chain projected a single MEAN (`project_player_games_won`) and graded it with
EVR = |projection − line| / σ. PTGW is BIMODAL: a straight-set loss lands ~6-9
games, ANY other outcome ~12-17. The mean sits in a valley the distribution rarely
occupies, and σ is a unimodal spread — so EVR is the wrong instrument, and a "fat
edge on the mean" was a disguised moneyline bet on the straight-set-loss mode.
The two same-day boards (Feistel 9.6 vs 6.7; Sasnovich 8.3 vs 7.0) were the deploy
cache-wipe: PTGW consumes `games_combined`/`expected_sets`/`win_prob` from the
Total Games chain, and the 20:39 exp_sets deploy both changed the multiplier
mid-slate AND wiped the in-process stat-rich cache, shifting those inputs.

**The rebuild.**
- SCENARIO-MIXTURE MODEL (`ptgw_scenario_mixture` in props.py): four scenarios —
  S1 win-straights · S2 win-decider · S3 lose-decider · S4 lose-straights.
  P(over) = Σ P(scenario)·P(games > line | scenario). Confidence = 100·P(chosen
  side). "80% confidence" now literally means the side hits 80% of the time.
- EMPIRICAL FIT: constants fit from 2,028 real Sofascore matches (ATP n=995 BO3,
  WTA n=941 BO3) — Sackmann (the spec's stated source) is DEAD (repos 404 / loader
  disabled), so the substitute is the same per-match source the games_per_set fit
  used; Shawn approved it. Method recorded in scratchpad/fit_ptgw_scenarios.py.
  BO5 uses a spec-sanctioned sane fallback table (winner min 18, not 12); the GS
  BO5 sample (ATP n=92, WTA n=0) is too thin to fit.
- HARD WINNER FLOOR: a BO3 winner always wins ≥12 games (18 in BO5), so
  P(over | win-scenario)=1.0 below that line — enforcing the identity the audit
  rests on: P(over) ≥ P(win), always.
- STRUCTURAL GUARDS (model-independent): BO3 line ≥11.5 → UNDER conf ≤ 100·P(lose)
  (BO5 ≥17.5); ANY PTGW UNDER blocked when model win prob >40%; max 2 PTGW/board +
  correlated-direction flag; implied match claim required on every PTGW pick.
- EVR skipped for PTGW in confidence.py; _edge_cap, the +8 dominant bonus, and the
  affinity underdog penalty skipped for PTGW in main.py — each is the same
  bimodal-mean-vs-line comparison.
- VERIFICATION GATE: PTGW_ENABLED (default FALSE). While false, PTGW is excluded
  from the ranked board / 3x / POTD, /prop returns "under rebuild", and the new
  chain runs in SHADOW (POD_PTGW_SHADOW logs its projection). Stays false until
  Shawn reviews live shadow output and flips it.

**Freeze compliance (scope check).** Non-PTGW paths are behaviour-identical:
- props.py: edits confined to `import math`, the new mixture function, and inside
  `project_player_games_won`. project_aces / _double_faults / _break_points /
  _total_games: untouched.
- confidence.py: the ONLY change adds `prop_type != "Player Total Games Won"` to
  the EVR guard — for every non-PTGW prop that condition is True, so the block runs
  byte-identically and every other prop gets the same EVR ceiling as before.
- main.py: every shared-path edit (dominant bonus, underdog penalty, _edge_cap) is
  gated by `not _ptgw_prob_base`, which is always True for non-PTGW props →
  identical behaviour. The PTGW probability base and new response keys execute /
  appear only for PTGW.
No non-PTGW number can move.

**Verification (2026-07-16, PTGW_ENABLED still FALSE).** The four 7/16 losers,
re-run through the new chain from their logged Pick records:
  Feistel U11.5  old 80 (POSTED) L → new P(over) 0.182, conf 62 — no post
  Faria   U10.5  old 77 L         → new P(over) 0.472, conf 53 — no post
  Basil.  U11.5  old 75 L         → new P(over) 0.365, conf 63 — no post
  Pelleg. U10.5  old 79 L         → new P(over) 0.447, conf 55 — no post
All four fall to 53-63, below even the 75 blowout-under exception bar they
originally qualified under. NULL CHECK PASSES: no loser survives at the 80 bar,
and none reaches 75. No lean flipped (all were genuine underdogs) — the model
still tilts under but refuses high conviction on a bimodal coin-flip. Basilashvili
remains a directional miss (new p_under 0.635 vs an actual over) but is harmless
below the bar. The empirical fit and this backtest are reproducible in
analysis/fit_ptgw_scenarios.py (data analysis/ptgw_rows.json) and
analysis/ptgw_backtest.py. PTGW_ENABLED stays FALSE until Shawn reviews live
shadow output and flips it.

---

## Entry 4 — Board qualification policy v2 (2026-07-16)

Board qualification policy v2 — uniform 65 board floor, uniform 80 POTD threshold,
DF permanently excluded from POTD, no-POTD fallback message added. SELECTION policy
change; projection math untouched.

**What changed (selection only).**
- Board + 3x eligibility: any prop qualifies at confidence >= 65. Replaces every
  v1 per-prop bar (standard 70/75, Total Games 80, PTGW 80, blowout-UNDER 75).
- 3x slip legs must be >= 70 (one notch above the board floor).
- Pick of the Day: uniform 80 across all prop types. Double Faults is the ONLY
  prop permanently blocked from the ⭐ slot (it now populates the board + 3x
  normally — v1 excluded it from the board entirely). The old TG-90%-favourite
  star bar and the PTGW-UNDER star gate are retired: one exclusion, one entry (DF).
- No-POTD fallback: when nothing star-eligible clears 80, the ranked board still
  posts with "No Pick of the Day today — no play met the 80% bar. Board below." in
  place of the ⭐ embed. Logged as POD_NO_POTD (date + highest star-eligible conf).
- Display: a "— Volume plays (65–79%) —" divider separates the conviction tier
  (>=80) from the volume tier in the ranked board embed.
- Records: picks carry board_policy_version (existing rows backfilled v1, new v2);
  POD_V2_DIFF logs picks that qualify under v2 but were excluded under v1 for one
  week (until 2026-07-23).

**What did NOT change (verified).** All confidence computation — EVR grading,
variance caps, data-level ceilings, bonus/penalty caps, floor 25 / cap 95, cap
reasons — is byte-for-byte untouched; no file under src/calculations was modified.
Knife-edge checks, the PTGW structural guards, depth ceilings, PTGW_ENABLED (still
false), and the ranking rule (confidence DESC, edge tiebreak) all stand.

**Verification.** A representative slate run through v1 and v2 side by side:
picks ADDED by v2 (a 68 Ace, a 72 BP, a 90 DF), NONE removed, and zero confidence
values changed (confidence is an input to selection, never mutated). Star pick
moved from the 91%-fav Total Games (v1's only TG-eligible) to the highest-
confidence non-DF play (v2). The no-POTD path yielded has_star=False with the
board intact and the highest star-eligible confidence logged (DF correctly
ignored). Harness: scratchpad verify_v2_policy.py.

---

## Entry 5 — Demon prop evaluation (2026-07-16)

Demon evaluation enabled under elevated bars (85 conf / 0.9 edge), over-only,
star-blocked. SELECTION policy; projection math untouched.

**What changed (selection only).**
- Board scan no longer discards odds_type=demon; demons run through the normal
  projection chain for their prop type. Goblins remain excluded entirely. odds_type
  flows through the pipeline into Pick records (new column, default "standard");
  the results tracker / recaps / hit rates segment standard vs demon
  (record_summary.by_odds_type).
- Demon qualification (config): DEMON_MIN_CONF=85 AND DEMON_MIN_EDGE=0.9 (absolute
  edge in the prop's units). Demons are OVER-only by platform rule — a demon whose
  model edge points UNDER is discarded and logged POD_DEMON_REJECT | demon_under_
  no_play. Every below-bar demon is logged (line/proj/conf/edge) as a rejection log
  to review what the bars filter. The backend's 85+ data ceiling (both players 15+
  stat-rich) is NOT weakened — a demon at 85 implicitly rests on deep data.
- Demons are board/3x eligible when they clear their bars, but NEVER star-eligible:
  the boosted-payout structure is not part of the standard public POTD record.
- Display: a "😈 DEMON" tag on the ranked line and 3x leg, with the boosted line
  shown against its standard-line context, so a demon can never be mistaken for a
  standard prop.

**What did NOT change.** No projection, confidence, or guard math. The demon line
is graded by the same chain as any other line; demons simply face a higher bar and
an over-only rule at the SELECTION layer.

**Verification.** Unit tests: a demon OVER at conf 86 / edge 1.0 qualifies; conf 84
rejects (below 85); edge 0.5 rejects (below 0.9); an UNDER-leaning demon is
discarded as demon_under_no_play; a demon is never star-eligible; standard props
still clear the 65 floor. Because demons are graded by EVR on the boosted (higher)
line, clearing BOTH the 0.9 edge and 85 confidence requires the projection to beat
the boosted line by a real margin — most demons reject, as intended.

---

## Entry 6 — Fantasy Score prop (scenario mixture, 2026-07-16)

Fantasy Score added as a scenario-mixture prop built on the PTGW machinery. New
prop; no existing projection math changed.

**Scoring.** FS = 10 + (games_won − games_lost) + 3·(sets_won − sets_lost)
+ 0.5·(aces − double_faults). Tiebreaks count as 1 game.

**Structure (mandatory mixture).** FS is more bimodal than PTGW (a straight-set win
~20+, a straight-set loss can be negative), so a point estimate would repeat the
PTGW error. Four scenarios S1/S2/S3/S4:
- Sets won/lost EXACT per scenario. Set margins BO3 +2/+1/−1/−2 (BO5 +3/+1.5/−1.5/−3).
- Games won reuse the per-tour/format Sofascore fit (_PTGW_SCEN_FIT); games LOST
  need no separate fit — by match symmetry the player's games-lost in scenario S
  equals the OPPONENT's games-won in the mirror scenario (S1↔S4, S2↔S3), so no
  re-fetch was needed. (The spec's "fit games-lost like games-won" is satisfied by
  this exact identity rather than a redundant second fit.)
- Aces/DF per scenario scale the match ace/DF projection by that scenario's set
  count over the match's expected sets. Independence of games/aces/DF assumed
  (noted in code) — acceptable, the between-scenario spread dominates.
- FS_var per scenario = games-margin var + 0.25·(ace+DF var); ace/DF var ≈ mean.
- P(over) = Σ P(scenario)·P(FS>line|scenario). Confidence maps from P(over) like
  PTGW. FS excluded from EVR and the Aces/DF variance cap; its own ceiling
  FS_CONF_CEILING = 80.

**Gate.** FS_ENABLED default FALSE. Shadow mode: FS is computed on every board and
logged (POD_FS_SHADOW / FS_PROB_BASE) but excluded from the posted board / 3x /
POTD until Shawn reviews a week of shadow vs actuals. FS demons are structurally
impossible (ceiling 80 < DEMON_MIN_CONF 85) and logged if one would qualify.
Display shows the implied match lean, same transparency rule as PTGW.

**Verification (live 7/16 dog spots, analysis/fs_sanity.py).** The FS distribution
is bimodal in every spot — S1 (win-straights) FS ≈ 20-21 vs S4 (lose-straights)
≈ −1 to −2, a ~22-point spread. The moneyline bound HOLDS everywhere: at a low
line (every win clears FS) P(over) ≥ P(win) — Feistel 0.119 ≥ 0.072, Faria
0.394 ≥ 0.279, Pellegrino 0.371 ≥ 0.256, Basilashvili 0.332 ≥ 0.224. Note (not a
bug): the model's own win probs on these clay dogs are low (Feistel 7%), the same
input-quality signal flagged in the PTGW work — the mixture is correct given its
inputs. The resolved-match FS backtest (actual FS vs shadow projection) is PENDING
shadow accumulation — no FS shadow rows exist yet since this just deployed.

---

## Entry 7 — 7/16 recap scope correction (2026-07-16, data-only)

One-time data correction; no selection logic changed. Scaffolding = the
excluded_from_record column + /api/results/exclude endpoint (commit above).

**What was found.** The 7/16 slate had multiple board generations, but 18h dedup
meant the tracker held only ONE record per games-won bet — carrying the EARLIER
generation's projection. The 15 candidate rows: 4 belonged to the 7/15 slate
(left untouched, per Shawn), and 11 were the 7/16 slate. Of those 11, the four
games-won ranks (Feistel/Sasnovich/Pellegrino/Basilashvili) existed only as the
earlier 4-pick board (ids 174/175/176/177, proj 6.7/7.0/8.3/9.0); the canonical
projections (9.6/8.3/8.9/9.1) were never logged distinctly.

**Correction (Shawn's decision: exclude earlier + create canonical).**
- Flagged ids 174/175/176/177 excluded_from_record=1 — RETAINED in the DB with
  their earlier projections for the reproducibility audit, invisible to record/recaps.
- Created 4 canonical records ids 189/190/191/192 (proj 9.6/8.3/9.1/8.9,
  conf 80/77/75/75). The bot's normal auto-resolver graded them: Feistel L,
  Sasnovich W, Basilashvili L, Pellegrino L — matching the verified finals
  (Sasnovich W cross-checks the excluded id 175, also W).
- The other 7 canonical (ids 180/181/182/185/186/187/188) stand as the canonical 11.

**Overall correction size: ZERO.** Before 39W-38L-3P (50.6%) → after 39W-38L-3P
(50.6%). The 4 excluded and 4 created records graded identically (1W+3L each), so
swapping them left the public record unchanged; only the displayed projections and
the audit trail were corrected.

**Unresolved (reported, NOT guessed).** 5 of the 11 could not auto-resolve —
"completed match not found": Riera Total Games O19.5 (180), Kovinić Games Won O8.5
(181), Udvardy Total Games O21.5 (182), Riera BP O5.5 (186), Trevisan BP O4.5
(187) — all OVER plays. Awaiting manual finals from Shawn before the recap posts.
Resolved so far: 2W-4L (Shubladze W, Sasnovich W; Faria/Feistel/Basilashvili/
Pellegrino L). No recap posted (it would be incomplete with 5 pending).

---

## Entry 8 — Total Games anchored to Sofascore total-games market (2026-07-22)

**What.** The Total Games (match total) projection is now blended toward the
sharp book number: `blended = 0.7·book_line + 0.3·model_proj`, where `book_line`
is the de-vigged "Total games won" O/U from the match's Sofascore odds feed
(new `get_match_total_games_line` in sofascore_client.py). Weight is env-tunable
via `TG_MARKET_WEIGHT` (default 0.7). No book market -> model-only, `tg_anchored`
false. Response surfaces tg_book_line / tg_model_proj / tg_blended_proj /
tg_book_edge (blended − PrizePicks line) / tg_divergent (|model − book| > 3 games).

**Why (Shawn's explicit call: "the sofascore sportsbook odds win").** The model
systematically over/under-shoots match totals; the two-way total-games market is
sharper. Anchoring tracks the market while still voicing a model edge + a
divergence flag when the model disagrees materially.

**Tension flagged.** This is the FIRST anchor that blends the FINAL projection
toward the book, not a structural input. The PTGW/FS anchors blend the WIN
PROBABILITY (a mixture input) — consistent with "fix structural bugs with market
lines, don't fit the final number to the book." Total Games has no intermediate
win-prob→games mapping to anchor, so the only market signal for a match total is
the total line itself; hence the direct blend. Done per explicit instruction;
weight is tunable to 1.0 (pure book) or lower if calibration argues for it.

**Verified live.** Navone vs Halys (clay): book 22.5, model 23.1, blended 22.7
(model_projection = 22.7 confirmed downstream), edge +0.18 OVER, not divergent,
conf 50. De-vig sanity: over 0.485 / under 0.515, overround 7.9%.

---

## Entry 9 — Break Points Won outcome conditioning (2026-07-23)

**Scoped freeze exception — structural math error, same class as the PTGW rebuild
(Entry 3).** The 7/23 audit confirmed the BP chain has NO outcome/scenario
conditioning: C8 (`expected_sets` from win-prob GAP) is direction-blind — a 14%
underdog and an 86% favorite in the same match get the identical multiplier — and
the only asymmetry is a pro-favorite deciding-set bonus. Result: the model
projected Spiteri (14% win) for 6.1 breaks → OVER 2.5 (POTD, conf 81; actual 1),
and Rublev (heavy favorite) for 2.9 → UNDER 3.5 (actual 5). **Both leans
inverted** — the signature of missing outcome conditioning.

Shipped in two commits.

**A1 — interim guard (this commit).** No projection number changes; a SUPPRESSION
guard only. The projector (`props.py`, ex-`BP_HIGH` block) now sets `bp_suspended`
when a BP projection sits in the outcome-inversion zone:
- **lopsided** — model win prob outside 30–70% (break count ill-defined when the
  match is a near-certain win/loss for one side); or
- **contradiction** — ≥4 projected breaks at <35% win prob (≥4 breaks implies the
  player wins; internally contradictory).
`main.py` exposes `bp_suspended`; the bot (`pick_of_day.py:_rank_board`) excludes
suspended BP picks from the board (`POD_BP_SUSPENDED`), logging the reason. The
projection VALUE is untouched (block, not cap) — it still shows for research.
Interim uses the MODEL win prob; **A2 replaces it with the market-anchored blend.**
Removes the inversion zone (both Spiteri and Rublev suspend) while keeping
mid-range (30–70% win) BP live. Aces/DF/other projector chains byte-identical.

**A2 — scenario rebuild (this commit).** Extends the existing four-scenario
mixture (win-in-2 / win-in-3 / lose-in-3 / lose-in-2, currently PTGW+FS only) to
Break Points Won, fitted per tour/format from Sofascore per-match records, anchored
by the SAME FS win-prob market anchor (0.7 market / 0.3 model, `main.py:1944`).
P(over)=Σ P(scenario)·P(breaks>line|scenario); confidence maps from P(over). C1–C7
become within-scenario rate inputs; C8's role is superseded.

*Empirical scenario fit* (`analysis/fit_bp_scenarios.py`, Sofascore per-match
breaks, deduped, RET dropped, BO3) — realized breaks mean/sd by scenario, into
`props.py:_BP_SCEN_FIT`:

| tour | S1 win-in-2 | S2 win-in-3 | S3 lose-in-3 | S4 lose-in-2 | pop. mean |
|------|-------------|-------------|--------------|--------------|-----------|
| ATP (n=1013) | 2.76 / 1.36 | 3.31 / 1.71 | 1.66 / 1.42 | 0.64 / 0.97 | 2.24 |
| WTA (n=1058) | 4.73 / 1.25 | 5.90 / 1.70 | 4.32 / 2.08 | 1.86 / 1.34 | 4.24 |

The fit exposes the asymmetry directly: a WTA win-in-3 averages 5.9 breaks, a
lose-in-2 only 1.86 — the SAME player, same matchup, ~3× the breaks depending
purely on which side of the result they land. An outcome-blind level cannot
represent that.

*Parameterized winners-scale / losers-empirical hybrid.* The matchup LEVEL
(`base_scale = clamp(base_proj / pop_mean, 0.5, 2.0)`) scales the scenario means.
Win scenarios (S1,S2) take the FULL matchup scale. Loss scenarios (S3,S4) take a
DAMPED scale: `loss_scale = 1 + (base_scale − 1)·BP_LOSS_MATCHUP_WEIGHT`.
Justification (a property of the sport, not of the two audit cases): breaks-in-a-
win are matchup-driven and wide — a strong returner against a weak server piles up
breaks; breaks-in-a-loss are floor-compressed — you lost, so you broke rarely
regardless of matchup, and opponent serve quality still matters (hence damped, not
zeroed). `BP_LOSS_MATCHUP_WEIGHT` parameterizes the two candidate designs as
endpoints: 0.0 = pure population in losses, 1.0 = uniform C1–C7 scaling. The
backtest chooses; we do not tune to cases.

*Out-of-sample backtest* (`analysis/backtest_bp_loss_weight.py`). The literal
"sweep on resolved BP picks" is **not runnable** — the picks table persists only
line/projection/lean/result, NOT the mixture inputs (base_proj, anchored win prob,
tour), and match records carry no odds/ranks. Reported plainly rather than faked.
Instead: every resolved MATCH is a labelled BP situation with the ground truth the
picks lack. Conditioning on the REALIZED outcome removes the win-prob term (that
mixing is PTGW machinery, already validated), isolating the scenario-scale test the
weight governs. Per player, TRAIN half (by time) → level, TEST half → eval (no
leakage). Metric: out-of-sample loss-subset MAE of predicted vs realized breaks +
Brier/accuracy over a line grid. Win-subset is identical across w (control).

| weight | ATP loss-MAE | ATP loss-acc | WTA loss-MAE | WTA loss-acc |
|--------|--------------|--------------|--------------|--------------|
| 0.00   | 0.901 | 0.877 | 1.332 | 0.829 |
| 0.25   | 0.896 | 0.879 | 1.330 | 0.833 |
| **0.35** | **0.895** | **0.881** | **1.330** | **0.828** |
| 0.50   | 0.893 | 0.889 | 1.335 | 0.828 |
| 1.00   | 0.896 | 0.889 | 1.366 | 0.829 |

Reading (honest, weak discrimination): the sweep is **largely flat across
0.0–0.5** (ATP spread 0.008 breaks = noise; WTA spread 0.005 across 0.0–0.5). The
one **clear** signal: **uniform scaling w=1.0 is distinctly worst on WTA** (MAE
+0.036, Brier 0.120→0.123) — the data REJECTS "losses scale fully with matchup,"
confirming the damped-hybrid over the old uniform behavior. ATP weakly favors
higher w (0.50 best by 0.002); WTA favors 0.25–0.35. **Shipped `BP_LOSS_MATCHUP_
WEIGHT = 0.35`** as the single global near-optimum for both tours (WTA optimum;
ATP within 0.2% of optimum). Per-tour weights were considered and rejected — the
ATP curve is flat within noise, so the added surface isn't earned. The backtest's
role here is mainly a NEGATIVE gate (rule out uniform scaling), not a precise
optimizer; stated as such.

*Confidence + guards.* BP joins PTGW/FS on the probability base: `confidence =
100·P(side)`, EVR/`_edge_cap`/dominant-bonus skipped (all mean-vs-line instruments,
the fallacy the rebuild removes), unanchored (no market moneyline) capped at 70.
BP added to the `confidence.py` EVR-skip list. The A1 suspension is re-keyed off the
market-anchored blend (was model-only). Lean is taken from P(over), not the
median-vs-line tie.

*Illustrative (NON-certifying) — the two audit cases.* Re-run through the mixture:
Spiteri (14% win, base 6.1) → P(over) 0.19–0.38 across lines 3.5–5.5 → **UNDER**
(was OVER; actual 1). Rublev (85% win, base 2.9) → P(over) 0.66 at 2.5, 0.57 at
3.0, 0.48 at 3.5 → **OVER at the lines ≤3.0** (was UNDER; actual 5). Both leans
flip in the correct direction. Explicitly NOT certification — two cases prove
nothing, and BOTH remain A1-suspended (lopsided win prob), so neither would post to
a board regardless. The out-of-sample backtest above is the gate; these are a
sanity check that the conditioning acts in the right direction.

*Scope.* Aces/DF/Total Games projector chains are byte-identical (the mixture lives
only in the BP `else` branch of `main.py` + two new functions in `props.py`;
`git diff` confirms no edit to `project_aces`/`project_double_faults`).
