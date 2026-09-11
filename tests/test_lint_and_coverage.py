"""The two checks this repository kept deferring, and what they are worth.

A lint job and a coverage job are easy to add and easy to make meaningless: a
`select` list that selects nothing, an `exclude` that covers every file that
would fail, a coverage floor set at 50% against a suite that measures 92%.
Each of those is a green tick that cannot go red, which is the failure mode
`tests/test_ci_floor.py` was written about -- `SUITE_FLOOR` sat at 740 against
a suite of 1199 and would have let 459 tests vanish silently.

So this file does not check that a lint job exists. It checks what the job can
catch:

  * **ruff.** The configuration is read out of `pyproject.toml` as parsed data,
    and asserted to select something, to exclude neither `ctx/` nor `tests/`,
    and not to have ignored away the rules that carry it. Where `ruff` is on
    PATH -- it is in the `lint` job, which runs this module for exactly that
    reason -- the configuration is additionally *executed*: once against the
    real tree, which must be clean, and once against a synthetic file with an
    undefined name in it, which must not be. A rule set that cannot fail is
    the thing being guarded against, so it is proved to fail.

  * **coverage.** The floors live in the workflow's gate step. That step is
    lifted out of the YAML and run against synthetic `coverage.json` reports:
    below the line floor, below the branch floor, at the floors, absent,
    malformed, and measured over a suspiciously tiny tree. A prose comment
    cannot make a script exit non-zero. `REQUIRED_LINE_FLOOR` and
    `REQUIRED_BRANCH_FLOOR` below are held equal to the workflow's numbers, so
    the pair cannot drift the way `SUITE_FLOOR` did.

  * **the sdist.** Unit 06 split the README into `docs/reference.md`,
    `docs/walkthroughs.md` and `docs/operations.md`; `docs/` was not in the
    sdist allowlist, so the artefact shipped a README pointing at files it did
    not contain. The test for that **builds the sdist and reads the archive**.
    Asserting that the allowlist mentions the string "/docs" would pass on a
    build that silently dropped it.
"""

import json
import math
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# One strict YAML subset and one strict TOML subset for this repository, not
# three: both readers are imported from the suites that already own them.
from test_ci_floor import load_workflow, steps_of, strip_comments  # noqa: E402
from test_packaging import load_pyproject  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"

# THE COVERAGE FLOORS, the copy that lives with the tests. Measured rather than
# chosen: `coverage run -m unittest discover -s tests` over 1401 tests reported
# 92.80% of 6364 statements and 86.83% of 2438 branches in `ctx/`, and the
# floors sit just under that -- close enough to notice a real drop, with about
# 1.8 points of headroom for the difference between the machine that measured
# and the runner that enforces. `LINE_FLOOR` and `BRANCH_FLOOR` in
# .github/workflows/ci.yml are the same decision; `test_the_floors_agree`
# fails if either side moves alone.
REQUIRED_LINE_FLOOR = 91.0
REQUIRED_BRANCH_FLOOR = 85.0

# What the measurement above was taken over, used to build synthetic reports
# whose denominators are plausible.
MEASURED_STATEMENTS = 6364
MEASURED_BRANCHES = 2438


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def job(name):
    workflow = load_workflow()
    if name not in workflow["jobs"]:
        raise AssertionError("no `%s` job in .github/workflows/ci.yml" % name)
    return workflow["jobs"][name]


def step_named(job_name, step_name):
    for step in steps_of(job(job_name)):
        if step.get("name") == step_name:
            return step
    raise AssertionError("the `%s` job has no step named %s"
                         % (job_name, step_name))


def run_scripts(job_name):
    """Every `run:` body in a job, with comments stripped -- what the job
    really executes, never what it says about itself."""
    return [strip_comments(step["run"]) for step in steps_of(job(job_name))
            if "run" in step]


def pip_installs(text):
    """Every package spec passed to `pip install` in a shell script, with
    backslash continuations joined first."""
    joined = re.sub(r"\\\n\s*", " ", text)
    specs = []
    for line in joined.splitlines():
        if "pip install" not in line:
            continue
        tail = line.split("pip install", 1)[1]
        for word in tail.split():
            if word.startswith("-"):
                continue
            specs.append(word)
    return specs


def ruff_config():
    return load_pyproject()["tool"]["ruff"]


def lint_config():
    return ruff_config().get("lint") or {}


def coverage_config():
    return load_pyproject()["tool"]["coverage"]


def gate_script():
    return step_named("coverage", "Coverage floor")["run"]


def workflow_coverage_floor(name):
    """A floor as the workflow really carries it: parsed out of the gate
    script with comments removed, so prose cannot supply the number."""
    found = re.findall(r"^%s = ([0-9.]+)$" % name, strip_comments(gate_script()),
                       re.MULTILINE)
    if len(found) != 1:
        raise AssertionError("expected exactly one %s assignment in the gate "
                             "step, found %d" % (name, len(found)))
    return float(found[0])


def totals(line_percent, branch_percent, statements=MEASURED_STATEMENTS,
           branches=MEASURED_BRANCHES):
    """A synthetic `coverage.json` totals block at a chosen coverage."""
    return {
        "num_statements": statements,
        # Rounded up, so "at the floor" is never a hair under it: this helper
        # exists to place a report on a chosen side of the line, and a
        # rounding artefact would make the control test lie.
        "covered_lines": int(math.ceil(statements * line_percent / 100.0)),
        "num_branches": branches,
        "covered_branches": int(math.ceil(branches * branch_percent / 100.0)),
        "percent_covered": (line_percent + branch_percent) / 2.0,
    }


def run_gate(report=None, raw=None):
    """Execute the workflow's own gate step against a synthetic report.

    `report` is a totals mapping; `raw` is literal file content for the
    malformed case. Passing neither leaves no coverage.json at all.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "gate.py").write_text(gate_script(), encoding="utf-8")
        if raw is not None:
            (root / "coverage.json").write_text(raw, encoding="utf-8")
        elif report is not None:
            (root / "coverage.json").write_text(
                json.dumps({"totals": report}), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "gate.py"], cwd=str(root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )


def run_ruff(*arguments):
    executable = shutil.which("ruff")
    if executable is None:
        raise unittest.SkipTest(
            "ruff is not on PATH. It is installed and pinned by the `lint` "
            "job in .github/workflows/ci.yml, which runs this module; locally, "
            "`pip install ruff` to execute these checks instead of skipping.")
    return subprocess.run(
        [executable] + list(arguments), cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


# --------------------------------------------------------------------------
# ruff: the job
# --------------------------------------------------------------------------

class RuffJobTests(unittest.TestCase):
    """Criterion: CI runs ruff over `ctx/` and `tests/`, pinned exactly, on
    one runner."""

    def test_a_job_runs_ruff_over_both_trees(self):
        scripts = [text for text in run_scripts("lint") if "ruff check" in text]
        self.assertEqual(len(scripts), 1, "expected one `ruff check` step")
        command = scripts[0]
        self.assertRegex(command, r"\bruff check\b")
        for tree in ("ctx/", "tests/"):
            self.assertIn(tree, command,
                          "ruff must be pointed at %s" % tree)

    def test_ruff_is_pinned_to_an_exact_version(self):
        """A linter that upgrades itself turns an unrelated push red on a rule
        nobody chose. Every pip install in the workflow names `name==x.y.z`."""
        specs = []
        for name in load_workflow()["jobs"]:
            for script in run_scripts(name):
                specs.extend(pip_installs(script))
        self.assertTrue(any(spec.startswith("ruff==") for spec in specs),
                        "the lint job must install a pinned ruff, got %r" % specs)
        for spec in specs:
            self.assertRegex(
                spec, r"^[A-Za-z0-9._-]+==[0-9]+(\.[0-9]+)*$",
                "%r is not an exact pin; CI pins its tools the way it pins "
                "its actions" % spec,
            )

    def test_ruff_runs_on_one_ubuntu_runner_only(self):
        """A static check reading the same bytes nine times proves the same
        thing nine times."""
        definition = job("lint")
        self.assertNotIn("strategy", definition,
                         "ruff does not need a matrix")
        self.assertTrue(str(definition["runs-on"]).startswith("ubuntu"),
                        definition["runs-on"])

    def test_the_lint_job_is_read_only(self):
        self.assertEqual(job("lint").get("permissions"), {"contents": "read"})


# --------------------------------------------------------------------------
# ruff: the configuration
# --------------------------------------------------------------------------

class RuffConfigurationTests(unittest.TestCase):
    """Criterion: the rule set may be narrow; it may not be empty, and it may
    not be emptied by an exclude."""

    def setUp(self):
        self.lint = lint_config()
        self.selected = self.lint.get("select") or []
        self.ignored = self.lint.get("ignore") or []

    def test_the_configuration_lives_in_pyproject(self):
        """Not in the workflow: `ruff check` run locally must be the same
        check CI runs."""
        self.assertIn("ruff", load_pyproject()["tool"])
        self.assertTrue(self.selected)

    def test_the_rule_set_is_not_empty(self):
        self.assertGreaterEqual(
            len(self.selected), 5,
            "a `select` of nothing is a check that cannot fail")

    def test_the_rule_set_is_not_emptied_by_the_ignore_list(self):
        """Ignoring every selected code is `select = []` written the long
        way."""
        live = [code for code in self.selected
                if not any(code.startswith(other) or other.startswith(code)
                           for other in self.ignored)]
        self.assertGreaterEqual(len(live), 5, "ignored: %r" % (self.ignored,))

    def test_pyflakes_undefined_names_are_still_enforced(self):
        """F821 is the single most valuable rule here and the tree passes it,
        so no ignore may cover it."""
        self.assertIn("F", self.selected)
        for code in self.ignored:
            self.assertFalse(
                "F821".startswith(code),
                "%r would switch off undefined-name detection" % code)

    def test_neither_tree_is_excluded(self):
        """The other way to make a linter unable to fail."""
        excludes = list(ruff_config().get("exclude") or [])
        excludes += list(ruff_config().get("extend-exclude") or [])
        for pattern in excludes:
            cleaned = pattern.strip()
            for noise in ("./", "/"):
                if cleaned.startswith(noise):
                    cleaned = cleaned[len(noise):]
            cleaned = cleaned.rstrip("/*").rstrip("/")
            self.assertNotIn(cleaned, ("ctx", "tests"),
                             "%r excludes a tree CI claims to lint" % pattern)

    def test_per_file_ignores_are_per_file(self):
        """A per-file ignore is a scalpel. One that names a directory, or a
        glob over a whole tree, is an exclude wearing a disguise."""
        for pattern, codes in (self.lint.get("per-file-ignores") or {}).items():
            self.assertTrue(pattern.endswith(".py"),
                            "%r is not a single file" % pattern)
            self.assertTrue((ROOT / pattern).is_file(),
                            "%r does not exist" % pattern)
            self.assertTrue(codes, "%r ignores nothing" % pattern)
            self.assertLessEqual(len(codes), 2,
                                 "%r exempts too much: %r" % (pattern, codes))

    def test_the_noqa_in_the_cli_registry_is_load_bearing(self):
        """`ctx/cli.py` carries one `# noqa: E731` on the REST lambda. It is
        only worth anything while E731 is selected and not globally ignored --
        otherwise it is decoration that a reader will trust."""
        source = (ROOT / "ctx" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("# noqa: E731", source)
        self.assertTrue(any("E7".startswith(code) or code.startswith("E7")
                            for code in self.selected))
        self.assertNotIn("E731", self.ignored)


class RuffReallyRunsTests(unittest.TestCase):
    """The configuration executed, not read. Skips where ruff is absent; the
    `lint` job installs it and runs this module so that it does not."""

    def test_the_tree_is_clean_under_the_configured_rules(self):
        done = run_ruff("check", "--no-cache", "ctx/", "tests/")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_configuration_can_fail(self):
        """The control. A file with an undefined name in it must come back
        red under this exact configuration -- if it does not, every green run
        above means nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            offender = Path(tmp) / "broken_on_purpose.py"
            offender.write_text("def go():\n    return not_defined_anywhere\n",
                                encoding="utf-8")
            done = run_ruff("check", "--no-cache", "--config",
                            str(ROOT / "pyproject.toml"), str(offender))
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("F821", done.stdout)


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

class CoverageJobTests(unittest.TestCase):

    def test_coverage_runs_on_one_ubuntu_runner_only(self):
        definition = job("coverage")
        self.assertNotIn("strategy", definition)
        self.assertTrue(str(definition["runs-on"]).startswith("ubuntu"),
                        definition["runs-on"])

    def test_the_coverage_job_is_read_only(self):
        self.assertEqual(job("coverage").get("permissions"), {"contents": "read"})

    def test_coverage_is_pinned_to_an_exact_version(self):
        specs = [spec for script in run_scripts("coverage")
                 for spec in pip_installs(script)]
        self.assertTrue(any(spec.startswith("coverage==") for spec in specs),
                        specs)

    def test_something_actually_measures_before_the_gate_reads(self):
        """The gate reads `coverage.json`. A job that never writes one would
        fail loudly rather than pass vacuously -- but the measure step is the
        point of the job, so its absence is caught here too."""
        scripts = "\n".join(run_scripts("coverage"))
        self.assertIn("coverage run", scripts)
        self.assertRegex(scripts, r"coverage json[^\n]*coverage\.json")

    def test_measurement_is_branch_coverage_over_the_runtime_package(self):
        run = coverage_config()["run"]
        self.assertIs(run.get("branch"), True)
        self.assertEqual(run.get("source"), ["ctx"])

    def test_the_number_cannot_be_improved_by_annotating(self):
        """`exclude_lines` would let uncovered code be exempted rather than
        covered, which turns the floor into a formality."""
        self.assertNotIn("exclude_lines", coverage_config().get("report") or {})


class CoverageFloorTests(unittest.TestCase):
    """The floors, established by running the real gate step."""

    def test_each_floor_is_named_once_in_code(self):
        script = strip_comments(gate_script())
        for name in ("LINE_FLOOR", "BRANCH_FLOOR"):
            found = re.findall(r"^%s = [0-9.]+$" % name, script, re.MULTILINE)
            self.assertEqual(len(found), 1,
                             "%s belongs in exactly one named place" % name)

    def test_the_floors_agree(self):
        """Two halves of one decision in two files. Equality, not `>=`:
        raising either alone must fail, so whoever raises one is forced to
        raise the other. The absence of this check is why SUITE_FLOOR sat 459
        tests below the suite it guarded."""
        self.assertEqual(workflow_coverage_floor("LINE_FLOOR"),
                         REQUIRED_LINE_FLOOR)
        self.assertEqual(workflow_coverage_floor("BRANCH_FLOOR"),
                         REQUIRED_BRANCH_FLOOR)

    def test_the_floors_are_near_the_measurement(self):
        """A floor far below the real number cannot see a drop. The suite
        measures ~92.8% line and ~86.8% branch; a floor of 50 would be a
        decoration."""
        self.assertGreaterEqual(REQUIRED_LINE_FLOOR, 85.0)
        self.assertGreaterEqual(REQUIRED_BRANCH_FLOOR, 80.0)

    def test_the_gate_step_is_python_that_compiles(self):
        self.assertEqual(step_named("coverage", "Coverage floor").get("shell"),
                         "python")
        compile(gate_script(), "ci.yml::Coverage floor", "exec")

    def test_the_gate_passes_at_the_floors(self):
        """The control: the gate is not simply always red, so the failures
        below are evidence about the floors and not about a broken script."""
        done = run_gate(totals(REQUIRED_LINE_FLOOR, REQUIRED_BRANCH_FLOOR))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK", done.stdout)

    def test_the_gate_passes_at_the_measured_coverage(self):
        done = run_gate(totals(92.80, 86.83))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_line_coverage_below_the_floor_fails(self):
        done = run_gate(totals(REQUIRED_LINE_FLOOR - 1.0, 99.0))
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("line coverage", done.stdout)
        self.assertIn("FAIL", done.stdout)

    def test_branch_coverage_below_the_floor_fails(self):
        """Branch coverage is the half that slips: a suite can touch every
        line and take one side of every `if`."""
        done = run_gate(totals(99.0, REQUIRED_BRANCH_FLOOR - 1.0))
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("branch coverage", done.stdout)

    def test_a_collapse_names_both_numbers(self):
        done = run_gate(totals(10.0, 10.0))
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("line coverage", done.stdout)
        self.assertIn("branch coverage", done.stdout)

    def test_a_missing_report_fails(self):
        """The measure step silently producing nothing must not read as
        success, for the same reason "Ran 0 tests" must not."""
        done = run_gate()
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("no coverage.json", done.stdout)

    def test_a_malformed_report_fails(self):
        done = run_gate(raw="{not json at all")
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("FAIL", done.stdout)

    def test_a_report_over_the_wrong_tree_fails(self):
        """100% of eleven statements is the vacuous pass: coverage pointed at
        one module, or at nothing, reports beautifully."""
        done = run_gate(totals(100.0, 100.0, statements=11, branches=4))
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("not the `ctx` package", done.stdout)

    def test_the_floor_carries_the_instruction_to_raise_it(self):
        comments = "\n".join(line for line in gate_script().splitlines()
                             if line.strip().startswith("#"))
        self.assertRegex(comments.lower(), r"raise them as coverage rises")


# --------------------------------------------------------------------------
# The sdist, read rather than trusted
# --------------------------------------------------------------------------

def readme_links(path, prefix):
    """Every local path a markdown file links to, resolved against the
    archive's layout. External links and bare anchors are not files."""
    text = path.read_text(encoding="utf-8")
    found = set()
    for match in re.finditer(r"\]\(([^)\s]+)\)", text):
        target = match.group(1).split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / target).resolve()
        try:
            relative = resolved.relative_to(ROOT)
        except ValueError:
            continue
        if not resolved.exists():
            continue  # a broken link is tests/test_docs_currency.py's business
        found.add(prefix + "/" + relative.as_posix()
                  + ("/" if target.endswith("/") else ""))
    return found


def build_sdist(destination):
    """Really build one. Returns the archive path, or raises SkipTest where no
    build backend is installed -- the `coverage` job installs `build` and
    `hatchling` precisely so that this does not skip in CI."""
    for module in ("build", "hatchling"):
        probe = subprocess.run([sys.executable, "-c", "import " + module],
                               capture_output=True)
        if probe.returncode != 0:
            raise unittest.SkipTest(
                "%s is not installed, so no sdist can be built here. CI's "
                "`coverage` job installs pinned `build` and `hatchling` so "
                "this check runs for real there." % module)
    done = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation",
         "--outdir", str(destination), str(ROOT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if done.returncode != 0:
        raise AssertionError("building the sdist failed:\n" + done.stdout
                             + done.stderr)
    built = sorted(Path(destination).glob("*.tar.gz"))
    if len(built) != 1:
        raise AssertionError("expected one sdist, got %r" % (built,))
    return built[0]


class SdistContainsWhatTheReadmeLinksTo(unittest.TestCase):
    """`docs/` was missing from the allowlist after unit 06 split the README
    into `docs/reference.md`, `docs/walkthroughs.md` and `docs/operations.md`,
    so the artefact shipped a front page pointing at files it did not contain.
    This builds the artefact and looks inside it: wave 3 recorded that the
    SBOM step only worked because it was executed rather than trusted."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        archive = build_sdist(cls._tmp.name)
        with tarfile.open(str(archive)) as tar:
            cls.names = set(tar.getnames())
        cls.prefix = sorted(cls.names)[0].split("/")[0]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def contains(self, name):
        if name.endswith("/"):
            return any(entry.startswith(name) for entry in self.names)
        return name in self.names

    def test_the_readme_and_the_three_pages_it_was_split_into_all_ship(self):
        for name in ("README.md", "docs/reference.md", "docs/walkthroughs.md",
                     "docs/operations.md"):
            self.assertTrue(self.contains(self.prefix + "/" + name),
                            "%s is not in the sdist" % name)

    def test_every_file_the_readme_links_to_is_in_the_archive(self):
        missing = sorted(name for name in readme_links(ROOT / "README.md",
                                                       self.prefix)
                         if not self.contains(name))
        self.assertEqual(
            missing, [],
            "the shipped README links to files the sdist does not contain; "
            "add them to [tool.hatch.build.targets.sdist].include or stop "
            "linking to them",
        )

    def test_the_shipped_docs_do_not_link_out_of_the_archive(self):
        missing = []
        for page in sorted((ROOT / "docs").glob("*.md")):
            missing += [name for name in readme_links(page, self.prefix)
                        if not self.contains(name)]
        self.assertEqual(sorted(set(missing)), [])

    def test_the_ledger_state_and_the_vendored_fixture_still_stay_out(self):
        """The allowlist grew; it must not have grown into 3.7 MB of this
        repository's own ledger, or into the frozen copy of an old module."""
        for entry in sorted(self.names):
            self.assertNotIn("/.ctx/", entry)
            self.assertNotIn("/tests/", entry)

    def test_the_runtime_is_still_there(self):
        """A guard against a green run over an empty tarball."""
        self.assertTrue(self.contains(self.prefix + "/ctx/cli.py"))
        self.assertGreater(len(self.names), 40, sorted(self.names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
