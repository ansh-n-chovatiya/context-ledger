---
ctx_schema: 1
unit: 01-input-limits
plan: close-the-routes-around-the-gate
tier: subagent
depends_on: []
owns:
  - ctx/spec.py
  - ctx/config.py
  - tests/test_input_limits.py
reads:
  - path: ctx/cli.py
    symbols:
      - cmd_spec
forbid:
  - ctx/cli.py
  - ctx/contract.py
  - ctx/verify.py
budget_tokens: 50000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Cap the spec slug where every caller benefits, and make the level fail-down say
so instead of demoting a project in silence.

## Context

Two independent findings, neither in `report.md`, sharing one file-ownership
boundary so they travel together.

**Slug length.** `ctx spec «a long intent»` raises an uncaught `OSError: [Errno
63] File name too long`. The catch-all added in the previous wave now converts
that into a clean `ctx spec failed:` line and exit 2, so it is no longer a
traceback — but the cap itself is still missing, and a clean refusal is a worse
outcome than a usable spec when the intent is merely wordy.

**Silent demotion.** `config.normalise_level` coerces any unrecognised level to
`"0"`, the least capable, and says nothing. `config.load` runs it on every read,
so a hand-edited `level: L3` in `ctx.yaml` demotes the whole project with no
message on any channel. The *direction* is correct and must not change:
`briefing_cap` is a spend limit, so coercing an unparseable level to the smallest
budget is the safe failure — mutating it to fail *up* buys a 2600-char briefing
off a typo instead of 220. The defect is the silence, not the coercion.

`tests/test_config_levels.py` already pins the current behaviour with 23 tests
including four mutation-proven ones. **You do not own that file.** Your change
must keep every one of those tests passing — if adding a warning breaks one, the
warning is on the wrong channel or in the wrong function.

## Interfaces

Consumes `spec.create(layout, slug, intent="", verify=None) -> (path, qpath)`
and `config.normalise_level(value) -> str`.

The cap goes in `ctx/spec.py`, not in `cmd_spec`. Every caller of `spec.create`
must benefit, not only the CLI path — that is criterion 7 of the spec and the
reason `ctx/cli.py` is in your `forbid` list rather than your `owns`.

## Acceptance criteria

1. A slug derived from a long intent is capped to a length that is safe on
   Linux, macOS and Windows. State the limit you chose and why in your report;
   the binding constraint is a filesystem component limit of 255 bytes, and the
   slug is a directory name with files beneath it.
2. The cap is deterministic: the same intent yields the same slug on every run
   and platform. If you disambiguate collisions with a hash, it must be derived
   from the intent, never from time, randomness, or iteration order.
3. Two different long intents that share a long common prefix do not collide
   into the same slug. Test this specifically — a naive truncation fails it.
4. Non-ASCII intents produce a valid slug rather than raising or emitting an
   empty string. An intent consisting entirely of characters that survive no
   normalisation must still yield a usable, non-empty directory name.
5. `ctx spec` with a 400-character intent now succeeds and creates a real spec
   directory, rather than exiting 2. Verify by running it, not by reasoning.
6. An unrecognised, non-`None` level emits one line on **stderr** naming the
   offending value and the level it fell down to. A recognised level, and `None`,
   emit nothing on any channel.
7. The warning does not fire once per config read in a way that floods output.
   Say in your report how often it fires for a single command invocation and why
   that is acceptable.
8. All 23 existing tests in `tests/test_config_levels.py` still pass, unmodified.
   That file is not in your `owns`; confirm with `git diff --exit-code
   tests/test_config_levels.py` and paste the result.
9. Positive control. Mutate each guard and record the test that dies, verbatim,
   then revert: (a) remove the slug cap; (b) make the collision disambiguator
   constant so two long intents collide; (c) remove the stderr warning. Each must
   kill at least one test.
10. `python3 -m unittest discover -s tests` is green, count not below 818.
11. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
chosen slug limit and the reasoning · the three mutation results with the dead
test for each · the `git diff --exit-code tests/test_config_levels.py` result ·
how often the warning fires per invocation · any interface you were forced to
change (that blocks the wave).
