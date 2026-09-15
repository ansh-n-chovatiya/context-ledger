# Preview Always Populated Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** By the time `ctx:start` would dispatch the first unit of a plan, `preview.html` already carries real content for every field it renders — never the generic placeholder — enforced by code-level gates, not skill-instruction convention.

**Architecture:** A five-tier fallback chain (authored in `plain.md` → resolved/inferred intake answer → the unit's own `Objective`/`Background` text → mechanical ownership/bottleneck facts already computed by `plan-check` → last-resort placeholder) replaces today's two-tier chain (authored → generic placeholder). `ctx spec.py` gains a small "inferred intake" record, additive to its existing questions file; `ctx spec-ready` and `ctx plan.check` become hard gates on it. `ctx/preview.py` — already the module that assembles data from `plan`, `verify` and `plain` for rendering — gains the new tiers; `ctx/plain.py`'s own contract (only a human writes `plain.md`) is untouched.

**Tech Stack:** Python 3.8+, stdlib `unittest`, no new dependencies. Existing modules only: `ctx/spec.py`, `ctx/plan.py`, `ctx/preview.py`, `ctx/preview_page.py`, `ctx/cli.py`, `ctx/commands.py`, `commands/spec.md`.

**Spec:** `docs/superpowers/specs/2026-09-14-preview-always-populated-design.md`

## Global Constraints

- L1 (`ctx:task`) is unaffected — no file in this plan touches `ctx/task.py` or L1's flow.
- `plain.md`'s contract is unchanged: human-authored text in it always wins over every other tier, and nothing here ever writes to `plain.md` on a user's behalf.
- An "inferred" answer must be traceable to real source text (the spec's own Intent, or the ticket text passed to `ctx spec`) — never invented from nothing. Every test that exercises an inferred answer asserts this by checking the inferred text shares real substance with its source, not just that a string exists.
- Generated/inferred text never invents a judgment plain.py doesn't already allow (`plain.py`'s own rule: "generated text states facts and never judges" — the new tiers add richer *facts*, never a "this is low risk" opinion).
- Both new gates (`spec-ready`, `plan.check`) must be code-level refusals with a non-zero exit code — matching the existing blocking-question refusal shape — not something documented only in a skill's markdown instructions.
- No CLI command in this plan takes more than 120s or spawns a subprocess; all of this is pure file/string manipulation.

---

## Task 1: Inferred intake records in `ctx/spec.py`

**Files:**
- Modify: `ctx/spec.py`
- Test: `tests/test_spec_intake.py` (new)

**Interfaces:**
- Consumes: `spec.questions_path`, `spec.RESOLVED`, `spec._append_to_section`, `frontmatter.read`/`Document.write` — all already defined in `ctx/spec.py`.
- Produces (for Task 2 and Task 4 to consume):
  - `INTAKE_CATEGORIES: tuple[str, ...]` = `("why now", "what could go wrong", "what changes for you")`
  - `record_inferred(layout, slug, category: str, answer: str, rationale: str) -> bool`
  - `intake_status(layout, slug) -> dict[str, bool]` — `{category: has_any_resolution}`
  - `intake_ready(layout, slug) -> tuple[bool, list[str]]` — `(all resolved, [missing categories])`

- [ ] **Step 1: Write the failing tests**

```python
"""ctx/spec.py: the inferred-intake record, additive to the questions file."""

import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx import spec as spec_mod
from ctx.paths import Layout  # existing layout constructor used across tests


def _layout(root):
    return Layout(Path(root) / ".ctx")


class TestRecordInferred(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = _layout(self._tmp.name)
        self.slug = "add-billing-export"
        spec_mod.create(self.layout, self.slug, intent="Let a customer export invoices.")

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_inferred_answer_lands_in_resolved(self):
        ok = spec_mod.record_inferred(
            self.layout, self.slug, "why now",
            "A customer asked for CSV export in the ticket.",
            "the ticket's own second paragraph says this directly",
        )
        self.assertTrue(ok)
        _blocking, _non, resolved = spec_mod.questions(self.layout, self.slug)
        self.assertEqual(len(resolved), 1)
        self.assertIn("Why now:", resolved[0])
        self.assertIn("customer asked for CSV export", resolved[0])
        self.assertIn("inferred, not asked", resolved[0])
        self.assertIn("ticket's own second paragraph", resolved[0])

    def test_rejects_an_unregistered_category(self):
        with self.assertRaises(ValueError):
            spec_mod.record_inferred(
                self.layout, self.slug, "not a real category", "x", "y")

    def test_never_touches_blocking_or_non_blocking(self):
        spec_mod.record_inferred(
            self.layout, self.slug, "risk", "n/a", "n/a")
        # "risk" is not a registered category either — this call is invalid
        # and must raise, not silently create a new section.


class TestIntakeStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = _layout(self._tmp.name)
        self.slug = "add-billing-export"
        spec_mod.create(self.layout, self.slug)

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_missing_when_nothing_recorded(self):
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertEqual(status, {c: False for c in spec_mod.INTAKE_CATEGORIES})

    def test_an_asked_and_answered_question_counts(self):
        spec_mod.add_questions(self.layout, self.slug,
                               ["Why now: is this urgent?"], blocking=True)
        spec_mod.resolve(self.layout, self.slug, "Why now",
                         "A customer is blocked without it.")
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertTrue(status["why now"])
        self.assertFalse(status["what could go wrong"])

    def test_an_inferred_answer_counts_the_same_as_an_asked_one(self):
        spec_mod.record_inferred(self.layout, self.slug, "risk placeholder", "x", "y")

    def test_ready_reports_exactly_the_missing_categories(self):
        spec_mod.record_inferred(self.layout, self.slug, "why now", "a", "b")
        ready, missing = spec_mod.intake_ready(self.layout, self.slug)
        self.assertFalse(ready)
        self.assertEqual(sorted(missing),
                         ["what changes for you", "what could go wrong"])

    def test_ready_is_true_once_all_three_are_recorded(self):
        for category in spec_mod.INTAKE_CATEGORIES:
            spec_mod.record_inferred(self.layout, self.slug, category, "a", "b")
        ready, missing = spec_mod.intake_ready(self.layout, self.slug)
        self.assertTrue(ready)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
```

Fix the deliberately-broken test above before running it: `test_never_touches_blocking_or_non_blocking` and `test_an_inferred_answer_counts_the_same_as_an_asked_one` call `record_inferred` with an invalid category ("risk", "risk placeholder") to double as an exception check — replace both bodies with:

```python
    def test_never_touches_blocking_or_non_blocking(self):
        spec_mod.record_inferred(
            self.layout, self.slug, "what could go wrong", "n/a", "n/a")
        blocking, non_blocking, _resolved = spec_mod.questions(self.layout, self.slug)
        self.assertEqual(blocking, [])
        self.assertEqual(non_blocking, [])
```

```python
    def test_an_inferred_answer_counts_the_same_as_an_asked_one(self):
        spec_mod.record_inferred(self.layout, self.slug, "risk", "x", "y")
```

wait — "risk" is not one of the three `INTAKE_CATEGORIES`, so that second one is still wrong. Use a real category:

```python
    def test_an_inferred_answer_counts_the_same_as_an_asked_one(self):
        spec_mod.record_inferred(self.layout, self.slug, "what could go wrong", "x", "y")
        status = spec_mod.intake_status(self.layout, self.slug)
        self.assertTrue(status["what could go wrong"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_spec_intake -v`
Expected: `AttributeError: module 'ctx.spec' has no attribute 'record_inferred'` (and similarly for `intake_status`/`intake_ready`/`INTAKE_CATEGORIES`).

- [ ] **Step 3: Implement `record_inferred`, `intake_status`, `intake_ready`**

Add to `ctx/spec.py`, near the other question-handling functions (after `resolve`, before `ready`):

```python
INTAKE_CATEGORIES = ("why now", "what could go wrong", "what changes for you")


def record_inferred(layout, slug, category, answer, rationale):
    """Append an inferred (never asked) intake answer straight into Resolved.

    Distinct from `resolve()`: there is no open question to tick off, because
    nobody was asked. The Resolved line still carries the category, the
    answer and *why nobody was asked* — the audit trail `resolve()` builds
    for a real question, extended to cover the case where the AI judged it
    confident enough to skip asking.
    """
    if category not in INTAKE_CATEGORIES:
        raise ValueError(
            f"{category!r} is not an intake category — use one of "
            f"{', '.join(INTAKE_CATEGORIES)}"
        )
    qpath = questions_path(layout, slug)
    doc = frontmatter.read(qpath)
    if doc is None:
        _, qpath = create(layout, slug)
        doc = frontmatter.read(qpath)
    label = category[:1].upper() + category[1:]
    line = (
        f"- {label}: {answer.strip()} — inferred, not asked "
        f"({rationale.strip()}) ({datetime.date.today().isoformat()})\n"
    )
    doc.body = _append_to_section(doc.body, RESOLVED, line)
    doc.write(qpath)
    return True


def intake_status(layout, slug):
    """`{category: bool}` — does any Resolved entry answer this category.

    A Resolved line counts whether it came from `resolve()` (a real question
    that named the category, e.g. "Why now: is this urgent?") or from
    `record_inferred()`. Matched on the same `"{Label}:"` prefix either way,
    so the two paths are indistinguishable to this check by design — the
    gate cares that the category was addressed, not how.
    """
    _blocking, _non, resolved = questions(layout, slug)
    out = {}
    for category in INTAKE_CATEGORIES:
        prefix = (category[:1].upper() + category[1:] + ":").lower()
        out[category] = any(item.lower().startswith(prefix) for item in resolved)
    return out


def intake_ready(layout, slug):
    """(all three intake categories addressed, [the ones that are not])."""
    status = intake_status(layout, slug)
    missing = [category for category in INTAKE_CATEGORIES if not status[category]]
    return (not missing), missing
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_spec_intake -v`
Expected: `OK` (9 tests).

- [ ] **Step 5: Run the full suite for regressions, then commit**

Run: `python3 -m unittest discover -s tests -q`
Expected: same one pre-existing, unrelated failure as before this plan started (`test_docs_currency.ArchivedDocumentTests.test_they_are_archived_and_not_deleted` — `report.md` at repo root), nothing new.

```bash
git add ctx/spec.py tests/test_spec_intake.py
git commit -m "Add inferred-intake records to the questions file

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `ctx infer` command, and hardening `ctx spec-ready`

**Files:**
- Modify: `ctx/commands.py` (add `cmd_infer`, extend `cmd_spec_ready`)
- Modify: `ctx/cli.py` (register the `infer` command)
- Test: `tests/test_infer_cli.py` (new)

**Interfaces:**
- Consumes: `spec.record_inferred`, `spec.intake_ready`, `spec.INTAKE_CATEGORIES` from Task 1; the existing `command()`/`flag()` CLI helpers and `_active_slug`/`_loaded`/`_echo`/`journal.append` helpers already used by `cmd_question`/`cmd_resolve`/`cmd_spec_ready`.
- Produces: `ctx infer <name> <category> <answer> --because "<rationale>"` as a CLI entry point; `cmd_spec_ready` now also fails on missing intake.

- [ ] **Step 1: Write the failing tests**

```python
"""`ctx infer` and the hardened `ctx spec-ready`."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Fixture

SLUG = "add-billing-export"


class TestInferCommand(Fixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("spec", SLUG)[0], 0)

    def test_infer_records_an_answer_without_a_prior_question(self):
        code, out = self.cli("infer", SLUG, "why now", "A customer asked for it.",
                             "--because", "the ticket says so directly")
        self.assertEqual(code, 0, out)
        _code, ask_out = self.cli("ask", SLUG)
        self.assertIn("resolved", ask_out.lower())

    def test_infer_refuses_an_unregistered_category(self):
        code, out = self.cli("infer", SLUG, "not-a-category", "x",
                             "--because", "y")
        self.assertNotEqual(code, 0)
        self.assertIn("not-a-category", out)


class TestSpecReadyRequiresIntake(Fixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("spec", SLUG)[0], 0)

    def test_refuses_with_no_intake_at_all(self):
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 1)
        self.assertIn("intake", out.lower())

    def test_refuses_naming_exactly_the_missing_categories(self):
        self.cli("infer", SLUG, "why now", "a", "--because", "b")
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 1)
        low = out.lower()
        self.assertIn("what could go wrong", low)
        self.assertIn("what changes for you", low)

    def test_ready_once_intake_and_blocking_questions_are_both_clear(self):
        for category in ("why now", "what could go wrong", "what changes for you"):
            self.cli("infer", SLUG, category, "a", "--because", "b")
        code, out = self.cli("spec-ready", SLUG)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_infer_cli -v`
Expected: the two `TestInferCommand` tests error out — `argparse` doesn't
recognize `infer` yet, so `build_parser().parse_args(...)` inside `cli_main`
calls `sys.exit(2)` on its own, before `main()`'s `try/except SystemExit`
block (which only wraps the command's own execution) can catch it — so this
surfaces as an uncaught `SystemExit: 2` from inside `self.cli(...)`, reported
by `unittest` as an ERROR on those two tests, not a clean assertion failure.
That's expected at this stage. The two `spec-ready` tests in
`TestSpecReadyRequiresIntake` fail normally (real `AssertionError`s): today's
`cmd_spec_ready` only checks blocking questions, so `test_refuses_with_no_intake_at_all`
gets exit 0 instead of the expected 1.

- [ ] **Step 3: Implement `cmd_infer` and harden `cmd_spec_ready`**

In `ctx/commands.py`, add after `cmd_resolve`:

```python
def cmd_infer(args):
    """Record an intake answer the AI was confident enough not to ask about."""
    layout, config = _loaded(args)
    slug = bundle.slugify(args.name)
    category = args.category.strip().lower()
    if category not in spec_mod.INTAKE_CATEGORIES:
        _echo(
            f"{category!r} is not an intake category — use one of "
            f"{', '.join(spec_mod.INTAKE_CATEGORIES)}"
        )
        return 1
    spec_mod.record_inferred(layout, slug, category, args.answer, args.because)
    journal.append(layout, config, "spec", slug, f"inferred: {category}")
    _echo(f"recorded an inferred answer for {category!r}")
    return 0
```

Replace `cmd_spec_ready` with:

```python
def cmd_spec_ready(args):
    """Gate 1 as an exit code, so CI can enforce it too."""
    layout, _config = _loaded(args)
    slug = _active_slug(layout, args.name, "spec")
    if not slug:
        # A gate, so this stays non-zero: "no spec" is not "spec is ready".
        _echo("no active spec — nothing to gate")
        return 1
    ready, blocking = spec_mod.ready(layout, slug)
    intake_ready, missing = spec_mod.intake_ready(layout, slug)
    if ready and intake_ready:
        _echo(f"spec {slug}: ready")
        return 0
    if not ready:
        _echo(f"spec {slug}: BLOCKED on {len(blocking)} question(s)")
        for item in blocking:
            _echo(f"  - {item}")
    if not intake_ready:
        _echo(f"spec {slug}: BLOCKED on intake — not yet answered or inferred: "
             + ", ".join(missing))
        _echo("  `ctx infer <name> <category> <answer> --because <why>` records "
             "a confident guess without asking; `ctx question`/`ctx resolve` "
             "ask and answer instead.")
    return 1
```

In `ctx/cli.py`, add the `infer` command next to `question`/`resolve` (inside the same command-table list):

```python
        command(
            "infer", "record a confident intake answer without asking", cmd_infer,
            NAME,
            flag("category"),
            flag("answer"),
            flag("--because", required=True,
                help="why this was confident enough not to ask"),
        ),
```

and add `cmd_infer` to the `from .commands import (...)` block at the top of `ctx/cli.py` alongside `cmd_question`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_infer_cli -v`
Expected: `OK` (5 tests).

- [ ] **Step 5: Run the full suite for regressions, then commit**

Run: `python3 -m unittest discover -s tests -q`
Expected: only the one pre-existing `report.md`-at-root failure.

```bash
git add ctx/commands.py ctx/cli.py tests/test_infer_cli.py
git commit -m "Add ctx infer, and refuse ctx spec-ready without intake

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Require a real `Objective` per unit

**Files:**
- Modify: `ctx/plan.py`
- Test: `tests/test_plan_fields.py` (extend the existing file — it already covers required-field validation, per its use in the ownership-gap report on this session's own plan)

**Interfaces:**
- Consumes: `plan.validate(units)`, `unit.doc.section("objective")` — both already exist.
- Produces: `validate()` now rejects a unit whose `## Objective` body is empty, with the same message shape as the existing `owns`-empty problem.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plan_fields.py`:

```python
class TestObjectiveIsRequired(unittest.TestCase):
    """A unit whose page has to describe it needs a real Objective to read."""

    def unit_with_objective(self, text):
        directory = plan_mod.units_dir(self.layout, self.slug)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "01-a.md"
        path.write_text(
            "---\nunit: 01-a\ntier: subagent\nowns:\n  - src/a.py\n"
            "verify:\n  - kind: cmd\n    run: \"true\"\n---\n\n"
            f"## Objective\n{text}\n", encoding="utf-8"
        )
        return plan_mod.load_units(self.layout, self.slug)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = Layout(Path(self._tmp.name))
        self.slug = "objective-check"

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_empty_objective_is_refused(self):
        units = self.unit_with_objective("")
        problems = plan_mod.validate(units)
        self.assertTrue(any("Objective" in problem for problem in problems), problems)

    def test_a_real_objective_passes(self):
        units = self.unit_with_objective("Stop the probe from running a shim.")
        problems = plan_mod.validate(units)
        self.assertFalse(any("Objective" in problem for problem in problems), problems)
```

Add the matching imports at the top of the file if not already present: `import tempfile`, `from pathlib import Path`, `from ctx.paths import Layout`, `from ctx import plan as plan_mod`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_plan_fields.TestObjectiveIsRequired -v`
Expected: `test_an_empty_objective_is_refused` fails — `validate()` currently returns no problem for an empty Objective.

- [ ] **Step 3: Implement the check**

In `ctx/plan.py`, inside `validate(units)`, immediately after the existing `owns`-empty block (the `for field in REQUIRED:` loop), add:

```python
        objective = (unit.doc.section("objective") or "").strip()
        if not objective:
            problems.append(
                f"{unit.name}: `## Objective` is empty — every field on the "
                "preview page that describes this step falls back to it; "
                "write one real sentence naming the observable outcome"
            )
```

This is deliberately not folded into the `REQUIRED` tuple/loop above it: every other entry in `REQUIRED` is a frontmatter *meta* field, read via `unit.doc.meta.get(field)`; `objective` lives in the document *body* under a heading, read via `unit.doc.section(...)`, so it needs its own check rather than silently returning `""` from a `.meta.get()` call that will never find it there.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_plan_fields.TestObjectiveIsRequired -v`
Expected: `OK` (2 tests).

- [ ] **Step 5: Run the full suite for regressions, then commit**

Run: `python3 -m unittest discover -s tests -q`

This WILL surface real breakage beyond the one known pre-existing failure: `ctx/plan.py`'s own `scaffold_unit` already always writes a placeholder objective (`"<one sentence: the observable outcome>"`), and any existing test fixture that scaffolds a unit without immediately overwriting that placeholder will now fail validation for the first time. Find every such fixture with:

```bash
grep -rln '<one sentence: the observable outcome>' tests/*.py
```

For each hit, either give the fixture a one-line real objective (preferred — matches what a real unit needs anyway) or, if the test is deliberately exercising a different validation failure and an objective would be a distraction, add `objective="placeholder objective for this test"` to its `scaffold_unit(...)` call. Do not weaken the new check to accommodate a fixture — fix the fixture.

```bash
git add ctx/plan.py tests/test_plan_fields.py
git commit -m "Require a real Objective for every unit

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Tiers 3 and 4 in `ctx/preview.py` — real per-unit text and real risk facts

**Files:**
- Modify: `ctx/preview.py`
- Test: `tests/test_preview_model.py` (extend)

**Interfaces:**
- Consumes: `plain.unit(name, facts)` (returns `generated: {field: bool}`, unchanged from Task-less baseline — see Task 1's design note: this task does **not** modify `ctx/plain.py`), `unit.doc.section("objective")`/`section("background")`, the already-computed `ownership_gaps`/`bottlenecks` local variables inside `view_model()`.
- Produces: `_step(...)`'s returned dict gains `plain["provenance"]: {field: "authored"|"inferred"|"generated"}`, additive alongside the existing `plain["generated"]: {field: bool}` (which keeps its current meaning — True whenever not authored — so nothing that already reads `generated` breaks). `view_model()`'s plan-level `plain` dict gains the same treatment for the three intake-backed sections.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_preview_model.py`:

This file already imports `from support import OK, Fixture` (check the top
of `tests/test_preview_model.py` — if it doesn't yet, add it, matching
`tests/test_preview_page.py`'s import exactly). Subclass `Fixture`, the same
base every other preview test file uses, rather than constructing a `Layout`
by hand — `Fixture.setUp()` already runs `ctx init` and sets `self.layout`.

```python
class TestTierThreeFromObjective(Fixture):
    """A unit's own Objective/Background fills 'what it does'/'why it matters'
    when plain.md has nothing, instead of the generic step-position sentence."""

    SLUG = "tier-three"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/a.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nStop the probe from executing a repo-shipped binary.\n\n"
            "## Background\nA cloned repo could ship a fake interpreter and have "
            "it run before the trust prompt ever shows.\n",
        ).write(directory / "01-a.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_what_it_does_comes_from_the_objective_not_the_position_sentence(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertIn("execut", step["plain"]["what"].lower())
        self.assertEqual(step["plain"]["provenance"]["what"], "inferred")

    def test_why_it_matters_comes_from_the_background(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertIn("trust prompt", step["plain"]["why"])
        self.assertEqual(step["plain"]["provenance"]["why"], "inferred")

    def test_generated_flag_still_true_for_backward_compatibility(self):
        vm = preview.view_model(self.layout, self.SLUG)
        step = vm["steps"][0]
        self.assertTrue(step["plain"]["generated"]["what"])


class TestTierFourFromOwnershipGaps(Fixture):
    """A unit's risk field, when unauthored, states the real ownership-gap
    fact for its own owned paths rather than 'Nobody has written this down
    yet.'"""

    SLUG = "tier-four"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        self.assertEqual(self.cli("plan", self.SLUG, "--no-spec")[0], 0)
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        # `ctx/hooks.py` is a real path in *this* repository, owned by no
        # unit and named by real tests — see the `ownership_gaps` list
        # printed on this session's own plan preview. Using a real path from
        # the repo under test (rather than a synthetic one) is deliberate:
        # `plan_mod.ownership_gaps` walks the actual `tests/` directory on
        # disk to find references, so a made-up path would never match.
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-a", "plan": self.SLUG,
             "tier": "subagent", "owns": ["ctx/hooks.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nDoes something.\n",
        ).write(directory / "01-a.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_risk_names_a_real_uncovered_test_file_when_one_exists(self):
        vm = preview.view_model(self.layout, self.SLUG)
        gaps = vm.get("ownership_gaps", {}).get("files", [])
        step = vm["steps"][0]
        if any(gap["path"] == "ctx/hooks.py" for gap in gaps):
            self.assertNotEqual(step["plain"]["risk"],
                               "Nobody has written this down yet.")
            self.assertEqual(step["plain"]["provenance"]["risk"], "inferred")
        else:
            self.skipTest("this checkout's own test suite no longer "
                          "references ctx/hooks.py outside its own unit — "
                          "pick another real gap path from a fresh "
                          "`ctx plan-check`'s ownership_gaps output")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_preview_model.TestTierThreeFromObjective tests.test_preview_model.TestTierFourFromOwnershipGaps -v`
Expected: `KeyError: 'provenance'` and the "what it does" assertions fail because today's fallback is the generic position sentence.

- [ ] **Step 3: Implement tiers 3 and 4**

In `ctx/preview.py`, add two small helpers above `_step`:

```python
def _plain_from_objective(unit):
    """(what_it_does, why_it_matters) drawn from the unit's own prose, or
    (None, None) when it has none to give — never invented."""
    objective = (unit.doc.section("objective") or "").strip()
    background = (unit.doc.section("background") or "").strip()
    what = objective or None
    why = background or None
    return what, why


def _plain_from_ownership_gap(unit, gap_paths):
    """A plain sentence naming an owned path this unit's own tests do not
    cover, or None when nothing is known. `gap_paths` is `ownership_gaps`'s
    own `files` list — `[{path, tests}, ...]` — already computed by the
    caller; this never recomputes it."""
    owned = set(unit.owns)
    hits = [gap for gap in gap_paths if gap["path"] in owned]
    if not hits:
        return None
    names = ", ".join(sorted(gap["path"] for gap in hits))
    return (
        f"Tests outside this plan already reference {names}; a change here "
        "could be caught by one of those instead of by this plan's own checks."
    )
```

Change `_step`'s signature to accept the ownership-gap list, and thread the new tiers through:

```python
def _step(unit, numbers, rounds, grouped, source, patterns, gap_files):
    """One step: what a reader needs, then what an engineer needs, separately."""
    level = rounds[unit.name]
    number = numbers[unit.name]
    alongside = sorted(numbers[other.name] for other in grouped[level]
                       if other.name != unit.name)
    waits_for = sorted(numbers[name] for name in unit.depends_on
                       if name in numbers)

    written = source.unit(unit.name, {
        "number": number,
        "of": len(numbers),
        "files": len(unit.owns),
        "waits_for": waits_for,
        "alongside": alongside,
        "checks": _check_phrases(unit.checks),
    })

    inferred_what, inferred_why = _plain_from_objective(unit)
    inferred_risk = _plain_from_ownership_gap(unit, gap_files)
    overrides = {"what it does": inferred_what, "why it matters": inferred_why,
                "risk": inferred_risk}

    prose, provenance = {}, {}
    for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS):
        was_generated = written["generated"][field]
        override = overrides.get(field)
        if not was_generated:
            provenance[key] = "authored"
            text = written[field]
        elif override:
            provenance[key] = "inferred"
            text = override
        else:
            provenance[key] = "generated"
            text = written[field]
        prose[key] = preview_html.markup(text, patterns)
    prose["generated"] = dict(
        (key, bool(written["generated"][field]))
        for field, key in zip(plain_mod.UNIT_FIELDS, FIELD_KEYS)
    )
    prose["provenance"] = provenance
    # ... title/derived and the returned dict are unchanged from here down;
    # only the `"plain": prose,` line's `prose` now carries `provenance` too.
```

Update `_step`'s one call site inside `view_model()` to pass `gap_files` (the same list `view_model()` already builds for the `ownership_gaps` key — pass it before it's serialised into the JSON shape, not after).

For the plan-level intake tiers, in `view_model()`, after `source = plain_mod.load(layout, slug)` (the existing `Plain` instance) and after reading the plan's own `spec:` slug from `plan.json`'s metadata (already read elsewhere in this function for the `"plan"` key of the returned dict), add:

Add `spec as spec_mod` to the existing top-level `from . import (...)` block
at `ctx/preview.py:68` (do not add a new function-level import — `ctx/spec.py`
imports only `config` and `frontmatter`, so `preview → spec` is acyclic at
module level and does not need to go through the deferred-import allowlist
in `tests/test_no_import_cycles.py` the way `worktree → commands` did):

```python
    spec_slug = str(graph.get("spec") or slug)  # the same expression already
                                                 # used a few lines below for
                                                 # the returned "plan"."spec" key
    _blocking, _non, resolved = spec_mod.questions(layout, spec_slug)
    plan_provenance = {}
    for section in plain_mod.PLAN_SECTIONS:
        authored = source.plan.get(section, "")
        if authored:
            plan_provenance[section] = "authored"
            continue
        category_text = None
        if section in intake_map:
            prefix = (section[:1].upper() + section[1:] + ":").lower()
            match = next((item for item in resolved if item.lower().startswith(prefix)), None)
            if match:
                category_text = match.split(":", 1)[1].split("—")[0].strip()
        if category_text:
            source.plan[section] = category_text
            plan_provenance[section] = "inferred"
        else:
            plan_provenance[section] = "generated"
```

(`spec_slug` is whatever local variable `view_model()` already uses to read the plan's originating spec — check the existing `_plan_title`/plan-metadata read near the top of the function for its exact name and reuse it; do not introduce a second way to find it.) Thread `plan_provenance` into the returned `"plain"` dict alongside the existing `"sections"` key.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_preview_model -v`
Expected: `OK`.

- [ ] **Step 5: Run the full suite for regressions, then commit**

Run: `python3 -m unittest discover -s tests -q`
Expected: only the one pre-existing `report.md` failure. Pay particular attention to `tests/test_docs_currency.py` (it counts fields in the view-model schema) and `tests/test_preview_cli.py` — if either asserts an exact key set for `plain`/`generated`, add `provenance` to its expected set rather than loosening the assertion.

```bash
git add ctx/preview.py tests/test_preview_model.py
git commit -m "Fill unauthored step fields from the unit's own text and real ownership gaps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Render provenance labels in `preview_page.py`

**Files:**
- Modify: `ctx/preview_page.py`
- Test: `tests/test_preview_page.py` (confirmed to exist — add the new test class to it, following its existing `PageCase(Fixture)` base class: build a real minimal plan and call through `view_model()`/`render()` the way `TestWhatItSays` already does, rather than hand-constructing a step dict, so the fixture stays consistent with every other test in the file)

**Interfaces:**
- Consumes: `step["plain"]["provenance"][key]` from Task 4.
- Produces: three distinct labels in the rendered page instead of one.

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_preview_page.py`, using the file's own `PageCase`
fixture (which already gives every unit `## Objective\nDo the thing.\n` via
its `BODY` constant, and no `plain.md` — see `PageCase.setUp`):

```python
class TestProvenanceLabels(PageCase):
    def test_an_objective_backed_field_is_labelled_inferred(self):
        html = self.page()
        self.assertIn("inferred from the plan, not directly confirmed", html)

    def test_a_field_with_no_source_at_all_is_still_labelled_generated(self):
        # No unit in PageCase has a `## Background`, so "why it matters" has
        # nothing to infer from and must still fall back to the old label.
        html = self.page()
        self.assertIn("put together automatically", html)

    def test_an_authored_field_carries_neither_label(self):
        self.write_plain(
            "## Unit: 01-alpha — Alpha\n**What it does:** Written by a human.\n"
        )
        html = self.page()
        self.assertIn("Written by a human.", html)
```

(`FIELD_KEYS = ("what", "why", "changes", "risk", "how_we_know")` at
`ctx/preview.py:83` — use `"how_we_know"`, not `"how"`, if you address any
field by its key directly rather than through the rendered HTML as above.)

(`plan_mod`/`preview`/`preview_page`/`PageCase` are already imported at the
top of `tests/test_preview_page.py` — no new imports needed.)

- [ ] **Step 2: Run the test to verify it fails**

Run the located test module. Expected: `AssertionError` — today's `_step_card` only ever emits "put together automatically" or nothing, never "inferred from the plan".

- [ ] **Step 3: Implement the three-way label**

In `ctx/preview_page.py`, replace the two-line `mark` computation inside `_step_card` (currently `mark = ('<span class="auto">put together automatically</span>' if generated.get(key) else "")`) with:

```python
    provenance = plain.get("provenance") or {}
    LABELS = {
        "inferred": '<span class="auto">inferred from the plan, not directly confirmed</span>',
        "generated": '<span class="auto">put together automatically</span>',
    }
    ...
    for key, label in FIELDS:
        mark = LABELS.get(provenance.get(key), "")
```

Keep the `generated.get(key)` fallback for any caller that hasn't been updated to pass `provenance` yet (defensive, not load-bearing): if `provenance` is empty but `generated.get(key)` is true, fall back to the old "generated" label so a page built by a not-yet-updated code path still renders sensibly rather than blank.

**Also fix `check()` in the same file** (`ctx/preview_page.py:970`) — it walks
`for key in sorted(plain): if key == "generated": continue` and then does
`value not in html` on whatever's left, which is exactly why `"generated"`
was skipped in the first place: its value is a `bool`/dict-of-bools, not
prose to search for. `"provenance"` (added in Task 4) is the same shape —
a dict, not a string — and without being skipped too, `check()` crashes with
`TypeError: 'in <string>' requires string as left operand, not dict` the
first time it runs against any page this plan produces. Change the skip to:

```python
        for key in sorted(plain):
            if key in ("generated", "provenance"):
                continue
```

Add a test in the same `TestTheChecker` class `tests/test_preview_page.py`
already has for `check()`:

```python
    def test_check_does_not_crash_on_the_provenance_dict(self):
        vm = self.model()
        vm["steps"][0]["plain"]["provenance"] = {"what": "inferred"}
        problems = preview_page.check(self.page(vm), vm)  # must not raise
        self.assertIsInstance(problems, list)
```

- [ ] **Step 4: Run the test to verify it passes**

Run the same test module. Expected: `OK`.

- [ ] **Step 5: Run the full suite for regressions, then commit**

Run: `python3 -m unittest discover -s tests -q`
Expected: only the one pre-existing `report.md` failure.

```bash
git add ctx/preview_page.py <the test file you edited>
git commit -m "Label inferred content distinctly from mechanically-generated text

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Update `commands/spec.md` to actually run the intake interview

**Files:**
- Modify: `commands/spec.md`

**Interfaces:** None — this is instructions read by an assistant, not code with a signature. The "interface" is behavioral: after this change, running `/ctx:spec` must, before returning control, have either asked or inferred all three `spec.INTAKE_CATEGORIES`.

- [ ] **Step 1: Add the intake step to the skill's instructions**

Insert into `commands/spec.md`, after the existing step that says "Write down every question whose answer would change what you build" and before "Ask them":

```markdown
3a. **Before asking, run the same judgment over three fixed categories this
    system checks for**: *why now*, *what could go wrong*, and *what changes
    for you*. For each, draft a candidate answer from the intent/ticket you
    were given.
    - If you're confident the draft is right, record it without asking:
      `ctx infer <slug> "<category>" "<answer>" --because "<one line: what in
      the ticket/intent makes you confident>"`.
    - If you're not confident, add it as a real question (blocking, since
      `ctx spec-ready` will not proceed without it) and offer your draft as
      the recommended option when you ask.
    Do this for all three every time — `ctx spec-ready` refuses to let
    planning start while any of them is unaddressed, the same way it refuses
    on an open blocking question.
```

- [ ] **Step 2: Manually verify the instruction reads correctly in context**

Run: `cat commands/spec.md` and read the full file top to bottom — confirm the new step doesn't contradict the existing numbered flow (renumber surrounding steps if the insertion shifts them) and that it reads as an instruction to *you*, matching the voice of the rest of the file.

- [ ] **Step 3: Commit**

```bash
git add commands/spec.md
git commit -m "Have /ctx:spec run the intake interview before asking anything else

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6a: Wire the intake gate into `ctx plan` itself

**Added after execution started** — a controller-discovered defect in this
plan's own decomposition, not an implementer error. Its own docstring says
"Gate 1 is enforced here: an ambiguous spec cannot be planned," but
`cmd_plan` (`ctx/commands.py`) only ever checked `spec_mod.ready(...)`
(blocking questions). Task 2 hardened the *standalone* `ctx spec-ready`
command to also check `spec_mod.intake_ready(...)`, but nothing ever added
the same check to `cmd_plan` itself — the actual gate `/ctx:plan` runs
through. Without this fix, Task 7's own end-to-end test (which asserts `ctx
plan` refuses a spec with no recorded intake) fails against real code.

**Files:**
- Modify: `ctx/commands.py` (`cmd_plan`)
- Modify: any shared test fixture found to need it (start with
  `tests/test_plan.py`'s `PlanFixture.ready_spec()` — fix the shared helper
  once rather than each call site)
- Test: `tests/test_plan.py` (extend `TestGateOnePlansOnlyReadySpecs`)

**Interfaces:**
- Consumes: `spec.intake_ready(layout, slug)` (Task 1), the existing
  `spec_mod.ready(...)` call already in `cmd_plan`.
- Produces: `cmd_plan` refuses (exit 1) when intake is incomplete, naming
  the missing categories, the same message shape `cmd_spec_ready` already
  uses.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_plan.py`'s `TestGateOnePlansOnlyReadySpecs`:

```python
    def test_planning_is_refused_while_intake_is_unaddressed(self):
        self.cli("spec", self.slug, "--intent", "Rotate keys without downtime.")
        code, out = self.cli("plan", self.slug)
        self.assertEqual(code, 1)
        self.assertIn("intake", out.lower())
        self.assertFalse(plan_mod.readme_path(self.layout, self.slug).exists())

    def test_planning_proceeds_once_intake_is_addressed(self):
        self.ready_spec()
        code, _out = self.cli("plan", self.slug)
        self.assertEqual(code, 0)
```

The second test only passes once `ready_spec()` itself is fixed (Step 3) —
until then it fails the same new way `test_planning_is_refused_while_intake_is_unaddressed`
does, which is expected at this stage.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_plan.TestGateOnePlansOnlyReadySpecs -v`
Expected: both new tests fail — `cmd_plan` doesn't check intake yet, so the
first gets exit 0 instead of 1, and the second's `ready_spec()` doesn't
record intake yet either (though it would currently "pass" for the wrong
reason — no gate exists yet to fail. Add it after Step 3 confirms the gate
exists, or accept it as a trivial pass-for-now here and treat Step 4 as the
real GREEN check for it).

- [ ] **Step 3: Implement the check, and fix `ready_spec()`**

In `ctx/commands.py`'s `cmd_plan`, immediately after the existing
`ready, blocking = spec_mod.ready(...)` block (inside the
`if spec_mod.spec_path(...).is_file():` branch), add:

```python
        intake_ready, missing = spec_mod.intake_ready(layout, spec_slug)
        if not intake_ready:
            _echo(f"refusing to plan: spec {spec_slug} has unaddressed intake: "
                 + ", ".join(missing))
            _echo("  `ctx infer <name> <category> <answer> --because <why>` "
                 "records a confident guess; `ctx question`/`ctx resolve` ask "
                 "and answer instead.")
            return 1
```

In `tests/test_plan.py`'s `PlanFixture.ready_spec()`, after the `self.cli("spec", ...)`
call, add the three `ctx infer` calls:

```python
    def ready_spec(self):
        self.cli("spec", self.slug, "--intent", "Rotate keys without downtime.")
        for category in ("why now", "what could go wrong", "what changes for you"):
            self.cli("infer", self.slug, category, "a", "--because", "b")
        return self.slug
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_plan -v`
Expected: `OK`.

- [ ] **Step 5: Sweep the rest of the suite for the same ripple**

Run the full suite. For every other file that creates a spec via
`self.cli("spec", ...)` and then plans it without `--no-spec`
(`tests/test_commands.py`, `tests/test_gates.py`, `tests/test_hardening.py`,
`tests/test_wave_cap.py`, `tests/test_reported_spend.py`,
`tests/test_audit_wave4.py`, `tests/test_advice.py`, `tests/test_infer_cli.py`
— not all of these necessarily plan afterward; check each), find whether it
has its own "ready spec" helper or inlines the two calls, and apply the same
fix once per shared helper. Do not weaken the new check to avoid fixing a
fixture.

Run: `python3 -m unittest discover -s tests -q`
Expected: back down to exactly the one pre-existing, unrelated `report.md`
failure.

```bash
git add ctx/commands.py tests/test_plan.py <any other fixture files touched>
git commit -m "Refuse ctx plan while intake is unaddressed, the same way it refuses on an open blocking question

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: End-to-end regression — the actual proof of the goal

**Files:**
- Test: `tests/test_preview_never_placeholder.py` (new)

**Interfaces:** None new — this composes every interface from Tasks 1-5.

- [ ] **Step 1: Write the failing test**

```python
"""The literal claim this whole plan exists to make true."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import frontmatter, plan as plan_mod, preview, preview_page, spec as spec_mod
from support import OK, Fixture

PLACEHOLDER = "Nobody has written this down yet."


class TestPreviewNeverPlaceholder(Fixture):
    SLUG = "fix-the-export-bug"

    def setUp(self):
        super().setUp()
        self.trust([{"kind": "cmd", "run": OK}])
        spec_mod.create(
            self.layout, self.SLUG,
            intent="Customers hit a 500 exporting more than 10k rows; cap the "
                   "export at 10k with a clear message instead."
        )
        for category in spec_mod.INTAKE_CATEGORIES:
            spec_mod.record_inferred(
                self.layout, self.SLUG, category,
                f"Answer for {category}, drawn from the intent above.",
                "the intent describes this directly",
            )
        self.assertEqual(self.cli("plan", self.SLUG)[0], 0)  # no --no-spec:
                                                              # this must link
                                                              # to the real spec
        directory = plan_mod.units_dir(self.layout, self.SLUG)
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter.Document(
            {"ctx_schema": 1, "unit": "01-cap-export", "plan": self.SLUG,
             "tier": "subagent", "owns": ["src/export.py"], "depends_on": [],
             "reads": [], "forbid": [], "status": "pending",
             "verify": [{"kind": "cmd", "run": OK}]},
            "## Objective\nCap CSV export at 10,000 rows and return a clear "
            "error above that instead of a 500.\n\n## Background\nThe "
            "unbounded query times out past 10k rows and the client sees a "
            "bare 500 with no explanation.\n",
        ).write(directory / "01-cap-export.md")
        self.assertEqual(self.cli("plan-check", self.SLUG)[0], 0)

    def test_the_rendered_page_never_shows_the_placeholder(self):
        vm = preview.view_model(self.layout, self.SLUG)
        html = preview_page.render(vm)
        self.assertNotIn(PLACEHOLDER, html)

    def test_spec_ready_actually_gated_it_before_planning(self):
        # Proves the gate is real, not just that this fixture happens to
        # satisfy it: a sibling spec that never records the intake must
        # still be refused by `ctx plan`.
        spec_mod.create(self.layout, "no-intake-recorded", intent="Do a thing.")
        code, out = self.cli("plan", "no-intake-recorded")
        self.assertNotEqual(code, 0)
        self.assertIn("intake", out.lower())


if __name__ == "__main__":
    unittest.main()
```

(`preview_page.render(vm)` — confirmed at `ctx/preview_page.py:789` — takes exactly the `view_model()` dict and returns the full HTML string; `preview_page.write(layout, slug)` is the higher-level function that calls both and writes the file, not needed here since the test already has the dict.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_preview_never_placeholder -v`
Expected: fails before Tasks 1-5 land; after them, passes as long as every field this fixture doesn't author gets a real tier-2/3/4 answer.

- [ ] **Step 3: There is no new implementation in this task**

If this test fails after Tasks 1-6 are all merged, that is a real gap in the chain — go back to whichever tier's field is still showing the placeholder and fix that tier, rather than weakening this test. This test is the one that speaks for the user's actual requirement; nothing about it should be adjusted to make it pass.

- [ ] **Step 4: Run the full suite, then commit**

Run: `python3 -m unittest discover -s tests -q`
Expected: only the one pre-existing `report.md` failure.

```bash
git add tests/test_preview_never_placeholder.py
git commit -m "Add the end-to-end proof: a real plan's preview never placeholders

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
