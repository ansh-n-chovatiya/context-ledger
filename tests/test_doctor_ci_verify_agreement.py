"""`ctx doctor` and `ctx ci` judge a `verify:` block the same way.

They did not. `cmd_doctor` counted a non-mapping entry as a problem; `cmd_ci`
filtered non-mappings out of the list *before* looking at it, and then — given
a block that held nothing else — printed "none configured" and exited 0. So a
ctx.yaml carrying

    verify:
      - "pytest -q"

made the command a developer runs exit 1 with `'pytest -q' is not a mapping`,
and the command a pipeline runs exit 0 with "all checks passed". The gate on
that project was empty in both readings; only one of the two said so, and it
was not the unattended one. ci also never asked `plan.kind_problem`, so a
typo'd `kind:` — dropped silently by `verify.ordered` at gate time — was
invisible to it as well.

The fix is one function, `commands.verify_entry_problem`, and both commands
call it. The tests below are written so that *re-diverging* fails them:

  * every malformed shape is driven through both commands in one loop, and the
    assertion is that the two agree — same problem text, same class of exit
    code — rather than that either prints something in particular;
  * the "none configured" wording is pinned as absent from a run whose block
    holds only unusable entries, because that exact sentence is what made the
    old ci sound healthy;
  * and the two bodies are read as source, to assert neither carries its own
    copy of the rule.
"""

import ast
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import commands, config as config_mod, miniyaml, verify  # noqa: E402
from support import Fixture  # noqa: E402


# Every shape that is not a usable check, and nothing that is. A bare string is
# the one from the report; the rest are the neighbouring mistakes — a scalar, a
# mapping with no `kind:` at all, and a `kind:` nothing registers.
UNUSABLE = [
    "pytest -q",
    5,
    {"run": "pytest -q"},
    {"kind": "rubrik", "about": "is it good?"},
]

# Judged by both commands without probing PATH or asking trust for anything:
# a `rubric` check has no command to run, so this control is about the
# validation rule and nothing else.
USABLE = {"kind": "rubric", "about": "does the page read clearly?"}


class VerifyBlockFixture(Fixture):

    def set_verify(self, entries):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8")) or {}
        data["verify"] = list(entries)
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        config_mod.reset_policy_warnings()
        return data["verify"]

    def doctor(self):
        return self.cli("doctor")

    def ci(self):
        return self.cli("ci")


# --------------------------------------------------------------------------- #
# the disagreement itself
# --------------------------------------------------------------------------- #

class TestTheTwoCommandsAgree(VerifyBlockFixture):

    def test_a_bare_string_entry_is_a_problem_to_both(self):
        """The report's case, verbatim."""
        self.set_verify(["pytest -q"])
        doctor_code, doctor_out = self.doctor()
        ci_code, ci_out = self.ci()

        self.assertEqual(doctor_code, 1, doctor_out)
        self.assertEqual(ci_code, 1, ci_out)
        for out in (doctor_out, ci_out):
            self.assertIn("'pytest -q' is not a mapping", out)

    def test_ci_no_longer_calls_an_unusable_block_none_configured(self):
        """The sentence that made the old ci sound healthy. An empty `verify:`
        still says it — that is a real state and a fine one at L0 — but a block
        holding an unusable entry is not an empty block."""
        self.set_verify([])
        self.assertIn("none configured", self.ci()[1])

        self.set_verify(["pytest -q"])
        code, out = self.ci()
        self.assertEqual(code, 1, out)
        self.assertNotIn("none configured", out)

    def test_every_unusable_shape_gets_the_same_answer_from_both(self):
        for entry in UNUSABLE:
            with self.subTest(entry=entry):
                self.set_verify([entry])
                problem = commands.verify_entry_problem(entry)
                self.assertTrue(problem, "the fixture entry must be unusable")
                doctor_code, doctor_out = self.doctor()
                ci_code, ci_out = self.ci()
                self.assertEqual(doctor_code, 1, doctor_out)
                self.assertEqual(ci_code, 1, ci_out)
                self.assertIn(problem, doctor_out)
                self.assertIn(problem, ci_out)

    def test_an_unregistered_kind_is_a_problem_to_ci_too(self):
        """ci never asked `plan.kind_problem`. `verify.ordered` drops an
        unregistered kind at gate time, so this is a gate with one fewer check
        in it than the file claims — and the headless command said nothing."""
        self.set_verify([{"kind": "rubrik", "about": "is it good?"}])
        code, out = self.ci()
        self.assertEqual(code, 1, out)
        self.assertIn("verify kind 'rubrik' is not registered", out)
        self.assertIn("rubric", out, "and it suggests the kind that was meant")

    def test_a_usable_block_is_a_problem_to_neither(self):
        """The control: both commands must be capable of saying yes, or the
        agreement above is an agreement to refuse everything."""
        self.set_verify([USABLE])
        doctor_code, doctor_out = self.doctor()
        ci_code, ci_out = self.ci()
        self.assertEqual(doctor_code, 0, doctor_out)
        self.assertEqual(ci_code, 0, ci_out)
        self.assertIn("all checks passed", ci_out)
        for out in (doctor_out, ci_out):
            self.assertNotIn("not a mapping", out)
            self.assertNotIn("not registered", out)

    def test_one_bad_entry_beside_a_good_one_fails_both(self):
        """The good entry must not launder the bad one — ci used to keep only
        the mappings and judge the block by what was left."""
        self.set_verify([USABLE, "pytest -q"])
        doctor_code, doctor_out = self.doctor()
        ci_code, ci_out = self.ci()
        self.assertEqual(doctor_code, 1, doctor_out)
        self.assertEqual(ci_code, 1, ci_out)
        for out in (doctor_out, ci_out):
            self.assertIn("'pytest -q' is not a mapping", out)


# --------------------------------------------------------------------------- #
# the rule they share
# --------------------------------------------------------------------------- #

class TestVerifyEntryProblem(unittest.TestCase):

    def test_a_usable_entry_has_no_problem(self):
        self.assertIsNone(commands.verify_entry_problem(USABLE))

    def test_every_registered_kind_is_accepted(self):
        for kind in verify.KIND_TABLE:
            with self.subTest(kind=kind):
                self.assertIsNone(commands.verify_entry_problem({"kind": kind}))

    def test_a_non_mapping_is_named_by_its_repr(self):
        self.assertEqual(commands.verify_entry_problem("pytest -q"),
                         "'pytest -q' is not a mapping")

    def test_a_missing_kind_is_reported_as_missing(self):
        problem = commands.verify_entry_problem({"run": "pytest -q"})
        self.assertIn("missing", problem)

    def test_the_mapping_check_comes_first(self):
        """`kind_problem` would raise on a string's `.get`; the order is not an
        accident and a reordering must fail here rather than at a user's."""
        self.assertIn("not a mapping", commands.verify_entry_problem([1, 2]))


class TestNeitherCommandKeepsItsOwnCopy(unittest.TestCase):
    """Source-level, because this is the property that decays. Both bodies
    calling the shared function is what stops the two from drifting apart the
    next time one of them learns a new rule."""

    def body(self, name):
        tree = ast.parse(inspect.getsource(commands))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} is not in ctx/commands.py")

    def calls(self, name):
        return {node.func.id for node in ast.walk(self.body(name))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    def test_both_commands_call_the_shared_rule(self):
        for command in ("cmd_doctor", "cmd_ci"):
            with self.subTest(command=command):
                self.assertIn("verify_entry_problem", self.calls(command))

    def test_neither_command_spells_the_rule_out_again(self):
        if not hasattr(ast, "unparse"):  # arrived in 3.9; this package claims 3.8
            self.skipTest("ast.unparse is not available on this interpreter")
        for command in ("cmd_doctor", "cmd_ci"):
            with self.subTest(command=command):
                source = ast.unparse(self.body(command))
                self.assertNotIn("is not a mapping", source)
                self.assertNotIn("kind_problem", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
