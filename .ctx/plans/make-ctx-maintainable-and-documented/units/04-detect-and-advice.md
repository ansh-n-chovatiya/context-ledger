---
ctx_schema: 1
unit: 04-detect-and-advice
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 03-verify-dispatch-and-gate
owns:
  - ctx/detect.py
  - ctx/complexity.py
  - ctx/advice.py
  - ctx/cli.py
  - tests/test_detect.py
  - tests/test_advice.py
  - tests/test_kind_table_reaches_complexity.py
reads:
  - path: ctx/config.py
    symbols:
      - DEFAULTS
      - PROFILES
  - path: ctx/trust.py
    symbols:
      - is_accepted
forbid:
  - ctx/verify.py
  - ctx/config.py
  - ctx/paths.py
  - README.md
budget_tokens: 75000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 3
---

## Objective
Three of the five extractions: the ecosystem detector, the product's decision
tree, and the five-way duplicated plan-resolution preamble.

## Interfaces

```python
# ctx/detect.py — from cli.py:151-341
def availability(...)      # was cli._availability, now public
# plus the ecosystem taxonomy and the profile markers

# ctx/advice.py — from cli.py
def next_action(layout, config, state) -> ...   # was cli._next_action

# ctx/cli.py
def _plan_or_report(layout, args, verb)   # the unified preamble
```

## Acceptance criteria

**`detect.py` (extraction 1).**
1. The ecosystem taxonomy and subprocess probing at `cli.py:151-341` live in
   `ctx/detect.py`. `cmd_ci` calls a **public** function rather than reaching
   into a private `_availability` — that reach-through is what identified this
   extraction, so leaving it private would miss the point.
2. `_PROFILE_MARKERS` moves with it and gains `research`, **or** `research` is
   removed from the profiles `--profile` accepts. Today `config.py` declares
   five profiles, `--profile` accepts all five, and the markers know four, so
   `research` can be selected but never auto-detected. State which you chose and
   why; a test pins that accepted profiles and detectable profiles agree.

**`advice.py` (extraction 3).**
3. `_next_action` lives in `ctx/advice.py` and is **callable without argparse**.
   It encodes the entire product decision tree and is currently reachable only
   by constructing a parser, which is why it is hard to test.
4. A test calls it directly across the states it decides between, which was not
   previously possible. This is new coverage of existing behaviour, not new
   behaviour.

**`_plan_or_report()` (extraction 5).**
5. The five duplicated plan-resolution preambles become one helper.
6. **The trap, named by the audit:** the copies differ in which argument they
   read — `args.plan` in some, `args.name` in others. A mechanical
   deduplication silently changes which argument a command reads. Enumerate all
   five call sites in your report with the argument each read **before** your
   change, and assert per call site that it still reads the same one.

**A gap unit 03 found in its own work and could not close.**
7. `complexity.py:30` does `_JUDGED_KINDS = tuple(verify.JUDGED)` **at import
   time**, so a judged kind registered into `verify.KIND_TABLE` after import is
   invisible to `complexity`. That silently weakens unit 03's central claim —
   a new kind is one dict entry only if every consumer sees it. Read
   `verify.JUDGED` at the call site instead of freezing it at import. Unit 03
   reported this rather than reaching outside its scope to fix it; `complexity.py`
   is now in your `owns` so you can.
8. A test registers a **judged** synthetic kind into `KIND_TABLE` after import
   and asserts `complexity.score` treats it as judged — the `judged_verify`
   weight of 2.0 must apply. Positive control: show it scoring as un-judged
   against the current code first. Then check whether any other module freezes
   a `verify` tuple at import time the same way; report what you find, and fix
   only what is in your `owns`.

**All three.**
9. **Behaviour-preserving.** Pre-existing tests covering all three areas pass
   **unedited**. Any test that must change is reported, not edited.
10. No import cycle: neither new module may import `cli.py`. A test walks the
   import graph and asserts the tree is still acyclic — the audit verified zero
   cycles across 27 modules and this wave must not be what introduces one.
11. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
    exit 0. Do not lower `SUITE_FLOOR` or `REQUIRED_FLOOR`.
12. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
five call sites for criterion 6 with the argument each read before and after ·
your decision on `research` · confirmation no pre-existing test was edited.
