"""Round escalation on the findings ledger.

`config.tier_up` names the next dearer model tier for a failed round; this
module is where that fact gets written down, because the whole point of the
findings ledger is that state changes are files, not something a transcript
remembers until compaction. Two things make these tests worth having rather
than merely convenient:

The most likely way to under-deliver this unit is to make escalation "mostly"
free when it is turned off — no *visible* difference, some extra attribute
nobody reads, an extra blank line nobody would notice. `models.
escalate_on_failed_round` defaults to `false` precisely so a project that
never turns it on pays nothing for its existence, so the disabled-path tests
below compare actual file bytes against the actual 0.7.0 source.

That source is vendored, not fetched from git at test time. A first version
of this file ran `git show f7fa623:ctx/findings.py` with `check=True` to get
it — which works on a full clone but raises `CalledProcessError` (not a skip,
an error) on the shallow, one-commit checkout `actions/checkout@v4` produces
by default, which is exactly the checkout this repo's CI uses. The proof this
criterion exists for must never depend on history being present, so the
baseline that proof compares against is committed verbatim as a fixture —
`tests/fixtures/findings_pre_escalation_0_7_0.py` — and git is demoted to a
second, skippable test that only corroborates the fixture still matches
history when history happens to be there.

The other likely gap is letting a tier change grow into a state a `Finding`
can sit in. The module docstring names this directly: there is no
`acknowledged` status because agreeing with a finding is not the same as
closing it, and an escalation record must not become a second way to dodge
that — it is a fact about a round, recorded once, never a status.
"""

import copy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import config as config_mod, findings as findings_mod  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# The commit `tests/fixtures/findings_pre_escalation_0_7_0.py` was vendored
# from — used only by the corroboration test below to detect drift, never by
# the proof itself. If a future change touches findings.py again before this
# ref is retired, that is fine: it only has to name *a* commit whose
# findings.py matches the vendored fixture, and 0.7.0's does.
PRE_ESCALATION_REF = "f7fa623"

# The vendored 0.7.0 baseline. Its content must stay byte-identical to
# `git show f7fa623:ctx/findings.py` — that is what
# `VendoredFixtureMatchesGitTests` checks — but this file is read directly, so
# the byte-identical proof never shells out to git and never depends on
# history being present in the checkout running it.
FIXTURE_PATH = FIXTURES_DIR / "findings_pre_escalation_0_7_0.py"


def _exec_findings_module(source, filename, modname):
    """Execute a `ctx/findings.py`-shaped source string as its own module.

    `__package__` is set to `ctx` so its own `from . import config as
    config_mod, frontmatter` resolves against the real, already-imported
    package — there is nothing pre-escalation about those two modules, so
    reusing the live ones is exactly what running that file inside this repo
    would have done.
    """
    module = types.ModuleType(modname)
    module.__package__ = "ctx"
    module.__file__ = filename
    exec(compile(source, filename, "exec"), module.__dict__)
    return module


def _load_vendored_pre_escalation_findings():
    """The PROOF baseline for criterion 2: 0.7.0's `ctx/findings.py`, read
    from the vendored fixture rather than git, so the byte-identical
    assertion below runs the same way in a full clone and in a CI runner's
    shallow one."""
    source = FIXTURE_PATH.read_text(encoding="utf-8")
    return _exec_findings_module(source, str(FIXTURE_PATH), "_findings_pre_escalation_vendored")


def _layout(root):
    """The one attribute `findings.path_for` reads off a Layout."""
    return types.SimpleNamespace(plans=root / "plans")


def _config(escalate):
    data = copy.deepcopy(config_mod.DEFAULTS)
    data["models"]["escalate_on_failed_round"] = escalate
    return data


def _seed(module, root):
    """The same little review history against either module: one critical
    finding, one minor, a round bump, a dispute, another finding, a second
    round bump — enough to exercise headings, fields, fences and the
    round-stamped evidence append all at once."""
    ledger = module.load(_layout(root), "billing", "01-api")
    ledger.add("critical", "does not validate input", where="src/a.py:12",
                evidence="curl ... -> 500")
    ledger.add("minor", "typo in docstring")
    return ledger


class ByteIdenticalToPreEscalationTests(unittest.TestCase):
    """Criterion 2 — the PROOF. `assertEqual` on real file text, not "no
    visible difference": a disabled feature that changes so much as a
    trailing newline still cost something, and this is the test that would
    catch it. This must never skip: it compares against the vendored
    `tests/fixtures/findings_pre_escalation_0_7_0.py`, not git, precisely so
    it runs the same way in a shallow CI checkout as it does here.
    """

    def setUp(self):
        self.old = _load_vendored_pre_escalation_findings()

    def _run(self, module, root, bump_args):
        ledger = _seed(module, root)
        ledger.bump_round(*bump_args)
        ledger.set_status(1, "disputed", evidence="actually validated at src/a.py:40")
        ledger.add("important", "missing test", source="reviewer")
        ledger.bump_round(*bump_args)
        return ledger.path.read_text(encoding="utf-8")

    def test_caller_that_omits_config_and_model_matches_0_7_0_exactly(self):
        """The upgrade path every existing caller takes: unchanged call site,
        unchanged file."""
        with tempfile.TemporaryDirectory() as old_dir, \
             tempfile.TemporaryDirectory() as new_dir:
            old_text = self._run(self.old, Path(old_dir), ())
            new_text = self._run(findings_mod, Path(new_dir), ())
            self.assertEqual(new_text, old_text)

    def test_flag_off_matches_0_7_0_exactly_even_when_config_and_model_are_given(self):
        """Disabling must make the escalation code unreachable, not merely
        harmless — a caller upstream that already passes `config`/`model`
        (as `ctx review` will once it is wired up) must still produce the
        identical file a caller that never heard of escalation would, as
        long as `models.escalate_on_failed_round` is at its default."""
        config = _config(escalate=False)
        with tempfile.TemporaryDirectory() as old_dir, \
             tempfile.TemporaryDirectory() as new_dir:
            old_text = self._run(self.old, Path(old_dir), ())
            new_text = self._run(findings_mod, Path(new_dir), (config, "haiku"))
            self.assertEqual(new_text, old_text)


class VendoredFixtureMatchesGitTests(unittest.TestCase):
    """CORROBORATION, not the proof — the one place git is allowed to be
    absent. `ByteIdenticalToPreEscalationTests` above never reaches into git
    and must never skip; this test is what keeps the vendored fixture honest
    against real history, and it is the only test in this file allowed to
    skip, because a shallow, one-commit checkout (this repo's own CI
    checkout) genuinely cannot corroborate anything — there is no history
    there to check against, not a broken test.
    """

    def test_vendored_fixture_matches_git_history(self):
        result = subprocess.run(
            ["git", "show", f"{PRE_ESCALATION_REF}:ctx/findings.py"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.skipTest(
                f"git show {PRE_ESCALATION_REF}:ctx/findings.py failed (exit "
                f"{result.returncode}): {result.stderr.strip()!r} — most likely "
                "this checkout is shallow (a `git clone --depth 1`, which is what "
                f"actions/checkout@v4 produces by default) and does not have "
                f"{PRE_ESCALATION_REF} in it. The vendored fixture cannot be "
                "corroborated against history here; it can still be trusted, "
                "just not re-checked, until a full clone runs this test."
            )
        self.assertEqual(
            FIXTURE_PATH.read_text(encoding="utf-8"), result.stdout,
            "tests/fixtures/findings_pre_escalation_0_7_0.py has drifted from "
            f"the real 0.7.0 ctx/findings.py at {PRE_ESCALATION_REF} — "
            "re-vendor it with `git show "
            f"{PRE_ESCALATION_REF}:ctx/findings.py > "
            "tests/fixtures/findings_pre_escalation_0_7_0.py`",
        )


class EscalationRecordingTests(unittest.TestCase):
    """Criterion 1: a failed round with escalation enabled records the tier
    change and its reason, and it round-trips through `load`/`save`."""

    def test_failed_round_records_tier_and_reason(self):
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            before_round = ledger.round
            new_round = ledger.bump_round(config, "haiku")

            self.assertEqual(new_round, before_round + 1)
            self.assertEqual(len(ledger.escalations), 1)
            escalation = ledger.escalations[0]
            self.assertEqual(escalation.round, new_round)
            self.assertEqual(escalation.from_model, "haiku")
            self.assertEqual(escalation.to_model, "sonnet")
            self.assertTrue(escalation.reason, "escalation must name a reason")
            self.assertIn(str(new_round), escalation.line())
            self.assertIn("haiku", escalation.line())
            self.assertIn("sonnet", escalation.line())

    def test_custom_reason_is_recorded_verbatim(self):
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            ledger.bump_round(config, "sonnet", reason="round 1 argued the same point twice")
            self.assertEqual(
                ledger.escalations[0].reason, "round 1 argued the same point twice"
            )

    def test_escalation_survives_a_reload(self):
        """`ctx findings` reads the ledger back off disk — an escalation that
        only lived in memory would be invisible to it."""
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = _seed(findings_mod, root)
            ledger.bump_round(config, "haiku", reason="round 1 left a critical finding open")

            reloaded = findings_mod.load(_layout(root), "billing", "01-api")
            self.assertEqual(len(reloaded.escalations), 1)
            record = reloaded.escalations[0]
            self.assertEqual(record.round, 2)
            self.assertEqual(record.from_model, "haiku")
            self.assertEqual(record.to_model, "sonnet")
            self.assertEqual(record.reason, "round 1 left a critical finding open")
            # And the findings that were already there are untouched by having
            # an escalation alongside them.
            self.assertEqual(len(reloaded.findings), 2)
            self.assertEqual(reloaded.findings[0].severity, "critical")

    def test_disabled_flag_records_nothing_even_with_config_and_model(self):
        config = _config(escalate=False)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            ledger.bump_round(config, "haiku")
            self.assertEqual(ledger.escalations, [])

    def test_no_room_to_escalate_records_nothing(self):
        """`tier_up` returns the dearest tier unchanged rather than raising;
        this module must treat "no move" as nothing to record, not as an
        escalation to the same model."""
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            ledger.bump_round(config, "opus")  # already the dearest tier
            self.assertEqual(ledger.escalations, [])

    def test_model_outside_tiers_records_nothing(self):
        """A unit's own explicit `model:` is never auto-escalated —
        `tier_up` leaves it alone, and so must this."""
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            ledger.bump_round(config, "some-house-model")
            self.assertEqual(ledger.escalations, [])


class NotAStatusTests(unittest.TestCase):
    """Criterion 4, and the design rule behind it: escalation is a fact about
    a round, never a status a finding can sit in."""

    def test_statuses_unchanged(self):
        self.assertEqual(
            findings_mod.STATUSES, ("open", "addressed", "disputed", "parked")
        )
        self.assertNotIn("acknowledged", findings_mod.STATUSES)
        self.assertNotIn("escalated", findings_mod.STATUSES)

    def test_no_acknowledgement_refusal_still_rejects_the_usual_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            for spelling in ("ack", "noted", "agreed", "acknowledged"):
                ok, why = ledger.set_status(1, spelling)
                self.assertFalse(ok, f"{spelling!r} must be refused")
                self.assertIn("no acknowledged status", why)

    def test_escalation_has_no_status_field(self):
        escalation = findings_mod.Escalation(2, "haiku", "sonnet", "why")
        self.assertFalse(hasattr(escalation, "status"))

    def test_escalating_a_round_does_not_touch_finding_status(self):
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            statuses_before = [f.status for f in ledger.findings]
            ledger.bump_round(config, "haiku")
            statuses_after = [f.status for f in ledger.findings]
            self.assertEqual(statuses_before, statuses_after)
            self.assertEqual(statuses_before, ["open", "open"])


class MaxRoundsIsTheCeilingNotTheSeatTests(unittest.TestCase):
    """Criterion 3: escalation changes what a round costs, never how many
    rounds there are."""

    def test_max_rounds_still_three(self):
        self.assertEqual(findings_mod.MAX_ROUNDS, 3)

    def test_escalating_every_round_does_not_move_the_cap(self):
        config = _config(escalate=True)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _seed(findings_mod, Path(tmp))
            model = "haiku"
            for _ in range(findings_mod.MAX_ROUNDS + 2):
                ledger.bump_round(config, model)
                if ledger.escalations:
                    model = ledger.escalations[-1].to_model
            # Three tiers, so at most two escalations are possible (haiku ->
            # sonnet -> opus) regardless of how many rounds ran past the cap
            # this module itself never enforces — MAX_ROUNDS is the caller's
            # ceiling to apply, not something bump_round polices.
            self.assertLessEqual(len(ledger.escalations), 2)
            self.assertEqual(findings_mod.MAX_ROUNDS, 3)

    def test_disabled_by_default_config(self):
        self.assertIs(
            config_mod.DEFAULTS["models"]["escalate_on_failed_round"], False
        )


if __name__ == "__main__":
    unittest.main()
