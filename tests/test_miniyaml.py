"""miniyaml — the serialiser under every durable ledger file.

The load-bearing assertion here is `loads(dumps(x)) == x` over the shapes the
ledger actually stores, because miniyaml is not a convenience: `ctx.yaml`, task
files, unit files, specs, ADRs and bundles are all written and re-read through
it, and a value that changes type on a rewrite changes what the gate decides.

Two of the round-trips below are safety checks rather than data checks, and they
are duplicated as end-to-end tests against `ctx.verify` and `ctx.frontmatter`
further down. Both defects turned a red gate green rather than breaking loudly:
a `contains` list that came back as a string matched character by character
against real source, and a multi-line value made the whole frontmatter block
unparseable, which `frontmatter.parse` swallows into an empty meta dict.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, miniyaml, verify  # noqa: E402


# Every entry is a document `dumps` must be able to write and `loads` must read
# back identically. Grouped by the defect or the behaviour it stands for, so a
# failure names the thing that regressed.
CORPUS = {
    # A collection nested inside a list item — the shape of every verify check.
    "check with an owns list": {"verify": [{"kind": "diff", "owns": ["src/", "docs/"]}]},
    "collection under the first key": {"verify": [{"owns": ["src/"], "kind": "diff"}]},
    "check with an env map": {
        "verify": [{"kind": "cmd", "run": "pytest -q", "env": {"CI": "1", "TZ": "UTC"}}]
    },
    "interface freeze": {
        "verify": [{"kind": "symbol", "path": "src/api.py",
                    "contains": ["def rotate(key)", "class Session"]}]
    },
    "reads as path and symbols": {
        "reads": [{"path": "src/api.py", "symbols": ["rotate"]}, "docs/api.md"]
    },
    "several checks at once": {
        "verify": [
            {"kind": "diff", "owns": ["ctx/"]},
            {"kind": "cmd", "run": "python -m unittest", "env": {"PYTHONHASHSEED": "0"}},
            {"kind": "rubric", "about": "the note reads as prose"},
        ]
    },
    # Multi-line strings.
    "multi-line note": {"note": "line1\nline2\nline3"},
    "crlf and tab inside a value": {"note": "head\r\nbody\tcell"},
    "lone carriage return": {"note": "before\rafter"},
    # `str.splitlines` breaks on more than \n and \r, so these split a value the
    # same way a newline did while looking nothing like one in the file.
    "exotic line breaks": {"note": "a\vb\fc\x1cd\x85e f g"},
    "key ending in a newline": {"k\n": 1},
    # Keys outside the bare-key pattern.
    "key with a space": {"a b": 1},
    "key with a colon": {"a:b": "v"},
    "empty key": {"": "v"},
    "key that needs escaping": {'say "hi"': "v"},
    # Strings a reader would otherwise coerce.
    "coercible strings": {
        "zero_padded": "007", "grouped": "1_000", "nan": "nan",
        "infinity": "Infinity", "boolean": "true", "yes": "yes", "tilde": "~",
        "nullish": "null", "brackets": "[]", "braces": "{}", "hex": "0x1f",
    },
    # Empty collections.
    "empty map": {"env": {}},
    "empty list": {"owns": []},
    "empty item in a list": {"owns": [{}]},
    "empty everything": {"a": {}, "b": [], "c": "", "d": None},
    # A list item that merely contains ": ".
    "redact patterns": {"redact": ["password: .*", "token: \\S+", "Bearer .*"]},
    # Real ctx.yaml.
    "config defaults": {
        "schema": 1, "profile": "code", "level": "0",
        "briefing_chars": {"l0": 220, "l1": 900, "l2": 2600},
        "journal": {"digest_lines": 12, "max_line_chars": 200, "enabled": True,
                    "keep_days": 0},
        "gate": {"enabled": True, "max_attempts": 3, "timeout_seconds": 240},
        "models": {"runner": "sonnet", "reviewer": "opus"},
        "auto_load": [], "redact": [], "verify_candidates": [], "verify": [],
    },
    # Real unit frontmatter.
    "unit frontmatter": {
        "ctx_schema": 1, "unit": "01-keys", "plan": "auth-rotation",
        "tier": "subagent", "depends_on": [], "owns": ["src/keys.py"],
        "reads": ["src/clock.py"], "forbid": ["src/wire.py"],
        "budget_tokens": 45000, "status": "pending", "verified": ["rubric"],
        "verify": [{"kind": "cmd", "run": "pytest -q tests/test_keys.py"}],
    },
    # Behaviour that already worked and must keep working.
    "quotes both ways": {"k": 'say "hi" now', "j": "it's fine"},
    "backslashes": {"k": "back\\slash", "j": 'both "\\" kinds', "i": "trailing\\"},
    "hash inside a string": {"k": "colour #ff00aa", "j": "a # not a comment"},
    "unicode and emoji": {"k": "ünïcode ✨ — em-dash", "j": "日本語"},
    "numbers": {"i": -3, "f": -2.5, "z": 0, "big": 10 ** 12},
    "booleans and null": {"t": True, "f": False, "n": None},
    "leading and trailing space": {"a": " lead", "b": "trail ", "c": "-dash"},
    "colon inside a value": {"home": "https://example.com/x", "k": "a: colon",
                             "run": "pytest -q --tb=short"},
    "list nested in a list": {"pairs": [["a", "b"], ["c"]]},
    "deep but legal nesting": {"a": {"b": {"c": {"d": ["e", {"f": ["g"]}]}}}},
}


class TestRoundTrip(unittest.TestCase):
    """The property the whole module exists to provide.

    Ledger files are rewritten on every status change, so a value that survives
    one cycle but not three is still a bug — hence the repeat.
    """

    def test_every_ledger_shape_survives_a_round_trip(self):
        for name, document in CORPUS.items():
            with self.subTest(shape=name):
                self.assertEqual(miniyaml.loads(miniyaml.dumps(document)), document)

    def test_three_rewrites_change_nothing(self):
        for name, document in CORPUS.items():
            with self.subTest(shape=name):
                text = miniyaml.dumps(document)
                for _ in range(3):
                    text = miniyaml.dumps(miniyaml.loads(text))
                self.assertEqual(miniyaml.loads(text), document)


class TestNestedCollections(unittest.TestCase):
    """A collection under a list item's key used to be written as a Python repr.

    `dumps` called `_emit` on every value of a list item, and `_emit` fell
    through to `str(value)`, so `owns: ["src/"]` was written as the string
    `"['src/']"` and read back as a string.
    """

    def test_a_list_under_a_list_item_stays_a_list(self):
        text = miniyaml.dumps({"verify": [{"kind": "diff", "owns": ["src/"]}]})
        self.assertNotIn("['src/']", text)
        self.assertEqual(miniyaml.loads(text)["verify"][0]["owns"], ["src/"])

    def test_a_map_under_a_list_item_stays_a_map(self):
        check = {"kind": "cmd", "run": "pytest", "env": {"CI": "1"}}
        parsed = miniyaml.loads(miniyaml.dumps({"verify": [check]}))
        self.assertEqual(parsed["verify"][0]["env"], {"CI": "1"})

    def test_the_nested_block_may_belong_to_any_key(self):
        """`_sequence` only ever handled a block under the item's *first* key;
        a block under a later key merged into the item at the wrong depth."""
        text = "verify:\n  - owns:\n      - src/\n    kind: diff\n"
        self.assertEqual(
            miniyaml.loads(text), {"verify": [{"owns": ["src/"], "kind": "diff"}]}
        )


class TestMultiLineValues(unittest.TestCase):
    def test_newlines_are_escaped_rather_than_written_raw(self):
        """A raw newline put an unparseable line in the middle of the block, so
        the failure was never local to the value that caused it."""
        text = miniyaml.dumps({"note": "line1\nline2"})
        self.assertEqual(len(text.splitlines()), 1)
        self.assertEqual(miniyaml.loads(text), {"note": "line1\nline2"})

    def test_a_literal_backslash_n_is_not_a_newline(self):
        self.assertEqual(miniyaml.loads(miniyaml.dumps({"k": "a\\nb"}))["k"], "a\\nb")

    def test_every_character_splitlines_breaks_on_is_escaped(self):
        """`\\v`, `\\f`, `\\x1c`-`\\x1e`, `\\x85` and the Unicode separators end a line
        for `str.splitlines` as surely as `\\n` does, so leaving them raw
        reproduces the whole-block corruption in a form nobody spots by eye."""
        for char in "\v\f\x1c\x1d\x1e\x85  ":
            with self.subTest(char=repr(char)):
                text = miniyaml.dumps({"note": "a" + char + "b"})
                self.assertEqual(len(text.splitlines()), 1)
                self.assertEqual(miniyaml.loads(text)["note"], "a" + char + "b")


class TestKeys(unittest.TestCase):
    def test_a_key_outside_the_bare_pattern_is_quoted_and_read_back(self):
        """`dumps` wrote `a b: 1`, which `loads` then rejected — the writer
        emitting files the reader refuses."""
        self.assertEqual(miniyaml.loads(miniyaml.dumps({"a b": 1})), {"a b": 1})

    def test_a_quoted_key_and_a_quoted_value_stay_apart(self):
        self.assertEqual(miniyaml.loads('"a b": "c: d"'), {"a b": "c: d"})


class TestCoercion(unittest.TestCase):
    """Reading coerces; writing has to quote whatever reading would change."""

    def test_strings_that_look_like_other_types_stay_strings(self):
        for value in ("007", "1_000", "nan", "Infinity", "true", "no", "~",
                      "null", "[]", "{}", "0x1f", "1e3", "-0"):
            with self.subTest(value=value):
                self.assertEqual(miniyaml.loads(miniyaml.dumps({"k": value}))["k"], value)

    def test_real_numbers_and_booleans_are_still_unquoted(self):
        text = miniyaml.dumps({"n": 7, "f": 1.5, "b": True, "z": None})
        self.assertNotIn('"', text)
        self.assertEqual(miniyaml.loads(text), {"n": 7, "f": 1.5, "b": True, "z": None})


class TestEmptyCollections(unittest.TestCase):
    def test_an_empty_map_does_not_become_null(self):
        """`dumps` wrote a bare `key:`, which reads back as null, so a check
        whose `env` had been emptied lost its type on the next rewrite."""
        self.assertEqual(miniyaml.loads(miniyaml.dumps({"env": {}})), {"env": {}})

    def test_an_empty_list_stays_a_list(self):
        self.assertEqual(miniyaml.loads(miniyaml.dumps({"owns": []})), {"owns": []})

    def test_an_empty_item_is_writable_and_readable(self):
        """`loads` produced `[{}]` from `- foo:` while `dumps` raised IndexError
        on it — a value the reader could make and the writer could not write."""
        self.assertEqual(miniyaml.loads("owns:\n  - foo:\n"), {"owns": [{"foo": None}]})
        self.assertEqual(miniyaml.loads(miniyaml.dumps({"owns": [{}]})), {"owns": [{}]})


class TestAmbiguousListItems(unittest.TestCase):
    def test_a_pattern_containing_a_colon_is_not_a_mapping(self):
        """`redact` entries are regexes; `password: .*` was read as a one-key
        map, so the pattern silently stopped redacting anything."""
        document = {"redact": ["password: .*"]}
        self.assertEqual(miniyaml.loads(miniyaml.dumps(document)), document)


class TestLimitsAndRefusals(unittest.TestCase):
    def test_deep_nesting_raises_miniyamlerror_not_recursionerror(self):
        """`frontmatter.parse` catches MiniYamlError only, so a RecursionError
        from a merely deep file took the whole command down."""
        text = "\n".join("  " * level + "k:" for level in range(400))
        with self.assertRaises(miniyaml.MiniYamlError):
            miniyaml.loads(text)
        document = {"k": 1}
        for _ in range(400):
            document = {"k": document}
        with self.assertRaises(miniyaml.MiniYamlError):
            miniyaml.dumps(document)

    def test_the_parser_refuses_what_it_cannot_represent(self):
        for name, text in (
            ("unterminated quote", 'a: "abc'),
            ("anchor", "a: &base 1"),
            ("alias", "a: *base"),
            ("tag", "a: !!str 1"),
            ("inline map", "a: {b: 1}"),
            ("tab indentation", "a:\n\tb: 1"),
            ("inconsistent indentation", "a:\n  b: 1\n   c: 2"),
            ("document marker", "---\na: 1"),
            ("block scalar", "a: |\n  line1\n  line2"),
            ("folded scalar", "a: >\n  line1"),
            ("merge key", "a:\n  <<: *base\n  b: 1"),
            ("byte order mark", "\ufeffa: 1"),
            ("not a mapping", "not a mapping at all"),
        ):
            with self.subTest(case=name):
                with self.assertRaises(miniyaml.MiniYamlError):
                    miniyaml.loads(text)

    def test_a_ten_megabyte_line_still_parses_quickly(self):
        import time

        value = "x" * 10_000_000
        for text in ("k: " + value, 'k: "' + value + '"'):
            started = time.perf_counter()
            self.assertEqual(len(miniyaml.loads(text)["k"]), len(value))
            self.assertLess(time.perf_counter() - started, 2.0)


class TestTheSymbolFreezeIsNotDisarmed(unittest.TestCase):
    """The measured consequence of the nested-collection defect.

    `contains` came back as the string `"['def rotate(key)']"`, and
    `_check_symbol` iterates whatever it is given: over a string that is one
    character per name, and single characters appear in any real source file. So
    a renamed signature — exactly what the freeze exists to catch — was reported
    as a PASS. This asserts against `ctx.verify` rather than against the parsed
    shape, because the guarantee is the verdict, not the type.
    """

    def setUp(self):
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name)

    def _verdict(self, source):
        (self.cwd / "api.py").write_text(source, encoding="utf-8")
        check = {"kind": "symbol", "path": "api.py", "contains": ["def rotate(key)"]}
        stored = miniyaml.loads(miniyaml.dumps({"verify": [check]}))["verify"][0]
        return verify._check_symbol(stored, self.cwd)

    def test_a_renamed_symbol_still_fails_after_a_round_trip(self):
        self.assertEqual(self._verdict("def spin(key):\n    return key\n").status,
                         verify.FAIL)

    def test_an_intact_symbol_still_passes_after_a_round_trip(self):
        self.assertEqual(self._verdict("def rotate(key):\n    return key\n").status,
                         verify.PASS)


class TestMultiLineMetaDoesNotWipeFrontmatter(unittest.TestCase):
    """The measured consequence of the raw-newline defect.

    `frontmatter.parse` swallows MiniYamlError and returns an empty meta dict, so
    one unparseable line cost the document *every* key: `owns` came back empty
    and `_check_diff` then returned PASS with "no owned scope declared" — a green
    gate over unscoped work. Reachable from `ctx verify --sign-off rubric --note`
    with a note read out of a file.
    """

    def test_a_note_with_newlines_leaves_the_other_keys_intact(self):
        meta = {
            "ctx_schema": 1, "unit": "01-keys", "status": "pending",
            "tier": "subagent", "owns": ["src/keys.py"],
            "verify": [{"kind": "diff", "owns": ["src/keys.py"]}],
            "note": "line one\nline two\n\nline four",
        }
        parsed = frontmatter.parse(frontmatter.Document(meta, "## Objective\nDo it.\n").render())
        self.assertEqual(parsed.meta, meta)
        self.assertEqual(parsed.meta["owns"], ["src/keys.py"])
        self.assertEqual(parsed.meta["note"], "line one\nline two\n\nline four")
        self.assertIn("Objective", parsed.body)


if __name__ == "__main__":
    unittest.main()
