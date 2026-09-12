"""`ctx.preview_page` — the page a non-technical person opens and signs.

The thing under test is not a string of HTML. It is whether somebody who does
not read `plan.json`, has no account anywhere, and may not be technical can
open one file with the wifi off and learn what is about to happen, why, what
will be different afterwards, which steps run together, what could go wrong and
what is deliberately not being done.

Most of that is only provable by looking, and a subagent cannot look — so the
unit ends with a rendered sample and a human gate. What *is* provable here:

* **Self-contained.** No URL, no `<link>`, no `<script src=`, no `fetch`. One
  file, opened from `file://`, with nothing to fetch.
* **In the reader's language.** Nine sections under headings they recognise,
  and the contract's vocabulary — `owns`, `forbid`, `wave`, `tier`,
  `budget_tokens`, `.py` paths — confined to the regions marked technical. That
  assertion is made over the *extracted* non-technical regions, because
  searching the whole document for a word that is legitimately in it would pass
  no matter what the page said.
* **Degraded, never broken.** No `plain.md` produces a page with a visible
  banner and every step still described, not a blank page or a traceback.
* **Static.** The technical toggle is a checkbox and a CSS rule. A page that
  needs JavaScript to have content is a page that is blank for a reader who
  turned it off, and that is a failure however good it looks here.
* **A calibrated checker.** `check()` names five kinds of loss, each proved by
  breaking a good page in exactly that way; and every one of those tests has a
  positive control showing `[]` once the defect is removed — including the
  renderer's own output, which is what stops the checker being vacuous.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (atomic, frontmatter, plain as plain_mod,  # noqa: E402
                 plan as plan_mod, preview, preview_html, preview_page)
from support import OK, Fixture  # noqa: E402


SOURCE = Path(__file__).resolve().parent.parent / "ctx" / "preview_page.py"

BODY = (
    "## Objective\nDo the thing.\n\n"
    "## Acceptance criteria\n"
    "1. the first one\n2. the `second` one\n3. the third one\n"
)

# `plain.py`'s own banned list: the contract's vocabulary, which the reader of
# the default view does not have.
BANNED = ("owns", "forbid", "depends_on", "wave", "tier", "budget_tokens",
          "subagent")

# Exactly the nine sections the contract names, in the order it names them,
# spelled as the page spells them. Deliberately typed out here rather than
# imported from the module: importing them would make a rename invisible, and
# the wording *is* the requirement.
HEADINGS = (
    "What we're going to do",
    "Why we're doing it",
    "What will be different afterwards",
    "How the work is split up",
    "Each step, in detail",
    "What could go wrong",
    "What we are not doing",
    "How we'll check it worked",
)


class PageCase(Fixture):
    """A four-step plan over three rounds, checked, with `plan.json` on disk.

        round 1  01-alpha  02-beta     <- two steps at the same time
        round 2  03-gamma              <- waits for 01-alpha
        round 3  04-delta              <- waits for 03-gamma

    `src/shared.py` is declared by alpha, gamma and delta, so the page has a
    contended file to talk about, and `02-beta` states no budget, so the
    totals are a sum rather than a multiplication.
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
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        # `plan-check` scaffolds `plain.md` itself now, so "nobody has written
        # any of this" — the state the degradation cases below are about — has
        # to be asked for rather than assumed. `write_plain` opts back in.
        plain_mod.path(self.layout, self.SLUG).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def write_unit(self, name, deps=(), owns=(), budget=1000, body=BODY):
        meta = {"ctx_schema": 1, "unit": name, "plan": self.SLUG,
                "tier": "subagent", "depends_on": list(deps),
                "owns": list(owns), "reads": ["docs/notes.md"], "forbid": [],
                "status": "pending",
                "verify": [{"kind": "cmd", "run": OK}, {"kind": "review"}]}
        if budget is not None:
            meta["budget_tokens"] = budget
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("%s.md" % name)
        frontmatter.Document(meta, body).write(path)
        return path

    def write_plain(self, body):
        meta = {"ctx_schema": 1, "plan": self.SLUG,
                "digest": plain_mod.digest(self.layout, self.SLUG)}
        path = plain_mod.path(self.layout, self.SLUG)
        atomic.write_text(path, frontmatter.Document(meta, body).render())
        return path

    def model(self):
        return preview.view_model(self.layout, self.SLUG)

    def page(self, model=None):
        return preview_page.render(model or self.model())

    def style(self, html=None):
        """Everything between `<style>` and `</style>`."""
        html = html if html is not None else self.page()
        return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))

    def block(self, css, opener):
        """The body of the first `@media ...` block whose header matches."""
        start = css.index(opener)
        cursor = css.index("{", start)
        depth, end = 0, None
        for index in range(cursor, len(css)):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if not depth:
                    end = index
                    break
        self.assertIsNotNone(end, "unclosed %r block" % opener)
        return css[cursor + 1:end]


# --------------------------------------------------------------------------- #
# 1-2 — self-contained
# --------------------------------------------------------------------------- #

class TestItIsSelfContained(PageCase):
    """Opened from a thumb drive with the wifi off, it is still the page."""

    def test_it_refers_to_nothing_outside_itself(self):
        html = self.page()
        for needle in ("http://", "https://", "//cdn", "<link", "fetch(",
                       "XMLHttpRequest", "<script src", "<iframe", "@import",
                       "url("):
            self.assertNotIn(needle, html, needle)

    def test_the_styles_are_inline_and_the_model_is_embedded(self):
        model = self.model()
        html = self.page(model)
        self.assertIn("<style>", html)
        self.assertIn('<script type="application/json"', html)
        # The embedded model is `embed_json`'s bytes exactly: a second
        # serialiser here would be a second answer to "what does the page
        # think the plan is".
        self.assertIn(preview_html.embed_json(model), html)

    def test_the_only_script_element_carries_data_rather_than_code(self):
        html = self.page()
        self.assertEqual(html.count("<script"), 1)
        self.assertIn('<script type="application/json"', html)
        for needle in ("document.", "addEventListener", "innerHTML",
                       "onclick", "javascript:"):
            self.assertNotIn(needle, html, needle)


# --------------------------------------------------------------------------- #
# 3-7 — what it says, and in what language
# --------------------------------------------------------------------------- #

class TestWhatItSays(PageCase):

    def test_the_nine_sections_appear_in_the_order_the_contract_names(self):
        html = self.page()
        # The plan's name in words is the first of the nine.
        title = html.index("<h1")
        positions = [title]
        for heading in HEADINGS:
            # The headings are printed through the escaper like everything
            # else, so `we're` reaches the file as `we&#x27;re` and reaches
            # the reader as `we're`. The wording is the requirement; the
            # entity is how it is spelled on disk.
            needle = preview_html.escape(heading)
            self.assertIn(needle, html, heading)
            positions.append(html.index(needle))
        self.assertEqual(positions, sorted(positions),
                         "the nine sections are out of order")

    def test_the_default_view_speaks_no_contract_vocabulary(self):
        """Asserted over the extracted non-technical regions only.

        The words are legitimately *in* the document — the technical view is
        made of them — so searching the whole thing would pass whatever the
        page said to the reader.
        """
        html = self.page()
        regions = preview_page.regions(html)
        plain = regions.plain_text.lower()

        # Calibration: the split is real in both directions. A parser that
        # returned nothing would pass the assertions below trivially.
        self.assertIn("each step, in detail", plain)
        self.assertIn("alpha", plain)
        self.assertGreater(len(plain), 1000)
        self.assertIn("owns", regions.tech_text.lower())
        self.assertIn("src/shared.py", regions.tech_text)

        for word in BANNED:
            self.assertNotIn(word, plain, word)
        self.assertNotIn(".py", plain)

    def test_steps_are_numbered_from_one_and_slugs_stay_technical(self):
        html = self.page()
        plain = preview_page.regions(html).plain_text
        self.assertIn("Step 1", plain)
        self.assertIn("Step 4", plain)
        self.assertNotIn("Step 0", plain)
        for slug in ("01-alpha", "02-beta", "03-gamma", "04-delta"):
            self.assertNotIn(slug, plain, slug)
            self.assertIn(slug, preview_page.regions(html).tech_text, slug)

    def test_concurrency_is_stated_in_words_not_in_rounds(self):
        plain = preview_page.regions(self.page()).plain_text.lower()
        # 01-alpha runs beside 02-beta; 03-gamma waits for 01-alpha.
        self.assertIn("at the same time as step 2", plain)
        self.assertIn("at the same time as step 1", plain)
        self.assertIn("waits for step 1", plain)
        self.assertIn("waits for step 3", plain)
        self.assertNotIn("wave", plain)

    def test_a_lone_step_says_so_rather_than_saying_nothing(self):
        plain = preview_page.regions(self.page()).plain_text.lower()
        self.assertIn("nothing else runs at the same time", plain)

    def test_a_glossary_covers_the_terms_that_cannot_be_avoided(self):
        plain = preview_page.regions(self.page()).plain_text.lower()
        self.assertIn("what the words mean", plain)
        for term in ("step", "round", "token", "technical detail"):
            self.assertIn(term, plain, term)

    def test_the_totals_are_stated_in_the_header(self):
        plain = preview_page.regions(self.page()).plain_text
        self.assertIn("4 steps", plain)
        self.assertIn("3 rounds", plain)
        self.assertIn("7,000", plain)

    def test_authored_prose_is_shown_where_a_human_wrote_it(self):
        self.write_plain(
            "## Summary\nWe are closing four gaps.\n\n"
            "## Why now\nBecause the next release depends on it.\n\n"
            "## What changes for you\nNothing you can see, yet.\n\n"
            "## What could go wrong\nThe estimate could be wrong.\n\n"
            "## Out of scope\nAnything to do with billing.\n\n"
            "## Unit: 01-alpha\n**What it does:** It makes the shared part safe.\n"
        )
        html = self.page()
        plain = preview_page.regions(html).plain_text
        for sentence in ("We are closing four gaps.",
                         "Because the next release depends on it.",
                         "Nothing you can see, yet.",
                         "The estimate could be wrong.",
                         "Anything to do with billing.",
                         "It makes the shared part safe."):
            self.assertIn(sentence, plain, sentence)

    def test_a_section_the_model_grew_is_rendered_rather_than_dropped(self):
        """A sixth plan section added upstream must reach the page.

        The page names the five it knows. A model carrying a sixth is a
        `plain.py` that grew a section, and silently dropping it is the one
        failure the reader cannot see. `check()` is what would catch it, so
        the renderer has to have somewhere to put it.
        """
        model = self.model()
        model["plain"]["present"] = True
        model["plain"]["sections"]["how we will pay for it"] = "<p>Out of savings.</p>"
        html = preview_page.render(model)
        self.assertIn("<p>Out of savings.</p>", html)
        self.assertIn("How we will pay for it", html)
        self.assertEqual(preview_page.check(html, model), [])


# --------------------------------------------------------------------------- #
# 8-13 — how it behaves
# --------------------------------------------------------------------------- #

class TestHowItBehaves(PageCase):

    def test_the_toggle_is_a_checkbox_and_a_css_rule(self):
        html = self.page()
        css = self.style(html)
        self.assertIn('<input type="checkbox" id="tech-toggle"', html)
        self.assertIn('<label class="toggle" for="tech-toggle"', html)
        self.assertIn("#tech-toggle:checked", css)

    def test_the_content_is_static_html_not_built_by_script(self):
        """With JavaScript off the page must still be the page.

        Everything the reader needs is in the markup that arrives; the toggle
        only reveals what is already there.
        """
        model = self.model()
        html = preview_page.render(model)
        for step in model["steps"]:
            self.assertIn(step["title"], html)
            self.assertIn(step["plain"]["what"], html)
            self.assertIn(step["tech"]["owns"][0], html)
        self.assertEqual(html.count("<script"), 1)

    def test_both_themes_set_a_background_and_a_foreground(self):
        """A transparent body borrows the host's theme: black on black."""
        css = self.style()
        body = css[css.index("body {"):css.index("}", css.index("body {"))]
        self.assertIn("background:", body)
        self.assertIn("color:", body)

        dark = self.block(css, "@media (prefers-color-scheme: dark)")
        self.assertIn("background:", dark)
        self.assertIn("color:", dark)

    def test_printing_expands_the_technical_view_and_drops_the_chrome(self):
        css = self.style()
        printed = self.block(css, "@media print")
        self.assertIn(".tech", printed)
        self.assertIn("display: block !important", printed)
        self.assertIn(".toggle", printed)
        self.assertIn("display: none !important", printed)
        # On paper there is nothing to scroll, so the diagram has to fit the
        # sheet rather than being cut off at its edge.
        self.assertIn("max-width: 100%", printed)
        self.assertIn("overflow: visible", printed)

    def test_only_the_tables_and_the_diagram_scroll_sideways(self):
        html = self.page()
        css = self.style(html)
        # One rule, one class, and every table and diagram inside it.
        self.assertEqual(css.count("overflow-x"), 1)
        self.assertIn(".scroll {", css)
        wrapped = re.findall(r'<div class="scroll">\s*<(table|svg)', html)
        self.assertEqual(len(wrapped), html.count("<table") + html.count("<svg"))
        self.assertGreater(len(wrapped), 1)
        # Long unbroken strings wrap rather than widening the page.
        self.assertIn("white-space: pre-wrap", css)
        self.assertIn("overflow-wrap: break-word", css)

    def test_it_is_laid_out_for_a_narrow_screen(self):
        html = self.page()
        css = self.style(html)
        self.assertIn('name="viewport"', html)
        self.assertIn("width=device-width", html)
        body = css[css.index("body {"):css.index("}", css.index("body {"))]
        self.assertIn("max-width:", body)
        # The diagram keeps its own size and its container scrolls, rather
        # than the diagram shrinking until its numbers are unreadable.
        self.assertRegex(html, r'<svg viewBox="0 0 \d+ \d+" width="\d+" height="\d+"')

    def test_rendering_the_same_model_twice_is_byte_identical(self):
        model = self.model()
        self.assertEqual(preview_page.render(model), preview_page.render(model))
        self.assertEqual(preview_page.render(self.model()), self.page())

    def test_the_module_cannot_read_a_clock(self):
        """The page is committed. Bytes that move because a day passed are a
        spurious diff on every reviewer's branch."""
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("import time", "import datetime", "datetime.now",
                          "time.time", "uuid.", "random.", "getpid"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_a_missing_plain_summary_degrades_visibly(self):
        model = self.model()
        self.assertFalse(model["plain"]["present"])
        html = preview_page.render(model)
        plain = preview_page.regions(html).plain_text

        self.assertIn("banner", html)
        self.assertIn("has not been written yet", plain)
        # Every step still says what it does, from generated text.
        for step in model["steps"]:
            self.assertIn(step["plain"]["what"], html)
            self.assertIn(step["plain"]["how_we_know"], html)
        self.assertEqual(preview_page.check(html, model), [])

    def test_a_stale_summary_says_so(self):
        self.write_plain("## Summary\nWe are closing four gaps.\n")
        self.write_unit("05-epsilon", [], ["src/e.py"], 500)
        code, out = self.cli("plan-check", self.SLUG)
        self.assertEqual(code, 0, out)
        model = self.model()
        self.assertTrue(model["plain"]["stale"])
        plain = preview_page.regions(preview_page.render(model)).plain_text
        self.assertIn("has changed since", plain)

    def test_a_plan_with_no_steps_still_renders(self):
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        for name, _deps, _owns, _budget in self.UNITS:
            (directory / ("%s.md" % name)).unlink()
        # `plan-check` refuses a plan with no units, so the graph on disk is
        # the one from setUp. That is the real shape of this failure: a plan
        # whose units were deleted or renamed under a graph that still
        # remembers them.
        model = self.model()
        self.assertEqual(model["steps"], [])
        html = preview_page.render(model)
        self.assertIn("no steps", preview_page.regions(html).plain_text)
        self.assertEqual(preview_page.check(html, model), [])


# --------------------------------------------------------------------------- #
# 14-16 — the checker, and its calibration
# --------------------------------------------------------------------------- #

class TestTheChecker(PageCase):
    """Five kinds of loss, each proved by causing exactly that one.

    Every test here is a rejection *and* its positive control: the same page,
    with the defect removed, returns `[]`. A checker that never returns `[]`
    is as useless as one that never returns a problem, and only the pair
    proves which of the two this is.
    """

    def setUp(self):
        super().setUp()
        self.vm = self.model()
        self.html = preview_page.render(self.vm)

    def one(self, problems, needle):
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(needle, problems[0])

    def test_the_renderers_own_output_passes_its_own_checker(self):
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_a_missing_step_name_is_reported(self):
        broken = self.html.replace("03-gamma", "")
        self.one(preview_page.check(broken, self.vm), "03-gamma")
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_a_missing_owned_path_is_reported(self):
        broken = self.html.replace("src/a.py", "")
        self.one(preview_page.check(broken, self.vm), "src/a.py")
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_an_owned_path_that_only_reaches_the_plain_view_is_reported(self):
        """Present on the page, absent from the technical regions.

        The rule is not "the string is somewhere in the file" — a path that
        landed in the reader's view instead of the engineer's is both a leak
        and a loss, and has to be reported as a loss too.
        """
        broken = self.html.replace('<td class="path">src/b.py</td>', "")
        broken = re.sub(r'<li class="owns">src/b\.py</li>', "", broken)
        broken = broken.replace("</h1>", "</h1><p>src/b.py</p>")
        self.one(preview_page.check(broken, self.vm), "src/b.py")
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_a_criteria_count_that_disagrees_with_the_model_is_reported(self):
        self.vm["steps"][0]["tech"]["criteria"].append("<p>a fourth one</p>")
        self.one(preview_page.check(self.html, self.vm), "acceptance criteria")
        self.vm["steps"][0]["tech"]["criteria"].pop()
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_a_plain_entry_the_page_lacks_is_reported(self):
        missing = self.vm["steps"][1]["plain"]["how_we_know"]
        broken = self.html.replace(missing, "")
        self.assertTrue(preview_page.check(broken, self.vm))
        self.assertIn("how_we_know", preview_page.check(broken, self.vm)[0])
        self.assertEqual(preview_page.check(self.html, self.vm), [])

    def test_a_missing_plan_section_is_reported(self):
        self.write_plain("## Summary\nWe are closing four gaps.\n")
        vm = self.model()
        html = preview_page.render(vm)
        self.assertEqual(preview_page.check(html, vm), [])
        broken = html.replace("<p>We are closing four gaps.</p>", "")
        self.one(preview_page.check(broken, vm), "summary")

    def test_a_url_a_contract_merely_quotes_is_not_an_external_reference(self):
        """Prose about a URL is not a reference to one.

        This very unit's contract says the page may contain no `https://`.
        Rendered, that sentence puts the characters on the page — and a
        checker that reported it would fail every plan that talks about the
        web, which is how a checker ends up switched off. The rule is about
        attributes and executable bodies, which is where a fetch can come
        from; the control below proves it still catches one.
        """
        self.vm["steps"][0]["tech"]["criteria"].append(
            "<p>No <code>https://cdn.example/x.js</code>, no "
            "<code>fetch(</code>, no <code>XMLHttpRequest</code>.</p>")
        html = preview_page.render(self.vm)
        self.assertIn("https://cdn.example/x.js", html)
        self.assertEqual(preview_page.check(html, self.vm), [])

        borrowed = html.replace(
            "</h1>", '</h1><img src="https://cdn.example/x.png">')
        self.assertTrue(any("outside" in problem
                            for problem in preview_page.check(borrowed, self.vm)))

    def test_an_external_reference_is_reported(self):
        broken = self.html.replace(
            "</h1>", '</h1><script src="https://cdn.example/x.js"></script>')
        problems = preview_page.check(broken, self.vm)
        self.assertTrue(problems)
        self.assertTrue(any("outside" in problem for problem in problems),
                        problems)
        self.assertEqual(preview_page.check(self.html, self.vm), [])


# --------------------------------------------------------------------------- #
# writing, and the project's rules
# --------------------------------------------------------------------------- #

class TestWriting(PageCase):

    def test_write_puts_the_page_beside_the_plan(self):
        path = preview_page.write(self.layout, self.SLUG)
        self.assertEqual(path, preview.html_path(self.layout, self.SLUG))
        self.assertEqual(path.read_text(encoding="utf-8"),
                         preview_page.render(self.model()))

    def test_writing_twice_leaves_the_same_bytes(self):
        first = preview_page.write(self.layout, self.SLUG).read_bytes()
        second = preview_page.write(self.layout, self.SLUG).read_bytes()
        self.assertEqual(first, second)

    def test_it_writes_through_the_atomic_primitive(self):
        """A committed artefact truncated by a `Path.write_text` that died
        half way through is a torn file in somebody's diff."""
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("atomic.write_text(", text)
        self.assertNotIn(".write_text(", text.replace("atomic.write_text(", ""))


class TestProjectRules(unittest.TestCase):

    def test_it_imports_only_the_standard_library_and_this_package(self):
        allowed = {"html", "re"}
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename="preview_page.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                self.assertIn((node.module or "").split(".")[0], allowed,
                              node.module)

    def test_it_escapes_through_the_safety_module_rather_than_by_hand(self):
        """Nothing here may reach for the stdlib's escaper directly.

        `preview_html` is where the escaping rules live — escape-then-mark-up,
        the backtick in attribute context, the redaction guards. A second copy
        of any of that would drift from the first, silently.
        """
        text = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(text, filename="preview_page.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotEqual((node.value.id, node.attr), ("html", "escape"))
        self.assertIn("preview_html.escape", text)


if __name__ == "__main__":
    unittest.main()
