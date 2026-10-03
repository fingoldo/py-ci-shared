"""Tests for py_ci_shared.hook_attestation: the trailer a commit-msg hook writes, and the range check over it.

The end-to-end tests drive real git (and pre-commit's own hook when it is installed): a commit through the hooks
carries the trailer, ``--no-verify`` leaves none, ``SKIP=`` is recorded, and the range check reports exactly those.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import hook_attestation as ha
from py_ci_shared._core import CorpusError

CONFIG_BYTES = b"repos: []\n"


def _git(repo: Path, *args: str, env: "dict[str, str] | None" = None) -> str:
    full_env = dict(os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="t@example.com")
    full_env.update(env or {})
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=full_env, check=True)
    return proc.stdout


def _repo(tmp_path: Path, *, config: bytes = CONFIG_BYTES, pre_commit_hook: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    if config:
        (repo / ha.CONFIG).write_bytes(config)
    if pre_commit_hook:
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_bytes(b"#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
    return repo


def _commit(repo: Path, name: str, *extra: str, env: "dict[str, str] | None" = None) -> str:
    (repo / name).write_bytes(name.encode() + b"\n")
    _git(repo, "add", "-A")
    src = str(Path(ha.__file__).resolve().parents[1])
    merged = {"PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    merged.update(env or {})
    _git(repo, "commit", "-q", "-m", f"add {name}", *extra, env=merged)
    return _git(repo, "rev-parse", "HEAD").strip()


def _commit_obj(sha: str = "a" * 40, author: str = "dev", trailers: "tuple[str, ...]" = ()) -> ha.Commit:
    return ha.Commit(sha, author, "subject", trailers)


class TestAttest:
    def test_a_commit_without_the_trailer_is_unverified(self):
        found = ha.attest([_commit_obj()], lambda sha: "abc")
        assert [(f.path, f.rule) for f in found] == [("a" * 12, ha.RULE_UNVERIFIED)]
        assert "--no-verify" in found[0].message

    def test_a_matching_trailer_passes(self):
        assert ha.attest([_commit_obj(trailers=("abc",))], lambda sha: "abc") == []

    def test_a_trailer_for_another_config_is_stale(self):
        found = ha.attest([_commit_obj(trailers=("old123",))], lambda sha: "new456")
        assert [f.rule for f in found] == [ha.RULE_STALE]
        assert "old123" in found[0].message and "new456" in found[0].message

    def test_skipped_hooks_are_reported_with_their_ids(self):
        found = ha.attest([_commit_obj(trailers=("abc; skipped=mypy,ruff",))], lambda sha: "abc")
        assert [f.rule for f in found] == [ha.RULE_SKIPPED]
        assert "mypy,ruff" in found[0].message

    def test_the_last_trailer_wins_after_an_amend(self):
        assert ha.attest([_commit_obj(trailers=("old", "abc"))], lambda sha: "abc") == []

    def test_bots_and_allowed_authors_are_left_out(self):
        commits = [_commit_obj(author="dependabot[bot]"), _commit_obj(author="renovate[bot]"), _commit_obj(author="release-robot")]
        assert ha.attest(commits, lambda sha: "abc", allow_authors=["release-robot"]) == []

    def test_a_commit_without_a_config_has_nothing_to_attest(self):
        assert ha.attest([_commit_obj()], lambda sha: None) == []


def test_trailer_value_sorts_and_dedupes_skip_ids():
    assert ha.trailer_value(CONFIG_BYTES) == ha.config_hash(CONFIG_BYTES)
    assert ha.trailer_value(CONFIG_BYTES, " ruff, mypy ,ruff,") == ha.config_hash(CONFIG_BYTES) + "; skipped=mypy,ruff"
    assert len(ha.config_hash(CONFIG_BYTES)) == 12


class TestEndToEnd:
    def test_hooked_bypassed_and_skipped_commits(self, tmp_path):
        repo = _repo(tmp_path)
        ha.install(repo)
        first = _commit(repo, "a.txt")
        bypassed = _commit(repo, "b.txt", "--no-verify")
        skipped = _commit(repo, "c.txt", env={"SKIP": "mypy"})

        found = ha.find_hook_attestation(repo, f"{first}^!")
        assert found == []  # the first commit went through the hooks
        found = ha.find_hook_attestation(repo, f"{first}..HEAD")
        assert sorted((f.path, f.rule) for f in found) == sorted([(bypassed[:12], ha.RULE_UNVERIFIED), (skipped[:12], ha.RULE_SKIPPED)])

    def test_a_config_changed_without_rerunning_the_stamp_is_stale(self, tmp_path):
        repo = _repo(tmp_path)
        ha.install(repo)
        _commit(repo, "a.txt")
        message = _git(repo, "log", "-1", "--format=%B")
        (repo / ha.CONFIG).write_bytes(b"repos: [] # changed\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "--no-verify", "-m", message)  # the old trailer copied by hand onto a new config
        assert [f.rule for f in ha.find_hook_attestation(repo, "HEAD^!")] == [ha.RULE_STALE]

    def test_no_trailer_without_an_installed_pre_commit_hook(self, tmp_path):
        repo = _repo(tmp_path, pre_commit_hook=False)
        ha.install(repo)
        _commit(repo, "a.txt")
        assert [f.rule for f in ha.find_hook_attestation(repo, "HEAD^!")] == [ha.RULE_UNVERIFIED]

    def test_an_all_zero_base_checks_the_tip_alone(self, tmp_path):
        repo = _repo(tmp_path)
        _commit(repo, "a.txt", "--no-verify")
        _commit(repo, "b.txt", "--no-verify")
        assert len(ha.find_hook_attestation(repo, "0" * 40 + "..HEAD")) == 1
        assert len(ha.find_hook_attestation(repo, "HEAD~1..HEAD")) == 1
        assert len(ha.find_hook_attestation(repo, "HEAD~1^!")) == 1

    def test_merges_are_not_checked(self, tmp_path):
        repo = _repo(tmp_path)
        base = _commit(repo, "a.txt", "--no-verify")
        _git(repo, "checkout", "-q", "-b", "side")
        _commit(repo, "b.txt", "--no-verify")
        _git(repo, "checkout", "-q", "-")
        _commit(repo, "c.txt", "--no-verify")
        _git(repo, "merge", "-q", "--no-verify", "--no-edit", "side")
        assert len(ha.find_hook_attestation(repo, f"{base}..HEAD")) == 2  # b and c, not the merge


class TestInstallAndCli:
    def test_install_is_idempotent_and_refuses_a_foreign_hook(self, tmp_path):
        repo = _repo(tmp_path)
        path = ha.install(repo, python="/usr/bin/python3")
        assert ha.install(repo, python="/usr/bin/python3") == path
        assert '"/usr/bin/python3" -m py_ci_shared.hook_attestation commit-msg "$1"' in path.read_text(encoding="utf-8")
        path.write_bytes(b"#!/bin/sh\necho mine\n")
        with pytest.raises(CorpusError, match="not the hook_attestation hook"):
            ha.install(repo)

    def test_check_exit_codes_and_warn_only(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        _commit(repo, "a.txt", "--no-verify")
        assert ha.main(["check", "--repo", str(repo), "--range", "HEAD^!"]) == 1
        assert "[hook-unverified]" in capsys.readouterr().out
        assert ha.main(["check", "--repo", str(repo), "--range", "HEAD^!", "--warn-only"]) == 0
        assert "::warning title=hook attestation::" in capsys.readouterr().out
        assert ha.main(["check", "--repo", str(repo), "--range", "HEAD^!", "--allow-author", "tester"]) == 0

    def test_a_bad_range_is_a_usage_error_not_a_pass(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        _commit(repo, "a.txt", "--no-verify")
        assert ha.main(["check", "--repo", str(repo), "--range", "nope..HEAD"]) == 2
        assert "hook_attestation:" in capsys.readouterr().err

    def test_the_commit_msg_entry_never_blocks_a_commit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # not a repository: the stamp fails, the hook still exits 0
        msg = tmp_path / "MSG"
        msg.write_text("subject\n", encoding="utf-8")
        assert ha.main(["commit-msg", str(msg)]) == 0
        assert msg.read_text(encoding="utf-8") == "subject\n"

    def test_assert_raises_with_the_findings(self, tmp_path):
        repo = _repo(tmp_path)
        _commit(repo, "a.txt", "--no-verify")
        with pytest.raises(AssertionError, match="did not go through their pre-commit hooks"):
            ha.assert_hook_attestation(repo, "HEAD^!")


def test_hook_script_runs_the_installing_interpreter():
    assert ha.hook_script(sys.executable).startswith("#!/bin/sh\n")
