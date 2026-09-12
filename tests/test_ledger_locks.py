"""Three read-modify-writes that used to lose updates, and the proof they don't.

The audit measured the telemetry one: twenty concurrent records across a forced
rotate, **zero** survivors. `_rotate` reads the tail and then replaces the file
with it, so every append that lands between the read and the replace is thrown
away by the replace. The findings and phases ledgers had the same shape without
the measurement — load, mutate the object that was loaded, render the *whole*
document back — so a reviewer recording a finding and an implementer resolving
one simply erased each other.

Every claim here carries its positive control, and every control is
**deterministic**: the interleaving that loses the update is forced by a
rendezvous on marker files, not hoped for by sleeping and racing. That matters
more here than it usually would. These controls are the only evidence that the
defect was ever real, so a control that goes red on a loaded machine is a
control somebody will quietly loosen while unblocking CI — and a loosened
control is one that can no longer fail, which is the fail-green pattern this
whole remediation exists to delete. A threshold like "fewer than half of twenty
survived" drifts with machine load. "This append was in the file, and the
rewrite that straddled it removed it" does not drift, because nothing in it is
timed.

The three controls, and what they assert:

* telemetry — twenty appends are made *while* a rotate is between its read and
  its replace (the rotate blocks until all twenty are on disk, and asserts it
  can see all twenty). All twenty are gone afterwards: **0 of 20**, the audit's
  own number, now by construction rather than by luck. With `record`'s lock
  around both halves, twenty concurrent records all survive: **20 of 20**;
* findings — a reviewer and an implementer both load, and neither writes until
  both have loaded. Unserialised, the second save renders the state it loaded
  and erases the first: **1 of the 2 writes survives**. Serialised: **2 of 2**;
* phases — the same, with two recorded runs of one phase: **1 of 2** before,
  **2 of 2** after.

A sequential loop would pass both ways and prove nothing, so every race here
forks real processes.
"""

import contextlib
import json
import os
import subprocess
import sys
import time
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    findings as findings_mod, frontmatter, lock, phases as phases_mod,
    plan as plan_mod, telemetry,
)
from support import OK, Fixture  # noqa: E402


REPO = str(Path(__file__).resolve().parent.parent)

# How long the artificial rewrite takes in the serialised burst. Only ever a
# lower bound on how long the lock is held: twenty of them in a row is still far
# inside the five-second timeout, so no worker fails open and appends unheld.
WINDOW = 0.02

# A ceiling on every rendezvous, so a worker that died takes the test down with
# a message instead of hanging the suite. It is not a timing assumption: no
# assertion depends on anything happening *before* it, only on the test not
# waiting forever.
DEADLINE = 120.0

# Each worker polls for its marker files. Short enough not to add measurable
# latency to a handshake, long enough not to spin a core.
POLL = 0.005


def _await(workers, case):
    for worker in workers:
        _out, err = worker.communicate(timeout=DEADLINE)
        case.assertEqual(worker.returncode, 0, err.decode())


def _wait_for(case, paths, why):
    """Block until every path exists. The rendezvous, parent side."""
    deadline = time.monotonic() + DEADLINE
    for path in paths:
        while not path.exists():
            if time.monotonic() > deadline:
                case.fail(f"timed out waiting for {why}: {path} never appeared")
            time.sleep(POLL)


# The rendezvous, worker side. Inlined into every worker script: a worker is a
# `python -c` with only `ctx` importable, so it cannot call the helper above.
_RENDEZVOUS = '''
def wait_for(*paths):
    deadline = time.monotonic() + {deadline!r}
    for path in paths:
        while not path.exists():
            assert time.monotonic() < deadline, "timed out waiting for %s" % path
            time.sleep({poll!r})
'''


# --------------------------------------------------------------------------- #
# telemetry
# --------------------------------------------------------------------------- #

_APPEND_WORKER = '''
import json, sys, time
sys.path.insert(0, {repo!r})
from ctx import paths, telemetry
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
index = int(sys.argv[1])
target = telemetry.path_for(layout)

# The rotate under test opens this once it has read the tail and is about to
# replace the file. Everything below therefore happens *inside* that window.
wait_for(layout.runtime / "rotate-has-read")

# `record`'s own last two lines: the append, and nothing else. What is on trial
# is the rotate that straddles it, so the appender stays as small as the thing
# the audit measured — one line of JSON arriving at a file that is being
# rewritten from a copy taken before it existed.
with target.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"event": "probe", "ms": 1.0, "probe": index}},
                            sort_keys=True) + "\\n")
(layout.runtime / ("appended-%d" % index)).write_text("1")
'''


_RECORD_WORKER = '''
import sys, time
sys.path.insert(0, {repo!r})
from ctx import atomic, paths, telemetry
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
telemetry.MAX_BYTES = {max_bytes!r}
telemetry.KEEP_LINES = {keep!r}
index = int(sys.argv[1])

# Widen the rewrite window rather than narrowing it: the fix has to hold under a
# rotate that takes a visible amount of time, not just under one that is over
# before anybody else is scheduled.
real = atomic.write_text
def slow(path, text, encoding="utf-8"):
    time.sleep({window!r})
    return real(path, text, encoding=encoding)
telemetry.atomic.write_text = slow

# Contention by construction, not by startup luck: every worker announces that
# it is at the door and waits until all {probes} of them are, so a slow
# interpreter delays the burst instead of dropping out of it.
(layout.runtime / ("ready-%d" % index)).write_text("1")
wait_for(*[layout.runtime / ("ready-%d" % other) for other in range({probes})])

telemetry.record(layout, "probe", 1.0, probe=index)
'''


class TelemetryRotateTests(Fixture):
    """Criteria 1-3. The audit's own measurement, reproduced and then closed."""

    PROBES = 20        # the audit's twenty
    SEED = 120         # enough seed lines that every record forces a rotate
    MAX_BYTES = 1024
    KEEP = 50          # comfortably more than PROBES: nothing is trimmed away

    def _seed(self):
        target = telemetry.path_for(self.layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps({"event": "seed", "ms": 1.0, "n": n}) + "\n"
                    for n in range(self.SEED)),
            encoding="utf-8",
        )
        self.assertGreater(target.stat().st_size, self.MAX_BYTES,
                           "the seed must be big enough to force a rotate")
        return target

    def _survivors(self):
        found = set()
        for line in telemetry.path_for(self.layout).read_text(
                encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue   # a torn line: an unserialised run produces those too
            if isinstance(entry, dict) and entry.get("event") == "probe":
                found.add(entry.get("probe"))
        return found

    def _spawn(self, script, count):
        return [
            subprocess.Popen([sys.executable, "-c", script, str(index)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for index in range(count)
        ]

    def test_an_unlocked_rotate_discards_every_concurrent_append(self):
        """The positive control: the window is real, and it swallows whatever
        is in it.

        Deterministic, with no timing in it anywhere. `_rotate` — the real one,
        called directly, which is `record` minus the lock — reads the tail, and
        is then held at the point of replacing the file until all twenty
        appends are provably on disk. It asserts it can see all twenty before
        it replaces anything, so the run cannot pass by never having raced.
        Then it replaces the file with the copy it took before they existed.

        Load cannot change this outcome. A slow machine makes the rendezvous
        take longer; it cannot make an append that is in the file survive a
        rewrite of that file from a copy that predates it.
        """
        target = self._seed()
        script = _APPEND_WORKER.format(repo=REPO, root=str(self.layout.root),
                                       deadline=DEADLINE, poll=POLL)
        workers = self._spawn(script, self.PROBES)
        witnessed = {}
        real = telemetry.atomic.write_text

        def gated(path, text, encoding="utf-8"):
            # `_rotate` has already read the tail — this is the replace. Open
            # the window, wait for every appender, and look at the file.
            (self.layout.runtime / "rotate-has-read").write_text("1")
            _wait_for(self, [self.layout.runtime / f"appended-{index}"
                             for index in range(self.PROBES)], "the appends")
            witnessed["before"] = self._survivors()
            return real(path, text, encoding=encoding)

        with unittest.mock.patch.object(telemetry, "MAX_BYTES", self.MAX_BYTES), \
                unittest.mock.patch.object(telemetry, "KEEP_LINES", self.KEEP), \
                unittest.mock.patch.object(telemetry.atomic, "write_text", gated):
            telemetry._rotate(target)
        _await(workers, self)

        self.assertIn("before", witnessed,
                      "the rotate never reached its replace — nothing was tested")
        if os.name == "nt":
            # Windows emulates append mode rather than implementing O_APPEND,
            # so twenty processes appending to one file is not atomic there and
            # a line can be lost before the rotate ever runs. The product does
            # not care — `record` appends under the lock — but this control
            # deliberately removes the lock, so it cannot claim all twenty
            # arrived. It still proves the thing it exists to prove: whatever
            # did land was in the file, and the rewrite discarded it.
            self.assertTrue(witnessed["before"],
                            "no append landed at all — the control proves nothing")
        else:
            self.assertEqual(sorted(witnessed["before"]), list(range(self.PROBES)),
                             "the appends never landed — the control proves nothing")
        self.assertEqual(self._survivors(), set(),
                         "the rewrite did not discard the concurrent appends")

    def test_every_concurrent_record_survives_the_rotate(self):
        """The same twenty records, through `record`, which holds the telemetry
        lock across the rotate and the append. All twenty survive.

        The burst is a real one: each worker waits at a rendezvous until all
        twenty are ready, so every one of them arrives at `record` while the
        others are arriving too, whatever the machine's load did to their
        startup times. Nineteen of them therefore wait on a lock somebody else
        is holding — this is not twenty processes politely taking turns by
        accident.
        """
        self._seed()
        script = _RECORD_WORKER.format(
            repo=REPO, root=str(self.layout.root), max_bytes=self.MAX_BYTES,
            keep=self.KEEP, window=WINDOW, probes=self.PROBES,
            deadline=DEADLINE, poll=POLL,
        )
        workers = self._spawn(script, self.PROBES)
        _await(workers, self)
        self.assertEqual(sorted(self._survivors()), list(range(self.PROBES)))

    def test_the_rotate_replaces_the_file_rather_than_reopening_it_for_write(self):
        """Criterion 1's second half. A lock only serialises the writers that
        ask; the atomic replace is what protects the ones that don't."""
        target = self._oversized()
        calls = []
        real = telemetry.atomic.write_text

        def traced(path, text, encoding="utf-8"):
            calls.append(path)
            return real(path, text, encoding=encoding)

        with unittest.mock.patch.object(telemetry.atomic, "write_text", traced):
            telemetry.record(self.layout, "SessionStart", 1.0)
        self.assertEqual(calls, [target])
        self.assertLess(target.stat().st_size, telemetry.MAX_BYTES)
        self.assertEqual(telemetry.read(self.layout)[-1]["event"], "SessionStart")

    def _oversized(self):
        """A real telemetry file, over the cap, whose tail is genuinely
        smaller than the whole — one enormous line would be trimmed to
        itself and prove nothing about the rewrite."""
        target = telemetry.path_for(self.layout)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"event": "seed", "ms": 1.0, "pad": "x" * 500}) + "\n"
        count = (telemetry.MAX_BYTES // len(line)) + telemetry.KEEP_LINES + 50
        target.write_text(line * count, encoding="utf-8")
        self.assertGreater(target.stat().st_size, telemetry.MAX_BYTES)
        return target

    def test_record_takes_the_telemetry_lock_and_takes_it_once(self):
        trace = LockTrace()
        with trace.patched():
            telemetry.record(self.layout, "SessionStart", 1.0)
        self.assertEqual(trace.names, [telemetry.LOCK_NAME])
        self.assertEqual(trace.taken, [True])
        self.assertEqual(trace.max_depth_for(telemetry.LOCK_NAME), 1)

    def test_a_read_only_runtime_directory_does_not_break_record(self):
        """Criterion 3. Telemetry is measurement; it is never a reason a hook
        fails. The lock cannot be created, the append cannot be made, and
        `record` still returns."""
        runtime = self.layout.runtime
        runtime.mkdir(parents=True, exist_ok=True)
        before = runtime.stat().st_mode
        runtime.chmod(0o500)
        try:
            probe = runtime / "probe"
            try:
                probe.write_text("x")
                probe.unlink()
                self.skipTest("this filesystem/user ignores a read-only directory")
            except OSError:
                pass
            telemetry.record(self.layout, "SessionStart", 1.0)
        finally:
            runtime.chmod(before)
        self.assertFalse(telemetry.path_for(self.layout).exists())

    def test_a_lock_that_cannot_be_taken_still_records(self):
        """`lock.held` fails open, and `record` must be happy with that: a lost
        measurement is a cost, a broken session is not."""
        @contextlib.contextmanager
        def refuses(layout, name):
            yield False

        with unittest.mock.patch.object(lock, "held", refuses):
            telemetry.record(self.layout, "SessionStart", 1.0)
        self.assertEqual(telemetry.read(self.layout)[-1]["event"], "SessionStart")

    def test_a_full_disk_during_the_rotate_does_not_break_record(self):
        def no_space(*_args, **_kwargs):
            raise OSError(28, "No space left on device")

        self._oversized()
        with unittest.mock.patch.object(telemetry.atomic, "write_text", no_space):
            telemetry.record(self.layout, "SessionStart", 1.0)
        self.assertTrue(True, "no exception escaped")


# --------------------------------------------------------------------------- #
# the findings and phases ledgers
# --------------------------------------------------------------------------- #

_LEDGER_WORKER = '''
import contextlib, sys, time
sys.path.insert(0, {repo!r})
from ctx import findings, paths, phases
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
module = {{"findings": findings, "phases": phases}}[sys.argv[1]]
role, mode = sys.argv[2], sys.argv[3]

if mode == "shipped":
    # ctx 0.8.0: load once, mutate the object that was loaded, render the whole
    # document back. `_exclusive` is the lock-and-re-read this unit added, and
    # `_add`/`_set_status` underneath it are the old bodies unchanged — so
    # neutralising it reproduces the shipped code rather than approximating it.
    @contextlib.contextmanager
    def unserialised(self):
        yield False
    module.Ledger._exclusive = unserialised

ledger = module.load(layout, {slug!r}, {unit!r})

# Neither process writes until both have read. That is the whole race, and
# holding it open with marker files rather than a sleep is what stops a busy
# machine from turning the control green by letting one process finish first.
(layout.runtime / ("read-%s" % role)).write_text("1")
wait_for(layout.runtime / "read-reviewer", layout.runtime / "read-implementer")

if module is findings:
    if role == "reviewer":
        ledger.add("critical", "raised by the reviewer")
    else:
        ledger.set_status(1, "addressed", evidence="fixed by the implementer")
else:
    ledger.add("reproduce", command=role, exit_code=1)
'''


class LedgerRaceFixture(Fixture):
    slug = "auth"
    unit_name = "01-api"

    def _race(self, module, mode):
        script = _LEDGER_WORKER.format(
            repo=REPO, root=str(self.layout.root), slug=self.slug,
            unit=self.unit_name, deadline=DEADLINE, poll=POLL,
        )
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", script, module, role, mode],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for role in ("reviewer", "implementer")
        ]
        _await(workers, self)


class FindingsRaceTests(LedgerRaceFixture):
    """Criterion 5. A reviewer raising a finding while an implementer resolves
    a different one — the exact pair the report named."""

    def setUp(self):
        super().setUp()
        seed = findings_mod.load(self.layout, self.slug, self.unit_name)
        seed.add("important", "the finding the implementer is resolving")

    def _survived(self):
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        first = ledger.get(1)
        return (
            len(ledger.findings) == 2
            and first is not None and first.status == "addressed"
        )

    def test_the_0_8_0_ledger_loses_one_of_the_two_writes(self):
        """The control. Deterministic: both processes have loaded the same
        state before either writes, so whichever renders the whole document
        second writes the other's change out of existence. 1 of the 2 survives,
        every time, on any machine."""
        self._race("findings", "shipped")
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        self.assertFalse(
            self._survived(),
            "nothing was lost — the race did not fire, so the test below proves "
            f"nothing (findings={len(ledger.findings)}, "
            f"status={ledger.get(1).status})",
        )

    def test_both_the_reviewer_and_the_implementer_survive(self):
        self._race("findings", "fixed")
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        self.assertEqual(len(ledger.findings), 2,
                         "the reviewer's finding was erased")
        self.assertEqual(ledger.get(1).status, "addressed",
                         "the implementer's resolution was erased")
        self.assertEqual(ledger.get(2).severity, "critical")


class PhasesRaceTests(LedgerRaceFixture):
    """Criterion 6. Two recorded runs of one phase, from two processes."""

    def _commands(self):
        ledger = phases_mod.load(self.layout, self.slug, self.unit_name)
        return sorted(entry.command for entry in ledger.entries)

    def test_the_0_8_0_ledger_loses_one_of_the_two_entries(self):
        """The control, deterministic for the same reason as the findings one:
        both processes read the empty ledger before either writes, so the file
        ends with exactly one entry."""
        self._race("phases", "shipped")
        self.assertNotEqual(
            self._commands(), ["implementer", "reviewer"],
            "nothing was lost — the race did not fire",
        )

    def test_both_recorded_runs_survive(self):
        self._race("phases", "fixed")
        self.assertEqual(self._commands(), ["implementer", "reviewer"])


# --------------------------------------------------------------------------- #
# the shape of the locking, not just its effect
# --------------------------------------------------------------------------- #

class LockTrace:
    """Every `lock.held` call in this process: the name, whether it was taken,
    and how deep the nesting got for that name.

    `taken` is the deadlock detector. `lock.held` is not re-entrant, so a path
    that took the same lock twice would not hang — it would stall for the full
    timeout and then fail open, yielding False. An acquisition that came back
    True is an acquisition that nothing else, including this process, was
    holding.
    """

    def __init__(self):
        self.names = []
        self.taken = []
        self._depth = {}
        self._peak = {}

    @contextlib.contextmanager
    def held(self, layout, name):
        self.names.append(name)
        self._depth[name] = self._depth.get(name, 0) + 1
        self._peak[name] = max(self._peak.get(name, 0), self._depth[name])
        try:
            with _REAL_HELD(layout, name) as taken:
                self.taken.append(taken)
                yield taken
        finally:
            self._depth[name] -= 1

    def max_depth_for(self, name):
        return self._peak.get(name, 0)

    def patched(self):
        return unittest.mock.patch.object(lock, "held", self.held)


_REAL_HELD = lock.held


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class LockShapeTests(Fixture):
    """Criteria 4, 7 and 8 — asserted on the code's structure, because
    "correct by accident today" is how the original defect got written."""

    slug = "auth"

    def unit(self, name="01-api"):
        checks = [{"kind": "cmd", "run": OK}]
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": "pending", "wave": 1, "verify": checks,
            },
            CRITERIA,
        ).write(path)
        self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")
        return path

    def _bug_unit(self, name="01-api"):
        return types.SimpleNamespace(name=name, kind="bug",
                                     reproduction="pytest -k bug")

    # --- criterion 4: the name is the plan, never the unit ------------------ #

    def test_both_ledgers_lock_on_the_plan_not_the_unit(self):
        trace = LockTrace()
        with trace.patched():
            findings_mod.load(self.layout, self.slug, "01-api").add("minor", "a")
            findings_mod.load(self.layout, self.slug, "02-cli").add("minor", "b")
            phases_mod.load(self.layout, self.slug, "03-db").add("reproduce")
        self.assertEqual(set(trace.names), {f"plan-{self.slug}"})
        self.assertEqual(len(trace.names), 3)

    def test_two_units_of_one_plan_contend_for_the_same_lock_file(self):
        """A per-unit lock would have let the reported race straight through:
        the reviewer and the implementer are usually on different units."""
        one = findings_mod.load(self.layout, self.slug, "01-api")
        with one._exclusive():
            path = lock.path_for(self.layout, f"plan-{self.slug}")
            self.assertTrue(path.is_file())
            other = phases_mod.load(self.layout, self.slug, "02-cli")
            self.assertEqual(
                lock.path_for(self.layout, f"plan-{other.slug}"), path
            )
        self.assertFalse(path.exists())

    # --- criterion 7: one acquisition, spanning the read and the write ------ #

    def _observe_io(self, action):
        """Was the plan lock on disk during each ledger read and write?"""
        seen = []
        path = lock.path_for(self.layout, f"plan-{self.slug}")
        real_read, real_write = frontmatter.read, frontmatter.Document.write

        def traced_read(target):
            seen.append(("read", path.is_file()))
            return real_read(target)

        def traced_write(document, target):
            seen.append(("write", path.is_file()))
            return real_write(document, target)

        trace = LockTrace()
        with unittest.mock.patch.object(frontmatter, "read", traced_read), \
                unittest.mock.patch.object(frontmatter.Document, "write", traced_write), \
                trace.patched():
            seen.clear()
            action()
        return seen, trace

    def test_a_findings_mutation_reads_and_writes_inside_one_acquisition(self):
        ledger = findings_mod.load(self.layout, self.slug, "01-api")
        seen, trace = self._observe_io(lambda: ledger.add("critical", "raised"))
        self.assertEqual(seen, [("read", True), ("write", True)])
        self.assertEqual(trace.names, [f"plan-{self.slug}"])
        self.assertEqual(trace.taken, [True])

    def test_a_phases_mutation_reads_and_writes_inside_one_acquisition(self):
        ledger = phases_mod.load(self.layout, self.slug, "01-api")
        seen, trace = self._observe_io(lambda: ledger.add("reproduce", exit_code=1))
        self.assertEqual(seen, [("read", True), ("write", True)])
        self.assertEqual(trace.names, [f"plan-{self.slug}"])
        self.assertEqual(trace.taken, [True])

    def test_the_gate_question_is_answered_inside_the_lock_that_writes(self):
        """`phases.record` asks `can_enter` and writes the entry it authorises.
        Asking outside the lock would judge a file another process is part way
        through replacing."""
        unit = self._bug_unit()
        path = lock.path_for(self.layout, f"plan-{self.slug}")
        held_during_gate = []
        real = phases_mod.can_enter

        def traced(*args, **kwargs):
            held_during_gate.append(path.is_file())
            return real(*args, **kwargs)

        trace = LockTrace()
        with unittest.mock.patch.object(phases_mod, "can_enter", traced), \
                trace.patched():
            ok, _ = phases_mod.record(self.layout, self.slug, unit, "reproduce",
                                      command="pytest -k bug", exit_code=1)
        self.assertTrue(ok)
        self.assertEqual(held_during_gate, [True])
        self.assertEqual(trace.names, [f"plan-{self.slug}"])

    def test_a_refused_phase_releases_the_lock(self):
        """The early return out of the `with` is still an exit from it."""
        unit = self._bug_unit()
        ok, why = phases_mod.record(self.layout, self.slug, unit, "fix",
                                    command="pytest -k bug", exit_code=0)
        self.assertFalse(ok)
        self.assertIn("reproduce", why)
        self.assertFalse(lock.path_for(self.layout, f"plan-{self.slug}").exists())

    def test_a_mutation_sees_a_write_that_landed_while_it_was_waiting(self):
        """The re-read is the half that matters. A lock alone would still write
        back the state loaded before the wait."""
        ledger = findings_mod.load(self.layout, self.slug, "01-api")
        other = findings_mod.load(self.layout, self.slug, "01-api")
        other.add("important", "landed first")
        ledger.add("critical", "landed second")
        again = findings_mod.load(self.layout, self.slug, "01-api")
        self.assertEqual([f.summary for f in again.findings],
                         ["landed first", "landed second"])
        self.assertEqual([f.id for f in again.findings], [1, 2])

    # --- criterion 8: nothing nests, so nothing deadlocks ------------------- #

    def test_no_locked_path_ever_takes_the_same_lock_twice(self):
        """`lock.held` is not re-entrant, so this unit's answer to the deadlock
        question is "no such nesting exists", asserted rather than assumed.

        A second acquisition of a lock this process already holds cannot hang —
        it stalls for the whole timeout and then fails open — so the tell is an
        acquisition that came back False, and a run that took longer than a
        timeout. Both are checked, across the direct API and the two CLI paths
        that drive it.
        """
        self.unit()
        unit = self._bug_unit()
        trace = LockTrace()
        started = time.monotonic()
        with trace.patched():
            ledger = findings_mod.load(self.layout, self.slug, "01-api")
            ledger.add("critical", "raised")
            ledger.set_status(1, "addressed", evidence="fixed")
            ledger.bump_round()
            phases_mod.load(self.layout, self.slug, "01-api").add("reproduce")
            phases_mod.record(self.layout, self.slug, unit, "reproduce",
                              command="pytest -k bug", exit_code=1)
            telemetry.record(self.layout, "SessionStart", 1.0)
            self.assertEqual(
                self.cli("findings", "01-api", "--add", "important",
                         "--summary", "via the cli")[0], 0)
            self.assertEqual(
                self.cli("findings", "01-api", "--set", "1",
                         "--status", "addressed", "--evidence", "again")[0], 0)
        elapsed = time.monotonic() - started
        self.assertTrue(trace.names, "nothing took a lock at all")
        self.assertTrue(all(trace.taken),
                        "an acquisition failed open — something is nested")
        for name in set(trace.names):
            self.assertEqual(trace.max_depth_for(name), 1, f"{name} nested")
        self.assertLess(elapsed, lock.LOCK_TIMEOUT,
                        "a whole timeout elapsed — something waited on itself")


if __name__ == "__main__":
    unittest.main()
