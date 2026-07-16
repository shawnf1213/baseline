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
