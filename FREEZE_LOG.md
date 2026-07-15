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

**Known defect, not yet fixed:** the differential's affinity and the per-surface
ranking disagree (Urgesi clay: ranking -2.10, differential -15.09). The ranking
computes both sides from raw match records; the differential compares
quality-weighted surface stats against a raw held-out reference. The ranking is
the honest number. The differential should read from the ranking.
