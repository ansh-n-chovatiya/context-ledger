"""A briefing that came back empty and reported that nothing was dropped.

`_fit` emits blocks in priority order until the next one would cross the cap,
then appends `…[briefing truncated]` so the reader knows the rest exists. The
marker costs 21 characters, and at a cap too small to afford both a block and
the marker the old code appended neither and returned `""`. `measure()` then
decided whether truncation had happened by looking for the marker *in that
text* — so it said `truncated: False`, and a caller could not tell "this
project has nothing to say" from "everything it had to say was dropped".
`ctx doctor` and `ctx ci` read exactly that field.

The fix reports truncation from `_fit`, which knows, instead of inferring it
from the rendered text, which cannot. The marker is also emitted now whenever
it fits at all, including when no block did — but the flag is the guarantee,
because at a cap below 21 characters there is no room to print anything.

What is deliberately *not* a truncation is `cap <= 0`. That is L0's documented
way to turn the briefing off (`briefing_chars.l0: 0` in `ctx.yaml`), and
conflating the two would make `ctx ci` fail every project that had done it.

Each test fails if the guard it covers is removed:
  - report truncation from the text again  -> TestNothingFits
  - treat `cap <= 0` as a truncation       -> TestNoBriefingIsNotATruncation
  - drop the clamp                         -> TestTheCapIsStillHonoured
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import briefing, state as state_mod  # noqa: E402
from support import Fixture  # noqa: E402


class BriefingFixture(Fixture):
    """An L0 project, measured at whatever cap the test wants."""

    def state(self):
        return state_mod.load(self.layout)

    def at(self, cap):
        """`measure()` with `briefing_chars.l0` set to `cap`."""
        config = dict(self.config, briefing_chars={"l0": cap})
        return briefing.measure(self.layout, config, self.state())

    def blocks(self):
        """The blocks the briefing would emit, at a cap that drops none."""
        return self.at(10_000)["text"].split("\n")


class TestTheFixtureItself(BriefingFixture):
    """The caps below are derived from these, so pin them first."""

    def test_an_l0_briefing_has_more_than_one_block(self):
        self.assertGreater(len(self.blocks()), 1, "otherwise nothing can be dropped")

    def test_an_uncapped_briefing_is_not_reported_as_truncated(self):
        measured = self.at(10_000)
        self.assertFalse(measured["truncated"])
        self.assertNotIn(briefing.TRUNCATED, measured["text"])

    def test_the_default_cap_is_not_truncated_either(self):
        """The shipped L0 budget must fit an empty project with room to spare."""
        measured = briefing.measure(self.layout, self.config, self.state())
        self.assertFalse(measured["truncated"], measured["text"])


class TestNothingFits(BriefingFixture):
    """Criterion 5 and 7: a cap too small for even one block."""

    def test_a_cap_below_the_marker_still_reports_truncation(self):
        """The defect. Nothing can be printed, so the flag is the only signal."""
        measured = self.at(5)
        self.assertEqual(measured["text"], "")
        self.assertTrue(
            measured["truncated"],
            "an empty briefing claiming nothing was dropped is indistinguishable "
            "from a project with nothing to report",
        )

    def test_a_cap_that_fits_only_the_marker_prints_the_marker(self):
        measured = self.at(len(briefing.TRUNCATED))
        self.assertEqual(measured["text"], briefing.TRUNCATED)
        self.assertTrue(measured["truncated"])

    def test_build_and_measure_agree_at_a_tiny_cap(self):
        config = dict(self.config, briefing_chars={"l0": 5})
        self.assertEqual(
            briefing.build(self.layout, config, self.state()),
            self.at(5)["text"],
        )

    def test_every_cap_from_zero_upwards_is_self_consistent(self):
        """`truncated` is false only when the whole briefing is present."""
        whole = self.at(10_000)["text"]
        for cap in range(1, len(whole) + 2):
            with self.subTest(cap=cap):
                measured = self.at(cap)
                self.assertEqual(
                    measured["truncated"], measured["text"] != whole,
                    f"cap={cap} text={measured['text']!r}",
                )


class TestExactlyOneBlock(BriefingFixture):
    """Criterion 7: a cap that fits exactly one block and no more."""

    def test_the_first_block_survives_and_truncation_is_reported(self):
        first = self.blocks()[0]
        measured = self.at(len(first))
        self.assertEqual(measured["text"], first, "the highest-priority block stays")
        self.assertTrue(measured["truncated"], "the blocks after it were dropped")

    def test_a_block_is_never_given_back_to_print_the_marker(self):
        """Blocks are in priority order: losing the headline to say "truncated"
        would cost the reader more than it told them."""
        first = self.blocks()[0]
        self.assertIn(first, self.at(len(first))["text"])

    def test_a_cap_one_short_of_the_first_block_keeps_the_marker(self):
        first = self.blocks()[0]
        measured = self.at(len(first) - 1)
        self.assertIn(briefing.TRUNCATED, measured["text"])
        self.assertTrue(measured["truncated"])

    def test_a_cap_that_fits_every_block_exactly_is_not_truncated(self):
        whole = self.at(10_000)["text"]
        measured = self.at(len(whole))
        self.assertEqual(measured["text"], whole)
        self.assertFalse(measured["truncated"])


class TestNoBriefingIsNotATruncation(BriefingFixture):
    """Criterion 6: `cap <= 0` is L0's "off", not a loss."""

    def test_a_zero_cap_reports_no_truncation_and_no_marker(self):
        measured = self.at(0)
        self.assertEqual(measured["text"], "")
        self.assertFalse(
            measured["truncated"],
            "`ctx doctor` would report a truncation on every project that had "
            "deliberately turned the briefing off",
        )
        self.assertNotIn(briefing.TRUNCATED, measured["text"])

    def test_a_negative_cap_is_the_same_case(self):
        """`briefing_cap` clamps to 0, but `_fit` is called from elsewhere too."""
        text, dropped = briefing._fit(["a block"], -5)
        self.assertEqual(text, "")
        self.assertFalse(dropped)

    def test_no_blocks_at_a_real_cap_is_not_a_truncation_either(self):
        text, dropped = briefing._fit([], 220)
        self.assertEqual(text, "")
        self.assertFalse(dropped)

    def test_ci_passes_with_the_briefing_turned_off(self):
        """Criterion 6 where it is actually observed."""
        from ctx import miniyaml

        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data["briefing_chars"] = {"l0": 0, "l1": 0, "l2": 0}
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        code, out = self.cli("ci")
        self.assertEqual(code, 0, out)
        self.assertNotIn("truncat", out.replace("without truncation", ""))


class TestTheCapIsStillHonoured(BriefingFixture):
    """The marker must not be what pushes the briefing over the budget."""

    def test_no_cap_produces_a_briefing_longer_than_itself(self):
        whole = self.at(10_000)["text"]
        for cap in range(0, len(whole) + 20):
            with self.subTest(cap=cap):
                measured = self.at(cap)
                self.assertLessEqual(measured["chars"], cap, repr(measured["text"]))
                self.assertEqual(measured["chars"], len(measured["text"]))

    def test_the_reported_cap_is_the_configured_one(self):
        self.assertEqual(self.at(37)["cap"], 37)


if __name__ == "__main__":
    unittest.main()
