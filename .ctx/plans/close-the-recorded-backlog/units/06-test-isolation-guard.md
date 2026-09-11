---
ctx_schema: 1
unit: 06-test-isolation-guard
plan: close-the-recorded-backlog
tier: subagent
depends_on:
  - 05-lint-debt-and-last-duplication
owns:
  - tests/support.py
  - tests/test_isolation_guard.py
reads:
  - path: ctx/paths.py
    symbols:
      - Layout
      - CTX_DIRNAME
      - project_root
forbid:
  - ctx/review.py
  - ctx/commands.py
budget_tokens: 50000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Make it impossible for a test to write into the real project's `.ctx/`, and
find the one that did.

## What happened
During this wave a test escaped its fixture and overwrote the repository's own
`.ctx/.gitignore` with a single `*`. It was caught by a unit noticing the file
looked wrong, not by anything in the suite. **Committed, that one character
would have gitignored the entire `.ctx/` tree** — the plans, the specs, the
journal, the decisions — and the ledger would have silently stopped being
tracked. Nothing would have failed; it would just quietly not be a ledger any
more.

Every test is supposed to get its own `TemporaryDirectory` (`support.py:59-61`).
One did not, and the suite had no way to say so.

## Acceptance criteria
1. A test that writes anywhere inside the **real project's** `.ctx/` fails
   loudly, naming the test and the path. Put the guard where every test
   inherits it — `tests/support.py` is yours.
2. **Find the test that did it.** The evidence is a `.ctx/.gitignore` written
   as `*` with no trailing newline. Run the suite with the guard armed and
   report which test trips it. If more than one trips, report all of them.
3. Fix what you find, if the fix is inside `tests/support.py` or the guard
   itself. If the offending test is in a file you do not own, **report it with
   the exact fix** rather than editing it — that pattern has worked five times
   in this project and it is how scope stays honest.
4. The guard distinguishes the real ledger from a fixture's. A test's own
   `TemporaryDirectory` ledger must stay completely free to write; only the
   checkout this suite is running from is protected. Get this wrong in the
   strict direction and you break every test in the suite.
5. The guard cannot be the thing that breaks a session: it is test-only
   infrastructure and must not import from `ctx/` in a way that changes
   product behaviour.
6. A test proves the guard fires — write to the real `.ctx/` deliberately,
   inside a test designed to be caught, and assert it is caught. A guard that
   has never been seen to fire is the fail-green shape this project keeps
   finding.
7. Existing tests pass **unedited** unless one of them is the offender. Any
   test you must change is reported with the reason.
8. `python3 -m unittest discover -s tests -q` passes; `ctx doctor`, `ctx ci`
   and `ruff` exit 0. Do not move any floor.
9. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · **which test wrote
to the real ledger**, or a clear statement that the guard caught nothing and
what that means · the run showing the guard firing · anything you reported
rather than edited.
