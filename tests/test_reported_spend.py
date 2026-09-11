"""Reported spend: `ctx telemetry --spend «tokens» --unit «name»`.

Spend arrives by report, not by observation — a CLI cannot see what a model
was billed — so this data is partial by construction. That is the hazard these
tests are mostly about. A partial number read as a full measurement would be
used to retune the complexity weights towards whichever units happened to get
reported, which is worse than having no number at all, so the labelling is
asserted as behaviour and not left as a comment: every surface that prints a
spend figure prints `telemetry.SPEND_NOTICE` with it, and a unit nobody
reported prints as *unreported* rather than as a zero.

The comparison against `complexity.score` is the reason the feature exists at
all: today a weight can only be checked against the reasoning that produced
it, and a reported cost next to a predicted score is the first thing able to
disagree with that reasoning.
"""

import contextlib
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import (  # noqa: E402
    frontmatter, miniyaml, plan as plan_mod, telemetry,
)
from support import OK, Fixture  # noqa: E402

CHECK = [{"kind": "cmd", "run": OK}]


class SpendFixture(Fixture):
    slug = "auth-rotation"

    def setUp(self):
        super().setUp()
        self.cli("spec", self.slug, "--intent", "Rotate keys without downtime.")

    def unit(self, name, *, owns=(), depends_on=(), budget=45000):
        plan_mod.units_dir(self.layout, self.slug).mkdir(parents=True, exist_ok=True)
        meta = {
            "ctx_schema": 1, "unit": name, "plan": self.slug, "tier": "subagent",
            "depends_on": list(depends_on), "owns": list(owns or [f"src/{name}.py"]),
            "reads": [], "forbid": [], "budget_tokens": budget,
            "status": "pending", "verify": list(CHECK),
        }
        path = plan_mod.units_dir(self.layout, self.slug) / f"{name}.md"
        frontmatter.Document(
            meta, f"## Objective\nDo {name}.\n\n## Acceptance criteria\n1. it works\n"
        ).write(path)
        self.trust(meta["verify"])
        return path

    def make_plan(self, *names):
        for name in names:
            self.unit(name)
        self.cli("plan", self.slug, "--no-spec")
        self.cli("plan-check", self.slug)

    def row_for(self, out, unit):
        """The `ctx telemetry` table line for one unit, as its cells."""
        for line in out.splitlines():
            if line.strip().startswith(unit + " ") or line.strip() == unit:
                return line.split()
        self.fail(f"no row for {unit} in:\n{out}")


class TestRecordingSpend(SpendFixture):
    def test_a_reported_spend_is_persisted_against_its_unit(self):
        code, out = self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            telemetry.spend_by_unit(self.layout),
            {"01-a": {"tokens": 12000, "reports": 1}},
        )

    def test_two_reports_for_one_unit_sum_and_are_counted(self):
        """A second round of a unit is a second cost. Summing without keeping
        the count would hide that 12,000 was two reports of 6,000."""
        self.cli("telemetry", "--spend", "6000", "--unit", "01-a")
        self.cli("telemetry", "--spend", "6000", "--unit", "01-a")
        self.assertEqual(
            telemetry.spend_by_unit(self.layout)["01-a"],
            {"tokens": 12000, "reports": 2},
        )

    def test_thousands_separators_are_accepted(self):
        """The number gets copied out of a usage report, commas and all."""
        self.assertEqual(
            self.cli("telemetry", "--spend", "12,000", "--unit", "01-a")[0], 0)
        self.assertEqual(telemetry.spend_by_unit(self.layout)["01-a"]["tokens"],
                         12000)

    def test_spend_without_a_unit_is_refused(self):
        code, out = self.cli("telemetry", "--spend", "12000")
        self.assertEqual(code, 2)
        self.assertIn("--unit", out)
        self.assertEqual(telemetry.spend_by_unit(self.layout), {})

    def test_a_spend_that_is_not_a_number_is_refused(self):
        code, out = self.cli("telemetry", "--spend", "lots", "--unit", "01-a")
        self.assertEqual(code, 2)
        self.assertEqual(telemetry.spend_by_unit(self.layout), {})

    def test_a_negative_spend_is_refused_rather_than_stored(self):
        """Not a smaller measurement — an absent one. Stored, it would drag a
        weight comparison towards a number that means nothing."""
        code, out = self.cli("telemetry", "--spend", "-5", "--unit", "01-a")
        self.assertEqual(code, 2)
        self.assertIn("negative", out)
        self.assertEqual(telemetry.spend_by_unit(self.layout), {})

    def test_recording_respects_the_telemetry_switch(self):
        data = miniyaml.loads(self.layout.config.read_text(encoding="utf-8"))
        data["telemetry"] = {"enabled": False}
        self.layout.config.write_text(miniyaml.dumps(data) + "\n", encoding="utf-8")
        code, out = self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        self.assertEqual(code, 0, out)
        self.assertIn("telemetry is off", out)
        self.assertEqual(telemetry.spend_by_unit(self.layout), {})

    def test_spend_records_do_not_pollute_the_hook_duration_table(self):
        """A reported token count is not a duration. Left in `summarise`, it
        would appear as an event with a 0.0ms median in a table about how slow
        hooks are."""
        self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        events = [row["event"] for row in telemetry.summarise(self.layout)]
        self.assertNotIn(telemetry.SPEND_EVENT, events)


class TestSpendUsesTheTelemetryLockOnce(SpendFixture):
    def test_record_spend_goes_through_the_one_locked_writer(self):
        """`lock.held` is not re-entrant, so the spend path must take the
        `telemetry` lock exactly once and must not be called from inside a
        span that already holds it. It gets that by going through `record`
        rather than opening the file itself."""
        depth = {"now": 0, "max": 0}
        names = []
        real = telemetry.lock.held

        @contextlib.contextmanager
        def traced(layout, name):
            names.append(name)
            depth["now"] += 1
            depth["max"] = max(depth["max"], depth["now"])
            try:
                with real(layout, name) as taken:
                    yield taken
            finally:
                depth["now"] -= 1

        telemetry.lock.held = traced
        try:
            telemetry.record_spend(self.layout, "01-a", 10)
        finally:
            telemetry.lock.held = real
        self.assertEqual(names, [telemetry.LOCK_NAME])
        self.assertEqual(depth["max"], 1, "the telemetry lock is not re-entrant")


class TestSpendNeverBreaksASession(SpendFixture):
    def test_a_read_only_runtime_directory_leaves_spend_returning(self):
        """The existing guarantee, extended to the new writer: telemetry is
        measurement and is never the reason a command fails."""
        runtime = self.layout.runtime
        runtime.mkdir(parents=True, exist_ok=True)
        before = stat.S_IMODE(runtime.stat().st_mode)
        runtime.chmod(0o500)
        try:
            probe = runtime / "probe"
            try:
                probe.write_text("x")
                probe.unlink()
                self.skipTest("this filesystem/user ignores a read-only directory")
            except OSError:
                pass
            self.assertIs(telemetry.record_spend(self.layout, "01-a", 10), False)
            code, out = self.cli("telemetry", "--spend", "10", "--unit", "01-a")
        finally:
            runtime.chmod(before)
        self.assertEqual(code, 0, "a lost data point is not a failed command")
        self.assertIn("not recorded", out)

    def test_reading_spend_survives_a_corrupt_record(self):
        self.cli("telemetry", "--spend", "10", "--unit", "01-a")
        with telemetry.path_for(self.layout).open("a", encoding="utf-8") as handle:
            handle.write('{"event": "spend", "unit": 7, "tokens": "many"}\n')
            handle.write("{not json\n")
        self.assertEqual(telemetry.spend_by_unit(self.layout),
                         {"01-a": {"tokens": 10, "reports": 1}})


class TestSpendIsShownAgainstThePredictedScore(SpendFixture):
    def test_the_table_pairs_reported_tokens_with_the_unit_score(self):
        """Criterion 6: the comparison is the entire reason this data is
        collected, so it has to be on one line, not in two commands."""
        self.make_plan("01-a", "02-b")
        self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        code, out = self.cli("telemetry")
        self.assertEqual(code, 0, out)
        self.assertIn("reported spend vs predicted score", out)
        cells = self.row_for(out, "01-a")
        self.assertEqual(cells[0], "01-a")
        self.assertRegex(cells[1], r"^\d+\.\d$", f"a predicted score: {cells}")
        self.assertEqual(cells[2], "12,000")

    def test_a_reported_unit_outside_the_active_plan_is_still_shown(self):
        """Its score is unknown, which is said with a dash. Dropping the row
        would hide a number somebody took the trouble to report."""
        self.make_plan("01-a")
        self.cli("telemetry", "--spend", "500", "--unit", "99-elsewhere")
        _code, out = self.cli("telemetry")
        cells = self.row_for(out, "99-elsewhere")
        self.assertEqual(cells[1], "-")
        self.assertEqual(cells[2], "500")

    def test_no_plan_and_no_spend_prints_no_spend_section_at_all(self):
        _code, out = self.cli("telemetry")
        self.assertNotIn("reported spend vs predicted", out)


class TestUnreportedIsNotZero(SpendFixture):
    def test_a_unit_nobody_reported_reads_as_unreported(self):
        self.make_plan("01-a", "02-b")
        self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        _code, out = self.cli("telemetry")
        self.assertEqual(self.row_for(out, "02-b")[2], "unreported")

    def test_a_reported_zero_and_an_absent_report_do_not_render_the_same(self):
        """Criterion 7, pinned. A zero somebody reported is a measurement; an
        absent one means nobody told us. Rendering both as `0` is how the
        complexity weights end up checked against a number that means
        nothing."""
        self.make_plan("01-a", "02-b")
        self.cli("telemetry", "--spend", "0", "--unit", "01-a")
        _code, out = self.cli("telemetry")
        reported_zero = self.row_for(out, "01-a")
        never_reported = self.row_for(out, "02-b")
        self.assertEqual(reported_zero[2], "0")
        self.assertEqual(reported_zero[3], "1", "one report, of zero")
        self.assertEqual(never_reported[2], "unreported")
        self.assertNotEqual(reported_zero[2], never_reported[2])

    def test_spend_by_unit_omits_unreported_units_rather_than_zeroing_them(self):
        """The absence is the data, at the module boundary too — a caller that
        gets `{"02-b": 0}` has already lost the distinction."""
        self.make_plan("01-a", "02-b")
        telemetry.record_spend(self.layout, "01-a", 0)
        totals = telemetry.spend_by_unit(self.layout)
        self.assertEqual(totals["01-a"]["tokens"], 0)
        self.assertNotIn("02-b", totals)


class TestEverySurfaceLabelsSpendAsPartial(SpendFixture):
    """Criterion 5. The label is the feature: without it the first person to
    tune a complexity weight against these numbers will treat a sample of the
    units that happened to get reported as a measurement of the wave."""

    def notice_present(self, out):
        return all(line in out for line in telemetry.SPEND_NOTICE)

    def test_the_notice_says_both_reported_and_incomplete(self):
        joined = " ".join(telemetry.SPEND_NOTICE).lower()
        self.assertIn("self-reported", joined)
        self.assertIn("incomplete", joined)
        self.assertIn("unreported, not zero", joined)

    def test_the_confirmation_of_a_recorded_spend_is_labelled(self):
        _code, out = self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        self.assertIn("12,000", out)
        self.assertTrue(self.notice_present(out), out)

    def test_the_table_is_labelled(self):
        self.make_plan("01-a")
        self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        _code, out = self.cli("telemetry")
        self.assertTrue(self.notice_present(out), out)

    def test_the_label_holds_when_only_some_units_in_a_wave_reported(self):
        """The partial case is the dangerous one, so it is asserted on its
        own: three units, one report, and the reader is still told that the
        other two are unreported rather than free."""
        self.make_plan("01-a", "02-b", "03-c")
        self.cli("telemetry", "--spend", "12000", "--unit", "02-b")
        _code, out = self.cli("telemetry")
        self.assertTrue(self.notice_present(out), out)
        self.assertEqual(self.row_for(out, "01-a")[2], "unreported")
        self.assertEqual(self.row_for(out, "03-c")[2], "unreported")

    def test_no_surface_prints_a_spend_figure_without_the_notice(self):
        """Generic, so a surface added later cannot quietly skip the label:
        every command that mentions reported spend at all is checked, in the
        state where it has a figure to show."""
        self.make_plan("01-a", "02-b")
        self.cli("telemetry", "--spend", "12000", "--unit", "01-a")
        self.run_hook("SessionStart")
        surfaces = {
            "telemetry": ("telemetry",),
            "telemetry --plan": ("telemetry", "--plan", self.slug),
            "record": ("telemetry", "--spend", "31000", "--unit", "02-b"),
            "budget": ("budget",),
            "status": ("status",),
        }
        for label, argv in surfaces.items():
            with self.subTest(surface=label):
                _code, out = self.cli(*argv)
                spend_lines = [
                    line for line in out.splitlines()
                    if ("reported spend" in line or "reported tokens" in line
                        or "unreported" in line)
                ]
                if not spend_lines:
                    continue
                self.assertTrue(
                    self.notice_present(out),
                    f"{label} shows spend without the partial-data notice:\n{out}",
                )

    def test_a_reported_figure_never_appears_in_the_duration_table(self):
        """The one place a spend number must not leak: an event table whose
        other rows are milliseconds, where nothing would label it."""
        self.run_hook("SessionStart")
        self.cli("telemetry", "--spend", "12345", "--unit", "01-a")
        _code, out = self.cli("telemetry")
        head = out.split("## reported spend")[0]
        self.assertNotIn("12,345", head)
        self.assertNotIn("spend", head.lower())


if __name__ == "__main__":
    unittest.main()
