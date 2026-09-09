"""Model tiers, complexity weights and `tier_up` — the one place a model name
may live outside `config.DEFAULTS`.

Every other module that needs a model — complexity-score, dispatch-selection,
findings-rounds — is expected to walk `models.tiers` rather than spell a model
name of its own, so the load-bearing property here is not just "the keys
exist" but that they survive an on-disk override (including a project that
reorders the tiers) and that `tier_up` never escalates past what a unit's own
`model:` deliberately chose.
"""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import config as config_mod, miniyaml  # noqa: E402
from support import Fixture  # noqa: E402


class TestDefaultsShape(unittest.TestCase):
    def test_tiers_are_cheapest_first(self):
        self.assertEqual(
            config_mod.DEFAULTS["models"]["tiers"], ["haiku", "sonnet", "opus"]
        )

    def test_escalation_flag_defaults_off(self):
        self.assertIs(
            config_mod.DEFAULTS["models"]["escalate_on_failed_round"], False
        )

    def test_weights_present(self):
        weights = config_mod.DEFAULTS["complexity"]["weights"]
        self.assertEqual(
            weights,
            {
                "budget_per_15k": 1.0,
                "owns_per_path": 0.5,
                "reads_per_2paths": 0.5,
                "depends_on_each": 0.5,
                "judged_verify": 2.0,
                "publishes_iface": 2.0,
                "kind_bug": 2.0,
            },
        )

    def test_thresholds_present(self):
        self.assertEqual(
            config_mod.DEFAULTS["complexity"]["thresholds"],
            {"standard": 3.0, "deep": 6.0},
        )

    def test_no_model_name_hardcoded_outside_defaults(self):
        """The tier vocabulary lives in exactly one place.

        A model name anywhere else in `config.py` would mean some function
        made its own private judgement about which model is dear, instead of
        walking `models.tiers` — and a project that reordered the list would
        then get honoured in some places and ignored in others.
        """
        import inspect

        source = inspect.getsource(config_mod)
        # Strip the DEFAULTS/PROFILES literal blocks, the only place these
        # words are allowed, before searching the rest of the module.
        body = source.split("PROFILES = {", 1)[1]
        body = body.split("\n}\n", 1)[1] if "\n}\n" in body else ""
        for name in ("haiku", "sonnet", "opus"):
            self.assertNotIn(name, body, f"{name!r} leaked outside DEFAULTS")


class TestMiniyamlRoundTrip(unittest.TestCase):
    """`config.render` must be able to serialise what it just grew."""

    def test_defaults_round_trip_through_miniyaml(self):
        original = copy.deepcopy(config_mod.DEFAULTS)
        text = miniyaml.dumps(original)
        self.assertEqual(miniyaml.loads(text), original)

    def test_nested_floats_and_lists_survive(self):
        text = miniyaml.dumps(config_mod.DEFAULTS["complexity"])
        back = miniyaml.loads(text)
        self.assertEqual(back["weights"]["budget_per_15k"], 1.0)
        self.assertIsInstance(back["weights"]["budget_per_15k"], float)
        self.assertEqual(back["thresholds"]["deep"], 6.0)

        tiers_text = miniyaml.dumps({"tiers": config_mod.DEFAULTS["models"]["tiers"]})
        self.assertEqual(
            miniyaml.loads(tiers_text)["tiers"], ["haiku", "sonnet", "opus"]
        )

    def test_render_produces_loadable_yaml(self):
        rendered = config_mod.render(copy.deepcopy(config_mod.DEFAULTS))
        # `render` prefixes commented prose `loads` must skip cleanly, then the
        # body itself must come back byte-for-byte the same structure.
        loaded = miniyaml.loads(rendered)
        self.assertEqual(loaded["models"]["tiers"], ["haiku", "sonnet", "opus"])
        self.assertEqual(loaded["complexity"]["weights"]["judged_verify"], 2.0)


class TestTierUp(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(config_mod.DEFAULTS)

    def test_escalates_to_the_next_dearer_model(self):
        self.assertEqual(config_mod.tier_up(self.config, "haiku"), "sonnet")
        self.assertEqual(config_mod.tier_up(self.config, "sonnet"), "opus")

    def test_dearest_tier_is_unchanged_not_raised(self):
        self.assertEqual(config_mod.tier_up(self.config, "opus"), "opus")

    def test_a_model_absent_from_tiers_is_never_escalated(self):
        """An explicit unit `model:` is a judgement call, not a default."""
        self.assertEqual(config_mod.tier_up(self.config, "house-model"), "house-model")
        self.assertEqual(config_mod.tier_up(self.config, ""), "")
        self.assertIsNone(config_mod.tier_up(self.config, None))

    def test_honours_a_reordered_or_trimmed_tier_list(self):
        self.config["models"]["tiers"] = ["opus", "sonnet"]
        self.assertEqual(config_mod.tier_up(self.config, "opus"), "sonnet")
        self.assertEqual(config_mod.tier_up(self.config, "sonnet"), "sonnet")


class TestOverrideMerge(Fixture):
    """Every new key must survive a hand-edited `ctx.yaml`, unharmed."""

    def _overwrite(self, patch):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data.update(patch)
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")

    def test_reordering_tiers_survives_the_merge(self):
        self._overwrite({"models": {"tiers": ["opus", "haiku"]}})
        merged = config_mod.load(self.layout)
        self.assertEqual(merged["models"]["tiers"], ["opus", "haiku"])
        # Sibling defaults in the same block are untouched by a partial override.
        self.assertEqual(merged["models"]["runner"], "sonnet")

    def test_escalation_flag_survives_the_merge(self):
        self._overwrite({"models": {"escalate_on_failed_round": True}})
        merged = config_mod.load(self.layout)
        self.assertIs(merged["models"]["escalate_on_failed_round"], True)
        self.assertEqual(merged["models"]["tiers"], ["haiku", "sonnet", "opus"])

    def test_weights_and_thresholds_survive_the_merge(self):
        self._overwrite({"complexity": {"weights": {"budget_per_15k": 2.0}}})
        merged = config_mod.load(self.layout)
        self.assertEqual(merged["complexity"]["weights"]["budget_per_15k"], 2.0)
        # An untouched weight keeps its default rather than vanishing.
        self.assertEqual(merged["complexity"]["weights"]["kind_bug"], 2.0)
        self.assertEqual(merged["complexity"]["thresholds"], {"standard": 3.0, "deep": 6.0})

    def test_tier_up_on_the_loaded_and_reordered_config(self):
        self._overwrite({"models": {"tiers": ["sonnet", "haiku", "opus"]}})
        merged = config_mod.load(self.layout)
        self.assertEqual(config_mod.tier_up(merged, "sonnet"), "haiku")
        self.assertEqual(config_mod.tier_up(merged, "opus"), "opus")


class TestRenderExplainsTheNewKeys(unittest.TestCase):
    def test_tiers_weights_and_escalation_are_explained_inline(self):
        rendered = config_mod.render(copy.deepcopy(config_mod.DEFAULTS))
        header = rendered.split("models:", 1)[0]
        self.assertIn("models.tiers", header)
        self.assertIn("models.escalate_on_failed_round", header)
        self.assertIn("complexity.weights", header)
        self.assertIn("complexity.thresholds", header)


if __name__ == "__main__":
    unittest.main()
