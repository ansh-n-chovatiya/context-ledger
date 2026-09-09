---
ctx_schema: 1
unit: 03-plan-unit-fields
plan: ctx-0-8
tier: subagent
depends_on: []
owns:
  - ctx/plan.py
  - tests/test_plan_fields.py
reads: []
forbid: []
budget_tokens: 35000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Expose the unit signals the complexity score and the phase gate need: `kind`, `reproduction`, `phases`, and whether the unit publishes an interface.

## Interfaces
Produces on `plan.Unit`:
`kind` -> str ("" or e.g. "bug"), `reproduction` -> str,
`phases` -> list[str], `publishes_interface` -> bool (a non-empty
`## Interfaces` section in the body, ignoring the HTML comment template).
Consumed by `complexity-score`, `phases-core`, `dispatch-selection`.

## Acceptance criteria
1. Each property reads its frontmatter key and degrades to a safe empty value
   when absent or malformed — same discipline as the existing `budget`/`tier`.
2. `publishes_interface` is False for a freshly scaffolded unit whose
   `## Interfaces` section holds only the template comment, and True once real
   content is written there.
3. `kind: bug` with no `reproduction` is reported by `plan-check` as a problem,
   naming the unit.
4. Existing plan parsing and wave computation are unchanged — `test_plan.py`
   passes untouched.
10. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
