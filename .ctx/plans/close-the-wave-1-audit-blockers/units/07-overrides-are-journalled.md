---
ctx_schema: 1
unit: 07-overrides-are-journalled
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on:
  - 04-baseline-survives-restart
  - 06-merge-preflight
owns:
  - ctx/cli.py
  - tests/test_audit_override_journal.py
reads:
  - path: ctx/worktree.py
    symbols:
      - merge
  - path: ctx/journal.py
    symbols:
      - append
  - path: ctx/verify.py
    symbols:
      - PASS
      - FAIL
      - ERROR
      - PENDING
  - path: tests/support.py
  - path: tests/test_audit_merge_preflight.py
  - path: report.md
forbid:
  - ctx/worktree.py
  - ctx/contract.py
  - ctx/review.py
  - ctx/snapshot.py
  - ctx/hooks.py
budget_tokens: 35000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 4
---

## Objective

Close an audit-trail asymmetry between the two ways to override the done-gate.

`03-ungated-is-not-done` made `ctx unit --status done --force` journal what it
stepped over: the entry records that the gate was overridden and the reason it
refused. `06-merge-preflight` added the equivalent refusal to `worktree.merge` and
an equivalent override in `--skip-gate`, but `cmd_merge` in `ctx/cli.py` journals
only `ok` or `refused`. The override text is emitted as a merge *message* — it
scrolls past in the terminal and is gone.

That is the shape the audit objects to elsewhere in this report: `CTX_GATE=off`
is called out at `report.md:123` precisely because it removes the control with
"no audit record". An override nobody can find later is not meaningfully
different from no gate. The two override paths should leave the same kind of
trace.

## Constraints

- `ctx/worktree.py` is **not** yours. `merge` already returns `(ok, messages)`
  and `06-merge-preflight` established that surface — consume it, do not change
  it. Everything you need is in what `merge` already hands back.
- Match the vocabulary `03-ungated-is-not-done` established for the forced-done
  journal entry. A reader grepping the journal for overrides should find both
  with one search; do not invent a second phrasing.
- Do not add a new journal event kind if an existing one fits.

## Acceptance criteria

1. A `--skip-gate` merge writes a journal entry recording that the gate was
   overridden and what was skipped, not merely `ok`.
2. That entry is greppable alongside the forced-done entry from
   `03-ungated-is-not-done`: one search over the journal finds both overrides.
   Assert this directly — perform the search in the test rather than asserting
   two separate strings.
3. An ordinary merge that passed its gate does **not** produce an override entry.
4. A refused merge still journals the refusal as it does today.
5. `tests/test_audit_override_journal.py` covers 1-4, with the negative control at
   criterion 3 proving the entry is written because of the override and not on
   every merge.
6. `python3 -m unittest discover -s tests` is green, including
   `tests/test_audit_merge_preflight.py`, `tests/test_worktree.py` and
   `tests/test_merge_safety.py`.
7. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the exact
journal line a skip-gate merge now writes · any interface you were forced to
change.
