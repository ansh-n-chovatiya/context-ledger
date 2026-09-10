# Plan — make-the-ctx-cli-safe-to-automate

Spec: `.ctx/specs/make-the-ctx-cli-safe-to-automate/spec.md`

## Approach
<!-- One paragraph: how the work was cut up, and why along these lines. -->

## Units

**Wave 1** — these may run concurrently

- `01-ci-suite-floor` (subagent, pending) — Make a green CI run entail that the test suite actually ran, at close to its current size, with the vendored-f
  - owns: .github/workflows/ci.yml, tests/test_ci_floor.py
- `02-level-fallback-tests` (subagent, pending) — Pin `config.normalise_level`'s fail-down behaviour with tests that die when the fallback is mutated, so the gu
  - owns: tests/test_config_levels.py

**Wave 2** — these may run concurrently

- `03-cli-error-reporting` (subagent, pending) — Make every `ctx` command report failure the way a script can detect: a non-zero exit and a message on stderr, 
  - owns: ctx/cli.py, tests/support.py, tests/test_cli_exit_codes.py, tests/test_commands.py, tests/test_unproven_claims.py

## Out of scope
