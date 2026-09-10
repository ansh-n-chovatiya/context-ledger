---
ctx_schema: 1
unit: 01-ci-suite-floor
plan: make-the-ctx-cli-safe-to-automate
tier: subagent
depends_on: []
owns:
  - .github/workflows/ci.yml
  - tests/test_ci_floor.py
reads:
  - path: tests/test_findings_rounds.py
    symbols:
      - the vendored-fixture corroboration test that calls skipTest
forbid:
  - ctx/cli.py
  - tests/support.py
  - tests/test_config_levels.py
budget_tokens: 45000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Make a green CI run entail that the test suite actually ran, at close to its
current size, with the vendored-fixture guard among the tests that executed.

## Context

`.github/workflows/ci.yml:44` runs `python -m unittest discover -s tests` and
trusts its exit code. `unittest discover` **exits 0 on "Ran 0 tests"** — a broken
top-level import in `tests/support.py` yields an empty suite and all 8 matrix
jobs go green having proved nothing. Every later remediation wave is validated
against this signal, so it is fixed first.

Separately, `tests/test_findings_rounds.py` corroborates a 399-line vendored
fixture against `git show <ref>:ctx/findings.py` and calls `skipTest` when that
ref is unavailable. `actions/checkout@v4` defaults to a depth-1 shallow clone, so
the ref is never present and that guard has skipped in every CI job on every push
since it was written.

The suite is at 749 tests today. The floor is set just under that — not the 600
the roadmap suggested — because the point is to catch a silent *drop*, which a
floor of 600 cannot do.

## Interfaces

Produces nothing other units consume. Consumes nothing.

Do not edit `tests/test_findings_rounds.py`. This unit makes its guard *run*; it
does not change what the guard asserts.

## Acceptance criteria

1. The Test step parses the suite's own `Ran N tests` output and exits non-zero
   when `N` is below a floor of `740`, when the count cannot be parsed at all, or
   when the suite itself fails. The floor sits in one named place with a comment
   saying to raise it as the suite grows.
2. The failure message names both the observed count and the floor, so a person
   reading a red job knows whether tests vanished or never ran.
3. Positive control, and this is the criterion that matters: demonstrate the
   floor fires. Run the new Test step logic locally against a deliberately empty
   or trivially small suite — for example `discover` pointed at a directory with
   no tests — and paste the verbatim non-zero output into your report. A floor
   that has never been seen to fail is not a floor.
4. Exactly one job requests full history (`fetch-depth: 0`) so the vendored
   fixture guard can resolve its git ref. Prefer the `ledger` job or the single
   ubuntu/3.13 matrix entry over paying full-clone cost on all nine jobs; say in
   your report which you chose and why.
5. `tests/test_ci_floor.py` asserts, by parsing `.github/workflows/ci.yml` as
   YAML where possible and by targeted string match otherwise, that: the Test
   step contains a count check with a floor of at least 740, and some job sets
   `fetch-depth: 0`. These assertions must not be satisfiable by a prose comment
   — `report.md:179` records that existing meta-tests are substring greps a
   comment would satisfy, and this unit must not add another. State in your
   report how you avoided that.
6. Cross-platform: the count check must work on the `windows-latest` matrix jobs,
   not only on bash. Either keep the check inside Python rather than shell, or
   confirm the step declares a shell available on every matrix OS.
7. `python3 -m unittest discover -s tests` stays green and the count does not
   drop below 749.
8. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
verbatim failing output from the criterion-3 positive control · which job got
`fetch-depth: 0` and why · how criterion 5 avoids being comment-satisfiable · any
interface you were forced to change (that blocks the wave).
