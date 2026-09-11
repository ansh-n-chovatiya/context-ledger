---
ctx_schema: 1
unit: 01-atomic-writer
plan: make-ctx-durable-under-concurrency
tier: subagent
depends_on: []
owns:
  - ctx/atomic.py
  - tests/test_atomic_writes.py
reads:
  - path: ctx/frontmatter.py
    symbols:
      - Document.write
forbid:
  - ctx/journal.py
  - ctx/migrate.py
  - ctx/lock.py
  - ctx/hooks.py
  - ctx/worktree.py
budget_tokens: 40000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Extract the temp-file + fsync + `os.replace` write that `frontmatter.Document.write`
already implements into one reusable `ctx/atomic.py`, so the call sites that
destroy history with `Path.write_text` have something to route through.

`frontmatter.Document.write` is your model and its docstring carries the
rationale — read it. `state.save` does the same thing and is deliberately *not*
in your `reads`: unit 02 is rewriting that file this wave.

## Interfaces

Produces — every later unit in this plan codes against this exact signature:

```python
# ctx/atomic.py
def write_text(path, text, encoding="utf-8"):
    """Write `text` to `path` atomically. Returns `path`.

    Creates parent directories. The temp file is made in the destination's own
    directory (os.replace is atomic only within one filesystem) with a `.ctx-`
    prefix and `.tmp` suffix, so it is not picked up by the `*.md` and
    `*.json` globs that scan the ledger. On any exception the temp file is
    removed and the original is left byte-identical.
    """
```

Do not change `frontmatter.Document.write` or `state.save` in this unit — both
already do the right thing, and `state.py` belongs to unit 02 this wave. This unit
adds the shared implementation; re-pointing those two at it is not in scope and
would put you outside `owns`.

## Acceptance criteria
1. `ctx/atomic.py` exists and exports `write_text(path, text, encoding="utf-8")`
   with the semantics above. Python 3.8 compatible: no walrus in a comprehension,
   no `Path.write_text(newline=)`, no PEP 604 unions — CI tests 3.8 and the floor
   is real.
2. Positive control, and it is the point of this unit: a test that makes the
   write fail *after* the temp file is written and *before* `os.replace` — patch
   `os.replace` to raise, do not mock the whole function — asserts the original
   file is byte-identical afterwards, for a target that already held content.
   Show that this test fails against `Path.write_text` before it passes against
   `atomic.write_text`; the report must quote both runs.
3. A test asserts no `.ctx-*.tmp` file survives either the success path or the
   failure path.
4. A test asserts the parent directory is created when absent.
5. A test asserts the temp file is created in the destination's directory, not
   in the system temp dir — the cross-filesystem `os.replace` failure is the one
   this would reintroduce.
6. `python3 -m unittest discover -s tests -q` passes and the suite count is
   strictly higher than the 1027 it starts at.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · the
before/after runs proving criterion 2 is a real positive control · any interface
you were forced to change (that blocks the wave).
