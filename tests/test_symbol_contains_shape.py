"""`kind: symbol` must not pass because `contains` was written as a string.

`_check_symbol` built its needles with

    names = [str(n) for n in (check.get("contains") or []) if str(n).strip()]

Given a list that is right. Given a *string* it iterates the characters, and
`d`, `e`, `f` appear in every source file, so `missing` came back empty and the
check passed no matter what the file contained:

    contains: "def totally_absent("    -> pass   (the symbol does not exist)
    contains: ["def totally_absent("]  -> fail   (correct)

Two ways to reach it, neither exotic. A human writes `contains: "def render("`
for a single symbol, which is the obvious spelling. Or a writer that cannot
represent a nested sequence rewrites the list into one string — which is what an
older `miniyaml` did to two unit contracts, leaving their interface freeze
vacuous while the gate still reported `ok symbol:`.

It matters because this is the check that stops a renamed signature reaching a
sibling unit that is coding against it, and it failed *open*: silently, and in
the direction that says everything is fine.

A bare string is now taken as one name. That is what the author meant, it cannot
be vacuous, and it keeps working for the corrupted-file case too.
"""

import string
import unittest

from ctx import verify

from support import Fixture


ABSENT = "def a_symbol_that_is_definitely_not_here("


class TestAStringContainsIsOneName(Fixture):

    def setUp(self):
        super().setUp()
        # Every printable character appears below, deliberately. The bug
        # looked each character of `contains` up on its own, so a fixture
        # missing even one of them makes these tests fail by luck and prove
        # nothing. Two earlier drafts of this test did exactly that: one had no
        # `y`, the next no `_`, and both looked green for the wrong reason.
        self.target = self.layout.root / "module.py"
        self.target.write_text(
            "# " + string.printable.replace("\x0b", "").replace("\x0c", "")
            + "\ndef render():\n    return 1\n",
            encoding="utf-8")

    def check(self, contains):
        return verify._check_symbol(
            {"kind": "symbol", "path": "module.py", "contains": contains},
            str(self.layout.root),
        )

    def test_a_string_naming_an_absent_symbol_fails(self):
        """The defect, stated as the assertion it failed to make."""
        self.assertEqual(self.check(ABSENT).status, verify.FAIL)

    def test_a_string_naming_a_present_symbol_passes(self):
        """The positive control: the fix must not reject what is genuinely there."""
        self.assertEqual(self.check("def render(").status, verify.PASS)

    def test_the_failure_names_what_was_missing(self):
        """A gate that fails without saying why trains people to ignore it."""
        self.assertIn("def a_symbol_that_is_definitely_not_here(",
                      self.check(ABSENT).message)

    def test_a_list_still_behaves_exactly_as_before(self):
        self.assertEqual(self.check([ABSENT]).status, verify.FAIL)
        self.assertEqual(self.check(["def render("]).status, verify.PASS)
        self.assertEqual(self.check(["def render(", ABSENT]).status, verify.FAIL)

    def test_a_corrupted_repr_string_no_longer_passes_vacuously(self):
        """The shape an older writer produced when it flattened a sequence.

        It is not a symbol anyone wrote, so it must fail — where before every
        one of its characters was looked up individually and all of them were
        found.
        """
        self.assertEqual(
            self.check("['def render(', 'def write(']").status, verify.FAIL)

    def test_an_empty_or_whitespace_string_is_still_a_configuration_error(self):
        """`contains` present but empty says nothing, and saying nothing must
        not be reported as a passing interface freeze."""
        for value in ("", "   ", []):
            self.assertEqual(self.check(value).status, verify.ERROR, repr(value))


class TestTheCharacterwiseBugCannotReturn(Fixture):
    """A regression guard aimed at the mechanism, not the symptom.

    If someone re-introduces iteration over a string, a single character that
    the file happens to contain would pass. This pins that a one-character
    `contains` is looked up as a *name*, so the file must actually contain it.
    """

    def setUp(self):
        super().setUp()
        (self.layout.root / "module.py").write_text("zzz\n", encoding="utf-8")

    def check(self, contains):
        return verify._check_symbol(
            {"kind": "symbol", "path": "module.py", "contains": contains},
            str(self.layout.root),
        )

    def test_a_single_character_absent_from_the_file_fails(self):
        self.assertEqual(self.check("q").status, verify.FAIL)

    def test_a_single_character_present_in_the_file_passes(self):
        self.assertEqual(self.check("z").status, verify.PASS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
