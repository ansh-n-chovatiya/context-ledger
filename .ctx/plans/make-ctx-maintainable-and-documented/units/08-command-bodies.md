---
ctx_schema: 1
unit: 08-command-bodies
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 05-commands-registry-and-json
owns:
  - ctx/cli.py
  - ctx/commands.py
  - tests/test_shared_paths.py
  - tests/test_command_bodies.py
reads:
  - path: ctx/verify.py
    symbols:
      - verify_plan
      - gate_before_done
      - gate_check
      - echo
  - path: ctx/advice.py
    symbols:
      - next_action
      - wave_in_flight
  - path: ctx/detect.py
    symbols:
      - availability
forbid:
  - ctx/verify.py
  - ctx/advice.py
  - ctx/detect.py
  - README.md
  - .github/workflows/ci.yml
budget_tokens: 90000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 5
---

## Objective
Move the 41 `cmd_*` bodies out of `cli.py` into `ctx/commands.py`, so `cli.py`
is the entry point and the registry rather than a quarter of the codebase.

## Why this unit exists, stated plainly
It was not in the original plan. Unit 05 delivered the `COMMANDS` registry and
`--json` and correctly reported that the spec's "under 2,000 lines" was
unreachable from inside its scope: **deleting `build_parser` entirely at HEAD
would still have left 2,691 lines.** That target was the orchestrator's
invention — the audit asked for five named extractions, all of which have
landed, and never named a size. But `cli.py` is now 3,328 lines, larger than
the 2,380 the audit complained about, because four waves of fixes and then
`--json`'s data collection all landed in it. The finding is "a quarter of the
codebase and imports 21 of 26 modules", and that is still true. This unit
addresses the finding rather than the number.

## Interfaces

```python
# ctx/commands.py — the 41 cmd_* functions and their private helpers
# ctx/cli.py     — argument parsing, the COMMANDS registry, _emit, main()
```

`cli.py` keeps `main`, `build_parser`, `commands()`, `Command`, `_emit`,
`_finish` and the shared plumbing the registry needs. Everything a subcommand
*does* moves. Where a helper is used only by command bodies it moves with them;
where it is used by both, keep it in `cli.py` and import it.

**`commands.py` must not import `cli` at module level** — `cli` imports it, and
`tests/test_no_import_cycles.py` from unit 05 asserts the module-level graph is
acyclic and that nothing imports `cli`. A function-level import is the existing
idiom if you genuinely need one; unit 05 pinned three such deliberate cycle
breakers by name, so add yours to that list if you create one.

## Acceptance criteria
1. `ctx/commands.py` holds the 41 `cmd_*` functions. `cli.py` is **under 2,000
   lines**, and unit 05's ceiling test is updated to the new real figure with
   the same "cannot silently grow back" intent.
2. **Behaviour-preserving, and this is a pure move — there is no failing case
   to reproduce.** The proof is that the pre-existing tests pass **unedited**.
   1,376 of them cover this surface. If any test needs editing, stop and
   report: that means the move changed behaviour.
3. Unit 05's generated 824-line parser-surface fixture
   (`tests/test_commands_registry.py::SURFACE`) still passes **untouched**. It
   pins every flag, dest, default, type, choices and help string of all 41
   subcommands, so it is the strongest single check that this move was
   invisible. Do not regenerate it — regenerating a fixture to match your own
   change is how a move stops being verifiable.
4. Unit 05's seven JSON golden outputs still pass untouched, and human output
   stays byte-identical.
5. `tests/test_shared_paths.py::ALL_MODULES` gains `commands.py`. **It is in
   your `owns` precisely because this unit adds a module** — two earlier units
   correctly blocked rather than edit it, and this is the change that is
   allowed to. Add the entry; do not convert the tuple to a glob, which is the
   one thing it exists to prevent.
6. The four audit test files that pin private `cli._*` names still pass. Unit
   04 left six one-line aliases in `cli.py` for exactly this reason; keep them
   working, wherever the target now lives, and keep the test asserting each
   alias `is` the public function.
7. `cli._PLAN_ARGUMENT` (unit 04) and its paired `tests/test_advice.py::
   READ_BEFORE` stay in agreement. If the table moves, both move together.
8. No import cycle. `tests/test_no_import_cycles.py` passes untouched, over 33
   modules rather than 32.
9. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
   exit 0. **Do not lower `SUITE_FLOOR` or `REQUIRED_FLOOR`** — they are pinned
   equal at 1190 and a pure move must not change the test count at all. If the
   count moves in either direction, say so and explain why.
10. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output ·
`cli.py`'s line count before and after, and `commands.py`'s · confirmation that
the 824-line parser fixture and the seven JSON goldens passed **untouched** ·
the test count before and after, which must match · anything you could not move
and why.
