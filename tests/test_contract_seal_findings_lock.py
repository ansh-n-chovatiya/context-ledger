"""`contract.seal_findings` is a read-modify-write, and it took no lock.

The seal is what makes a deleted finding visible: `findings_drift` compares the
findings file against the ids ctx sealed, so a finding that reached the seal
cannot afterwards be removed from the file by hand without the done-gate saying
so. Every one of the six call sites — `ctx findings --add`, `--set`, the listing
path, `ctx review`, the gate's success path — called it outside any lock, and
the function itself read the seal, merged the ledger into its own copy, and
wrote the whole thing back.

So two processes recording a finding each both read the same seal, each add
their own id to their own copy, and the second write renders the first one's
finding out of the seal. The id that lost is then in the findings file but not
in the seal, and `findings_drift` only ever looks at what the seal knows: the
finding can be deleted from the file afterwards and nothing notices. The
report's repro, reproduced below verbatim — ledger `[1, 2]`, seal `['1']`,
drift `[]` — and then the hand-deletion that drift sails past.

The fix is `findings.Ledger._exclusive`'s, in the module next door and under the
same lock name: hold `plan-<slug>` across the read *and* the write, and read
inside it. The lock is what serialises; the read inside it is what makes the
merge see whatever landed while this process was waiting.

Three claims, in the shape `tests/test_ledger_locks.py` uses:

* the **shape** — the read and the write happen inside one acquisition of
  `plan-<slug>`, asserted on the code rather than on a race, because "correct
  by accident today" is how the original defect got written;
* the **positive control** — the losing interleaving, forced deterministically
  on marker files rather than hoped for by sleeping, against the unlocked half
  (`_seal_findings_locked`, which is `seal_findings` minus the lock, exactly as
  `telemetry._rotate` is `record` minus the lock). It loses the finding, every
  time, on any machine;
* the **guard** — two real processes, one finding each, both going through the
  public `seal_findings`. Both ids survive. The process holding the smaller view
  of the ledger is made to write last, which is the only order that can lose an
  id, so a machine fast enough to run the test at all cannot make this one green
  by accident.
"""

import contextlib
import json
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    contract, findings as findings_mod, frontmatter, lock, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

REPO = str(Path(__file__).resolve().parent.parent)
CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"

# A ceiling on every rendezvous, so a worker that died takes the test down with
# a message instead of hanging the suite. Nothing asserts that anything happens
# *before* it.
DEADLINE = 120.0
POLL = 0.005

# How long the process with the smaller view of the ledger is made to take over
# its seal write. Only ever a lower bound on how long it holds the lock, and far
# inside `lock.LOCK_TIMEOUT`, so the other process waits its turn rather than
# failing open and writing unserialised.
WINDOW = 0.3


def _wait_for(case, paths, why):
    deadline = time.monotonic() + DEADLINE
    for path in paths:
        while not path.exists():
            if time.monotonic() > deadline:
                case.fail(f"timed out waiting for {why}: {path} never appeared")
            time.sleep(POLL)


_RENDEZVOUS = '''
def wait_for(*paths):
    deadline = time.monotonic() + {deadline!r}
    for path in paths:
        while not path.exists():
            assert time.monotonic() < deadline, "timed out waiting for %s" % path
            time.sleep({poll!r})
'''


# The worker for the control: it waits until the parent's `seal_findings` has
# read the seal and is about to write it, and does its whole add-and-seal inside
# that window. The parent holds no lock — it is calling the unlocked half — so
# nothing here can block on one.
_RACING_ADDER = '''
import sys, time
sys.path.insert(0, {repo!r})
from ctx import contract, findings, paths
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
wait_for(layout.runtime / "seal-has-read")
ledger = findings.load(layout, {slug!r}, {unit!r})
ledger.add("critical", "raised while the other process was sealing")
contract.seal_findings(layout, {slug!r}, {unit!r}, ledger, authoritative=True)
(layout.runtime / "worker-sealed").write_text("1")
'''


# The worker for the guard: add a finding, wait until every worker has added one
# — so each is holding a differently stale view of the ledger — and only then
# seal. The worker that minted id 1 read the ledger when it held nothing, so its
# view is the one that loses ids if it reads first and writes last. It is made
# to do exactly that, and the other process is held at the door until its read
# has happened, so the losing order is forced rather than hoped for.
_SEALING_WORKER = '''
import sys, time
sys.path.insert(0, {repo!r})
from ctx import atomic, contract, findings, paths
''' + _RENDEZVOUS + '''
layout = paths.Layout({root!r})
role = sys.argv[1]
ledger = findings.load(layout, {slug!r}, {unit!r})
mine = ledger.add("critical", "raised by %s" % role)

(layout.runtime / ("added-%s" % role)).write_text("1")
wait_for(*[layout.runtime / ("added-%s" % other) for other in {roles!r}])

if mine.id == 1:
    # The smallest view of the ledger, made to read first and write last: it
    # announces that the seal has been read from inside the write itself, and
    # then takes {window!r}s over that write. Unserialised, this is exactly the
    # interleaving that loses the other process's finding. Serialised, it is a
    # lock held for {window!r}s — well inside `lock.LOCK_TIMEOUT` — while the
    # other process waits its turn and then merges into what it finds.
    real = atomic.write_text
    def slow(path, text, encoding="utf-8"):
        (layout.runtime / "seal-has-read").write_text("1")
        time.sleep({window!r})
        return real(path, text, encoding=encoding)
    contract.atomic.write_text = slow
else:
    # Waits outside `seal_findings`, holding nothing: the process above may be
    # holding the plan lock, and a waiter that held one too would be a test
    # that deadlocks the code it is meant to be checking.
    wait_for(layout.runtime / "seal-has-read")

contract.seal_findings(layout, {slug!r}, {unit!r}, ledger, authoritative=True)
'''


class SealFixture(Fixture):
    """One unit with a dispatch seal and one finding already in it."""

    slug = "auth"
    unit_name = "01-api"

    def setUp(self):
        super().setUp()
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": self.unit_name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": ["src/api.py"],
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": "running", "wave": 1,
                "verify": [{"kind": "cmd", "run": OK}],
            },
            CRITERIA,
        ).write(directory / f"{self.unit_name}.md")
        self.unit = plan_mod.find_unit(self.layout, self.slug, self.unit_name)
        contract.seal(self.layout, self.config, self.slug, self.unit)

    def sealed_ids(self):
        data = contract.load_seal(self.layout, self.slug, self.unit_name) or {}
        return sorted(data.get("findings") or {})

    def ledger_ids(self):
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        return [f.id for f in ledger.findings]

    def seed(self, summary="the finding that was already sealed"):
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        ledger.add("important", summary)
        contract.seal_findings(self.layout, self.slug, self.unit_name, ledger,
                               authoritative=True)
        return ledger


# --------------------------------------------------------------------------- #
# the shape
# --------------------------------------------------------------------------- #

class TestTheReadAndTheWriteShareOneAcquisition(SealFixture):
    """Asserted on the code's structure rather than on a race: a lock that
    happens to be uncontended today still has to be the thing holding the two
    halves together.

    `taken == [True]` is the deadlock check. `lock.held` is not re-entrant, so
    an acquisition nested inside one this process already holds would not hang
    — it would stall for the full timeout and come back False, unserialised.
    """

    def _trace(self, action):
        real_held = lock.held
        seen, names, taken = [], [], []
        lock_path = lock.path_for(self.layout, f"plan-{self.slug}")

        @contextlib.contextmanager
        def traced_held(layout, name):
            names.append(name)
            with real_held(layout, name) as got:
                taken.append(got)
                yield got

        real_load, real_write = contract.load_seal, contract._write_seal

        def traced_load(layout, slug, unit_name):
            seen.append(("read", lock_path.is_file()))
            return real_load(layout, slug, unit_name)

        def traced_write(layout, slug, unit_name, data):
            seen.append(("write", lock_path.is_file()))
            return real_write(layout, slug, unit_name, data)

        with unittest.mock.patch.object(lock, "held", traced_held), \
                unittest.mock.patch.object(contract, "load_seal", traced_load), \
                unittest.mock.patch.object(contract, "_write_seal", traced_write):
            action()
        return seen, names, taken

    def test_seal_findings_reads_and_writes_inside_one_acquisition(self):
        ledger = self.seed()
        seen, names, taken = self._trace(
            lambda: contract.seal_findings(self.layout, self.slug,
                                           self.unit_name, ledger)
        )
        self.assertEqual(seen, [("read", True), ("write", True)])
        self.assertEqual(names, [f"plan-{self.slug}"])
        self.assertEqual(taken, [True])

    def test_the_lock_is_released_afterwards(self):
        """Including on the early return for a unit that was never sealed —
        a lock left behind would stall every sibling in the wave for its full
        timeout."""
        contract.discard(self.layout, self.slug, self.unit_name)
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        self.assertIsNone(
            contract.seal_findings(self.layout, self.slug, self.unit_name, ledger)
        )
        self.assertFalse(
            lock.path_for(self.layout, f"plan-{self.slug}").exists())

    def test_it_is_the_same_lock_the_findings_ledger_takes(self):
        """Two different lock names would serialise each half of the record
        against its own kind and leave the pair to interleave."""
        names = []
        real_held = lock.held

        @contextlib.contextmanager
        def traced(layout, name):
            names.append(name)
            with real_held(layout, name) as got:
                yield got

        with unittest.mock.patch.object(lock, "held", traced):
            ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
            ledger.add("important", "a finding")
            contract.seal_findings(self.layout, self.slug, self.unit_name,
                                   ledger, authoritative=True)
        self.assertEqual(names, [f"plan-{self.slug}", f"plan-{self.slug}"])


# --------------------------------------------------------------------------- #
# the positive control
# --------------------------------------------------------------------------- #

class TestTheUnserialisedSealLosesAFinding(SealFixture):
    """The report's repro, forced rather than raced.

    `_seal_findings_locked` is `seal_findings` minus the lock — the shipped
    body, unchanged — so this measures the defect rather than an approximation
    of it. It passes against the fixed code too, on purpose: it is the evidence
    that the guard below is guarding something.
    """

    def test_a_concurrent_finding_is_rendered_out_of_the_seal(self):
        ledger = self.seed()          # id 1, sealed. This view never sees id 2.
        self.assertEqual(self.sealed_ids(), ["1"])

        script = _RACING_ADDER.format(
            repo=REPO, root=str(self.layout.root), slug=self.slug,
            unit=self.unit_name, deadline=DEADLINE, poll=POLL,
        )
        worker = subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        witnessed = {}
        real = contract.atomic.write_text

        def gated(path, text, encoding="utf-8"):
            # The seal has been read and is about to be written. Open the
            # window, let the other process do its whole add-and-seal in it,
            # and look at what it left before writing the stale copy over it.
            (self.layout.runtime / "seal-has-read").write_text("1")
            _wait_for(self, [self.layout.runtime / "worker-sealed"],
                      "the other process's seal")
            witnessed["before"] = self.sealed_ids()
            return real(path, text, encoding=encoding)

        with unittest.mock.patch.object(contract.atomic, "write_text", gated):
            contract._seal_findings_locked(self.layout, self.slug,
                                           self.unit_name, ledger, True)
        _out, err = worker.communicate(timeout=DEADLINE)
        self.assertEqual(worker.returncode, 0, err.decode())

        self.assertEqual(witnessed.get("before"), ["1", "2"],
                         "the other process never sealed its finding — the "
                         "control raced nothing and proves nothing")
        self.assertEqual(self.ledger_ids(), [1, 2])
        self.assertEqual(self.sealed_ids(), ["1"],
                         "the stale write did not lose the concurrent finding")

    def test_and_the_lost_finding_can_then_be_deleted_unnoticed(self):
        """Why losing it matters. `findings_drift` only knows what the seal
        knows, so a critical finding the seal never heard of can be taken out
        of the findings file by hand and the done-gate reports nothing."""
        self.test_a_concurrent_finding_is_rendered_out_of_the_seal()
        self.assertEqual(
            contract.findings_drift(self.layout, self.slug, self.unit_name), [])

        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        ledger.findings = [f for f in ledger.findings if f.id != 2]
        ledger.save()   # the hand-edit, in one call: render the file without it

        self.assertEqual(self.ledger_ids(), [1])
        self.assertEqual(
            contract.findings_drift(self.layout, self.slug, self.unit_name), [],
            "the deletion was visible after all — then the lost id was not "
            "actually lost",
        )


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #

class TestNoFindingIdIsLostFromTheSeal(SealFixture):
    """Two processes, one finding each, both through the public entry point."""

    ROLES = ("reviewer", "implementer")

    def _race(self):
        script = _SEALING_WORKER.format(
            repo=REPO, root=str(self.layout.root), slug=self.slug,
            unit=self.unit_name, deadline=DEADLINE, poll=POLL,
            roles=list(self.ROLES), window=WINDOW,
        )
        workers = [
            subprocess.Popen([sys.executable, "-c", script, role],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for role in self.ROLES
        ]
        for worker in workers:
            _out, err = worker.communicate(timeout=DEADLINE)
            self.assertEqual(worker.returncode, 0, err.decode())

    def test_both_findings_reach_the_seal(self):
        self._race()
        self.assertEqual(self.ledger_ids(), [1, 2],
                         "both processes must have recorded a finding, or "
                         "there was nothing for the seal to lose")
        self.assertEqual(self.sealed_ids(), ["1", "2"])

    def test_the_seal_still_says_what_each_finding_was(self):
        """Not just the ids: a merge that kept the key and dropped the severity
        would satisfy the assertion above while sealing nothing worth having."""
        self._race()
        data = contract.load_seal(self.layout, self.slug, self.unit_name)
        for key in ("1", "2"):
            self.assertEqual(data["findings"][key]["severity"], "critical")
            self.assertIn("raised by", data["findings"][key]["summary"])

    def test_and_a_deletion_is_then_visible_for_either_of_them(self):
        """The consequence, the right way round: both ids reached the seal, so
        removing either one from the findings file by hand is drift the gate
        reports."""
        self._race()
        ledger = findings_mod.load(self.layout, self.slug, self.unit_name)
        ledger.findings = [f for f in ledger.findings if f.id != 2]
        ledger.save()
        drift = contract.findings_drift(self.layout, self.slug, self.unit_name)
        self.assertTrue(any("[2]" in line and "deleted" in line for line in drift),
                        drift)


class TestTheSealSurvivesARealDispatch(SealFixture):
    """A last sanity check that the seal written here is the one on disk — the
    tests above all read it back through `load_seal`."""

    def test_the_seal_file_is_valid_json_with_its_findings(self):
        self.seed()
        path = contract.seal_path(self.layout, self.slug, self.unit_name)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data["findings"]), ["1"])


if __name__ == "__main__":
    unittest.main()
