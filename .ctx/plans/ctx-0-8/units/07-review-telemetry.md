---
ctx_schema: 1
unit: 07-review-telemetry
plan: ctx-0-8
tier: subagent
depends_on:
  - 01-telemetry-fields
owns:
  - ctx/review.py
  - tests/test_review_telemetry.py
reads:
  - ctx/telemetry.py
forbid: []
budget_tokens: 30000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Give `review` the package-size and violation signals the reviewer's model choice needs, as a reusable function rather than numbers computed inside a CLI command.

## Interfaces
Produces `review.dispatch_stats(...)` returning package bytes and
out-of-scope count for a built package, so `dispatch-selection` can size the
reviewer's model without rebuilding the package.
Note: `cli.py:1340` already records a `review` telemetry event with `bytes`,
`round` and `out_of_scope` — contrary to the brief, this is NOT missing. Do not
rebuild it; `cli-wiring` adds `model`/`role` to that existing call.
Consumed by `dispatch-selection`, `cli-wiring`.

## Acceptance criteria
1. `dispatch_stats` reports package bytes and out-of-scope count for a built
   package without rebuilding it.
2. Two units reviewed in the same wave report their own package's stats, not a
   shared or last-writer value.
3. No behaviour change to the package format itself — `test_review.py` passes
   untouched.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any interface change (which stops the wave).
