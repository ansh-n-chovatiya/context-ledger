"""Slash-command descriptions are listed in context every session: keep them short."""

import unittest
from pathlib import Path

COMMANDS = Path(__file__).resolve().parent.parent / "commands"
LIMIT = 80

NAMES = set("""ask context decide doctor drop escalate findings handoff init load merge
next phase plan preview resume review save spec start status task trust verify""".split())

OLD_PHASE = ("Advance or inspect a unit's phase gate (reproduce/locate/fix/guard "
             "for kind: bug, or a declared `phases:` list)")
OLD_TRUST = "Review the shell commands the done-gate will run on this machine, and accept them"


# Commands that take no argument; every other command carries `argument-hint`.
NO_HINT = {"drop", "next", "resume", "status"}


def within_limit(description):
    return len(description) <= LIMIT


def front(path):
    lines = path.read_text().splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    out = {}
    for ln in lines[1:end]:
        k, _, v = ln.partition(":")
        out[k.strip()] = v.strip()
    return out


class CommandText(unittest.TestCase):
    def test_descriptions_within_limit(self):
        for p in sorted(COMMANDS.glob("*.md")):
            d = front(p)["description"]
            self.assertTrue(within_limit(d), p.name)

    def test_positive_control_old_descriptions_fail(self):
        self.assertFalse(within_limit(OLD_PHASE))
        self.assertFalse(within_limit(OLD_TRUST))

    def test_command_set_and_keys(self):
        self.assertEqual({p.stem for p in COMMANDS.glob("*.md")}, NAMES)
        for p in COMMANDS.glob("*.md"):
            keys = set(front(p)) - {"description"}
            expected = {"allowed-tools"} | (set() if p.stem in NO_HINT else {"argument-hint"})
            self.assertEqual(keys, expected, p.name)

    def test_status_has_no_second_pass(self):
        t = (COMMANDS / "status.md").read_text()
        self.assertNotIn("Summarise", t)
        self.assertIn('ctx" status', t)
        self.assertEqual(front(COMMANDS / "status.md")["allowed-tools"], "Bash")


if __name__ == "__main__":
    unittest.main()
