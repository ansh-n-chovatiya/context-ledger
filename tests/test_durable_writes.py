"""One enumeration, over every module that writes a git-tracked ledger artifact.

`Path.write_text` truncates the destination before it writes a byte. A process
that dies in that window — a ^C, an OOM kill, a laptop lid — leaves a prefix of
the new file where the old one was, and every reader downstream sees a document
that parses to something plausible and wrong. `atomic.write_text` writes a temp
file in the destination's own directory, fsyncs it and `os.replace`s it into
place, so a reader sees the old file or the new one and never a half of either.

This file is the guard that makes the *next* plain write somebody adds fail
here, at the line they added it, instead of shipping and tearing a `plan.json`
once a year on somebody else's machine.

Unit 07 wrote a four-module version of this scoped to what that unit owned
(`tests/test_migration_durability.py`, `OWNED_MODULES`). This is the complete
set: `journal.py` and `plan.py` belonged to sibling units then and have landed
since, so there is no longer any reason for the enumeration to be partial.
"""

import ast
import unittest
from pathlib import Path


# Named one by one, never globbed over `ctx/*.py`. A glob would also sweep in
# the runtime writers below, whoever hit that failure first would weaken the
# assertion until it caught nothing, and a weakened guard is worse than none —
# it reads as protection while providing none.
#
# Every module here writes a file that is committed and shared: the journal and
# its digest, `plan.json` and its archived revisions, the plan README, migrated
# ledgers, dispatch snapshots and seals, the context index, contract seals.
LEDGER_MODULES = (
    "journal.py",
    "plan.py",
    "migrate.py",
    "snapshot.py",
    "bundle.py",
    "contract.py",
)

# Deliberately excluded, and listed so the next reader does not "fix" it:
#
#   trust.py    the machine-local acceptance store, outside the repository
#   verify.py   captured command output under gitignored `runtime/verify/`
#   state.py    the disposable session pointer and its nudge, in `runtime/`
#   hooks.py    the hook error log, in `runtime/`
#
# None of these files is tracked, none is read by anything but the machine that
# wrote it, and a torn one is discarded and rebuilt rather than merged. Adding
# them here would buy nothing and would put an atomic replace on the hot path of
# every hook fire.
RUNTIME_WRITERS_DELIBERATELY_EXCLUDED = (
    "trust.py", "verify.py", "state.py", "hooks.py",
)


def plain_write_text_calls(source, name):
    """`[(name, lineno)]` for every `<something>.write_text(...)` that is not
    `atomic.write_text(...)`. Parsed, not grepped: a comment mentioning
    `write_text` is not a call, and this must not be dodgeable by wrapping the
    line."""
    offenders = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "write_text"):
            continue
        if isinstance(func.value, ast.Name) and func.value.id == "atomic":
            continue  # `atomic.write_text(path, text)` is the sanctioned form
        offenders.append((name, node.lineno))
    return offenders


class TestEveryLedgerWriteIsAtomic(unittest.TestCase):

    def package(self):
        return Path(__file__).resolve().parent.parent / "ctx"

    def test_no_ledger_module_calls_path_write_text(self):
        offenders = []
        for name in LEDGER_MODULES:
            path = self.package() / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            offenders += plain_write_text_calls(
                path.read_text(encoding="utf-8"), name)
        self.assertEqual(
            offenders, [],
            "route these through ctx.atomic.write_text: "
            + ", ".join(f"{n}:{line}" for n, line in offenders),
        )

    def test_plan_py_is_in_the_enumeration(self):
        """`plan.json` is the wave graph: which units may run concurrently, and
        what each of them owns. A torn one mis-resolves the graph, so this
        module being covered is the point of widening the set, not an
        incidental extra."""
        self.assertIn("plan.py", LEDGER_MODULES)

    def test_journal_py_is_in_the_enumeration(self):
        self.assertIn("journal.py", LEDGER_MODULES)

    def test_the_set_is_the_whole_set(self):
        """Every module in `ctx/` is accounted for: either it is enumerated
        above, or it is one of the four runtime writers excluded on purpose, or
        it does not call `write_text` at all.

        Without this, the enumeration decays silently — a new module that writes
        a tracked file is covered by nothing, and no test anywhere goes red.
        """
        unaccounted = []
        for path in sorted(self.package().glob("*.py")):
            if path.name in LEDGER_MODULES:
                continue
            if path.name in RUNTIME_WRITERS_DELIBERATELY_EXCLUDED:
                continue
            if plain_write_text_calls(path.read_text(encoding="utf-8"), path.name):
                unaccounted.append(path.name)
        self.assertEqual(
            unaccounted, [],
            "these write with a plain Path.write_text and are in neither list — "
            "add them to LEDGER_MODULES if the file is tracked, or to the "
            "excluded runtime writers if it lives under runtime/: "
            + ", ".join(unaccounted),
        )

    def test_the_enumeration_would_catch_a_new_offender(self):
        """The guard above is only a guard if it can fail. Feed it one."""
        self.assertEqual(
            plain_write_text_calls(
                "def f(path):\n    path.write_text('x')\n", "sample.py"),
            [("sample.py", 2)],
        )

    def test_the_sanctioned_form_is_not_reported(self):
        self.assertEqual(
            plain_write_text_calls(
                "def f(path):\n    atomic.write_text(path, 'x')\n", "sample.py"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
