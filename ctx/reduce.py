"""Shrink command output for the model while keeping what a failing run needs.

`ctx.verify.truncate` keeps the first 40 and last 20 lines. For a test runner
that is exactly the wrong cut on a long run: the failing assertion usually sits
in the middle (after hundreds of progress lines, before captured output), so
the model is shown the noise on both sides and none of the signal, and a gate
retry is spent guessing.

`reduce_output` is the replacement. It is pure — text in, text out — reads no
files, environment or network, is stdlib-only and Python 3.8-compatible, and
never raises on any `str`. The pipeline, in order:

1. `strip_ansi` — drop CSI/OSC escapes and collapse carriage-return redraws.
2. Collapse runs of identical consecutive non-blank lines to `line (×N)`.
   Runs of blank lines are deliberately left as they are: plain text with
   blank lines must still reduce to exactly `truncate_lines`, which keeps
   them.
3. Recognise a test runner by its marker lines (never by command name) and
   extract the failure blocks plus the summary line(s). If nothing is
   recognised, or nothing is extracted, fall back to `truncate_lines`, which
   is byte-for-byte the old `truncate`.
4. Cap each line at `line_cap` characters.
5. Cap the whole result at `char_cap` characters, keeping head and tail; the
   final line (the runner's summary) always survives.

Every regex here is anchored, free of nested quantifiers, and never puts an
unbounded wildcard on both sides of an alternation; keyword checks that would
need that shape (`_py_final`) match the line's shape and search the keywords
separately. So reduction is linear in the input.
"""
import re

__all__ = ["strip_ansi", "truncate_lines", "reduce_output"]

# --- 1. escapes and redraws -------------------------------------------------

_CSI = r"\x1b\[[0-?]*[ -/]*[@-~]"
_OSC = r"\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?"  # unterminated: stops at its own line end
_ESC2 = r"\x1b[@-Z\\-_]"
_C1_CSI = r"\x9b[0-?]*[ -/]*[@-~]"
_ANSI = re.compile(f"{_CSI}|{_OSC}|{_ESC2}|{_C1_CSI}")


def strip_ansi(text):
    """Remove ANSI CSI/OSC escapes and collapse carriage-return redraws.

    A line redrawn with `\\r` keeps only its last non-empty segment, so
    `"a\\rb\\rc\\n"` becomes `"c\\n"`. `\\r\\n` is treated as a plain newline.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _ANSI.sub("", text).replace("\x1b", "")  # then any lone ESC left over
    if "\r" not in text:
        return text
    text = text.replace("\r\n", "\n")
    out = []
    for line in text.split("\n"):
        if "\r" in line:
            segments = [s for s in line.split("\r") if s]
            line = segments[-1] if segments else ""
        out.append(line)
    return "\n".join(out)


# --- the old cut, kept exactly ----------------------------------------------


def truncate_lines(text, head, tail):
    """Head + tail lines — byte-identical to the original `ctx.verify.truncate`."""
    lines = (text or "").strip().splitlines()
    if len(lines) <= head + tail:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head] + [f"… {omitted} lines omitted …"] + (lines[-tail:] if tail else [])
    )


# --- 2. repeated runs -------------------------------------------------------


def _collapse(lines):
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        j = i + 1
        while j < n and lines[j] == line:
            j += 1
        count = j - i
        if count == 1:
            out.append(line)
        elif line.strip():
            out.append(f"{line} (×{count})")
        else:
            out.extend(lines[i:j])  # blank runs are left alone
        i = j
    return out


# --- 3. runner recognition --------------------------------------------------

_BLOCK_HEAD = 10
_BLOCK_TAIL = 20
_BLOCK_KEEP = 20  # extra "looks like the error" lines kept from a block's middle
_SUMMARY_MAX = 30
_SIGNAL_SCAN = 2000  # how far into a block's middle to look for error lines
_SIGNAL = re.compile(
    r"^E\s|Error\b|assert|panicked|Expected|Received|expected|FAILED|Exception"
)


def _bound(block):
    """Bound one failure block: its head, its tail, and error-looking middle lines.

    Returns the bounded lines and how many source lines they show.
    """
    if len(block) <= _BLOCK_HEAD + _BLOCK_TAIL:
        return list(block), len(block)
    middle = block[_BLOCK_HEAD:-_BLOCK_TAIL]
    kept = []
    for m in middle[:_SIGNAL_SCAN]:
        if _SIGNAL.search(m):
            kept.append(m)
            if len(kept) >= _BLOCK_KEEP:
                break
    omitted = len(middle) - len(kept)
    out = list(block[:_BLOCK_HEAD])
    if kept:
        out.extend(kept)
    if omitted:
        out.append(f"  … {omitted} lines omitted …")
    out.extend(block[-_BLOCK_TAIL:])
    return out, _BLOCK_HEAD + len(kept) + _BLOCK_TAIL


def _trim(block):
    """Drop leading/trailing blank and separator-only lines.

    Returns how many leading lines were dropped, and the trimmed block.
    """
    start, end = 0, len(block)
    while start < end and _is_filler(block[start]):
        start += 1
    while end > start and _is_filler(block[end - 1]):
        end -= 1
    return start, block[start:end]


_RULE = re.compile(r"^\s*([=\-_~⎯])\1{9,}\s*$")


def _is_filler(line):
    return not line.strip() or bool(_RULE.match(line))


def _blocks_between(lines, starts, is_end):
    """Cut a block at each start index, ending at the next start or `is_end` line."""
    blocks = []
    start_set = set(starts)
    for s in starts:
        j = s + 1
        while j < len(lines) and j not in start_set and not is_end(lines[j]):
            j += 1
        offset, block = _trim(lines[s:j])
        if block:
            blocks.append((s + offset, block))  # the trimmed block's real start
    return blocks


# unittest

_UT_RAN = re.compile(r"^Ran \d+ tests? in ")
_UT_RESULT = re.compile(r"^(FAILED \(.*\)|OK(\s*\(.*\))?)\s*$")
_UT_SEP = re.compile(r"^={10,}\s*$")


def _unittest(lines):
    ran = [i for i, ln in enumerate(lines) if ln.startswith("Ran ") and _UT_RAN.match(ln)]
    if not ran:
        return None
    starts = [
        i for i, ln in enumerate(lines) if ln.startswith(("FAIL: ", "ERROR: "))
    ]
    if not starts:
        return None

    def end(line):
        if line.startswith("="):
            return bool(_UT_SEP.match(line))
        return line.startswith("Ran ") and bool(_UT_RAN.match(line))

    blocks = _blocks_between(lines, starts, end)
    summary = []
    for i in ran:
        summary.append(i)
        for k in range(i + 1, min(i + 4, len(lines))):
            if _UT_RESULT.match(lines[k]):
                summary.append(k)
                break
    return blocks, summary


# pytest

_PY_SECTION = re.compile(r"^=+ (FAILURES|ERRORS) =+\s*$")
_PY_ANY_SECTION = re.compile(r"^=+ .* =+\s*$")
_PY_TEST = re.compile(r"^_+ .* _+\s*$")
_PY_SHORT = re.compile(r"^=+ short test summary info =+\s*$")
_PY_FINAL_WORDS = re.compile(r"\b(?:failed|passed|error|errors|skipped|no tests ran)\b")


def _py_final(line):
    """A `=== … N failed … ===` line: the section shape, then a keyword search.

    One regex with `.*` on both sides of the keyword alternation backtracks
    quadratically on a long line with many keyword hits; two linear passes do not.
    """
    return _py_section(line) and bool(_PY_FINAL_WORDS.search(line))


def _py_section(line):
    return line.startswith("=") and bool(_PY_ANY_SECTION.match(line))


def _pytest(lines):
    sections = [i for i, ln in enumerate(lines) if ln.startswith("=") and _PY_SECTION.match(ln)]
    if not sections:
        return None
    starts = []
    for s in sections:
        j = s + 1
        while j < len(lines) and not _py_section(lines[j]):
            if lines[j].startswith("_") and _PY_TEST.match(lines[j]):
                starts.append(j)
            j += 1
    if not starts:
        starts = [s + 1 for s in sections if s + 1 < len(lines)]

    blocks = _blocks_between(lines, starts, _py_section)
    summary = []
    for i, ln in enumerate(lines):
        if ln.startswith("=") and _PY_SHORT.match(ln):
            j = i + 1
            while j < len(lines) and not _py_section(lines[j]):
                if lines[j].strip() and len(summary) < _SUMMARY_MAX:
                    summary.append(j)
                j += 1
    finals = [i for i, ln in enumerate(lines) if _py_final(ln)]
    if finals:
        summary.append(finals[-1])
    return blocks, summary


# cargo

_CARGO_HEAD = re.compile(r"^---- .+ (stdout|stderr) ----\s*$")
_CARGO_FAILURES = re.compile(r"^failures:\s*$")


def _cargo(lines):
    results = [i for i, ln in enumerate(lines) if ln.startswith("test result: ")]
    if not results:
        return None
    starts = [i for i, ln in enumerate(lines) if ln.startswith("---- ") and _CARGO_HEAD.match(ln)]

    def end(line):
        if line.startswith("failures:"):
            return bool(_CARGO_FAILURES.match(line))
        return line.startswith("test result: ")

    blocks = _blocks_between(lines, starts, end)
    return blocks, results


# jest / vitest

_JEST_SUMMARY = re.compile(r"^\s*(Test Suites|Test Files|Tests|Snapshots|Time|Duration)\b:?\s+\S")
_JEST_TESTS = re.compile(r"^\s*Tests:?\s+\d")
_JEST_FAIL_FILE = re.compile(r"^\s*(FAIL|PASS)\s")
_JEST_BULLET = re.compile(r"^\s*● ")
_JEST_CONSOLE = re.compile(r"^\s*● Console\s*$")
_VITEST_RULE = re.compile(r"^\s*⎯{3,}")


_JEST_FAIL = re.compile(r"^\s*FAIL\s")
_JEST_WORDS = ("Test", "Snapshots", "Time", "Duration")


def _jest_summary(line):
    return any(w in line for w in _JEST_WORDS) and bool(_JEST_SUMMARY.match(line))


def _jest(lines):
    summary = [i for i, ln in enumerate(lines) if _jest_summary(ln)]
    if not any(_JEST_TESTS.match(lines[i]) for i in summary):
        return None
    starts = [
        i for i, ln in enumerate(lines)
        if "●" in ln and _JEST_BULLET.match(ln) and not _JEST_CONSOLE.match(ln)
    ]
    if not starts:
        starts = [i for i, ln in enumerate(lines) if "FAIL" in ln and _JEST_FAIL.match(ln)]

    def end(line):
        return bool(
            _jest_summary(line)
            or ("⎯" in line and _VITEST_RULE.match(line))
            or (("FAIL" in line or "PASS" in line) and _JEST_FAIL_FILE.match(line))
            or ("●" in line and _JEST_BULLET.match(line))
        )

    blocks = _blocks_between(lines, starts, end)
    return blocks, summary[-_SUMMARY_MAX:]


# Each runner is only scanned line by line when one of its markers occurs at all.
_RUNNERS = (
    (_pytest, ("FAILURES", "ERRORS")),
    (_unittest, ("Ran ",)),
    (_cargo, ("test result: ",)),
    (_jest, ("Tests",)),
)


def _extract(lines):
    """Failure blocks + summary for a recognised runner, or None."""
    joined = "\n".join(lines)
    for runner, needles in _RUNNERS:
        if not any(n in joined for n in needles):
            continue
        found = runner(lines)
        if not found:
            continue
        blocks, summary = found
        if not blocks:
            continue
        out = []
        for _, block in blocks:
            bounded, _shown = _bound(block)
            out.extend(bounded)
            out.append("")
        block_lines = {i for s, b in blocks for i in range(s, s + len(b))}
        tail = []
        for i in sorted(set(summary)):
            if i not in block_lines:
                tail.append(lines[i])
        # Every line of a block is either shown or counted by that block's own
        # marker, so the global marker counts only lines outside every block.
        omitted = max(len(lines) - len(block_lines) - len(tail), 0)
        if omitted:
            out.append(f"… {omitted} lines omitted …")
        out.extend(tail)
        while out and not out[-1].strip():
            out.pop()
        return "\n".join(out)
    return None


# --- 4 and 5. caps ----------------------------------------------------------


def _cap_lines(text, line_cap):
    if line_cap is None or line_cap < 0:
        return text
    out = []
    for line in text.split("\n"):
        if len(line) > line_cap:
            line = f"{line[:line_cap]} …[cut {len(line) - line_cap} chars]"
        out.append(line)
    return "\n".join(out)


def _cap_chars(text, char_cap):
    if char_cap is None or len(text) <= char_cap:
        return text
    char_cap = max(int(char_cap), 0)
    last = text[text.rfind("\n") + 1:]
    tail_n = min(max(char_cap // 3, len(last)), char_cap)
    head_n = char_cap - tail_n
    head = text[:head_n]
    if "\n" in head:
        head = head[: head.rfind("\n")]
    tail = text[len(text) - tail_n:] if tail_n else ""
    nl = tail.find("\n")
    if nl != -1 and len(tail) - nl - 1 >= len(last):
        tail = tail[nl + 1:]
    omitted = len(text) - len(head) - len(tail)
    parts = [p for p in (head, f"… {omitted} chars omitted …", tail) if p]
    return "\n".join(parts)


# --- the pipeline -----------------------------------------------------------


def _reduce(text, head=40, tail=20, line_cap=2000, char_cap=6000):
    """The pipeline itself, unguarded, so tests can see it raise."""
    if not text:
        text = ""
    elif not isinstance(text, str):
        text = str(text)
    lines = _collapse(strip_ansi(text).splitlines())
    reduced = _extract(lines)
    if reduced is None or not reduced.strip():
        reduced = truncate_lines("\n".join(lines), head, tail)
    return _cap_chars(_cap_lines(reduced, line_cap), char_cap)


def reduce_output(text, head=40, tail=20, line_cap=2000, char_cap=6000):
    """Reduce command output for the model; see the module docstring.

    Runs of identical blank lines are not collapsed to `(×N)`, only non-blank ones.
    """
    try:
        return _reduce(text, head, tail, line_cap, char_cap)
    except Exception:  # never raise: this runs on the gate's failure path
        try:
            return _cap_chars(_cap_lines(truncate_lines(str(text), head, tail), line_cap), char_cap)
        except Exception:
            return ""
