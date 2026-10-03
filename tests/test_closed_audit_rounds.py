"""Tests for py_ci_shared.closed_audit_rounds, over real throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError
from py_ci_shared.closed_audit_rounds import RULE, assert_closed_audit_rounds_append_only, find_closed_round_edits, main

REPORT = "# Round\n\n### F-1\n\nThe seed finding text.\n\n**Disposition:** OPEN\n"
CLOSED = "audits/implemented/2026-09-01/report.md"


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@e", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True).stdout.decode()


def _put(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_bytes(text.encode())


def _commit(repo: Path, message: str = "change") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _put(tmp_path, CLOSED, REPORT)
    _put(tmp_path, "audits/implemented/2026-09-01/TRACKER.md", "| status | id |\n|---|---|\n| OPEN | F-1 |\n")
    _put(tmp_path, "audits/2026-10-01/open.md", "open round\n")
    _commit(tmp_path, "base")
    _git(tmp_path, "tag", "base")
    return tmp_path


def _found(repo: Path, **kw: object) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.message) for f in find_closed_round_edits(repo, "base", **kw)]  # type: ignore[arg-type]


def test_reports_a_rewritten_finding_line(repo: Path) -> None:
    _put(repo, CLOSED, REPORT.replace("The seed finding text.", "A softer text."))
    _commit(repo)
    assert _found(repo) == [
        (
            CLOSED,
            5,
            "line 5 of a closed round deleted or rewritten (was 'The seed finding text.'); append instead, or declare it with `Audit-Edit: <reason>`",
        )
    ]
    assert find_closed_round_edits(repo, "base")[0].rule == RULE


def test_appending_and_updating_dispositions_and_tracker_rows_is_allowed(repo: Path) -> None:
    _put(repo, CLOSED, REPORT.replace("**Disposition:** OPEN", "**Disposition:** RESOLVED -- fixed in x.py") + "\n### F-1 follow-up\n\nmore\n")
    _put(repo, "audits/implemented/2026-09-01/TRACKER.md", "| status | id |\n|---|---|\n| RESOLVED | F-1 |\n")
    _put(repo, "audits/2026-10-01/open.md", "rewritten freely: the round is open\n")
    _commit(repo)
    assert _found(repo) == []


def test_closing_an_open_round_by_moving_it_in_is_not_an_edit(repo: Path) -> None:
    _git(repo, "mv", "audits/2026-10-01", "audits/implemented/2026-10-01")
    _put(repo, "audits/implemented/2026-10-01/open.md", "changed while being closed\n")
    _commit(repo)
    assert _found(repo) == []


def test_deleting_or_moving_out_a_closed_file_is_reported(repo: Path) -> None:
    (repo / "attic").mkdir()
    _git(repo, "mv", CLOSED, "attic/report.md")
    _git(repo, "rm", "-q", "audits/implemented/2026-09-01/TRACKER.md")
    _commit(repo)
    messages = [m for _, _, m in _found(repo)]
    assert any("moved out of audits/implemented/ to attic/report.md" in m for m in messages)
    assert any("a closed audit file was deleted" in m for m in messages)


def test_a_table_row_outside_a_tracker_is_frozen(repo: Path) -> None:
    _put(repo, CLOSED, REPORT + "| a | b |\n")
    _commit(repo)
    _git(repo, "tag", "-f", "base")
    _put(repo, CLOSED, REPORT + "| a | c |\n")
    _commit(repo)
    assert [line for _, line, _ in _found(repo)] == [8]


def test_an_audit_edit_trailer_on_a_commit_touching_the_file_exempts_it(repo: Path) -> None:
    _put(repo, CLOSED, REPORT.replace("seed", "fixed-typo"))
    _commit(repo, "fix a typo\n\nAudit-Edit: the link pointed at the wrong file")
    assert _found(repo) == []
    _commit(repo, "unrelated\n\nAudit-Edit: elsewhere")  # touches nothing: exempts nothing on its own
    _put(repo, CLOSED, REPORT.replace("seed", "again"))
    _commit(repo, "x")
    assert _found(repo) == []  # the file still has a declared edit in the range
    assert len(_found(repo, trailer="Other-Key")) == 1


def test_line_ending_changes_are_ignored(repo: Path) -> None:
    _put(repo, CLOSED, REPORT.replace("\n", "\r\n"))
    _commit(repo)
    assert _found(repo) == []


def test_an_unknown_base_raises(repo: Path) -> None:
    with pytest.raises(CoreError):
        find_closed_round_edits(repo, "no-such-ref")


def test_assert_and_cli(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert_closed_audit_rounds_append_only(repo, base="base")
    assert main(["--repo", str(repo), "--base", "base"]) == 0
    _put(repo, CLOSED, REPORT.replace("The seed finding text.\n", ""))
    _commit(repo)
    with pytest.raises(AssertionError, match="1 edit"):
        assert_closed_audit_rounds_append_only(repo, base="base")
    assert main(["--repo", str(repo), "--base", "base"]) == 1
    assert main(["--repo", str(repo), "--base", "nope"]) == 1
    assert "nope" in capsys.readouterr().err
