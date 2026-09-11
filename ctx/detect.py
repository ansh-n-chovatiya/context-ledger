"""What the toolchain on disk says the project is, and whether a command on
that machine could actually start.

`ctx init` is the only command that has to answer "what kind of project is
this, and what would gate it?" from nothing but a directory listing, and
`ctx doctor` / `ctx ci` have to answer "could this command run here?" for a
verify entry that arrived in a committed `ctx.yaml`. Both questions are about
the *environment*, not about the ledger, which is why they live apart from the
command layer: nothing here reads `.ctx/`, and nothing here prints.

The split matters for one reason beyond tidiness. `availability` shells out —
it runs an interpreter to ask whether a module can be imported — and it does so
on strings the user has not reviewed yet. Keeping it in one module with one
documented probe is what makes that blast radius reviewable; it used to sit in
the middle of a three-thousand-line command file.

Every function here is public; the tables and the probe source stay private,
because they are the implementation of those functions and not an interface.
`cmd_ci` reaching into a private `cli._availability` is what identified this
module as a module, so leaving the entry points private would have moved the
code without moving the boundary.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

# (profile, marker, weight). Weight is how much evidence the marker really is.
# A build manifest at the root says what the project *is*; a directory named
# `docs` or `notebooks` says only that the project has some, which most projects
# of every kind do.
_PROFILE_MARKERS = (
    ("code", "package.json", 10), ("code", "pyproject.toml", 10),
    ("code", "go.mod", 10), ("code", "Cargo.toml", 10),
    ("code", "pom.xml", 10), ("code", "build.gradle", 10),
    ("code", "build.gradle.kts", 10), ("code", "Gemfile", 10),
    ("code", "composer.json", 10), ("code", "mix.exs", 10),
    ("code", "Package.swift", 10), ("code", "*.sln", 10),
    ("code", "*.csproj", 10), ("code", "setup.py", 8),
    ("code", "Makefile", 4),
    ("infra", "main.tf", 10), ("infra", "Chart.yaml", 10),
    ("infra", "terraform", 3),
    ("data", "dbt_project.yml", 10), ("data", "notebooks", 2),
    ("docs", "mkdocs.yml", 10), ("docs", "docusaurus.config.js", 10),
    ("docs", "docs", 2),
    # `research` was the fifth profile `config.PROFILES` declared and the one
    # this table did not know, so `--profile research` could be *chosen* but
    # never *detected* — the only profile in the product that needed a human to
    # know it existed. A bibliography is the one artefact that says "this
    # repository's output is an argument, not a build": nothing else ships a
    # `.bib`, and Zotero and Papers both export one. The directory names below
    # are the `docs`-style weak evidence, deliberately at the same weight of 2,
    # so a research folder inside a Python project still loses to the manifest.
    ("research", "*.bib", 10), ("research", "*.bibtex", 10),
    ("research", "references", 2), ("research", "literature", 2),
    ("research", "sources", 2),
)

# Ties break toward the profile that has real commands to propose. `research`
# is last for the same reason `docs` is late: its fallback is a `rubric`, so a
# tie resolved its way costs the project a runnable gate.
_PROFILE_ORDER = ("code", "infra", "data", "docs", "research")


def detect_profile(root):
    """Score every marker rather than returning on the first one that matches.

    First-match tested `docs` before `code`, and its markers included a bare
    `docs` directory — so a Python project that documented itself came out as a
    documentation project, which has no command candidates at all and fell back
    to a judged check. Most repositories have a `docs/`, so most repositories
    were mis-profiled into an ungated ledger.
    """
    scores = {}
    for profile, marker, weight in _PROFILE_MARKERS:
        if next(root.glob(marker), None) is not None:
            scores[profile] = scores.get(profile, 0) + weight
    if not scores:
        return "code"
    best = max(scores.values())
    for profile in _PROFILE_ORDER:
        if scores.get(profile) == best:
            return profile
    return "code"


def python_exe():
    """An interpreter name that will still resolve when the gate runs.

    `python` is absent from Homebrew and python.org installs, so proposing
    `python -m pytest` had `runnable` reject it and `init` wrote `verify: []` —
    an ungated ledger, from the feature whose whole job is to configure the gate.
    A bare name rather than `sys.executable` because ctx.yaml is committed and
    shared; an absolute path from one machine is wrong on every other.
    """
    for candidate in ("python3", "python"):
        if shutil.which(candidate):
            return candidate
    return sys.executable or "python3"


# (marker, commands). Data rather than an if-ladder so adding an ecosystem is a
# line here — and so `ctx.yaml` can extend it without a code change. Ordered by
# how commonly the marker is the project's real entry point.
_ECOSYSTEMS = (
    ("go.mod", ("go build ./...", "go test ./...")),
    ("Cargo.toml", ("cargo check",)),
    ("pom.xml", ("mvn -q -B test-compile",)),
    ("build.gradle", ("./gradlew --console=plain compileJava",)),
    ("build.gradle.kts", ("./gradlew --console=plain compileKotlin",)),
    ("Gemfile", ("bundle exec rake test",)),
    ("composer.json", ("composer run-script test",)),
    ("mix.exs", ("mix compile --warnings-as-errors",)),
    ("Package.swift", ("swift build",)),
    ("*.sln", ("dotnet build --nologo",)),
    ("*.csproj", ("dotnet build --nologo",)),
    ("dbt_project.yml", ("dbt compile",)),
    ("main.tf", ("terraform validate",)),
    ("Chart.yaml", ("helm lint .",)),
)

# Node is special-cased because what to run is inside package.json, not implied
# by its presence — and the workspace tools each front the same scripts.
_NODE_RUNNERS = (
    ("pnpm-workspace.yaml", "pnpm"), ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
)


def node_candidates(root):
    manifest = root / "package.json"
    if not manifest.is_file():
        return []
    text = manifest.read_text(encoding="utf-8", errors="replace")
    runner = "npm"
    for marker, name in _NODE_RUNNERS:
        if (root / marker).exists():
            runner = name
            break
    run = f"{runner} run" if runner == "npm" else runner
    out = []
    if '"typecheck"' in text:
        out.append(f"{run} typecheck")
    elif '"tsc"' in text or (root / "tsconfig.json").exists():
        out.append("npx tsc --noEmit")
    if '"test"' in text:
        out.append(f"{runner} test")
    if '"lint"' in text:
        out.append(f"{run} lint")
    return out


def verify_candidates(root, profile, extra=()):
    """Commands we would propose. Availability is checked; passing is not.

    `extra` comes from `verify_candidates` in ctx.yaml, so a house toolchain no
    table could anticipate — bazel, a wrapper script, a Makefile target — is a
    config line rather than a fork.
    """
    out = []
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        out.append(f"{python_exe()} -m pytest -q")
    out.extend(node_candidates(root))
    for marker, commands in _ECOSYSTEMS:
        if next(root.glob(marker), None) is not None:
            out.extend(commands)
    if (root / "Makefile").exists():
        text = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
        for target in ("test", "check", "build"):
            if re.search(rf"(?m)^{target}\s*:", text):
                out.append(f"make {target}")
                break
    for entry in extra or ():
        if str(entry).strip():
            out.append(str(entry).strip())

    seen, unique = set(), []
    for command in out:
        if command not in seen:
            seen.add(command)
            unique.append(command)
    return unique


# A module name, not a path and not an expression. Anything else is refused
# rather than resolved: the string reaches here from a committed ctx.yaml.
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Asked as a question about the *machine*, which means it must read nothing
# from the repository. The name under test arrives in a cloned ctx.yaml, and
# this probe was once the shortest path to arbitrary code execution in the
# whole tool, so the defences are layered rather than singular:
#
#   * the name is passed as `argv[1]`, never interpolated into source;
#   * only its top-level component is resolved by the caller — `find_spec`
#     on a dotted name *imports the parent package* to read its `__path__`,
#     so `find_spec("evilpkg.sub")` executed `evilpkg/__init__.py`;
#   * `sys.path[0]` is dropped, because `python -c` prepends the working
#     directory there, and any other entry naming that directory goes too;
#   * and the caller runs it in an empty scratch directory, so `sys.path[0]`
#     was never the repository to begin with.
#
# `find_spec` locates a module without executing it, which is the entire point
# of using it over an import — but only once the search path is trustworthy.
_PROBE = (
    "import importlib.util, os, sys; "
    "here = os.getcwd(); "
    "sys.path = [p for p in sys.path[1:] if p and os.path.abspath(p) != here]; "
    "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
)


def availability(command):
    """(can_it_start, why_not). The reason is user-facing, so it must be true.

    PATH alone is not enough for the interpreter forms: `python3 -m pytest` with
    pytest absent exits 1 from an interpreter that is very much present, so a
    PATH check accepts a command the gate can never actually run — and reports
    the wrong reason when it does reject one.

    Availability is decided *before* the trust store is consulted — `ctx doctor`
    prints MISS for a command it will never run — so everything this function
    does happens to strings the user has not reviewed yet. It therefore reads
    the module name and runs nothing that the repository supplied.

    A dotted name is answered for its top-level package only. `python3 -m
    json.tool` on a machine with `json` is reported available even though
    nothing checked `tool`; the alternative is importing `json` to ask, and the
    cost of that trade is a command that fails at the gate instead of at the
    probe, one layer later and with the trust store already passed.
    """
    parts = command.split()
    if not parts:
        return False, "empty command"
    if shutil.which(parts[0]) is None:
        return False, f"{parts[0]} is not on PATH"
    if os.path.basename(parts[0]).startswith("python") and "-m" in parts[:3]:
        module = parts[parts.index("-m") + 1:parts.index("-m") + 2]
        if module:
            top = module[0].split(".", 1)[0]
            if not _MODULE_NAME.match(top):
                return False, f"{parts[0]} cannot import {module[0]}"
            try:
                with tempfile.TemporaryDirectory() as elsewhere:
                    ok = subprocess.run(
                        [parts[0], "-c", _PROBE, top], capture_output=True,
                        timeout=20, cwd=elsewhere,
                    ).returncode == 0
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                return False, f"could not probe {module[0]}: {exc}"
            if not ok:
                return False, f"{parts[0]} cannot import {module[0]}"
    return True, ""


def runnable(command):
    return availability(command)[0]
