---
ctx_schema: 1
unit: 03-cli-error-reporting
plan: make-the-ctx-cli-safe-to-automate
tier: subagent
depends_on:
  - 01-ci-suite-floor
  - 02-level-fallback-tests
owns:
  - ctx/cli.py
  - tests/support.py
  - tests/test_cli_exit_codes.py
  - tests/test_commands.py
  - tests/test_unproven_claims.py
reads:
  - path: ctx/hooks.py
    symbols:
      - main
forbid:
  - .github/workflows/ci.yml
  - tests/test_config_levels.py
  - ctx/config.py
budget_tokens: 90000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective

Make every `ctx` command report failure the way a script can detect: a non-zero
exit and a message on stderr, with unexpected exceptions turned into one
diagnosable line instead of a traceback.

## Context

`ctx/cli.py:2686`:

```python
HARD_FAIL = frozenset({"verify", "ci", "spec-ready", "plan-check", "doctor",
                       "migrate", "trust"})

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        message = str(exc)
        if message and not message.isdigit():
            if args.command in HARD_FAIL:
                print(message, file=sys.stderr)
                return 2
            print(message)
            return 0
        raise
```

Commands signal refusal by raising `SystemExit("message")`. For the 7 allowlisted
commands that becomes exit 2 on stderr. For the other **34** it is printed to
*stdout* and returns **0** — a pipeline cannot distinguish a refusal from a
success. There is also no catch-all: any exception that is not `SystemExit`
escapes as a raw traceback.

The recorded decision for this wave (`.ctx/specs/make-the-ctx-cli-safe-to-automate/questions.md`)
is the **hybrid**: real errors exit non-zero by default — that is what closes the
P0 — while `--strict`/`CTX_STRICT=1` additionally escalates paths that are
advisory by design. This is a deliberate breaking change.

## Interfaces

**Exit code contract, exactly this — do not invent a third code:**

- `0` — success, and advisory conditions when `--strict` is off.
- `1` — an advisory condition, escalated, only under `--strict`/`CTX_STRICT=1`.
- `2` — the command refused or failed: a message-carrying `SystemExit` from any
  command, or an unexpected exception.

`2` for refusals is chosen over `1` because the 7 currently-hard-failing commands
already return `2`; keeping it means this change only ever moves commands from
`0` to `2` and never renumbers an exit code a caller already depends on.

**`tests/support.py` — you own it, and every other test file reads it.** Its
`cli()` helper currently captures stdout only:

```python
def cli(self, *args):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli_main(["--cwd", str(self.root), *args])
    return code, buffer.getvalue()
```

Moving messages to stderr would silently empty that buffer for every existing
assertion on message text. Keep `cli()`'s two-value return shape and have its
second value contain **both** streams, so existing call sites keep working, and
add a *separate* accessor for tests that must tell the streams apart. Do not
change `cli()`'s arity or return shape — 41 test files call it.

## Acceptance criteria

1. `main()` has a catch-all. An unexpected exception prints
   `ctx <cmd> failed: <msg>` to stderr and returns 2. `KeyboardInterrupt` and
   `SystemExit` are not swallowed by it.
2. With `CTX_DEBUG=1` set, the full traceback is printed; without it, no
   traceback reaches the user. Both directions are tested.
3. A message-carrying `SystemExit` from **any** command returns 2 and writes to
   stderr. The `HARD_FAIL` allowlist is gone, replaced by an explicitly
   enumerated advisory set.
4. That advisory set is small, and each member carries a comment saying why it is
   advisory rather than an error. Derive it from behaviour, not guesswork: a
   condition qualifies only if exiting 0 is the *designed* outcome (a warning, a
   "nothing to do" notice), not an oversight. List the members and your reasoning
   in your report — this is the judgement call in this unit, and I want to see it.
5. `--strict` and `CTX_STRICT=1` both escalate advisory conditions to exit 1, and
   neither changes the exit code of a success or of an error. `--strict` is
   accepted on every command, not a subset. Precedence between flag and
   environment variable is tested and stated in your report.
6. `ctx spec` with an intent long enough to overflow the filesystem name limit
   exits 2 with a `ctx spec failed:` message and no traceback. Add a test that
   drives a genuinely over-long intent through `main()`. Note: a length *cap* on
   the slug is a separate finding and out of scope here — this unit only proves
   the catch-all converts it into a clean error.
7. Positive control, and this is the criterion that matters. For each of the
   three guards you add — the catch-all, the stderr routing, the strict
   escalation — mutate it so it no longer fires, run the suite, and record the
   test that died. Specifically: (a) remove the `except Exception` clause;
   (b) change the error path to write to stdout instead of stderr; (c) make
   `--strict` a no-op. Each must kill at least one test. Report the mutation, the
   dead test, and its assertion message, verbatim. Revert each after recording.
8. Existing tests in `tests/test_commands.py` and `tests/test_unproven_claims.py`
   that assert a command exits 0 on an error path are updated to assert the new
   code. For each one changed, your report states whether the old assertion was
   pinning the bug or pinning intended behaviour — if any turns out to be
   intended behaviour, that is an advisory-set member under criterion 4, not a
   test to rewrite.
9. `README.md` and `CHANGELOG.md` are **not** edited by this unit even though the
   change is breaking. Documenting it is a follow-up unit; note in your report
   exactly what needs saying so it can be written without re-deriving it.
10. `python3 -m unittest discover -s tests` is green, the count has not dropped
    below the level unit 02 left it at, and `ctx doctor` and `ctx ci` both exit 0.
11. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
advisory set with per-member reasoning (criterion 4) · the three mutation results
from criterion 7 with the test that died for each · the per-test verdict from
criterion 8 · flag-vs-environment precedence · what the changelog follow-up must
say (criterion 9) · any interface you were forced to change (that blocks the
wave).
