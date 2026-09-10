---
ctx_schema: 1
task: stop-the-new-exit-codes-aborting-four-slash-commands
level: 1
status: done
created: 2026-09-10
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Objective

`/ctx:status`, `/ctx:resume`, `/ctx:drop` and `/ctx:context` keep working in a
project with no `.ctx/`, and the README stops making two claims that are false.

## Acceptance criteria

1. Every `commands/*.md` file whose body contains a `!` line ends that line with
   `|| true`. Four files need it today: `context.md`, `drop.md`, `resume.md`,
   `status.md`. `save.md` has no `!` line and needs no change.
2. A test asserts criterion 1 across *all* command files by parsing them, so a
   command file added later without `|| true` fails the suite. It must not be a
   substring grep a prose comment could satisfy.
3. Positive control: remove `|| true` from one command file, run the suite, and
   record verbatim which test died and its assertion message. Then restore it.
4. README's claim that "each command file ends its `!` line with `|| true`" is
   corrected or made true. It was already false for 5 of 22 files *before* this
   wave — criterion 1 makes it true, so verify rather than reword if it now holds.
5. README's claim near lines 832-835 that "having nothing to do yet (no ledger,
   no active task, no plan, no name given) exits 0 and says so" is corrected: the
   *no ledger* case now exits 2. The other three still exit 0 and are the
   advisory set, so the sentence needs splitting, not deleting.
6. CHANGELOG records the breaking change from the previous commit, using the
   follow-up text already drafted in that unit's report.
7. `python3 -m unittest discover -s tests` is green and the count has not dropped
   below 817. `ctx doctor` and `ctx ci` exit 0.

## Notes

Why this is urgent rather than tidy: a non-zero exit from a slash command's `!`
line kills the command before its prompt body is read. The `HARD_FAIL` allowlist
removed in commit e66da96 existed specifically to prevent that, and its own
comment said so. Removing it was correct — it was also masking that these four
command files never carried the guard the other 17 do.

Do not weaken the exit codes to fix this. The fix belongs in the command files.

Also worth a warning wherever `--strict` gets documented: an exported
`CTX_STRICT=1` makes a bare `/ctx:task` exit 1 and abort the same way.
