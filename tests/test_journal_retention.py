"""What `journal.keep_days: 0` means, and the notice that keeps it honest.

The audit finding was that `keep_days` defaults to 0, `prune` reads it, `ctx
prune` advises setting it — and nobody had written down what 0 does, so the
committed journal grew for ever and the documentation neither promised that
nor forbade it.

The decision, asserted here so it cannot drift back into ambiguity:

  **0 means never prune.** It stays the shipped default, because `prune`
  rewrites *committed* files and a default that folds tracked history on a
  schedule nobody set is a worse failure than a directory that grows. What 0
  is no longer allowed to be is invisible — past `GROWTH_WARN_FILES` day
  files, `journal.overgrown()` says how big the journal is, names the setting
  and names the command.
"""

import datetime
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ctx import config as config_mod, journal  # noqa: E402
from support import Fixture  # noqa: E402

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"


class Retention(Fixture):
    def with_keep_days(self, days):
        return dict(self.config,
                    journal=dict(self.config["journal"], keep_days=days))

    def day(self, age_days, note="did a thing"):
        """One entry, `age_days` in the past. Lands in that day's file."""
        when = datetime.datetime.now() - datetime.timedelta(days=age_days)
        journal.append(self.layout, self.config, "edit", "src/a.py", note,
                       when=when)

    def day_files(self, count, first_age=1000):
        """`count` day files, one per day, oldest `first_age` days back."""
        made = []
        for index in range(count):
            day = (datetime.date.today()
                   - datetime.timedelta(days=first_age - index))
            path = self.layout.journal / f"{day.isoformat()}.md"
            path.write_text(f"# {day}\n\n09:00 | edit | src/a.py\n",
                            encoding="utf-8")
            made.append(path)
        return made


# --------------------------------------------------------------------------- #
# what 0 means
# --------------------------------------------------------------------------- #

class TestZeroMeansNeverPrune(Retention):
    def test_the_shipped_default_is_zero(self):
        self.assertEqual(config_mod.DEFAULTS["journal"]["keep_days"], 0)
        self.assertEqual((self.config.get("journal") or {}).get("keep_days"), 0)

    def test_nothing_is_folded_however_old_it_is(self):
        self.day(2000)
        self.day(1500)
        folded, archives = journal.prune(self.layout, self.config)
        self.assertEqual(folded, [], "0 must not fold history")
        self.assertEqual(archives, [])

    def test_and_nothing_is_deleted(self):
        """The stronger claim: not merely 'returns nothing', but 'the files
        are still there'. A prune that unlinked without archiving would also
        return an empty archive list."""
        self.day(2000)
        before = sorted(p.name for p in self.layout.journal.glob("*.md"))
        journal.prune(self.layout, self.config)
        after = sorted(p.name for p in self.layout.journal.glob("*.md"))
        self.assertEqual(before, after)

    def test_an_explicit_before_still_prunes_under_the_default(self):
        """0 disables the *schedule*, not the command. `ctx prune --before` is
        an explicit act each time, which is the point."""
        self.day(400)
        folded, archives = journal.prune(
            self.layout, self.config,
            before=datetime.date.today() - datetime.timedelta(days=30))
        self.assertEqual(len(folded), 1)
        self.assertTrue(archives)

    def test_a_positive_keep_days_prunes_on_that_schedule(self):
        self.day(400)
        self.day(1)
        folded, _archives = journal.prune(self.layout, self.with_keep_days(30))
        self.assertEqual(len(folded), 1, "only the old day is folded")

    def test_pruning_keeps_the_entries(self):
        """Folding is a rollup, not a delete. If this ever stops holding, a
        non-zero default becomes destructive and the decision above changes."""
        self.day(400, note="a thing worth remembering")
        _folded, archives = journal.prune(self.layout, self.with_keep_days(30))
        text = "\n".join(p.read_text(encoding="utf-8") for p in archives)
        self.assertIn("a thing worth remembering", text)

    def test_a_junk_keep_days_is_treated_as_zero_rather_than_guessed(self):
        for value in ("", None, "lots", -5):
            with self.subTest(value=value):
                self.day(2000)
                folded, _a = journal.prune(self.layout,
                                           self.with_keep_days(value))
                self.assertEqual(folded, [])


# --------------------------------------------------------------------------- #
# growth that says so
# --------------------------------------------------------------------------- #

class TestTheGrowthNotice(Retention):
    def test_a_small_journal_says_nothing(self):
        self.day_files(5)
        self.assertIsNone(journal.overgrown(self.layout, self.config))

    def test_a_journal_just_under_the_threshold_says_nothing(self):
        self.day_files(journal.GROWTH_WARN_FILES - 1)
        self.assertIsNone(journal.overgrown(self.layout, self.config))

    def test_past_the_threshold_it_names_the_count(self):
        self.day_files(journal.GROWTH_WARN_FILES)
        notice = journal.overgrown(self.layout, self.config)
        self.assertIsNotNone(notice)
        self.assertIn(str(journal.GROWTH_WARN_FILES), notice)

    def test_it_names_the_setting_and_the_command(self):
        """A notice that does not say what to do is noise the reader learns
        to skip."""
        self.day_files(journal.GROWTH_WARN_FILES)
        notice = journal.overgrown(self.layout, self.config)
        self.assertIn("journal.keep_days", notice)
        self.assertIn("ctx prune", notice)

    def test_it_says_that_zero_means_for_ever(self):
        self.day_files(journal.GROWTH_WARN_FILES)
        notice = journal.overgrown(self.layout, self.config)
        self.assertIn("keep_days is 0", notice)
        self.assertIn("for ever", notice)

    def test_it_promises_that_pruning_keeps_the_entries(self):
        """Without this the advice reads as 'delete your history to save
        space', and the right response to it is to ignore it."""
        self.day_files(journal.GROWTH_WARN_FILES)
        self.assertIn("kept", journal.overgrown(self.layout, self.config))

    def test_with_keep_days_set_it_points_at_the_command_instead(self):
        self.day_files(journal.GROWTH_WARN_FILES)
        notice = journal.overgrown(self.layout, self.with_keep_days(90))
        self.assertIn("90", notice)
        self.assertIn("ctx prune", notice)
        self.assertNotIn("for ever", notice)

    def test_it_is_advisory_and_never_prunes(self):
        made = self.day_files(journal.GROWTH_WARN_FILES)
        journal.overgrown(self.layout, self.config)
        self.assertTrue(all(p.exists() for p in made))

    def test_it_never_raises(self):
        """It decorates `ctx doctor`. An advisory line may not be the thing
        that fails the command it decorates."""
        self.day_files(journal.GROWTH_WARN_FILES)
        for config in ({}, {"journal": None}, {"journal": {"keep_days": "x"}}):
            with self.subTest(config=config):
                self.assertIsNotNone(journal.overgrown(self.layout, config)
                                     or "no notice is a valid answer")

    def test_a_missing_journal_directory_is_not_a_crash(self):
        for path in self.layout.journal.glob("*.md"):
            path.unlink()
        self.layout.journal.rmdir()
        self.assertIsNone(journal.overgrown(self.layout, self.config))

    def test_archives_and_the_digest_are_not_counted_as_day_files(self):
        """Otherwise the notice fires on the very thing it recommends."""
        self.day_files(10)
        for month in range(1, 13):
            (self.layout.journal / f"archive-2024-{month:02d}.md").write_text(
                "# archive\n", encoding="utf-8")
        self.assertIsNone(journal.overgrown(self.layout, self.config))


# --------------------------------------------------------------------------- #
# the code and the documentation say the same thing
# --------------------------------------------------------------------------- #

class TestTheDocumentationAgrees(unittest.TestCase):
    """The finding was as much a documentation defect as a code one: the
    default was 0 and no document said what 0 did."""

    def documents(self):
        return [path for path in sorted(DOCS.rglob("*.md"))
                if "history" not in path.parts]

    def test_a_living_document_explains_what_zero_does(self):
        texts = [p.read_text(encoding="utf-8") for p in self.documents()]
        explained = [
            text for text in texts
            if "keep_days" in text and any(
                phrase in text.lower()
                for phrase in ("never prune", "keeps everything",
                               "keep everything", "for ever", "forever"))
        ]
        self.assertTrue(
            explained,
            "no living document says what `journal.keep_days: 0` does")

    def test_the_module_states_the_decision_where_prune_is(self):
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "ctx" / "journal.py").read_text(encoding="utf-8")
        head = source[:source.index("def prune(")]
        self.assertIn("never prune", head.lower())


if __name__ == "__main__":
    unittest.main()
