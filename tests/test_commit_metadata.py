"""Tests for py_ci_shared.commit_metadata, over real throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError
from py_ci_shared.commit_metadata import assert_commit_metadata, check_message, find_commit_metadata_problems, main, parse_trailers, read_commits

BOM = "﻿"
COAUTHOR = "Co-Authored-By: Claude <noreply@anthropic.com>"


def _git(repo: Path, *args: str, author: str = "Dev") -> str:
    env_args = ["-c", f"user.name={author}", "-c", "user.email=d@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *env_args, *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _commit(repo: Path, message: str, *, author: str = "Dev") -> str:
    (repo / "f.txt").write_text(message[:20] + str(len(list(repo.iterdir()))) + _git(repo, "rev-list", "--all", "--count"))
    _git(repo, "add", "-A")
    msg = repo.parent / f"{repo.name}-msg.txt"
    msg.write_bytes(message.encode("utf-8"))  # -F keeps a BOM, as PowerShell 5 Set-Content wrote it
    _git(repo, "commit", "-q", "--cleanup=verbatim", "-F", str(msg), author=author)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _commit(r, "base")
    return r


def test_reports_a_bom_subject_and_a_forbidden_trailer_in_the_range(repo: Path) -> None:
    bad_bom = _commit(repo, BOM + "code_audit: seed\n\nbody\n")
    bad_trailer = _commit(repo, f"seed: feature\n\nwhy\n\n{COAUTHOR}\n")
    _commit(repo, "seed: clean\n\nbody mentions Co-Authored-By: in prose\n")
    found = find_commit_metadata_problems(repo, "HEAD~3..HEAD", forbidden_trailers=["Co-Authored-By"])
    assert [(f.path, f.rule) for f in found] == [(bad_bom[:12], "bom-subject"), (bad_trailer[:12], "forbidden-trailer")]
    assert "subject 'code_audit: seed'" in found[0].message
    assert "trailer `Co-Authored-By: Claude <noreply@anthropic.com>` is forbidden here ('Co-Authored-By')" in found[1].message


def test_the_default_trailer_list_is_empty(repo: Path) -> None:
    _commit(repo, f"seed\n\n{COAUTHOR}\n")
    assert find_commit_metadata_problems(repo, "HEAD^!") == []


@pytest.mark.parametrize(
    ("message", "rules"),
    [
        ("​zero width start", ["invisible-subject"]),
        (" leading space", ["invisible-subject"]),
        ("\x07bell", ["invisible-subject"]),
        ("ends with bom﻿", ["bom-subject"]),
        ("plain subject", []),
        ("Ünïcödé subject is fine", []),
    ],
)
def test_subject_rules(message: str, rules: list) -> None:
    assert [r for r, _ in check_message(message)] == rules


@pytest.mark.parametrize(
    ("message", "forbidden", "hits"),
    [
        (f"s\n\nbody\n\n{COAUTHOR}", ["co-authored-by"], 1),  # case-insensitive key
        (f"s\n\nbody\n\n{COAUTHOR}", ["Co-Authored-By: *claude*"], 1),  # value glob
        (f"s\n\nbody\n\n{COAUTHOR}", ["Co-Authored-By: *Gemini*"], 0),
        (f"s\n\n{COAUTHOR}\nSigned-off-by: X <x@y>", ["Signed-off-by"], 1),
        (f"s\n\n{COAUTHOR}\nnot a trailer line", ["Co-Authored-By"], 0),  # the last paragraph is not a trailer block
        (COAUTHOR, ["Co-Authored-By"], 0),  # a one-paragraph message has no trailers
        (f"s\n\n{COAUTHOR}\n\nlater prose", ["Co-Authored-By"], 0),  # trailers live in the LAST paragraph
    ],
)
def test_forbidden_trailers(message: str, forbidden: list, hits: int) -> None:
    assert len([r for r, _ in check_message(message, forbidden_trailers=forbidden) if r == "forbidden-trailer"]) == hits


def test_parse_trailers_folds_continuation_lines() -> None:
    assert parse_trailers("s\n\nKey: one\n  two\nOther: x") == [("Key", "one two"), ("Other", "x")]


def test_bots_and_merges_are_skipped_but_bot_suffix_is_literal(repo: Path) -> None:
    _commit(repo, f"bump\n\n{COAUTHOR}\n", author="dependabot[bot]")
    _commit(repo, f"seed\n\n{COAUTHOR}\n", author="fingoldo")  # ends in 'o': a glob `*[bot]` would have skipped it
    found = find_commit_metadata_problems(repo, "HEAD~2..HEAD", forbidden_trailers=["Co-Authored-By"])
    assert len(found) == 1
    _git(repo, "checkout", "-q", "-b", "side", "HEAD~1")
    (repo / "side.txt").write_text("side")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-q", "-m", "side work")
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "side", "-m", BOM + "Merge side")
    assert [f.rule for f in find_commit_metadata_problems(repo, "HEAD^!")] == []
    assert [f.rule for f in find_commit_metadata_problems(repo, "HEAD^!", include_merges=True)] == ["bom-subject"]


def test_a_bad_range_raises_instead_of_reading_as_empty(repo: Path) -> None:
    with pytest.raises(CoreError, match="no-such-ref"):
        read_commits(repo, "no-such-ref..HEAD")


def test_assert_defaults_to_head_without_an_upstream(repo: Path) -> None:
    _commit(repo, BOM + "bad")
    with pytest.raises(AssertionError, match="bom-subject"):
        assert_commit_metadata(repo)
    _commit(repo, "good")
    assert_commit_metadata(repo)


def test_cli_message_file_range_and_pyproject_config(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_bytes(f"seed\n\n{COAUTHOR}\n# Please enter the commit message\n".encode())
    assert main(["--repo", str(repo), "--message-file", str(msg)]) == 0  # no configured list: nothing forbidden
    assert main(["--repo", str(repo), "--message-file", str(msg), "--forbid", "Co-Authored-By"]) == 1
    (repo / "pyproject.toml").write_text('[tool.py_ci_shared.gates.commit_metadata]\nforbidden_trailers = ["Co-Authored-By"]\n')
    assert main(["--repo", str(repo), "--message-file", str(msg)]) == 1
    msg.write_bytes(("﻿seed\n").encode())
    assert main(["--repo", str(repo), "--message-file", str(msg)]) == 1
    assert "bom-subject" in capsys.readouterr().out
    _commit(repo, f"seed\n\n{COAUTHOR}\n")
    assert main(["--repo", str(repo), "--range", "HEAD^!"]) == 1
    assert main(["--repo", str(repo), "--range", "HEAD^!", "--forbid", "Nothing"]) == 0
    assert main(["--repo", str(repo), "--range", "nope..HEAD"]) == 1
    assert "nope" in capsys.readouterr().err
