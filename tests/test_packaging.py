"""What `pip install context-ledger` is allowed to mean.

Before `pyproject.toml` existed, "we run ctx 0.8.0" was not a checkable
statement: there were no tags, `.claude-plugin/marketplace.json` declares
`"source": "./"` so the plugin *was* whatever `main` held at each engineer's
last sync, and no scanner — pip-audit, Dependabot, Snyk, Syft — had a manifest
to read. Packaging is where that evidence lives, so the two claims the project
actually rests on are asserted here rather than described in prose:

  * **zero runtime dependencies.** The runtime is stdlib top to bottom. That is
    the strongest supply-chain claim this project has, and an empty
    `dependencies` list is its machine-readable form. Adding one turns this
    suite red, so it has to be a deliberate act.
  * **one version, not three.** `ctx/__init__.py` is the source of truth;
    `pyproject.toml` derives from it and `.claude-plugin/plugin.json` is held
    equal to it.

And one boundary: `.github/workflows/release.yml` builds and attaches artefacts
to a GitHub Release. It **does not publish**, to any index, by any mechanism.
`NoPublishStepTests` asserts that structurally so the boundary cannot be
crossed by an accident three months from now.

Two notes on method:

  * the workflow assertions parse the file and read *structure*, and where they
    do look at text they look at text with comments removed. This file's own
    subject matter is full of the words it forbids ("twine", "id-token"), and
    `report.md:179` records meta-tests in this repository that a prose comment
    would have satisfied. `strip_comments` is imported from
    `tests/test_ci_floor.py` along with the workflow reader: duplicating a YAML
    parser to avoid a test-to-test import would be the worse trade.
  * `tomllib` arrived in 3.11 and this package supports 3.8, so a small TOML
    subset reader sits below. Where `tomllib` exists, `TomlFallbackTests`
    parses the real `pyproject.toml` both ways and asserts the two agree, so
    the fallback cannot quietly drift into reading a different file than the
    one Python itself reads.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The workflow reader is shared with the CI-floor tests on purpose: one strict
# YAML subset for this repository, not two.
from test_ci_floor import load_workflow, strip_comments  # noqa: E402

from ctx import __version__ as CTX_VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


# --------------------------------------------------------------------------
# A strict TOML subset, enough for a pyproject.
# --------------------------------------------------------------------------

class TomlSubsetError(ValueError):
    pass


def _parse_value(text, pos):
    """Return (value, next_pos) for the value starting at `pos`."""
    pos = _skip(text, pos)
    if pos >= len(text):
        raise TomlSubsetError("value expected at end of file")
    char = text[pos]
    if char == '"':
        end = pos + 1
        out = []
        while end < len(text) and text[end] != '"':
            if text[end] == "\\":
                out.append(text[end + 1])
                end += 2
                continue
            out.append(text[end])
            end += 1
        if end >= len(text):
            raise TomlSubsetError("unterminated string")
        return "".join(out), end + 1
    if char == "[":
        items = []
        pos += 1
        while True:
            pos = _skip(text, pos)
            if pos >= len(text):
                raise TomlSubsetError("unterminated array")
            if text[pos] == "]":
                return items, pos + 1
            value, pos = _parse_value(text, pos)
            items.append(value)
            pos = _skip(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
    if char == "{":
        table = {}
        pos += 1
        while True:
            pos = _skip(text, pos)
            if pos >= len(text):
                raise TomlSubsetError("unterminated inline table")
            if text[pos] == "}":
                return table, pos + 1
            key, pos = _parse_key(text, pos)
            pos = _skip(text, pos)
            if text[pos] != "=":
                raise TomlSubsetError("expected '=' in inline table")
            value, pos = _parse_value(text, pos + 1)
            table[key] = value
            pos = _skip(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos += 1
    bare = re.match(r"[^,\]\}\n]+", text[pos:])
    if bare is None:
        raise TomlSubsetError("unreadable value at offset %d" % pos)
    word = bare.group(0).strip()
    end = pos + len(bare.group(0))
    if word in ("true", "false"):
        return word == "true", end
    if re.match(r"^-?\d+$", word):
        return int(word), end
    raise TomlSubsetError("outside the subset: %r" % word)


def _parse_key(text, pos):
    if text[pos] == '"':
        return _parse_value(text, pos)
    bare = re.match(r"[A-Za-z0-9_.-]+", text[pos:])
    if bare is None:
        raise TomlSubsetError("expected a key at offset %d" % pos)
    return bare.group(0), pos + len(bare.group(0))


def _skip(text, pos):
    """Advance past whitespace, newlines and whole-line/trailing comments."""
    while pos < len(text):
        if text[pos] in " \t\r\n":
            pos += 1
        elif text[pos] == "#":
            newline = text.find("\n", pos)
            pos = len(text) if newline < 0 else newline + 1
        else:
            return pos
    return pos


def _toml_subset(text):
    """Parse the subset a pyproject needs: tables, keys, strings, arrays,
    inline tables, ints and bools. Anything else raises rather than being
    guessed at."""
    root = {}
    table = root
    pos = 0
    while True:
        pos = _skip(text, pos)
        if pos >= len(text):
            return root
        if text[pos] == "[":
            close = text.index("]", pos)
            table = root
            for part in text[pos + 1:close].split("."):
                table = table.setdefault(part.strip(), {})
            pos = close + 1
            continue
        key, pos = _parse_key(text, pos)
        pos = _skip(text, pos)
        if pos >= len(text) or text[pos] != "=":
            raise TomlSubsetError("expected '=' after key %r" % key)
        value, pos = _parse_value(text, pos + 1)
        table[key] = value


def load_pyproject(text=None):
    if text is None:
        text = PYPROJECT.read_text(encoding="utf-8")
    return _toml_subset(text)


def plugin_manifest():
    return json.loads(PLUGIN.read_text(encoding="utf-8"))


def release_workflow():
    return load_workflow(RELEASE.read_text(encoding="utf-8"))


def _uses(workflow):
    """Every `uses:` value in the workflow, in file order."""
    found = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "uses" in step:
                found.append(step["uses"])
    return found


def _scripts(workflow):
    """Every `run:` body in the workflow."""
    found = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "run" in step:
                found.append(step["run"])
    return found


class TomlFallbackTests(unittest.TestCase):
    """The subset reader must read the same file `tomllib` does."""

    def test_agrees_with_tomllib(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib is 3.11+; the fallback is what runs here")
        with PYPROJECT.open("rb") as handle:
            expected = tomllib.load(handle)
        self.assertEqual(load_pyproject(), expected)

    def test_refuses_what_it_cannot_read(self):
        with self.assertRaises(TomlSubsetError):
            _toml_subset("key = 2026-09-11\n")


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.project = load_pyproject()["project"]

    def test_python_floor_is_an_install_constraint(self):
        # The README's "3.8+" becomes something pip enforces, so a 3.7 box is
        # refused at install time rather than at the first f-string.
        self.assertEqual(self.project["requires-python"], ">=3.8")

    def test_license_metadata_is_present(self):
        license_field = self.project["license"]
        text = license_field if isinstance(license_field, str) else license_field.get("text")
        self.assertEqual(text, "MIT")
        self.assertIn(
            "License :: OSI Approved :: MIT License", self.project["classifiers"]
        )
        self.assertTrue((ROOT / "LICENSE").is_file())

    def test_console_script_points_at_the_real_entry_point(self):
        self.assertEqual(load_pyproject()["project"]["scripts"], {"ctx": "ctx.cli:main"})
        # Not just a string that looks right: the target has to import and be
        # callable, and it has to be the same one bin/ctx.py already calls.
        from ctx.cli import main

        self.assertTrue(callable(main))
        self.assertIn(
            "from ctx.cli import main",
            (ROOT / "bin" / "ctx.py").read_text(encoding="utf-8"),
        )

    def test_distribution_name_is_context_ledger(self):
        self.assertEqual(self.project["name"], "context-ledger")


class ZeroRuntimeDependencyTests(unittest.TestCase):
    """The claim the whole supply-chain story rests on.

    Structural, not a grep: an added dependency is a non-empty list here no
    matter how it is formatted, and the two back doors — a `dynamic` entry that
    lets the backend inject dependencies at build time, and a `[project]`
    dependency table added under another spelling — are closed too.
    """

    def setUp(self):
        self.project = load_pyproject()["project"]

    def test_no_runtime_dependencies(self):
        self.assertEqual(
            self.project.get("dependencies", []),
            [],
            "context-ledger promises a stdlib-only runtime that adds nothing to "
            "a host project's dependency tree. A runtime dependency has to be a "
            "deliberate decision, recorded, not a drive-by edit to pyproject.toml.",
        )

    def test_dependencies_are_not_smuggled_in_dynamically(self):
        # `dynamic = ["dependencies"]` hands the list to the build backend,
        # where the test above cannot see it.
        self.assertNotIn("dependencies", self.project.get("dynamic", []))
        self.assertNotIn("optional-dependencies", self.project.get("dynamic", []))

    def test_no_optional_dependency_groups(self):
        self.assertEqual(self.project.get("optional-dependencies", {}), {})

    def test_the_runtime_really_is_importable_without_installing_anything(self):
        # The metadata claim, corroborated against the code: every top-level
        # import in the package resolves to the standard library or to ctx.
        # `sys.stdlib_module_names` is 3.10+. The previous form was
        # `set(getattr(...)) or None`, which on 3.8 and 3.9 collapsed an empty
        # set to None, skipped the filter entirely, and reported every stdlib
        # import as a foreign dependency. It never fired because this suite had
        # not reached a 3.8/3.9 runner since the test was written — the first
        # push to CI failed on it. `skipTest` is the honest answer: on an
        # interpreter that cannot enumerate its own stdlib, this check has
        # nothing to check.
        stdlib_ok = set(getattr(sys, "stdlib_module_names", ()))
        if not stdlib_ok:
            self.skipTest("sys.stdlib_module_names is 3.10+; nothing to "
                          "compare against on this interpreter")
        imported = set()
        for module in sorted((ROOT / "ctx").glob("*.py")):
            for line in module.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", line)
                if match:
                    imported.add(match.group(1).split(".")[0])
        foreign = {name for name in imported
                   if name not in ("ctx",) and name not in stdlib_ok}
        self.assertEqual(foreign, set(), "non-stdlib top-level import in ctx/")


class OneVersionTests(unittest.TestCase):
    """Derived, then checked.

    The version is *derived*: `[tool.hatch.version] path` reads
    `ctx/__init__.py`, so `pyproject.toml` has no version string of its own to
    drift. Derivation alone would still leave `plugin.json` free to wander, so
    the equality is asserted here as well — CI's `ledger` job asserts the same
    pair, and this makes it true for anyone running the suite locally.
    """

    def setUp(self):
        self.config = load_pyproject()

    def test_pyproject_does_not_restate_the_version(self):
        self.assertNotIn("version", self.config["project"])
        self.assertIn("version", self.config["project"]["dynamic"])

    def test_the_derivation_reads_the_source_of_truth(self):
        self.assertEqual(self.config["tool"]["hatch"]["version"]["path"], "ctx/__init__.py")
        self.assertTrue((ROOT / "ctx" / "__init__.py").is_file())

    def test_all_three_sources_agree(self):
        source = (ROOT / "ctx" / "__init__.py").read_text(encoding="utf-8")
        declared = re.search(r'__version__ = "([^"]+)"', source).group(1)
        derived_from = self.config["tool"]["hatch"]["version"]["path"]
        self.assertEqual(derived_from, "ctx/__init__.py")
        self.assertEqual(declared, CTX_VERSION)
        self.assertEqual(
            plugin_manifest()["version"],
            CTX_VERSION,
            ".claude-plugin/plugin.json and ctx.__version__ describe the same "
            "release or neither of them means anything.",
        )


class SdistContentsTests(unittest.TestCase):
    """A guard on the allowlist. The archive itself is inspected at release
    time; what this catches is the allowlist rotting — a path removed, or a
    directory of ledger state added back."""

    def setUp(self):
        self.include = load_pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"][
            "include"
        ]

    def test_a_rebuild_has_what_it_needs(self):
        for required in ("/ctx", "/bin", "/.claude-plugin", "/pyproject.toml"):
            self.assertIn(required, self.include)

    def test_ledger_state_and_fixtures_stay_out(self):
        for path in self.include:
            self.assertFalse(
                path.startswith("/.ctx"),
                "%s would ship this repository's own ledger state" % path,
            )
            self.assertFalse(
                path.startswith("/tests"),
                "%s would ship test fixtures" % path,
            )

    def test_every_allowlisted_path_exists(self):
        for path in self.include:
            self.assertTrue(
                (ROOT / path.lstrip("/")).exists(), "%s is allowlisted but absent" % path
            )

    def test_the_wheel_is_the_package_only(self):
        wheel = load_pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertEqual(wheel["packages"], ["ctx"])


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = release_workflow()

    def test_triggers_on_a_version_tag(self):
        self.assertEqual(self.workflow["on"]["push"]["tags"], ["v*"])
        # A tag, and only a tag: artefacts are named after `v*`, so a run that
        # is not anchored to one produces something nobody can pin.
        self.assertEqual(list(self.workflow["on"]), ["push"])
        self.assertEqual(list(self.workflow["on"]["push"]), ["tags"])

    def test_produces_four_artifacts(self):
        body = "\n".join(_scripts(self.workflow))
        self.assertIn("--wheel", body)
        self.assertIn("--sdist", body)
        self.assertIn("sha256sum", body)
        self.assertIn("SHA256SUMS", body)
        self.assertIn("cyclonedx-py", body)
        self.assertIn(".cdx.json", body)

    def test_all_four_attach_to_the_release(self):
        attach = [line for line in "\n".join(_scripts(self.workflow)).splitlines()
                  if "gh release create" in line]
        self.assertTrue(attach, "nothing attaches artefacts to a GitHub Release")
        step = next(script for script in _scripts(self.workflow)
                    if "gh release create" in script)
        for artifact in ("dist/*.whl", "dist/*.tar.gz", "dist/SHA256SUMS",
                         "dist/context-ledger.cdx.json"):
            self.assertIn(artifact, step)

    def test_every_action_is_pinned_to_a_commit_sha(self):
        used = _uses(self.workflow)
        self.assertTrue(used, "no actions found — did the parse silently fail?")
        for reference in used:
            self.assertRegex(
                reference,
                r"^[^@]+@[0-9a-f]{40}$",
                "%s is a mutable reference; pin it to a full commit SHA" % reference,
            )

    def test_least_privilege_permissions(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        jobs = self.workflow["jobs"]
        self.assertEqual(list(jobs), ["release"])
        # `contents: write` attaches release assets. Nothing more is needed and
        # nothing more is granted.
        self.assertEqual(jobs["release"]["permissions"], {"contents": "write"})

    def test_the_tag_is_checked_against_the_source(self):
        body = "\n".join(_scripts(self.workflow))
        self.assertIn("GITHUB_REF_NAME", body)
        self.assertIn("plugin.json", body)


class NoPublishStepTests(unittest.TestCase):
    """The boundary: build and attach, never publish.

    Trusted Publishing needs registry-side configuration only the maintainer
    can do, and a half-wired publish step fails on tag day in the one workflow
    nobody rehearses. These assertions read the workflow with comments removed,
    because the file explains this policy in prose using the very words being
    forbidden — a test that grepped the raw text would fail on its own rationale.
    """

    def setUp(self):
        self.raw = RELEASE.read_text(encoding="utf-8")
        self.code = strip_comments(self.raw)
        self.workflow = release_workflow()

    def test_no_publishing_action(self):
        for banned in ("pypa/gh-action-pypi-publish", "gh-action-pypi-publish"):
            self.assertNotIn(banned, self.code)
        for reference in _uses(self.workflow):
            self.assertNotIn("pypi", reference.lower())

    def test_no_twine_upload(self):
        lowered = self.code.lower()
        self.assertNotIn("twine", lowered)
        self.assertNotIn("upload-to-pypi", lowered)
        # `pip install twine` is caught above; this catches the invocation
        # under any other name it might be installed as.
        self.assertNotIn("upload --repository", lowered)

    def test_no_id_token_permission(self):
        # Structural first: no permissions block anywhere mentions id-token,
        # which is the credential a Trusted Publishing step would need.
        blocks = [self.workflow.get("permissions") or {}]
        for job in self.workflow["jobs"].values():
            blocks.append(job.get("permissions") or {})
        for block in blocks:
            self.assertNotIn("id-token", block)
        # And textually, to catch it appearing anywhere else at all.
        self.assertNotIn("id-token", self.code)

    def test_the_prose_that_explains_the_rule_is_not_what_passes_it(self):
        # A guard on the guard. The workflow's rationale comments genuinely do
        # contain the banned words, so the tests above are only meaningful if
        # `strip_comments` really removes comments — proven here against a
        # sample where the banned word appears *only* in a comment.
        self.assertIn("twine", self.raw.lower())
        sample = "steps:\n  # twine upload dist/*\n  - run: echo built\n"
        self.assertNotIn("twine", strip_comments(sample))
        self.assertIn("echo built", strip_comments(sample))


if __name__ == "__main__":
    unittest.main()
