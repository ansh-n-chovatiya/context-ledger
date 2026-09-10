---
ctx_schema: 1
unit: 06-merge-preflight
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on:
  - 03-ungated-is-not-done
owns:
  - ctx/worktree.py
  - tests/test_audit_merge_preflight.py
reads:
  - path: ctx/verify.py
    symbols:
      - run
      - verdict_of
      - PASS
      - FAIL
      - ERROR
      - PENDING
  - path: ctx/plan.py
    symbols:
      - find_unit
      - Unit
  - path: ctx/findings.py
    symbols:
      - load
      - blocking
  - path: tests/support.py
  - path: tests/test_merge_safety.py
  - path: tests/test_worktree.py
  - path: report.md
forbid:
  - ctx/cli.py
  - ctx/contract.py
  - ctx/review.py
  - ctx/snapshot.py
  - tests/test_merge_safety.py
  - tests/test_worktree.py
budget_tokens: 55000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 3
---

## Objective

Two findings in one function, `worktree.merge`'s preflight.

**(a) The all-ERROR bypass survives here.** `03-ungated-is-not-done` closed the
`--status done` transition: a gate in which no result reached PASS now refuses,
because an all-ERROR gate is ungated and ungated is not done. The merge preflight
was named in the same decision but sits in a file that unit did not own, so at
`ctx/worktree.py:445-450` an all-ERROR verdict is still treated as a warning —
the merge proceeds and then sets `status="done"`. `ctx merge` is therefore a
complete route around the fix that just landed, on the same default configuration:
a machine that has not run `ctx trust` errors every `cmd` check.

**(b) The PENDING refusal is a hollow guard.** `ctx/worktree.py:442` refuses a
merge when the verdict is PENDING — a `rubric` or `human` check that nobody has
signed off. `report.md:189` records that mutating `if verdict == verify.PENDING:`
to `if False:` **survives the entire suite**, and that `PENDING` and `sign-off`
appear nowhere in `tests/test_merge_safety.py` or `tests/test_worktree.py`. FAIL
and ERROR at the same call site are both covered; the judged half — the half no
command can decide, which is the half the product's pitch rests on — is guarded by
code nobody is watching. This is `PRODUCTION-AUDIT.md` mutation-survivor #2,
flagged as untested two minor versions ago and still untested.

## Scope note

(b) is roadmap item 10, nominally wave 2. It is pulled forward deliberately
because it is a hollow guard in the exact function (a) rewrites — leaving it
untested while editing around it is how the mutation survivor got there in the
first place. The other half of item 10, `config.normalise_level`, is a different
file and stays out of scope.

## Constraints

- Mirror the shape of the fix in `cli._gate_check`, do not invent a second
  vocabulary for it: refuse unless some result reached PASS, and say so in terms
  the user has already seen from the done-gate.
- Do **not** edit `tests/test_merge_safety.py` or `tests/test_worktree.py`. Read
  them for setup patterns; put new tests in your own file. Their existing FAIL and
  ERROR coverage at this call site must keep passing untouched.
- Do not weaken the merge's other refusals — the base-branch guard, the ledger
  check, and the dirty-tree check are prior-audit remediations that still hold.

## Acceptance criteria

1. `worktree.merge` refuses when the gate produced no PASS, for the same reason
   the done transition does, and does **not** set `status="done"` on refusal.
2. The default-configuration path is covered end to end, as it is for the done
   transition: a unit whose checks are all `cmd`, on a ledger with no trust
   acceptance, cannot be merged. Assert the branch was not merged and the unit
   file still does not read `status: done`.
3. A gate with at least one PASS alongside some ERRORs still merges, with today's
   warning behaviour.
4. Whatever `--force`-equivalent the merge path already offers still works, and
   says what it overrode.
5. The PENDING refusal is armed by a test that fails when the branch is removed:
   arm a unit with an unsigned `rubric` check, assert `merge()` returns `False`,
   and assert the message names sign-off. Verify by mutation — change
   `if verdict == verify.PENDING:` to `if False:`, confirm your new test fails,
   then restore. Report the mutation result explicitly.
6. The FAIL and ERROR refusals at that call site still behave as they do today,
   proven by the existing tests continuing to pass.
7. `tests/test_audit_merge_preflight.py` covers criteria 1-5, each refusal paired
   with a positive control proving a legitimate merge still succeeds.
8. `python3 -m unittest discover -s tests` is green, including every existing test
   in `tests/test_merge_safety.py` and `tests/test_worktree.py`.
9. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
verbatim result of the criterion 5 mutation check, both directions · any interface
you were forced to change.
