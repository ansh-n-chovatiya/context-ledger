"""The security-critical primitives behind the plan preview page.

Pure functions. No I/O, no clock, no knowledge of plans — a caller decides what
prose exists; this module decides what is safe to put in a browser.

**Why the paranoia.** The artefact these build is a committed HTML file that
somebody double-clicks and reads from `file://`. There is no server, so there
is no Content-Security-Policy to fall back on, and the prose arrives from unit
contracts that may have been authored in a shared repository by somebody else.
If a `<script>` survives this module it runs, in a local-file origin, in front
of the one reviewer who is least equipped to notice.

Four invariants, each of which has a control in
`tests/test_preview_html_safety.py`:

1. **Escape first, mark up second.** Every string is HTML-escaped, and only the
   already-escaped text is handed to the markdown subset. The subset then emits
   the only `<` characters in the output — its own fixed tag names, which carry
   no attributes at all. Reverse the two and `` `<b>` `` either loses its
   `<code>` wrapper or renders as real bold; both are the same bug.
2. **Redact before escaping, and only over prose.** `redact.scrub`'s value
   class excludes `&`, so scrubbing text that has already been escaped matches
   nothing: `'"password": "hunter2"'` sails through once its quotes are
   `&quot;`. Scrubbing therefore runs on the raw text, and the placeholder it
   leaves is escaped afterwards, so a reader *sees* `<<redacted>>` instead of
   the browser swallowing it as an unknown element.
3. **Guard the ledger's own vocabulary.** `redact.scrub` is tuned for the
   journal, where recall matters more than a mangled sentence. Here it is
   irreversible *and* read by a human: unguarded it rewrites
   `budget_tokens: 60000` to `budget_tokens: <<redacted>>` and turns
   `credential: none` into `credential: <<redacted>>`, which inverts the
   meaning of the sentence a reviewer is being asked to approve. Both are
   recorded incidents, not hypotheticals. So assignment values that are purely
   numeric, or that are an explicit negation (`none`, `n/a`, `unset`, `false`),
   are held back from the scrubber — unless the key is itself literally a
   credential name, where a numeric value is still treated as a secret.
   The protection covers house patterns too, which is the one place it is
   arguably too broad; a house pattern that must eat a bare number should be
   applied by its author before the text reaches here.
4. **Deterministic bytes.** Sorted keys, fixed separators, no clock and no
   identity in the output. The page is committed, so a second run that
   produces different bytes is a spurious diff on every reviewer's branch.

Redaction deliberately does *not* run inside `embed_json`. What prose enters
the model is the caller's decision, and scrubbing structured values a second
time is exactly how `budget_tokens` was eaten the first time.
"""

import html
import json
import re

from . import redact

# The complete markdown vocabulary of the page. Anything not named here is
# prose: no links, no images, no headings, no tables, no raw HTML. Adding a
# member means adding an element name to the page's grammar, so this tuple and
# the renderer are checked against each other by test.
SUBSET = ("paragraph", "list", "fence", "code", "bold", "italic")

# ---------------------------------------------------------------------------
# Sanitising: characters that are hostile before any markup question arises
# ---------------------------------------------------------------------------
# Lone surrogates cannot be UTF-8 encoded, so one reaching the writer turns the
# whole page into a `UnicodeEncodeError` at the last possible moment. Control
# characters are invisible in the source and meaningful to some parsers. Bidi
# overrides let `invoice<U+202E>gnp.exe` display as `invoiceexe.png` — a spoof
# aimed squarely at a reader approving a list of file names.
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_LINE_SEPARATOR = re.compile(r"[\u2028\u2029]")


def _text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _sanitize(value):
    out = _text(value).replace("\r\n", "\n").replace("\r", "\n")
    out = _LINE_SEPARATOR.sub("\n", out)
    out = _SURROGATE.sub("\ufffd", out)
    out = _CONTROL.sub("", out)
    return _INVISIBLE.sub("", out)


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

def escape(text):
    """HTML-escape `&<>"'` and do nothing else.

    No stripping, no normalising, no entity re-interpretation: an input that is
    already escaped is escaped again, because it is text, and text is what a
    reader must see.
    """
    return html.escape(_text(text), quote=True)


# A backtick is escaped in attribute context only: older IE treated it as a
# quote character, and an attribute value is the one place that mattered.
def attr(value):
    """Escape `value` for use inside a double-quoted HTML attribute."""
    out = _sanitize(value).replace("\n", " ").replace("\t", " ")
    return escape(out).replace("`", "&#x60;")


# ---------------------------------------------------------------------------
# Redaction, with the ledger's vocabulary held back
# ---------------------------------------------------------------------------

# Values that are never a credential, and whose removal changes what the
# sentence means rather than merely hiding a secret.
_NEGATED = frozenset({
    "none", "null", "nil", "na", "n/a", "unset", "empty", "unknown",
    "true", "false", "yes", "no", "tbd", "-", "--",
})
# Keys that *are* the word "secret": here a numeric value gets no protection,
# so `password: 1234` is still scrubbed. `budget_tokens` is not in this set
# and is not meant to be — the key has to be the credential name itself.
_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "pass", "secret", "secrets", "api_key",
    "apikey", "token", "access_key", "secret_key", "private_key",
    "auth_token", "credential", "credentials", "key",
})
_NUMERIC = re.compile(r"[-+]?\d[\d,._]*\Z")
_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<key>[\"']?[A-Za-z0-9_.\-]{1,64}[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[\"']?[A-Za-z0-9_./+,\-]{1,32}[\"']?)"
    r"(?![A-Za-z0-9_])"
)
# `\x01` cannot appear in sanitised text — `_CONTROL` has already removed it —
# so a placeholder built from it cannot collide with anything a hostile author
# wrote, and needs no randomness to be unique.
_SENTINEL = re.compile("\x01(\\d+)\x01")


def _bare(token):
    token = token.strip()
    if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1]
    return token


def _protected(match):
    value = _bare(match.group("value")).lower()
    key = _bare(match.group("key")).lower().replace("-", "_")
    if value in _NEGATED:
        return True
    return bool(_NUMERIC.match(value)) and key not in _SECRET_KEYS


def _redact(text, extra_patterns=()):
    """`redact.scrub`, with numeric and negated assignment values held back."""
    held = []

    def hold(match):
        if not _protected(match):
            return match.group(0)
        held.append(match.group(0))
        return "\x01%d\x01" % (len(held) - 1)

    guarded = _CANDIDATE.sub(hold, text)
    scrubbed = redact.scrub(guarded, extra_patterns)
    if not held:
        return scrubbed
    return _SENTINEL.sub(lambda m: held[int(m.group(1))], scrubbed)


# ---------------------------------------------------------------------------
# The markdown subset, applied to already-escaped text
# ---------------------------------------------------------------------------
#
# Every pattern below runs over text in which `&<>"'` are already entities, so
# none of them can see — let alone reconstruct — a tag from the input. The only
# `<` in the result are the literal tag names written here.

_FENCE = re.compile(r"^\s{0,3}```")
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+(?P<item>.*)$")
_ORDERED = re.compile(r"^\s{0,3}\d{1,3}[.)]\s+(?P<item>.*)$")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(\S(?:[^*\n]*\S)?)\*\*")
_ITALIC_STAR = re.compile(r"(?<![*\w])\*(\S(?:[^*\n]*\S)?)\*(?!\*)")
# An underscore italic needs a non-word character on both outer sides, or the
# ledger's own `budget_tokens and depends_on` becomes one slanted run.
_ITALIC_UNDER = re.compile(r"(?<![A-Za-z0-9_])_(\S(?:[^_\n]*\S)?)_(?![A-Za-z0-9_])")


def _inline(text):
    """Code spans, then bold, then italic — over escaped text only."""
    pieces = _CODE_SPAN.split(text)
    out = []
    for position, piece in enumerate(pieces):
        if position % 2:
            out.append("<code>" + piece + "</code>")
            continue
        piece = _BOLD.sub(r"<strong>\1</strong>", piece)
        piece = _ITALIC_STAR.sub(r"<em>\1</em>", piece)
        piece = _ITALIC_UNDER.sub(r"<em>\1</em>", piece)
        out.append(piece)
    return "".join(out)


def _list_item(line):
    match = _ORDERED.match(line) or _BULLET.match(line)
    return match.group("item").strip() if match else None


def _blocks(text):
    lines = text.split("\n")
    blocks = []
    cursor = 0
    while cursor < len(lines):
        line = lines[cursor]
        if _FENCE.match(line):
            cursor += 1
            body = []
            while cursor < len(lines) and not _FENCE.match(lines[cursor]):
                body.append(lines[cursor])
                cursor += 1
            cursor += 1  # the closing fence, or the end of the text
            blocks.append("<pre><code>" + "\n".join(body) + "</code></pre>")
            continue
        ordered = bool(_ORDERED.match(line))
        if ordered or _BULLET.match(line):
            pattern = _ORDERED if ordered else _BULLET
            items = []
            while cursor < len(lines) and pattern.match(lines[cursor]):
                items.append(_inline(_list_item(lines[cursor])))
                cursor += 1
            tag = "ol" if ordered else "ul"
            blocks.append(
                "<%s>%s</%s>"
                % (tag, "".join("<li>" + item + "</li>" for item in items), tag)
            )
            continue
        if not line.strip():
            cursor += 1
            continue
        paragraph = []
        while cursor < len(lines):
            current = lines[cursor]
            if not current.strip() or _FENCE.match(current):
                break
            if _BULLET.match(current) or _ORDERED.match(current):
                break
            paragraph.append(current.strip())
            cursor += 1
        blocks.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
    return "\n".join(blocks)


def markup(text, extra_patterns=()):
    """Render untrusted prose as safe HTML: sanitize, redact, escape, subset.

    `extra_patterns` carries house redaction patterns (`config["redact"]`)
    through to `redact.scrub`; a pattern that fails to compile is ignored
    there rather than breaking the page.
    """
    sanitized = _sanitize(text)
    if not sanitized.strip():
        return ""
    return _blocks(escape(_redact(sanitized, extra_patterns)))


# ---------------------------------------------------------------------------
# JSON for a <script type="application/json"> block
# ---------------------------------------------------------------------------

def embed_json(obj):
    """Serialise `obj` so it can sit inside a `<script>` element unharmed.

    `<`, `>` and `&` become their `\\uXXXX` escapes, which JSON parses back to
    the same string but an HTML tokeniser cannot read as markup — so a value
    containing `</script>`, `</SCRIPT`, `<!--` or `-->` cannot end the element
    early. `ensure_ascii` handles lone surrogates and U+2028/U+2029 on the way
    past. `allow_nan` is off because `NaN` is not JSON: a page carrying it
    fails at `JSON.parse` in front of the reviewer, where nobody is watching a
    console, so it has to fail here instead.

    Deliberately does not redact: scrubbing is the prose path's job.
    """
    text = json.dumps(
        obj, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    )
    for character, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e")):
        text = text.replace(character, escaped)
    return text
