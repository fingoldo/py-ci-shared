"""``replace_file`` retries a Windows ``PermissionError`` (audit 45-F6); every ``os.replace`` of a staged file goes through it."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from py_ci_shared._core import baseline
from py_ci_shared._core.baseline import atomic_write_text, replace_file

_SRC = Path(__file__).resolve().parent.parent / "src" / "py_ci_shared"


def _flaky(real, failures: int, calls: list):
    def replace(source, target):
        calls.append(1)
        if len(calls) <= failures:
            raise PermissionError("locked")
        return real(source, target)

    return replace


def test_retries_a_locked_target_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline.sys, "platform", "win32")
    monkeypatch.setattr(baseline.time, "sleep", lambda _s: None)
    calls: list = []
    monkeypatch.setattr(baseline.os, "replace", _flaky(os.replace, 2, calls))
    (tmp_path / "a").write_text("new")
    replace_file(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b").read_text() == "new" and len(calls) == 3


def test_gives_up_after_three_tries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline.sys, "platform", "win32")
    monkeypatch.setattr(baseline.time, "sleep", lambda _s: None)
    calls: list = []
    monkeypatch.setattr(baseline.os, "replace", _flaky(os.replace, 99, calls))
    (tmp_path / "a").write_text("x")
    with pytest.raises(PermissionError):
        replace_file(tmp_path / "a", tmp_path / "b")
    assert len(calls) == 3


def test_no_retry_off_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline.sys, "platform", "linux")
    calls: list = []
    monkeypatch.setattr(baseline.os, "replace", _flaky(os.replace, 99, calls))
    (tmp_path / "a").write_text("x")
    with pytest.raises(PermissionError):
        replace_file(tmp_path / "a", tmp_path / "b")
    assert len(calls) == 1


def test_atomic_write_text_survives_one_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline.sys, "platform", "win32")
    monkeypatch.setattr(baseline.time, "sleep", lambda _s: None)
    monkeypatch.setattr(baseline.os, "replace", _flaky(os.replace, 1, []))
    atomic_write_text(tmp_path / "t.json", "{}\n")
    assert (tmp_path / "t.json").read_text() == "{}\n"


def test_only_replace_file_calls_os_replace_in_src() -> None:
    offenders = [
        f"{path.relative_to(_SRC).as_posix()}:{node.lineno}"
        for path in sorted(_SRC.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr == "replace" and isinstance(node.value, ast.Name) and node.value.id == "os"
    ]
    assert len(offenders) == 1 and offenders[0].startswith("_core/baseline.py:"), offenders
