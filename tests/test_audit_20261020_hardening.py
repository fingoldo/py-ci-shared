"""Regression tests for the 2026-10-20 deserialization and file-trust audit (DS-6, DS-10, DS-11, DS-12)."""

from __future__ import annotations

import io
import os
import subprocess
import tarfile

import pytest

from py_ci_shared import audit_round_format, black_filtered_apply, consumer_import_census, install_safe_hook
from py_ci_shared._core import CoreError


def test_black_gets_the_file_names_after_a_double_dash(monkeypatch):
    """DS-6: a tracked file named like an option must not be read as one."""
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(black_filtered_apply.subprocess, "run", fake_run)

    black_filtered_apply.files_black_would_change(["--fast.py", "-c.py"], "pyproject.toml")

    (cmd,) = seen
    assert "--" in cmd and cmd.index("--") < cmd.index("--fast.py") and cmd.index("--") < cmd.index("-c.py")


def test_a_huge_expression_falls_back_to_the_text_test_instead_of_crashing():
    """DS-10: ast.parse raises RecursionError or MemoryError on a crafted expression, and the gate must still answer."""
    expr = "src" + ".b" * 100000

    assert audit_round_format.is_whole_file_read(expr) is False


def test_a_symlinked_hook_is_left_untouched(tmp_path):
    """DS-11: write_bytes follows a symlink, so the patch would land on the link's target."""
    target = tmp_path / "real_hook"
    original = install_safe_hook._TARGET.encode("utf-8") + bytes([10])  # the target text is present, so only the symlink check can stop the write
    target.write_bytes(original)
    link = tmp_path / "pre-commit"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here ({type(exc).__name__}); needs developer mode or admin on Windows")

    assert install_safe_hook._patch_one(link) == 1
    assert target.read_bytes() == original, "the link target must not be rewritten"


def _tar_with(name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        data = b"x"
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_a_tar_member_that_climbs_out_of_the_destination_is_refused(tmp_path, monkeypatch):
    """DS-12: git archive refuses such names today; the extraction must not depend on that."""
    archive = _tar_with("../ESCAPED.txt")
    monkeypatch.setattr(consumer_import_census, "run_git", lambda *a, **k: subprocess.CompletedProcess([], 0, archive, b""))
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(CoreError, match="leave the extraction directory"):
        consumer_import_census._extract_ref(tmp_path, "HEAD", dest)

    assert not (tmp_path / "ESCAPED.txt").exists()


def test_an_ordinary_tar_member_is_extracted(tmp_path, monkeypatch):
    archive = _tar_with("pkg/mod.py")
    monkeypatch.setattr(consumer_import_census, "run_git", lambda *a, **k: subprocess.CompletedProcess([], 0, archive, b""))
    dest = tmp_path / "dest"
    dest.mkdir()

    consumer_import_census._extract_ref(tmp_path, "HEAD", dest)

    assert (dest / "pkg" / "mod.py").read_bytes() == b"x"
