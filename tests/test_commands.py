"""The slash-command surface: does what the user types actually reach the CLI?

Everything else in this suite calls the CLI the way a well-behaved script would.
That is exactly the blind spot that let `/ctx:load «name»` ship broken — the
command file interpolated `$1`, which Claude Code never substitutes, so the
argument was silently dropped and argparse aborted the whole slash command.

So these tests read `commands/*.md` and drive the CLI the way that file says it
will be driven: loose words, no quoting, and sometimes nothing at all.
"""

import re
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Fixture  # noqa: E402

COMMANDS = Path(__file__).resolve().parent.parent / "commands"

# The `!`-prefixed line each command file runs before the model sees anything.
BANG = re.compile(r"^!`(.+)`\s*$", re.M)


def command_files():
    return sorted(COMMANDS.glob("*.md"))


def bang_line(path):
    match = BANG.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


class TestCommandFiles(unittest.TestCase):
    """Static checks on what the command files tell Claude Code to run."""

    def test_only_arguments_is_substituted(self):
        """`$1`, `$2`, … are never expanded by Claude Code — only `$ARGUMENTS`.

        A `$1` reaches bash unset and expands to nothing, so the command runs
        with no argument no matter what the user typed.
        """
        for path in command_files():
            body = path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"\$\{?[1-9]\}?", body),
                f"{path.name} uses a positional like $1; use $ARGUMENTS",
            )

    def test_every_bang_line_names_a_real_subcommand(self):
        from ctx.cli import build_parser

        known = set(build_parser()._subparsers._group_actions[0].choices)
        for path in command_files():
            line = bang_line(path)
            if line is None:
                continue
            tokens = shlex.split(line.replace("$ARGUMENTS", ""))
            self.assertGreaterEqual(len(tokens), 2, f"{path.name}: {line}")
            self.assertIn(tokens[1], known, f"{path.name} calls unknown `ctx {tokens[1]}`")

    def test_argument_hint_matches_what_the_cli_accepts(self):
        """A hint promising free prose must not hit a single-token positional."""
        for path in command_files():
            body = path.read_text(encoding="utf-8")
            hint = re.search(r"^argument-hint:\s*(.+)$", body, re.M)
            line = bang_line(path)
            if hint is None or line is None:
                continue
            if "«" not in hint.group(1):
                continue
            self.assertIn(
                "$ARGUMENTS", line,
                f"{path.name} advertises a name but does not pass $ARGUMENTS",
            )


class TestBareInvocation(Fixture):
    """`/ctx:foo` with nothing typed after it must not abort the command.

    Claude Code runs the `!` line first and gives up on the whole slash command
    if it exits non-zero, so the user never reaches the prompt that would have
    asked them for the missing name.
    """

    BARE = [
        ("task",), ("spec",), ("load",), ("promote",), ("save",),
        ("decide",), ("plan",), ("merge",), ("unit",), ("status",),
        ("list",), ("resume",), ("ask",), ("handoff",), ("drop",),
        # Nothing tracked yet, so these have nothing to act on. "Nothing to do"
        # is an answer, not a failure.
        ("verify",), ("start",), ("plan-check",), ("digest",), ("briefing",),
    ]

    def test_bare_commands_exit_zero(self):
        for argv in self.BARE:
            with self.subTest(command=argv[0]):
                code, out = self.cli(*argv)
                self.assertEqual(code, 0, f"ctx {argv[0]} exited {code}: {out}")
                self.assertTrue(out.strip(), f"ctx {argv[0]} said nothing")

    def test_missing_name_explains_itself(self):
        for name in ("task", "spec", "save", "decide", "plan"):
            with self.subTest(command=name):
                _code, out = self.cli(name)
                self.assertIn("no ", out.lower())
                self.assertIn("ctx " + name, out)

    def test_load_without_a_name_lists_what_exists(self):
        self.cli("save", "alpha")
        _code, out = self.cli("load")
        self.assertIn("alpha", out)


class TestNoActiveWork(Fixture):
    """Nothing tracked yet. Every command should say so, by name.

    `bundle.slugify("")` returns "context", so slugifying an empty fallback used
    to invent a plan called `context` and made every `if not slug` guard below
    it unreachable — `/ctx:start` reported problems with a plan nobody created.
    """

    def test_plan_commands_report_no_active_plan(self):
        for argv in (("start",), ("plan-check",), ("merge", "01-thing"),
                     ("unit", "01-thing"), ("plan-unit", "01-thing")):
            with self.subTest(command=argv[0]):
                code, out = self.cli(*argv)
                self.assertEqual(code, 0, out)
                self.assertIn("no active plan", out)
                self.assertNotIn("context", out)

    def test_spec_commands_report_no_active_spec(self):
        for argv in (("ask",), ("resolve", "--question", "q", "--answer", "a")):
            with self.subTest(command=argv[0]):
                code, out = self.cli(*argv)
                self.assertEqual(code, 0, out)
                self.assertIn("no active spec", out)

    def test_spec_ready_still_fails_with_no_spec(self):
        code, out = self.cli("spec-ready")
        self.assertEqual(code, 1, "a gate must not pass when there is no spec")
        self.assertIn("no active spec", out)


class TestLooseWords(Fixture):
    """`$ARGUMENTS` is spliced in unquoted, so names arrive as several tokens."""

    # Command files quote `$ARGUMENTS`, so the whole thing usually arrives as
    # one token; if a shell ever splits it, the loose-word form must work too.
    def _both_forms(self, *argv):
        return [(" ".join(argv),), argv]

    def test_kebab_first_word_names_the_task_and_the_rest_is_the_objective(self):
        for form in self._both_forms("add-auth", "let", "users", "log", "in"):
            with self.subTest(form=form):
                self.cli("task", *form, "--force")
                body = (self.layout.tasks / "add-auth.md").read_text(encoding="utf-8")
                self.assertIn("let users log in", body)

    def test_plain_words_stay_one_title(self):
        """`Fix Token Refresh` is a name, not `fix` plus an objective."""
        code, out = self.cli("task", "Fix Token Refresh")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.layout.task_file("fix-token-refresh").is_file())

    def test_spec_takes_a_dash_separated_intent(self):
        for form in self._both_forms("search-api", "—", "add", "a", "search", "endpoint"):
            with self.subTest(form=form):
                code, out = self.cli("spec", *form)
                self.assertEqual(code, 0, out)
                body = (self.layout.specs / "search-api" / "spec.md").read_text("utf-8")
                intent = body.split("## Intent", 1)[1].split("##", 1)[0]
                self.assertEqual(intent.strip(), "add a search endpoint")

    def test_decide_survives_an_apostrophe(self):
        code, out = self.cli("decide", "don't cache refresh tokens")
        self.assertEqual(code, 0, out)
        self.assertIn("don-t-cache-refresh-tokens", out)

    def test_save_and_load_round_trip_through_loose_words(self):
        code, _ = self.cli("save", "auth", "notes")
        self.assertEqual(code, 0)
        code, out = self.cli("load", "auth", "notes")
        self.assertEqual(code, 0)
        self.assertIn("auth-notes", out)


class TestPlanResolvesTheActiveSpec(Fixture):
    """A plan is rarely named after its spec; it should use the active one."""

    def test_plan_uses_the_active_spec_when_names_differ(self):
        self.cli("spec", "search-api", "--intent", "add search")
        code, out = self.cli("plan", "search-v1")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.layout.plans / "search-v1").is_dir())

    def test_plan_still_refuses_while_the_active_spec_is_blocked(self):
        self.cli("spec", "search-api", "--intent", "add search")
        self.cli("question", "search-api", "should results be paginated?")
        code, out = self.cli("plan", "search-v1")
        self.assertEqual(code, 1)
        self.assertIn("refusing to plan", out)


class TestUntrackedProject(Fixture):
    """No `.ctx/` yet. Guidance, not a shell failure — except for the gates."""

    def cli_outside(self, *args):
        import contextlib
        import io

        from ctx.cli import main as cli_main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_main(["--cwd", str(self.untracked), *args])
        return code, buffer.getvalue()

    def test_informational_commands_exit_zero_and_point_at_init(self):
        for name in ("status", "list", "resume", "load", "task", "ask"):
            with self.subTest(command=name):
                code, out = self.cli_outside(name)
                self.assertEqual(code, 0, f"ctx {name} exited {code}")
                self.assertIn("/ctx:init", out)

    def test_gates_still_fail(self):
        for name in ("verify", "ci", "spec-ready", "doctor"):
            with self.subTest(command=name):
                code, _out = self.cli_outside(name)
                self.assertNotEqual(code, 0, f"ctx {name} passed with no ledger")


if __name__ == "__main__":
    unittest.main()
