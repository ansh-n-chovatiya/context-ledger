"""`ctx.preview` — the one dict every rendered page is built from.

The property this file exists to defend is **agreement**. `plan-check --json`
already derives waves, ordering, contention and a critical-path estimate from
the same unit contracts, and it has done so since before there was a page. A
view-model that derived any of that a second time would be a second truth, free
to disagree with the first on the one artefact a non-technical person signs. So
`TestItAgreesWithPlanCheck` runs the real command against a real fixture plan
and compares the two documents fact by fact; if it ever goes red, the module is
wrong, not the test.

Four more properties, each with its own class:

* **Structure.** Steps are numbered from 1 in wave order, and concurrency is
  expressed as step *numbers* — the page must never have to know a slug.
* **Determinism.** Two calls on unchanged input are byte-identical, and the
  module never reads a clock. The page is committed; a clock in it is a
  spurious diff on every reviewer's branch.
* **Safety.** Prose goes through `preview_html.markup` (so it is redacted and
  escaped); structured values do not, because scrubbing `budget_tokens` is a
  recorded past defect rather than a hypothetical one.
* **Degradation.** No `plain.md`, no units, a plan that was never checked — the
  first two produce a complete model, the third refuses with the command you
  need rather than a `KeyError`.
"""

import ast
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (atomic, frontmatter, plain as plain_mod,  # noqa: E402
                 plan as plan_mod, preview, spec as spec_mod, verify)
from support import OK, Fixture  # noqa: E402


SOURCE = (Path(__file__).resolve().parent.parent / "ctx" / "preview.py")

BODY = (
    "## Objective\nDo the thing.\n\n"
    "## Acceptance criteria\n1. the first one\n2. the second one\n"
)

# Straight from `plain.py`: the contract's own vocabulary, which the generated
# sentences and the generated check phrasings may not use.
BANNED = ("owns", "forbid", "depends_on", "wave", "tier", "budget_tokens",
          "subagent")


class ModelCase(Fixture):
    """A four-unit plan over three waves, checked, with `plan.json` on disk.

        wave 1  01-alpha  02-beta
        wave 2  03-gamma          (waits for 01-alpha)
        wave 3  04-delta          (waits for 03-gamma)

    `src/shared.py` is declared by alpha, gamma and delta — three owners in
    three different waves, so it is a bottleneck and is *not* contested.
    `02-beta` states no budget at all, which is what makes the budget sum a
    real assertion rather than a multiplication.
    """

    SLUG = "demo"
    UNITS = (
        ("01-alpha", [], ["src/shared.py", "src/a.py"], 1000),
        ("02-beta", [], ["src/b.py"], None),
        ("03-gamma", ["01-alpha"], ["src/shared.py", "src/c.py"], 2000),
        ("04-delta", ["03-gamma"], ["src/shared.py"], 4000),
    )

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        for name, deps, owns, budget in self.UNITS:
            self.write_unit(name, deps, owns, budget)
        self.check()

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def write_unit(self, name, deps=(), owns=(), budget=1000, body=BODY):
        meta = {"ctx_schema": 1, "unit": name, "plan": self.SLUG,
                "tier": "subagent", "depends_on": list(deps),
                "owns": list(owns), "reads": [], "forbid": [],
                "status": "pending",
                "verify": [{"kind": "cmd", "run": OK}]}
        if budget is not None:
            meta["budget_tokens"] = budget
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("%s.md" % name)
        frontmatter.Document(meta, body).write(path)
        return path

    def check(self):
        """`ctx plan-check`, leaving `plain.md` exactly as it found it.

        `plan-check` scaffolds the form itself now, so running it is no longer
        a way of getting a plan on disk without one. These cases are about the
        *model*, and several of them are about what it says when nobody has
        written any prose at all — so the form is whatever the case asked for:
        absent unless `write_plain` was called, and untouched when it was.
        """
        form = plain_mod.path(self.layout, self.SLUG)
        existed = form.is_file()
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        if not existed:
            form.unlink(missing_ok=True)

    def model(self):
        return preview.view_model(self.layout, self.SLUG)

    def step(self, model, slug):
        for entry in model["steps"]:
            if entry["slug"] == slug:
                return entry
        self.fail("no step %r in %r" % (slug, [s["slug"] for s in model["steps"]]))

    def write_plain(self, body, digest=True):
        meta = {"ctx_schema": 1, "plan": self.SLUG}
        if digest:
            meta["digest"] = plain_mod.digest(self.layout, self.SLUG)
        path = plain_mod.path(self.layout, self.SLUG)
        atomic.write_text(path, frontmatter.Document(meta, body).render())
        return path


# --------------------------------------------------------------------------- #
# 1-2 — agreement with the derivation that already exists
# --------------------------------------------------------------------------- #

class TestItAgreesWithPlanCheck(ModelCase):
    """The view-model and `plan-check --json` cannot disagree.

    Both are asked of the same plan in the same fixture, and every fact the
    page shows that the command also derives is compared. A second derivation
    would show up here as a mismatch on one of these five.
    """

    def document(self):
        code, out, err = self.cli_streams("plan-check", self.SLUG, "--json")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)["data"]

    def test_wave_membership_is_the_same_in_both(self):
        model, data = self.model(), self.document()
        theirs = dict((entry["wave"], [u["name"] for u in entry["units"]])
                      for entry in data["waves"])
        mine = {}
        for step in model["steps"]:
            mine.setdefault(step["round"], []).append(step["slug"])
        self.assertEqual(mine, theirs)

    def test_the_unit_and_wave_counts_are_the_same_in_both(self):
        model, data = self.model(), self.document()
        self.assertEqual(model["plan"]["counts"]["units"], data["units"])
        self.assertEqual(model["plan"]["counts"]["waves"], len(data["waves"]))

    def test_the_parallelism_ratio_is_the_same_in_both(self):
        model, data = self.model(), self.document()
        self.assertEqual(model["concurrency"], data["parallelism"])
        self.assertEqual(model["concurrency"]["ratio"], 1.33)

    def test_the_critical_path_estimate_is_the_same_in_both(self):
        model, data = self.model(), self.document()
        mine, theirs = dict(model["critical_path"]), dict(data["critical_path"])
        # The basis is a sentence, and this page writes it for a reader who
        # does not have the word `budget_tokens`. Everything numeric is
        # compared; the two sentences deliberately differ.
        mine.pop("basis"), theirs.pop("basis")
        self.assertEqual(mine, theirs)
        self.assertEqual(mine["estimate_tokens"], 1000 + 2000 + 4000)

    def test_contention_and_ownership_gaps_are_the_same_in_both(self):
        model, data = self.model(), self.document()
        numbers = dict((s["slug"], s["number"]) for s in model["steps"])
        theirs = [{"path": entry["path"],
                   "steps": sorted(numbers[name] for name in entry["units"])}
                  for entry in data["bottlenecks"]]
        self.assertEqual(model["bottlenecks"], theirs)
        self.assertEqual([e["path"] for e in theirs], ["src/shared.py"])
        self.assertEqual(model["ownership_gaps"], data["ownership_gaps"])

    def test_the_derivation_is_called_rather_than_repeated(self):
        """The equalities above hold for a faithful copy too, on this fixture.

        What must be true is that the *functions* are called, so that a change
        to how waves are computed reaches the page without anyone editing it.
        That is a claim about the source, so it is checked on the source.
        """
        text = SOURCE.read_text(encoding="utf-8")
        for name in ("plan_mod.check", "plan_mod.waves", "plan_mod.parallelism",
                     "plan_mod.critical_path", "plan_mod.bottlenecks",
                     "plan_mod.ownership_gaps", "plan_mod.covers_any"):
            self.assertIn(name + "(", text, name)


# --------------------------------------------------------------------------- #
# 3-5 — structure
# --------------------------------------------------------------------------- #

class TestTheStructure(ModelCase):

    def test_steps_are_in_wave_order_numbered_from_one(self):
        model = self.model()
        self.assertEqual([s["slug"] for s in model["steps"]],
                         ["01-alpha", "02-beta", "03-gamma", "04-delta"])
        self.assertEqual([s["number"] for s in model["steps"]], [1, 2, 3, 4])
        self.assertEqual([s["round"] for s in model["steps"]], [1, 1, 2, 3])

    def test_concurrency_is_expressed_as_step_numbers_not_slugs(self):
        model = self.model()
        self.assertEqual(self.step(model, "01-alpha")["alongside"], [2])
        self.assertEqual(self.step(model, "02-beta")["alongside"], [1])
        self.assertEqual(self.step(model, "03-gamma")["alongside"], [])
        self.assertEqual(self.step(model, "03-gamma")["waits_for"], [1])
        self.assertEqual(self.step(model, "04-delta")["waits_for"], [3])
        self.assertEqual(self.step(model, "01-alpha")["waits_for"], [])
        for step in model["steps"]:
            for number in step["alongside"] + step["waits_for"]:
                self.assertIsInstance(number, int)

    def test_a_title_a_reader_can_read_carries_no_slug_and_no_path(self):
        model = self.model()
        self.assertEqual(self.step(model, "01-alpha")["title"], "Alpha")
        self.assertEqual(model["plan"]["title"], "Demo")
        for step in model["steps"]:
            self.assertNotIn(step["slug"], step["title"])
            self.assertNotIn("/", step["title"])

    def test_a_path_two_steps_in_one_round_declare_is_contested(self):
        """Contention is a same-round question, and only a same-round one.

        `src/shared.py` has three owners and is *not* contested, because they
        are in three different rounds and will never be writing at once.
        """
        self.write_unit("02-beta", [], ["src/b.py", "src/a.py"], 1000)
        entries = dict((e["path"], e) for e in self.model()["ownership"])

        self.assertEqual(entries["src/a.py"]["steps"], [1, 2])
        self.assertTrue(entries["src/a.py"]["contested"])

        self.assertEqual(entries["src/shared.py"]["steps"], [1, 3, 4])
        self.assertFalse(entries["src/shared.py"]["contested"])
        self.assertTrue(entries["src/shared.py"]["bottleneck"])

    def test_a_unit_with_no_budget_contributes_zero_rather_than_raising(self):
        model = self.model()
        self.assertEqual(model["plan"]["counts"]["budget_tokens"], 7000)
        self.assertEqual(self.step(model, "02-beta")["tech"]["budget_tokens"], 0)
        # The two sums are over the same units, so they must not drift.
        self.assertEqual(model["critical_path"]["total_tokens"],
                         model["plan"]["counts"]["budget_tokens"])

    def test_a_budget_that_is_not_a_number_is_zero_rather_than_raising(self):
        self.write_unit("02-beta", [], ["src/b.py"], "lots")
        model = self.model()
        self.assertEqual(self.step(model, "02-beta")["tech"]["budget_tokens"], 0)
        self.assertEqual(model["plan"]["counts"]["budget_tokens"], 7000)

    def test_the_graph_names_every_step_and_every_dependency_edge(self):
        model = self.model()
        self.assertEqual([node["number"] for node in model["graph"]["nodes"]],
                         [1, 2, 3, 4])
        self.assertEqual(model["graph"]["edges"],
                         [{"from": 1, "to": 3}, {"from": 3, "to": 4}])

    def test_the_technical_view_carries_the_contract_verbatim(self):
        tech = self.step(self.model(), "01-alpha")["tech"]
        self.assertEqual(tech["owns"], ["src/shared.py", "src/a.py"])
        self.assertEqual(tech["tier"], "subagent")
        self.assertEqual(tech["status"], "pending")
        self.assertEqual(len(tech["criteria"]), 2)
        self.assertIn("the first one", tech["criteria"][0])
        self.assertEqual([c["kind"] for c in tech["verify"]], ["cmd"])

    def revision(self):
        graph = plan_mod.graph_path(self.layout, self.SLUG)
        return json.loads(graph.read_text(encoding="utf-8"))["revision"]

    def test_a_re_check_that_changes_nothing_moves_nothing_in_the_model(self):
        """The counter must not reach the model at all.

        This test used to assert the opposite — that `revision` was carried and
        incremented, and that only the prose held still. That was the wrong
        promise: the model is embedded verbatim in a **committed** page which
        `plan-check` now rewrites on every run, so a counter anywhere in it
        means every `plan-check` dirties a tracked file forever, whether or not
        the plan changed. The digest below is the page's identity precisely
        because it moves with what the units promise and not with a run count.

        The bump is forced and asserted rather than assumed: `plan.json` is
        read either side, so a `write_graph` that stopped incrementing could
        not make this pass by accident.
        """
        before = self.revision()
        first = json.dumps(self.model(), sort_keys=True)
        self.check()
        self.assertEqual(self.revision(), before + 1,
                         "the revision did not move, so this proves nothing")
        self.assertEqual(json.dumps(self.model(), sort_keys=True), first)

    def test_the_revision_is_not_in_the_model_at_all(self):
        """Not merely unrendered: it is embedded as JSON, so absence from the
        page means absence from the dict."""
        model = self.model()
        self.assertNotIn("revision", model["plan"])
        self.assertNotIn("revision", json.dumps(model))

    def test_the_digest_stays_and_is_the_page_identity(self):
        """`ctx start` keys its staleness advisory off the digest embedded in
        the page, so this key is load-bearing for a caller outside this unit."""
        model = self.model()
        self.assertEqual(model["plan"]["digest"],
                         plain_mod.digest(self.layout, self.SLUG))
        self.assertTrue(model["plan"]["digest"])


# --------------------------------------------------------------------------- #
# 6-8 — determinism
# --------------------------------------------------------------------------- #

class TestDeterminism(ModelCase):

    def test_two_calls_on_unchanged_input_are_byte_identical(self):
        first, second = self.model(), self.model()
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_the_module_never_reads_a_clock(self):
        """A committed page that changes because a day passed is a spurious
        diff on every reviewer's branch. `plan.json`'s `generated` date is
        carried through as data; the clock behind it is `plan.py`'s."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename="preview.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("now", "today", "utcnow", "time",
                                             "monotonic", "fromtimestamp"))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     ("datetime", "time", "random", "uuid"))
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0],
                                 ("datetime", "time", "random", "uuid"))

    def test_the_detector_would_notice_a_clock(self):
        """A positive control: the walk above must fire on the thing it bans."""
        tree = ast.parse("import datetime\nx = datetime.datetime.now()\n")
        attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        self.assertIn("now", attrs)

    def test_the_generated_date_is_carried_through_as_data(self):
        graph = json.loads(
            plan_mod.graph_path(self.layout, self.SLUG).read_text(encoding="utf-8"))
        self.assertEqual(self.model()["plan"]["generated"], graph["generated"])


# --------------------------------------------------------------------------- #
# 9 — safety: prose is scrubbed, structure is not
# --------------------------------------------------------------------------- #

class TestSafety(ModelCase):

    def test_authored_prose_is_escaped_and_redacted(self):
        self.write_plain(
            "## Summary\nA <script>alert(1)</script> summary.\n\n"
            "## Unit: 01-alpha\n"
            "**What it does:** It rotates the password: hunter2 everywhere.\n"
        )
        model = self.model()
        summary = model["plain"]["sections"]["summary"]
        self.assertNotIn("<script>", summary)
        self.assertIn("&lt;script&gt;", summary)

        what = self.step(model, "01-alpha")["plain"]["what"]
        self.assertNotIn("hunter2", what)
        self.assertIn("redacted", what)

    def test_structured_values_are_not_scrubbed(self):
        """`budget_tokens: 60000` became `budget_tokens: <<redacted>>` once,
        and `credential: none` inverted. Neither is prose; neither is
        scrubbed."""
        self.write_unit("01-alpha", [], ["src/shared.py", "src/a.py"], 60000)
        tech = self.step(self.model(), "01-alpha")["tech"]
        self.assertEqual(tech["budget_tokens"], 60000)
        self.assertEqual(tech["owns"], ["src/shared.py", "src/a.py"])
        self.assertNotIn("redacted", json.dumps(tech["owns"]))

    def test_every_prose_field_is_rendered_html_not_raw_text(self):
        self.write_plain("## Summary\nPlain words.\n")
        model = self.model()
        self.assertTrue(model["plain"]["sections"]["summary"].startswith("<p>"))
        for step in model["steps"]:
            for value in step["plain"].values():
                if isinstance(value, str) and value:
                    self.assertTrue(value.startswith("<p>"), value)

    def test_the_json_survives_a_round_trip_through_a_script_block(self):
        from ctx import preview_html
        self.write_plain("## Summary\nEnds with </script> and & and <.\n")
        model = self.model()
        embedded = preview_html.embed_json(model)
        self.assertNotIn("<", embedded)
        self.assertEqual(json.loads(embedded), model)


# --------------------------------------------------------------------------- #
# 10-12 — degradation
# --------------------------------------------------------------------------- #

class TestDegradation(ModelCase):

    def test_with_no_plain_file_every_step_is_still_complete(self):
        model = self.model()
        self.assertFalse(model["plain"]["present"])
        self.assertEqual(model["plain"]["missing"],
                         ["01-alpha", "02-beta", "03-gamma", "04-delta"])
        self.assertEqual(len(model["steps"]), 4)
        for step in model["steps"]:
            self.assertTrue(all(step["plain"]["generated"].values()), step["slug"])
            self.assertTrue(step["plain"]["what"])
            self.assertTrue(step["plain"]["how_we_know"])

    def test_a_partly_written_unit_is_not_reported_as_missing(self):
        self.write_plain("## Unit: 01-alpha\n**Risk:** Low, in the author's view.\n")
        model = self.model()
        self.assertTrue(model["plain"]["present"])
        self.assertNotIn("01-alpha", model["plain"]["missing"])
        alpha = self.step(model, "01-alpha")["plain"]
        self.assertFalse(alpha["generated"]["risk"])
        self.assertTrue(alpha["generated"]["why"])

    def test_a_block_naming_a_unit_the_plan_does_not_have_is_reported(self):
        self.write_plain("## Unit: 99-ghost\n**Risk:** None.\n")
        self.assertEqual(self.model()["plain"]["unknown"], ["99-ghost"])

    def test_staleness_follows_the_digest_and_not_the_revision(self):
        self.write_plain("## Summary\nWritten once.\n")
        self.assertFalse(self.model()["plain"]["stale"])
        self.check()  # bumps `revision`, changes nothing the plan promises
        self.assertFalse(self.model()["plain"]["stale"])
        self.write_unit("05-epsilon", [], ["src/e.py"], 1000)
        self.assertTrue(self.model()["plain"]["stale"])

    def test_a_plan_with_no_units_is_a_valid_empty_model(self):
        for path in sorted(plan_mod.units_dir(self.layout, self.SLUG).glob("*.md")):
            path.unlink()
        model = self.model()
        self.assertEqual(model["steps"], [])
        self.assertEqual(model["ownership"], [])
        self.assertEqual(model["graph"], {"nodes": [], "edges": []})
        self.assertEqual(model["plan"]["counts"],
                         {"units": 0, "waves": 0, "budget_tokens": 0})
        self.assertEqual(model["concurrency"]["ratio"], 0.0)
        self.assertEqual(json.loads(json.dumps(model)), model)

    def test_a_plan_that_was_never_checked_names_the_command_to_run(self):
        self.assertEqual(self.cli("plan", "fresh", "--no-spec")[0], 0)
        with self.assertRaises(SystemExit) as caught:
            preview.view_model(self.layout, "fresh")
        self.assertIn("ctx plan-check", str(caught.exception))

    def test_an_unreadable_graph_names_the_command_too(self):
        atomic.write_text(plan_mod.graph_path(self.layout, self.SLUG), "{not json")
        with self.assertRaises(SystemExit) as caught:
            self.model()
        self.assertIn("ctx plan-check", str(caught.exception))

    def test_a_unit_that_cannot_be_ordered_is_still_shown(self):
        """A dependency cycle leaves `waves` with nowhere to put its members.

        Dropping them would be the worst possible answer: the page would be
        silently short a step. They go in a trailing round instead.
        """
        self.write_unit("05-epsilon", ["06-zeta"], ["src/e.py"], 1000)
        self.write_unit("06-zeta", ["05-epsilon"], ["src/f.py"], 1000)
        model = self.model()
        self.assertEqual([s["slug"] for s in model["steps"]][-2:],
                         ["05-epsilon", "06-zeta"])
        self.assertEqual(len(model["steps"]), 6)


class TestTheAuthoredStepTitle(ModelCase):
    """A step is headed by what a human called it, when a human called it
    anything.

    `Plain source`, `Safe html`, `Cli wiring` — the slug-derived titles are
    honest and say nothing, and a title taken from the unit's objective would
    put a `.py` path in the one region of the page that may not contain one.
    So the title is authored in `plain.md`'s block heading, and this class
    holds the three things that has to be true of: the authored one is
    preferred, the derived one is untouched underneath it, and the authored one
    is prose — escaped and redacted like every other thing a human typed.
    """

    def test_an_authored_title_reaches_the_step(self):
        self.write_plain(
            "## Unit: 01-alpha — Make the safety check honest\n"
            "**Risk:** Low, in the author's view.\n"
        )
        step = self.step(self.model(), "01-alpha")
        self.assertEqual(step["title"], "Make the safety check honest")
        self.assertFalse(step["title_generated"])

    def test_a_plain_hyphen_separator_reaches_the_step_too(self):
        self.write_plain("## Unit: 02-beta - Stop logging the session token\n")
        step = self.step(self.model(), "02-beta")
        self.assertEqual(step["title"], "Stop logging the session token")
        self.assertFalse(step["title_generated"])

    def test_a_step_with_no_authored_title_keeps_the_derived_one(self):
        """The fallback is unchanged, and it is used per step, not per file.

        `01-alpha` is titled and `02-beta` is not, in the same `plain.md`: one
        authored heading must not decide the other three.
        """
        self.write_plain("## Unit: 01-alpha — Make the safety check honest\n")
        model = self.model()
        self.assertEqual(self.step(model, "02-beta")["title"], "Beta")
        self.assertTrue(self.step(model, "02-beta")["title_generated"])
        self.assertEqual(
            [s["title"] for s in model["steps"]][1:],
            ["Beta", "Gamma", "Delta"])

    def test_with_no_plain_file_at_all_every_title_is_the_derived_one(self):
        model = self.model()
        self.assertEqual([s["title"] for s in model["steps"]],
                         ["Alpha", "Beta", "Gamma", "Delta"])
        self.assertTrue(all(s["title_generated"] for s in model["steps"]))

    def test_an_authored_title_is_escaped_and_redacted_like_any_prose(self):
        self.write_plain(
            "## Unit: 01-alpha — Rotate <script>alert(1)</script> keys\n\n"
            "## Unit: 02-beta — Stop printing password: hunter2 at startup\n"
        )
        model = self.model()

        alpha = self.step(model, "01-alpha")["title"]
        self.assertNotIn("<script>", alpha)
        self.assertIn("&lt;script&gt;", alpha)

        beta = self.step(model, "02-beta")["title"]
        self.assertNotIn("hunter2", beta)
        self.assertIn("redacted", beta)

        # Inline, not a block: these sit inside a heading element, and a `<p>`
        # in an `<h2>` closes the heading at the parser.
        for step in model["steps"]:
            self.assertNotIn("<p>", step["title"])

    def test_a_title_that_sanitizes_away_to_nothing_falls_back(self):
        """An authored heading of invisible characters is not an authored
        title, and a step headed by an empty string is unreadable."""
        invisible = "\u200b" * 3  # zero-width spaces: a heading, not a title
        self.write_plain("## Unit: 01-alpha \u2014 %s\n" % invisible)
        step = self.step(self.model(), "01-alpha")
        self.assertEqual(step["title"], "Alpha")
        self.assertTrue(step["title_generated"])

    def test_the_scaffolded_placeholder_is_not_an_authored_title(self):
        self.write_plain(
            "## Unit: 01-alpha — %s\n" % plain_mod.TITLE_PLACEHOLDER)
        step = self.step(self.model(), "01-alpha")
        self.assertEqual(step["title"], "Alpha")
        self.assertTrue(step["title_generated"])

    def test_an_authored_title_does_not_disturb_determinism(self):
        self.write_plain("## Unit: 01-alpha — Make the safety check honest\n")
        first, second = self.model(), self.model()
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_the_flag_says_which_of_the_two_was_used(self):
        self.write_plain("## Unit: 03-gamma — Widen the gate\n")
        flags = dict((s["slug"], s["title_generated"])
                     for s in self.model()["steps"])
        self.assertEqual(flags, {"01-alpha": True, "02-beta": True,
                                 "03-gamma": False, "04-delta": True})


class TestWritingTheData(ModelCase):

    def test_it_writes_where_it_says_and_round_trips_unchanged(self):
        path = preview.write_data(self.layout, self.SLUG)
        self.assertEqual(path, preview.data_path(self.layout, self.SLUG))
        self.assertEqual(path.name, "preview.data.json")
        self.assertEqual(path.parent, plan_mod.plan_dir(self.layout, self.SLUG))
        with path.open(encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), self.model())

    def test_it_goes_through_the_atomic_writer(self):
        with mock.patch("ctx.preview.atomic.write_text",
                        wraps=atomic.write_text) as writer:
            preview.write_data(self.layout, self.SLUG)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(writer.call_args[0][0],
                         preview.data_path(self.layout, self.SLUG))

    def test_the_html_path_sits_beside_it(self):
        path = preview.html_path(self.layout, self.SLUG)
        self.assertEqual(path.name, "preview.html")
        self.assertEqual(path.parent, plan_mod.plan_dir(self.layout, self.SLUG))

    def test_the_schema_is_stamped_into_the_document(self):
        self.assertEqual(preview.SCHEMA, 1)
        self.assertEqual(self.model()["schema"], preview.SCHEMA)


# --------------------------------------------------------------------------- #
# the generated "how we'll know" phrasings
# --------------------------------------------------------------------------- #

class TestTheCheckPhrasings(unittest.TestCase):
    """`plain._generate` renders these verbatim into "How we'll know".

    So they are the one place this module writes English a reviewer reads
    directly, and the rules are the same ones `plain.py` holds itself to: no
    command, no path, none of the contract's vocabulary.
    """

    def test_every_verify_kind_has_a_phrasing(self):
        """A kind added to `KIND_TABLE` must be given one here, not fall
        through to a generic sentence that tells the reader nothing."""
        self.assertEqual(sorted(preview.CHECK_PHRASES), sorted(verify.KIND_TABLE))

    def test_no_phrasing_uses_a_path_or_the_contract_vocabulary(self):
        for kind, phrase in sorted(preview.CHECK_PHRASES.items()):
            self.assertNotIn("/", phrase, kind)
            self.assertNotIn("\\", phrase, kind)
            self.assertNotIn("`", phrase, kind)
            for word in BANNED:
                self.assertNotIn(word, phrase.lower(), kind)

    def test_no_phrasing_changes_when_it_is_escaped(self):
        """So the sentence in the model and the sentence on the page are the
        same sentence, rather than the same sentence and its entities."""
        from ctx import preview_html
        for kind, phrase in sorted(preview.CHECK_PHRASES.items()):
            self.assertEqual(preview_html.escape(phrase), phrase, kind)


class TestTheCheckPhrasingsReachTheStep(ModelCase):

    def test_the_generated_how_we_know_uses_them_and_deduplicates(self):
        self.write_unit("01-alpha", [], ["src/a.py"], 1000)
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        doc = frontmatter.read(directory / "01-alpha.md")
        doc.meta["verify"] = [{"kind": "cmd", "run": OK},
                              {"kind": "cmd", "run": OK},
                              {"kind": "human"}]
        doc.write(directory / "01-alpha.md")

        how = self.step(self.model(), "01-alpha")["plain"]["how_we_know"]
        self.assertIn(preview.CHECK_PHRASES["cmd"], how)
        self.assertIn(preview.CHECK_PHRASES["human"], how)
        self.assertEqual(how.count(preview.CHECK_PHRASES["cmd"]), 1)
        # No path separator in what the reader actually sees. The `</p>` the
        # renderer wraps it in is markup, not text, so tags come off first.
        self.assertNotIn("/", re.sub(r"</?[a-z]+>", "", how))


# --------------------------------------------------------------------------- #
# 17-19 — the better fallbacks: the unit's own words, then real facts
# --------------------------------------------------------------------------- #

class TestTierThreeFromObjective(Fixture):
    """A unit's own Objective/Background fills 'what it does'/'why it matters'
    when plain.md has nothing, instead of the generic step-position sentence."""

    SLUG = "tier-three"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/a.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nStop the probe from executing a repo-shipped binary.\n\n"
            "## Background\nA cloned repo could ship a fake interpreter and have "
            "it run before the trust prompt ever shows.\n",
        ).write(directory / "01-a.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_what_it_does_comes_from_the_objective_not_the_position_sentence(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertIn("execut", step["plain"]["what"].lower())
        self.assertEqual(step["plain"]["provenance"]["what"], "inferred")

    def test_why_it_matters_comes_from_the_background(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertIn("trust prompt", step["plain"]["why"])
        self.assertEqual(step["plain"]["provenance"]["why"], "inferred")

    def test_generated_flag_still_true_for_backward_compatibility(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertTrue(step["plain"]["generated"]["what"])

    def test_authored_text_still_wins_over_the_units_own_objective(self):
        """The whole point of `plain.md` is that a human's words are final."""
        meta = {"ctx_schema": 1, "plan": self.SLUG,
                "digest": plain_mod.digest(self.layout, self.SLUG)}
        atomic.write_text(
            plain_mod.path(self.layout, self.SLUG),
            frontmatter.Document(
                meta,
                "## Unit: 01-a\n**What it does:** It stops a nasty surprise.\n",
            ).render(),
        )
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertIn("nasty surprise", step["plain"]["what"])
        self.assertEqual(step["plain"]["provenance"]["what"], "authored")
        self.assertFalse(step["plain"]["generated"]["what"])
        # And the field nobody authored still falls back to the unit's prose.
        self.assertEqual(step["plain"]["provenance"]["why"], "inferred")

    def test_an_objective_that_is_only_a_scaffold_comment_is_not_quoted(self):
        """A section holding nothing but the form's own `<!-- ... -->` hint has
        not been written. Quoting the hint at a non-technical reader is worse
        than the generated sentence it would displace."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        doc = frontmatter.read(directory / "01-a.md")
        doc.body = "## Objective\n<!-- one sentence: the observable outcome -->\n"
        doc.write(directory / "01-a.md")
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertNotIn("observable outcome", step["plain"]["what"])
        self.assertEqual(step["plain"]["provenance"]["what"], "generated")

    def assert_reader_safe(self, step, key):
        """The rule `plain.py` states and `test_preview_page` enforces on the
        rendered plain half: no file path, none of the contract's words."""
        text = re.sub(r"</?[a-z]+>", "", step["plain"][key])
        self.assertNotIn(".py", text, key)
        self.assertNotIn("/", text, key)
        self.assertNotIn("\\", text, key)
        for word in BANNED:
            self.assertNotIn(word, text.lower(), (key, word))

    def test_an_objective_written_for_an_implementer_is_declined_not_quoted(self):
        """An `## Objective` is written for the person doing the work, and
        routinely names files and says `owns`/`forbid`. That is right there and
        barred here, so the text is declined rather than laundered — the field
        falls through to the generated sentence as if nothing were written.
        """
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        doc = frontmatter.read(directory / "01-a.md")
        doc.body = ("## Objective\nMake ctx/verify.py report a real breakage.\n\n"
                    "## Background\nThe step that owns src/a.py must forbid it.\n")
        doc.write(directory / "01-a.md")

        # Calibration: the fixture really does carry the vocabulary, so this
        # cannot pass by there being nothing to decline.
        contract = (directory / "01-a.md").read_text(encoding="utf-8")
        self.assertIn("ctx/verify.py", contract)
        self.assertIn("forbid", contract)

        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        for key in ("what", "why"):
            self.assert_reader_safe(step, key)
            self.assertEqual(step["plain"]["provenance"][key], "generated")
            self.assertTrue(step["plain"][key], key)

    def test_only_the_field_that_trips_the_check_is_declined(self):
        """One unsafe section does not cost a safe one its tier."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        doc = frontmatter.read(directory / "01-a.md")
        doc.body = ("## Objective\nStop the probe from running a shipped binary.\n\n"
                    "## Background\nThe step that owns src/a.py did it first.\n")
        doc.write(directory / "01-a.md")
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertIn("shipped binary", step["plain"]["what"])
        self.assertEqual(step["plain"]["provenance"]["what"], "inferred")
        self.assertEqual(step["plain"]["provenance"]["why"], "generated")
        self.assert_reader_safe(step, "why")

    def test_english_that_merely_uses_a_slash_is_not_mistaken_for_a_path(self):
        """"and/or" is not a file path, and declining it would cost a reader a
        perfectly good sentence."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        doc = frontmatter.read(directory / "01-a.md")
        doc.body = "## Objective\nStop read/write access and/or execution.\n"
        doc.write(directory / "01-a.md")
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertIn("and/or", step["plain"]["what"])
        self.assertEqual(step["plain"]["provenance"]["what"], "inferred")

    def test_the_declined_words_are_the_same_words_the_page_rule_names(self):
        """Two lists of the same rule would eventually disagree about it, and
        the quiet direction is the dangerous one: a word dropped from the
        module's tuple would let that word onto the page with nothing red."""
        self.assertEqual(sorted(preview.CONTRACT_WORDS), sorted(BANNED))

    def test_a_field_with_no_source_at_all_is_still_marked_generated(self):
        """Tier 3 has nothing to say about "what changes", so the generated
        sentence stands and says so."""
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertEqual(step["plain"]["provenance"]["changes"], "generated")
        self.assertTrue(step["plain"]["changes"])


class TestTierFourFromOwnershipGaps(Fixture):
    """A unit's risk field, when unauthored, states the real ownership-gap
    fact for its own owned paths rather than the generic sentence.

    The brief's draft of this case owned `ctx/hooks.py` and leaned on *this*
    repository's own `tests/` tree being walked. It is not:
    `plan.ownership_gaps` walks `Path(layout.root).parent`, which for a
    `Fixture` is the throwaway temp project — so that version could only ever
    skip. The gap is built inside the fixture instead, which is both a real
    assertion and independent of what this checkout's own tests happen to
    import.
    """

    SLUG = "tier-four"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        # A test nobody in the plan owns, that names a module somebody does.
        self.write("tests/test_thing.py", "from src import a\n\n\ndef test_a():\n    pass\n")
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/a.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nDoes something.\n",
        ).write(directory / "01-a.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_the_fixture_really_has_the_gap_this_case_is_about(self):
        """Calibration: without this the case below would pass vacuously."""
        vm = preview.view_model(self.layout, self.SLUG)
        self.assertEqual(
            [gap["path"] for gap in vm["ownership_gaps"]["files"]], ["src/a.py"])

    def test_risk_states_the_real_gap_as_a_count(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertIn("One test elsewhere in the project", step["plain"]["risk"])
        self.assertEqual(step["plain"]["provenance"]["risk"], "inferred")

    def test_the_count_is_of_tests_and_moves_with_them(self):
        """Calibration: a fixed sentence would pass the case above too."""
        self.write("tests/test_other.py", "from src import a\n")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        step = preview.view_model(self.layout, self.SLUG)["steps"][0]
        self.assertIn("2 tests elsewhere in the project", step["plain"]["risk"])

    def test_the_risk_never_names_a_file_even_though_it_knows_one(self):
        """`plain.py`'s rule for this half of the page: no file path and none
        of the contract's vocabulary, "because the reader of this page does not
        have those words". Inferred text is held to it exactly as generated
        text is — the paths are in the technical view and the gap table, which
        is where a reader who has those words will look.
        """
        vm = preview.view_model(self.layout, self.SLUG)
        # Calibration: the model really does know the path, elsewhere.
        self.assertIn("src/a.py", json.dumps(vm["ownership_gaps"]))
        for step in vm["steps"]:
            risk = re.sub(r"</?[a-z]+>", "", step["plain"]["risk"])
            self.assertNotIn(".py", risk, step["slug"])
            self.assertNotIn("/", risk, step["slug"])
            self.assertNotIn("\\", risk, step["slug"])
            for word in BANNED:
                self.assertNotIn(word, risk.lower(), (step["slug"], word))

    def test_a_unit_owning_the_gap_by_pattern_is_told_about_it_too(self):
        """Who owns a path is `plan.covers_any`'s question everywhere else in
        this module. A unit owning `src/*.py` owns `src/a.py` for the wave
        collision check, so it owns it for the risk it is shown."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "02-b", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/*.py"], "depends_on": ["01-a"],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nDoes something else.\n",
        ).write(directory / "02-b.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        vm = preview.view_model(self.layout, self.SLUG)
        second = [s for s in vm["steps"] if s["slug"] == "02-b"][0]
        self.assertIn("One test elsewhere in the project",
                      second["plain"]["risk"])
        self.assertEqual(second["plain"]["provenance"]["risk"], "inferred")

    def test_a_unit_owning_nothing_in_the_gap_list_keeps_the_generated_risk(self):
        """The fact is the unit's own, not the plan's. A second step owning a
        path no outside test names must not inherit the first one's risk."""
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "02-b", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/b.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nDoes something else.\n",
        ).write(directory / "02-b.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        vm = preview.view_model(self.layout, self.SLUG)
        second = [s for s in vm["steps"] if s["slug"] == "02-b"][0]
        self.assertEqual(second["plain"]["provenance"]["risk"], "generated")
        self.assertNotIn("elsewhere in the project", second["plain"]["risk"])
        # And the step that does have the gap still says so, in the same model.
        first = [s for s in vm["steps"] if s["slug"] == "01-a"][0]
        self.assertEqual(first["plain"]["provenance"]["risk"], "inferred")


class TestThePlanLevelIntakeTier(Fixture):
    """The three intake categories answer the plan sections of the same name.

    `ctx spec` collects "why now", "what could go wrong" and "what changes for
    you" before a plan may be written. Those are three of `plain.PLAN_SECTIONS`
    by name, so a plan whose summary nobody has written by hand is not
    blank — it repeats what the spec was already told.
    """

    SLUG = "intake-tier"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        spec_mod.create(self.layout, self.SLUG)
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/a.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nDoes something.\n",
        ).write(directory / "01-a.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def model(self):
        return preview.view_model(self.layout, self.SLUG)

    def test_an_inferred_intake_answer_fills_the_section_of_the_same_name(self):
        spec_mod.record_inferred(
            self.layout, self.SLUG, "why now",
            "The probe runs before anyone is asked to trust it",
            "stated twice in the audit")
        model = self.model()
        self.assertIn("asked to trust it", model["plain"]["sections"]["why now"])
        self.assertEqual(model["plain"]["provenance"]["why now"], "inferred")
        # The audit trail the Resolved line carries is not prose for a reader.
        self.assertNotIn("inferred, not asked",
                         model["plain"]["sections"]["why now"])

    def test_an_answered_question_contributes_its_answer_not_its_question(self):
        spec_mod.add_questions(
            self.layout, self.SLUG,
            ["What could go wrong: is there any way back?"])
        spec_mod.resolve(self.layout, self.SLUG, "What could go wrong",
                         "A reviewer could sign a page that is already stale")
        model = self.model()
        section = model["plain"]["sections"]["what could go wrong"]
        self.assertIn("already stale", section)
        self.assertNotIn("is there any way back", section)
        self.assertEqual(model["plain"]["provenance"]["what could go wrong"],
                         "inferred")

    def test_a_section_with_no_intake_category_is_still_generated(self):
        """"Summary" and "out of scope" are not intake categories. Nothing is
        invented for them."""
        model = self.model()
        for section in ("summary", "out of scope"):
            self.assertEqual(model["plain"]["provenance"][section], "generated")
            self.assertEqual(model["plain"]["sections"][section], "")

    def test_authored_prose_still_wins_over_the_intake_answer(self):
        spec_mod.record_inferred(self.layout, self.SLUG, "why now",
                                 "Because of the audit", "stated in the audit")
        meta = {"ctx_schema": 1, "plan": self.SLUG,
                "digest": plain_mod.digest(self.layout, self.SLUG)}
        atomic.write_text(
            plain_mod.path(self.layout, self.SLUG),
            frontmatter.Document(
                meta, "## Why now\nBecause a customer asked.\n").render(),
        )
        model = self.model()
        self.assertIn("customer asked", model["plain"]["sections"]["why now"])
        self.assertNotIn("audit", model["plain"]["sections"]["why now"])
        self.assertEqual(model["plain"]["provenance"]["why now"], "authored")

    def test_an_unreadable_questions_file_degrades_rather_than_crashing(self):
        """Guarded the way `plain.load` guards its own read. `ctx preview` is
        often the first thing anyone runs against a ledger somebody else
        wrote, and a page that will not render says far less than a page with
        one tier missing."""
        spec_mod.record_inferred(self.layout, self.SLUG, "why now",
                                 "Because of the audit", "stated in the audit")
        with mock.patch.object(spec_mod, "questions",
                               side_effect=OSError("unreadable")):
            model = self.model()
        self.assertEqual(model["plain"]["provenance"]["why now"], "generated")
        self.assertEqual(model["plain"]["sections"]["why now"], "")
        # The rest of the page is entirely unaffected.
        self.assertEqual(len(model["steps"]), 1)
        self.assertTrue(model["steps"][0]["plain"]["what"])

    def test_every_plan_section_has_a_provenance(self):
        model = self.model()
        self.assertEqual(sorted(model["plain"]["provenance"]),
                         sorted(plain_mod.PLAN_SECTIONS))


if __name__ == "__main__":
    unittest.main()
