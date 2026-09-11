---
ctx_schema: 1
unit: 05-journal-durability-and-authors
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on:
  - 01-atomic-writer
owns:
  - ctx/journal.py
  - tests/test_journal_authors.py
reads:
  - path: ctx/atomic.py
    symbols:
      - write_text
  - path: ctx/paths.py
    symbols:
      - Layout
      - journal_file
      - digest
forbid:
  - ctx/atomic.py
  - ctx/cli.py
  - ctx/hooks.py
  - ctx/migrate.py
  - .ctx/.gitignore
budget_tokens: 70000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 2
---

## Objective
Stop `journal.prune` from being the one operation in the product that can destroy
history, and stop two people working the same day from conflicting on nearly
every merge.

## Interfaces
Consumes `atomic.write_text(path, text, encoding="utf-8")` from unit 01.

Produces, for unit 08's `ctx doctor` work:

```python
# ctx/journal.py
def author_key(layout):
    """The current author's journal file key. Slugified, filesystem-safe."""

def legacy_day_files(layout):
    """Day files in the pre-author shape, oldest first. Read, never rewritten."""
```

## Acceptance criteria

**Durability (roadmap item 17).** `journal.py:181` truncates and rewrites an
archive that holds months of folded history whose source day files were unlinked
by *earlier* prune runs. A crash mid-write loses history that has no other copy.

1. `prune`'s archive write and `write_digest` both route through
   `atomic.write_text`.
2. Positive control, and the one most likely to go fail-green in this unit: seed
   an archive with content from a prior run, run a prune whose write fails partway
   through (patch `os.replace` to raise — a mocked-out whole function proves
   nothing), and assert the pre-existing archive is byte-identical afterwards and
   still holds every line it had. Quote the run against the current code showing
   the truncated archive, and the run after. **A real interruption, not a
   simulated one.**
3. Day files are not unlinked when the archive write failed. Today the unlink
   loop runs regardless; assert the source files survive a failed write, because
   an archive that was not written plus inputs that were deleted is the total-loss
   case.

**Per-author journals (roadmap item 21).** `journal.py:44` keys the day file on
the date alone, so two people on one repo conflict on `journal/2026-09-10.md` and
on `DIGEST.md` on nearly every merge.

4. `append` writes to a per-author file. The author key derives from
   `git config user.email` (then `user.name`), slugified, with a documented
   fallback when git is unavailable or unconfigured — the fallback must be
   deterministic and must not be a hostname or anything that leaks more than the
   git identity already committed to the repo does.
5. **Decided, do not reopen:** existing `journal/YYYY-MM-DD.md` day files are
   *not* rewritten or migrated. They are read as one unattributed stream
   alongside the new per-author files. The reason, recorded so it is not
   re-litigated: those files record no author at all, so migrating them would
   stamp every historical entry with whoever ran the migration.
6. `tail`, `write_digest` and `prune` read every author's file *and* the legacy
   day files as one merged, time-ordered stream. A test seeds a tree with both
   shapes, interleaved in time, and asserts the merged order is correct and the
   legacy file is byte-identical afterwards.
7. Line-atomicity survives: the existing guarantee is 8 forks × 150 records =
   1200/1200 present, 0 malformed. Reproduce it across *two different authors*
   writing concurrently and assert the same.
8. `prune` folds per-author files into the monthly archive too, so the new shape
   retires the same way the old one does.
9. Appends still never raise. A test asserts a read-only journal directory leaves
   `append` returning without an exception, as today.
10. `python3 -m unittest discover -s tests -q` passes; suite count strictly up.
11. No file outside `owns` is modified. In particular `.ctx/.gitignore` and the
    `ctx doctor` advisory about a tracked `DIGEST.md` belong to unit 08 — export
    `legacy_day_files` and `author_key` for it and stop there.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs proving criteria 2 and 3 are real positive controls · the exact
author-key fallback you chose and why · the two symbols unit 08 consumes.
