"""``py-ci-shared new-gate``: run in a tmp copy of the package layout, never in this checkout."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import cli
from py_ci_shared._scaffold import ScaffoldError, _insert_registry, find_checkout, new_gate
from py_ci_shared._toml_compat import tomllib

REPO = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache")


def _copy_files(root: Path, rels: tuple[str, ...]) -> None:
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, root / rel)


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    """The files the scaffolder reads and edits, copied byte for byte: registry, pyproject, README, the teeth test."""
    root = tmp_path / "pcs"
    _copy_files(root, ("src/py_ci_shared/registry.toml", "tests/test_gate_teeth.py", "README.md", "pyproject.toml"))
    (root / "tests" / "canary").mkdir()
    return root


@pytest.fixture
def full_layout(tmp_path: Path) -> Path:
    """The whole package, the two inventory tests, conftest, the canaries, README, pyproject and hooks."""
    root = tmp_path / "pcs"
    shutil.copytree(REPO / "src" / "py_ci_shared", root / "src" / "py_ci_shared", ignore=IGNORE)
    shutil.copytree(REPO / "tests" / "canary", root / "tests" / "canary")
    _copy_files(
        root, ("tests/test_gate_teeth.py", "tests/test_package_inventory.py", "tests/conftest.py", "README.md", "pyproject.toml", ".pre-commit-hooks.yaml")
    )
    return root


def _registry(root: Path) -> list[dict]:
    return tomllib.loads((root / "src" / "py_ci_shared" / "registry.toml").read_bytes().decode("utf-8"))["gate"]


def test_a_gate_lands_in_every_place_with_since_from_pyproject(layout):
    done = new_gate(layout, "seeded_widgets", summary="Widgets are seeded")
    assert done.created == (
        "src/py_ci_shared/seeded_widgets.py",
        "tests/test_seeded_widgets.py",
        "tests/canary/seeded_widgets/violation/seed.py.canary",
        "tests/canary/seeded_widgets/clean/seed.py.canary",
        "tests/canary/seeded_widgets/bom/seed.py.canary",
        "tests/canary/seeded_widgets/unparsable/broken.py.canary",
    )
    gates = _registry(layout)
    names = [g["name"] for g in gates]
    assert names == sorted(names) and len(names) == len(set(names))
    (entry,) = [g for g in gates if g["name"] == "seeded_widgets"]
    version = tomllib.loads((layout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert entry == {"name": "seeded_widgets", "kind": "gate", "entries": ["assert_seeded_widgets"], "since": version, "summary": "Widgets are seeded"}
    readme = (layout / "README.md").read_text(encoding="utf-8")
    assert f"| [`seeded_widgets`](src/py_ci_shared/seeded_widgets.py) | gate | {version} | `assert_seeded_widgets` | Widgets are seeded |" in readme
    teeth = (layout / "tests" / "test_gate_teeth.py").read_text(encoding="utf-8")
    assert 'Canary("seeded_widgets", lambda d: _gate("seeded_widgets").assert_seeded_widgets(d, use_git=False)),' in teeth
    module = (layout / "src" / "py_ci_shared" / "seeded_widgets.py").read_text(encoding="utf-8")
    for needle in ("from ._core import Baseline, Finding, ImportAliases, scan_python", "min_files", "allow_unparsed", "def find_seeded_widgets"):
        assert needle in module
    compile(module, "seeded_widgets.py", "exec")
    bom = layout / "tests" / "canary" / "seeded_widgets" / "bom" / "seed.py.canary"
    assert bom.read_bytes() == b"\xef\xbb\xbf" + (bom.parents[1] / "violation" / "seed.py.canary").read_bytes()


def test_the_edited_files_keep_their_line_endings(layout):
    registry = layout / "src" / "py_ci_shared" / "registry.toml"
    registry.write_bytes(registry.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    readme = layout / "README.md"
    readme.write_bytes(readme.read_bytes().replace(b"\r\n", b"\n"))
    new_gate(layout, "aaa_first", summary="First")
    assert b"\r\n" in registry.read_bytes() and registry.read_bytes().count(b"\n") == registry.read_bytes().count(b"\r\n")
    assert b"\r\n" not in readme.read_bytes()
    assert _registry(layout)[0]["name"] == "aaa_first"


def test_a_library_has_a_finder_and_no_assert(layout):
    new_gate(layout, "zzz_last_library", kind="library", summary="A library")
    (entry,) = [g for g in _registry(layout) if g["name"] == "zzz_last_library"]
    assert entry["kind"] == "library" and "entries" not in entry
    assert [g["name"] for g in _registry(layout)][-1] == "zzz_last_library"
    module = (layout / "src" / "py_ci_shared" / "zzz_last_library.py").read_text(encoding="utf-8")
    assert "def find_zzz_last_library" in module and "def assert_" not in module and "Baseline" not in module
    teeth = (layout / "tests" / "test_gate_teeth.py").read_text(encoding="utf-8")
    assert '_assert_empty(_gate("zzz_last_library").find_zzz_last_library(d, use_git=False))' in teeth


def test_it_refuses_to_overwrite_and_changes_nothing(layout):
    (layout / "tests" / "test_half_done.py").write_bytes(b"# someone's work\n")
    before = {p: p.read_bytes() for p in layout.rglob("*") if p.is_file()}
    with pytest.raises(ScaffoldError, match=r"refusing to overwrite: tests/test_half_done\.py"):
        new_gate(layout, "half_done", summary="x")
    after = {p: p.read_bytes() for p in layout.rglob("*") if p.is_file()}
    assert after == before
    with pytest.raises(ScaffoldError, match=r"already in registry\.toml"):
        new_gate(layout, "naive_utcnow", summary="x")


@pytest.mark.parametrize("name", ["Bad", "_private", "class", "with-dash", "1abc", ""])
def test_it_refuses_a_name_that_is_no_public_module(layout, name):
    with pytest.raises(ScaffoldError, match="not a public module name"):
        new_gate(layout, name, summary="x")


def test_kind_and_summary_are_validated(layout):
    with pytest.raises(ScaffoldError, match="kind must be"):
        new_gate(layout, "okname", kind="cli", summary="x")
    with pytest.raises(ScaffoldError, match="one non-empty line"):
        new_gate(layout, "okname", summary="two\nlines")


def test_registry_insertion_keeps_name_order_and_blank_lines():
    text = '# head\n\n[[gate]]\nname = "b"\nsummary = "B"\n\n[[gate]]\nname = "d"\nsummary = "D"\n'
    block = '[[gate]]\nname = "c"\nsummary = "C"\n'
    assert (
        _insert_registry(text, block, "c")
        == '# head\n\n[[gate]]\nname = "b"\nsummary = "B"\n\n[[gate]]\nname = "c"\nsummary = "C"\n\n[[gate]]\nname = "d"\nsummary = "D"\n'
    )
    assert _insert_registry(text, '[[gate]]\nname = "e"\n', "e").endswith('summary = "D"\n\n[[gate]]\nname = "e"\n')
    assert _insert_registry(text, '[[gate]]\nname = "a"\n', "a").startswith('# head\n\n[[gate]]\nname = "a"\n\n[[gate]]\nname = "b"')


def test_find_checkout_walks_up_and_fails_outside_one(layout, tmp_path):
    deep = layout / "tests" / "canary"
    assert find_checkout(deep) == layout.resolve()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ScaffoldError, match="no py-ci-shared checkout"):
        find_checkout(elsewhere)


def test_the_cli_subcommand(layout, capsys):
    assert cli.main(["new-gate", "cli_made", "--summary", "Made by the CLI", "--repo", str(layout)]) == 0
    out = capsys.readouterr().out
    assert "created src/py_ci_shared/cli_made.py" in out and "updated README.md" in out
    assert cli.main(["new-gate", "cli_made", "--repo", str(layout)]) == 2
    assert "already in registry.toml" in capsys.readouterr().err


def test_the_scaffold_passes_the_inventory_and_fails_until_filled_in(full_layout):
    """In the copy: the inventory accepts the new gate, while its own tests and its canary fail."""
    new_gate(full_layout, "unfinished_gate", summary="Not written yet")
    env = dict(os.environ, PYTHONPATH=str(full_layout / "src"), PYTHONUNBUFFERED="1")

    def pytest_run(*ids: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider", "-rA", *ids]
        return subprocess.run(cmd, cwd=full_layout, env=env, capture_output=True, text=True, check=False)

    passing = pytest_run(
        "tests/test_package_inventory.py::test_every_module_is_registered_or_declared_internal",
        "tests/test_package_inventory.py::test_registry_names_are_unique_and_sorted",
        "tests/test_package_inventory.py::test_each_spec_matches_its_module[unfinished_gate]",
        "tests/test_package_inventory.py::test_each_registered_module_has_a_test_file_that_imports_it[unfinished_gate]",
        "tests/test_package_inventory.py::test_the_readme_catalogue_is_the_rendered_registry",
        "tests/test_gate_teeth.py::test_every_scanning_gate_has_a_canary_or_a_reasoned_exemption",
    )
    assert passing.returncode == 0 and "6 passed" in passing.stdout, passing.stdout[-3000:] + passing.stderr[-2000:]
    failing = pytest_run("tests/test_unfinished_gate.py", "tests/test_gate_teeth.py::test_canary_fixture_has_teeth[unfinished_gate]")
    assert failing.returncode == 1 and "6 failed" in failing.stdout and " passed" not in failing.stdout, failing.stdout[-3000:]
    canary = pytest_run(
        "tests/test_gate_teeth.py::test_gate_bites_its_canary[unfinished_gate-violation]",
        "tests/test_gate_teeth.py::test_gate_bites_its_canary[unfinished_gate-clean]",
    )
    assert canary.returncode == 1 and "2 failed" in canary.stdout, canary.stdout[-3000:]
    assert "not naming the seed" in canary.stdout and "reported its clean control" in canary.stdout
