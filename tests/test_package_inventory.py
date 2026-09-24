"""The package's own inventory agrees with itself: modules, registry, tests, README, console scripts, shipped configs."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

from py_ci_shared import cli, registry
from py_ci_shared._toml_compat import tomllib
from py_ci_shared.entry_points_resolvable import assert_all_entry_points_resolvable

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "py_ci_shared"
README = REPO / "README.md"
PYPROJECT = REPO / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_every_module_is_registered_or_declared_internal():
    missing = registry.unregistered_modules(PKG)
    assert not missing, (
        f"module(s) {missing} are neither in registry.GATES nor in registry.INTERNAL_MODULES. Add a GateSpec for each "
        f"public one (name, kind, entries, since, budget_s, summary), or an INTERNAL_MODULES line saying why it is private."
    )


def test_the_registry_names_only_modules_that_exist():
    on_disk = set(registry.discover_modules(PKG))
    ghosts = sorted({s.name for s in registry.GATES} - on_disk)
    ghosts += sorted(set(registry.INTERNAL_MODULES) - on_disk)
    assert not ghosts, f"registry entries with no module on disk: {ghosts}; delete them"


def test_registry_names_are_unique_and_sorted():
    names = [s.name for s in registry.GATES]
    assert len(names) == len(set(names)), "duplicate GateSpec names"
    assert names == sorted(names), "keep registry.GATES sorted by name so the README catalogue is stable"


@pytest.mark.parametrize("spec", registry.GATES, ids=lambda s: s.name)
def test_each_spec_matches_its_module(spec: registry.GateSpec):
    module = importlib.import_module(spec.module)
    public_asserts = sorted(n for n, f in inspect.getmembers(module, inspect.isfunction) if n.startswith("assert_") and f.__module__ == module.__name__)
    assert sorted(spec.entries) == public_asserts, f"{spec.name}: registry entries {sorted(spec.entries)} != module's assert_* {public_asserts}"
    assert spec.kind == ("gate" if spec.entries else ("cli" if spec.cli else "library")), f"{spec.name}: kind {spec.kind!r} does not fit"
    if spec.cli:
        assert callable(getattr(module, "main", None)), f"{spec.name} is marked cli but has no main()"
    assert spec.summary and spec.since and spec.budget_s > 0, f"{spec.name}: summary, since and budget_s are required"


@pytest.mark.parametrize("spec", registry.GATES, ids=lambda s: s.name)
def test_each_registered_module_has_a_test_file_that_imports_it(spec: registry.GateSpec):
    files = [REPO / f for f in spec.test_files]
    absent = [str(f.relative_to(REPO)) for f in files if not f.is_file()]
    assert not absent, f"{spec.name}: test file(s) {absent} do not exist; add one or set GateSpec.tests"
    texts = [f.read_text(encoding="utf-8") for f in files]
    assert any(spec.module in t or f"import {spec.name}" in t for t in texts), f"{spec.name}: none of {spec.test_files} imports it"


def test_the_readme_catalogue_is_the_rendered_registry():
    text = README.read_text(encoding="utf-8")
    start, end = text.find(registry.CATALOGUE_START), text.find(registry.CATALOGUE_END)
    assert start != -1 and end != -1, "README.md has no gate catalogue markers; paste `py-ci-shared list --markdown` under 'Gate catalogue'"
    block = text[start : end + len(registry.CATALOGUE_END)]
    assert block == registry.render_catalogue(), (
        "README.md's gate catalogue is out of date. Replace the block between the markers with the output of " "`py-ci-shared list --markdown`."
    )


def test_every_registered_module_has_a_readme_row():
    """Each row links to the module file, which must exist."""
    text = README.read_text(encoding="utf-8")
    for spec in registry.GATES:
        assert f"[`{spec.name}`](src/py_ci_shared/{spec.name}.py)" in text, f"{spec.name} has no README catalogue row"
        assert (PKG / f"{spec.name}.py").is_file()


def test_every_console_script_resolves():
    assert_all_entry_points_resolvable(PYPROJECT, min_entries=4)


def test_the_console_scripts_cover_the_umbrella_cli_and_the_pytest_plugin():
    project = _pyproject()["project"]
    assert project["scripts"]["py-ci-shared"] == "py_ci_shared.cli:main"
    assert project["entry-points"]["pytest11"] == {"py_ci_shared": "py_ci_shared.pytest_plugin"}
    importlib.import_module("py_ci_shared.pytest_plugin")


@pytest.mark.parametrize("spec", [s for s in registry.GATES if s.cli], ids=lambda s: s.name)
def test_every_cli_module_is_reachable_through_the_umbrella_script(spec: registry.GateSpec):
    """``py-ci-shared tool <name>`` finds a main; the subcommand's parser accepts the name."""
    args = cli.build_parser().parse_args(["tool", spec.name, "--help"])
    assert args.module == spec.name and args.args == ["--help"]
    assert callable(importlib.import_module(spec.module).main)


def test_shipped_configs_are_byte_identical_to_the_repo_copies():
    for name in cli.CONFIG_NAMES:
        shipped = cli.config_path(name)
        repo_copy = REPO / "configs" / shipped.name
        assert shipped.read_bytes() == repo_copy.read_bytes(), f"{shipped} differs from {repo_copy}; configs/ is the source, copy it over the package-data copy"


def test_package_data_ships_the_configs():
    assert _pyproject()["tool"]["setuptools"]["package-data"]["py_ci_shared"] == ["configs/*.toml", "registry.toml", "py.typed"]


def test_the_package_is_marked_typed():
    """PEP 561: without py.typed a consumer's mypy reports every py_ci_shared import as import-untyped."""
    assert (REPO / "src" / "py_ci_shared" / "py.typed").is_file()


def test_every_pre_commit_hook_runs_a_module_that_has_a_main():
    import yaml

    hooks = yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    ours = [h for h in hooks if h["entry"].startswith("python -m py_ci_shared.")]
    assert len(ours) >= 4, [h["id"] for h in hooks]
    for hook in ours:
        module = hook["entry"].split()[2]
        assert callable(getattr(importlib.import_module(module), "main", None)), f"hook {hook['id']}: {module} has no main()"
