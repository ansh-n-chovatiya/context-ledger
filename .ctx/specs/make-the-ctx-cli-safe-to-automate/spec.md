---
ctx_schema: 1
spec: make-the-ctx-cli-safe-to-automate
status: ready
created: 2026-09-10
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

`ctx` is currently safe to drive by hand and unsafe to drive from a script. Three
things cause that. CI's green light does not entail that the test suite ran —
`unittest discover` exits 0 on "Ran 0 tests", so a broken import in `support.py`
turns 8 matrix jobs green having proved nothing, and every downstream wave is
validated against that signal. `main()` has no catch-all, so an unexpected
exception reaches the user as a raw traceback rather than a diagnosable message.
And 34 of 41 commands report failure by printing to *stdout* and exiting 0, so a
pipeline cannot tell a refusal from a success. This wave makes "green" mean the
suite ran, makes failures legible, and makes exit codes trustworthy enough to
branch on.

## Acceptance criteria

1. CI fails when the suite does not run. Replacing `tests/` with an empty
   directory, or breaking a top-level import so discovery yields an empty suite,
   makes the Test step exit non-zero rather than green.
2. CI fails when the test count silently drops below the recorded floor, and the
   failure message names both the observed count and the floor.
3. The vendored-fixture guard in `tests/test_findings_rounds.py` executes rather
   than skipping in at least one CI job — the job that runs it has full history
   available, and a test asserts the workflow requests it.
4. An unexpected exception from any command prints `ctx <cmd> failed: <msg>` to
   stderr and exits 2. No traceback reaches the user unless `CTX_DEBUG=1` is set,
   in which case the full traceback is printed.
5. A command that refuses or fails — unreadable config, missing ledger, a failed
   gate — exits non-zero and writes its message to **stderr**, not stdout. This
   holds for all 41 commands, not an allowlisted 7.
6. Commands whose non-zero-worthy condition is genuinely advisory (warnings that
   today exit 0 by design) keep exiting 0 by default, and exit 1 under `--strict`
   or `CTX_STRICT=1`. The set of advisory conditions is enumerated in the code
   with a comment explaining why each is advisory rather than an error.
7. `ctx spec` with an intent long enough to overflow the filesystem's name limit
   fails with a `ctx spec failed:` message and exit 2, never an uncaught OSError.
8. `config.normalise_level` has tests that pin its fail-down behaviour, and
   mutating the fallback to return something other than `"0"` makes at least one
   test fail.
9. Every new test carries a positive control: for each guard added, the unit
   report names the mutation applied to the guard and the test that died as a
   result. A test that still passes when its mechanism is disabled does not count
   as coverage.
10. `python3 -m unittest discover -s tests` is green, the count has not dropped
    below 749, and `ctx doctor` and `ctx ci` both exit 0.

## Out of scope

- `--json` on `status`/`next`/`doctor`/`ci`/`verify`/`plan-check`/`findings`
  (roadmap item 9). It belongs to this wave but is sequenced as a second plan
  wave because it rewrites the same output paths in `cli.py` that criteria 4–6
  touch; running both concurrently guarantees a merge conflict in one file.
- `--cov-fail-under=88` and a `ruff` job (part of roadmap item 11). Both need
  dev-dependency configuration that arrives with `pyproject.toml` in roadmap
  item 12, wave 3. Deferred deliberately, not forgotten.
- The merge gate's `PENDING` refusal (roadmap item 10, first half) — already
  closed in wave 1 by `worktree.py:442`.
- Every other still-open finding: the release artifact, the policy layer, the CI
  recipe rewrite, and the five undocumented gate-bypass findings. Those are
  waves 3+ and get their own specs.
- Changing what any command *does*. This wave changes how commands report, not
  what they decide.

## Notes

Criterion 5 is a deliberate behaviour change, chosen over the roadmap's
opt-in-only `--strict`: an opt-in flag mitigates the P0 rather than closing it.
Anyone scripting against the current exit 0 was scripting against a bug, but the
change is real and belongs in the changelog as breaking.

The mechanism today is `HARD_FAIL` (`cli.py:2682`) — an allowlist of 7 commands
for which a `SystemExit` carrying a message becomes exit 2. The shape of the fix
is to invert it: a message-carrying `SystemExit` is an error everywhere, with a
small enumerated advisory set as the exception.
