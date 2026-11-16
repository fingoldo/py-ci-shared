"""Every ``python -m py_ci_shared.<gate>`` main survives a non-encodable finding on a legacy console (audit 45-T1).

A finding that names a Cyrillic path, printed to a cp1252 pipe, used to raise ``UnicodeEncodeError`` out of a raw
``sys.stdout.write``: the finding was lost and the exit code became the traceback's 1. ``safe_stdio`` re-configures the
standard streams to ``backslashreplace``; every module whose ``__main__`` guard runs a gate calls it first.
"""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

from py_ci_shared._core.console import safe_stdio

_SRC = Path(__file__).resolve().parent.parent / "src" / "py_ci_shared"


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    operands = [node.test.left, *node.test.comparators]
    names = {type(op).__name__ for op in operands}
    values = {op.value for op in operands if isinstance(op, ast.Constant)}
    return "Name" in names and "__main__" in values


def _main_guard_modules() -> "list[Path]":
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_main_guard(node) for node in tree.body):
            found.append(path)
    return found


def test_safe_stdio_writes_an_unencodable_finding_as_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    safe_stdio()
    sys.stdout.write("finding in пакет\n")
    stream.flush()
    text = raw.getvalue().decode("cp1252")
    assert "finding in " in text and "\\u043f" in text


def test_safe_stdio_covers_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stderr", stream)
    safe_stdio()
    sys.stderr.write("модуль\n")
    stream.flush()
    assert "\\u043c" in raw.getvalue().decode("cp1252")


def test_safe_stdio_accepts_a_stream_without_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    safe_stdio()  # must not raise


def test_every_main_guard_module_calls_safe_stdio() -> None:
    modules = _main_guard_modules()
    assert len(modules) >= 43, "the scan must see every entry module (43 at 2026-11-16), not a shrunken set"
    missing = [str(p.relative_to(_SRC)) for p in modules if "safe_stdio" not in p.read_text(encoding="utf-8")]
    assert not missing, f"__main__ entry modules without safe_stdio(): {missing}"
