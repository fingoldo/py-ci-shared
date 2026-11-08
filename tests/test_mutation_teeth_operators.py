"""Tests for py_ci_shared.mutation_teeth: operator shapes, AST-span operators and substitution rules.

Split from test_mutation_teeth.py; see its docstring.
"""

from __future__ import annotations

import ast
import tokenize

import pytest

from py_ci_shared import mutation_teeth


@pytest.mark.skipif(
    not hasattr(tokenize, "FSTRING_MIDDLE"),
    reason="before 3.12 an f-string is a single STRING token, so it has no literal segment to mutate",
)
class TestProseInsideAnFStringCanBeChallenged:
    """On 3.12+ the literal text of an f-string is FSTRING_MIDDLE, which the string operator never
    saw. In this project that is where the prose lives: a prompt template rendered as one f-string
    yielded mutants only for its `{...}` slots, so no sentence in it could be challenged."""

    def test_a_literal_segment_is_mutable(self, tmp_path):
        src = tmp_path / "m.py"
        src.write_text('def go(name):\n    return f"### {name} fenced by a rule"\n', encoding="utf-8")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)
        segments = [m for m in mutants if "f-string segment" in m.description]

        assert segments, "the literal text of an f-string produced no mutants"
        assert any("fenced by a rule" in m.original_span for m in segments)

    def test_the_interpolations_survive_the_edit(self, tmp_path):
        """The objection that made the whole f-string be skipped: emptying an entire f-string token
        turns `f"{x} and {'q'}"` into something where the second interpolation is no longer one.
        Deleting a SEGMENT cannot do that, and this is the assertion that says so."""
        src = tmp_path / "m.py"
        src.write_text('def go(a, b):\n    return f"{a} middle {b} tail"\n', encoding="utf-8")
        mutants, _t, _s = mutation_teeth.generate_mutants(src)
        segments = [m for m in mutants if "f-string segment" in m.description]

        assert segments
        for m in segments:
            tree = ast.parse(m.mutated_file_text)
            slots = [n for n in ast.walk(tree) if isinstance(n, ast.FormattedValue)]
            assert len(slots) == 2, f"an interpolation was lost by {m.original_span!r}"


class TestTheOperatorSetReachesTheShapesThisCodeProduces:
    """Each of these was absent, and each names a real defect shape in the consuming project."""

    def _descriptions(self, tmp_path, body: str) -> set[str]:
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        return {m.description for m in mutation_teeth.generate_mutants(src)[0]}

    def test_an_accumulator_can_stop_accumulating(self, tmp_path):
        """`+=` -> `=` is the attachment-budget defect exactly: the running total stops running, and
        every item is then measured against the full budget instead of what is left of it."""
        got = self._descriptions(tmp_path, "def go(items):\n    used = 0\n    for i in items:\n        used += i\n    return used\n")

        assert any("+= becomes =" in d for d in got), sorted(got)

    def test_a_loop_can_stop_instead_of_skipping(self, tmp_path):
        """`continue` -> `break` in a cap-enforcement loop is the difference between skipping one
        item and abandoning the rest, and it is the same family as an off-by-one at the cap."""
        got = self._descriptions(tmp_path, "def go(items):\n    for i in items:\n        if i:\n            continue\n        print(i)\n")

        assert any("continue becomes break" in d for d in got), sorted(got)

    def test_a_clamp_can_invert(self, tmp_path):
        got = self._descriptions(tmp_path, "def go(n, cap):\n    return max(1, min(n, cap))\n")

        assert any("max becomes min" in d for d in got), sorted(got)

    def test_integer_division_and_modulo_are_reachable(self, tmp_path):
        got = self._descriptions(tmp_path, "def go(n):\n    return n // 3, n % 7, n ** 2\n")

        for wanted in ("// becomes /", "% becomes *", "** becomes *"):
            assert any(wanted in d for d in got), f"{wanted} missing from {sorted(got)}"

    def test_every_generated_mutant_still_compiles(self, tmp_path):
        """The guard on the whole set: a new operator that emits invalid syntax would be filtered
        as 'does not compile' and silently reduce coverage instead of adding it."""
        body = (
            "def go(items, n, cap):\n"
            "    used = 0\n"
            "    for i in items:\n"
            "        if i is None:\n"
            "            continue\n"
            "        used += i // 2\n"
            "        if used > cap:\n"
            "            break\n"
            "    return max(used, min(n, cap)), any(items), all(items)\n"
        )
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        mutants, _total, _s = mutation_teeth.generate_mutants(src)

        # Asserted by NAME, not by count: a count is a guess about the operator set, and the point
        # here is that each new operator fires and none of them emits invalid syntax -- an operator
        # that did would be filtered as "does not compile" and quietly shrink coverage.
        fired = {m.description for m in mutants}
        for wanted in ("+= becomes =", "continue becomes break", "break becomes continue", "max becomes min"):
            assert any(wanted in d for d in fired), f"{wanted} did not fire: {sorted(fired)}"
        for m in mutants:
            ast.parse(m.mutated_file_text)


class TestAnnotationsThatCarryEnforcedBounds:
    """The exclusion of annotations was right for the reason it gave and too wide by one case."""

    def _descriptions(self, tmp_path, body: str) -> set[str]:
        src = tmp_path / "m.py"
        src.write_text(body, encoding="utf-8")
        return {m.description for m in mutation_teeth.generate_mutants(src)[0]}

    def test_a_literal_choice_set_is_mutable(self, tmp_path):
        """`Literal[...]` is read when the model is built and enforced on every instance, so
        emptying one of its members is a defect a test observes. Excluding every annotation hid
        that behind the same rule that correctly excludes a forward reference."""
        got = self._descriptions(tmp_path, 'from typing import Literal\n\nStatus = 1\n\n\ndef f(s: Literal["draft", "sent"]) -> int:\n    return 1\n')

        assert any("emptied a string" in d for d in got), sorted(got)

    def test_a_bare_forward_reference_is_still_left_alone(self, tmp_path):
        """The counterweight, and the reason the original exclusion exists: under
        `from __future__ import annotations` this is never evaluated, so the mutant is unkillable
        by construction and would be a permanent false survivor."""
        got = self._descriptions(tmp_path, 'from __future__ import annotations\n\n\ndef f(x: "MyType") -> "int":\n    return x\n')

        assert not any("emptied a string" in d for d in got), sorted(got)


class TestTheOperatorsThatNeedAstSpans:
    """Deferred once, because AST columns are BYTE offsets and this is where that bites.

    The historical failure is on record in the module docstring: a span taken as a character offset
    turned a whole statement into `a >= b` and reported it at a line nobody had touched. Both
    operators here take every boundary through `_abs_index`, and the first test puts an emoji in
    front of the call so a regression shows up as a mangled splice rather than as a subtly wrong
    line number.
    """

    def _mutants(self, tmp_path, body: str):
        src = tmp_path / "m.py"
        open(src, "w", encoding="utf-8", newline="").write(body)
        return mutation_teeth.generate_mutants(src)[0]

    def test_adjacent_arguments_are_transposed(self, tmp_path):
        """`log("%s kept %d of %d", uid, n, cap)` type-checks, reads correctly, and lies when two of
        those values change places. No single token is wrong, so no token operator can express it."""
        mutants = self._mutants(tmp_path, 'def go(uid, n, cap):\n    log("kept", uid, n, cap)\n')
        swaps = [m for m in mutants if "transposed" in m.description]

        assert len(swaps) == 3, [m.description for m in swaps]
        assert any("uid <-> n" in m.description for m in swaps)

    def test_a_transposition_survives_a_non_ascii_prefix(self, tmp_path):
        """The trap itself. With an emoji earlier on the line, a span read as a character offset
        lands two positions late and destroys the surrounding text."""
        body = 'def go(uid, n):\n    x = "\U0001f600\U0001f600"; log("kept", uid, n)\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "transposed" in m.description]

        assert swaps
        for mutant in swaps:
            line = mutant.mutated_file_text.split("\n")[1]
            assert '"\U0001f600\U0001f600"' in line, f"the text before the call was destroyed: {line}"
            ast.parse(mutant.mutated_file_text)

    def test_star_args_are_left_alone(self, tmp_path):
        """`*args` has no fixed position, so there is nothing to transpose and the splice would be
        meaningless rather than wrong."""
        mutants = self._mutants(tmp_path, "def go(a, rest):\n    log(a, *rest)\n")

        assert not [m for m in mutants if "transposed" in m.description]

    def test_a_named_slice_bound_can_be_removed(self, tmp_path):
        """`text[:limit]` -> `text[:]` is the unbounded-slice defect. The numeric operator reached
        this only when the bound was a literal, and a bound worth having is usually a name."""
        mutants = self._mutants(tmp_path, "def go(text, limit):\n    return text[:limit]\n")
        cuts = [m for m in mutants if m.description.startswith("slice:")]

        assert cuts, [m.description for m in mutants]
        assert "text[:]" in cuts[0].mutated_file_text

    def test_a_literal_slice_bound_is_left_to_the_numeric_operator(self, tmp_path):
        """The counterweight: two operators firing on one site is noise, and `10` -> `11` already
        says everything a removed literal bound would."""
        mutants = self._mutants(tmp_path, "def go(text):\n    return text[:10]\n")

        assert not [m for m in mutants if m.description.startswith("slice:")]
        assert any("10 becomes 11" in m.description for m in mutants)


class TestSubstitutionNeedsARuleAndHasOne:
    """Both of these were deferred for want of a rule, not for want of effort.

    Emptying a string is unambiguous; substituting one has to answer "with WHAT", and an arbitrary
    answer would add to the 56% of all candidates that string-emptying already produces.
    """

    def _mutants(self, tmp_path, body: str):
        src = tmp_path / "m.py"
        open(src, "w", encoding="utf-8", newline="").write(body)
        return mutation_teeth.generate_mutants(src)[0]

    def test_a_guard_pattern_is_widened_at_its_anchors(self, tmp_path):
        """Each perturbation makes the pattern match MORE, which is the direction a guard fails in:
        a narrowed guard misses something and says so, a widened one quietly says yes."""
        body = _RAW_GUARD
        widened = [m for m in self._mutants(tmp_path, body) if "regex widened" in m.description]

        assert widened, [m.description for m in self._mutants(tmp_path, body)]
        assert "word-boundary" in widened[0].description

    def test_prose_in_the_same_shape_is_not_treated_as_a_pattern(self, tmp_path):
        """The counterweight. `\b` in a log message is not an anchor, and perturbing it would be a
        mutant about nothing -- the rule is that the string must reach a regex CALL."""
        body = _RAW_PROSE
        widened = [m for m in self._mutants(tmp_path, body) if "regex widened" in m.description]

        assert not widened

    def test_a_pattern_that_would_not_compile_is_not_emitted(self, tmp_path):
        """A perturbation that breaks the pattern is not a mutant no test kills -- it is not a
        mutant. Emitting it would put a permanent unkillable entry in every survivor list."""
        body = _RAW_ANCHORED
        widened = [m for m in self._mutants(tmp_path, body) if "regex widened" in m.description]

        assert widened, "the anchored pattern must yield a widened mutant, or this test checks nothing"
        for mutant in widened:
            import re as _re

            pattern = mutant.mutated_file_text.split('r"')[1].split('"')[0]
            _re.compile(pattern)

    def test_a_literal_is_swapped_for_a_confusable_one_from_the_same_file(self, tmp_path):
        """The vocabulary is the file's own, so the rule invents nothing: if `NFKD` and `NFKC` both
        appear, the author held both in mind and "does anything check which one this is" is a real
        question."""
        body = 'def go(text, mode):\n    a = norm("NFKD", text)\n    b = norm("NFKC", text)\n    return a, b\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "became" in m.description]

        assert swaps, [m.description for m in self._mutants(tmp_path, body)]
        assert any("NFKD" in m.description and "NFKC" in m.description for m in swaps)

    def test_a_literal_with_no_confusable_neighbour_gets_no_mutant(self, tmp_path):
        """The other half of the rule, and what keeps it cheap: no neighbour, no mutant. Measured at
        1.6% of candidates across five real modules."""
        body = 'def go(text):\n    return norm("NFKD", text)\n'
        swaps = [m for m in self._mutants(tmp_path, body) if "became" in m.description]

        assert not swaps


#: Raw string bodies. A backslash-b inside an ordinary Python string is the BACKSPACE
#: escape, and an earlier version of these tests handed the harness that control character
#: and then reported that no widening was produced -- true, and about nothing. Four separate
#: attempts lost a level of escaping between a shell heredoc and this file; the rule that
#: finally worked is to stop escaping rather than to escape more carefully.
_RAW_GUARD = r"""import re


P = re.compile(r"\bnot the right fit\b")
"""

_RAW_PROSE = r"""def go(log):
    log("a message with a \b in it")
"""

_RAW_ANCHORED = r"""import re


P = re.compile(r"^a+$")
"""
