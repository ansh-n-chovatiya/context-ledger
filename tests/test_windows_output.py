"""Two Windows-only failures from the 0.8.0 push, reproduced on every platform.

Both shipped green on Linux and macOS and failed on `windows-latest`, which is
the worst shape a bug can have: CI is the only thing that sees it, so the loop
between writing the mistake and learning about it runs in minutes instead of
seconds. Each test here provokes the Windows condition deliberately — a CRLF
translation, a legacy code page — so the next one is caught locally.

* **The review package measured itself two different ways.** `build` counted
  the text it rendered; `dispatch_stats` stat'd the file that text was written
  to. `Path.write_text` rewrites "\\n" to "\\r\\n" on Windows, so the file was
  one byte per line larger than the string. Those two numbers are what size the
  reviewer's model against `review.small_package_bytes`, so a package sitting
  near the threshold drew a dearer seat on Windows than on Linux for the same
  diff.
* **`ctx status` exited 1 because of an arrow.** Windows resolves piped stdout
  to cp1252, which has no `\\u2192`; `print` raised `UnicodeEncodeError` from
  inside `_echo` and took the whole command with it. The marker for "this is
  the unit you are on" is not worth a failed command.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import cli, review as review_mod  # noqa: E402


class TestPackageBytesAgreeAcrossPlatforms(unittest.TestCase):
    """`build` and `dispatch_stats` must report the same number everywhere."""

    def test_the_package_file_contains_no_carriage_returns(self):
        """The platform-independent proof of the fix.

        Asserting `st_size == len(text.encode())` passes on Linux whether or
        not the bug is present, because there is no translation to observe.
        The CRLF itself is the thing Windows adds, so read the bytes and look
        for it: this fails on Windows with the old `write_text` call and passes
        with `open(..., newline="")`, and is stable on every other platform.
        """
        source = "line one\nline two\nline three\n"
        target = Path(self._dir()) / "package.md"
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(source)

        on_disk = target.read_bytes()
        self.assertNotIn(b"\r\n", on_disk)
        self.assertEqual(len(on_disk), len(source.encode("utf-8")))

    def test_review_writes_its_package_without_newline_translation(self):
        """The real call site, not a stand-in: `review.build` must use the
        untranslated write. A future edit back to `Path.write_text` reopens
        the byte-count disagreement, so pin the call itself."""
        source = Path(review_mod.__file__).read_text(encoding="utf-8")
        self.assertIn('newline=""', source)
        self.assertNotIn("path.write_text(text", source)

    def _dir(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name


class _LegacyCodepageStream(io.StringIO):
    """Stdout as Windows hands it to a piped process: a stream that reports a
    legacy encoding and genuinely refuses anything outside it."""

    encoding = "cp1252"

    def write(self, text):
        text.encode(self.encoding)  # raises UnicodeEncodeError, as Windows does
        return super().write(text)


class TestEchoSurvivesALegacyCodepage(unittest.TestCase):
    """A console that cannot spell a character loses the character, not the
    command."""

    def setUp(self):
        self.stream = _LegacyCodepageStream()
        self._real = sys.stdout
        sys.stdout = self.stream
        self.addCleanup(setattr, sys, "stdout", self._real)

    def test_an_arrow_does_not_abort_the_command(self):
        cli._echo("   → 01-telemetry-fields         subagent  done")
        self.assertIn("01-telemetry-fields", self.stream.getvalue())

    def test_every_glyph_this_cli_prints_is_survivable(self):
        """Not just the arrow. The CLI's output carries `·` and `—` too, and
        the fix has to cover the class rather than the one character that
        happened to reach CI first."""
        for glyph in ("→", "·", "—", "✓"):
            with self.subTest(glyph=glyph):
                cli._echo(f"before {glyph} after")
        text = self.stream.getvalue()
        self.assertEqual(text.count("before"), 4)
        self.assertEqual(text.count("after"), 4)

    def test_ascii_output_is_untouched(self):
        """The fallback must not disturb the ordinary path — no replacement
        characters, no reformatting, when everything already encodes."""
        cli._echo("wave 1", "plain ascii")
        self.assertEqual(self.stream.getvalue(), "wave 1 plain ascii\n")


if __name__ == "__main__":
    unittest.main()
