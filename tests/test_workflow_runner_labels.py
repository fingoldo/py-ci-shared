"""Tests for py_ci_shared.workflow_runner_labels."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import CoreError
from py_ci_shared.workflow_runner_labels import assert_workflow_runner_labels_pinned, find_workflow_runner_label_problems, fix_workflow_runner_labels, main

WF = """name: ci
on: push
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]  # both move
    steps:
      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567  # v5
      - uses: ./.github/actions/local
      - run: echo "macos-latest is only text here" # a comment about ubuntu-latest
  lint:
    runs-on: macos-latest  # moving-label-ok: needs the newest Xcode
    steps:
      - run: echo hi
"""


def _repo(tmp_path: Path, wf: str = WF, dependabot: str = "") -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_bytes(wf.encode())
    if dependabot:
        (tmp_path / ".github" / "dependabot.yml").write_bytes(dependabot.encode())
    return tmp_path


def _found(root: Path) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.rule) for f in find_workflow_runner_label_problems(root)]


def test_reports_moving_labels_and_the_missing_update_channel(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _found(root) == [
        (".github/workflows/ci.yml", 8, "moving-runner-label"),
        (".github/workflows/ci.yml", 8, "moving-runner-label"),
        (".github/workflows/ci.yml", 12, "moving-runner-label"),  # inside a quoted run string: still a label in YAML text
        (".github/dependabot.yml", 1, "no-actions-update-channel"),
    ]
    assert "1 pinned `uses:`" in find_workflow_runner_label_problems(root)[-1].message


def test_a_github_actions_update_channel_or_renovate_satisfies_the_pin_rule(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        WF.replace("ubuntu-latest, windows-latest", "ubuntu-24.04").replace('echo "macos-latest is only text here"', "true"),
        "version: 2\nupdates:\n  - package-ecosystem: 'github-actions'\n    directory: /\n",
    )
    assert _found(root) == []
    (root / ".github" / "dependabot.yml").write_text("version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n")
    assert [r for _, _, r in _found(root)] == ["no-actions-update-channel"]
    (root / "renovate.json").write_text("{}")
    assert _found(root) == []


def test_only_local_actions_need_no_channel(tmp_path: Path) -> None:
    root = _repo(tmp_path, "jobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: ./local\n")
    assert _found(root) == []


def test_the_floor(tmp_path: Path) -> None:
    with pytest.raises(CoreError, match="0 workflow file"):
        find_workflow_runner_label_problems(tmp_path, min_files=1)
    with pytest.raises(CoreError, match="0 workflow file"):
        assert_workflow_runner_labels_pinned(tmp_path)
    assert find_workflow_runner_label_problems(tmp_path) == []


def test_fix_rewrites_labels_keeps_crlf_and_adds_the_update_channel(tmp_path: Path) -> None:
    root = _repo(tmp_path, WF.replace("\n", "\r\n"))
    assert fix_workflow_runner_labels(root, labels={"windows": "windows-2022"}) == [".github/workflows/ci.yml", ".github/dependabot.yml"]
    data = (root / ".github/workflows/ci.yml").read_bytes()
    assert b"[ubuntu-24.04, windows-2022]  # both move\r\n" in data and b"runs-on: macos-latest  # moving-label-ok" in data
    assert b"\r\r" not in data and b"a comment about ubuntu-latest" in data
    assert 'package-ecosystem: "github-actions"' in (root / ".github/dependabot.yml").read_text()
    assert b'echo "macos-15 is only text here"' in data  # the fix rewrites what the check reports, no more
    assert _found(root) == []


def test_fix_extends_an_existing_dependabot_file_only_when_updates_is_last(tmp_path: Path) -> None:
    root = _repo(tmp_path, dependabot="version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n")
    fix_workflow_runner_labels(root)
    text = (root / ".github/dependabot.yml").read_text()
    assert text.count("package-ecosystem") == 2
    (root / ".github/dependabot.yml").write_text("updates:\n  - package-ecosystem: pip\nregistries:\n  x: {}\n")
    with pytest.raises(CoreError, match="not the last top-level key"):
        fix_workflow_runner_labels(root)


def test_assert_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _repo(tmp_path)
    with pytest.raises(AssertionError, match="--fix"):
        assert_workflow_runner_labels_pinned(root)
    assert main([str(root)]) == 1
    assert main([str(root), "--fix"]) == 0
    assert "fixed .github/workflows/ci.yml" in capsys.readouterr().out
    assert main([str(root), "--label", "bad"]) == 1
