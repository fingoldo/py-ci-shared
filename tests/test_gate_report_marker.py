"""line_has_marker finds the opt-out in the real trailing comment only (audit 33-F32)."""

from __future__ import annotations

import pytest

from py_ci_shared._gate_report import line_has_marker


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("x = 1  # lf-ok: reason", True),
        ("# lf-ok", True),
        ("x = '# lf-ok'", False),
        ('call("# lf-ok")', False),
        ('x = "#"; open("a.sh", "w")  # unrelated', False),
        ('x = "#"; open("a.sh", "w")  # lf-ok: reason', True),
        ("x = 'a" + chr(92) + "'# lf-ok'", False),
        ("it's prose  # lf-ok", True),
        ("no comment at all", False),
    ],
)
def test_marker_must_sit_in_the_comment(line, expected):
    assert line_has_marker([line], 1, "lf-ok") is expected


def test_a_marker_inside_a_multi_line_string_is_not_a_comment():
    """Audit 42-F3: a docstring or fixture string mentioning the marker must not suppress a finding."""
    lines = ['x = """a', "# foo-ok: y", '"""', "y = 1  # foo-ok: real"]
    assert line_has_marker(lines, 2, "foo-ok:") is False
    assert line_has_marker(lines, 4, "foo-ok:") is True


def test_a_comment_after_the_closing_quotes_is_still_a_comment():
    lines = ['x = """a', 'b"""  # foo-ok: real']
    assert line_has_marker(lines, 2, "foo-ok:") is True


def test_non_python_text_keeps_the_line_rule():
    assert line_has_marker(["run: echo '", "  # foo-ok: y"], 2, "foo-ok:") is True
