"""Unit tests for black_filtered_apply.py's diff-opcode filtering logic.

These test filtered_apply() and its helpers DIRECTLY against hand-constructed
(orig, formatted) string pairs -- no real `black` invocation needed, since the
function under test only cares about the diff between two already-known strings.
process_one()/main() (which shell out to black) are exercised by py-ci-shared's
own pre-commit/CI usage instead, not unit-tested here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.black_filtered_apply import filtered_apply, looks_like_import_or_call_list, norm  # noqa: E402


class TestBlankLineInsertion:
    def test_pure_blank_line_insertion_is_rejected(self):
        orig = "def f():\n    x = 1\n    return x\n"
        formatted = "def f():\n    x = 1\n\n    return x\n"
        assert filtered_apply(orig, formatted) == orig

    def test_blank_line_insertion_bundled_with_a_1to2_line_content_change_rejects_both(self):
        """Documents a real, pre-existing limitation (not introduced or fixed by this test
        suite): when a content change on ONE line and a blank-line insertion right after it land
        in the SAME difflib 'replace' opcode with no further decomposition available (old=1 line,
        new=2 lines, no equal anchor for SequenceMatcher to split on), looks_like_import_or_call_
        list() sees "same normalized text, different line count" and misclassifies it as an
        explosion -- rejecting the WHOLE replace, including the otherwise-legitimate quote fix
        that happened to be adjacent to the blank line. A real explosion (call reformatted across
        many lines) and "one line's content changed, and Black also wants a blank line here" are
        structurally indistinguishable to looks_like_import_or_call_list at this granularity.
        Confirmed unrelated to the string-literal norm() fix (reproduces identically pre-fix)."""
        orig = "def f():\n    x = 'a'\n    return x\n"
        formatted = 'def f():\n    x = "a"\n\n    return x\n'
        # Known-suboptimal: the quote fix is lost along with the (correctly) rejected blank line.
        assert filtered_apply(orig, formatted) == orig


class TestBlankLineDeletion:
    def test_pure_blank_line_removal_is_accepted(self):
        orig = "def f():\n    x = 1\n\n    return x\n"
        formatted = "def f():\n    x = 1\n    return x\n"
        assert filtered_apply(orig, formatted) == formatted


class TestExplosion:
    def test_arg_list_explosion_is_rejected(self):
        orig = "foo(a, b, c)\n"
        formatted = "foo(\n    a,\n    b,\n    c,\n)\n"
        assert filtered_apply(orig, formatted) == orig

    def test_import_list_explosion_is_rejected(self):
        orig = "from x import a, b, c\n"
        formatted = "from x import (\n    a,\n    b,\n    c,\n)\n"
        assert filtered_apply(orig, formatted) == orig


class TestCollapse:
    def test_arg_list_collapse_is_accepted(self):
        orig = "foo(\n    a,\n    b,\n    c,\n)\n"
        formatted = "foo(a, b, c)\n"
        assert filtered_apply(orig, formatted) == formatted


class TestSemicolonSplit:
    def test_semicolon_joined_statements_kept_compact_not_split(self):
        orig = "a = 1; b = 2\n"
        formatted = "a = 1\nb = 2\n"
        # Black's one-statement-per-line split normalizes the same as an explosion under norm()
        # (';' stripped like a structural separator) -- rejected, original compact form kept.
        assert filtered_apply(orig, formatted) == orig


class TestNonExcludedChangesStillApply:
    def test_quote_style_change_is_applied(self):
        orig = "x = 'a'\n"
        formatted = 'x = "a"\n'
        assert filtered_apply(orig, formatted) == formatted

    def test_redundant_paren_removal_is_applied(self):
        orig = "return (x)\n"
        formatted = "return x\n"
        assert filtered_apply(orig, formatted) == formatted

    def test_simple_line_collapse_is_applied(self):
        orig = "x = (\n    1\n)\n"
        formatted = "x = 1\n"
        assert filtered_apply(orig, formatted) == formatted


class TestStringLiteralContentNotTreatedAsStructural:
    """Regression test for a 2026-07-09 CI/CD architecture review finding: norm() used to strip
    whitespace/commas/parens EVEN INSIDE string literals, so two blocks whose actual string
    CONTENTS differ (not just structural formatting) could normalize to the same character bag
    and be misclassified as an explosion/collapse of the same call -- silently rejecting a
    genuine, non-excluded Black change."""

    def test_norm_preserves_comma_inside_string_literal(self):
        # Before the fix: norm() stripped the comma inside "a, b" the same as a structural
        # comma, so this and a hypothetical two-separate-strings variant could normalize equal.
        assert norm('foo("a, b")') == 'foo"a, b"'

    def test_looks_like_import_or_call_list_does_not_confuse_string_content_with_explosion(self):
        # Same visible characters once structural punctuation is stripped, but the STRING
        # CONTENT differs ("a, b" as one string vs "a" and "b" as two) -- not an explosion of
        # the same call, a real content change that must NOT be classified as 'explode'.
        old_block = ['foo("a, b")\n']
        new_block = ["foo(\n", '    "a", "b"\n', ")\n"]
        assert looks_like_import_or_call_list(old_block, new_block) is None

    def test_genuine_explosion_with_string_args_still_detected(self):
        old_block = ['foo("a", "b", "c")\n']
        new_block = ["foo(\n", '    "a",\n', '    "b",\n', '    "c",\n', ")\n"]
        assert looks_like_import_or_call_list(old_block, new_block) == "explode"

    def test_filtered_apply_applies_real_string_content_change_not_masked_as_explosion(self):
        orig = 'foo("a, b")\n'
        formatted = 'foo(\n    "a", "b"\n)\n'
        # This is a real content change (one string became two), not an arg-list explosion of
        # the same call -- must be APPLIED, not rejected.
        assert filtered_apply(orig, formatted) == formatted

    def test_apostrophe_in_comment_does_not_swallow_the_rest_of_the_block_as_a_string(self):
        """Regression test for a bug in this fix's own first version: an apostrophe used as an
        English contraction inside a `#` comment (e.g. "don't") has no closing quote on its own
        line, so an earlier, newline-unaware version of the string-literal regex kept scanning
        past the newline looking for one and matched all the way to an UNRELATED quote several
        lines down -- "protecting" (and so preserving verbatim, unstripped) a huge, wrong span of
        comments and code as if it were one string literal's content. Confirmed against a real
        block from pyutilz's own source during this fix's regression testing: a genuine explosion
        of a commented frozenset() call was no longer detected as an explosion at all once this
        bug was in play, because the comment text survived normalization as fake "string content"
        and made the before/after blocks compare unequal."""
        orig = (
            "_X = frozenset({\n"
            "    # Coercions that don't change the semantics\n"
            '    "int", "float", "bool",\n'
            "})\n"
        )
        formatted = (
            "_X = frozenset(\n"
            "    {\n"
            "        # Coercions that don't change the semantics\n"
            '        "int",\n'
            '        "float",\n'
            '        "bool",\n'
            "    }\n"
            ")\n"
        )
        assert filtered_apply(orig, formatted) == orig
