"""Safety tests for `ctx/preview_html.py`.

The page these primitives build is committed, shared, and opened from
`file://` — no CSP, no server, no review step between a unit contract written
somewhere else and a browser executing it. These four functions are the entire
defence, so the assertions here are deliberately paranoid: every output is
parsed with a real HTML parser and required to contain *only* the handful of
element names this module generates, with no attributes at all.

Two properties get their own controls because this repository has already been
bitten by both:

* escaping runs *before* the markdown subset (swap the two and `` `<b>` ``
  either loses its `<code>` wrapper or renders real bold); and
* redaction runs *before* escaping and over prose only (run it afterwards and
  `redact.scrub`'s value class, which excludes `&`, matches nothing; run it
  unguarded and it eats `budget_tokens: 60000` and inverts `credential: none`).
"""

import ast
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import preview_html, redact  # noqa: E402


# The complete set of element names this module is allowed to emit. Anything
# else in a parsed output is markup that came from the input.
GENERATED = frozenset({"p", "ul", "ol", "li", "pre", "code", "strong", "em"})
_GENERATED_TAG = re.compile(r"</?(?:p|ul|ol|li|pre|code|strong|em)>")

MODULE = Path(__file__).resolve().parent.parent / "ctx" / "preview_html.py"


class _Collector(HTMLParser):
    """Records every construct a browser's parser would recognise."""

    def __init__(self):
        HTMLParser.__init__(self)
        self.tags = []
        self.attributes = []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_endtag(self, tag):
        self.tags.append(tag)

    def handle_data(self, data):
        self.chunks.append(data)

    def handle_comment(self, data):
        self.tags.append("!comment")

    def handle_decl(self, decl):
        self.tags.append("!decl")

    def unknown_decl(self, data):
        self.tags.append("!cdata")

    def handle_pi(self, data):
        self.tags.append("!pi")

    @property
    def text(self):
        return "".join(self.chunks)


def parse(fragment):
    collector = _Collector()
    collector.feed(fragment)
    collector.close()
    return collector


def visible(fragment):
    """What a reader actually sees: markup removed, entities resolved."""
    return parse(fragment).text


# --------------------------------------------------------------------------- #
# The hostile corpus
# --------------------------------------------------------------------------- #
#
# Twenty-six inputs, each of which has executed in somebody's browser. Every
# one is run through `markup` and required to produce nothing but this module's
# own tags.

LONG_TOKEN = "A" * 5000

HOSTILE = (
    ("script-and-img", "</script><img src=x onerror=alert(1)>"),
    ("nested-close-script", "</scr</script>ipt><script>alert(1)</script>"),
    ("html-comment", "<!-- <script>alert(1)</script> -->"),
    ("cdata", "<![CDATA[ <script>alert(1)</script> ]]>"),
    ("data-uri-link",
     "[report](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)"),
    ("javascript-href", "[click me](javascript:alert(1))"),
    ("svg-onload", "<svg/onload=alert(1)></svg>"),
    ("null-byte", "before\x00after <script>alert(1)</script>"),
    ("unpaired-surrogate", "lead \ud800 tail <b>bold</b>"),
    ("very-long-token", LONG_TOKEN + "<script>alert(1)</script>"),
    ("rtl-override", "invoice\u202egnp.exe <img src=x onerror=alert(1)>"),
    ("iframe-js", '<iframe src="javascript:alert(1)"></iframe>'),
    ("attribute-breakout", '"><script>alert(1)</script>'),
    ("entity-encoded-href", '<a href="&#106;avascript:alert(1)">x</a>'),
    ("style-url", '<style>body{background:url("javascript:alert(1)")}</style>'),
    ("mutation-xss",
     "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>"),
    ("code-span-html", "`<b>bold</b>`"),
    ("fence-html", "```\n<script>alert(1)</script>\n```"),
    ("list-html", "- <img src=x onerror=alert(1)>\n- <b>x</b>"),
    ("formaction", "<form><button formaction=javascript:alert(1)>go</button></form>"),
    ("mixed-case-script", "<ScRiPt>alert(1)</ScRiPt>"),
    ("textarea-escape", "<textarea></textarea><script>alert(1)</script>"),
    ("quote-handler", '" onmouseover="alert(1)'),
    ("base-tag", '<base href="//evil.example">'),
    ("line-separator", "line\u2028separator <object data=x>"),
    ("pre-escaped", "&lt;script&gt;alert(1)&lt;/script&gt;"),
)


class SafetyAssertions(unittest.TestCase):
    """Shared assertion: the output contains only tags this module wrote."""

    def assert_only_generated_markup(self, out, label=""):
        collector = parse(out)
        self.assertLessEqual(
            set(collector.tags), GENERATED,
            "%s: parser saw markup this module did not generate: %r"
            % (label, sorted(set(collector.tags) - GENERATED)),
        )
        self.assertEqual(
            collector.attributes, [],
            "%s: generated markup must carry no attributes at all" % label,
        )
        residue = _GENERATED_TAG.sub("", out)
        self.assertNotIn(
            "<", residue,
            "%s: unescaped '<' outside a generated tag: %r" % (label, residue[:200]),
        )
        lowered = out.lower()
        for forbidden in ("<script", "<img", "<a ", "<iframe", "<svg", "<style"):
            self.assertNotIn(forbidden, lowered, "%s: %r" % (label, forbidden))
        self.assertIsNone(
            re.search(r"<[^>]*\son\w+\s*=", out, re.I),
            "%s: an event-handler attribute reached tag context" % label,
        )


class TestEscape(SafetyAssertions):
    def test_escapes_exactly_the_five_characters(self):
        self.assertEqual(
            preview_html.escape("""&<>"'"""),
            "&amp;&lt;&gt;&quot;&#x27;",
        )

    def test_nothing_clever(self):
        # No stripping, no markdown, no unicode normalisation, no entity
        # re-interpretation: an already-escaped input is escaped again, so it
        # renders as the text it is.
        self.assertEqual(preview_html.escape("&lt;b&gt;"), "&amp;lt;b&amp;gt;")
        self.assertEqual(preview_html.escape("  naïve ✓  "), "  naïve ✓  ")
        self.assertEqual(preview_html.escape("**bold**"), "**bold**")
        self.assertEqual(preview_html.escape(""), "")
        self.assertEqual(preview_html.escape(None), "")

    def test_escaped_output_parses_as_text(self):
        out = preview_html.escape("</script><img src=x onerror=alert(1)>")
        self.assertEqual(parse(out).tags, [])
        self.assertEqual(visible(out), "</script><img src=x onerror=alert(1)>")


class TestMarkupInjection(SafetyAssertions):
    def test_script_and_img_payload(self):
        payload = "</script><img src=x onerror=alert(1)>"
        out = preview_html.markup(payload)
        self.assertNotIn("<script", out.lower())
        self.assertNotIn("<img", out.lower())
        # `onerror` survives as *visible text* — that is the point — but it
        # can never reach attribute position, because the only `<` in the
        # output are this module's own tags.
        self.assertIsNone(re.search(r"<[^>]*onerror", out, re.I))
        self.assertIn("onerror", visible(out))
        self.assertEqual(visible(out), payload)
        self.assert_only_generated_markup(out, "script-and-img")

    def test_link_position_schemes_are_inert(self):
        # There is no link syntax in SUBSET at all, so a URL in link position
        # is prose. The assertion is that it stays prose: no anchor element,
        # no `href` anywhere in tag context, and the scheme visible as text.
        for payload in (
            "[click me](javascript:alert(1))",
            "[report](data:text/html;base64,PHNjcmlwdD4=)",
            '<a href="javascript:alert(1)">x</a>',
            '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        ):
            with self.subTest(payload=payload):
                out = preview_html.markup(payload)
                self.assertNotIn("<a", out.lower())
                self.assertIsNone(re.search(r"<[^>]*href", out, re.I))
                self.assert_only_generated_markup(out, payload)
                self.assertEqual(visible(out), payload)

    def test_hostile_corpus_has_at_least_twenty_inputs(self):
        self.assertGreaterEqual(len(HOSTILE), 20)
        self.assertEqual(len(HOSTILE), len({name for name, _ in HOSTILE}))

    def test_hostile_corpus_produces_only_generated_markup(self):
        for label, payload in HOSTILE:
            with self.subTest(case=label):
                out = preview_html.markup(payload)
                self.assert_only_generated_markup(out, label)

    def test_control_characters_never_reach_the_page(self):
        out = preview_html.markup("before\x00after\x08 \x1b[31m red")
        for ch in ("\x00", "\x08", "\x1b"):
            self.assertNotIn(ch, out)
        self.assertIn("before", visible(out))
        self.assertIn("after", visible(out))

    def test_lone_surrogate_is_replaced_so_the_page_can_be_written(self):
        out = preview_html.markup("lead \ud800 tail")
        self.assertIsNone(re.search(r"[\ud800-\udfff]", out))
        out.encode("utf-8")  # would raise UnicodeEncodeError on a surrogate

    def test_bidi_overrides_are_stripped(self):
        out = preview_html.markup("invoice\u202egnp.exe and \u200f rtl mark")
        for ch in ("\u202e", "\u200f"):
            self.assertNotIn(ch, out)
        self.assertIn("invoice", visible(out))

    def test_very_long_token_survives_intact(self):
        out = preview_html.markup(LONG_TOKEN)
        self.assertIn(LONG_TOKEN, visible(out))
        self.assert_only_generated_markup(out, "long token")


class TestMarkupSubset(SafetyAssertions):
    def test_subset_constant(self):
        self.assertEqual(
            preview_html.SUBSET,
            ("paragraph", "list", "fence", "code", "bold", "italic"),
        )

    def test_every_member_of_the_subset_renders(self):
        rendered = {
            "paragraph": ("plain words", "<p>plain words</p>"),
            "list": ("- one\n- two", "<ul><li>one</li><li>two</li></ul>"),
            "fence": ("```\nx = 1\n```", "<pre><code>x = 1</code></pre>"),
            "code": ("a `token` b", "<p>a <code>token</code> b</p>"),
            "bold": ("a **strong** b", "<p>a <strong>strong</strong> b</p>"),
            "italic": ("a *slanted* b", "<p>a <em>slanted</em> b</p>"),
        }
        self.assertEqual(set(rendered), set(preview_html.SUBSET))
        for member, (source, expected) in rendered.items():
            with self.subTest(member=member):
                self.assertEqual(preview_html.markup(source), expected)

    def test_nothing_outside_the_subset_renders(self):
        for source, forbidden in (
            ("# Heading", "<h1"),
            ("## Heading", "<h2"),
            ("[link](https://example.com)", "<a"),
            ("![alt](https://example.com/x.png)", "<img"),
            ("> quoted", "<blockquote"),
            ("| a | b |\n| - | - |", "<table"),
            ("---", "<hr"),
            ("~~struck~~", "<del"),
            ("<b>x</b>", "<b>"),
            ("<div>x</div>", "<div"),
        ):
            with self.subTest(source=source):
                out = preview_html.markup(source)
                self.assertNotIn(forbidden, out.lower())
                self.assert_only_generated_markup(out, source)

    def test_raw_html_becomes_visible_text(self):
        out = preview_html.markup("<b>x</b>")
        self.assertEqual(out, "<p>&lt;b&gt;x&lt;/b&gt;</p>")
        self.assertEqual(visible(out), "<b>x</b>")

    def test_escaping_precedes_markup(self):
        # THE ordering control. `<b>` inside a code span:
        #   escape then subset  -> <p><code>&lt;b&gt;</code></p>   (correct)
        #   subset then escape  -> <p>&lt;code&gt;&lt;b&gt;...     (tags eaten)
        #   subset only         -> <p><code><b></code></p>         (real bold)
        # Each of the three assertions below fails under a swapped order.
        out = preview_html.markup("`<b>`")
        self.assertEqual(out, "<p><code>&lt;b&gt;</code></p>")
        self.assertIn("<code>&lt;b&gt;</code>", out)
        self.assertNotIn("&lt;code&gt;", out)
        self.assertNotIn("<b>", out)
        self.assertEqual(visible(out), "<b>")

    def test_markup_inside_a_code_span_is_not_applied(self):
        self.assertEqual(
            preview_html.markup("`**not bold**`"),
            "<p><code>**not bold**</code></p>",
        )

    def test_fence_content_is_literal(self):
        out = preview_html.markup("```\n**x** `y` <b>z</b>\n```")
        self.assertEqual(out, "<pre><code>**x** `y` &lt;b&gt;z&lt;/b&gt;</code></pre>")
        self.assertEqual(visible(out), "**x** `y` <b>z</b>")

    def test_underscored_identifiers_are_not_italicised(self):
        # `budget_tokens and depends_on` must not become one italic run: the
        # ledger's own vocabulary is full of snake_case.
        out = preview_html.markup("budget_tokens and depends_on are fields")
        self.assertNotIn("<em>", out)
        self.assertIn("budget_tokens and depends_on", visible(out))

    def test_blocks_and_paragraphs_separate(self):
        out = preview_html.markup("one\n\ntwo\n\n- a\n- b\n\nthree")
        self.assertEqual(
            out,
            "<p>one</p>\n<p>two</p>\n<ul><li>a</li><li>b</li></ul>\n<p>three</p>",
        )

    def test_ordered_lists_are_lists(self):
        self.assertEqual(
            preview_html.markup("1. first\n2. second"),
            "<ol><li>first</li><li>second</li></ol>",
        )

    def test_empty_input(self):
        self.assertEqual(preview_html.markup(""), "")
        self.assertEqual(preview_html.markup(None), "")
        self.assertEqual(preview_html.markup("   \n\n  "), "")

    def test_markup_is_deterministic(self):
        source = "a **b** `c`\n\n- x\n- y\n\n```\nz\n```"
        self.assertEqual(preview_html.markup(source), preview_html.markup(source))


class TestEmbedJson(SafetyAssertions):
    payload = {
        "closing": "</script>",
        "shouting": "</SCRIPT foo",
        "comment_open": "<!--",
        "comment_close": "-->",
        "surrogate": "lead \ud800 tail",
        "amp": "a & b",
        "sep": "line\u2028sep\u2029end",
        "nested": {"z": 1, "a": [1, 2, {"k": "</script>"}]},
    }

    def test_survives_inside_a_script_element(self):
        out = preview_html.embed_json(self.payload)
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        self.assertNotIn("&", out)
        self.assertNotIn("</script", out.lower())
        page = '<script type="application/json">%s</script>' % out
        collector = parse(page)
        self.assertEqual([t for t in collector.tags if t == "script"], ["script", "script"])
        self.assertEqual(json.loads(collector.text), self.payload)
        self.assertEqual(json.loads(out), self.payload)

    def test_output_is_pure_ascii(self):
        out = preview_html.embed_json(self.payload)
        out.encode("ascii")

    def test_deterministic_and_sorted(self):
        first = preview_html.embed_json(self.payload)
        second = preview_html.embed_json(self.payload)
        self.assertEqual(first, second)
        reordered = {"b": 2, "a": 1}
        other = {"a": 1, "b": 2}
        self.assertEqual(preview_html.embed_json(reordered),
                         preview_html.embed_json(other))
        self.assertEqual(preview_html.embed_json(other), '{"a":1,"b":2}')

    def test_nan_is_refused_rather_than_written(self):
        with self.assertRaises(ValueError):
            preview_html.embed_json({"x": float("nan")})

    def test_does_not_scrub_structured_values(self):
        # Criterion 9: scrubbing belongs on the prose path, in `markup`.
        # Double-scrubbing structured data is how `budget_tokens` got eaten.
        data = {"budget_tokens": 60000, "credential": "none",
                "path": "ctx/preview_html.py"}
        self.assertEqual(json.loads(preview_html.embed_json(data)), data)
        self.assertNotIn(redact.PLACEHOLDER, preview_html.embed_json(data))


class TestRedaction(SafetyAssertions):
    def test_secrets_are_removed_from_prose(self):
        for source, leaked in (
            ("deploy key AKIAIOSFODNN7EXAMPLE is rotated", "AKIAIOSFODNN7EXAMPLE"),
            ("we connect to postgres://user:hunter2@host/db", "hunter2"),
        ):
            with self.subTest(source=source):
                out = preview_html.markup(source)
                self.assertNotIn(leaked, out)
                self.assertIn(redact.PLACEHOLDER, visible(out))

    def test_redaction_precedes_escaping(self):
        # Proven two ways. First, the placeholder is itself escaped, which can
        # only happen if scrubbing ran first.
        out = preview_html.markup("key AKIAIOSFODNN7EXAMPLE")
        self.assertIn("&lt;&lt;redacted&gt;&gt;", out)
        self.assertNotIn("<<redacted>>", out)
        self.assertEqual(visible(out), "key <<redacted>>")
        # Second, the shape `redact.scrub` *cannot* see once escaped: its value
        # class excludes `&`, so scrubbing escaped text matches nothing.
        source = 'config was {"password": "hunter2correct"}'
        escaped_first = redact.scrub(preview_html.escape(source))
        self.assertNotIn(redact.PLACEHOLDER, escaped_first)  # the bug
        self.assertIn(redact.PLACEHOLDER, visible(preview_html.markup(source)))

    def test_positive_control_ordinary_file_path(self):
        source = "the change lands in ctx/preview_html.py and its test"
        self.assertEqual(visible(preview_html.markup(source)), source)

    def test_positive_control_budget_tokens_survives(self):
        for source in ("budget_tokens: 60000",
                       "each unit gets budget_tokens: 60000 to work with"):
            with self.subTest(source=source):
                self.assertEqual(visible(preview_html.markup(source)), source)
                self.assertNotIn(redact.PLACEHOLDER, preview_html.markup(source))

    def test_positive_control_credential_none_is_not_inverted(self):
        for source in ("credential: none",
                       "credential: none needed for this step",
                       "auth: none", "secret: n/a", "api_key: unset"):
            with self.subTest(source=source):
                out = preview_html.markup(source)
                self.assertEqual(visible(out), source)
                self.assertNotIn(redact.PLACEHOLDER, out)

    def test_the_incident_is_real_not_hypothetical(self):
        # Unguarded, `redact.scrub` eats both — which is why `markup` protects
        # numeric and negated values. If this control ever goes green,
        # `redact.scrub` has been fixed and `markup`'s guard can be revisited.
        self.assertIn(redact.PLACEHOLDER, redact.scrub("budget_tokens: 60000"))
        self.assertIn(redact.PLACEHOLDER, redact.scrub("credential: none"))

    def test_guard_does_not_protect_real_credentials(self):
        for source in ("password: hunter2correct",
                       "api_key: sk-abcdefghijklmnopqrstuvwx",
                       "token: ghp_abcdefghijklmnopqrstuvwxyz012345",
                       "Authorization: Bearer abcdefghijklmnop"):
            with self.subTest(source=source):
                self.assertIn(redact.PLACEHOLDER, visible(preview_html.markup(source)))

    def test_house_patterns_are_honoured(self):
        out = preview_html.markup("codename ZEPHYRSTORM ships friday",
                                  extra_patterns=[r"ZEPHYR[A-Z]+"])
        self.assertNotIn("ZEPHYRSTORM", out)
        self.assertIn(redact.PLACEHOLDER, visible(out))

    def test_a_bad_house_pattern_does_not_break_the_page(self):
        out = preview_html.markup("ordinary prose", extra_patterns=["("])
        self.assertEqual(visible(out), "ordinary prose")


class TestAttr(SafetyAssertions):
    def test_breaks_out_of_nothing(self):
        hostile = 'x" onmouseover="alert(1)'
        value = preview_html.attr(hostile)
        self.assertNotIn('"', value)
        collector = parse('<div class="%s">t</div>' % value)
        self.assertEqual(collector.attributes, [("class", hostile)])
        self.assertEqual(collector.tags, ["div", "div"])

    def test_single_quote_context(self):
        value = preview_html.attr("x' onmouseover='alert(1)")
        self.assertNotIn("'", value)

    def test_angle_brackets_and_backticks(self):
        value = preview_html.attr("<script>`x`")
        self.assertNotIn("<", value)
        self.assertNotIn("`", value)

    def test_control_characters_and_newlines(self):
        value = preview_html.attr("a\nb\tc\x00d")
        for ch in ("\n", "\x00"):
            self.assertNotIn(ch, value)

    def test_non_strings(self):
        self.assertEqual(preview_html.attr(None), "")
        self.assertEqual(preview_html.attr(3), "3")
        self.assertEqual(preview_html.attr(True), "True")


class TestProjectRules(unittest.TestCase):
    def test_standard_library_only(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        absolute, relative = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                absolute.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    relative.update(alias.name for alias in node.names)
                else:
                    absolute.add((node.module or "").split(".")[0])
        self.assertLessEqual(absolute, {"html", "json", "re"}, sorted(absolute))
        self.assertLessEqual(relative, {"redact"}, sorted(relative))

    def _names_used(self):
        """Every bare name and attribute root the module actually references.

        Checked by parsing rather than by substring: a prose word in a comment
        is not a clock, and a test that cannot tell the two apart forces the
        documentation to be written around it.
        """
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    used.add(root.id + "." + node.attr)
                    used.add(root.id)
        return used

    def test_no_clock_and_no_randomness(self):
        # The page is committed, so anything varying between two runs on the
        # same input is a diff on somebody's branch.
        used = self._names_used()
        for banned in ("datetime", "random", "time", "uuid", "hash", "id",
                       "object", "repr"):
            self.assertNotIn(banned, used, banned)

    def test_module_has_no_io(self):
        used = self._names_used()
        for banned in ("open", "print", "input", "Path", "os", "io",
                       "subprocess", "sys"):
            self.assertNotIn(banned, used, banned)

    def test_python_38_compatible_syntax(self):
        # `ast.parse` with the feature_version floor rejects syntax newer than
        # the supported interpreter, e.g. a match statement or `x: int = 1`
        # patterns only 3.9+ accepts.
        ast.parse(MODULE.read_text(encoding="utf-8"), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
