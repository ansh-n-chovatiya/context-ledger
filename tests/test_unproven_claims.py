"""Three claims this project shipped decisions on, and never proved.

Each one is load-bearing. `review.py` chose content snapshots over a commit
range *because* a wave can then be reviewed concurrently; `findings.py` writes
on every mutation *because* a killed session must not lose the loop it was in;
`trust.py` keys acceptance on the command string *because* nothing about how a
dispatch is sized may change what this machine has agreed to run. All three
were argued in prose in the module that depends on them, and asserted nowhere.

This file asserts them from outside `ctx/`. Nothing here imports a private
helper to make a proof easier, and nothing here adds production code: if a
claim cannot be demonstrated through the same surfaces a user drives, that is a
finding about the system, not a reason to reach inside it.

What each proof insists on, and why the cheaper version of it proves nothing:

**Concurrency must actually interleave.** Two units run one after the other
say nothing about a wave — sequential execution is the case that was never in
doubt. Both threads here pass through a `threading.Barrier` before every step,
so both are inside `snapshot.capture` at once, both writing at once, and both
inside `review.build` at once. A barrier that times out breaks rather than
hangs, so a regression fails the suite instead of wedging it.

**A kill must lose the process.** Calling a resume path in the process that
wrote the state proves only that the objects are still in memory, which they
were never at risk of not being. The proof here starts a real child, lets it
walk into the middle of a fix loop, kills it from outside with no cooperation
and no chance to flush or clean up, and then reads the state back in a *third*
process that shares nothing with the first but the filesystem.

**Equality must be byte equality.** "Escalation does not widen trust" is only
worth asserting if it is checked as bytes: the whole global store's file tree
before and after, not a summary of it that a widening could slip past.

The concurrency proof also found the honest limit of claim 1, which is pinned
in `TestSiblingWritesAreReportedNotSilentlyBlended` rather than left implicit —
see that class for what does *not* hold, and what proving it would take.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    config as config_mod, dispatch, findings as findings_mod, frontmatter,
    miniyaml, paths, plan as plan_mod, review as review_mod,
    snapshot as snapshot_mod, trust, verify,
)
from ctx.cli import main as cli_main  # noqa: E402
from support import OK, Fixture  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Long enough that a loaded CI box is never the reason a barrier breaks, short
# enough that a genuine deadlock fails the suite instead of hanging it. A
# concurrency test that can hang is a concurrency test that gets deleted.
BARRIER_TIMEOUT = 60.0

# Distinctive on purpose: every assertion below turns on whether one unit's
# bytes appear where only the other unit's should, so the markers must not be
# substrings of anything the package renders on its own.
ALPHA_MARK = "ALPHA_ONLY_MARKER_7f3a"
BETA_MARK = "BETA_ONLY_MARKER_91c2"


# --------------------------------------------------------------------------- #
# reading a review package back
#
# The package is the product under test, so it is parsed the way a reviewer
# reads it — by its headings — rather than by asking `review.py` what it meant
# to write. A parser that shared code with the renderer would agree with it
# even when both were wrong.
# --------------------------------------------------------------------------- #

def section_lines(text, heading):
    """The lines under one `## heading`, up to the next `## `."""
    lines = text.splitlines()
    try:
        start = lines.index(f"## {heading}")
    except ValueError:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return out


def changed_paths(text):
    """`## Files changed` as a set of paths, dropping the added/modified verb."""
    found = set()
    for line in section_lines(text, "Files changed"):
        if not line.startswith("- "):
            continue
        parts = line[2:].split()
        if len(parts) == 2 and parts[0] in ("added", "modified", "deleted"):
            found.add(parts[1])
    return found


def violation_paths(text):
    """`## Scope violations` as a set of paths. Empty when none were found —
    the section always exists, and says so in prose when it is empty."""
    return {
        line[2:].strip() for line in section_lines(text, "Scope violations")
        if line.startswith("- ")
    }


def diff_chunks(text):
    """`## Diff` as {path: fenced chunk}, keyed by the side that exists.

    A chunk's own `--- before/<path>` / `+++ after/<path>` headers are what
    name it, so this reads the package exactly as a reviewer would: whatever
    the diff says it is about.
    """
    chunks, current = {}, None
    for line in text.splitlines():
        if line.startswith("```diff"):
            current = []
            continue
        if current is not None and line.startswith("```"):
            body = "\n".join(current)
            name = None
            for prefix in ("+++ after/", "--- before/"):
                for candidate in current:
                    if candidate.startswith(prefix):
                        name = candidate[len(prefix):].strip()
                        break
                if name:
                    break
            if name:
                chunks[name] = body
            current = None
            continue
        if current is not None:
            current.append(line)
    return chunks


def tree_bytes(root):
    """Every file under `root` as {relative path: bytes}. The comparison unit
    for "this store did not change" — a dict of names would miss a rewrite."""
    root = Path(root)
    if not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            key = str(path.relative_to(root)).replace(os.sep, "/")
            out[key] = path.read_bytes()
    return out


class ClaimsFixture(Fixture):
    """A throwaway ledger with hand-written unit files.

    The units are written directly rather than scaffolded through
    `ctx plan-unit`, the same shortcut `test_cli_wiring.py` takes: what is
    under test is what the system does with a unit already on disk.
    """

    slug = "claims"

    def unit(self, name, *, owns=("src/a.py",), reads=(), budget=1000,
             checks=None, wave=1):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": list(owns),
                "reads": list(reads), "forbid": [], "budget_tokens": budget,
                "status": "pending", "wave": wave,
                "verify": list(checks if checks is not None
                               else [{"kind": "cmd", "run": OK}]),
            },
            "## Objective\nDo the one thing this unit owns.\n\n"
            "## Acceptance criteria\n1. It does that thing.\n",
        ).write(path)
        return plan_mod.Unit(path, frontmatter.read(path))

    def overwrite_config(self, patch):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data.update(patch)
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        self.config = config_mod.load(self.layout)
        return self.config


# --------------------------------------------------------------------------- #
# Claim 1 — two units in one wave, run concurrently, keep their own packages
# --------------------------------------------------------------------------- #

class ConcurrentWaveFixture(ClaimsFixture):
    """Two units of one wave, driven through a real barrier-forced overlap.

    Shared by the two classes below — the one that proves what holds and the
    one that pins what does not — so both are arguing about the same run.
    """

    def concurrently(self, work, units):
        """Run `work(unit, barrier)` for each unit in its own thread.

        Errors are collected rather than raised, because an exception inside a
        thread is otherwise invisible to unittest — the test would pass with
        half of it never having run.
        """
        barrier = threading.Barrier(len(units))
        errors = []

        def target(unit):
            try:
                work(unit, barrier)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(f"{getattr(unit, 'name', unit)}: {exc!r}")
                barrier.abort()

        threads = [threading.Thread(target=target, args=(unit,), daemon=True)
                   for unit in units]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=BARRIER_TIMEOUT * 2)
            self.assertFalse(thread.is_alive(), "a wave thread never finished")
        self.assertEqual(errors, [])

    def wave_of_two(self):
        """Two units of one wave, snapshotted, written and reviewed at once.

        Every step is separated by a barrier, so the two threads are inside
        `snapshot.capture` together, then writing together, then inside
        `review.build` together. Sequential execution — the case nobody
        doubted — cannot satisfy this shape.
        """
        alpha = self.unit("01-alpha", owns=["src/alpha.py"])
        beta = self.unit("02-beta", owns=["src/beta.py"])
        self.write("src/alpha.py", "alpha = 1\n")
        self.write("src/beta.py", "beta = 1\n")

        marks = {"01-alpha": ALPHA_MARK, "02-beta": BETA_MARK}
        owned = {"01-alpha": "src/alpha.py", "02-beta": "src/beta.py"}
        packages = {}

        def work(unit, barrier):
            barrier.wait(BARRIER_TIMEOUT)
            review_mod.capture_before(
                self.layout, self.config, unit, self.slug, self.root
            )
            barrier.wait(BARRIER_TIMEOUT)
            path = self.root / owned[unit.name]
            path.write_text(
                path.read_text(encoding="utf-8") + f"{marks[unit.name]} = 2\n",
                encoding="utf-8",
            )
            barrier.wait(BARRIER_TIMEOUT)
            built, stats, problem = review_mod.build(
                self.layout, self.config, unit, self.slug, self.root
            )
            packages[unit.name] = (built, stats, problem)

        self.concurrently(work, [alpha, beta])
        return alpha, beta, packages


class TestConcurrentUnitsKeepTheirOwnReviewPackage(ConcurrentWaveFixture):
    """The argument for snapshots over commit ranges, asserted at last.

    `review.py`'s docstring makes a specific promise: "a whole wave can be
    reviewed concurrently with each unit's diff staying clean". A commit range
    cannot do this — two units committing into one branch interleave into a
    range that nothing can untangle afterwards, so each unit's review would
    contain the other's commits with no mechanical way to tell whose work was
    whose. The snapshot substrate is supposed to make that a non-question,
    because each unit's package is reconstructed from two captures keyed to
    that unit alone.

    That was the whole reason for the design and it was never asserted. What
    is proved here is the part the design rests on: with both units captured,
    both writing, and both rendered *at the same time* — barrier-forced, not
    merely started together — each package attributes to its unit exactly its
    own owned change, quotes its own bytes, and quotes none of the sibling's
    inside the scope it owns. Neither unit's snapshot store, blob directory or
    package file is touched by the other.
    """

    def test_each_package_carries_its_own_owned_change_and_not_the_siblings(self):
        alpha, beta, packages = self.wave_of_two()

        for unit in (alpha, beta):
            built, _stats, problem = packages[unit.name]
            self.assertEqual(problem, "", f"{unit.name} failed to build a package")
            self.assertTrue(built.is_file())

        alpha_text = packages["01-alpha"][0].read_text(encoding="utf-8")
        beta_text = packages["02-beta"][0].read_text(encoding="utf-8")

        # Two files, never one shared one: the package path is keyed by plan,
        # unit and round, which is what stops a concurrent wave from having a
        # last writer at all.
        self.assertNotEqual(packages["01-alpha"][0], packages["02-beta"][0])
        self.assertIn("unit `01-alpha`", alpha_text.splitlines()[0])
        self.assertIn("unit `02-beta`", beta_text.splitlines()[0])

        # The claim itself: within the scope each unit declared, the two
        # packages are disjoint, and each holds exactly that unit's change.
        alpha_in_scope = {p for p in changed_paths(alpha_text)
                          if snapshot_mod.covers(p, alpha.owns)}
        beta_in_scope = {p for p in changed_paths(beta_text)
                         if snapshot_mod.covers(p, beta.owns)}
        self.assertEqual(alpha_in_scope, {"src/alpha.py"})
        self.assertEqual(beta_in_scope, {"src/beta.py"})
        self.assertEqual(alpha_in_scope & beta_in_scope, set())

        # And the bytes: each unit's own diff quotes its own marker, and the
        # sibling's marker is nowhere inside it. This is the assertion a
        # commit range cannot make at all.
        alpha_own = diff_chunks(alpha_text)["src/alpha.py"]
        beta_own = diff_chunks(beta_text)["src/beta.py"]
        self.assertIn(ALPHA_MARK, alpha_own)
        self.assertNotIn(BETA_MARK, alpha_own)
        self.assertIn(BETA_MARK, beta_own)
        self.assertNotIn(ALPHA_MARK, beta_own)

    def test_neither_units_snapshot_store_is_touched_by_the_other(self):
        """The substrate underneath the package, checked for crossing.

        A package can only stay clean if the captures it is built from stayed
        separate. Both threads write into `.ctx/runtime/snapshots/` at the same
        moment, so this pins that the four directories involved — a before and
        an after per unit — carry their own unit's content set and nothing
        else, and that the blobs stored for one unit are not visible under the
        other's key.
        """
        alpha, beta, _packages = self.wave_of_two()

        for unit, mine, theirs, mark, their_mark in (
            (alpha, "src/alpha.py", "src/beta.py", ALPHA_MARK, BETA_MARK),
            (beta, "src/beta.py", "src/alpha.py", BETA_MARK, ALPHA_MARK),
        ):
            before_key = review_mod.before_key(self.slug, unit.name)
            after_key = review_mod.after_key(self.slug, unit.name, 1)
            before = snapshot_mod.load(self.layout, before_key)
            after = snapshot_mod.load(self.layout, after_key)
            self.assertIsNotNone(before, f"{unit.name} lost its before snapshot")
            self.assertIsNotNone(after, f"{unit.name} lost its after snapshot")

            # The declared content set is the unit's own, in both captures.
            self.assertEqual(before["content_paths"], list(unit.owns) + list(unit.reads))
            self.assertEqual(after["content_paths"], list(unit.owns) + list(unit.reads))

            # Its own bytes are stored under its own key, with its own marker.
            self.assertIn(mine, after["stored"])
            self.assertIn(mark, snapshot_mod.stored_text(self.layout, after_key, mine))

            # The sibling's bytes were never stored as part of this unit's
            # *declared* capture. (`review.build` fills the sibling's path in
            # afterwards, as a named violation — see the next class — so this
            # asserts against the capture, which is the shared moment.)
            self.assertNotIn(theirs, before["stored"])
            self.assertIsNone(
                snapshot_mod.stored_text(self.layout, before_key, theirs),
                "one unit's pre-dispatch capture stored a sibling's content",
            )
            self.assertNotIn(
                their_mark,
                snapshot_mod.stored_text(self.layout, before_key, mine) or "",
            )

    def test_a_units_own_diff_is_identical_whether_or_not_a_sibling_ran(self):
        """Concurrency changes nothing about what a unit's own diff says.

        The sharpest form of the claim: build the same unit's package twice —
        once in a wave where a sibling was capturing, writing and rendering at
        the same instant, and once alone in a project where no sibling exists
        at all — and the chunk attributed to the path it owns is byte-identical.
        Anything shared between concurrent builds (a blob directory, a cached
        manifest, a last-writer temp file) would show up here as a difference,
        and a commit range would differ in both directions.
        """
        alpha, _beta, packages = self.wave_of_two()
        concurrent_chunk = diff_chunks(
            packages["01-alpha"][0].read_text(encoding="utf-8")
        )["src/alpha.py"]

        solo_chunk = self._solo_alpha_chunk()
        self.assertEqual(
            concurrent_chunk, solo_chunk,
            "a sibling running concurrently changed what this unit's own diff said",
        )

    def _solo_alpha_chunk(self):
        """The same unit, the same change, in a project with no wave at all.

        A second throwaway ledger rather than a reset of this one: the control
        has to be a tree where the sibling never existed, not a tree where it
        has been tidied away.
        """
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        (root / ".git").mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main(["--cwd", str(root), "init"]), 0)
        layout = paths.Layout(root / ".ctx")
        config = config_mod.load(layout)

        directory = plan_mod.units_dir(layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        source = plan_mod.units_dir(self.layout, self.slug) / "01-alpha.md"
        (directory / "01-alpha.md").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        unit = plan_mod.Unit(directory / "01-alpha.md",
                             frontmatter.read(directory / "01-alpha.md"))

        target = root / "src" / "alpha.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha = 1\n", encoding="utf-8")
        review_mod.capture_before(layout, config, unit, self.slug, root)
        target.write_text(f"alpha = 1\n{ALPHA_MARK} = 2\n", encoding="utf-8")
        built, _stats, problem = review_mod.build(
            layout, config, unit, self.slug, root
        )
        self.assertEqual(problem, "")
        return diff_chunks(built.read_text(encoding="utf-8"))["src/alpha.py"]


class TestASiblingsDeclaredPathIsNotThisUnitsViolation(ConcurrentWaveFixture):
    """The limit this class used to pin has been closed — kept as the regression.

    When `11-proofs` first ran, disjointness held for everything a unit *owned*
    but not for the whole package text, and the reason was structural:
    `snapshot.capture` fingerprints every file in the project, precisely so a
    write to a path nobody declared is still visible. In a shared working tree —
    where `subagent`-tier units run, one tree, several Task calls — a sibling's
    write to its own declared path landed between this unit's before and after
    captures, and `snapshot.out_of_scope` had no way to know that some *other*
    unit in the wave had declared it. It was reported here as a scope violation,
    with its bytes quoted, under prose telling the reviewer it was "not open to
    argument. Report it as Critical."

    That made every wave of two or more units deadlock its own gate: `review` is
    a mechanical gate check and Critical findings block, so N units produced N-1
    false Criticals apiece and none of them could pass.

    `review.wave_scope` closed it — the review now knows its wave, and a path
    declared by a sibling still running is not this unit's violation. The
    assertions below are inverted from the ones that pinned the bug, and this
    class is now the regression test for the fix. What has NOT changed, and is
    asserted alongside it in `tests/test_sibling_scope.py`, is that a path
    declared by *nobody* is still a violation and still Critical — the fix
    narrowed the check to the truth, it did not weaken it.
    """

    def test_a_siblings_declared_path_is_not_this_units_violation(self):
        alpha, beta, packages = self.wave_of_two()
        alpha_text = packages["01-alpha"][0].read_text(encoding="utf-8")
        beta_text = packages["02-beta"][0].read_text(encoding="utf-8")

        # Neither package blames the other unit's declared path any more.
        self.assertEqual(violation_paths(alpha_text), set())
        self.assertEqual(violation_paths(beta_text), set())
        self.assertEqual(packages["01-alpha"][1]["out_of_scope"], 0)
        self.assertEqual(packages["02-beta"][1]["out_of_scope"], 0)

        # The reason it is not a violation: the sibling declared it. That is
        # what `wave_scope` now knows and `out_of_scope` alone never could.
        self.assertTrue(snapshot_mod.covers("src/beta.py", beta.owns))
        self.assertTrue(snapshot_mod.covers("src/alpha.py", alpha.owns))

        # The package says what it actually checked, rather than repeating an
        # absolute rule the code no longer applies: it names the sibling as
        # running alongside, and says a path nobody declared would still be
        # listed. Documentation that outlives its behaviour is how a reviewer
        # learns to distrust the whole package.
        self.assertIn("running alongside", alpha_text)
        self.assertIn("A path nobody in the wave declared would still be listed",
                      alpha_text)

        # And it still never blends: the sibling's bytes never appear inside
        # the diff chunk for a path this unit owns. That was true before the
        # fix and is the half of claim 1 that always held.
        self.assertNotIn(BETA_MARK, diff_chunks(alpha_text)["src/alpha.py"])
        self.assertNotIn(ALPHA_MARK, diff_chunks(beta_text)["src/beta.py"])


# --------------------------------------------------------------------------- #
# Claim 2 — a session killed mid-fix-loop resumes intact
# --------------------------------------------------------------------------- #

# Walks into the middle of a fix loop and then stops, alive, waiting to be
# killed. Everything it writes goes through the ordinary mutators — no test
# helper writes any of this state — because what is being proved is that those
# mutators commit as they go rather than at the end of a turn.
CHILD_SOURCE = '''
import sys, time
from pathlib import Path

sys.path.insert(0, {repo!r})

from ctx import findings as findings_mod, paths, state as state_mod

root, slug, unit = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
layout = paths.Layout(root / ".ctx")

# Round 1: the reviewer raises two findings against the unit under review.
ledger = findings_mod.load(layout, slug, unit)
ledger.add("critical", "the refresh token is written to the log",
           where="src/auth.py:42")
ledger.add("important", "no test covers the expiry path",
           where="tests/test_auth.py")

# The implementer closes one of them, and the loop moves to round 2 with the
# other still open. This is the state a session is in when it is interrupted.
ok, problem = ledger.set_status(2, "addressed")
assert ok, problem
ledger.bump_round()

# And the session pointer: this is the unit under review, at L2.
state_mod.update(layout, level="2", plan=slug, unit=unit)

sys.stdout.write("READY\\n")
sys.stdout.flush()
while True:
    time.sleep(0.05)
'''

# A third process, sharing nothing with the first but the filesystem. It reads
# the ledger back through the same public loaders any session would use.
READER_SOURCE = '''
import json, sys
from pathlib import Path

sys.path.insert(0, {repo!r})

from ctx import findings as findings_mod, paths, state as state_mod

root, slug, unit = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
layout = paths.Layout(root / ".ctx")
ledger = findings_mod.load(layout, slug, unit)
current = state_mod.load(layout)

print(json.dumps({{
    "pid": None,
    "round": ledger.round,
    "unit": ledger.unit,
    "plan": ledger.slug,
    "open": [f.as_dict() for f in ledger.open_findings()],
    "all": [f.as_dict() for f in ledger.findings],
    "summary": ledger.summary(),
    "state": {{
        "level": current.get("level"),
        "plan": current.get("plan"),
        "unit": current.get("unit"),
    }},
}}))
'''


class TestKilledSessionResumesIntact(ClaimsFixture):
    """A session killed mid-fix-loop, and what is still there afterwards.

    `findings.py` opens with the reason this file exists at all: "A finding
    that lives only in a transcript is gone by the third fix round", so
    "findings live in a file, and every state change is a write". That is a
    durability claim, and durability is only ever proved by losing the process.

    So the interruption here is real. A child process is started, walks into
    the middle of a loop — two findings raised, one addressed, round advanced
    to 2, the session pointer set to the unit under review — announces that it
    got there, and is then killed from the outside with no cooperation: no
    signal handler, no `finally`, no flush, no atexit. The state is then read
    back in a *third* process, which shares no memory with the one that wrote
    it and can only see what actually reached the disk.

    Calling a resume path in the writing process would prove none of this; it
    would prove that Python objects still exist, which was never the question.
    """

    unit_name = "03-auth"

    def _script(self, name, source):
        """Write a helper script outside the project.

        Outside deliberately: a file inside the fixture's tree would be part of
        the project these very modules snapshot and gate on.
        """
        path = Path(self.untracked) / name
        path.write_text(source.format(repo=str(REPO_ROOT)), encoding="utf-8")
        return path

    def _run(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), str(self.root), self.slug, *args],
            cwd=str(self.root), env=dict(os.environ),
            capture_output=True, text=True,
        )

    def _kill_a_session_mid_loop(self):
        """Start the child, wait for it to reach the middle, kill it dead."""
        self.unit(self.unit_name, owns=["src/auth.py"])
        script = self._script("mid_loop.py", CHILD_SOURCE)
        child = subprocess.Popen(
            [sys.executable, str(script), str(self.root), self.slug, self.unit_name],
            cwd=str(self.root), env=dict(os.environ),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            ready = child.stdout.readline()
            self.assertEqual(
                ready.strip(), "READY",
                "the child never reached the middle of the loop: "
                + (child.stderr.read() if child.poll() is not None else ""),
            )
            self.assertIsNone(child.poll(), "the child exited on its own")
            child.kill()
        finally:
            # Belt and braces: a failed assertion above must not leave a live
            # process behind. Guarded because terminating an already-reaped
            # process is an error on Windows and a no-op everywhere else.
            if child.poll() is None:
                try:
                    child.kill()
                except OSError:  # pragma: no cover - it exited between the two
                    pass
            child.wait(timeout=BARRIER_TIMEOUT)
        # Killed, not exited: no clean shutdown ran, so nothing on disk was
        # written by anything other than the mutators themselves. POSIX reports
        # the signal as -9; Windows `TerminateProcess` reports 1. Both say the
        # same thing, which is why only "not a clean exit" is asserted.
        self.assertIsNotNone(child.returncode)
        self.assertNotEqual(child.returncode, 0)
        child.stdout.close()
        child.stderr.close()
        return child

    def _read_back(self):
        reader = self._script("read_back.py", READER_SOURCE)
        result = self._run(reader, self.unit_name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_round_open_findings_and_unit_under_review_all_survive(self):
        child = self._kill_a_session_mid_loop()
        found = self._read_back()

        # The round number: the loop was in round 2 when it died.
        self.assertEqual(found["round"], 2)
        self.assertIn("round 2/3", found["summary"])

        # The open findings: one still open, with its severity, its summary and
        # the file:line it was raised against; the closed one still closed.
        self.assertEqual(len(found["open"]), 1)
        still_open = found["open"][0]
        self.assertEqual(still_open["id"], 1)
        self.assertEqual(still_open["severity"], "critical")
        self.assertEqual(still_open["status"], "open")
        self.assertEqual(still_open["summary"],
                         "the refresh token is written to the log")
        self.assertEqual(still_open["where"], "src/auth.py:42")
        closed = [f for f in found["all"] if f["id"] == 2]
        self.assertEqual([f["status"] for f in closed], ["addressed"])

        # The unit under review: both in the ledger and in the session pointer.
        self.assertEqual(found["unit"], self.unit_name)
        self.assertEqual(found["plan"], self.slug)
        self.assertEqual(found["state"]["unit"], self.unit_name)
        self.assertEqual(found["state"]["plan"], self.slug)
        self.assertEqual(found["state"]["level"], "2")

        # And the process really is gone — this was not a simulation.
        self.assertIsNotNone(child.returncode)
        self.assertNotEqual(child.returncode, 0)

    def test_a_fresh_cli_process_reports_the_loop_the_killed_session_was_in(self):
        """The read-back a person actually does after losing a session.

        Structured state surviving is necessary but not sufficient: what the
        next session sees is whatever `ctx` prints. This runs the real CLI in
        its own process — `python -m ctx`, nothing from this one — and checks
        that it names the round, the open finding and the unit.
        """
        self._kill_a_session_mid_loop()
        result = subprocess.run(
            [sys.executable, "-m", "ctx", "--cwd", str(self.root),
             "findings", self.unit_name, "--plan", self.slug],
            cwd=str(self.root),
            env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
            capture_output=True, text=True,
        )
        # Exit 1 is the answer, not a failure: `ctx findings` refuses while a
        # blocking finding is open, and the finding raised before the kill is
        # still open. A fresh process that exited 0 here would mean the loop
        # had been forgotten, which is the thing being guarded against.
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("round 2/3", result.stdout)
        self.assertIn("the refresh token is written to the log", result.stdout)
        self.assertIn("src/auth.py:42", result.stdout)
        self.assertIn("1 blocking finding(s)", result.stdout)

        briefing = subprocess.run(
            [sys.executable, "-m", "ctx", "--cwd", str(self.root), "status"],
            cwd=str(self.root),
            env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
            capture_output=True, text=True,
        )
        self.assertEqual(briefing.returncode, 0, briefing.stderr)
        self.assertIn(self.unit_name, briefing.stdout)


# --------------------------------------------------------------------------- #
# Claim 3 — an escalated dispatch cannot widen what the machine has accepted
# --------------------------------------------------------------------------- #

class TestEscalationCannotWidenTrust(ClaimsFixture):
    """The answer to the plan's sixth open question, asserted instead of argued.

    `trust.py` is the boundary between a ledger that travels — `ctx.yaml` and
    every unit file are committed — and a machine that runs shell commands out
    of it. Acceptance is keyed on `trust.command_id`: a digest of the command,
    its cwd and its env, and nothing else. Escalation, meanwhile, changes the
    *model named on a dispatch line*. The two are supposed to be unable to
    reach each other.

    Unable, but never checked, and the failure would be quiet: an escalation
    path that resolved checks slightly differently — a new cwd, an inherited
    env var, a phase check folded in for the dearer tier — would produce
    command ids that had never been reviewed, and `verify.run` would either
    run something unaccepted or report a configuration error, which warns and
    passes. Neither is visible in the output a user reads.

    So the assertion is byte equality, taken twice around a real escalation
    driven through `ctx review`: the whole global trust store's file tree, the
    accepted map, the resolved set of commands the ledger declares, each one's
    acceptance verdict, and the unit's own resolved check list.

    The store deliberately lives *outside* the repository, under
    `paths.global_root()`, keyed by the project's absolute path — so this test
    first proves it is looking at the fixture's isolated store and not the
    developer's real `~/.claude/ctx`. A trust test that polluted the machine
    store would be worse than no trust test.
    """

    unit_name = "04-rotate"

    # Accepted below. Distinguishable from the second command so the assertions
    # can tell which one moved.
    ACCEPTED = {"kind": "cmd", "run": OK}

    def setUp(self):
        super().setUp()
        self.overwrite_config({
            "models": {
                "runner": "sonnet", "reviewer": "opus", "verifier": "opus",
                "tiers": ["haiku", "sonnet", "opus"],
                "escalate_on_failed_round": True,
            },
            # A command the ledger declares and this machine has *not*
            # accepted. Escalation must not quietly turn it into one it has.
            "verify": [{"kind": "cmd", "run": self.py("pass  # never accepted")}],
        })

    def _isolated_store(self):
        """The store this test writes to is the fixture's, not the machine's."""
        store = trust.path_for(self.layout)
        self.assertTrue(
            str(store).startswith(str(Path(self.untracked).resolve()))
            or str(store).startswith(str(Path(self.untracked))),
            f"the trust store under test is not isolated: {store}",
        )
        self.assertEqual(paths.global_root(), Path(self.untracked) / "global")
        return store

    def _resolved(self, unit):
        """Everything that decides what will be run, and whether it may be.

        Three separate views, because a widening could hide in any one of them:
        the ledger-wide declared command set (`trust.declared`), the acceptance
        verdict for each of those, and the unit's own gate list as `verify.run`
        would order it.
        """
        accepted = trust.load(self.layout)
        declared = trust.declared(self.layout, self.config)
        return {
            "accepted": json.dumps(accepted, sort_keys=True),
            "declared": json.dumps(
                sorted(
                    (trust.command_id(check), verify.label_of(check), str(source))
                    for check, source in declared
                ),
                sort_keys=True,
            ),
            "verdicts": json.dumps(
                {trust.command_id(check): trust.is_accepted(check, accepted)
                 for check, _source in declared},
                sort_keys=True,
            ),
            "unit_checks": json.dumps(verify.ordered(unit.checks), sort_keys=True),
        }

    def test_escalating_a_round_changes_the_model_and_nothing_else(self):
        unit = self.unit(self.unit_name, owns=["src/rotate.py"], budget=1000,
                         checks=[dict(self.ACCEPTED), {"kind": "review"}])
        self.trust([dict(self.ACCEPTED)])
        store = self._isolated_store()
        self.assertTrue(store.is_file(), "nothing was accepted, so nothing is at risk")

        self.write("src/rotate.py", "rotate = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/rotate.py", "rotate = 2\n")
        self.assertEqual(
            self.cli("review", unit.name, "--plan", self.slug)[0], 0
        )

        before_store = tree_bytes(paths.global_root())
        before_bytes = store.read_bytes()
        before_resolved = self._resolved(unit)
        before_model = dispatch.model_for(self.config, unit, role="runner", round=1)

        # A blocking finding, so the next `ctx review` is a failed round rather
        # than a re-read — that is the only thing that escalates.
        code, out = self.cli("findings", unit.name, "--plan", self.slug,
                             "--add", "critical", "--summary", "still broken")
        self.assertEqual(code, 0, out)
        self.write("src/rotate.py", "rotate = 3\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)

        # The escalation actually happened — otherwise the equalities below
        # would be true for the boring reason that nothing moved.
        self.assertIn("escalated", out)
        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(ledger.round, 2)
        self.assertEqual(len(ledger.escalations), 1)
        moved = ledger.escalations[0]
        self.assertNotEqual(moved.from_model, moved.to_model)
        after_model = dispatch.model_for(self.config, unit, role="runner", round=2)
        self.assertNotEqual(before_model, after_model)
        self.assertEqual(after_model, moved.to_model)

        # And nothing about what may be run moved with it.
        self.assertEqual(store.read_bytes(), before_bytes,
                         "the trust store changed across an escalation")
        self.assertEqual(tree_bytes(paths.global_root()), before_store,
                         "an escalation wrote somewhere under the global root")
        self.assertEqual(self._resolved(unit), before_resolved,
                         "an escalation changed the resolved verify-command set")

    def test_a_command_the_machine_never_accepted_is_still_unaccepted_after(self):
        """The widening that would matter, checked directly.

        Byte equality of the store already covers this, but only by implication
        — and the implication is the whole question the open question asked. So
        it is asserted on its own terms: the unaccepted command declared in
        `ctx.yaml` is unaccepted before the escalation and unaccepted after,
        and the accepted one is neither revoked nor duplicated.
        """
        unit = self.unit(self.unit_name, owns=["src/rotate.py"], budget=1000,
                         checks=[dict(self.ACCEPTED), {"kind": "review"}])
        self.trust([dict(self.ACCEPTED)])
        self._isolated_store()
        stranger = (self.config.get("verify") or [])[0]

        def verdicts():
            accepted = trust.load(self.layout)
            return (
                trust.is_accepted(self.ACCEPTED, accepted),
                trust.is_accepted(stranger, accepted),
                sorted(accepted),
            )

        self.write("src/rotate.py", "rotate = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/rotate.py", "rotate = 2\n")
        self.assertEqual(self.cli("review", unit.name, "--plan", self.slug)[0], 0)
        before = verdicts()
        self.assertEqual(before[0], True)
        self.assertEqual(before[1], False)

        self.assertEqual(
            self.cli("findings", unit.name, "--plan", self.slug,
                     "--add", "critical", "--summary", "still broken")[0], 0
        )
        self.write("src/rotate.py", "rotate = 3\n")
        code, out = self.cli("review", unit.name, "--plan", self.slug)
        self.assertEqual(code, 0, out)
        self.assertIn("escalated", out)

        self.assertEqual(verdicts(), before)

    def test_escalation_is_recorded_in_the_ledger_and_not_in_the_trust_store(self):
        """Where the record of an escalation is allowed to live.

        An escalation is a fact about a round, so it belongs in the unit's
        findings file. The failure this guards against is a well-meaning
        shortcut: recording the dearer tier next to the commands it will run,
        which would put a model name inside the one file whose entire purpose
        is to be the thing an attacker cannot write.
        """
        unit = self.unit(self.unit_name, owns=["src/rotate.py"], budget=1000,
                         checks=[dict(self.ACCEPTED), {"kind": "review"}])
        self.trust([dict(self.ACCEPTED)])
        store = self._isolated_store()

        self.write("src/rotate.py", "rotate = 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/rotate.py", "rotate = 2\n")
        self.assertEqual(self.cli("review", unit.name, "--plan", self.slug)[0], 0)
        self.assertEqual(
            self.cli("findings", unit.name, "--plan", self.slug,
                     "--add", "critical", "--summary", "still broken")[0], 0
        )
        self.write("src/rotate.py", "rotate = 3\n")
        self.assertEqual(self.cli("review", unit.name, "--plan", self.slug)[0], 0)

        ledger = findings_mod.load(self.layout, self.slug, unit.name)
        self.assertEqual(len(ledger.escalations), 1)
        recorded = ledger.escalations[0]

        ledger_text = findings_mod.path_for(
            self.layout, self.slug, unit.name
        ).read_text(encoding="utf-8")
        self.assertIn(recorded.to_model, ledger_text)

        store_text = store.read_text(encoding="utf-8")
        for tier in (self.config.get("models") or {}).get("tiers") or []:
            self.assertNotIn(
                tier, store_text,
                "a model tier reached the trust store, which records commands only",
            )


if __name__ == "__main__":
    unittest.main()
