"""ctx/spec.py: the inferred-intake record, additive to the questions file."""

import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import spec as spec_mod
from ctx.paths import Layout  # existing layout constructor used across tests


def _layout(root):
    return Layout(Path(root) / ".ctx")


class TestRecordInferred(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = _layout(self._tmp.name)
        self.slug = "add-billing-export"
        spec_mod.create(self.layout, self.slug, intent="Let a customer export invoices.")

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_inferred_answer_lands_in_resolved(self):
        ok = spec_mod.record_inferred(
            self.layout, self.slug, "why now",
            "A customer asked for CSV export in the ticket.",
            "the ticket's own second paragraph says this directly",
        )
        self.assertTrue(ok)
        _blocking, _non, resolved = spec_mod.questions(self.layout, self.slug)
        self.assertEqual(len(resolved), 1)
        self.assertIn("Why now:", resolved[0])
        self.assertIn("customer asked for CSV export", resolved[0])
        self.assertIn("inferred, not asked", resolved[0])
        self.assertIn("ticket's own second paragraph", resolved[0])

    def test_rejects_an_unregistered_category(self):
        with self.assertRaises(ValueError):
            spec_mod.record_inferred(
                self.layout, self.slug, "not a real category", "x", "y")

    def test_never_touches_blocking_or_non_blocking(self):
        spec_mod.record_inferred(
            self.layout, self.slug, "what could go wrong", "n/a", "n/a")
        blocking, non_blocking, _resolved = spec_mod.questions(self.layout, self.slug)
        self.assertEqual(blocking, [])
        self.assertEqual(non_blocking, [])


class TestIntakeStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = _layout(self._tmp.name)
        self.slug = "add-billing-export"
        spec_mod.create(self.layout, self.slug)

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_missing_when_nothing_recorded(self):
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertEqual(status, {c: False for c in spec_mod.INTAKE_CATEGORIES})

    def test_an_asked_and_answered_question_counts(self):
        spec_mod.add_questions(self.layout, self.slug,
                               ["Why now: is this urgent?"], blocking=True)
        spec_mod.resolve(self.layout, self.slug, "Why now",
                         "A customer is blocked without it.")
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertTrue(status["why now"])
        self.assertFalse(status["what could go wrong"])

    def test_an_inferred_answer_counts_the_same_as_an_asked_one(self):
        spec_mod.record_inferred(self.layout, self.slug, "what could go wrong", "x", "y")
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertTrue(status["what could go wrong"])

    def test_ready_reports_exactly_the_missing_categories(self):
        spec_mod.record_inferred(self.layout, self.slug, "why now", "a", "b")
        ready, missing = spec_mod.intake_ready(self.layout, self.slug)
        self.assertFalse(ready)
        self.assertEqual(sorted(missing),
                         ["what changes for you", "what could go wrong"])

    def test_ready_is_true_once_all_three_are_recorded(self):
        for category in spec_mod.INTAKE_CATEGORIES:
            spec_mod.record_inferred(self.layout, self.slug, category, "a", "b")
        ready, missing = spec_mod.intake_ready(self.layout, self.slug)
        self.assertTrue(ready)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
