"""Mutation coverage for two of the availability probe's four defence layers
(`ctx/detect.py:194-205`).

docs/history/ENTERPRISE-READINESS-REVIEW.md §3.7 mutation-tested the probe and found two of its four layers
armed by the existing suite (`tests/test_audit_probe_isolation.py`) and two
watched by nothing:

  * dropping the caller's `sys.path[0]` (`detect.py:211`) — survives deletion
    because `availability()` already runs the probe in a fresh, empty scratch
    directory (`cwd=elsewhere`), so a hostile package sitting in the real
    working directory is never reachable even without the filter;
  * validating the module name before it reaches the probe (`detect.py:244`)
    — survives deletion because the name is passed as `argv[1]`, never
    interpolated into the `-c` source string, so `find_spec` on a hostile
    string is inert rather than executed.

Both are equivalent mutants. Each class below proves it by deleting the named
layer and observing nothing changes, then — the positive control the report's
finding demands — also breaking the sibling layer that was actually doing the
work, and observing the *same* input now gets through.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctx import detect  # noqa: E402

_SOURCE = (ROOT / "ctx" / "detect.py").read_text(encoding="utf-8")
_START = "def availability(command, project_root):"
_END = "\n\ndef runnable(command, project_root):"
_AVAILABILITY_BLOCK = _SOURCE[_SOURCE.index(_START):_SOURCE.index(_END)]

# `ctx/detect.py:243-245` at last check — the module-name validation.
_NAME_VALIDATION = (
    "            if not _MODULE_NAME.match(top):\n"
    '                return False, f"{parts[0]} cannot import {module[0]}"\n'
)
assert _NAME_VALIDATION in _AVAILABILITY_BLOCK, (
    "ctx/detect.py's module-name validation text has drifted; "
    "update this test's anchor to match"
)

# `ctx/detect.py:263-266` at last check — argv-only invocation of the probe.
_ARGV_INVOCATION = (
    "                    ok = subprocess.run(\n"
    '                        [parts[0], "-c", _PROBE, top], capture_output=True,\n'
    "                        timeout=20, cwd=elsewhere,\n"
    "                    ).returncode == 0\n"
)
assert _ARGV_INVOCATION in _AVAILABILITY_BLOCK, (
    "ctx/detect.py's probe invocation text has drifted; update this test's anchor to match"
)


def _load_availability(block, *, probe, label):
    """Exec an isolated copy of `availability()` from source text extracted
    out of `ctx/detect.py`, so a textual mutation applied to `block` is a
    mutation of the real guard rather than of a reimplementation."""
    namespace = {
        "os": os, "sys": sys, "shutil": shutil, "subprocess": subprocess,
        "tempfile": tempfile, "_MODULE_NAME": detect._MODULE_NAME,
        "_PROBE": probe, "__name__": f"detect_mutant_{label}",
    }
    exec(compile(block, f"<mutant:{label}>", "exec"), namespace)  # noqa: S102
    return namespace["availability"]


class _FakeTempDir:
    """A `tempfile.TemporaryDirectory()` stand-in that hands back a directory
    the caller chose, instead of a fresh empty one."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        return False


class _FakeTempfileModule:
    def __init__(self, path):
        self._path = path

    def TemporaryDirectory(self, *args, **kwargs):
        return _FakeTempDir(self._path)


class TestProbeDropsSysPathZero(unittest.TestCase):
    """`detect.py:211` — the probe filters its own `sys.path[0]` (which
    `python -c` sets to the caller's cwd) back out."""

    def setUp(self):
        self.hostile = tempfile.TemporaryDirectory()
        self.addCleanup(self.hostile.cleanup)
        pkg = pathlib.Path(self.hostile.name) / "evilpkg"
        pkg.mkdir()
        # `find_spec` on a plain top-level name never executes this — if it
        # did, the assertions below would fail loudly rather than silently.
        (pkg / "__init__.py").write_text("raise SystemExit('should never run')\n")

        mutated = detect._PROBE.replace(
            "sys.path = [p for p in sys.path[1:] if p and os.path.abspath(p) != here]; ",
            "",
        )
        self.assertNotEqual(mutated, detect._PROBE, "the sys.path[0] filter text was not found")
        self.mutated_probe = mutated

    def patch_probe(self, value):
        original = detect._PROBE
        detect._PROBE = value
        self.addCleanup(setattr, detect, "_PROBE", original)

    def test_mutant_is_still_safe_because_the_scratch_cwd_catches_it(self):
        """With the filter gone, `evilpkg` is still not found, because
        `availability()` runs the probe with `cwd` set to a fresh, empty
        scratch directory rather than the caller's real working directory."""
        self.patch_probe(self.mutated_probe)
        available, why = detect.availability(
            f"{sys.executable} -m evilpkg", self.hostile.name)
        self.assertFalse(available)
        self.assertIn("evilpkg", why)

    def test_faking_the_scratch_dir_away_lets_the_hostile_cwd_through(self):
        """Positive control: with the filter *and* the fresh-scratch-directory
        layer both gone — the probe now runs with the hostile directory as
        its own cwd — `evilpkg` is found on `sys.path[0]` and availability is
        (wrongly) reported True. This is what proves the scratch directory,
        not some accident, was the layer doing the real work above."""
        self.patch_probe(self.mutated_probe)

        original_tempfile = detect.tempfile
        detect.tempfile = _FakeTempfileModule(self.hostile.name)
        self.addCleanup(setattr, detect, "tempfile", original_tempfile)

        available, why = detect.availability(
            f"{sys.executable} -m evilpkg", self.hostile.name)
        self.assertTrue(available, why)


class TestProbeModuleNameValidation(unittest.TestCase):
    """`detect.py:244` — a module name failing `_MODULE_NAME` is refused
    before it reaches the probe subprocess at all."""

    def setUp(self):
        no_validation = _AVAILABILITY_BLOCK.replace(_NAME_VALIDATION, "")
        self.assertNotEqual(
            no_validation, _AVAILABILITY_BLOCK,
            "the module-name validation text was not found to remove",
        )
        self.no_validation_block = no_validation

        self.marker_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.marker_dir, ignore_errors=True)
        self.marker = os.path.join(self.marker_dir, "pwned")
        # No spaces AND no dots: `availability()` tokenises the whole command
        # on whitespace first, so the hostile name has to survive that as one
        # token — and then, unvalidated or not, `top = module[0].split(".",
        # 1)[0]` (`ctx/detect.py`) still runs, truncating at the *first* dot
        # the way a real dotted import name would be. `echo.>path`, the usual
        # no-space idiom for an empty file on Windows, has exactly that dot
        # and was silently cut down to `echo` before ever reaching the shell
        # — the bug in the previous version of this fix, caught only because
        # the positive control it was meant to arm still failed. `echo>path`
        # (no dot) creates the file the same way and survives the split.
        #
        # OS-native otherwise, because the positive control below interpolates
        # this into a real `subprocess.run(..., shell=True)` call: POSIX `;`
        # chains commands and `${IFS}` expands to a space, both only once an
        # actual `/bin/sh` reads them — `cmd.exe` has neither, so the POSIX
        # payload is simply inert text there, and the positive control it is
        # meant to prove would fail to demonstrate the vulnerability rather
        # than demonstrating it, on the one platform in the CI matrix that
        # never runs `/bin/sh`. `&` chains commands in cmd.exe the way `;`
        # does in sh.
        if os.name == "nt":
            self.hostile_top = f"x&echo>{self.marker}"
        else:
            self.hostile_top = f"x;touch${{IFS}}{self.marker}"

    def test_mutant_is_still_safe_because_argv_is_never_shell_interpreted(self):
        """Mutant: the regex validation is gone. The hostile name still does
        nothing, because it reaches the child only as `argv[1]` — `find_spec`
        is asked about it, never a shell."""
        availability = _load_availability(
            self.no_validation_block, probe=detect._PROBE, label="no_validation",
        )
        available, why = availability(
            f"{sys.executable} -m {self.hostile_top}", self.marker_dir)

        self.assertFalse(available)
        self.assertFalse(
            os.path.exists(self.marker),
            "a hostile module name executed a command while probing availability",
        )

    def test_interpolating_it_into_a_shell_command_would_have_run_it(self):
        """Positive control: with validation gone *and* the probe invoked by
        interpolating the name into a shell string instead of passing it as
        a separate `argv` element, the same input now runs an arbitrary
        command — proving the argv/no-shell layer, not luck, is what kept the
        first test's input inert."""
        shell_invocation = (
            "                    ok = subprocess.run(\n"
            "                        f'{parts[0]} -c \"{_PROBE}\" {top}', shell=True,\n"
            "                        capture_output=True, timeout=20, cwd=elsewhere,\n"
            "                    ).returncode == 0\n"
        )
        vulnerable_block = self.no_validation_block.replace(_ARGV_INVOCATION, shell_invocation)
        self.assertNotEqual(
            vulnerable_block, self.no_validation_block,
            "the argv-invocation text was not found to replace",
        )
        availability = _load_availability(
            vulnerable_block, probe=detect._PROBE, label="shell_interpolated",
        )

        availability(f"{sys.executable} -m {self.hostile_top}", self.marker_dir)

        self.assertTrue(
            os.path.exists(self.marker),
            "removing both layers should have let the hostile name run a command",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
