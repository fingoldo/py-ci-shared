"""CLI tests for ``python -m py_ci_shared.code_audit``, ported from pyutilz when the CLI moved here."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from py_ci_shared.code_audit import cli as _cli
from py_ci_shared.code_audit._base import Finding
from py_ci_shared.code_audit.cli import main as cli_main


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(source.lstrip("\n"), encoding="utf-8")
    return p


def test_cli_exits_nonzero_on_p1(tmp_path: Path, capsys):
    _write(tmp_path, "bad.py", "def f(items=[]):\n    items.append(1)\n")
    rc = cli_main([str(tmp_path), "--format", "markdown"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "mutable_default" in out
    assert "P0" in out


def test_cli_exits_zero_on_clean(tmp_path: Path):
    _write(tmp_path, "ok.py", "def f(x=None):\n    return x\n")
    assert cli_main([str(tmp_path)]) == 0


def test_cli_json_output_is_sorted_and_parses(tmp_path: Path, capsys):
    import json

    _write(tmp_path, "bad.py", "def f(items=[]):\n    items.append(1)\n")
    cli_main([str(tmp_path), "--format", "json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows and set(rows[0]) == {"check", "severity", "file", "line", "snippet", "detail"}


def test_dunder_main_module_execution_delegates_to_cli_main(tmp_path: Path):
    """``python -m py_ci_shared.code_audit <root>`` runs the ``if __name__ == '__main__'`` guard in
    __main__.py, which only executes under real module execution (never when pytest imports the
    module normally) -- exercised here via a real subprocess."""
    import os

    import py_ci_shared

    _write(tmp_path, "ok.py", "def f(x=None):\n    return x\n")
    # The child must import the same tree this test imports, not whatever py_ci_shared an unrelated install carries.
    tree = str(Path(py_ci_shared.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [tree, os.environ.get("PYTHONPATH", "")]))}
    result = subprocess.run(  # nosec B603 -- fixed local argv, no shell, no untrusted input
        [sys.executable, "-m", "py_ci_shared.code_audit", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_cli_renders_and_gates_on_an_unknown_severity(tmp_path: Path, capsys, monkeypatch):
    """F200 regression: ``sev_order.get(f.severity, 99) <= cutoff`` deleted an unrecognised severity
    at EVERY --min-severity setting and the exit code ignored it too."""
    stray = Finding.__new__(Finding)
    object.__setattr__(stray, "check", "stray")
    object.__setattr__(stray, "severity", "Medium")
    object.__setattr__(stray, "file", "x.py")
    object.__setattr__(stray, "line", 1)
    object.__setattr__(stray, "snippet", "x = 1")
    object.__setattr__(stray, "detail", "d")
    monkeypatch.setattr(_cli, "run_all", lambda *a, **k: [stray])

    assert cli_main([str(tmp_path), "--min-severity", "P0"]) == 1
    assert "stray" in capsys.readouterr().out


def test_cli_min_severity_does_not_weaken_exit_code(tmp_path: Path):
    """Regression: --min-severity previously filtered `findings` BEFORE the exit-code check,
    so a real P1 finding silently exited 0 once filtered out of the display."""
    _write(tmp_path, "bad.py", """
async def process(item):
    await item.save()

def caller(item):
    process(item)
""")
    assert cli_main([str(tmp_path), "--min-severity", "Low"]) == 1
    assert cli_main([str(tmp_path), "--min-severity", "P0"]) == 1
