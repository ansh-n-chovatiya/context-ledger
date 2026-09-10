---
ctx_schema: 1
spec: close-the-wave-1-audit-blockers
---

## Blocking



<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] For the exists and symbol kinds: confine paths to the project root, or additionally require trust acceptance the way cmd does? Confinement alone removes the content-oracle primitive without adding a trust prompt to a check that executes nothing; trust-gating is stricter but means every file check in every ledger needs ctx trust before it can run.
- [x] Should ctx start set units to status running when it dispatches them? That is what makes a re-run report in-flight units instead of re-snapshotting them, but status running is currently never set by any code path, so the board, the next-action advisor and the gate have never seen it.
- [x] How should contract tampering be caught: compare the unit frontmatter against the before snapshot inside the gate, or narrow is_ledger so unit and findings files become visible to the ordinary diff check? The diff route also flags a sibling unit's legitimate ledger writes in a concurrent wave.

## Non-blocking


<!-- Worth knowing, but you can proceed without it. -->
- [x] What should the per-check opt-out in criterion 10 be called - optional: true, or skip_if_missing: true?
- [x] Should the all-ERROR refusal also apply to a bare ctx verify run, or only to the --status done transition and the merge preflight?

## Resolved





- For the exists and symbol kinds: confine paths to the project root, or additionally require trust acceptance the way cmd does? Confinement alone removes the content-oracle primitive without adding a trust prompt to a check that executes nothing; trust-gating is stricter but means every file check in every ledger needs ctx trust before it can run. → Confine paths to the project root. Reject absolute paths and any path escaping via .. with ERROR naming the refusal. No trust acceptance is required for these kinds - they execute nothing, and confinement already removes the content-oracle primitive. The cmd trust gate is unchanged. (2026-09-10)
- Should ctx start set units to status running when it dispatches them? That is what makes a re-run report in-flight units instead of re-snapshotting them, but status running is currently never set by any code path, so the board, the next-action advisor and the gate have never seen it. → Yes. ctx start sets status running on dispatch, a re-run reports in-flight units and skips their snapshots, and an explicit re-baseline flag deliberately re-captures after a real crash. The board, the next-action advisor and the gate must each be checked against the new status. (2026-09-10)
- How should contract tampering be caught: compare the unit frontmatter against the before snapshot inside the gate, or narrow is_ledger so unit and findings files become visible to the ordinary diff check? The diff route also flags a sibling unit's legitimate ledger writes in a concurrent wave. → Compare the unit contract against the before snapshot inside the gate. Hash the verify, owns and acceptance-criteria fields plus the findings ledger, refuse on mismatch, and name the changed field. is_ledger is left as it is, so ordinary ledger churn and sibling writes in a concurrent wave stay non-violations. (2026-09-10)
- What should the per-check opt-out in criterion 10 be called - optional: true, or skip_if_missing: true? → optional: true, matching the report wording. (2026-09-10)
- Should the all-ERROR refusal also apply to a bare ctx verify run, or only to the --status done transition and the merge preflight? → Only the --status done transition and the merge preflight. A bare ctx verify is a report, not a transition, so it keeps showing ERROR without refusing. (2026-09-10)
