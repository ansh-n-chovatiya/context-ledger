---
ctx_schema: 1
unit: 02-seal-and-diff-scope
plan: close-the-routes-around-the-gate
tier: subagent
depends_on:
  - 01-input-limits
owns:
  - ctx/contract.py
  - ctx/cli.py
  - ctx/verify.py
  - tests/test_gate_bypass.py
reads:
  - path: ctx/snapshot.py
    symbols:
      - capture_before
  - path: ctx/worktree.py
    symbols:
      - the merge preflight gate
forbid:
  - ctx/spec.py
  - ctx/config.py
  - tests/test_input_limits.py
  - tests/test_config_levels.py
budget_tokens: 120000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective

Close the four routes around the gate: re-sealing an edited contract, dispatching
without a seal at all, hiding an out-of-scope edit by committing it, and siblings
failing each other's gates.

## Context

Wave 1 made the gate strong **where it runs**. These findings share one shape —
the routes *around* running it are not themselves gated. They were found by using
the tool on itself, and two of them partly reopen what wave 1 closed. Read
`git log ab02287..HEAD` for why the wave-1 fixes took the shape they did before
changing that code.

These are coupled on purpose and travel as one unit: the seal must learn a commit
SHA (`contract.py`), the gate must refuse an unsealed unit and pass wave scope
down (`cli.py`), and the diff check must use both (`verify.py`). Splitting them
would mean units calling functions their siblings had not written yet.

**Finding 1 — re-sealing an edited contract.** `ctx start --rebaseline <unit>`
re-seals. A dispatched runner has Bash, so it can re-seal a contract it just
edited. Findings *are* preserved across a re-seal, so blocking findings cannot be
erased this way; the exposed surface is the `verify` block, `owns`, and the
criteria body.

**Finding 2 — no seal at all.** The seal exists only if the orchestrator
dispatched through `ctx start`. A direct Task call leaves the unit unsealed and
nothing warns, at dispatch or at the gate.

**Finding 3 — committing hides the edit.** `verify.changed_files`
(`verify.py:721`) shells out to `git status --porcelain --untracked-files=all`,
which by construction only ever sees the working tree. Once a runner commits, its
out-of-scope changes are invisible to the check meant to police them.

**Finding 4 — siblings fail each other.** The diff check has no wave-scope
expansion, so a concurrent sibling's uncommitted files fail this unit's gate
(`report.md:85`). The current workaround is a manual `git stash` dance; it is
error-prone and has already cost time in two sessions.

**Finding 9 — refusals reported as success.** `ctx start` prints "not ready to
dispatch — nothing was started" and returns **0** — a refusal reported as
success, reached by `return 0` rather than by `SystemExit`, so the previous
wave's allowlist removal did not catch it. `ctx load` and `ctx merge` do the same
for a name that does not resolve.

## Interfaces

**Recorded decisions — these are settled, not open questions.**

*Diff scope:* the gate compares the **dispatch seal's commit** to `HEAD` plus the
working tree. The seal records HEAD at dispatch; committing therefore no longer
hides anything. You must define and test the behaviour when history moves under a
running unit (rebase, amend, or a seal SHA no longer reachable) — a seal pointing
at a vanished commit must fail loudly, never pass by default.

*Rebaseline:* `--rebaseline` retakes the review **baseline** only. When the
`verify` block, `owns`, or criteria body differ from the seal, it **refuses**,
names which fields changed, and directs the user to a distinct, loudly-journalled
`--reseal`. Shape of the refusal:

```
ctx start --rebaseline 03-foo
  contract changed: verify[0].run, owns[+1]
  REFUSED - baseline retake will not re-seal a changed contract
  to accept the new contract: ctx start --reseal 03-foo
```

`contract.compare(layout, slug, unit)` and `contract.field_digests(doc)` already
exist and are how you tell which fields moved. Do not rewrite `verify.is_ledger`
— wave 1 deliberately closed contract forgery by comparing against a dispatch
seal instead, and this unit does not revisit that.

## Acceptance criteria

1. `--rebaseline` refuses when `verify`, `owns`, or the criteria body differ from
   the seal, and the refusal names the changed fields. Retaking a baseline with
   an *unchanged* contract still works.
2. `--reseal` exists, accepts the changed contract, and writes a journal entry
   recording which fields changed. It is distinct from `--rebaseline` and never
   implied by it.
3. `ctx unit <name> --status done` refuses when no dispatch seal exists, and
   names `ctx start` as the route that creates one.
4. `ctx status` and the wave board show an unsealed unit as **unsealed**, not as
   pending, so the gap is visible before the gate.
5. A runner that edits a file outside its `owns` and **commits** it still fails
   its gate. Prove this with a test that actually commits, not one that stages.
6. A seal whose recorded commit is no longer reachable (amended or rebased away)
   fails loudly with a message naming the missing SHA. It must not pass, and it
   must not crash.
7. A unit's diff check ignores changes confined to a **sibling's** `owns` within
   the same wave, and still fails on a change to a path owned by nobody.
8. Criterion 7 is not a new hole: a sibling's path is ignored only while that
   sibling is in flight in the same wave — not once it is gated, and never for a
   unit outside the wave. Test all three cases.
9. `ctx start` when nothing is ready to dispatch exits non-zero. `ctx load` and
   `ctx merge` exit non-zero for a name that does not resolve. Existing tests in
   `tests/test_dispatch*.py` and `tests/test_merge_safety.py` that assert exit 0
   for these are updated; for each, say whether it was pinning the bug or
   intended behaviour.
10. The manual `git stash` workaround is no longer needed to gate a concurrent
    wave. Demonstrate it: create two units with disjoint `owns`, dirty both, and
    gate one without stashing the other. Paste the run.
11. Positive control. Mutate each guard and record the dead test verbatim, then
    revert: (a) let `--rebaseline` re-seal a changed contract; (b) let an unsealed
    unit gate; (c) revert the diff check to `git status` only, so a committed
    out-of-scope edit passes; (d) make wave-scope expansion ignore *every* path
    rather than only siblings'. Each must kill at least one test.
12. `python3 -m unittest discover -s tests` is green, the count has not dropped
    below where unit 01 left it, and `ctx doctor` and `ctx ci` exit 0.
13. No file outside `owns` is modified. `README.md` and `CHANGELOG.md` are not
    edited — report what the follow-up must say instead.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the four
mutation results from criterion 11 with the dead test for each · the criterion-10
demonstration output · what you chose for a seal SHA that has vanished and why ·
the per-test verdict from criterion 9 · what the README/CHANGELOG follow-up must
say · any interface you were forced to change (that blocks the wave).
