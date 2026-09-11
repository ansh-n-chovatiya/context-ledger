# Context Ledger — a plain guide

Claude forgets. Long chats get compacted, sessions end, and the plan you agreed
an hour ago is gone. Context Ledger writes that state to files in your project
instead, so it survives — and it adds a gate that can **refuse to let Claude call
something "done"** until the checks actually pass.

You mostly type slash commands. Claude does the rest.

---

## Setup — once per project

```
/ctx:init
```

That's it. It looks at your project, finds your test command, and creates a
`.ctx/` folder. Commit that folder — it's meant to be shared and reviewed.

After this, nothing changes about how you work. It stays quiet until you ask for
more.

---

## The one idea: three levels

You choose how much process a piece of work deserves. **Most work needs none.**

| Level | Use it when | You get |
|---|---|---|
| **L0** — trace | Default. Anything you'd finish in one sitting. | Nothing in your way. Work is quietly logged. |
| **L1** — tracked | One change with a "done" you can describe. | A checklist Claude can't skip. |
| **L2** — planned | Several independent pieces, or work spanning days. | A spec, a plan, and parallel work. |

The common mistake is jumping to L2. If a spec for a two-line fix sounds absurd,
it is — stay at L0.

Drop back down any time with `/ctx:drop`.

---

## Everyday commands

You will use these four more than all the others combined:

```
/ctx:task «what you want»    start tracking a change, with a done-check
/ctx:verify                  are we actually done?
/ctx:status                  where am I?
/ctx:resume                  what was I doing? (after a break or a new session)
```

---

## A full example

A real task, start to finish: **"users can't reset their password if their email
has a capital letter."**

### 1. Start tracking it

```
/ctx:task fix-password-reset
```

Claude writes `.ctx/tasks/fix-password-reset.md` with what you want and how it
will be checked. You can open that file and read it. It's yours.

### 2. Say what "done" means

Just tell Claude, in normal words:

> Done means: a reset with `Alice@Example.com` works, the existing tests still
> pass, and we haven't broken sign-in.

Claude turns that into a checklist in the file. This matters — it's what the gate
checks later.

### 3. Let Claude work

Nothing special. Work as you normally would.

### 4. Try to finish

When Claude thinks it's done, the gate runs automatically. If the tests fail,
**Claude is not allowed to end the session claiming success.** You'll see:

```
The done-gate blocked completion of `fix-password-reset` (attempt 1 of 3).
  FAIL    cmd: npm test
          1 failing: resets a password for a mixed-case email
```

It tries again. After three failures it stops and explains what's wrong rather
than looping forever.

### 5. Confirm

```
/ctx:verify
```

Shows each criterion and whether it passed. Some checks need a judgement rather
than a test — Claude sends those to a separate reviewer that never saw the code
being written, so it can't mark its own homework.

---

## When the work is bigger

Say it's not one fix — it's "rebuild how billing works". Same start, more
structure:

```
/ctx:spec billing-rework
```

Claude asks questions rather than guessing. **Unanswered questions block
planning** — that's the point. You'll see them with:

```
/ctx:ask
```

Answer them, then:

```
/ctx:plan
```

This splits the work into pieces that don't touch the same files, so several can
run at once safely. Then:

```
/ctx:start
```

Claude dispatches the pieces. Each one gets its own fresh worker with only the
instructions it needs.

### Reviewing the work

```
/ctx:review 01-payments
```

A separate reviewer inspects what changed. It also checks something no human
needs to judge: **did this piece touch files it wasn't supposed to?** That's
answered by comparing before and after, not by asking anyone's opinion.

Anything it finds is recorded:

```
/ctx:findings 01-payments
```

Findings block finishing until they're dealt with. There is deliberately no way
to just *agree* with a finding — you either fix it, disprove it with evidence, or
formally decide to live with it and say why. "Good catch, noted" isn't a state
this tool records, because that's how things get quietly dropped.

**Nothing here needs you to commit anything.** Your changes stay uncommitted in
your normal working folder until *you* decide to commit them.

---

## Conventions worth knowing

**Commit `.ctx/`.** It's designed to be read in pull requests. Your teammates can
see what was decided and why.

**Write decisions down when you make them.**

```
/ctx:decide use short-lived tokens instead of session cookies
```

Saves a short note explaining the choice. Six weeks later, nobody re-argues it.

**Hand off cleanly.**

```
/ctx:handoff
```

Writes a summary another person — or another session — can pick up cold.

**Git is yours.** Context Ledger never commits, merges, or rewrites history on
its own. It only touches git when you explicitly ask, with a command that says so.

**If it says no, it means no.** A blocked gate, an unanswered question, or an open
finding is the tool doing its job. Don't force past it unless you mean to — and if
you do, say so out loud so it's on the record.

---

## When something's wrong

```
/ctx:status     where things stand
/ctx:doctor     check the setup and get told exactly what to fix
/ctx:next       just tell me the single most useful next thing
```

If Claude proposes running a command on your machine that you didn't set up,
it won't run until you review it:

```
ctx trust
```

Shows you the exact command and asks. This exists because a project you cloned
from someone else can carry instructions with it.

---

## The short version

1. `/ctx:init` once.
2. Work normally.
3. `/ctx:task` when a change is worth tracking.
4. Say what "done" means in plain words.
5. Let the gate stop you from shipping something that isn't.

Everything else is there when you need it, and out of the way when you don't.

When you want the detail: [README.md](README.md) for the command tables,
[docs/walkthroughs.md](docs/walkthroughs.md) for the same examples at full size,
[docs/reference.md](docs/reference.md) for every setting.
