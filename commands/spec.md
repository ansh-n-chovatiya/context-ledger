---
description: Turn an intent into checkable criteria, asking before assuming
allowed-tools: Bash, Read, Grep, Glob, Edit, AskUserQuestion
argument-hint: «short-name» — «what you want»
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" spec "$ARGUMENTS" || true`

If it reported that no name was given, ask what to call this piece of work and
what they want, run it again, then continue.

Now do the work this command exists for: **surface what you would otherwise assume.**

1. **Read enough of the repo** to know what the request touches. Delegate broad
   exploration to a subagent — you need conclusions, not file contents.

2. **Write the spec file.** Intent as one paragraph. Then acceptance criteria that
   are *checkable*: name the observable, not the implementation. "Handles errors"
   is not a criterion; "a failed refresh surfaces AuthExpiredError, never a raw
   network error" is. Add an **Out of scope** section — it is often the most
   valuable part, because it is where silent scope creep gets caught.

3. **Write down every question whose answer would change what you build.** Use
   `ctx question <slug> "..."` for blocking ones, `--non-blocking` for the rest.
   Be honest about which is which: a blocking question is one where guessing wrong
   means rework.

4. **Before asking, run the same judgment over three fixed categories this
   system checks for**: *why now*, *what could go wrong*, and *what changes
   for you*. For each, draft a candidate answer from the intent/ticket you
   were given.
   - If you're confident the draft is right, record it without asking:
     `ctx infer <slug> "<category>" "<answer>" --because "<one line: what in
     the ticket/intent makes you confident>"`.
   - If you're not confident, add it as a real question (blocking, since
     `ctx spec-ready` will not proceed without it) and offer your draft as
     the recommended option when you ask.
   Do this for all three every time — `ctx spec-ready` refuses to let
   planning start while any of them is unaddressed, the same way it refuses
   on an open blocking question.

5. **Ask them.** Use AskUserQuestion, recommending an option where you have a
   view. Then record each answer:
   `ctx resolve <slug> --question "<substring>" --answer "<what they chose>"`

Do not write implementation code in this command. The spec is not ready to plan
while any blocking question is open, or while any of the three intake categories
above is unaddressed — `ctx spec-ready <slug>` is the check, and it exits non-zero
until both are clear.

Once the spec is ready, `/ctx:plan` decomposes it into independently
dispatchable units — and refuses to run while any blocking question is open.

If the request is genuinely unambiguous and small, say so and suggest
`/ctx:task` instead. A spec for a two-line fix is how this system gets abandoned.
