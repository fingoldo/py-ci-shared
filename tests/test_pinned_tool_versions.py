"""The ruff version is defined once, in tool_versions.py, and everything else is checked against it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared import pinned_tool_versions as ptv
from py_ci_shared.tool_versions import RUFF_VERSION

REPO = Path(__file__).resolve().parent.parent
_PIN = f'    "ruff=={RUFF_VERSION}",  # a comment\n'


def _matching(_module: str) -> str:
    return RUFF_VERSION


class TestFindProblems:
    def test_all_three_agree(self) -> None:
        assert ptv.find_problems(_PIN, installed=_matching) == []

    def test_a_pin_behind_the_shared_version_is_named_with_both_versions(self) -> None:
        problems = ptv.find_problems('"ruff==0.0.1",', installed=_matching)

        assert len(problems) == 1
        assert "pins 0.0.1" in problems[0] and RUFF_VERSION in problems[0]

    def test_a_missing_pin_is_reported(self) -> None:
        assert "no exact pin" in ptv.find_problems('"ruff>=0.1"', installed=_matching)[0]

    def test_the_installed_tool_is_checked_even_when_the_pin_agrees(self) -> None:
        problems = ptv.find_problems(_PIN, installed=lambda m: "9.9.9")

        assert problems == [p for p in problems if "this interpreter has 9.9.9" in p] and len(problems) == 1

    def test_a_tool_that_cannot_run_is_reported(self) -> None:
        assert "not runnable" in ptv.find_problems(_PIN, installed=lambda m: None)[0]

    def test_a_marker_after_the_pin_does_not_leak_into_the_version(self) -> None:
        assert ptv.pinned_version('"ruff==0.16.1 ; python_version >= \'3.9\'"', "ruff") == "0.16.1"


def test_main_reads_the_given_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ptv, "installed_version", _matching)
    monkeypatch.setattr(ptv.find_problems, "__defaults__", (_matching, ""))
    good = tmp_path / "good.toml"
    good.write_text(_PIN, encoding="utf-8")
    bad = tmp_path / "bad.toml"
    bad.write_text('"ruff==0.0.1"', encoding="utf-8")
    no_precommit = str(tmp_path / "absent.yaml")

    assert ptv.main(["--pyproject", str(good), "--precommit", no_precommit]) == 0
    assert ptv.main(["--pyproject", str(bad), "--precommit", no_precommit]) == 1
    assert "pinned-tool-version mismatch: ruff: pyproject.toml pins 0.0.1" in capsys.readouterr().out


class TestRuffPreCommitRev:
    _CONFIG = "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: v{}\n    hooks:\n      - id: ruff\n"

    def test_a_matching_rev_passes(self) -> None:
        assert ptv.find_problems(_PIN, installed=_matching, precommit_text=self._CONFIG.format(RUFF_VERSION)) == []

    def test_a_rev_behind_the_shared_version_is_named(self) -> None:
        problems = ptv.find_problems(_PIN, installed=_matching, precommit_text=self._CONFIG.format("0.11.0"))

        assert len(problems) == 1 and "ruff-pre-commit at v0.11.0" in problems[0]

    def test_every_ruff_pre_commit_block_is_read(self) -> None:
        text = self._CONFIG.format("0.1.0") + self._CONFIG.format("0.2.0").replace("repos:\n", "")

        assert ptv.precommit_ruff_revs(text) == ["0.1.0", "0.2.0"]


def test_this_repos_own_dev_pin_is_the_shared_version() -> None:
    """timezone_honest's tests run the dev-extra ruff; it must be the version the workflows run."""
    assert ptv.pinned_version((REPO / "pyproject.toml").read_text(encoding="utf-8"), "ruff") == RUFF_VERSION


def test_no_workflow_hardcodes_a_ruff_version() -> None:
    """Every ruff invocation in a workflow must take the version from tool_versions.py."""
    hardcoded = [
        f"{p.name}:{n}"
        for p in sorted((REPO / ".github" / "workflows").glob("*.yml"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"ruff==[0-9]", line)
    ]

    assert not hardcoded, f"use ruff==${{RUFF_VERSION}} from py_ci_shared/tool_versions.py instead: {hardcoded}"


@pytest.mark.parametrize("workflow", ["ruff-blocking.yml", "lint-advisory.yml", "self-ci.yml"])
def test_each_ruff_workflow_resolves_the_shared_version(workflow: str) -> None:
    text = (REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8")

    assert "tool_versions.py" in text and 'ruff==${RUFF_VERSION}' in text
