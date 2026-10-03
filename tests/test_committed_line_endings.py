"""Tests for py_ci_shared.committed_line_endings, over real git repositories built in tmp_path.

Every repo is committed with ``core.autocrlf=false`` so the bytes written are the bytes in the index, which is the
point: the gate reads index blobs, never the worktree.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from py_ci_shared import committed_line_endings as cle
from py_ci_shared._core import Baseline, CorpusError, EmptyScanError

BOM = b"\xef\xbb\xbf"


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _repo(tmp_path: Path, files: "dict[str, bytes]") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "core.safecrlf", "false")
    for rel, data in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(data)
    _git(repo, "add", "-A")
    return repo


def _rules(repo: Path) -> "list[tuple[str, str]]":
    return [(f.path, f.rule) for f in cle.find_committed_line_endings(repo)]


def test_the_seed_bare_cr_file_is_reported_though_git_calls_it_binary(tmp_path):
    """pyutilz fa1aab0: 306 bare CR + 18 CRLF + 0 LF; git classifies such a blob as i/-text."""
    seed = b"import os\r" * 3 + b"x = 1\r\n" * 2
    repo = _repo(tmp_path, {"seed.py": seed, "ok.py": b"x = 1\n"})
    found = cle.find_committed_line_endings(repo)
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 1, cle.RULE_BARE_CR)]
    assert "3 bare CR, 2 CRLF, 0 LF" in found[0].message


def test_negative_control_lf_files_pass(tmp_path):
    repo = _repo(tmp_path, {"a.py": b"x = 1\ny = 2\n", "b.md": b"# t\n", "empty.txt": b""})
    assert cle.find_committed_line_endings(repo) == []


def test_mixed_and_all_crlf_are_reported_by_rule(tmp_path):
    repo = _repo(tmp_path, {"mixed.txt": b"a\r\nb\n", "crlf.py": b"a = 1\r\nb = 2\r\n", "lf.py": b"a = 1\n"})
    assert _rules(repo) == [("crlf.py", cle.RULE_CRLF), ("mixed.txt", cle.RULE_MIXED)]


def test_minus_text_and_binary_attributes_opt_a_path_out(tmp_path):
    attrs = b"fixtures/** -text\n*.dat binary\n"
    repo = _repo(tmp_path, {".gitattributes": attrs, "fixtures/raw.txt": b"a\rb\r\n", "blob.dat": b"a\r\nb\n", "a.py": b"x = 1\r\n"})
    assert _rules(repo) == [("a.py", cle.RULE_CRLF)]


def test_eol_crlf_is_a_checkout_setting_and_does_not_excuse_a_crlf_blob(tmp_path):
    """git stores a text file with LF whatever eol= says; a CRLF blob that predates the attribute is still wrong."""
    repo = _repo(tmp_path, {"old.bat": b"echo a\r\n"})  # committed before the attribute existed: the blob keeps CRLF
    (repo / ".gitattributes").write_bytes(b"*.bat text eol=crlf\n")
    (repo / "new.bat").write_bytes(b"echo b\r\n")  # added under the attribute: git normalizes the blob to LF
    _git(repo, "add", ".gitattributes", "new.bat")
    assert _rules(repo) == [("old.bat", cle.RULE_CRLF)]


def test_real_binary_blobs_are_skipped(tmp_path):
    repo = _repo(tmp_path, {"img.png": b"\x89PNG\r\n\x1a\n\x00\x00\rIHDR", "latin1.txt": b"caf\xe9\r", "a.py": b"x = 1\n"})
    assert cle.find_committed_line_endings(repo) == []


def test_the_worktree_is_not_what_is_read(tmp_path):
    """An LF blob whose checkout became CRLF (core.autocrlf) is clean; a CRLF blob since fixed only on disk is not."""
    repo = _repo(tmp_path, {"blob_lf.py": b"x = 1\n", "blob_crlf.py": b"x = 1\r\n"})
    (repo / "blob_lf.py").write_bytes(b"x = 1\r\n")
    (repo / "blob_crlf.py").write_bytes(b"x = 1\n")
    assert _rules(repo) == [("blob_crlf.py", cle.RULE_CRLF)]


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _repo(tmp_path / "p", {"seed.txt": b"a\rb\n"})
    bom = _repo(tmp_path / "b", {"seed.txt": BOM + b"a\rb\n"})
    assert cle.find_committed_line_endings(bom) == cle.find_committed_line_endings(plain)


def test_the_baseline_key_does_not_change_with_the_counts(tmp_path):
    one = cle.find_committed_line_endings(_repo(tmp_path / "1", {"s.txt": b"a\rb\n"}))
    two = cle.find_committed_line_endings(_repo(tmp_path / "2", {"s.txt": b"a\rb\rc\r\n"}))
    assert one[0].key == two[0].key == f"{cle.RULE_BARE_CR}::s.txt"
    assert one[0].message != two[0].message


def test_an_empty_index_fails_the_floor_and_a_non_repo_is_an_error(tmp_path):
    with pytest.raises(EmptyScanError):
        cle.find_committed_line_endings(_repo(tmp_path / "e", {}))
    (tmp_path / "plain").mkdir()
    with pytest.raises(CorpusError):
        cle.find_committed_line_endings(tmp_path / "plain")
    with pytest.raises(CorpusError):
        cle.find_committed_line_endings(tmp_path / "missing")


def test_assert_and_baseline(tmp_path):
    repo = _repo(tmp_path, {"seed.txt": b"a\rb\n", "ok.txt": b"a\n"})
    with pytest.raises(AssertionError, match=r"seed\.txt"):
        cle.assert_committed_line_endings(repo)
    baseline = tmp_path / "baseline.json"
    Baseline(baseline).save(Baseline.count(cle.find_committed_line_endings(repo)))
    cle.assert_committed_line_endings(repo, baseline_path=baseline)
    (repo / "seed.txt").write_bytes(b"a\nb\n")
    _git(repo, "add", "-A")
    with pytest.raises(pytest.fail.Exception, match="no longer found"):
        cle.assert_committed_line_endings(repo, baseline_path=baseline)  # fixed: the entry is stale


def test_index_endings_counts(tmp_path):
    repo = _repo(tmp_path, {"a.txt": b"1\r\n2\n3\r4"})
    assert cle.index_endings(repo) == [cle.Endings("a.txt", crlf=1, lf=1, bare_cr=1)]
