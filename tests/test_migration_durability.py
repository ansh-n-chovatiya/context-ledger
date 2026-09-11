"""Migration durability: an interrupted run must leave a migratable ledger, and
`ctx migrate --check` must not report clean on a ledger it cannot actually read.

Two failures live here, both from report §3.3.

The first is the torn write. `migrate` wrote `ctx.yaml` and `plan.json` with a
plain `Path.write_text`, which truncates the destination before it writes a
byte. A run that dies in that window leaves a prefix of the new file where the
old one was — and the module's headline promise, "idempotent, a half-finished
run is recoverable by running it again", stops holding, because `_CONFIG_LINE`
happily matches against the garbage that is left.

The second is the blind spot. `discover()` never globbed the findings or phases
ledgers, so a tree whose findings ledgers sit at a schema this plugin misparses
reported *clean*. Findings gate merges: a misread findings ledger is a merge
that should have been blocked and was not.
"""

import ast
import contextlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from support import Fixture  # noqa: E402

from ctx import (  # noqa: E402
    config as config_mod, findings as findings_mod, frontmatter,
    migrate as migrate_mod, phases as phases_mod,
)


# --------------------------------------------------------------------------- #
# fault injection
# --------------------------------------------------------------------------- #

class Interrupted(Exception):
    """Stands in for the process dying part-way through one file's write.

    Deliberately not an `OSError` or a `ValueError`: `upgrade()` catches those
    and turns them into a reported problem, which is the *handled* path. A
    process that dies does not get to handle anything, so this propagates out
    of `upgrade` the way a `SIGKILL` would end the run.
    """


@contextlib.contextmanager
def dies_writing(target, prefix_chars=12):
    """Kill the run at the moment `target` is being written — however it is written.

    The same physical event (the machine loses power between the truncate and
    the last byte) reaches the two implementations at two different points, so
    the injection has to cover both or it would only ever exercise one:

    * a plain `Path.write_text` has *already truncated* `target` and is part
      way through refilling it, so the crash leaves a prefix on disk;
    * `atomic.write_text` has written and fsynced a temp file and is about to
      `os.replace` it into place, so the crash leaves `target` untouched.

    Nothing here is timing-dependent: the interruption is triggered by the
    destination path, so it lands on exactly the same file on every run and on
    any load.
    """
    real_write_text = Path.write_text
    real_replace = os.replace

    def torn_write_text(self, data, *args, **kwargs):
        if Path(self) == target:
            real_write_text(self, data[:prefix_chars], *args, **kwargs)
            raise Interrupted(f"died part-way through writing {target.name}")
        return real_write_text(self, data, *args, **kwargs)

    def die_before_replace(src, dst, *args, **kwargs):
        if Path(dst) == target:
            raise Interrupted(f"died before renaming into {target.name}")
        return real_replace(src, dst, *args, **kwargs)

    with mock.patch.object(Path, "write_text", torn_write_text), \
            mock.patch("os.replace", die_before_replace):
        yield


class MigrationFixture(Fixture):
    """A fixture whose ledger has been rolled back to schema 0 in several families."""

    SLUG = "demo-plan"
    UNIT = "01-first-unit"

    def downgrade_config(self):
        """Strip the `schema:` stamp — an unstamped `ctx.yaml` is schema 0."""
        text = self.layout.config.read_text(encoding="utf-8")
        text = migrate_mod._CONFIG_LINE.sub("", text, count=1)
        self.layout.config.write_text(text, encoding="utf-8")
        return self.layout.config

    def write_doc(self, path, meta, body="# body\n"):
        frontmatter.Document(dict(meta), body).write(path)
        return path

    def old_task(self, name="a-task"):
        return self.write_doc(self.layout.tasks / f"{name}.md",
                              {"task": name, "status": "pending"})

    def old_unit(self):
        return self.write_doc(
            self.layout.plans / self.SLUG / "units" / f"{self.UNIT}.md",
            {"unit": self.UNIT, "plan": self.SLUG})

    def old_graph(self):
        path = self.layout.plans / self.SLUG / "plan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"slug": self.SLUG, "units": []}, indent=2) + "\n",
                        encoding="utf-8")
        return path

    def old_findings(self):
        """An unstamped findings ledger, at the path `findings.path_for` produces."""
        path = findings_mod.path_for(self.layout, self.SLUG, self.UNIT)
        return self.write_doc(path, {"unit": self.UNIT, "plan": self.SLUG, "round": 1},
                              "# Findings\n\n## Open\n\n_None._\n")

    def old_phases(self):
        """An unstamped phase ledger, at the path `phases.path_for` produces."""
        path = phases_mod.path_for(self.layout, self.SLUG, self.UNIT)
        return self.write_doc(path, {"unit": self.UNIT, "plan": self.SLUG},
                              "# Phase record\n\n## Entries\n\n_None._\n")

    def versions(self):
        """{relative path: discovered schema} for the whole tree."""
        return {self.layout.rel(i.path): i.version
                for i in migrate_mod.discover(self.layout)}


# --------------------------------------------------------------------------- #
# 1-2. an interrupted bulk migration leaves a ledger that can still be migrated
# --------------------------------------------------------------------------- #

class TestInterruptedMigrationIsRecoverable(MigrationFixture):
    """`migrate`'s promise is that a half-finished run is fixed by rerunning it.
    That promise is only worth anything if the half-finished run left files that
    still parse."""

    def test_crash_writing_ctx_yaml_leaves_it_intact_and_migratable(self):
        config_path = self.downgrade_config()
        self.old_task()
        self.old_unit()
        before = config_path.read_text(encoding="utf-8")
        loaded = dict(config_mod.load(self.layout))
        self.assertEqual(self.versions()[self.layout.rel(config_path)], 0)

        with dies_writing(config_path):
            with self.assertRaises(Interrupted):
                migrate_mod.upgrade(self.layout)

        # Still the file it was: not a prefix, not a truncation.
        self.assertEqual(config_path.read_text(encoding="utf-8"), before,
                         "an interrupted migration must not leave a torn ctx.yaml")
        # Parseable, still meaning what it meant, and reporting the schema it
        # is genuinely at.
        self.assertEqual(dict(config_mod.load(self.layout)), loaded)
        self.assertEqual(self.versions()[self.layout.rel(config_path)], 0,
                         "--check must report the file's true schema, not a "
                         "number scraped out of a half-written file")
        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 1)
        self.assertIn("ctx.yaml", out)

        # And the documented recovery — rerun it — actually recovers.
        code, out = self.cli("migrate")
        self.assertEqual(code, 0, out)
        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to migrate", out)

    def test_crash_writing_plan_json_leaves_valid_json(self):
        """`plan.json` is the other plain write, and it is reached mid-run:
        the config and every markdown family migrate before it."""
        self.downgrade_config()
        self.old_task()
        self.old_unit()
        graph = self.old_graph()
        before = graph.read_text(encoding="utf-8")

        with dies_writing(graph):
            with self.assertRaises(Interrupted):
                migrate_mod.upgrade(self.layout)

        self.assertEqual(graph.read_text(encoding="utf-8"), before,
                         "an interrupted migration must not leave a torn plan.json")
        self.assertEqual(json.loads(graph.read_text(encoding="utf-8"))["slug"], self.SLUG)
        self.assertEqual(self.versions()[self.layout.rel(graph)], 0)

        code, out = self.cli("migrate")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.versions()[self.layout.rel(graph)], config_mod.SCHEMA)
        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- #
# 4. no plain Path.write_text in the modules this unit owns
# --------------------------------------------------------------------------- #

# Named one by one rather than globbed. A glob over `ctx/*.py` would also sweep
# in the runtime writers that are *deliberately* excluded — `trust.py`,
# `verify.py`'s output capture, `state.py`'s nudge, `hooks.py`'s error log —
# whose files are disposable and gitignored, and the first person to hit that
# failure would weaken the assertion until it caught nothing. `journal.py` and
# `plan.py` belong to sibling units in this plan and are enumerated there.
OWNED_MODULES = ("migrate.py", "snapshot.py", "bundle.py", "contract.py")


class TestOwnedModulesNeverWriteTextInPlace(unittest.TestCase):
    """Every write of a git-tracked ledger artifact in these modules goes
    through `atomic.write_text`. This is the guard that makes the *next* write
    someone adds fail here instead of shipping a torn file."""

    def _plain_write_text_calls(self, source, name):
        offenders = []
        for node in ast.walk(ast.parse(source, filename=name)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "write_text"):
                continue
            # `atomic.write_text(path, text)` is the sanctioned form.
            if isinstance(func.value, ast.Name) and func.value.id == "atomic":
                continue
            offenders.append((name, node.lineno))
        return offenders

    def test_no_module_calls_path_write_text(self):
        package = Path(__file__).resolve().parent.parent / "ctx"
        offenders = []
        for name in OWNED_MODULES:
            path = package / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            offenders += self._plain_write_text_calls(
                path.read_text(encoding="utf-8"), name)
        self.assertEqual(offenders, [],
                         "route these through ctx.atomic.write_text: "
                         + ", ".join(f"{n}:{line}" for n, line in offenders))

    def test_the_enumeration_would_catch_a_new_offender(self):
        """The guard above is only a guard if it can fail. Feed it one."""
        self.assertEqual(
            self._plain_write_text_calls(
                "def f(path):\n    path.write_text('x')\n", "sample.py"),
            [("sample.py", 2)])


# --------------------------------------------------------------------------- #
# 5-8. findings and phases ledgers are discovered, checked and migrated
# --------------------------------------------------------------------------- #

class TestFindingsAndPhasesAreMigratable(MigrationFixture):
    """Both families stamp `ctx_schema` when this plugin writes them, so both
    can be behind — and `--check` reporting clean on them is the plugin
    promising it can read a file it cannot."""

    def test_discover_reports_a_findings_ledger_and_its_schema(self):
        path = self.old_findings()
        self.assertEqual(self.versions().get(self.layout.rel(path)), 0)

    def test_discover_reports_a_phases_ledger_and_its_schema(self):
        path = self.old_phases()
        self.assertEqual(self.versions().get(self.layout.rel(path)), 0)

    def test_discover_reports_a_current_ledger_as_current(self):
        """A ledger this plugin just wrote is already stamped, so it is not
        'behind' — otherwise `--check` would red on every healthy repo."""
        ledger = findings_mod.Ledger(
            findings_mod.path_for(self.layout, self.SLUG, self.UNIT),
            self.SLUG, self.UNIT, layout=self.layout)
        ledger.save()
        phases_mod.Ledger(
            phases_mod.path_for(self.layout, self.SLUG, self.UNIT),
            self.SLUG, self.UNIT, layout=self.layout).save()
        versions = self.versions()
        self.assertEqual(versions[self.layout.rel(ledger.path)], config_mod.SCHEMA)
        behind, ahead = migrate_mod.pending(self.layout)
        self.assertEqual(([], []), (behind, ahead))

    def test_check_fails_and_names_an_out_of_date_findings_ledger(self):
        path = self.old_findings()
        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 1, out)
        self.assertIn(self.layout.rel(path), out)
        self.assertIn("findings", out)

    def test_check_fails_and_names_an_out_of_date_phases_ledger(self):
        path = self.old_phases()
        code, out = self.cli("migrate", "--check")
        self.assertEqual(code, 1, out)
        self.assertIn(self.layout.rel(path), out)
        self.assertIn("phases", out)

    def test_check_never_writes(self):
        path = self.old_findings()
        before = path.read_text(encoding="utf-8")
        self.assertEqual(self.cli("migrate", "--check")[0], 1)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_migrate_stamps_both_families_and_is_a_no_op_second_time(self):
        findings_path = self.old_findings()
        phases_path = self.old_phases()

        code, out = self.cli("migrate")
        self.assertEqual(code, 0, out)
        self.assertIn(self.layout.rel(findings_path), out)
        self.assertIn(self.layout.rel(phases_path), out)

        for path in (findings_path, phases_path):
            doc = frontmatter.read(path)
            self.assertEqual(doc.meta.get("ctx_schema"), config_mod.SCHEMA)

        # Re-running reports nothing to do, and changes nothing on disk.
        stamped = {p: p.read_text(encoding="utf-8")
                   for p in (findings_path, phases_path)}
        code, out = self.cli("migrate")
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to migrate", out)
        for path, text in stamped.items():
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_a_migrated_findings_ledger_still_loads(self):
        """Stamping must not cost the file its meaning to the reader that gates
        merges — a migration that breaks `findings.load` is worse than the drift."""
        self.old_findings()
        self.assertEqual(self.cli("migrate")[0], 0)
        ledger = findings_mod.load(self.layout, self.SLUG, self.UNIT)
        self.assertEqual(ledger.unit, self.UNIT)
        self.assertEqual(ledger.findings, [])

    def test_a_migrated_phase_ledger_still_loads(self):
        self.old_phases()
        self.assertEqual(self.cli("migrate")[0], 0)
        ledger = phases_mod.load(self.layout, self.SLUG, self.UNIT)
        self.assertEqual(ledger.unit_name, self.UNIT)


if __name__ == "__main__":
    unittest.main()
