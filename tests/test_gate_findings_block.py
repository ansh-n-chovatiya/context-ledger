"""The done-gate consults the findings ledger, whether or not the unit asked.

`findings.PREAMBLE` is written into the head of every findings file and says,
flatly, that "`critical` and `important` block the done-gate while they are
open". That was true only for a unit whose own `verify:` block happened to
declare a `kind: review` check — 6 of the 63 units in this repository's own
plans. For the other 57, a reviewer could raise a `critical` finding and the
implementer could walk the unit to `done` with the finding still open, while
the ledger it was written into promised the opposite.

So `verify.gate_check` now reads the ledger itself, as a step of its own,
before it spends a process on anything. Every test below carries the positive
control beside it: a refusal that fires for every finding is worth no more than
one that fires for none, so each is shown *not* blocking the legitimate close
of the same finding.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    findings as findings_mod, frontmatter, plan as plan_mod, verify,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
BODY = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"


class FindingsGateFixture(Fixture):
    """A one-unit plan whose gate contains no `review` check at all.

    That absence is the premise of the whole file, so it is asserted rather
    than assumed — see `test_the_premise_holds`.
    """

    slug = "findings-gate"

    def unit(self, name="01-api", checks=None):
        checks = list(CHECK if checks is None else checks)
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": [f"src/{name}.py"],
                "reads": [], "forbid": [], "budget_tokens": 1000,
                "status": "pending", "wave": 1, "verify": checks,
            },
            BODY,
        ).write(directory / f"{name}.md")
        self.trust(checks)
        self.assertEqual(self.cli("plan", self.slug, "--no-spec")[0], 0)
        return plan_mod.find_unit(self.layout, self.slug, name)

    def raise_finding(self, severity="critical", summary="the parser drops rows",
                      name="01-api"):
        code, out = self.cli("findings", name, "--add", severity,
                             "--summary", summary, "--where", "src/parse.py:40")
        self.assertEqual(code, 0, out)
        return findings_mod.path_for(self.layout, self.slug, name)

    def close(self, *args, name="01-api", id="1"):
        return self.cli("findings", name, "--set", id, *args)

    def mark_done(self, name="01-api", *extra):
        return self.cli("unit", name, "--status", "done", *extra)

    def status_of(self, name="01-api"):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        return frontmatter.read(path).meta["status"]


class TestTheGateConsultsFindingsWithoutAReviewCheck(FindingsGateFixture):

    def test_the_premise_holds(self):
        """The unit under test declares no `review` check.

        Without this, every assertion below could be passing because the old,
        opt-in path ran — which is precisely the path that did not cover 57 of
        63 units.
        """
        unit = self.unit()
        kinds = [str(check.get("kind")) for check in verify.ordered(unit.checks)]
        self.assertNotIn("review", kinds, kinds)
        self.assertEqual(kinds, ["cmd"])

    def test_an_open_critical_finding_refuses_done_and_names_it(self):
        self.unit()
        self.raise_finding("critical", "the parser drops rows")
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.status_of(), "pending", "nothing was written")
        self.assertIn("refusing", out)
        self.assertIn("[1]", out, "the refusal names the finding id")
        self.assertIn("critical", out, "and its severity")
        self.assertIn("the parser drops rows", out, "and what it was about")

    def test_an_open_important_finding_refuses_too(self):
        self.unit()
        self.raise_finding("important", "no test covers the expiry path")
        code, out = self.mark_done()
        self.assertEqual(code, 1, out)
        self.assertIn("[1]", out)
        self.assertIn("important", out)

    def test_a_minor_finding_never_blocks(self):
        """`minor` is recorded and deferred, never blocking — a gate that can
        hold on nits is a gate people learn to route around."""
        self.unit()
        self.raise_finding("minor", "could use a docstring")
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_a_unit_with_no_findings_at_all_is_untouched(self):
        self.unit()
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")


class TestTheLegitimateCloses(FindingsGateFixture):
    """Positive controls: each way out of `open` actually opens the gate."""

    def test_addressed_with_evidence_lets_the_unit_through(self):
        self.unit()
        self.raise_finding()
        code, out = self.close("--status", "addressed",
                               "--evidence", "src/parse.py:41 now reads the last row")
        self.assertEqual(code, 0, out)
        code, out = self.mark_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")

    def test_disputed_with_evidence_lets_the_unit_through(self):
        self.unit()
        self.raise_finding()
        code, out = self.close("--status", "disputed",
                               "--evidence", "tests/test_parse.py:12 covers that row")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mark_done()[0], 0)

    def test_parked_with_a_ruling_lets_the_unit_through(self):
        self.unit()
        self.raise_finding()
        code, out = self.close("--status", "parked",
                               "--ruling", "deferred to the follow-up plan")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.mark_done()[0], 0)

    def test_force_is_the_way_past_and_the_refusal_says_so(self):
        """An escape hatch that is not named in the refusal is an escape hatch
        nobody finds, and a gate nobody can pass is a gate that gets deleted."""
        self.unit()
        self.raise_finding()
        _code, out = self.mark_done()
        self.assertIn("--force", out)
        code, out = self.mark_done("01-api", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status_of(), "done")


class TestWhatCountsAsUnresolved(Fixture):
    """`findings.unresolved` on its own, away from the CLI."""

    def ledger(self):
        return findings_mod.Ledger(self.root / "findings.md", "p", "01-api")

    def test_open_blocking_findings_are_unresolved(self):
        ledger = self.ledger()
        ledger.findings.append(findings_mod.Finding(1, "critical", "boom"))
        ledger.findings.append(findings_mod.Finding(2, "important", "bang"))
        ledger.findings.append(findings_mod.Finding(3, "minor", "typo"))
        self.assertEqual([f.id for f in findings_mod.unresolved(ledger)], [1, 2])

    def test_addressed_with_no_evidence_at_all_is_still_unresolved(self):
        """`set_status` refuses to write that state, so a ledger holding it was
        edited by hand — and the gate does not take a hand-edit's word for a
        critical finding being fixed."""
        ledger = self.ledger()
        ledger.findings.append(
            findings_mod.Finding(1, "critical", "boom", status="addressed")
        )
        self.assertEqual([f.id for f in findings_mod.unresolved(ledger)], [1])

    def test_addressed_with_evidence_is_resolved(self):
        ledger = self.ledger()
        ledger.findings.append(findings_mod.Finding(
            1, "critical", "boom", status="addressed", evidence="src/a.py:1 fixed"))
        self.assertEqual(findings_mod.unresolved(ledger), [])

    def test_disputed_and_parked_are_resolved_when_they_carry_their_justification(self):
        """`disputed` needs evidence and `parked` needs a ruling to move at
        all through the CLI (`_set_status`) — `unresolved()` holds a
        hand-edited file to the same bar (see
        `test_findings_addressed_evidence.py`'s
        `TestUnresolvedDoesNotTakeAHandEditsWordForDisputedOrParkedEither`),
        so a bare `disputed`/`parked` with nothing written is *not* resolved.
        This is the positive control: with the justification present, both
        still open the gate exactly as they always have."""
        ledger = self.ledger()
        ledger.findings.append(findings_mod.Finding(
            1, "critical", "a", status="disputed",
            evidence="src/a.py:1 the guard is intact"))
        ledger.findings.append(findings_mod.Finding(
            2, "important", "b", status="parked",
            ruling="accepted risk, see ADR-0012"))
        self.assertEqual(findings_mod.unresolved(ledger), [])


if __name__ == "__main__":
    unittest.main()
