"""`ctx init` must render every key in `config.DEFAULTS`, not a hand-picked
subset.

`cmd_init` used to build the settings dict it writes to `ctx.yaml` from a
literal, hand-enumerated list of keys. Nothing forced that list to stay in
sync with `config.DEFAULTS`, so a new top-level default — `complexity` was
the first in the ledger's history — could be added to `DEFAULTS` and never
reach a generated `ctx.yaml`: a default that only lives in Python is a
default nobody can find, and the loss was silent because nothing here
noticed either list drift.

`tests/test_core.py::TestInit::test_generated_config_exposes_every_tunable`
guards the *instance* of that bug by walking today's `DEFAULTS` keys. It
would go green again the day someone "fixes" the next missing key by typing
it into a literal dict at the call site — reinstating the exact class of bug
it caught the first time. These tests guard the *class*: they inject a key
`DEFAULTS` cannot have known about ahead of time, so the only way to pass is
for `cmd_init` to still be deriving `settings` from `DEFAULTS` itself rather
than from anyone's memory of its keys.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import config as config_mod, miniyaml  # noqa: E402
from support import Fixture  # noqa: E402


class TestInitRendersEveryDefault(Fixture):
    """A `Fixture` whose `setUp` plants a synthetic key in `config.DEFAULTS`
    *before* calling `super().setUp()`, so the `ctx init` that fixture runs
    is the one under test. The key is a nested mapping, deliberately shaped
    like `complexity` (a dict of a dict), because a flat scalar default would
    not exercise the deep-copy guarantee these tests also check.
    """

    #: Unlikely enough that a real default will never collide with it, and
    #: named so a stray leak into another test's failure output is obvious
    #: about where it came from.
    SYNTHETIC_KEY = "synthetic_probe_key__test_init_renders_defaults"

    def setUp(self):
        self.assertNotIn(
            self.SYNTHETIC_KEY, config_mod.DEFAULTS,
            "the synthetic probe key collided with a real default — rename it",
        )
        config_mod.DEFAULTS[self.SYNTHETIC_KEY] = {"nested": {"deep": 1}}
        # `addCleanup` rather than `try`/`finally`: it runs even if `setUp`
        # raises partway through (for instance if the fixture's own `ctx
        # init` fails), and — unlike a `finally` a later edit could delete —
        # it cannot be dropped by accident. `DEFAULTS` is module-level global
        # state shared by every test in the process; leaking this key into it
        # would silently contaminate whichever test happens to run next, and
        # that failure would point nowhere near here.
        self.addCleanup(config_mod.DEFAULTS.pop, self.SYNTHETIC_KEY, None)
        super().setUp()

    def test_synthetic_default_is_rendered_with_no_matching_code_change(self):
        """The class, not the instance: this key exists nowhere but this
        test, yet `ctx init` (run by `Fixture.setUp`, above) must have
        written it to `ctx.yaml` anyway. If `cmd_init` ever goes back to
        listing keys by hand, this is the key nobody remembered to add, and
        this assertion is what turns that omission into a failing test
        instead of a silent one."""
        written = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        self.assertIn(
            self.SYNTHETIC_KEY, written,
            "a top-level key present in config.DEFAULTS but absent from the "
            "generated ctx.yaml — cmd_init is enumerating keys by hand again",
        )
        self.assertEqual(written[self.SYNTHETIC_KEY]["nested"]["deep"], 1)

    def test_settings_do_not_alias_defaults_nested_mappings(self):
        """`dict(DEFAULTS[...])` copies the outer mapping but keeps every
        nested mapping — `complexity.weights`, `models.tiers`, this test's
        own synthetic `nested` block — as the *same object* `config.DEFAULTS`
        holds. Writing `ctx.yaml` and reading it back can never show that:
        text serialisation always produces fresh objects, so the aliasing is
        only visible in the dict `cmd_init` hands to `config.render` before
        it is turned into text. Capturing that call is the only place a
        regression from `copy.deepcopy` back to `dict(...)` is observable."""
        captured = {}
        original_render = config_mod.render

        def capture(settings):
            captured["settings"] = settings
            return original_render(settings)

        with unittest.mock.patch.object(config_mod, "render", side_effect=capture):
            self.assertEqual(self.cli("init", "--force")[0], 0)

        settings = captured.get("settings")
        self.assertIsNotNone(settings, "cmd_init never called config.render")

        # Mutate the nested mapping this run wrote out, the way a project
        # hand-editing its own ctx.yaml and reloading it in-process might.
        settings[self.SYNTHETIC_KEY]["nested"]["deep"] = 999
        self.assertEqual(
            config_mod.DEFAULTS[self.SYNTHETIC_KEY]["nested"]["deep"], 1,
            "mutating cmd_init's generated settings mutated config.DEFAULTS "
            "too — cmd_init shallow-copied a nested default instead of "
            "deep-copying it",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
