---
ctx_schema: 1
spec: close-the-recorded-backlog
status: draft
created: 2026-09-12
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent

Everything left on the backlog that does not need a GitHub setting. Five waves
of remediation closed items 1–26 of `report.md` and five of six ctx
improvements; what remains is a list each earlier wave recorded with a reason
for deferring rather than forgot. The reasons have now expired: the code has
stopped moving, the suite is at 1494, and there is no wave in flight for any of
this to collide with.

Nothing here is broken in the sense waves 1–4 dealt with. Three items are
shipped features that cannot be used as documented, three are P2 defects the
prior audits recorded and nobody has contradicted, one is a P1 operations gap,
and the last is the performance work I deferred twice on the grounds that gate
surgery costs three times what it looks like — which is still true, and is why
it carries the tightest constraints in this spec.

## Acceptance criteria

**A shipped feature nobody can use.**

1. `test_first` gains a CLI surface. Its evidence is writable today only through
   `snapshot.record_test_run` from Python, so a verify kind the docs describe
   cannot be fed from the command line. A subcommand or flag records a test run,
   `docs/reference.md` and `docs/walkthroughs.md` stop describing it as a gap,
   and a test drives the kind end to end through the CLI alone.

**Operations (report §3.4, P1).**

2. `ctx` gains a logging module. There are zero uses of `logging` across `ctx/`
   today, and `telemetry.record` and `journal.append` swallow everything by
   design, so a read-only mount, a full disk and a permissions error all present
   as "everything is fine". Verbosity is raisable for a support case.
3. The existing never-raise guarantee is **unchanged**. Journalling and
   telemetry still must not break a session; they log the failure instead of
   discarding it. A test asserts a read-only runtime directory still leaves both
   returning normally, and that the failure is now visible somewhere.
4. Logging is off or minimal by default. A tool whose value is a small
   always-on footprint must not start writing logs nobody asked for; the
   default is controlled by an environment variable and/or a config key, and
   the default state is documented.

**Recorded P2 defects.**

5. Reserved Windows device names (`con`, `prn`, `aux`, `nul`, `com1`–`com9`,
   `lpt1`–`lpt9`, and any of those with an extension) cannot reach a ledger
   path. `bundle.slugify` is the named site; check whether `spec.normalise_slug`
   has the same hole and fix both if so. A test asserts `ctx task «con»` and a
   bundle named `con` both produce a usable path on any platform.
6. `briefing._fit` no longer returns an empty briefing with no marker while
   `measure()` reports `truncated: False`. When the cap is too small to fit
   anything, the caller can tell that content was dropped.
7. `journal.keep_days: 0` no longer lets the committed journal grow unbounded.
   Decide and document what 0 means — the current default is 0 and `ctx prune`
   already advises setting it — then make the behaviour match the documentation
   either way. Changing the shipped default is in scope if that is the right
   answer; say so and say why.

**Concurrency, still unreproduced.**

8. The stale-lock reclaim double-holder path (`lock.py`, formerly
   `state.py:91-107`) is either reproduced and fixed, or **reported as
   unreproducible with the attempt described**. Wave 4 and the wave-4 spec both
   recorded that it needs a reproduction before it needs a fix; a test that
   asserts a race nobody has demonstrated is worth less than an honest note.
   Do not manufacture a passing test for it.

**Lint debt the ruff unit deliberately parked.**

9. The rules wave 5 left in `ignore` with their sites named are enabled and
   their violations fixed: `F401` (`commands.py`, `hooks.py`, six test
   modules), `F841`, `F541`, `B007`, `B904`, `E741`. Each fix is a real
   correction, not a `# noqa`. Anything that turns out to be load-bearing stays
   ignored **with its reason recorded** rather than silenced.
10. `E501` and the formatting families stay out, for the reason already
    recorded: a rule a regeneration can break is a rule that gets switched off
    in a hurry rather than obeyed.

**The last duplication.**

11. `Layout.unit_files()` exists and the three modules still globbing
    `*/units/*.md` directly go through it. `tests/test_shared_paths.py`'s
    `UNITS_GLOB_SWEEPS` set shrinks to empty or documents what genuinely cannot
    move.

**The performance work I deferred twice.**

12. A wave's units gate against **one** suite run rather than one per unit.
    Measured on this repository, that is ~18 minutes per plan of wave 5's size.
13. **Every existing gate guarantee survives, and this is the criterion that
    governs the rest.** Each unit still gets its own `owns`-scoped diff check,
    its own contract-seal comparison, its own findings re-seal, and its own
    refusal on an all-ERROR result. Batching changes *when the shared checks
    run*, never *what any unit is held to*.
14. If closing 12 would weaken 13 in any way the implementer cannot fully
    mitigate, **stop and report rather than trading it away.** Two waves have
    now recorded that gate work costs three times what it looks like, and the
    gate is the product's central claim. A slower correct gate beats a faster
    one.

**Whole-wave.**

15. The full suite, `ctx doctor`, `ctx ci` and `ruff` all pass. `SUITE_FLOOR`
    and `REQUIRED_FLOOR` stay equal; the coverage floors stay equal to their
    test-side copies. Raising any floor is a deliberate act to be reported, not
    a silent adjustment.

## Out of scope

- The two GitHub settings: private vulnerability reporting, and branch
  protection requiring Code Owner review. Neither is reachable from here.
- **Granting execution from `trust.lock`.** Unchanged from waves 4 and 5: only
  defensible once Code Owner review is enforced, which is one of the two
  settings above. Not a decision to take while that is off.
- Re-auditing. `report.md` stays the authoritative audit and is not rewritten.

## Notes

Contract weight follows the wave-6 rule rather than wave-5's: criteria 5, 6,
9 and 11 are corrections whose tests can simply assert the fixed behaviour, and
no reproduce-it-failing control is required for them. Criteria 1, 7, 12 and 13
change behaviour users depend on and keep full measured controls. Criterion 8
is the explicit exception where **failing to reproduce is an acceptable
result** and must be reported as such.

Two decisions are recorded rather than asked, each conventional and each one
edit to reverse: logging defaults to off or minimal (criterion 4), and `E501`
stays out (criterion 10).
