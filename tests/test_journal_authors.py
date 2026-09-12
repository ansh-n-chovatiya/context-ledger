"""The journal under two pressures: a crash, and a second person.

`prune` was the one operation in the product that could destroy history. It
folds day files into a monthly archive and then unlinks them, so after the
first run that archive is the *only* copy of everything it holds. It wrote it
with `Path.write_text`, which truncates the file before writing a byte, and it
unlinked the day files whether or not the write had succeeded. A crash in that
window took the month with it.

The second pressure is ordinary collaboration: the day file was keyed on the
date alone, so two people working one repo on one day appended to the same
file and regenerated the same `DIGEST.md`, and conflicted on nearly every
merge. Entries are now partitioned by author as well as by date, and every
reader merges the files back into one time-ordered stream.

The destructive cases are pinned with *real* interruptions — a process the
kernel kills partway through the write, and a failing `os.replace` — rather
than by stubbing out the function under test, which would pass against code
that had never been fixed.
"""

import datetime
import os
import re
import signal
import socket
import subprocess
import sys
import unittest
from unittest import mock

from support import Fixture  # noqa: E402

from ctx import journal  # noqa: E402

POSIX = hasattr(os, "fork")
if POSIX:
    import resource  # noqa: E402 - POSIX only, and only the killed-writer test needs it

# The month everything destructive happens in. Fixed rather than relative so a
# run in the first days of a month cannot straddle two archives.
OLD = datetime.date(2024, 1, 5)
NEWER = datetime.date(2024, 1, 7)
CUTOFF = datetime.date(2024, 2, 1)


def entry(minute, target):
    return "%02d:%02d | edit | %s" % (9 + minute // 60, minute % 60, target)


def day_lines(count, prefix):
    return ["# seeded", ""] + [entry(i, "src/%s_%04d.py" % (prefix, i))
                               for i in range(count)]


def seed_day(layout, day, count, prefix, author=""):
    """Write one day file directly, in either shape. Returns the path."""
    path = journal.day_file(layout, day.isoformat(), author=author)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(day_lines(count, prefix)) + "\n", encoding="utf-8")
    return path


def archive_of(layout, day=OLD):
    return layout.journal / ("archive-%s.md" % day.strftime("%Y-%m"))


def die_partway_through_a_write_of(limit):
    """Arrange for this process to be killed by the kernel mid-write.

    `RLIMIT_FSIZE` is a real interruption, not a simulated one: the kernel
    writes the bytes that fit, then raises `SIGXFSZ`. CPython ignores that
    signal by default, which would turn it into a catchable `OSError` and let
    cleanup run — so the default disposition is restored first. The process
    then dies where it stands, with no unwinding, no `finally` and nothing to
    catch, which is what a crash during `prune` actually looks like.
    """
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


# --------------------------------------------------------------------------- #
# durability — criteria 1, 2, 3
# --------------------------------------------------------------------------- #

class PruneDurability(Fixture):

    def seed_prior_archive(self, count=1200):
        """An archive written by a *previous* prune, whose sources are gone."""
        seed_day(self.layout, OLD, count, "old")
        folded, archives = journal.prune(self.layout, self.config, before=CUTOFF)
        self.assertEqual(len(folded), 1)
        self.assertEqual(len(archives), 1)
        archive = archives[0]
        self.assertFalse(self.layout.journal_file(OLD.isoformat()).exists(),
                         "the prior run unlinked its sources, as it documents")
        return archive, archive.read_bytes()

    def assert_archive_intact(self, archive, before, count=1200):
        after = archive.read_bytes()
        self.assertEqual(
            after, before,
            "the archive is the only copy of %d entries; it changed by %d bytes"
            % (count, len(after) - len(before)))
        kept = [line for line in after.decode("utf-8").splitlines()
                if "| edit |" in line]
        self.assertEqual(len(kept), count, "every line it held must still be there")
        self.assertIn("src/old_0000.py", after.decode("utf-8"))
        self.assertIn("src/old_%04d.py" % (count - 1), after.decode("utf-8"))

    @unittest.skipUnless(POSIX, "needs fork to kill a process mid-write")
    def test_a_prune_killed_mid_write_leaves_the_archive_byte_identical(self):
        """Criterion 2's positive control, and as real as an interruption gets:
        the kernel writes what fits and then kills the writing process, so
        nothing unwinds — no `finally`, no cleanup, no exception to catch."""
        archive, before = self.seed_prior_archive()
        seed_day(self.layout, NEWER, 40, "new")

        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            try:
                die_partway_through_a_write_of(len(before) // 2)
                journal.prune(self.layout, self.config, before=CUTOFF)
            except BaseException:
                os._exit(2)
            os._exit(3)
        _pid, status = os.waitpid(pid, 0)

        self.assertTrue(os.WIFSIGNALED(status),
                        "the child must have died mid-write, not returned "
                        "(exit %s)" % (os.WEXITSTATUS(status),))
        self.assertEqual(os.WTERMSIG(status), signal.SIGXFSZ)
        self.assert_archive_intact(archive, before)
        self.assertTrue(self.layout.journal_file(NEWER.isoformat()).exists(),
                        "the day file the dead run was folding must survive")

    def test_a_failed_replace_leaves_the_archive_and_its_sources_alone(self):
        """Criteria 1 and 3. `os.replace` is the atomic step; if it does not
        happen, the old archive must still be there *and* the day files that
        were about to be folded must not have been unlinked. An archive that
        was not written plus inputs that were deleted is the total-loss case."""
        archive, before = self.seed_prior_archive(count=200)
        source = seed_day(self.layout, NEWER, 40, "new")

        with mock.patch("os.replace", side_effect=OSError(28, "no space")):
            folded, archives = journal.prune(self.layout, self.config, before=CUTOFF)

        self.assert_archive_intact(archive, before, count=200)
        self.assertEqual(archives, [], "an archive that did not land is not reported")
        self.assertEqual(folded, [], "nothing was folded, so nothing was folded")
        self.assertTrue(source.exists(), "the source of the unwritten archive")
        self.assertEqual(len(journal._entry_lines(source, limit=None)), 40)

    def test_the_month_is_retried_by_the_next_run(self):
        """The point of keeping the day files: a failure costs a retry, not a
        month of history."""
        archive, _before = self.seed_prior_archive(count=200)
        seed_day(self.layout, NEWER, 40, "new")
        with mock.patch("os.replace", side_effect=OSError(28, "no space")):
            journal.prune(self.layout, self.config, before=CUTOFF)

        folded, archives = journal.prune(self.layout, self.config, before=CUTOFF)
        self.assertEqual(len(folded), 1)
        self.assertEqual(archives, [archive])
        text = archive.read_text(encoding="utf-8")
        self.assertEqual(
            len([line for line in text.splitlines() if "| edit |" in line]), 240)

    def test_the_digest_survives_a_failed_write(self):
        """Criterion 1 for `write_digest`. The digest is read by SessionStart on
        every session, so a torn one is read immediately."""
        journal.append(self.layout, self.config, "edit", "src/first.py")
        journal.write_digest(self.layout, self.config)
        before = self.layout.digest.read_bytes()

        journal.append(self.layout, self.config, "edit", "src/second.py")
        with mock.patch("os.replace", side_effect=OSError(28, "no space")):
            with self.assertRaises(OSError):
                journal.write_digest(self.layout, self.config)

        self.assertEqual(self.layout.digest.read_bytes(), before)
        self.assertIn("src/first.py", before.decode("utf-8"))

    def test_no_half_written_file_is_left_in_the_journal_glob(self):
        """The temp file must not be picked up as a day file by any reader."""
        self.seed_prior_archive(count=50)
        seed_day(self.layout, NEWER, 10, "new")
        with mock.patch("os.replace", side_effect=OSError(28, "no space")):
            journal.prune(self.layout, self.config, before=CUTOFF)
        self.assertEqual([p.name for p in self.layout.journal.glob("*.tmp")], [])
        self.assertEqual([p.name for _d, _a, p in journal._day_files(self.layout)],
                         ["%s.md" % NEWER.isoformat()])


# --------------------------------------------------------------------------- #
# who wrote it — criterion 4
# --------------------------------------------------------------------------- #

class AuthorKey(Fixture):

    def setUp(self):
        super().setUp()
        journal._AUTHOR_CACHE.clear()
        # The developer's own `~/.gitconfig` must not decide what these assert.
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
        os.environ.pop(journal.AUTHOR_ENV, None)

    def tearDown(self):
        journal._AUTHOR_CACHE.clear()
        super().tearDown()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args],
                       check=True, capture_output=True)

    def test_the_key_is_the_git_email_slugified(self):
        self.git_init()
        journal._AUTHOR_CACHE.clear()
        self.assertEqual(journal.author_key(self.layout), "t-example-com")
        journal.append(self.layout, self.config, "edit", "src/a.py")
        self.assertTrue(
            (self.layout.journal / ("%s--t-example-com.md" % journal.today())).is_file())

    def test_the_name_is_used_when_there_is_no_email(self):
        self.git_init()
        self.git("config", "--unset", "user.email")
        journal._AUTHOR_CACHE.clear()
        self.assertEqual(journal.author_key(self.layout), "test")

    def test_an_unconfigured_checkout_falls_back_to_the_shared_stream(self):
        """The documented fallback: no identity, no attribution. It must be
        deterministic and must not invent one out of the machine."""
        self.git_init()
        self.git("config", "--unset", "user.email")
        self.git("config", "--unset", "user.name")
        journal._AUTHOR_CACHE.clear()
        key = journal.author_key(self.layout)
        self.assertEqual(key, "")
        self.assertEqual(journal.author_key(self.layout), key, "deterministic")
        journal.append(self.layout, self.config, "edit", "src/a.py")
        self.assertTrue(self.layout.journal_file(journal.today()).is_file(),
                        "it goes to the unattributed stream, the pre-author shape")

    def test_nothing_about_the_machine_leaks_into_the_key(self):
        """Not a hostname, not a login, not a PID: the key may carry no more
        than the git identity the repository already records."""
        self.assertEqual(journal.author_key(self.layout), "",
                         "the fixture is not a work tree, so there is no identity")
        host = socket.gethostname().lower()
        user = (os.environ.get("USER") or os.environ.get("USERNAME") or "").lower()
        self.git_init()
        journal._AUTHOR_CACHE.clear()
        key = journal.author_key(self.layout)
        for leak in (host, host.split(".")[0], user, str(os.getpid())):
            if leak:
                self.assertNotIn(leak, key)

    def test_a_directory_that_is_not_a_work_tree_asks_git_nothing(self):
        """`git config` answers out of the user's global config from anywhere.
        Reading it outside a repository would attribute a ledger in a plain
        directory to whoever last configured git on the box."""
        with mock.patch.object(journal, "_git",
                               side_effect=AssertionError("git was consulted")):
            self.assertEqual(journal.author_key(self.layout), "")

    def test_the_environment_overrides_git(self):
        self.git_init()
        journal._AUTHOR_CACHE.clear()
        os.environ[journal.AUTHOR_ENV] = "Ada Lovelace <ada@example.com>"
        self.assertEqual(journal.author_key(self.layout),
                         "ada-lovelace-ada-example-com")

    def test_keys_are_bounded_and_still_distinct(self):
        long_a = "a" * 40 + "-one@example.com"
        long_b = "a" * 40 + "-two@example.com"
        keys = []
        for value in (long_a, long_b):
            os.environ[journal.AUTHOR_ENV] = value
            keys.append(journal.author_key(self.layout))
        for key in keys:
            self.assertLessEqual(len(key), journal.AUTHOR_MAX)
            self.assertRegex(key, r"^[a-z0-9][a-z0-9-]*$")
        self.assertNotEqual(keys[0], keys[1],
                            "a shared prefix must not become a shared file")

    def test_a_key_of_pure_punctuation_is_no_key_at_all(self):
        os.environ[journal.AUTHOR_ENV] = "  ///  "
        self.assertEqual(journal.author_key(self.layout), "")

    def test_a_broken_git_never_breaks_an_append(self):
        self.git_init()
        journal._AUTHOR_CACHE.clear()
        with mock.patch("subprocess.run", side_effect=OSError("no git here")):
            self.assertEqual(journal.author_key(self.layout), "")
            self.assertIsNotNone(
                journal.append(self.layout, self.config, "edit", "src/a.py"))


# --------------------------------------------------------------------------- #
# both shapes, one stream — criteria 5, 6, 8
# --------------------------------------------------------------------------- #

class MergedStream(Fixture):

    DAY = "2026-09-01"

    def setUp(self):
        super().setUp()
        journal._AUTHOR_CACHE.clear()
        os.environ.pop(journal.AUTHOR_ENV, None)

    def tearDown(self):
        journal._AUTHOR_CACHE.clear()
        super().tearDown()

    def seed_interleaved(self):
        """One day, three writers, interleaved in time.

        legacy  09:00           11:00
        ada           10:00                   13:00
        grace                          12:00
        """
        legacy = self.layout.journal_file(self.DAY)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            "# %s\n\n09:00 | edit | src/legacy_a.py\n11:00 | edit | src/legacy_b.py\n"
            % self.DAY, encoding="utf-8")
        ada = self.layout.journal / ("%s--ada.md" % self.DAY)
        ada.write_text(
            "# %s\n\n10:00 | edit | src/ada_a.py\n13:00 | edit | src/ada_b.py\n"
            % self.DAY, encoding="utf-8")
        grace = self.layout.journal / ("%s--grace.md" % self.DAY)
        grace.write_text(
            "# %s\n\n12:00 | edit | src/grace_a.py\n" % self.DAY, encoding="utf-8")
        return legacy, ada, grace

    ORDER = ["src/legacy_a.py", "src/ada_a.py", "src/legacy_b.py",
             "src/grace_a.py", "src/ada_b.py"]

    def targets(self, lines):
        return [line.split(journal.SEP)[2].strip() for line in lines]

    def test_tail_merges_both_shapes_in_time_order(self):
        legacy, _ada, _grace = self.seed_interleaved()
        before = legacy.read_bytes()

        entries, earlier = journal.tail(self.layout, 10)
        self.assertEqual(self.targets(entries), self.ORDER)
        self.assertEqual(earlier, 0)
        for line in entries:
            self.assertTrue(line.startswith(self.DAY + " "),
                            "entries are stamped with the date, not the file name")
        self.assertEqual(legacy.read_bytes(), before,
                         "reading a legacy day file must not rewrite it")

    def test_the_digest_merges_both_shapes_in_time_order(self):
        legacy, _ada, _grace = self.seed_interleaved()
        before = legacy.read_bytes()
        journal.write_digest(self.layout, self.config)
        body = [line[2:]
                for line in self.layout.digest.read_text(encoding="utf-8").splitlines()
                if line.startswith("- ")]
        self.assertEqual(self.targets(body), self.ORDER)
        self.assertEqual(legacy.read_bytes(), before)

    def test_a_bounded_tail_still_counts_what_it_skipped(self):
        self.seed_interleaved()
        entries, earlier = journal.tail(self.layout, 2)
        self.assertEqual(self.targets(entries), self.ORDER[-2:])
        self.assertEqual(earlier, 3)

    def test_recent_paths_sees_every_authors_edits(self):
        self.seed_interleaved()
        self.assertEqual(journal.recent_paths(self.layout, 5),
                         list(reversed(self.ORDER)))

    def test_legacy_day_files_are_reported_and_never_migrated(self):
        """Criterion 5, recorded so it is not re-litigated: a pre-author day
        file records no author, so migrating it would stamp every historical
        entry with whoever ran the migration."""
        legacy, ada, _grace = self.seed_interleaved()
        before = legacy.read_bytes()
        names = [p.name for p in journal.legacy_day_files(self.layout)]
        self.assertEqual(names, [legacy.name])
        self.assertNotIn(ada.name, names)

        journal.tail(self.layout, 50)
        journal.write_digest(self.layout, self.config)
        journal.append(self.layout, self.config, "edit", "src/new.py")
        self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual([p.name for p in journal.legacy_day_files(self.layout)
                          if p.name == legacy.name], [legacy.name])

    def test_legacy_files_are_ordered_oldest_first(self):
        for day in ("2026-09-03", "2026-09-01", "2026-09-02"):
            self.layout.journal.mkdir(parents=True, exist_ok=True)
            self.layout.journal_file(day).write_text("09:00 | edit | a.py\n",
                                                     encoding="utf-8")
        (self.layout.journal / "2026-09-02--ada.md").write_text(
            "09:30 | edit | a.py\n", encoding="utf-8")
        self.assertEqual([p.stem for p in journal.legacy_day_files(self.layout)],
                         ["2026-09-01", "2026-09-02", "2026-09-03"],
                         "oldest first, and an attributed file is not legacy")

    def test_prune_folds_every_authors_file_into_one_dated_section(self):
        """Criterion 8: the new shape retires the same way the old one does."""
        self.seed_interleaved()
        folded, archives = journal.prune(
            self.layout, self.config,
            before=datetime.date.today() + datetime.timedelta(days=1))
        self.assertEqual(len(folded), 3, "all three shapes were folded")
        self.assertEqual(len(archives), 1)
        for path in folded:
            self.assertFalse(path.exists())
        text = archives[0].read_text(encoding="utf-8")
        self.assertEqual(text.count("## %s" % self.DAY), 1,
                         "one section per day, not one per author")
        body = [line for line in text.splitlines() if "| edit |" in line]
        self.assertEqual(self.targets(body), self.ORDER)

    def test_an_archive_is_never_read_back_as_a_day_file(self):
        self.seed_interleaved()
        journal.prune(self.layout, self.config,
                      before=datetime.date.today() + datetime.timedelta(days=1))
        self.assertEqual(journal._day_files(self.layout), [])
        self.assertEqual(journal.tail(self.layout, 10), ([], 0))


# --------------------------------------------------------------------------- #
# line atomicity across authors — criterion 7
# --------------------------------------------------------------------------- #

@unittest.skipUnless(POSIX, "needs fork")
class ConcurrentAuthors(Fixture):
    """The standing guarantee is 8 forks × 150 records = 1200 present, 0
    malformed. Partitioning by author must not weaken it: one author can still
    have several agents running at once, so the per-line append is still what
    makes a concurrent write safe."""

    AUTHORS = ("ada@example.com", "grace@example.com")
    FORKS_EACH = 4
    RECORDS = 150
    SHAPE = re.compile(r"^\d\d:\d\d \| edit \| src/[a-z]+-\d-\d{3}\.py$")

    def test_eight_processes_and_two_authors_lose_nothing(self):
        pids = []
        for author in self.AUTHORS:
            for slot in range(self.FORKS_EACH):
                pid = os.fork()
                if pid == 0:  # pragma: no cover - child
                    code = 0
                    try:
                        os.environ[journal.AUTHOR_ENV] = author
                        journal._AUTHOR_CACHE.clear()
                        who = author.split("@")[0]
                        for index in range(self.RECORDS):
                            journal.append(self.layout, self.config, "edit",
                                           "src/%s-%d-%03d.py" % (who, slot, index))
                    except BaseException:
                        code = 1
                    os._exit(code)
                pids.append(pid)
        for pid in pids:
            _pid, status = os.waitpid(pid, 0)
            self.assertEqual(status, 0, "a writing child failed")

        rows = journal._day_files(self.layout)
        self.assertEqual(sorted(author for _d, author, _p in rows),
                         ["ada-example-com", "grace-example-com"],
                         "each author writes to their own file, and only theirs")

        seen = []
        for _day, _author, path in rows:
            for line in journal._entry_lines(path, limit=None):
                self.assertRegex(line, self.SHAPE, "a torn line")
                seen.append(line.split(journal.SEP)[2].strip())
        total = len(self.AUTHORS) * self.FORKS_EACH * self.RECORDS
        self.assertEqual(len(seen), total, "%d/%d present" % (len(seen), total))
        self.assertEqual(len(set(seen)), total, "0 duplicated, 0 lost")

        entries, _earlier = journal.tail(self.layout, total)
        self.assertEqual(len(entries), total,
                         "the merged stream sees every author's records")


# --------------------------------------------------------------------------- #
# appends still never raise — criterion 9
# --------------------------------------------------------------------------- #

class AppendsNeverRaise(Fixture):

    @unittest.skipUnless(POSIX and os.getuid() != 0, "root ignores mode bits")
    def test_a_read_only_journal_directory_is_survivable(self):
        self.layout.journal.mkdir(parents=True, exist_ok=True)
        mode = self.layout.journal.stat().st_mode
        os.chmod(self.layout.journal, 0o500)
        try:
            self.assertIsNone(
                journal.append(self.layout, self.config, "edit", "src/a.py"),
                "journalling must not break a session")
        finally:
            os.chmod(self.layout.journal, mode)

    def test_a_read_only_ledger_root_is_survivable(self):
        with mock.patch.object(type(self.layout.journal), "mkdir",
                               side_effect=PermissionError("read-only")):
            self.assertIsNone(
                journal.append(self.layout, self.config, "edit", "src/a.py"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
