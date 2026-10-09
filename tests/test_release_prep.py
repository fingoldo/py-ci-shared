"""``release_guard.py prepare``: one command brings every place that names the release to one version, before the tag is pushed.

v1.22.0 and v1.22.2 were both tagged with the README still naming the previous release, and the Release run's ``verify`` job failed on
``test_the_readme_install_tag_is_the_newest_release_or_this_one``: the tag cannot be moved, so each cost a version number. These tests run the edit
over a COPY of this repository's own files, so they notice when a file changes shape and the edit no longer knows it."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO / ".github" / "scripts" / "release_guard.py"


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("release_guard_prep", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("release_guard_prep", module)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the files the edit touches, laid out like the repository."""
    for name in guard.PREP_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    return tmp_path


def _text(root: Path, name: str) -> str:
    return (root / name).read_text(encoding="utf-8")


def test_a_release_ahead_of_the_published_one_changes_every_file_that_names_it(tree: Path):
    changed = guard.run_prep(tree, "99.0.0", "v1.0.0", write=False)
    assert set(changed) == set(guard.PREP_FILES)


def test_the_written_tree_names_the_new_version_in_every_place_the_release_test_looks(tree: Path):
    guard.run_prep(tree, "99.0.0", "v1.0.0", write=True)
    assert re.search(r'^version = "99\.0\.0"', _text(tree, "pyproject.toml"), re.M)
    assert re.search(r'^__version__ = "99\.0\.0"', _text(tree, "src/py_ci_shared/__init__.py"), re.M)
    assert set(re.findall(r"py-ci-shared\.git@(v[\d.]+)", _text(tree, "README.md"))) == {"v99.0.0"}
    assert guard.PREP_FILES[4:], "no workflow among the prepared files"
    for name in guard.PREP_FILES[4:]:
        assert 'default: "v99.0.0"' in _text(tree, name), name


def test_a_second_run_finds_nothing_left_to_change(tree: Path):
    guard.run_prep(tree, "99.0.0", "v1.0.0", write=True)
    assert guard.run_prep(tree, "99.0.0", "v1.0.0", write=False) == []


def test_only_modules_newer_than_the_published_release_get_the_new_since(tree: Path):
    before = re.findall(r'^since = "([\d.]+)"', _text(tree, "src/py_ci_shared/registry.toml"), re.M)
    guard.run_prep(tree, "99.0.0", "v1.21.1", write=True)
    after = re.findall(r'^since = "([\d.]+)"', _text(tree, "src/py_ci_shared/registry.toml"), re.M)
    assert len(before) == len(after) > 50
    for was, now in zip(before, after, strict=True):
        shipped = tuple(int(p) for p in was.split(".")) <= (1, 21, 1)
        assert now == (was if shipped else "99.0.0"), (was, now)
    assert "99.0.0" in after and "1.21.1" in after


def test_the_readme_catalogue_moves_with_the_registry(tree: Path):
    """The catalogue is the rendered registry (`test_the_readme_catalogue_is_the_rendered_registry`), so its version column follows `since`."""
    guard.run_prep(tree, "99.0.0", "v1.21.1", write=True)
    moved = _text(tree, "src/py_ci_shared/registry.toml").count('since = "99.0.0"')
    rows = len(re.findall(r"^\| \[`[^`]+`\]\([^)]*\) \| [a-z]+ \| 99\.0\.0 \|", _text(tree, "README.md"), re.M))
    assert moved > 0 and rows == moved


def test_since_is_compared_numerically_not_as_text(tree: Path):
    """1.9.0 is older than 1.10.0; a text comparison would call it newer and rewrite a module that shipped long ago."""
    registry = tree / "src" / "py_ci_shared" / "registry.toml"
    registry.write_text(registry.read_text(encoding="utf-8") + '\n[[gate]]\nname = "x"\nkind = "gate"\nentries = ["x"]\nsince = "1.9.0"\nsummary = "x"\n', encoding="utf-8")
    guard.run_prep(tree, "99.0.0", "v1.10.0", write=True)
    assert _text(tree, "src/py_ci_shared/registry.toml").rstrip().endswith('since = "1.9.0"\nsummary = "x"')


def test_crlf_files_stay_crlf(tree: Path):
    target = tree / "README.md"
    target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    guard.run_prep(tree, "99.0.0", "v1.0.0", write=True)
    data = target.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")


@pytest.mark.parametrize("version, published", [("1.22.1", "v1.22.1"), ("1.22.0", "v1.22.1"), ("1.9.0", "v1.10.0")])
def test_a_version_not_ahead_of_the_published_release_is_refused(tree: Path, version: str, published: str):
    with pytest.raises(ValueError, match="not ahead"):
        guard.run_prep(tree, version, published, write=False)


@pytest.mark.parametrize("version, published", [("1.2", "v1.0.0"), ("1.2.3", "v1.0"), ("x", "v1.0.0")])
def test_a_malformed_version_or_tag_is_refused(tree: Path, version: str, published: str):
    with pytest.raises(ValueError, match=r"X.Y.Z"):
        guard.run_prep(tree, version, published, write=False)


def test_a_file_that_changed_shape_is_named_instead_of_skipped(tree: Path):
    workflow = tree / ".github" / "workflows" / "ruff-blocking.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("py-ci-shared-ref", "ref"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"ruff-blocking.yml"):
        guard.run_prep(tree, "99.0.0", "v1.0.0", write=False)
    (tree / "README.md").write_text("no install line here\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"README.md"):
        guard.run_prep(tree, "99.0.0", "v1.0.0", write=False)


def test_check_exits_nonzero_while_files_differ_and_zero_when_ready(tree: Path, capsys: pytest.CaptureFixture[str]):
    args = ["prepare", "99.0.0", "--repo-dir", str(tree), "--published", "v1.0.0"]
    assert guard.main([*args, "--check"]) == 1
    assert "would change" in capsys.readouterr().out
    assert guard.main([*args, "--write"]) == 0
    assert guard.main([*args, "--check"]) == 0
    assert "ready to tag" in capsys.readouterr().out


def test_a_version_the_edit_refuses_is_a_usage_error_not_a_traceback(tree: Path, capsys: pytest.CaptureFixture[str]):
    assert guard.main(["prepare", "1.0.0", "--repo-dir", str(tree), "--published", "v1.5.0"]) == 2
    assert "::error::" in capsys.readouterr().out


def test_the_real_tree_is_reported_not_ready_for_a_future_release_and_no_file_is_touched(capsys: pytest.CaptureFixture[str]):
    """A dry run on the repository itself: it lists files, writes none."""
    before = {name: (REPO / name).read_bytes() for name in guard.PREP_FILES}
    assert guard.main(["prepare", "99.0.0", "--repo-dir", str(REPO), "--published", "v1.0.0", "--check"]) == 1
    capsys.readouterr()
    assert {name: (REPO / name).read_bytes() for name in guard.PREP_FILES} == before
