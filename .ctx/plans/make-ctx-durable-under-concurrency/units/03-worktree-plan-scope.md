---
ctx_schema: 1
unit: 03-worktree-plan-scope
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on: []
owns:
  - ctx/worktree.py
  - tests/test_worktree_plan_scope.py
  - tests/test_worktree.py
  - tests/test_merge_safety.py
  - tests/test_git_invariant.py
  - tests/test_audit_merge_preflight.py
  - tests/test_audit_override_journal.py
  - tests/test_audit_wave1.py
  - tests/test_flow_end_to_end.py
reads:
  - path: ctx/plan.py
    symbols:
      - find_unit
      - Unit
  - path: ctx/cli.py
    symbols:
      - cmd_worktree
forbid:
  - ctx/cli.py
  - ctx/plan.py
  - ctx/atomic.py
  - ctx/lock.py
  - ctx/journal.py
budget_tokens: 60000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Give `worktree.path_for` the plan slug that `worktree.branch_for` already has, so
`ctx worktree remove 01-api --force` run from plan-b stops deleting plan-a's live
worktree and the uncommitted work inside it.

## Interfaces

```python
# ctx/worktree.py
def path_for(layout, plan_slug, unit_name):
    """.ctx/runtime/worktrees/<plan_slug>/<unit_name>. `plan_slug` is required."""

def remove(layout, unit_name, plan_slug=None, delete_branch=True, force=False):
    """Signature unchanged. `plan_slug=None` now *resolves* rather than guesses."""
```

Two decisions are already made; implement them rather than reopening them.

**`plan_slug` is a required positional on `path_for`.** Not an optional keyword.
Every caller omits it today — that is the defect, so an optional parameter
preserves it for everyone who does not opt in.

**`remove` with no slug refuses an ambiguous name instead of picking one.** Scan
`.ctx/runtime/worktrees/*/` for a directory named `unit_name`. Exactly one match:
proceed with that slug. More than one: refuse, naming every plan that has a tree
by that name and telling the caller to pass `--plan`. Zero: today's
not-found behaviour. This is what lets `ctx worktree remove 01-a` keep working
unchanged for the ordinary single-plan case — which several existing CLI tests
drive — while making the cross-plan case a refusal rather than a silent deletion.
`ctx/cli.py` is not yours; leave `cmd_worktree` alone and it will keep working.

`worktree.py` has five internal `path_for` callers: `create` (~151), `remove`
(~253), `changed_paths` (~311, which recovers the unit name by
`branch.rsplit("/", 1)[-1]` and must now recover the slug from the branch too),
`merge`'s gate `cwd` (~446) and the conflict message (~517).

You own nine test files because `path_for` and `remove` are called from all of
them: `test_git_invariant.py` (~299, 310, 314, 333, 348, 370),
`test_worktree.py` (~104, 114, 116, 216, 365), `test_merge_safety.py` (~233, 236),
`test_audit_wave1.py` (~340), `test_audit_merge_preflight.py` (~94, 119),
`test_audit_override_journal.py` (~221), `test_flow_end_to_end.py` (~212).
**Change the call, never the assertion.** If an assertion has to move, that is a
finding for your report, not a quiet edit — a previous wave in this repo changed
three assertions in unowned test files and each one had been pinning real
behaviour.

## Acceptance criteria
1. `path_for(layout, plan_slug, unit_name)` returns a path containing the plan
   slug, and every internal caller passes a real slug.
2. Positive control, and the reason this unit exists: a test creates a worktree
   for unit `01-api` in plan-a and another for `01-api` in plan-b, runs the
   plan-b removal with `--force`, and asserts plan-a's worktree directory and
   the uncommitted file inside it both still exist. Run this test against the
   current code first — it must delete plan-a's tree today — and quote both runs
   in your report. A test for this that passes before your change is not a test.
3. `remove` with no `plan_slug` and two matching trees refuses, names both plans,
   and deletes neither tree and neither branch.
4. `remove` refuses a tree whose checked-out branch is not
   `branch_for(plan_slug, unit_name)`, returns a message naming both branches,
   and leaves the tree and branch alone.
5. `changed_paths` still reports committed and uncommitted work correctly now
   that it derives the slug from the branch rather than the unit name alone. A
   test covers a plan slug containing a hyphen, so the split is not merely
   guessable from the happy path.
6. A worktree directory left under the old flat layout is not silently orphaned:
   either `remove` finds it or the code says it cannot find it. State which you
   chose and cover it with a test.
7. Every existing test in the nine owned files passes with call sites updated and
   assertions unchanged.
8. `python3 -m unittest discover -s tests -q` passes; the suite count is strictly
   above the 1027 it starts at.
9. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs proving criterion 2 is a real positive control · which option
you took for criterion 6 · any assertion in an owned test file you found yourself
wanting to change, and why you did not.
