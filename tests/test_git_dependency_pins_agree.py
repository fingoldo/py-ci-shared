"""Tests for pin agreement and for an installed copy that includes its pin (git_dependency_pins, v1.14)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.git_dependency_pins import assert_installed_includes_pin, assert_pins_agree, installed_pin_problem, pinned_shas

A = "a" * 40
B = "b" * 40


class TestPinsAgree:
    def _files(self, tmp_path, second_sha=A):
        (tmp_path / "requirements.txt").write_text(f"pyutilz[web] @ git+https://github.com/fingoldo/pyutilz.git@{A}\n", encoding="utf-8")
        (tmp_path / "ci.yml").write_text(
            f"      - uses: x/install-pyutilz@{'c' * 40}\n        with:\n          pyutilz-ref: {A}  # pragma\n"
            f"        git -C pyutilz checkout {second_sha}\n",
            encoding="utf-8",
        )
        (tmp_path / "uv.lock").write_text(f'source = {{ git = "https://github.com/fingoldo/pyutilz.git#{A}" }}\n', encoding="utf-8")
        return sorted(tmp_path.iterdir())

    def test_every_spelling_is_found_and_the_action_pin_is_not(self, tmp_path):
        found = pinned_shas(self._files(tmp_path), "pyutilz", root=tmp_path)
        assert list(found) == [A]
        assert sorted(found[A]) == ["ci.yml:3", "ci.yml:4", "requirements.txt:1", "uv.lock:1"]

    def test_agreeing_pins_pass_and_return_the_commit(self, tmp_path):
        assert assert_pins_agree(self._files(tmp_path), "pyutilz", root=tmp_path) == A

    def test_one_drifted_pin_fails_and_names_both_commits(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="2 different commits") as caught:
            assert_pins_agree(self._files(tmp_path, second_sha=B), "pyutilz", root=tmp_path)
        assert "aaaaaaaaaaaa" in str(caught.value) and "bbbbbbbbbbbb: ci.yml:4" in str(caught.value)

    def test_too_few_pins_fail(self, tmp_path):
        (tmp_path / "x.txt").write_text("nothing pinned here\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=re.escape("found 0 pin(s)")):
            assert_pins_agree([tmp_path / "x.txt"], "pyutilz")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class TestInstalledIncludesPin:
    @pytest.fixture
    def checkout(self, tmp_path, monkeypatch):
        """A git checkout holding an importable ``pinpkg`` with two commits; returns (first, second) SHAs."""
        repo = tmp_path / "repo"
        (repo / "pinpkg").mkdir(parents=True)
        _git(tmp_path, "init", "-q", str(repo))
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "pinpkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one")
        first = _git(repo, "rev-parse", "HEAD")
        (repo / "pinpkg" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "two")
        second = _git(repo, "rev-parse", "HEAD")
        monkeypatch.syspath_prepend(str(repo))
        monkeypatch.delitem(sys.modules, "pinpkg", raising=False)
        return repo, first, second

    def test_a_checkout_at_or_past_the_pin_passes(self, checkout):
        _repo, first, second = checkout
        assert installed_pin_problem("pinpkg", first) == (None, None)
        assert_installed_includes_pin("pinpkg", second)

    def test_a_checkout_behind_the_pin_fails(self, checkout):
        repo, first, second = checkout
        _git(repo, "checkout", "-q", first)
        with pytest.raises(pytest.fail.Exception, match="does not include the pinned commit"):
            assert_installed_includes_pin("pinpkg", second)

    def test_a_pin_the_checkout_does_not_have_fails(self, checkout):
        with pytest.raises(pytest.fail.Exception, match="without the pinned commit"):
            assert_installed_includes_pin("pinpkg", "f" * 40)

    def test_a_plain_directory_install_skips_with_the_reason(self, tmp_path, monkeypatch):
        (tmp_path / "plainpkg").mkdir()
        (tmp_path / "plainpkg" / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "plainpkg", raising=False)
        problem, skip = installed_pin_problem("plainpkg", A)
        assert problem is None and skip and "records no commit" in skip
