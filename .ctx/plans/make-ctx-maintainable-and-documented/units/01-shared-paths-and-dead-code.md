---
ctx_schema: 1
unit: 01-shared-paths-and-dead-code
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on: []
owns:
  - ctx/paths.py
  - ctx/snapshot.py
  - ctx/worktree.py
  - ctx/briefing.py
  - ctx/work.py
  - ctx/hooks.py
  - tests/test_shared_paths.py
reads:
  - path: ctx/plan.py
    symbols:
      - units_dir
      - unit_path
forbid:
  - ctx/cli.py
  - ctx/verify.py
  - ctx/config.py
  - ctx/plan.py
budget_tokens: 55000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Put the three copies of `LEDGER_PREFIX` and the five hand-built unit-file paths
behind the single definitions that already exist or should, and delete a dead
function that would `rmtree` the entire snapshot root.

## Interfaces

Produces — unit 03 adopts both in `verify.py`:

```python
# ctx/paths.py
LEDGER_PREFIX = CTX_DIRNAME + "/"   # ".ctx/" — derived, never retyped

class Layout:
    def unit_file(self, plan_slug, unit_name):
        """.ctx/plans/<plan_slug>/units/<unit_name>.md"""
```

`ctx/verify.py` also carries a `LEDGER_PREFIX` copy and is **not** yours — unit
03 owns it and will adopt `paths.LEDGER_PREFIX`. Convert the copies in
`snapshot.py:150` and `worktree.py:130` only, and say in your report that
verify's is left for 03. `plan.py` already has `units_dir`/`unit_path` and is
not yours either: read it, and make `Layout.unit_file` agree with it rather
than inventing a second shape.

## Acceptance criteria
1. `paths.LEDGER_PREFIX` is defined once and derived from `CTX_DIRNAME`, not
   retyped as a literal. `snapshot.py` and `worktree.py` use it; their local
   copies are gone.
2. `Layout.unit_file(plan_slug, unit_name)` exists and returns the same path
   `plan.units_dir(...)` would build. A test asserts the two agree, so the
   accessor cannot drift from the module that owns plan layout.
3. `briefing.py:88`, `hooks.py:455`, `work.py:107` and `work.py:119` go through
   the accessor. A test asserts no module outside `plan.py` and `paths.py`
   builds the string `"units"` into a path — enumerate the modules explicitly,
   the way wave 4's writer enumeration does, rather than globbing.
4. `snapshot.discard_all` is deleted. Before deleting, confirm it is genuinely
   unreferenced — grep `ctx/`, `tests/`, `commands/`, `hooks/` and `bin/` — and
   quote the search in your report. It would `rmtree` the whole snapshot root,
   so the one thing worse than leaving it is deleting something that is called.
5. The second dead function the audit names is deleted on the same evidence, or
   your report says why it is not dead. Do not delete a function on suspicion.
6. **This is a behaviour-preserving change.** There is no failing case to
   reproduce, so the proof is that the pre-existing tests covering these
   modules pass **unedited**. If any test needs editing, stop and report it —
   that means behaviour moved, which is not what this unit is for.
7. `python3 -m unittest discover -s tests -q` passes. The suite floor is pinned
   at 1190 against 1199 and `SUITE_FLOOR`/`REQUIRED_FLOOR` must stay equal —
   **do not lower either.** If your change removes tests, stop and report.
8. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
grep evidence that each deleted function was unreferenced · confirmation that no
pre-existing test was edited · the two symbols unit 03 consumes.
