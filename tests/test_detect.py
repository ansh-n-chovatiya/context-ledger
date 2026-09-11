"""`ctx/detect.py`: the ecosystem taxonomy and the availability probe, and the
module boundary that pulling them out of `cli.py` is supposed to create.

Three things are pinned here.

*The extraction is real.* `cmd_ci`, `cmd_doctor` and `cmd_init` used to reach
into a private `cli._availability`; a "module" whose consumers all call a
leading-underscore name is a file, not a boundary. So the public names are
asserted to exist, and `cli.py` is asserted to call them rather than the
private spellings it kept only as compatibility shims for the audit suites.

*The five profiles agree.* `config.PROFILES` declared five, `--profile`
accepted five, and the marker table knew four: `research` could be chosen by
hand and never detected. That is not a missing feature, it is two lists that
were never made to agree, so the test is the agreement rather than a test of
the markers that closed it.

*The import tree stays acyclic.* Two new modules in one wave is exactly when a
cycle gets introduced, and neither of them may import `cli`.
"""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctx import cli, config as config_mod, detect  # noqa: E402
from support import Fixture  # noqa: E402


PACKAGE = Path(__file__).resolve().parent.parent / "ctx"


def profile_choices():
    """What `ctx init --profile` actually accepts, read off the built parser
    rather than off the constant it was built from — the point of the check is
    that the two ends agree, so reading one end twice would prove nothing."""
    parser = cli.build_parser()
    actions = parser._subparsers._group_actions[0].choices["init"]._actions
    for action in actions:
        if action.dest == "profile":
            return set(action.choices)
    raise AssertionError("`ctx init` no longer has a --profile argument")


def detectable_profiles():
    return {profile for profile, _marker, _weight in detect._PROFILE_MARKERS}


# --------------------------------------------------------------------------- #
# the extraction
# --------------------------------------------------------------------------- #

class TestTheModuleIsPublic(unittest.TestCase):

    def test_the_probe_and_the_taxonomy_are_public_names(self):
        for name in ("availability", "runnable", "detect_profile",
                     "python_exe", "node_candidates", "verify_candidates"):
            self.assertTrue(callable(getattr(detect, name, None)),
                            f"detect.{name} is not a public callable")

    def test_no_command_reaches_into_the_private_spelling(self):
        """`cmd_ci` calling `_availability` is what identified this extraction.

        The old private names survive in `cli.py` as one-line aliases, because
        three audit test modules pin them and those modules are not this unit's
        to edit. An alias is not a call: what must not survive is `cli.py`
        *calling* a private detection name, which is the reach-through.
        """
        source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        private = {"_availability", "_runnable", "_detect_profile",
                   "_python_exe", "_node_candidates", "_verify_candidates"}
        offenders = [
            f"line {node.lineno}: {node.func.id}(...)"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id in private
        ]
        self.assertEqual(offenders, [], "cli.py still calls a private detector")

    def test_the_aliases_are_the_public_functions(self):
        """The shims must not become a second implementation."""
        self.assertIs(cli._availability, detect.availability)
        self.assertIs(cli._runnable, detect.runnable)
        self.assertIs(cli._detect_profile, detect.detect_profile)
        self.assertIs(cli._python_exe, detect.python_exe)
        self.assertIs(cli._verify_candidates, detect.verify_candidates)

    def test_detect_reads_nothing_from_the_ledger(self):
        """It answers questions about the machine. A `.ctx/` import here would
        mean the module had quietly grown a second job."""
        source = (PACKAGE / "detect.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                imported.update(alias.name for alias in node.names)
        self.assertEqual(imported, set(),
                         "detect.py imports from the package; it used to need "
                         "nothing but the standard library")


# --------------------------------------------------------------------------- #
# criterion 2 — the fifth profile
# --------------------------------------------------------------------------- #

class TestEveryProfileIsDetectable(unittest.TestCase):
    """A profile that can be selected but never detected is a profile that
    only exists for someone who already knows it exists."""

    def test_accepted_and_detectable_profiles_agree(self):
        accepted = profile_choices()
        detectable = detectable_profiles()
        self.assertEqual(
            accepted, detectable,
            "`--profile` and detect._PROFILE_MARKERS disagree — either give "
            "the new profile markers or stop accepting it")

    def test_the_declared_profiles_are_the_accepted_ones(self):
        self.assertEqual(set(config_mod.PROFILES), profile_choices())

    def test_every_detectable_profile_can_be_returned(self):
        """A profile in the marker table but absent from `_PROFILE_ORDER` can
        win the score and still never be returned, which is the same hole one
        layer down."""
        self.assertEqual(set(detect._PROFILE_ORDER), detectable_profiles())


class TestResearchIsDetected(Fixture):
    """`research` was the profile the markers did not know."""

    def write_file(self, name, text="x\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_bibliography_detects_research(self):
        self.write_file("references.bib", "@article{a, title={A}}\n")
        self.assertEqual(detect.detect_profile(self.root), "research")

    def test_a_bare_sources_directory_does_not_outrank_a_manifest(self):
        """Weak evidence stays weak: the `docs/` mistake, not repeated."""
        self.write_file("pyproject.toml", "[project]\nname='x'\n")
        self.write_file("sources/paper.md", "# Notes\n")
        self.assertEqual(detect.detect_profile(self.root), "code")

    def test_research_is_usable_end_to_end(self):
        code, out = self.cli("init", "--profile", "research", "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(config_mod.load(self.layout)["profile"], "research")


# --------------------------------------------------------------------------- #
# criterion 10 — the import tree
# --------------------------------------------------------------------------- #

def internal_imports(path, nested=False):
    """Names this module imports from its own package.

    Module-level only, by default. A handful of functions in `verify.py`
    import `contract`, `review` and `findings` *inside the function body* —
    that is the deliberate escape hatch, and it is how the gate can call back
    into modules that import it. Counting those as edges would report the
    cycles they exist to avoid, so the graph is the one the interpreter builds
    at import time, which is the one that can actually deadlock.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = ast.walk(tree) if nested else tree.body
    out = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ctx."):
                    out.add(alias.name.split(".", 1)[1])
    return out


class TestTheImportTreeIsStillAcyclic(unittest.TestCase):
    """The audit measured zero cycles across the package. Splitting `cli.py`
    into three modules is exactly the change that would introduce the first
    one, so the whole graph is walked rather than the two new edges."""

    def graph(self):
        modules = {p.stem: p for p in PACKAGE.glob("*.py")
                   if p.stem not in ("__init__", "__main__")}
        return {name: internal_imports(path) & set(modules)
                for name, path in modules.items()}

    def test_no_module_import_cycle_anywhere_in_the_package(self):
        graph = self.graph()
        state, cycles = {}, []

        def walk(node, trail):
            if state.get(node) == "done":
                return
            if state.get(node) == "open":
                cycles.append(" -> ".join(trail[trail.index(node):] + [node]))
                return
            state[node] = "open"
            for nxt in sorted(graph[node]):
                walk(nxt, trail + [nxt])
            state[node] = "done"

        for name in sorted(graph):
            walk(name, [name])
        self.assertEqual(cycles, [], "an import cycle appeared")

    def test_neither_new_module_imports_the_command_layer(self):
        """Checked including function-level imports: a deferred `from . import
        cli` is still `cli`, and it is the shape an extraction reaches for
        when it turns out to have left something behind."""
        for name in ("detect", "advice"):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cli", internal_imports(PACKAGE / f"{name}.py", nested=True))

    def test_the_walk_would_find_a_cycle_if_there_were_one(self):
        """A guard that cannot fail is not a guard."""
        graph = {"a": {"b"}, "b": {"a"}}
        state, cycles = {}, []

        def walk(node, trail):
            if state.get(node) == "done":
                return
            if state.get(node) == "open":
                cycles.append(" -> ".join(trail[trail.index(node):] + [node]))
                return
            state[node] = "open"
            for nxt in sorted(graph[node]):
                walk(nxt, trail + [nxt])
            state[node] = "done"

        walk("a", ["a"])
        self.assertTrue(cycles)


if __name__ == "__main__":
    unittest.main()
