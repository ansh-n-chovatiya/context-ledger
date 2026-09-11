---
ctx_schema: 1
unit: 05-commands-registry-and-json
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 04-detect-and-advice
owns:
  - ctx/cli.py
  - tests/test_commands_registry.py
  - tests/test_json_output.py
  - tests/test_no_import_cycles.py
reads:
  - path: ctx/verify.py
    symbols:
      - verify_plan
      - gate_before_done
  - path: ctx/advice.py
    symbols:
      - next_action
  - path: ctx/detect.py
    symbols:
      - availability
forbid:
  - ctx/verify.py
  - ctx/advice.py
  - ctx/detect.py
  - README.md
budget_tokens: 85000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 4
---

## Objective
Turn `build_parser` into a registry, then use it to add `--json` in one place
instead of forty-one — the pairing the audit identified as what makes the
registry worth building.

## Acceptance criteria

**The registry (extraction 4, last of five).**
1. `build_parser` becomes a `COMMANDS` registry. A new command is one registry
   entry, not two or three coordinated edits.
2. Every one of the 41 subcommands still parses identically: same flags, same
   defaults, same help text, same exit codes. A test enumerates the parser
   before and after and asserts the surfaces match — generate the "before" list
   from the current code and commit it as a fixture rather than hand-typing it.

**`--json` (roadmap item 9, carried from wave 2 — decided, do not redesign).**
3. `--json` on `status`, `next`, `doctor`, `ci`, `verify`, `plan-check` and
   `findings`, routed through **one** `_emit(data, human_fn)` helper. Per-command
   serialisation is what this unit exists to avoid.
4. **Human output is byte-identical without the flag.** Assert it per command
   against captured current output. This is what keeps `--json` from becoming a
   rewrite of every command's prose, and it is the criterion most likely to be
   quietly broken.
5. **The JSON is a stable, documented contract.** Each of the seven shapes is
   pinned by a schema test; changing one is a breaking change. Output nobody can
   depend on would answer the audit's complaint in form only — a pipeline that
   must regex prose and a pipeline that must track an unversioned shape are the
   same problem.
6. `--json` and `--strict` compose: a refusal still exits non-zero with the
   error on stderr and valid JSON on stdout. A caller parsing stdout must not
   get a half-written document on the error path.
7. Exit codes are unchanged by the flag. `--json` changes rendering, nothing
   else.

**Whole-wave structural criteria.**
8. `cli.py` is **under 2,000 lines**, down from 3,399. A test asserts the
   ceiling so it cannot silently grow back — this is the measurable outcome of
   item 23 and the reason the extractions were sequenced first.
9. `tests/test_no_import_cycles.py` walks the import graph over every module in
   `ctx/` and asserts it is acyclic. The audit verified this by AST walk across
   27 modules; it becomes a test here so the property is kept rather than
   re-audited.
10. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
    exit 0. Do not lower `SUITE_FLOOR` or `REQUIRED_FLOOR`.
11. No file outside `owns` is modified. Document nothing in `README.md` — it is
    unit 06's; report what needs documenting instead.

## Return contract
Report: files changed · which criteria passed · verbatim verify output ·
`cli.py`'s line count before and after · the seven JSON shapes, so unit 06 can
document them · confirmation that human output is byte-identical, per command.
