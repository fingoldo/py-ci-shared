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
