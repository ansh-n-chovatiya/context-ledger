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

    def test_infer_refuses_an_empty_answer(self):
        """An empty answer used to satisfy the intake gate exactly as well as
        a real one — `intake_status` only checks the line starts with the
        category's prefix. `--because` alone cannot make an empty `answer`
        pass argparse's `required=True`, because required only means present,
        not non-empty: `--because ""` still parses."""
        code, out = self.cli("infer", SLUG, "why now", "",
                             "--because", "a real reason")
        self.assertNotEqual(code, 0, out)
        _code, ask_out = self.cli("ask", SLUG)
        self.assertNotIn("resolved", ask_out.lower())

    def test_infer_refuses_an_empty_because(self):
        code, out = self.cli("infer", SLUG, "why now", "a real answer",
                             "--because", "   ")
        self.assertNotEqual(code, 0, out)
        _code, ask_out = self.cli("ask", SLUG)
        self.assertNotIn("resolved", ask_out.lower())


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

    def test_three_blank_answers_cannot_satisfy_spec_ready(self):
        """The bypass this whole file exists to close: `record_inferred` used
        to accept an empty `answer`/`--because`, and `intake_status` only
        checks that a Resolved line starts with the category's prefix — so
        `ctx infer` on all three categories with nothing but whitespace used
        to leave `ctx spec-ready` reporting 0, unblocking `ctx plan` on a spec
        nobody actually gave a reason, a risk, or an audience for."""
        for category in ("why now", "what could go wrong", "what changes for you"):
            code, out = self.cli("infer", SLUG, category, "  ", "--because", "  ")
            self.assertNotEqual(code, 0, out)
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 1, out)
        self.assertIn("intake", out.lower())


if __name__ == "__main__":
    unittest.main()
