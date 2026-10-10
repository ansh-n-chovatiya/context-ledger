"""The gate's failure excerpt goes through `reduce`, then `redact`, in that order.

Head+tail (`verify.truncate`) threw away the middle of a long run — which is
exactly where a unittest failure block lands when a suite prints a lot. These
tests pin the new shape against the old one: the failing assertion now
survives, plain output is unchanged, the raw log is untouched, secrets are
scrubbed from whatever survives, the two new caps are configurable and fail
soft, and the journal never takes more than one bounded line of output.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import config as config_mod, journal, redact, verify  # noqa: E402
from support import Fixture  # noqa: E402

ASSERTION = "AssertionError: 41 != 42 : the-sentinel-assertion"
SUMMARY = "FAILED (failures=1)"


def unittest_run_600_lines():
    """600 lines with one failing unittest block in the middle of them."""
    lines = [f"noise before {i}" for i in range(300)]
    lines += [
        "=" * 70,
        "FAIL: test_answer (pkg.test_mod.TestAnswer.test_answer)",
        "-" * 70,
        "Traceback (most recent call last):",
        '  File "/src/pkg/test_mod.py", line 9, in test_answer',
        "    self.assertEqual(41, 42, 'the-sentinel-assertion')",
        ASSERTION,
    ]
    lines += [f"noise after {i}" for i in range(600 - len(lines) - 4)]
    lines += ["-" * 70, "Ran 12 tests in 0.204s", "", SUMMARY]
    assert len(lines) == 600
    return "\n".join(lines) + "\n"


class _GateCase(Fixture):
    def fail_with(self, output, key="k"):
        """Run one cmd check that prints `output` and exits 1."""
        source = self.root / "output.txt"
        source.write_text(output, encoding="utf-8")
        command = (
            f'"{sys.executable}" -c "import sys; '
            f"sys.stdout.write(open('output.txt', encoding='utf-8').read()); "
            f'sys.exit(1)"'
        )
        checks = [{"kind": "cmd", "run": command}]
        self.trust(checks)
        results, verdict = verify.run(
            self.layout, self.config, checks, cwd=self.root, key=key,
        )
        self.assertEqual(verdict, verify.FAIL)
        return command, results[0]

    @staticmethod
    def excerpt_of(message):
        """The excerpt between the `exit N` line and the log pointer."""
        body = message.split("\n", 1)[1]
        return body.rsplit("\n(full output: ", 1)[0]

    def old_excerpt(self, output):
        gate = self.config["gate"]
        return redact.scrub(
            verify.truncate(output, gate["output_head"], gate["output_tail"]),
            self.config.get("redact") or [],
        )


class TestFailureShape(_GateCase):
    def test_a_failure_in_the_middle_reaches_the_model(self):
        output = unittest_run_600_lines()
        _command, result = self.fail_with(output)
        self.assertIn(ASSERTION, result.message)
        self.assertIn(SUMMARY, result.message)
        self.assertTrue(result.message.startswith("exit 1\n"))
        self.assertRegex(result.message, r"\n\(full output: [^\n]+\)$")

    def test_positive_control_head_and_tail_drop_it(self):
        """The old cut genuinely loses the assertion — so the test above is
        measuring the reducer, not a fixture too small to need one."""
        output = unittest_run_600_lines()
        old = self.old_excerpt(output)
        self.assertNotIn(ASSERTION, old)
        self.assertIn(SUMMARY, old)

    def test_unrecognised_output_is_exactly_what_it_was(self):
        output = "\n".join(f"plain line {i}" for i in range(300)) + "\n"
        _command, result = self.fail_with(output)
        self.assertEqual(self.excerpt_of(result.message), self.old_excerpt(output))

    def test_truncate_is_unchanged(self):
        def original(text, head, tail):  # the pre-change body, verbatim
            lines = (text or "").strip().splitlines()
            if len(lines) <= head + tail:
                return "\n".join(lines)
            omitted = len(lines) - head - tail
            return "\n".join(
                lines[:head] + [f"… {omitted} lines omitted …"]
                + (lines[-tail:] if tail else [])
            )

        samples = [
            "\n".join(str(i) for i in range(100)), "  a\nb  \n", None, "",
            "\n".join("abcdef"), "x\r\ny\r\n" * 40, unittest_run_600_lines(),
        ]
        for text in samples:
            for head, tail in ((3, 2), (2, 0), (40, 20), (0, 0)):
                with self.subTest(text=(text or "")[:20], head=head, tail=tail):
                    self.assertEqual(verify.truncate(text, head, tail),
                                     original(text, head, tail))


class TestRawLog(_GateCase):
    def test_the_log_is_raw_and_complete(self):
        output = unittest_run_600_lines()
        command, result = self.fail_with(output, key="raw-log")
        path = self.layout.verify_logs / "raw-log.log"
        self.assertTrue(path.is_file())
        self.assertIn(self.layout.rel(path), result.message)
        written = path.read_text(encoding="utf-8")
        self.assertEqual(written, f"$ {command}\n\n{output}")
        for i in range(300):
            self.assertIn(f"noise before {i}\n", written)


class TestSecretsAfterReduction(_GateCase):
    def test_secrets_in_collapsed_and_kept_lines_are_scrubbed(self):
        self.config["redact"] = [r"hunter2-[a-z0-9]+"]
        collapsed_secret = "hunter2-collapsed9"
        kept_secret = "hunter2-keptblock7"
        lines = [f"connecting with token={collapsed_secret}"] * 200
        lines += [f"noise {i}" for i in range(100)]
        lines += [
            "=" * 70,
            "FAIL: test_login (pkg.test_auth.TestLogin.test_login)",
            "-" * 70,
            "Traceback (most recent call last):",
            f"AssertionError: rejected credential {kept_secret}",
        ]
        lines += [f"more noise {i}" for i in range(100)]
        lines += ["-" * 70, "Ran 3 tests in 0.010s", "", SUMMARY]
        output = "\n".join(lines) + "\n"

        _command, result = self.fail_with(output)
        # The kept failure block is in the message, scrubbed.
        self.assertIn("AssertionError: rejected credential", result.message)
        self.assertNotIn(collapsed_secret, result.message)
        self.assertNotIn(kept_secret, result.message)
        self.assertNotIn("hunter2", result.message)
        # The raw log keeps them: it is the debugging copy, never redacted.
        log = (self.layout.verify_logs / "k.log").read_text(encoding="utf-8")
        self.assertIn(collapsed_secret, log)
        self.assertIn(kept_secret, log)

    def test_a_collapsed_secret_line_is_scrubbed_when_it_is_shown(self):
        """No runner recognised: the collapsed line itself is in the excerpt."""
        self.config["redact"] = [r"hunter2-[a-z0-9]+"]
        output = "\n".join(["auth hunter2-repeated1"] * 50) + "\n"
        _command, result = self.fail_with(output)
        self.assertIn("(×50)", result.message)
        self.assertNotIn("hunter2-repeated1", result.message)


AWS_SECRET = "AKIAQ7ZR3MXW9KPL2VTD"


def fragments(secret, width=8):
    """Every `width`-character window of `secret`: prefix, suffix and middle."""
    return [secret[i:i + width] for i in range(len(secret) - width + 1)]


class TestSecretsStraddlingACut(_GateCase):
    """A cap that cuts mid-line must not leave a fragment no pattern matches.

    `AKIA` + 16 is a fixed-length shape: cut it at 10 characters and `scrub`
    no longer recognises it, so the excerpt has to be scrubbed on whole lines
    before any cap runs.
    """

    def assert_no_fragment(self, secret, text):
        for piece in fragments(secret):
            self.assertNotIn(piece, text)

    def test_a_secret_straddling_the_line_cap_cut(self):
        self.config["gate"]["output_line_cap"] = 100
        # The secret starts at column 90; the line cap cuts at 100.
        output = "x" * 89 + " " + AWS_SECRET + " " + "y" * 50 + "\n"
        _command, result = self.fail_with(output)
        self.assertIn("…[cut", result.message)  # the cap really did cut
        self.assert_no_fragment(AWS_SECRET, result.message)
        log = (self.layout.verify_logs / "k.log").read_text(encoding="utf-8")
        self.assertIn(AWS_SECRET, log)

    def test_a_secret_straddling_the_char_cap_cut(self):
        self.config["gate"]["output_char_cap"] = 400
        # One long first line and a short last one: the char cap keeps
        # 267 characters of the first line, cutting it mid-line 12 characters
        # into the secret (which starts at 255).
        output = ("x" * 254 + " " + AWS_SECRET + " " + "y" * 300 + "\n"
                  + "last line\n")
        _command, result = self.fail_with(output)
        self.assertIn("chars omitted", result.message)  # the cap really did cut
        self.assert_no_fragment(AWS_SECRET, result.message)
        log = (self.layout.verify_logs / "k.log").read_text(encoding="utf-8")
        self.assertIn(AWS_SECRET, log)

    def test_a_secret_split_by_an_ansi_escape_and_then_cut(self):
        # The escape hides the token from the first scrub; stripping it joins the
        # token back up, and the cap then cuts through the joined token.
        self.config["gate"]["output_line_cap"] = 100
        split = AWS_SECRET[:8] + "\x1b[0m" + AWS_SECRET[8:]
        output = "x" * 89 + " " + split + " " + "y" * 50 + "\n"
        _command, result = self.fail_with(output)
        self.assertIn("…[cut", result.message)
        self.assert_no_fragment(AWS_SECRET, result.message)


class TestConfigCaps(_GateCase):
    def test_defaults_are_published(self):
        self.assertEqual(config_mod.DEFAULTS["gate"]["output_line_cap"], 2000)
        self.assertEqual(config_mod.DEFAULTS["gate"]["output_char_cap"], 6000)

    def test_absent_keys_mean_the_defaults(self):
        self.assertEqual(config_mod.gate_output_caps({}), (2000, 6000))
        self.assertEqual(config_mod.gate_output_caps({"gate": {}}), (2000, 6000))
        self.assertEqual(config_mod.gate_output_caps({"gate": None}), (2000, 6000))
        self.assertEqual(config_mod.gate_output_caps(self.config), (2000, 6000))

    def test_garbage_values_mean_the_defaults(self):
        for bad in ("lots", None, 0, -5, [], {}, True, "0", 1.5e-3):
            with self.subTest(bad=bad):
                config = {"gate": {"output_line_cap": bad, "output_char_cap": bad}}
                self.assertEqual(config_mod.gate_output_caps(config), (2000, 6000))
        self.assertEqual(config_mod.gate_output_caps({"gate": "nonsense"}),
                         (2000, 6000))

    def test_set_values_are_honoured(self):
        config = {"gate": {"output_line_cap": 80, "output_char_cap": "500"}}
        self.assertEqual(config_mod.gate_output_caps(config), (80, 500))

    def test_a_small_char_cap_shortens_the_message(self):
        output = "\n".join(f"plain line {i} " + "x" * 60 for i in range(300))
        _command, default = self.fail_with(output)
        self.config["gate"]["output_char_cap"] = 400
        _command, capped = self.fail_with(output)
        self.assertLess(len(capped.message), len(default.message))
        self.assertLessEqual(len(self.excerpt_of(capped.message)), 400)

    def test_a_small_line_cap_cuts_long_lines(self):
        output = "y" * 5000 + "\n"
        self.config["gate"]["output_line_cap"] = 100
        _command, result = self.fail_with(output)
        self.assertNotIn("y" * 101, result.message)
        self.assertIn("y" * 100, result.message)

    def test_garbage_in_the_live_config_does_not_break_the_gate(self):
        self.config["gate"]["output_char_cap"] = "plenty"
        self.config["gate"]["output_line_cap"] = -1
        output = unittest_run_600_lines()
        _command, result = self.fail_with(output)
        self.assertIn(ASSERTION, result.message)


class TestJournalBound(Fixture):
    def big_output(self):
        return unittest_run_600_lines() + ("z" * 4000) + "\n"

    def test_excerpt_fits_one_journal_line(self):
        limit = self.config["journal"]["max_line_chars"]
        text = journal.excerpt(self.config, self.big_output())
        self.assertNotIn("\n", text)
        self.assertLessEqual(len(text), limit)

    def test_an_entry_fed_multi_kb_output_stays_within_the_limit(self):
        limit = self.config["journal"]["max_line_chars"]
        output = self.big_output()
        self.assertGreater(len(output), 4000)
        for note in (output, journal.excerpt(self.config, output),
                     f"done refused ({journal.excerpt(self.config, output)})"):
            with self.subTest(note=note[:30]):
                line = journal.append(self.layout, self.config, "unit", "u", note)
                self.assertIsNotNone(line)
                self.assertLessEqual(len(line), limit)
        for raw in journal.day_file(self.layout, journal.today()).read_text(
                encoding="utf-8").splitlines():
            self.assertLessEqual(len(raw), limit)

    def test_a_small_limit_is_honoured(self):
        self.config["journal"]["max_line_chars"] = 40
        line = journal.append(self.layout, self.config, "unit", "u",
                              journal.excerpt(self.config, self.big_output()))
        self.assertLessEqual(len(line), 40)

    def test_a_secret_straddling_the_journal_cut_never_reaches_it(self):
        """`excerpt` scrubs before it cuts, as `append` always did."""
        limit = self.config["journal"]["max_line_chars"]
        self.assertEqual(limit, 200)
        # A last line longer than the limit: its line cap and the char cap's
        # tail both cut through the secret.
        reason = "gate failed\n" + "y" * 10 + " " + AWS_SECRET + " " + "z" * 300
        text = journal.excerpt(self.config, reason)
        for piece in fragments(AWS_SECRET):
            self.assertNotIn(piece, text)
        journal.append(self.layout, self.config, "unit", "u",
                       f"done refused ({text})")
        written = journal.day_file(self.layout, journal.today()).read_text(
            encoding="utf-8")
        self.assertIn("done refused (", written)
        for piece in fragments(AWS_SECRET):
            self.assertNotIn(piece, written)

    def test_a_secret_at_the_journal_line_cut(self):
        reason = "y" * 185 + " " + AWS_SECRET + " tail"
        text = journal.excerpt(self.config, reason)
        for piece in fragments(AWS_SECRET):
            self.assertNotIn(piece, text)

    def test_excerpt_never_raises(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("boom")
        self.assertEqual(journal.excerpt(self.config, Hostile()), "")
        self.assertEqual(journal.excerpt({"journal": "nonsense"}, "ok"), "ok")

    def test_short_text_passes_through(self):
        self.assertEqual(journal.excerpt(self.config, "gate failed"), "gate failed")


if __name__ == "__main__":
    unittest.main()
