"""Tests for py_ci_shared.git_changed_lines.

Each case is one of the traps named in the module docstring. They are traps because every one of
them produces a plausible-looking WRONG range rather than an error, and a check scoped by a wrong
range examines the wrong code while reporting success.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared.git_changed_lines import changed_lines, lines_for


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_a_single_line_change_is_found(repo: Path):
    """The `,d` group is OMITTED from a hunk header when d == 1, so a parser that splits on the
    comma drops every single-line change -- the most common kind there is."""
    text = (repo / "a.py").read_text(encoding="utf-8").split("\n")
    text[4] = "CHANGED"
    (repo / "a.py").write_text("\n".join(text), encoding="utf-8")

    result = changed_lines(repo)

    assert lines_for(result, "a.py") == [range(5, 6)]


def test_several_hunks_are_reported_separately(repo: Path):
    """A real commit touches several places. One merged range would pull in everything between
    them, which is the opposite of scoping."""
    text = (repo / "a.py").read_text(encoding="utf-8").split("\n")
    text[1] = "CHANGED_EARLY"
    text[15] = "CHANGED_LATE"
    (repo / "a.py").write_text("\n".join(text), encoding="utf-8")

    ranges = lines_for(changed_lines(repo), "a.py")

    assert ranges == [range(2, 3), range(16, 17)]


def test_a_pure_deletion_contributes_no_range(repo: Path):
    """`d == 0` marks a deletion. There are no NEW lines to mutate, and a naive parser turns it into
    a one-line range pointing at whatever now occupies that position."""
    text = (repo / "a.py").read_text(encoding="utf-8").split("\n")
    del text[4:8]
    (repo / "a.py").write_text("\n".join(text), encoding="utf-8")

    ranges = lines_for(changed_lines(repo), "a.py")

    assert all(len(r) > 0 for r in ranges)
    assert 5 not in [r.start for r in ranges] or all(r.stop > r.start for r in ranges)


def test_context_lines_are_not_counted_as_changed(repo: Path):
    """Without `--unified=0` the hunk header covers three unchanged lines either side, so a
    "changed lines" set built from it includes lines the commit never touched."""
    text = (repo / "a.py").read_text(encoding="utf-8").split("\n")
    text[9] = "CHANGED"
    (repo / "a.py").write_text("\n".join(text), encoding="utf-8")

    ranges = lines_for(changed_lines(repo), "a.py")

    assert ranges == [range(10, 11)], "context lines leaked into the range"


def test_an_untracked_file_is_reported_whole(repo: Path):
    """Every line of a new file is a changed line, and a check that skipped new files would skip
    exactly the code most likely to be under-tested."""
    (repo / "new.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    ranges = lines_for(changed_lines(repo), "new.py")

    assert ranges and ranges[0].start == 1 and ranges[0].stop - 1 >= 3


def test_untracked_files_can_be_excluded(repo: Path):
    (repo / "new.py").write_text("one\n", encoding="utf-8")

    assert lines_for(changed_lines(repo, include_untracked=False), "new.py") == []


def test_a_path_with_a_space_survives(repo: Path):
    """git quotes and C-escapes such paths unless `-z` is used, and a quoted path silently fails to
    match anything the caller looks up."""
    (repo / "two words.py").write_text("x = 1\n", encoding="utf-8")

    assert lines_for(changed_lines(repo), "two words.py")


def test_a_clean_tree_reports_nothing(repo: Path):
    """Distinguishable from a failure, which raises."""
    assert changed_lines(repo) == {}


def test_a_bad_revision_raises_rather_than_returning_empty(repo: Path):
    """An empty result must mean "nothing changed", never "the command failed" -- otherwise a
    misconfigured hook silently checks zero lines and passes."""
    with pytest.raises(RuntimeError):
        changed_lines(repo, rev="no-such-revision")
