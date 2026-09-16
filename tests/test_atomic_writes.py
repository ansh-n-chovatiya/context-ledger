"""`ctx.atomic.write_text` — the shared temp-file + fsync + `os.replace` write
that `frontmatter.Document.write` already uses, lifted out so every other call
site that still truncates in place (`Path.write_text`) has something safe to
route through.

The positive control (`TestReplaceFailureLeavesOriginalIntact`) is the point
of this file: it fails against a plain `Path.write_text` and only passes
against `atomic.write_text`, proving the mock actually exercises the failure
window it claims to.

`TestParentDirectoryIsFsynced` covers the parent-directory durability gap:
`os.replace` alone makes the new directory entry visible, but a crash right
after it can still lose that entry on some filesystems unless the
*directory* is fsynced too.
Each case spies on real `os.open`/`os.fsync` (wrapping, not replacing, the
real calls) so the write still actually happens, and asserts the parent
directory was opened read-only and that exact fd was later fsynced —
narrow enough that it fails against the pre-fix code, which never opens the
parent directory at all.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ctx import atomic, frontmatter, state  # noqa: E402
from ctx import log as log_mod  # noqa: E402
from ctx.paths import Layout  # noqa: E402


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


class _DirFsyncSpy:
    """Wraps real `os.open`/`os.fsync` and records which fds were opened
    read-only against a given directory, and which fds were later fsynced.

    Wrapping (not replacing) the real calls means the write under test still
    actually happens — this only observes it, proving the fsync that never
    happened on the pre-fix code, not just that the write still succeeded
    either way.
    """

    def __init__(self, directory):
        self.directory = os.path.realpath(str(directory))
        self.opened_dir_fds = []
        self.fsynced_fds = []
        self._real_open = os.open
        self._real_fsync = os.fsync

    def open_spy(self, path, flags, *args, **kwargs):
        fd = self._real_open(path, flags, *args, **kwargs)
        if flags == os.O_RDONLY and os.path.realpath(str(path)) == self.directory:
            self.opened_dir_fds.append(fd)
        return fd

    def fsync_spy(self, fd):
        self.fsynced_fds.append(fd)
        return self._real_fsync(fd)

    def assert_directory_was_fsynced(self, test):
        test.assertTrue(
            self.opened_dir_fds,
            f"parent directory {self.directory!r} was never opened read-only",
        )
        test.assertTrue(
            set(self.opened_dir_fds) & set(self.fsynced_fds),
            "the parent directory fd that was opened was never passed to os.fsync",
        )

    def patches(self):
        return (
            mock.patch("os.open", side_effect=self.open_spy),
            mock.patch("os.fsync", side_effect=self.fsync_spy),
        )


class TestParentDirectoryIsFsynced(unittest.TestCase):
    """Every atomic write path fsyncs the parent directory after
    `os.replace`, not just the temp file before it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_atomic_write_text_fsyncs_parent_directory(self):
        path = self.root / "doc.md"
        spy = _DirFsyncSpy(self.root)
        patch_open, patch_fsync = spy.patches()
        with patch_open, patch_fsync:
            atomic.write_text(path, "content\n")
        spy.assert_directory_was_fsynced(self)
        self.assertEqual(path.read_text(encoding="utf-8"), "content\n")

    def test_frontmatter_document_write_fsyncs_parent_directory(self):
        path = self.root / "unit.md"
        spy = _DirFsyncSpy(self.root)
        doc = frontmatter.Document({"status": "pending"}, "Body text.\n")
        patch_open, patch_fsync = spy.patches()
        with patch_open, patch_fsync:
            doc.write(path)
        spy.assert_directory_was_fsynced(self)
        self.assertIn("status: pending", path.read_text(encoding="utf-8"))

    def test_state_save_fsyncs_parent_directory(self):
        layout = Layout(self.root)
        runtime = layout.runtime
        spy = _DirFsyncSpy(runtime)
        patch_open, patch_fsync = spy.patches()
        with patch_open, patch_fsync:
            state.save(layout, dict(state.EMPTY))
        spy.assert_directory_was_fsynced(self)
        self.assertTrue(layout.state.is_file())


class TestAFailedDirectoryFsyncIsNotAWriteFailure(unittest.TestCase):
    """By the time `fsync_parent_dir` runs, `os.replace` has already made the
    new content durable — a transient failure to *also* fsync the directory
    is a weaker durability guarantee, not the write failing. Each of the
    three write paths used to call it inside the same try/except that rolls
    back an actually-failed write, so a raised `OSError` there propagated as
    though nothing had been written at all, even though the file on disk
    already held the new content.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # `atomic.write_text` reports this failure through `ctx.log` (the same
        # off-by-default, `CTX_LOG`-gated channel `lock.py`'s fail-open paths
        # use) rather than `warnings.warn` — a process-global mechanism any
        # other code in the same process can silence with one call, unlike a
        # destination the operator explicitly asked for.
        log_mod.reset_notices()
        self.addCleanup(log_mod.reset_notices)
        self.log_file = self.root / "diagnostics.log"
        env = dict(os.environ)

        def restore_env():
            os.environ.clear()
            os.environ.update(env)

        self.addCleanup(restore_env)
        os.environ[log_mod.ENV_LEVEL] = "warn"
        os.environ[log_mod.ENV_FILE] = str(self.log_file)

    def logged(self):
        if not self.log_file.exists():
            return ""
        return self.log_file.read_text(encoding="utf-8")

    def test_atomic_write_text_still_returns_and_the_content_still_lands(self):
        path = self.root / "doc.md"
        with mock.patch("ctx.atomic.fsync_parent_dir",
                        side_effect=OSError("EMFILE")):
            result = atomic.write_text(path, "content\n")
        self.assertEqual(result, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "content\n")
        self.assertIn("atomic.write_text.fsync_parent_dir", self.logged())

    def test_frontmatter_document_write_still_returns_and_the_content_still_lands(self):
        path = self.root / "unit.md"
        doc = frontmatter.Document({"status": "pending"}, "Body text.\n")
        with mock.patch("ctx.atomic.fsync_parent_dir",
                        side_effect=OSError("EMFILE")):
            doc.write(path)
        self.assertIn("status: pending", path.read_text(encoding="utf-8"))
        self.assertIn("frontmatter.write.fsync_parent_dir", self.logged())

    def test_the_failure_is_silent_without_ctx_log(self):
        """Off by default is the same bargain `lock.py`'s fail-open logging
        makes — this is the regression that would slip through if the two
        tests above mocked `ctx.log.warn` directly instead of reading the real
        destination `CTX_LOG_FILE` points at."""
        del os.environ[log_mod.ENV_LEVEL]
        del os.environ[log_mod.ENV_FILE]
        path = self.root / "doc.md"
        with mock.patch("ctx.atomic.fsync_parent_dir",
                        side_effect=OSError("EMFILE")):
            atomic.write_text(path, "content\n")
        self.assertFalse(self.log_file.exists())

    def test_state_save_still_returns_and_the_content_still_lands(self):
        layout = Layout(self.root)
        with mock.patch("ctx.atomic.fsync_parent_dir",
                        side_effect=OSError("EMFILE")):
            result = state.save(layout, dict(state.EMPTY))
        self.assertEqual(result, dict(state.EMPTY))
        self.assertTrue(layout.state.is_file())


if __name__ == "__main__":
    unittest.main()
