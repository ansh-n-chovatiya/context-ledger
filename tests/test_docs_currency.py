"""The documentation, checked against the code it describes.

Every claim in here is one that has already gone stale at least once. The
README described a `phase` subcommand that did not exist, then shipped one it
never mentioned; it claimed seven verify kinds while `verify.KIND_TABLE` held
eight; it documented a `plan:` block that had grown a key. Prose has no
compiler, so the checkable half of it gets a test instead.

Three rules shape what is asserted:

**Read the claim from the code, never from another document.** The subcommand
list comes from `cli.commands()`, the kinds from `verify.KIND_TABLE`, the
configuration keys from `config.DEFAULTS`. A test that compared one document
with another would go green the moment both were wrong in the same way, which
is exactly how a copied-forward summary becomes a third false claim.

**Assert coverage, not wording.** These tests say a subcommand is named
somewhere a reader will find it; they do not say what the sentence beside it
must be. A test that pins prose is a test somebody deletes.

**Only claims a machine can settle.** Whether a paragraph is *true* is not
checkable here. Whether the command it names exists is.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import cli, config, verify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"

# The documents that describe the tool as it is now. `.ctx/` is the project's
# own ledger and `docs/history/` is archived by definition: both are records of
# a past state, and holding them to the present would mean falsifying them.
def living_docs():
    return [README] + sorted(
        path for path in DOCS.rglob("*.md") if "history" not in path.parts
    )


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, check=True
    )
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def read(path):
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# markdown helpers
# --------------------------------------------------------------------------- #

_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def table_cells(text, column=0):
    """Every cell in `column` of every pipe table in `text`.

    Separator rows (`|---|---|`) are dropped; header rows are not, because a
    header cell never looks like a command name and keeping the parse simple
    is worth more than excluding them.
    """
    cells = []
    for line in text.splitlines():
        match = _TABLE_ROW.match(line.strip())
        if not match:
            continue
        parts = [part.strip() for part in match.group(1).split("|")]
        if all(set(part) <= set("-: ") for part in parts):
            continue
        if column < len(parts):
            cells.append(parts[column])
    return cells


def slugs(text):
    """GitHub's heading anchors, close enough to catch a broken link."""
    found = set()
    for _level, title in _HEADING.findall(text):
        slug = title.replace("`", "").lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        found.add(re.sub(r"\s+", "-", slug.strip()))
    return found


# --------------------------------------------------------------------------- #
# the command surface
# --------------------------------------------------------------------------- #

class CommandTableTests(unittest.TestCase):
    def test_every_registered_subcommand_is_in_a_cli_table(self):
        """`ctx phase` shipped undocumented for two minor versions.

        The names come from the registry, so a subcommand added later fails
        this until somebody writes the row.
        """
        documented = set()
        for cell in table_cells(read(README)):
            match = re.match(r"^`ctx ([a-z][a-z-]*)", cell)
            if match:
                documented.add(match.group(1))
        missing = sorted({entry.name for entry in cli.commands()} - documented)
        self.assertFalse(
            missing,
            "these subcommands are registered in ctx/cli.py but appear in no "
            "README CLI table: " + ", ".join(missing),
        )

    def test_no_cli_table_row_names_a_subcommand_that_does_not_exist(self):
        known = {entry.name for entry in cli.commands()}
        for cell in table_cells(read(README)):
            match = re.match(r"^`ctx ([a-z][a-z-]*)", cell)
            if match:
                self.assertIn(
                    match.group(1), known,
                    f"README documents `ctx {match.group(1)}`, which is not a "
                    "registered subcommand",
                )

    def test_every_slash_command_file_is_in_the_slash_table(self):
        """A shipped command nobody lists costs context and earns nothing."""
        documented = set()
        for cell in table_cells(read(README)):
            match = re.match(r"^`/ctx:([a-z-]+)", cell)
            if match:
                documented.add(match.group(1))
        shipped = {path.stem for path in (ROOT / "commands").glob("*.md")}
        self.assertEqual(
            shipped - documented, set(),
            "these files exist in commands/ but are in no README slash table",
        )
        self.assertEqual(
            documented - shipped, set(),
            "the README's slash table promises commands that do not ship",
        )


# --------------------------------------------------------------------------- #
# the claims the code can settle
# --------------------------------------------------------------------------- #

_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_KIND_COUNT = re.compile(r"\b([A-Za-z]+|\d+) kinds\b")


class ClaimedCountTests(unittest.TestCase):
    def test_the_claimed_verify_kind_count_matches_the_table(self):
        """"Seven kinds" outlived the seventh kind by a whole release."""
        claims = []
        for path in living_docs():
            for raw in _KIND_COUNT.findall(read(path)):
                value = _COUNT_WORDS.get(raw.lower())
                if value is None and raw.isdigit():
                    value = int(raw)
                if value is not None:
                    claims.append((path.name, value))
        self.assertTrue(
            claims, "no document states how many verify kinds there are"
        )
        for name, value in claims:
            self.assertEqual(
                value, len(verify.KINDS),
                f"{name} claims {value} verify kinds; verify.KIND_TABLE holds "
                f"{len(verify.KINDS)}",
            )

    def test_every_verify_kind_is_documented(self):
        text = "\n".join(read(path) for path in living_docs())
        for kind in verify.KINDS:
            self.assertIn(
                f"`{kind}`", text, f"the `{kind}` verify kind is undocumented"
            )

    def test_every_json_command_is_named_in_the_json_section(self):
        text = read(DOCS / "reference.md")
        for name in cli.JSON_COMMANDS:
            self.assertIn(
                f"`{name}`", text,
                f"`ctx {name} --json` is registered but the reference does not "
                "name it",
            )
        self.assertIn(
            f'"schema": {cli.JSON_SCHEMA}', text,
            "the documented envelope does not carry cli.JSON_SCHEMA",
        )

    def test_every_configuration_default_is_documented(self):
        """A key `ctx init` writes into every new ledger and nobody explains."""
        text = "\n".join(read(path) for path in living_docs())
        missing = []
        for dotted, leaf, parent in _flatten(config.DEFAULTS):
            if dotted in text:
                continue
            # Or the form a YAML example uses: the leaf spelled as a mapping
            # key, with its enclosing block named somewhere in the same set of
            # documents. `enabled:` alone would match anything; `enabled:`
            # under a documented `journal:` is the claim being made.
            if f"{leaf}:" in text and (not parent or f"{parent}:" in text):
                continue
            missing.append(dotted)
        self.assertFalse(
            missing,
            "config.DEFAULTS carries these keys and no document mentions them: "
            + ", ".join(missing),
        )


def _flatten(mapping, prefix=""):
    """`(dotted, leaf, parent)` for every leaf key in `config.DEFAULTS`."""
    keys = []
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            keys.extend(_flatten(value, f"{dotted}."))
        else:
            keys.append((dotted, key, prefix.rstrip(".").split(".")[-1]))
    return keys


# --------------------------------------------------------------------------- #
# links, and the documents that moved
# --------------------------------------------------------------------------- #

class LinkTests(unittest.TestCase):
    def markdown(self):
        return [path for path in tracked_files()
                if path.suffix == ".md" and ".ctx" not in path.parts]

    def test_every_internal_link_resolves(self):
        broken = []
        for path in self.markdown():
            text = read(path)
            own = slugs(text)
            for target in _LINK.findall(text):
                if target.startswith(("http://", "https://", "mailto:", "#!")):
                    continue
                if target.startswith("#"):
                    if target[1:] not in own:
                        broken.append(f"{path.name} → {target} (no such heading)")
                    continue
                relative, _, fragment = target.partition("#")
                destination = (path.parent / relative).resolve()
                if not destination.exists():
                    broken.append(f"{path.name} → {target} (no such path)")
                    continue
                if fragment and destination.suffix == ".md":
                    if fragment not in slugs(read(destination)):
                        broken.append(
                            f"{path.name} → {target} (no such heading there)"
                        )
        self.assertFalse(broken, "broken internal links:\n  " + "\n  ".join(broken))


class ArchivedDocumentTests(unittest.TestCase):
    """`AUDIT.md` and `PRODUCTION-AUDIT.md` moved to `docs/history/`.

    Both described releases four and two minors old while sitting at the root
    where a reader takes them as current — `PRODUCTION-AUDIT.md`'s "snapshot.py
    and findings.py: zero tests, zero callers" is false twice over now. They
    record real decisions, so they are archived rather than deleted, and
    nothing living may point at where they used to be.
    """

    MOVED = ("AUDIT.md", "PRODUCTION-AUDIT.md")

    # Dated records of a past state. Rewriting a changelog entry or an audit to
    # use a path that did not exist when it was written would falsify it, so
    # these are exempt by rule rather than by convenience.
    EXEMPT = (
        ".ctx",                              # the project's own ledger
        "CHANGELOG.md",                      # release notes, dated
        "report.md",                         # the current audit, dated
        "docs/history",                      # the archived documents themselves
        "tests/test_audit_merge_preflight.py",  # cites the audit that flagged it
        "tests/test_docs_currency.py",       # this file names them to check them
    )

    def test_they_are_archived_and_not_deleted(self):
        for name in self.MOVED:
            self.assertFalse((ROOT / name).exists(), f"{name} is back at the root")
            self.assertTrue((ROOT / "docs/history" / name).is_file())

    def test_each_carries_a_historical_header(self):
        for name in self.MOVED:
            first = read(ROOT / "docs/history" / name).splitlines()[0]
            self.assertIn("Historical", first, f"{name} does not say it is historical")
            self.assertRegex(
                first, r"v\d+\.\d+\.\d+",
                f"{name}'s header does not name the version it describes",
            )

    def test_nothing_living_points_at_the_old_location(self):
        offenders = []
        for path in tracked_files():
            relative = path.relative_to(ROOT).as_posix()
            if any(relative.startswith(skip) for skip in self.EXEMPT):
                continue
            if not path.is_file():
                continue
            try:
                text = read(path)
            except UnicodeDecodeError:
                continue
            for name in self.MOVED:
                for match in re.finditer(re.escape(name), text):
                    before = text[max(0, match.start() - 13):match.start()]
                    if before.endswith("docs/history/"):
                        continue
                    offenders.append(f"{relative} references {name}")
        self.assertFalse(
            offenders,
            "these point at the pre-archive location:\n  " + "\n  ".join(
                sorted(set(offenders))
            ),
        )


# --------------------------------------------------------------------------- #
# the split itself
# --------------------------------------------------------------------------- #

class SplitTests(unittest.TestCase):
    def test_the_readme_stays_short_enough_to_read(self):
        lines = len(read(README).splitlines())
        self.assertLess(
            lines, 300,
            f"README.md is {lines} lines; reference material belongs in docs/",
        )

    def test_the_documents_the_readme_promises_exist(self):
        for name in ("reference.md", "walkthroughs.md", "operations.md"):
            self.assertTrue((DOCS / name).is_file(), f"docs/{name} is missing")


if __name__ == "__main__":
    unittest.main()
