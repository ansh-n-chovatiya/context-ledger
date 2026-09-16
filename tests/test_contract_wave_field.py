"""`wave:` is part of the promise, so editing it after dispatch is refused.

`contract.FIELDS` sealed `verify`, `owns`, `reads`, `forbid`, `depends_on`,
`verified` and the acceptance criteria — everything that says what the unit must
achieve and where it may write. It did not seal `wave`, on the reading that a
wave number is scheduling, like `tier` or `budget_tokens`, rather than part of
the contract.

It is not. `review.wave_scope` reads a unit's `wave:` verbatim and excuses every
path owned by another unit in the same wave, because those are the units that
may be writing to this shared tree right now. So `wave` is the field that
decides *which changed paths this unit has to answer for* — and a runner holds
`Write` over its own unit file. Changing `wave: 1` to `wave: 2` after dispatch
re-labels a stray write as some other unit's declared, concurrent work: the
review package then prints "Scope violations: None" and drops the diff out of
the package with it, because a sibling's path is deliberately left to that
sibling's own review.

The fix is one entry in `FIELDS`, which is the point of the file: the
changed-field reporting `compare` already does picks `wave` up with no new code
path, so the refusal a forged `verify:` gets is exactly the refusal a forged
`wave:` gets. These tests pin both halves — that the digest moves when the
schedule moves, and that the *gate* is what refuses, in the same words, naming
`wave`.

The backward-compatibility case is pinned too: a seal written before `wave` was
in `FIELDS` has no `wave` digest to compare against, and `compare` skips a field
the baseline never recorded. Refusing there would have bricked every in-flight
plan on upgrade — the same trade `baseline()` already makes.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import contract, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402

CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"
UNITS = ("01-api", "02-store")


class WaveContractFixture(Fixture):
    """Two concurrent units, dispatched through `ctx start` so that a real seal
    exists to compare against."""

    slug = "auth"

    def plan(self, names=UNITS, wave=1):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        checks = [{"kind": "cmd", "run": OK}]
        for name in names:
            frontmatter.Document(
                {
                    "ctx_schema": 1, "unit": name, "plan": self.slug,
                    "tier": "subagent", "depends_on": [],
                    "owns": [f"src/{name}.py"], "reads": [], "forbid": [],
                    "budget_tokens": 1000, "status": "pending", "wave": wave,
                    "verify": list(checks),
                },
                CRITERIA,
            ).write(directory / f"{name}.md")
            self.trust(checks)
        self.cli("plan", self.slug, "--no-spec")

    def dispatch(self):
        code, out = self.cli("start")
        self.assertEqual(code, 0, out)

    def edit(self, name, **changes):
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        doc = frontmatter.read(path)
        doc.meta.update(changes)
        doc.write(path)
        return plan_mod.find_unit(self.layout, self.slug, name)

    def unit(self, name):
        return plan_mod.find_unit(self.layout, self.slug, name)

    def done(self, name):
        return self.cli("unit", name, "--status", "done", "--plan", self.slug)


# --------------------------------------------------------------------------- #
# the field itself
# --------------------------------------------------------------------------- #

class TestWaveIsSealed(WaveContractFixture):

    def test_wave_is_one_of_the_sealed_fields(self):
        self.assertIn("wave", contract.FIELDS)
        self.assertIn("wave", contract.LABELS)
        self.assertIn("wave", contract.LABELS["wave"])

    def test_every_sealed_field_is_digested(self):
        """`combine` walks `FIELDS`, so a name in `FIELDS` that `field_digests`
        does not produce would hash as the empty string — sealed in name only."""
        self.plan()
        digests = contract.field_digests(self.unit("01-api").doc)
        self.assertEqual(sorted(digests), sorted(contract.FIELDS))
        self.assertTrue(digests["wave"])

    def test_the_digest_moves_when_the_scheduled_wave_moves(self):
        self.plan()
        before = contract.digest(self.unit("01-api"))
        self.assertNotEqual(before, contract.digest(self.edit("01-api", wave=2)))

    def test_the_digest_does_not_move_for_a_wave_that_schedules_the_same(self):
        """`Unit.wave` reads the value through `int`, so `1` and `"1"` are one
        wave. A digest that told them apart would refuse a unit for a YAML
        quoting change, which is how people learn to pass `--force`."""
        self.plan()
        before = contract.digest(self.unit("01-api"))
        self.assertEqual(before, contract.digest(self.edit("01-api", wave="1")))

    def test_the_two_digest_routes_still_agree_about_wave(self):
        """`digest` parses the document, `digest_text` parses the bytes. The
        gate seals one way and checks the other."""
        self.plan()
        unit = self.edit("01-api", wave=3)
        self.assertEqual(
            contract.digest(unit),
            contract.digest_text(unit.path.read_text(encoding="utf-8")),
        )


# --------------------------------------------------------------------------- #
# the refusal
# --------------------------------------------------------------------------- #

class TestEditingTheWaveAfterDispatchIsRefused(WaveContractFixture):

    def test_compare_names_wave(self):
        self.plan()
        self.dispatch()
        intact, changed = contract.compare(
            self.layout, self.slug, self.edit("01-api", wave=2))
        self.assertFalse(intact)
        self.assertTrue(any("wave" in item for item in changed), changed)

    def test_the_done_gate_refuses_and_names_wave(self):
        """The whole point: not a digest that moved, a unit that cannot be
        marked done. `02-store` is still running and owns `src/02-store.py`, so
        moving `01-api` into wave 2 is exactly the edit that would stop
        `02-store` from excusing anything — and it is now refused."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.edit("01-api", wave=2)

        code, out = self.done("01-api")
        self.assertEqual(code, 1, out)
        self.assertIn("contract changed after it was dispatched", out)
        self.assertIn("wave", out)
        self.assertEqual(self.unit("01-api").status, "running")

    def test_it_is_the_same_refusal_an_edited_verify_gets(self):
        """One code path, not a special case for `wave`. The two refusals
        differ only in the field they name."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        self.write("src/02-store.py", "# 02-store\n")

        self.edit("01-api", wave=2)
        self.edit("02-store", verify=[{"kind": "cmd", "run": self.py("print(0)")}])
        forged_wave = self.done("01-api")[1]
        forged_verify = self.done("02-store")[1]

        for out in (forged_wave, forged_verify):
            self.assertIn("contract changed after it was dispatched", out)
            self.assertIn("A unit does not get to rewrite the promise", out)
        self.assertIn("  changed: wave", forged_wave)
        self.assertIn("  changed: verify", forged_verify)

    def test_an_untouched_wave_is_not_a_refusal(self):
        """The control. Everything else here asserts a refusal, so a gate that
        refused every unit would satisfy the lot."""
        self.plan()
        self.dispatch()
        self.write("src/01-api.py", "# 01-api\n")
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.unit("01-api").status, "done")

    def test_a_seal_that_predates_wave_still_passes(self):
        """A plan dispatched by an older ctx has no `wave` digest in its seal.
        `compare` skips a field the baseline never recorded, so upgrading ctx
        mid-plan does not brick the plan."""
        self.plan()
        self.dispatch()
        path = contract.seal_path(self.layout, self.slug, "01-api")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["fields"].pop("wave")
        path.write_text(json.dumps(data, indent=2, sort_keys=True),
                        encoding="utf-8")

        self.write("src/01-api.py", "# 01-api\n")
        self.edit("01-api", wave=2)
        code, out = self.done("01-api")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
