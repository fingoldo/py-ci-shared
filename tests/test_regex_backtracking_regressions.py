"""Regexes in gate parsers that backtracked on a crafted line: each now finishes at an input size the old pattern could not.

The old patterns were exponential (the Dart empty-catch body) or quadratic (the others). Each input below makes the old pattern run
for tens of seconds or longer; the timeout marker fails the test if a pattern slides back, and the result assertions keep the
fixed pattern matching what the old one matched on honest input.
"""

from __future__ import annotations

import pytest

from py_ci_shared import _ci_install_parts, dart_scanners, edge_function_hygiene, external_fact_tables


@pytest.mark.timeout(20)
def test_dart_empty_catch_does_not_backtrack_on_many_line_comments_before_a_nested_block():
    comments = "// note\n" * 25
    source = "catch (_) {\n" + comments + "if (x) {\n}\n}\n"

    assert dart_scanners._EMPTY_CATCH.search(source) is None


def test_dart_empty_catch_still_matches_a_genuinely_empty_or_comment_only_body():
    assert dart_scanners._EMPTY_CATCH.search("catch (_) {}") is not None
    assert dart_scanners._EMPTY_CATCH.search("catch (_) { // ignored\n }") is not None
    assert dart_scanners._EMPTY_CATCH.search("catch (_) { log(e); }").group("body").strip() == "log(e);"


@pytest.mark.timeout(20)
def test_url_pattern_is_linear_on_a_long_dotted_token():
    token = "a." * 20000 + "!"

    assert external_fact_tables._URL.search(token) is None


def test_url_pattern_still_finds_a_bare_domain_url_and_a_scheme_url():
    assert external_fact_tables._URL.search("see docs.example.com/page for it").group(0) == "docs.example.com/page"
    assert external_fact_tables._URL.search("see https://example.org/x, then").group(0) == "https://example.org/x"
    assert external_fact_tables._URL.search("no url here, only example.com") is None


@pytest.mark.timeout(20)
def test_empty_left_operand_pattern_is_linear_on_a_long_whitespace_run():
    text = "null" + " " * 40000 + "x"

    assert edge_function_hygiene._EMPTY_LEFT_RE.search(text) is None


def test_empty_left_operand_pattern_still_matches_an_empty_operand_before_an_operator():
    assert edge_function_hygiene._EMPTY_LEFT_RE.search("x = null  ") is not None
    assert edge_function_hygiene._EMPTY_LEFT_RE.search("f(undefined ) ) ") is not None
    assert edge_function_hygiene._EMPTY_LEFT_RE.search("valid") is None


@pytest.mark.timeout(20)
def test_split_requirement_is_linear_on_a_long_space_run_in_the_specifier():
    spec = "pkg<" + " " * 40000 + ",x"

    assert _ci_install_parts.split_requirement(spec) is None


def test_split_requirement_accepts_specifier_lists():
    assert _ci_install_parts.split_requirement("pkg>=1.0,<2") == ("pkg", [], None)
    assert _ci_install_parts.split_requirement("pkg >=1.0, <2") == ("pkg", [], None)
    assert _ci_install_parts.split_requirement("pkg") == ("pkg", [], None)
