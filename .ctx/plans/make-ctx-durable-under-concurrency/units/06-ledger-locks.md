---
ctx_schema: 1
unit: 06-ledger-locks
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 02-plan-scoped-lock
owns:
  - ctx/findings.py
  - ctx/phases.py
  - ctx/telemetry.py
  - tests/test_ledger_locks.py
reads:
  - path: ctx/lock.py
    symbols:
      - held
  - path: ctx/frontmatter.py
    symbols:
      - Document.write
      - read
forbid:
  - ctx/lock.py
  - ctx/state.py
  - ctx/cli.py
  - ctx/plan.py
budget_tokens: 60000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Serialise the three read-modify-write paths that currently lose updates: the
findings ledger between a reviewer and an implementer, the phases ledger the same
way, and `telemetry._rotate`, which is measured to discard **every** append made
while it runs.

## Interfaces
Consumes `lock.held(layout, name)` from unit 02 — a context manager yielding
True when the lock was taken, False when it was not, failing open either way.

## Acceptance criteria

**Telemetry rotate (report §3.3, P2, reproduced).** `telemetry.py:66-69` reads the
tail, then reopens the file `"w"` and rewrites it. 0 of 20 concurrent records
survived when the audit measured it.

1. `record` and `_rotate` run under `lock.held(layout, "telemetry")`, and the
   rewrite itself routes through an atomic replace rather than reopening the
   destination `"w"`.
2. Positive control: reproduce the audit's measurement first — 20 concurrent
   records across a forced rotate, asserting how many survive — and quote the
   before number. It should be at or near 0. Then assert 20 of 20 survive after.
   The test must fork or thread for real; a sequential loop cannot fail here and
   would be fail-green.
3. Telemetry still never raises and never breaks a session: a lock that cannot be
   taken, a read-only runtime directory and a full disk all leave `record`
   returning normally. Assert at least the read-only case.

**Findings and phases ledgers (report §3.3, P2).** `findings.py:305` and the
equivalent in `phases.py` read, modify and write with no lock, so a reviewer
recording a finding and an implementer resolving one lose each other's write.

4. Every read-modify-write in `findings.py` and `phases.py` holds
   `lock.held(layout, f"plan-{slug}")` for the span from read to write. The lock
   name must be the plan slug, not the unit — a reviewer and an implementer
   touching *different* units of the same plan still share `plan.json` and the
   round counter.
5. Positive control: a test where a reviewer write and an implementer write race
   on one findings ledger, asserting both survive. Show it losing one write
   against the current code and quote both runs.
6. The same for the phases ledger.
7. The lock is held across read *and* write, never re-acquired between them — a
   lock taken twice around the two halves serialises nothing. Make this visible
   in the code structure, not just correct by accident.
8. No deadlock: a path that already holds the plan lock and calls another locked
   function must not hang. Either make `held` re-entrant (coordinate with unit
   02's implementation — it is not yours to edit, so if it is not re-entrant,
   restructure your callers), or assert by test that no such nesting exists.
   State which in your report.
9. Gate and merge behaviour is unchanged: every existing findings and phases test
   passes untouched. If one fails, it is pinning something real — report it, do
   not edit it. Those files are not in your `owns`.
10. `python3 -m unittest discover -s tests -q` passes; suite count strictly up.
11. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after numbers for criteria 2 and 5 · your answer to criterion 8, stated
either way · any existing test that failed and what it was pinning.
