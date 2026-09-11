---
ctx_schema: 1
spec: make-ctx-durable-under-concurrency
---

## Blocking


<!-- Anything whose answer changes what gets built. Unchecked boxes
     here block planning: `- [ ] Q1: …` -->
- [x] Per-author journals and DIGEST.md: do existing day files migrate to per-author files, or does only new writing split while the old files stay readable where they are? And DIGEST.md is tracked today — does making it derived-and-gitignored mean git rm'ing a file that is committed in every existing tree?
- [x] ADR ids: ULIDs, which make a collision impossible but make .ctx/decisions/ unsortable and unreadable by eye, or keep the 0001- sequence and add a ctx doctor check that catches duplicates after the merge that created them?

## Non-blocking
<!-- Worth knowing, but you can proceed without it. -->

## Resolved


- Per-author journals and DIGEST.md: do existing day files migrate to per-author files, or does only new writing split while the old files stay readable where they are? And DIGEST.md is tracked today — does making it derived-and-gitignored mean git rm'ing a file that is committed in every existing tree? → Only new writing splits. Existing journal/YYYY-MM-DD.md day files are left untouched and read as one unattributed stream alongside the new per-author files; readers (tail, write_digest, prune) merge both shapes in time order. No committed journal file is rewritten, because the old day files record no author at all — migrating them would stamp every historical entry with whoever ran the migration. prune folds day files into monthly archives, so the legacy shape retires on its own. DIGEST.md: untrack it here (git rm --cached plus a .ctx/.gitignore line, regenerated on the next hook fire), and for other projects ctx doctor reports a tracked DIGEST.md as a conflict source and prints the commands — the tool never deletes a tracked file in someone else's repo on its own. (2026-09-11)
- ADR ids: ULIDs, which make a collision impossible but make .ctx/decisions/ unsortable and unreadable by eye, or keep the 0001- sequence and add a ctx doctor check that catches duplicates after the merge that created them? → Keep the zero-padded 0001- sequence and add a duplicate-id check to ctx doctor and ctx ci that fails and names both colliding files. Detection after the merge rather than prevention before it, accepted deliberately: the merge is when anyone looks, CI is where it surfaces, and the remedy is a rename. ULIDs would make .ctx/decisions/ unsortable and unspeakable — 'ADR 0003' is a thing people say — and would leave two id shapes in the directory forever. (2026-09-11)
