---
ctx_schema: 1
unit: 08-test-lint-debt
plan: close-the-recorded-backlog
tier: subagent
depends_on:
  - 05-lint-debt-and-last-duplication
owns:
  - pyproject.toml
  - tests/test_audit_wave1.py
  - tests/test_core.py
  - tests/test_flow_end_to_end.py
  - tests/test_plan.py
  - tests/test_plan_lock.py
  - tests/test_cli_wiring.py
  - tests/test_sibling_scope.py
  - tests/test_supply_chain.py
  - tests/test_ci_floor.py
  - tests/test_dispatch.py
  - tests/test_dispatch_selection.py
  - tests/test_gate_bypass.py
  - tests/test_journal_authors.py
  - tests/test_tier_from_the_graph.py
reads: []
forbid:
  - tests/support.py
  - ctx/review.py
  - ctx/verify.py
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Turn on the last four ruff rules by fixing the 25 violations left in test
modules, so the ignore list holds only rules that are ignored on merit.

## Why this unit exists
Unit 05 fixed every one of these rules in `ctx/` and could not enable them:
the remaining sites are all in test modules, and its contract listed none of
them in `owns`. That was **my planning error, not its judgement** — the
criterion said "six test modules" while the ownership block named zero. This
unit is those files.

Unit 05 also re-derived the site list with `ruff check --isolated` and found
the previous comment wrong in five places — four wrong line numbers and one
wrong count. **Do the same: re-derive from a real ruff run rather than trusting
the list below**, which is recorded for scope, not as gospel.

## The 25 sites, as unit 05 measured them
- `F401` unused import — `test_audit_wave1` (`io`, `os`), `test_core` (`os`,
  `tempfile`), `test_flow_end_to_end` (`ctx.work`), `test_plan` (`ctx.spec`),
  `test_plan_lock` (`time`)
- `F841` unused local — `test_cli_wiring:221` (`unit`)
- `B007` unused loop variable — `test_sibling_scope:168`,
  `test_supply_chain:173` (×2)
- `E741` ambiguous name `l` — 14 sites across `test_ci_floor`,
  `test_cli_wiring`, `test_core`, `test_dispatch`, `test_dispatch_selection`,
  `test_gate_bypass`, `test_journal_authors`, `test_tier_from_the_graph`

## Acceptance criteria
1. `F401`, `F841`, `B007` and `E741` are removed from `ignore` in
   `pyproject.toml` and **selected**, and `ruff check ctx/ tests/` exits 0.
2. Every fix is a real correction. **A `# noqa` is not a fix**, and neither is
   deleting an assertion to remove the variable that fed it.
3. **`F841` and `B007` deserve a moment's thought each.** An unused local or
   loop variable in a *test* can mean the test meant to assert on it and
   doesn't. Before deleting `unit` at `test_cli_wiring:221` or the loop
   variables, check whether the assertion is missing rather than the variable
   being surplus. If one is a missing assertion, **write it** and say so — that
   is a real finding, and it is the reason these rules are worth turning on.
4. An `F401` import that exists for a side effect, or a name re-exported for
   another module, is not surplus. If you find one, keep it and record why
   rather than deleting it to satisfy the linter.
5. No test's behaviour changes. Every one of these files passes **unedited in
   substance** — renaming `l` to `line` is not a behaviour change; removing the
   only reference to a fixture is. The suite count must not fall.
6. The four ignored codes leave `pyproject.toml`'s comment with them. What
   stays ignored is only `E501`, `UP`, `Q`, `COM` and `PT`, with their existing
   recorded reasons untouched — do not re-litigate those.
7. `python3 -m unittest discover -s tests -q` passes; `ctx doctor`, `ctx ci`
   and `ruff` all exit 0. Do not move any floor.
8. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the site list you
re-derived and how it differed from the one above · **any missing assertion you
found behind an unused variable**, which is the most valuable thing this unit
can produce · anything you kept and why.
