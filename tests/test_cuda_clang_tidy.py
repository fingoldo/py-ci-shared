"""Tests for py_ci_shared.cuda_clang_tidy (a fake clang-tidy script stands in for LLVM; one test uses the real one when present)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from py_ci_shared import cuda_clang_tidy as cct
from py_ci_shared.cuda_clang_tidy import EXIT_REQUIRED_MISSING, EXIT_SKIPPED, find_clang_tidy, find_cuda_clang_tidy, main, resolve_placeholders

KERNEL = 'SRC = """extern "C" __global__ void k({REAL}* x, int n) {\n  int i = blockIdx.x * 2;\n  x[i] = 1;\n}\n"""\n'

FAKE = """import sys
src = sys.argv[1]
print(src + ":2:7: warning: narrowing conversion from 'unsigned int' to signed type 'int' [bugprone-narrowing-conversions]")
print(src + ":3:5: warning: narrowing conversion from 'double' to 'int' [bugprone-narrowing-conversions]")
print(src + ":3:5: warning: Value stored is never read [clang-analyzer-deadcode.DeadStores]")
"""


def _fake(tmp_path: Path) -> str:
    script = tmp_path / "fake_tidy.py"
    script.write_text(FAKE)
    if sys.platform == "win32":
        exe = tmp_path / "fake_tidy.bat"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        exe = tmp_path / "fake_tidy.sh"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        exe.chmod(0o755)
    return str(exe)


def _corpus(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "m.py").write_text(body)
    return root


def test_builtin_narrowing_is_filtered_and_findings_are_formatted(tmp_path):
    rep = find_cuda_clang_tidy(_corpus(tmp_path, KERNEL), clang_tidy=_fake(tmp_path), use_git=False, cuda_path="")
    assert rep.checked == 1 and not rep.skipped
    assert len(rep.findings) == 2, rep.findings
    assert rep.findings[0].startswith("m.py::SRC: bugprone-narrowing-conversions: narrowing conversion from 'double'")
    assert any("clang-analyzer-deadcode.DeadStores" in f for f in rep.findings)
    assert not any("unsigned int" in f for f in rep.findings)


def test_unresolved_placeholder_is_reported_not_dropped(tmp_path):
    body = 'SRC = """extern "C" __global__ void k(const %MYSTERY%* cols) {}"""\n'
    rep = find_cuda_clang_tidy(_corpus(tmp_path, body), clang_tidy=_fake(tmp_path), use_git=False)
    assert rep.checked == 0 and len(rep.skipped) == 1 and "MYSTERY" in rep.skipped[0] and "m.py::SRC" in rep.skipped[0]


def test_define_resolves_a_placeholder(tmp_path):
    body = 'SRC = """extern "C" __global__ void k(const %MYSTERY%* cols) {}"""\n'
    rep = find_cuda_clang_tidy(_corpus(tmp_path, body), clang_tidy=_fake(tmp_path), use_git=False, defines={"MYSTERY": "int"})
    assert rep.checked == 1 and not rep.skipped


def test_resolve_placeholders():
    text, bad = resolve_placeholders("{REAL} {ACC} %THREADS% {BLOCK_SIZE} {x} {WEIRD}")
    assert text == "float double 256 256 {x} {WEIRD}" and bad == ["WEIRD"]


def _no_tool(monkeypatch):
    monkeypatch.setattr(cct.shutil, "which", lambda _n: None)
    monkeypatch.delenv("CLANG_TIDY", raising=False)
    monkeypatch.setattr(cct, "_vs_llvm_candidates", lambda: [])


def test_absent_clang_tidy_skips_loudly(tmp_path, monkeypatch, capsys):
    _no_tool(monkeypatch)
    root = _corpus(tmp_path, KERNEL)
    assert main([str(root)]) == EXIT_SKIPPED
    err = capsys.readouterr().err
    assert "SKIPPED (NOT A PASS)" in err and "PATH" in err and "CLANG_TIDY" in err


def test_require_makes_absence_a_failure(tmp_path, monkeypatch, capsys):
    _no_tool(monkeypatch)
    assert main([str(_corpus(tmp_path, KERNEL)), "--require"]) == EXIT_REQUIRED_MISSING
    assert "FAILED" in capsys.readouterr().err


def test_env_var_is_found(tmp_path, monkeypatch):
    _no_tool(monkeypatch)
    fake = _fake(tmp_path)
    monkeypatch.setenv("CLANG_TIDY", fake)
    assert find_clang_tidy()[0] == fake


def test_main_exit_one_on_findings(tmp_path, capsys):
    rc = main([str(_corpus(tmp_path, KERNEL)), "--clang-tidy", _fake(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1 and "m.py::SRC: clang-analyzer-deadcode.DeadStores" in out


def test_real_clang_tidy_on_a_seeded_dead_store(tmp_path):
    exe, _ = find_clang_tidy()
    if not exe:
        pytest.skip("no real clang-tidy found")
    body = 'SRC = """#define __global__\nextern "C" __global__ void k(int* x) {\n  int j = x[1];\n  j = 5;\n  x[2] = j;\n}\n"""\n'
    rep = find_cuda_clang_tidy(_corpus(tmp_path, body), use_git=False)
    assert rep.checked == 1 and not rep.errors, rep.errors
    assert any("DeadStores" in f for f in rep.findings), rep.findings
