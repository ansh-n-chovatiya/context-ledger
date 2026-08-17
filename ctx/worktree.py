"""The worktree tier: physically separate trees for units that write.

Two units in the same wave own disjoint paths, so they cannot conflict *in
principle*. Worktrees make that true *in practice* — each writing unit gets its
own checkout and branch, so a unit that misbehaves is discarded by deleting a
directory rather than untangled out of a shared tree.

One correction to the original design, found while implementing it: that design
said "sequential fast-forward merges in wave order". That is wrong for the second
merge onward — once the first branch lands, the integration branch has moved and
the next branch is no longer a fast-forward. This uses a real merge commit
instead, and treats **any conflict as a violated ownership contract**: if `owns`
sets were disjoint and the units honoured them, git has nothing to reconcile.
"""

import subprocess

from . import plan as plan_mod, verify

WORKTREE_SUBDIR = "worktrees"


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

    worktree_root(layout).mkdir(parents=True, exist_ok=True)
    code, output = git(["rev-parse", "--verify", branch], root)
    args = (
        ["worktree", "add", str(path), branch] if code == 0
        else ["worktree", "add", "-b", branch, str(path), "HEAD"]
    )
    code, output = git(args, root)
    if code != 0:
        return None, branch, False, output.strip().splitlines()[-1:] and \
            output.strip().splitlines()[-1] or "git worktree add failed"
    return path, branch, True, ""


def listing(layout):
    """(unit_name, path, branch) for every ctx-managed worktree git knows about."""
    code, output = git(["worktree", "list", "--porcelain"], repo_root(layout))
    if code != 0:
        return []
    out, current = [], {}
    for line in output.splitlines() + [""]:
        if not line.strip():
            if current.get("worktree") and "ctx/" in current.get("branch", ""):
                path = current["worktree"]
                branch = current["branch"].replace("refs/heads/", "")
                out.append((branch.rsplit("/", 1)[-1], path, branch))
            current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = ""
    return out


def remove(layout, unit_name, delete_branch=True, force=False):
    """Discard a worktree. This is how a failed unit is thrown away."""
    root = repo_root(layout)
    path = path_for(layout, unit_name)
    args = ["worktree", "remove", str(path)]
    if force:
        args.append("--force")
    code, output = git(args, root)
    if code != 0 and path.exists():
        return output.strip()
    git(["worktree", "prune"], root)
    if delete_branch:
        for plan_dir in (layout.plans.iterdir() if layout.plans.is_dir() else []):
            branch = branch_for(plan_dir.name, unit_name)
            git(["branch", "-D", branch], root)
    return ""


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

    # 1. The integration tree must be clean, or a merge would entangle unrelated work.
    changed, error = dirty_paths(layout)
    if error:
        return False, [error]
    if changed:
        return False, [
            "the integration tree has uncommitted changes: "
            + ", ".join(sorted(changed)[:8]),
            "commit or stash them before merging a unit",
        ]

    # 2. Ownership: the branch may only have touched what the unit declared.
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

    # 3. The done-gate, run inside the worktree so it judges the unit's own tree.
    if not skip_gate:
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
        if verdict == verify.ERROR:
            messages.append(
                "warning: no check could run in the worktree (configuration, not work)"
            )
        else:
            messages.append(f"gate passed in {unit_name}'s worktree")

    # 4. Merge. Disjoint ownership means a conflict is a violated contract.
    code, output = git(
        ["merge", "--no-ff", "--no-edit", "-m",
         f"Merge unit {unit_name} of plan {plan_slug}", branch],
        root,
    )
    if code != 0:
        conflicts, _ = git(["diff", "--name-only", "--diff-filter=U"], root)
        git(["merge", "--abort"], root)
        return False, [
            f"merge conflicted, so an ownership contract was violated: {conflicts}",
            "ownership was disjoint, so git had nothing to reconcile — one unit wrote "
            "outside its scope. Merge aborted; nothing changed.",
            output.strip().splitlines()[-1] if output.strip() else "",
        ]

    messages.append(f"merged {branch} ({len(touched)} file(s))")
    error = remove(layout, unit_name)
    messages.append(
        f"worktree kept (could not remove: {error})" if error
        else "worktree and branch removed"
    )
    unit.set(status="done")
    messages.append(f"{unit_name}: done")
    return True, messages
