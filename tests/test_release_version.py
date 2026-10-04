"""One version: pyproject, ``__version__``, the reusable workflows' default ref and the release tags agree.

Also the release rules of ``.github/scripts/release_guard.py`` (which tag may move ``v1``, the rollback target, the
self-ci check), run against scratch git repositories."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

import py_ci_shared
from py_ci_shared._core.git import git_env
from py_ci_shared._toml_compat import tomllib
from py_ci_shared.registry import load_gates
from py_ci_shared.version_consistency import assert_versions_agree
from py_ci_shared.version_tag_currency import semver_tags

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
GUARD_PATH = REPO / ".github" / "scripts" / "release_guard.py"
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = PYPROJECT["project"]["version"]


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("release_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("release_guard", module)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.lstrip("v").split("."))


def test_pyproject_and_the_module_agree():
    assert_versions_agree(REPO, files=["src/py_ci_shared/__init__.py"], modules=["py_ci_shared"])
    assert py_ci_shared.__version__ == VERSION


def test_the_version_is_plain_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), f"{VERSION!r} is not X.Y.Z; release.yml only accepts vX.Y.Z tags"


def _git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=True, env=git_env()).stdout.strip()


def _tags() -> list[str]:
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, timeout=30)
    if inside.returncode != 0:
        pytest.skip("not a git checkout (an sdist or wheel): no tags to compare with")
    return semver_tags(REPO)


def test_the_version_is_at_least_the_newest_release_tag():
    """Between releases the declared version is the next one; it may never fall behind a tag that already shipped."""
    tags = _tags()
    assert tags, "no vX.Y.Z tags visible; CI must check out with fetch-depth: 0 so tags are fetched"
    newest = tags[-1]
    fix = "Bump version in pyproject.toml and src/py_ci_shared/__init__.py to the next release."
    assert _key(VERSION) >= _key(newest), f"pyproject declares {VERSION} but {newest} already shipped. {fix}"


def test_a_released_version_is_declared_only_by_its_own_tagged_commit():
    """``version == a shipped tag`` is right only on that tag's commit; one commit later it is the next release's job.

    The former test only checked that the tag was an ancestor of HEAD, so a tree three commits past v1.19.0 still
    declaring 1.19.0 passed (audit 2026-10-03 WF-3 / N-13)."""
    tags = _tags()
    tag = f"v{VERSION}"
    tag_commit = _git("rev-parse", f"{tag}^{{commit}}") if tag in tags else None
    problem = guard.version_problem(VERSION, tags, _git("rev-parse", "HEAD"), tag_commit)
    assert problem is None, problem


def test_every_registry_since_is_a_release_up_to_the_declared_version():
    """``since`` is the first release that ships a module, so it can never be ahead of the version this tree declares."""
    specs = load_gates()
    assert len(specs) >= 100, "the registry was not read"
    ahead = {s.name: s.since for s in specs if _key(s.since) > _key(VERSION)}
    assert ahead == {}, f"since is ahead of pyproject's {VERSION}: {ahead}. Bump the version or correct since."


@pytest.mark.parametrize("name", ["black-filtered.yml", "ruff-blocking.yml", "lint-advisory.yml"])
def test_each_reusable_workflow_defaults_to_its_own_release(name: str):
    """A caller pinned to a tag gets that tag's configs: the default ref is the version the file ships in."""
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    inputs = data[True]["workflow_call"]["inputs"]  # PyYAML reads the bare `on` key as True
    assert (
        inputs["py-ci-shared-ref"]["default"] == f"v{VERSION}"
    ), f"{name}: py-ci-shared-ref defaults to {inputs['py-ci-shared-ref']['default']!r}; set it to v{VERSION} with the version bump"


def test_the_readme_install_tag_is_the_newest_release_or_this_one():
    """The README's pip install line names a release that exists (the newest) or the one this commit becomes."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    pinned = set(re.findall(r"py-ci-shared\.git@(v\d+\.\d+\.\d+)", text))
    assert pinned, "no `py-ci-shared.git@vX.Y.Z` install line in README.md; the check lost its subject"
    tags = _tags()
    allowed = {f"v{VERSION}", *(tags[-1:] if tags else [])}
    assert pinned <= allowed, f"README.md installs {sorted(pinned - allowed)}; use the newest release or v{VERSION}"


def test_the_install_pyutilz_action_defaults_to_the_pinned_commit():
    pin = next(d for d in PYPROJECT["project"]["optional-dependencies"]["dev"] if d.startswith("pyutilz"))
    sha = pin.rsplit("@", 1)[1]
    action = yaml.safe_load((REPO / ".github" / "actions" / "install-pyutilz" / "action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["pyutilz-ref"]["default"] == sha, "install-pyutilz's default ref and the [dev] pyutilz pin must be the same commit"


# --- release_guard: pure rules ---------------------------------------------------------------------------------------


def test_release_tags_sort_numerically_and_drop_non_releases():
    tags = ["v1.9.0", "v1", "v1.10.0", "v1.2", "v1.10.0-rc1", "v0.9.9", "x1.0.0", "v2.0.0"]
    assert guard.release_tags(tags) == ["v0.9.9", "v1.9.0", "v1.10.0", "v2.0.0"]
    assert guard.highest(tags) == "v2.0.0" and guard.highest(tags, 1) == "v1.10.0" and guard.highest(tags, 3) is None


@pytest.mark.parametrize(
    "tag, problem",
    [
        ("v1.20.0", None),
        ("v1.19.1", "is not the newest v1 release (v1.20.0 is)"),  # a back-port must not move v1 backwards
        ("v1.9.0", "is not the newest v1 release (v1.20.0 is)"),  # numeric, not string, order
        ("v2.0.0", None),
        ("v1.21.0", "is not among the repository's tags"),
        ("v1", "is not a vX.Y.Z release tag"),
    ],
)
def test_major_move_only_for_the_newest_release_of_its_major(tag: str, problem: Optional[str]):
    tags = ["v1", "v1.9.0", "v1.19.0", "v1.19.1", "v1.20.0", "v2.0.0"]
    got = guard.major_move_problem(tag, tags)
    assert (got is None) if problem is None else (got is not None and problem in got), got


def test_rollback_targets_only_an_existing_release():
    tags = ["v1", "v1.18.0", "v1.19.0"]
    assert guard.rollback_problem("v1.18.0", tags) is None
    assert "not an existing tag" in (guard.rollback_problem("v1.17.0", tags) or "")
    assert "not a vX.Y.Z" in (guard.rollback_problem("v1", tags) or "")


@pytest.mark.parametrize(
    "version, head, tag_commit, problem",
    [
        ("1.20.0", "b", None, None),  # the next release, not tagged yet
        ("1.19.0", "a", "a", None),  # exactly the tagged commit: release.yml's own checkout
        ("1.19.0", "b", "a", "already shipped"),  # the audit's case: HEAD past the tag, version not bumped
        ("1.18.0", "b", None, "is behind the newest release v1.19.0"),
        ("1.19", "b", None, "is not X.Y.Z"),
    ],
)
def test_version_problem(version: str, head: str, tag_commit: Optional[str], problem: Optional[str]):
    got = guard.version_problem(version, ["v1.18.0", "v1.19.0"], head, tag_commit)
    assert (got is None) if problem is None else (got is not None and problem in got), got


@pytest.mark.parametrize(
    "runs, state",
    [
        ([{"status": "completed", "conclusion": "success"}], "green"),
        ([{"status": "completed", "conclusion": "failure"}, {"status": "completed", "conclusion": "success"}], "green"),
        ([{"status": "in_progress", "conclusion": None}], "pending"),
        ([{"status": "completed", "conclusion": "failure"}], "red"),
        ([{"status": "completed", "conclusion": "cancelled"}], "red"),
        ([], "red"),
    ],
)
def test_ci_verdict(runs: list, state: str):
    assert guard.ci_verdict(runs)[0] == state


def test_wait_for_ci_polls_until_the_run_finishes():
    answers = iter([[{"status": "in_progress"}], [{"status": "in_progress"}], [{"status": "completed", "conclusion": "success"}]])
    calls: list[str] = []
    slept: list[float] = []

    def api(path: str) -> Any:
        calls.append(path)
        return {"workflow_runs": next(answers)}

    state, _ = guard.wait_for_ci(api, "o/r", "abc", wait=600, poll=5, sleep=slept.append, clock=lambda: 0.0)
    assert state == "green" and slept == [5, 5]
    assert calls[0] == "repos/o/r/actions/workflows/self-ci.yml/runs?head_sha=abc&per_page=50"


def test_wait_for_ci_gives_up_as_pending_after_the_wait():
    ticks = iter([0.0, 10.0, 20.0, 30.0])
    state, why = guard.wait_for_ci(
        lambda p: {"workflow_runs": [{"status": "queued"}]}, "o/r", "abc", wait=15, poll=5, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert state == "pending" and "still running" in why


# --- release_guard: the command line against a scratch repository ----------------------------------------------------


def _scratch_repo(tmp_path: Path, tags: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    for i, tag in enumerate(tags):
        (repo / "f.txt").write_text(str(i), encoding="utf-8")
        _git("add", "f.txt", cwd=repo)
        _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", f"c{i}", cwd=repo)
        _git("-c", "user.name=t", "-c", "user.email=t@example.com", "tag", "-a", tag, "-m", tag, cwd=repo)
    return repo


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD_PATH), *args], capture_output=True, text=True, timeout=60, env=git_env())


def test_cli_refuses_to_move_v1_for_a_back_port_tag(tmp_path: Path):
    # Released in this order: v1.18.0, v1.19.0, then a back-port v1.18.1 on top. v1.18.1 is the newest commit but not
    # the newest release, so pushing it must not move v1 from v1.19.0 back to it.
    repo = _scratch_repo(tmp_path, ["v1.18.0", "v1.19.0", "v1.18.1"])
    back_port = _cli("major-move", "v1.18.1", "--repo-dir", str(repo))
    assert back_port.returncode == 1 and "::error::v1.18.1 is not the newest v1 release (v1.19.0 is)" in back_port.stdout
    newest = _cli("major-move", "v1.19.0", "--repo-dir", str(repo))
    assert newest.returncode == 0, newest.stdout + newest.stderr


def test_cli_rollback_accepts_an_earlier_release_and_refuses_an_unknown_one(tmp_path: Path):
    repo = _scratch_repo(tmp_path, ["v1.18.0", "v1.19.0"])
    assert _cli("rollback", "v1.18.0", "--repo-dir", str(repo)).returncode == 0
    missing = _cli("rollback", "v1.17.0", "--repo-dir", str(repo))
    assert missing.returncode == 1 and "not an existing tag" in missing.stdout


def test_version_problem_on_a_scratch_repository(tmp_path: Path):
    """The test above, on real git objects: the declared version equals a tag, then HEAD moves one commit past it."""
    repo = _scratch_repo(tmp_path, ["v1.18.0", "v1.19.0"])
    tag_commit = _git("rev-parse", "v1.19.0^{commit}", cwd=repo)
    tags = _git("tag", "--list", "v*", cwd=repo).split()
    assert guard.version_problem("1.19.0", tags, _git("rev-parse", "HEAD", cwd=repo), tag_commit) is None
    (repo / "g.txt").write_text("next", encoding="utf-8")
    _git("add", "g.txt", cwd=repo)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "next", cwd=repo)
    head = _git("rev-parse", "HEAD", cwd=repo)
    assert "already shipped" in (guard.version_problem("1.19.0", tags, head, tag_commit) or "")
    assert guard.version_problem("1.20.0", tags, head, None) is None


# --- release.yml ----------------------------------------------------------------------------------------------------


def _release() -> dict:
    return yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


def test_release_verify_requires_the_newest_tag_green_self_ci_and_the_whole_suite():
    verify = _release()["jobs"]["verify"]
    runs = _runs(verify)
    assert 'release_guard.py major-move "$TAG"' in runs
    assert "release_guard.py ci-green" in runs and "--sha" in runs
    assert verify["permissions"]["actions"] == "read", "listing self-ci's runs needs actions: read"
    suite = [line for line in runs.splitlines() if "pytest" in line]
    assert suite == ["python -m pytest tests/ -ra"], f"release.yml must run the whole suite, not a subset: {suite}"


def test_release_publish_is_rerunnable_and_moves_v1_before_the_release_page():
    publish = _release()["jobs"]["publish"]
    names = [s.get("name") for s in publish["steps"]]
    assert names.index("Move the major tag") < names.index("Publish the GitHub release")
    runs = _runs(publish)
    assert 'gh release view "$TAG"' in runs, "gh release create fails on an existing release, so a re-run never got to the tag"
    assert "already points at" in runs, "a re-run after a successful tag move must not need the token again"
    assert 'release_guard.py major-move "$TAG"' in runs
    assert publish["environment"] == "release"


def test_release_rollback_is_a_dispatch_through_the_guard():
    data = _release()
    assert data[True]["workflow_dispatch"]["inputs"]["rollback-to"]["required"] is True
    rollback = data["jobs"]["rollback"]
    assert rollback["if"] == "github.event_name == 'workflow_dispatch'" and rollback["environment"] == "release"
    assert 'release_guard.py rollback "$TARGET"' in _runs(rollback)
    for name in ("verify", "publish"):
        assert data["jobs"][name]["if"] == "github.event_name == 'push'", f"{name} must not run on the rollback dispatch"


def test_the_rollback_procedure_is_documented():
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Rolling back a release" in claude and "rollback-to" in claude
    assert "rollback-to" in (REPO / "README.md").read_text(encoding="utf-8")
