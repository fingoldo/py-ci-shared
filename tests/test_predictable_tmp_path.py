"""Tests for py_ci_shared.predictable_tmp_path: the X-12 shape, its nearest correct form, and the scan's own failure modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.predictable_tmp_path import assert_predictable_tmp_path, find_predictable_tmp_path

BOM = b"\xef\xbb\xbf"

VIOLATION = "import os\nimport tempfile\n\n\ndef lock_path(name):\n    return os.path.join(tempfile.gettempdir(), name + '.lock')\n"
CLEAN = (
    "import os\nimport tempfile\n\n\ndef lock_path(name):\n"
    "    base = os.path.join(tempfile.gettempdir(), f'app-{os.getuid()}')\n    return os.path.join(base, name + '.lock')\n"
)


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"mod.py": VIOLATION.encode()})

    findings = find_predictable_tmp_path(root, use_git=False)

    assert [(f.path, f.line, f.rule) for f in findings] == [("mod.py", 6, "predictable-tmp-path")]


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    root = _corpus(tmp_path, {"mod.py": CLEAN.encode()})

    assert find_predictable_tmp_path(root, use_git=False) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"mod.py": VIOLATION.encode()})
    bom = _corpus(tmp_path / "bom", {"mod.py": BOM + VIOLATION.encode()})

    assert [(f.line, f.rule) for f in find_predictable_tmp_path(bom, use_git=False)] == [
        (f.line, f.rule) for f in find_predictable_tmp_path(plain, use_git=False)
    ]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"mod.py": VIOLATION.encode(), "broken.py": b"def broken(:\n"})

    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_predictable_tmp_path(root, use_git=False)

    assert [f.path for f in find_predictable_tmp_path(root, allow_unparsed=True, use_git=False)] == ["mod.py"]


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_predictable_tmp_path(tmp_path, use_git=False)


def test_suppression_marker_and_assert_both_behave(tmp_path):
    marked = VIOLATION.replace("name + '.lock')", "name + '.lock')  # tmp-path-ok: fixture only")
    root = _corpus(tmp_path, {"mod.py": marked.encode()})

    assert find_predictable_tmp_path(root, use_git=False) == []
    with pytest.raises(AssertionError, match="predictable-tmp-path"):
        assert_predictable_tmp_path(_corpus(tmp_path / "bad", {"mod.py": VIOLATION.encode()}), use_git=False)
