"""A kind registered into `verify.KIND_TABLE` must be judged everywhere, not
just where `verify` itself looks.

`KIND_TABLE` exists to make one claim: adding a verify kind is one dict entry,
and every consumer sees it. `verify.KINDS`, `MECHANICAL`, `JUDGED` and `COST`
are derived through a module `__getattr__` so that claim holds for the module
that owns the table — `tests/test_verify_kinds_table.py` pins that end of it.

`complexity.py` was the consumer that broke it. It did

    _JUDGED_KINDS = tuple(verify.JUDGED)

at import time, which snapshots the two kinds that happened to exist when the
module was first imported. A kind registered afterwards — by a plugin, by a
test, by any registration that is not a literal edit to `KIND_TABLE` before
first import — was scored as though it were mechanical: the unit declaring it
lost the `judged_verify` weight of 2.0 and could be dispatched a tier cheaper
than its gate deserves. The table was right and the consumer was stale, which
is exactly the failure `KIND_TABLE` was built to end.

Every assertion here is paired with the control that makes it mean something:
the frozen-tuple behaviour is *reproduced* and shown to score the same unit
differently, so "the fix works" is a measured difference rather than a number
that happens to be 2.0.
"""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    complexity, config as config_mod, frontmatter, plan as plan_mod, verify,
)
from support import OK, Fixture  # noqa: E402


PACKAGE = Path(__file__).resolve().parent.parent / "ctx"

# The kinds derived from `KIND_TABLE`. Freezing any of them at import time is
# the same bug wearing a different name.
DERIVED = ("KINDS", "MECHANICAL", "JUDGED", "COST")

JUDGED_WEIGHT = config_mod.DEFAULTS["complexity"]["weights"]["judged_verify"]


class FrozenVerify:
    """`verify`, as `complexity` used to see it.

    Not a mock of the fix — a reproduction of the bug. `JUDGED` here is the
    tuple as it stood at some earlier moment, which is precisely what a
    module-level `tuple(verify.JUDGED)` stored.
    """

    def __init__(self, judged):
        self.JUDGED = tuple(judged)


class LateKind(Fixture):
    """A plan with one unit, and a judged kind registered after import."""

    slug = "late-kind"
    NAME = "oracle"
    _counter = 0

    def register_judged(self):
        """One dict entry, the way the table says a kind is added."""
        self.assertNotIn(self.NAME, verify.KIND_TABLE)
        verify.KIND_TABLE[self.NAME] = verify.Kind(
            7, lambda check: "an oracle says so",
            lambda check, ctx: verify.Result(
                self.NAME, "an oracle says so", verify.PASS, "ok"),
            judged=True,
        )
        self.addCleanup(verify.KIND_TABLE.pop, self.NAME, None)

    def unit(self, checks, *, owns=(), budget=0):
        LateKind._counter += 1
        name = f"{LateKind._counter:02d}-unit"
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        frontmatter.Document(
            {
                "ctx_schema": 1, "unit": name, "plan": self.slug,
                "tier": "subagent", "depends_on": [], "owns": list(owns),
                "reads": [],
                "forbid": [], "budget_tokens": budget, "status": "pending",
                "verify": list(checks), "kind": "",
            },
            "## Objective\nDo it.\n",
        ).write(path)
        return plan_mod.find_unit(self.layout, self.slug, name)


# --------------------------------------------------------------------------- #
# the gap, and the positive control for it
# --------------------------------------------------------------------------- #

class TestALateKindIsScoredAsJudged(LateKind):

    def test_a_kind_registered_after_import_carries_the_judged_weight(self):
        self.register_judged()
        unit = self.unit([{"kind": self.NAME}])
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(breakdown, [(self.NAME, JUDGED_WEIGHT)])
        self.assertEqual(value, JUDGED_WEIGHT)

    def test_the_frozen_tuple_would_have_missed_it(self):
        """The positive control. The same unit, scored against the code as it
        was: no judged term, and a score two points lower."""
        before = tuple(verify.JUDGED)
        self.register_judged()
        unit = self.unit([{"kind": self.NAME}])

        live, live_breakdown = complexity.score(self.config, unit)
        frozen_module = FrozenVerify(before)
        original = complexity.verify
        complexity.verify = frozen_module
        try:
            frozen, frozen_breakdown = complexity.score(self.config, unit)
        finally:
            complexity.verify = original

        self.assertEqual(frozen_breakdown, [],
                         "the reproduction did not reproduce the bug")
        self.assertEqual(frozen, 0.0)
        self.assertEqual(live_breakdown, [(self.NAME, JUDGED_WEIGHT)])
        self.assertEqual(live - frozen, JUDGED_WEIGHT)

    def test_a_late_mechanical_kind_still_does_not_carry_the_weight(self):
        """The other half: reading the table live must not make everything
        judged. A kind registered with `judged=False` is still mechanical."""
        verify.KIND_TABLE["abacus"] = verify.Kind(
            1, lambda check: "counted",
            lambda check, ctx: verify.Result("abacus", "counted", verify.PASS, ""),
            judged=False,
        )
        self.addCleanup(verify.KIND_TABLE.pop, "abacus", None)
        unit = self.unit([{"kind": "abacus"}])
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual((value, breakdown), (0.0, []))

    def test_it_fires_once_however_many_judged_kinds_are_present(self):
        """`judged_verify` is flat, and the label names every kind that fired.
        A late kind joins that label rather than being charged separately."""
        self.register_judged()
        unit = self.unit([{"kind": "rubric", "about": "x"}, {"kind": self.NAME}])
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(breakdown, [(f"{self.NAME}+rubric", JUDGED_WEIGHT)])
        self.assertEqual(value, JUDGED_WEIGHT)

    def test_the_tier_moves_with_it(self):
        """The score exists to pick a dispatch tier, so the gap is only closed
        if the tier changes too."""
        self.register_judged()
        # Two paths and a 15k budget put a unit at 2.0 — under the 3.0 that
        # crosses into standard dispatch. The judged kind is the only
        # difference between the pair, and it is what carries them over.
        shape = {"owns": ["src/a.py", "src/b.py"], "budget": 15000}
        mechanical = self.unit([{"kind": "cmd", "run": OK}], **shape)
        judged = self.unit([{"kind": self.NAME}], **shape)
        cheap, _ = complexity.score(self.config, mechanical)
        dear, _ = complexity.score(self.config, judged)
        self.assertEqual(dear - cheap, JUDGED_WEIGHT)
        self.assertEqual(complexity.tier_for(self.config, cheap), "light")
        self.assertEqual(complexity.tier_for(self.config, dear), "standard")

    def test_the_breakdown_still_sums_to_the_score(self):
        """The reconciliation invariant, with a late kind in the breakdown."""
        self.register_judged()
        unit = self.unit([{"kind": self.NAME}])
        value, breakdown = complexity.score(self.config, unit)
        self.assertEqual(value, sum(points for _, points in breakdown))


# --------------------------------------------------------------------------- #
# and nowhere else in the package does the same thing
# --------------------------------------------------------------------------- #

def frozen_derivations(path):
    """Module-level bindings whose value reads a derived `verify` name.

    Function-level reads are fine — those happen at call time, which is the
    whole point. What this looks for is the shape that runs once at import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Attribute)
                    and inner.attr in DERIVED
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id.startswith("verify")):
                offenders.append(f"{path.name}:{node.lineno} reads verify.{inner.attr}")
    return offenders


class TestNoOtherModuleFreezesTheTable(unittest.TestCase):
    """`complexity.py` was the only one. This keeps it that way."""

    def test_no_module_level_binding_reads_a_derived_verify_name(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            offenders += frozen_derivations(path)
        self.assertEqual(
            offenders, [],
            "a derived verify name is being frozen at import time — read it at "
            "the call site instead, or a kind registered later is invisible")

    def test_the_detector_would_catch_the_bug_it_was_written_for(self):
        """A guard that cannot fail is not a guard. This is the exact line
        that used to sit at `complexity.py:30`."""
        source = "from . import verify\n_JUDGED_KINDS = tuple(verify.JUDGED)\n"
        path = Path(self._make(source))
        self.assertEqual(len(frozen_derivations(path)), 1)

    def _make(self, source):
        import tempfile
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(source)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_a_call_site_read_is_not_flagged(self):
        """The fix itself must not trip the detector, or the detector would be
        weakened until it caught nothing."""
        source = ("from . import verify\n"
                  "def score(unit):\n"
                  "    return [c for c in unit if c in verify.JUDGED]\n")
        self.assertEqual(frozen_derivations(Path(self._make(source))), [])


if __name__ == "__main__":
    unittest.main()
