"""`model` and `role` on telemetry records, and the `by_role` breakdown they
make possible.

This is A4's substrate for ctx-0-8: dispatch and review-telemetry need to
answer "how is this role actually doing" before they can act on it, and that
question only exists once records carry `role` and `summarise()` groups on
it. Everything the existing suite already asserts about telemetry — silent
failure, size-bounded files, `median_chars` — must keep holding; these tests
add the role/model behaviour on top rather than replacing it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import telemetry  # noqa: E402
from support import Fixture  # noqa: E402


class TestRoleAndModelFields(Fixture):
    def test_record_persists_model_and_role_when_given(self):
        telemetry.record(self.layout, "dispatch", 12.0, model="haiku", role="reviewer")
        entries = telemetry.read(self.layout)
        self.assertEqual(entries[-1]["model"], "haiku")
        self.assertEqual(entries[-1]["role"], "reviewer")

    def test_record_omits_model_and_role_when_not_given(self):
        telemetry.record(self.layout, "dispatch", 12.0)
        entries = telemetry.read(self.layout)
        self.assertNotIn("model", entries[-1])
        self.assertNotIn("role", entries[-1])

    def test_record_omits_a_field_explicitly_passed_as_none(self):
        """Matches the existing behaviour for every other optional field —
        `role=None` is "no role", not "a role literally called null"."""
        telemetry.record(self.layout, "dispatch", 12.0, model=None, role=None)
        entries = telemetry.read(self.layout)
        self.assertNotIn("model", entries[-1])
        self.assertNotIn("role", entries[-1])


class TestByRoleBreakdown(Fixture):
    def test_summarise_groups_by_role_with_count_and_median(self):
        telemetry.record(self.layout, "dispatch", 10.0, role="reviewer")
        telemetry.record(self.layout, "dispatch", 20.0, role="reviewer")
        telemetry.record(self.layout, "dispatch", 100.0, role="planner")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        by_role = rows["dispatch"]["by_role"]
        self.assertEqual(by_role["reviewer"]["count"], 2)
        self.assertEqual(by_role["reviewer"]["median_ms"], 15.0)
        self.assertEqual(by_role["planner"]["count"], 1)
        self.assertEqual(by_role["planner"]["median_ms"], 100.0)

    def test_records_without_a_role_are_absent_from_by_role(self):
        telemetry.record(self.layout, "dispatch", 10.0)
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["dispatch"]["by_role"], {})

    def test_by_role_is_scoped_per_event_not_pooled_globally(self):
        telemetry.record(self.layout, "dispatch", 10.0, role="reviewer")
        telemetry.record(self.layout, "review", 999.0, role="reviewer")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["dispatch"]["by_role"]["reviewer"]["median_ms"], 10.0)
        self.assertEqual(rows["review"]["by_role"]["reviewer"]["median_ms"], 999.0)


class TestExistingReportingUnaffected(Fixture):
    def test_median_chars_reporting_is_unchanged(self):
        telemetry.record(self.layout, "SessionStart", 5.0, chars=100)
        telemetry.record(self.layout, "SessionStart", 5.0, chars=300)
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["SessionStart"]["median_chars"], 200)

    def test_median_chars_is_none_with_no_chars_recorded(self):
        telemetry.record(self.layout, "dispatch", 5.0, role="reviewer")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertIsNone(rows["dispatch"]["median_chars"])

    def test_event_level_count_and_median_still_span_every_role(self):
        telemetry.record(self.layout, "dispatch", 10.0, role="reviewer")
        telemetry.record(self.layout, "dispatch", 30.0, role="planner")
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["dispatch"]["count"], 2)
        self.assertEqual(rows["dispatch"]["median_ms"], 20.0)


class TestTelemetryNeverRaises(Fixture):
    def test_a_malformed_record_is_swallowed(self):
        telemetry.record(self.layout, "dispatch", "not-a-number", role="reviewer")
        telemetry.record(self.layout, "dispatch", 5.0, role=object())
        self.assertTrue(True, "no exception escaped")

    def test_summarise_tolerates_a_non_string_role_in_the_file(self):
        telemetry.record(self.layout, "dispatch", 5.0)
        target = telemetry.path_for(self.layout)
        with target.open("a", encoding="utf-8") as handle:
            handle.write('{"event": "dispatch", "ms": 5.0, "role": 7}\n')
        rows = {r["event"]: r for r in telemetry.summarise(self.layout)}
        self.assertEqual(rows["dispatch"]["by_role"], {})


if __name__ == "__main__":
    unittest.main()
