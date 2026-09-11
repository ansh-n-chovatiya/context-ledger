"""Gating a whole wave against one suite run — and every guarantee surviving it.

`ctx unit --status done` runs the unit's whole `verify` block, and a wave's
units nearly always declare the *same* `cmd` check: one suite, spawned once per
unit, over bytes that did not move between the runs. On a plan of this project's
own size that is around eighteen minutes, most of it re-derivation.

The saving is easy. Keeping it honest is the part worth testing, because the
rest of what the gate does is emphatically **not** shared: each unit has its own
`owns`, its own dispatch seal, its own findings, its own refusals and its own
line in the journal. So the sharing is done as a *result cache*, `_SharedCmd` in
`ctx.verify`, and `gate_check` itself is untouched — the batched path is the
per-unit path, with one subprocess replaced by the result it already produced.

Three conditions make the reuse a fact rather than an assumption, and this file
pins all three: the same command asked the same way, a tree that is identical to
the byte (`snapshot.tree_token`), and a command that was observed to leave that
tree alone. ERROR is never reused, because an ERROR is a verdict about the
machine and the machine is what the token cannot see.

The second half of the file is the one that matters. For every guarantee the
per-unit gate makes, there is a test that the batched path still refuses the
case the per-unit path refused — including a unit that wrote outside its `owns`
while gated in a batch of three.

Sharing is **off** unless `gate.share_cmd_results` or `CTX_GATE_SHARE` asks for
it, which `TestSharingIsOptIn` holds it to. A slower gate that is right beats a
faster one that might not be.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    contract as contract_mod, frontmatter, plan as plan_mod,
    trust as trust_mod, verify,
)
from support import Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
UNITS = ("01-api", "02-store", "03-cli")


class WaveFixture(Fixture):
    """A real repository, a plan of three concurrent units, gated one by one."""

    slug = "wave"

    def setUp(self):
        super().setUp()
        self.git_init()
        self.counter = Path(self._outside.name) / "runs.txt"
        # A developer who exported `CTX_GATE_SHARE` for a wave must not thereby
        # change what this file measures. `Fixture.tearDown` puts the
        # environment back, so clearing it here is local to the test.
        self.share(False)

    # -- a `cmd` check that counts its own invocations --------------------- #

    def counted(self, exit_code=0, touches=None):
        """A command that records having run — outside the tree, on purpose.

        The counter must not be part of what `tree_token` digests, or every run
        would invalidate the cache it is here to measure. `touches` names a path
        *inside* the tree, for the one test that needs a command with a side
        effect on the project.
        """
        body = f"import pathlib; pathlib.Path(r'{self.counter}').open('a').write('x')"
        if touches:
            body += f"; pathlib.Path(r'{self.root / touches}').open('a').write('y')"
        if exit_code:
            body += f"; raise SystemExit({exit_code})"
        return self.py(body)

    def runs(self):
        """How many times the counted command actually spawned."""
        return len(self.counter.read_text()) if self.counter.exists() else 0

    # -- the plan ---------------------------------------------------------- #

    def plan(self, checks_for=None, names=UNITS, wave=1):
        """Three units, each owning one file, each with the same suite check."""
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        shared = [{"kind": "diff"}, {"kind": "cmd", "run": self.counted()}]
        for name in names:
            checks = (checks_for or {}).get(name, shared)
            frontmatter.Document(
                {
                    "ctx_schema": 1, "unit": name, "plan": self.slug,
                    "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                    "reads": [], "forbid": [], "budget_tokens": 1000,
                    "status": "pending", "wave": wave, "verify": list(checks),
                },
                CRITERIA,
            ).write(directory / f"{name}.md")
            self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")

    def work(self, names=UNITS):
        """Each unit writes the one file it owns."""
        for name in names:
            self.write(f"src/{name}.py", f"# {name}\n")

    def ready(self, checks_for=None, names=UNITS):
        """Plan, do the work, land it, dispatch. In that order, deliberately.

        The work is committed *before* dispatch because of something a wave
        gated one unit at a time runs into that has nothing to do with sharing:
        `review.wave_scope` excuses a sibling's declared paths only while that
        sibling is not `done`, so the first `ctx unit --status done` narrows
        every later unit's scope, and units two and three then fail their own
        `diff` check on the first one's uncommitted work. Landing the wave's
        work first is the arrangement in which a wave can be gated at all —
        and it is unchanged by anything in this file.
        """
        self.plan(checks_for=checks_for, names=names)
        self.work(names)
        self.git("add", "-A")
        self.git("commit", "-qm", "the wave's work")
        self.dispatch()

    def dispatch(self):
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        return out

    def share(self, on=True):
        if on:
            os.environ["CTX_GATE_SHARE"] = "1"
        else:
            os.environ.pop("CTX_GATE_SHARE", None)

    def done(self, name, *extra):
        return self.cli("unit", name, "--status", "done", "--plan", self.slug, *extra)

    def gate_all(self, names=UNITS):
        """Gate every unit, one `ctx unit --status done` at a time."""
        return [self.done(name) for name in names]

    def status_of(self, name):
        return plan_mod.find_unit(self.layout, self.slug, name).status

    def edit(self, name, **changes):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        doc.write(path)

    def cache_entries(self):
        path = self.layout.runtime / verify.SHARE_CACHE_NAME
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8")).get("entries", {})

    def journal_text(self):
        return "\n".join(path.read_text(encoding="utf-8")
                         for path in sorted(self.layout.journal.glob("*.md")))

    def git(self, *args):
        completed = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True,
            env=dict(self._env, GIT_CONFIG_GLOBAL="/dev/null",
                     GIT_CONFIG_SYSTEM="/dev/null",
                     GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
                     GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com"),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()


# --------------------------------------------------------------------------- #
# criterion 4 — one suite run for the whole wave
# --------------------------------------------------------------------------- #

class TestOneRunForTheWave(WaveFixture):
    def test_three_gates_spawn_the_shared_suite_once(self):
        self.ready()
        self.share()
        codes = [code for code, _out in self.gate_all()]
        self.assertEqual(codes, [0, 0, 0])
        self.assertEqual([self.status_of(n) for n in UNITS], ["done"] * 3)
        self.assertEqual(self.runs(), 1,
                         "the identical suite ran once per unit, not once")

    def test_a_failing_suite_is_shared_too(self):
        """The refusal is the expensive case: three units, one red suite."""
        self.ready()
        self.share()
        for name in UNITS:
            self.edit(name, verify=[{"kind": "diff"},
                                    {"kind": "cmd", "run": self.counted(exit_code=1)}])
        self.trust([{"kind": "cmd", "run": self.counted(exit_code=1)}])
        # Re-seal, because editing a contract after dispatch is itself refused.
        self.cli("start", *sum([["--reseal", n] for n in UNITS], []))
        codes = [code for code, _out in self.gate_all()]
        self.assertEqual(codes, [1, 1, 1])
        self.assertEqual([self.status_of(n) for n in UNITS], ["running"] * 3)
        self.assertEqual(self.runs(), 1)

    def test_the_verdict_a_batched_unit_gets_is_the_one_it_earned(self):
        """A shared PASS is still reported against each unit by name."""
        self.ready()
        self.share()
        for name in UNITS:
            code, out = self.done(name)
            self.assertEqual(code, 0, out)
            self.assertIn(f"{name}: done", out)


class TestSharingIsOptIn(WaveFixture):
    def test_off_by_default_every_gate_runs_the_suite_itself(self):
        self.ready()
        self.gate_all()
        self.assertEqual(self.runs(), 3)
        self.assertEqual(self.cache_entries(), {},
                         "nothing is cached when nothing asked for caching")

    def test_the_project_can_ask_for_it_in_ctx_yaml(self):
        """`gate.share_cmd_results: true`, without an environment variable."""
        self.ready()
        config = dict(self.config)
        config["gate"] = dict(config.get("gate") or {}, share_cmd_results=True)
        self.assertIsNotNone(verify.sharing(self.layout, config))
        self.assertIsNone(verify.sharing(self.layout, self.config))

    def test_doctor_says_so_when_the_gate_may_reuse_a_result(self):
        """Visible from outside. A gate running in a cached mode is a fact an
        operator has to be able to read off `ctx doctor`, not infer."""
        self.share(False)
        self.assertNotIn("sharing=on", self.cli("doctor")[1])
        self.share(True)
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("sharing=on", out)
        self.assertIn("gate.share_cmd_results", out)

    def test_the_environment_asks_for_it_for_one_run(self):
        self.share()
        self.assertIsNotNone(verify.sharing(self.layout, self.config))
        self.share(False)
        self.assertIsNone(verify.sharing(self.layout, self.config))


# --------------------------------------------------------------------------- #
# what makes the reuse safe: the tree, and only the tree
# --------------------------------------------------------------------------- #

class TestTheCacheIsKeyedOnTheTree(WaveFixture):
    def test_a_changed_file_between_two_gates_runs_the_suite_again(self):
        self.ready()
        self.share()
        self.assertEqual(self.done("01-api")[0], 0)
        self.assertEqual(self.runs(), 1)
        # A byte moves in a file nobody's gate has looked at yet.
        self.write("src/02-store.py", "# 02-store, rewritten\n")
        self.assertEqual(self.done("02-store")[0], 0)
        self.assertEqual(self.runs(), 2,
                         "a different tree is a different question")

    def test_ledger_churn_between_gates_does_not_invalidate_anything(self):
        """Every ctx command writes to `.ctx/`; that is not the project changing.

        If the token counted the ledger, the cache would miss every single time
        and the whole mechanism would be dead code that looks alive.
        """
        self.ready()
        self.share()
        self.assertEqual(self.done("01-api")[0], 0)
        self.assertEqual(self.cli("journal", "note", "something")[0], 0)
        self.assertEqual(self.cli("status")[0], 0)
        self.assertEqual(self.done("02-store")[0], 0)
        self.assertEqual(self.runs(), 1)

    def test_a_command_that_changes_the_tree_is_never_shared(self):
        """It did not answer a question about the tree it left behind."""
        touching = [{"kind": "cmd", "run": self.counted(touches="build.log")}]
        self.ready(checks_for={name: touching for name in UNITS})
        self.share()
        self.gate_all()
        self.assertEqual(self.runs(), 3)
        self.assertEqual(self.cache_entries(), {})

    def test_an_error_is_never_shared(self):
        """A missing tool is a fact about this machine, not about the work."""
        missing = [{"kind": "cmd", "run": "ctx-no-such-tool-xyz --run"}]
        self.ready(checks_for={name: missing for name in UNITS})
        self.share()
        codes = [code for code, _out in self.gate_all()]
        self.assertEqual(codes, [1, 1, 1], "an all-ERROR gate is still refused")
        self.assertEqual(self.cache_entries(), {})

    def test_two_different_commands_do_not_share_a_result(self):
        checks = {
            "01-api": [{"kind": "cmd", "run": self.counted()}],
            "02-store": [{"kind": "cmd", "run": self.counted(exit_code=3)}],
            "03-cli": [{"kind": "cmd", "run": self.counted()}],
        }
        self.ready(checks_for=checks)
        self.share()
        codes = [code for code, _out in self.gate_all()]
        self.assertEqual(codes, [0, 1, 0], "02's own command decided 02's gate")
        self.assertEqual(self.runs(), 2, "01's result was reused by 03, not by 02")


# --------------------------------------------------------------------------- #
# criterion 5 & 6 — every per-unit guarantee, still refused in a batch
# --------------------------------------------------------------------------- #

class TestTheOwnsScopedDiffSurvives(WaveFixture):
    """Guarantee 1: each unit still gets its own `owns`-scoped diff check."""

    def test_a_unit_that_wrote_outside_its_owns_is_refused_in_a_batch_of_three(self):
        self.ready()
        # 02 writes a path no unit in the wave declared. The wave excuses a
        # sibling's declared paths and nothing else.
        self.write("src/rogue.py", "# nobody owns this\n")
        self.share()
        code, out = self.done("02-store")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.status_of("02-store"), "running")
        self.assertIn("src/rogue.py", out)
        self.assertIn("refusing to mark 02-store done", out)

    def test_the_batched_refusal_is_the_same_refusal_the_single_path_gives(self):
        """Run the identical scene twice: gated alone, then gated shared.

        The tree does not move between the two passes, so anything the batched
        path is willing to excuse would show up as a difference here.
        """
        self.ready()
        self.write("src/rogue.py", "# nobody owns this\n")
        self.share(False)
        alone = [out for _code, out in self.gate_all()]
        self.share(True)
        shared = [out for _code, out in self.gate_all()]
        self.assertEqual(alone, shared)
        for out in shared:
            self.assertIn("src/rogue.py", out)

    def test_a_scope_violation_still_short_circuits_before_the_suite(self):
        """`diff` is cheaper than `cmd` and fails first — batching changes
        neither the order nor the fact that the suite is never reached."""
        self.ready()
        self.write("src/rogue.py", "# nobody owns this\n")
        self.share()
        self.gate_all()
        self.assertEqual(self.runs(), 0)

    def test_a_committed_out_of_scope_edit_is_still_caught_in_a_batch(self):
        """Committing hides a change from `git status`, never from the gate."""
        self.ready()
        self.write("src/rogue.py", "# nobody owns this\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "hide it")
        self.share()
        code, out = self.done("03-cli")
        self.assertEqual(code, 1, out)
        self.assertIn("src/rogue.py", out)


class TestTheContractSealSurvives(WaveFixture):
    """Guarantee 2: each unit's contract is still compared against dispatch."""

    def test_only_the_unit_that_rewrote_its_contract_is_refused(self):
        self.ready()
        self.edit("02-store", owns=["src/02-store.py", "src/anything.py"])
        self.share()
        results = dict(zip(UNITS, self.gate_all()))
        self.assertEqual(results["02-store"][0], 1)
        self.assertIn("contract changed after it was dispatched",
                      results["02-store"][1])
        self.assertIn("changed: owns", results["02-store"][1])
        self.assertEqual(results["01-api"][0], 0)
        self.assertEqual(results["03-cli"][0], 0)
        self.assertEqual(
            [self.status_of(n) for n in UNITS], ["done", "running", "done"],
            "one unit's forged contract is one unit's problem, and not the wave's",
        )

    def test_a_unit_dispatched_around_ctx_is_still_refused_in_a_batch(self):
        """Guarantee 0: no dispatch seal, nothing to compare, not done."""
        self.ready()
        contract_mod.seal_path(self.layout, self.slug, "02-store").unlink()
        self.share()
        results = dict(zip(UNITS, self.gate_all()))
        self.assertEqual(results["02-store"][0], 1)
        self.assertIn("no dispatch seal", results["02-store"][1])
        self.assertEqual(results["01-api"][0], 0)


class TestTheFindingsReSealSurvives(WaveFixture):
    """Guarantee 3: a gate that passes re-seals the findings it saw."""

    def test_each_unit_in_the_batch_re_seals_its_own_findings(self):
        self.ready()
        for name in UNITS:
            code, out = self.cli("findings", name, "--plan", self.slug,
                                 "--add", "minor", "--summary", f"note for {name}")
            self.assertEqual(code, 0, out)
        self.share()
        self.assertEqual([code for code, _ in self.gate_all()], [0, 0, 0])
        for name in UNITS:
            with self.subTest(unit=name):
                sealed = contract_mod.load_seal(self.layout, self.slug, name)
                summaries = [entry.get("summary")
                             for entry in (sealed.get("findings") or {}).values()]
                self.assertEqual(summaries, [f"note for {name}"])

    def test_an_open_critical_finding_still_refuses_its_own_unit(self):
        """The `review` kind is per unit, and a shared suite does not speak for it."""
        with_review = [{"kind": "review"}, {"kind": "cmd", "run": self.counted()}]
        self.ready(checks_for={name: with_review for name in UNITS})
        self.cli("findings", "02-store", "--plan", self.slug, "--add", "critical",
                 "--summary", "wrong in a way that matters")
        self.share()
        results = dict(zip(UNITS, self.gate_all()))
        self.assertEqual(results["02-store"][0], 1)
        self.assertEqual(results["01-api"][0], 0)
        self.assertEqual(results["03-cli"][0], 0)
        self.assertEqual(self.status_of("02-store"), "running")


class TestTheAllErrorRefusalSurvives(WaveFixture):
    """Guarantee 4: a unit not one of whose checks could run is not done."""

    def test_the_unit_whose_only_check_cannot_run_is_refused_beside_two_that_pass(self):
        checks = {"02-store": [{"kind": "cmd", "run": "ctx-no-such-tool-xyz --run"}]}
        self.ready(checks_for=checks)
        self.share()
        results = dict(zip(UNITS, self.gate_all()))
        self.assertEqual(results["02-store"][0], 1)
        self.assertIn("not one check could run", results["02-store"][1])
        self.assertEqual(results["01-api"][0], 0)
        self.assertEqual(results["03-cli"][0], 0)

    def test_an_unaccepted_command_is_still_this_machine_s_answer(self):
        """Trust is asked before the cache is: a sibling's accepted run cannot
        launder a command this ledger never accepted."""
        self.ready()
        self.share()
        self.assertEqual(self.done("01-api")[0], 0)
        trust_mod.path_for(self.layout).unlink()
        code, out = self.done("02-store")
        self.assertIn("has not been accepted on this machine", out)
        self.assertNotIn("ok      cmd", out,
                         "a cached PASS must not stand in for acceptance")
        self.assertEqual(self.runs(), 1, "and it must not be run either")
        self.assertEqual(code, 0, out)  # today's behaviour: `diff` still passed


class TestThePerUnitTrailSurvives(WaveFixture):
    """Guarantee 5: one journal line per unit, and `--force` still says what
    it overrode."""

    def test_every_unit_in_the_batch_gets_its_own_journal_line(self):
        self.ready()
        self.share()
        self.gate_all()
        lines = [line for line in self.journal_text().splitlines()
                 if "unit" in line and "done" in line]
        self.assertEqual(len(lines), 3, lines)
        for name in UNITS:
            with self.subTest(unit=name):
                self.assertTrue(any(name in line for line in lines), lines)

    def test_a_refusal_in_a_batch_is_journalled_against_that_unit(self):
        self.ready()
        self.edit("02-store", owns=["src/02-store.py", "src/anything.py"])
        self.share()
        self.gate_all()
        text = self.journal_text()
        self.assertIn("done refused (contract edited after dispatch)", text)
        self.assertIn("02-store", text)

    def test_force_in_a_batch_still_records_what_it_overrode(self):
        self.ready()
        self.write("src/rogue.py", "# nobody owns this\n")
        self.share()
        code, out = self.done("02-store", "--force")
        self.assertEqual(code, 0, out)
        self.assertIn("--force overrode the done-gate for 02-store", out)
        self.assertIn("done (--force overrode the gate", self.journal_text())


if __name__ == "__main__":
    unittest.main()
