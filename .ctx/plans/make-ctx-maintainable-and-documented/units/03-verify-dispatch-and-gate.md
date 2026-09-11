---
ctx_schema: 1
unit: 03-verify-dispatch-and-gate
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 01-shared-paths-and-dead-code
  - 02-caps-and-reported-spend
owns:
  - ctx/verify.py
  - ctx/cli.py
  - tests/test_verify_kinds_table.py
  - tests/test_contract_digest_agreement.py
reads:
  - path: ctx/paths.py
    symbols:
      - LEDGER_PREFIX
  - path: ctx/contract.py
    symbols:
      - seal_findings
forbid:
  - ctx/paths.py
  - ctx/detect.py
  - ctx/advice.py
  - README.md
budget_tokens: 75000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Make a new verify kind one dict entry instead of four coordinated edits, and
move the two gate-orchestration functions out of `cli.py` into the module whose
call shapes they rebuild.

## Interfaces

Produces, for unit 05's registry work:

```python
# ctx/verify.py
def verify_plan(layout, config, slug, ...):    # was cli._verify_plan
def gate_before_done(layout, config, slug, unit):  # was cli._gate_before_done
```

Keep the parameter order and return shapes exactly as the `cli.py` versions
have them. These are moves, not redesigns; a signature change here is a change
unit 05 has to absorb.

## Acceptance criteria

**The dispatch table (item 24).**
1. A new verify kind is **one dict entry**. Today it costs four coordinated
   edits — `KINDS`, `COST`, and two if-ladders. A test registers a synthetic
   kind through the table alone and asserts it is recognised, costed and
   dispatched, with no edit anywhere else.
2. Positive control: show that adding the same synthetic kind against the
   current code requires more than the table — quote the failure (an unknown
   kind, or a zero cost) from before the change.
3. All eight existing kinds keep their behaviour **and their cost weights**.
   Capture `COST` before your change and assert the values after are identical,
   one by one. The weights encode the cheapest-first ordering the gate depends
   on; a silent reorder changes which check runs first.

**The extraction (item 23, second of five).**
4. `_verify_plan` and `_gate_before_done` live in `verify.py` as `verify_plan`
   and `gate_before_done`; `cli.py` calls them. The audit ranked this second by
   risk reduced because both rebuild near-identical `verify.run` call shapes.
5. **Behaviour-preserving.** There is no failing case to reproduce, so the proof
   is that every pre-existing test covering the gate and `ctx verify` passes
   **unedited**. If a test needs editing, stop and report — that means the move
   changed behaviour, and these two functions are the done-gate.
6. The wave-4 guarantees they carry are intact and still pinned: the gate runs
   **outside** the per-plan lock, the all-ERROR refusal still refuses, and
   `gate_before_done`'s success path still re-seals findings. The existing tests
   assert all three; they must pass untouched.
7. `verify.py` adopts `paths.LEDGER_PREFIX` from unit 01 and its local copy at
   `verify.py:216` is gone — the last of the three.

**A third dead function, handed over from unit 01.**
8. `contract.digest_text` is unreferenced across `ctx/`, `tests/`, `commands/`,
   `hooks/` and `bin/`. **Do not delete it** — unlike `snapshot.discard_all` it
   is a two-line pure function that destroys nothing, so the "dead code that
   deletes data" rule does not reach it. Make it live instead, because the
   invariant it exists for is one the seal actually depends on: add a test
   asserting `contract.digest(unit)` equals
   `contract.digest_text(unit.path.read_text())` for the same unit, including a
   unit whose frontmatter has been reordered and one carrying a multi-line
   value. If the two ever disagree, a contract sealed from a parsed document
   and the same contract sealed from bytes would hash differently, and the
   tamper check would fire on a file nobody touched. `ctx/contract.py` is not
   in your `owns` and does not need to be — this is a test, not a change.

**Both.**
9. No import cycle is created. `verify.py` must not import `cli.py`.
10. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
    exit 0. Do not lower `SUITE_FLOOR` or `REQUIRED_FLOOR`.
11. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after for criterion 2 · the `COST` table before and after, side by side ·
confirmation that no pre-existing gate test was edited · the two signatures unit
05 consumes.
