"""The code-audit result memo: its key follows the content of the scanned tree and of the scanner package, not modification times."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from py_ci_shared.code_audit import _scan_cache as sc
from py_ci_shared.code_audit._base import Finding


@pytest.fixture(autouse=True)
def _private_memo(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_cache_dir", lambda: tmp_path / "memo")
    (tmp_path / "memo").mkdir()
    monkeypatch.setattr(sc, "_DIGESTS", {})
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "scanner.py").write_text("RULE = 1\n", encoding="utf-8")
    monkeypatch.setattr(sc, "_PACKAGE_DIR", package)
    return package


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _key(root: Path, exclude=frozenset()):
    return sc.cache_key(root, ["default_via_or"], exclude, ())


def test_the_package_signed_is_the_whole_py_ci_shared_package(monkeypatch):
    monkeypatch.undo()  # the autouse fixture pointed it at a temp package; this reads the real constant
    assert sc._PACKAGE_DIR.name == "py_ci_shared"
    assert (sc._PACKAGE_DIR / "code_audit").is_dir() and (sc._PACKAGE_DIR / "_core").is_dir()


def test_a_touch_without_a_content_change_keeps_the_key(tmp_path):
    root = _tree(tmp_path)
    before = _key(root)
    later = time.time_ns() + 10_000_000_000
    os.utime(root / "pkg" / "mod.py", ns=(later, later))
    assert _key(root) == before


def test_a_rewrite_with_the_same_size_and_new_bytes_changes_the_key(tmp_path):
    root = _tree(tmp_path)
    before = _key(root)
    (root / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    later = time.time_ns() + 10_000_000_000
    os.utime(root / "pkg" / "mod.py", ns=(later, later))
    assert _key(root) != before


def test_a_file_that_is_not_python_counts(tmp_path):
    root = _tree(tmp_path)
    before = _key(root)
    (root / "pkg" / "schema.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert _key(root) != before


def test_an_excluded_directory_does_not_change_the_key(tmp_path):
    root = _tree(tmp_path)
    exclude = frozenset({"legacy"})
    before = _key(root, exclude)
    (root / "legacy").mkdir()
    (root / "legacy" / "old.py").write_text("y = 1\n", encoding="utf-8")
    assert _key(root, exclude) == before


def test_a_change_anywhere_in_the_scanner_package_changes_the_key(tmp_path, _private_memo):
    root = _tree(tmp_path)
    before = _key(root)
    (_private_memo / "scanner.py").write_text("RULE = 2\n", encoding="utf-8")
    later = time.time_ns() + 10_000_000_000
    os.utime(_private_memo / "scanner.py", ns=(later, later))
    assert _key(root) != before


def test_a_stored_result_is_loaded_for_the_same_signature_only(tmp_path):
    root = _tree(tmp_path)
    digest, signature = _key(root)
    finding = Finding(check="default_via_or", severity="P2", file="pkg/mod.py", line=1, snippet="x = 1", detail="why")
    sc.store(digest, signature, [finding], {"scanner": "ValueError: boom"})
    assert sc.load(digest, signature) == ([finding], {"scanner": "ValueError: boom"})
    (root / "pkg" / "mod.py").write_text("x = 3\n", encoding="utf-8")
    later = time.time_ns() + 10_000_000_000
    os.utime(root / "pkg" / "mod.py", ns=(later, later))
    assert sc.load(digest, _key(root)[1]) is None
