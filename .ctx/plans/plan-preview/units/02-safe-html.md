---
ctx_schema: 1
unit: 02-safe-html
plan: plan-preview
tier: subagent
depends_on: []
owns:
  - ctx/preview_html.py
  - tests/test_preview_html_safety.py
reads:
  - path: ctx/redact.py
    symbols:
      - scrub
      - PLACEHOLDER
  - path: tests/support.py
forbid:
  - ctx/plain.py
  - ctx/preview.py
  - ctx/preview_page.py
  - ctx/commands.py
  - ctx/cli.py
budget_tokens: 55000
status: done
verify:
  - kind: diff
  - kind: symbol
    path: ctx/preview_html.py
    contains:
      - SUBSET
      - def escape(
      - def markup(
      - def embed_json(
      - def attr(
  - kind: review
  - kind: cmd
    run: python3 -m unittest discover -s tests -q
  - kind: cmd
    run: python3 -m ruff check ctx/preview_html.py tests/test_preview_html_safety.py
wave: 1
---

## Objective
`ctx/preview_html.py`: the security-critical primitives that turn untrusted
prose into HTML that is safe to double-click. Pure functions, no I/O, no
knowledge of plans.

## Context you need
The output of this feature is a committed, shareable HTML file opened from
`file://`. Unit prose can arrive from a shared repository, so it is untrusted
input, and the page has no CSP to fall back on. These four functions are the
entire defence.

## Interfaces
Produced — units `03` and `04` code against these:

```python
preview_html.SUBSET = ("paragraph", "list", "fence", "code", "bold", "italic")
preview_html.escape(text) -> str        # &<>"' and nothing clever
preview_html.markup(text) -> str        # scrub, escape, THEN the tiny subset
preview_html.embed_json(obj) -> str     # </script>-safe, sorted, stable
preview_html.attr(value) -> str         # attribute-context escaping
```

## Acceptance criteria

**Escaping**
1. `markup("</script><img src=x onerror=alert(1)>")` produces text containing no
   `<script`, no `<img` and no `onerror`, and renders visibly as that literal
   string. The same holds for `javascript:` and `data:text/html` in link
   position.
2. The markdown subset is exactly `SUBSET`. Raw HTML in the source is escaped,
   never passed through: `<b>x</b>` in comes out as visible text.
3. **Escaping precedes markup**, proven by an input whose output differs if the
   order is reversed — `` `<b>` `` is the canonical case. Write the assertion
   so that swapping the two steps in the implementation makes it fail, then say
   in your report that you checked it does.
4. At least **twenty** hostile inputs, each asserting the output contains no
   unescaped `<` outside tags your own code generated. Include at minimum:
   nested `</script`, `<!--`, `<![CDATA[`, a data URI, a `javascript:` href, an
   SVG `onload`, a null byte, an unpaired surrogate, a very long single token,
   and RTL override characters.

**Embedded JSON**
5. `embed_json` output can sit inside `<script type="application/json">` and
   survive a payload containing `</script>`, `</SCRIPT`, `<!--`, `-->` and lone
   surrogates, parsing back to an identical object.
6. `embed_json` is deterministic: same object in, byte-identical output, keys
   sorted. Asserted by two calls, not by inspection.

**Redaction — the ordering this repo already got wrong once**
7. Redaction runs on the write path, inside `markup`, and it runs **before**
   escaping: `redact.scrub` then `escape` then the subset. An AWS-shaped key and
   `postgres://user:pw@host` each become `<<redacted>>`.
8. **Positive controls, all three required.** An ordinary file path
   (`ctx/preview_html.py`) is not redacted; `budget_tokens: 60000` survives
   intact; `credential: none` survives intact and is **not** inverted. Running
   redaction the other way round eats the first and inverts the last — that is
   a recorded incident in this repo, not a hypothetical.
9. Redaction applies to prose only. `embed_json` does not scrub structured
   values — `03` decides what prose enters the model, and double-scrubbing
   structured data is how `budget_tokens` got eaten before.

**Project rules**
10. Python 3.8 compatible, standard library only (`html`, `json`, `re`), `ruff`
    clean.
11. Every test is shown failing before it passes. Report the failure output.
12. No file outside `owns` is modified.

## Orchestrator rulings on this unit's report

**Criterion 1's wording was wrong; the unit's reading is accepted.** "Contains no
`onerror`" and "renders visibly as that literal string" cannot both hold — the
visible text *is* the payload, and the payload contains the word `onerror`. The
security-meaningful assertion is the one that shipped: no `onerror` in *tag*
context, an `HTMLParser` walk proving the output carries no attributes anywhere,
and `visible(out) == payload`. That is stronger than what was asked for. The
defect was in this contract, not in the implementation.

**`markup(text, extra_patterns=())` is accepted as an additive interface.** The
published form `markup(text)` is unchanged, so units 03 and 04 code against
exactly what they were promised and the wave is not stopped. The seam exists so
`config["redact"]` house patterns can reach `redact.scrub`, which this repo's
recorded redaction note asks for. Keep it.

## Return contract
Report: files changed · each criterion and how it was checked · verbatim verify
output · the list of twenty hostile inputs · confirmation that the
escape-before-markup assertion actually fails when the order is swapped.
