"""A read-only pass over a plan must not fingerprint a unit's mid-flight work.

`review._concurrent_siblings` records the calling unit's "own work" baseline
as a side effect of answering "what may this unit's review treat as
declared" — correct when the caller is `verify.gate_before_done`, deciding
that exact unit's `done` right now, and wrong when the caller is `ctx verify
--plan` or `ctx ci` asking the same question about *every* in-flight unit of
a plan just to report a verdict. Recording there would freeze a unit's
current dirty state as though its gate had just run, before it actually has —
corrupting the baseline a later, real gate decision for that unit (or a
sibling excusing it) would read.

`wave_scope`/`_concurrent_siblings`/`gate_check` all take `record=False` for
exactly this: the two read-only callers pass it, the one real decision path
(`gate_before_done`) does not.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, review, verify  # noqa: E402
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]
BODY = "## Objective\nDo it.\n\n## Acceptance criteria\n1. it works\n"


class ReadOnlyRecordFixture(Fixture):
    slug = "quiet-check"

    def unit(self, name="01-a", wave=1):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
             "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
             "forbid": [], "budget_tokens": 1000, "status": "running",
             "wave": wave, "verify": list(CHECK)},
            BODY,
        ).write(directory / f"{name}.md")
        self.trust(list(CHECK))
        self.cli("plan", self.slug, "--no-spec")
        return plan_mod.find_unit(self.layout, self.slug, name)

    def record_path(self, name="01-a"):
        return review.owns_record_path(self.layout, self.slug, name)


class TestReadOnlyCallersDoNotRecord(ReadOnlyRecordFixture):

    def test_wave_scope_with_record_false_writes_nothing(self):
        unit = self.unit()
        self.write("src/01-a.py", "# dirty, uncommitted\n")
        review.wave_scope(self.layout, self.slug, unit, record=False)
        self.assertFalse(self.record_path().exists())

    def test_wave_scope_default_still_records(self):
        """The control: the real decision path's behavior is unchanged."""
        unit = self.unit()
        self.write("src/01-a.py", "# dirty, uncommitted\n")
        review.wave_scope(self.layout, self.slug, unit)
        self.assertTrue(self.record_path().exists())

    def test_gate_check_with_record_false_writes_nothing(self):
        unit = self.unit()
        self.write("src/01-a.py", "# dirty, uncommitted\n")
        verify.gate_check(self.layout, self.config, self.slug, unit, record=False)
        self.assertFalse(self.record_path().exists())

    def test_gate_check_default_still_records(self):
        unit = self.unit()
        self.write("src/01-a.py", "# dirty, uncommitted\n")
        verify.gate_check(self.layout, self.config, self.slug, unit)
        self.assertTrue(self.record_path().exists())

    def test_verify_plan_does_not_record_an_in_flight_unit(self):
        """`ctx verify --plan`'s own entry point, not the module function
        directly — this is the caller the finding was about."""
        self.unit()
        self.write("src/01-a.py", "# dirty, uncommitted\n")
        code, out = self.cli("verify", "--plan", self.slug)
        self.assertFalse(self.record_path().exists(), out)


if __name__ == "__main__":
    unittest.main()
