---
ctx_schema: 1
spec: make-ctx-tell-you-the-plan-is-slow
status: draft
created: 2026-09-11
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Wave 5 of the remediation took ~95 minutes of critical path to run 8 units,
because five of them owned `cli.py` and `ctx` never said so. The `owns` model
is what makes concurrent dispatch safe, and it is also what serialises a plan
when one file dominates — but `plan-check` reports waves without ever
reporting that the plan it just computed has no parallelism in it. Everything
needed to say so is already in the graph.

This wave makes `ctx` surface, at plan time, what it already knows: how much
of the plan can actually run at once, which file is serialising it, which
owned source files have tests nobody owns, and roughly how long the critical
path is. It also fixes a scoring bug that put 16 of 17 units on the most
expensive model, and adds one warning at the gate for a fail-green this
project hit for real.

Nothing here changes what `ctx` does. It changes what `ctx` tells you before
you spend two hours finding out.

## Acceptance criteria

1. `ctx plan-check` reports parallelism — units, waves, and the ratio — and
   names the file when one is owned by three or more units, telling the reader
   to extract it first or merge those units. Against the committed
   `make-ctx-maintainable-and-documented` plan it must name `ctx/cli.py`.
2. It reports an estimated critical path, labelled an estimate, derived from
   per-wave `budget_tokens`. A number presented as a measurement would be
   worse than none — wave 5 is the evidence.
3. It reports ownership gaps: for each owned source file, any test file
   referencing it that no unit in the plan owns. Two units blocked mid-wave
   this session for exactly this, and both were detectable at plan time.
4. All three are **advisory**: `plan-check` still exits 0 on a clean-but-slow
   plan, and `--strict`/`CTX_STRICT=1` escalates them to 1. This matches the
   existing advisory contract; a serial plan is sometimes correct.
5. `complexity.score`'s `publishes_iface` term fires only when another unit in
   the same plan both `depends_on` this unit and reads a path it owns. Today it
   fires whenever an `## Interfaces` section exists, so 16 of this session's 17
   units scored into the `deep` tier — including a pure file move. The **weight
   stays 2.0**; only when it applies changes.
6. Criterion 5 is shown as a measured before/after on this repository's own
   committed plans: the tier each unit would have been dispatched at, before
   and after. This one is behavioural and load-bearing, so it gets a real
   control.
7. The `ctx-0-8` spec recorded the weights as a decision. Any existing test
   pinning them must be judged individually, not bulk-updated. Report each one
   changed and why.
8. The done-gate warns when a unit leaves new files untracked, naming the
   count. The gate runs *before* the commit, so a check scoped to `git
   ls-files` cannot see files the unit just wrote — three broken doc links
   passed a gate here and went red on the next commit.
9. The full suite, `ctx doctor` and `ctx ci` pass. `SUITE_FLOOR` and
   `REQUIRED_FLOOR` are pinned equal at 1420; do not move either.

## Out of scope

- **Wave-level gating** (one suite run for a whole wave). The biggest single
  saving on paper — ~18 minutes per plan of this size — and deliberately not
  here. Each unit's gate also runs a diff check scoped to its own `owns` and
  re-seals its findings, so batching means restructuring the gate, which is
  the product's central claim. This session's evidence is that gate work costs
  three times what it looks like. It gets its own wave, with its own care.
- Changing the `owns` model. It caught three real scope errors this session by
  making units block rather than quietly widen. The problem was never that
  ownership serialises work; it is that nothing said so in advance.
- Auto-fixing a slow plan. `ctx` reports; the human re-cuts.

## Notes

**Contracts in this wave are deliberately lighter than wave 5's.** Most of
this is "print something `ctx` can already compute", and demanding a
reproduce-it-failing control for each new line of output is the discipline
that cost ~25–30% per unit without buying anything here. Criteria 5 and 6 are
the exception and keep a full measured control, because they change dispatch
behaviour and spend real money.

Applying the wave's own lesson to itself: this plan is two units in one wave,
cut by owner rather than by concern, and neither declares an `## Interfaces`
block because neither consumes the other's symbols.

Three decisions recorded rather than asked, because each has a conventional
default and the cost of a wrong guess is one small edit: advisory-not-refusing
(criterion 4, matching the existing `--strict` contract); the three-unit
threshold for naming a bottleneck file; and the `publishes_iface` weight
staying at 2.0 while only its trigger changes.
