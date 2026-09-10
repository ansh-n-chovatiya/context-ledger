"""What a green CI run is allowed to mean.

`python -m unittest discover` exits 0 on "Ran 0 tests". A single broken
top-level import in `tests/support.py` therefore takes every matrix job green
having proved nothing, and every later remediation wave is validated against
that signal. `.github/workflows/ci.yml` now parses the suite's own count and
holds it to a floor; this file is what keeps that check honest.

The assertions here are deliberately not substring greps. `report.md:179`
records that several existing meta-tests grep `ci.yml` for plain substrings
that a prose comment in the file would satisfy, so this file does two things
instead:

  * it parses the workflow as YAML (a strict subset, since the plugin promises
    stdlib-only and PyYAML is not a dependency) and asserts against parsed
    *structure* — a comment is not a mapping entry, and `ParserRefusesCommentsTests`
    below proves the parser drops comments rather than reading them as data; and
  * it **executes** the Test step's own script against synthetic suites of known
    size. A comment cannot make a script exit non-zero. The floor is established
    by behaviour: 739 tests must fail the step and 740 must pass it, which pins
    the floor to a real number in real code no matter what the file says in prose.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"

# The floor this repository requires of CI. The workflow may sit above it; it
# may never sit below it. Kept separate from the workflow's own SUITE_FLOOR on
# purpose, so that lowering the workflow's floor breaks a test.
REQUIRED_FLOOR = 740


# --------------------------------------------------------------------------
# A strict YAML subset, enough for a GitHub Actions workflow.
# --------------------------------------------------------------------------

class WorkflowSyntaxError(ValueError):
    pass


def _strip_comment(line):
    """Drop a trailing `#` comment, respecting quotes."""
    out = []
    quote = None
    for char in line:
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(char)
    return "".join(out).rstrip()


def _scalar(text):
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part.strip()) for part in inner.split(",")]
    if text.startswith("{") and text.endswith("}"):
        if not text[1:-1].strip():
            return {}
        raise WorkflowSyntaxError("inline mappings are outside the subset: " + text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    if re.match(r"^-?\d+$", text):
        return int(text)
    return text


class _Reader(object):
    """Indentation-driven reader for the shapes a workflow actually uses:
    nested mappings, sequences of mappings, inline lists, and `|` block
    scalars. Anything else raises rather than being guessed at."""

    def __init__(self, text):
        self.raw = text.splitlines()
        self.pos = 0

    def parse(self):
        value = self._block(0)
        self._peek()
        if self.pos < len(self.raw):
            raise WorkflowSyntaxError("trailing content at line %d" % (self.pos + 1))
        return value

    def _peek(self):
        """Return (indent, text) of the next significant line, or (None, None)."""
        while self.pos < len(self.raw):
            line = _strip_comment(self.raw[self.pos])
            if line.strip():
                return len(line) - len(line.lstrip(" ")), line.strip()
            self.pos += 1
        return None, None

    def _block(self, indent):
        found, text = self._peek()
        if found is None or found < indent:
            return None
        if text.startswith("- ") or text == "-":
            return self._sequence(found)
        return self._mapping(found)

    def _sequence(self, indent):
        items = []
        while True:
            found, text = self._peek()
            if found is None or found < indent or not (
                text.startswith("- ") or text == "-"
            ):
                return items
            if found > indent:
                raise WorkflowSyntaxError("bad indent at line %d" % (self.pos + 1))
            body = text[2:].strip()
            if ":" in body and not body.startswith("{"):
                # `- key: value` — reread the line as the first entry of a
                # mapping by blanking the dash in place.
                line = self.raw[self.pos]
                dash = line.index("-")
                self.raw[self.pos] = line[:dash] + " " + line[dash + 1:]
                items.append(self._mapping(indent + 2))
            else:
                self.pos += 1
                items.append(_scalar(body))

    def _mapping(self, indent):
        result = {}
        while True:
            found, text = self._peek()
            if found is None or found < indent:
                return result
            if found > indent or text.startswith("- "):
                raise WorkflowSyntaxError("bad indent at line %d" % (self.pos + 1))
            if ":" not in text:
                raise WorkflowSyntaxError("not a mapping entry at line %d: %r"
                                          % (self.pos + 1, text))
            key, _, rest = text.partition(":")
            key = _scalar(key.strip())
            rest = rest.strip()
            self.pos += 1
            if rest in ("|", "|-", ">", ">-"):
                result[key] = self._block_scalar(indent)
            elif rest == "":
                result[key] = self._block(indent + 1)
            else:
                result[key] = _scalar(rest)

    def _block_scalar(self, indent):
        body = []
        while self.pos < len(self.raw):
            line = self.raw[self.pos]
            if not line.strip():
                body.append("")
                self.pos += 1
                continue
            found = len(line) - len(line.lstrip(" "))
            if found <= indent:
                break
            body.append(line)
            self.pos += 1
        while body and not body[-1]:
            body.pop()
        if not body:
            return ""
        pad = min(len(l) - len(l.lstrip(" ")) for l in body if l.strip())
        return "\n".join(l[pad:] if l.strip() else "" for l in body) + "\n"


def load_workflow(text=None):
    if text is None:
        text = WORKFLOW.read_text(encoding="utf-8")
    return _Reader(text).parse()


def strip_comments(text):
    """The workflow with every comment removed — what a comment cannot fake."""
    return "\n".join(_strip_comment(line) for line in text.splitlines())


def steps_of(job):
    return job.get("steps") or []


def test_step():
    workflow = load_workflow()
    for step in steps_of(workflow["jobs"]["test"]):
        if step.get("name") == "Test":
            return step
    raise AssertionError("the `test` job has no step named Test")


# --------------------------------------------------------------------------
# Running the Test step's real script against synthetic suites.
# --------------------------------------------------------------------------

_GENERATED = '''\
import unittest


def _build(count, failing):
    body = {}
    for index in range(count):
        body["test_%04d" % index] = lambda self: None
    if failing:
        body["test_%04d" % (count - 1)] = lambda self: self.fail("deliberate")
    return type("Generated", (unittest.TestCase,), body)


Generated = _build(COUNT, FAILING)
'''


def run_test_step(count=None, failing=False, tests_dir=True):
    """Execute the workflow's Test step script against a suite of `count`
    trivial tests, and return the CompletedProcess.

    `count=None` with `tests_dir=True` gives an empty-but-present tests
    directory: the "Ran 0 tests" case that exits 0 under bare `discover`.
    `tests_dir=False` removes the directory entirely, which is the case where
    no count can be parsed out at all.
    """
    script = test_step()["run"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        step = root / "ci_test_step.py"
        step.write_text(script, encoding="utf-8")
        if tests_dir:
            (root / "tests").mkdir()
            if count:
                (root / "tests" / "test_generated.py").write_text(
                    _GENERATED.replace("COUNT", str(count)).replace(
                        "FAILING", "True" if failing else "False"),
                    encoding="utf-8",
                )
        return subprocess.run(
            [sys.executable, str(step)], cwd=str(root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )


class ParserRefusesCommentsTests(unittest.TestCase):
    """The parser must read data, not prose. If this class fails, every
    structural assertion below is worthless, so it is asserted first."""

    def test_a_comment_is_not_a_mapping_entry(self):
        document = load_workflow(
            "jobs:\n"
            "  test:\n"
            "    # fetch-depth: 0\n"
            "    # shell: python\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
        )
        job = document["jobs"]["test"]
        self.assertNotIn("fetch-depth", job)
        self.assertNotIn("shell", job)
        self.assertEqual(job["steps"], [{"uses": "actions/checkout@v4"}])

    def test_the_shapes_the_workflow_uses_survive_a_round_trip(self):
        document = load_workflow(
            "name: ci\n"
            "on:\n"
            "  pull_request:\n"
            "jobs:\n"
            "  test:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        os: [ubuntu-latest, windows-latest]\n"
            "        include:\n"
            "          - os: ubuntu-22.04\n"
            "            python: \"3.8\"\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "        with:\n"
            "          fetch-depth: 0\n"
            "      - name: Test\n"
            "        run: |\n"
            "          import sys  # a comment inside the script survives\n"
            "          sys.exit(0)\n"
        )
        self.assertEqual(document["name"], "ci")
        self.assertIsNone(document["on"]["pull_request"])
        matrix = document["jobs"]["test"]["strategy"]["matrix"]
        self.assertEqual(matrix["os"], ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix["include"], [{"os": "ubuntu-22.04", "python": "3.8"}])
        steps = document["jobs"]["test"]["steps"]
        self.assertEqual(steps[0]["with"]["fetch-depth"], 0)
        self.assertEqual(
            steps[1]["run"],
            "import sys  # a comment inside the script survives\nsys.exit(0)\n",
        )

    def test_the_real_workflow_parses(self):
        workflow = load_workflow()
        self.assertEqual(workflow["name"], "ci")
        self.assertEqual(
            sorted(workflow["jobs"]), ["ledger", "test", "windows-wrapper"])
        self.assertIn(
            "unittest discover",
            strip_comments(WORKFLOW.read_text(encoding="utf-8")),
            "the discovery command must be code, not a comment",
        )


class TestStepIsPortableTests(unittest.TestCase):

    def test_the_count_check_runs_on_every_matrix_os(self):
        """windows-latest is in the matrix, so the check may not assume bash.
        `shell: python` is the one shell present on all three runner images."""
        self.assertEqual(test_step().get("shell"), "python")

    def test_the_step_script_is_python_that_compiles(self):
        compile(test_step()["run"], "ci.yml::Test", "exec")


class SuiteFloorTests(unittest.TestCase):
    """The floor, established by running the real step, not by reading it."""

    def test_the_floor_is_named_once_in_code_and_is_high_enough(self):
        script = strip_comments(test_step()["run"])
        found = re.findall(r"^SUITE_FLOOR = (\d+)$", script, re.MULTILINE)
        self.assertEqual(len(found), 1,
                         "the floor belongs in exactly one named place")
        self.assertGreaterEqual(int(found[0]), REQUIRED_FLOOR)

    def test_the_floor_carries_the_instruction_to_raise_it(self):
        script = test_step()["run"]
        comments = "\n".join(
            line for line in script.splitlines() if line.strip().startswith("#"))
        self.assertRegex(comments.lower(), r"rais(e|ing) it as the suite grows")

    def test_an_empty_suite_fails_the_step(self):
        """`discover` exits 0 on "Ran 0 tests". The step must not."""
        done = run_test_step()
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("Ran 0 tests", done.stdout)

    def test_the_failure_names_the_count_and_the_floor(self):
        done = run_test_step()
        floor = int(re.search(r"^SUITE_FLOOR = (\d+)$",
                              strip_comments(test_step()["run"]),
                              re.MULTILINE).group(1))
        message = done.stdout.splitlines()[-1]
        self.assertIn("0 tests", message)
        self.assertIn(str(floor), message)

    def test_a_suite_one_test_below_the_floor_fails_the_step(self):
        """Behavioural proof that the floor is at least REQUIRED_FLOOR: a
        passing suite of REQUIRED_FLOOR - 1 tests is still a red job."""
        done = run_test_step(count=REQUIRED_FLOOR - 1)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn(str(REQUIRED_FLOOR - 1), done.stdout.splitlines()[-1])

    def test_a_suite_at_the_floor_passes_the_step(self):
        """The other half of the control: the step is not simply always red,
        so the test above is evidence about the floor and not about a broken
        script."""
        done = run_test_step(count=REQUIRED_FLOOR)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("OK", done.stdout)

    def test_a_failing_suite_above_the_floor_still_fails_the_step(self):
        """Parsing a large enough count must not swallow the suite's verdict."""
        done = run_test_step(count=REQUIRED_FLOOR, failing=True)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn(str(REQUIRED_FLOOR), done.stdout.splitlines()[-1])

    def test_an_unparsable_count_fails_the_step(self):
        """Discovery that blows up before running anything prints no
        `Ran N tests` line at all. That is the loudest form of "the suite did
        not run", and it must not be read as success."""
        done = run_test_step(tests_dir=False)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        message = done.stdout.splitlines()[-1]
        self.assertIn("no test count", message)
        self.assertIn(str(REQUIRED_FLOOR), message)


# --------------------------------------------------------------------------
# Full history for exactly one job.
# --------------------------------------------------------------------------

def matrix_combinations(job):
    matrix = job["strategy"]["matrix"]
    combos = []
    for os_name in matrix["os"]:
        for python in matrix["python"]:
            combos.append({"os": os_name, "python": python})
    for extra in matrix.get("include") or []:
        if not any(c["os"] == extra["os"] and c["python"] == extra["python"]
                   for c in combos):
            combos.append(dict(extra))
    return combos


def evaluate(condition, values):
    """Evaluate the tiny slice of the Actions expression grammar the checkout
    conditions use: `matrix.<key>` compared to a literal, joined by && / ||."""
    if not re.match(r"^[A-Za-z0-9_.'\"\- =!&|()\t ]*$", condition):
        raise AssertionError("unsupported condition: " + condition)
    python = condition.replace("&&", " and ").replace("||", " or ")
    python = re.sub(r"matrix\.([A-Za-z_][A-Za-z0-9_]*)",
                    lambda m: "values[%r]" % m.group(1), python)
    return bool(eval(python, {"__builtins__": {}}, {"values": values}))


class FullHistoryTests(unittest.TestCase):
    """`tests/test_findings_rounds.py` corroborates a 399-line vendored fixture
    against `git show f7fa623:ctx/findings.py` and skips when the ref is
    absent. A depth-1 checkout never has it, so that guard skipped in all nine
    jobs on every push. One job now takes the full clone."""

    def setUp(self):
        self.workflow = load_workflow()

    def deep_steps(self):
        found = []
        for name, job in self.workflow["jobs"].items():
            for step in steps_of(job):
                with_ = step.get("with") or {}
                if with_.get("fetch-depth") == 0:
                    found.append((name, step))
        return found

    def test_exactly_one_step_asks_for_full_history(self):
        found = self.deep_steps()
        self.assertEqual(
            [name for name, _ in found], ["test"],
            "full history costs a full clone; exactly one job should pay it",
        )
        self.assertTrue(found[0][1]["uses"].startswith("actions/checkout@"))

    def test_full_history_is_pinned_to_one_matrix_entry(self):
        _, step = self.deep_steps()[0]
        enabled = [combo for combo in matrix_combinations(self.workflow["jobs"]["test"])
                   if evaluate(step["if"], combo)]
        self.assertEqual(enabled, [{"os": "ubuntu-latest", "python": "3.13"}])

    def test_every_matrix_entry_still_checks_the_repository_out(self):
        """A conditional checkout that no branch selects would leave a job with
        no source and a green tick."""
        job = self.workflow["jobs"]["test"]
        checkouts = [step for step in steps_of(job)
                     if str(step.get("uses", "")).startswith("actions/checkout@")]
        self.assertGreaterEqual(len(checkouts), 1)
        for combo in matrix_combinations(job):
            active = [step for step in checkouts
                      if "if" not in step or evaluate(step["if"], combo)]
            self.assertEqual(
                len(active), 1,
                "%s should run exactly one checkout, ran %d" % (combo, len(active)),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
