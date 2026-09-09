---
ctx_schema: 1
unit: 01-telemetry-fields
plan: ctx-0-8
tier: subagent
depends_on: []
owns:
  - ctx/telemetry.py
  - tests/test_telemetry.py
reads: []
forbid: []
budget_tokens: 30000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Teach the telemetry store to carry `model` and `role`, and to aggregate spend per role. This is A4's substrate and everything in P0-A is unevaluable without it.

## Interfaces
Produces: `telemetry.record(..., model=, role=)` accepted and persisted;
`telemetry.summarise()` rows gain `by_role` — a mapping of role to count and
median ms. Consumed by `review-telemetry` and `dispatch-selection`.
Note: `record` already accepts arbitrary `**fields`, so the field half may need
no change — verify that before writing code, and if so say so rather than
adding a redundant signature.

## Acceptance criteria
1. `record` persists `model` and `role` when given, and omits them when not.
2. `summarise` groups by role as well as by event, reporting count and median
   duration per role.
3. The existing `median_chars` reporting is unchanged — no regression in the
   rows `ctx telemetry` already prints.
4. Telemetry still never raises: a malformed record is swallowed, as today.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
