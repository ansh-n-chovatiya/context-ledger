---
ctx_schema: 1
unit: 13-list-continuations
plan: ctx-0-8
tier: subagent
depends_on: []
owns:
  - ctx/frontmatter.py
  - ctx/spec.py
  - tests/test_list_continuations.py
reads:
  - ctx/review.py
  - ctx/briefing.py
  - ctx/work.py
  - ctx/board.py
forbid: []
budget_tokens: 45000
status: done
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 1
---

## Objective
Markdown list items silently lose every line after their first. Fix it at the
two sources — `frontmatter.Document.list_items` and `spec._open_items`/
`_all_items` — so a multi-line acceptance criterion or question survives intact
everywhere it is read.

## Interfaces
`list_items(*names) -> list[str]` and `spec.questions(...)` keep their exact
signatures and return types. Only the *content* of the returned strings
changes: a continuation line is now joined onto its item instead of vanishing.
Consumers need no change — `review.py`, `briefing.py`, `work.py`, `cli.py` and
`board.py` all pick the fix up for free.

## Acceptance criteria
1. A list item whose source spans three or more physical lines is returned
   whole, interior whitespace collapsed, joined with single spaces. Assert a
   distinctive word from the **last** line survives — that is the assertion
   whose absence let this ship.
2. The same holds for `spec.questions` across blocking, non-blocking and
   resolved items, including a checkbox item (`- [ ] …`) wrapped over lines.
3. A line is a continuation when it is non-blank, does not itself start a list
   marker, and is indented relative to its marker. A new marker, a blank line
   ending the item, or the end of the section closes it.
4. Nothing else about list parsing changes: single-line items, empty sections,
   a missing section, and items containing `-` or digits mid-text all behave
   exactly as before.
5. The real ledger proves it: `.ctx/specs/ctx-0-8/spec.md`'s first acceptance
   criterion is four physical lines, and reading it must yield text ending
   "One unit test per signal."
6. Existing tests still pass untouched. If any existing test asserts the
   *current truncating* behaviour, do NOT edit it — report it, naming the test.
   A test that pins a bug in place is a finding worth surfacing, not a file to
   quietly rewrite.
7. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any
existing test that asserted the old behaviour · any interface change.
