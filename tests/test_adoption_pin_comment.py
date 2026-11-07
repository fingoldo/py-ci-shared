"""A `# vX.Y.Z` comment after a SHA pin must name the commit of that release tag (audits 46-F10, 47-F2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared._core.git import git_env
from py_ci_shared.adoption_matrix import RefResolver, find_pins, main, scan_repo

URL = "git+https://github.com/fingoldo/py-ci-shared.git"
RULE = "ci-pin-comment-not-release"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=git_env())
    return proc.stdout.strip()


@pytest.fixture()
def shared(tmp_path):
    """A py-ci-shared checkout: commit `early`, commit `released` tagged v1.2.3, and a later commit `late`."""
    repo = tmp_path / "pcs"
    repo.mkdir()
    _git(repo, "init", "-q")
    shas = {}
    for name in ("early", "released", "late"):
        _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", name)
        shas[name] = _git(repo, "rev-parse", "HEAD")
        if name == "released":
            _git(repo, "tag", "-a", "-m", "release", "v1.2.3")
    return repo, shas


def _consumer(tmp_path: Path, line: str) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "requirements-dev.txt").write_bytes(f"{line}\n".encode())
    return root


def _rules(root: Path, repo: Path, **kw) -> list[str]:
    report = scan_repo(root, local_copies=False, resolver=RefResolver(repo), allow_behind=9, **kw)
    return [f.rule for f in report.findings()]


def test_the_comment_is_carried_on_the_pin(tmp_path, shared):
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shared[1]['early']}  # v1.2.3 (note)")
    (pin,) = find_pins(root)
    assert pin.comment == "v1.2.3 (note)" and pin.comment_tag == "v1.2.3"


def test_a_sha_that_is_not_the_tagged_commit_is_flagged(tmp_path, shared):
    repo, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v1.2.3")
    assert _rules(root, repo) == [RULE]
    finding = next(f for f in scan_repo(root, local_copies=False, resolver=RefResolver(repo), allow_behind=9).findings() if f.rule == RULE)
    assert shas["released"][:7] in finding.message and shas["early"][:7] in finding.message


def test_the_tagged_commit_is_not_flagged(tmp_path, shared):
    repo, shas = shared
    assert _rules(_consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['released']}  # v1.2.3"), repo) == []


def test_an_abbreviated_sha_of_the_tagged_commit_is_not_flagged(tmp_path, shared):
    repo, shas = shared
    assert _rules(_consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['released'][:9]}  # v1.2.3"), repo) == []


def test_a_comment_without_a_version_is_not_flagged(tmp_path, shared):
    repo, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # master {shas['early'][:7]}")
    assert find_pins(root)[0].comment_tag is None and _rules(root, repo) == []


def test_a_tag_that_does_not_exist_is_flagged(tmp_path, shared):
    repo, shas = shared
    assert _rules(_consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v9.9.9"), repo) == [RULE]


def test_no_checkout_means_no_finding(tmp_path, shared):
    _, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v1.2.3")
    assert [f.rule for f in scan_repo(root, local_copies=False).findings()] == []


def test_a_checkout_without_tags_does_not_invent_findings(tmp_path):
    repo = tmp_path / "pcs"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    sha = _git(repo, "rev-parse", "HEAD")
    assert _rules(_consumer(tmp_path, f"py-ci-shared @ {URL}@{sha}  # v1.2.3"), repo) == []


def test_workflow_and_pyproject_comments_are_read(tmp_path, shared):
    repo, shas = shared
    root = tmp_path / "consumer"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_bytes(f"      - uses: fingoldo/py-ci-shared/.github/workflows/x.yml@{shas['early']}  # v1.2.3\n".encode())
    (root / "pyproject.toml").write_bytes(f'[project]\nname = "c"\ndependencies = ["py-ci-shared @ {URL}@{shas["early"]}"]  # v1.2.3\n'.encode())
    assert sorted(p.comment_tag for p in find_pins(root)) == ["v1.2.3", "v1.2.3"]
    assert _rules(root, repo).count(RULE) == 2


def test_it_reports_without_failing_unless_strict(tmp_path, shared):
    repo, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v1.2.3")
    assert not scan_repo(root, local_copies=False, resolver=RefResolver(repo), allow_behind=9).failing
    assert scan_repo(root, local_copies=False, resolver=RefResolver(repo), allow_behind=9, enforce_comments=True).failing


def test_main_exit_code_follows_the_strict_flag(tmp_path, shared, capsys):
    repo, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v1.2.3")
    base = [str(root), "--resolve-in", str(repo), "--no-local-copies", "--allow-behind", "9"]
    assert main(base) == 0 and RULE in capsys.readouterr().out
    assert main([*base, "--strict-pin-comments"]) == 1


def test_main_without_checkout_says_the_comments_were_not_checked(tmp_path, shared, capsys):
    _, shas = shared
    root = _consumer(tmp_path, f"py-ci-shared @ {URL}@{shas['early']}  # v1.2.3")
    main([str(root), "--no-local-copies", "--advisory"])
    assert "not checked against the release tags" in capsys.readouterr().err
