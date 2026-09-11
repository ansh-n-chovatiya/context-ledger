---
ctx_schema: 1
unit: 02-lock-reclaim
plan: close-the-recorded-backlog
tier: subagent
depends_on: []
owns:
  - ctx/lock.py
  - ctx/state.py
  - tests/test_lock_reclaim.py
reads: []
forbid:
  - ctx/commands.py
  - ctx/journal.py
  - ctx/telemetry.py
budget_tokens: 45000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Settle the stale-lock reclaim double-holder path: reproduce it and fix it, or
report honestly that it does not reproduce.

## This unit is allowed to conclude "no bug"
`report.md` §3.3 records that the stale-lock reclaim "can theoretically hand
the lock to two holders and unlinks locks it does not own", explicitly
**code-path only, not reproduced under 4-way contention**. Wave 4 put it out of
scope on the grounds that it needs a reproduction before it needs a fix, and
wave 6's spec repeated that.

So the deliverable is a verdict with evidence, not a fix at any cost.
**Manufacturing a passing test for a race nobody has demonstrated is the worst
outcome available here** — it would read as coverage of a guarantee that was
never established.

## Acceptance criteria
1. Attempt the reproduction properly: multiple real processes, a lock aged past
   `LOCK_STALE_SECONDS` by setting its mtime, and a window where the original
   holder is still running when the reclaimer takes it. Use the deterministic
   rendezvous idiom already in `tests/test_ledger_locks.py` (`_RENDEZVOUS`,
   `_wait_for`) rather than a timing race — a racing test that fails to
   reproduce proves nothing either way.
2. **If it reproduces:** fix it so two holders cannot both believe they hold
   the lock, and so a holder never unlinks a lock file whose token it does not
   own. Wave 4 already added token-ownership checking on release; verify
   whether that closed half of it.
3. **If it does not reproduce:** say so plainly, describe exactly what you
   tried and why the path resists it, and leave a test documenting the
   behaviour you *could* establish. Do not leave a test named for a race it
   does not exercise.
4. Either way, the existing guarantees hold untouched: the 8×40 contention test
   still lands on exactly 320, `state.locked` still delegates and its tests
   still pass unedited, and failing open is still what happens when a lock
   cannot be taken.
5. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. Do not move any floor.
6. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · **the verdict:
reproduced and fixed, or not reproduced** · exactly what you tried · what the
test you leave behind actually asserts.
