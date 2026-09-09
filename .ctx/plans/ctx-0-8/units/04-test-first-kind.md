---
ctx_schema: 1
unit: 04-test-first-kind
plan: ctx-0-8
tier: subagent
depends_on: []
owns:
  - ctx/verify.py
  - ctx/snapshot.py
  - tests/test_test_first.py
reads: []
forbid: []
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Add a `test_first` verify kind that fails when the implementation snapshot precedes any captured failing run of the unit's test paths.

## Interfaces
Produces: `test_first` in `verify.MECHANICAL` and `KINDS`, with a `COST`
entry placed by what it actually costs (a snapshot read, so near `review`).
Snapshot support for recording a test run's exit status and timestamp against
the unit's test paths.
Nothing else in this plan depends on it; it is isolated on purpose.

## Acceptance criteria
1. A unit whose snapshots show a failing test run *before* the implementation
   snapshot passes the check.
2. A unit whose implementation snapshot precedes every recorded test run fails
   it, with a message naming what was missing.
3. A unit with no recorded runs at all fails rather than silently passing — an
   unguarded unit must not look guarded (the `PROFILES` precedent).
4. The kind is ordered cheapest-first correctly and short-circuits like the
   others; `test_gates.py` still passes.
9. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
