"""Mutation coverage for `_confined`'s absolute-path fast path (`ctx/verify.py`).

docs/history/ENTERPRISE-READINESS-REVIEW.md §3.7 mutation-tested this guard by deleting it and found the suite
did not notice — but the deletion turned out to be harmless: `_confined`
re-checks the resolved pair with `_inside()` after `realpath`, so an absolute
path such as `/etc/hosts` is still refused even without the fast path. That
makes the fast path an *equivalent mutant*, correct but unwatched. Nothing
pinned that finding, so this file arms it:

  * `TestExtractionIsFaithful` pins that a source-extracted, unmodified copy
    of `resolve_cwd`/`_confined`/`_inside` behaves exactly like the live
    module, so every mutation below is a mutation of the real guard, not of
    a hand-written stand-in that could silently diverge from it.
  * `TestTheFastPathIsRedundantButCorrect` deletes the fast path and shows
    the absolute path is still refused, then — the positive control the
    report's finding demands — deletes `_inside()` too and shows the *same*
    input now escapes, proving `_inside()` is what was doing the work above,
    not an accident of the input chosen.
  * `TestTheRealModuleStillDependsOnInside` repeats the causal claim against
    the production module itself (not an extracted copy), using a relative
    `..` path the fast path never sees at all, so a real regression in
    `_inside()` has a test that would fail because of it.
"""

import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctx import verify  # noqa: E402

_SOURCE = (ROOT / "ctx" / "verify.py").read_text(encoding="utf-8")
_START = "def resolve_cwd(check, cwd):"
_END = "\n\ndef _remaining(deadline):"
_BLOCK = _SOURCE[_SOURCE.index(_START):_SOURCE.index(_END)]

# `ctx/verify.py:591-593` at last check — the guard docs/history/ENTERPRISE-READINESS-REVIEW.md calls
# `_confined`'s absolute-path fast path.
_FAST_PATH = (
    '    if raw.startswith("~") or os.path.isabs(raw) or (os.name == "nt" and ":" in raw):\n'
    '        return "", ("path must be relative to the project — "\n'
    '                    "the gate refuses to read outside it")\n'
)
assert _FAST_PATH in _BLOCK, (
    "ctx/verify.py's absolute-path fast path text has drifted; "
    "update this test's anchor to match"
)

_INSIDE_DEF = (
    "def _inside(target, root):\n"
    "    try:\n"
    "        return os.path.commonpath([target, root]) == root\n"
    "    except ValueError:  # different drives on Windows, or a mix of abs and rel\n"
    "        return False\n"
)
assert _INSIDE_DEF in _BLOCK, (
    "ctx/verify.py's _inside() text has drifted; update this test's anchor to match"
)

_INSIDE_ALWAYS_TRUE = (
    "def _inside(target, root):\n"
    "    return True  # mutated: the deeper realpath/commonpath check is gone\n"
)


def _load(block, *, label):
    """Exec an isolated copy of `resolve_cwd`/`_confined`/`_inside` from source
    text extracted out of `ctx/verify.py`, so a textual mutation applied to
    `block` is a mutation of the real guard rather than of a reimplementation."""
    namespace = {"os": os, "__name__": f"verify_mutant_{label}"}
    exec(compile(block, f"<mutant:{label}>", "exec"), namespace)  # noqa: S102
    return namespace["resolve_cwd"], namespace["_confined"], namespace["_inside"]


class TestExtractionIsFaithful(unittest.TestCase):
    """Before mutating anything: the unmodified extraction must match the
    live module, or the mutants below are exercising a hand-copy."""

    def test_baseline_matches_the_real_module(self):
        _, confined, inside = _load(_BLOCK, label="baseline")
        with tempfile.TemporaryDirectory() as root:
            for raw in ("docs/guide.md", "../outside", "/etc/hosts", "~/x"):
                self.assertEqual(
                    confined(raw, {}, root), verify._confined(raw, {}, root),
                    f"extracted _confined diverges from the real one for {raw!r}",
                )
            self.assertIs(inside(root, root), verify._inside(root, root))


class TestTheFastPathIsRedundantButCorrect(unittest.TestCase):

    def setUp(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.secret = os.path.join(outside.name, "id_rsa")
        with open(self.secret, "w", encoding="utf-8") as fh:
            fh.write("BEGIN OPENSSH PRIVATE KEY")
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = project.name

    def test_deleting_the_fast_path_still_refuses_an_absolute_path(self):
        """Mutant: the isabs()/startswith('~') fast path is gone. If `_inside()`
        genuinely re-checks the resolved pair, an absolute path outside the
        project must still come back refused."""
        mutated = _BLOCK.replace(_FAST_PATH, "")
        self.assertNotEqual(mutated, _BLOCK, "the fast-path text was not found to remove")
        _, confined, _ = _load(mutated, label="no_fast_path")

        target, refusal = confined(self.secret, {}, self.project)

        self.assertEqual(target, "")
        self.assertTrue(refusal, "an absolute path outside the project was accepted")

    def test_removing_inside_too_turns_the_same_input_into_an_escape(self):
        """Positive control for the test above: with the fast path *and*
        `_inside()` both gone, the identical input is no longer refused —
        proving `_inside()`, not some other accident, was the layer doing the
        work. Without this, the previous test would pass just as happily if
        `_confined` refused everything unconditionally."""
        mutated = _BLOCK.replace(_FAST_PATH, "").replace(_INSIDE_DEF, _INSIDE_ALWAYS_TRUE)
        self.assertNotIn(_FAST_PATH, mutated)
        self.assertNotIn(_INSIDE_DEF, mutated)
        _, confined, _ = _load(mutated, label="no_fast_path_no_inside")

        target, refusal = confined(self.secret, {}, self.project)

        self.assertEqual(refusal, "")
        self.assertEqual(
            target, os.path.realpath(self.secret),
            "removing both layers should have let the absolute path through",
        )


class TestTheRealModuleStillDependsOnInside(unittest.TestCase):
    """Same causal claim, against the production module rather than an
    extracted copy — a relative `..` escape never reaches the fast path at
    all, so this pins `_inside()` directly: if it regressed in
    `ctx/verify.py`, this is the test that would fail because of it."""

    def test_breaking_inside_on_the_real_module_lets_a_dotdot_escape_through(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = os.path.join(outside.name, "id_rsa")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("BEGIN OPENSSH PRIVATE KEY")
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        relative = os.path.relpath(secret, project.name)
        self.assertIn("..", relative)

        # Sanity: with the real, unbroken `_inside`, this is refused today.
        _, refusal_today = verify._confined(relative, {}, project.name)
        self.assertTrue(refusal_today, "the dotdot escape should be refused before mutation")

        original = verify._inside
        verify._inside = lambda target, root: True
        try:
            target, refusal = verify._confined(relative, {}, project.name)
        finally:
            verify._inside = original

        self.assertEqual(refusal, "")
        self.assertEqual(target, os.path.realpath(secret))


if __name__ == "__main__":
    unittest.main(verbosity=2)
