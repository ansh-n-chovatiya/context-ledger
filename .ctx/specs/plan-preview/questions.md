---
ctx_schema: 1
spec: plan-preview
---

## Blocking



<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] Who authors plain.md in practice — should plan-check scaffold and top it up, or stay opt-in via ctx preview --scaffold-plain?
- [x] Should plan-check write preview.html on every run, only on ctx preview, or refresh it only when it already exists?
- [x] What should plain.md staleness and page determinism be measured against, given that plan.json's revision counter bumps on every plan-check run?

## Non-blocking


<!-- Worth knowing, but you can proceed without it. -->
- [x] Should an unfilled scaffold field count as authored prose or as missing?
- [x] Should preview.html be gitignored rather than committed, as DIGEST.md was?

## Resolved





- Who authors plain.md in practice — should plan-check scaffold and top it up, or stay opt-in via ctx preview --scaffold-plain? → plan-check tops it up: it scaffolds plain.md when absent and appends a blank block for any unit lacking one, never touching authored text. ctx plan creates zero units in practice (commands.py:1564), so plan-check is the first moment the unit list exists, and it reruns after every re-cut so a later unit cannot go silently missing. ctx preview --scaffold-plain stays for explicit regeneration. (2026-09-12)
- Should plan-check write preview.html on every run, only on ctx preview, or refresh it only when it already exists? → REVERSED after the intent was restated. plan-check writes preview.html on EVERY run. First answer was refresh-only-if-exists; that makes the page default-off, and the feature exists for people who do not know the command. Cost is smaller than it looked: nothing automated runs plan-check (no hook, no ci, no doctor), and it already rewrites plan.json, a revisions archive, README and a journal entry every run. The content-digest decision keeps the page from diffing unless the plan actually changed. `ctx preview` remains the explicit way to render one on demand. (2026-09-12)
- What should plain.md staleness and page determinism be measured against, given that plan.json's revision counter bumps on every plan-check run? → A content digest over the units' substantive fields, reusing ctx/contract.py field_digests — not plan.json's revision counter. The counter increments on every plan-check run (plan.py:867), so revision-based staleness would flag plain.md after a no-op re-check and preview.html would diff on every run, contradicting the byte-determinism the feature promises. revision still appears under the technical toggle. (2026-09-12)
- Should an unfilled scaffold field count as authored prose or as missing? → As missing. A present-but-empty field lands in missing and falls back to generated text, so an unfilled form is never mistaken for authored prose — this is what keeps the scaffold-by-default decision safe. (2026-09-12)
- Should preview.html be gitignored rather than committed, as DIGEST.md was? → Committed. PREVIEW-PLAN.md section 3 argues the DIGEST.md precedent: DIGEST.md is regenerated at session end by every agent, so concurrent agents conflict on a file neither authored; preview.html regenerates only when plan-check runs, is byte-deterministic, and the whole point is handing it to somebody who cannot run ctx to regenerate it. (2026-09-12)
