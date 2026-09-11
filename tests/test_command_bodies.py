"""The command bodies live in `ctx/commands.py`, and the move was invisible.

`cli.py` was 3,328 lines and imported twenty-one of the package's twenty-six
modules — the audit's "a quarter of the codebase" finding. Four earlier
extractions moved every *module-shaped* piece out of it and it still grew,
because what was left was the forty-one command bodies themselves. They are in
`ctx/commands.py` now and `cli.py` is 538 lines: argument parsing, the
registry, `_finish` and `main`.

Nothing else about this repository changed. That claim is not this file's to
prove — it is proved by the 1,376 tests that already covered this surface and
passed unedited, by `tests/test_commands_registry.py`'s generated 824-line
parser fixture, and by the seven JSON goldens in `tests/test_json_output.py`.
What *this* file holds is the shape of the split, which nothing else checks:

  * the bodies are in `commands.py` and none of them is back in `cli.py`;
  * the registry rows point at the moved functions, reached through `cli`'s
    globals the way `commands()` promises;
  * `cli.py` has a ceiling it cannot quietly grow back through;
  * the two mutable holders `main` and the bodies share are the *same objects*,
    not two copies that agree on the first run;
  * and `_CommandLayer` — writing `cli.<name>` still reaches the body, which is
    the property four suites' patch points depend on and the one thing a plain
    re-export would have silently taken away.

The import direction is asserted elsewhere, on purpose:
`tests/test_no_import_cycles.py` walks the whole package rather than this one
edge, and holds `cli` to being the sink nothing imports back.
"""

import ast
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import cli, commands  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "ctx"
CLI_PATH = PACKAGE / "cli.py"
COMMANDS_PATH = PACKAGE / "commands.py"

EXPECTED_BODIES = 41


def function_names(path):
    """Every top-level `def` in a module, by name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef)}


def re_exported_names():
    """The names `cli.py` pulls back out of `ctx.commands`.

    Read from the source rather than from `dir(cli)`, so the assertion is
    about the import statement a reader sees and not about whatever happens
    to be bound after import.
    """
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"), filename="cli.py")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 \
                and node.module == "commands":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


class TestTheBodiesMoved(unittest.TestCase):

    def test_all_forty_one_are_defined_in_commands(self):
        bodies = {name for name in function_names(COMMANDS_PATH)
                  if name.startswith("cmd_")}
        self.assertEqual(len(bodies), EXPECTED_BODIES, sorted(bodies))

    def test_cli_defines_none_of_them(self):
        """Re-exporting a name is not defining one. What must not come back is
        a `def cmd_…` in the entry point."""
        left = {name for name in function_names(CLI_PATH)
                if name.startswith("cmd_")}
        self.assertEqual(left, set(),
                         f"ctx/cli.py defines command bodies again: {sorted(left)}")

    def test_the_registry_names_exactly_those_bodies(self):
        """The registry is the other half: forty-one rows, forty-one bodies,
        and no function stranded in `commands.py` that nothing dispatches to."""
        rows = {entry.func.__name__ for entry in cli.commands()}
        bodies = {name for name in function_names(COMMANDS_PATH)
                  if name.startswith("cmd_")}
        self.assertEqual(rows, bodies)
        self.assertEqual(len(cli.commands()), EXPECTED_BODIES)

    def test_every_row_runs_the_function_that_moved(self):
        """`commands()` looks each `cmd_*` up in `cli`'s globals. Those globals
        are the re-export, so the row must reach the object in `ctx.commands` —
        if the two ever diverged, the parser would dispatch to one and every
        test that patches the other would be testing nothing."""
        for entry in cli.commands():
            with self.subTest(command=entry.name):
                name = entry.func.__name__
                self.assertIs(entry.func, getattr(commands, name))
                self.assertIs(entry.func, getattr(cli, name))
                self.assertEqual(entry.func.__module__, "ctx.commands")


class TestTheReExportIsNotACopy(unittest.TestCase):

    def test_every_re_exported_name_is_the_same_object(self):
        names = re_exported_names()
        self.assertGreaterEqual(len(names), EXPECTED_BODIES)
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertIs(getattr(cli, name), getattr(commands, name))

    def test_the_two_run_scoped_holders_are_shared_by_identity(self):
        """`main` clears `_ADVISED` and fills `_RENDER`; the bodies append to
        one and read the other. They are mutated in place and never rebound,
        so the layers must hold *the same* list and dict. Two equal copies
        would pass every `==` and lose every advisory notice and every `--json`
        document to the wrong module.
        """
        self.assertIs(cli._ADVISED, commands._ADVISED)
        self.assertIs(cli._RENDER, commands._RENDER)

    def test_an_advisory_recorded_by_a_body_is_seen_by_main(self):
        """The behavioural half of the above, without an argparse namespace."""
        before = list(commands._ADVISED)
        try:
            commands._ADVISED.clear()
            commands._advise("no-active-work")
            self.assertEqual(cli._ADVISED, ["no-active-work"])
        finally:
            commands._ADVISED[:] = before


class TestWritingThroughCliReachesTheBody(unittest.TestCase):
    """`_CommandLayer`, which is the one piece of machinery the move needed.

    Four suites install a replacement over a command or a private helper by
    assigning to `cli.<name>` — `cli.cmd_status = stub`, and two positive
    controls that put the pre-fix `_set_unit_status` back to prove the write it
    lost was really lost. Before the move those writes landed in the same
    module as the body. A plain re-export would rebind only the name in `cli`,
    leave the body calling the original, and turn a control that must fail into
    one that passes for no reason at all.
    """

    def test_a_name_the_bodies_define_is_forwarded(self):
        def replacement(*_args, **_kwargs):
            raise AssertionError("not meant to run")

        original = commands._set_unit_status
        cli._set_unit_status = replacement
        try:
            self.assertIs(commands._set_unit_status, replacement)
        finally:
            cli._set_unit_status = original
        self.assertIs(commands._set_unit_status, original)
        self.assertIs(cli._set_unit_status, original)

    def test_it_survives_a_patch_object_round_trip(self):
        """`unittest.mock` restores by assignment, so the restore forwards too
        and the next test does not inherit a stub."""
        original = commands.cmd_status

        def stub(_args):
            return 0

        with unittest.mock.patch.object(cli, "cmd_status", stub):
            self.assertIs(commands.cmd_status, stub)
        self.assertIs(commands.cmd_status, original)
        self.assertIs(cli.cmd_status, original)

    def test_a_name_only_the_entry_point_has_is_left_alone(self):
        """Forwarding is scoped to names `ctx.commands` already defines, so
        `cli`'s own state cannot leak into the command layer."""
        self.assertFalse(hasattr(commands, "JSON_SCHEMA"))
        original = cli.JSON_SCHEMA
        cli.JSON_SCHEMA = 99
        try:
            self.assertFalse(hasattr(commands, "JSON_SCHEMA"))
        finally:
            cli.JSON_SCHEMA = original

    def test_reads_are_ordinary(self):
        """No `__getattr__` fallback: a name that is not re-exported is absent
        here, so `mock.patch` cannot delete one that was never in `__dict__`."""
        self.assertTrue(hasattr(commands, "GITIGNORE"))
        self.assertFalse(hasattr(cli, "GITIGNORE"))


class TestTheEntryPointHasACeiling(unittest.TestCase):
    """`cli.py` may not grow back into the file it was.

    Unit 05 set this ceiling at 3,350 — the file as it then stood — and
    reported that the plan's 2,000-line target was unreachable from inside its
    scope, because what was left to move was the command bodies and neither
    `ctx/commands.py` nor the module registry in `tests/test_shared_paths.py`
    was its to write. That move is this unit, and the target is met with room
    to spare: 538 lines, of which the registry is most.

    The number below is the file as it is, plus enough slack for a comment.
    Lowering it as code leaves is the cheap win; raising it is the thing this
    test exists to make someone argue for.
    """

    CEILING = 560
    TARGET = 2000

    def test_cli_is_under_the_ceiling(self):
        lines = len(CLI_PATH.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            lines, self.CEILING,
            f"ctx/cli.py is {lines} lines. Extract, do not raise the ceiling.",
        )

    def test_the_ceiling_is_below_the_target_the_plan_named(self):
        """A ceiling above the goal is not a ceiling. This is what stops the
        constant above from being walked back to 2,000 one commit at a time."""
        self.assertLess(self.CEILING, self.TARGET)


if __name__ == "__main__":
    unittest.main()
