"""What a tag push is allowed to publish.

`.github/workflows/release.yml` triggers on `tags: ["v*"]` and had no
`needs:`, no test step and no coverage gate: it built and attached a wheel,
sdist, SBOM and SHA256SUMS to a GitHub Release in about two minutes, having
run zero tests. `.github/workflows/ci.yml` triggers on `branches: [main]` and
`pull_request`, neither of which matches `refs/tags/*`, so tagging bypassed
the suite entirely — the one moment a release is actually produced was the
one moment nothing checked it.

Separately: `ci.yml`'s `coverage` job pinned `hatchling==1.27.0` while
`release.yml` pinned `hatchling==1.32.0`. `SdistContainsWhatTheReadmeLinksTo`
(`tests/test_lint_and_coverage.py`) is the only test that builds an sdist and
reads the archive rather than grepping the include list, and it runs solely
in the `coverage` job — under the older pin — so the backend the release
actually uses was never exercised by that test.

Like `tests/test_ci_floor.py` and `tests/test_packaging.py`, this parses the
workflows as YAML (a strict subset — this plugin promises stdlib-only, so no
PyYAML) and reads *structure*, and where it reads raw text it reads text with
comments stripped first: both workflow files explain these very policies in
prose, using the words the checks below forbid.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The workflow reader and comment-stripper are shared with the CI-floor tests
# on purpose: one strict YAML subset for this repository, not two.
from test_ci_floor import load_workflow, strip_comments  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"

# The fail-green escape hatches this repository's CI has never used. Their
# absence is what makes a red step actually stop a job; see report.md's
# assessment of both workflows.
FORBIDDEN = ("continue-on-error", "if: always()", "set +e", "|| true")


def release_workflow():
    return load_workflow(RELEASE.read_text(encoding="utf-8"))


def ci_workflow():
    return load_workflow(CI.read_text(encoding="utf-8"))


def steps_of(job):
    return job.get("steps") or []


def scripts_of(job):
    return [step["run"] for step in steps_of(job) if "run" in step]


def hatchling_pin(text):
    found = re.findall(r"hatchling==([^\s\"']+)", text)
    if len(found) != 1:
        raise AssertionError(
            "expected exactly one hatchling==X.Y.Z pin in the given scripts, "
            "found %d" % len(found))
    return found[0]


class ReleaseRunsTheTestSuiteTests(unittest.TestCase):
    """A tag push must not publish a release having run zero tests."""

    def setUp(self):
        self.workflow = release_workflow()
        self.steps = steps_of(self.workflow["jobs"]["release"])

    def _index(self, predicate, description):
        for index, step in enumerate(self.steps):
            if predicate(step):
                return index
        raise AssertionError("no step %s in release.yml" % description)

    def test_a_step_runs_the_test_suite(self):
        index = self._index(
            lambda step: "unittest discover" in step.get("run", ""),
            "runs the test suite via `unittest discover`",
        )
        step = self.steps[index]
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if", step)

    def test_the_test_step_precedes_the_build_step(self):
        test_index = self._index(
            lambda step: "unittest discover" in step.get("run", ""),
            "runs the test suite",
        )
        build_index = self._index(
            lambda step: "-m build" in step.get("run", ""),
            "builds the sdist/wheel",
        )
        self.assertLess(
            test_index, build_index,
            "the test suite must run before the build step, or a red suite "
            "still produces artefacts",
        )

    def test_the_test_step_precedes_the_release_create_step(self):
        test_index = self._index(
            lambda step: "unittest discover" in step.get("run", ""),
            "runs the test suite",
        )
        release_index = self._index(
            lambda step: "gh release create" in step.get("run", ""),
            "attaches artefacts to the GitHub Release",
        )
        self.assertLess(
            test_index, release_index,
            "the test suite must run before artefacts are attached to a "
            "release",
        )

    def test_no_step_in_the_job_can_let_a_failure_continue(self):
        """Not just the test step: a `continue-on-error` or `if: always()`
        on any later step would let a red suite still reach `gh release
        create`."""
        for step in self.steps:
            self.assertNotIn("continue-on-error", step)
            self.assertNotIn("always()", str(step.get("if", "")))

    def test_no_fail_green_escape_hatch_anywhere_in_the_file(self):
        raw = RELEASE.read_text(encoding="utf-8")
        code = strip_comments(raw)
        for banned in FORBIDDEN:
            self.assertNotIn(
                banned, code,
                "%r must not appear in release.yml outside a comment" % banned,
            )
        # Guard on the guard: the rationale for the new test step names these
        # very escape hatches in prose, so this is only meaningful if
        # `strip_comments` really drops comments rather than reading them.
        self.assertIn("continue-on-error", raw)
        self.assertNotIn("continue-on-error", strip_comments(raw))


class NoFailGreenInCITests(unittest.TestCase):
    """The same absence, held for `ci.yml`, which this unit also touches (the
    `hatchling` pin below) without changing its steps."""

    def test_no_fail_green_escape_hatch_anywhere_in_the_file(self):
        code = strip_comments(CI.read_text(encoding="utf-8"))
        for banned in FORBIDDEN:
            self.assertNotIn(
                banned, code, "%r must not appear in ci.yml" % banned)


class HatchlingPinsAgreeTests(unittest.TestCase):
    """`ci.yml`'s `coverage` job and `release.yml` must name the identical
    `hatchling` version, or the only test that builds and inspects a real
    sdist exercises a different build backend than the one the release
    actually ships."""

    def test_the_two_pins_are_the_same_version(self):
        release_pin = hatchling_pin(
            "\n".join(scripts_of(release_workflow()["jobs"]["release"])))
        coverage_pin = hatchling_pin(
            "\n".join(scripts_of(ci_workflow()["jobs"]["coverage"])))

        self.assertEqual(
            release_pin, coverage_pin,
            "release.yml pins hatchling==%s and ci.yml's coverage job pins "
            "hatchling==%s; align them so the coverage job's sdist build "
            "exercises the backend the release actually ships"
            % (release_pin, coverage_pin),
        )


if __name__ == "__main__":
    unittest.main()
