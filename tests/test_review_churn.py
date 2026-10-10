"""Churn: mechanical, advisory, never critical, never gate-relevant."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import review as review_mod  # noqa: E402
from test_review import ReviewFixture  # noqa: E402


def section(text):
    match = re.search(r"## Churn\n(.*?)\n## Diff", text, re.S)
    return match.group(1)


def mock_budget(n):
    from unittest import mock
    return mock.patch.object(review_mod, "MAX_PACKAGE_BYTES", n)


class TestChurnFunction(unittest.TestCase):
    def test_empty_and_binary_style_input_do_not_raise(self):
        result = review_mod.churn("")
        self.assertEqual(result["files"], [])
        self.assertEqual(result["whitespace_only_total"], 0)

    def test_real_nul_bytes_diff_text_does_not_raise(self):
        diff = "--- before/b.bin\n+++ after/b.bin\n@@ -1,1 +1,1 @@\n-a\x00b\n+a\x00 b\n"
        result = review_mod.churn(diff)
        self.assertEqual(len(result["files"]), 1)

    def test_whole_file_add_and_delete_are_counted(self):
        diff = (
            "--- /dev/null\n+++ after/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
            "--- before/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x\n"
        )
        result = review_mod.churn(diff)
        by = {f["path"]: f for f in result["files"]}
        self.assertEqual(by["new.py"]["added"], 2)
        self.assertEqual(by["old.py"]["removed"], 1)
        self.assertEqual(result["whitespace_only_total"], 0)
        self.assertEqual(result["ratio"], 2.0)

    def test_content_line_starting_with_dashes_is_not_a_header(self):
        diff = ("--- before/a.md\n+++ after/a.md\n@@ -1,1 +1,1 @@\n"
                "--- old rule\n+++ new rule\n")
        result = review_mod.churn(diff)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["removed"], 1)


class TestChurnInThePackage(ReviewFixture):
    def prepare(self, before, after):
        unit = self.unit()
        self.write("src/a.py", before)
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        self.write("src/a.py", after)
        return unit

    def test_reindent_is_reported_and_names_the_file(self):
        unit = self.prepare("def a():\n  return 1\n", "def a():\n    return 1\n")
        path, _stats, _p = self.build(unit)
        text = section(path.read_text(encoding="utf-8"))
        self.assertIn("src/a.py", text)
        self.assertIn("important", text)
        self.assertIn("1 whitespace-only", text)

    def test_logic_change_reports_zero(self):
        unit = self.prepare("def a():\n    return 1\n", "def a():\n    return 2\n")
        path, _stats, _p = self.build(unit)
        text = section(path.read_text(encoding="utf-8"))
        self.assertEqual(len([ln for ln in text.splitlines() if ln.strip()]), 1)
        self.assertIn("No whitespace-only hunks", text)

    def test_churn_never_says_critical_and_leaves_the_gate_alone(self):
        unit = self.prepare("def a():\n  return 1\n", "def a():\n    return 1\n")
        path, stats, _p = self.build(unit)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("critical", section(text).lower())
        self.assertEqual(stats["out_of_scope"], 0)
        self.assertIn("None — every changed path is within the declared `owns`.", text)
        self.assertLess(text.index("## Scope violations"), text.index("## Churn"))
        self.assertLess(text.index("## Churn"), text.index("## Diff"))

    def test_gate_values_equal_a_build_without_churn(self):
        from unittest import mock
        unit = self.prepare("def a():\n  return 1\n", "def a():\n    return 1\n")
        _path, with_stats, _p = self.build(unit)
        empty = {"files": [], "whitespace_only_total": 0, "ratio": 0.0}
        with mock.patch.object(review_mod, "churn", return_value=empty):
            _path, without_stats, _p = self.build(unit)
        # `bytes` is the package size, which the Churn text itself changes.
        def strip(d):
            return {k: v for k, v in d.items() if k != "bytes"}

        self.assertEqual(strip(with_stats), strip(without_stats))

    def test_binary_file_goes_through_build_without_a_bogus_hunk(self):
        unit = self.unit(owns=("src/a.py", "src/b.bin"))
        self.write("src/a.py", "def a():\n    return 1\n")
        review_mod.capture_before(self.layout, self.config, unit, self.slug, self.root)
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "b.bin").write_bytes(b"a\x00b\x00\xff")
        path, _stats, _p = self.build(unit)
        text = section(path.read_text(encoding="utf-8"))
        self.assertNotIn("b.bin", text)
        self.assertIn("No whitespace-only hunks", text)

    def test_reindent_in_a_file_dropped_by_the_budget_is_still_reported(self):
        unit = self.prepare("def a():\n  return 1\n", "def a():\n    return 1\n")
        with mock_budget(10):
            path, _stats, _p = self.build(unit)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Not shown", text)
        self.assertIn("1 whitespace-only", section(text))

    def test_list_is_bounded_to_ten_files(self):
        diff = "".join(
            f"--- before/f{i}.py\n+++ after/f{i}.py\n@@ -1,1 +1,1 @@\n-a b\n+ab\n"
            for i in range(13)
        )
        lines = review_mod.render_churn(review_mod.churn(diff))
        self.assertEqual(sum(1 for ln in lines if ln.startswith("- ")), 10)
        self.assertEqual(lines[-1], "… 3 more")


if __name__ == "__main__":
    unittest.main()
