"""Pins the surgical-change wording in the agent prompts."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
BASE = {"unit-runner": 3226, "reviewer": 4787, "re-reviewer": 5040}
LIMIT = {"unit-runner": 900, "reviewer": 700, "re-reviewer": 700}
RUNNER_STRINGS = ["assumptions:", "noticed:", "skipped / unverified:",
                  "take precedence over \"smaller\""]
REVIEW_STRINGS = ["Unrequested change", "`important` at most", "never critical"]
# Front matter as it was before this unit.
FRONT = {
    "unit-runner": ["name", "description", "tools"],
    "reviewer": ["name", "description", "tools"],
    "re-reviewer": ["name", "description", "tools"],
}
TOOLS = {
    "unit-runner": "Bash, Read, Grep, Glob, Edit, Write, NotebookEdit",
    "reviewer": "Read, Grep, Glob",
    "re-reviewer": "Read, Grep, Glob",
}
# Pre-change excerpts, pinned so the positive controls never depend on git.
OLD_RUNNER = """## Rules

1. **Never write outside `owns`.** If the objective cannot be met without it,
   stop and report that — do not do it anyway. Wave isolation is the reason
   several units can run at once.
5. **Run the `verify` checks yourself** before reporting. Do not report success
   on unverified work.

## Return

Your final message is the report the orchestrator acts on. No preamble, no
summary of what you read.

```
unit: <name>
status: done | blocked
files_changed:
  - <path>
criteria:
  1: pass | fail — <evidence>
verify: <verbatim output, or the failing command and its exit code>
interface_changed: none | <what, and why it was unavoidable>
notes: <only what the next unit must know; omit if nothing>
```
"""
OLD_REVIEWER = """## Rules

- **You do not dispatch subagents.** Never spawn a reviewer for a second opinion
  or to split the diff.
- **Severity.** `critical` = broken behaviour, data loss, security. `important` =
  this unit cannot be trusted until it is fixed — a missed acceptance criterion,
  incorrect or fragile behaviour. `minor` = polish, and "coverage could be
  broader". Not everything is Critical; inflation makes the loop useless.
- Every finding cites `file:line`. A finding with no location is not actionable.
"""


def read(n):
    return (AGENTS / f"{n}.md").read_text(encoding="utf-8")


def front(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "front matter missing"
    out = {}
    for line in m.group(1).splitlines():
        assert ": " in line, f"front matter line lacks ': ': {line!r}"
        k, v = line.split(": ", 1)
        out[k] = v
    return out


def missing(text, strings):
    return [s for s in strings if s not in text]


class AgentPrompts(unittest.TestCase):
    def test_runner_wording_and_growth(self):
        t = read("unit-runner")
        self.assertEqual(missing(t, RUNNER_STRINGS), [])
        self.assertLessEqual(len(t.encode()) - BASE["unit-runner"], LIMIT["unit-runner"])

    def test_reviewers_wording_and_growth(self):
        for n in ("reviewer", "re-reviewer"):
            t = read(n)
            self.assertEqual(missing(t, REVIEW_STRINGS), [], n)
            self.assertIn("scope-violation", t)
            self.assertLessEqual(len(t.encode()) - BASE[n], LIMIT[n], n)

    def test_positive_control_old_text_lacks_new_strings(self):
        self.assertEqual(missing(OLD_RUNNER, RUNNER_STRINGS), RUNNER_STRINGS)
        self.assertEqual(missing(OLD_REVIEWER, ["Unrequested change"]),
                         ["Unrequested change"])
        self.assertEqual(missing(read("unit-runner"), RUNNER_STRINGS), [])

    def test_front_matter_unchanged(self):
        for n, keys in FRONT.items():
            fm = front(read(n))
            self.assertEqual(list(fm), keys, n)
            self.assertEqual(fm["tools"], TOOLS[n], n)


if __name__ == "__main__":
    unittest.main()
