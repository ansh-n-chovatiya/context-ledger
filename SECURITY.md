# Security policy

`ctx` is a local developer tool. It writes to a `.ctx/` directory in your
repository, shells out to `git`, and — through its hooks, gates and dispatch
tiers — runs commands that arrive as *data*: verify commands recorded in a plan
file, a command in a hook, a spec pulled in from a branch you fetched. A
malicious or merely careless plan file is therefore a plausible attack surface,
and reports about it are wanted.

## Reporting a vulnerability

**Use GitHub private vulnerability reporting:**

<https://github.com/ansh-n-chovatiya/context-ledger/security/advisories/new>

That form is the disclosure channel for this project. It is deliberately the
only one: this project publishes no security mailbox, so please do not send
reports to any address you find in a commit trailer or a package manifest —
those are authorship metadata, not a monitored intake, and a report sent there
may simply never be read.

Please do **not** open a public issue for a suspected vulnerability. Public
issues are the right place for a bug; they are the wrong place for a working
exploit against people who have not upgraded yet.

Include, as far as you can:

* the version (`ctx --version`) and platform,
* what an attacker gets and what they need in order to get it,
* a reproduction — a minimal `.ctx/` tree or plan file is ideal.

## Response window

| Stage | Target |
| --- | --- |
| Acknowledgement that the report was received | within **3 business days** |
| An initial assessment — accepted, needs more information, or not a vulnerability | within **10 business days** |
| A fix or a documented mitigation for an accepted report | within **90 days** of the assessment |

These are targets, not a contractual SLA. This is a small project maintained by
one person; if a deadline is going to slip you will be told, rather than left
waiting. If a report gets no acknowledgement inside the window, treat that as a
failure of the channel and escalate by opening a *non-detailed* public issue
that says only "a private security report is awaiting acknowledgement".

## Supported versions

Only the latest released minor version receives security fixes. Fixes ship in a
new patch release from `main`; there are no long-term-support branches and no
backports to earlier minors.

| Version | Supported |
| --- | --- |
| Latest released minor (see `.claude-plugin/plugin.json`) | Yes |
| Any earlier version | No — upgrade |

Because the plugin is stdlib-only, upgrading is a `git pull` or a reinstall of
the plugin; there is no dependency tree to reconcile.

## Disclosure

Accepted reports are disclosed through a GitHub Security Advisory once a fix is
released, crediting the reporter unless anonymity is requested. If a report is
declined you will be told why, so you can disagree.

## What is out of scope

* Anything that requires an attacker to already have arbitrary code execution as
  your user. `ctx` cannot defend a machine that is already lost.
* The fact that `ctx` runs commands you have written into your own `.ctx/`
  files. That is the tool's purpose. The interesting reports are about commands
  arriving from somewhere you did not intend — a merged branch, a fetched plan,
  an unvalidated field.
* Findings against a version that is not the latest released minor.
