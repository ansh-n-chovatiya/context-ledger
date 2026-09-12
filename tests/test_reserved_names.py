"""A name Windows hands to a device instead of to the filesystem.

`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9` and `LPT1`-`LPT9` are not filenames
on Windows. They are devices, in every directory, with any extension after
them: `.ctx\\tasks\\con.md` is the console. Opening one for writing *succeeds*
and the bytes go nowhere recoverable, so `ctx task con` reported a task
written and left an empty `tasks/` directory; the paths with a directory
component (`.ctx\\specs\\con\\spec.md`) fail outright instead.

Both slug functions kept the name intact — `bundle.slugify` strips to
`[a-z0-9-]`, which leaves `con` exactly as typed, and `spec.normalise_slug`
only removed characters that are illegal on their own. Neither had any notion
of a name that is legal character by character and reserved as a whole.

Two things this file is careful about:

  * **It never skips.** A `skipUnless(os.name == "nt")` here would run on one
    job of the CI matrix at most, and on nobody's laptop — which is exactly
    how the defect survived. The guard is unconditional in the source, so the
    test is unconditional too, and `reserved_stem` below re-implements the
    Windows matching rule rather than asking the platform.
  * **It asserts the filename, not the slug.** The slug is an implementation
    detail; the thing that has to be openable is `.ctx/tasks/<slug>.md` and
    `.ctx/contexts/<slug>.ctx.md`, extension included.

Removing `spec.avoid_reserved_name` from either caller fails this file.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import bundle, frontmatter  # noqa: E402
from ctx import spec as spec_mod  # noqa: E402
from support import Fixture  # noqa: E402


# The names as a user would type them, and as Windows documents them.
DEVICE_NAMES = (
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{d}" for d in range(1, 10)]
    + [f"LPT{d}" for d in range(1, 10)]
)


def reserved_stem(filename):
    """Does Windows resolve `filename` to a device? The rule, not the guess.

    Written out here on purpose: asserting against `spec.RESERVED_DEVICE_NAMES`
    alone would pass if the fix and the test shared one wrong idea of the rule.
    Matching is on the part before the first dot, case-insensitively, ignoring
    trailing dots and spaces.
    """
    stem = str(filename).split(".", 1)[0].strip(" .").lower()
    return stem in {
        "con", "prn", "aux", "nul",
        *(f"com{d}" for d in range(1, 10)),
        *(f"lpt{d}" for d in range(1, 10)),
    }


class TestTheRuleUnderTest(unittest.TestCase):
    """The helper above is the oracle, so pin it before trusting it."""

    def test_it_recognises_a_device_with_and_without_an_extension(self):
        for name in ("con", "CON", "con.md", "CON.CTX.MD", "nul.", "aux "):
            with self.subTest(name=name):
                self.assertTrue(reserved_stem(name))

    def test_it_leaves_ordinary_names_alone(self):
        for name in ("console.md", "context.md", "com10.md", "connection",
                     "lpt.md", "my-con.md", "con-ctx.md"):
            with self.subTest(name=name):
                self.assertFalse(reserved_stem(name))


class TestSlugify(unittest.TestCase):
    """Criterion 1, for `bundle.slugify` — the audit named this one."""

    def test_no_device_name_survives_slugify(self):
        for name in DEVICE_NAMES:
            for typed in (name, name.lower(), f"{name}.md", f" {name} "):
                with self.subTest(typed=typed):
                    slug = bundle.slugify(typed)
                    self.assertFalse(reserved_stem(slug), slug)

    def test_the_filename_the_slug_becomes_is_not_a_device_either(self):
        """`con` + `.ctx.md` is still the console; the extension does not save it."""
        for name in DEVICE_NAMES:
            with self.subTest(name=name):
                self.assertFalse(reserved_stem(bundle.slugify(name) + bundle.SUFFIX))
                self.assertFalse(reserved_stem(bundle.slugify(name) + ".md"))

    def test_the_name_stays_recognisable(self):
        """A suffix, not a hash: the user typed a real word."""
        self.assertEqual(bundle.slugify("con"), "con-ctx")
        self.assertEqual(bundle.slugify("LPT1"), "lpt1-ctx")
        for name in DEVICE_NAMES:
            with self.subTest(name=name):
                self.assertTrue(bundle.slugify(name).startswith(name.lower()))

    def test_slugify_is_idempotent_on_a_device_name(self):
        """Callers re-slugify slugs — `resolve` does it to what `save` wrote."""
        for name in DEVICE_NAMES:
            with self.subTest(name=name):
                once = bundle.slugify(name)
                self.assertEqual(bundle.slugify(once), once)

    def test_ordinary_names_are_untouched(self):
        for name, expected in (
            ("Demo Bundle", "demo-bundle"),
            ("console", "console"),
            ("com10", "com10"),
            ("contract", "contract"),
            ("my con", "my-con"),
            ("", "context"),
            ("con man", "con-man"),
        ):
            with self.subTest(name=name):
                self.assertEqual(bundle.slugify(name), expected)


class TestNormaliseSlug(unittest.TestCase):
    """Criterion 2: the same hole, in the other slug function."""

    def test_no_device_name_survives_normalise_slug(self):
        for name in DEVICE_NAMES:
            for typed in (name, name.lower(), f"{name}.md", f"{name}."):
                with self.subTest(typed=typed):
                    slug = spec_mod.normalise_slug(typed)
                    self.assertFalse(reserved_stem(slug), slug)

    def test_a_device_name_with_an_extension_keeps_the_extension(self):
        """`normalise_slug` does not strip dots, so the suffix goes on the stem."""
        self.assertEqual(spec_mod.normalise_slug("con.md"), "con-ctx.md")

    def test_it_stays_idempotent(self):
        for name in DEVICE_NAMES + ["con.md", "billing", "a" * 400]:
            with self.subTest(name=name):
                once = spec_mod.normalise_slug(name)
                self.assertEqual(spec_mod.normalise_slug(once), once)

    def test_the_byte_cap_still_holds(self):
        """Criterion 4: the suffix must not push anything past the cap."""
        for name in DEVICE_NAMES + ["a" * 400, "日本語" * 100, "billing"]:
            with self.subTest(name=name):
                slug = spec_mod.normalise_slug(name)
                self.assertLessEqual(
                    len(slug.encode("utf-8")), spec_mod.SLUG_MAX_BYTES
                )

    def test_the_sha256_suffix_for_long_slugs_is_unchanged(self):
        """Criterion 4: the disambiguator a previous wave added still stands."""
        self.assertEqual(spec_mod.normalise_slug("z" * 400), "z" * 71 + "-6768a45e")

    def test_the_fallback_is_unchanged(self):
        self.assertEqual(spec_mod.normalise_slug("///"), spec_mod.SLUG_FALLBACK)
        self.assertFalse(reserved_stem(spec_mod.SLUG_FALLBACK))

    def test_the_two_functions_agree(self):
        """A spec and a bundle of one name must not disagree about its path."""
        for name in DEVICE_NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    spec_mod.normalise_slug(bundle.slugify(name)),
                    bundle.slugify(name),
                )


class TestOnDisk(Fixture):
    """Criterion 3: `ctx task «con»` and a bundle named `con`, run for real."""

    def test_ctx_task_con_writes_a_file_that_is_not_a_device(self):
        code, out, err = self.cli_streams("task", "con")
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        files = sorted(p.name for p in self.layout.tasks.glob("*.md"))
        self.assertEqual(len(files), 1, files)
        self.assertFalse(reserved_stem(files[0]), files[0])
        self.assertTrue((self.layout.tasks / files[0]).is_file())

    def test_every_device_name_is_a_usable_task_path(self):
        for name in DEVICE_NAMES:
            with self.subTest(name=name):
                path = self.layout.task_file(bundle.slugify(name))
                self.assertFalse(reserved_stem(path.name), path.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
                self.assertEqual(path.read_text(encoding="utf-8"), "x")

    def test_the_task_the_state_points_at_is_the_file_on_disk(self):
        """The suffix has to reach `state.json` too, or the briefing looks for
        a task file that was never written under that name."""
        from ctx import state as state_mod

        self.assertEqual(self.cli_streams("task", "con")[0], 0)
        slug = state_mod.load(self.layout).get("task")
        self.assertEqual(slug, "con-ctx")
        self.assertTrue(self.layout.task_file(slug).is_file())

    def test_a_bundle_named_con_saves_and_resolves(self):
        body = "## Situation\nWe are testing.\n\n## Resume here\nRun tests.\n"
        path = bundle.save(self.layout, "con", body, config=self.config)
        self.assertTrue(path.is_file())
        self.assertFalse(reserved_stem(path.name), path.name)
        self.assertEqual(bundle.resolve(self.layout, "con"), path)
        self.assertEqual(bundle.resolve(self.layout, "CON"), path)
        self.assertEqual(frontmatter.read(path).meta["name"], "con-ctx")

    def test_a_bundle_named_con_is_listed_and_indexed(self):
        bundle.save(self.layout, "con", "## Situation\nHere.\n", config=self.config)
        names = [row[1] for row in bundle.listing(self.layout, include_global=False)]
        self.assertEqual(names, ["con-ctx"])
        self.assertIn("con-ctx", self.layout.context_index.read_text(encoding="utf-8"))

    def test_a_bundle_named_con_promotes_to_the_global_store(self):
        bundle.save(self.layout, "con", "## Situation\nHere.\n", config=self.config)
        target = bundle.promote(self.layout, "con")
        self.assertIsNotNone(target)
        self.assertTrue(target.is_file())
        self.assertFalse(reserved_stem(target.name), target.name)

    def test_ctx_spec_con_creates_a_directory_that_is_not_a_device(self):
        code, out, err = self.cli_streams("spec", "con")
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        directories = [p for p in self.layout.specs.iterdir() if p.is_dir()]
        self.assertEqual(len(directories), 1, out)
        self.assertFalse(reserved_stem(directories[0].name), directories[0].name)
        self.assertTrue((directories[0] / "spec.md").is_file())

    def test_the_spec_gate_still_works_on_a_device_named_spec(self):
        """The name is retyped uncapped by the next command, as a user would."""
        self.assertEqual(self.cli_streams("spec", "con")[0], 0)
        code, out, err = self.cli_streams("question", "con", "Which tenants?")
        self.assertEqual(code, 0, f"out={out!r} err={err!r}")
        blocking, _non, _resolved = spec_mod.questions(self.layout, "con")
        self.assertEqual(blocking, ["Which tenants?"])

    def test_an_ordinary_task_name_is_unaffected(self):
        """Criterion 4, end to end: nothing changes for names nobody reserved."""
        self.assertEqual(self.cli_streams("task", "fix-token-refresh")[0], 0)
        self.assertTrue(self.layout.task_file("fix-token-refresh").is_file())


if __name__ == "__main__":
    unittest.main()
