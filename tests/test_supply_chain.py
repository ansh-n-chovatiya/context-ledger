"""What CI is pinned to, what it is allowed to do, and who answers for it.

Three separate claims live here, and each one is asserted against *structure*
rather than against text:

  * **Pinning.** Every `uses:` in `.github/workflows/ci.yml` names a full
    40-character commit SHA, not a mutable tag. `actions/checkout@v4` is
    whatever that tag points at the moment a job starts; a SHA is a fixed
    artefact.
  * **Least privilege.** Every job declares its own `permissions:` block, and
    the blocks grant `contents: read` and nothing else. Absent a block, a job
    inherits the repository's default token scope, which on many repositories
    is still read *and write*.
  * **Governance.** `SECURITY.md`, `CODEOWNERS` and `.github/dependabot.yml`
    exist and say something a reader can act on.

**How these assertions avoid being comment-satisfiable.** `report.md:179`
records the failure mode this file is written against: a meta-test that greps a
workflow for a substring passes when someone writes that substring in a prose
comment, so the test measures the file's vocabulary rather than its behaviour.
Nothing here greps the workflow. Every assertion about `ci.yml` and
`dependabot.yml` runs against the mapping returned by `load_workflow()` — the
strict YAML-subset parser already in `tests/test_ci_floor.py`, reused rather
than rewritten — and that parser drops comments before it builds any data
(`ParserRefusesCommentsTests` in that file proves it does). A comment is not a
mapping entry, so a comment cannot create a `permissions` key or a `uses`
value. `CommentsCannotSatisfyTheseChecksTests` below closes the loop by feeding
each check a synthetic workflow whose pinning and permissions exist *only* in
comments, and asserting that each check still reports a violation.

The one place a raw line is read is the trailing `# vX.Y.Z` version comment,
which is by definition a comment — but the SHA it annotates is taken from the
parsed structure, so the comment can decorate the pin and cannot fake it.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reused, not reimplemented: the strict YAML-subset parser lives in
# tests/test_ci_floor.py because the plugin promises stdlib-only and PyYAML is
# not a dependency. A second parser would be a second thing to keep honest.
from test_ci_floor import (  # noqa: E402
    WORKFLOW,
    _strip_comment,
    load_workflow,
    steps_of,
)

ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "SECURITY.md"
CODEOWNERS = ROOT / "CODEOWNERS"
DEPENDABOT = ROOT / ".github/dependabot.yml"
PYPROJECT = ROOT / "pyproject.toml"

# A pinned reference: `owner/repo[/subdir]@<40 hex>`. Docker and local (`./`)
# action references are not used by this repository and are not accepted here.
PINNED = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._/-]+)?@[0-9a-f]{40}$")

# The scopes a test job may ask for. `contents: read` and nothing else.
ALLOWED_PERMISSIONS = {"contents": "read"}


# --------------------------------------------------------------------------
# Structural checks, written as functions so they can be aimed at a synthetic
# workflow as easily as at the real one. This is what makes criterion "a job
# added later is caught too" testable: the checks walk `workflow["jobs"]`,
# they do not consult a hardcoded list of job names.
# --------------------------------------------------------------------------

def all_uses(workflow):
    """Every (job name, step index, `uses` value) in the workflow."""
    found = []
    for name, job in sorted((workflow.get("jobs") or {}).items()):
        for index, step in enumerate(steps_of(job)):
            if "uses" in step:
                found.append((name, index, step["uses"]))
    return found


def unpinned_uses(workflow):
    """Every `uses:` that names something other than a full commit SHA."""
    return [entry for entry in all_uses(workflow)
            if not PINNED.match(str(entry[2]))]


def permission_violations(workflow):
    """Every job whose `permissions:` block is missing, not a mapping, or
    grants anything beyond `contents: read`."""
    problems = []
    for name, job in sorted((workflow.get("jobs") or {}).items()):
        if "permissions" not in job:
            problems.append((name, "no permissions block"))
            continue
        granted = job["permissions"]
        if not isinstance(granted, dict):
            # `permissions: write-all` and `permissions: read-all` parse as
            # scalars. Both are the opposite of least privilege.
            problems.append((name, "permissions is not a mapping: %r" % (granted,)))
            continue
        if granted != ALLOWED_PERMISSIONS:
            problems.append((name, "grants %r, expected %r"
                             % (granted, ALLOWED_PERMISSIONS)))
    return problems


def version_comments(text):
    """Map each pinned `uses:` SHA to the trailing comment on its line.

    Only lines that are really `uses:` entries are considered: the line is run
    through the workflow parser's own comment stripper first, so a `uses:`
    written inside a prose comment contributes nothing.
    """
    found = {}
    for line in text.splitlines():
        code = _strip_comment(line)
        stripped = code.strip()
        if not (stripped.startswith("- uses:") or stripped.startswith("uses:")):
            continue
        _, _, ref = stripped.partition("uses:")
        marker = line.find("#", len(code))
        found[ref.strip()] = line[marker:].strip() if marker != -1 else ""
    return found


# --------------------------------------------------------------------------

class PinnedActionsTests(unittest.TestCase):
    """Criterion: every action is a commit SHA, with its version in a comment."""

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = load_workflow(self.text)

    def test_the_workflow_actually_uses_actions(self):
        """Guard against a vacuous pass: a workflow with no `uses:` at all
        would satisfy every assertion below by having nothing to check."""
        self.assertGreaterEqual(len(all_uses(self.workflow)), 4)

    def test_every_action_is_pinned_to_a_full_commit_sha(self):
        self.assertEqual(
            unpinned_uses(self.workflow), [],
            "a tag is whatever it points at today; pin to a 40-character SHA",
        )

    def test_the_pins_are_lowercase_hex_of_exactly_forty_characters(self):
        """An abbreviated SHA is ambiguous and GitHub will not resolve it."""
        for name, index, ref in all_uses(self.workflow):
            digest = str(ref).split("@", 1)[1]
            self.assertEqual(len(digest), 40, (name, index, ref))
            self.assertEqual(digest, digest.lower(), (name, index, ref))
            int(digest, 16)  # raises ValueError on anything non-hex

    def test_each_pin_carries_a_human_readable_version_comment(self):
        """A bare SHA is unreadable in review. The trailing comment is what
        tells a reviewer whether the bump was v4.4.0 or v3."""
        comments = version_comments(self.text)
        for name, index, ref in all_uses(self.workflow):
            self.assertIn(ref, comments, (name, index))
            self.assertRegex(
                comments[ref], r"^#\s*v\d+(\.\d+)*\s*$",
                "%s step %d: pin %s needs a trailing `# vX.Y.Z` comment"
                % (name, index, ref),
            )

    def test_the_same_action_is_pinned_to_one_sha_throughout(self):
        """Two SHAs for `actions/checkout` in one workflow means one of them
        was missed by a bump."""
        by_action = {}
        for _name, _index, ref in all_uses(self.workflow):
            action, _, digest = str(ref).partition("@")
            by_action.setdefault(action, set()).add(digest)
        for action, digests in sorted(by_action.items()):
            self.assertEqual(len(digests), 1,
                             "%s is pinned to %d different SHAs" % (action, len(digests)))

    def test_an_unpinned_action_is_detected_in_a_job_added_later(self):
        """The check walks the jobs mapping rather than a known list, so a job
        appended after this test was written is still covered."""
        document = load_workflow(
            self.text
            + "\n"
            + "  brand-new-job:\n"
            + "    runs-on: ubuntu-latest\n"
            + "    permissions:\n"
            + "      contents: read\n"
            + "    steps:\n"
            + "      - uses: actions/checkout@v4\n"
        )
        self.assertEqual(
            unpinned_uses(document),
            [("brand-new-job", 0, "actions/checkout@v4")],
        )

    def test_a_moving_tag_on_any_action_is_detected(self):
        for tag in ("v4", "v4.4.0", "main", "master", "11d5960"):
            with self.subTest(tag=tag):
                document = load_workflow(
                    "jobs:\n"
                    "  job:\n"
                    "    steps:\n"
                    "      - uses: actions/setup-python@%s\n" % tag
                )
                self.assertEqual(len(unpinned_uses(document)), 1, tag)


class LeastPrivilegeTests(unittest.TestCase):
    """Criterion: every job declares `contents: read` and nothing more."""

    def setUp(self):
        self.workflow = load_workflow()

    def test_the_workflow_actually_has_jobs(self):
        self.assertGreaterEqual(len(self.workflow["jobs"]), 3)

    def test_every_job_declares_an_explicit_permissions_block(self):
        self.assertEqual(permission_violations(self.workflow), [])

    def test_each_job_grants_read_and_only_read(self):
        for name, job in sorted(self.workflow["jobs"].items()):
            self.assertEqual(job.get("permissions"), {"contents": "read"}, name)

    def test_no_job_is_granted_a_write_scope(self):
        """Read as a value, not as an absence: `id-token: write` added beside
        `contents: read` would still be a write scope."""
        for name, job in sorted(self.workflow["jobs"].items()):
            granted = job.get("permissions")
            # `permissions: write-all` parses as a scalar, so this must be a
            # clean failure rather than an AttributeError on `.items()`.
            self.assertIsInstance(granted, dict, "%s: %r" % (name, granted))
            for scope, level in granted.items():
                self.assertEqual(level, "read", "%s grants %s: %s"
                                 % (name, scope, level))

    def test_the_workflow_level_default_is_also_read_only(self):
        """A job added without its own block inherits this, so it must not be
        a wide default. Its presence is optional; being wide is not."""
        default = self.workflow.get("permissions")
        if default is not None:
            self.assertIsInstance(default, dict, default)
            for scope, level in default.items():
                self.assertEqual(level, "read", (scope, level))

    def test_a_missing_block_is_detected_in_a_job_added_later(self):
        document = load_workflow(
            "jobs:\n"
            "  publish:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        self.assertEqual(permission_violations(document),
                         [("publish", "no permissions block")])

    def test_write_all_is_detected(self):
        document = load_workflow(
            "jobs:\n"
            "  publish:\n"
            "    permissions: write-all\n"
            "    steps:\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        problems = permission_violations(document)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("not a mapping", problems[0][1])

    def test_a_widened_scope_is_detected(self):
        document = load_workflow(
            "jobs:\n"
            "  publish:\n"
            "    permissions:\n"
            "      contents: write\n"
            "    steps:\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        problems = permission_violations(document)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("grants", problems[0][1])

    def test_an_extra_scope_beside_contents_read_is_detected(self):
        document = load_workflow(
            "jobs:\n"
            "  publish:\n"
            "    permissions:\n"
            "      contents: read\n"
            "      id-token: write\n"
            "    steps:\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        self.assertEqual(len(permission_violations(document)), 1)


class CommentsCannotSatisfyTheseChecksTests(unittest.TestCase):
    """The point of the file. Each check is aimed at a workflow where the
    property it looks for is present *only* as prose, and must still fail."""

    def test_a_commented_permissions_block_does_not_count(self):
        document = load_workflow(
            "jobs:\n"
            "  job:\n"
            "    # permissions:\n"
            "    #   contents: read\n"
            "    # This job runs with least privilege: contents: read only.\n"
            "    steps:\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        self.assertEqual(permission_violations(document),
                         [("job", "no permissions block")])

    def test_a_sha_written_in_a_comment_does_not_pin_anything(self):
        document = load_workflow(
            "jobs:\n"
            "  job:\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps:\n"
            "      # Pinned to %s (v4.4.0) for supply-chain safety.\n"
            "      - uses: actions/checkout@v4\n" % ("a" * 40)
        )
        self.assertEqual(unpinned_uses(document),
                         [("job", 0, "actions/checkout@v4")])

    def test_a_uses_line_inside_a_comment_is_not_read_as_a_pin(self):
        """The other direction: prose mentioning an unpinned action must not
        make a properly pinned workflow fail, or the check would be unusable."""
        document = load_workflow(
            "jobs:\n"
            "  job:\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps:\n"
            "      # We used to write `uses: actions/checkout@v4` here.\n"
            "      - uses: actions/checkout@%s\n" % ("a" * 40)
        )
        self.assertEqual(unpinned_uses(document), [])

    def test_the_version_comment_reader_ignores_commented_uses_lines(self):
        found = version_comments(
            "steps:\n"
            "  # - uses: actions/checkout@v4 # v4\n"
            "  - uses: actions/checkout@%s # v4.4.0\n" % ("a" * 40)
        )
        self.assertEqual(found, {"actions/checkout@" + "a" * 40: "# v4.4.0"})


class WhatCiStillProvesTests(unittest.TestCase):
    """Pinning and permissions were added to a workflow that already earned
    its keep. Nothing here may have been traded away for them. The floor and
    the deep checkout have their own file (tests/test_ci_floor.py, which this
    unit did not touch); these are the jobs and steps that do not."""

    def setUp(self):
        self.workflow = load_workflow()

    def test_the_original_three_jobs_are_all_still_present(self):
        """Subset, not equality.

        This asserted `sorted(jobs) == [...]`, which fails when a job is
        *added* — the opposite of what the class exists to catch. It went red
        the first time CI gained a job (lint and coverage), and the claim it
        is making is "nothing was traded away", which is a subset claim. A
        removal still fails; an addition no longer does.
        """
        for name in ("ledger", "test", "windows-wrapper"):
            self.assertIn(name, self.workflow["jobs"])

    def test_the_windows_wrapper_job_still_runs_on_windows(self):
        job = self.workflow["jobs"]["windows-wrapper"]
        self.assertEqual(job["runs-on"], "windows-latest")
        names = [step.get("name") for step in steps_of(job)]
        self.assertIn("Version agrees with the python entry point", names)
        self.assertIn("A failing command exits non-zero", names)

    def test_the_ledger_job_still_scaffolds_and_checks(self):
        job = self.workflow["jobs"]["ledger"]
        names = [step.get("name") for step in steps_of(job)]
        self.assertIn("Scaffold and check", names)
        self.assertIn("Entry points agree", names)

    def test_the_version_agreement_check_still_runs(self):
        job = self.workflow["jobs"]["ledger"]
        script = [step["run"] for step in steps_of(job)
                  if step.get("name") == "Version is in step with the manifest"]
        self.assertEqual(len(script), 1)
        self.assertIn(".claude-plugin/plugin.json", script[0])
        self.assertIn("__version__", script[0])

    def test_every_job_still_checks_the_repository_out(self):
        for name, job in sorted(self.workflow["jobs"].items()):
            checkouts = [step for step in steps_of(job)
                         if str(step.get("uses", "")).startswith("actions/checkout@")]
            self.assertTrue(checkouts, "%s never checks out" % name)


class SecurityPolicyTests(unittest.TestCase):
    """Criterion: a disclosure channel that exists, a supported-versions
    statement, and a response window."""

    def setUp(self):
        self.assertTrue(SECURITY.is_file(), "SECURITY.md is missing")
        self.text = SECURITY.read_text(encoding="utf-8")

    def headings(self):
        return [line.lstrip("#").strip().lower()
                for line in self.text.splitlines() if line.startswith("#")]

    def test_it_names_githubs_private_vulnerability_reporting_for_this_repo(self):
        """The channel must be one that actually exists. GitHub's private
        reporting form lives at a fixed path under the repository."""
        self.assertIn(
            "https://github.com/ansh-n-chovatiya/context-ledger/security/advisories/new",
            self.text,
        )

    def test_it_does_not_invent_a_security_mailbox(self):
        """A SECURITY.md that promises `security@...` when no such mailbox is
        monitored is worse than no SECURITY.md: a reporter follows it, gets no
        answer, and reasonably concludes the project does not care. Any email
        address appearing here would be a channel this project cannot honour."""
        addresses = re.findall(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", self.text)
        self.assertEqual(addresses, [],
                         "SECURITY.md must not name an address nobody reads")
        self.assertNotIn("mailto:", self.text.lower())

    def test_it_states_a_response_window_with_a_real_number(self):
        window = re.search(
            r"(\d+)\s*(business\s+)?days?", self.text, re.IGNORECASE)
        self.assertIsNotNone(window, "no acknowledgement window is stated")
        self.assertGreater(int(window.group(1)), 0)

    def test_it_has_a_supported_versions_statement(self):
        self.assertTrue(
            any("supported versions" in head for head in self.headings()),
            self.headings(),
        )

    def test_it_has_a_reporting_section_and_a_scope_section(self):
        heads = self.headings()
        self.assertTrue(any("report" in head for head in heads), heads)
        self.assertTrue(any("scope" in head for head in heads), heads)

    def test_it_tells_reporters_not_to_open_a_public_issue(self):
        self.assertRegex(self.text.lower(), r"not\b.{0,40}public issue")


class CodeownersTests(unittest.TestCase):

    def setUp(self):
        self.assertTrue(CODEOWNERS.is_file(), "CODEOWNERS is missing")
        self.text = CODEOWNERS.read_text(encoding="utf-8")

    def rules(self):
        """Parsed rules: (pattern, [owners]). Comments and blanks dropped, so
        a commented-out rule owns nothing."""
        found = []
        for line in self.text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            found.append((fields[0], fields[1:]))
        return found

    def test_there_is_a_catch_all_rule(self):
        patterns = [pattern for pattern, _ in self.rules()]
        self.assertIn("*", patterns, patterns)

    def test_every_rule_names_at_least_one_owner(self):
        for pattern, owners in self.rules():
            self.assertTrue(owners, "%s has no owner" % pattern)

    def test_every_owner_is_a_github_handle_or_team(self):
        for pattern, owners in self.rules():
            for owner in owners:
                self.assertRegex(
                    owner, r"^@[A-Za-z0-9][A-Za-z0-9-]*(/[A-Za-z0-9._-]+)?$",
                    "%s: %r is not a @handle or @org/team" % (pattern, owner),
                )

    def test_the_workflows_directory_has_an_owner(self):
        """The whole point of pinning is undone by an unreviewed workflow
        change, so `.github/` is claimed explicitly rather than by the
        catch-all."""
        patterns = [pattern for pattern, _ in self.rules()]
        self.assertTrue(
            any(pattern.rstrip("/").endswith(".github") for pattern in patterns),
            patterns,
        )


class DependabotTests(unittest.TestCase):

    def setUp(self):
        self.assertTrue(DEPENDABOT.is_file(), ".github/dependabot.yml is missing")
        self.config = load_workflow(DEPENDABOT.read_text(encoding="utf-8"))

    def ecosystems(self):
        return [entry.get("package-ecosystem")
                for entry in (self.config.get("updates") or [])]

    def test_it_declares_the_schema_version_dependabot_requires(self):
        self.assertEqual(self.config.get("version"), 2)

    def test_it_watches_github_actions(self):
        """The counterpart to pinning: a SHA pin freezes an action at a known
        artefact, and without something bumping it the pin freezes it at a
        known *stale* artefact too."""
        self.assertIn("github-actions", self.ecosystems())

    def test_the_github_actions_entry_is_scheduled_and_rooted(self):
        entry = [item for item in self.config["updates"]
                 if item.get("package-ecosystem") == "github-actions"][0]
        self.assertEqual(entry.get("directory"), "/")
        self.assertIn("interval", entry.get("schedule") or {})

    def test_no_ecosystem_is_declared_without_its_manifest(self):
        """Dependabot errors on an ecosystem whose manifest it cannot find, and
        a repository full of failing Dependabot runs is a repository where
        nobody reads Dependabot. `pyproject.toml` is being added by separate
        work; the `pip` entry may only follow it, never precede it."""
        manifests = {
            "pip": PYPROJECT,
            "uv": PYPROJECT,
            "cargo": ROOT / "Cargo.toml",
            "npm": ROOT / "package.json",
            "docker": ROOT / "Dockerfile",
        }
        for ecosystem in self.ecosystems():
            manifest = manifests.get(ecosystem)
            if manifest is not None:
                self.assertTrue(
                    manifest.is_file(),
                    "dependabot.yml declares %r but %s does not exist"
                    % (ecosystem, manifest.name),
                )

    def test_the_config_is_harmless_today(self):
        """Stated as a positive: with no Python manifest guaranteed to exist,
        the only ecosystem configured is one whose manifest is this repository
        itself — `.github/workflows/`, which is present by construction."""
        self.assertEqual(self.ecosystems(), ["github-actions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
