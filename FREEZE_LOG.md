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
