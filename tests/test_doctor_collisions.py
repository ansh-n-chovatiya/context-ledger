"""Two merge hazards that a branch-per-unit wave creates, and how ctx answers.

Both come from the same place: concurrent branches write into one ledger, and
git merges cleanly when they touched different bytes — which is exactly when the
result is wrong.

**Duplicate ADR ids.** `spec.next_adr_number` allocates local-max+1. Two
branches each see `0002` as the highest, each write `0003-<their title>.md`, and
the merge takes both. No conflict, no error, and two files now claim ADR 0003 —
so `0003` stops identifying a decision, which is the only job the number has.
The decision taken for this wave was to *keep the padded sequence and detect the
duplicate*: a padded id is what makes "see 0007" citable in prose and in commit
messages, and a ULID would trade that away to fix something a check catches in
milliseconds. So `spec.py` is untouched and `doctor`/`ci` name the collision.

**A tracked `DIGEST.md`.** The digest is derived — regenerated from the day
files on every session — and rewritten by every author. Tracked, it conflicts on
every merge of two branches that both did any work, and the conflict carries no
information, because either side can be rebuilt with `ctx digest`. This ledger
untracks it and `ctx init` ignores it from now on. For anybody else's
repository, `doctor` says so and prints the two commands, and **nothing here
runs them**: a diagnostic command that quietly stages a deletion in someone's
repo is a worse surprise than the conflict it saves. It is also not a failure —
an existing project must not find its pipeline red the morning after it upgrades
ctx.

One more thing is pinned here, from a tension unit 04 created rather than a
defect: a corrupt *policy* file no longer stops a hook (a session must not
brick) but must still stop `ctx doctor` and `ctx ci` (a control plane that fails
soft is a control silently not applied). That split is only defensible while
both halves hold, so both halves are asserted.
"""

import os
import shutil
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    cli, config as config_mod, frontmatter, spec as spec_mod,
)
from support import Fixture  # noqa: E402


REPO = Path(__file__).resolve().parent.parent


def adr(layout, number, slug, title="Something"):
    """One ADR file, written the way `spec.write_decision` writes them."""
    layout.decisions.mkdir(parents=True, exist_ok=True)
    path = layout.decisions / f"{number:04d}-{slug}.md"
    frontmatter.Document(
        {"ctx_schema": config_mod.SCHEMA, "adr": number, "title": title,
         "status": "accepted", "date": "2026-01-01"},
        f"# {number:04d}. {title}\n\n## Decision\nWe did it.\n",
    ).write(path)
    return path


# --------------------------------------------------------------------------- #
# criteria 8-10 — duplicate ADR ids
# --------------------------------------------------------------------------- #

class TestDuplicateAdrIdsAreNamed(Fixture):

    def collide(self):
        """What a clean merge of two branches leaves behind."""
        return (adr(self.layout, 3, "cache-the-briefing"),
                adr(self.layout, 3, "drop-the-windows-runner"))

    def test_doctor_fails_and_names_both_files(self):
        first, second = self.collide()
        code, out = self.cli("doctor")
        self.assertEqual(code, 1, out)
        self.assertIn(first.name, out)
        self.assertIn(second.name, out)
        self.assertIn("0003", out)

    def test_ci_fails_and_names_both_files(self):
        first, second = self.collide()
        code, out = self.cli("ci")
        self.assertEqual(code, 1, out)
        self.assertIn(first.name, out)
        self.assertIn(second.name, out)

    def test_three_files_on_one_id_are_all_named(self):
        """Two branches is the common case, not the limit — a wave is as wide
        as the plan makes it."""
        names = [adr(self.layout, 7, slug).name
                 for slug in ("alpha", "beta", "gamma")]
        code, out = self.cli("doctor")
        self.assertEqual(code, 1, out)
        for name in names:
            self.assertIn(name, out)

    def test_two_ids_colliding_are_both_reported(self):
        adr(self.layout, 3, "alpha")
        adr(self.layout, 3, "beta")
        adr(self.layout, 4, "gamma")
        adr(self.layout, 4, "delta")
        self.assertEqual(
            cli.duplicate_adrs(self.layout),
            [(3, ["0003-alpha.md", "0003-beta.md"]),
             (4, ["0004-delta.md", "0004-gamma.md"])],
        )

    def test_a_clean_sequence_is_not_a_problem(self):
        """Criterion 10, on a synthetic tree: consecutive ids, one file each."""
        for number, slug in ((1, "alpha"), (2, "beta"), (3, "gamma")):
            adr(self.layout, number, slug)
        self.assertEqual(cli.duplicate_adrs(self.layout), [])
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)

    def test_this_repositorys_own_decisions_are_clean(self):
        """Criterion 10, on the real thing. The check has to be quiet on a tree
        with nothing wrong with it, or it is noise everyone learns to scroll
        past — so it is run against this repository's actual ADRs, copied into
        a throwaway ledger so the assertion is about the files and not about
        whatever else is on this machine.
        """
        source = REPO / ".ctx" / "decisions"
        self.assertTrue(source.is_dir(), "this repository has no decisions/")
        found = sorted(source.glob("*.md"))
        self.assertTrue(found, "no ADRs to check — the assertion would be empty")
        for path in found:
            shutil.copy2(path, self.layout.decisions / path.name)
        self.assertEqual(cli.duplicate_adrs(self.layout), [])
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)

    def test_a_file_that_is_not_an_adr_is_ignored(self):
        adr(self.layout, 1, "alpha")
        (self.layout.decisions / "README.md").write_text("notes\n", encoding="utf-8")
        (self.layout.decisions / "draft.md").write_text("notes\n", encoding="utf-8")
        self.assertEqual(cli.duplicate_adrs(self.layout), [])


class TestTheAllocatorIsUnchanged(Fixture):
    """Criterion 9. The decision was detect-not-prevent, so `next_adr_number`
    must still be local-max+1. A ULID here would be a different decision taken
    quietly in the course of implementing this one."""

    def test_next_is_still_local_max_plus_one(self):
        self.assertEqual(spec_mod.next_adr_number(self.layout), 1)
        adr(self.layout, 1, "alpha")
        self.assertEqual(spec_mod.next_adr_number(self.layout), 2)
        adr(self.layout, 7, "gap")
        self.assertEqual(spec_mod.next_adr_number(self.layout), 8)

    def test_a_duplicate_does_not_change_what_comes_next(self):
        adr(self.layout, 3, "alpha")
        adr(self.layout, 3, "beta")
        self.assertEqual(spec_mod.next_adr_number(self.layout), 4)

    def test_write_decision_still_produces_a_padded_sequence(self):
        first = spec_mod.write_decision(self.layout, "First", "first")
        second = spec_mod.write_decision(self.layout, "Second", "second")
        self.assertEqual(first.name, "0001-first.md")
        self.assertEqual(second.name, "0002-second.md")


# --------------------------------------------------------------------------- #
# criteria 11-14 — the tracked digest
# --------------------------------------------------------------------------- #

class TestInitIgnoresTheDigest(Fixture):
    """Criterion 13. A ledger created from now on never tracks the digest in
    the first place, which is the only fix that scales past this repository."""

    def test_the_generated_gitignore_carries_the_line(self):
        text = (self.layout.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(cli.DIGEST_IGNORE_LINE, text.split("\n"))

    def test_runtime_is_still_ignored(self):
        """The line was added, not substituted for the one that was there."""
        text = (self.layout.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("runtime/", text.split("\n"))

    def test_the_pattern_matches_the_digest_path_ctx_actually_writes(self):
        """Spelled relative to `.ctx/`, because that is where the `.gitignore`
        lives. Derived from `layout.digest` so the two cannot drift."""
        relative = str(self.layout.digest.relative_to(self.layout.root))
        self.assertEqual(relative.replace(os.sep, "/"), cli.DIGEST_IGNORE_LINE)


class TrackedDigestFixture(Fixture):
    """A project that tracked `DIGEST.md` before this change — which is every
    project that has ever run `ctx init`."""

    def setUp(self):
        super().setUp()
        self.git_init()
        self.assertTrue(self.layout.digest.is_file())
        # `-f` because init's `.gitignore` now covers it: this is a repository
        # that committed the file back when nothing ignored it.
        subprocess.run(
            ["git", "add", "-f", str(self.layout.digest)],
            cwd=str(self.root), capture_output=True, text=True, check=True,
        )

    def tracked(self):
        return subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(self.layout.digest)],
            cwd=str(self.root), capture_output=True, text=True,
        ).returncode == 0


class TestDoctorAdvisesAboutATrackedDigest(TrackedDigestFixture):

    def test_the_advisory_names_the_file_and_both_commands(self):
        code, out = self.cli("doctor")
        self.assertIn(".ctx/journal/DIGEST.md", out)
        self.assertIn("git rm --cached .ctx/journal/DIGEST.md", out)
        self.assertIn(f"echo {cli.DIGEST_IGNORE_LINE} >> .ctx/.gitignore", out)
        self.assertEqual(code, 0, out)

    def test_it_is_advisory_and_not_a_failure(self):
        """Criterion 14. A project that upgrades ctx must not wake up to a red
        pipeline over a file it has tracked since the day it was created."""
        self.assertEqual(self.cli("doctor")[0], 0)
        self.assertEqual(self.cli("ci")[0], 0)

    def test_ci_reports_it_too(self):
        code, out = self.cli("ci")
        self.assertEqual(code, 0, out)
        self.assertIn("git rm --cached", out)

    def test_nothing_untracks_the_file(self):
        """Criterion 12, the load-bearing half. The tool advises; the human
        acts. Asserted twice: the file is still in the index afterwards, and no
        `git rm` was ever spawned."""
        calls = []
        real_run = subprocess.run

        def traced(args, *rest, **kwargs):
            calls.append(list(args) if isinstance(args, (list, tuple)) else [args])
            return real_run(args, *rest, **kwargs)

        with unittest.mock.patch.object(cli.subprocess, "run", traced):
            self.assertEqual(self.cli("doctor")[0], 0)

        self.assertTrue(self.tracked(), "doctor untracked a file in this repo")
        self.assertTrue(calls, "doctor never asked git anything — no evidence")
        for argv in calls:
            self.assertNotIn("rm", argv, f"doctor ran: {' '.join(argv)}")
        for argv in calls:
            if argv and argv[0] == "git":
                self.assertIn(argv[1], ("ls-files", "status", "rev-parse", "diff"),
                              f"doctor ran an unexpected git command: {argv}")

    def test_an_untracked_digest_says_nothing_at_all(self):
        """The check must be silent when there is nothing to say, or it is one
        more line nobody reads."""
        subprocess.run(["git", "rm", "--cached", "-q", str(self.layout.digest)],
                       cwd=str(self.root), capture_output=True, check=True)
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertNotIn("git rm --cached", out)

    def test_a_project_that_is_not_a_git_repository_says_nothing(self):
        self.rmtree(self.root / ".git")
        code, out = self.cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertNotIn("git rm --cached", out)


class TestThisLedgerHasAlreadyDoneIt(unittest.TestCase):
    """Criterion 11, asserted against this repository rather than a fixture.
    The advice `doctor` gives other projects is advice this one has taken."""

    def test_the_ledger_gitignore_carries_the_line(self):
        text = (REPO / ".ctx" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(cli.DIGEST_IGNORE_LINE, text.split("\n"))

    def test_the_digest_is_not_tracked_here(self):
        if not (REPO / ".git").exists():
            self.skipTest("not a git checkout")
        listed = subprocess.run(
            ["git", "ls-files", "--", ".ctx/journal/DIGEST.md"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        self.assertEqual(
            listed.stdout.strip(), "",
            "DIGEST.md is tracked again — every merge of two working branches "
            "will conflict on it",
        )

    def test_the_digest_is_not_tracked_but_is_still_ignored_on_purpose(self):
        """Untracked, not deleted — asserted against git, not against disk.

        This used to assert the file was present in *this checkout*. That
        passes on a machine where a session has run and regenerated it, and
        fails in every fresh clone — which is what CI is, so it went red the
        first time this branch reached a runner. The claim worth making is
        about the repository's intent, and git is where that lives: the path is
        ignored, and it is not tracked. Whether it happens to exist right now
        is a property of the working copy, not of the project.

        That it comes *back* is the other half, and it has its own class
        below, driven through a fixture rather than through this checkout.
        """
        ignore = (REPO / ".ctx" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("journal/DIGEST.md", ignore.split())
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".ctx/journal/DIGEST.md"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        self.assertNotEqual(
            tracked.returncode, 0,
            "DIGEST.md is tracked again — untracking it is what stopped two "
            "agents finishing at once from conflicting on a derived file",
        )


class TestTheDigestRegeneratesOnceUntracked(Fixture):
    """Untracking it is only safe because nothing depends on it being in git:
    it is derived, and the next hook fire rebuilds it from the day files."""

    def test_a_deleted_digest_comes_back_on_the_next_hook_fire(self):
        """`SessionEnd` and `PreCompact` both call `journal.write_digest`, so
        the file is rebuilt at the end of every session and before every
        compaction whether or not git has ever seen it."""
        self.assertEqual(self.cli("level", "1")[0], 0)
        for event in ("SessionEnd", "PreCompact"):
            self.layout.digest.unlink()
            self.assertFalse(self.layout.digest.exists())
            code, _out = self.run_hook(event)
            self.assertEqual(code, 0)
            self.assertTrue(
                self.layout.digest.is_file(),
                f"{event} did not regenerate the digest — untracking it would "
                "lose it for good",
            )

    def test_ctx_digest_rebuilds_it_too(self):
        self.layout.digest.unlink()
        self.assertEqual(self.cli("digest")[0], 0)
        self.assertTrue(self.layout.digest.is_file())


# --------------------------------------------------------------------------- #
# criterion 18 — the hook fails open, the CLI does not
# --------------------------------------------------------------------------- #

class TestACorruptPolicyStillStopsDoctorAndCi(Fixture):
    """Unit 04 made `hooks.main` fail open on `config.load`'s `SystemExit`,
    which now includes an unreadable or non-mapping *policy* file. Wave 3 made
    that condition deliberately fatal: "a control plane that fails soft is a
    control silently not applied".

    The split is defensible — a machine-wide typo must not brick every session
    in every project, and CI must still refuse so somebody fixes it — but only
    while both halves hold. If the CLI ever starts shrugging too, the hook's
    fail-open has quietly become the whole product's.

    This pins existing behaviour. Nothing here owns `config.py`.
    """

    def setUp(self):
        super().setUp()
        config_mod.reset_policy_warnings()
        self.addCleanup(config_mod.reset_policy_warnings)
        policy = self.untracked / "staged-system-policy.yaml"
        policy.write_text("- not\n- a mapping\n", encoding="utf-8")
        os.environ["CTX_POLICY_SYSTEM"] = str(policy)
        self.policy = policy

    def test_doctor_refuses(self):
        code, out = self.cli("doctor")
        self.assertNotEqual(code, 0, out)
        self.assertIn("policy", out.lower())

    def test_ci_refuses(self):
        code, out = self.cli("ci")
        self.assertNotEqual(code, 0, out)
        self.assertIn("policy", out.lower())

    def test_the_hook_still_does_not_brick_the_session(self):
        """The other half of the split, so the two are asserted together and a
        change to either is visible as a change to a pair."""
        config_mod.reset_policy_warnings()
        code, _out = self.run_hook("SessionStart")
        self.assertEqual(code, 0)

    def test_both_are_green_again_once_the_policy_is_valid(self):
        self.policy.write_text("level: '1'\n", encoding="utf-8")
        config_mod.reset_policy_warnings()
        self.assertEqual(self.cli("doctor")[0], 0)
        self.assertEqual(self.cli("ci")[0], 0)


if __name__ == "__main__":
    unittest.main()
