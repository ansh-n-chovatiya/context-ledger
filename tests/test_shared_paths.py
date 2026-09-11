"""One definition of the ledger prefix, one definition of the unit-file path.

Two strings were spelled out by hand in module after module:

* `LEDGER_PREFIX = ".ctx/"` in `snapshot.py`, `worktree.py` and `verify.py`,
  none of them referencing `paths.CTX_DIRNAME`, which already held the name.
* `layout.plans / plan / "units" / f"{unit}.md"` in `briefing.py`, `hooks.py`
  and twice in `work.py`, alongside `plan.units_dir`, the function whose job
  that is.

Neither is a bug today. Both are the shape a bug arrives in: rename the ledger
directory and two of the three prefixes keep matching a directory that is gone;
change the plan layout and four readers keep looking where units used to live.
Nothing goes red, because agreement between four hand-typed copies is not
checked by anything.

So this file checks it. The prefix is derived from `CTX_DIRNAME` in `paths.py`
and nowhere else re-declared; the accessor is proved equal to the function that
owns plan layout; and an enumeration over every module in `ctx/` fails the next
time somebody types `"units"` into a path outside the two modules allowed to.
"""

import ast
import unittest
from pathlib import Path

from ctx import paths, plan as plan_mod, snapshot, worktree


PACKAGE = Path(__file__).resolve().parent.parent / "ctx"


# Every module in `ctx/`, named one by one. Deliberately not a glob: the point
# of the enumeration below is that a *new* module is either listed here or the
# completeness test goes red, and a glob would quietly absorb it instead.
ALL_MODULES = (
    "__init__.py", "__main__.py", "advice.py", "atomic.py", "briefing.py",
    "bundle.py", "cli.py", "complexity.py", "config.py", "contract.py",
    "detect.py", "dispatch.py",
    "findings.py", "frontmatter.py", "hooks.py", "journal.py", "lock.py",
    "migrate.py", "miniyaml.py", "paths.py", "phases.py", "plan.py",
    "redact.py", "review.py", "snapshot.py", "spec.py", "state.py",
    "telemetry.py", "trust.py", "verify.py", "work.py", "worktree.py",
)

# The two modules allowed to build `units` into a path:
#
#   plan.py    owns plan layout — `plan_dir`, `units_dir`, `graph_path`
#   paths.py   `Layout.unit_file`, the read-only accessor everybody else uses
#
# Any third module doing it is a fourth copy of the layout waiting to disagree
# with the other three.
UNITS_SEGMENT_OWNERS = ("plan.py", "paths.py")

# Three modules pass `"*/units/*.md"` to `Path.glob`. That is a *search* across
# every plan, not the construction of one unit's path, and there is no accessor
# to route it through — `Layout.unit_file` needs a plan and a unit, which is
# exactly what a sweep does not have. They are named here one by one, and the
# test below is a subset check, so a fourth module cannot join them quietly:
#
#   cli.py       `ctx check` sweeping every unit file for contract drift
#   migrate.py   the per-kind migration sweep
#   trust.py     collecting every declared verify command in the ledger
#
# A `Layout.unit_files()` iterator would fold these in. It would also mean this
# unit writing three files it does not own, so the sweep sites are left as they
# are and recorded here rather than silently skipped.
UNITS_GLOB_SWEEPS = {"cli.py", "migrate.py", "trust.py"}

# `verify.py` still carries its own `LEDGER_PREFIX`. It belongs to a sibling
# unit in this plan, which adopts `paths.LEDGER_PREFIX` there; converting it
# from here would have meant two units writing one file. The assertion below is
# a subset check rather than an equality so that removing it turns this file
# green, not red.
PREFIX_DECLARERS_STILL_ALLOWED = {"paths.py", "verify.py"}


def source(name):
    return (PACKAGE / name).read_text(encoding="utf-8")


def tree(name):
    return ast.parse(source(name), filename=name)


def module_level_assignments(name, target):
    """`[node]` for every top-level `target = <...>` in the named module."""
    out = []
    for node in tree(name).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if target in names:
                out.append(node)
    return out


def _glob_pattern_lines(src, name):
    """Line numbers of string constants handed to `glob`/`rglob`.

    A glob pattern is a search over paths that already exist, not a path being
    constructed, and the two are separated so that excusing the sweeps cannot
    accidentally excuse a hand-built path on the same line.
    """
    lines = set()
    for node in ast.walk(ast.parse(src, filename=name)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("glob", "rglob")):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                lines.add(arg.lineno)
    return lines


def units_in_paths(src, name, include_globs=False):
    """`[lineno]` for every place `src` builds the segment `units` into a path.

    Parsed rather than grepped, because the word appears legitimately all over
    the package — in prose, in `"unit" if n == 1 else "units"`, as the
    `plan.json` key. What this looks for is the three ways a *path* gets the
    segment: an operand of `/`, an argument to `joinpath`, and a literal that
    carries its own separator.

    Glob patterns are excluded unless asked for; see `UNITS_GLOB_SWEEPS`.
    """
    skip = set() if include_globs else _glob_pattern_lines(src, name)
    hits = []
    for node in ast.walk(ast.parse(src, filename=name)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == "units":
                    hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "joinpath":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "units":
                        hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.lineno in skip:
                continue
            if "units/" in node.value or "/units" in node.value:
                hits.append(node.lineno)
    return sorted(set(hits))


class TestLedgerPrefixIsDerivedOnce(unittest.TestCase):

    def test_the_value_is_the_directory_name_plus_a_slash(self):
        self.assertEqual(paths.LEDGER_PREFIX, paths.CTX_DIRNAME + "/")
        self.assertEqual(paths.LEDGER_PREFIX, ".ctx/")

    def test_it_is_derived_rather_than_retyped(self):
        """Equality above passes just as well for a second `".ctx/"` literal.

        The criterion is that the prefix *references* `CTX_DIRNAME`, so that
        renaming the ledger directory moves the prefix with it. That is a claim
        about the expression, so it is checked on the expression.
        """
        assigns = module_level_assignments("paths.py", "LEDGER_PREFIX")
        self.assertEqual(len(assigns), 1, "paths.LEDGER_PREFIX is assigned once")
        value = assigns[0].value
        self.assertIsInstance(
            value, ast.BinOp,
            "LEDGER_PREFIX must be built from CTX_DIRNAME, not written out")
        names = [n.id for n in ast.walk(value) if isinstance(n, ast.Name)]
        self.assertIn("CTX_DIRNAME", names)

    def test_no_module_outside_paths_redeclares_it(self):
        declarers = {
            name for name in ALL_MODULES
            if module_level_assignments(name, "LEDGER_PREFIX")
        }
        self.assertIn("paths.py", declarers)
        self.assertLessEqual(
            declarers, PREFIX_DECLARERS_STILL_ALLOWED,
            "these keep a private copy of the ledger prefix instead of using "
            "paths.LEDGER_PREFIX: "
            + ", ".join(sorted(declarers - PREFIX_DECLARERS_STILL_ALLOWED)),
        )

    def test_snapshot_and_worktree_no_longer_carry_a_copy(self):
        for name in ("snapshot.py", "worktree.py"):
            self.assertEqual(
                module_level_assignments(name, "LEDGER_PREFIX"), [],
                f"{name} still defines its own LEDGER_PREFIX")

    def test_both_ledger_checks_follow_the_shared_definition(self):
        """The behavioural half: move the shared prefix and both callers move.

        A module that had merely *imported* a copy at definition time would
        keep answering for `.ctx/` here.
        """
        original = paths.LEDGER_PREFIX
        try:
            paths.LEDGER_PREFIX = ".ledger/"
            self.assertTrue(snapshot.is_ledger(".ledger/journal/2026-01-01.md"))
            self.assertTrue(worktree._is_ledger(".ledger/plans/p/plan.json"))
            self.assertFalse(snapshot.is_ledger(".ctx/journal/2026-01-01.md"))
            self.assertFalse(worktree._is_ledger(".ctx/plans/p/plan.json"))
        finally:
            paths.LEDGER_PREFIX = original
        self.assertTrue(snapshot.is_ledger(".ctx/journal/2026-01-01.md"))
        self.assertTrue(worktree._is_ledger(".ctx/plans/p/plan.json"))

    def test_windows_separators_still_match(self):
        layout_free = snapshot.is_ledger(".ctx\\journal\\2026-01-01.md")
        self.assertTrue(layout_free)
        self.assertTrue(worktree._is_ledger(".ctx\\plans\\p\\plan.json"))


class TestUnitFileAccessorAgreesWithPlan(unittest.TestCase):

    def layout(self):
        return paths.Layout(Path("/tmp/project/.ctx"))

    def test_it_matches_what_plan_units_dir_would_build(self):
        """`plan.py` owns plan layout. The accessor is a convenience for
        readers, so it has to produce the byte-identical path — otherwise it is
        a second definition wearing the first one's name."""
        layout = self.layout()
        for slug, unit in (
            ("make-ctx-durable", "01-shared-paths"),
            ("p", "u"),
            ("a-b-c", "99-z"),
        ):
            self.assertEqual(
                layout.unit_file(slug, unit),
                plan_mod.units_dir(layout, slug) / f"{unit}.md",
            )

    def test_it_matches_what_scaffold_unit_writes(self):
        """The writer's path and the readers' path are the same path."""
        layout = self.layout()
        self.assertEqual(
            layout.unit_file("plan-slug", "03-verify"),
            plan_mod.units_dir(layout, "plan-slug") / "03-verify.md",
        )

    def test_the_shape_is_the_documented_one(self):
        layout = paths.Layout(Path("/repo/.ctx"))
        self.assertEqual(
            layout.unit_file("my-plan", "01-a").as_posix(),
            "/repo/.ctx/plans/my-plan/units/01-a.md",
        )

    def test_it_is_relative_to_the_layout_root(self):
        other = paths.Layout(Path("/elsewhere/.ctx"))
        self.assertEqual(
            other.unit_file("my-plan", "01-a").as_posix(),
            "/elsewhere/.ctx/plans/my-plan/units/01-a.md",
        )


class TestNobodyElseBuildsTheUnitsSegment(unittest.TestCase):

    def test_only_plan_and_paths_build_it(self):
        offenders = []
        for name in ALL_MODULES:
            if name in UNITS_SEGMENT_OWNERS:
                continue
            for line in units_in_paths(source(name), name):
                offenders.append(f"{name}:{line}")
        self.assertEqual(
            offenders, [],
            "these hand-build the unit-file path — call "
            "layout.unit_file(plan, unit) (or plan.units_dir) instead: "
            + ", ".join(offenders),
        )

    def test_the_four_converted_call_sites_stay_converted(self):
        """Named individually, so that a revert to a hand-built path fails on
        the module it happened in rather than in one anonymous list."""
        for name in ("briefing.py", "hooks.py", "work.py"):
            self.assertEqual(units_in_paths(source(name), name), [],
                             f"{name} builds a unit path by hand again")
            self.assertIn("unit_file(", source(name),
                          f"{name} no longer goes through the accessor")
        self.assertEqual(source("work.py").count("layout.unit_file("), 2)

    def test_only_the_named_sweeps_glob_the_segment(self):
        """The globs are excused, not ignored. A fourth module joining them
        fails here, which is the difference between a recorded exception and a
        hole in the guard."""
        sweepers = set()
        for name in ALL_MODULES:
            if name in UNITS_SEGMENT_OWNERS:
                continue
            src = source(name)
            with_globs = set(units_in_paths(src, name, include_globs=True))
            without = set(units_in_paths(src, name))
            if with_globs - without:
                sweepers.add(name)
        self.assertEqual(
            sweepers, UNITS_GLOB_SWEEPS,
            "the set of modules globbing */units/*.md has changed — add the "
            "new one to UNITS_GLOB_SWEEPS with its reason, or route it "
            "through plan.py")

    def test_the_glob_excuse_does_not_cover_a_hand_built_path(self):
        """Excluding glob patterns must not exclude construction. A module
        doing both is still an offender."""
        both = 'a = root.glob("*/units/*.md")\nb = root / "units" / "x.md"\n'
        self.assertEqual(units_in_paths(both, "x"), [2])

    def test_the_enumeration_covers_every_module(self):
        """Without this, a module added tomorrow is checked by nothing and no
        test anywhere goes red."""
        on_disk = sorted(p.name for p in PACKAGE.glob("*.py"))
        self.assertEqual(
            sorted(ALL_MODULES), on_disk,
            "ctx/ and ALL_MODULES disagree — add the new module to the tuple")

    def test_the_detector_would_catch_a_new_offender(self):
        """A guard is only a guard if it can fail. Feed it three shapes."""
        self.assertEqual(
            units_in_paths('p = layout.plans / plan / "units" / f"{u}.md"', "x"),
            [1])
        self.assertEqual(
            units_in_paths('p = root.joinpath("units", name)', "x"), [1])
        self.assertEqual(
            units_in_paths('p = f"{base}/units/{name}.md"', "x"), [1])

    def test_the_detector_does_not_fire_on_the_word_alone(self):
        """`complexity.py` pluralises the word and `plan.py` uses it as a JSON
        key. Neither is a path, and a detector that flagged them would be
        weakened until it caught nothing."""
        self.assertEqual(
            units_in_paths('plural = "unit" if n == 1 else "units"', "x"), [])
        self.assertEqual(units_in_paths('graph = {"units": {}}', "x"), [])
        self.assertEqual(
            units_in_paths('"""Two units in the same wave."""', "x"), [])


class TestDeadCodeIsGone(unittest.TestCase):
    """Both functions were unreferenced across `ctx/`, `tests/`, `commands/`,
    `hooks/` and `bin/`. `snapshot.discard_all` was the dangerous one: an
    `rmtree` of the whole snapshot root, which is every dispatch's `before`
    baseline — the evidence a review diffs against. `hooks._edit_target` was
    the singular leftover of `_edit_targets`, returning the first of the paths
    a tool call writes and silently dropping the rest."""

    def test_snapshot_discard_all_is_deleted(self):
        self.assertFalse(hasattr(snapshot, "discard_all"))

    def test_the_per_key_discard_survives(self):
        """`discard` takes a key and is called; only the blast-radius version
        went."""
        self.assertTrue(callable(snapshot.discard))

    def test_hooks_edit_target_is_deleted(self):
        from ctx import hooks
        self.assertFalse(hasattr(hooks, "_edit_target"))

    def test_the_plural_edit_targets_survives(self):
        from ctx import hooks
        self.assertTrue(callable(hooks._edit_targets))
        self.assertEqual(
            hooks._edit_targets({"tool_name": "Write",
                                 "tool_input": {"file_path": "a.py"}}),
            ["a.py"])

    def test_neither_name_appears_anywhere_in_the_package(self):
        for name in ALL_MODULES:
            text = source(name)
            self.assertNotIn("discard_all", text, f"{name} references discard_all")
            self.assertNotIn("_edit_target(", text,
                             f"{name} references _edit_target")


if __name__ == "__main__":
    unittest.main()
