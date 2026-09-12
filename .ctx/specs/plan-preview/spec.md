---
ctx_schema: 1
spec: plan-preview
status: ready
created: 2026-09-12
verify:
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
---

## Intent
A `ctx` plan is readable only by people who already read `plan.json`, unit
contracts and `owns:` globs. There is no artefact a non-technical reviewer can
look at and say "yes, do that" before work starts — and the product's thesis is
that somebody who is not writing prompts by hand can still direct parallel work.
This feature gives every plan a self-contained `preview.html`: plain language,
opens by double-clicking, no account, no server, no network. Substance comes
from an authored `plain.md`; structure comes from the derivation `plan-check`
already performs. The page degrades to structural sentences plus a visible
banner when `plain.md` is unwritten — never to broken.

## Acceptance criteria

**Plain-language source**
1. `plain.load` on a plan with no `plain.md` returns a `Plain` with
   `exists=False`, every section empty and every unit listed in `missing`,
   without raising.
2. A `## Unit: <name>` block's five bolded fields parse with whitespace
   stripped; an unrecognised field is ignored rather than an error; a `## Unit:`
   naming a unit absent from the plan lands in `unknown`.
3. A field present but empty counts as unauthored: it lands in `missing` and
   falls back to generated text, so an unfilled scaffold is never mistaken for
   authored prose.
4. `plain.md` is stale when the plan's **content digest** differs from the one
   stamped in its frontmatter, and when no digest is stamped. The digest is
   derived from the units' substantive fields, not from `plan.json`'s
   `revision` counter — the counter increments on every `plan-check` run and
   would report staleness after a no-op re-check.
5. Generated fallback text is built only from facts — step position, how many
   files change, what it waits for, what runs alongside, how it is checked — and
   never asserts a purpose or a risk level.
6. Generated text contains none of `owns`, `forbid`, `depends_on`, `wave`,
   `tier`, `budget_tokens`, `subagent`, or any path separator. A positive
   control proves the assertion fires when a banned word is introduced.

**Safe HTML**
7. `markup("</script><img src=x onerror=alert(1)>")` renders as that literal
   text and contains no `<script`, `<img` or `onerror`; the same holds for
   `javascript:` and `data:text/html` in link position.
8. Escaping precedes markup, proven by an input whose output differs if the
   order reverses.
9. `embed_json` survives `</script>`, `<!--` and lone surrogates inside a
   `<script type="application/json">` block, parses back to an identical
   object, and is byte-identical run to run with keys sorted.
10. Redaction runs on the write path over prose only, and before escaping: an
    AWS-shaped key and `postgres://user:pw@host` become `<<redacted>>`, while an
    ordinary file path, `budget_tokens: 60000` and `credential: none` survive
    intact.
11. At least twenty hostile inputs each assert no unescaped `<` survives outside
    generated tags.

**View model**
12. Every wave, graph and ownership fact in the view-model comes from the same
    derivation `plan-check --json` uses; a test asserts the two agree on a
    fixture rather than trusting they do.
13. `view_model` returns every unit in wave order, numbered from 1, with
    `alongside` and `waits_for` derived rather than authored.
14. Called twice on unchanged input the model is equal and
    `json.dumps(sort_keys=True)` byte-identical; the module source contains no
    `datetime.now()`.
15. A plan with zero units yields a valid empty model; a missing `plan.json`
    raises a `SystemExit` naming `ctx plan-check`, not a `KeyError`.

**The page**
16. One self-contained document: no `http://`, `https://`, `//cdn`, external
    `<link>`, `fetch(`, `XMLHttpRequest` or `<script src=`.
17. All nine reviewer sections appear in order: heading, what we're going to do,
    why, what will be different, how the work is split up, each step, what could
    go wrong, what we are not doing, how we'll check it worked.
18. Steps are numbered from 1; slugs and banned vocabulary appear only inside
    regions marked technical, asserted by extracting the non-technical DOM.
19. Every step with siblings states its concurrency in words.
20. The technical toggle is CSS-class based: a page rendered with JavaScript
    disabled still shows its content.
21. `prefers-color-scheme: dark` is honoured, both themes set explicit
    background and foreground, and no text falls below 4.5:1 contrast.
22. The print stylesheet expands collapsed regions, so `Cmd-P → PDF` is
    complete.
23. The page renders at 400px with no horizontal page scroll; tables and the SVG
    each sit in their own scroll container.
24. `render(vm)` twice is byte-identical.
25. When `plain.present` is False a visible banner says so and every step still
    renders.
26. `check()` flags a missing unit name, an `owns` path absent from technical
    regions, a criteria count disagreeing with the model, a missing plain entry
    the model has, and any external reference — and returns `[]` for the
    renderer's own output. Every rejection test has a positive control showing
    `[]` once the defect is removed.
27. The page has been opened in a browser at desktop width and 400px, in light
    and dark, with the print preview inspected, and the report says what was
    seen.

**CLI**
28. `ctx preview [slug]` renders and writes `preview.html`, echoing the relative
    path; with no slug it uses the active plan, and with no active plan prints
    the same guidance the other plan commands print and exits 0.
29. `ctx plan-check` writes `preview.html` on every run, so a plan has a
    reviewable page without anyone knowing the command exists — the feature is
    for people who do not read `plan.json`, and a default-off artefact is not
    available to them. If rendering fails, `plan-check` still succeeds and
    prints a `note:`: a preview problem must never block planning.
30. `ctx plan-check` scaffolds `plain.md` when it is absent and appends a blank
    block for any unit that has none, never modifying authored text.
31. `ctx start` prints exactly one advisory line — the preview's path, or that
    it is missing or behind — with no other new output and no exit-code change.
32. `ctx preview --data` writes `preview.data.json`; `--check` runs the checker
    against the file on disk, printing each problem and exiting non-zero when
    there are any; `--open` uses `webbrowser.open` and prints the path rather
    than raising on a headless machine; `--scaffold-plain` writes the form and
    refuses to overwrite without `--force`.
33. `ctx preview --json` is supported, its shape is added to the schema test,
    `plan-check`'s JSON gains the preview path, and the golden is updated in the
    same change.
34. `commands/preview.md` follows house frontmatter, ends its `!` line with
    `|| true`, and appears in the slash-command table.

**Project-wide**
35. Every existing test passes. Any assertion changed is reported with what it
    was pinning, not quietly edited.
36. `SUITE_FLOOR` and `REQUIRED_FLOOR` stay equal and are not lowered; the
    coverage floors likewise stay pinned in pairs.
37. Python 3.8 compatible, standard library only, `ruff` clean, and green on CI
    across the full matrix — not merely on one macOS laptop.
38. `docs/reference.md` documents `ctx preview`, its flags and `plain.md`'s
    format; `docs/walkthroughs.md` shows the flow end to end; README gains at
    most a few lines and a link.
39. CHANGELOG gains a 0.9.0 entry and the version is bumped in **both**
    `ctx/__init__.py` and `.claude-plugin/plugin.json`.
40. `ctx doctor` output and the briefing budget are unchanged by this feature.

## Out of scope
- Any blocking approval gate, or approval state stored anywhere.
- Interactive review: checkboxes, comments, verdicts exported back to the ledger.
- Hosting or uploading. It is a file on disk.
- Previews for L1 tasks or for specs — plans only.
- A bespoke per-plan design. Cut deliberately; `--check` survives without it.
- Localisation.

## Notes
Five things this project learned the hard way, all of them load-bearing here:
show every test failing before it passes; force interleavings rather than
counting survivors; walk the working tree, because the done-gate runs before the
commit; scrub before escaping, prose only; and push to CI early, because a green
local suite says nothing about 3.8, 3.9 or Windows.

`plain.md` is authored, not generated. The feature is exactly as good as those
five sentences per unit.
