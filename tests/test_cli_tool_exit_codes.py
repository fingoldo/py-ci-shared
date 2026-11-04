"""34-F8: ``py-ci-shared tool`` passes a gate's exit code through, rejects a non-int return, and does not turn a gate's own KeyError into a usage error."""

from __future__ import annotations

import sys
import types

import pytest

from py_ci_shared import cli


def _fake_tool(monkeypatch, main):
    mod = types.ModuleType("py_ci_shared.worktree_hygiene")
    mod.main = main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "py_ci_shared.worktree_hygiene", mod)


@pytest.mark.parametrize("result, code", [(None, 0), (0, 0), (3, 3), (True, 1)])
def test_int_and_none_returns_are_the_exit_code(monkeypatch, result, code):
    _fake_tool(monkeypatch, lambda argv: result)
    assert cli.main(["tool", "worktree_hygiene"]) == code


@pytest.mark.parametrize("result", [["finding"], "problems", {"a": 1}, 1.5])
def test_a_non_exit_code_return_is_a_usage_error_not_success(monkeypatch, capsys, result):
    _fake_tool(monkeypatch, lambda argv: result)
    assert cli.main(["tool", "worktree_hygiene"]) == 2
    assert "not an exit code" in capsys.readouterr().err


def test_a_keyerror_inside_the_gate_keeps_its_traceback(monkeypatch):
    def broken(argv):
        raise KeyError("inside the gate")

    _fake_tool(monkeypatch, broken)
    with pytest.raises(KeyError, match="inside the gate"):
        cli.main(["tool", "worktree_hygiene"])


def test_unknown_module_is_still_a_usage_error(capsys):
    assert cli.main(["tool", "no_such_module_at_all"]) == 2
    assert "unknown py-ci-shared module" in capsys.readouterr().err
