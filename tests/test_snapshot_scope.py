"""A repo-supplied `ignore:` must never blind `capture` to the ledger's own
directory.

`snapshot.capture` fingerprints the whole project so that a write to a path
nobody declared is still visible — that is what every scope-violation check
this project has is built from. But `settings()` lets a committed `ctx.yaml`
*replace* the default ignore set wholesale via `review: {ignore: [...]}`, and
before this fix that replacement could name the ledger's own directory
(`.ctx/`, `paths.LEDGER_PREFIX`) and make `capture` skip it entirely. A unit
writing somewhere under `.ctx/` it does not own would then leave no trace in
either snapshot's manifest, so the delta between them would not show it —
invisible to the one mechanical check standing between a real scope violation
and a signed-off `done`.

The fix hard-excludes `.ctx/` from ever being ignorable by a repo-configured
`ignore:`, with one carve-out: `.ctx/runtime` holds the snapshots `capture`
itself is writing, so making it *un*-ignorable the same way would mean a
capture fingerprinting its own half-written manifest.

That carve-out first shipped by leaving `.ctx/runtime` "governed by `ignore`
exactly as before" — which quietly depended on `DEFAULT_IGNORE` still
carrying a matching pattern. A `review: {ignore: [...]}` that replaces the
whole set with something that never mentions `.ctx/runtime` at all (not a
hostile one aimed at the ledger — an ordinary `["vendor/"]`) removed that
pattern along with everything else, and nothing else stood in for it. The
exclusion is now unconditional and independent of `ignore` in both
directions: `.ctx/` cannot be hidden, and `.ctx/runtime` cannot be exposed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import snapshot  # noqa: E402
from support import Fixture  # noqa: E402

# A path under the ledger that no unit's `owns` would ever name — standing in
# for a write a unit made outside its declared scope, landing in `.ctx/`.
ROGUE_RELPATH = ".ctx/plans/other-plan/units/rogue-file.md"
ROGUE_MARK = "NOBODY_IN_THIS_UNIT_OWNS_THIS"


class TestLedgerDirectoryCannotBeIgnoredAway(Fixture):
    """`review: {ignore: [...]}` narrows what `capture` may skip; it must
    never be able to widen that far enough to hide the ledger itself."""

    KEY = "ledger-ignore-scope"

    def _hostile_config(self, ignore):
        """A config whose `review.ignore` *replaces* the default set, the way
        `settings()` already documents it does — this is not a made-up shape."""
        config = dict(self.config)
        config["review"] = {"ignore": list(ignore)}
        return config

    def test_a_repo_ignore_cannot_hide_ctx_from_fingerprinting(self):
        """The exact scenario: a unit writes into `.ctx/` it does not own, and
        a repo `ctx.yaml` sets `review: {ignore: [".ctx/"]}` to hide it. The
        write must still be fingerprinted, and still show up in the diff the
        scope-violation check is built from.

        This assertion is the one that fails against the pre-fix code: before
        the fix, `.ctx/` matched the hostile ignore pattern like any other
        directory, `walk` pruned it, and neither manifest ever recorded
        `ROGUE_RELPATH` at all.
        """
        hostile = self._hostile_config([".ctx/"])
        before = snapshot.capture(self.layout, hostile, self.KEY, self.root)
        self.write(ROGUE_RELPATH, f"{ROGUE_MARK} = 1\n")
        after = snapshot.capture(
            self.layout, hostile, self.KEY, self.root, force=True
        )
        self.assertIn(ROGUE_RELPATH, after["files"],
                      "the ledger write was invisible to the manifest")
        delta = snapshot.compare(before, after)
        self.assertIn(
            ROGUE_RELPATH, delta.added,
            "the ledger write never reached the diff the scope check consumes",
        )

    def test_a_legitimate_non_ledger_ignore_entry_still_works(self):
        """Narrowing what is ignorable must not become removing the mechanism:
        a real, non-ledger `ignore` entry still hides exactly what it names."""
        hostile = self._hostile_config(["vendor/"])
        self.write("vendor/thirdparty.py", "x = 1\n")
        self.write("src/mine.py", "x = 1\n")
        manifest = snapshot.capture(
            self.layout, hostile, self.KEY + "-legit", self.root
        )
        self.assertNotIn("vendor/thirdparty.py", manifest["files"])
        self.assertIn("src/mine.py", manifest["files"])

    def test_the_ledgers_own_runtime_is_still_never_captured(self):
        """`.ctx/runtime` holds the snapshots themselves. Making the rest of
        the ledger un-ignorable must not drag `runtime` along with it, even
        under a hostile ignore override that would otherwise have hidden the
        whole `.ctx/` tree — a capture must never fingerprint its own
        half-written manifest."""
        hostile = self._hostile_config([".ctx/"])
        manifest = snapshot.capture(
            self.layout, hostile, self.KEY + "-runtime", self.root, force=True
        )
        runtime_paths = [
            p for p in manifest["files"] if p.startswith(".ctx/runtime/")
        ]
        self.assertEqual(runtime_paths, [])

    def test_an_ordinary_ignore_that_never_mentions_ctx_still_excludes_runtime(self):
        """The review scenario, not the hostile one: `review: {ignore:
        [\"vendor/\"]}` names nothing under `.ctx/` at all — it is not aimed at
        the ledger, it just replaces `DEFAULT_IGNORE` wholesale the way
        `settings()` always has. `.ctx/runtime` must still be excluded:
        depending on the replaced ignore list to happen to still cover it is
        exactly what let this gap through before."""
        self.write(".ctx/runtime/marker.json", "{}\n")
        ordinary = self._hostile_config(["vendor/"])
        manifest = snapshot.capture(
            self.layout, ordinary, self.KEY + "-ordinary-ignore", self.root,
            force=True,
        )
        runtime_paths = [
            p for p in manifest["files"] if p.startswith(".ctx/runtime/")
        ]
        self.assertEqual(runtime_paths, [])

    def test_ledger_bookkeeping_does_not_starve_source_files_of_the_cap(self):
        """`.ctx/` can no longer be excluded from the walk, so a plain
        alphabetical directory order — `.ctx` sorts before every ordinary
        name — would visit ledger bookkeeping first and could exhaust
        `max_files` before a real source directory is ever entered. With a
        cap of 2 and one `.ctx/` file plus one real source file, the source
        file must be the one that survives."""
        self.write(".ctx/plans/some-plan/units/01-a.md", "unit\n")
        self.write("src/real_code.py", "x = 1\n")
        found, truncated = snapshot.walk(self.root, max_files=1)
        self.assertTrue(truncated)
        self.assertEqual(found, ["src/real_code.py"])

    def test_walk_reports_the_ledger_regardless_of_how_ignore_spells_it(self):
        """Lower-level pin on `walk` itself, independent of `capture`'s own
        bookkeeping: `.ctx/` paths come back from the walk even when `ignore`
        is exactly what would otherwise have excluded them."""
        self.write(ROGUE_RELPATH, f"{ROGUE_MARK} = 1\n")
        found, _truncated = snapshot.walk(
            self.root, ignore=(".ctx/",), protect=("ctx/",)
        )
        # A `protect` entry that does not match the real ledger prefix earns
        # no special treatment — the guarantee is specifically about `.ctx/`,
        # not about `protect` overriding `ignore` for anything asked of it.
        self.assertNotIn(ROGUE_RELPATH, found)
        found, _truncated = snapshot.walk(
            self.root, ignore=(".ctx/",), protect=(".ctx/",)
        )
        self.assertIn(ROGUE_RELPATH, found)


if __name__ == "__main__":
    unittest.main()
