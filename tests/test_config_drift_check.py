"""Unit tests for the consumer-config drift report: divergence detection over parsed pyproject documents."""

from __future__ import annotations

from py_ci_shared.config_drift_check import _diff_section, main


def _doc(**ruff):
    return {"tool": {"ruff": ruff}}


def test_equal_values_do_not_diverge_and_different_ones_do():
    assert _diff_section("ruff", ("line-length",), {"a": _doc(**{"line-length": 160}), "b": _doc(**{"line-length": 160})}) == []
    lines = _diff_section("ruff", ("line-length",), {"a": _doc(**{"line-length": 160}), "b": _doc(**{"line-length": 120})})
    assert len(lines) == 1 and "line-length" in lines[0]


def test_a_field_missing_in_one_repo_is_divergence():
    lines = _diff_section("ruff", ("target-version",), {"a": _doc(**{"target-version": "py39"}), "b": _doc()})
    assert len(lines) == 1 and "<missing>" in lines[0]
    assert _diff_section("ruff", ("target-version",), {"a": _doc(), "b": _doc()}) == []


def test_a_list_valued_field_is_compared_not_crashed_on():
    same = {"a": _doc(extend=["x", "y"]), "b": _doc(extend=["x", "y"])}
    assert _diff_section("ruff", ("extend",), same) == []
    differ = {"a": _doc(extend=["x", "y"]), "b": _doc(extend=["x"])}
    assert len(_diff_section("ruff", ("extend",), differ)) == 1
    tables = {"a": _doc(extend={"k": 1, "j": 2}), "b": _doc(extend={"j": 2, "k": 1})}
    assert _diff_section("ruff", ("extend",), tables) == []


def test_main_reports_divergence_without_failing(capsys):
    docs = {"u1": {"tool": {"ruff": {"line-length": 160}, "mypy": {}}}, "u2": {"tool": {"ruff": {"line-length": 120}}}}
    assert main({"a": "u1", "b": "u2"}, fetch=docs.__getitem__) == 0
    out = capsys.readouterr().out
    assert "line-length diverges" in out
    assert main({"a": "u1", "b": "u1"}, fetch=docs.__getitem__) == 0
    assert "No divergence" in capsys.readouterr().out
