"""One version: pyproject, ``__version__``, the reusable workflows' default ref and the release tags agree.

Also the release rules of ``.github/scripts/release_guard.py`` (which tag may move ``v1``, the rollback target, the
self-ci check), run against scratch git repositories."""

from __future__ import annotations

import atexit
import functools
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
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
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, timeout=30, env=git_env())
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


REUSABLE = ["black-filtered.yml", "ruff-blocking.yml", "lint-advisory.yml", "gates.yml"]


def _ref_default(name: str) -> str:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data[True]["workflow_call"]["inputs"]["py-ci-shared-ref"]["default"]  # PyYAML reads the bare `on` key as True


def test_the_reusable_workflow_list_is_the_list_release_guard_prepares():
    assert [Path(n).name for n in guard.WORKFLOW_FILES] == REUSABLE


def test_the_reusable_workflows_default_to_one_ref_that_exists_or_is_being_released():
    """Between a release and the next, a caller on master gets the LAST PUBLISHED tag (43-F3, 46-F6, 46-F7).

    The bump-to-next-release commit leaves the defaults alone; `release_guard.py prepare X.Y.Z --write` moves all four to v{VERSION} in the commit that
    is then tagged. A default naming a tag that does not exist makes every caller's install fail with no fallback, and four files disagreeing mixes
    two versions in one consumer pipeline."""
    defaults = {n: _ref_default(n) for n in REUSABLE}
    assert len(set(defaults.values())) == 1, f"the four reusable workflows name different refs: {defaults}"
    ref = next(iter(defaults.values()))
    tags = _tags()
    assert ref in tags or ref == f"v{VERSION}", (
        f"py-ci-shared-ref defaults to {ref}, which is neither a published tag (newest {tags[-1:]}) nor the release being prepared v{VERSION}; "
        "keep it at the last published tag until `release_guard.py prepare` moves it"
    )
    if ref != f"v{VERSION}":
        assert ref == tags[-1], f"{ref} is not the newest published tag {tags[-1]}: a caller on master would run old configs"


def test_the_readme_install_tag_is_the_newest_release_or_this_one():
    """The README's pip install line names a release that exists (the newest) or the one this commit becomes."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    pinned = set(re.findall(r"py-ci-shared\.git@(v\d+\.\d+\.\d+)", text))
    assert pinned, "no `py-ci-shared.git@vX.Y.Z` install line in README.md; the check lost its subject"
    tags = _tags()
    allowed = {f"v{VERSION}", *(tags[-1:] if tags else [])}
    assert pinned <= allowed, f"README.md installs {sorted(pinned - allowed)}; use the newest release or v{VERSION}"


def test_the_install_pyutilz_action_has_no_pinned_commit_of_its_own():
    """The action reads the pin from the calling repository's pyproject.toml, so a constant here could only drift."""
    action = yaml.safe_load((REPO / ".github" / "actions" / "install-pyutilz" / "action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["pyutilz-ref"]["default"] == ""


def test_the_resolver_returns_the_commit_this_repository_pins():
    import importlib.util

    spec = importlib.util.spec_from_file_location("resolve_pin", REPO / ".github" / "actions" / "install-pyutilz" / "resolve_pin.py")
    resolve_pin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolve_pin)
    pin = next(d for d in PYPROJECT["project"]["optional-dependencies"]["dev"] if d.startswith("pyutilz"))
    assert resolve_pin.pins((REPO / "pyproject.toml").read_text(encoding="utf-8")) == {pin.rsplit("@", 1)[1]}


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
    assert calls[0] == "repos/o/r/actions/workflows/self-ci.yml/runs?head_sha=abc&branch=master&per_page=50"


def test_wait_for_ci_survives_a_transient_api_error_and_reports_pending_when_it_persists():
    """46-F15: one 503 aborted the release verification with a traceback; now it is retried, and a persisting outage is pending."""
    import urllib.error

    def flaky_then_green(path: str) -> Any:
        if flaky_then_green.failures:
            flaky_then_green.failures -= 1
            raise urllib.error.HTTPError(path, 503, "unavailable", None, None)
        return {"workflow_runs": [{"status": "completed", "conclusion": "success"}]}

    flaky_then_green.failures = 1
    assert guard.wait_for_ci(flaky_then_green, "o/r", "abc", wait=0, sleep=lambda _s: None, clock=lambda: 0.0)[0] == "green"

    def always_503(path: str) -> Any:
        raise urllib.error.HTTPError(path, 503, "unavailable", None, None)

    state, reason = guard.wait_for_ci(always_503, "o/r", "abc", wait=0, sleep=lambda _s: None, clock=lambda: 0.0)
    assert state == "pending" and "re-run this job" in reason


def test_wait_for_ci_still_raises_on_a_client_error(monkeypatch):
    import urllib.error

    def forbidden(path: str) -> Any:
        raise urllib.error.HTTPError(path, 403, "forbidden", None, None)

    with pytest.raises(urllib.error.HTTPError):
        guard.wait_for_ci(forbidden, "o/r", "abc", wait=0, sleep=lambda _s: None, clock=lambda: 0.0)


def test_wait_for_ci_gives_up_as_pending_after_the_wait():
    ticks = iter([0.0, 10.0, 20.0, 30.0])
    state, why = guard.wait_for_ci(
        lambda p: {"workflow_runs": [{"status": "queued"}]}, "o/r", "abc", wait=15, poll=5, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert state == "pending" and "still running" in why


# --- release_guard: the command line against a scratch repository ----------------------------------------------------


def _build_scratch_repo(root: Path, tags: tuple[str, ...]) -> None:
    root.mkdir()
    _git("init", "-q", cwd=root)
    for i, tag in enumerate(tags):
        (root / "f.txt").write_text(str(i), encoding="utf-8")
        _git("add", "f.txt", cwd=root)
        _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", f"c{i}", cwd=root)
        _git("-c", "user.name=t", "-c", "user.email=t@example.com", "tag", "-a", tag, "-m", tag, cwd=root)


@functools.cache
def _scratch_template(tags: tuple[str, ...]) -> Path:
    """One git repo per tag layout, built once per session: each test copies it instead of re-running ~3 commits and tags."""
    root = Path(tempfile.mkdtemp(prefix="py-ci-shared-tags-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    _build_scratch_repo(root / "template", tags)
    return root / "template"


def _scratch_repo(tmp_path: Path, tags: list[str]) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(_scratch_template(tuple(tags)), repo)
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


def test_rollback_refuses_when_the_releases_cannot_be_read(tmp_path: Path, monkeypatch, capsys):
    """An unreadable releases API used to skip the published-release check, so a tag with no release could become v1."""
    guard = _load_guard()
    repo = _scratch_repo(tmp_path, ["v1.18.0", "v1.19.0"])
    argv = ["rollback", "v1.18.0", "--repo-dir", str(repo), "--repo", "owner/name"]
    monkeypatch.setattr(guard, "_release_names", lambda args: None)
    assert guard.main(argv) == 1
    assert "cannot read the GitHub releases of owner/name" in capsys.readouterr().out
    monkeypatch.setattr(guard, "_release_names", lambda args: ["v1.18.0"])
    assert guard.main(argv) == 0
    monkeypatch.setattr(guard, "_release_names", lambda args: ["v1.19.0"])
    assert guard.main(argv) == 1
    assert "has no GitHub release" in capsys.readouterr().out


def test_prepare_refuses_when_the_releases_cannot_be_read(tmp_path: Path, monkeypatch, capsys):
    """An unreadable releases API used to fall back to the newest local tag, so prepare could name a version nobody published."""
    guard = _load_guard()
    repo = _scratch_repo(tmp_path, ["v1.18.0", "v1.19.0"])
    argv = ["prepare", "1.19.1", "--repo-dir", str(repo), "--repo", "owner/name"]

    def unreachable(path):
        raise OSError("api down")

    monkeypatch.setattr(guard, "_github", lambda token: unreachable)
    assert guard.main(argv) == 2
    assert "cannot read the GitHub releases of owner/name" in capsys.readouterr().out
    monkeypatch.setattr(guard, "_github", lambda token: (lambda path: [{"tag_name": "v1.18.0"}]))
    assert guard._published_release("owner/name", "", ["v1.19.0"]) == "v1.18.0"


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
    # Only from master: a dispatch from a feature branch would run that branch's release_guard.py next to the release token.
    assert rollback["if"] == "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/master'"
    assert rollback["environment"] == "release"
    assert 'release_guard.py rollback "$TARGET"' in _runs(rollback)
    for name in ("verify", "publish"):
        assert data["jobs"][name]["if"] == "github.event_name == 'push'", f"{name} must not run on the rollback dispatch"


def test_the_rollback_procedure_is_documented():
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Rolling back a release" in claude and "rollback-to" in claude
    assert "rollback-to" in (REPO / "README.md").read_text(encoding="utf-8")


# --- audit 2026-11-07 46-F1: ci-green needs the full SHA ---------------------------------------------------------------


@pytest.mark.parametrize("sha", ["213ce77", "A" * 40, "a" * 39, ""])
def test_ci_green_refuses_anything_but_a_full_lowercase_sha(sha: str):
    """GitHub matches head_sha on all 40 characters, so a short SHA is answered 'never ran' for a green commit (46-F1)."""
    out = _cli("ci-green", "--repo", "o/r", "--sha", sha)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "40-character" in out.stdout + out.stderr


def test_rollback_requires_a_published_release_when_the_releases_are_known():
    """A tag whose Release run failed (v1.23.2, v1.23.0 once) exists but never passed verify (46-F12)."""
    tags = ["v1.18.0", "v1.19.0", "v1.20.0"]
    bad = guard.rollback_problem("v1.19.0", tags, ["v1.18.0", "v1.20.0"])
    assert bad is not None and "no GitHub release" in bad and "v1.20.0, v1.18.0" in bad
    assert guard.rollback_problem("v1.18.0", tags, ["v1.18.0"]) is None
    assert guard.rollback_problem("v1.19.0", tags, None) is None


def test_ci_verdict_says_how_to_recover_a_commit_with_only_cancelled_runs():
    verdict, why = guard.ci_verdict([{"status": "completed", "conclusion": "cancelled", "html_url": "u"}])
    assert verdict == "red" and "workflow_dispatch" in why
    assert "workflow_dispatch" not in guard.ci_verdict([{"status": "completed", "conclusion": "failure"}])[1]


def test_failed_legs_of_a_green_run_are_named():
    """A continue-on-error leg that failed leaves the run green (46-F3): the jobs say otherwise."""
    jobs = [{"name": "test (ubuntu, 3.11)", "conclusion": "success"}, {"name": "test (windows-latest, 3.9, true)", "conclusion": "failure"}]
    assert guard.failed_job_names(jobs) == ["test (windows-latest, 3.9, true)"]
    assert guard.green_run_id([{"id": 1, "conclusion": "failure"}, {"id": 2, "conclusion": "success"}]) == 2
    assert guard.green_run_id([]) is None


def _self_ci() -> dict:
    return yaml.safe_load((WORKFLOWS / "self-ci.yml").read_text(encoding="utf-8"))


def test_self_ci_push_runs_are_grouped_per_commit_so_a_newer_push_cannot_cancel_a_queued_run():
    """With cancel-in-progress false a ref-wide group still replaces the one pending run (46-F2): the release commit lost its only run."""
    data = _self_ci()
    assert "github.sha" in data["concurrency"]["group"]
    assert "pull_request" in data["concurrency"]["group"] and "github.ref" in data["concurrency"]["group"]
    assert data[True]["workflow_dispatch"] is None or data[True]["workflow_dispatch"] == {}, "a SHA with only a cancelled run needs a way to get a fresh one"


def test_every_continue_on_error_matrix_leg_has_a_reporter_job():
    """46-F3: a failing preview leg leaves the run green, so a job with `if: always()` must list the failed legs."""
    data = _self_ci()
    assert "continue-on-error" in data["jobs"]["test"]
    reporter = data["jobs"]["preview-report"]
    assert reporter["needs"] == "test" and "always()" in str(reporter["if"]) and reporter["permissions"]["actions"] == "read"


def test_release_verify_waits_for_the_observed_worst_case_queue_and_checks_prepare():
    """46-F5 (queue plus run reached 2 h) and 46-F7 (the tagged commit must be a fixed point of prepare)."""
    verify = _release()["jobs"]["verify"]
    runs = _runs(verify)
    wait = int(re.search(r"ci-green .*--wait (\d+)", runs).group(1))  # type: ignore[union-attr]
    assert wait >= 6000 and verify["timeout-minutes"] * 60 > wait
    assert "release_guard.py prepare" in runs and "--check" in runs


def test_only_release_yml_names_the_release_token():
    """46-F9: the token that force-moves v1 is read by one workflow only; any other file naming it is a second way to leak it."""
    named = sorted(p.name for p in WORKFLOWS.glob("*.yml") if "secrets.RELEASE_TOKEN" in p.read_text(encoding="utf-8"))
    assert named == ["release.yml"], named


# --- CHANGELOG headings (audit 43-F1) ---------------------------------------------------------------------------------


def _changelog_headings() -> list[str]:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]


def test_every_release_tag_from_1_23_0_has_a_changelog_heading():
    """A consumer bumping from one release to another must be able to see which release brought a Behaviour change (43-F1).

    v1.23.3 to v1.23.8 once sat under 'Unreleased (next: 1.23.3)'; map entries to the tag that first contains them (`git blame`, `git tag --contains`)."""
    headings = set(_changelog_headings())
    missing = [t for t in _tags() if _key(t) >= (1, 23, 0) and t[1:] not in headings]
    assert missing == [], f"CHANGELOG.md has no '## X.Y.Z' heading for {missing}"


def test_the_changelog_top_heading_is_the_unreleased_next_version_or_the_release_being_tagged():
    first = _changelog_headings()[0]
    assert first in (
        f"Unreleased (next: {VERSION})",
        VERSION,
    ), f"the newest CHANGELOG heading is '## {first}'; expected '## Unreleased (next: {VERSION})' (or '## {VERSION}' in the commit that is tagged)"
    tag = f"v{VERSION}"
    if tag in _tags() and _git("rev-parse", f"{tag}^{{commit}}") == _git("rev-parse", "HEAD"):
        assert first == VERSION, f"this is the commit tagged {tag}: rename '## {first}' to '## {VERSION}'"
    assert _changelog_headings().count("Unreleased") == 0, "a bare '## Unreleased' belongs to no release; use '## Unreleased (next: X.Y.Z)' once"


def test_the_release_push_passes_the_token_header_through_the_environment_not_argv():
    """51-F4: ``git -c <header-with-token>`` put the base64 token in argv, visible in a process listing; both pushes use GIT_CONFIG_* env."""
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "git -c" not in text or "extraheader" not in text.split("git -c", 1)[1].split("\n", 1)[0]
    assert text.count("GIT_CONFIG_VALUE_0=") == 2
