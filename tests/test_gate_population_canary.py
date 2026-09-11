"""Tests for py_ci_shared.gate_population_canary: the 06-F01 shape, and each exclusion.

The defect being modelled: a gate whose pattern stopped matching what it was written for, whose population was
therefore empty, and whose result stayed green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.gate_population_canary import (
    assert_canary_is_matched,
    assert_every_gate_declares_its_population,
    assert_population_is_not_empty,
    canary_problem,
    find_gates_without_population,
    gate_canaries,
    population_problem,
)

_INERT_GATE = '''
"""The 06-F01 shape: the pattern requires a call, so an assignment never enters the population."""
import re
from pathlib import Path

_SDK_CALL_RE = re.compile(r"\\.messages\\.create\\s*\\(")
_CANARY = (".messages.create(", ".messages.create = AsyncMock(")
_SOURCES = []


def _candidate_files():
    return list(_SOURCES)


def _build_offending_set():
    return {p for p in _candidate_files() if _SDK_CALL_RE.search(p.read_text())}
'''

_HEALTHY_GATE = '''
"""Both spellings matched, and a population that is the files scanned rather than the files matching."""
import re
from pathlib import Path

_SDK_CALL_RE = re.compile(r"\\.messages\\.create\\s*[(=]")
_CANARY = (".messages.create(", ".messages.create = AsyncMock(")
_HERE = Path(__file__).resolve().parent


def _candidate_files():
    return sorted(_HERE.glob("*.py"))


def _build_offending_set():
    return set()
'''


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source.lstrip("\n"), encoding="utf-8")
    return path


def test_a_gate_that_examined_nothing_is_reported(tmp_path: Path):
    """An empty population is the finding: the gate's silence says nothing about the tree."""
    path = _write(tmp_path, "test_inert.py", _INERT_GATE)

    problem = population_problem(path)

    assert problem is not None
    assert "examined 0 files" in problem


def test_a_gate_that_walks_real_files_is_clean(tmp_path: Path):
    path = _write(tmp_path, "test_healthy.py", _HEALTHY_GATE)

    assert population_problem(path) is None
    assert_population_is_not_empty(path)


def test_the_canary_catches_the_pattern_that_stopped_matching(tmp_path: Path):
    """`.messages.create = AsyncMock(` is the string that would have gone red the day the gate was written."""
    inert = _write(tmp_path, "test_inert.py", _INERT_GATE)
    healthy = _write(tmp_path, "test_healthy.py", _HEALTHY_GATE)

    assert canary_problem(inert, ".messages.create(") is None
    assert "no longer sees what it was written for" in (canary_problem(inert, ".messages.create = AsyncMock(") or "")
    assert canary_problem(healthy, ".messages.create = AsyncMock(") is None
    assert_canary_is_matched(healthy, ".messages.create = AsyncMock(")


def test_the_canaries_are_collected_for_parametrization(tmp_path: Path):
    _write(tmp_path, "test_healthy.py", _HEALTHY_GATE)
    _write(tmp_path, "test_plain.py", "def test_nothing():\n    assert True\n")

    canaries = gate_canaries(tmp_path)

    assert [c for _, c in canaries] == [".messages.create(", ".messages.create = AsyncMock("]


def test_a_gate_with_no_declared_population_is_reported(tmp_path: Path):
    _write(tmp_path, "test_undeclared.py", "import re\n\n\ndef _build_offending_set():\n    return set()\n")
    _write(tmp_path, "test_healthy.py", _HEALTHY_GATE)

    assert find_gates_without_population(tmp_path) == ["test_undeclared.py"]
    with pytest.raises(pytest.fail.Exception, match=r"test_undeclared\.py"):
        assert_every_gate_declares_its_population(tmp_path)


def test_a_declared_empty_population_is_a_recorded_decision(tmp_path: Path):
    """A gate aimed at a construct the repo does not use yet says so, with the reason written out."""
    source = "_EXPECTED_EMPTY_POPULATION = 'no CONCURRENTLY migration exists in this repo yet'\n\n\ndef _build_offending_set():\n    return set()\n"
    path = _write(tmp_path, "test_declared_empty.py", source)

    assert population_problem(path) is None
    assert find_gates_without_population(tmp_path) == []


def test_a_one_word_reason_does_not_count(tmp_path: Path):
    source = "_EXPECTED_EMPTY_POPULATION = 'n/a'\n\n\ndef _build_offending_set():\n    return set()\n"
    path = _write(tmp_path, "test_lazy_reason.py", source)

    assert "needs a written reason" in (population_problem(path) or "")


def test_a_module_that_is_not_a_gate_is_left_alone(tmp_path: Path):
    path = _write(tmp_path, "test_ordinary.py", "def test_two_and_two():\n    assert 2 + 2 == 4\n")

    assert population_problem(path) is None
    assert gate_canaries(tmp_path) == []
    assert find_gates_without_population(tmp_path) == []
