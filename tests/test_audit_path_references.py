"""Unit tests for audit_path_references: code never pins an open audit round's path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.audit_path_references import assert_no_open_round_paths, find_open_round_literals, open_round_names


@pytest.fixture
def audits(tmp_path: Path) -> Path:
    a = tmp_path / "audits"
    (a / "2026-01-01").mkdir(parents=True)
    (a / "implemented" / "2025-12-01").mkdir(parents=True)
    (a / "notes").mkdir()
    return a


def test_only_dated_open_rounds_count(audits):
    assert open_round_names([audits]) == {"2026-01-01"}


def test_a_pinned_open_round_is_reported_and_a_docstring_or_closed_round_is_not(tmp_path, audits):
    code = tmp_path / "claims.py"
    code.write_text(
        '"""Docstrings may name audits/2026-01-01/01.md freely."""\n'
        "from pathlib import Path\n"
        'A = Path("audits") / "2026-01-01" / "01.md"\n'
        'B = "dashboard/audits/2026-01-01/03.md"\n'
        'C = Path("audits") / "implemented" / "2025-12-01" / "01.md"\n'
        'D = "2026-01-01 was a Thursday"\n',
        encoding="utf-8",
    )
    problems = find_open_round_literals([code], [audits], root=tmp_path)
    assert [p.split(":")[1] for p in problems] == ["3", "4"]


def test_no_open_rounds_means_nothing_to_pin(tmp_path):
    (tmp_path / "audits" / "implemented").mkdir(parents=True)
    code = tmp_path / "c.py"
    code.write_text('X = "audits/2026-01-01/x.md"\n', encoding="utf-8")
    assert find_open_round_literals([code], [tmp_path / "audits"]) == []


def test_assert(tmp_path, audits):
    code = tmp_path / "c.py"
    code.write_text('X = "audits/2026-01-01/x.md"\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="open audit round"):
        assert_no_open_round_paths([code], [audits])
