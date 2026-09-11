---
ctx_schema: 1
unit: 01-plan-time-intelligence
plan: make-ctx-tell-you-the-plan-is-slow
tier: subagent
depends_on: []
owns:
  - ctx/plan.py
  - ctx/complexity.py
  - ctx/dispatch.py
  - ctx/commands.py
  - tests/test_plan_intelligence.py
  - tests/test_tier_from_the_graph.py
reads:
  - path: ctx/config.py
    symbols:
      - DEFAULTS
forbid:
  - ctx/verify.py
  - ctx/config.py
budget_tokens: 85000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Make `ctx plan-check` say how much of a plan can actually run at once, what is
serialising it, and where a unit owns source whose tests nobody owns — and stop
`complexity.score` putting nearly every unit on the most expensive model.

## Scope note — read this before starting
These four modules are one unit **on purpose**. `cmd_plan_check` lives in
`commands.py`, the graph lives in `plan.py`, scoring lives in `complexity.py`,
and its callers are in `dispatch.py` and `commands.py`. Cutting this by concern
would have produced three units that could not run concurrently anyway, which is
the exact mistake this wave exists to help people stop making.

**Contracts in this wave are deliberately lighter.** Criteria 1–4 are new output
computed from data you already have; a test that asserts the output is correct is
enough, and you do **not** need to reproduce a "before" for them. Criteria 5–7
are different and keep a full measured control — they change which model a
dispatch uses and therefore what a wave costs.

## Acceptance criteria

**Plan-time reporting.** All three are advisory: `plan-check` still exits 0, and
`--strict`/`CTX_STRICT=1` escalates to 1. A serial plan is sometimes correct, so
this reports rather than refuses.

1. `plan-check` reports units, waves and the parallelism ratio, and when one file
   is in the `owns` of three or more units it names that file and says to extract
   it first or merge those units. Run it against the committed
   `make-ctx-maintainable-and-documented` plan: it must name `ctx/cli.py`. Quote
   that run in your report.
2. It reports an estimated critical path from per-wave `budget_tokens`,
   **labelled an estimate**. Do not present it as a measurement.
3. It reports ownership gaps: for each owned source file, any test file that
   references it and that no unit in the plan owns. Against the committed
   `make-ctx-durable-under-concurrency` plan it should surface the
   `tests/test_core.py` case that blocked a unit mid-wave. If it does not,
   say so and explain what your detection does catch — a heuristic that finds
   nothing is worth knowing about.
4. `--json` carries all three (the flag and `_emit` already exist; add fields,
   and note that `tests/test_json_output.py` pins the plan-check shape — adding
   keys may need that fixture updated, which is a test you own reading a file
   you do not; if it breaks, report it rather than editing that file).

**Scoring.** `complexity.score`'s `publishes_iface` term currently fires whenever
a unit has an `## Interfaces` section, so 16 of this session's 17 units scored
into the `deep` (opus) tier — including a pure file move.

5. `publishes_iface` fires only when another unit **in the same plan** both
   declares `depends_on` this unit and reads a path this unit owns. The weight
   stays **2.0**; only the trigger changes. `score`'s signature may grow an
   optional parameter for the sibling units; all four call sites
   (`dispatch.py:99`, `dispatch.py:211`, `commands.py:1862`, `commands.py:2722`)
   must pass it or keep working without it.
6. **Measured control:** for each unit of this repository's own committed plans,
   print the tier it would have been dispatched at before your change and after.
   Quote the table. That is the evidence the change does what it claims; an
   assertion that "the term is narrower now" is not.
7. The `ctx-0-8` spec recorded these weights as a decision. Judge every existing
   test that pins them **individually** — report each one you changed and why.
   Do not bulk-update assertions to match new scores.

**All.**
8. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. `SUITE_FLOOR` and `REQUIRED_FLOOR` are pinned equal at 1420 — do not
   move either.
9. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the `plan-check` run
naming `ctx/cli.py` · the before/after tier table · every weight-pinning test you
touched and why · anything your ownership-gap detection misses.
