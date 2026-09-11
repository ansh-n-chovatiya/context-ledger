---
ctx_schema: 1
unit: 09-suite-floor
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 08-gate-window-and-doctor
owns:
  - .github/workflows/ci.yml
  - tests/test_ci_floor.py
reads:
  - path: ctx/__init__.py
    symbols:
      - __version__
forbid:
  - ctx/cli.py
  - ctx/journal.py
  - .ctx/.gitignore
budget_tokens: 35000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 5
---

## Objective
Move the CI test-count floor up with the suite. It was set at 740 against a suite
of 749; the suite is now well past 1027, so a silent drop of 280 tests — the exact
regression the floor exists to catch — currently passes it.

## Interfaces
Consumes nothing. `SUITE_FLOOR` in `.github/workflows/ci.yml` (~line 86) and
`REQUIRED_FLOOR` in `tests/test_ci_floor.py` (~line 37) are two halves of one
decision and **must move together** — the test asserts the workflow's floor is at
least `REQUIRED_FLOOR`, so raising only one of them either does nothing or breaks
the build.

## Acceptance criteria
1. Both floors rise to just under the count the suite lands at after units 01–08.
   Count it yourself by running the suite; do not take the number from a report.
   "Just under" means the same margin the current pair uses — set just below the
   real count, because the stated purpose is detecting a *silent drop*, which a
   loose floor cannot do.
2. `tests/test_ci_floor.py`'s behavioural tests still prove the floor by executing
   the extracted step against synthetic suites of known size: `REQUIRED_FLOOR - 1`
   tests must fail the step and `REQUIRED_FLOOR` must pass it. They are
   parameterised on the constant, so they should follow — confirm by running, not
   by reading.
3. The three refusal modes are unchanged: an unparsable count, a count below the
   floor, and a non-zero suite exit all still fail the step.
4. A test asserts the workflow's `SUITE_FLOOR` and the test file's
   `REQUIRED_FLOOR` agree, so the next person to raise one is forced to raise the
   other. This is the check whose absence made this follow-up necessary.
5. Nothing else in `ci.yml` changes — not the SHA pins, not the `permissions:`
   blocks, not the two complementary `fetch-depth` checkout steps. Those are wave
   2 and wave 3 decisions with tests behind them.
6. `python3 -m unittest discover -s tests -q` passes.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the exact
suite count you measured and the floor you chose · confirmation that criterion 4's
agreement test fails when either constant is moved alone.
