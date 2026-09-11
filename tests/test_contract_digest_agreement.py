"""`contract.digest` and `contract.digest_text` must agree, always.

`digest_text` had no caller anywhere in `ctx/`, `tests/`, `commands/`, `hooks/`
or `bin/`. The usual treatment for dead code is deletion, and that is what
`snapshot.discard_all` got — but the rule that earns it is "dead code that
destroys data", and this is two lines that compute a hash. Deleting it would
have thrown away the only expression of an invariant the seal genuinely rests
on, which is a worse trade than keeping an unused function.

The invariant: a unit's contract can be digested from the parsed document
(`digest(unit)`) or from the file's bytes (`digest_text(path.read_text())`),
and the two must produce the same hex string. `contract.compare` is what the
done-gate uses to refuse a unit that rewrote its own promise after dispatch,
and it is a hash comparison — so if the two routes could ever disagree, a
contract sealed one way and checked the other would report *edited after
dispatch* for a file nobody had touched. The refusal would be unanswerable:
the diff it names would be empty.

So the function stops being dead by becoming the thing this file asserts. Each
case below is a shape where the two routes could plausibly part company:
frontmatter written in a different key order, a key whose value spans several
lines, and a multi-line acceptance-criteria section — the one field digested
out of the body rather than the metadata.

Negative controls at the foot: a digest that could not tell two different
contracts apart would satisfy every assertion above by being constant.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import contract, frontmatter, plan as plan_mod  # noqa: E402
from support import Fixture  # noqa: E402


SLUG = "auth"

PLAIN = """\
---
ctx_schema: 1
unit: 01-api
plan: auth
tier: subagent
depends_on: []
owns:
  - src/api.py
  - tests/test_api.py
reads: []
forbid:
  - src/db.py
budget_tokens: 1000
status: pending
wave: 1
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Objective
Build the API.

## Acceptance criteria
1. It answers.
2. It refuses what it should refuse.
"""

# The same promise, every key in a different position. `field_digests` sorts
# `owns`, `forbid` and the rest for exactly this reason: re-ordering a list is
# not a change to what the unit may write, and a digest that tripped on
# formatting would teach people to pass `--force`.
REORDERED = """\
---
wave: 1
status: pending
verify:
  - run: python3 -m unittest discover -s tests -q
    kind: cmd
budget_tokens: 1000
forbid:
  - src/db.py
reads: []
owns:
  - tests/test_api.py
  - src/api.py
depends_on: []
tier: subagent
plan: auth
unit: 01-api
ctx_schema: 1
---

## Objective
Build the API.

## Acceptance criteria
1. It answers.
2. It refuses what it should refuse.
"""

# Values that do not fit on their line: nested mappings under `reads` and
# `verify`, a list under a list item, and a criteria section of several
# paragraphs. Every one of these is a place where a parse and a re-parse of the
# emitted bytes could drift.
MULTILINE = """\
---
ctx_schema: 1
unit: 02-gate
plan: auth
tier: subagent
depends_on:
  - 01-api
owns:
  - ctx/verify.py
reads:
  - path: ctx/paths.py
    symbols:
      - LEDGER_PREFIX
      - Layout
  - path: ctx/contract.py
    symbols:
      - seal_findings
forbid:
  - ctx/detect.py
budget_tokens: 75000
status: running
wave: 2
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: symbol
    path: ctx/verify.py
    contains:
      - def verify_plan(
      - def gate_before_done(
---

## Objective
Move the gate beside the thing it calls.

## Acceptance criteria
1. A new verify kind is one dict entry. Today it costs four coordinated
   edits, and a test registers a synthetic kind through the table alone.

2. Behaviour-preserving: every pre-existing test covering the gate passes
   unedited. If a test needs editing, stop and report.

3. No import cycle is created.
"""


class DigestAgreement(Fixture):
    """A plan whose unit files can be written verbatim and then loaded."""

    def write_unit(self, name, text):
        directory = plan_mod.units_dir(self.layout, SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return plan_mod.find_unit(self.layout, SLUG, name)

    def assertRoutesAgree(self, unit):
        """The two ways to digest one contract, side by side."""
        from_document = contract.digest(unit)
        from_bytes = contract.digest_text(
            unit.path.read_text(encoding="utf-8"))
        self.assertEqual(from_document, from_bytes)
        return from_document


class TestTheTwoRoutesAgree(DigestAgreement):

    def test_a_plain_unit(self):
        unit = self.write_unit("01-api", PLAIN)
        self.assertRoutesAgree(unit)

    def test_frontmatter_written_in_a_different_key_order(self):
        """Reordered keys, and reordered entries inside `owns`.

        Both routes must land on the same digest, and — because the fields are
        sorted before hashing — it must be the *same* digest as the unit whose
        keys are in the usual order. A unit reformatted by an editor has not
        rewritten its promise.
        """
        plain = self.assertRoutesAgree(self.write_unit("01-api", PLAIN))
        shuffled = self.assertRoutesAgree(self.write_unit("01-api", REORDERED))
        self.assertEqual(plain, shuffled)

    def test_a_unit_carrying_multi_line_values(self):
        """Nested blocks under `reads` and `verify`, and a criteria section of
        several paragraphs — the one field digested out of the body."""
        unit = self.write_unit("02-gate", MULTILINE)
        self.assertRoutesAgree(unit)

    def test_every_field_digest_agrees_field_by_field(self):
        """Not just the combined hash. A combine that dropped a field would
        agree on both routes while digesting less than it claims to."""
        for name, text in (("01-api", PLAIN), ("02-gate", MULTILINE)):
            unit = self.write_unit(name, text)
            from_document = contract.field_digests(unit.doc)
            from_bytes = contract.field_digests(
                frontmatter.parse(unit.path.read_text(encoding="utf-8")))
            self.assertEqual(sorted(from_document), sorted(contract.FIELDS))
            for field in contract.FIELDS:
                with self.subTest(unit=name, field=field):
                    self.assertEqual(from_document[field], from_bytes[field])

    def test_a_document_round_tripped_through_a_write_still_agrees(self):
        """`ctx` rewrites unit files constantly — every `status:` flip goes
        through `Document.write`. The bytes it emits must digest to what the
        object it emitted them from digests to, or the first status change
        after dispatch would look like a forged contract.
        """
        unit = self.write_unit("02-gate", MULTILINE)
        before = self.assertRoutesAgree(unit)
        unit.doc.meta["status"] = "done"
        unit.doc.write(unit.path)
        reloaded = plan_mod.find_unit(self.layout, SLUG, "02-gate")
        self.assertEqual(self.assertRoutesAgree(reloaded), before)


class TestTheDigestIsNotConstant(DigestAgreement):
    """Negative controls. An agreement between two constants is not an
    agreement about anything."""

    def test_changing_a_criterion_changes_both_routes(self):
        unit = self.write_unit("01-api", PLAIN)
        before = self.assertRoutesAgree(unit)
        tampered = PLAIN.replace("2. It refuses what it should refuse.",
                                 "2. It refuses nothing at all.")
        after = self.assertRoutesAgree(self.write_unit("01-api", tampered))
        self.assertNotEqual(before, after)

    def test_widening_owns_changes_both_routes(self):
        unit = self.write_unit("01-api", PLAIN)
        before = self.assertRoutesAgree(unit)
        widened = PLAIN.replace("  - src/api.py", "  - src/api.py\n  - src/db.py")
        after = self.assertRoutesAgree(self.write_unit("01-api", widened))
        self.assertNotEqual(before, after)

    def test_changing_a_verify_command_changes_both_routes(self):
        unit = self.write_unit("01-api", PLAIN)
        before = self.assertRoutesAgree(unit)
        loosened = PLAIN.replace("python3 -m unittest discover -s tests -q",
                                 "true")
        after = self.assertRoutesAgree(self.write_unit("01-api", loosened))
        self.assertNotEqual(before, after)

    def test_a_multi_line_value_is_digested_line_by_line(self):
        """Dropping one symbol out of a nested block must move the digest —
        otherwise `reads` would be hashed as "there is a list here"."""
        unit = self.write_unit("02-gate", MULTILINE)
        before = self.assertRoutesAgree(unit)
        trimmed = MULTILINE.replace("      - LEDGER_PREFIX\n", "")
        after = self.assertRoutesAgree(self.write_unit("02-gate", trimmed))
        self.assertNotEqual(before, after)


class TestDigestTextIsNoLongerDead(unittest.TestCase):

    def test_it_has_a_caller(self):
        """The reason this file exists rather than a deletion commit."""
        here = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("contract.digest_text(", here)


if __name__ == "__main__":
    unittest.main()
