"""The consumer list and its checkout policy: private repos without a token are skipped loudly, failed clones fail."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared import _consumers
from py_ci_shared._consumers import Consumer, checkout, load_consumers, write_repos_file
from py_ci_shared._core import CoreError
from py_ci_shared.adoption_matrix import load_repo_list

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "consumers.toml"


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "consumers.toml"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_checked_in_list_names_every_consumer_with_owner_repo_and_branch():
    consumers = {c.name: c for c in load_consumers(CONFIG)}
    assert set(consumers) == {
        "autopsia",
        "mlframe",
        "pyutilz",
        "social",
        "dash_app_core",
        "llm_bench",
        "glossum_backend_scripts",
        "flutter_app_core",
        "polyvocab_app",
        "noema_app",
        "flutter_uptime_monitor",
        "algopacksimple",
        "claude-usage-notifier",
    }
    assert all(c.repo.startswith("fingoldo/") for c in consumers.values())
    assert consumers["polyvocab_app"].branch == "staging"
    assert consumers["llm_bench"].branch == "main" and consumers["mlframe"].branch == "master"
    corpus = sorted(n for n, c in consumers.items() if c.corpus)
    assert 2 <= len(corpus) <= 3 and all(not consumers[n].private for n in corpus), corpus


@pytest.mark.parametrize(
    "text, message",
    [
        ('[[consumer]]\nname = "a"\nrepo = "o/a"\n', "lacks ['branch']"),
        ('[[consumer]]\nname = "a"\nrepo = "a"\nbranch = "m"\n', "owner/name"),
        ('[[consumer]]\nname = "a"\nrepo = "o/a"\nbranch = "m"\n[[consumer]]\nname = "a"\nrepo = "o/b"\nbranch = "m"\n', "duplicate"),
    ],
)
def test_a_malformed_list_raises(tmp_path, text, message):
    with pytest.raises(CoreError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_consumers(_config(tmp_path, text))


def test_private_defaults_to_true_so_a_forgotten_flag_never_leaks_a_token_free_clone_attempt(tmp_path):
    (c,) = load_consumers(_config(tmp_path, '[[consumer]]\nname = "a"\nrepo = "o/a"\nbranch = "m"\n'))
    assert c.private and not c.corpus and c.url == "https://github.com/o/a.git"


def _fake(fail: set[str]):
    calls: list[tuple[str, Optional[str]]] = []

    def clone(consumer: Consumer, dest: Path, token: Optional[str]) -> Optional[str]:
        calls.append((consumer.name, token))
        if consumer.name in fail:
            return "fatal: repository not found"
        dest.mkdir(parents=True)
        return None

    return clone, calls


PUB = Consumer("pub", "o/pub", "main", private=False)
PRIV = Consumer("priv", "o/priv", "master", private=True)


def test_without_a_token_private_repos_are_skipped_and_public_ones_cloned(tmp_path):
    clone, calls = _fake(set())
    result = checkout([PUB, PRIV], tmp_path / "d", token=None, clone=clone)
    assert [c.name for c, _ in result.cloned] == ["pub"] and result.skipped == [PRIV] and not result.failed
    assert calls == [("pub", None)]


def test_with_a_token_every_repo_is_cloned_and_a_failure_is_reported(tmp_path):
    clone, calls = _fake({"priv"})
    result = checkout([PUB, PRIV], tmp_path / "d", token="t0k", clone=clone)
    assert [c.name for c, _ in result.cloned] == ["pub"] and not result.skipped
    assert result.failed == [(PRIV, "fatal: repository not found")]
    assert calls == [("pub", "t0k"), ("priv", "t0k")]


def test_the_written_repos_file_is_what_adoption_matrix_reads(tmp_path):
    root = tmp_path / "d" / "pub"
    root.mkdir(parents=True)
    listing = tmp_path / "repos.toml"
    write_repos_file(listing, [(PUB, root)])
    assert load_repo_list(listing) == [("pub", root.resolve())]


def test_main_warns_on_a_skip_and_fails_on_a_failed_clone(tmp_path, monkeypatch, capsys):
    config = _config(
        tmp_path,
        '[[consumer]]\nname = "pub"\nrepo = "o/pub"\nbranch = "m"\nprivate = false\ncorpus = true\n'
        '[[consumer]]\nname = "priv"\nrepo = "o/priv"\nbranch = "m"\n',
    )
    monkeypatch.delenv(_consumers.TOKEN_ENV, raising=False)
    clone, _ = _fake(set())
    monkeypatch.setattr(_consumers, "_git_clone", clone)
    args = ["checkout", str(config), "--dest", str(tmp_path / "d"), "--repos-file", str(tmp_path / "repos.toml")]
    assert _consumers.main(args) == 0
    out = capsys.readouterr().out
    assert "::warning title=consumer skipped::o/priv is private" in out and "cloned 1, skipped 1" in out
    clone, _ = _fake({"pub"})
    monkeypatch.setattr(_consumers, "_git_clone", clone)
    args = ["checkout", str(config), "--dest", str(tmp_path / "e"), "--repos-file", str(tmp_path / "r2.toml"), "--corpus-only"]
    assert _consumers.main(args) == 1
    assert "::error title=consumer checkout failed::o/pub@m: fatal: repository not found" in capsys.readouterr().out


def test_main_fails_when_nothing_was_cloned(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path, '[[consumer]]\nname = "priv"\nrepo = "o/priv"\nbranch = "m"\n')
    monkeypatch.delenv(_consumers.TOKEN_ENV, raising=False)
    assert _consumers.main(["checkout", str(config), "--dest", str(tmp_path / "d"), "--repos-file", str(tmp_path / "r.toml")]) == 1
    capsys.readouterr()


class _LocalConsumer(Consumer):
    @property
    def url(self) -> str:  # a local repo instead of github.com
        return self.repo


def test_git_clone_is_shallow_on_the_branch_and_keeps_the_token_out_of_argv(tmp_path, monkeypatch):
    src = tmp_path / "src"
    git = ["git", "-C", str(src), "-c", "user.email=t@e", "-c", "user.name=t"]
    src.mkdir()
    subprocess.run([*git, "init", "-q", "-b", "staging"], check=True, capture_output=True)
    for msg in ("one", "two"):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", msg], check=True, capture_output=True)
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **k):
        seen.append(list(cmd))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(_consumers.subprocess, "run", spy)
    consumer = _LocalConsumer("x", "file://" + src.as_posix(), "staging", private=True)
    assert _consumers._git_clone(consumer, tmp_path / "dst", "s3cret") is None
    assert "s3cret" not in " ".join(seen[0])
    monkeypatch.setattr(_consumers.subprocess, "run", real_run)
    log = subprocess.run(["git", "-C", str(tmp_path / "dst"), "log", "--oneline"], check=True, capture_output=True, text=True).stdout
    assert log.strip().endswith("two") and len(log.strip().splitlines()) == 1
    missing = _LocalConsumer("y", "file://" + src.as_posix(), "nope", private=False)
    assert "nope" in (_consumers._git_clone(missing, tmp_path / "dst2", None) or "")
