"""A `done` sibling's uncommitted work must not deadlock the next gate.

Two rules in `review.wave_scope` are each correct alone. A concurrent sibling
that is not yet `done` may be writing to this shared tree right now, so its
`owns` is excused from this unit's scope check — otherwise every wave of two or
more deadlocked its own gate on its own legitimate writes (`test_sibling_scope`
pins that). A `done` sibling, on the other hand, has been reviewed and its
writes have landed, so a *later* change to its paths is somebody else's and
still belongs in a report — excusing it forever would let a wave, once one unit
finished, write anywhere under that unit's old `owns` without consequence.

Both rules are right and they miss a case neither anticipated: `ctx unit
--status done` marks a unit done the moment its own gate passes, which is well
before anyone commits. A wave gated one unit at a time — the realistic shape,
since a runner writes its work *after* dispatch rather than staging it ahead of
`ctx start` — can run three gates in a row with no commit between any of them.
The first unit's `owns` are then still sitting uncommitted in the one shared
tree while the next unit's gate runs, and the old, unconditional exclusion of
`done` siblings reproduced the exact false-Critical this project already fixed
once, just moved from "running" to "done but not yet committed".

The fix: a `done` sibling is excused for exactly as long as its own `owns`
paths are still uncommitted, and not a moment longer. `TestGatingDoesNotStall`
is the positive control — the same scene this file's own history shows failing
on the code before this fix, quoted in the unit's report. Everything else pins
that the widening did not go further than that one gap: a path nobody in the
wave declared is still a violation even with a dirty `done` sibling sitting in
the same tree, a `done` sibling's *committed* work still excuses nothing (the
rule this file's fix must not disturb), and a unit in a different wave is never
an excuse regardless of its status or its git state.

**And a second gap, underneath that one.** "Uncommitted under a `done` unit's
`owns`" was taken to mean "the done unit's own leftover work", but it only ever
tested that *something* under there was dirty. A still-running sibling that
writes into a path an already-`done` unit owns produces the same `git status`
line, so its write was reclassified as the done unit's leftovers: excused,
"Scope violations: None", and the diff withheld from the package, because a
sibling's path is deliberately left to that sibling's own review. The docstring
argued the done-gate's contract check covered it; it does not, and cannot —
`contract.compare` hashes the unit's frontmatter, never the contents of the
files it owns.

`TestAnotherUnitsWriteUnderADoneUnitsOwns` is that scene, in the report's own
sequence: `01-api` finishes, gates and commits, and then `02-store` overwrites
a file `01-api` owns. It must be reported against `02-store`. What tells the
two apart is the digest `review._record_own_work` takes of a unit's uncommitted
`owns` while it is still running — at its own gate, among other moments — and
freezes when it goes `done`: dirt that still digests to what the unit wrote is
its own, and dirt that does not is somebody else's.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, review as review_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
UNITS = ("01-api", "02-store", "03-cli")


class WaveFixture(Fixture):
    """A real repository, a plan of three concurrent units, gated one by one
    with no commit anywhere in the sequence — dispatch, then each unit's file
    lands only in the working tree, exactly as a runner leaves it."""

    slug = "wave"

    def setUp(self):
        super().setUp()
        self.git_init()

    def plan(self, names=UNITS, wave=1, depends_on=None):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        checks = [{"kind": "diff"}, {"kind": "cmd", "run": OK}]
        for name in names:
            frontmatter.Document(
                {
                    "ctx_schema": 1, "unit": name, "plan": self.slug,
                    "tier": "subagent",
                    "depends_on": list((depends_on or {}).get(name, [])),
                    "owns": [f"src/{name}.py"], "reads": [], "forbid": [],
                    "budget_tokens": 1000, "status": "pending", "wave": wave,
                    "verify": list(checks),
                },
                CRITERIA,
            ).write(directory / f"{name}.md")
            self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")

    def dispatch(self):
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)

    def done(self, name):
        return self.cli("unit", name, "--status", "done", "--plan", self.slug)

    def status_of(self, name):
        return plan_mod.find_unit(self.layout, self.slug, name).status

    def git(self, *args):
        env = dict(
            self._env, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
            GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com",
        )
        completed = subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()


# --------------------------------------------------------------------------- #
# criterion 1 & 4 — the positive control
# --------------------------------------------------------------------------- #

class TestGatingDoesNotStallOnADoneSiblingsUncommittedWork(WaveFixture):
    """This is the scene that failed before this unit's fix. Quoted in the
    unit's report, against the code before and after: gate1 passed either way,
    but gate2 and gate3 refused on `src/01-api.py` — a file `01-api` finished
    and was marked `done` for, sitting uncommitted while `02-store`'s and
    `03-cli`'s own gates ran, in the one shared tree every `subagent`-tier
    wave actually runs in."""

    def test_three_gates_in_sequence_with_no_commit_between_them_all_pass(self):
        self.plan()
        self.dispatch()

        self.write("src/01-api.py", "# 01-api\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)

        self.write("src/02-store.py", "# 02-store\n")
        code, out = self.done("02-store")
        self.assertEqual(code, 0, out)

        self.write("src/03-cli.py", "# 03-cli\n")
        code, out = self.done("03-cli")
        self.assertEqual(code, 0, out)

        self.assertEqual([self.status_of(n) for n in UNITS], ["done"] * 3)

    def test_the_wave_scope_helper_excuses_the_done_siblings_uncommitted_owns(self):
        """The same claim, read straight off `wave_scope` rather than through
        three CLI calls — a failure here says the widening itself is wrong,
        not just something downstream of it."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)

        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        patterns, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        # `03-cli` is still `running` (not `done` at all) and is excused on
        # that ground alone — this pins the *other* sibling, `01-api`, `done`
        # with its owns still uncommitted.
        self.assertEqual(
            siblings,
            [("01-api", ["src/01-api.py"]), ("03-cli", ["src/03-cli.py"])],
            "a done sibling with uncommitted owns must still be reported as "
            "running alongside this unit",
        )
        self.assertIn("src/01-api.py", patterns)


# --------------------------------------------------------------------------- #
# criterion 2 — the widening is not a blank cheque
# --------------------------------------------------------------------------- #

class TestTheProtectionIsNotWidenedIntoUselessness(WaveFixture):
    def test_a_path_nobody_in_the_wave_declared_is_still_a_violation(self):
        """A `done`, still-dirty sibling in the tree does not turn into "any
        path any unit ever writes is fine"."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)

        # Nobody in this wave declared this path.
        self.write("src/rogue.py", "# nobody owns this\n")
        code, out = self.done("02-store")
        self.assertEqual(code, 1, out)
        self.assertIn("src/rogue.py", out)
        self.assertEqual(self.status_of("02-store"), "running")

    def test_a_done_siblings_committed_work_reverts_to_excusing_nothing(self):
        """Once the done sibling's work actually lands and the tree is clean
        again, the original rule this fix must not disturb is back in force —
        the exclusion is not "done, ever", it is "done, and still dirty"."""
        self.plan(names=("01-api", "02-store"))
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)

        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        _before, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual(siblings, [("01-api", ["src/01-api.py"])],
                         "still uncommitted, so still excused")

        self.git("add", "-A")
        self.git("commit", "-qm", "land 01-api's work")

        _after, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual(siblings, [],
                         "committed and clean — the done sibling's owns is "
                         "no longer an excuse for anything")

    def test_a_different_waves_done_sibling_never_excuses_a_path(self):
        """A unit in another wave is not running now, whatever its status or
        its git state — the exclusion this file's fix must not touch."""
        self.plan(
            names=("01-api", "02-later"),
            depends_on={"02-later": ["01-api"]},
        )
        # `02-later` is scheduled for wave 2 by the dependency; make that
        # explicit rather than relying on `plan-check` having run.
        self.edit("02-later", wave=2, status="done")
        self.write("src/02-later.py", "# 02-later, uncommitted\n")

        subject = plan_mod.find_unit(self.layout, self.slug, "01-api")
        patterns, siblings = review_mod.wave_scope(self.layout, self.slug, subject)
        self.assertEqual(siblings, [])
        self.assertEqual(patterns, ["src/01-api.py"])

    def edit(self, name, **changes):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        doc.write(path)


# --------------------------------------------------------------------------- #
# the second gap — somebody else's write under a done unit's owns
# --------------------------------------------------------------------------- #

class TestAnotherUnitsWriteUnderADoneUnitsOwns(WaveFixture):
    """The report's sequence, and the two tighter variants of it.

    The excuse has to distinguish "this done sibling's own work is still sitting
    here uncommitted" from "something under this sibling's owns is no longer
    what the sibling wrote". Only the first is the done unit's to answer for.
    """

    def test_an_overwrite_after_the_done_unit_committed_is_reported(self):
        """Verbatim from the report: `01-api` finishes, gates, commits; then
        `02-store` — same wave, still running — overwrites a path `01-api`
        owns. The overwrite is `02-store`'s, and it must be named."""
        self.plan()
        self.dispatch()

        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "land 01-api's work")

        # `02-store` does its own declared work, and also writes where it may
        # not. Nothing in the tree says which unit's hand did either.
        self.write("src/02-store.py", "# 02-store\n")
        self.write("src/01-api.py", "# 02-store was here\n")

        code, out = self.done("02-store")
        self.assertEqual(code, 1, out)
        self.assertIn("src/01-api.py", out)
        self.assertEqual(self.status_of("02-store"), "running")

    def test_the_same_overwrite_before_the_done_unit_committed_is_reported(self):
        """The harder half. `01-api`'s own work is *still uncommitted* — the
        case the done-sibling excuse exists for — and `02-store` overwrites it
        anyway. "Something under there is dirty" cannot tell this from the
        legitimate case; the digest of what `01-api` actually wrote can."""
        self.plan()
        self.dispatch()

        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)

        self.write("src/01-api.py", "# 02-store was here\n")

        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        _patterns, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual(
            [name for name, _owns in siblings], ["03-cli"],
            "a done sibling whose owns no longer hold what it wrote must stop "
            "excusing anything",
        )

        self.write("src/02-store.py", "# 02-store\n")
        code, out = self.done("02-store")
        self.assertEqual(code, 1, out)
        self.assertIn("src/01-api.py", out)

    def test_a_new_file_under_a_done_units_owns_is_reported(self):
        """The done unit never wrote this path at all, so there is no digest of
        it to match — an absent entry is a divergence, not a missing one."""
        self.plan(names=("01-api", "02-store"))
        # `01-api` owns a directory, so a file can appear under it that `01-api`
        # itself never touched. Declared before dispatch, so the contract it is
        # sealed with is this one.
        self.edit("01-api", owns=["src/api/"])
        self.dispatch()
        self.write("src/api/core.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "land 01-api's work")

        self.write("src/api/extra.py", "# 02-store was here\n")
        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        _patterns, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual(siblings, [])

    def test_the_review_package_shows_the_violation_instead_of_None(self):
        """Where the defect was actually read: the package printed "Scope
        violations: None" and left the overwrite out of the diff, because a
        sibling's path is deliberately not this unit's to show."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "land 01-api's work")

        self.write("src/02-store.py", "# 02-store\n")
        self.write("src/01-api.py", "# 02-store was here\n")

        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        path, stats, problem = review_mod.build(
            self.layout, self.config, store, self.slug, self.root)
        self.assertEqual(problem, "")
        self.assertEqual(stats["out_of_scope"], 1)
        package = path.read_text(encoding="utf-8")
        self.assertIn("- src/01-api.py", package)
        self.assertNotIn("None — every changed path", package)

    def test_a_done_sibling_with_no_record_keeps_the_old_excuse(self):
        """The deliberate fallback, pinned so it stays deliberate. A unit
        marked done by `--force` never ran the gate that takes the digest, and
        one done under an older ctx has none on disk. Refusing there would
        deadlock the waves this whole exclusion exists to unblock, so the
        pre-digest behaviour stands: dirty under its `owns` is excused."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)
        review_mod.owns_record_path(self.layout, self.slug, "01-api").unlink()

        self.write("src/01-api.py", "# somebody else was here\n")
        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        _patterns, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual([name for name, _owns in siblings], ["01-api", "03-cli"])

    def test_the_done_units_own_untouched_work_is_still_excused(self):
        """The behaviour that must survive the narrowing, restated here beside
        the tests that narrow it: nothing else changed, so `01-api`'s own
        uncommitted work is still `01-api`'s to answer for."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.assertEqual(self.done("01-api")[0], 0)

        store = plan_mod.find_unit(self.layout, self.slug, "02-store")
        _patterns, siblings = review_mod.wave_scope(self.layout, self.slug, store)
        self.assertEqual([name for name, _owns in siblings], ["01-api", "03-cli"])

    def done(self, name, *extra):
        return self.cli("unit", name, "--status", "done", "--plan", self.slug,
                        *extra)

    def edit(self, name, **changes):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        doc.write(path)


if __name__ == "__main__":
    unittest.main()
