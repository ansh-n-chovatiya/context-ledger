"""`config.normalise_level`'s fail-down, pinned.

`normalise_level` coerces anything it does not recognise to `"0"` — the
*least* capable level. That is a deliberate fail-down, and until this file
existed nothing in the suite referenced the function at all, so the fallback
could be mutated (to fail *up*, or to vanish entirely) without a single test
noticing.

The load-bearing property is not "the result is a member of `LEVELS`" but
that it is `"0"` specifically. `briefing_cap` looks the result up in
`DEFAULTS["briefing_chars"][f"l{level}"]`, so a fallback that returned
anything outside `LEVELS` would raise `KeyError` on a caller's behalf, and a
fallback that returned `"2"` would hand an unparseable config the *largest*
briefing budget rather than the smallest.

The tests below are written so each of these mutations kills at least one:
  - `else "0"` -> `else "2"`  (fail-up)         -> TestFailDown
  - `else "0"` -> no fallback (`return text`)   -> TestFailDown, TestBriefingCap
  - drop `.strip()`                             -> TestWhitespace
  - drop `.lstrip("L")`                         -> TestSpellings
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import config as config_mod  # noqa: E402
from test_policy import PolicyFixture  # noqa: E402


class NotALevel:
    """An object whose `__str__` is emphatically not a level name."""

    def __str__(self):
        return "<NotALevel object>"


class TestValidLevelsRoundTrip(unittest.TestCase):
    def test_every_declared_level_round_trips(self):
        self.assertEqual(config_mod.LEVELS, ("0", "1", "2"))
        for level in config_mod.LEVELS:
            with self.subTest(level=level):
                self.assertEqual(config_mod.normalise_level(level), level)

    def test_every_declared_level_round_trips_as_int(self):
        for level in config_mod.LEVELS:
            with self.subTest(level=level):
                self.assertEqual(config_mod.normalise_level(int(level)), level)

    def test_every_declared_level_round_trips_l_prefixed(self):
        for level in config_mod.LEVELS:
            with self.subTest(level=level):
                self.assertEqual(config_mod.normalise_level(f"l{level}"), level)
                self.assertEqual(config_mod.normalise_level(f"L{level}"), level)


class TestSpellings(unittest.TestCase):
    """The spellings a hand-edited ctx.yaml is allowed to use."""

    def test_lowercase_l_prefix(self):
        self.assertEqual(config_mod.normalise_level("l0"), "0")
        self.assertEqual(config_mod.normalise_level("l1"), "1")
        self.assertEqual(config_mod.normalise_level("l2"), "2")

    def test_uppercase_l_prefix(self):
        # Dropping `.lstrip("L")` turns "L2" into an unrecognised string, which
        # then fails down to "0" — silently demoting a level 2 project.
        self.assertEqual(config_mod.normalise_level("L2"), "2")
        self.assertEqual(config_mod.normalise_level("L1"), "1")

    def test_bare_string_digit(self):
        self.assertEqual(config_mod.normalise_level("2"), "2")

    def test_bare_int(self):
        self.assertEqual(config_mod.normalise_level(2), "2")
        self.assertEqual(config_mod.normalise_level(0), "0")

    def test_none_is_level_zero(self):
        self.assertEqual(config_mod.normalise_level(None), "0")

    def test_result_is_always_a_string(self):
        for value in ("l0", "L2", "2", 2, None, "banana", ["2"]):
            with self.subTest(value=value):
                self.assertIsInstance(config_mod.normalise_level(value), str)


class TestWhitespace(unittest.TestCase):
    """Surrounding whitespace is stripped before the level is recognised."""

    def test_leading_whitespace(self):
        self.assertEqual(config_mod.normalise_level("  2"), "2")
        self.assertEqual(config_mod.normalise_level("  l1"), "1")

    def test_trailing_whitespace(self):
        self.assertEqual(config_mod.normalise_level("2  "), "2")
        self.assertEqual(config_mod.normalise_level("L1  "), "1")

    def test_surrounding_whitespace_including_tabs_and_newlines(self):
        self.assertEqual(config_mod.normalise_level("\t l2 \n"), "2")

    def test_whitespace_only_is_level_zero(self):
        self.assertEqual(config_mod.normalise_level("   "), "0")


class TestFailDown(unittest.TestCase):
    """Unrecognised input yields `"0"` — not "some member of LEVELS"."""

    UNRECOGNISED = (
        "banana",
        "3",
        "L3",
        "level 2",
        "",
        "   ",
        ["2"],
        {"level": "2"},
        NotALevel(),
        object(),
        3.5,
        True,
    )

    def test_unrecognised_input_falls_down_to_level_zero(self):
        for value in self.UNRECOGNISED:
            with self.subTest(value=repr(value)):
                self.assertEqual(
                    config_mod.normalise_level(value),
                    "0",
                    "unrecognised input must fail *down* to the least capable "
                    "level, not up and not through",
                )

    def test_fallback_is_the_least_capable_level_specifically(self):
        # The point of the fail-down: an unparseable level must not be handed
        # the *largest* briefing budget. Asserting membership in LEVELS would
        # not catch that; asserting the exact value does.
        result = config_mod.normalise_level("banana")
        self.assertEqual(result, "0")
        self.assertNotEqual(result, "1")
        self.assertNotEqual(result, "2")
        self.assertEqual(result, min(config_mod.LEVELS))

    def test_empty_string_falls_down(self):
        self.assertEqual(config_mod.normalise_level(""), "0")

    def test_unrecognised_type_falls_down(self):
        self.assertEqual(config_mod.normalise_level(NotALevel()), "0")
        self.assertEqual(config_mod.normalise_level(["2"]), "0")

    def test_result_is_always_a_known_level(self):
        for value in self.UNRECOGNISED + ("l0", "L2", "2", 2, None):
            with self.subTest(value=repr(value)):
                self.assertIn(config_mod.normalise_level(value), config_mod.LEVELS)


class TestBriefingCapInteraction(unittest.TestCase):
    """`briefing_cap` depends on the fail-down landing inside `LEVELS`."""

    def test_unrecognised_level_gets_the_l0_default_cap(self):
        self.assertEqual(
            config_mod.briefing_cap({}, "banana"),
            config_mod.DEFAULTS["briefing_chars"]["l0"],
        )

    def test_unrecognised_level_does_not_raise_key_error(self):
        for value in ("banana", "", "L3", ["2"], NotALevel(), None):
            with self.subTest(value=repr(value)):
                try:
                    cap = config_mod.briefing_cap({}, value)
                except KeyError as exc:  # pragma: no cover - the failure mode
                    self.fail(
                        "briefing_cap raised KeyError for "
                        f"{value!r}: {exc!r} — the fail-down must land inside "
                        "DEFAULTS['briefing_chars']"
                    )
                self.assertEqual(cap, config_mod.DEFAULTS["briefing_chars"]["l0"])

    def test_unrecognised_level_uses_an_l0_override_not_l2(self):
        caps = {"l0": 11, "l1": 22, "l2": 33}
        self.assertEqual(config_mod.briefing_cap({"briefing_chars": caps}, "banana"), 11)

    def test_recognised_levels_still_reach_their_own_cap(self):
        caps = {"l0": 11, "l1": 22, "l2": 33}
        config = {"briefing_chars": caps}
        self.assertEqual(config_mod.briefing_cap(config, "L2"), 33)
        self.assertEqual(config_mod.briefing_cap(config, " l1 "), 22)
        self.assertEqual(config_mod.briefing_cap(config, 0), 11)

    def test_defaults_cover_every_declared_level(self):
        for level in config_mod.LEVELS:
            with self.subTest(level=level):
                self.assertIn(f"l{level}", config_mod.DEFAULTS["briefing_chars"])


class TestLockingSurvivesVoidingTheParentKey(PolicyFixture):
    """`_lock_holder` matches a lock on `dotted` or any prefix of it, so
    `locked: [gate]` correctly covers `gate.enabled`. The reverse was
    unguarded — a lock on `gate.allow_override` did not cover an assignment
    to `gate` itself, and `_assign` replaces the whole mapping. A repo
    `ctx.yaml` setting `gate: []` collapsed the locked value along with
    everything else under `gate`, with no refusal recorded anywhere.

    `PolicyFixture` (and the prefix-lock test this mirrors,
    `test_locking_a_parent_key_locks_everything_under_it`) live in
    `test_policy.py`, a widely-shared file this plan's wave leaves to no
    single unit — imported here rather than edited there.
    """

    def test_voiding_the_parent_key_does_not_clear_the_locked_child(self):
        self.write_policy(
            "user", gate={"allow_override": True}, locked=["gate.allow_override"]
        )
        self.write_repo_config(gate=[])
        config, _err = self.load()
        self.assertIs(config["gate"]["allow_override"], True)

    def test_the_refusal_is_visible_on_stderr(self):
        self.write_policy(
            "user", gate={"allow_override": True}, locked=["gate.allow_override"]
        )
        self.write_repo_config(gate=[])
        _config, err = self.load()
        self.assertIn("gate.allow_override", err)
        self.assertIn("user policy locks it", err)

    def test_the_refusal_is_recorded_for_doctor_to_report(self):
        self.write_policy(
            "user", gate={"allow_override": True}, locked=["gate.allow_override"]
        )
        self.write_repo_config(gate=[])
        refusals = config_mod.resolve_policy(self.layout).refusals
        self.assertEqual(len(refusals), 1)
        source, key, _attempted, held, holder = refusals[0]
        self.assertEqual((source, key, held, holder),
                         ("repo", "gate.allow_override", True, "user"))

    def test_doctor_names_the_refusal_and_counts_it_as_a_problem(self):
        self.write_policy(
            "user", gate={"allow_override": True}, locked=["gate.allow_override"]
        )
        self.write_repo_config(gate=[])
        code, out = self.cli("doctor")
        self.assertIn("gate.allow_override", out)
        self.assertEqual(code, 1, "a setting that cannot apply is a problem")

    def test_locking_a_parent_still_covers_the_child_unchanged(self):
        """The working direction (`locked: [gate]` covers `gate.enabled`) is
        untouched by this fix — it does not go through `_locked_descendants`
        at all, since the assigned key there (`gate.enabled`) is what is
        locked, not an ancestor of it."""
        self.write_policy("user", gate={"max_attempts": 4}, locked=["gate"])
        self.write_repo_config(gate={"max_attempts": 11})
        config, _err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 4)


if __name__ == "__main__":
    unittest.main()
