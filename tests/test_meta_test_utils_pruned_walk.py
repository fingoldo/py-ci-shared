"""The meta-test helpers' tree walks must not enter ``.claude`` (Claude Code's agent worktrees, each a COMPLETE copy of the repository).

Each test hands a toy repo ROOT to a tool, with a real file and a decoy under ``.claude/worktrees/agent-x/``, and checks both the
result and that no directory below ``.claude`` was listed (``os.scandir`` recorded).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from py_ci_shared import meta_test_utils as mtu

DECOY_DIR = ".claude/worktrees/agent-x"


def write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def listed(monkeypatch):
    seen = []
    real = os.scandir

    def recording(path="."):
        seen.append(os.fsdecode(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", recording)
    return seen


def assert_never_entered(seen):
    assert seen, "the tool listed no directory at all, so this test proved nothing"
    assert [p for p in seen if ".claude" in Path(p).parts] == []


def test_rglob_test_files(tmp_path, listed):
    write(tmp_path, "tests/test_a.py")
    write(tmp_path, f"{DECOY_DIR}/tests/test_b.py")
    assert [p.name for p in mtu._rglob_test_files(tmp_path)] == ["test_a.py"]
    assert_never_entered(listed)


def test_consumer_corpus(tmp_path, listed):
    write(tmp_path, "pkg/a.py", "REAL_MARKER = 1\n")
    write(tmp_path, f"{DECOY_DIR}/pkg/b.py", "DECOY_MARKER = 1\n")
    corpus = mtu.consumer_corpus(tmp_path)
    assert "REAL_MARKER" in corpus
    assert "DECOY_MARKER" not in corpus
    assert_never_entered(listed)


def test_scan_todo_markers(tmp_path, listed):
    write(tmp_path, "pkg/a.py", "# TODO(me): real\n")
    write(tmp_path, f"{DECOY_DIR}/pkg/b.py", "# TODO(me): decoy\n")
    assert [Path(p).name for p, *_ in mtu.scan_todo_markers(tmp_path)] == ["a.py"]
    assert_never_entered(listed)


def test_unbacked_audit_dispositions_reads_only_the_audit_dir_it_was_given(tmp_path, listed):
    audit = tmp_path / "audits"
    write(audit, "a.md", "| 1 | Low | RESOLVED nothing cited |\n")
    write(audit, f"{DECOY_DIR}/b.md", "| 2 | Low | RESOLVED nothing cited |\n")
    out = mtu.unbacked_audit_dispositions(audit, tmp_path)
    assert len(out) == 1
    assert_never_entered(listed)
