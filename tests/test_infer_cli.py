"""`ctx infer` and the hardened `ctx spec-ready`."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Fixture

SLUG = "add-billing-export"


class TestInferCommand(Fixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("spec", SLUG)[0], 0)

    def test_infer_records_an_answer_without_a_prior_question(self):
        code, out = self.cli("infer", SLUG, "why now", "A customer asked for it.",
                             "--because", "the ticket says so directly")
        self.assertEqual(code, 0, out)
        _code, ask_out = self.cli("ask", SLUG)
        self.assertIn("resolved", ask_out.lower())

    def test_infer_refuses_an_unregistered_category(self):
        code, out = self.cli("infer", SLUG, "not-a-category", "x",
                             "--because", "y")
        self.assertNotEqual(code, 0)
        self.assertIn("not-a-category", out)


class TestSpecReadyRequiresIntake(Fixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("spec", SLUG)[0], 0)

    def test_refuses_with_no_intake_at_all(self):
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 1)
        self.assertIn("intake", out.lower())

    def test_refuses_naming_exactly_the_missing_categories(self):
        self.cli("infer", SLUG, "why now", "a", "--because", "b")
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 1)
        low = out.lower()
        self.assertIn("what could go wrong", low)
        self.assertIn("what changes for you", low)

    def test_ready_once_intake_and_blocking_questions_are_both_clear(self):
        for category in ("why now", "what could go wrong", "what changes for you"):
            self.cli("infer", SLUG, category, "a", "--because", "b")
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
