---
ctx_schema: 1
unit: 04-command-surface-and-wave-gating
plan: close-the-recorded-backlog
tier: subagent
depends_on: []
owns:
  - ctx/commands.py
  - ctx/cli.py
  - ctx/verify.py
  - ctx/snapshot.py
  - tests/test_test_first_cli.py
  - tests/test_wave_gating.py
reads:
  - path: ctx/plan.py
    symbols:
      - load_units
      - find_unit
  - path: ctx/contract.py
    symbols:
      - seal_findings
forbid:
  - ctx/journal.py
  - ctx/config.py
  - ctx/lock.py
  - ctx/plan.py
budget_tokens: 90000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Give `test_first` a CLI surface, and let a wave's units gate against one suite
run instead of one per unit — without weakening a single thing the gate holds
a unit to.

## Acceptance criteria

**`test_first` (a shipped kind nobody can feed).**

1. A subcommand or flag records a test run, so the `test_first` verify kind can
   be satisfied from the command line. Today its evidence is writable only
   through `snapshot.record_test_run` from Python, which the wave-5 docs unit
   found and documented as a gap.
2. A test drives the kind end to end **through the CLI alone** — record a run,
   then have `ctx verify` see it. That is the criterion; a unit test of
   `record_test_run` is not.
3. `docs/reference.md` and `docs/walkthroughs.md` describe it as a gap. Those
   files are **not** in your `owns` — report the exact replacement text.

**Wave gating — read the constraint before the criterion.**

Each `ctx unit --status done` runs the whole suite: ~18 minutes per plan of
wave 5's size, mostly re-running identical code. The saving is real. So is the
risk: I deferred this twice because each unit's gate also does work that is
**not** shared, and two waves have now recorded that gate work costs three
times what it looks like.

4. A wave's units can be gated against **one** suite run. Shape it however the
   code wants — a multi-name `ctx unit`, a `ctx wave --done`, a cache keyed on
   tree state — but the shared `cmd` checks must run once, not N times.
5. **Every per-unit guarantee survives, and this criterion outranks 4.** Each
   unit still gets: its own `owns`-scoped diff check, its own contract-seal
   comparison against dispatch, its own findings re-seal on success, its own
   all-ERROR refusal, and its own journal line. Batching changes *when the
   shared checks run*, never *what any unit is held to*.
6. Prove 5 rather than asserting it: for each guarantee, a test that the
   batched path still refuses the case the per-unit path refused. A unit that
   edited outside its `owns` must still be refused when gated in a batch of
   three.
7. The single-unit path keeps working unchanged. Every existing gate test
   passes **unedited** — `test_gates`, `test_gate_bypass`,
   `test_audit_ungated_gate`, `test_gate_window`, `test_gate_untracked_warning`.
   If one needs editing, that is criterion 5 being violated.
8. Measure it: the wall-clock and suite-run count for gating three units
   individually versus batched, on this repository. Quote both.
9. **If closing 4 would weaken 5 in any way you cannot fully mitigate, stop and
   report instead of trading it away.** A slower correct gate beats a faster
   one, and this is the product's central claim. Reporting "not safely
   possible, here is why" is a successful outcome for this half of the unit.

**Both.**
10. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
    exit 0. Do not move any floor.
11. No file outside `owns` is modified.

## Return contract
Files changed · criteria passed · verbatim verify output · the end-to-end CLI
run satisfying `test_first` · the before/after timing for criterion 8 · **for
each guarantee in 5, the test that proves it survived** · the doc text unit 05
must place · anything you stopped on.
