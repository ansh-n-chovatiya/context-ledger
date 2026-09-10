---
ctx_schema: 1
unit: 03-ungated-is-not-done
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on:
  - 01-verify-kinds
  - 02-probe-isolation
owns:
  - ctx/cli.py
  - ctx/hooks.py
  - ctx/contract.py
  - tests/test_audit_ungated_gate.py
reads:
  - path: ctx/verify.py
    symbols:
      - run
      - verdict_of
      - PASS
      - FAIL
      - ERROR
      - PENDING
      - Result
      - is_ledger
  - path: ctx/snapshot.py
    symbols:
      - capture
      - snapshot_dir
      - load_manifest
  - path: ctx/review.py
    symbols:
      - before_key
      - capture_before
  - path: ctx/plan.py
    symbols:
      - find_unit
      - Unit
  - path: ctx/findings.py
    symbols:
      - load
      - blocking
  - path: ctx/config.py
    symbols:
      - PROFILES
  - path: tests/support.py
  - path: report.md
forbid:
  - ctx/verify.py
  - ctx/review.py
  - ctx/snapshot.py
budget_tokens: 75000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 2
---

## Objective

Make `status: done` mean a check actually passed. Two findings, both landing in
`_gate_before_done`.

**(a) An unrunnable check scores as a passing check.** `verify.verdict_of`
correctly ranks ERROR above PASS, but both consumers then treat ERROR as
non-blocking: `_gate_before_done` prints a warning and returns `None`, so
`cmd_unit` proceeds to `unit.set(status="done")`. Combine that with
`PROFILES["code"] = []` — the default profile carries only `cmd` checks, and on
any machine that has not run `ctx trust`, **every** `cmd` check returns ERROR.
Clone the repo on a second laptop or in CI, `ctx start`, `ctx unit 01-api --status
done`, and you get *"warning: no check could run… so this is not blocking"*,
status `done`, plan advanced, board green, zero checks executed. This is the
default configuration on a freshly cloned repo.

**(b) A runner can forge its own contract.** The `unit-runner` agent holds `Write`
and `Edit`, and `verify.is_ledger` exempts everything under `.ctx/` from the
`diff` scope check. A runner that cannot make the tests pass can edit its own unit
file — delete the failing `verify:` entry, append `verified: [rubric, human]`,
trim an acceptance criterion — or delete blocking findings from
`.ctx/plans/<slug>/findings/<unit>.md`. `_check_judged` passes on the mere
*presence* of a kind in `recorded`; `_check_review` passes on an empty ledger.
Nothing hashes or compares the contract.

## Decisions already taken (do not relitigate)

- Forgery is caught by **comparing against the `before` snapshot inside the
  gate**, not by narrowing `verify.is_ledger`. `is_ledger` keeps its current
  breadth: narrowing it re-creates the concurrent-wave false Criticals that
  `review.wave_scope` exists to prevent, because a sibling unit's legitimate
  ledger writes land between this unit's before and after captures.
- The all-ERROR refusal applies to the `--status done` transition and the merge
  preflight **only**. A bare `ctx verify` is a report, not a transition, and keeps
  printing ERROR without refusing. The `Stop` hook keeps returning `""` on ERROR —
  bricking every session in a project whose toolchain is not installed is not an
  improvement.
- `--force` remains the escape hatch, and stays loud.

## Design

Put the contract hashing in a **new module `ctx/contract.py`** rather than in
`cli.py`, which the audit already flags at 2,380 lines. Suggested surface:

- `contract.digest(unit)` → a stable hex digest over the fields that constitute
  the promise: `verify`, `owns`, `reads`, `forbid`, `depends_on`, `recorded`/
  `verified`, and the `## Acceptance criteria` section body. Deliberately **not**
  `status`, which the runner is supposed to flip.
- `contract.baseline(layout, slug, unit)` → the digest recorded at dispatch, or
  `None` when there is no snapshot to compare against.
- `contract.compare(layout, slug, unit)` → `(ok, changed_fields)`.

Read the unit's pre-work bytes from the `before` snapshot that `review.capture_before`
already stores under `review.before_key(slug, unit.name)` — the unit file is
inside `.ctx/`, which `snapshot.capture` fingerprints. If the snapshot has no
entry for the unit file, treat that as "no baseline" (see criterion 7), not as a
violation.

## Interfaces

Produces, for `04-baseline-survives-restart` to code against:

- `contract.digest(unit)` → `str`
- `contract.compare(layout, slug, unit)` → `(bool, list[str])`
- `_gate_before_done(layout, config, slug, unit)` → `int | None` — signature
  unchanged; `None` still means "the transition may proceed".

Consumes, unchanged, from `01-verify-kinds`:
`verify.run(...)` → `(results, verdict)`, `verify.PASS/FAIL/ERROR/PENDING`,
`verify.is_ledger(path)`.

## Acceptance criteria

1. `ctx unit <name> --status done` on a unit whose every check returns ERROR exits
   non-zero, leaves the unit's `status:` field unchanged on disk, and prints a
   message that names the configuration problem *and* says that ungated is not
   done.
2. The specific default-configuration path is covered end to end: a plan whose
   units carry only `cmd` checks, on a ledger with an empty trust store, cannot
   reach `done`. Assert the unit file still reads `status: pending` afterwards.
3. A gate with at least one PASS alongside some ERRORs behaves exactly as it does
   today — the refusal is for *no check reached PASS*, not for *any check
   errored*.
4. `--force` still completes the transition, and the journal entry for a forced
   done records that the gate was overridden and why it was refused.
5. The `Stop` hook (`hooks.on_stop`) still returns `""` for an all-ERROR result
   and still journals it as incomplete rather than as a pass. Assert the return
   value directly; a session must never be bricked by infrastructure.
6. A bare `ctx verify` on an all-ERROR unit still reports ERROR and does not
   refuse anything.
7. A unit whose `verify:` block, `owns:` list, or acceptance-criteria body was
   modified after dispatch cannot reach `done`: the gate refuses and the message
   names which field changed. Cover all three fields separately.
8. Deleting or downgrading a blocking finding from
   `.ctx/plans/<slug>/findings/<unit>.md` after dispatch is likewise refused.
9. Flipping only the unit's own `status:` field is **not** a violation, and a unit
   that did nothing but its declared work still passes its gate. Journal appends,
   `state.json` writes and telemetry during the gate are likewise not violations.
10. A unit with no `before` snapshot at all — dispatched before this change, or
    snapshotted on a machine where the capture failed — is not refused for
    forgery. It reports that no baseline exists and falls through to the ordinary
    gate. Silently failing closed here would brick every in-flight plan on
    upgrade.
11. `tests/test_audit_ungated_gate.py` covers criteria 1-10, each with a positive
    control proving the mechanism fires when it should.
12. `python3 -m unittest discover -s tests` is green, including every existing
    test in `tests/test_gates.py` and `tests/test_merge_safety.py`.
13. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the exact
`contract.digest` field list you settled on · any interface you were forced to
change (that blocks `04`).
