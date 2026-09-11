---
ctx_schema: 1
unit: 02-plan-scoped-lock
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on: []
owns:
  - ctx/lock.py
  - ctx/state.py
  - tests/test_plan_lock.py
reads:
  - path: ctx/paths.py
    symbols:
      - Layout
forbid:
  - ctx/atomic.py
  - ctx/findings.py
  - ctx/phases.py
  - ctx/telemetry.py
  - ctx/cli.py
budget_tokens: 45000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Generalise the `O_EXCL` lock that `state.locked` already holds correctly under
8-way contention into a named, reusable lock, so the unit file, the findings and
phases ledgers and the telemetry rotate can each serialise their
read-modify-write without four more copies of the same loop.

## Interfaces

Produces — units 06 and 08 code against this exact signature:

```python
# ctx/lock.py
LOCK_TIMEOUT = 5.0          # seconds to wait before giving up
LOCK_STALE_SECONDS = 120.0  # a lock older than this is reclaimed

@contextlib.contextmanager
def held(layout, name):
    """Serialise one read-modify-write under `.ctx/runtime/locks/<name>.lock`.

    `name` is slugified; callers pass a logical key such as
    f"plan-{slug}" or "telemetry". Yields True when the lock was actually
    taken and False when it was not — callers that must know may check, and
    callers that only want best-effort serialisation may ignore it.

    Fails open, exactly as `state.locked` does today: a lock that cannot be
    taken is a reason to risk a lost update, never a reason to break a
    session. EACCES and EROFS break immediately (a read-only checkout has
    nothing to serialise against).
    """
```

`state.locked` must be rewritten to delegate to `lock.held(layout, "state")` and
keep its current external behaviour — the existing state-lock tests are not yours
to edit and must pass unchanged. The lock file moves from
`.ctx/runtime/state.lock` to `.ctx/runtime/locks/state.lock`; if any existing test
asserts that literal path, say so in your report rather than editing it, and keep
the old path for `state` specifically.

## Acceptance criteria
1. `ctx/lock.py` exists with `held(layout, name)` as specified, Python 3.8
   compatible.
2. `state.locked` delegates to it and every existing state-lock test passes
   untouched, including the 8-processes × 40-increments contention test that
   currently produces exactly 320.
3. The same contention test, re-run against `lock.held` under a non-`state`
   name, produces no lost updates.
4. Two holders are impossible for the stale-reclaim path *as exercised*: a test
   takes the lock, ages the file past `LOCK_STALE_SECONDS` by setting its mtime,
   and asserts the reclaiming holder gets it while the original holder's release
   does not unlink a lock it no longer owns. If you cannot make the
   double-holder window reproduce, say so plainly in the report — the spec puts
   the theoretical race out of scope and an unreproducible claim must not be
   written as a passing test.
5. Positive control: a test that removes the locking from `held` (or drives the
   unlocked path directly) demonstrably loses updates, so criterion 3 is not
   vacuous. Quote both runs.
6. `name` is slugified so a plan slug containing `/` or `..` cannot escape
   `.ctx/runtime/locks/`. A test asserts an attempted escape stays inside.
7. Locks live under `runtime/`, which is already gitignored — assert that a lock
   taken during a test leaves `git status` clean.
8. `python3 -m unittest discover -s tests -q` passes; suite count strictly up.
9. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · whether
criterion 4's reclaim race reproduced or not, stated either way · any interface
you were forced to change (that blocks the wave).
