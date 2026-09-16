"""`addressed` has to say what fixed it, exactly as `disputed` and `parked` do.

`findings`'s module docstring names the failure the whole state machine exists
to rule out: "performative agreement — 'good catch, noted' — which reads as
progress, closes nothing, and leaves the defect in the tree". `acknowledged` is
deliberately absent for that reason, and `disputed` and `parked` are each held
to a bar (evidence, a ruling) before they will move.

`addressed` was not. It accepted unconditionally, re-sealed the ledger as
authoritative, and dropped the finding out of the blocking set — so it was both
the easiest status to reach and the only one that actually opened the gate. The
label was real; the check behind it was the one the docstring says cannot
exist. It now asks for the same kind of evidence its two siblings do.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import findings as findings_mod, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
BODY = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class AddressedFixture(Fixture):
    slug = "addressed-plan"

    def unit(self, name="01-api"):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": "pending", "wave": 1, "verify": list(CHECK),
            },
            BODY,
        ).write(directory / f"{name}.md")
        self.trust(list(CHECK))
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)
        return name

    def raise_finding(self, name="01-api"):
        code, out = self.cli("findings", name, "--add", "critical",
                             "--summary", "the parser drops rows",
                             "--where", "src/parse.py:40")
        self.assertEqual(code, 0, out)

    def ledger(self, name="01-api"):
        return findings_mod.load(self.layout, self.slug, name)


class TestTheCommandRefusesABareAddressed(AddressedFixture):

    def test_no_evidence_is_refused_and_the_message_names_what_is_missing(self):
        self.unit()
        self.raise_finding()
        code, out = self.cli("findings", "01-api", "--set", "1",
                             "--status", "addressed")
        self.assertEqual(code, 1, out)
        self.assertIn("evidence", out)
        self.assertIn("finding 1", out)

    def test_the_finding_does_not_move_on_a_refusal(self):
        """A refusal that had already written the status would be worse than
        no check at all: the gate would open and the message would say it had
        not."""
        self.unit()
        self.raise_finding()
        self.cli("findings", "01-api", "--set", "1", "--status", "addressed")
        finding = self.ledger().get(1)
        self.assertEqual(finding.status, "open")
        self.assertEqual(len(findings_mod.unresolved(self.ledger())), 1)

    def test_with_evidence_it_succeeds_and_reseals_as_before(self):
        """Positive control, and the whole point: the legitimate close still
        works, still moves the finding, and still records the evidence."""
        self.unit()
        self.raise_finding()
        code, out = self.cli("findings", "01-api", "--set", "1",
                             "--status", "addressed",
                             "--evidence", "src/parse.py:41 now reads the last row")
        self.assertEqual(code, 0, out)
        finding = self.ledger().get(1)
        self.assertEqual(finding.status, "addressed")
        self.assertIn("src/parse.py:41", finding.evidence)
        self.assertEqual(findings_mod.unresolved(self.ledger()), [])
        # Re-sealed authoritatively, so the done-gate does not read the close
        # as a hand-edit of the ledger.
        code, out = self.cli("unit", "01-api", "--status", "done")
        self.assertEqual(code, 0, out)

    def test_whitespace_is_not_evidence(self):
        self.unit()
        self.raise_finding()
        code, out = self.cli("findings", "01-api", "--set", "1",
                             "--status", "addressed", "--evidence", "   ")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.ledger().get(1).status, "open")


class TestTheStateMachineItself(Fixture):
    """`set_status` directly: a refusal is a return value, not an exception."""

    def ledger(self):
        led = findings_mod.Ledger(self.root / "findings.md", "p", "01-api")
        led.findings.append(findings_mod.Finding(1, "critical", "boom"))
        return led

    def test_a_bare_addressed_returns_false_and_a_reason(self):
        ok, problem = self.ledger().set_status(1, "addressed")
        self.assertFalse(ok)
        self.assertIn("evidence", problem)

    def test_addressed_now_asks_for_the_same_thing_disputed_does(self):
        """Parity is the point. Three exits from `open`, three states that
        have to be paid for — otherwise the cheapest one is the one every
        model reaches for, and it is the one that opens the gate."""
        led = self.ledger()
        for status, kwargs in (("addressed", {}), ("disputed", {}), ("parked", {})):
            ok, problem = led.set_status(1, status, **kwargs)
            self.assertFalse(ok, f"{status} accepted with nothing to show for it")
            self.assertTrue(problem)
            self.assertEqual(led.get(1).status, "open")

    def test_addressed_with_evidence_moves_and_keeps_the_note(self):
        led = self.ledger()
        ok, problem = led.set_status(1, "addressed", evidence="src/a.py:1 re-raises")
        self.assertTrue(ok, problem)
        self.assertEqual(led.get(1).status, "addressed")
        self.assertIn("src/a.py:1 re-raises", led.get(1).evidence)

    def test_a_minor_finding_is_held_to_the_same_bar(self):
        """The rule is about the status, not the severity: a `minor` finding
        closed with nothing written is the same unverifiable claim, and it is
        what a reader of the file has to trust later."""
        led = findings_mod.Ledger(self.root / "minor.md", "p", "01-api")
        led.findings.append(findings_mod.Finding(1, "minor", "typo"))
        self.assertFalse(led.set_status(1, "addressed")[0])


class TestUnresolvedDoesNotTakeAHandEditsWordForDisputedOrParkedEither(unittest.TestCase):
    """`_set_status` refuses a bare `disputed` or `parked` exactly as it
    refuses a bare `addressed` — but `unresolved()` (what the done-gate
    actually calls) used to re-check only `addressed`. A findings.md edited
    directly, outside the CLI, to `disputed` with no evidence or `parked` with
    no ruling passed through the gate unresolved-list untouched: the same
    loophole `addressed` had, just moved one status over.
    """

    def _ledger(self, status, **fields):
        led = findings_mod.Ledger(Path("/does/not/matter") / "findings.md",
                                  "p", "01-api")
        led.findings.append(
            findings_mod.Finding(1, "critical", "boom", status=status, **fields)
        )
        return led

    def test_a_hand_edited_disputed_with_no_evidence_still_blocks(self):
        led = self._ledger("disputed")
        self.assertEqual(len(findings_mod.unresolved(led)), 1)

    def test_a_hand_edited_disputed_with_evidence_does_not_block(self):
        led = self._ledger("disputed", evidence="src/a.py:1 the guard is intact")
        self.assertEqual(findings_mod.unresolved(led), [])

    def test_a_hand_edited_parked_with_no_ruling_still_blocks(self):
        led = self._ledger("parked")
        self.assertEqual(len(findings_mod.unresolved(led)), 1)

    def test_a_hand_edited_parked_with_a_ruling_does_not_block(self):
        led = self._ledger("parked", ruling="accepted risk, see ADR-0012")
        self.assertEqual(findings_mod.unresolved(led), [])


if __name__ == "__main__":
    unittest.main()
