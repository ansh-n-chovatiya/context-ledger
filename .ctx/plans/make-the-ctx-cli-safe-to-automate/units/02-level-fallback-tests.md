---
ctx_schema: 1
unit: 02-level-fallback-tests
plan: make-the-ctx-cli-safe-to-automate
tier: subagent
depends_on: []
owns:
  - tests/test_config_levels.py
reads:
  - path: ctx/config.py
    symbols:
      - normalise_level
      - briefing_cap
      - LEVELS
      - DEFAULTS
forbid:
  - ctx/cli.py
  - ctx/config.py
  - tests/support.py
  - .github/workflows/ci.yml
budget_tokens: 45000
status: running
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective

Pin `config.normalise_level`'s fail-down behaviour with tests that die when the
fallback is mutated, so the guard stops being hollow.

## Context

`ctx/config.py:187` is three lines:

```python
def normalise_level(value):
    text = str(value if value is not None else "0").strip().upper().lstrip("L")
    return text if text in LEVELS else "0"
```

It silently coerces anything unrecognised to `"0"`, which is the *least*
capable level — a fail-down. `report.md` item 10 calls this a hollow guard, and
a grep confirms it: `normalise_level` has **zero** references anywhere under
`tests/`. It survives the full 749-test suite when mutated. Whether the fail-down
is the right policy is not this unit's question; that it is unpinned is.

Note the interaction worth covering: `briefing_cap` calls `normalise_level` and
then looks the result up in `DEFAULTS["briefing_chars"][f"l{level}"]`. A fallback
that returned an unknown level would raise `KeyError` there, so the fail-down is
load-bearing for a caller, not just cosmetic.

## Interfaces

Consumes `ctx.config.normalise_level(value) -> str` and
`ctx.config.briefing_cap(config, level) -> int`. Produces nothing other units
consume.

**This unit is read-only with respect to `ctx/config.py`.** It writes tests only.
If you conclude the fail-down policy itself is wrong, say so in your report —
do not change it. That is a finding, not this unit's scope.

## Acceptance criteria

1. Tests cover, at minimum: each valid level in `LEVELS` round-tripping; the
   documented input spellings `"l0"`, `"L2"`, `"2"`, `2`, and `None`; leading and
   trailing whitespace; an unrecognised string; an unrecognised *type* (a list or
   an object whose `__str__` is not a level); and the empty string.
2. A test asserts the fail-down target specifically — that an unrecognised input
   yields `"0"` and not merely "some member of LEVELS". This is the assertion
   that a mutation must break.
3. A test covers the `briefing_cap` interaction: an unrecognised level still
   returns the L0 default cap rather than raising `KeyError`.
4. Positive control, and this is the criterion that matters. Apply each of these
   mutations to `ctx/config.py` in turn, run the suite, record which test failed,
   then **revert the mutation**:
   a. `return text if text in LEVELS else "0"` → `return text if text in LEVELS
      else "2"` (fail-*up* instead of down)
   b. the same line → `return text` (no fallback at all)
   c. delete the `.strip()` call
   d. delete the `.lstrip("L")` call
   Each mutation must kill at least one test. Report the mutation, the test that
   died, and its assertion message, verbatim. If any mutation leaves the suite
   green, your tests do not yet cover it — add one that does.
5. `ctx/config.py` is byte-identical to its committed state when you finish.
   Verify with `git diff --exit-code ctx/config.py` and paste the result.
6. `python3 -m unittest discover -s tests` is green and the count has risen above
   749 by the number of tests you added.
7. No file outside `owns` is modified.

## Return contract

Report: files changed · which criteria passed · verbatim verify output · the
four mutation results from criterion 4 with the test that died for each · the
`git diff --exit-code ctx/config.py` result · whether you think the fail-down
policy is correct (as a finding, not a change) · any interface you were forced to
change (that blocks the wave).
