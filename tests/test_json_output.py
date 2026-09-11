"""`--json` on the seven commands a pipeline reads: one flag, one helper.

The audit's complaint was that everything ctx knows is prose, so a pipeline
that wants an answer has to regex a status board. The fix is not seven
serialisers — that is the same forty-one-copies problem the registry was for —
it is one `_emit(data, human_fn)`, one envelope, and one place that writes it:
`cli._finish`, after the exit code is known.

Four properties, and the order they are tested in is the order they break in:

1. **The prose is untouched.** `GOLDEN` below was captured from the command
   layer *before* `--json` existed and committed verbatim. Every line of it is
   asserted, per command. A `--json` that quietly reworded `ctx status` would
   be a rewrite of every command wearing a flag's clothes.
2. **The flag only changes rendering.** Same exit code, and the prose that
   would have been printed is in the document's `lines` instead — because
   `main` captures stdout for the whole call rather than trusting each printer.
3. **The shapes are a contract.** Each of the seven is pinned key by key. A
   consumer that cannot rely on the shape is back to regexing, only now
   against JSON.
4. **Refusals compose.** `--json` with `--strict`, and `--json` against a
   refusal, still exit non-zero, still put the reason on stderr, and still put
   one whole, valid JSON document on stdout — never half of one.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import cli, frontmatter, plan as plan_mod  # noqa: E402
from support import OK, Fixture  # noqa: E402


CRITERIA = "## Objective\nDo the thing.\n\n## Acceptance criteria\n1. it works\n"

# The seven, and the argv each is exercised with. One ledger, built by
# `Scenario.build`, run in this order — the goldens were captured in it.
COMMANDS = [
    ("status", ["status"]),
    ("next", ["next"]),
    ("doctor", ["doctor"]),
    ("ci", ["ci"]),
    ("verify", ["verify"]),
    ("plan-check", ["plan-check", "auth"]),
    ("findings", ["findings", "02-logout", "--plan", "auth"]),
]


class Scenario(Fixture):
    """A ledger with enough in it that all seven commands have work to do:
    a plan, two units in a wave, one of them focused and gated, one finding,
    and a `verify` block on disk that has drifted from ctx.yaml."""

    def setUp(self):
        super().setUp()
        self.build()

    def build(self):
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", "auth", "--no-spec")[0], 0)
        directory = plan_mod.units_dir(self.layout, "auth")
        directory.mkdir(parents=True, exist_ok=True)
        for name, status in (("01-login", "done"), ("02-logout", "running")):
            frontmatter.Document(
                {"ctx_schema": 1, "unit": name, "plan": "auth", "tier": "subagent",
                 "depends_on": [], "owns": [f"src/{name}.py"], "reads": [],
                 "forbid": [], "budget_tokens": 1000, "status": status,
                 "wave": 1, "verify": [{"kind": "cmd", "run": OK}]},
                CRITERIA,
            ).write(directory / f"{name}.md")
        self.assertEqual(self.cli("plan-check", "auth")[0], 0)
        self.assertEqual(self.cli("unit", "02-logout", "--plan", "auth")[0], 0)
        self.cli("findings", "02-logout", "--plan", "auth", "--add", "minor",
                 "--summary", "a nit", "--where", "src/02-logout.py")

    # ----------------------------------------------------------------------- #
    # capture
    # ----------------------------------------------------------------------- #

    def normalise(self, text):
        """Everything about the output that is about *this* machine, removed.

        The golden text has to survive a different temp directory, a different
        interpreter path and tomorrow; nothing else about it may move.
        """
        text = text.replace(str(self.root), "<ROOT>")
        text = text.replace(str(self.untracked), "<GLOBAL>")
        text = text.replace(sys.executable, "<PYTHON>")
        text = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+", "<TS>", text)
        text = re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", text)
        text = re.sub(r"\b\d{2}:\d{2}\b", "<TIME>", text)
        return text

    def human(self, argv):
        """`(exit code, normalised stdout lines)` without the flag."""
        code, out, err = self.cli_streams(*argv)
        self.assertEqual(err, "", f"{argv} wrote to stderr")
        return code, self.normalise(out).split("\n")[:-1]

    def document(self, argv):
        """`(exit code, the parsed document, stderr)` with the flag.

        `json.loads` over the whole of stdout is the assertion that matters:
        it fails if a single line of prose was printed beside the document.
        """
        code, out, err = self.cli_streams(*argv, "--json")
        try:
            document = json.loads(out)
        except ValueError as exc:  # pragma: no cover — the failure message
            self.fail(f"{argv} --json did not write one JSON document: {exc}\n"
                      f"{out!r}")
        return code, document, err


def rewritten(lines):
    """Lines with the plan graph revision blanked.

    `plan-check` writes a new revision every time it runs, so the same command
    run twice — which is exactly what comparing the two renderings does — is
    not expected to name the same one.
    """
    return [re.sub(r"\br\d+\b", "r<N>", line) for line in lines]


GOLDEN = {
    'status': (0, 
        """\
level    L2 (planned)   profile code
task     —
plan     auth   unit 02-logout
briefing 239/2600 chars (~66 tokens)

wave board — plan auth:
  wave 1
     01-login                 subagent  done
   → 02-logout                subagent  running (in flight)
   next: wave 1 is in flight — 1 unit(s) dispatched, none left to start; review what comes back

recent journal:
  <DATE> <TIME> | plan | auth | opened (0 unit stubs)
  <DATE> <TIME> | plan | auth | checked, graph r1
  <DATE> <TIME> | unit | 02-logout | running
  <DATE> <TIME> | finding | 02-logout | minor #1
""".split("\n")[:-1]),
    'next': (0, 
        """\
next: /ctx:verify
      unit 02-logout is in progress — run its gate
""".split("\n")[:-1]),
    'doctor': (0, 
        """\
## layout
  ok   .ctx
  ok   .ctx/tasks
  ok   .ctx/specs
  ok   .ctx/plans
  ok   .ctx/contexts
  ok   .ctx/journal
  ok   .ctx/decisions
  ok   .ctx/runtime
  ok   .ctx/runtime/verify
## briefing budget
  ok   L0 93/220 chars (~26 tokens)
  ok   L1 83/900 chars (~23 tokens)
  ok   L2 239/2600 chars (~66 tokens)
## verify commands
  none configured (fine at L0; required for L1/L2 gates)
## command trust
  ok   1 command(s) accepted on this machine
## verify drift
  warn 2 work file(s) carry a `verify` block that no longer matches ctx.yaml
       .ctx/plans/auth/units/01-login.md
       .ctx/plans/auth/units/02-logout.md
       that is expected for finished work; re-scaffold if it is not
## decisions
  ok   every ADR id is claimed by exactly one file
## plugin footprint
  the briefing above is the hook cost only; the plugin's own always-on
  context is separate — measure it with: claude plugin details ctx
## policy
  none system     /etc/ctx/policy.yaml  (absent)
  none user       <GLOBAL>/global/policy.yaml  (absent)
  ok   repo       /private<ROOT>/.ctx/ctx.yaml
       no policy above the repository — ctx.yaml decides everything
## gate
  enabled=True  max_attempts=3

all checks passed
""".split("\n")[:-1]),
    'ci': (0, 
        """\
## ledger
  ok   layout complete
  ok   schema current
## budgets
  ok   L0 briefing fits without truncation
  ok   L1 briefing fits without truncation
  ok   L2 briefing fits without truncation
## verify commands
  none configured
## command trust
  ok   every verify command is accepted on this machine
## decisions
  ok   every ADR id is claimed by exactly one file
## spec
  ok   auth has no open blocking questions
## plan auth
  ok   graph is valid and collision-free

all checks passed
""".split("\n")[:-1]),
    'verify': (0, 
        """\
02-logout — PASS
  ok      cmd: "<PYTHON>" -c pass
""".split("\n")[:-1]),
    'plan-check': (0, 
        """\
plan auth: 2 unit(s) in 1 wave(s) · graph r2
  wave 1: 01-login, 02-logout
wrote .ctx/plans/auth/plan.json
""".split("\n")[:-1]),
    'findings': (0, 
        """\
round 1/3 — 1 minor open
  [1] minor: a nit (src/02-logout.py)
""".split("\n")[:-1]),
}

# --------------------------------------------------------------------------- #
# the shapes
# --------------------------------------------------------------------------- #
#
# A spec is a dict (keys must match exactly — an added key is a new promise, a
# removed one is a broken one), a `Subset` (these keys at least, because the
# row carries extra fields that depend on what it found), a one-item list (the
# spec for every element), or a type / tuple of types.

NONE = type(None)


class Subset(dict):
    """These keys at least, with these types. For rows that carry extras."""


ENVELOPE = {
    "schema": int,
    "command": str,
    "ok": bool,
    "exit_code": int,
    "advisory": [str],
    "error": (str, NONE),
    "data": (dict, NONE),
    "lines": [str],
}

SHAPES = {
    "status": {
        "level": int,
        "level_name": str,
        "profile": (str, NONE),
        "task": (str, NONE),
        "plan": (str, NONE),
        "unit": (str, NONE),
        "briefing": {"chars": int, "cap": int, "approx_tokens": int,
                     "truncated": bool},
        "attempts": dict,
        "board": {
            "plan": str,
            "units": [{"wave": int, "name": str, "tier": str, "status": str,
                       "owns": [str], "active": bool, "unsealed": bool}],
            "problems": [str],
            "unsealed": [str],
            "next_wave": (int, NONE),
            "in_flight": bool,
        },
        "orchestrator_edits": [str],
        "journal": {"entries": [str], "earlier": int},
    },
    "next": {"action": str, "why": str},
    "doctor": {
        "problems": int,
        "ok": bool,
        # Every row says which section it came from and how it went; the rest
        # of its keys depend on what the section checks.
        "checks": [Subset({"section": str, "status": str,
                           "detail": (str, NONE)})],
    },
    "ci": {
        "ok": bool,
        "failures": [str],
        "checks": [{"section": str, "name": str, "ok": bool, "detail": str}],
    },
    "verify": {
        "mode": str,
        "plan": (str, NONE),
        "key": (str, NONE),
        "verdict": (str, NONE),
        "checks": [{"kind": str, "label": str, "status": str, "message": str}],
        "pending": [{"kind": str, "message": str}],
    },
    "plan-check": {
        "plan": str,
        "problems": [str],
        "units": int,
        "revision": (int, NONE),
        "graph": (str, NONE),
        "waves": [{"wave": int, "units": [{"name": str, "tier": str}]}],
        "session_units": [str],
    },
    "findings": {
        "mode": str,
        "plan": str,
        "unit": str,
        "round": int,
        "summary": str,
        "findings": [{"id": int, "severity": str, "status": str, "summary": str,
                      "evidence": str, "where": str, "round": int,
                      "ruling": str, "source": str}],
        "escalations": [{"round": int, "from": str, "to": str, "reason": str}],
        "blocking": int,
        "ok": bool,
        "problem": (str, NONE),
    },
}


class ShapeAssertions:
    """`assertShape`, used by every test below and defined once."""

    def assertShape(self, value, spec, where="data"):
        if isinstance(spec, Subset):
            self.assertIsInstance(value, dict, where)
            missing = set(spec) - set(value)
            self.assertFalse(missing, f"{where}: {sorted(missing)} missing")
            for key, sub in spec.items():
                self.assertShape(value[key], sub, f"{where}.{key}")
        elif isinstance(spec, dict):
            self.assertIsInstance(value, dict, where)
            self.assertEqual(
                sorted(value), sorted(spec),
                f"{where}: the keys changed. That is a breaking change to a "
                "documented contract — bump cli.JSON_SCHEMA and say so.")
            for key, sub in spec.items():
                self.assertShape(value[key], sub, f"{where}.{key}")
        elif isinstance(spec, list):
            self.assertIsInstance(value, list, where)
            for index, item in enumerate(value):
                self.assertShape(item, spec[0], f"{where}[{index}]")
        else:
            self.assertIsInstance(value, spec, where)


# --------------------------------------------------------------------------- #
# 1. the prose is untouched
# --------------------------------------------------------------------------- #

class TestHumanOutputIsByteIdentical(Scenario):
    """Captured before `--json` existed. Not regenerated from this code."""

    def test_the_seven_print_exactly_what_they_printed(self):
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                code, lines = self.human(argv)
                self.assertEqual((code, lines), GOLDEN[label])

    def test_the_golden_covers_every_command_that_gained_the_flag(self):
        self.assertEqual(set(GOLDEN), set(cli.JSON_COMMANDS))
        self.assertEqual(len(cli.JSON_COMMANDS), 7)


# --------------------------------------------------------------------------- #
# 2. the flag only changes rendering
# --------------------------------------------------------------------------- #

class TestTheFlagOnlyChangesRendering(Scenario):

    def test_stdout_is_one_document_and_the_prose_is_inside_it(self):
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                code, lines = self.human(argv)
                json_code, document, err = self.document(argv)
                self.assertEqual(err, "")
                self.assertEqual(json_code, code, "the flag changed the exit code")
                self.assertEqual(document["exit_code"], code)
                self.assertEqual(
                    rewritten(self.normalise("\n".join(document["lines"])).split("\n")),
                    rewritten(lines),
                    "the document's `lines` are not the prose the command "
                    "would have printed")

    def test_the_exit_code_survives_a_failing_command(self):
        """A non-zero exit is the part a pipeline actually reads."""
        self.assertEqual(
            self.cli("findings", "02-logout", "--plan", "auth", "--add",
                     "important", "--summary", "this one blocks")[0], 0)
        argv = ["findings", "02-logout", "--plan", "auth"]
        code, _lines = self.human(argv)
        json_code, document, _err = self.document(argv)
        self.assertEqual(code, 1)
        self.assertEqual(json_code, 1)
        self.assertEqual(document["exit_code"], 1)
        self.assertFalse(document["ok"])
        self.assertEqual(document["data"]["blocking"], 1)

    def test_a_command_without_the_flag_still_refuses_it(self):
        """`--json` is on the seven that answer in it, and nowhere else.

        Rejected by argparse, before anything runs — which is why this is a
        raised `SystemExit` rather than a return code.
        """
        for name in ("briefing", "save", "start"):
            with self.subTest(command=name):
                self.assertNotIn(name, cli.JSON_COMMANDS)
                with self.assertRaises(SystemExit) as raised:
                    self.cli_streams(name, "--json")
                self.assertEqual(raised.exception.code, 2)


# --------------------------------------------------------------------------- #
# 3. the shapes are a contract
# --------------------------------------------------------------------------- #

class TestTheSevenShapes(ShapeAssertions, Scenario):

    def test_the_envelope_is_the_same_for_all_seven(self):
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                _code, document, _err = self.document(argv)
                self.assertShape(document, ENVELOPE, "envelope")
                self.assertEqual(document["schema"], cli.JSON_SCHEMA)
                self.assertEqual(document["command"], label)

    def test_each_command_answers_in_its_pinned_shape(self):
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                _code, document, _err = self.document(argv)
                self.assertShape(document["data"], SHAPES[label], f"{label}.data")

    def test_the_shapes_carry_the_answer_and_not_only_the_prose(self):
        """Spot-checks: a consumer can read the state without the lines."""
        _c, status, _e = self.document(["status"])
        self.assertEqual(status["data"]["plan"], "auth")
        self.assertEqual(status["data"]["unit"], "02-logout")
        self.assertEqual([u["name"] for u in status["data"]["board"]["units"]],
                         ["01-login", "02-logout"])

        _c, verify_doc, _e = self.document(["verify"])
        self.assertEqual(verify_doc["data"]["verdict"], "pass")
        self.assertEqual([c["kind"] for c in verify_doc["data"]["checks"]], ["cmd"])

        _c, plan_doc, _e = self.document(["plan-check", "auth"])
        self.assertEqual(plan_doc["data"]["units"], 2)
        self.assertEqual(plan_doc["data"]["waves"][0]["wave"], 1)

        _c, findings_doc, _e = self.document(
            ["findings", "02-logout", "--plan", "auth"])
        self.assertEqual(findings_doc["data"]["mode"], "list")
        self.assertEqual(findings_doc["data"]["findings"][0]["severity"], "minor")

        _c, ci_doc, _e = self.document(["ci"])
        self.assertTrue(ci_doc["data"]["ok"])
        self.assertIn("layout complete", [c["name"] for c in ci_doc["data"]["checks"]])

        _c, doctor_doc, _e = self.document(["doctor"])
        self.assertEqual(doctor_doc["data"]["problems"], 0)
        self.assertIn("layout", {c["section"] for c in doctor_doc["data"]["checks"]})

        _c, next_doc, _e = self.document(["next"])
        self.assertTrue(next_doc["data"]["action"].startswith("/ctx:"))

    def test_the_document_is_pure_ascii(self):
        """A console that cannot spell `→` must not lose the document.

        `_echo` degrades a glyph to keep the command alive; a JSON document
        cannot be degraded, because half a character is not parseable. It does
        not have to be: `json.dumps` escapes non-ASCII, so the arrows and
        em-dashes in `lines` travel as `\\u2192` and arrive intact.
        """
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                _code, out, _err = self.cli_streams(*argv, "--json")
                out.encode("ascii")  # raises if anything needs a wider codec

    def test_the_documents_survive_a_round_trip(self):
        """Nothing in a shape is a type `json` had to be told how to render.

        `default=str` in `_finish` keeps an unexpected object from crashing the
        command, but a shape that relies on it is not a contract — it is a repr.
        """
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                _code, document, _err = self.document(argv)
                self.assertEqual(json.loads(json.dumps(document)), document)


# --------------------------------------------------------------------------- #
# 4. refusals and --strict compose
# --------------------------------------------------------------------------- #

class TestRefusalsCompose(Scenario):

    def test_a_refusal_is_a_whole_document_an_exit_2_and_a_stderr_line(self):
        for label, argv in COMMANDS:
            with self.subTest(command=label):
                code, out, err = self.cli_streams(
                    *argv, "--json", cwd=self.untracked)
                self.assertEqual(code, 2)
                self.assertIn("no .ctx/", err)
                document = json.loads(out)
                self.assertFalse(document["ok"])
                self.assertEqual(document["exit_code"], 2)
                self.assertIn("no .ctx/", document["error"])
                self.assertIsNone(document["data"])

    def test_strict_escalates_the_exit_code_and_still_prints_the_document(self):
        code, out, err = self.cli_streams(
            "findings", "--plan", "nothing-here", "--json", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("strict:", err)
        document = json.loads(out)
        self.assertEqual(document["exit_code"], 1)
        self.assertFalse(document["ok"])
        self.assertIsNone(document["error"])
        self.assertTrue(document["advisory"])

    def test_strict_without_the_flag_is_unchanged(self):
        """The escalation is the old one; `--json` did not move it."""
        plain = self.cli_streams("findings", "--plan", "nothing-here", "--strict")
        self.assertEqual(plain[0], 1)
        self.assertIn("strict:", plain[2])


class TestABareLedgerHasAdvisories(Fixture):
    """`ctx init` and nothing else: the paths that answer "there is none yet"."""

    def test_the_advisory_notice_is_in_the_document_not_on_stdout(self):
        code, out, err = self.cli_streams("plan-check", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        # No active plan. The command's answer *is* the notice, and the notice
        # is prose — so it lands in `lines`, `data` stays null, and the
        # condition is named in `advisory` where a script can branch on it.
        document = json.loads(out)
        self.assertIsNone(document["data"])
        self.assertEqual(document["advisory"], ["no-active-work"])
        self.assertIn("no active plan — /ctx:plan «slug» first", document["lines"])

    def test_the_same_run_under_strict_exits_1_with_the_same_document(self):
        relaxed = json.loads(self.cli_streams("plan-check", "--json")[1])
        code, out, err = self.cli_streams("plan-check", "--json", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("strict: no-active-work", err)
        strict = json.loads(out)
        self.assertEqual(strict["lines"], relaxed["lines"])
        self.assertEqual(strict["advisory"], relaxed["advisory"])
        self.assertEqual((relaxed["exit_code"], strict["exit_code"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
