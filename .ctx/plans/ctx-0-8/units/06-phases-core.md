---
ctx_schema: 1
unit: 06-phases-core
plan: ctx-0-8
tier: subagent
depends_on:
  - 03-plan-unit-fields
owns:
  - ctx/phases.py
  - tests/test_phases.py
reads:
  - ctx/plan.py
forbid: []
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
The general `phases:` mechanism, plus `kind: bug` as a preset expanding to four gated phases — so "no fix before a failing reproduction" becomes a machine fact rather than a request.

## Interfaces
Produces:
`phases.for_unit(unit) -> [Phase]` — declared `phases:`, or the bug preset.
`phases.BUG = ("reproduce", "locate", "fix", "guard")`.
`phases.can_enter(unit, record, phase) -> (bool, why)` — the gate.
`phases.record(...)` — persist a phase outcome into the work file.
Consumed by `cli-wiring`.

## Acceptance criteria
1. `reproduce` is satisfied only by a recorded **non-zero** exit of the unit's
   `reproduction` command. A zero exit does not satisfy it — the test must
   genuinely fail for the stated reason before anything else may happen.
2. `fix` cannot be entered until both `reproduce` and `locate` are recorded;
   the refusal names which is missing.
3. `locate` requires evidence in `file:line` form; a bare assertion does not
   satisfy it.
4. After `fix`, the same `reproduction` command must be recorded exiting zero.
5. `guard` is a `rubric` check routed to the existing `verifier` agent.
6. A non-bug unit declaring `phases: [a, b, c]` gates in that order with no bug
   semantics attached.
12. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
