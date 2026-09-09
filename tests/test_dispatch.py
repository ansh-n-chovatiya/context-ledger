"""Dispatch tests — what the brief tells the orchestrator to do.

Two properties are load-bearing here and neither is decidable by reading prose.

**Every dispatch names a model.** A Task call that omits one inherits the
orchestrating session's model, which is the most capable and most expensive one
available. A wave of eight one-line units then books eight of the priciest seats
on the account, silently, and nothing in the output would say so.

**Nothing reaches git unless it was asked for.** A worktree holds its branch
exclusively: while one exists, `git checkout` of that branch in the main tree is
refused. Creating worktrees by default therefore took away the tree the user
actually tests in, which is the opposite of what the tier is for.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import config as config_mod, dispatch, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class DispatchFixture(Fixture):
    slug = "auth-rotation"

    def unit(self, name, *, tier="subagent", owns=(), depends_on=(), model=None):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
            "depends_on": list(depends_on), "owns": list(owns), "reads": [],
            "forbid": [], "budget_tokens": 45000, "status": "pending",
            "verify": list(CHECK),
        }
        if model:
            meta["model"] = model
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        self.trust(CHECK)
        return path

    def brief(self, *args):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("start", *args)
        self.assertEqual(code, 0, out)
        return out


class TestModelSelection(DispatchFixture):
    def test_every_concurrent_dispatch_names_a_model(self):
        self.unit("01-a", owns=["src/a.py"])
        self.unit("02-b", owns=["src/b.py"])
        out = self.brief()
        default = config_mod.DEFAULTS["models"]["runner"]
        for name in ("01-a", "02-b"):
            line = next(l for l in out.splitlines() if l.startswith(f"- `{name}`"))
            self.assertIn(default, line, line)

    def test_a_unit_may_override_the_model_for_its_own_work(self):
        self.unit("01-cheap", owns=["src/a.py"])
        self.unit("02-hard", owns=["src/b.py"], model="opus")
        out = self.brief()
        cheap = next(l for l in out.splitlines() if l.startswith("- `01-cheap`"))
        hard = next(l for l in out.splitlines() if l.startswith("- `02-hard`"))
        self.assertIn(config_mod.DEFAULTS["models"]["runner"], cheap)
        self.assertIn("opus", hard)

    def test_the_brief_says_why_an_omitted_model_is_not_free(self):
        self.unit("01-a", owns=["src/a.py"])
        out = self.brief()
        self.assertIn("inherits this session's model", out)

    def test_config_overrides_the_built_in_default(self):
        self.assertEqual(dispatch.model_for({"models": {"runner": "haiku"}}), "haiku")

    def test_a_unit_model_beats_config(self):
        class FakeUnit:
            model = "opus"

        self.assertEqual(
            dispatch.model_for({"models": {"runner": "haiku"}}, FakeUnit()), "opus"
        )

    def test_roles_are_resolved_separately(self):
        config = config_mod.DEFAULTS
        self.assertEqual(
            dispatch.model_for(config, role="reviewer"), config["models"]["reviewer"]
        )

    def test_the_models_block_reaches_the_generated_config(self):
        """A default that only lives in Python is a default nobody can retune."""
        text = self.layout.config.read_text(encoding="utf-8")
        self.assertIn("models:", text)


class TestGitIsOnlyTouchedOnRequest(DispatchFixture):
    def test_session_units_default_to_the_main_tree(self):
        self.unit("01-writer", tier="session", owns=["src/w.py"])
        out = self.brief()
        self.assertIn("run them in this tree", out)
        self.assertIn("leaves git alone unless asked", out)

    def test_the_main_tree_path_still_arms_the_gate(self):
        self.unit("01-writer", tier="session", owns=["src/w.py"])
        out = self.brief()
        self.assertIn("ctx unit 01-writer", out)
        self.assertIn("CTX_UNIT=01-writer", out)

    def test_opting_in_is_advertised_with_its_cost(self):
        self.unit("01-writer", tier="session", owns=["src/w.py"])
        out = self.brief()
        self.assertIn("--worktree", out)
        self.assertIn("git refuses to check it out here", out,
                      "the branch lock is the whole reason this is not the default")


if __name__ == "__main__":
    unittest.main()
