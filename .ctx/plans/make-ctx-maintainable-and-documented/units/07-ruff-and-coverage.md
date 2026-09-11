---
ctx_schema: 1
unit: 07-ruff-and-coverage
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 06-docs-split-and-currency
owns:
  - pyproject.toml
  - .github/workflows/ci.yml
  - tests/test_ci_floor.py
  - tests/test_lint_and_coverage.py
reads:
  - path: ctx/cli.py
forbid:
  - README.md
  - docs/
budget_tokens: 55000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 7
---

## Objective
The two checks deferred from wave 2 to wave 3 and from wave 3 to here, now that
`pyproject.toml` exists to configure them and the code has stopped moving.

## Acceptance criteria

**ruff.**
1. CI runs `ruff` over `ctx/` and `tests/`, configured in `pyproject.toml`,
   pinned to an exact version like every other action and tool in this
   repository.
2. **The rule set may be narrow. It may not be empty.** Prefer rules the code
   already passes plus any whose violations you can fix within `owns` — but a
   configuration that selects nothing, or that excludes every file that would
   fail, is a check that cannot fail. Record the rules you chose and why, and
   record what you deliberately did not enable.
3. You may **not** fix lint violations in `ctx/*.py` — those files are outside
   `owns` and five other units just finished moving them. If a rule you want
   requires source changes, either leave that rule out and say so, or report it
   as a follow-up. Do not widen your scope to make a rule pass.
4. Ruff runs on Ubuntu only, not across the whole matrix. It is a static check;
   running it nine times proves the same thing nine times.

**Coverage.**
5. CI enforces a coverage floor, measured rather than chosen: run coverage, take
   the real number, set the floor just under it — the same margin discipline
   `SUITE_FLOOR` uses at 1190 against 1199. The audit measured 91% line / 89%
   branch at 625 tests; measure it again, do not reuse that number.
6. The floor is defined in **one** place, or pinned by a test that the copies
   agree — exactly the failure that made `SUITE_FLOOR` drift 449 tests from the
   suite it guarded. That test is the deliverable, not the number.
7. Coverage runs on one matrix entry, for the same reason as ruff.
8. A test proves the floor can fail: run the extracted step against a synthetic
   coverage figure below the floor and assert a non-zero exit, the way
   `test_ci_floor.py` already proves the suite floor behaviourally rather than
   by grepping YAML.

**A packaging gap unit 06 found and could not fix.**
9. `docs/` is not in `[tool.hatch.build.targets.sdist].include`. Unit 06 split
   the README into `docs/reference.md`, `docs/walkthroughs.md` and
   `docs/operations.md`, so **the sdist now ships a README linking to files it
   does not contain.** `AUDIT.md` and `PRODUCTION-AUDIT.md` were never
   allowlisted, so nothing regressed — but the split turned a harmless omission
   into a broken artifact. Add `docs/` to the allowlist.
10. A test asserts the built sdist actually contains the files the README links
    to. Wave 3 recorded that the SBOM step only worked because it was executed
    rather than trusted; the same applies here — build the artifact and look
    inside it rather than asserting the allowlist mentions a string.

**Do not disturb.**
11. Nothing else in `ci.yml` changes — not the SHA pins, not the `permissions:`
   blocks, not the two complementary `fetch-depth` checkout steps, not
   `SUITE_FLOOR`. Those are wave 2, 3 and 4 decisions with tests behind them.
12. `SUITE_FLOOR` and `REQUIRED_FLOOR` must still be equal when you finish. If
    this wave's refactors changed the test count, raising them is a deliberate
    wave-level act: report the new count and say what you did, rather than
    adjusting quietly.
13. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and
    `ctx ci` exit 0.
14. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
ruff rules enabled and deliberately not enabled, with reasons · the measured
coverage number and the floor chosen · the red run proving criterion 8 · any
lint violation you found in `ctx/` and left for a follow-up.
