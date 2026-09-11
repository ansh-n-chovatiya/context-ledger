---
ctx_schema: 1
unit: 07-migration-and-snapshot-durability
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 01-atomic-writer
  - 06-ledger-locks
owns:
  - ctx/migrate.py
  - ctx/snapshot.py
  - ctx/bundle.py
  - ctx/contract.py
  - tests/test_migration_durability.py
reads:
  - path: ctx/atomic.py
    symbols:
      - write_text
  - path: ctx/findings.py
    symbols:
      - path_for
  - path: ctx/phases.py
    symbols:
      - path_for
  - path: ctx/config.py
    symbols:
      - SCHEMA
forbid:
  - ctx/atomic.py
  - ctx/findings.py
  - ctx/phases.py
  - ctx/journal.py
  - ctx/plan.py
  - ctx/cli.py
budget_tokens: 60000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Make an interrupted migration leave a ledger that can still be migrated, and make
`ctx migrate --check` stop reporting clean on a tree whose findings and phases
ledgers sit at a schema the running plugin will silently misparse.

## Interfaces
Consumes `atomic.write_text(path, text, encoding="utf-8")` from unit 01.

## Acceptance criteria

**Interrupted migration (report §3.3, P1).** `migrate.py:147,153` write `ctx.yaml`
and `plan.json` with plain `write_text`, so an interrupted bulk migration leaves a
half-written config — and the module's "idempotent, rerun it" promise then fails,
because `_CONFIG_LINE` matches against garbage.

1. Both `migrate` writes route through `atomic.write_text`.
2. Positive control: interrupt a bulk migration mid-run (patch `os.replace` to
   raise on the Nth call), then assert `ctx.yaml` is still parseable, that
   `migrate --check` reports the file's *true* schema, and that re-running the
   migration completes. Quote the current-code run showing the half-written file
   and the promise failing.
3. `snapshot.py`'s three manifest writes (~225, ~264, ~324) and `bundle.py`'s
   context index (~133) and `contract.py`'s seal write (~244) route through it
   too — every write of a git-tracked ledger artifact. Runtime and gitignored
   files (`trust.py`, `verify.py`'s output capture, `state.py`'s nudge,
   `hooks.py`'s error log) are deliberately out of scope: they are disposable and
   routing them buys nothing.
4. A test enumerates **the four modules this unit owns** by parsing them and
   asserts none calls `Path.write_text` — so the next site someone adds there
   fails this test instead of shipping. Name the modules explicitly rather than
   globbing `ctx/*.py`: a glob would also catch the deliberately-excluded runtime
   writers, and the test would then get weakened to nothing to make it pass.
   `journal.py` and `plan.py` are **not** yours to enumerate — unit 05 is
   rewriting one and unit 08 the other, in this wave and the next. Unit 08 owns
   the complete enumeration across all six modules once they have all landed.

**Migration blind spots (report §3.3, P1, roadmap item 20).** `migrate.py:55-62`
globs task/spec/questions/unit/decision/bundle plus `plan.json`, but not the
findings or phases ledgers — both of which stamp `ctx_schema`. Findings gate
merges, so a misread ledger is a merge that should have been blocked.

5. `discover()` reports `findings/` and `phases/` ledgers with their true
   `ctx_schema`, using the same path shapes `findings.path_for` and
   `phases.path_for` produce. Read those functions; do not re-derive the glob
   from memory.
6. Positive control: `ctx migrate --check` on a tree holding an out-of-date
   findings ledger exits non-zero and names the file. Show it reporting clean
   against the current code first.
7. The same for a phases ledger.
8. `ctx migrate` actually migrates both new families, and re-running it is a
   no-op. A test asserts the second run reports nothing to do.
9. `python3 -m unittest discover -s tests -q` passes; suite count strictly up.
10. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs proving criteria 2, 6 and 7 are real positive controls · the
exact module list criterion 4's enumeration covers and why each excluded writer is
excluded.
