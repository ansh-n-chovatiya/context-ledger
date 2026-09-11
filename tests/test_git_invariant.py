"""The git invariant, made mechanical.

Context-Ledger may create and delete temporary worktrees and temporary branches
as disposable scaffolding. It must never create commits, amend, merge, rebase,
reset, restore, checkout, switch, cherry-pick, stash, push or pull — unless the
user explicitly asked for that operation. Until now that principle lived in
prose, which is the one place a regression cannot be caught.

Everything here asserts against real `subprocess` git output in real throwaway
repositories. A mock would confirm that the calls we thought about are absent
and say nothing about the ones we did not — and the whole risk is the call
nobody thought about. The unit of assertion is a *snapshot*: HEAD, the commit
graph, every branch, the stash, the working tree and the worktree list, taken
before and after, and compared apart from an explicitly named set of allowed
differences.

The ledger's own writes under `.ctx/` are the one standing allowance. They are
files ctx is *for*, they never touch git history, and every command makes them —
so `git status` is compared with `.ctx/` paths filtered out and everything else
compared verbatim.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, hooks, journal, plan as plan_mod, trust, verify,
    worktree as wt,
)
from support import OK, Fixture  # noqa: E402


# Every facet of git state a snapshot records. `commits` is redundant given
# `log`, and is kept separate anyway: "how many commits exist" is the question
# the invariant is actually about, and a failure that names it reads better than
# a diff of two log listings.
FACETS = ("head", "commits", "log", "branches", "stash", "status", "worktrees")

LEDGER = (".ctx/",)


class GitState:
    """Snapshot and comparison helpers. Mixed into the fixtures below."""

    def git_env(self):
        """Git with the developer's own configuration out of the way.

        A global `commit.gpgsign`, `merge.ff` or hook path would otherwise make
        these assertions depend on the machine running them.
        """
        return dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                    GIT_CONFIG_SYSTEM="/dev/null")

    def git_out(self, *args, cwd=None):
        """Combined output of one git command. Never raises: a snapshot has to
        work in a directory that is not a repository.

        Only trailing whitespace is trimmed. Leading whitespace is data here —
        a porcelain status line for an unstaged edit begins with a space, and
        stripping it moves every path three characters left.
        """
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, env=self.git_env(),
        )
        return ((completed.stdout or "") + (completed.stderr or "")).rstrip()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, env=self.git_env(), check=True,
        )

    def snapshot(self, cwd=None):
        """Complete git state, as text, for later comparison."""
        return {
            "head": self.git_out("rev-parse", "HEAD", cwd=cwd),
            "commits": self.git_out("rev-list", "--count", "--all", cwd=cwd),
            "log": self.git_out("log", "--oneline", "--all", cwd=cwd),
            "branches": self.git_out("branch", "-a", cwd=cwd),
            "stash": self.git_out("stash", "list", cwd=cwd),
            "status": self.git_out(
                "status", "--porcelain", "--untracked-files=all", cwd=cwd
            ),
            "worktrees": self.git_out("worktree", "list", "--porcelain", cwd=cwd),
        }

    @staticmethod
    def visible_status(text, ignored):
        """Porcelain lines whose path is not under one of `ignored`."""
        kept = []
        for line in text.splitlines():
            entry = line[3:].strip().strip('"')
            if " -> " in entry:  # renames report "old -> new"
                entry = entry.split(" -> ", 1)[1]
            if any(entry.startswith(prefix) for prefix in ignored):
                continue
            kept.append(line)
        return "\n".join(kept)

    def assert_git_unchanged(self, before, after, *, allow=(), ignoring=LEDGER,
                             note=""):
        """Two snapshots agree, except on the facets named in `allow`.

        `allow` is deliberately an explicit list rather than a default: a test
        that permits `head` to move is a test that has decided the user asked
        for that, and the decision is visible at the call site.
        """
        for facet in FACETS:
            if facet in allow:
                continue
            old, new = before[facet], after[facet]
            if facet == "status":
                old = self.visible_status(old, ignoring)
                new = self.visible_status(new, ignoring)
            self.assertEqual(
                old, new,
                f"git {facet} changed" + (f" — {note}" if note else ""),
            )

    def assert_no_stash(self, snap):
        self.assertEqual(snap["stash"], "", "ctx must never stash the user's work")


class GitInvariantFixture(GitState, Fixture):
    """A throwaway project that is also a real git repository with one commit."""

    slug = "auth"

    def setUp(self):
        super().setUp()
        self.git_init()

    # -- ledger scaffolding ------------------------------------------------- #

    def gate(self, checks, task):
        """Point a task file at specific verify checks, and accept them."""
        path = self.layout.task_file(task)
        doc = frontmatter.read(path)
        doc.meta["verify"] = list(checks)
        doc.write(path)
        self.trust(checks)
        return checks

    def unit(self, name, *, owns, tier="session", depends_on=(), checks=None):
        checks = [{"kind": "cmd", "run": OK}] if checks is None else checks
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": tier,
                "depends_on": list(depends_on), "owns": list(owns), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": list(checks),
            },
            f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / f"{name}.md")
        self.trust(checks)
        return directory / f"{name}.md"

    def plan_ready(self):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("plan-check", self.slug)
        self.assertEqual(code, 0, out)
        # The plan is tracked, so commit it: this is the *user's* commit, made
        # before the snapshots below, and it is what makes the integration tree
        # clean enough for the worktree tier to be exercised at all.
        self.git("add", "-A")
        self.git("commit", "-qm", "plan")

    def contents(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# A and B — the ordinary path writes no history at all
# --------------------------------------------------------------------------- #

# A gate that reads the work rather than a marker file, so driving it does not
# itself change which paths appear in `git status`. That matters: these tests
# compare the working tree before and after, and a marker would show up as the
# very difference they are looking for.
def reads_work(fixture):
    return [{"kind": "cmd", "run": fixture.py(
        "import sys; sys.exit(0 if 'return True' in open('src/app.py').read() else 1)"
    )}]


FAILING_WORK = "def ok():\n    return False\n"
PASSING_WORK = "def ok():\n    return True\n"


class TestTrackedWorkCreatesNoHistory(GitInvariantFixture):
    def setUp(self):
        super().setUp()
        code, out = self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        self.assertEqual(code, 0, out)
        self.gate(reads_work(self), "add-auth")

    def test_a_normal_tracked_change_creates_no_history(self):
        """The everyday path — track a change, edit, let the gate run — must
        leave git exactly as it found it.

        This is the test that catches the tempting shortcut: a gate that
        `git stash`es to get a clean tree, an autosave that commits the ledger,
        a hook that checks out a pristine copy to diff against. Any of those
        would pass every other test in the suite and quietly rewrite the user's
        working state.
        """
        self.write("src/app.py", FAILING_WORK)
        before = self.snapshot()

        code, out = self.run_hook("Stop")
        self.assertEqual(code, 0)
        self.assertIn('"decision": "block"', out, "the gate must actually engage")

        self.write("src/app.py", PASSING_WORK)
        code, out = self.run_hook("Stop")
        self.assertEqual(code, 0)
        self.assertEqual(out, "", "a passing gate lets the session end")

        after = self.snapshot()
        self.assert_git_unchanged(before, after, note="a tracked change was made")
        self.assert_no_stash(after)
        self.assertEqual(
            self.contents("src/app.py"), PASSING_WORK,
            "the user's uncommitted edit survives byte for byte",
        )
        self.assertIn(
            "?? src/app.py", after["status"],
            "and it is still uncommitted — the gate did not commit it for them",
        )


class TestRepeatedRoundsCreateNoHistory(GitInvariantFixture):
    def test_repeated_gate_rounds_create_no_history(self):
        """Three fail-then-fix rounds, checked after every one.

        Asserting only at the end would miss a write that a later round undoes,
        and it would miss the shape that actually worries us: something that
        fires once per attempt — a snapshot commit taken so the gate can diff
        against it, say — and is cleaned up on the way out.
        """
        self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        self.gate(reads_work(self), "add-auth")
        self.write("src/app.py", PASSING_WORK)
        before = self.snapshot()

        for round_number in range(1, 4):
            self.write("src/app.py", FAILING_WORK)
            _code, out = self.run_hook("Stop")
            self.assertIn('"decision": "block"', out, f"round {round_number}")
            self.assert_git_unchanged(
                before, after := self.snapshot(),
                note=f"round {round_number}, gate failing",
            )
            self.assert_no_stash(after)

            self.write("src/app.py", PASSING_WORK)
            _code, out = self.run_hook("Stop")
            self.assertEqual(out, "", f"round {round_number} should end clean")
            self.assert_git_unchanged(
                before, after := self.snapshot(),
                note=f"round {round_number}, gate passing",
            )
            self.assert_no_stash(after)

        self.assertEqual(self.contents("src/app.py"), PASSING_WORK)


# --------------------------------------------------------------------------- #
# C and D — a worktree is scaffolding, and cleanup is not destruction
# --------------------------------------------------------------------------- #

class TestWorktreesAreScaffolding(GitInvariantFixture):
    def test_a_temporary_worktree_leaves_no_history_behind(self):
        """Create and discard a worktree; the user's history is untouched.

        The allowance is exactly two facets — the branch list and the worktree
        list — and both are asserted to come back to where they started. HEAD,
        the commit graph and the commit count are never allowed to move, which
        is the difference between scaffolding and history.
        """
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        before = self.snapshot()

        code, out = self.cli("start", "--worktree")
        self.assertEqual(code, 0, out)

        during = self.snapshot()
        branch = wt.branch_for(self.slug, "01-a")
        self.assertTrue(wt.path_for(self.layout, self.slug, "01-a").is_dir(), "a tree appeared")
        self.assertIn(branch, during["branches"], "and so did its branch")
        self.assertIn(branch, during["worktrees"], "git knows it as a worktree")
        self.assert_git_unchanged(
            before, during, allow=("branches", "worktrees"),
            note="creating a worktree must not touch history",
        )
        self.assertEqual(
            before["head"], during["head"], "the user's checkout did not move"
        )

        code, out = self.cli("worktree", "remove", "01-a")
        self.assertEqual(code, 0, out)

        after = self.snapshot()
        self.assertFalse(wt.path_for(self.layout, self.slug, "01-a").exists(), "tree is gone")
        self.assertNotIn(branch, after["branches"], "branch is gone")
        self.assert_git_unchanged(
            before, after, note="a worktree round trip must be a no-op",
        )
        self.assert_no_stash(after)

    def test_cleanup_cannot_destroy_uncommitted_work(self):
        """`worktree remove` without `--force` refuses, and the work survives.

        Deleting a directory is how a failed unit is thrown away, so the delete
        path is the one place ctx can lose work that nothing else can recover.
        It has to refuse by default and destroy only when told to in so many
        words.
        """
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        self.assertEqual(self.cli("start", "--worktree")[0], 0)

        tree = wt.path_for(self.layout, self.slug, "01-a")
        work = tree / "src" / "a.py"
        work.parent.mkdir(parents=True, exist_ok=True)
        work.write_text("PRECIOUS = 1\n", encoding="utf-8")

        code, out = self.cli("worktree", "remove", "01-a")
        self.assertEqual(code, 1, out)
        self.assertIn("could not remove", out)
        self.assertIn("--force", out, "and it names the flag that would destroy it")
        self.assertTrue(tree.is_dir(), "the worktree survives a refused removal")
        self.assertEqual(
            work.read_text(encoding="utf-8"), "PRECIOUS = 1\n",
            "and so does the uncommitted work inside it",
        )

        code, out = self.cli("worktree", "remove", "01-a", "--force")
        self.assertEqual(code, 0, out)
        self.assertFalse(tree.exists(), "the explicit flag is what destroys it")


# --------------------------------------------------------------------------- #
# E — the explicit path is still allowed
# --------------------------------------------------------------------------- #

class TestTheExplicitMergeStillWorks(GitInvariantFixture):
    def test_an_asked_for_merge_really_merges(self):
        """`ctx merge` is a git write the user asked for by name.

        Without this the invariant could be satisfied by never calling git at
        all, which would be a different product. The boundary is *automatic
        versus asked for*, so the asked-for side has to be shown working, and
        the resulting commit has to be attributable to the command that made it.
        """
        self.unit("01-a", owns=["src/a.py"])
        self.plan_ready()
        self.assertEqual(self.cli("start", "--worktree")[0], 0)

        tree = wt.path_for(self.layout, self.slug, "01-a")
        (tree / "src").mkdir(parents=True, exist_ok=True)
        (tree / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "unit work", cwd=tree)

        before = self.snapshot()
        code, out = self.cli("merge", "01-a")
        self.assertEqual(code, 0, out)
        after = self.snapshot()

        self.assertNotEqual(before["head"], after["head"], "HEAD advanced")
        self.assertEqual(
            self.git_out("log", "-1", "--pretty=%s"),
            f"Merge unit 01-a of plan {self.slug}",
            "the commit says which command made it",
        )
        parents = self.git_out("rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(len(parents), 3, "a real merge commit, not a fast-forward")
        self.assertIn(before["head"], parents, "it builds on where the user was")
        self.assertEqual(
            int(after["commits"]) - int(before["commits"]), 1,
            "exactly one commit was created, and it is the merge",
        )
        self.assertTrue((self.root / "src" / "a.py").is_file(), "the work landed")
        self.assert_no_stash(after)


# --------------------------------------------------------------------------- #
# F — nothing that runs without being asked reaches a git write
# --------------------------------------------------------------------------- #

# Hook payloads for events that need one. Every event in `hooks.HANDLERS` is
# driven, whether or not it appears here, so a newly registered hook is covered
# the moment it is added — and only needs an entry here if it reads a payload.
HOOK_PAYLOADS = {
    "PreToolUse": {"tool_name": "Edit", "tool_input": {"file_path": "src/app.py"}},
    "PostToolUse": {"tool_name": "Edit", "tool_input": {"file_path": "src/app.py"}},
    "SessionEnd": {"reason": "clear"},
}

# Subcommands a session runs without the user authorising anything in
# particular — reporting, inspection, and the two that only print a brief. Add a
# row when a subcommand is added; a row is a claim that it writes no git state.
AUTOMATIC_COMMANDS = (
    ("status",),
    ("verify",),
    ("doctor",),
    ("ci",),
    ("next",),
    ("resume",),
    ("journal", "note", "probe"),
    ("budget",),
    ("start",),
    ("unit", "01-a"),
    ("plan-check", "auth"),
)


class TestNoAutomaticPathWritesGit(GitInvariantFixture):
    """The load-bearing test: everything that runs unasked, run for real.

    Each entry point is driven against a repository at L2 with an active plan,
    an active unit and an active task — the state in which ctx is doing the most
    — and the whole git snapshot is compared before and after. Nothing is
    allowed to differ except paths under `.ctx/`, which is the ledger writing
    itself down and touches no git state.
    """

    def setUp(self):
        super().setUp()
        self.unit("01-a", owns=["src/app.py"])
        self.plan_ready()
        self.write("src/app.py", "A = 1\n")
        self.assertEqual(self.cli("task", "probe", "--objective", "probe it")[0], 0)
        self.gate([{"kind": "cmd", "run": OK}], "probe")
        self.assertEqual(self.cli("unit", "01-a")[0], 0)

    def entry_points(self):
        for event in sorted(hooks.HANDLERS):
            payload = HOOK_PAYLOADS.get(event, {})
            yield f"hook {event}", lambda e=event, p=payload: self.run_hook(e, **p)
        for argv in AUTOMATIC_COMMANDS:
            yield "ctx " + " ".join(argv), lambda a=argv: self.cli(*a)

    def test_no_automatic_entry_point_reaches_a_git_write(self):
        before = self.snapshot()
        for label, drive in self.entry_points():
            with self.subTest(entry_point=label):
                drive()
                self.assert_git_unchanged(
                    before, after := self.snapshot(), note=label,
                )
                self.assert_no_stash(after)

    def test_the_fixture_is_actually_at_l2_with_work_active(self):
        """A guard on the test above: driven against an idle L0 ledger every
        hook returns immediately and the assertions prove nothing."""
        from ctx import state, work

        current = state.load(self.layout)
        self.assertEqual(current["level"], "2")
        self.assertEqual(current["plan"], self.slug)
        self.assertEqual(current["unit"], "01-a")
        self.assertEqual(current["task"], "probe")
        self.assertIsNotNone(work.active(self.layout, current), "the gate has work")


# --------------------------------------------------------------------------- #
# G — an unavailable git is an error, never a pass
# --------------------------------------------------------------------------- #

class TestUnavailableGitIsNeverAPass(Fixture):
    """The fixture's `.git` is a plain directory, so every git call fails —
    which is what a project with no VCS looks like to the gate."""

    def test_a_scope_check_that_could_not_run_is_an_error(self):
        """An ERROR beside a PASS must not collapse into a PASS.

        Outside a repository the `diff` kind cannot run, so the `owns` scope
        check — the one mechanical guarantee the parallel tiers rest on — is
        silently absent. Reporting that as green is how the gate came to sign
        off on work it had never checked.
        """
        checks = [
            {"kind": "cmd", "run": OK},
            {"kind": "diff", "owns": ["src/"]},
        ]
        self.trust(checks)
        results, verdict = verify.run(
            self.layout, self.config, checks, cwd=self.root, key="k",
        )
        by_kind = {r.kind: r for r in results}
        self.assertEqual(by_kind["cmd"].status, verify.PASS)
        self.assertEqual(by_kind["diff"].status, verify.ERROR)
        self.assertIn("not a git repository", by_kind["diff"].message)
        self.assertEqual(verdict, verify.ERROR)
        self.assertNotEqual(verdict, verify.PASS, "an unrun check is not a pass")

    def test_the_stop_hook_journals_it_as_incomplete_rather_than_a_pass(self):
        """Infrastructure is not the work's fault, so the gate does not block —
        but it must not record a pass it never earned either."""
        self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        checks = [
            {"kind": "cmd", "run": OK},
            {"kind": "diff", "owns": ["src/"]},
        ]
        path = self.layout.task_file("add-auth")
        doc = frontmatter.read(path)
        doc.meta["verify"] = list(checks)
        doc.write(path)
        self.trust(checks)

        code, out = self.run_hook("Stop")
        self.assertEqual(code, 0)
        self.assertEqual(out, "", "a configuration failure never blocks a session")

        entries, _earlier = journal.tail(self.layout, 60)
        gate_lines = [line for line in entries if "gate" in line]
        self.assertTrue(gate_lines, "the gate ran and said something")
        joined = "\n".join(gate_lines)
        self.assertIn("incomplete", joined)
        self.assertNotIn("pass", joined, "it signed nothing, so it recorded nothing")


# --------------------------------------------------------------------------- #
# H — a verify command cannot smuggle in a git write
# --------------------------------------------------------------------------- #

class TestVerifyCommandsCannotSmuggleGitWrites(GitInvariantFixture):
    """`verify.cmd.run` is shell out of a committed file, executed by the Stop
    hook — where no permission prompt appears. Trust is the whole boundary."""

    def smuggler(self, marker):
        """A command that would leave two kinds of evidence if it ever ran: a
        file on disk, and a commit in the user's history."""
        return {"kind": "cmd", "run": self.py(
            "import pathlib, subprocess; "
            f"pathlib.Path('{marker}').write_text('ran'); "
            "subprocess.run(['git', 'commit', '--allow-empty', '-m', 'smuggled'])"
        )}

    def test_an_unaccepted_command_does_not_execute(self):
        self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        check = self.smuggler("smuggled.txt")
        path = self.layout.task_file("add-auth")
        doc = frontmatter.read(path)
        doc.meta["verify"] = [check]
        doc.write(path)
        # Deliberately not trusted: this stands in for a `ctx.yaml` that arrived
        # with a clone rather than one a developer here wrote.
        self.assertFalse(trust.is_accepted(check, trust.load(self.layout)))

        before = self.snapshot()
        code, out = self.run_hook("Stop")
        self.assertEqual(code, 0)
        self.assertEqual(out, "", "an unaccepted command is a config error, not a block")

        self.assertFalse(
            (self.root / "smuggled.txt").exists(),
            "the command left no evidence, so it never ran",
        )
        self.assert_git_unchanged(before, self.snapshot(), note="unaccepted command")

    def test_acceptance_is_what_lets_a_command_run(self):
        """The counterpart: without it, the test above passes even if the gate
        never ran anything at all."""
        self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        check = {"kind": "cmd", "run": self.py(
            "import pathlib; pathlib.Path('accepted-ran.txt').write_text('ran')"
        )}
        self.gate([check], "add-auth")

        self.run_hook("Stop")
        self.assertTrue(
            (self.root / "accepted-ran.txt").is_file(),
            "an accepted command does run, so the marker is real evidence",
        )

    def test_editing_an_accepted_command_invalidates_its_acceptance(self):
        """Acceptance is a digest of what would be executed, not of the file it
        came from — so a one-character edit is a new command that nobody here
        has reviewed."""
        original = {"kind": "cmd", "run": self.py(
            "import pathlib; pathlib.Path('first.txt').write_text('ran')"
        )}
        self.cli("task", "add-auth", "--objective", "SSO sign-in works")
        self.gate([original], "add-auth")
        accepted = trust.load(self.layout)
        self.assertTrue(trust.is_accepted(original, accepted))

        edited = dict(original, run=self.py(
            "import pathlib; pathlib.Path('second.txt').write_text('ran')"
        ))
        self.assertFalse(
            trust.is_accepted(edited, accepted),
            "editing the command drops it back outside the boundary",
        )

        path = self.layout.task_file("add-auth")
        doc = frontmatter.read(path)
        doc.meta["verify"] = [edited]
        doc.write(path)

        before = self.snapshot()
        self.run_hook("Stop")
        self.assertFalse(
            (self.root / "second.txt").exists(),
            "the edited command did not run",
        )
        self.assert_git_unchanged(before, self.snapshot(), note="edited command")

    def test_changing_only_the_environment_is_also_a_new_command(self):
        """`env` and `cwd` change what a command does without changing a
        character of `run`, so acceptance has to cover them too."""
        original = {"kind": "cmd", "run": OK}
        self.trust([original])
        accepted = trust.load(self.layout)
        self.assertTrue(trust.is_accepted(original, accepted))
        self.assertFalse(
            trust.is_accepted(dict(original, env={"PATH": "/tmp"}), accepted)
        )
        self.assertFalse(trust.is_accepted(dict(original, cwd="src"), accepted))



# --------------------------------------------------------------------------- #
# the hostile ledger — a ctx.yaml that arrived from somewhere else
# --------------------------------------------------------------------------- #

class TestAHostileLedgerCannotCommit(GitInvariantFixture):
    """A `ctx.yaml` is executable shell, and it travels with the repository.

    The tests above prove that an *unaccepted* command does not run. That is one
    door. These are the others: every path that can put a command into the
    accepted set, or run it without consulting the accepted set at all. Clone a
    repository whose ledger carries a hostile `verify` block, do something
    ordinary, and see whether a commit lands on your branch.

    The scenario is not exotic. `.ctx/` is designed to be committed and shared —
    that is the product's whole premise — so a ledger written by someone else is
    the normal case, not the attack case.
    """

    HOSTILE = "git commit --allow-empty -m HOSTILE"

    def hostile_ledger(self, **extra):
        """Overwrite the fixture's generated ctx.yaml with an attacker's."""
        lines = ['schema: 1', 'profile: code', 'level: "0"']
        for key, value in extra.items():
            lines.append(f"{key}:")
            lines.append(f"  - {value}")
        lines += ["verify:", "  - kind: cmd", f"    run: {self.HOSTILE}"]
        self.layout.config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # The victim has never accepted anything: this is a fresh clone.
        trust.path_for(self.layout).unlink(missing_ok=True)

    def assertNoHostileCommit(self, label):
        log = self.git_out("log", "--oneline", "--all")
        self.assertNotIn("HOSTILE", log, f"{label} executed the ledger's command")

    def test_init_over_a_hostile_ledger_does_not_accept_its_commands(self):
        """`ctx init` on an existing ledger accepted the file's whole `verify`
        block while printing only the commands it had detected itself — so the
        review the trust store exists to force never happened."""
        self.hostile_ledger()
        self.cli("init")
        code, out = self.cli("trust")
        self.assertIn("not yet accepted", out,
                      "a command from a cloned ledger must still need review")
        self.assertNotEqual(code, 0)

    def test_a_cloned_ledger_cannot_commit_through_the_stop_hook(self):
        """The full path, end to end: clone, init, ask for a change, session
        ends. The Stop hook runs verify commands with no permission prompt, so
        anything accepted by then executes in the user's working tree."""
        self.hostile_ledger()
        self.cli("init")
        self.cli("task", "fix-a-bug")
        before = self.snapshot()
        self.run_hook("Stop")
        self.assertNoHostileCommit("the Stop hook")
        self.assert_git_unchanged(before, self.snapshot())

    def test_doctor_does_not_run_what_it_reports_as_untrusted(self):
        """`ctx doctor --verify` ran every command in ctx.yaml through the shell
        without consulting the trust store, then printed, in the same output,
        that those commands would not run until reviewed."""
        self.hostile_ledger()
        before = self.snapshot()
        self.cli("doctor", "--verify")
        self.assertNoHostileCommit("ctx doctor --verify")
        self.assert_git_unchanged(before, self.snapshot())

    def test_init_verify_now_does_not_probe_untrusted_candidates(self):
        """`--verify-now` executes candidate commands to see whether they pass on
        a clean tree. Candidates come from `verify_candidates:` in the same
        cloned file, and were run before they were printed."""
        self.hostile_ledger(verify_candidates=self.HOSTILE)
        before = self.snapshot()
        self.cli("init", "--verify-now", "--force")
        self.assertNoHostileCommit("ctx init --verify-now")
        self.assert_git_unchanged(before, self.snapshot())

    def test_the_trust_store_is_not_kept_inside_the_repository(self):
        """A store under `.ctx/runtime/` is protected only by a `.gitignore`,
        which `git add -f` defeats — so an attacker ships the acceptance along
        with the command it accepts and no review is required at all."""
        store = trust.path_for(self.layout)
        self.assertFalse(
            str(store).startswith(str(self.layout.root)),
            "the record of what this machine agreed to run must not travel "
            "with the repository that supplies the commands",
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
