"""`dispatch.model_for` — the band-to-model bridge, and the precedence chain
around it.

Four things are load-bearing here and none of them is decidable by reading
`model_for`'s callers in isolation.

**Precedence.** An explicit unit `model:` beats a score-derived tier, which
beats the `models.<role>` floor, which beats the built-in default. Each link
is tested on its own — not just the happy path where they all agree — because
the default config makes several of them coincide by accident (light maps to
the cheapest tier, which also happens to be nobody's floor; standard maps to
`sonnet`, which happens to be `models.runner`'s own default). A test that only
exercises the defaults would not notice if the precedence order were wrong,
only if the mapping were.

**The positional band-to-model bridge.** `complexity.tier_for` returns a band
name; `models.tiers` holds model names cheapest-first. Nothing before this
module maps one to the other (ADR 0002: `.ctx/decisions/0002-*.md`). The
mapping is positional and clamped — the clamp is asserted here with a
two-entry `models.tiers`, the case an unclamped index gets wrong silently and
the default three-entry list never exercises.

**Round-based escalation.** `models.escalate_on_failed_round` defaults to
`false`, and that is the path real projects run — so it is asserted
explicitly, not just inferred from the "on" case passing. An explicit
`model:` is immune to escalation too: an intentional choice does not get
walked up the tier list out from under the unit that made it.

**No model name in `ctx/dispatch.py`.** Every model name this module ever
returns came from `config` — `models.tiers`, `models.<role>`, or
`config.DEFAULTS`. A literal model string anywhere in that module is a name
this module was not supposed to know.
"""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    complexity, config as config_mod, dispatch, frontmatter, plan as plan_mod,
)
from support import OK, Fixture  # noqa: E402

DEFAULT_TIERS = config_mod.DEFAULTS["models"]["tiers"]  # ["haiku", "sonnet", "opus"]


def make_unit(name="01-test", **meta):
    """An in-memory `plan.Unit` — `model_for` only reads frontmatter-backed
    properties, so writing a real file to disk would just be slower and would
    not exercise anything a plain `frontmatter.Document` does not already
    give us."""
    base = {
        "ctx_schema": 1, "unit": name, "tier": "subagent",
        "owns": [], "reads": [], "depends_on": [], "forbid": [],
        "budget_tokens": 0, "status": "pending", "verify": [],
    }
    base.update(meta)
    doc = frontmatter.Document(base, "## Objective\ntest\n")
    return plan_mod.Unit(Path(f"{name}.md"), doc)


class TestPrecedence(unittest.TestCase):
    """explicit `model:` > score-derived tier > `models.<role>` floor >
    `DEFAULTS`. Each link tested separately from the others."""

    def test_explicit_model_beats_the_score_derived_tier(self):
        # This unit's own frontmatter scores deep (huge budget, many owned
        # paths) — which would otherwise map to the dearest tier — but it
        # names its own model, so the heuristic never runs.
        unit = make_unit(model="haiku", budget_tokens=200000,
                          owns=["a", "b", "c", "d"])
        self.assertEqual(dispatch.model_for(config_mod.DEFAULTS, unit), "haiku")

    def test_explicit_model_beats_the_role_floor_and_defaults_too(self):
        unit = make_unit(model="opus")
        config = {"models": {"runner": "haiku"}}
        self.assertEqual(dispatch.model_for(config, unit, "runner"), "opus")

    def test_score_derived_tier_beats_the_role_floor(self):
        # Light band (score 0) maps to the cheapest tier, `haiku` — even
        # though `models.runner`'s floor here is explicitly set to the
        # dearest tier. The score, not the floor, must win.
        unit = make_unit(budget_tokens=0, owns=[])
        config = {"models": {"runner": "opus", "tiers": list(DEFAULT_TIERS)}}
        score, _ = complexity.score(config, unit)
        self.assertEqual(complexity.tier_for(config, score), "light")
        self.assertEqual(dispatch.model_for(config, unit, "runner"), "haiku")

    def test_role_floor_used_when_no_unit_and_no_stats(self):
        config = {"models": {"verifier": "haiku"}}
        self.assertEqual(dispatch.model_for(config, role="verifier"), "haiku")

    def test_builtin_default_used_when_config_has_no_models_block_at_all(self):
        self.assertEqual(
            dispatch.model_for({}, role="runner"),
            config_mod.DEFAULTS["models"]["runner"],
        )
        self.assertEqual(
            dispatch.model_for({}, role="reviewer"),
            config_mod.DEFAULTS["models"]["reviewer"],
        )


class TestBandToModelBridge(unittest.TestCase):
    """The positional mapping this unit owns, per ADR 0002."""

    def test_light_standard_deep_map_positionally_onto_the_default_tiers(self):
        light = make_unit(name="01-light", budget_tokens=0, owns=[])
        standard = make_unit(name="02-standard", budget_tokens=45000, owns=["a"])
        deep = make_unit(name="03-deep", budget_tokens=90000, owns=[])

        light_score, _ = complexity.score(config_mod.DEFAULTS, light)
        standard_score, _ = complexity.score(config_mod.DEFAULTS, standard)
        deep_score, _ = complexity.score(config_mod.DEFAULTS, deep)
        self.assertEqual(complexity.tier_for(config_mod.DEFAULTS, light_score), "light")
        self.assertEqual(
            complexity.tier_for(config_mod.DEFAULTS, standard_score), "standard"
        )
        self.assertEqual(complexity.tier_for(config_mod.DEFAULTS, deep_score), "deep")

        self.assertEqual(
            dispatch.model_for(config_mod.DEFAULTS, light), DEFAULT_TIERS[0]
        )
        self.assertEqual(
            dispatch.model_for(config_mod.DEFAULTS, standard), DEFAULT_TIERS[1]
        )
        self.assertEqual(
            dispatch.model_for(config_mod.DEFAULTS, deep), DEFAULT_TIERS[2]
        )

    def test_deep_clamps_onto_the_dearest_of_a_two_entry_tier_list(self):
        """The case an unclamped index gets wrong silently, and the default
        three-entry `models.tiers` never exercises."""
        config = {"models": {"tiers": ["haiku", "sonnet"]}}
        unit = make_unit(budget_tokens=90000, owns=[])
        score, _ = complexity.score(config, unit)
        self.assertEqual(complexity.tier_for(config, score), "deep")
        # No IndexError, and the result is the dearer of the two entries —
        # not the third band's slot, which does not exist in this list.
        self.assertEqual(dispatch.model_for(config, unit), "sonnet")


class TestReviewerPackageDriven(unittest.TestCase):
    """Criterion 3: package size and scope violations, not a unit score,
    drive the reviewer's tier."""

    def test_small_clean_package_picks_the_cheaper_reviewer_tier(self):
        threshold = config_mod.DEFAULTS["review"]["small_package_bytes"]
        stats = {"bytes": threshold, "out_of_scope": 0}
        model = dispatch.model_for(config_mod.DEFAULTS, role="reviewer", stats=stats)
        floor = config_mod.DEFAULTS["models"]["reviewer"]
        self.assertNotEqual(model, floor)
        self.assertEqual(model, DEFAULT_TIERS[DEFAULT_TIERS.index(floor) - 1])

    def test_a_package_over_the_threshold_keeps_the_floor(self):
        threshold = config_mod.DEFAULTS["review"]["small_package_bytes"]
        stats = {"bytes": threshold + 1, "out_of_scope": 0}
        model = dispatch.model_for(config_mod.DEFAULTS, role="reviewer", stats=stats)
        self.assertEqual(model, config_mod.DEFAULTS["models"]["reviewer"])

    def test_any_scope_violation_keeps_the_floor_even_if_the_package_is_tiny(self):
        stats = {"bytes": 10, "out_of_scope": 1}
        model = dispatch.model_for(config_mod.DEFAULTS, role="reviewer", stats=stats)
        self.assertEqual(model, config_mod.DEFAULTS["models"]["reviewer"])

    def test_no_stats_at_all_falls_through_to_the_floor(self):
        """`dispatch_stats` returns `None` before any package exists — that
        must read as "no signal", never as a zero-byte, zero-violation
        package that would otherwise pick the cheap tier."""
        model = dispatch.model_for(config_mod.DEFAULTS, role="reviewer", stats=None)
        self.assertEqual(model, config_mod.DEFAULTS["models"]["reviewer"])

    def test_the_threshold_is_a_real_config_value_a_project_can_retune(self):
        """Same shape as complexity's "halve a weight, watch the score move":
        a package that is `small` under the built-in default must become
        `not small` once `ctx.yaml` lowers `review.small_package_bytes`
        below that package's own size — proving the number actually comes
        from `config`, not from a literal baked into `dispatch.py`."""
        stats = {"bytes": 5000, "out_of_scope": 0}
        default_config = copy.deepcopy(config_mod.DEFAULTS)
        floor = default_config["models"]["reviewer"]
        cheaper = DEFAULT_TIERS[DEFAULT_TIERS.index(floor) - 1]

        # Under the built-in default (20,000), 5,000 bytes is small.
        self.assertEqual(
            dispatch.model_for(default_config, role="reviewer", stats=stats), cheaper
        )

        # A project that tunes the threshold down below 5,000 must see this
        # exact same package stop qualifying — the cutoff moved, nothing
        # about the package did.
        tightened = copy.deepcopy(config_mod.DEFAULTS)
        tightened["review"]["small_package_bytes"] = 1000
        self.assertEqual(
            dispatch.model_for(tightened, role="reviewer", stats=stats), floor
        )


class TestRoundEscalation(unittest.TestCase):
    """Criterion 4, both paths of `models.escalate_on_failed_round`."""

    def test_escalation_off_by_default_keeps_every_round_on_the_same_model(self):
        config = copy.deepcopy(config_mod.DEFAULTS)
        self.assertIs(config["models"]["escalate_on_failed_round"], False)
        floor = config["models"]["runner"]
        for round_number in (1, 2, 3):
            self.assertEqual(
                dispatch.model_for(config, role="runner", round=round_number), floor
            )

    def test_escalation_on_moves_round_two_one_tier_up(self):
        config = copy.deepcopy(config_mod.DEFAULTS)
        config["models"]["escalate_on_failed_round"] = True
        floor = config["models"]["runner"]
        expected_round_two = config_mod.tier_up(config, floor)
        self.assertNotEqual(expected_round_two, floor)

        self.assertEqual(dispatch.model_for(config, role="runner", round=1), floor)
        self.assertEqual(
            dispatch.model_for(config, role="runner", round=2), expected_round_two
        )

    def test_escalation_never_raises_past_the_dearest_tier(self):
        config = copy.deepcopy(config_mod.DEFAULTS)
        config["models"]["escalate_on_failed_round"] = True
        model = dispatch.model_for(config, role="runner", round=10)
        self.assertEqual(model, DEFAULT_TIERS[-1])

    def test_an_explicit_unit_model_is_immune_to_escalation(self):
        config = copy.deepcopy(config_mod.DEFAULTS)
        config["models"]["escalate_on_failed_round"] = True
        unit = make_unit(model="haiku")
        self.assertEqual(
            dispatch.model_for(config, unit, round=3), "haiku"
        )


class TestNoModelNameInThisModule(unittest.TestCase):
    def test_grep_for_tier_names_in_dispatch_py(self):
        source = Path(dispatch.__file__).read_text(encoding="utf-8")
        for name in ("haiku", "sonnet", "opus"):
            self.assertNotIn(name, source, f"literal model name {name!r} in dispatch.py")


class DispatchLineFixture(Fixture):
    """A single subagent-tier unit, dispatched, so the wave brief's own
    dispatch line can be inspected."""

    slug = "score-visibility"

    def unit(self, name, *, budget_tokens=45000, owns=("src/a.py",)):
        check = [{"kind": "cmd", "run": OK}]
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": [], "owns": list(owns), "reads": [], "forbid": [],
            "budget_tokens": budget_tokens, "status": "pending", "verify": check,
        }
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        self.trust(check)
        return path

    def brief(self):
        self.cli("plan", self.slug, "--no-spec")
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)
        return out


class TestDispatchLinePrintsTheScore(DispatchLineFixture):
    """Criterion 2: a wrong tier must be diagnosable from the dispatch line
    alone, without re-running `complexity.score` by hand."""

    def test_the_score_and_every_contributing_input_are_on_the_line(self):
        self.unit("01-a", budget_tokens=45000, owns=["src/a.py"])
        out = self.brief()
        line = next(l for l in out.splitlines() if l.startswith("- `01-a`"))

        unit = next(u for u in plan_mod.load_units(self.layout, self.slug)
                    if u.name == "01-a")
        score, breakdown = complexity.score(self.config, unit)
        tier = complexity.tier_for(self.config, score)

        self.assertIn(str(score), line, line)
        self.assertIn(tier, line, line)
        for label, points in breakdown:
            self.assertIn(f"{label}={points}", line, line)


if __name__ == "__main__":
    unittest.main()
