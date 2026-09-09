"""Three ways the ledger used to lose data it had promised to keep.

Every case here is destructive rather than merely wrong: redaction replaced
content that never reached disk in its original form, `prune` unlinked day
files it had only partly read, and a torn document write left a unit file whose
frontmatter no longer parsed. None of them fail loudly, which is why they are
pinned here as behaviour rather than left to review.
"""

import datetime
import os
import unittest
from unittest import mock

from support import Fixture  # noqa: E402

from ctx import frontmatter, journal, redact  # noqa: E402


def _shaped(prefix, body):
    """Assemble a credential-shaped string at run time.

    The corpus is only worth having if it carries realistic shapes — but a
    realistic shape written as a literal is precisely what a secret scanner is
    built to find, and GitHub's push protection rejects the push for the whole
    repository. None of these is a real credential; splitting the recognisable
    prefix from its body leaves nothing in the file for a scanner to match while
    the value each test actually sees is unchanged.
    """
    return prefix + body


# (label, text, the material that must not survive)
MUST_SCRUB = (
    ("aws access key id", _shaped("AKIA", "IOSFODNN7EXAMPLE"), _shaped("AKIA", "IOSFODNN7EXAMPLE")),
    ("aws secret access key",
     "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
     "wJalrXUtnFEMI"),
    ("github personal token", _shaped("ghp", "_16C7e42F292c6912E7710c838347Ae178B4a"), "ghp_16C7"),
    ("github oauth token", _shaped("gho", "_16C7e42F292c6912E7710c838347Ae178B4a"), "gho_16C7"),
    ("github app token", _shaped("ghs", "_16C7e42F292c6912E7710c838347Ae178B4a"), "ghs_16C7"),
    ("gitlab pat", _shaped("glpat", "-ABCDEFGH12345678ijkl"), "glpat-ABCD"),
    ("slack bot token",
     _shaped("xoxb", "-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"), "xoxb-1234"),
    ("slack webhook url",
     _shaped("https://hooks.slack.com/servi", "ces/T00000000/B00000000/" + "X" * 24),
     "hooks.slack.com/services/T00000000"),
    ("stripe live key", _shaped("sk_live", "_51H8xY2eZvKYlo2CabcdefghijklmnopQ"), "sk_live_51H8"),
    ("stripe test key", _shaped("pk_test", "_51H8xY2eZvKYlo2CabcdefghijklmnopQ"), "pk_test_51H8"),
    ("google api key", _shaped("AIzaSy", "D-1234567890abcdefghijklmnopqrstuvw"), "AIzaSyD-"),
    ("openai key",
     _shaped("sk-proj", "-abcdefghijklmnopqrstuvwxyz1234567890ABCD"), "sk-proj-abcd"),
    ("anthropic key",
     _shaped("sk-ant", "-api03-abcdefghijklmnopqrstuvwxyz0123456789-AA"), "sk-ant-api03"),
    ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
     "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk", "eyJhbGciOiJIUzI1NiIs"),
    ("rsa private key block",
     "-----BEGIN RSA PRIVATE KEY-----\n"
     "MIIEowIBAAKCAQEAy8Dbv8prpJ/0kKhlGeJYozo2t60EG8L0561g13R29LvMR5hy\n"
     "-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
    ("openssh private key block",
     "-----BEGIN OPENSSH PRIVATE KEY-----\n"
     "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdz\n"
     "-----END OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXktdjEA"),
    (".env line", "DATABASE_PASSWORD=sup3r-s3cret-value", "sup3r-s3cret-value"),
    ("authorization bearer",
     "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345678", "abcdefghijkl"),
    ("authorization basic",
     "Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("connection string password",
     "Server=db;Database=app;User Id=sa;Password=P@ssw0rd!;", "P@ssw0rd!"),
    ("postgres url credentials",
     "postgres://admin:s3cr3tP4ssw0rd@host/db", "s3cr3tP4ssw0rd"),
    ("https basic-auth url",
     "https://user:hunter2pass@example.com/repo.git", "hunter2pass"),
    ("json password field", '{"password": "sw0rdf1sh"}', "sw0rdf1sh"),
    ("mysql -p flag", "mysql -uroot -pSecretPass -h db.internal", "SecretPass"),
    ("bare 32-char hex secret", "3f8a9b2c1d4e5f60718293a4b5c6d7e8", "3f8a9b2c"),
    ("twilio sid and token",
     _shaped("AC", "af9b1c2d3e4f5061728394a5b6c7d8e9:0123456789abcdef0123456789abcdef"),
     "0123456789abcdef0123456789abcdef"),
)

# Content that must come back byte-for-byte. Every one of these is ordinary
# output of the tools this project journals — and most of them were destroyed
# by the entropy heuristic that used to run here.
MUST_SURVIVE = (
    ("git sha", "356a192b7913b04c54574d18c28d46e6395428ab"),
    ("short git sha", "fixed in 79504ec"),
    ("uuid", "123e4567-e89b-12d3-a456-426614174000"),
    ("sha256 checksum",
     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("docker digest",
     "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"),
    ("semver", "v2.14.0-rc.3+build.2024"),
    ("deep source path",
     "src/components/dashboard/widgets/AnalyticsSummaryPanel.tsx"),
    ("dotted module path", "ctx.frontmatter.Document.write"),
    ("npm integrity hash",
     "sha512-Bq6+ZBpM0Mn2rMbpmVLE1kbfHCkGZ/bPqjOZLBVFEOFPZUE7L0nJPnDNvVXWY1sZ6cvNQ=="),
    ("base64 data uri",
     "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"),
    ("merge commit subject", "Merge pull request #412 from acme/feature/x"),
    ("traceback frame",
     'File "/usr/lib/python3.9/json/decoder.py", line 355, in raw_decode'),
    ("k8s resource name", "deployment.apps/api-gateway-7d8f9c5b6-x2k4m"),
    ("auth prose", "auth: none"),
    ("auth prose with value", "auth: cookie-based sessions"),
)


class TestRedactionPrecision(unittest.TestCase):
    """Redaction runs on the write path, so both kinds of error are permanent.

    The heuristic this replaces destroyed `AnalyticsSummaryPanel.tsx` and let
    `postgres://admin:pass@host/db` through — it was inverted, not imprecise.
    """

    def test_every_credential_shape_is_scrubbed(self):
        for label, text, material in MUST_SCRUB:
            out = redact.scrub(text)
            self.assertIn(redact.PLACEHOLDER, out, label)
            self.assertNotIn(material, out, label)

    def test_no_benign_content_is_touched(self):
        for label, text in MUST_SURVIVE:
            self.assertEqual(redact.scrub(text), text, label)

    def test_the_separator_and_surrounding_syntax_survive(self):
        """`auth: cookie-based` came back as `auth=<<redacted>>` — the old
        substitution rebuilt the pair with `=` instead of what it matched."""
        self.assertEqual(
            redact.scrub("token: " + _shaped("ghp", "_abcdefghijklmnopqrstuvwxyz012345")),
            "token: " + redact.PLACEHOLDER,
        )
        self.assertEqual(
            redact.scrub('{"password": "hunter2"}'),
            '{"password": "%s"}' % redact.PLACEHOLDER,
        )
        self.assertNotIn("=", redact.scrub("secret : hunter2seekrit"))

    def test_a_document_of_benign_content_is_returned_unchanged(self):
        document = "\n".join(text for _label, text in MUST_SURVIVE)
        self.assertEqual(redact.scrub(document), document)

    def test_a_bad_user_pattern_still_does_not_break_the_write_path(self):
        self.assertEqual(redact.scrub("hello", ["("]), "hello")


class TestJournalledPathsSurvive(Fixture):
    """`journal.append`'s target is a path by contract, and the briefing reads
    it straight back — a redacted path is a lie told to the next session."""

    def test_a_deep_source_path_round_trips_through_the_journal(self):
        path = "src/components/dashboard/widgets/AnalyticsSummaryPanel.tsx"
        journal.append(self.layout, self.config, "edit", path)
        self.assertEqual(journal.recent_paths(self.layout, 1), [path])
        body = self.layout.journal_file(journal.today()).read_text(encoding="utf-8")
        self.assertNotIn(redact.PLACEHOLDER, body)

    def test_a_credential_in_a_note_is_still_scrubbed(self):
        journal.append(self.layout, self.config, "edit", "src/api.py",
                       "used " + _shaped("ghp", "_abcdefghijklmnopqrstuvwxyz012345"))
        body = self.layout.journal_file(journal.today()).read_text(encoding="utf-8")
        self.assertNotIn("ghp_abcd", body)
        self.assertIn(redact.PLACEHOLDER, body)


class TestPruneKeepsEveryEntry(Fixture):
    """`prune` folds day files into monthly archives and then unlinks them, so
    whatever it fails to read is destroyed. It read a 64 KiB tail: a 5,000-entry
    day archived as 949 entries, and the other 4,051 were gone."""

    ENTRIES = 2500

    def _busy_day(self, when):
        for index in range(self.ENTRIES):
            journal.append(self.layout, self.config, "edit",
                           "src/module_%04d.py" % index,
                           "touched during a long session", when=when)
        return self.layout.journal_file(when.date().isoformat())

    def test_a_day_file_over_the_tail_bound_is_archived_whole(self):
        when = datetime.datetime.now() - datetime.timedelta(days=90)
        day = self._busy_day(when)
        size = day.stat().st_size
        self.assertGreater(size, journal.TAIL_BYTES,
                           "the fixture must exceed the tail bound to prove anything")

        # The tail bound is still in force for readers — that is not the bug.
        self.assertLess(len(journal._entry_lines(day)), self.ENTRIES)
        self.assertEqual(len(journal._entry_lines(day, limit=None)), self.ENTRIES)

        _folded, archives = journal.prune(
            self.layout, self.config,
            before=datetime.date.today() - datetime.timedelta(days=30),
        )
        self.assertFalse(day.exists(), "the day file is unlinked, as documented")
        text = "\n".join(p.read_text(encoding="utf-8") for p in archives)
        kept = [line for line in text.splitlines() if "| edit |" in line]
        self.assertEqual(len(kept), self.ENTRIES, "prune must fold, not truncate")
        self.assertIn("src/module_0000.py", text, "the oldest entry of the day")
        self.assertIn("src/module_%04d.py" % (self.ENTRIES - 1), text)

    def test_the_briefing_read_path_stays_bounded(self):
        """The 64 KiB tail is what keeps SessionStart flat; only `prune` needs
        the whole file, and it must not become the default by accident."""
        when = datetime.datetime.now()
        day = self._busy_day(when)
        self.assertGreater(day.stat().st_size, journal.TAIL_BYTES)
        entries, earlier = journal.tail(self.layout, 12)
        self.assertEqual(len(entries), 12)
        self.assertGreater(earlier, 0)


class TestDocumentWritesAreAtomic(Fixture):
    """`Document.write` is the write path for every committed artifact. A torn
    write leaves frontmatter that no longer parses, and the tolerant reader then
    reports a finished unit as `pending` — which re-dispatches its work."""

    def _unit(self):
        return frontmatter.Document(
            {"unit": "u1", "status": "running", "tier": "subagent",
             "owns": ["src/api.py"]},
            "## Objective\nShip it.\n",
        )

    def test_a_document_round_trips_and_creates_its_parent(self):
        path = self.layout.root / "units" / "nested" / "u1.md"
        self._unit().write(path)
        again = frontmatter.read(path)
        self.assertEqual(again.meta["status"], "running")
        self.assertEqual(again.meta["owns"], ["src/api.py"])

    def test_the_destination_is_never_truncated_mid_write(self):
        """The invariant a plain `write_text` breaks: at the moment the new
        bytes are complete, the old file is still whole."""
        path = self.layout.root / "units" / "u1.md"
        self._unit().write(path)
        before = path.read_text(encoding="utf-8")
        seen = {}
        real_replace = os.replace

        def spy(src, dst):
            seen["destination"] = path.read_text(encoding="utf-8")
            with open(src, encoding="utf-8") as staged:
                seen["staged"] = staged.read()
            return real_replace(src, dst)

        doc = self._unit()
        doc.meta["status"] = "done"
        with mock.patch("os.replace", spy):
            doc.write(path)
        self.assertEqual(seen["destination"], before,
                         "the old document is intact until the replace")
        self.assertIn("status: done", seen["staged"])
        self.assertIn("status: done", path.read_text(encoding="utf-8"))

    def test_a_failed_write_leaves_the_original_and_no_debris(self):
        path = self.layout.root / "units" / "u1.md"
        self._unit().write(path)
        before = path.read_text(encoding="utf-8")

        doc = self._unit()
        doc.meta["status"] = "done"
        with mock.patch("os.replace", side_effect=OSError("no space left")):
            with self.assertRaises(OSError):
                doc.write(path)

        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["u1.md"],
                         "a temp file must not be left beside a committed document")

    def test_the_temp_file_shares_the_destination_directory(self):
        """`os.replace` is only atomic within one filesystem, so staging in the
        system temp directory would trade a torn write for a cross-device error."""
        path = self.layout.root / "units" / "u1.md"
        staged = {}
        real_replace = os.replace

        def spy(src, dst):
            staged["dir"] = os.path.dirname(os.path.realpath(src))
            return real_replace(src, dst)

        with mock.patch("os.replace", spy):
            self._unit().write(path)
        self.assertEqual(staged["dir"], os.path.realpath(str(path.parent)))


if __name__ == "__main__":
    unittest.main()
