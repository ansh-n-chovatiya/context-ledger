"""Both doors out of the done-gate must leave the same trace.

`ctx unit --status done --force` records what it stepped over: its journal entry
reads `done (--force overrode the gate: …)`. `ctx merge --skip-gate` is the other
door to the same place — a unit reaches `done` with its checks unrun — and it
journalled `ok`. The override text existed, but only as a merge *message*: it
printed to the terminal and scrolled away.

That is the objection `report.md` raises against `CTX_GATE=off` at line 123 —
an escape hatch with "no audit record" is not meaningfully different from no
gate, because nobody can find it afterwards. The fix is not a second vocabulary:
one grep over the journal has to find both overrides, so `--skip-gate` writes
the same words the forced-done entry uses.

The negative control at `TestOrdinaryMergeIsNotAnOverride` is what makes the
rest mean anything: an entry written on every merge would satisfy every grep
above while recording nothing about the override.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, worktree as wt  # noqa: E402
from support import FAILS, OK  # noqa: E402
from test_audit_merge_preflight import MergePreflightFixture  # noqa: E402


# One search, run over the whole journal, that both overrides have to answer to.
# `03-ungated-is-not-done` chose these words for the forced-done entry; a reader
# looking for "when was a gate overridden here" types this and nothing else.
OVERRIDE = re.compile(r"overrode the gate")


class OverrideJournalFixture(MergePreflightFixture):
    """`test_audit_merge_preflight`'s repository, driven through the CLI.

    That module calls `worktree.merge` directly, which is the right level for a
    preflight refusal. The journal entry is written by `cmd_merge`, so every
    test here goes through `ctx merge` — the entry is not reachable from the
    library call at all.
    """

    UNTRUSTED = [
        {"kind": "cmd", "run": "ctx-lint --strict"},
        {"kind": "cmd", "run": "ctx-test --all"},
    ]

    def merge_cli(self, name="01-a", *extra):
        return self.cli("merge", name, "--plan", self.slug, *extra)

    def journal_text(self):
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(self.layout.journal.glob("*.md"))
        )

    def override_lines(self):
        """Every journal line the one search finds."""
        return [
            line.strip()
            for line in self.journal_text().splitlines()
            if OVERRIDE.search(line)
        ]

    def entries(self, kind, name="01-a"):
        """Journal lines for one event kind and target, notes only."""
        found = []
        for line in self.journal_text().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[1] == kind and parts[2] == name:
                found.append(parts[3] if len(parts) > 3 else "")
        return found


# --------------------------------------------------------------------------- #
# 1 — the override is written down
# --------------------------------------------------------------------------- #

class TestSkipGateIsJournalled(OverrideJournalFixture):

    def test_a_skip_gate_merge_journals_the_override_and_what_it_skipped(self):
        """Criterion 1. `ok` is what this used to say, and `ok` is a lie of
        omission: the gate did not pass, it was not run."""
        branch = self.dispatched(checks=self.UNTRUSTED, accept=())

        code, out = self.merge_cli("01-a", "--skip-gate")

        self.assertEqual(code, 0, out)
        self.assert_merged(branch)
        notes = self.entries("merge")
        self.assertEqual(len(notes), 1, self.journal_text())
        note = notes[0]
        self.assertIn("--skip-gate", note, "the door that was used")
        self.assertIn("overrode the gate", note, "that a gate was overridden")
        self.assertIn("2 verify check(s) were not run", note, "how much was skipped")
        self.assertNotEqual(note, "ok")

    def test_the_journalled_override_names_the_checks_that_did_not_run(self):
        """The count alone answers "was this ungated"; the names answer "what
        was never verified", which is the question asked six months later."""
        self.dispatched(checks=self.UNTRUSTED, accept=())

        self.assertEqual(self.merge_cli("01-a", "--skip-gate")[0], 0)

        note = self.entries("merge")[0]
        self.assertIn("ctx-lint --strict", note)

    def test_the_override_survives_into_the_journal_not_only_the_terminal(self):
        """The regression this closes: the text existed, as a merge message.
        Printing is not recording — the terminal scrolls, the journal does not."""
        self.dispatched(checks=self.UNTRUSTED, accept=())

        _code, out = self.merge_cli("01-a", "--skip-gate")

        self.assertIn("--skip-gate overrode the done-gate", out, "still printed")
        self.assertTrue(self.override_lines(), "and now also written down")


# --------------------------------------------------------------------------- #
# 2 — one search finds both doors
# --------------------------------------------------------------------------- #

class TestOneSearchFindsBothOverrides(OverrideJournalFixture):

    def test_a_single_grep_over_the_journal_finds_both_overrides(self):
        """Criterion 2. Asserted as the search itself, not as two strings: two
        separate assertions would pass just as happily on two vocabularies,
        which is the failure mode this criterion is about.
        """
        self.unit("02-b", owns=("src/b.py",), depends_on=["01-a"],
                  checks=[{"kind": "cmd", "run": FAILS}])
        branch = self.dispatched(checks=self.UNTRUSTED, accept=())

        # Door one: merge without running the gate.
        self.assertEqual(self.merge_cli("01-a", "--skip-gate")[0], 0)
        self.assert_merged(branch)
        # Door two: mark done past a gate that ran and failed.
        code, out = self.cli("unit", "02-b", "--plan", self.slug,
                             "--status", "done", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            frontmatter.read(plan_mod.units_dir(self.layout, self.slug) / "02-b.md")
            .meta["status"], "done",
        )

        found = self.override_lines()

        kinds = sorted(line.split("|")[1].strip() for line in found)
        self.assertEqual(
            kinds, ["merge", "unit"],
            f"one search must find both doors, found: {found}",
        )
        targets = sorted(line.split("|")[2].strip() for line in found)
        self.assertEqual(targets, ["01-a", "02-b"])


# --------------------------------------------------------------------------- #
# 3 — the negative control
# --------------------------------------------------------------------------- #

class TestOrdinaryMergeIsNotAnOverride(OverrideJournalFixture):
    """Criterion 3. Without this, an entry written on every merge would pass
    every test above while recording nothing about the override."""

    def test_a_merge_whose_gate_passed_journals_no_override(self):
        branch = self.dispatched(checks=[{"kind": "cmd", "run": OK}])

        code, out = self.merge_cli("01-a")

        self.assertEqual(code, 0, out)
        self.assert_merged(branch)
        self.assertIn("gate passed", out)
        self.assertEqual(self.entries("merge"), ["ok"],
                         "an ordinary merge still journals exactly `ok`")
        self.assertEqual(self.override_lines(), [],
                         "the one search must find nothing at all here")

    def test_the_two_merges_differ_only_in_the_flag(self):
        """The same unit, the same checks, the same repository: the entry
        changes because of `--skip-gate` and nothing else."""
        checks = [{"kind": "cmd", "run": OK}]
        self.dispatched(checks=checks)

        self.assertEqual(self.merge_cli("01-a", "--skip-gate")[0], 0)

        self.assertNotEqual(self.entries("merge"), ["ok"])
        self.assertTrue(self.override_lines())


# --------------------------------------------------------------------------- #
# 4 — a refusal still reads as a refusal
# --------------------------------------------------------------------------- #

class TestRefusalIsUnchanged(OverrideJournalFixture):

    def test_a_refused_merge_still_journals_refused(self):
        """Criterion 4. Nothing merged, so nothing was overridden — the entry
        must not drift into the override vocabulary."""
        branch = self.dispatched(checks=[{"kind": "cmd", "run": FAILS}])

        code, out = self.merge_cli("01-a")

        self.assertEqual(code, 1, out)
        self.assertIn("nothing was merged", out)
        self.assert_not_merged(branch)
        self.assertEqual(self.entries("merge"), ["refused"])
        self.assertEqual(self.override_lines(), [])

    def test_a_merge_refused_after_the_gate_was_skipped_still_reads_refused(self):
        """`--skip-gate` skips the gate, not the ownership preflight. A merge
        that never landed did not take a unit to `done`, so there is no override
        to record — `refused` is the honest entry."""
        self.dispatched(checks=self.UNTRUSTED, accept=(), relative="src/a.py")
        # A stray write outside `owns`, committed in the worktree: refused at
        # step 3, before the gate is reached at all.
        tree = wt.path_for(self.layout, "01-a")
        stray = tree / "src" / "stray.py"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("y = 2\n", encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-qm", "stray", cwd=tree)

        code, out = self.merge_cli("01-a", "--skip-gate")

        self.assertEqual(code, 1, out)
        self.assertEqual(self.entries("merge"), ["refused"])
        self.assertEqual(self.override_lines(), [])


if __name__ == "__main__":
    unittest.main()
