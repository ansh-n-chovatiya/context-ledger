---
ctx_schema: 1
spec: close-the-wave-1-audit-blockers
status: ready
created: 2026-09-10
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

The v0.8.0 enterprise readiness audit (`report.md`) found that two of the
product's three core promises are mechanically defeatable, and that one path
executes attacker-controlled code from a cloned repo before the trust gate ever
runs. Wave 1 closes the six findings behind those two verdicts: one code
execution hole, four independent ways to reach `status: done` with nothing
verified, and one content-oracle primitive in the file-reading verify kinds.
None of them is architectural — each is a missing guard on an existing, well
placed seam. When this spec is satisfied, "done" means a check passed, and
`ctx doctor` on a hostile clone runs no code the trust store has not accepted.

## Acceptance criteria

### A. The availability probe executes no repo code

1. A repo containing `evilpkg/__init__.py` that writes a marker file, plus a
   committed `.ctx/ctx.yaml` declaring `run: python3 -m evilpkg.sub`, leaves the
   marker absent after `ctx init`, `ctx doctor` and `ctx ci` each run in that
   repo. A test asserts the marker does not exist.
2. The probe still reports availability correctly: a genuinely importable module
   yields `(True, "")`, a genuinely absent one yields
   `python3 cannot import <name>`, and a binary off PATH yields
   `<name> is not on PATH`. Existing probe tests continue to pass unchanged.
3. The hostile-clone test is a positive control: it also asserts the marker file
   *is* written when the same package is imported directly, so the test cannot
   pass vacuously if the fixture stops being parsed.

### B. An all-ERROR gate cannot mark a unit done

4. `ctx unit <name> --status done` on a unit whose every check returns ERROR
   exits non-zero, leaves the unit's `status:` unchanged, and prints a message
   naming the configuration problem and the fact that ungated is not done.
5. The same all-ERROR result from the `Stop` hook still returns `""` — a session
   is never bricked by infrastructure — and is still journalled as incomplete
   rather than as a pass.
6. `--force` still completes the transition, and the journal entry for a forced
   done records that the gate was overridden.
7. A gate with at least one PASS and some ERRORs behaves as it does today, and a
   bare `ctx verify` still reports ERROR without refusing anything — the refusal
   belongs to the `--status done` transition and the merge preflight only.

### C. Real regressions are not laundered as missing tools

8. A `cmd` check that launches successfully and exits non-zero with
   `ModuleNotFoundError: No module named 'ctx.foo'` anywhere in its output is
   FAIL, not ERROR. A test asserts the verdict for exactly this output.
9. A launcher that genuinely could not start — exit 127, or `OSError` from
   `subprocess.run` — is still ERROR with a message naming the tool.
10. A project that deliberately wants a check skipped when its tool is absent
    declares `optional: true` on that check, and only such a check ERRORs on an
    absent tool. Checks that do not declare it do not get the benefit.
11. A test whose own output contains a shell "not found" string does not turn a
    genuine failure into an ERROR.

### D. The work's own contract is visible to the gate

12. A unit whose unit file has its `verify:` block, `owns:` list or acceptance
    criteria modified after dispatch cannot reach `done`: the gate compares the
    unit's contract against the `before` snapshot and refuses on a mismatch,
    naming the field that changed.
13. Deleting or downgrading a blocking finding from
    `.ctx/plans/<slug>/findings/<unit>.md` after dispatch is likewise refused.
14. Ordinary ledger churn — the unit's own `status:` field, journal appends,
    `state.json`, telemetry — is still not a scope violation, so a unit that did
    nothing but its declared work still passes its gate.

### E. A re-run of `ctx start` cannot destroy the review baseline

15. Running `ctx start` twice for the same wave does not overwrite an existing
    `before` snapshot for any unit that already has one.
16. After the second run, `ctx review` for such a unit still diffs against the
    pre-work baseline, so a unit with real changes produces a non-empty package
    rather than an empty one that reviews as `approved`.
17. `ctx start` sets `status: running` on the units it dispatches, so the second
    run reports them as in flight rather than silently re-snapshotting them. An
    explicit `--rebaseline` deliberately re-captures a named unit after a real
    crash. The board, `_next_action` and the gate each behave sensibly for a unit
    in `running` — a status no code path set before this change.

### F. The file-reading kinds are not a content oracle

18. An `exists` or `symbol` check whose `path` is absolute, or escapes the
    project root via `..`, returns ERROR naming the refusal — never PASS or FAIL,
    so the verdict carries no information about the file's contents. These kinds
    are *not* trust-gated: they execute nothing, and confinement is what removes
    the oracle.
19. A `matches` regex is bounded by the same gate deadline the `cmd` kind uses: a
    catastrophic-backtracking pattern returns ERROR at the deadline instead of
    running unbounded inside the `Stop` hook.
20. `python3 -m unittest discover -s tests` stays green, and the suite grows by
    at least one test per finding above.

## Out of scope

- Waves 2 through 5 of the report's roadmap: the `main()` catch-all, `--strict`,
  `--json`, packaging and tagging, the policy layer, the CI floor, the durability
  fixes and the `cli.py` extractions. They are separately shippable and get their
  own plan.
- The P1 and P2 findings not listed above, including the absolute host paths in
  the journal, the `redact` gaps, `--sign-off` being unconditional, `forbid`
  being advisory, and `escalate_on_failed_round` never reaching a re-dispatched
  runner.
- Any change to the trust store's design, keying, or `command_id` derivation. The
  audit found the `cmd` trust gate itself sound; this spec does not touch it.
- Rewriting the two prior audit documents. Their disposition is a separate call.

## Decisions taken

Recorded in `questions.md`, and binding on the plan:

- **exists/symbol:** confine to the project root; do not trust-gate. The `cmd`
  trust gate is untouched.
- **`ctx start`:** sets `running`, skips snapshots for in-flight units, and gains
  an explicit `--rebaseline` escape hatch.
- **Contract tampering:** caught by comparing the unit's contract against the
  `before` snapshot inside the gate. `is_ledger` keeps its current breadth, so
  sibling ledger writes in a concurrent wave stay non-violations.
- **Opt-out spelling:** `optional: true`.
- **Scope of the all-ERROR refusal:** the `--status done` transition and the
  merge preflight; a bare `ctx verify` stays a report.

## Notes

Every finding here was re-verified against the working tree before this spec was
written, not taken from the report on faith: the probe's `find_spec` call, the
`ERROR` branch that returns `None` from `_gate_before_done`, the `_MISSING_TOOL`
patterns scanning combined stdout+stderr, `is_ledger` exempting all of `.ctx/`,
`snapshot.capture` calling `rmtree` before writing, and `_check_exists` honouring
absolute paths with no trust consultation.

Two of the report's other findings reproduced live while this spec was being
created: `ctx spec` with a long intent raised an uncaught `OSError` traceback
(no catch-all in `main()`, and no length cap on a spec slug). Both belong to
wave 2 and are recorded here only as corroboration.
