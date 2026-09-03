"""The whole flow, driven the way a session drives it.

Every other file in this suite tests one seam. Four waves of changes touched the
gate, detection, state, trust, the hooks and the CLI, and pieces that each pass
in isolation can still fail to compose — the trust boundary landing on top of the
per-check `cwd`, say, or `escalate` handing a spec to `plan` in a shape
`plan-check` rejects.

So this walks L0 → L1 → L2 → dispatch → merge in one repository, asserting at
each step the thing the level is *for*.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, journal, plan as plan_mod, state, trust, work,
)
from support import Fixture  # noqa: E402


class TestFullFlow(Fixture):
    """L0 through a merged wave, in one repository."""

    def setUp(self):
        super().setUp()
        self.git_init()
        # Re-init so detection sees a real project, and accept what it proposes.
        self.write("Makefile", "test:\n\t@echo ok\n")
        self.cli("init", "--force")

    def git(self, *args, cwd=None):
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                   GIT_CONFIG_SYSTEM="/dev/null")
        return subprocess.run(["git", *args], cwd=str(cwd or self.root),
                              capture_output=True, text=True, env=env, check=True)

    def test_the_whole_flow_composes(self):
        # ---- L0: detection landed on `code`, and init accepted its own commands
        config = __import__("ctx.config", fromlist=["config"]).load(self.layout)
        self.assertEqual(config["profile"], "code",
                         "a Makefile project is code, not docs")
        commands = [c for c in config["verify"] if c.get("kind") == "cmd"]
        self.assertTrue(commands, "init must configure a runnable gate")
        accepted = trust.load(self.layout)
        for check in commands:
            self.assertTrue(trust.is_accepted(check, accepted),
                            "init accepts what it proposed")

        code, out = self.cli("next")
        self.assertEqual(code, 0, out)
        self.assertIn("/ctx:task", out, "L0 with nothing recorded points at a task")

        # ---- L1: a task, gated
        code, out = self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        self.assertEqual(code, 0, out)
        self.assertIn("read as", out, "the name/objective split is visible")
        self.assertEqual(state.load(self.layout)["level"], "1")

        path = self.layout.task_file("add-auth")
        doc = frontmatter.read(path)
        doc.body = ("## Objective\nSSO sign-in works\n\n"
                    "## Acceptance criteria\n"
                    "1. A failed handshake surfaces AuthExpiredError\n"
                    "2. Refresh happens exactly once per expiry\n")
        doc.write(path)

        code, out = self.cli("verify")
        self.assertEqual(code, 0, f"the inherited gate passes on a clean tree: {out}")

        # ---- the gate really blocks when the work fails
        doc = frontmatter.read(path)
        doc.meta["verify"] = [{"kind": "cmd", "run": "exit 1"}]
        doc.write(path)
        self.trust(doc.meta["verify"])
        _code, stop_out = self.run_hook("Stop")
        self.assertIn('"decision": "block"', stop_out)

        # ...and a subagent finishing does not trigger it
        _code, sub_out = self.run_hook("SubagentStop")
        self.assertEqual(sub_out, "", "the gate belongs to the owning session")

        doc = frontmatter.read(path)
        doc.meta["verify"] = list(config["verify"])
        doc.write(path)

        # ---- L1 → L2: criteria come across rather than being retyped
        code, out = self.cli("escalate")
        self.assertEqual(code, 0, out)
        spec = (self.layout.specs / "add-auth" / "spec.md").read_text(encoding="utf-8")
        self.assertIn("AuthExpiredError", spec)
        self.assertEqual(state.load(self.layout)["level"], "2")

        # ---- the ambiguity gate holds planning shut
        self.cli("question", "add-auth", "Which IdP is authoritative?")
        code, out = self.cli("plan", "auth-rollout", "--spec", "add-auth")
        self.assertEqual(code, 1, "an unanswered blocking question refuses planning")
        self.assertIn("refusing to plan", out)

        code, out = self.cli("next")
        self.assertIn("/ctx:ask", out, "and `next` says so")

        self.cli("resolve", "add-auth", "--question", "IdP",
                 "--answer", "Okta, migrating off Auth0 later")
        code, _out = self.cli("spec-ready", "add-auth")
        self.assertEqual(code, 0)

        # ---- L2: a plan whose units own disjoint paths
        code, out = self.cli("plan", "auth-rollout", "--spec", "add-auth")
        self.assertEqual(code, 0, out)
        for name, owns, tier in (
            ("01-keys", "src/keys.py", "subagent"),
            ("02-clock", "src/clock.py", "session"),
        ):
            self.cli("plan-unit", name, "--plan", "auth-rollout",
                     "--tier", tier, "--owns", owns,
                     "--objective", f"build {name}")
        code, out = self.cli("plan-check", "auth-rollout")
        self.assertEqual(code, 0, out)
        self.assertIn("wave 1", out, "disjoint owners share a wave")

        # ---- a collision is caught rather than dispatched
        unit = plan_mod.find_unit(self.layout, "auth-rollout", "02-clock")
        unit.set(owns=["src/keys.py"])
        code, out = self.cli("plan-check", "auth-rollout")
        self.assertEqual(code, 1, "overlapping owners must refuse")
        self.assertIn("both own", out)
        unit.set(owns=["src/clock.py"])
        self.assertEqual(self.cli("plan-check", "auth-rollout")[0], 0)

        # ---- a command nobody here accepted does not run mid-flow
        keys = plan_mod.find_unit(self.layout, "auth-rollout", "01-keys")
        marker = self.root / "executed.txt"
        keys.set(verify=[{"kind": "cmd", "run": f"touch {marker}"}])
        code, out = self.cli("unit", "01-keys", "--status", "done")
        self.assertFalse(marker.exists(),
                         "an unaccepted command must not execute")
        self.assertIn("not been accepted", out)
        keys.set(verify=list(config["verify"]))

        # ---- dispatch
        code, out = self.cli("start", "--no-worktree")
        self.assertEqual(code, 0, out)
        self.assertIn("01-keys", out)
        self.assertIn("do not read source files", out.lower())

        # ---- `done` is earned, not asserted
        keys = plan_mod.find_unit(self.layout, "auth-rollout", "01-keys")
        keys.set(verify=[{"kind": "cmd", "run": "exit 1"}])
        self.trust([{"kind": "cmd", "run": "exit 1"}])
        code, out = self.cli("unit", "01-keys", "--status", "done")
        self.assertEqual(code, 1, "a failing unit cannot be marked done")
        self.assertEqual(
            plan_mod.find_unit(self.layout, "auth-rollout", "01-keys").status,
            "pending", "and nothing was written",
        )

        keys = plan_mod.find_unit(self.layout, "auth-rollout", "01-keys")
        keys.set(verify=list(config["verify"]))
        code, out = self.cli("unit", "01-keys", "--status", "done")
        self.assertEqual(code, 0, out)

        # ---- scope enforcement sees the shell
        self.cli("unit", "02-clock")
        self.run_hook("PreToolUse", tool_name="Bash",
                      tool_input={"command": "sed -i '' 's/a/b/' src/keys.py"})
        _code, nudge = self.run_hook("UserPromptSubmit")
        self.assertIn("outside the `owns` scope", nudge)

        # ---- the journal recorded the whole thing without injecting anything
        entries, _earlier = journal.tail(self.layout, 60)
        joined = "\n".join(entries)
        for expected in ("task", "level", "plan", "unit", "gate"):
            self.assertIn(expected, joined, f"{expected} was never journalled")

        # ---- and the briefing still fits its budget at every level
        code, out = self.cli("ci")
        self.assertIn("L0 briefing fits without truncation", out)
        self.assertIn("L2 briefing fits without truncation", out)

    def test_a_worktree_unit_merges_only_after_its_own_gate_passes(self):
        """The session tier, end to end: isolated checkout, gate run in that
        checkout, ownership enforced at merge."""
        directory = plan_mod.units_dir(self.layout, "auth")
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-keys", "plan": "auth", "tier": "session",
             "depends_on": [], "owns": ["src/keys.py"], "reads": [], "forbid": [],
             "budget_tokens": 1000, "status": "pending",
             "verify": [{"kind": "cmd", "run": self.py(
                 "import os, sys; sys.exit(0 if os.path.isfile(os.path.join('src', 'keys.py')) else 1)")}]},
            "## Objective\nBuild keys.\n\n## Acceptance criteria\n1. it exists\n",
        ).write(directory / "01-keys.md")
        self.trust([{"kind": "cmd", "run": self.py(
            "import os, sys; sys.exit(0 if os.path.isfile(os.path.join('src', 'keys.py')) else 1)")}])
        self.cli("plan", "auth", "--no-spec")
        self.assertEqual(self.cli("plan-check", "auth")[0], 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")

        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        self.assertIn("worktree", out.lower())

        wt = __import__("ctx.worktree", fromlist=["worktree"])
        tree = wt.path_for(self.layout, "01-keys")
        self.assertTrue(tree.is_dir(), "the unit got its own checkout")
        self.assertTrue((tree / ".ctx").is_dir(),
                        "and therefore its own runtime state")

        # Work outside `owns` is refused at merge, and nothing changes.
        (tree / "src").mkdir(parents=True, exist_ok=True)
        (tree / "src" / "keys.py").write_text("KEY = 1\n")
        (tree / "src" / "elsewhere.py").write_text("nope\n")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "work", cwd=tree)

        code, out = self.cli("merge", "01-keys", "--plan", "auth")
        self.assertEqual(code, 1)
        self.assertIn("outside its `owns` scope", out)
        self.assertEqual(
            plan_mod.find_unit(self.layout, "auth", "01-keys").status, "pending"
        )

        # Within scope, it lands and the worktree is cleaned up.
        self.git("rm", "-q", "src/elsewhere.py", cwd=tree)
        self.git("commit", "-qm", "scope", cwd=tree)
        code, out = self.cli("merge", "01-keys", "--plan", "auth")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / "src" / "keys.py").is_file())
        self.assertFalse(tree.exists(), "the worktree is removed on success")
        self.assertEqual(
            plan_mod.find_unit(self.layout, "auth", "01-keys").status, "done"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
