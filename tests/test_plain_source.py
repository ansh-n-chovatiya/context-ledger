"""`ctx.plain` — the authored plain-language file, and honest filler for the rest.

Four properties, in the order they break in:

1. **Absence is normal.** Most plans have no `plain.md`. Reading one that is
   not there returns an empty, well-formed answer rather than an exception.
2. **Unfilled is not authored.** `plan-check` scaffolds the form, so the common
   state on disk is five headings and five bolded labels with nothing after
   them. A blank field must read as nobody-wrote-this, or the page will show a
   reviewer an empty form and call it prose.
3. **Staleness is content, not a counter.** `plan.write_graph` bumps
   `revision` on every run, so a no-op re-check would mark `plain.md` stale
   under any revision-based scheme. It is keyed off the unit contracts' own
   digest instead, and a test runs the no-op re-check to prove it.
4. **Generated text says only what a machine can know.** Position, file count,
   ordering, checks. Never a purpose, never a risk level, and never a word out
   of the contract's vocabulary — with a positive control that puts a banned
   word in the generator's input and proves the vocabulary assertion fires.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import atomic, frontmatter, plain, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"

# Straight from the unit contract: words the generated sentences may not use,
# because a non-technical reviewer does not have them.
BANNED = ("owns", "forbid", "depends_on", "wave", "tier", "budget_tokens",
          "subagent")
# Words that would be a judgement rather than a fact.
JUDGEMENTS = ("low", "medium", "high", "safe", "improve", "better",
              "important", "critical", "minor", "trivial")


class PlainCase(Fixture):
    """A two-unit plan, plus helpers for building `plain.md` by hand."""

    SLUG = "demo"
    UNITS = (("01-alpha", []), ("02-beta", ["01-alpha"]))

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        for name, deps in self.UNITS:
            self.write_unit(name, deps)

    # ----------------------------------------------------------------- #
    # helpers
    # ----------------------------------------------------------------- #

    def write_unit(self, name, deps=(), owns=None, criteria=CRITERIA):
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": self.SLUG,
             "tier": "subagent", "depends_on": list(deps),
             "owns": list(owns or [f"src/{name}.py"]), "reads": [],
             "forbid": [], "budget_tokens": 1000, "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            criteria,
        ).write(directory / f"{name}.md")

    def write_plain(self, body, digest=None, stamp=True):
        meta = {"ctx_schema": 1, "plan": self.SLUG}
        if stamp:
            meta["digest"] = digest or plain.digest(self.layout, self.SLUG)
        text = frontmatter.Document(meta, body).render()
        return atomic.write_text(plain.path(self.layout, self.SLUG), text)

    def facts(self, **extra):
        base = {"number": 2, "of": 5, "files": 3, "waits_for": [1],
                "alongside": [3, 4],
                "checks": ["the test suite runs", "a person reads the page"]}
        base.update(extra)
        return base

    def generated_text(self, rendered):
        return "\n".join(str(rendered[field]) for field in plain.UNIT_FIELDS)

    def assert_clean(self, text):
        """The vocabulary contract. Deliberately usable as a positive control:
        `test_vocabulary_assertion_fires_on_bad_input` proves it can fail."""
        lowered = text.lower()
        for word in BANNED:
            self.assertNotIn(word, lowered, f"banned word {word!r} in: {text}")
        self.assertNotIn("/", text, f"path separator in: {text}")
        self.assertNotIn("\\", text, f"path separator in: {text}")

    def unit_block(self, name, **fields):
        lines = [f"## Unit: {name}"]
        for field in plain.UNIT_FIELDS:
            label = field[:1].upper() + field[1:]
            lines.append(f"**{label}:** {fields.get(field, '')}".rstrip())
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# parsing and absence
# --------------------------------------------------------------------------- #

class Parsing(PlainCase):

    def test_absent_plain_md_reads_as_empty_rather_than_raising(self):
        """Criterion 1."""
        doc = plain.load(self.layout, self.SLUG)
        self.assertFalse(doc.exists)
        self.assertIsNone(doc.digest)
        self.assertEqual(sorted(doc.plan), sorted(plain.PLAN_SECTIONS))
        self.assertEqual([doc.plan[s] for s in plain.PLAN_SECTIONS],
                         [""] * len(plain.PLAN_SECTIONS))
        self.assertEqual(doc.missing, ["01-alpha", "02-beta"])
        self.assertEqual(doc.unknown, [])

    def test_absent_plan_directory_is_also_not_an_error(self):
        """Criterion 1 — nothing on disk at all."""
        doc = plain.load(self.layout, "no-such-plan")
        self.assertFalse(doc.exists)
        self.assertEqual(doc.missing, [])
        self.assertEqual(doc.unknown, [])

    def test_sections_and_fields_parse_with_whitespace_stripped(self):
        """Criterion 2."""
        self.write_plain(
            "## Summary\n\n  We are fixing two things.  \n\n"
            "## Why now\nBecause the check can miss the one failure.\n\n"
            "## What changes for you\nNothing you click.\n\n"
            "## What could go wrong\nA report reads stricter than before.\n\n"
            "## Out of scope\nThe billing screen.\n\n"
            "## Unit: 01-alpha\n"
            "**What it does:**   Makes the check notice a real breakage.   \n"
            "**Why it matters:** The one failure it exists to catch\n"
            "is the one it can miss.\n"
            "**What changes:** One file that decides pass or fail.\n"
            "**Risk:** Low, it only makes an existing check stricter.\n"
            "**How we'll know:** New tests break something on purpose.\n"
        )
        doc = plain.load(self.layout, self.SLUG)
        self.assertTrue(doc.exists)
        self.assertEqual(doc.plan["summary"], "We are fixing two things.")
        self.assertEqual(doc.plan["out of scope"], "The billing screen.")
        fields = doc.units["01-alpha"]
        self.assertEqual(fields["what it does"],
                         "Makes the check notice a real breakage.")
        self.assertEqual(fields["why it matters"],
                         "The one failure it exists to catch\nis the one it can miss.")
        self.assertEqual(fields["risk"],
                         "Low, it only makes an existing check stricter.")
        self.assertEqual(doc.missing, ["02-beta"])

    def test_curly_apostrophe_in_a_label_still_parses(self):
        """Criterion 2 — a human typing in a word processor."""
        self.write_plain(
            "## Unit: 01-alpha\n"
            "**What it does:** Renames a column.\n"
            "**How we’ll know:** The old name is gone.\n"
        )
        fields = plain.load(self.layout, self.SLUG).units["01-alpha"]
        self.assertEqual(fields["how we'll know"], "The old name is gone.")

    def test_unrecognised_bold_field_is_ignored_not_an_error(self):
        """Criterion 2 — a human wrote this by hand and got a label wrong."""
        self.write_plain(
            "## Unit: 01-alpha\n"
            "**What it does:** Renames a column.\n"
            "**Who asked for it:** The support team.\n"
            "**Risk:** Small, one table.\n"
        )
        doc = plain.load(self.layout, self.SLUG)
        fields = doc.units["01-alpha"]
        self.assertEqual(sorted(fields), sorted(plain.UNIT_FIELDS))
        self.assertEqual(fields["what it does"], "Renames a column.")
        self.assertEqual(fields["risk"], "Small, one table.")
        self.assertNotIn("who asked for it", fields)

    def test_unit_block_not_in_the_plan_is_reported_never_dropped(self):
        """Criterion 3."""
        self.write_plain(
            "## Unit: 01-alpha\n**What it does:** Renames a column.\n\n"
            "## Unit: 09-ghost\n**What it does:** Something deleted last week.\n"
        )
        doc = plain.load(self.layout, self.SLUG)
        self.assertEqual(doc.unknown, ["09-ghost"])
        self.assertNotIn("09-ghost", doc.units)
        self.assertEqual(doc.missing, ["02-beta"])

    def test_present_but_empty_field_counts_as_unauthored(self):
        """Criterion 4 — the single most important behaviour here."""
        plain.scaffold(self.layout, self.SLUG, ["01-alpha", "02-beta"])
        text = plain.path(self.layout, self.SLUG).read_text(encoding="utf-8")
        self.assertIn("**Risk:**", text)  # the form really is on disk

        doc = plain.load(self.layout, self.SLUG)
        self.assertTrue(doc.exists)
        self.assertEqual(doc.missing, ["01-alpha", "02-beta"])
        self.assertEqual([doc.plan[s] for s in plain.PLAN_SECTIONS],
                         [""] * len(plain.PLAN_SECTIONS))
        rendered = doc.unit("01-alpha", self.facts())
        self.assertEqual(sorted(f for f, gen in rendered["generated"].items() if gen),
                         sorted(plain.UNIT_FIELDS))
        self.assertFalse(rendered["authored"])

    def test_a_half_filled_form_generates_only_the_blank_fields(self):
        """Criterion 4 and 8 — per-field, not per-unit."""
        self.write_plain(self.unit_block(
            "01-alpha", **{"what it does": "Renames a column."}))
        doc = plain.load(self.layout, self.SLUG)
        self.assertNotIn("01-alpha", doc.missing)  # it has authored text
        self.assertEqual(doc.missing_fields["01-alpha"],
                         [f for f in plain.UNIT_FIELDS if f != "what it does"])
        rendered = doc.unit("01-alpha", self.facts())
        self.assertEqual(rendered["what it does"], "Renames a column.")
        self.assertFalse(rendered["generated"]["what it does"])
        self.assertTrue(rendered["generated"]["risk"])
        self.assertTrue(rendered["authored"])


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #

class Staleness(PlainCase):

    def test_digest_is_stable_and_hex(self):
        """Criterion 5."""
        first = plain.digest(self.layout, self.SLUG)
        second = plain.digest(self.layout, self.SLUG)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{16,}$")

    def test_digest_follows_the_units_substantive_fields(self):
        """Criterion 5 — it is the contract digest, not a file hash."""
        before = plain.digest(self.layout, self.SLUG)

        # A substantive change: different owned paths.
        self.write_unit("01-alpha", [], owns=["src/renamed.py"])
        self.assertNotEqual(plain.digest(self.layout, self.SLUG), before)

        # And the acceptance criteria, which is prose the contract seals.
        self.write_unit("01-alpha", [], owns=["src/renamed.py"],
                        criteria="## Acceptance criteria\n1. it works differently\n")
        reworded = plain.digest(self.layout, self.SLUG)
        self.assertNotEqual(reworded, before)

        # Status is not substantive: it changes on every dispatch.
        units = {u.name: u for u in plan_mod.load_units(self.layout, self.SLUG)}
        units["01-alpha"].set(status="running")
        self.assertEqual(plain.digest(self.layout, self.SLUG), reworded)

        # Nor is `verified`, which records progress. Work getting signed off is
        # not the plain-language prose going out of date.
        units["01-alpha"].set(verified=["cmd"])
        self.assertEqual(plain.digest(self.layout, self.SLUG), reworded)

    def test_stale_when_nothing_is_stamped(self):
        """Criterion 6."""
        self.write_plain("## Summary\nWords.\n", stamp=False)
        doc = plain.load(self.layout, self.SLUG)
        self.assertIsNone(doc.digest)
        self.assertTrue(doc.stale)

    def test_fresh_when_stamped_digest_matches_and_stale_when_it_does_not(self):
        """Criterion 6."""
        self.write_plain("## Summary\nWords.\n")
        self.assertFalse(plain.load(self.layout, self.SLUG).stale)

        self.write_unit("02-beta", ["01-alpha"], owns=["src/moved.py"])
        self.assertTrue(plain.load(self.layout, self.SLUG).stale)

    def test_absent_plain_md_is_stale(self):
        """Criterion 6 — nothing stamped, because nothing exists."""
        self.assertTrue(plain.load(self.layout, self.SLUG).stale)

    def test_a_noop_re_check_does_not_make_plain_md_stale(self):
        """Criterion 7 — the whole reason this is not keyed off `revision`."""
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        self.write_plain("## Summary\nWords.\n")
        self.assertFalse(plain.load(self.layout, self.SLUG).stale)

        graph = plan_mod.graph_path(self.layout, self.SLUG)
        before = json.loads(graph.read_text(encoding="utf-8"))["revision"]
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        after = json.loads(graph.read_text(encoding="utf-8"))["revision"]

        # The positive control for this test: the re-check really did bump the
        # counter, so a revision-based staleness scheme would now report stale.
        self.assertGreater(after, before)
        self.assertFalse(plain.load(self.layout, self.SLUG).stale)


# --------------------------------------------------------------------------- #
# the generated fallback
# --------------------------------------------------------------------------- #

class Generated(PlainCase):

    def rendered(self, **extra):
        doc = plain.load(self.layout, self.SLUG)
        return doc.unit("01-alpha", self.facts(**extra))

    def test_generated_sentences_are_built_from_the_facts(self):
        """Criterion 8."""
        rendered = self.rendered()
        text = self.generated_text(rendered)
        self.assertIn("2", rendered["what it does"])      # step position
        self.assertIn("5", rendered["what it does"])
        self.assertIn("3", rendered["what changes"])      # how many files
        self.assertIn("1", rendered["what it does"])      # what it waits for
        self.assertIn("4", rendered["what it does"])      # what runs alongside
        self.assertIn("the test suite runs", rendered["how we'll know"])
        self.assertTrue(all(rendered["generated"][f] for f in plain.UNIT_FIELDS))
        self.assertTrue(text.strip())

    def test_generated_text_survives_thin_facts(self):
        """Criterion 8 — a lone unit, nothing to wait for, nothing recorded."""
        rendered = self.rendered(number=1, of=1, files=0, waits_for=[],
                                 alongside=[], checks=[])
        for field in plain.UNIT_FIELDS:
            self.assertTrue(str(rendered[field]).strip(), field)
        self.assert_clean(self.generated_text(rendered))

    def test_generated_text_never_claims_a_purpose_or_a_risk_level(self):
        """Criterion 9."""
        text = self.generated_text(self.rendered()).lower()
        for word in JUDGEMENTS:
            self.assertNotIn(word, text, f"judgement word {word!r} in: {text}")

    def test_generated_vocabulary_is_clean(self):
        """Criterion 10."""
        self.assert_clean(self.generated_text(self.rendered()))
        self.assert_clean(self.generated_text(
            self.rendered(number=7, of=9, files=1, waits_for=[2, 3],
                          alongside=[8], checks=["a person reads the page"])))

    def test_vocabulary_assertion_fires_on_bad_input(self):
        """Criterion 10's positive control: the same assertion, proven to fail.

        Without this, `test_generated_vocabulary_is_clean` could be asserting
        against text that never had a chance of containing a banned word."""
        with self.assertRaises(AssertionError):
            self.assert_clean(self.generated_text(
                self.rendered(checks=["the tier check passes"])))
        with self.assertRaises(AssertionError):
            self.assert_clean(self.generated_text(
                self.rendered(checks=["ruff reads ctx/plain.py"])))

    def test_authored_text_is_returned_untouched(self):
        """Criterion 8 — generation is a fallback, not a filter."""
        self.write_plain(self.unit_block(
            "01-alpha", **{f: f"authored {f}" for f in plain.UNIT_FIELDS}))
        rendered = plain.load(self.layout, self.SLUG).unit("01-alpha", self.facts())
        for field in plain.UNIT_FIELDS:
            self.assertEqual(rendered[field], f"authored {field}")
            self.assertFalse(rendered["generated"][field])


# --------------------------------------------------------------------------- #
# scaffolding
# --------------------------------------------------------------------------- #

class Scaffolding(PlainCase):

    def names(self):
        return [name for name, _ in self.UNITS]

    def test_scaffold_writes_a_form_with_every_field_present_and_empty(self):
        """Criterion 11."""
        written = plain.scaffold(self.layout, self.SLUG, self.names())
        self.assertEqual(written, plain.path(self.layout, self.SLUG))
        text = written.read_text(encoding="utf-8")
        for section in plain.PLAN_SECTIONS:
            self.assertIn(f"## {section[:1].upper()}{section[1:]}", text)
        for name in self.names():
            self.assertIn(f"## Unit: {name}", text)
        for field in plain.UNIT_FIELDS:
            label = f"**{field[:1].upper()}{field[1:]}:**"
            self.assertEqual(text.count(label), len(self.names()))

        parsed = frontmatter.parse(text)
        self.assertEqual(parsed.meta.get("digest"),
                         plain.digest(self.layout, self.SLUG))
        self.assertFalse(plain.load(self.layout, self.SLUG).stale)

    def test_scaffold_appends_only_new_units_and_never_edits_prose(self):
        """Criterion 12."""
        authored = (
            "## Summary\nWe are renaming one column.\n\n"
            "## Unit: 01-alpha\n"
            "**What it does:** Renames a column.\n"
            "**Why it matters:** The old name misleads everyone who reads it.\n"
            "**What changes:** One screen.\n"
            "**Risk:** Small, one table.\n"
            "**How we'll know:** The old name is gone from the screen.\n"
        )
        target = self.write_plain(authored)
        before = target.read_text(encoding="utf-8")

        self.write_unit("03-gamma", ["02-beta"])
        again = plain.scaffold(self.layout, self.SLUG,
                               self.names() + ["03-gamma"])
        after = again.read_text(encoding="utf-8")

        # Byte-for-byte: everything that was there is still there, in order.
        self.assertTrue(after.startswith(before), after)
        self.assertEqual(after.count("## Unit: 01-alpha"), 1)
        self.assertEqual(after.count("**What it does:** Renames a column."), 1)
        self.assertIn("## Unit: 02-beta", after)
        self.assertIn("## Unit: 03-gamma", after)

        doc = plain.load(self.layout, self.SLUG)
        self.assertEqual(doc.units["01-alpha"]["what it does"],
                         "Renames a column.")
        self.assertEqual(doc.missing, ["02-beta", "03-gamma"])

    def test_scaffold_force_rewrites_the_whole_form(self):
        """Criterion 12 — the explicit escape hatch."""
        target = self.write_plain(self.unit_block(
            "01-alpha", **{"risk": "Small, one table."}))
        plain.scaffold(self.layout, self.SLUG, self.names(), force=True)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn("Small, one table.", text)
        self.assertEqual(plain.load(self.layout, self.SLUG).missing,
                         ["01-alpha", "02-beta"])

    def test_scaffold_is_a_noop_when_every_unit_already_has_a_block(self):
        """Criterion 12 — re-scaffolding must not grow the file."""
        plain.scaffold(self.layout, self.SLUG, self.names())
        target = plain.path(self.layout, self.SLUG)
        before = target.read_text(encoding="utf-8")
        plain.scaffold(self.layout, self.SLUG, self.names())
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_scaffold_writes_through_atomic_write_text(self):
        """Criterion 13."""
        for force in (False, True):
            with mock.patch("ctx.plain.atomic.write_text",
                            wraps=atomic.write_text) as spy:
                plain.scaffold(self.layout, self.SLUG, self.names(), force=force)
            self.assertTrue(spy.called, f"force={force}")
        # And the append path, which is the third way in.
        self.write_unit("03-gamma", ["02-beta"])
        with mock.patch("ctx.plain.atomic.write_text",
                        wraps=atomic.write_text) as spy:
            plain.scaffold(self.layout, self.SLUG, self.names() + ["03-gamma"])
        self.assertTrue(spy.called)


if __name__ == "__main__":
    unittest.main()
