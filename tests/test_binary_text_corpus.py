"""37-F1: a binary file under a text corpus (a PNG, a zip, a profile) is skipped, not read as text and reported unreadable.

The fixture bytes are not valid UTF-8 (a PNG signature with a 0xFF), so the old reader fails on them exactly as it did
on the real polyvocab_app and glossum files.
"""

from __future__ import annotations

from pathlib import Path

from py_ci_shared._core import read_text_if_not_binary
from py_ci_shared._gate_report import read_text_corpus
from py_ci_shared.comment_repo_paths import find_comment_repo_path_problems
from py_ci_shared.skip_as_pass import find_skip_as_pass

_BINARY = b"\x89PNG\r\n\x1a\n\x00\xff\xfe\x00binary"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_binary_file_is_not_text(tmp_path: Path):
    f = tmp_path / "icon.png"
    _write(f, _BINARY)
    assert read_text_if_not_binary(f) is None


def test_text_file_is_still_read(tmp_path: Path):
    f = tmp_path / "run.sh"
    _write(f, b"#!/bin/sh\necho hi\n")
    assert read_text_if_not_binary(f) == "#!/bin/sh\necho hi\n"


def test_undecodable_text_is_still_a_read_error(tmp_path: Path):
    # No NUL in the probe window, so it is text, and invalid UTF-8: it must stay an unreadable finding.
    f = tmp_path / "latin.sh"
    _write(f, b"# caf\xe9\necho hi\n")
    corpus = read_text_corpus(tmp_path, patterns=("*",), skip=())
    assert [p.rel for p in corpus.unreadable] == ["latin.sh"]


def test_text_corpus_skips_binary_without_reporting_it(tmp_path: Path):
    _write(tmp_path / "assets" / "logo.png", _BINARY)
    _write(tmp_path / "ok.txt", b"plain\n")
    corpus = read_text_corpus(tmp_path, patterns=("*",), skip=())
    assert [f.rel for f in corpus.files] == ["ok.txt"]
    assert corpus.unreadable == []


def test_skip_as_pass_reports_no_unreadable_for_a_binary_under_tool(tmp_path: Path):
    _write(tmp_path / "tool" / "profile.prof", _BINARY)
    _write(tmp_path / "tool" / "guard.sh", b'echo "SKIPPED: no docker"\nexit 0\n')
    found = find_skip_as_pass(tmp_path)
    assert not any("unreadable" in key for key in found)
    assert any(key.startswith("tool/guard.sh:") for key in found)


def test_comment_repo_paths_ignores_a_binary_under_tools(tmp_path: Path):
    _write(tmp_path / "tools" / "dump.zip", _BINARY)
    _write(tmp_path / "scripts" / "run.sh", b"# see tools/missing.sh\n")
    problems = find_comment_repo_path_problems(tmp_path, use_git=False)
    assert [(p.path, p.rule) for p in problems] == [("scripts/run.sh", "comment-repo-path")]
