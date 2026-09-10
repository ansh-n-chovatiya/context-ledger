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

from . import plan as plan_mod, verify

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


def path_for(layout, unit_name):
    return worktree_root(layout) / unit_name


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


LEDGER_PREFIX = ".ctx/"


def _is_ledger(path):
    """Ledger bookkeeping, excluded from merge preflight.

    Found the hard way: every `ctx` command appends to the journal and flips unit
    `status:` fields, so the integration tree is *never* clean and `ctx merge`
    could never run. Excluding `.ctx/` is not a workaround — those files are
    merge-safe by construction (append-only journal partitioned by date, one file
    per unit, immutable ADRs), which is exactly what §03 of the design claimed.
    """
    return str(path).replace("\\", "/").startswith(LEDGER_PREFIX)


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
    path = path_for(layout, unit_name)
    branch = branch_for(plan_slug, unit_name)

    if path.is_dir():
        code, _ = git(["rev-parse", "--verify", branch], root)
        if code == 0:
            return path, branch, False, ""
        return path, branch, False, (
            f"{path} exists but branch {branch} does not — remove the directory"
        )

    try:
        worktree_root(layout).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The documented contract is `(path, branch, created, error)` and never an
        # exception: one unwritable runtime directory must not abort the dispatch
        # of every other unit in the wave.
        return None, branch, False, f"cannot create {worktree_root(layout)}: {exc}"

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
    """The branch this worktree is actually on, according to git."""
    for name, _path, branch in listing(layout):
        if name == unit_name:
            return branch
    return ""


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
    """
    root = repo_root(layout)
    path = path_for(layout, unit_name)
    branch = branch_for(plan_slug, unit_name) if plan_slug else branch_of(layout, unit_name)

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
    path = path_for(layout, branch.rsplit("/", 1)[-1])
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
            layout, config, unit.checks, cwd=path_for(layout, unit_name),
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
                f"{path_for(layout, unit_name)} merge {target}`), resolve the "
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
