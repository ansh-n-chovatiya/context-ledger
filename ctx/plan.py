"""Plans, units and waves.

A plan is a directory of unit files plus a generated graph. The unit files are
the authored artifact; `plan.json` is derived from them, so `depends_on` is the
single source of truth for ordering and nobody has to keep a wave number in sync
by hand.

Two safety checks run before anything is dispatched, and both are decidable by
this module without a model:

* **Disjoint ownership.** Two units in the same wave may not declare overlapping
  `owns`. That is what makes parallel writes safe rather than hopeful.
* **No read/write races.** A unit that *reads* a path another unit in the same
  wave *owns* would read a file mid-rewrite. `owns` sets can be disjoint and this
  can still be wrong, which is why it is a separate check.

Neither is auto-repaired. Both report the exact `depends_on` line that fixes
them — rewriting someone's dependency graph silently is not a favour.
"""

import bisect
import datetime
import fnmatch
import json
import os
import re
from pathlib import Path

from . import atomic, config as config_mod, frontmatter, verify

TIERS = ("inline", "subagent", "session")
STATUSES = ("pending", "running", "blocked", "verify_failed", "done")

REQUIRED = ("unit", "tier", "owns")
_UNIT_NAME = re.compile(r"^[0-9]{2}-[a-z0-9][a-z0-9-]*$")
# What `publishes_interface` strips before deciding a `## Interfaces` section is
# empty — the scaffolded template is only an HTML comment, so a unit that never
# touched the section must not read as having published anything.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

UNIT_TEMPLATE = """## Objective
{objective}

## Interfaces
<!-- What this consumes from earlier units, and what it produces that later units
     depend on. Name exact signatures: a sibling unit is relying on them. -->

## Acceptance criteria
1. <checkable — name the observable, not the implementation>
2. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output · any
interface you were forced to change (that blocks the wave).
"""


class Unit:
    def __init__(self, path, doc):
        self.path = path
        self.doc = doc
        self.name = str(doc.meta.get("unit") or path.stem)

    def __repr__(self):
        return f"<Unit {self.name} wave={self.wave} tier={self.tier}>"

    def _list(self, key):
        value = self.doc.meta.get(key) or []
        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value if str(item).strip()]

    depends_on = property(lambda self: self._list("depends_on"))
    owns = property(lambda self: self._list("owns"))
    reads = property(lambda self: self._read_paths())
    forbid = property(lambda self: self._list("forbid"))

    @property
    def tier(self):
        tier = str(self.doc.meta.get("tier") or "").strip().lower()
        return tier if tier in TIERS else ""

    @property
    def status(self):
        status = str(self.doc.meta.get("status") or "pending").strip().lower()
        return status if status in STATUSES else "pending"

    @property
    def wave(self):
        try:
            return int(self.doc.meta.get("wave"))
        except (TypeError, ValueError):
            return None

    @property
    def budget(self):
        try:
            return int(self.doc.meta.get("budget_tokens") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def checks(self):
        return self.doc.meta.get("verify") or []

    @property
    def model(self):
        """Per-unit override for the dispatch model. Empty means the config default."""
        return str(self.doc.meta.get("model") or "").strip()

    @property
    def recorded(self):
        """Judged kinds already signed off. Mirrors work.Work so the same gate
        code can run against a unit or a task."""
        return [str(k) for k in (self.doc.meta.get("verified") or [])]

    @property
    def kind(self):
        """Free-form classification, e.g. "bug" or "feature". Empty means
        unclassified — nothing downstream requires it."""
        return self._text("kind")

    @property
    def reproduction(self):
        """Repro steps for a `kind: bug` unit. `validate` flags a bug unit
        that has none, because a bug fix nobody can reproduce is unreviewable."""
        return self._text("reproduction")

    def _text(self, key):
        """A frontmatter value as text, or "" for anything that is not text —
        a mapping or a list under a scalar key is malformed, not a string in
        disguise."""
        value = self.doc.meta.get(key)
        if isinstance(value, (dict, list)):
            return ""
        return str(value or "").strip()

    @property
    def phases(self):
        """Named phases the complexity score and the phase gate key off of."""
        return self._list("phases")

    @property
    def publishes_interface(self):
        """True once `## Interfaces` holds real content, not just the scaffolded
        HTML-comment template `UNIT_TEMPLATE` writes. A sibling unit can only
        code against a signature that is actually written down."""
        text = _HTML_COMMENT.sub("", self.doc.section("interfaces")).strip()
        return bool(text)

    def _read_paths(self):
        """`reads` may be bare paths or {path, symbols} mappings."""
        out = []
        raw = self.doc.meta.get("reads") or []
        if isinstance(raw, str):
            raw = [raw]
        for entry in raw:
            if isinstance(entry, dict):
                path = entry.get("path")
                if path:
                    out.append(str(path))
            elif str(entry).strip():
                out.append(str(entry))
        return out

    def set(self, **changes):
        self.doc.meta.update(changes)
        self.doc.write(self.path)


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

def plan_dir(layout, slug):
    return layout.plans / slug


def units_dir(layout, slug):
    return plan_dir(layout, slug) / "units"


def graph_path(layout, slug):
    return plan_dir(layout, slug) / "plan.json"


def readme_path(layout, slug):
    return plan_dir(layout, slug) / "README.md"


def create(layout, slug, spec_slug=None):
    units_dir(layout, slug).mkdir(parents=True, exist_ok=True)
    path = readme_path(layout, slug)
    if not path.exists():
        atomic.write_text(
            path,
            f"# Plan — {slug}\n\n"
            f"Spec: `.ctx/specs/{spec_slug or slug}/spec.md`\n\n"
            "## Approach\n"
            "<!-- One paragraph: how the work was cut up, and why along these lines. -->\n\n"
            "## Units\n"
            "<!-- Regenerated by `ctx plan-check`. -->\n\n"
            "## Out of scope\n",
        )
    return path


def scaffold_unit(layout, slug, name, objective="", tier="subagent", owns=(), verify_checks=()):
    """Write an empty unit file for the model to fill in."""
    path = units_dir(layout, slug) / f"{name}.md"
    if path.exists():
        return path, False
    meta = {
        "ctx_schema": config_mod.SCHEMA,
        "unit": name,
        "plan": slug,
        "tier": tier,
        "depends_on": [],
        "owns": list(owns),
        "reads": [],
        "forbid": [],
        "budget_tokens": 45000,
        "status": "pending",
        "verify": list(verify_checks),
    }
    body = UNIT_TEMPLATE.format(objective=objective or "<one sentence: the observable outcome>")
    frontmatter.Document(meta, body).write(path)
    return path, True


def load_units(layout, slug):
    directory = units_dir(layout, slug)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        doc = frontmatter.read(path)
        if doc is not None:
            out.append(Unit(path, doc))
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

def validate(units):
    """Structural problems, independent of scheduling. Empty list means valid."""
    problems = []
    if not units:
        return ["no unit files — write .ctx/plans/<slug>/units/NN-name.md first"]

    names = [u.name for u in units]
    duplicates = {n for n in names if names.count(n) > 1}
    for name in sorted(duplicates):
        problems.append(f"duplicate unit name {name!r}")

    known = set(names)
    for unit in units:
        if not _UNIT_NAME.match(unit.name):
            problems.append(
                f"{unit.name}: name must be NN-kebab-case (the prefix orders the plan)"
            )
        for field in REQUIRED:
            if field == "owns":
                if not unit.owns and unit.tier != "inline":
                    problems.append(
                        f"{unit.name}: `owns` is empty — a unit with no declared write "
                        "scope cannot be checked for collisions"
                    )
            elif not unit.doc.meta.get(field):
                problems.append(f"{unit.name}: missing `{field}`")
        if unit.doc.meta.get("tier") and not unit.tier:
            problems.append(
                f"{unit.name}: tier {unit.doc.meta.get('tier')!r} is not one of "
                + "/".join(TIERS)
            )
        if unit.kind == "bug" and not unit.reproduction:
            problems.append(
                f"{unit.name}: kind is `bug` but `reproduction` is empty — add the "
                "steps that reproduce it before this unit can be dispatched"
            )
        if not verify.ordered(unit.checks):
            problems.append(
                f"{unit.name}: no usable `verify` checks — the done-gate cannot hold"
            )
        for dependency in unit.depends_on:
            if dependency not in known:
                problems.append(f"{unit.name}: depends_on unknown unit {dependency!r}")
            if dependency == unit.name:
                problems.append(f"{unit.name}: depends on itself")
    return problems


def waves(units):
    """(wave_number -> [Unit], problems). Wave 1 has no unmet dependencies."""
    by_name = {u.name: u for u in units}
    depth, problems = {}, []
    remaining = set(by_name)

    while remaining:
        ready = [
            name for name in remaining
            if all(
                dependency not in remaining
                for dependency in by_name[name].depends_on
                if dependency in by_name
            )
        ]
        if not ready:
            cycle = ", ".join(sorted(remaining))
            problems.append(f"dependency cycle among: {cycle}")
            break
        for name in sorted(ready):
            dependencies = [
                depth[d] for d in by_name[name].depends_on if d in depth
            ]
            depth[name] = (max(dependencies) + 1) if dependencies else 1
            remaining.discard(name)

    grouped = {}
    for name, level in depth.items():
        grouped.setdefault(level, []).append(by_name[name])
    for level in grouped:
        grouped[level].sort(key=lambda u: u.name)
    return grouped, problems


MAX_REPORTED = 20


def collisions(wave_units):
    """Ownership overlaps and read/write races within a single wave.

    Indexed, not pairwise. The pairwise version compared every pattern of every
    unit against every pattern of every other one — n² pairs times m² patterns —
    which measured 9.6 s for 200 units owning a dozen paths each, long enough
    that `ctx start` reads as hung, and 4½ minutes at 1,000 units. `_OwnsIndex`
    answers "which units own something touching this path?" in one lookup, but it
    answers with *exactly* the rules `_covers`/`_overlap` define, in both
    directions. That is deliberate: `owns` matching is the safety boundary that
    makes parallel writes safe, so an index that quietly matched less than the
    scan it replaced would be a worse bug than the slowness it fixes.
    """
    index = _OwnsIndex(wave_units)

    # (i, j) -> the raw paths of unit i that collide, so each pair is reported
    # once no matter how many of its paths overlap.
    overlaps, races = {}, {}
    for position, unit in enumerate(wave_units):
        for path in unit.owns:
            for other in index.owners_of(path):
                if other > position:
                    overlaps.setdefault((position, other), set()).add(path)
    for position, reader in enumerate(wave_units):
        for path in reader.reads:
            for other in index.owners_of(path):
                # The self-pair: a unit reading what it owns is the normal case,
                # not a race. Skipping it here is what the old inner loop's
                # name comparison did, minus the pass over every sibling.
                if other != position:
                    races.setdefault((position, other), set()).add(path)

    overlapping, racing = [], []
    for key in sorted(overlaps):
        first, second = wave_units[key[0]], wave_units[key[1]]
        overlapping.append(
            f"{first.name} and {second.name} both own "
            f"{', '.join(sorted(overlaps[key]))} "
            f"— add `depends_on: [{first.name}]` to {second.name} or split the paths"
        )
    for key in sorted(races):
        reader, writer = wave_units[key[0]], wave_units[key[1]]
        racing.append(
            f"{reader.name} reads {', '.join(sorted(races[key]))} while {writer.name} "
            f"rewrites it — add `depends_on: [{writer.name}]` to {reader.name}"
        )
    # Capped per kind, not once over the total: a wall of ownership overlaps must
    # not push the read/write races out of the report entirely.
    return _capped(overlapping) + _capped(racing)


def _capped(problems):
    """Report the first few and count the rest.

    A wave whose ownership is badly cut produces collisions quadratically — a
    thousand units with realistic scopes produced 19,000 full sentences, all
    printed. The refusal has to be readable to be acted on, and the fix for a
    plan with hundreds of overlaps is to re-cut it, not to work down the list.
    """
    if len(problems) <= MAX_REPORTED:
        return problems
    return problems[:MAX_REPORTED] + [
        f"... and {len(problems) - MAX_REPORTED} more of the same kind — the wave "
        "needs re-cutting rather than a fix per line"
    ]


def _overlap(left, right):
    """Paths in `left` covered by a pattern or prefix in `right`, and vice versa.

    The reference definition of a collision. `collisions()` uses `_OwnsIndex`
    instead, for speed; this stays as the plain statement of the rule the index
    has to reproduce, and the index is tested against it.
    """
    found = set()
    for a in left:
        for b in right:
            if _covers(a, b) or _covers(b, a):
                found.add(a)
    return found


# `*`, `?` and `[` are the only characters fnmatch treats as anything but text.
# A pattern without one of them can only ever match itself.
_MAGIC = re.compile(r"[*?\[]")


def _normalise(pattern):
    """The same shape `_covers` compares in: forward slashes, no trailing one."""
    return str(pattern).replace(os.sep, "/").rstrip("/")


def _ancestors(path):
    """Every strict directory prefix: "src/a/b" -> ["src/a", "src"].

    `_covers` asks whether `path` starts with `pattern + "/"`, which is true
    exactly when one of these strings *is* the pattern — so a dict lookup per
    ancestor decides the prefix rule without looking at any other pattern.
    """
    out = []
    cut = path.rfind("/")
    while cut > 0:
        path = path[:cut]
        out.append(path)
        cut = path.rfind("/")
    return out


def _literal_head(pattern):
    """The magic-free directory prefix of a glob, ending in "/" — or "".

    fnmatch compiles to an anchored regex, so nothing matches a glob without
    starting with the text before its first magic character. Bucketing globs by
    that head is a filter and not a heuristic: it can only rule out candidates
    that could not have matched anyway.
    """
    found = _MAGIC.search(pattern)
    head = pattern[:found.start()] if found else pattern
    cut = head.rfind("/")
    return head[:cut + 1] if cut >= 0 else ""


def _heads(path):
    """Every directory prefix of `path` ending in "/", plus "" — the glob buckets
    that could hold a pattern matching it."""
    out = [""]
    cut = path.find("/")
    while cut >= 0:
        out.append(path[:cut + 1])
        cut = path.find("/", cut + 1)
    return out


class _OwnsIndex:
    """A wave's `owns` patterns, keyed by every way `_covers` can match.

    `_overlap` asks four questions of a pattern pair — equality, either side
    living under the other as a directory, and fnmatch in either direction — so
    the index keeps one structure per question. Case folding follows fnmatch,
    which is case-sensitive on every platform; the prefix rules are plain string
    comparisons, exactly as `_covers` has them.
    """

    def __init__(self, units):
        self._exact = {}    # pattern -> {unit position}: equality
        self._folded = {}   # _fold(pattern) -> {position}: fnmatch, magic-free
        self._under = {}    # ancestor of a pattern -> {position}: pattern under path
        self._globs = {}    # literal head -> [glob pattern]: path matched by pattern
        seen = set()
        for position, unit in enumerate(units):
            for raw in unit.owns:
                pattern = _normalise(raw)
                if not pattern:
                    continue
                self._exact.setdefault(pattern, set()).add(position)
                for ancestor in _ancestors(pattern):
                    self._under.setdefault(ancestor, set()).add(position)
                if _MAGIC.search(pattern):
                    if pattern not in seen:
                        seen.add(pattern)
                        head = _fold(_literal_head(pattern))
                        self._globs.setdefault(head, []).append(pattern)
                else:
                    self._folded.setdefault(
                        _fold(pattern), set()
                    ).add(position)
        # The one question no key can answer in advance: a *query* that is itself
        # a glob may match patterns spread anywhere under its literal head, so
        # those are found by bisecting a sorted list instead.
        self._sorted = sorted(
            (_fold(pattern), pattern) for pattern in self._exact
        )

    def owners_of(self, raw):
        """Positions of the units owning a pattern `_covers` relates to `raw`."""
        path = _normalise(raw)
        if not path:
            return set()
        found = set()
        hit = self._exact.get(path)
        if hit:
            found |= hit                                    # path == pattern
        hit = self._folded.get(_fold(path))
        if hit:
            found |= hit                                    # fnmatch, magic-free
        for ancestor in _ancestors(path):
            hit = self._exact.get(ancestor)
            if hit:
                found |= hit                                # path under pattern
        hit = self._under.get(path)
        if hit:
            found |= hit                                    # pattern under path
        if self._globs:
            for head in _heads(_fold(path)):
                for pattern in self._globs.get(head, ()):
                    if fnmatch.fnmatchcase(path, pattern):
                        found |= self._exact[pattern]       # path matched by glob
        if _MAGIC.search(path):
            head = _fold(_literal_head(path))
            for pattern in self._candidates(head):
                if fnmatch.fnmatchcase(pattern, path):
                    found |= self._exact[pattern]           # pattern matched by glob
        return found

    def _candidates(self, head):
        """Patterns whose folded text starts with `head`, found by bisection."""
        out = []
        start = bisect.bisect_left(self._sorted, (head, ""))
        for folded, pattern in self._sorted[start:]:
            if not folded.startswith(head):
                break
            out.append(pattern)
        return out


def covers_any(path, patterns):
    """True when `path` falls inside any of `patterns`. Same rule the wave
    collision check uses, so merge-time scope enforcement cannot drift from it."""
    return any(_covers(path, pattern) for pattern in patterns or ())


def _fold(text):
    """The identity, named — scope matching must not vary by platform.

    This was `os.path.normcase`, which on Windows lowercases and rewrites "/" to
    "\\". The index folded its keys that way while `_covers` compared forward
    slashes, so on Windows the two disagreed about who owned `src/auth.py` and
    a real ownership collision went unreported. Keeping the seam as a function
    makes the decision visible rather than implied by an absent call.
    """
    return text


def _covers(path, pattern):
    path = str(path).replace(os.sep, "/").rstrip("/")
    pattern = str(pattern).replace(os.sep, "/").rstrip("/")
    if not path or not pattern:
        return False
    if path == pattern:
        return True
    if fnmatch.fnmatchcase(path, pattern):
        return True
    return path.startswith(pattern + "/")


# --------------------------------------------------------------------------- #
# plan-time intelligence
# --------------------------------------------------------------------------- #
#
# Everything below reports; nothing below refuses. A serial plan is sometimes
# the correct plan, and a warning that cannot be overruled is a refusal wearing
# a warning's clothes — so these are advisory at the command layer and `ctx
# plan-check` still exits 0 on a slow-but-valid plan.
#
# None of it needs new data. The graph already knows who owns what, which unit
# waits on which, and what each unit says it will cost; the reason a wave of
# eight units took ninety-five minutes of wall clock was not that the
# information was missing, it was that nobody printed it.

# How many units have to own one path before it is worth naming. Three is a
# judgement call, recorded as a decision rather than asked: two units sharing a
# file is an ordinary `depends_on` edge, three is a file that is deciding the
# shape of the plan.
BOTTLENECK_OWNERS = 3


def parallelism(grouped):
    """`(units, waves, ratio)` — how much of this plan can actually run at once.

    The ratio is units per wave, which is the average width of the plan. It is
    deliberately not "speed-up": the widest wave and the critical path say
    different things, and a single number claiming to be both would be wrong
    twice.
    """
    units = sum(len(members) for members in grouped.values())
    waves_count = len(grouped)
    ratio = round(units / waves_count, 2) if waves_count else 0.0
    return units, waves_count, ratio


def bottlenecks(units, minimum=BOTTLENECK_OWNERS):
    """`[(path, [unit name, ...]), ...]` — every path `minimum`+ units own.

    Asked through `_OwnsIndex`, not by comparing the raw strings, so a unit
    owning `ctx/*.py` counts as an owner of `ctx/cli.py` exactly as it would
    for a collision. The two questions have to agree: a path that would collide
    if those units shared a wave is the same path that is serialising them now
    that they do not.
    """
    ordered = list(units)
    index = _OwnsIndex(ordered)
    found = {}
    for unit in ordered:
        for raw in unit.owns:
            path = _normalise(raw)
            if not path or path in found:
                continue
            owners = sorted({ordered[position].name
                             for position in index.owners_of(path)})
            if len(owners) >= minimum:
                found[path] = owners
    # Most contended first; ties by path so the report is stable run to run.
    return sorted(found.items(), key=lambda item: (-len(item[1]), item[0]))


def critical_path(grouped):
    """`(estimate, total, [(wave, tokens), ...])` — an **estimate**, in tokens.

    A wave's units run concurrently, so the wave costs about what its widest
    unit costs, and the plan costs about the sum of those. That is the whole
    model, and it is wrong in both directions: units do not all start at once,
    a stated `budget_tokens` is the author's guess rather than a measurement,
    and tokens are not minutes. It is still the only number available before
    anything runs, which is why it is reported *and* labelled an estimate
    everywhere it is printed. Presenting it as a measurement would be worse
    than not printing it: wave 5 of the remediation is the evidence — a plan
    that looked eight-wide took ninety-five minutes of critical path.
    """
    rows = []
    for level in sorted(grouped):
        rows.append((level, max((unit.budget for unit in grouped[level]),
                                default=0)))
    estimate = sum(tokens for _, tokens in rows)
    total = sum(unit.budget for members in grouped.values() for unit in members)
    return estimate, total, rows


# A test file by name, either convention. Matched on the basename: where a
# project keeps its tests is its own business, what they are called is not.
_TEST_FILE = re.compile(r"^(test_[^/]+|[^/]+_test)\.py$")

# Directories never walked looking for tests: version control, the ledger
# itself, and the usual vendored or generated trees. A dotted directory is
# skipped wholesale — `.git` and `.venv` are the cases that matter and neither
# holds a test this repository wrote.
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", "build", "dist", "site-packages",
    "venv", "env",
})

# How many test files are read before the scan gives up. Reading every test in
# a large monorepo at plan-check time would turn an advisory report into the
# slowest thing the command does; a truncated scan reports what it found and
# says it was truncated, which is the honest failure mode.
MAX_TEST_FILES = 400

# `from pkg import a, b as c` — the parenthesised form spans lines, so the
# alternation takes the whole bracket before it falls back to the rest of the
# line.
_FROM_IMPORT = re.compile(
    r"^[ \t]*from[ \t]+([\w.]+)[ \t]+import[ \t]+(\([^)]*\)|[^\n]*)", re.M
)
_PLAIN_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+)", re.M)
# A path-ish literal anywhere in the text: a test that names `ctx/plan.py` in a
# string is exercising it even if it never imports it.
_PATH_LITERAL = re.compile(r"[\w./-]+\.py")


def _imported(text):
    """Dotted module names a Python source file refers to.

    Import statements only, plus `.py` literals — read with regexes rather than
    `ast`, because a test file that does not parse (a syntax error mid-edit, a
    file for a newer interpreter) must degrade to "found nothing here" instead
    of taking `plan-check` down with it.
    """
    modules, literals = set(), set(_PATH_LITERAL.findall(text))
    for module, names in _FROM_IMPORT.findall(text):
        modules.add(module)
        for entry in names.strip("()").split(","):
            first = entry.strip().split()[0] if entry.strip() else ""
            if first.isidentifier():
                modules.add(f"{module}.{first}")
    for module in _PLAIN_IMPORT.findall(text):
        modules.add(module)
    return modules, literals


def _dotted(path):
    """`ctx/plan.py` -> `ctx.plan`. Empty for anything that is not a module."""
    path = _normalise(path)
    if not path.endswith(".py"):
        return ""
    return path[:-3].replace("/", ".")


def _is_test_path(path):
    return bool(_TEST_FILE.match(_normalise(path).rsplit("/", 1)[-1]))


def _test_files(root):
    """`([repo-relative path, ...], truncated)` — every test file under `root`."""
    found, truncated = [], False
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories
            if name not in _SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if not _TEST_FILE.match(name):
                continue
            if len(found) >= MAX_TEST_FILES:
                truncated = True
                return found, truncated
            found.append(
                os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
            )
    return found, truncated


def ownership_gaps(layout, units, root=None):
    """`([(source, [test, ...]), ...], truncated)` — tests nobody in the plan owns.

    The failure this is for: a unit owns `ctx/state.py`, changes it, and is
    blocked mid-wave by `tests/test_core.py` — a file it may not edit, because
    no unit in the plan declared it. Everything needed to see that coming is on
    disk at plan time; nothing was looking.

    The detection is a heuristic and is worth stating as one. It finds a test
    that *imports* the module (either import form, including the parenthesised
    multi-line one) or names its path in a string. It does **not** find a test
    that reaches the module indirectly — through the CLI, through a subprocess,
    or through another module that imports it — so a clean report here means
    "no test names this file", not "no test can break". It also only looks at
    owned paths that are Python modules: an owned `README.md` or workflow file
    has no import to find.
    """
    root = Path(root) if root is not None else Path(layout.root).parent
    owned_patterns = [path for unit in units for path in unit.owns]
    sources = sorted({
        _normalise(path) for unit in units for path in unit.owns
        if _dotted(path) and not _is_test_path(path) and not _MAGIC.search(str(path))
    })
    if not sources:
        return [], False

    candidates, truncated = _test_files(root)
    unowned = [path for path in candidates if not covers_any(path, owned_patterns)]

    referenced = {}
    for test in unowned:
        try:
            text = (root / test).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        referenced[test] = _imported(text)

    gaps = []
    for source in sources:
        dotted = _dotted(source)
        hits = sorted(
            test for test, (modules, literals) in referenced.items()
            if dotted in modules or source in literals
        )
        if hits:
            gaps.append((source, hits))
    return gaps, truncated


def consumers_of(unit, siblings):
    """Units that both `depends_on` this one and read a path it owns.

    This is what "publishes an interface" has to mean to be worth scoring. A
    `## Interfaces` section on its own says the author wrote a heading; a
    sibling that declares the dependency *and* reads the file is the thing that
    makes getting the signature wrong cost somebody else's work — which is the
    entire justification `config.DEFAULTS` gives for the weight.

    Both halves are required on purpose. `depends_on` alone is ordinary
    sequencing (a unit that waits for a migration to run reads none of its
    code), and reading an owned path alone is what the wave's read/write race
    check already refuses. Together they are a consumer.
    """
    found = []
    for other in siblings or ():
        if other.name == unit.name:
            continue
        if unit.name not in other.depends_on:
            continue
        # `_overlap`, not `covers_any`: the same both-directions rule the
        # wave's read/write race check uses. A sibling that reads `src/*.py`
        # is reading `src/api.py`, and the two questions must not disagree
        # about the same pair of units.
        if _overlap(other.reads, unit.owns):
            found.append(other.name)
    return sorted(found)


def siblings_on_disk(unit):
    """The plan `unit` belongs to, re-read from its own directory.

    The fallback for a caller that scores one unit without having loaded the
    plan around it. A `Unit` knows its own path, and a plan is a directory of
    unit files, so "which units are in the same plan as this one" is answerable
    without the caller threading a list through — and a caller that cannot
    answer it must not silently get the old, wider behaviour instead. Anything
    that is not a unit file sitting in a `units/` directory gets an empty list
    rather than a scan of whatever directory it happens to be in.
    """
    path = Path(unit.path)
    directory = path.parent
    if directory.name != "units" or not path.is_file():
        return []
    out = []
    for candidate in sorted(directory.glob("*.md")):
        doc = frontmatter.read(candidate)
        if doc is not None:
            out.append(Unit(candidate, doc))
    return out


def check(layout, slug):
    """Full pre-dispatch check. (grouped_waves, problems)."""
    units = load_units(layout, slug)
    problems = validate(units)
    if problems:
        return {}, problems
    grouped, problems = waves(units)
    for level in sorted(grouped):
        problems.extend(
            f"wave {level}: {problem}" for problem in collisions(grouped[level])
        )
    return grouped, problems


def apply_waves(grouped):
    """Write the computed wave back into each unit so it is visible on disk."""
    for level, units in grouped.items():
        for unit in units:
            if unit.wave != level:
                unit.set(wave=level)


def write_graph(layout, slug, grouped, spec_slug=None):
    """Derive plan.json. Prior revisions are archived, never overwritten."""
    path = graph_path(layout, slug)
    revision = 1
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            revision = int(previous.get("revision", 1)) + 1
            archive = plan_dir(layout, slug) / "revisions"
            archive.mkdir(parents=True, exist_ok=True)
            atomic.write_text(
                archive / f"plan.r{previous.get('revision', 1)}.json",
                json.dumps(previous, indent=2) + "\n",
            )
        except (OSError, ValueError):
            revision = 1

    graph = {
        "ctx_schema": config_mod.SCHEMA,
        "plan": slug,
        "spec": spec_slug or slug,
        "revision": revision,
        "generated": datetime.date.today().isoformat(),
        "waves": [
            [unit.name for unit in grouped[level]] for level in sorted(grouped)
        ],
        "units": {
            unit.name: {
                "tier": unit.tier,
                "wave": level,
                "depends_on": unit.depends_on,
                "owns": unit.owns,
                "reads": unit.reads,
                "forbid": unit.forbid,
                "budget_tokens": unit.budget,
            }
            for level in sorted(grouped)
            for unit in grouped[level]
        },
    }
    atomic.write_text(path, json.dumps(graph, indent=2) + "\n")
    return path, revision


def render_readme_units(layout, slug, grouped):
    """Keep the human-facing Units section in step with the graph."""
    path = readme_path(layout, slug)
    if not path.is_file():
        return None
    lines = []
    for level in sorted(grouped):
        lines.append(f"**Wave {level}** — these may run concurrently")
        lines.append("")
        for unit in grouped[level]:
            objective = " ".join(unit.doc.section("objective").split())[:110]
            lines.append(
                f"- `{unit.name}` ({unit.tier}, {unit.status}) — {objective}"
            )
            if unit.owns:
                lines.append(f"  - owns: {', '.join(unit.owns)}")
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"

    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"(^## Units\s*$)(.*?)(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(lambda m: f"{m.group(1)}\n\n{body}\n", text, count=1)
    else:
        text = text.rstrip() + f"\n\n## Units\n\n{body}"
    atomic.write_text(path, text)
    return path


def board(layout, slug):
    """Rows for the status board: (wave, unit, tier, status, owns)."""
    grouped, problems = check(layout, slug)
    rows = []
    for level in sorted(grouped):
        for unit in grouped[level]:
            rows.append((level, unit.name, unit.tier, unit.status, unit.owns))
    return rows, problems


def next_wave(layout, slug):
    """Lowest wave with unfinished units, or None when the plan is complete."""
    grouped, problems = check(layout, slug)
    if problems:
        return None
    for level in sorted(grouped):
        if any(unit.status != "done" for unit in grouped[level]):
            return level
    return None


def find_unit(layout, slug, name):
    for unit in load_units(layout, slug):
        if unit.name == name:
            return unit
    return None
