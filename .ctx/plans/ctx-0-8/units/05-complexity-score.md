---
ctx_schema: 1
unit: 05-complexity-score
plan: ctx-0-8
tier: subagent
depends_on:
  - 02-config-tiers
  - 03-plan-unit-fields
owns:
  - ctx/complexity.py
  - tests/test_complexity.py
reads:
  - ctx/config.py
  - ctx/plan.py
forbid: []
budget_tokens: 40000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Score a unit's difficulty from signals already on disk, and map the score to a tier — legibly, as a weighted sum whose inputs can be printed.

## Interfaces
Produces `complexity.score(config, unit) -> (float, [(label, points), ...])`
and `complexity.tier_for(config, score) -> tier name`.
The breakdown list is what the dispatch line prints (criterion 2), so labels
must be human-readable: `budget 45k=3.0`, `owns 4 paths=2.0`, `rubric=2.0`.
Consumed by `dispatch-selection`.

## Acceptance criteria
1. Two units differing only in `budget_tokens` score differently; likewise for
   `owns` breadth alone, and for the presence of a `rubric`/`human` verify
   check alone. One test per signal.
2. Weights and thresholds come from config — a project that halves a weight in
   `ctx.yaml` changes the score, with no code change.
3. The returned breakdown sums to the returned score, exactly. A breakdown that
   does not reconcile is the bug this criterion exists to catch.
4. A unit with every signal at its floor scores 0.0 and maps to the cheapest
   tier; the score is never negative.
10. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
