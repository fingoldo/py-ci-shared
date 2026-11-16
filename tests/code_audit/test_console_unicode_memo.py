"""console_unicode reads each package ``__init__.py`` once per scan, not once per module (audit 2026-11-16 48-T2)."""

from __future__ import annotations

from pathlib import Path

import py_ci_shared.code_audit.console_unicode as cu


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_init_read_once_per_directory_not_per_module(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    for i in range(30):
        _write(tmp_path, f"pkg/mod{i}.py", 'print("a → b")\n')
    reads: list[Path] = []
    real = cu._read_init_source

    def counting(init_file: Path) -> str:
        reads.append(init_file)
        return real(init_file)

    monkeypatch.setattr(cu, "_read_init_source", counting)
    findings = cu.scan_console_unicode(tmp_path)
    assert len(findings) == 30
    assert len(reads) == 1, reads


def test_memo_gives_the_same_answer_for_every_directory_on_the_chain(tmp_path: Path) -> None:
    _write(tmp_path, "__init__.py", "import sys\nsys.stdout.reconfigure(encoding='utf-8')\n")
    _write(tmp_path, "sub/__init__.py", "")
    _write(tmp_path, "sub/deep/mod.py", "x = 1\n")
    memo: dict[Path, bool] = {}
    assert cu._ancestor_package_has_stdio_utf8_reconfigure(tmp_path / "sub" / "deep" / "mod.py", tmp_path, memo) is True
    assert memo[tmp_path / "sub" / "deep"] is True
    assert memo[tmp_path / "sub"] is True
    assert memo[tmp_path] is True
