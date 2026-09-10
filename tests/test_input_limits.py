"""Two ways an input got past a limit that was never there.

**A long intent.** `ctx spec «400 characters»` slugified the whole sentence into
one directory name and handed it to the filesystem, which refused it:
`OSError: [Errno 63] File name too long`. The catch-all in `main` turned that
into a clean `ctx spec failed:` and exit 2, so it stopped looking like a bug —
but a clean refusal is still a refusal, and the intent was only wordy, not
wrong. The cap belongs in `spec.normalise_slug`, reached through `spec_dir`, so
every caller of `spec.create` gets it and not just the one CLI path.

The interesting property is not the cap. It is that the cap cannot be a plain
truncation: two long intents that agree for their first eighty characters are
*exactly* what someone writes when describing one feature twice, and truncating
both would silently open the second one's spec onto the first one's directory.
`TestPrefixCollisions` is the test a naive truncation fails.

**A silent demotion.** `config.normalise_level` coerces anything it cannot read
to `"0"`, and said nothing at all. The coercion is right — `briefing_cap` turns
the level into a spend limit, so an unreadable one must buy the smallest
briefing rather than the largest, and `tests/test_config_levels.py` pins that
direction with 23 tests that this file must not disturb. The defect was the
silence: a hand-edited `level: L3` demoted a whole project with no message on
any channel. What is added here is one line on stderr, and the tests below
pin the channel (`stderr`, never `stdout`), the content (the offending value
*and* where it landed), the silence for `None` and for a level that was fine,
and the deduplication that holds it to one line per invocation however many
times the fall-down is reached.

Each test is written so that removing the guard it covers fails it:
  - drop the byte cap in `normalise_slug`   -> TestSlugCap, TestSpecCommand
  - make the digest a constant              -> TestPrefixCollisions
  - drop the `_warn_level_fallback` call    -> TestLevelWarning
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import bundle, config as config_mod, frontmatter  # noqa: E402
from ctx import spec as spec_mod  # noqa: E402
from support import Fixture  # noqa: E402


# A wordy but entirely reasonable intent: no punctuation trouble, just long.
LONG_INTENT = (
    "add user facing audit logging to the billing service so that support can "
    "answer refund questions without asking an engineer to run a query by hand "
    "against the production replica every single time a customer writes in "
    "about a charge they do not recognise on their statement this quarter or "
    "the last one and also the one before that as well as any earlier period "
    "for which we still hold delivery records in the warm storage tier today"
)


def slug_for(intent):
    """The slug the CLI would derive, before the cap sees it."""
    return bundle.slugify(intent)


class TestSlugCap(unittest.TestCase):
    """Criterion 1 and 2: short enough everywhere, and the same every time."""

    def test_a_long_intent_is_capped(self):
        slug = spec_mod.normalise_slug(slug_for(LONG_INTENT))
        self.assertLessEqual(
            len(slug.encode("utf-8")),
            spec_mod.SLUG_MAX_BYTES,
            "an uncapped slug is what raised OSError: File name too long",
        )

    def test_the_cap_is_well_inside_the_filesystem_component_limit(self):
        """255 bytes is the ext4/APFS/NTFS limit for one path component."""
        self.assertLessEqual(spec_mod.SLUG_MAX_BYTES, 255)

    def test_the_whole_relative_path_stays_short_enough_for_windows(self):
        """The slug is a directory with files under it, so the binding length
        is `.ctx/specs/<slug>/questions.md`, not the slug alone."""
        slug = spec_mod.normalise_slug(slug_for(LONG_INTENT))
        relative = f".ctx/specs/{slug}/questions.md"
        self.assertLess(
            len(relative), 130,
            "a legacy Windows MAX_PATH is 260 characters including the "
            "repository root, which has to fit in what is left",
        )

    def test_any_length_of_input_lands_under_the_cap(self):
        for size in (0, 1, 79, 80, 81, 255, 256, 400, 4000):
            with self.subTest(size=size):
                slug = spec_mod.normalise_slug("a" * size)
                self.assertTrue(slug)
                self.assertLessEqual(
                    len(slug.encode("utf-8")), spec_mod.SLUG_MAX_BYTES
                )

    def test_a_short_slug_is_returned_untouched(self):
        for slug in ("billing", "auth-rotation", "fix-token-refresh-2"):
            with self.subTest(slug=slug):
                self.assertEqual(spec_mod.normalise_slug(slug), slug)

    def test_the_cap_is_deterministic_across_calls(self):
        slug = slug_for(LONG_INTENT)
        results = {spec_mod.normalise_slug(slug) for _ in range(20)}
        self.assertEqual(
            len(results), 1,
            "a disambiguator drawn from the clock, randomness or a counter "
            "would scaffold a second spec every time the same intent was typed",
        )

    def test_the_cap_is_idempotent(self):
        """`spec_dir` sees slugs that have already been through the cap."""
        for source in (LONG_INTENT, "a" * 500, "日本語" * 100, "billing"):
            with self.subTest(source=source[:20]):
                once = spec_mod.normalise_slug(slug_for(source))
                self.assertEqual(spec_mod.normalise_slug(once), once)

    def test_the_digest_is_a_function_of_the_slug_alone(self):
        """Same input, fresh interpreter — the digest must not move. Pinning
        the literal is what catches a disambiguator that reads `id()`, the
        clock, or `PYTHONHASHSEED`-sensitive `hash()`."""
        self.assertEqual(
            spec_mod.normalise_slug("z" * 400),
            "z" * 71 + "-6768a45e",
        )


class TestPrefixCollisions(unittest.TestCase):
    """Criterion 3. The test a naive truncation fails."""

    PREFIX = (
        "rebuild the notification pipeline so that a delivery failure is "
        "retried with backoff and the reason is recorded, specifically for "
    )

    def test_two_long_intents_sharing_a_long_prefix_do_not_collide(self):
        first = slug_for(self.PREFIX + "password reset emails sent to users")
        second = slug_for(self.PREFIX + "invoice receipts sent to accounts")
        self.assertEqual(
            first[: spec_mod.SLUG_MAX_BYTES],
            second[: spec_mod.SLUG_MAX_BYTES],
            "the fixture is wrong if the slugs differ before the cap bites",
        )
        self.assertNotEqual(
            spec_mod.normalise_slug(first),
            spec_mod.normalise_slug(second),
            "truncation alone would open the second intent's spec onto the "
            "first one's directory",
        )

    def test_intents_differing_only_in_the_last_character_do_not_collide(self):
        base = "a" * 400
        self.assertNotEqual(
            spec_mod.normalise_slug(base + "b"),
            spec_mod.normalise_slug(base + "c"),
        )



class TestNonAscii(unittest.TestCase):
    """Criterion 4. No exception, and never an empty component."""

    def test_a_slug_of_characters_that_survive_no_normalisation(self):
        for text in ("日本語のテスト", "Ω" * 5, "🙂🙂🙂", "—— ——"):
            with self.subTest(text=text):
                slug = spec_mod.normalise_slug(slug_for(text))
                self.assertTrue(slug, "an empty component would write into .ctx/specs itself")
                self.assertNotIn("/", slug)
                self.assertNotIn("\\", slug)

    def test_a_slug_that_is_empty_after_normalisation_gets_a_usable_name(self):
        for value in ("", "   ", None, "...", "///", "\x00\x01"):
            with self.subTest(value=repr(value)):
                slug = spec_mod.normalise_slug(value)
                self.assertTrue(slug)
                self.assertEqual(slug, spec_mod.SLUG_FALLBACK)

    def test_a_long_non_ascii_slug_is_cut_on_a_character_boundary(self):
        slug = spec_mod.normalise_slug("日本語" * 100)
        self.assertLessEqual(len(slug.encode("utf-8")), spec_mod.SLUG_MAX_BYTES)
        # Decoding is the assertion: a cut inside a UTF-8 sequence would have
        # produced bytes that are not a string at all.
        self.assertEqual(slug.encode("utf-8").decode("utf-8"), slug)

    def test_path_separators_never_survive_into_a_component(self):
        for value in ("../../etc/passwd", "a/b", "a\\b", "..", "a:b*c?d"):
            with self.subTest(value=value):
                slug = spec_mod.normalise_slug(value)
                self.assertNotIn("/", slug)
                self.assertNotIn("\\", slug)
                self.assertNotIn("..", slug)


class TestCreateHonoursTheCap(Fixture):
    """Criterion 1 via the interface every caller uses, not via the CLI."""

    def test_create_writes_a_real_spec_for_a_long_intent(self):
        slug = slug_for(LONG_INTENT)
        path, qpath = spec_mod.create(self.layout, slug, LONG_INTENT)
        self.assertTrue(path.is_file())
        self.assertTrue(qpath.is_file())
        self.assertLessEqual(
            len(path.parent.name.encode("utf-8")), spec_mod.SLUG_MAX_BYTES
        )

    def test_the_frontmatter_names_the_directory_it_lives_in(self):
        slug = slug_for(LONG_INTENT)
        path, _qpath = spec_mod.create(self.layout, slug, LONG_INTENT)
        doc = frontmatter.read(path)
        self.assertEqual(doc.meta["spec"], path.parent.name)

    def test_the_uncapped_slug_still_resolves_to_the_same_spec(self):
        """`cmd_spec` records the raw slug in `state.json`; every lookup after
        that passes it back in uncapped, and must find the same directory."""
        slug = slug_for(LONG_INTENT)
        spec_mod.create(self.layout, slug, LONG_INTENT)
        added = spec_mod.add_questions(self.layout, slug, ["Which tenants?"])
        self.assertEqual(added, 1)
        blocking, _non, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(blocking, ["Which tenants?"])
        ready, still_blocking = spec_mod.ready(self.layout, slug)
        self.assertFalse(ready)
        self.assertEqual(len(still_blocking), 1)

    def test_create_is_still_safe_to_re_run_for_a_long_intent(self):
        slug = slug_for(LONG_INTENT)
        first = spec_mod.create(self.layout, slug, LONG_INTENT)
        spec_mod.add_questions(self.layout, slug, ["Which tenants?"])
        second = spec_mod.create(self.layout, slug, LONG_INTENT)
        self.assertEqual(first, second)
        blocking, _non, _resolved = spec_mod.questions(self.layout, slug)
        self.assertEqual(blocking, ["Which tenants?"], "a re-run must not wipe the gate")
        self.assertEqual(len(list(self.layout.specs.iterdir())), 1)

    def test_two_prefix_sharing_intents_get_two_directories_on_disk(self):
        """Criterion 3 as the thing it is actually about: the second intent
        must not open the first one's spec."""
        prefix = TestPrefixCollisions.PREFIX
        first = slug_for(prefix + "password reset emails sent to users")
        second = slug_for(prefix + "invoice receipts sent to accounts")
        spec_mod.create(self.layout, first, "first")
        spec_mod.add_questions(self.layout, first, ["Which tenants?"])
        spec_mod.create(self.layout, second, "second")
        directories = sorted(p.name for p in self.layout.specs.iterdir() if p.is_dir())
        self.assertEqual(len(directories), 2, directories)
        # And the second spec is genuinely its own, not the first one re-read.
        self.assertEqual(spec_mod.questions(self.layout, second)[0], [])
        self.assertEqual(spec_mod.questions(self.layout, first)[0], ["Which tenants?"])

    def test_a_long_decision_title_also_gets_a_filename(self):
        title = LONG_INTENT
        path = spec_mod.write_decision(self.layout, title, slug_for(title))
        self.assertTrue(path.is_file())
        self.assertLessEqual(len(path.name.encode("utf-8")), 255)


class TestSpecCommand(Fixture):
    """Criterion 5. Run it, do not reason about it."""

    def test_a_400_character_intent_creates_a_spec_and_exits_zero(self):
        # The original repro: everything the user typed arrives as one token,
        # `_split_name` reads it as a title, and the whole thing is slugified.
        intent = LONG_INTENT[:400]
        self.assertEqual(len(intent), 400)
        code, out, err = self.cli_streams("spec", intent)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        self.assertNotIn("failed", err)
        directories = [p for p in self.layout.specs.iterdir() if p.is_dir()]
        self.assertEqual(len(directories), 1, out)
        created = directories[0]
        self.assertTrue((created / "spec.md").is_file())
        self.assertTrue((created / "questions.md").is_file())
        self.assertLessEqual(
            len(created.name.encode("utf-8")), spec_mod.SLUG_MAX_BYTES
        )

    def test_the_spec_body_keeps_the_whole_intent(self):
        """Capping the *name* must not cap the content."""
        name = LONG_INTENT[:400]
        code, out, err = self.cli_streams("spec", name, "--intent", LONG_INTENT)
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        created = [p for p in self.layout.specs.iterdir() if p.is_dir()][0]
        body = (created / "spec.md").read_text(encoding="utf-8")
        self.assertIn(LONG_INTENT, body)

    def test_the_gate_still_works_on_a_long_named_spec(self):
        name = LONG_INTENT[:400]
        self.assertEqual(self.cli_streams("spec", name)[0], 0)
        # The name is passed back in uncapped, exactly as a user would retype
        # it or a command file would substitute it.
        code, out, err = self.cli_streams("question", name, "Which tenants?")
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        code, out = self.cli("spec-ready")
        self.assertEqual(code, 1, out)
        self.assertIn("BLOCKED", out)

    def test_a_non_ascii_intent_creates_a_spec(self):
        code, out, err = self.cli_streams("spec", "日本語のテストをする")
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        directories = [p for p in self.layout.specs.iterdir() if p.is_dir()]
        self.assertEqual(len(directories), 1)
        self.assertTrue(directories[0].name)


class TestLevelWarning(unittest.TestCase):
    """Criteria 6 and 7. The direction of the fail-down is not under test here
    — `tests/test_config_levels.py` owns that, and it must keep passing."""

    def setUp(self):
        config_mod.reset_level_warnings()
        self.addCleanup(config_mod.reset_level_warnings)

    def normalise(self, value):
        """(result, stdout, stderr) for one call."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = config_mod.normalise_level(value)
        return result, out.getvalue(), err.getvalue()

    def test_an_unrecognised_level_warns_on_stderr(self):
        result, out, err = self.normalise("L3")
        self.assertEqual(result, "0")
        self.assertEqual(out, "", "a warning on stdout corrupts `ctx briefing`")
        self.assertTrue(err, "a silent demotion is the whole defect")

    def test_the_warning_is_exactly_one_line(self):
        _result, _out, err = self.normalise("L3")
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_the_warning_names_the_offending_value(self):
        _result, _out, err = self.normalise("L3")
        self.assertIn("L3", err)

    def test_the_warning_names_the_level_it_fell_down_to(self):
        _result, _out, err = self.normalise("banana")
        self.assertIn("banana", err)
        self.assertIn("L0", err)

    def test_a_recognised_level_says_nothing_on_any_channel(self):
        for value in ("0", "1", "2", "L2", "l1", 2, " l1 "):
            with self.subTest(value=repr(value)):
                config_mod.reset_level_warnings()
                result, out, err = self.normalise(value)
                self.assertIn(result, config_mod.LEVELS)
                self.assertEqual(out, "")
                self.assertEqual(err, "")

    def test_none_says_nothing(self):
        """`None` is "unset", not a mistake — the caller's default applies."""
        result, out, err = self.normalise(None)
        self.assertEqual(result, "0")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_an_unrecognised_value_warns_once_per_process_not_once_per_call(self):
        first = self.normalise("L3")[2]
        repeats = [self.normalise("L3")[2] for _ in range(10)]
        self.assertTrue(first)
        self.assertEqual(
            repeats, [""] * 10,
            "normalise_level runs on every config and state read; a command "
            "that loads config twice must still say this once",
        )

    def test_a_second_distinct_bad_value_still_gets_its_own_line(self):
        self.assertTrue(self.normalise("L3")[2])
        self.assertIn("banana", self.normalise("banana")[2])

    def test_the_warning_is_a_single_ascii_line_whatever_the_value_is(self):
        for value in ("café", "two\nlines", "\U0001f642", ["2"], 3.5):
            with self.subTest(value=repr(value)):
                config_mod.reset_level_warnings()
                _result, _out, err = self.normalise(value)
                self.assertEqual(len(err.strip().splitlines()), 1, err)
                err.encode("ascii")  # raises if a cp1252 console could not print it

    def test_an_unreadable_level_in_ctx_yaml_warns_once_through_load(self):
        """Criterion 7, measured where it matters: `config.load` normalises on
        every read, and `state.load` and three call sites in `hooks` do too."""
        import tempfile

        from ctx import paths

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ctx").mkdir()
            layout = paths.Layout(root / ".ctx")
            layout.config.write_text("schema: 1\nlevel: L3\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                for _ in range(5):
                    data = config_mod.load(layout)
            self.assertEqual(data["level"], "0", "the fail-down direction is unchanged")
            self.assertEqual(len(err.getvalue().strip().splitlines()), 1, err.getvalue())
            self.assertIn("L3", err.getvalue())


if __name__ == "__main__":
    unittest.main()
