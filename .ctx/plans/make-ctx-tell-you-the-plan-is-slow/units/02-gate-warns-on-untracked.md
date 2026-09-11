---
ctx_schema: 1
unit: 02-gate-warns-on-untracked
plan: make-ctx-tell-you-the-plan-is-slow
tier: subagent
depends_on: []
owns:
  - ctx/verify.py
  - tests/test_gate_untracked_warning.py
reads:
  - path: ctx/paths.py
    symbols:
      - LEDGER_PREFIX
forbid:
  - ctx/commands.py
  - ctx/plan.py
  - ctx/complexity.py
  - ctx/dispatch.py
budget_tokens: 35000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Warn at the done-gate when a unit leaves new files untracked, because the gate
runs before the commit and a whole class of check cannot see them.

## Why — this is a real bug, not a hypothetical
The done-gate runs **before** the work is committed. So any test that enumerates
through `git ls-files` is blind to the files the unit under test has just
written: they are still untracked. It passes vacuously at gate time and goes red
on the next commit.

This happened here on 2026-09-11. One unit wrote `docs/*.md` and a link checker
in the same change; the checker enumerated tracked files, found none of the new
pages, and passed. Three broken anchors shipped, and `ctx unit --status done`,
`ctx doctor` and `ctx ci` all reported green on a tree that was already red.

The gate cannot know which tests are scoped that way. It can know that new files
exist, and say so.

## Acceptance criteria
1. When the gate passes and the unit has untracked files inside its `owns`, it
   prints one advisory line naming the count, and says a check that enumerates
   `git ls-files` will not see them until they are committed.
2. It is **advisory only**: the verdict, the exit code and the journal entry are
   unchanged. This warns; it never blocks. A gate that started refusing on
   untracked files would break every normal unit, since producing new files is
   what most units do.
3. Ledger churn is excluded. `.ctx/` paths are already classified by
   `is_ledger`; reuse it rather than writing a second rule.
4. A test drives a unit that creates an untracked file and asserts the line
   appears; another asserts a unit with no new files stays silent; a third
   asserts the exit code and verdict are identical in both cases.
5. Contracts in this wave are lighter by design — asserting the new output is
   enough here, and no "before" reproduction is needed for a warning that did
   not previously exist.
6. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. Do not move `SUITE_FLOOR` or `REQUIRED_FLOOR` (pinned equal at 1420).
7. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the warning line,
quoted · confirmation that verdict and exit code are unchanged.
