"""Unit tests for the version/tag currency check. Real scratch git repos with real tags, same
no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.version_tag_currency import (
    assert_version_is_tagged,
    declared_version,
    find_stale_pin,
    find_version_tag_problems,
    semver_tags,
)


def _repo(tmp_path: Path, version: str, tags: tuple[str, ...] = ()) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "pubspec.yaml").write_text(f"name: pkg\nversion: {version}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)
    for t in tags:
        subprocess.run(["git", "tag", t], cwd=tmp_path, check=True)
    return tmp_path


class TestDeclaredVersion:
    def test_pubspec_version_is_read(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0")
        assert declared_version(repo, "pubspec.yaml") == "0.6.0"

    def test_pyproject_version_is_read(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
        assert declared_version(tmp_path, "pyproject.toml") == "1.2.3"

    def test_missing_manifest_returns_none(self, tmp_path):
        assert declared_version(tmp_path, "pubspec.yaml") is None


class TestTags:
    def test_tags_sort_numerically_not_lexically(self, tmp_path):
        repo = _repo(tmp_path, "0.10.0", ("v0.2.0", "v0.10.0", "v0.9.0"))
        assert semver_tags(repo) == ["v0.2.0", "v0.9.0", "v0.10.0"]

    def test_untagged_version_is_flagged(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0", ("v0.5.9",))
        problems = find_version_tag_problems(repo, "pubspec.yaml")
        assert len(problems) == 1
        assert "no matching tag" in problems[0]

    def test_tagged_version_passes(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0", ("v0.6.0",))
        assert find_version_tag_problems(repo, "pubspec.yaml") == []

    def test_tag_not_reachable_from_head_is_flagged(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0")
        subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo, check=True)
        (repo / "x.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "side"], cwd=repo, check=True)
        subprocess.run(["git", "tag", "v0.6.0"], cwd=repo, check=True)
        subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
        problems = find_version_tag_problems(repo, "pubspec.yaml")
        assert len(problems) == 1
        assert "not an ancestor" in problems[0]


class TestStalePin:
    def test_pin_behind_is_reported(self, tmp_path):
        pkg = _repo(tmp_path / "pkg", "0.6.0", ("v0.4.0", "v0.5.0", "v0.6.0"))
        consumer = tmp_path / "app" / "pubspec.yaml"
        consumer.parent.mkdir(parents=True)
        consumer.write_text(
            "dependencies:\n  flutter_app_core:\n    git:\n      url: x\n      ref: v0.4.0\n",
            encoding="utf-8",
        )
        msg = find_stale_pin(consumer, pkg, "flutter_app_core")
        assert msg is not None
        assert "2 release(s) behind" in msg

    def test_current_pin_is_silent(self, tmp_path):
        pkg = _repo(tmp_path / "pkg", "0.6.0", ("v0.6.0",))
        consumer = tmp_path / "app" / "pubspec.yaml"
        consumer.parent.mkdir(parents=True)
        consumer.write_text("dependencies:\n  flutter_app_core:\n    git:\n      ref: v0.6.0\n", encoding="utf-8")
        assert find_stale_pin(consumer, pkg, "flutter_app_core") is None


class TestAssert:
    def test_assert_passes(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0", ("v0.6.0",))
        assert_version_is_tagged(repo, "pubspec.yaml")

    def test_assert_fails(self, tmp_path):
        repo = _repo(tmp_path, "0.7.0", ("v0.6.0",))
        with pytest.raises(pytest.fail.Exception, match="no matching tag"):
            assert_version_is_tagged(repo, "pubspec.yaml")


class TestAuditRegressions:
    def test_a_git_failure_is_cannot_determine_not_not_an_ancestor(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path, "0.6.0", ("v0.6.0",))
        from py_ci_shared import version_tag_currency as vtc

        real = vtc._run_git
        calls: list = []

        def fake(repo_, *args):  # type: ignore[no-untyped-def]
            calls.append(args)
            if args[:2] == ("merge-base", "--is-ancestor"):
                return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: bad object")
            return real(repo_, *args)

        monkeypatch.setattr(vtc, "_run_git", fake)
        (problem,) = find_version_tag_problems(repo, "pubspec.yaml")
        assert "cannot determine" in problem and "not an ancestor" not in problem
        assert sum(1 for a in calls if a[:1] == ("merge-base",)) == 1

    def test_missing_git_is_reported_not_raised(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path, "0.6.0", ("v0.6.0",))
        from py_ci_shared import version_tag_currency as vtc

        def no_git(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise FileNotFoundError("git")

        monkeypatch.setattr(vtc.subprocess, "run", no_git)
        (problem,) = find_version_tag_problems(repo, "pubspec.yaml")
        assert "git is not available" in problem

    def test_the_pin_is_read_from_the_named_dependency_only(self, tmp_path):
        pkg = _repo(tmp_path / "pkg", "0.6.0", ("v0.4.0", "v0.5.0", "v0.6.0"))
        consumer = tmp_path / "app" / "pubspec.yaml"
        consumer.parent.mkdir(parents=True)
        consumer.write_text(
            "dependencies:\n"
            "  app_core:\n    git:\n      url: x\n      ref: v0.4.0\n"
            "  core:\n    path: ../core\n"
            "  other:\n    git:\n      url: y\n      ref: 'v0.5.0'\n",
            encoding="utf-8",
        )
        assert find_stale_pin(consumer, pkg, "core") is None
        assert "2 release(s) behind" in (find_stale_pin(consumer, pkg, "app_core") or "")
        assert "1 release(s) behind" in (find_stale_pin(consumer, pkg, "other") or "")

    def test_the_project_version_is_read_not_the_first_version_key(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.foo]\nversion = "9.9.9"\n\n[project]\nname = "p"\nversion = "0.6.0-beta.1"\n', encoding="utf-8")
        assert declared_version(tmp_path, "pyproject.toml") == "0.6.0-beta.1"
        (tmp_path / "pubspec.yaml").write_text("name: p\nversion: 1.2.3+45\n", encoding="utf-8")
        assert declared_version(tmp_path, "pubspec.yaml") == "1.2.3"

    def test_a_prerelease_needs_its_own_tag_and_sorts_before_its_release(self, tmp_path):
        repo = _repo(tmp_path, "0.6.0-beta.1", ("v0.6.0-beta.1", "v0.5.0", "v0.6.0", "v0.6.0-beta.2"))
        assert semver_tags(repo) == ["v0.5.0", "v0.6.0-beta.1", "v0.6.0-beta.2", "v0.6.0"]
        assert find_version_tag_problems(repo, "pubspec.yaml") == []
        other = _repo(tmp_path / "other", "0.7.0-rc.1", ("v0.7.0",))
        assert "no matching tag" in find_version_tag_problems(other, "pubspec.yaml")[0]
