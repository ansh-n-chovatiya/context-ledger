"""The worktree tier: physically separate trees for units that write.

Two units in the same wave own disjoint paths, so they cannot conflict *in
principle*. Worktrees make that true *in practice* — each writing unit gets its
own checkout and branch, so a unit that misbehaves is discarded by deleting a
directory rather than untangled out of a shared tree.

One correction to the original design, found while implementing it: that design
said "sequential fast-forward merges in wave order". That is wrong for the second
merge onward — once the first branch lands, the integration branch has moved and
the next branch is no longer a fast-forward. This uses a real merge commit
instead. A conflict is still exceptional — if `owns` sets were disjoint and the
units honoured them, git has nothing to reconcile — but the second correction is
that "exceptional" is not the same as "the unit's fault": the integration branch
moving under a worktree produces the same conflict, and blaming the unit for it
sends the reader auditing the wrong thing. `merge` distinguishes the two.

The third correction is where a merge lands. `git merge` at the repo root merges
into whatever HEAD happens to be, which on a detached HEAD means the merge commit
is reachable from nothing — and the unit branch, the only other ref to that work,
is deleted a moment later. So the fork point is recorded per unit at creation
time, and `merge` refuses both a detached HEAD and a branch that is not the one
the unit was planned against.
"""

import os
import subprocess

from . import paths, plan as plan_mod, verify

WORKTREE_SUBDIR = "worktrees"
BRANCH_PREFIX = "ctx/"
REF_PREFIX = "refs/heads/"


def git(args, cwd, timeout=60):
    """(returncode, combined_output). Never raises."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout,
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
    except FileNotFoundError:
        return 127, "git is not installed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"git failed: {exc}"


def repo_root(layout):
    return layout.root.parent


def worktree_root(layout):
    return layout.runtime / WORKTREE_SUBDIR


def path_for(layout, plan_slug, unit_name):
    """`.ctx/runtime/worktrees/<plan_slug>/<unit_name>`.

    `plan_slug` is required, and positional rather than optional on purpose: the
    defect this partition fixes is that *every* caller omitted the plan, so an
    optional parameter would have preserved the collision for everyone who did
    not opt in. Two plans with a unit called `01-api` — numbered kebab names make
    that likely, not exotic — used to share one directory, and discarding one
    unit deleted the other's tree and the uncommitted work inside it.
    """
    if not plan_slug:
        raise ValueError(
            "path_for needs a plan slug — a worktree path is not knowable from "
            "the unit name alone"
        )
    return worktree_root(layout) / plan_slug / unit_name


def legacy_path_for(layout, unit_name):
    """Where a worktree made before the partition sits: `worktrees/<unit_name>`."""
    return worktree_root(layout) / unit_name


def split_branch(branch):
    """`ctx/<plan-slug>/<unit>` -> `(plan_slug, unit_name)`, or `("", branch)`.

    Splits off the `ctx/` prefix from the left and the unit from the right, so a
    plan slug with hyphens in it — every real one — survives the round trip.
    """
    if not branch.startswith(BRANCH_PREFIX):
        return "", branch
    rest = branch[len(BRANCH_PREFIX):]
    if "/" not in rest:
        return "", rest
    slug, unit_name = rest.rsplit("/", 1)
    return slug, unit_name


def branch_for(plan_slug, unit_name):
    return f"ctx/{plan_slug}/{unit_name}"


def current_branch(layout):
    """(branch, problem) for the repo root. A detached HEAD is a problem, not a name.

    Nothing in the merge path used to ask this, so `ctx merge` merged into
    whatever HEAD happened to be — including no branch at all.
    """
    code, output = git(["symbolic-ref", "-q", "--short", "HEAD"], repo_root(layout))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if code != 0 or not lines:
        return "", (
            "HEAD is detached — it is not on a branch, so a merge commit made here "
            "would be reachable from nothing once the unit branch is deleted"
        )
    return lines[0], ""


def check_repo(layout):
    """"" when the repo can host worktrees, else why not."""
    root = repo_root(layout)
    code, output = git(["rev-parse", "--is-inside-work-tree"], root)
    if code == 127:
        return "git is not installed — the worktree tier needs it"
    if code != 0:
        return "not a git repository — use tier `subagent` instead"
    code, output = git(["rev-parse", "HEAD"], root)
    if code != 0:
        return "repository has no commits yet — commit once before dispatching"
    return ""


def _is_ledger(path):
    """Ledger bookkeeping, excluded from merge preflight.

    Found the hard way: every `ctx` command appends to the journal and flips unit
    `status:` fields, so the integration tree is *never* clean and `ctx merge`
    could never run. Excluding `.ctx/` is not a workaround — those files are
    merge-safe by construction (append-only journal partitioned by date, one file
    per unit, immutable ADRs), which is exactly what §03 of the design claimed.
    """
    return str(path).replace("\\", "/").startswith(paths.LEDGER_PREFIX)


def dirty_paths(layout):
    """Uncommitted changes that would entangle a merge, ignoring ledger writes."""
    changed, error = verify.changed_files(repo_root(layout))
    return [path for path in changed if not _is_ledger(path)], error


def _record_fork_point(layout, plan_slug, unit_name):
    """Write the branch HEAD is on into the unit file, as `base_branch`.

    Creation time is the only moment the fork point is knowable. By merge time
    the user may have checked out anything, and `merge` deletes the unit branch
    the instant it lands — so merging into the wrong base is not a mistake you
    undo by moving a ref back, it is one that leaves the work unreachable.
    Recorded per unit rather than per plan because a plan can be dispatched in
    waves days apart, from different branches.
    """
    if not plan_slug:
        return ""
    branch, _problem = current_branch(layout)
    if not branch:
        return ""
    unit = plan_mod.find_unit(layout, plan_slug, unit_name)
    if unit is None or unit.doc.meta.get("base_branch") == branch:
        return branch
    # `create` promises never to raise. An unrecorded fork point costs only the
    # merge-time confirmation, which is not worth aborting a dispatch for.
    try:
        unit.set(base_branch=branch)
    except OSError:
        pass
    return branch


def create(layout, plan_slug, unit_name):
    """(path, branch, created, error). Idempotent: an existing worktree is reused."""
    problem = check_repo(layout)
    if problem:
        return None, "", False, problem

    root = repo_root(layout)
    path = _tree_path(layout, plan_slug, unit_name)
    branch = branch_for(plan_slug, unit_name)

    if path.is_dir():
        code, _ = git(["rev-parse", "--verify", branch], root)
        if code == 0:
            return path, branch, False, ""
        return path, branch, False, (
            f"{path} exists but branch {branch} does not — remove the directory"
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The documented contract is `(path, branch, created, error)` and never an
        # exception: one unwritable runtime directory must not abort the dispatch
        # of every other unit in the wave.
        return None, branch, False, f"cannot create {path.parent}: {exc}"

    code, _output = git(["rev-parse", "--verify", branch], root)
    adopted = code == 0
    args = (
        ["worktree", "add", str(path), branch] if adopted
        else ["worktree", "add", "-b", branch, str(path), "HEAD"]
    )
    code, output = git(args, root)
    if code != 0:
        lines = output.strip().splitlines()
        return None, branch, False, (lines[-1] if lines else "git worktree add failed")

    _record_fork_point(layout, plan_slug, unit_name)
    if adopted:
        # Not a fresh branch. Whatever that branch already holds is now this
        # unit's starting point, and `ctx merge` force-deletes the branch once it
        # lands — so a silent `(created)` here is how a unit ends up building on,
        # and then destroying, a stranger's work.
        return path, branch, False, (
            f"branch {branch} already existed, so the worktree starts from it and "
            f"not from HEAD — check it holds what you expect (`git log {branch}`), "
            "then re-run to dispatch the unit"
        )
    return path, branch, True, ""


def _short_branch(ref):
    """`refs/heads/x` -> `x`, stripping the prefix rather than replacing it
    wherever it appears (3.8 floor: no `str.removeprefix`)."""
    return ref[len(REF_PREFIX):] if ref.startswith(REF_PREFIX) else ref


def listing(layout):
    """(unit_name, path, branch) for every ctx-managed worktree git knows about.

    The `ctx/` test is a *prefix* test. It used to be a substring test, which
    claimed a user's own `docs/ctx/readme` branch as a ctx unit named `readme` —
    and `ctx worktree remove readme` then resolved to `git branch -D
    docs/ctx/readme`. That only ever failed to destroy anything because git
    refuses to delete a checked-out branch: the safety was git's, not ours.
    """
    code, output = git(["worktree", "list", "--porcelain"], repo_root(layout))
    if code != 0:
        return []
    out, current = [], {}
    for line in output.splitlines() + [""]:
        if not line.strip():
            branch = _short_branch(current.get("branch", ""))
            if current.get("worktree") and branch.startswith(BRANCH_PREFIX):
                out.append((branch.rsplit("/", 1)[-1], current["worktree"], branch))
            current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = ""
    return out


def branch_of(layout, unit_name):
    """The branch this worktree is actually on, according to git.

    Answers by unit name alone, so it cannot tell two plans' `01-api` apart.
    `remove` resolves the plan first and uses `branch_for`; this is left for the
    case where no directory is left to resolve from.
    """
    for name, _path, branch in listing(layout):
        if name == unit_name:
            return branch
    return ""


def branch_at(layout, path):
    """The branch git says is checked out in `path`, "" if it is not a worktree.

    The tree is the thing being deleted, so its branch is read off the tree and
    not off the name we hoped it had.
    """
    code, output = git(["worktree", "list", "--porcelain"], repo_root(layout))
    if code != 0:
        return ""
    target = os.path.realpath(str(path))
    here = False
    for line in output.splitlines():
        if line.startswith("worktree "):
            here = os.path.realpath(line.split(" ", 1)[1]) == target
        elif here and line.startswith("branch "):
            return _short_branch(line.split(" ", 1)[1])
    return ""


def _tree_path(layout, plan_slug, unit_name):
    """This unit's worktree directory, preferring the plan-scoped layout.

    A tree created before worktrees were partitioned by plan sits flat, at
    `worktrees/<unit>`. Ignoring it would orphan a real checkout with real work
    in it — `create` would try to add a second worktree on a branch git has
    already checked out, and `merge` would stop seeing the uncommitted work that
    a merge silently drops. So a flat directory git still counts as a worktree is
    adopted — but only when git says it has *this unit's* branch checked out. A
    flat tree belonging to another plan is exactly the collision the partition
    exists to end, and adopting it here would let it back in through the side
    door. A leftover directory git has pruned is not adopted either; the
    plan-scoped path is used instead.
    """
    path = path_for(layout, plan_slug, unit_name)
    if path.is_dir():
        return path
    legacy = legacy_path_for(layout, unit_name)
    if (legacy.is_dir() and _registered(layout, legacy)
            and branch_at(layout, legacy) == branch_for(plan_slug, unit_name)):
        return legacy
    return path


def trees_named(layout, unit_name):
    """[(plan_slug, path)] for every worktree directory that could be `unit_name`.

    This is how `remove` answers "which plan?" without being told. Scanning the
    directories rather than the branches is deliberate: a branch may exist with
    no tree, and the tree is what a removal destroys.
    """
    root = worktree_root(layout)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    found = [
        (entry.name, entry / unit_name)
        for entry in entries
        if entry.is_dir() and (entry / unit_name).is_dir()
    ]
    legacy = legacy_path_for(layout, unit_name)
    if legacy.is_dir() and _registered(layout, legacy):
        slug, _unit = split_branch(branch_at(layout, legacy))
        found.append((slug, legacy))
    return found


def remove(layout, unit_name, plan_slug=None, delete_branch=True, force=False):
    """Discard a worktree. This is how a failed unit is thrown away.

    Exactly one branch is deleted, resolved before the tree goes. The previous
    version looped over every directory in `.ctx/plans/` deleting
    `ctx/<plan>/<unit>` for each — so discarding `01-api` in one plan also
    destroyed `01-api` in an unrelated one, and with it any commits that branch
    was the only reference to. Numbered kebab names make that collision likely,
    not exotic.

    Returns the *real* outcome. Both halves used to be reported as success: a
    removal git refused fell through to `branch -D` whenever the directory
    happened to be absent, and the `branch -D` return code was discarded
    entirely, so `ctx worktree remove` printed "removed worktree and branch"
    over a branch that was still there.

    `plan_slug` is still optional, because `ctx worktree remove <name>` is how a
    unit is discarded and the single-plan case should not have to say the plan
    twice. Omitting it now *resolves* rather than guesses: one tree by that name
    is used, several is a refusal naming each plan, none falls back to whatever
    git still registers. The flat `worktrees/<unit>` path the previous layout
    used is resolved too, so a tree left by it is not orphaned.
    """
    root = repo_root(layout)
    if plan_slug:
        path = _tree_path(layout, plan_slug, unit_name)
        branch = branch_for(plan_slug, unit_name)
    else:
        path, branch, problem = _resolve(layout, unit_name)
        if problem:
            return problem

    # The tree, not the name, decides. A directory in `plan-a`'s slot that has
    # some other branch checked out is not this unit's worktree, and deleting it
    # would take work nothing else references.
    checked_out = branch_at(layout, path)
    if checked_out and branch and checked_out != branch:
        return (
            f"{path} has {checked_out} checked out, not {branch} — refusing to "
            "remove a worktree that is not this unit's; nothing was removed"
        )

    args = ["worktree", "remove", str(path)]
    if force:
        args.append("--force")
    code, output = git(args, root)
    if code != 0 and (path.exists() or _registered(layout, path)):
        # Still there: git refused for a reason (uncommitted work, most often),
        # and the branch must survive — it is the only ref to the unit's commits.
        return output.strip() or "git worktree remove failed"
    git(["worktree", "prune"], root)
    if delete_branch and branch:
        code, output = git(["branch", "-D", branch], root)
        if code != 0:
            lines = output.strip().splitlines()
            return (
                f"the worktree is gone but branch {branch} was not deleted: "
                + (lines[-1] if lines else "git branch -D failed")
            )
    return ""


def _resolve(layout, unit_name):
    """(path, branch, problem) for a removal that was not told which plan.

    Exactly one tree by that name: use it. Several: refuse and name them all,
    because picking one is how `ctx worktree remove 01-api` run from plan-b came
    to delete plan-a's tree. None: today's behaviour — git may still register a
    worktree whose directory was taken away behind ctx's back, and the branch it
    holds is still worth deleting.
    """
    matches = trees_named(layout, unit_name)
    if len(matches) > 1:
        plans = ", ".join(sorted(slug or "(unpartitioned)" for slug, _p in matches))
        return None, "", (
            f"{len(matches)} plans have a worktree named {unit_name}: {plans} — "
            f"pass --plan to say which one to discard. Nothing was removed, and "
            "no branch was deleted"
        )
    if matches:
        slug, path = matches[0]
        branch = branch_for(slug, unit_name) if slug else branch_at(layout, path)
        return path, branch, ""
    return legacy_path_for(layout, unit_name), branch_of(layout, unit_name), ""


def _registered(layout, path):
    """Does git still count this path as a worktree?

    A `worktree remove` that failed only because the directory was already gone
    has, in substance, done its job. One git still knows about has not — and the
    difference decides whether the unit's branch may be deleted.
    """
    code, output = git(["worktree", "list", "--porcelain"], repo_root(layout))
    if code != 0:
        return False
    target = os.path.realpath(str(path))
    for line in output.splitlines():
        if line.startswith("worktree ") and \
                os.path.realpath(line.split(" ", 1)[1]) == target:
            return True
    return False


def branch_changes(layout, branch):
    """Files the branch changed relative to where it diverged. (paths, error)."""
    root = repo_root(layout)
    code, output = git(["merge-base", "HEAD", branch], root)
    if code != 0:
        return [], f"cannot find a merge base for {branch}"
    base = output.strip().splitlines()[0] if output.strip() else ""
    code, output = git(["diff", "--name-only", f"{base}..{branch}"], root)
    if code != 0:
        return [], output.strip()
    committed = [
        line.strip() for line in output.splitlines()
        if line.strip() and not _is_ledger(line.strip())
    ]

    # Work left uncommitted inside the worktree would be silently dropped by a
    # merge, so it counts as a change the caller must be told about.
    slug, unit_name = split_branch(branch)
    path = (_tree_path(layout, slug, unit_name) if slug
            else legacy_path_for(layout, unit_name))
    uncommitted = []
    if path.is_dir():
        code, output = git(["status", "--porcelain", "--untracked-files=all"], path)
        if code == 0:
            uncommitted = [
                line[3:].strip().strip('"')
                for line in output.splitlines()
                if line[3:].strip() and not _is_ledger(line[3:].strip().strip('"'))
            ]
    return committed, ("uncommitted work in the worktree: " + ", ".join(uncommitted)
                      if uncommitted else "")


def _conflicted(root):
    """The paths git could not reconcile.

    `git()` returns `(returncode, output)`; unpacking it the other way round is
    how the refusal came to read `merge conflicted ...: 0` — an integer where the
    files should have been, which is the one thing the message existed to name.
    """
    code, output = git(["diff", "--name-only", "--diff-filter=U"], root)
    if code != 0:
        return []
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def _landed_since_fork(root, branch, paths):
    """Which of `paths` the integration branch changed since `branch` forked.

    This is what separates "your branch is behind" from a violated ownership
    contract. The scope check has already established that the unit touched
    nothing outside its `owns`, so a conflict means the *other* side wrote a file
    the unit owns — almost always because a sibling unit landed first or a second
    merge is running concurrently, and only rarely because someone wrote outside
    their scope. The old message asserted the rare case unconditionally and sent
    the reader auditing the wrong thing.
    """
    if not paths:
        return []
    code, output = git(["merge-base", "HEAD", branch], root)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if code != 0 or not lines:
        return []
    code, output = git(["diff", "--name-only", f"{lines[0]}..HEAD"], root)
    if code != 0:
        return []
    changed = set(line.strip() for line in output.splitlines() if line.strip())
    return [path for path in paths if path in changed]


def merge(layout, config, plan_slug, unit_name, skip_gate=False):
    """Preflight, then merge. (ok, messages). Nothing merges past a failed gate."""
    root = repo_root(layout)
    messages = []

    problem = check_repo(layout)
    if problem:
        return False, [problem]

    unit = plan_mod.find_unit(layout, plan_slug, unit_name)
    if unit is None:
        return False, [f"no unit {unit_name!r} in plan {plan_slug}"]

    branch = branch_for(plan_slug, unit_name)
    code, _ = git(["rev-parse", "--verify", branch], root)
    if code != 0:
        return False, [f"no branch {branch} — was this unit dispatched to a worktree?"]

    # 1. Where the merge would land. This is the data-loss guard: a merge commit
    #    is reachable only from HEAD, and step 5 deletes the unit branch the
    #    moment the merge succeeds. On a detached HEAD that leaves the unit's
    #    work with nothing pointing at it at all, while `ctx status` reports
    #    `done` and `ctx doctor` reports all checks passed.
    target, problem = current_branch(layout)
    if problem:
        return False, [
            problem,
            f"check out a branch first (`git checkout <branch>`), then re-run "
            f"`ctx merge {unit_name}` — {branch} still holds the work",
        ]
    base = str(unit.doc.meta.get("base_branch") or "").strip()
    if base and base != target:
        return False, [
            f"{unit_name} forked from {base}, but HEAD is on {target} — refusing "
            "to merge a unit into a branch it was not planned against",
            f"`git checkout {base}` and re-run to land it there, or set "
            f"`base_branch: {target}` in {unit.path.name} if you mean it to land here",
        ]

    # 2. The integration tree must be clean, or a merge would entangle unrelated work.
    changed, error = dirty_paths(layout)
    if error:
        return False, [error]
    if changed:
        return False, [
            "the integration tree has uncommitted changes: "
            + ", ".join(sorted(changed)[:8]),
            "commit or stash them before merging a unit",
        ]

    # 3. Ownership: the branch may only have touched what the unit declared.
    touched, warning = branch_changes(layout, branch)
    if warning:
        return False, [warning, "commit inside the worktree, then merge"]
    if not touched:
        return False, [f"{branch} changed nothing — nothing to merge"]
    stray = [p for p in touched if not plan_mod.covers_any(p, unit.owns)]
    if stray:
        return False, [
            f"{unit_name} modified files outside its `owns` scope: "
            + ", ".join(sorted(stray)),
            "that breaks the isolation its siblings relied on — discard the worktree "
            f"(`ctx worktree remove {unit_name} --force`) or widen `owns` and re-plan",
        ]

    # 4. The done-gate, run inside the worktree so it judges the unit's own tree.
    if skip_gate:
        # An override that records "skipped" and nothing else throws away the
        # only fact anyone will want later: what was stepped over. Name the
        # checks that did not run.
        skipped = verify.ordered(unit.checks)
        named = "; ".join(verify.label_of(check) for check in skipped[:3])
        messages.append(
            f"warning: --skip-gate overrode the done-gate for {unit_name} — "
            f"{len(skipped)} verify check(s) were not run, so nothing about this "
            "unit was verified before merging"
            + (f": {named}" if named else "")
            + (" (+%d more)" % (len(skipped) - 3) if len(skipped) > 3 else "")
        )
    else:
        checks = verify.ordered(unit.checks)
        if not checks:
            return False, [f"{unit_name} has no verify checks — refusing to merge blind"]
        results, verdict = verify.run(
            layout, config, unit.checks,
            cwd=_tree_path(layout, plan_slug, unit_name),
            key=f"merge-{unit_name}", owns=unit.owns, recorded=unit.recorded,
            judged=False,
        )
        if verdict == verify.FAIL:
            return False, [
                f"the done-gate failed in {unit_name}'s worktree — not merging",
                verify.summarise(results),
            ]
        if verdict == verify.PENDING:
            return False, [
                f"{unit_name} has judged checks awaiting sign-off — not merging",
                verify.summarise(results),
            ]
        if verdict == verify.ERROR and not any(
            r.status == verify.PASS for r in results
        ):
            # Nothing ran. `ctx unit --status done` refuses here for the same
            # reason, and a merge that did not would be a complete route around
            # that refusal — on the default configuration, no less: with
            # `PROFILES["code"]` carrying only `cmd` checks, a machine that has
            # never run `ctx trust` errors *every* check. This branch used to
            # warn, merge, and then set `status="done"`, so the unit came out
            # done with zero checks executed. Name the configuration problem and
            # refuse anyway.
            return False, [
                f"not one check could run in {unit_name}'s worktree, so nothing "
                "about this unit was verified — not merging",
                *[result.line() for result in results],
                "That is a configuration problem, not a work failure — often a "
                "command this machine has never accepted (`ctx trust`), or a "
                "missing tool. But an ungated unit is not a done unit: ungated "
                "is not done.",
                "Fix the configuration and re-run, or pass --skip-gate to "
                "override.",
            ]
        if verdict == verify.ERROR:
            # Some check did reach PASS, so the gate is not blind — today's
            # behaviour, kept deliberately. The refusal above is for *no check
            # reached PASS*, never for *any check errored*.
            messages.append(
                "warning: not every check could run in the worktree "
                "(configuration, not work) — the gate signed nothing"
            )
        else:
            messages.append(f"gate passed in {unit_name}'s worktree")

    # 5. Merge — into `target`, named out loud, because a merge that lands
    #    somewhere unexpected is expensive to undo.
    messages.append(
        f"merging into {target}" if base else
        f"merging into {target} (no fork point recorded — this worktree predates "
        "`base_branch`)"
    )
    code, output = git(
        ["merge", "--no-ff", "--no-edit", "-m",
         f"Merge unit {unit_name} of plan {plan_slug}", branch],
        root,
    )
    if code != 0:
        conflicts = _conflicted(root)
        landed = _landed_since_fork(root, branch, conflicts)
        git(["merge", "--abort"], root)
        named = ", ".join(conflicts) if conflicts else "(git named no paths)"
        if landed:
            detail = [
                f"{unit_name} is behind {target}: {', '.join(landed)} changed on "
                f"{target} since this worktree forked. A sibling unit landing "
                "first, or a second merge running concurrently, is the usual "
                "cause — not this unit",
                f"bring the unit up to date in its own tree (`git -C "
                f"{_tree_path(layout, plan_slug, unit_name)} merge {target}`), "
                "resolve the "
                "conflict there, then merge again",
            ]
        else:
            detail = [
                f"nothing on {target} touched those paths since {unit_name} forked "
                "and ownership was disjoint, so git had nothing to reconcile — one "
                "unit wrote outside its scope",
            ]
        last = output.strip().splitlines()[-1] if output.strip() else ""
        return False, [f"merge conflicted in {named}"] + detail + [
            line for line in ["Merge aborted; nothing changed.", last] if line
        ]

    messages.append(f"merged {branch} into {target} ({len(touched)} file(s))")
    error = remove(layout, unit_name, plan_slug)
    messages.append(
        f"cleanup incomplete: {error}" if error else "worktree and branch removed"
    )
    unit.set(status="done")
    messages.append(f"{unit_name}: done")
    return True, messages
