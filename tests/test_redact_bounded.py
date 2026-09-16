"""`redact.scrub` must not hang on a hostile pattern from `ctx.yaml`.

`scrub` runs on the journal write path inside `PostToolUse`/`Stop` hooks, so a
pattern that triggers catastrophic backtracking in `re.sub` hangs the hook,
not just a test. `(a+)+$` against `'a' * 40 + 'X'` is the textbook shape:
Python's `re` engine tries every way to split the run of `a`s before it can
report "no match," and that search is exponential in the run's length.
"""

import subprocess
import sys
import time
import unittest
import warnings

from ctx import redact

_CATASTROPHIC = r"(a+)+$"
_HOSTILE_INPUT = "a" * 40 + "X"


class ScrubDoesNotHangOnAHostilePattern(unittest.TestCase):
    def test_a_catastrophic_pattern_returns_quickly_and_the_rest_still_redact(self):
        text = f"note: {_HOSTILE_INPUT}\napi_key: sk-abcdefghijklmnopqrstuvwx"
        started = time.monotonic()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = redact.scrub(text, [_CATASTROPHIC, r"sk-[A-Za-z0-9]+"])
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed, 15,
            "scrub took too long — the hostile pattern was not bounded",
        )
        # The catastrophic pattern never actually matches this input, so the
        # hostile text itself is untouched...
        self.assertIn(_HOSTILE_INPUT, out)
        # ...but the *other* extra pattern, evaluated after it, still redacts.
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwx", out)
        self.assertIn(redact.PLACEHOLDER, out)

    def test_a_hostile_pattern_is_skipped_with_a_recorded_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            redact.scrub(_HOSTILE_INPUT, [_CATASTROPHIC])
        messages = [str(w.message) for w in caught]
        self.assertTrue(
            any("redact" in m and _CATASTROPHIC in m for m in messages),
            f"expected a warning naming the skipped pattern, got: {messages}",
        )

    def test_a_pattern_that_still_fails_to_compile_is_skipped_without_a_hang(self):
        # Unrelated to the timeout mechanism, but this must keep working: a
        # bad pattern is a configuration error, not a crash.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertEqual(redact.scrub("hello", ["("]), "hello")

    def test_the_bound_is_real_not_coincidentally_fast_on_this_input(self):
        """Proves the fix is load-bearing rather than accidental.

        This runs the *unbounded* substitution — the pre-fix code path — in a
        child process with a hard, short deadline of its own, so a regression
        here costs a couple of seconds instead of hanging the test suite. If
        `re.sub` on this exact pattern and input actually terminated quickly
        on its own, bounding it would prove nothing; this asserts the
        opposite: the unbounded call really does not return.
        """
        probe = (
            "import re\n"
            f"re.sub({_CATASTROPHIC!r}, 'x', {_HOSTILE_INPUT!r})\n"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(
                [sys.executable, "-c", probe], timeout=2, capture_output=True,
            )


class BoundedSubMechanism(unittest.TestCase):
    """`_bounded_sub` in isolation, so the bound is asserted on the mechanism
    itself and not only on the kind of pattern that happens to trigger it."""

    def test_it_returns_the_original_text_and_a_problem_on_timeout(self):
        started = time.monotonic()
        out, problem = redact._bounded_sub(_CATASTROPHIC, _HOSTILE_INPUT, 1)
        elapsed = time.monotonic() - started
        self.assertEqual(out, _HOSTILE_INPUT)
        self.assertIn("did not finish", problem)
        self.assertLess(elapsed, 15)

    def test_it_still_substitutes_a_pattern_that_actually_matches(self):
        out, problem = redact._bounded_sub("needle", "a needle here", 30)
        self.assertEqual(out, f"a {redact.PLACEHOLDER} here")
        self.assertEqual(problem, "")

    def test_a_pattern_with_no_match_is_returned_unchanged(self):
        out, problem = redact._bounded_sub("needle", "no such thing", 30)
        self.assertEqual(out, "no such thing")
        self.assertEqual(problem, "")

    def test_an_unparseable_pattern_is_reported_not_raised(self):
        out, problem = redact._bounded_sub("(", "text", 30)
        self.assertEqual(out, "text")
        self.assertIn("bad pattern", problem)


if __name__ == "__main__":
    unittest.main()
