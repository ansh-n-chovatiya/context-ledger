"""The availability probe must not execute code that came with the repository.

`_availability` decides whether a verify command *could* run. For the
interpreter forms it asked a subprocess whether the module is importable. The
module name is attacker-controlled — it is a substring of `verify.cmd.run` in a
committed `.ctx/ctx.yaml` — and two facts turned that question into an answer
that runs code:

  * `importlib.util.find_spec("evilpkg.sub")` imports `evilpkg` to read its
    `__path__`, so `evilpkg/__init__.py` executes;
  * `python -c` puts the current working directory at the front of `sys.path`,
    and the probe inherited the caller's cwd — the cloned repository.

So a clone shipping `run: python3 -m evilpkg.sub` plus an `evilpkg/__init__.py`
ran that file on `ctx doctor`, the first command the docs give a new user. The
trust store then correctly refused to run the command it had just probed.

Every hostile-clone test here carries a positive control: the same fixture
package, imported deliberately with the repo on `sys.path`, must still write its
marker. Without that, these tests would pass just as happily if the fixture
stopped being a fixture at all.
"""

import os
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ctx import cli, trust  # noqa: E402
from support import Fixture  # noqa: E402


class HostileProbeFixture(Fixture):
    """A clone whose ledger names a module that lives in the clone."""

    def plant_package(self):
        """`evilpkg/__init__.py` — reached by resolving the *parent* of a
        dotted name."""
        marker = self.root / "pkg-marker.txt"
        self.write(
            "evilpkg/__init__.py",
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('owned')\n",
        )
        self.write("evilpkg/sub.py", "")
        return marker

    def plant_module(self):
        """`evilmod.py` — a top-level name, no parent package involved."""
        marker = self.root / "mod-marker.txt"
        self.write(
            "evilmod.py",
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('owned')\n",
        )
        return marker

    def hostile_ledger(self, command):
        """Overwrite the generated ctx.yaml with the attacker's.

        The command lands in both `verify:` (which `doctor` and `ci` probe) and
        `verify_candidates:` (which `init` probes), because the three entry
        points read different keys.
        """
        self.layout.config.write_text(
            "schema: 1\n"
            "profile: code\n"
            'level: "0"\n'
            "verify_candidates:\n"
            f"  - {command}\n"
            "verify:\n"
            "  - kind: cmd\n"
            f"    run: {command}\n",
            encoding="utf-8",
        )
        # A fresh clone: this machine has never accepted anything.
        store = trust.path_for(self.layout)
        store.exists() and store.unlink()

    def ctx(self, *args):
        """Run the real entry point *from inside the clone*.

        Not `self.cli(...)`: that calls `main()` in this process, whose cwd is
        the checkout running the tests, and the whole finding turns on the probe
        inheriting the cwd of a user standing in the hostile repository. An
        in-process call would report this hole as fixed while it was wide open.
        """
        env = dict(os.environ, PYTHONPATH=str(pathlib.Path(cli.__file__).parent.parent))
        return subprocess.run(
            [sys.executable, "-m", "ctx", *args],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=120,
        )

    def import_it(self, name):
        """The positive control: import the planted name on purpose, with the
        repository on `sys.path`, and confirm the fixture still detonates."""
        completed = subprocess.run(
            [sys.executable, "-c", f"import {name}"],
            cwd=str(self.root), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"the fixture stopped importing at all: {completed.stderr}",
        )


class TestTheProbeRunsNoRepositoryCode(HostileProbeFixture):

    def assertInert(self, marker, command):
        """No entry point that probes availability may fire the marker."""
        self.hostile_ledger(command)
        for entry in (("init",), ("doctor",), ("doctor", "--verify"), ("ci",)):
            completed = self.ctx(*entry)
            self.assertFalse(
                marker.exists(),
                f"`ctx {' '.join(entry)}` executed code from the repository "
                f"while probing {command!r}\n{completed.stdout}{completed.stderr}",
            )

    def test_a_dotted_name_does_not_import_its_parent_package(self):
        marker = self.plant_package()
        self.assertInert(marker, f"{cli._python_exe()} -m evilpkg.sub")

    def test_the_package_fixture_would_have_fired(self):
        marker = self.plant_package()
        self.import_it("evilpkg.sub")
        self.assertTrue(
            marker.exists(),
            "positive control: importing evilpkg.sub must write the marker, "
            "or the test above proves nothing",
        )

    def test_a_top_level_name_from_the_repository_is_not_run(self):
        marker = self.plant_module()
        self.assertInert(marker, f"{cli._python_exe()} -m evilmod")

    def test_the_module_fixture_would_have_fired(self):
        marker = self.plant_module()
        self.import_it("evilmod")
        self.assertTrue(
            marker.exists(),
            "positive control: importing evilmod must write the marker",
        )

    def test_the_repository_is_not_on_the_probe_path_at_all(self):
        """Stronger than the marker: a module planted in the clone must not
        even be *found*. If the clone can answer the probe, availability is
        reported on the strength of a file the attacker wrote — and the next
        loophole in `find_spec` lands straight back here."""
        self.plant_module()
        command = f"{cli._python_exe()} -m evilmod"
        self.hostile_ledger(command)
        out = self.ctx("doctor").stdout
        self.assertIn(f"MISS {command}", out,
                      f"the clone's own files answered the probe:\n{out}")


class TestTheProbeStillAnswersCorrectly(Fixture):
    """The probe exists because PATH alone accepts `python3 -m pytest` on a
    machine with no pytest. Isolating it must not cost that."""

    def test_a_module_that_genuinely_imports_is_available(self):
        self.assertEqual(
            cli._availability(f"{cli._python_exe()} -m json.tool"), (True, ""))

    def test_an_absent_module_is_reported_by_name(self):
        available, why = cli._availability(
            f"{cli._python_exe()} -m definitely_not_a_module_xyz")
        self.assertFalse(available)
        self.assertIn("definitely_not_a_module_xyz", why)

    def test_a_binary_off_path_is_reported_by_name(self):
        available, why = cli._availability("definitely_not_a_binary_xyz --version")
        self.assertFalse(available)
        self.assertIn("definitely_not_a_binary_xyz", why)
        self.assertIn("not on PATH", why)

    def test_an_empty_command_is_not_available(self):
        self.assertFalse(cli._availability("   ")[0])


class TestTheProbeCannotHangTheCaller(Fixture):

    def patch_run(self, replacement):
        original = cli.subprocess.run
        cli.subprocess.run = replacement
        self.addCleanup(setattr, cli.subprocess, "run", original)

    def test_a_timeout_is_reported_not_raised(self):
        def hangs(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

        self.patch_run(hangs)
        available, why = cli._availability(f"{cli._python_exe()} -m json.tool")
        self.assertFalse(available)
        self.assertIn("json.tool", why)

    def test_the_probe_is_bounded_and_runs_outside_the_repository(self):
        seen = {}

        def record(*args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs
            raise subprocess.TimeoutExpired(args[0], 20)

        self.patch_run(record)
        cli._availability(f"{cli._python_exe()} -m json.tool")
        self.assertLessEqual(seen["kwargs"].get("timeout", 999), 20)
        cwd = seen["kwargs"].get("cwd")
        self.assertIsNotNone(cwd, "the probe must not inherit the caller's cwd")
        self.assertNotIn(
            str(self.root), str(pathlib.Path(cwd).resolve()),
            "the probe's cwd must be outside the repository under test",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
