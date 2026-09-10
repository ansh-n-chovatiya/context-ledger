---
ctx_schema: 1
unit: 01-verify-kinds
plan: close-the-wave-1-audit-blockers
tier: subagent
depends_on: []
owns:
  - ctx/verify.py
  - tests/test_audit_verify_kinds.py
  - tests/test_gates.py
reads:
  - path: ctx/config.py
    symbols:
      - PROFILES
      - load
  - path: ctx/hooks.py
    symbols:
      - on_stop
  - path: tests/support.py
  - path: report.md
forbid:
  - ctx/cli.py
  - ctx/hooks.py
  - ctx/review.py
  - ctx/snapshot.py
  - ctx/contract.py
budget_tokens: 60000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: diff
wave: 1
---

## Objective

Close two audit findings that both live in `ctx/verify.py`:

**(a) Real regressions are laundered as infrastructure.** `_check_cmd` calls
`_missing_tool(output)` on the combined stdout+stderr of the *test run*, not on
the launcher's failure. A unit that deletes or renames a module produces
`ModuleNotFoundError: No module named 'ctx.foo'` inside pytest's own output, which
matches `_MISSING_TOOL[0]` and becomes ERROR — and ERROR is treated as
non-blocking downstream. The precise regression the gate exists to catch is the
one it launders. The second pattern,
`(?m)^.*?([\w.-]+): (?:command )?not found`, additionally trips on any test that
legitimately asserts a shell error string anywhere in a long log.

**(b) `exists` and `symbol` are a content oracle.** `verify.run` consults
`trust.load` only for `kind == "cmd"`, while `_check_exists` and `_check_symbol`
honour absolute paths (`os.path.isabs(raw)` branches). A committed `.ctx/ctx.yaml`
can therefore declare `{kind: exists, path: /Users/victim/.ssh/id_rsa, matches:
"BEGIN OPENSSH"}` and read the PASS/FAIL verdict as a one-bit oracle over any
readable file, with `symbol`'s `contains` echoing the probe string back in the
gate message. `matches` is also an unbounded `re.search`, and the gate's
`timeout_seconds` budget is applied **only** on the `cmd` branch — a
catastrophic-backtracking pattern runs unbounded inside the `Stop` hook.

## Decisions already taken (do not relitigate)

- These kinds are **not** trust-gated. They execute nothing; confining the path is
  what removes the oracle. Do not add a `trust.load` consultation for them, and do
  not touch the `cmd` trust gate — the audit found it sound.
- The per-check opt-out for (a) is spelled `optional: true`.
- `verdict_of`'s ranking (FAIL > PENDING > ERROR > PASS) is correct and stays.

## Interfaces

Produces, for `03-ungated-is-not-done` and `04-baseline-survives-restart` to code
against — keep these signatures stable:

- `verify.run(layout, config, checks, cwd, key, owns, recorded, judged)` →
  `(results, verdict)` — unchanged.
- `verify.Result(kind, label, status, message="", log_path=None)` — unchanged.
- `verify.PASS / FAIL / ERROR / PENDING` and `verify.verdict_of(results)` —
  unchanged.
- `verify.is_ledger(path)` — unchanged, and deliberately keeps its current
  breadth. Contract forgery is caught by `03` against the snapshot, not here.
- New: `verify.KINDS` gains no members. `optional` is a per-check key, not a kind.

## Acceptance criteria

1. A `cmd` check that launched successfully and exited non-zero is FAIL, not
   ERROR, even when its output contains `ModuleNotFoundError: No module named
   'ctx.foo'`, `bash: frobnicate: command not found`, or any other
   `_MISSING_TOOL` signature. Classification as infrastructure is decided by *how
   the launcher failed*, not by sniffing the child's output.
2. A launcher that genuinely could not start is still ERROR naming the tool:
   exit 127, and `OSError` raised by `subprocess.run`.
3. `python3 -m pytest` on a machine without pytest still reports ERROR rather than
   FAIL. This is the case `_missing_tool` was introduced for, and it must keep
   working — reach it through the pre-flight availability path or an explicit
   `optional: true`, not through output sniffing.
4. A check declaring `optional: true` that cannot run returns ERROR without the
   run being treated as a work failure. A check that does not declare it gets no
   such benefit.
5. An `exists` or `symbol` check whose `path` is absolute returns ERROR whose
   detail names the refusal (e.g. "path must be inside the project"), never PASS
   and never FAIL — so the verdict leaks nothing about the file's contents.
6. The same for a path that escapes the project root via `..`, including after
   `resolve_cwd` is applied, and including a symlink whose target resolves
   outside the root.
7. A `matches` regex is bounded by the same gate deadline the `cmd` kind uses. A
   catastrophic-backtracking pattern against a large file returns ERROR at the
   deadline instead of running unbounded. Assert this with a wall-clock bound in
   the test, not by eye.
8. Every existing test in `tests/` still passes. One amendment is authorised and
   only one: `tests/test_gates.py::test_absent_tool_that_exits_1_is_still_a_config_error`
   asserts the laundering this unit removes, and reaches it by *faking* a log
   line. Rewrite its body so it reaches the same case through a genuinely absent
   module — the pre-flight — and keep its intent, its name and its docstring.
   `test_missing_tool_detection_is_targeted` and
   `test_a_real_test_failure_is_still_a_work_failure` stay untouched and must
   still pass. No other existing test may be edited; in particular the
   `exists`/`symbol` tests using ordinary relative paths must be untouched.
9. `tests/test_audit_verify_kinds.py` covers criteria 1-7, each with a positive
   control: for every "this is refused" assertion, a sibling assertion proves the
   same mechanism produces a real verdict when it should. A negative assertion
   with no demonstration that the mechanism can fire is the fail-green shape this
   audit exists to remove.
10. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · any
interface you were forced to change (that blocks the wave).
