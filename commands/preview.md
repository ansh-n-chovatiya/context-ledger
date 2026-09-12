---
description: Render the plan as a page a non-technical reviewer can read and approve
allowed-tools: Bash
argument-hint: [«plan»] [--data] [--check] [--open] [--scaffold-plain]
---
!`"${CLAUDE_PLUGIN_ROOT}/bin/ctx" preview $ARGUMENTS || true`

The line above names a **preview page**: one self-contained HTML file, beside
the plan, that says what the work is and why in plain words. It has no network
in it and no build step — the reviewer opens the file.

`ctx plan-check` writes this page on every run, so most of the time it already
exists and this command has just refreshed it. Say where it is in one line and
stop. Do not read the page back: it is a rendering of `plan.json` and the unit
files, and pulling it into this session buys nothing the plan did not already
say.

The page reads its plain-language half out of `plain.md` beside the plan. Any
part nobody has filled in shows as *"Nobody has written this down yet."* — that
is the form working as intended, not a fault to repair. **Do not write that
file on the user's behalf.** The five fields ask what the work is for and why it
matters, and an agent answering them is an agent grading its own homework; the
whole point of the page is that a human said it.

If the user asks for a form to fill in, run
`ctx preview «plan» --scaffold-plain`. It refuses to overwrite an existing
`plain.md` — offer `--force` only if they say plainly that they want what is
there now thrown away.

`--check` holds the file on disk against the plan and exits non-zero if the page
has lost something: a step, a file it declares, an acceptance criterion, or a
line of somebody's prose. Use it after hand-editing the page, and take a
non-zero exit as a real answer — re-render rather than arguing with it.

`--data` writes the same model as JSON, for a script. `--open` asks the machine
for a browser and prints the path instead when there is none.
