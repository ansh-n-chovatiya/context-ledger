"""List-item continuations.

`frontmatter.Document.list_items` and `spec._open_items`/`_all_items` used to
match exactly one physical line per item — an indented continuation line was
silently dropped rather than folded onto the item it belonged to. That is not
a formatting nit: `list_items("acceptance criteria")` is what a reviewer
judges a unit against (`review.py`), what the SessionStart briefing shows
(`briefing.py`), what feeds the done-gate (`work.py`), and what `cli.py` and
`board.py` print — every multi-line acceptance criterion in this system was
losing its tail everywhere it was read. `spec.questions()` had the identical
defect independently, for the exact same reason (it does not go through
`Document.list_items`, since it also has to preserve checkbox state).

This module pins the fix at both sources.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, spec as spec_mod  # noqa: E402
from support import Fixture  # noqa: E402


# --------------------------------------------------------------------------- #
# frontmatter.Document.list_items
# --------------------------------------------------------------------------- #

class ListItemContinuations(unittest.TestCase):
    def test_three_line_item_survives_whole_with_the_last_words_intact(self):
        """Criterion 1. The failure this whole unit exists to fix: a
        continuation line vanished, so the *last* line's content is exactly
        what a test must check for — checking the first line would have let
        the bug ship, since the first line was never the one being dropped.
        """
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\n   continued text here\n   and a tail word finalword\n"
            "2. two\n"
        )
        items = doc.list_items("acceptance criteria")
        self.assertEqual(
            items,
            ["one continued text here and a tail word finalword", "two"],
        )
        self.assertTrue(items[0].endswith("finalword"))

    def test_interior_whitespace_collapses_to_single_spaces(self):
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\n   \t  extra   spaced   continuation\n"
        )
        items = doc.list_items("acceptance criteria")
        self.assertEqual(items, ["one extra spaced continuation"])

    def test_numbered_and_bullet_markers_both_join_continuations(self):
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "- first\n  more of first\n* second\n  more of second\n"
        )
        self.assertEqual(
            doc.list_items("acceptance criteria"),
            ["first more of first", "second more of second"],
        )

    def test_blank_line_closes_the_item(self):
        """Criterion 3. A blank line ends the item even though a section can
        keep going after it — the text after the blank belongs to nothing,
        since nothing re-opened a marker."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\n   still one\n\n   orphaned, not appended\n2. two\n"
        )
        self.assertEqual(doc.list_items("acceptance criteria"), ["one still one", "two"])

    def test_a_new_marker_closes_the_previous_item_even_if_less_indented(self):
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\n   continuing one\n2. two\n   continuing two\n"
        )
        self.assertEqual(
            doc.list_items("acceptance criteria"),
            ["one continuing one", "two continuing two"],
        )

    def test_end_of_section_closes_the_final_item(self):
        """A continuation as the very last line of the section (no trailing
        blank, no following marker) must still be folded in — the item is
        closed by falling off the end of the section, not by any sentinel
        line existing after it."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n1. one\n   trailing\n"
        )
        self.assertEqual(doc.list_items("acceptance criteria"), ["one trailing"])

    def test_a_line_not_indented_past_its_marker_is_not_a_continuation(self):
        """Criterion 3's other half: 'indented relative to its marker' is
        load-bearing. A line at or before the marker's own column is not a
        continuation — it is dropped, exactly as the pre-fix code dropped
        every non-marker line, because nothing here can distinguish a stray
        paragraph from a mistake."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\nnot indented at all, should not attach\n2. two\n"
        )
        self.assertEqual(doc.list_items("acceptance criteria"), ["one", "two"])

    def test_mid_text_hyphen_and_digit_do_not_look_like_markers(self):
        """Criterion 4. A continuation containing '-' or a digit mid-sentence
        must not be mistaken for a new item — the marker regex only matches
        at the start of the (stripped) line."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. one\n   has a hyphen - right here and a 3rd word too\n"
        )
        self.assertEqual(
            doc.list_items("acceptance criteria"),
            ["one has a hyphen - right here and a 3rd word too"],
        )

    def test_a_nested_indented_marker_still_opens_its_own_flat_item(self):
        """Design decision, stated in the docstring: this method returns a
        flat list and does not represent nesting. An indented line that is
        itself a marker still opens a new, separate entry rather than being
        folded into its parent's text — the pre-existing flattening
        behaviour every caller (review/briefing/work/cli/board) already
        relies on, which this fix deliberately does not change."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n"
            "1. parent\n   - nested child\n2. two\n"
        )
        self.assertEqual(
            doc.list_items("acceptance criteria"),
            ["parent", "nested child", "two"],
        )

    def test_single_line_items_unaffected(self):
        """Criterion 4. No continuation lines present at all."""
        doc = frontmatter.parse(
            "---\na: 1\n---\n\n## Acceptance criteria\n1. one\n2. two\n"
        )
        self.assertEqual(doc.list_items("acceptance criteria"), ["one", "two"])

    def test_empty_section_returns_empty_list(self):
        doc = frontmatter.parse("---\na: 1\n---\n\n## Acceptance criteria\n")
        self.assertEqual(doc.list_items("acceptance criteria"), [])

    def test_missing_section_returns_empty_list(self):
        doc = frontmatter.parse("---\na: 1\n---\n\n## Objective\nDo the thing.\n")
        self.assertEqual(doc.list_items("acceptance criteria"), [])


class RealSpecFileProvesTheFix(unittest.TestCase):
    def test_the_real_ledger_spec_no_longer_truncates_criterion_one(self):
        """Criterion 5. `.ctx/specs/ctx-0-8/spec.md`'s own first acceptance
        criterion spans four physical lines in the source file. Reading it
        through the fixed code must yield the full sentence, tail intact."""
        repo_root = Path(__file__).resolve().parent.parent
        path = repo_root / ".ctx" / "specs" / "ctx-0-8" / "spec.md"
        doc = frontmatter.read(path)
        self.assertIsNotNone(doc, f"expected {path} to exist")
        items = doc.list_items("acceptance criteria")
        self.assertTrue(items, "expected at least one acceptance criterion")
        self.assertTrue(
            items[0].endswith("One unit test per signal."),
            f"criterion 1 came back truncated: {items[0]!r}",
        )


# --------------------------------------------------------------------------- #
# spec._open_items / spec._all_items (via spec.questions)
# --------------------------------------------------------------------------- #

class SpecQuestionContinuations(Fixture):
    def write_questions_body(self, slug, body):
        spec_mod.create(self.layout, slug)
        qpath = spec_mod.questions_path(self.layout, slug)
        doc = frontmatter.read(qpath)
        doc.body = body
        doc.write(qpath)

    def test_a_wrapped_blocking_question_survives_whole(self):
        """Criterion 2, blocking."""
        slug = "billing"
        self.write_questions_body(
            slug,
            "## Blocking\n"
            "- [ ] Does invoice_v1 still take traffic\n"
            "  from the old checkout flow finaltail\n\n"
            "## Non-blocking\n\n"
            "## Resolved\n",
        )
        blocking, _non, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(
            blocking,
            ["Does invoice_v1 still take traffic from the old checkout flow finaltail"],
        )

    def test_a_wrapped_non_blocking_question_survives_whole(self):
        """Criterion 2, non-blocking."""
        slug = "billing"
        self.write_questions_body(
            slug,
            "## Blocking\n\n"
            "## Non-blocking\n"
            "- [ ] Any preference on log format\n"
            "  or is the default fine finaltail\n\n"
            "## Resolved\n",
        )
        _blocking, non_blocking, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(
            non_blocking,
            ["Any preference on log format or is the default fine finaltail"],
        )

    def test_a_wrapped_resolved_item_survives_whole(self):
        """Criterion 2, resolved — a plain bullet, no checkbox."""
        slug = "billing"
        self.write_questions_body(
            slug,
            "## Blocking\n\n"
            "## Non-blocking\n\n"
            "## Resolved\n"
            "- Does invoice_v1 still take traffic → No, read-only\n"
            "  since July finaltail (2026-01-01)\n",
        )
        _blocking, _non, resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(
            resolved,
            ["Does invoice_v1 still take traffic → No, read-only since July finaltail (2026-01-01)"],
        )

    def test_a_wrapped_checkbox_item_keeps_its_checked_state(self):
        """Criterion 2: a checked box (`- [x] …`) that wraps must still be
        excluded from the *open* list (its state is preserved through the
        join, not lost)."""
        slug = "billing"
        self.write_questions_body(
            slug,
            "## Blocking\n"
            "- [x] Already answered elsewhere\n"
            "  with a wrapped tail finaltail\n\n"
            "## Non-blocking\n\n"
            "## Resolved\n",
        )
        blocking, _non, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(blocking, [], "a checked box must never be open")

    def test_fresh_scaffold_has_no_open_questions(self):
        """Criterion 4 (spec side): the HTML-comment scaffolding `create()`
        seeds into a fresh questions file must not be mistaken for a
        continuation or an item — the gate must start open."""
        slug = "fresh"
        spec_mod.create(self.layout, slug)
        blocking, non_blocking, resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual((blocking, non_blocking, resolved), ([], [], []))

    def test_single_line_questions_unaffected(self):
        slug = "billing"
        self.write_questions_body(
            slug,
            "## Blocking\n- [ ] one\n- [ ] two\n\n## Non-blocking\n\n## Resolved\n",
        )
        blocking, _non, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(blocking, ["one", "two"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
