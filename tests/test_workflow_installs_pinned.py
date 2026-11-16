"""workflow_installs_pinned: a CI step that clones or pip-installs a first-party repo from git must pin a commit (mlframe X_CICD_DEPENDENCIES-1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.workflow_installs_pinned import (
    RULE,
    assert_workflow_installs_pinned,
    find_workflow_installs_pinned,
)

SHA = "6b512f2b6f7c9e353ee1831d0130b8b194c1706d"  # pragma: allowlist secret (a git commit id, not a credential)


def _wf(tmp_path: Path, body: str, name: str = "ci.yml") -> Path:
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return d


def test_an_unpinned_clone_in_a_multiline_run_block_is_reported(tmp_path):
    d = _wf(
        tmp_path,
        "steps:\n" "  - run: |\n" "      git clone https://github.com/example/shared-lib.git\n" "      pip install ./shared-lib\n",
    )
    found = find_workflow_installs_pinned(d)
    assert [(f.path, f.line, f.rule) for f in found] == [("ci.yml", 3, RULE)]


def test_a_clone_followed_by_a_sha_checkout_in_the_same_block_is_clean(tmp_path):
    d = _wf(
        tmp_path,
        "steps:\n" "  - run: |\n" "      git clone https://github.com/example/shared-lib.git\n" f"      git -C shared-lib checkout {SHA}\n",
    )
    assert find_workflow_installs_pinned(d) == []


def test_a_clone_checked_out_at_a_variable_is_clean(tmp_path):
    d = _wf(
        tmp_path,
        "steps:\n" "  - run: |\n" "      git clone https://github.com/example/shared-lib.git\n" "      git -C shared-lib checkout ${SHARED_SHA}\n",
    )
    assert find_workflow_installs_pinned(d) == []


def test_a_checkout_in_a_later_step_does_not_pin_an_earlier_clone(tmp_path):
    d = _wf(
        tmp_path,
        "steps:\n" "  - run: git clone https://github.com/example/shared-lib.git\n" "  - run: |\n" f"      git -C shared-lib checkout {SHA}\n",
    )
    found = find_workflow_installs_pinned(d)
    assert [f.line for f in found] == [2]


def test_an_unpinned_git_pip_install_is_reported_and_a_pinned_one_is_clean(tmp_path):
    d = _wf(
        tmp_path,
        "steps:\n"
        '  - run: pip install "pyutilz @ git+https://github.com/fingoldo/pyutilz.git"\n'
        f'  - run: pip install "pyutilz @ git+https://github.com/fingoldo/pyutilz.git@{SHA}"\n'
        '  - run: pip install "pyutilz @ git+https://github.com/fingoldo/pyutilz.git@${PYUTILZ_SHA}"\n',
    )
    assert [f.line for f in find_workflow_installs_pinned(d)] == [2]


def test_a_reasoned_marker_accepts_the_line(tmp_path):
    d = _wf(tmp_path, "steps:\n  # unpinned-ok: tracking the default branch on purpose, reviewed\n  - run: git clone https://x.example/repo.git\n")
    assert find_workflow_installs_pinned(d) == []


def test_a_marker_without_a_reason_is_itself_a_finding(tmp_path):
    d = _wf(tmp_path, "steps:\n  - run: git clone https://x.example/repo.git  # unpinned-ok:\n")
    found = find_workflow_installs_pinned(d)
    assert len(found) == 1 and "needs a reason" in found[0].message


def test_an_empty_workflow_directory_fails_the_floor(tmp_path):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    with pytest.raises(EmptyScanError):
        assert_workflow_installs_pinned(d)


def test_the_assert_names_the_finding(tmp_path):
    d = _wf(tmp_path, "steps:\n  - run: git clone https://x.example/repo.git\n")
    with pytest.raises(AssertionError, match=r"ci\.yml:2"):
        assert_workflow_installs_pinned(d)


def test_a_clone_in_a_non_run_block_scalar_is_pinned_by_its_own_block(tmp_path):
    """A reusable-workflow input (install-command: |) is a command block too; its checkout pins the clone."""
    d = _wf(
        tmp_path,
        "jobs:\n"
        "  x:\n"
        "    with:\n"
        "      install-command: |\n"
        "        git clone https://github.com/fingoldo/pyutilz.git\n"
        f"        git -C pyutilz checkout {SHA}\n",
    )
    assert find_workflow_installs_pinned(d) == []


def test_a_git_url_inside_a_yaml_comment_is_not_an_install(tmp_path):
    d = _wf(tmp_path, "steps:\n  # name @ git+https://github.com/x/y.git would fail on PyPI\n  - run: echo ok\n")
    assert find_workflow_installs_pinned(d) == []
