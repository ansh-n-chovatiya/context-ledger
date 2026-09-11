"""Adding a verify kind is one dict entry, and the gate lives beside `run`.

Two maintainability findings against `ctx/verify.py`, both about the same
thing: knowledge of the gate scattered across places that have to agree.

**The kinds.** A kind was spelled out in four places — the `MECHANICAL` or
`JUDGED` tuple, the `COST` map, the if-ladder in `label_of` and the if-ladder
in `run` — and getting three of the four right failed *silently*, in whichever
direction you missed. Miss the kinds tuple and `ordered` drops the check, so a
gate with one check in it returns `pass` having executed nothing. Miss the
ladder in `run` and the check falls into the judged branch and reports
`pending` for ever. Both read like an answer. The positive control below runs
those two half-registrations against the current code and asserts they are now
refused *by name*, so the claim "this used to be four edits" is checked on
every run rather than remembered.

**The gate.** `_verify_plan` (what `ctx ci` runs) and `_gate_before_done` (what
stands between a unit and `done`) lived in `cli.py` and each rebuilt the same
`verify.run(...)` call: same `cwd`, same `since` out of the dispatch seal, same
`wave` out of `review.wave_scope`. Two copies of one call shape, in a module
that cannot be imported by the gate's other callers, is how `ctx ci` and the
done-gate come to disagree about what a unit's scope is — a disagreement that
would surface as a real violation being excused, and nowhere else.

Nothing in this file asserts the gate's *behaviour*. That is the point of a
move: the behaviour is already asserted by `test_gates.py`,
`test_gate_bypass.py`, `test_gate_window.py`, `test_ci_floor.py` and
`test_audit_ungated_gate.py`, and every one of them passes unedited.
"""

import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import cli, frontmatter, paths, plan as plan_mod, verify  # noqa: E402
from support import Fixture  # noqa: E402


REPO = Path(__file__).resolve().parent.parent


# The eight kinds as they were weighted before the table existed, copied out of
# the module by hand. Cheapest-first is the only reason the gate's
# short-circuit is worth anything — a scope violation must not pay for a test
# run — so these are an ordering, and a silent reorder changes which check
# runs first. Pinned by value, one by one.
COST_BEFORE = {
    "diff": 0,
    "exists": 1,
    "symbol": 2,
    "review": 3,
    "test_first": 3,
    "cmd": 4,
    "rubric": 5,
    "human": 6,
}
KINDS_BEFORE = ("diff", "exists", "symbol", "review", "test_first", "cmd",
                "rubric", "human")
MECHANICAL_BEFORE = ("diff", "exists", "symbol", "review", "test_first", "cmd")
JUDGED_BEFORE = ("rubric", "human")


class SyntheticKind(Fixture):
    """A fixture that can register one made-up kind and take it away again."""

    NAME = "synthetic"

    def register(self, cost=2, judged=False, status=None, label=None):
        """Add `synthetic` to the table. One statement, one dict entry."""
        calls = []

        def runner(check, ctx):
            calls.append((check, ctx))
            return verify.Result(self.NAME, verify.label_of(check),
                                 status or verify.PASS, "dispatched")

        verify.KIND_TABLE[self.NAME] = verify.Kind(
            cost, label or (lambda check: "a synthetic kind"), runner,
            judged=judged,
        )
        self.addCleanup(verify.KIND_TABLE.pop, self.NAME, None)
        return calls


# --------------------------------------------------------------------------- #
# (a) one dict entry is the whole registration
# --------------------------------------------------------------------------- #

class TestOneDictEntry(SyntheticKind):

    def test_a_new_kind_is_recognised_costed_and_dispatched(self):
        """Recognised, costed, dispatched — off one entry, with no other edit.

        The three things the four old edits were each responsible for, asserted
        together: `ordered` keeps the check (the kinds tuple's old job), `COST`
        knows its weight (the cost map's), `label_of` describes it (one
        ladder's) and `run` actually calls its runner (the other's).
        """
        calls = self.register(cost=2)
        check = {"kind": self.NAME}

        # recognised
        self.assertEqual(verify.ordered([check]), [check])
        self.assertIn(self.NAME, verify.KINDS)
        self.assertIn(self.NAME, verify.MECHANICAL)
        self.assertNotIn(self.NAME, verify.JUDGED)
        # costed
        self.assertEqual(verify.COST[self.NAME], 2)
        # labelled
        self.assertEqual(verify.label_of(check), "a synthetic kind")
        # dispatched
        results, verdict = verify.run(
            self.layout, self.config, [check], cwd=self.root, key="k")
        self.assertEqual([r.message for r in results], ["dispatched"])
        self.assertEqual([r.kind for r in results], [self.NAME])
        self.assertEqual(verdict, verify.PASS)
        self.assertEqual(len(calls), 1)

    def test_the_entry_is_the_only_edit_anywhere(self):
        """No other file in the package learns the kind's name.

        The registration above is one statement. This asserts the corollary the
        finding was really about: `synthetic` appears nowhere in `ctx/`, so
        nothing was quietly edited to make the test above pass.
        """
        self.register()
        offenders = [
            path.name for path in sorted((REPO / "ctx").glob("*.py"))
            if self.NAME in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_its_cost_decides_when_it_runs(self):
        """A weight is an ordering, so a new kind's weight has to order it."""
        self.register(cost=-1)
        ordered = verify.ordered([
            {"kind": "cmd", "run": "x"},
            {"kind": self.NAME},
            {"kind": "diff"},
        ])
        self.assertEqual([c["kind"] for c in ordered],
                         [self.NAME, "diff", "cmd"])

    def test_its_runner_is_handed_the_whole_gate_pass(self):
        """The context replaces what the if-ladder read from enclosing scope.

        A kind that could not see `layout`, `cwd`, `key`, `owns`, `since` or
        `wave` would force the next kind's author to change `run`'s call shape
        — which is the coupling this removes, not a smaller version of it.
        """
        calls = self.register()
        verify.run(
            self.layout, self.config, [{"kind": self.NAME}],
            cwd=self.root, key="plan/unit", owns=["src"], recorded=("rubric",),
            judged=True, since="abc123", wave=["other"],
        )
        (_check, ctx), = calls
        self.assertIs(ctx.layout, self.layout)
        self.assertEqual(str(ctx.cwd), str(self.root))
        self.assertEqual(ctx.key, "plan/unit")
        self.assertEqual(ctx.owns, ["src"])
        self.assertEqual(ctx.since, "abc123")
        self.assertEqual(ctx.wave, ["other"])
        self.assertEqual(ctx.recorded, ("rubric",))
        self.assertTrue(ctx.judged)

    def test_a_judged_kind_is_the_same_one_entry(self):
        """The one distinction between the families is a keyword argument."""
        self.register(judged=True)
        self.assertIn(self.NAME, verify.JUDGED)
        self.assertNotIn(self.NAME, verify.MECHANICAL)

    def test_a_new_kind_short_circuits_like_every_other(self):
        """Registration buys the gate's semantics, not just its dispatch."""
        self.register(cost=-1, status=verify.FAIL)
        results, verdict = verify.run(
            self.layout, self.config,
            [{"kind": self.NAME}, {"kind": "exists", "path": "nope"}],
            cwd=self.root, key="k",
        )
        self.assertEqual(verdict, verify.FAIL)
        self.assertEqual([r.kind for r in results], [self.NAME])


# --------------------------------------------------------------------------- #
# (b) positive control: the two half-registrations that used to be possible
# --------------------------------------------------------------------------- #

class TestTheOldFourEditsAreRefused(SyntheticKind):
    """What "it used to cost four edits" means, asserted rather than recalled.

    Both of these ran green against the code before this change, and both were
    wrong in a way nothing reported:

        verify.COST["synthetic"] = 2                  # edit 1 of 4
        verify.run(layout, config, [check], ...)
        -> ([], 'pass')            # dropped by `ordered`; green, ran nothing

        verify.KINDS = verify.KINDS + ("synthetic",)  # edit 2 of 4
        verify.run(layout, config, [check], ...)
        -> ([<synthetic ... pending>], 'pending')
           message: 'needs explicit sign-off: /ctx:verify --sign-off'
                                   # fell through to the judged branch, for ever

    Neither is reachable now. `COST` is a proxy over the table and `KINDS` is
    not assignable at all, so the two paths that used to *look* like registering
    a kind raise and say where the entry belongs.
    """

    def test_editing_the_cost_map_alone_is_refused(self):
        with self.assertRaises(TypeError):
            verify.COST[self.NAME] = 2
        self.assertNotIn(self.NAME, verify.KINDS)

    def test_editing_the_kinds_tuple_alone_is_refused(self):
        for name in ("KINDS", "MECHANICAL", "JUDGED", "COST"):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError) as caught:
                    setattr(verify, name, ("synthetic",))
                self.assertIn("KIND_TABLE", str(caught.exception))

    def test_an_unregistered_kind_is_still_dropped_loudly_enough(self):
        """The old silent `pass` is still the behaviour for a genuinely
        unknown kind — and must be, because a malformed `ctx.yaml` is not a
        reason to brick a session. What changed is that a *registered* kind can
        no longer land in this branch by accident."""
        results, verdict = verify.run(
            self.layout, self.config, [{"kind": self.NAME}],
            cwd=self.root, key="k")
        self.assertEqual(results, [])
        self.assertEqual(verdict, verify.PASS)


# --------------------------------------------------------------------------- #
# (c) the eight that were already there
# --------------------------------------------------------------------------- #

class TestTheEightKindsAreUnchanged(Fixture):

    def test_every_weight_is_what_it_was(self):
        """One by one, not as a dict comparison: a weight is a position in an
        ordering, and naming each one makes a reorder say which kind moved."""
        for kind, cost in COST_BEFORE.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, verify.KIND_TABLE)
                self.assertEqual(verify.COST[kind], cost)
        self.assertEqual(dict(verify.COST), COST_BEFORE)

    def test_the_derived_views_read_exactly_as_they_did(self):
        self.assertEqual(verify.KINDS, KINDS_BEFORE)
        self.assertEqual(verify.MECHANICAL, MECHANICAL_BEFORE)
        self.assertEqual(verify.JUDGED, JUDGED_BEFORE)

    def test_cheapest_first_still_orders_the_whole_set(self):
        checks = [{"kind": kind} for kind in reversed(KINDS_BEFORE)]
        got = [c["kind"] for c in verify.ordered(checks)]
        self.assertEqual([verify.COST[kind] for kind in got],
                         sorted(COST_BEFORE.values()))
        # `review` and `test_first` share weight 3, so their order is the
        # input's — the sort is stable, and a tie is not a ranking.
        self.assertEqual(got, ["diff", "exists", "symbol", "test_first",
                               "review", "cmd", "rubric", "human"])

    def test_every_kind_still_labels_itself_as_before(self):
        """The label ladder, kind by kind, including the two defaults."""
        cases = [
            ({"kind": "diff"}, "changed files within owned scope"),
            ({"kind": "exists", "path": "a.py"}, "a.py"),
            ({"kind": "exists"}, "<no path>"),
            ({"kind": "symbol", "path": "a.py", "contains": ["def f("]},
             "a.py still provides def f("),
            ({"kind": "review"},
             "no unaddressed critical or important review findings"),
            ({"kind": "test_first", "tests": ["tests/t.py"]},
             "a failing run of tests/t.py precedes the implementation"),
            ({"kind": "test_first"},
             "a failing run of <no tests> precedes the implementation"),
            ({"kind": "cmd", "run": "pytest -q"}, "pytest -q"),
            ({"kind": "cmd"}, "<no command>"),
            ({"kind": "rubric"}, "criteria judged against the diff"),
            ({"kind": "rubric", "about": "is it simple"}, "is it simple"),
            ({"kind": "human"}, "explicit sign-off"),
            ({"kind": "human", "about": "ship it"}, "ship it"),
            # Unknown kinds keep the old fallback: `label_of` is called on
            # checks that never reach `ordered`, and a label does not refuse.
            ({"kind": "nonsense"}, "explicit sign-off"),
            ({"kind": "nonsense", "about": "whatever"}, "whatever"),
            ({}, "explicit sign-off"),
        ]
        for check, expected in cases:
            with self.subTest(check=check):
                self.assertEqual(verify.label_of(check), expected)

    def test_every_kind_still_reaches_its_own_implementation(self):
        """Dispatch, kind by kind, by the message only that kind produces.

        A table whose entries all pointed at the same runner would pass every
        test above. These are the eight distinct verdicts.
        """
        cases = [
            ({"kind": "diff"}, "no owned scope declared"),
            ({"kind": "exists", "path": "missing.txt"}, "path does not exist"),
            ({"kind": "symbol", "path": "missing.py", "contains": ["x"]},
             "file does not exist"),
            ({"kind": "review"}, "`review` applies to a plan unit"),
            ({"kind": "test_first", "tests": ["tests/t.py"]},
             "no snapshot captured"),
            ({"kind": "cmd", "run": "some-command"},
             ""),  # trust refuses it: asserted by kind, not message, below
            ({"kind": "rubric"}, "run /ctx:verify to have a verifier judge this"),
            ({"kind": "human"},
             "needs explicit sign-off: /ctx:verify --sign-off"),
        ]
        for check, fragment in cases:
            with self.subTest(kind=check["kind"]):
                results, _verdict = verify.run(
                    self.layout, self.config, [check], cwd=self.root, key="k")
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].kind, check["kind"])
                self.assertIn(fragment, results[0].message)

    def test_an_untrusted_command_is_still_refused_by_the_cmd_entry(self):
        """The `cmd` branch carried the trust check and the spent-budget check
        inside the ladder; both moved into the entry's runner with it."""
        results, _ = verify.run(
            self.layout, self.config, [{"kind": "cmd", "run": "danger"}],
            cwd=self.root, key="k")
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertEqual(results[0].message, verify.trust.REASON)

    def test_a_spent_budget_is_still_reported_by_the_cmd_entry(self):
        check = {"kind": "cmd", "run": "anything"}
        self.trust([check])
        config = dict(self.config, gate={"timeout_seconds": 1})
        with mock.patch.object(verify.time, "monotonic",
                               side_effect=[0.0, 99.0, 99.0]):
            results, _ = verify.run(
                self.layout, config, [check], cwd=self.root, key="k")
        self.assertEqual(results[0].status, verify.ERROR)
        self.assertIn("budget was spent before this check ran",
                      results[0].message)


# --------------------------------------------------------------------------- #
# (d) the gate orchestration moved, and cannot have brought `cli` with it
# --------------------------------------------------------------------------- #

class TestTheGateLivesBesideRun(Fixture):

    def test_both_entry_points_are_in_verify_with_their_old_signatures(self):
        """A move, not a redesign — unit 05 registers these by signature."""
        self.assertEqual(str(inspect.signature(verify.verify_plan)),
                         "(layout, config, slug)")
        self.assertEqual(str(inspect.signature(verify.gate_before_done)),
                         "(layout, config, slug, unit)")
        self.assertEqual(str(inspect.signature(verify.gate_check)),
                         "(layout, config, slug, unit)")

    def test_cli_no_longer_carries_a_copy(self):
        for name in ("_verify_plan", "_gate_before_done", "_gate_check",
                     "_missing_commit_advice"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cli, name))

    def test_cli_calls_the_moved_done_gate(self):
        """`ctx unit --status done` reaches `verify.gate_before_done` itself,
        rather than a second copy that could drift from it."""
        slug, name = "auth", "01-api"
        directory = plan_mod.units_dir(self.layout, slug)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": name, "plan": slug, "tier": "subagent",
             "owns": ["src/a.py"], "budget_tokens": 1000, "status": "running",
             "wave": 1, "verify": [{"kind": "exists", "path": "src/a.py"}]},
            "## Objective\nx\n\n## Acceptance criteria\n1. it works\n",
        ).write(directory / f"{name}.md")

        with mock.patch.object(verify, "gate_before_done",
                               return_value=7) as gate:
            code, _out = self.cli("unit", name, "--status", "done",
                                  "--plan", slug)
        self.assertEqual(code, 7)
        self.assertEqual(gate.call_count, 1)

    def test_cli_calls_the_moved_plan_verifier(self):
        with mock.patch.object(verify, "verify_plan", return_value=5) as run:
            code, _out = self.cli("verify", "--plan", "auth")
        self.assertEqual(code, 5)
        self.assertEqual(run.call_count, 1)

    def test_verify_does_not_import_cli(self):
        """The direction of this dependency is why `plan`, `hooks` and
        `worktree` can use the gate at all. Asserted by importing the module on
        its own in a fresh interpreter, not by reading the source."""
        proof = subprocess.run(
            [sys.executable, "-c",
             "import sys; import ctx.verify; "
             "print('ctx.cli' in sys.modules)"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proof.returncode, 0, proof.stderr)
        self.assertEqual(proof.stdout.strip(), "False")

    def test_echo_has_one_implementation(self):
        """`cli._echo` is the same function, reached from the other side —
        the Windows code-page fallback is not written twice."""
        source = inspect.getsource(cli._echo)
        self.assertIn("verify.echo(*parts)", source)
        self.assertNotIn("UnicodeEncodeError", source)
        with mock.patch.object(verify, "echo") as echo:
            cli._echo("wave 1", "plain ascii")
        echo.assert_called_once_with("wave 1", "plain ascii")


# --------------------------------------------------------------------------- #
# (e) the last of the three ledger prefixes
# --------------------------------------------------------------------------- #

class TestLedgerPrefixComesFromPaths(unittest.TestCase):

    def test_verify_no_longer_keeps_its_own_copy(self):
        source = (REPO / "ctx" / "verify.py").read_text(encoding="utf-8")
        self.assertNotIn('LEDGER_PREFIX = ".ctx/"', source)
        self.assertIn("paths.LEDGER_PREFIX", source)

    def test_is_ledger_answers_from_the_shared_prefix(self):
        self.assertTrue(verify.is_ledger(paths.LEDGER_PREFIX + "journal/x.md"))
        self.assertTrue(verify.is_ledger(
            paths.LEDGER_PREFIX.replace("/", "\\") + "journal\\x.md"))
        self.assertFalse(verify.is_ledger("src/app.py"))
        self.assertFalse(verify.is_ledger("dotctx/app.py"))


if __name__ == "__main__":
    unittest.main()
