---
ctx_schema: 1
spec: make-ctx-maintainable-and-documented
status: ready
created: 2026-09-11
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Wave 5 of the v0.8.0 remediation roadmap: structure and documentation
(report.md §3.5 and §3.6, roadmap items 23–26), plus the three follow-ups
earlier waves deferred here on purpose — `--json` (item 9), coverage
thresholds and `ruff`.

The four waves before this one fixed things that were *wrong*: a security
hole, a gate that did not gate, an unreproducible artifact, and six ways to
lose work already done. Nothing in wave 5 is wrong in that sense. The tool
does what it says; the problem is that it has become hard to change safely
and hard to learn. `cli.py` is 3,399 lines — a quarter of the codebase, up
from the 2,380 the audit measured, because four waves of fixes all landed in
it. A new verify kind costs four coordinated edits with no dispatch table. A
new command costs two or three with no registry. And the README is 1,575
lines documenting a feature set that is now two releases stale: `kind: bug`,
`models.tiers`, `escalate_on_failed_round`, `complexity:` and `/ctx:phase`
return **zero** hits between them.

That difference in kind changes how the wave must be judged. A refactor has
no failing case to reproduce, so the positive-control discipline that carried
waves 1–4 does not transfer: there is nothing to show failing first. The
guarantee has to come from somewhere else, and it is stated once here so each
unit inherits it — **an extraction must not change behaviour, and the proof
is that the tests that covered the code before it moved still pass, unedited,
against the code after.** A unit that finds itself rewriting assertions to
accommodate its own refactor has changed behaviour and must stop and say so.

## Acceptance criteria

**Extractions (item 23).** In the order the audit ranked them by risk reduced.

1. `ctx/detect.py` holds the ecosystem taxonomy and subprocess probing now at
   `cli.py:151-341`. `cmd_ci` stops reaching into a private `_availability`
   — the reach-through is the signal that this belongs elsewhere.
2. `_verify_plan` and `_gate_before_done` move into `verify.py`, which is
   where the near-identical `verify.run` call shapes they both rebuild belong.
3. `_next_action` moves to `ctx/advice.py` and is callable without going
   through argparse. It encodes the entire product decision tree and is
   currently reachable only by constructing a parser.
4. `build_parser` becomes a `COMMANDS` registry. This is the criterion that
   pays for the next two: it is what makes `--json` and any future flag a
   one-place change instead of 41.
5. The five-way duplicated plan-resolution preamble becomes `_plan_or_report()`.
   **The copies differ in which argument they read** (`args.plan` vs
   `args.name`); the audit flags this as a trap for mechanical deduplication.
   A unit that unifies them must prove each call site still reads the argument
   it read before.
6. Every extraction is behaviour-preserving under the rule stated in the
   intent: the pre-existing tests covering each moved function pass unedited.
   Any test that must change is reported, not quietly edited.

**Dispatch tables (item 24).**

7. A new verify kind is one dict entry. Today it is four coordinated edits —
   `KINDS`, `COST`, and two if-ladders in `verify.py`. A test adds a synthetic
   kind through the table alone and asserts it runs, costs and dispatches.
8. All eight existing kinds keep their current behaviour and cost weights,
   asserted against the values before the change.

**`--json` (item 9, carried from wave 2).**

9. `--json` on `status`, `next`, `doctor`, `ci`, `verify`, `plan-check` and
   `findings`, routed through one `_emit(data, human_fn)` helper rather than
   per-command serialisation.
10. Human output is byte-identical without the flag. This is the criterion
    that keeps `--json` from becoming a rewrite of every command's prose.
11. **Decided:** the JSON output is a stable, documented contract. Each of the
    seven commands' shapes is pinned by a schema test, and changing one is a
    breaking change with a CHANGELOG entry. Output nobody can depend on would
    answer the audit's complaint in form only.

**Documentation (item 25).**

12. The 0.8.0 feature set is documented: `phase`/`/ctx:phase`, `kind: bug`
    with its four gated phases, `test_first`, the `models:` block including
    `tiers` and `escalate_on_failed_round`, and the `complexity:` block with
    its weights and thresholds. Each documented from the code, not from a
    sibling's summary — wave 3 recorded that a copied-forward summary is how
    a third false claim would arrive.
13. Every remaining false or stale claim is corrected. Already fixed in wave
    3 and not to be re-fixed: the "seven kinds" claim now reads eight. Still
    open: `README.md:1153` says the suite runs "on macOS, Linux and Windows
    across Python 3.8–3.13" when only Ubuntu runs the full spread.
14. A test asserts the documentation cannot silently go stale again for the
    things that are checkable: every registered subcommand appears in a CLI
    table, every file in `commands/` appears in the slash-command table, and
    the claimed verify-kind count equals `len(verify.KINDS)`.
15. **Decided:** the full three-way split. README keeps Why, Requirements,
    Install, Quickstart and the command tables under ~300 lines;
    `docs/reference.md` takes the configuration and verify-kind reference;
    `docs/walkthroughs.md` takes the worked examples. The size is the cause and
    not a side effect — a 55KB file reads as accumulated release notes, which
    is how an entire release's feature set never reached it.

**Caps and telemetry (item 26).**

16. `plan.max_wave_units` is a hard cap with a shipped default, refusing a
    wave above it the way `wave_budget_tokens` already refuses one over
    budget, and named in the same refusal vocabulary.
17. **Decided:** spend arrives by report, not observation. `ctx telemetry
    --spend <tokens> --unit <name>` lets the orchestrator record what it spent,
    because a CLI cannot see a model's token usage. The data is partial by
    construction, and **every surface that displays it must label it as
    reported and incomplete** — that labelling is a criterion, not a nicety.
    An unlabelled partial number will be used to tune the complexity weights as
    though it were a full measurement, which is worse than having none.

**Carried: coverage and ruff.**

18. CI runs `ruff` over `ctx/` and `tests/`, with the configuration in
    `pyproject.toml`. The initial rule set may be narrow; it may not be empty,
    and the set chosen is recorded with its reason.
19. CI enforces a coverage floor. The floor is set from a measured run, not
    chosen aspirationally, and — like `SUITE_FLOOR` — is asserted in exactly
    one place or pinned by a test that the two copies agree.

**Cleanups the audit itemised (§3.5 P2, §3.8).**

20. `snapshot.discard_all` is deleted. It is dead code that would `rmtree` the
    entire snapshot root; dead code that deletes data is the one kind worth
    removing on sight. The second dead function goes with it.
21. `_PROFILE_MARKERS` gains `research`, or `research` is removed from the
    five profiles `--profile` accepts. Today it is accepted and can never be
    auto-detected.
22. `LEDGER_PREFIX` is defined once, referencing `paths.CTX_DIRNAME`, which
    already holds it. The unit-file path stops being hand-built in five
    modules and goes through a `Layout` accessor.
23. `/ctx:trust` exists. Twenty subcommands have no slash command and that is
    mostly correct — each one costs always-on context — but `trust` is the
    security gate that `_next_action` literally advises the user to run, and
    advising a command with no slash command is a dead end.
24. **Decided:** `AUDIT.md` and `PRODUCTION-AUDIT.md` move to `docs/history/`,
    each gaining a one-line header naming the version it describes and saying
    it is historical. They record real decisions, so they are not deleted — but
    `PRODUCTION-AUDIT.md`'s "snapshot.py and findings.py: zero tests, zero
    callers" verdict reads as current while both now have dedicated suites.
    `report.md` remains the authoritative audit. Any link to either path is
    updated, asserted by a test that no tracked file references the old
    locations.

**Whole-wave.**

25. The full suite, `ctx doctor` and `ctx ci` all pass. **The suite floor
    constraint applies**: `SUITE_FLOOR` and `REQUIRED_FLOOR` are pinned equal
    at 1190 against 1199, so a unit that removes ten or more tests turns CI
    red until both move together, deliberately. Extractions are the kind of
    work that does this. No unit lowers a floor on its own; if the count
    genuinely falls, that is a wave-level decision.
26. `cli.py` is materially smaller — under 2,000 lines — and no extraction
    creates an import cycle. The audit verified zero cycles by AST walk over
    all 27 modules; a test preserves that.

## Out of scope

- A logging subsystem (report §3.4 P1). Still real, still not a structure or
  documentation concern, and it would touch every module in the tree.
- The stale-lock reclaim double-holder path (`state.py:91-107`). Code-path
  only, never reproduced under contention. It needs a reproduction before it
  needs a fix, and wave 4 already recorded that.
- Granting execution from `trust.lock`. Unchanged from wave 4: only
  defensible once branch protection requires Code Owner review, which is
  still not enabled. Adding `/ctx:trust` here does not touch it.
- The remaining §3.8 P2s: reserved Windows device names reaching
  `.ctx/tasks/con.md`, `_fit`'s empty briefing with no marker, and
  `keep_days: 0` letting the committed journal grow unbounded. Each is real
  and each is its own small unit; none is structure or documentation.
- Slash commands for the other nineteen subcommands. Deliberate: every slash
  command is always-on context, and the README's reasoning for withholding
  them is sound.

## Notes

The positive-control requirement does not disappear, it changes shape. For
the behavioural criteria here — the dispatch table, `--json`, the cap, the
doc-currency tests — a control is still required and still means the same
thing: show the assertion failing before it passes. For the extractions there
is no failing case, and the substitute is stated in the intent. Do not
manufacture a fake before/after for a pure move; say plainly that the test
that covered it before still covers it after.

Wave 4 closed with 1199 tests and a tightly pinned floor. That pin was
deliberate and this wave is the first to feel it.
