---
ctx_schema: 1
spec: ctx-0-8
status: ready
created: 2026-09-09
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent
Make model selection follow the task rather than the role, instrument dispatch and review with telemetry, add gated debugging phases and a test_first verify kind, and close the two acceptance criteria the 0.7.0 brief specified but never proved.

## Acceptance criteria

**A. Model selection follows the task (P0-A)**

1. `model_for` returns a different model for two units differing in exactly one
   complexity signal — `budget_tokens`, `owns` breadth, or the presence of a
   `rubric`/`human` verify check — with weights and thresholds read from
   `ctx.yaml`. One unit test per signal.
2. The dispatch line prints the score and every input that produced it, so a
   wrong tier is diagnosable without re-running anything.
3. A review package under the small threshold with zero scope violations
   dispatches the reviewer on the cheaper tier; one over the threshold, or with
   any scope violation, does not.
4. An explicit `model:` in unit frontmatter wins over every heuristic, and a
   model not named in `models.tiers` is never auto-escalated.
5. With `models.escalate_on_failed_round: true`, a failed fix round raises the
   runner one tier for the next round and the journal names the escalation and
   its reason. With the flag at its default (`false`), all three rounds
   re-dispatch at the same model — asserted both ways.
6. `ctx dispatch` and `ctx review` emit telemetry events **at all** — they are
   silent today — and every entry carries model, role, review-package bytes and
   round number. `ctx telemetry` reports spend per role.

**B. Gated debugging phases (P0-B)**

7. A unit declaring `kind: bug` cannot enter the `fix` phase until its
   `reproduction` command has been recorded exiting **non-zero**. Advancing to
   `fix` without that recording is refused with the reason.
8. The `guard` phase is a `rubric` check judged by the existing `verifier`; a
   bug unit is not done while it is unrecorded.
9. `phases:` works as a general mechanism on a non-bug unit, and `kind: bug`
   expands to the four gated phases without the author restating them.

**C. Test-first (P1)**

10. A `test_first` verify check fails when the implementation snapshot precedes
    any failing run of the unit's test paths, and passes when a captured
    failing run precedes the implementation snapshot.

**D. Claims the 0.7.0 brief made and never proved**

11. Two units in one wave, run concurrently, produce two **disjoint** review
    packages — asserted directly. The case for snapshots over commit ranges
    rests on this and it is currently unproven.
12. A session killed mid-fix-loop resumes via `/ctx:resume` with the round
    number, the open findings and the unit under review intact.
13. An escalated dispatch cannot widen what `trust.py` has accepted: escalating
    a unit leaves the trust store and the resolved verify-command set unchanged.

**E. Invariants that must survive**

14. `grep -rn "git" ctx/*.py` surfaces no new call sites, and
    `tests/test_git_invariant.py` passes unchanged.
15. The full suite passes — 415 tests today, plus the new ones.
16. `python -c "import ctx"` still needs no third-party package.
17. No model name is hardcoded outside `config.DEFAULTS`.

## Out of scope

- **G7 harness portability.** Declined, with an ADR recording the refusal.
  Eight harness variants multiply the maintenance surface and no P0/P1 value
  depends on it.
- **Brainstorming's visual companion (G5).** Superpowers ships a 723-line Node
  server; porting it breaks stdlib-only. A visual mode, if ever wanted, is a
  separate opt-in package. The divergent *phase* of `/ctx:spec` is also not in
  0.8 — it is P2 and unranked against the P0 work.
- **`/ctx:author` (G6).** P2, and nothing in this spec depends on it.
- **Tuning the shipped weights against real data.** A4 exists so the numbers
  become measurable; actually re-tuning them is 0.9 work, after telemetry has
  something to say. Shipping reasoned defaults is in scope, claiming they are
  optimal is not.
- **Any git write.** Unchanged and absolute: no commits, no branches, no
  worktree dependency, no third-party packages.
- **Verbatim text from superpowers.** Port the design; write our own prose.
  Its LICENSE is read before any text is lifted.

## Notes

**Correction to the brief (verified against the tree).** The brief claims
"review and dispatch produce no telemetry events at all" and sizes A4 as
"instrumenting two subsystems that are currently silent". That is half wrong.
`grep -rn "telemetry\.record" ctx/ hooks/` returns two call sites:
`hooks.py:83` and **`cli.py:1340`**, and the second already records a `review`
event carrying `bytes`, `round` and `out_of_scope`. The brief's grep looked in
`review.py`/`findings.py`/`dispatch.py`, but ctx puts command implementations in
`cli.py` — the domain modules are libraries the commands call. So the previous
brief's "telemetry records review-package bytes and round counts" criterion
*is* implemented; it was mis-assessed, not missing.

What is actually absent:
- **dispatch telemetry entirely** — `cmd_start` journals but records nothing;
- **`model` and `role` on every event** — absent from `telemetry.py` outright,
  which is what makes "is opus-for-reviewer worth it" unanswerable;
- **per-role aggregation** in `summarise`, which buckets by event only.

A4 therefore stays first but is smaller than briefed: add two fields, add one
call site, teach `summarise` to group by role. A1–A3 remain tuning decisions
that can only be argued rather than evaluated until it lands.

The governing rule, carried from 0.7.0: **do not port the document, port the
mechanism.** If a discipline says "always do X", ship a check that fails when X
did not happen. A grep catches the cases someone thought of; a test over real
state catches the one nobody did.
