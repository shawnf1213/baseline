# BASELINE — PROJECT NORTH STAR

*Read before any task. This governs all work.*

## WHAT BASELINE IS

A sports prop analytics product. Tennis is the flagship and the primary marketed
product. MLB is being added as a secondary "addon" sport. More sports may follow.
The product is sold as ONE optimizer/tool that covers multiple sports,
tennis-forward.

## ARCHITECTURE PRINCIPLE — shared core, sport modules

- A sport-agnostic **CORE** handles board scanning, Discord posting, results
  tracking, recaps, the calibration ledger, the market-anchor de-vig math, the
  scenario-mixture engine, and the confidence→probability mapping.
- Each **SPORT** (`tennis/`, `mlb/`) is a module implementing a common interface:
  `scan_board()`, `project()`, `resolve()`, `grade()`, plus its own scenario
  definitions. The scenario MACHINERY is shared; the scenario DEFINITIONS are
  per-sport.
- One optimizer, dispatches by sport parameter (default tennis).

## NON-NEGOTIABLE RULES

1. **TENNIS IS SACRED.** No task may change tennis projections, confidence, board
   composition, or posting behavior unless the task explicitly IS a tennis change.
   Any refactor touching shared code must be verified byte-identical on a cached
   tennis slate before it's considered done. If tennis output moves unexpectedly,
   stop and report — that's a failure, not a side effect.
2. **HARD SPORT ISOLATION.** An exception in one sport's code path is caught and
   logged and CANNOT propagate to another sport's pipeline. MLB breaking must
   never take tennis down, and vice versa. Every sport's scan/post/resolve runs
   inside its own error boundary.
3. **RECORDS NEVER POOL ACROSS SPORTS.** Every pick/result/calibration record
   carries a `sport` field. Every record and hit-rate query filters by sport. A
   65% tennis pick and a 65% MLB pick are different claims and never share a
   denominator.
4. **NEW PROPS SHIP IN SHADOW.** Any new prop or model — any sport — launches
   behind an ENABLED flag, default false, computing and logging without posting,
   until reviewed against actuals. This is because new props have repeatedly
   shipped with hidden outcome-conditioning bugs. No exceptions.
5. **SCENARIO MIXTURES, NOT POINT ESTIMATES**, for any prop whose outcome is
   bimodal (tennis match-outcome props; MLB volume-driven props like strikeouts).
   A probability-weighted mean of a bimodal distribution is a value that rarely
   occurs — it misprices. Always integrate over scenarios.
6. **MARKET ANCHOR** where odds exist. Real moneylines/totals anchor win-prob and
   volume inputs. Reuse the existing anchor code; don't rebuild it per sport.

## WORKING RULES

- Report null results and contradictions plainly. Never certify a partial pass.
  Never manufacture an expected outcome.
- Decisions live in the repo (this file, `FREEZE_LOG.md`), not in chat.
- Data bugs fixed on sight; model-philosophy changes get logged as scoped
  exceptions below.
- Shawn authors prompts; Claude implements. When a task is ambiguous or would
  violate a rule above, stop and ask rather than guessing.

## CURRENT STATE

Tennis is live with an open reliability backlog (grading resolver, stale
date-override constants, surface-resolution skips). MLB is greenfield,
strikeouts-first, shadow only. Tennis reliability takes priority over MLB
features unless told otherwise.

---

# DECISIONS LOG

### 2026-08-07 — Rule 5 scoped exception: MLB strikeouts use an overdispersed count model, not a scenario mixture

**Status: APPROVED by Shawn.** Rule 5 names MLB strikeouts as bimodal and mandates
a mixture. Measured on 416 real starts (every announced starter on the 2026-08-07
slate), strikeouts are **not** bimodal:

```
0 K  2 | 1 K 18 | 2 K 40 | 3 K 48 | 4 K 71 | 5 K 62 | 6 K 62
7 K 47 | 8 K 22 | 9 K 26 | 10 K 14 | 11+ 4
mean 5.14   median 5   sd 2.41   skew +0.42   kurtosis 3.03
variance/mean = 1.13        (Poisson = 1.00)
```

Single peak, mild right skew, near-normal kurtosis, 13% overdispersed.

The tennis rationale does not transfer. Tennis match-outcome props are bimodal
because they split on a genuine binary — a winner takes at least 12 games, a loser
fewer — so the mean lands in an empty valley between two bands. Strikeouts have no
such binary: start length is concentrated (batters faced mean 22.7, sd 3.5), short
hooks are only 3.4% of starts, and K is a count process on top. There is no valley
for the mean to fall into.

`mlb/strikeouts.py` therefore uses a **negative binomial** at the measured
dispersion. The scenario machinery remains available for MLB props that genuinely
are bimodal (pitcher win, or anything conditioned on game outcome).

### 2026-08-07 — MLB lookback window is CURRENT SEASON (supersedes the 52-week decision made earlier the same day)

The MLB regular season is ~27 weeks (2026: Mar 25 → Sep 27, 186 days; 2025: 194
days), so a 52-week window necessarily spans the offseason and drags in the
previous campaign.

**First call (WRONG):** a holdout on 21 pitchers from a single slate favoured 52
weeks — 3.52pp vs 3.72pp mean absolute error on K rate — and the module shipped
that way.

**Multi-season fit reversed it.** Same holdout design on all 59 qualified 2026
starters:

| lookback | mean abs error on K rate |
|---|---|
| **current season** | **3.861 pp** |
| 52-week rolling | 4.020 pp |

52 weeks was better on only **11 of 59** pitchers (19%). The first result was
small-sample noise. Pitchers change materially between seasons — role, health,
pitch mix — so last year's tail is stale rather than extra signal.

**Early-season fallback:** below `MIN_STARTS` (5) the window extends into the
prior season, because a two-start sample is not a rate. The extension is reported
in the projection output (`window_extended`) so it is never silent.

*Lesson worth keeping: a 21-pitcher holdout off one slate produced a confident and
wrong answer. This is the third time in two days a small sample has done that on
this project — see also the contaminated 590-match tennis sample and the bp_saved
window artifact.*

### 2026-08-07 — Sport-column-before-MLB-writes gate (STANDING, not yet satisfied)

Rule 3 requires every pick/result/calibration row to carry a `sport` field and
every record query to filter on it. The picks schema has no such column. **MLB
writes zero rows until that migration lands and tennis records are verified
byte-identical across it.** The `mlb/` module currently satisfies this by writing
nothing at all — it projects and logs only.

### 2026-08-07 — Tennis baseline fixture (STANDING, not yet captured)

Before any core/sport-module refactor moves a single file, a cached tennis slate's
full board — projections, confidence, leans, composition — must be snapshotted to
a committed fixture. That fixture is the reference for "byte-identical" under Rule
1. Current tennis behaviour (post C7 restoration, `_BP_BASE_POP` recalibration and
the PTGW match-length coupling) is the correct reference state.

### 2026-08-07 — Resolver VOID guard: ATTEMPTED, THEN WITHDRAWN

A physical-completion guard (void any pick whose match shows fewer than 12 total
games in BO3 / 18 in BO5, since a completed match cannot go below those floors)
was written and then reverted at Shawn's direction before commit or deploy. Not in
the codebase. The underlying defect is unfixed: the resolver graded Erel (5.0
total games), Molčan (DNP) and Shapovalov as real results. Note that Svajda's
2026-08-04 misgrade reads 23 total games — **above** the floor — so it is a
separate defect the guard would not have caught.

*A recap-hold flag written alongside it was reverted at the same time. Recaps are
not blocked in any capacity.*
