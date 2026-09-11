"""`ctx.atomic.write_text` — the shared temp-file + fsync + `os.replace` write
that `frontmatter.Document.write` already uses, lifted out so every other call
site that still truncates in place (`Path.write_text`) has something safe to
route through.

The positive control (`TestReplaceFailureLeavesOriginalIntact`) is the point
of this file: it fails against a plain `Path.write_text` and only passes
against `atomic.write_text`, proving the mock actually exercises the failure
window it claims to.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctx import atomic  # noqa: E402


class TestReplaceFailureLeavesOriginalIntact(unittest.TestCase):
    """A failure between the temp file being written and `os.replace` must
    leave the destination byte-identical to what it held before the call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_atomic_write_text_survives_a_failed_replace(self):
        path = self.root / "doc.md"
        path.write_text("original content\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")

        # Patch only os.replace — the point is that the temp file is already
        # fully written and fsynced by the time this raises, so the real
        # failure window (between the write completing and the rename) is
        # what gets exercised, not a stand-in for the whole function.
        with mock.patch("os.replace", side_effect=OSError("no space left on device")):
            with self.assertRaises(OSError):
                atomic.write_text(path, "new content\n")

        self.assertEqual(path.read_text(encoding="utf-8"), before,
                          "the original must be untouched when the replace fails")


class TestNoTempFileSurvives(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _debris(self):
        return [p.name for p in self.root.iterdir() if p.name != "doc.md"]

    def test_no_debris_on_success(self):
        path = self.root / "doc.md"
        atomic.write_text(path, "content\n")
        self.assertEqual(self._debris(), [])

    def test_no_debris_on_failure(self):
        path = self.root / "doc.md"
        path.write_text("original\n", encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("no space left on device")):
            with self.assertRaises(OSError):
                atomic.write_text(path, "new\n")
        self.assertEqual(self._debris(), [])


class TestParentDirectoryIsCreated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_parent_is_created(self):
        path = self.root / "nested" / "deeper" / "doc.md"
        self.assertFalse(path.parent.exists())
        result = atomic.write_text(path, "content\n")
        self.assertEqual(result, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "content\n")


class TestTempFileSharesDestinationDirectory(unittest.TestCase):
    """`os.replace` is only atomic within one filesystem, so staging in the
    system temp directory would trade a torn write for a cross-device error —
    exactly the regression this module exists to prevent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_temp_file_is_staged_beside_the_destination(self):
        path = self.root / "sub" / "doc.md"
        staged = {}
        real_replace = os.replace

        def spy(src, dst):
            staged["dir"] = os.path.dirname(os.path.realpath(src))
            staged["name"] = os.path.basename(src)
            return real_replace(src, dst)

        with mock.patch("os.replace", spy):
            atomic.write_text(path, "content\n")

        self.assertEqual(staged["dir"], os.path.realpath(str(path.parent)))
        self.assertTrue(staged["name"].startswith(".ctx-"))
        self.assertTrue(staged["name"].endswith(".tmp"))


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_write_then_read_matches(self):
        path = self.root / "doc.md"
        atomic.write_text(path, "hello\nworld\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\nworld\n")

    def test_overwrites_existing_content(self):
        path = self.root / "doc.md"
        atomic.write_text(path, "first\n")
        atomic.write_text(path, "second\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "second\n")


if __name__ == "__main__":
    unittest.main()
