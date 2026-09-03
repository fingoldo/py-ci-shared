"""Unit tests for the repo hygiene check. Real scratch repos (git init) and workflow files, same
no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.repo_hygiene import (
    assert_repo_hygiene,
    find_missing_required_files,
    find_tracked_generated_files,
    find_unguarded_numeric_gates,
)


def _git_repo(tmp_path: Path, *tracked: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel in tracked:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    # -f: these fixtures deliberately track files a developer's global gitignore excludes
    # (__pycache__, *.pyc). Without it the test's own premise depends on whose machine it runs on.
    subprocess.run(["git", "add", "-A", "-f"], cwd=tmp_path, check=True)
    return tmp_path


def _workflows(tmp_path: Path, body: str) -> Path:
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    (d / "ci.yml").write_text(body, encoding="utf-8")
    return d


class TestTrackedGeneratedFiles:
    def test_pycache_is_flagged(self, tmp_path):
        repo = _git_repo(tmp_path, "tool/__pycache__/x.cpython-314.pyc", "tool/x.py")
        hits = find_tracked_generated_files(repo)
        assert len(hits) == 1
        assert "__pycache__" in hits[0]

    def test_clean_repo_passes(self, tmp_path):
        repo = _git_repo(tmp_path, "lib/main.dart", "README.md")
        assert find_tracked_generated_files(repo) == []

    def test_untracked_pycache_is_not_flagged(self, tmp_path):
        repo = _git_repo(tmp_path, "lib/main.dart")
        (repo / "__pycache__").mkdir()
        (repo / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
        assert find_tracked_generated_files(repo) == []


class TestRequiredFiles:
    def test_missing_file_is_named(self, tmp_path):
        assert find_missing_required_files(tmp_path, ["analysis_options.yaml"]) == ["analysis_options.yaml"]

    def test_present_file_passes(self, tmp_path):
        (tmp_path / "analysis_options.yaml").write_text("include: package:flutter_lints/flutter.yaml\n", encoding="utf-8")
        assert find_missing_required_files(tmp_path, ["analysis_options.yaml"]) == []


class TestNumericGates:
    def test_unguarded_bc_comparison_is_flagged(self, tmp_path):
        wf = _workflows(
            tmp_path,
            'jobs:\n  a:\n    steps:\n      - run: |\n          COV=$(grep -oP "\\d+" out.txt)\n          if (( $(echo "$COV < 80" | bc -l) )); then exit 1; fi\n',
        )
        problems = find_unguarded_numeric_gates(wf)
        assert len(problems) == 1
        assert "COV" in problems[0]

    def test_emptiness_guard_satisfies_it(self, tmp_path):
        wf = _workflows(
            tmp_path,
            'jobs:\n  a:\n    steps:\n      - run: |\n          COV=$(grep -oP "\\d+" out.txt)\n          [ -n "$COV" ] || exit 1\n          if (( $(echo "$COV < 80" | bc -l) )); then exit 1; fi\n',
        )
        assert find_unguarded_numeric_gates(wf) == []

    def test_test_style_comparison_is_flagged(self, tmp_path):
        wf = _workflows(
            tmp_path,
            'jobs:\n  a:\n    steps:\n      - run: |\n          N=$(wc -l < f)\n          [ "$N" -lt 5 ] && exit 1\n',
        )
        assert len(find_unguarded_numeric_gates(wf)) == 1

    def test_parameter_default_counts_as_a_guard(self, tmp_path):
        wf = _workflows(
            tmp_path,
            'jobs:\n  a:\n    steps:\n      - run: |\n          N=${N:-0}\n          [ "$N" -lt 5 ] && exit 1\n',
        )
        assert find_unguarded_numeric_gates(wf) == []


class TestAssert:
    def test_assert_passes_on_a_clean_repo(self, tmp_path):
        repo = _git_repo(tmp_path, "README.md", "analysis_options.yaml")
        _workflows(repo, "jobs:\n  a:\n    steps:\n      - run: echo hi\n")
        assert_repo_hygiene(repo, required_files=["analysis_options.yaml"], workflows_dir=repo / ".github" / "workflows")

    def test_assert_fails_on_tracked_pycache(self, tmp_path):
        repo = _git_repo(tmp_path, "tool/__pycache__/x.pyc")
        with pytest.raises(pytest.fail.Exception, match="__pycache__"):
            assert_repo_hygiene(repo)
