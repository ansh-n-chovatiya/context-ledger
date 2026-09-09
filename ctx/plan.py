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

from . import config as config_mod, frontmatter, verify

TIERS = ("inline", "subagent", "session")
STATUSES = ("pending", "running", "blocked", "verify_failed", "done")

REQUIRED = ("unit", "tier", "owns")
_UNIT_NAME = re.compile(r"^[0-9]{2}-[a-z0-9][a-z0-9-]*$")

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
        path.write_text(
            f"# Plan — {slug}\n\n"
            f"Spec: `.ctx/specs/{spec_slug or slug}/spec.md`\n\n"
            "## Approach\n"
            "<!-- One paragraph: how the work was cut up, and why along these lines. -->\n\n"
            "## Units\n"
            "<!-- Regenerated by `ctx plan-check`. -->\n\n"
            "## Out of scope\n",
            encoding="utf-8",
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
    which normcases both sides; the prefix rules are plain string comparisons and
    are deliberately *not* folded, exactly as `_covers` has them.
    """

    def __init__(self, units):
        self._exact = {}    # pattern -> {unit position}: equality
        self._folded = {}   # normcase(pattern) -> {position}: fnmatch, magic-free
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
                        head = os.path.normcase(_literal_head(pattern))
                        self._globs.setdefault(head, []).append(pattern)
                else:
                    self._folded.setdefault(
                        os.path.normcase(pattern), set()
                    ).add(position)
        # The one question no key can answer in advance: a *query* that is itself
        # a glob may match patterns spread anywhere under its literal head, so
        # those are found by bisecting a sorted list instead.
        self._sorted = sorted(
            (os.path.normcase(pattern), pattern) for pattern in self._exact
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
        hit = self._folded.get(os.path.normcase(path))
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
            for head in _heads(os.path.normcase(path)):
                for pattern in self._globs.get(head, ()):
                    if fnmatch.fnmatch(path, pattern):
                        found |= self._exact[pattern]       # path matched by glob
        if _MAGIC.search(path):
            head = os.path.normcase(_literal_head(path))
            for pattern in self._candidates(head):
                if fnmatch.fnmatch(pattern, path):
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


def _covers(path, pattern):
    path = str(path).replace(os.sep, "/").rstrip("/")
    pattern = str(pattern).replace(os.sep, "/").rstrip("/")
    if not path or not pattern:
        return False
    if path == pattern:
        return True
    if fnmatch.fnmatch(path, pattern):
        return True
    return path.startswith(pattern + "/")


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
            (archive / f"plan.r{previous.get('revision', 1)}.json").write_text(
                json.dumps(previous, indent=2) + "\n", encoding="utf-8"
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
    path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
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
    path.write_text(text, encoding="utf-8")
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
