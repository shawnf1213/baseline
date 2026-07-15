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
