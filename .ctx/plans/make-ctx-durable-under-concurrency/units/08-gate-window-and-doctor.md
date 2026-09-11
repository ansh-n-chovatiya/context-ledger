---
ctx_schema: 1
unit: 08-gate-window-and-doctor
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 01-atomic-writer
  - 02-plan-scoped-lock
  - 03-worktree-plan-scope
  - 05-journal-durability-and-authors
  - 07-migration-and-snapshot-durability
owns:
  - ctx/cli.py
  - ctx/plan.py
  - .ctx/.gitignore
  - tests/test_gate_window.py
  - tests/test_doctor_collisions.py
  - tests/test_durable_writes.py
reads:
  - path: ctx/atomic.py
    symbols:
      - write_text
  - path: ctx/lock.py
    symbols:
      - held
  - path: ctx/journal.py
    symbols:
      - author_key
      - legacy_day_files
      - write_digest
  - path: ctx/worktree.py
    symbols:
      - path_for
      - remove
      - branch_for
  - path: ctx/spec.py
    symbols:
      - next_adr_number
      - write_decision
  - path: ctx/contract.py
    symbols:
      - seal_findings
forbid:
  - ctx/journal.py
  - ctx/worktree.py
  - ctx/atomic.py
  - ctx/lock.py
  - ctx/migrate.py
  - ctx/spec.py
budget_tokens: 90000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 4
---

## Objective
Close the read-modify-write window that spans the done-gate and silently erases
`base_branch`, and give `ctx doctor` the two checks the wave's decisions traded
prevention for: duplicate ADR ids, and a `DIGEST.md` that is still tracked.

## Interfaces
Consumes `atomic.write_text` (01), `lock.held` (02), `worktree.path_for` /
`remove` in their new plan-scoped form (03), and `journal.author_key` /
`legacy_day_files` (05).

## Acceptance criteria

**The gate window (report §3.3, P1, roadmap item 18).** In `cmd_unit`,
`plan_mod.find_unit` parses the unit at t0, `_gate_before_done` then runs the
full verify suite — up to 240s per `cmd` — and `unit.set(status=...)` finally
writes the **whole t0 document** back. Any write landing in that window is
erased, including `worktree._record_fork_point`'s `base_branch`, which disarms
the merge-target guard `worktree.py` was built around.

1. `cmd_unit` re-reads the unit file after the gate returns and before it writes,
   and the read-modify-write is held under `lock.held(layout, f"plan-{slug}")`.
   The gate itself must run **outside** the lock — holding a lock for 240s would
   block every sibling in a concurrent wave, which is a worse defect than the one
   being fixed. Take the lock only around re-read → modify → write.
2. Positive control, and the point of this unit: a test that writes `base_branch`
   into the unit file *while a slow gate is running* and asserts the value
   survives the `done` transition. Make the gate genuinely slow (a verify `cmd`
   that sleeps) and do the interleaved write from another process or thread.
   Run it against the current code first — `base_branch` must be erased today —
   and quote both runs.
3. The status the gate decided on is still the status written. Re-reading must
   not let a concurrent `status:` write win over the gate's verdict; the gate's
   verdict wins and the other fields are preserved. Pin both halves.
4. `unit.set` in `plan.py` remains a whole-document write; the fix belongs at the
   call site that has the long window, not in a method with a dozen short-window
   callers. If you conclude otherwise, say so in your report rather than changing
   it quietly.
5. The same window in `cli.py:1610` (`unit.set(status="running")` at dispatch) is
   checked: state whether it has the same exposure and either fix it or record
   why it does not.

**`plan.json` durability (roadmap item 17).**
6. `plan.py`'s four `write_text` sites (~196, ~610, ~639, ~668) route through
   `atomic.write_text`. A mis-resolved or half-written `plan.json` silently
   corrupts the wave graph.
7. `tests/test_durable_writes.py` holds the **complete** enumeration: all six
   modules — `journal.py`, `plan.py`, `migrate.py`, `snapshot.py`, `bundle.py`,
   `contract.py` — parsed, asserting none calls `Path.write_text`. Unit 07 wrote
   a four-module version scoped to what it owned; this is the whole set, now that
   05 and this unit have landed. Name the modules explicitly. The runtime writers
   in `trust.py`, `verify.py`, `state.py` and `hooks.py` are deliberately excluded
   — say so in a comment so the next reader does not "fix" it.

**Doctor: duplicate ADR ids (decided, blocking question 2).** ADR numbers are
allocated local-max+1 (`spec.py:240-253`), so two branches both emit `0003-*.md`
under different slugs, merge cleanly, and leave two ADR 0003s with no git
conflict. **The decision is recorded: keep the zero-padded sequence, detect the
duplicate.** Do not implement ULIDs.

8. `ctx doctor` and `ctx ci` fail and name **both** colliding files when two ADRs
   share an id. A test writes two ADRs with the same number under different slugs
   and asserts the non-zero exit and both filenames in the message.
9. `spec.py` is not yours: `next_adr_number` keeps allocating local-max+1
   unchanged.
10. A tree with no duplicates is unaffected — assert `doctor` still exits 0 on
    this repository's own `.ctx/decisions/`.

**Doctor: tracked DIGEST.md (decided, blocking question 1).**
11. `.ctx/.gitignore` gains `journal/DIGEST.md`, and `DIGEST.md` is untracked in
    this repository. Run `git rm --cached .ctx/journal/DIGEST.md` as part of your
    change so the deletion lands in your unit's diff, and confirm it regenerates
    on the next hook fire.
12. For any *other* project, `ctx doctor` reports a tracked `DIGEST.md` as a
    merge-conflict source and prints the two commands to fix it. **No code path
    untracks a file in someone else's repository** — the tool advises, the human
    acts. A test asserts `doctor` prints the advisory and does not run `git rm`.
13. `ctx init` writes the gitignore line for new ledgers.
14. The advisory is not a failure: a tracked `DIGEST.md` elsewhere makes `doctor`
    report, not exit non-zero. Pin that, so an existing project does not have its
    CI broken by an upgrade.

**The worktree call site (from unit 03).**
15. `cmd_worktree` passes the active plan slug to `worktree.remove` when there is
    one, so the common case resolves without relying on 03's ambiguity scan. The
    ambiguity refusal stays reachable for the no-active-plan case; assert both.

**All.**
16. `python3 -m unittest discover -s tests -q` passes, `ctx doctor` exits 0 and
    `ctx ci` exits 0 on this repository. Suite count strictly up.
17. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs proving criterion 2 is a real positive control · your answers to
criteria 4 and 5, stated either way · confirmation that `DIGEST.md` regenerates
after being untracked · the final suite count, which unit 09 needs.
