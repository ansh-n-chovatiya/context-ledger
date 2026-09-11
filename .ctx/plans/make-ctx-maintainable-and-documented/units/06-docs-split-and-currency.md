---
ctx_schema: 1
unit: 06-docs-split-and-currency
plan: make-ctx-maintainable-and-documented
tier: subagent
depends_on:
  - 08-command-bodies
owns:
  - README.md
  - docs/
  - AUDIT.md
  - PRODUCTION-AUDIT.md
  - GUIDE.md
  - commands/trust.md
  - tests/test_docs_currency.py
reads:
  - path: ctx/cli.py
    symbols:
      - COMMANDS
  - path: ctx/verify.py
    symbols:
      - KINDS
      - COST
  - path: ctx/config.py
    symbols:
      - DEFAULTS
forbid:
  - ctx/cli.py
  - ctx/verify.py
  - ctx/config.py
budget_tokens: 90000
status: pending
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
wave: 6
---

## Objective
Document the tool that exists after this wave, split the 1,575-line README into
something maintainable, and make the checkable parts of the documentation
impossible to let rot silently.

## Acceptance criteria

**Currency (item 25).** A grep for these returns **zero** hits in `README.md`
today. Every one ships in the product.

1. Documented, each read **from the code, not from a sibling's report**:
   `/ctx:phase` and the `phase` subcommand; `kind: bug` and its four gated
   phases with the required reproduction command; `test_first`; the `models:`
   block including `tiers` and `escalate_on_failed_round`; the `complexity:`
   block with its weights and thresholds. Wave 3 recorded that a copied-forward
   summary is how a third false claim arrives — verify each against the source.
2. Also document what this wave added: `--json` and its seven shapes (unit 05's
   report lists them), `plan.max_wave_units`, and `ctx telemetry --spend`
   **including that reported spend is partial by construction**.
3. Remaining false claims corrected. `README.md:1153` says the suite runs "on
   macOS, Linux and Windows across Python 3.8–3.13" — only Ubuntu runs the full
   spread. **Already fixed in wave 3, do not re-fix:** the verify-kind count now
   correctly reads eight.

**The split (decided, do not redesign).**
4. `README.md` keeps Why, Requirements, Install, Quickstart and the command
   tables, **under 300 lines**. `docs/reference.md` takes the configuration and
   verify-kind reference. `docs/walkthroughs.md` takes the worked examples.
5. No content is lost in the move — only relocated, corrected, or deliberately
   cut. List anything you cut and why.
6. Every internal link resolves. A test asserts no tracked file links to a
   heading or path that does not exist.

**Archiving (decided).**
7. `AUDIT.md` and `PRODUCTION-AUDIT.md` move to `docs/history/`, each gaining a
   one-line header naming the version it describes and saying it is historical.
   They record real decisions so they are not deleted — but
   `PRODUCTION-AUDIT.md` says "snapshot.py and findings.py: zero tests, zero
   callers" and both now have dedicated suites, which a reader takes as current.
8. `report.md` stays where it is and remains the authoritative audit.
9. A test asserts no tracked file references the old top-level paths.

**`/ctx:trust` (criterion 23 of the spec).**
10. `commands/trust.md` exists. Twenty subcommands have no slash command and
    that is mostly correct — each costs always-on context — but `trust` is the
    security gate `_next_action` advises the user to run, and advising a command
    with no slash command is a dead end.
11. It ends its `!` line with `|| true`, like the other 22. The existing bang-line
    test asserts this and counts at least 21 bang lines; adding a command must
    not break it.

**Anti-rot tests.**
12. A test asserts every registered subcommand appears in a CLI table, every
    file in `commands/` appears in the slash-command table, and the claimed
    verify-kind count equals `len(verify.KINDS)`. These are the checkable
    claims; they are what drifted, and a test is the only thing that stops them
    drifting again.
13. The test must be able to fail: add a synthetic subcommand and show it goes
    red. A documentation test that cannot fail is worse than none.

**All.**
14. `python3 -m unittest discover -s tests -q` passes; `ctx doctor` and `ctx ci`
    exit 0. Do not lower `SUITE_FLOOR` or `REQUIRED_FLOOR`.
15. No file outside `owns` is modified.

## Return contract
Report: files changed · which criteria passed · verbatim verify output ·
README's line count before and after · anything cut rather than moved, and why ·
the red run proving criterion 13 · any claim you found false and could not fix.
