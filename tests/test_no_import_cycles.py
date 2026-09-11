"""The import graph over `ctx/` is acyclic, and stays that way.

The audit that produced this plan checked the property once, by hand, with an
AST walk across every module in the package. Once is not a property: the next
extraction that reaches back into `cli` for one helper reintroduces the cycle,
the package still imports (Python tolerates a great deal), and the failure
surfaces later as an `AttributeError` on a half-initialised module, or as an
import order that only works because `__init__` happens to list the modules in
a lucky sequence.

Two things are asserted, and the difference between them is the whole design:

  * **module-level** imports form a directed acyclic graph. These run at import
    time, so a cycle here is the one that can actually deadlock or half-build a
    module.
  * **function-level** imports may form cycles, and three of them do. They are
    the deliberate breakers: `verify` is used by `plan`, `hooks` and `worktree`,
    so it may not import them at the top — it reaches for `plan`, `contract`,
    `review`, `findings` and `journal` inside the functions that need them.
    That is the pattern this test protects, so it is named rather than banned.

`cli` is the sink: it imports most of the package and nothing imports it back,
which is what lets every other module be tested without an argparse namespace.
"""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PACKAGE = Path(__file__).resolve().parent.parent / "ctx"


def _imports(path):
    """`(module_level, deferred)` — sibling modules imported by this file.

    Deferred means "inside a function or a method": it runs when the function
    is called, long after every module is built, so it cannot participate in an
    import-time cycle.
    """
    top, deferred = set(), set()

    class Walk(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0

        def visit_FunctionDef(self, node):
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def _record(self, name):
            (deferred if self.depth else top).add(name)

        def visit_ImportFrom(self, node):
            if node.level == 1:
                for alias in node.names:
                    self._record(alias.name)
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name.startswith("ctx."):
                    self._record(alias.name.split(".", 1)[1])
            self.generic_visit(node)

    Walk().visit(ast.parse(path.read_text(encoding="utf-8")))
    return top, deferred


def _graph(deferred_too=False):
    """The import graph over `ctx/`, keyed by module name."""
    modules = {}
    for path in sorted(PACKAGE.glob("*.py")):
        top, deferred = _imports(path)
        modules[path.stem] = (top | deferred) if deferred_too else top
    names = set(modules)
    return {name: sorted(edge for edge in edges if edge in names)
            for name, edges in modules.items()}


def _cycles(graph):
    """Every cycle in `graph`, as the list of modules that closes it."""
    found, state = [], {}

    def walk(node, stack):
        state[node] = "open"
        stack.append(node)
        for neighbour in graph.get(node, ()):
            if state.get(neighbour) == "open":
                found.append(stack[stack.index(neighbour):] + [neighbour])
            elif not state.get(neighbour):
                walk(neighbour, stack)
        stack.pop()
        state[node] = "done"

    for node in sorted(graph):
        if not state.get(node):
            walk(node, [])
    return found


class TestTheImportGraph(unittest.TestCase):

    def test_every_module_in_the_package_is_walked(self):
        """A property checked over half the package is not the property."""
        graph = _graph()
        self.assertGreaterEqual(len(graph), 27, graph)
        for expected in ("cli", "verify", "advice", "detect", "paths", "plan"):
            self.assertIn(expected, graph)

    def test_it_is_acyclic_at_import_time(self):
        cycles = _cycles(_graph())
        self.assertEqual(
            cycles, [],
            "module-level import cycle(s): "
            + "; ".join(" -> ".join(cycle) for cycle in cycles)
            + ". Move the import inside the function that needs it, the way "
              "`verify` does, or the two modules are one module.",
        )

    def test_nothing_imports_the_command_layer(self):
        """`cli` is the sink. Every other module stays testable without it."""
        for name, edges in _graph(deferred_too=True).items():
            self.assertNotIn("cli", edges,
                             f"{name} imports cli — that is the cycle the "
                             "extractions were for; `cli` imports it already")

    def test_the_deferred_imports_are_the_cycle_breakers_and_are_named(self):
        """The cycles that exist are deferred, deliberate, and this short list.

        If this fails, either a new deferred import appeared — say so here and
        explain why it cannot be top-level — or one became top-level, which
        `test_it_is_acyclic_at_import_time` will already have caught.
        """
        deferred = {}
        for path in sorted(PACKAGE.glob("*.py")):
            top, later = _imports(path)
            if later - top:
                deferred[path.stem] = sorted(later - top)
        self.assertEqual(deferred, {
            # `config` journals a policy refusal; `journal` reads config.
            "config": ["journal"],
            # `trust` parses a unit file's frontmatter to find its commands.
            "trust": ["frontmatter"],
            # The gate. Everything that runs it imports it, so it may import
            # none of them at the top.
            "verify": ["contract", "findings", "journal", "plan", "review"],
        })


if __name__ == "__main__":
    unittest.main()
