"""``.github/scripts/release_local.py``: the release path while GitHub Actions cannot run on this account.

Every side effect (git state, running a check, tagging, pushing, the confirmation prompt) is injected, so these tests drive the decisions
without a network, a tag or a subprocess: it refuses a dirty tree, a HEAD that is not origin/master and any failing check, never tags before
every check passed, takes the tag back when a tag-time check fails, and pushes nothing without the typed confirmation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "release_local.py"
HEAD = "a" * 40
UPSTREAM = "b" * 40
OLD_V1 = "c" * 40


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("release_local_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("release_local_under_test", module)
    spec.loader.exec_module(module)
    return module


rl = _load()


def state(**kw: Any) -> Any:
    base = dict(dirty="", head=HEAD, upstream=HEAD, head_descends_from_upstream=True, local_tag_commit=None, remote_tag_commit=None, remote_major_ref=OLD_V1)
    base.update(kw)
    return rl.RepoState(**base)


class Harness:
    """Records what ``release`` did; ``fail`` names a step (by substring) whose runner exits 1."""

    def __init__(self, st: Any, fail: Optional[str] = None, answer: str = "") -> None:
        self.st, self.fail, self.answer = st, fail, answer
        self.ran: list[Any] = []
        self.tagged: list[tuple[str, str]] = []
        self.untagged: list[str] = []
        self.lines: list[str] = []

    def runner(self, step: Any) -> int:
        self.ran.append(step)
        return 1 if self.fail and self.fail in step.name else 0

    def __call__(self, version: str = "1.22.9", **kw: Any) -> int:
        return rl.release(
            version,
            runner=self.runner,
            state_reader=lambda tag, remote, branch: self.st,
            tagger=lambda tag, sha: self.tagged.append((tag, sha)),
            untagger=self.untagged.append,
            confirm=lambda prompt: self.answer,
            out=self.lines.append,
            venv_root=Path("venvs"),
            **kw,
        )

    @property
    def pushes(self) -> list[Any]:
        return [s for s in self.ran if s.argv[0] in ("git", "gh")]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def test_refuses_a_dirty_tree_before_running_anything() -> None:
    h = Harness(state(dirty="?? stray.txt"))
    assert h() == 1
    assert h.ran == [] and h.tagged == []
    assert "not clean" in h.text and "stray.txt" in h.text


def test_refuses_head_that_is_not_origin_master() -> None:
    h = Harness(state(upstream=UPSTREAM))
    assert h() == 1
    assert h.ran == [] and h.tagged == []
    assert "is not origin/master" in h.text


def test_ahead_ok_allows_a_fast_forward_and_prints_the_branch_push_first() -> None:
    h = Harness(state(upstream=UPSTREAM))
    assert h(ahead_ok=True) == 0
    assert h.tagged == [("v1.22.9", HEAD)]
    commands = [line.strip() for line in h.lines if line.startswith("  ")]
    assert commands[0] == f"git push origin {HEAD}:refs/heads/master"
    assert commands[1] == "git push origin refs/tags/v1.22.9"


def test_ahead_ok_still_refuses_a_head_that_diverged() -> None:
    h = Harness(state(upstream=UPSTREAM, head_descends_from_upstream=False))
    assert h(ahead_ok=True) == 1
    assert "not a fast-forward" in h.text and h.ran == []


def test_refuses_a_tag_already_on_the_remote() -> None:
    h = Harness(state(remote_tag_commit=HEAD))
    assert h() == 1
    assert "never moved" in h.text and h.ran == []


def test_refuses_a_local_tag_on_another_commit_but_reuses_one_on_head() -> None:
    other = Harness(state(local_tag_commit=UPSTREAM))
    assert other() == 1 and other.ran == []
    same = Harness(state(local_tag_commit=HEAD))
    assert same() == 0
    assert same.tagged == [], "an existing local tag on HEAD is reused, not re-created"


@pytest.mark.parametrize(
    "failing", ["prepare --check", "pre-commit", "[3.9] the whole test suite", "mypy src/py_ci_shared", "ruff over the whole tree", "black-filtered"]
)
def test_a_failing_check_refuses_without_tagging(failing: str) -> None:
    h = Harness(state(), fail=failing)
    assert h() == 1
    assert h.tagged == [] and h.pushes == []
    assert f"'{h.ran[-1].name}' failed; nothing was tagged" in h.text
    assert failing in h.ran[-1].name, "it stops at the first failure"


@pytest.mark.parametrize("failing", ["version_tag_currency", "major-move"])
def test_a_failing_tag_check_deletes_the_tag_it_created(failing: str) -> None:
    h = Harness(state(), fail=failing)
    assert h() == 1
    assert h.tagged == [("v1.22.9", HEAD)]
    assert h.untagged == ["v1.22.9"]
    assert h.pushes == []


def test_print_only_by_default() -> None:
    h = Harness(state(), answer="v1.22.9")
    assert h() == 0
    assert h.pushes == [], "without --push nothing is pushed, whatever the prompt would have said"
    commands = [line.strip() for line in h.lines if line.startswith("  ")]
    assert commands[0] == "git push origin refs/tags/v1.22.9"
    assert commands[1] == f"git push --force-with-lease=refs/tags/v1:{OLD_V1} origin {HEAD}:refs/tags/v1"
    assert commands[2].startswith("gh release create v1.22.9 --verify-tag --title v1.22.9 --notes-file ")


def test_push_needs_the_typed_tag() -> None:
    wrong = Harness(state(), answer="y")
    assert wrong(push=True) == 1
    assert wrong.pushes == []
    right = Harness(state(), answer="v1.22.9")
    assert right(push=True) == 0
    assert [s.argv[:2] for s in right.pushes] == [("git", "push"), ("git", "push"), ("gh", "release")]


def test_bad_version_is_usage_error() -> None:
    h = Harness(state())
    assert h("1.22") == 2 and h.ran == []


def test_checks_cover_every_blocking_ci_step() -> None:
    """The workflow jobs that cannot run on this account each have a local step here."""
    steps = rl.check_steps("1.22.9", pythons=rl.PYTHONS, venv_root=Path("v"), workers=4)
    names = [s.name for s in steps]
    argvs = [" ".join(s.argv) for s in steps]
    for py in rl.PYTHONS:
        suite = [a for n, a in zip(names, argvs) if n.startswith(f"[{py}] the whole test suite")]
        assert len(suite) == 1 and "pytest tests/ -ra -n 4" in suite[0]
        assert ("--cov=py_ci_shared" in suite[0]) == (py == rl.COVERAGE_PYTHON)
        assert any(n.startswith(f"[{py}] py-ci-shared run-all") and "py_ci_shared.cli run-all" in a for n, a in zip(names, argvs))
    for needle in (
        "prepare 1.22.9 --check",
        "pre_commit run --all-files",
        "-m mypy src/py_ci_shared",
        f"ruff=={rl.ruff_version()} check . --ignore C901",
        "--config configs/ruff-base.toml --show-settings",
        "black_filtered_apply --config pyproject.toml --check",
        "--follow-imports=silent src/py_ci_shared/__init__.py",
        f"-c {rl.RUNTIME_CONSTRAINTS} -e .[dev]",
    ):
        assert any(needle in a for a in argvs), needle
    black = next(s for s in steps if s.name.startswith("black-filtered"))
    assert black.env["BLACK_CMD"] == f"uvx black=={rl.black_version()}"


def test_python_matrix_and_mypy_pin_follow_self_ci() -> None:
    ci = yaml.safe_load((REPO / ".github/workflows/self-ci.yml").read_text(encoding="utf-8"))
    test = ci["jobs"]["test"]
    assert tuple(test["strategy"]["matrix"]["python"]) == rl.PYTHONS
    run = " ".join(str(s.get("run", "")) for s in test["steps"])
    assert f'"mypy=={rl.MYPY_VERSION}"' in run


def test_release_notes_prefer_the_version_section() -> None:
    log = "# Changelog\n\n## Unreleased\n\n- next\n\n## 1.22.9\n\n- this one\n\n## 1.22.8\n\n- old\n"
    assert rl.release_notes(log, "1.22.9") == "- this one\n"
    assert rl.release_notes(log, "1.23.0") == "- next\n"
    with pytest.raises(ValueError):
        rl.release_notes("# Changelog\n\n## 1.0.0\n\n- x\n", "1.1.0")


def test_rollback_commands_lease_the_current_v1() -> None:
    assert rl.rollback_commands("v1.19.0", HEAD, remote="origin", old_major=OLD_V1) == [
        ["git", "push", f"--force-with-lease=refs/tags/v1:{OLD_V1}", "origin", f"{HEAD}:refs/tags/v1"]
    ]


def test_precondition_problems_is_empty_on_a_clean_release_commit() -> None:
    assert rl.precondition_problems(state(), "v1.22.9", ahead_ok=False) == []
