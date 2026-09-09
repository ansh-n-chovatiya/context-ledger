"""A strict, tiny YAML subset — enough for ctx.yaml and file frontmatter.

Deliberately not PyYAML: the plugin promises stdlib-only so it adds nothing to
the host project's dependency tree. The subset is:

    key: scalar
    key:
      nested: scalar
    key: [inline, list]
    key: {}
    key:
      - item
      - nested: map
        over: lines
        with:
          - its own block

Scalars are int, float, bool, null, or string (optionally quoted). A quoted
string may carry `\\n`, `\\r`, `\\t` and `\\uXXXX`, which is how a multi-line value
survives a line-oriented format. A key that is not an identifier-ish token is
quoted too, with the same escapes.
Anything outside the subset raises MiniYamlError rather than guessing, so a
malformed config fails loudly at init instead of silently at runtime.

The reader and the writer are one contract, not two features: everything this
module serialises is committed ledger state that gets re-read, edited and
rewritten many times, so `loads(dumps(x)) == x` is the property that matters.
Where the two ever disagreed the damage was silent — a `verify` check whose
`contains` list came back as a string still ran, and matched every character
against the file it was meant to freeze, so the gate went green on a broken
interface. Hence the rule `_needs_quotes` now enforces: quote whenever reading
the bare text back would not return the text.
"""

import re

__all__ = ["loads", "dumps", "MiniYamlError"]

# Deep nesting is a bug or an attack, never a ledger document — the real shapes
# bottom out at four levels. The cap exists because the alternative is
# RecursionError, which is not a MiniYamlError, so `frontmatter.parse` would not
# catch it and a merely deep file would take the whole command down.
MAX_DEPTH = 64


class MiniYamlError(ValueError):
    pass


# A key is an identifier-ish token followed by a colon and then whitespace or
# end-of-line. Requiring the whitespace is what keeps `- https://example.com`
# and `run: pytest -q --tb=short` from being mistaken for nested maps. Leading
# digits are allowed so numeric keys (`0: 220`, `2026-08-17: …`) round-trip.
_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?:\s+(.*))?\Z")
# Anything else — `a b`, `a:b`, `` — has to be written quoted, and so has to be
# readable quoted, or `dumps` would emit files `loads` rejects. `\Z` and not
# `$` throughout: `$` also matches before a trailing newline, so a key ending
# in one looked bare and was written unquoted onto two lines.
_BARE_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*\Z")
_QUOTED_KEY = re.compile(r"""^(['"])((?:\\.|(?!\1).)*)\1\s*:(?:\s+(.*))?\Z""")

# Characters no value may carry raw. `\n` and `\r` end a line and `\t` would be
# read as indentation — but `str.splitlines` also breaks on \v, \f, \x1c-\x1e,
# \x85 and the two Unicode separators, so a value holding one of those splits
# across lines the parser then reads separately. Same corruption as a raw
# newline; it just does not look like one when you read the file.
_BREAKS = "\n\r\t\v\f\x1c\x1d\x1e\x85  "
_UNSAFE = re.compile("[%s]" % re.escape(_BREAKS))
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
_LITERALS = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_LITERALS.update(
    dict((char, "\\u%04x" % ord(char)) for char in _BREAKS if char not in _LITERALS)
)
_ESCAPE = re.compile("[%s]" % re.escape("".join(_LITERALS)))
_HEX = re.compile(r"^[0-9a-fA-F]{4}$")


def loads(text):
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise MiniYamlError(f"line {lineno}: tab indentation is not allowed")
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped, lineno))
    if not lines:
        return {}
    value, index = _block(lines, 0, lines[0][0], 0)
    if index != len(lines):
        raise MiniYamlError(f"line {lines[index][2]}: unexpected indentation")
    return value


def _block(lines, i, indent, depth):
    if depth > MAX_DEPTH:
        raise MiniYamlError(
            f"line {lines[i][2]}: nested deeper than {MAX_DEPTH} levels"
        )
    if _is_item(lines[i][1]):
        return _sequence(lines, i, indent, depth)
    return _mapping(lines, i, indent, depth)


def _is_item(text):
    """A list item is `- value`, or a bare `-` owning the block beneath it."""
    return text == "-" or text.startswith("- ")


def _item_indent(indent, text):
    """The column an item's own keys sit in — where its first key starts.

    The dash's line carries that key, so the column is the dash's indent plus
    whatever separates the two. `strip()` only took the ends off the line, so a
    hand-written `-   kind: cmd` still says its keys live at column 4 and its
    continuation lines must line up there. Inferring the column from the
    continuations instead cannot work: a one-key item whose value is a block has
    no line at the key's own column to infer it from.
    """
    rest = text[1:]
    return indent + 1 + (len(rest) - len(rest.lstrip(" ")))


def _sequence(lines, i, indent, depth):
    out = []
    while i < len(lines) and lines[i][0] == indent and _is_item(lines[i][1]):
        item, lineno = lines[i][1][1:].strip(), lines[i][2]
        if not item:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                nested, i = _block(lines, i + 1, lines[i + 1][0], depth + 1)
                out.append(nested)
            else:
                out.append(None)
                i += 1
            continue
        if _quoted(item) is not None or _split_key(item) is None:
            out.append(_scalar(item, lineno, depth))
            i += 1
            continue
        # Give the first key back its own line at the column its siblings use,
        # then let `_mapping` read the item whole. That is what makes a nested
        # block legal under *any* key of an item rather than only the first.
        column = _item_indent(indent, lines[i][1])
        entry, consumed = _mapping(
            [(column, item, lineno)] + lines[i + 1:], 0, column, depth + 1
        )
        out.append(entry)
        i += consumed
    return out, i


def _mapping(lines, i, indent, depth):
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        raw, lineno = lines[i][1], lines[i][2]
        if _is_item(raw):
            raise MiniYamlError(f"line {lineno}: list item where a key was expected")
        parts = _split_key(raw)
        if parts is None:
            raise MiniYamlError(f"line {lineno}: cannot parse {raw!r}")
        key, inline = parts
        i += 1
        if inline:
            out[key] = _scalar(inline, lineno, depth)
        elif i < len(lines) and lines[i][0] > indent:
            out[key], i = _block(lines, i, lines[i][0], depth + 1)
        else:
            out[key] = None
    return out, i


def _quoted(text):
    """The body of a fully quoted scalar, or None if `text` is not one.

    Hand-rolled rather than a regex. The pattern that correctly rejects
    `"a": "b: c"` — a body holding no *unescaped* copy of the delimiter — needs
    a per-character alternation, and that turns the 10 MB values this parser is
    expected to shrug off into a second of backtracking. Here the usual case is
    one `str.find` that fails.
    """
    if len(text) < 2:
        return None
    quote = text[0]
    if quote not in "\"'" or text[-1] != quote:
        return None
    body = text[1:-1]
    index = body.find(quote)
    while index != -1:
        # A delimiter is real unless an odd number of backslashes precedes it.
        run = 0
        while run < index and body[index - 1 - run] == "\\":
            run += 1
        if run % 2 == 0:
            return None
        index = body.find(quote, index + 1)
    # `"abc\"` closes on an escaped quote, which means it never closed at all.
    if (len(body) - len(body.rstrip("\\"))) % 2:
        return None
    return body


def _split_key(text):
    """`key: value` split into its two halves, or None if this is not a key.

    The quoted form is tried first: `dumps` quotes any key outside `_KEY`, so
    without this the writer would emit files the reader rejects — which is how
    `loads(dumps({"a b": 1}))` came to raise.
    """
    if text[:1] in ("'", '"'):
        match = _QUOTED_KEY.match(text)
        if match:
            return _unescape(match.group(2)), (match.group(3) or "").strip()
    match = _KEY.match(text)
    if match:
        return match.group(1), (match.group(2) or "").strip()
    return None


def _scalar(text, lineno, depth=0):
    if depth > MAX_DEPTH:
        raise MiniYamlError(f"line {lineno}: nested deeper than {MAX_DEPTH} levels")
    quoted = _quoted(text)
    if quoted is not None:
        return _unescape(quoted)
    # The docstring promises we raise instead of guessing; these used to be
    # guessed. `a: "abc` came back as the literal `"abc`, and an anchor, alias or
    # tag came back as its own source text — so a file meaning something this
    # module cannot represent loaded as though it meant a string.
    if text[:1] in ("'", '"'):
        raise MiniYamlError(f"line {lineno}: unterminated quoted scalar {text!r}")
    if text[:1] in ("&", "*", "!"):
        raise MiniYamlError(
            f"line {lineno}: anchors, aliases and tags are not supported"
        )
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p, lineno, depth + 1) for p in _split_items(inner)]
    if text.startswith("{"):
        # `{}` alone is the empty mapping, which the writer needs a notation for
        # — a bare `key:` reads back as null, so `{"a": {}}` used to lose its
        # type on the first rewrite. Anything else inside braces is still a guess.
        if text == "{}":
            return {}
        raise MiniYamlError(f"line {lineno}: inline maps are not supported")
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _unescape(text):
    r"""Undo what `_emit` did to a quoted scalar.

    Without this the writer and the reader disagree: `_emit` escapes `"` as `\"`
    but nothing ever put it back, so every save/load cycle added another
    backslash. Unit frontmatter is rewritten on each status change, so the
    damage compounded rather than staying put. `\n`, `\r`, `\t` and `\uXXXX`
    joined the table when the writer learned to escape line breaks.
    """
    if "\\" not in text:
        # The overwhelmingly common case, and worth the check: the loop below is
        # per-character, which on a 10 MB value costs a third of a second.
        return text
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in _ESCAPES:
                out.append(_ESCAPES[following])
                index += 2
                continue
            digits = text[index + 2:index + 6]
            if following == "u" and len(digits) == 4 and _HEX.match(digits):
                out.append(chr(int(digits, 16)))
                index += 6
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _split_items(text):
    """Split an inline list on commas that are not inside a quoted item."""
    items, current, quote, escaped = [], [], "", False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif quote:
            current.append(char)
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return items


def dumps(value, indent=0):
    """Round-trip the same subset. Used to write frontmatter back after edits."""
    if indent > MAX_DEPTH:
        raise MiniYamlError(f"nested deeper than {MAX_DEPTH} levels")
    pad = "  " * indent
    out = []
    if isinstance(value, dict):
        for key, item in value.items():
            out.extend(_entry(f"{pad}{_emit_key(key)}", item, indent + 1))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                # Every key of the item, not just the first: a key whose value is
                # a collection gets a block of its own, indented past the sibling
                # keys. Emitting `_emit(nested)` here instead is what turned
                # `owns: ["src/"]` into the string "['src/']".
                for position, (key, sub) in enumerate(item.items()):
                    lead = f"{pad}- " if position == 0 else f"{pad}  "
                    out.extend(_entry(f"{lead}{_emit_key(key)}", sub, indent + 2))
            elif isinstance(item, (list, dict)) and item:
                # A bare dash owns the block beneath it — the only notation the
                # subset has for a collection sitting directly inside a list.
                out.append(f"{pad}-")
                out.append(dumps(item, indent + 1))
            elif isinstance(item, dict):
                out.append(f"{pad}- {{}}")
            elif isinstance(item, list):
                out.append(f"{pad}- []")
            else:
                out.append(f"{pad}- {_emit(item, as_item=True)}")
    else:
        out.append(f"{pad}{_emit(value)}")
    return "\n".join(out)


def _entry(lead, item, indent):
    """One `<lead>: <value>` line, plus the nested block when there is one."""
    if isinstance(item, (dict, list)) and item:
        return [f"{lead}:", dumps(item, indent)]
    if isinstance(item, dict):
        return [f"{lead}: {{}}"]
    if isinstance(item, list):
        return [f"{lead}: []"]
    return [f"{lead}: {_emit(item)}"]


def _emit_key(key):
    text = str(key)
    return text if _BARE_KEY.match(text) else _quote(text)


def _emit(value, as_item=False):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return _quote(text) if _needs_quotes(text, as_item) else text


def _quote(text):
    # One pass, not a chain of `str.replace`: replacing the backslash after the
    # quote would re-escape the backslash the quote's own escape just produced.
    return '"%s"' % _ESCAPE.sub(lambda match: _LITERALS[match.group()], text)


def _needs_quotes(text, as_item=False):
    if text == "" or text.strip() != text:
        return True
    if _UNSAFE.search(text):
        # A raw newline does not just corrupt this value: it puts a line the
        # parser cannot read into the middle of the block, `frontmatter.parse`
        # swallows the error, and the document loses *every* key. Reachable from
        # any pasted note, so it has to be escaped rather than rejected.
        return True
    if text[0] in "-[{#'\"&*!":
        return True
    if as_item and _KEY.match(text):
        # Only in a list item is `password: .*` ambiguous — as a mapping value
        # the first `: ` has already been consumed by the key.
        return True
    # The one rule that matters: if reading the bare text back would not return
    # the text, quote it. That covers "007", "1_000", "nan", "Infinity", "true",
    # "[]", "{}" and every future coercion, and it cannot drift out of step with
    # `_scalar` the way a hand-kept list of special cases did.
    try:
        return _scalar(text, 0) != text
    except MiniYamlError:
        return True
