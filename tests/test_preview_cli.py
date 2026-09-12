"""`ctx preview`, and the two commands that reach for it without being asked.

The page is only worth building if the person it was built for can find it, and
that person does not read this repository's command list. So the wiring, not
the renderer, is what this file is about:

  * `ctx preview` itself — writes the page, the data file, the form; checks the
    page *on disk*; opens a browser when there is one and says where the file
    is when there is not.
  * `ctx plan-check` writes the page on **every** run, tops up `plain.md`, and
    **cannot be failed by either**. A preview that will not render is a note on
    an otherwise successful plan check, never a reason planning stopped.
  * `ctx start` adds exactly one line — where the page is, or that it is
    missing or behind. The line count is asserted, not just its presence: an
    advisory that grows into a paragraph is how a brief stops being read.

The renderer's own correctness lives in `tests/test_preview_page.py` and the
model's in `tests/test_preview_model.py`. Nothing here re-asserts either.
"""

import json
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    commands, frontmatter, plain as plain_mod, plan as plan_mod,
    preview, preview_page,
)
from support import OK, Fixture  # noqa: E402

BODY = ("## Objective\nDo the thing.\n\n"
        "## Acceptance criteria\n1. it works\n2. it keeps working\n")


class PlanCase(Fixture):
    """A two-unit plan in one wave, checked once so `plan.json` exists."""

    SLUG = "auth"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        self.write_unit("01-login")
        self.write_unit("02-logout")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def write_unit(self, name, deps=(), owns=None):
        meta = {"ctx_schema": 1, "unit": name, "plan": self.SLUG,
                "tier": "subagent", "depends_on": list(deps),
                "owns": list(owns or ["src/%s.py" % name]), "reads": [],
                "forbid": [], "budget_tokens": 1000, "status": "pending",
                "verify": [{"kind": "cmd", "run": OK}]}
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(meta, BODY).write(directory / ("%s.md" % name))

    # ---------------------------------------------------------------- #

    @property
    def page(self):
        return preview.html_path(self.layout, self.SLUG)

    @property
    def form(self):
        return plain_mod.path(self.layout, self.SLUG)

    def document(self, *argv):
        code, out, err = self.cli_streams(*argv, "--json")
        self.assertEqual(err, "", err)
        return code, json.loads(out)["data"]


# --------------------------------------------------------------------------- #
# 1-2 — the command writes the page, and the data beside it
# --------------------------------------------------------------------------- #

class TestTheCommandWrites(PlanCase):

    def test_it_writes_the_page_and_echoes_the_relative_path(self):
        self.page.unlink()
        code, out = self.cli("preview", self.SLUG)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.page.is_file())
        self.assertEqual(out.strip(), "wrote .ctx/plans/auth/preview.html")
        self.assertNotIn(str(self.root), out, "an absolute path leaked")

    def test_with_no_slug_it_uses_the_active_plan(self):
        self.page.unlink()
        code, out = self.cli("preview")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.page.is_file())

    def test_a_named_plan_is_never_mistaken_for_a_unit(self):
        """`ctx preview 01-login` must look for a *plan* called `01-login` —
        the positional is the plan, as it is for `plan-check` and `start`."""
        self.assertEqual(commands._PLAN_ARGUMENT["preview"], "name")
        code, out = self.cli("preview", "01-login")
        self.assertEqual(code, 2, out)
        self.assertNotIn("wrote", out)

    def test_data_writes_the_model_beside_the_page(self):
        code, out = self.cli("preview", self.SLUG, "--data")
        self.assertEqual(code, 0, out)
        data = preview.data_path(self.layout, self.SLUG)
        self.assertIn("wrote .ctx/plans/auth/preview.data.json", out)
        self.assertEqual(json.loads(data.read_text(encoding="utf-8"))["plan"]["slug"],
                         self.SLUG)

    def test_data_is_off_unless_asked_for(self):
        preview.data_path(self.layout, self.SLUG).unlink(missing_ok=True)
        self.cli("preview", self.SLUG)
        self.assertFalse(preview.data_path(self.layout, self.SLUG).is_file())


class TestWithNoPlanAtAll(Fixture):
    """A ledger with nothing planned in it — the state a first-time user is in."""

    def test_it_says_there_is_no_plan_and_exits_zero(self):
        """The same preamble the other plan commands print, from the same
        helper — `_plan_or_report`, not a fourth copy of the words.

        Exit 0 because Claude Code abandons a slash command whose `!` line
        failed, and the user would never reach the prompt that asks for a plan.
        """
        code, out = self.cli("preview")
        self.assertEqual(code, 0, out)
        self.assertIn("no active plan", out)
        self.assertIn("/ctx:plan", out)

    def test_strict_is_how_a_script_asks_to_hear_about_it(self):
        self.assertEqual(self.cli("preview", "--strict")[0], 1)

    def test_with_no_ledger_at_all_it_refuses_like_every_other_command(self):
        code, out, err = self.cli_streams("preview", cwd=self.untracked)
        self.assertEqual(code, 2, out + err)
        self.assertIn("/ctx:init", err)
        self.assertEqual(out, "", "a refusal must not go to stdout")


# --------------------------------------------------------------------------- #
# 3 — --check reads the file on disk
# --------------------------------------------------------------------------- #

class TestCheck(PlanCase):

    def test_a_freshly_written_page_checks_clean(self):
        self.assertEqual(self.cli("preview", self.SLUG)[0], 0)
        code, out = self.cli("preview", self.SLUG, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("carries the whole plan", out)

    def test_a_page_that_lost_the_plan_exits_non_zero_and_names_what_went(self):
        """The positive control for the paragraph above: the checker has to be
        able to fail, and it has to say which step it lost."""
        self.page.write_text("<!DOCTYPE html>\n<html><body><p>nothing</p></body></html>\n",
                             encoding="utf-8")
        code, out = self.cli("preview", self.SLUG, "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("01-login", out)
        self.assertIn("02-logout", out)

    def test_check_reads_the_disk_rather_than_rendering_a_page_to_check(self):
        """A `--check` that re-rendered first would only ever prove that the
        renderer agrees with itself."""
        broken = "<!DOCTYPE html>\n<html><body><p>nothing</p></body></html>\n"
        self.page.write_text(broken, encoding="utf-8")
        self.cli("preview", self.SLUG, "--check")
        self.assertEqual(self.page.read_text(encoding="utf-8"), broken)

    def test_with_no_page_on_disk_it_refuses_and_names_the_command(self):
        self.page.unlink()
        code, out = self.cli("preview", self.SLUG, "--check")
        self.assertEqual(code, 2, out)
        self.assertIn("ctx preview auth", out)
        self.assertFalse(self.page.is_file())

    def test_the_json_shape_carries_the_problems(self):
        self.page.write_text("<html><body>gone</body></html>\n", encoding="utf-8")
        code, data = self.document("preview", self.SLUG, "--check")
        self.assertEqual(code, 1)
        self.assertTrue(data["checked"])
        self.assertTrue(data["problems"])
        self.assertEqual(data["page"], ".ctx/plans/auth/preview.html")


# --------------------------------------------------------------------------- #
# 4 — --open, including on a machine with no browser
# --------------------------------------------------------------------------- #

class TestOpen(PlanCase):

    def browser(self, result):
        fake = unittest.mock.Mock()
        if isinstance(result, Exception):
            fake.open.side_effect = result
        else:
            fake.open.return_value = result
        return unittest.mock.patch.object(commands, "webbrowser", fake), fake

    def test_it_asks_the_machine_for_a_browser(self):
        patch, fake = self.browser(True)
        with patch:
            code, out = self.cli("preview", self.SLUG, "--open")
        self.assertEqual(code, 0, out)
        self.assertIn("opened it in your browser", out)
        (url,), _kw = fake.open.call_args
        self.assertTrue(url.startswith("file://"), url)
        self.assertTrue(url.endswith("preview.html"), url)

    def test_a_headless_machine_prints_the_path_instead_of_raising(self):
        patch, _fake = self.browser(False)
        with patch:
            code, out = self.cli("preview", self.SLUG, "--open")
        self.assertEqual(code, 0, out)
        self.assertIn("no browser on this machine", out)
        self.assertIn("preview.html", out)

    def test_a_browser_that_raises_is_not_a_failed_command(self):
        """`webbrowser.open` raises rather than returning False on some
        platforms. Either way the answer is the path."""
        patch, _fake = self.browser(RuntimeError("no display"))
        with patch:
            code, out = self.cli("preview", self.SLUG, "--open")
        self.assertEqual(code, 0, out)
        self.assertIn("no browser on this machine", out)


# --------------------------------------------------------------------------- #
# 5 — --scaffold-plain
# --------------------------------------------------------------------------- #

class TestScaffoldPlain(PlanCase):

    def test_it_writes_the_form_with_a_block_per_unit(self):
        self.form.unlink()
        code, out = self.cli("preview", self.SLUG, "--scaffold-plain")
        self.assertEqual(code, 0, out)
        text = self.form.read_text(encoding="utf-8")
        self.assertIn("## Unit: 01-login", text)
        self.assertIn("## Unit: 02-logout", text)
        self.assertIn("wrote .ctx/plans/auth/plain.md", out)

    def test_it_refuses_to_overwrite_what_somebody_wrote(self):
        self.form.write_text("---\nplan: auth\n---\n## Summary\nMine.\n",
                             encoding="utf-8")
        code, out = self.cli("preview", self.SLUG, "--scaffold-plain")
        self.assertEqual(code, 2, out)
        self.assertIn("already exists", out)
        self.assertIn("--force", out)
        self.assertIn("Mine.", self.form.read_text(encoding="utf-8"))

    def test_force_rewrites_it(self):
        self.form.write_text("---\nplan: auth\n---\n## Summary\nMine.\n",
                             encoding="utf-8")
        code, out = self.cli("preview", self.SLUG, "--scaffold-plain", "--force")
        self.assertEqual(code, 0, out)
        text = self.form.read_text(encoding="utf-8")
        self.assertNotIn("Mine.", text)
        self.assertIn("## Unit: 01-login", text)


# --------------------------------------------------------------------------- #
# 6 — --json
# --------------------------------------------------------------------------- #

class TestJson(PlanCase):

    def test_the_document_is_the_whole_of_stdout(self):
        code, data = self.document("preview", self.SLUG, "--data")
        self.assertEqual(code, 0)
        self.assertEqual(data["page"], ".ctx/plans/auth/preview.html")
        self.assertEqual(data["data"], ".ctx/plans/auth/preview.data.json")
        self.assertEqual(data["problems"], [])

    def test_the_shape_is_the_same_whichever_mode_ran(self):
        """A consumer reading `problems` should not have to know which flag was
        typed to know the key is there."""
        self.form.unlink()
        keys = set()
        for argv in (("preview", self.SLUG),
                     ("preview", self.SLUG, "--scaffold-plain"),
                     ("preview", self.SLUG, "--check")):
            keys.add(tuple(sorted(self.document(*argv)[1])))
        self.assertEqual(len(keys), 1, keys)

    def test_the_flag_does_not_change_what_was_written(self):
        self.cli("preview", self.SLUG)
        plain = self.page.read_bytes()
        self.document("preview", self.SLUG)
        self.assertEqual(self.page.read_bytes(), plain)


# --------------------------------------------------------------------------- #
# 7-8 — plan-check writes the page, and can never be failed by it
# --------------------------------------------------------------------------- #

class TestPlanCheckWritesThePage(PlanCase):

    def test_it_writes_the_page_on_every_run_and_echoes_the_path(self):
        self.page.unlink()
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.page.is_file())
        self.assertIn("wrote .ctx/plans/auth/preview.html", out)

    def test_the_page_comes_after_the_graph_and_the_readme(self):
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertLess(lines.index("wrote .ctx/plans/auth/plan.json"),
                        lines.index("wrote .ctx/plans/auth/preview.html"))
        self.assertTrue((plan_mod.plan_dir(self.layout, self.SLUG)
                         / "README.md").is_file())

    def test_two_renders_of_one_graph_are_byte_identical(self):
        """The page is committed, so the renderer has to be deterministic: same
        `plan.json`, same bytes, whoever ran it and whenever."""
        self.cli("preview", self.SLUG)
        first = self.page.read_bytes()
        self.cli("preview", self.SLUG)
        self.assertEqual(self.page.read_bytes(), first)

    def test_across_plan_checks_only_the_revision_it_quotes_moves(self):
        """`plan-check` bumps `plan.json`'s revision on every run, and the page
        quotes it — so the page does churn in a diff, exactly as `plan.json`
        already does and for the same reason. Nothing *else* about it moves,
        which is what keeps the churn readable rather than a rewrite.
        """
        def without_revision(raw):
            # Twice over: the sentence a reader sees in the footer, and the
            # same number inside the embedded view-model.
            text = re.sub(r"revision \d+", "revision <N>", raw.decode("utf-8"))
            return re.sub(r'"revision":\s*\d+', '"revision":<N>',
                          text).encode("utf-8")

        self.cli("plan-check", self.SLUG)
        first = self.page.read_bytes()
        self.cli("plan-check", self.SLUG)
        second = self.page.read_bytes()
        self.assertNotEqual(second, first, "the revision did not move at all")
        self.assertEqual(without_revision(second), without_revision(first))

    def test_the_json_document_carries_the_page(self):
        code, data = self.document("plan-check", self.SLUG)
        self.assertEqual(code, 0)
        self.assertEqual(data["preview"], ".ctx/plans/auth/preview.html")

    def test_a_preview_that_will_not_render_does_not_fail_the_plan_check(self):
        """The property this is all conditional on. `plan-check` is a gate that
        CI runs; a renderer bug must not be able to close it."""
        def boom(*_a, **_kw):
            raise RuntimeError("the renderer fell over")

        with unittest.mock.patch.object(preview_page, "write", boom):
            code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        self.assertIn("note:", out)
        self.assertIn("RuntimeError", out)
        self.assertIn("the renderer fell over", out)
        self.assertIn("wrote .ctx/plans/auth/plan.json", out)

    def test_the_failure_note_reaches_the_json_document_as_a_null_path(self):
        def boom(*_a, **_kw):
            raise RuntimeError("nope")

        with unittest.mock.patch.object(preview_page, "write", boom):
            code, data = self.document("plan-check", self.SLUG)
        self.assertEqual(code, 0)
        self.assertIsNone(data["preview"])


# --------------------------------------------------------------------------- #
# 9 — plan-check tops up plain.md and never edits it
# --------------------------------------------------------------------------- #

class TestPlanCheckScaffoldsTheForm(PlanCase):

    def test_it_writes_the_form_when_there_is_none(self):
        self.form.unlink()
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        text = self.form.read_text(encoding="utf-8")
        self.assertIn("## Unit: 01-login", text)
        self.assertIn("## Unit: 02-logout", text)

    def test_authored_prose_survives_a_plan_that_grew(self):
        """The whole risk of scaffolding on every run, made into a test: a
        human writes an answer, the plan gains a unit, and the answer is still
        there afterwards — byte for byte, with a blank block added for the new
        unit and for nothing else."""
        self.cli("plan-check", self.SLUG)
        authored = self.form.read_text(encoding="utf-8").replace(
            "## Unit: 01-login — <!-- a short title, in plain words -->\n"
            "**What it does:**",
            "## Unit: 01-login — Let people sign in\n"
            "**What it does:** Gives everyone a way in, with their own password.",
        )
        self.assertIn("Let people sign in", authored, "the fixture did not match")
        self.form.write_text(authored, encoding="utf-8")

        self.write_unit("03-audit", deps=["01-login"])
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)

        after = self.form.read_text(encoding="utf-8")
        self.assertIn("## Unit: 01-login — Let people sign in", after)
        self.assertIn("Gives everyone a way in, with their own password.", after)
        self.assertIn("## Unit: 03-audit", after)
        self.assertTrue(after.startswith(authored.rstrip("\n")),
                        "the authored half of the file was rewritten, not appended to")
        self.assertEqual(after.count("## Unit: 01-login"), 1)

    def test_the_page_shows_what_a_human_wrote(self):
        """The point of the form, end to end: prose in `plain.md` reaches the
        page that `plan-check` renders, with no other command run."""
        self.cli("plan-check", self.SLUG)
        text = self.form.read_text(encoding="utf-8").replace(
            "**What it does:**",
            "**What it does:** Gives everyone a way in.", 1)
        self.form.write_text(text, encoding="utf-8")
        self.cli("plan-check", self.SLUG)
        self.assertIn("Gives everyone a way in.",
                      self.page.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 11 — `ctx start` gains one line and nothing else
# --------------------------------------------------------------------------- #

class TestStartAdvisesOnce(PlanCase):

    def dispatch(self):
        """`ctx start`, with the brief itself stubbed to a single known line.

        That is what makes "exactly one new line" an assertion about the line
        *count* rather than about the presence of a word: everything this
        command printed is either the brief or the new advisory.
        """
        with unittest.mock.patch.object(commands.dispatch, "instructions",
                                        lambda *a, **kw: "<<BRIEF>>"):
            code, out, err = self.cli_streams("start", self.SLUG)
        self.assertEqual(err, "", err)
        return code, out.splitlines()

    def test_exactly_one_line_is_added_and_it_names_the_page(self):
        code, lines = self.dispatch()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(lines[0], "<<BRIEF>>")
        self.assertEqual(lines[1], "preview: .ctx/plans/auth/preview.html")

    def test_a_missing_page_is_one_line_too(self):
        self.page.unlink()
        code, lines = self.dispatch()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("none yet", lines[1])
        self.assertIn("ctx preview auth", lines[1])

    def test_a_page_behind_the_plan_says_so_rather_than_pointing_at_it(self):
        """Staleness is measured against `plain.digest` — what the units
        promise — not against `plan.json`'s revision, which moves on every
        `plan-check` and would call every page stale the moment it was written.
        """
        self.write_unit("03-audit", deps=["01-login"])
        code, lines = self.dispatch()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("is behind the plan", lines[1])
        self.assertIn("ctx plan-check auth", lines[1])

    def test_a_broken_preview_still_leaves_exactly_one_line(self):
        def boom(*_a, **_kw):
            raise RuntimeError("nope")

        with unittest.mock.patch.object(plain_mod, "digest", boom):
            code, lines = self.dispatch()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("could not be checked", lines[1])

    def test_the_revision_counter_alone_does_not_make_a_page_stale(self):
        """The negative control for the paragraph above.

        `plan-check` is run again with the *page write suppressed*, so the
        revision moves and the file on disk does not. Measured by the revision
        this would read as stale; measured by what the units promise, it is
        exactly as current as it was.
        """
        current = self.page.read_bytes()
        with unittest.mock.patch.object(preview_page, "write",
                                        lambda *a, **kw: self.page):
            self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)
        self.assertEqual(self.page.read_bytes(), current, "the fixture rewrote it")
        _code, lines = self.dispatch()
        self.assertEqual(lines[1], "preview: .ctx/plans/auth/preview.html")


if __name__ == "__main__":
    unittest.main()
