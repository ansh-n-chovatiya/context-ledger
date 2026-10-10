"""`ctx/reduce.py` — shrinking command output without losing the failure.

The old cut (`ctx.verify.truncate`, first 40 + last 20 lines) drops the middle
of a long test run, which is exactly where a runner's failing assertion lives.
Each test that claims the reducer keeps something the old cut loses also
asserts the old cut loses it — the positive control — so a test fixture that
happens to put the failure in the first 40 lines cannot pass vacuously.
"""
import ast
import random
import re
import time
import unittest
from pathlib import Path

from ctx import reduce as R
from ctx.reduce import reduce_output, strip_ansi, truncate_lines


def old_truncate(text, head, tail):
    """A frozen copy of `ctx.verify.truncate` as it stood before reduce.py."""
    lines = (text or "").strip().splitlines()
    if len(lines) <= head + tail:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head] + [f"… {omitted} lines omitted …"] + (lines[-tail:] if tail else [])
    )


ASSERTION = "AssertionError: expected 41 to equal 42 in the ledger total"


def unittest_output():
    """~600 lines: verbose test lines, one FAIL block near line 300, then captured stdout."""
    lines = [f"test_case_{i:03d} (tests.test_mod.T) ... ok" for i in range(290)]
    lines += [
        "test_ledger_total (tests.test_mod.T) ... FAIL",
        "",
        "=" * 70,
        "FAIL: test_ledger_total (tests.test_mod.T)",
        "-" * 70,
        "Traceback (most recent call last):",
        '  File "tests/test_mod.py", line 12, in test_ledger_total',
        "    self.assertEqual(total, 42)",
        ASSERTION,
        "",
        "Stdout:",
    ]
    lines += [f"captured log line {i}" for i in range(290)]
    lines += ["", "-" * 70, "Ran 291 tests in 1.234s", "", "FAILED (failures=1)"]
    return "\n".join(lines) + "\n"


def pytest_output():
    lines = ["============================= test session starts =============================="]
    lines += [f"tests/test_mod.py::test_case_{i:03d} PASSED" for i in range(280)]
    lines += [
        "tests/test_mod.py::test_ledger_total FAILED",
        "",
        "=================================== FAILURES ===================================",
        "______________________________ test_ledger_total _______________________________",
        "",
        "    def test_ledger_total():",
        ">       assert total() == 42",
        "E       " + ASSERTION,
        "",
        "tests/test_mod.py:12: AssertionError",
        "----------------------------- Captured stdout call -----------------------------",
    ]
    lines += [f"captured log line {i}" for i in range(290)]
    lines += [
        "=========================== short test summary info ============================",
        "FAILED tests/test_mod.py::test_ledger_total - AssertionError: expected 41",
        "======================== 1 failed, 280 passed in 3.21s =========================",
    ]
    return "\n".join(lines)


def cargo_output():
    lines = ["running 300 tests"]
    lines += [f"test tests::case_{i:03d} ... ok" for i in range(290)]
    lines += [
        "test tests::ledger_total ... FAILED",
        "",
        "failures:",
        "",
        "---- tests::ledger_total stdout ----",
        "thread 'tests::ledger_total' panicked at src/lib.rs:12:5:",
        "assertion `left == right` failed: " + ASSERTION,
        "note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace",
        "",
    ]
    lines += [f"noise {i}" for i in range(100)]
    lines += [
        "failures:",
        "    tests::ledger_total",
        "",
        "test result: FAILED. 290 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out",
    ]
    return "\n".join(lines)


def jest_output():
    lines = [f"  console.log line {i}" for i in range(290)]
    lines += [
        "FAIL src/ledger.test.js",
        "  ● ledger › total",
        "",
        "    expect(received).toBe(expected)",
        "    " + ASSERTION,
        "",
        "      10 | test('total', () => {",
    ]
    lines += [f"  more console output {i}" for i in range(290)]
    lines += [
        "Test Suites: 1 failed, 12 passed, 13 total",
        "Tests:       1 failed, 290 passed, 291 total",
    ]
    return "\n".join(lines)


class RunnerExtraction(unittest.TestCase):
    def _check(self, text, summary):
        self.assertGreater(len(text.splitlines()), 400)
        old = old_truncate(text, 40, 20)
        self.assertNotIn(ASSERTION, old, "positive control: the old cut must lose it")
        new = reduce_output(text)
        self.assertIn(ASSERTION, new)
        self.assertIn(summary, new)
        self.assertIn("lines omitted", new)
        self.assertLess(len(new), len(text))
        return new

    def test_unittest(self):
        text = unittest_output()
        self.assertTrue(500 < len(text.splitlines()) < 700)
        idx = text.splitlines().index(ASSERTION)
        self.assertTrue(250 < idx < 350, idx)
        new = self._check(text, "Ran 291 tests in 1.234s")
        self.assertIn("FAILED (failures=1)", new)
        self.assertEqual(new.splitlines()[-1], "FAILED (failures=1)")

    def test_pytest(self):
        new = self._check(pytest_output(), "1 failed, 280 passed in 3.21s")
        self.assertIn("FAILED tests/test_mod.py::test_ledger_total", new)
        self.assertTrue(new.splitlines()[-1].startswith("====="))

    def test_cargo(self):
        new = self._check(cargo_output(), "test result: FAILED.")
        self.assertIn("panicked at", new)

    def test_jest(self):
        new = self._check(jest_output(), "Tests:       1 failed")
        self.assertIn("● ledger › total", new)

    def test_passing_run_falls_back(self):
        text = "\n".join([f"test_{i} ... ok" for i in range(200)] + ["-" * 70, "Ran 200 tests in 0.1s", "", "OK"])
        self.assertEqual(reduce_output(text), truncate_lines(text, 40, 20))

    def test_summary_survives_char_cap(self):
        lines = [f"line {i}" for i in range(10)]
        lines += ["=" * 70, "FAIL: test_x (m.T)", "-" * 70]
        lines += [f"trace line {i} " + "x" * 150 for i in range(100)]
        lines += ["-" * 70, "Ran 1 test in 0.1s", "", "FAILED (failures=1)"]
        new = reduce_output("\n".join(lines), char_cap=1500)
        self.assertLessEqual(len(new), 1500 + 120)
        self.assertEqual(new.splitlines()[-1], "FAILED (failures=1)")
        self.assertIn("chars omitted", new)


class TruncateLinesIdentity(unittest.TestCase):
    CASES = [
        "",
        "   ",
        "one line",
        "a\nb\nc",
        "\n".join(str(i) for i in range(60)),  # exactly head+tail for (40, 20)
        "\n".join(str(i) for i in range(61)),
        "\n".join(f"line {i}" for i in range(1000)),
        "  leading and trailing  \n\n  x  \n\t\n",
        "trailing whitespace   \nnext\t\t\n   ",
        "crlf\r\nlines\r\n",
        "form\x0cfeed\x1cgroup",
    ]
    PARAMS = [(40, 20), (3, 2), (5, 0), (0, 5), (0, 0), (1, 1)]

    def test_table(self):
        for text in self.CASES:
            for head, tail in self.PARAMS:
                with self.subTest(text=text[:20], head=head, tail=tail):
                    self.assertEqual(truncate_lines(text, head, tail), old_truncate(text, head, tail))

    def test_none(self):
        self.assertEqual(truncate_lines(None, 40, 20), old_truncate(None, 40, 20))


class PlainTextIsUnchanged(unittest.TestCase):
    def test_property(self):
        rng = random.Random(1234)
        words = ["alpha", "beta", "gamma", "delta", "ledger", "unit", "wave", "plan"]
        for n in (0, 1, 10, 59, 60, 61, 100, 300):
            for _ in range(5):
                lines = [
                    f"{k} " + " ".join(rng.choice(words) for _ in range(rng.randint(0, 8)))
                    for k in range(n)
                ]
                text = "\n".join(lines)
                with self.subTest(n=n):
                    self.assertEqual(reduce_output(text), truncate_lines(text, 40, 20))


class Cleanup(unittest.TestCase):
    def test_ansi_removed(self):
        text = "\x1b[31mred\x1b[0m \x1b[1;32mbold green\x1b[m \x1b]0;title\x07done \x1b]8;;http://x\x1b\\link"
        self.assertEqual(strip_ansi(text), "red bold green done link")

    def test_cr_redraws(self):
        self.assertEqual(strip_ansi("a\rb\rc\n"), "c\n")
        self.assertEqual(strip_ansi("10%\r50%\r100%\r\nnext"), "100%\nnext")
        self.assertEqual(strip_ansi("x\r\ny\r\n"), "x\ny\n")

    def test_unterminated_osc_stops_at_its_line(self):
        lines = [f"test_{i} ... ok" for i in range(10)]
        lines[5] = "progress \x1b]0;half-written title"
        lines += ["=" * 70, "FAIL: test_x (m.T)", "-" * 70, ASSERTION,
                  "-" * 70, "Ran 11 tests in 0.1s", "", "FAILED (failures=1)"]
        text = "\n".join(lines)
        # positive control: the old OSC pattern ran across newlines to the end
        old_osc = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
        self.assertNotIn(ASSERTION, old_osc.sub("", text))
        self.assertIn(ASSERTION, strip_ansi(text))
        self.assertIn("progress ", strip_ansi(text))
        out = reduce_output(text)
        self.assertIn(ASSERTION, out)
        self.assertIn("FAILED (failures=1)", out)

    def test_lone_esc_removed(self):
        for text in ("a\x1b", "x\x1b[\ny", "x\x1b\x01z", "\x1b[", "\x1b"):
            with self.subTest(text=text):
                self.assertNotIn("\x1b", strip_ansi(text))
                self.assertNotIn("\x1b", reduce_output(text))
        self.assertEqual(strip_ansi("a\x1b"), "a")

    def test_ansi_in_reduce(self):
        out = reduce_output("\x1b[32mok\x1b[0m\nprogress 1%\rprogress 100%\n")
        self.assertEqual(out, "ok\nprogress 100%")

    def test_run_collapse(self):
        out = reduce_output("start\n" + "same line\n" * 200 + "end")
        self.assertEqual(out, "start\nsame line (×200)\nend")

    def test_non_adjacent_not_merged(self):
        out = reduce_output("a\nb\na\nb\na")
        self.assertEqual(out, "a\nb\na\nb\na")
        self.assertNotIn("×", out)


class Caps(unittest.TestCase):
    def test_long_line(self):
        out = reduce_output("y" * 50000, line_cap=2000)
        line = out.splitlines()[0]
        self.assertTrue(line.startswith("y" * 2000))
        self.assertIn("…[cut 48000 chars]", line)
        self.assertLessEqual(len(line), 2000 + len(" …[cut 48000 chars]"))

    def test_char_cap(self):
        rng = random.Random(7)
        for n in (100, 1000, 5000):
            text = "\n".join(f"row {k} " + "abc xyz " * rng.randint(0, 400) for k in range(n))
            out = reduce_output(text)
            self.assertLessEqual(len(out), 6000 + 120)
            for cap in (0, 1, 50, 500):
                self.assertLessEqual(len(reduce_output(text, char_cap=cap)), cap + 120)


class NeverRaises(unittest.TestCase):
    # Each input runs through the unguarded pipeline `_reduce` too: the guard in
    # `reduce_output` would otherwise hide a pipeline that raised on everything.
    def test_edges(self):
        for text in ("", " ", "\n\n\n", "\t \r\n", None, "\x1b", "\x1b[", "\x1b]unterminated", "\r\r\r", "×" * 10):
            self.assertIsInstance(R._reduce(text), str)
            self.assertIsInstance(reduce_output(text), str)

    def test_fuzz(self):
        rng = random.Random(20261010)
        alphabet = (
            [chr(c) for c in range(0, 128)]
            + ["\x1b[", "\x1b]", "\r", "\n", "●", "⎯", "×", "�", "\x9b"]
            + ["FAIL: x\n", "Ran 3 tests in 0s\n", "=" * 70 + "\n", "=== FAILURES ===\n",
               "____ t ____\n", "---- a stdout ----\n", "test result: FAILED\n",
               "  ● t\n", "Tests: 1 failed\n", "failures:\n", "FAILED (errors=1)\n"]
        )
        for _ in range(200):
            size = rng.choice((0, 1, 10, 100, 1000, 20000))
            text = "".join(rng.choice(alphabet) for _ in range(size))
            head, tail = rng.randint(0, 50), rng.randint(0, 30)
            raw = R._reduce(text, head=head, tail=tail)
            out = reduce_output(text, head=head, tail=tail)
            self.assertEqual(out, raw)
            self.assertIsInstance(out, str)
            self.assertLessEqual(len(out), 6000 + 120)


class Performance(unittest.TestCase):
    def test_linear(self):
        rng = random.Random(3)
        lines = [f"line {i} " + "x" * rng.randint(0, 80) for i in range(200000)]
        lines[100000] = "=" * 70
        lines[100001] = "FAIL: test_big (m.T)"
        lines += ["Ran 1 test in 9s", "FAILED (failures=1)"]
        text = "\n".join(lines)
        started = time.perf_counter()
        out = reduce_output(text)
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertIn("FAIL: test_big", out)

    def test_pathological_escapes(self):
        text = "\x1b]" * 50000 + "\x1b[" * 50000 + "\r" * 50000
        started = time.perf_counter()
        reduce_output(text)
        self.assertLess(time.perf_counter() - started, 2.0)


    def _fast(self, fn, line):
        started = time.perf_counter()
        fn(line)
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_adversarial_lines(self):
        kw = "= " + "failed " * 30000
        self.assertFalse(R._py_final(kw))
        self._fast(R._py_final, kw)
        self.assertTrue(R._py_final("=== 1 failed, 2 passed in 1s ==="))
        self.assertFalse(R._py_final("=== test session starts ==="))
        for line in (
            kw,
            "= " + "= " * 30000 + "x",
            "_ " + "_ " * 30000 + "x",
            "FAILED (" + ") " * 30000 + "x",
            "OK (" + ") " * 30000 + "x",
            "---- " + "a stdout ---- " * 30000 + "x",
            "  " + "=" * 60000 + "x",
            "Tests " * 30000,
        ):
            with self.subTest(line=line[:20]):
                text = f"=== FAILURES ===\n___ t ___\nE assert 0\n{line}\n=== 1 failed in 1s ===\nRan 1 test in 1s\n{line}\ntest result: FAILED\n{line}"
                self._fast(reduce_output, text)
                for rx in (R._PY_ANY_SECTION, R._PY_TEST, R._UT_RESULT, R._CARGO_HEAD,
                           R._RULE, R._JEST_SUMMARY, R._PY_SHORT, R._PY_SECTION):
                    self._fast(rx.match, line)


class Bookkeeping(unittest.TestCase):
    def test_each_omitted_line_counted_once(self):
        lines = [f"test_{i} ... ok" for i in range(50)]
        lines += ["=" * 70, "FAIL: t (m.T)"]
        lines += [f"trace {i}" for i in range(100)]
        lines += ["Ran 1 test in 0.1s", "FAILED (failures=1)"]
        self.assertEqual(len(lines), 154)
        out = reduce_output("\n".join(lines))
        # block: 101 lines, 30 shown, 71 behind its own marker; 2 summary lines;
        # the 51 lines outside the block are the global marker's, and no more.
        self.assertIn("  … 71 lines omitted …", out)
        self.assertIn("\n… 51 lines omitted …", out)
        self.assertNotIn("122 lines omitted", out)

    def test_block_start_is_trimmed_start(self):
        lines = ["head", "", "", "-" * 20, "body 1", "body 2", "", "END", "x"]
        blocks = R._blocks_between(lines, [1], lambda ln: ln == "END")
        self.assertEqual(len(blocks), 1)
        start, block = blocks[0]
        self.assertEqual(block, ["body 1", "body 2"])
        self.assertEqual(start, 4)
        self.assertEqual(lines[start:start + len(block)], block)

    def test_summary_not_duplicated_on_pytest_fallback(self):
        lines = ["=== test session starts ==="]
        lines += [f"t{i} PASSED" for i in range(100)]
        lines += ["=== FAILURES ===", "", "", "E   " + ASSERTION, "E   second",
                  "=== short test summary info ===", "FAILED t - boom",
                  "=== 1 failed, 100 passed in 1s ==="]
        out = reduce_output("\n".join(lines))
        self.assertIn(ASSERTION, out)
        self.assertEqual(out.count("FAILED t - boom"), 1)
        self.assertEqual(out.count("1 failed, 100 passed"), 1)
        self.assertEqual(out.count("E   second"), 1)


class Purity(unittest.TestCase):
    def test_no_io_imports_or_calls(self):
        source = Path(R.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported, {"re"})
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertFalse(called & {"open", "eval", "exec", "__import__", "input"})


if __name__ == "__main__":
    unittest.main()
