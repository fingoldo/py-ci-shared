"""advisory_warn's banner makes no claim about the consumer's CI (audit 49-F13)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from py_ci_shared import advisory_warn


@pytest.fixture
def ruff_finds(monkeypatch, tmp_path):
    source = tmp_path / "m.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("PY_CI_SHARED_SRC_PATH", str(tmp_path))
    monkeypatch.setattr(advisory_warn, "_tool_installed", lambda module: module == "ruff")
    monkeypatch.setattr(advisory_warn.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    return str(source)


def test_the_banner_names_no_ci_workflow_of_the_consumer(ruff_finds, capsys) -> None:
    """Negative control (49-F13): the old banner asserted the consumer mirrors lint-advisory.yml."""
    assert advisory_warn.main([ruff_finds]) == 0
    err = capsys.readouterr().err
    assert "WARNINGS ONLY" in err
    assert "lint-advisory.yml" not in err
    assert "does not say whether CI runs these checks" in err
