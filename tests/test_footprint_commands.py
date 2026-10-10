"""The footprint, trust pointer, drift and status wording, wired into the CLI.

Each behaviour has a positive control beside it: the warning that must appear
is shown appearing, so a test that asserts its absence cannot pass because the
section was never printed at all.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Fixture, _cleanup  # noqa: E402

from ctx import advice, footprint, frontmatter, plan as plan_mod, trust  # noqa: E402
from ctx import config as config_mod  # noqa: E402


class HomeIsolated(Fixture):
    """A fixture whose `~` is an empty temp dir, so the developer's own
    ~/.claude/CLAUDE.md never leaks into a measured footprint."""

    def setUp(self):
        super().setUp()
        self._home = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._home.name
        os.environ["USERPROFILE"] = self._home.name
        for name in ("python3_status", "rtk_on_path"):
            patcher = mock.patch.object(
                footprint, name, return_value=(True, "") if name == "python3_status" else False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        super().tearDown()
        _cleanup(self._home)

    def doctor_json(self):
        code, out, _err = self.cli_streams("doctor", "--json")
        return code, json.loads(out)["data"]


class TestDoctorFootprint(HomeIsolated):

    def test_a_large_claude_md_warns_and_is_labelled_an_estimate(self):
        """Positive control: 40,000 chars is ~10,000 tokens, over 6,000."""
        baseline_code, _out = self.cli("doctor")
        self.write("CLAUDE.md", "x" * 40000)
        code, out = self.cli("doctor")
        self.assertIn("## context footprint", out)
        self.assertIn("CLAUDE.md  40,000 chars  ~10,000 tokens", out)
        self.assertIn("estimate (chars/4)", out)
        self.assertIn("warn auto-loaded context is over ~6,000 tokens", out)
        self.assertEqual(code, baseline_code, "over the threshold is not fatal")
        _c, data = self.doctor_json()
        rows = [r for r in data["checks"] if r["section"] == "context-footprint"]
        self.assertEqual([r["status"] for r in rows], ["warn"])

    def test_a_small_claude_md_does_not_warn_and_keeps_the_exit_code(self):
        baseline_code, _out = self.cli("doctor")
        self.write("CLAUDE.md", "y" * 400)
        code, out = self.cli("doctor")
        self.assertIn("## context footprint", out)
        self.assertIn("CLAUDE.md  400 chars  ~100 tokens", out)
        self.assertNotIn("auto-loaded context is over", out)
        self.assertEqual(code, baseline_code)

    def test_the_threshold_is_configurable_and_garbage_means_the_default(self):
        self.write("CLAUDE.md", "y" * 400)
        real = config_mod.load
        with mock.patch.object(config_mod, "load", side_effect=lambda layout: dict(
                real(layout), footprint={"threshold_tokens": 50})):
            _code, out = self.cli("doctor")
        self.assertIn("over ~50 tokens", out, "a configured threshold is used")
        from ctx import commands
        self.assertEqual(commands._footprint_threshold(
            {"footprint": {"threshold_tokens": "lots"}}), 6000)
        self.assertEqual(commands._footprint_threshold(
            {"footprint": {"threshold_tokens": -1}}), 6000)
        self.assertEqual(commands._footprint_threshold({}), 6000)

    def test_big_dirs_are_an_info_line_pointing_at_deny_rules(self):
        (self.root / "node_modules").mkdir()
        _code, out = self.cli("doctor")
        self.assertIn("info node_modules", out)
        self.assertIn("permission deny rule", out)


class TestDoctorHooks(HomeIsolated):

    def test_missing_python3_warns_without_changing_the_exit_code(self):
        baseline_code, out = self.cli("doctor")
        self.assertNotIn("is not on PATH", out)
        with mock.patch.object(footprint, "python3_status",
                               return_value=(False, "`python3` is not on PATH")):
            code, out = self.cli("doctor")
        self.assertIn("## hooks", out)
        self.assertIn("warn `python3` is not on PATH", out)
        self.assertEqual(code, baseline_code)

    def test_rtk_on_path_is_one_info_line(self):
        with mock.patch.object(footprint, "rtk_on_path", return_value=False):
            baseline_code, out = self.cli("doctor")
        self.assertNotIn("rtk is on PATH", out)
        with mock.patch.object(footprint, "rtk_on_path", return_value=True):
            code, out = self.cli("doctor")
        self.assertIn("info rtk is on PATH; ctx does not configure it", out)
        self.assertIn("gate output may already be filtered", out)
        self.assertEqual(code, baseline_code)


class TestDoctorGateKeys(HomeIsolated):

    def test_a_bad_cap_is_named_and_the_default_reported(self):
        _code, out = self.cli("doctor")
        self.assertNotIn("gate.output_line_cap must be", out)
        real = config_mod.load
        with mock.patch.object(config_mod, "load", side_effect=lambda layout: dict(
                real(layout), gate=dict(real(layout)["gate"],
                                        output_line_cap="huge",
                                        output_char_cap=0))):
            code, out = self.cli("doctor")
        self.assertIn("warn gate.output_line_cap must be a positive integer", out)
        self.assertIn("default 2000 is used", out)
        self.assertIn("warn gate.output_char_cap must be a positive integer", out)
        self.assertIn("default 6000 is used", out)

    def test_values_the_loader_accepts_are_not_warned_about(self):
        """The loader reads "500" and 2.5 as positive ints, so doctor must not
        claim the default is used for them."""
        real = config_mod.load
        with mock.patch.object(config_mod, "load", side_effect=lambda layout: dict(
                real(layout), gate=dict(real(layout)["gate"],
                                        output_line_cap="500",
                                        output_char_cap=2.5))):
            _code, out = self.cli("doctor")
        self.assertNotIn("gate.output_line_cap must be", out)
        self.assertNotIn("gate.output_char_cap must be", out)


class TestDrift(HomeIsolated):

    def drifted_unit(self, status):
        directory = plan_mod.units_dir(self.layout, "p")
        directory.mkdir(parents=True, exist_ok=True)
        name = f"01-{status}"
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": "p", "tier": "subagent",
             "depends_on": [], "owns": ["a.py"], "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": status,
             "verify": [{"kind": "cmd", "run": "stale-command"}]},
            "## Objective\nx\n\n## Acceptance criteria\n1. y\n",
        ).write(directory / f"{name}.md")
        return name

    def test_a_finished_unit_prints_no_drift_warning(self):
        self.drifted_unit("done")
        _code, out = self.cli("doctor")
        self.assertNotIn("verify drift", out)
        self.assertNotIn("expected for finished work", out)

    def test_an_escalated_unit_prints_no_drift_warning(self):
        self.drifted_unit("escalated")
        _code, out = self.cli("doctor")
        self.assertNotIn("verify drift", out)

    def test_an_unfinished_unit_still_warns(self):
        """Positive control for the test above."""
        for status in ("pending", "running", "blocked", "verify_failed",
                       "active", "complete"):
            with self.subTest(status=status):
                name = self.drifted_unit(status)
                _code, out = self.cli("doctor")
                self.assertIn("## verify drift", out)
                self.assertIn(f"{name}.md", out)
                self.assertNotIn("expected for finished work", out)


class TestBudget(HomeIsolated):

    def snapshot(self):
        result = {}
        for base in (self.root, Path(self.untracked)):
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    p = Path(dirpath) / name
                    result[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return result

    def test_budget_prints_the_section_and_writes_nothing(self):
        self.write("CLAUDE.md", "z" * 4000)
        before = self.snapshot()
        code, out = self.cli("budget")
        after = self.snapshot()
        self.assertEqual(code, 0, out)
        self.assertIn("## auto-loaded context (estimate)", out)
        self.assertIn("CLAUDE.md  4,000 chars  ~1,000 tokens", out)
        self.assertTrue(before, "the snapshot saw files")
        self.assertEqual(before, after, "budget wrote to the filesystem")


class TestInitTrustPointer(HomeIsolated):

    def test_init_points_at_trust_and_never_accepts(self):
        """A fresh git clone carrying a ledger that declares a command: init
        prints it and the /ctx:trust line, and the trust store is untouched."""
        self.git_init()
        config = config_mod.load(self.layout)
        config["verify"] = [{"kind": "cmd", "run": "echo cloned-check"}]
        self.layout.config.write_text(config_mod.render(config), encoding="utf-8")
        store = trust.path_for(self.layout)
        self.assertFalse(store.exists(), "precondition: nothing accepted yet")

        code, out = self.cli("init")
        self.assertEqual(code, 0, out)
        self.assertIn("echo cloned-check", out)
        self.assertIn("To let the done-gate run these, review and accept them "
                      "with /ctx:trust", out)
        self.assertFalse(store.exists(), "init must never write a trust acceptance")
        self.assertFalse(trust.is_accepted(
            {"kind": "cmd", "run": "echo cloned-check"}, trust.load(self.layout)))

        code, out = self.cli("next")
        self.assertEqual(code, 0, out)
        self.assertIn("/ctx:trust", out)

    def test_a_verify_candidate_is_listed_once_and_gets_no_trust_pointer(self):
        """`verify_candidates` are not declared commands: `ctx next` cannot name
        /ctx:trust for them, so init must neither repeat them nor point there."""
        self.git_init()
        config = config_mod.load(self.layout)
        config["verify_candidates"] = [{"kind": "cmd", "run": "echo candidate-check"}]
        self.layout.config.write_text(config_mod.render(config), encoding="utf-8")

        code, out = self.cli("init")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("echo candidate-check"), 1, out)
        self.assertNotIn("To let the done-gate run these", out)

    def test_no_pointer_when_nothing_is_pending(self):
        """Positive control: an initialised ledger with no commands says nothing."""
        code, out = self.cli("init")
        self.assertEqual(code, 0, out)
        self.assertNotIn("/ctx:trust", out)

    def test_other_trust_advice_is_unchanged_past_l0(self):
        config = dict(self.config, verify=[{"kind": "cmd", "run": "echo x"}])
        command, _why = advice.next_action(self.layout, config, {"level": "2"})
        self.assertEqual(command, "ctx trust")
        command, why = advice.next_action(self.layout, config, {"level": "0"})
        self.assertEqual(command, "/ctx:trust")
        self.assertIn("will not run until", why)
        self.assertIn("waiting to be accepted", why)
        self.assertNotIn("found by init", why)


class TestStatusLabels(HomeIsolated):

    def test_the_wave_board_heading_carries_a_plain_label(self):
        directory = plan_mod.units_dir(self.layout, "p")
        directory.mkdir(parents=True, exist_ok=True)
        self.cli("level", "2")
        from ctx import state
        state.update(self.layout, plan="p")
        _code, out = self.cli("status")
        self.assertIn("wave board", out)
        self.assertIn("wave board (groups of units that can run at the same time)",
                      out)
