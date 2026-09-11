"""Shared check: the suite examines the code in THIS checkout, not a copy installed somewhere else.

Everything else a suite asserts is worthless if this fails, and nothing else notices. Two ways it goes
wrong, one found in each of the repos that carried a copy of this check:

* **A namespace portion.** A stale editable-install mapping (the checkout it pointed at was deleted) makes
  ``import pkg`` fall through to a directory on ``sys.path`` that merely has the package's name -- a second
  clone's repo root, say. It imports, reports ``__file__ = None``, and fails later with
  ``cannot import name '_json' from 'pkg' (unknown location)``, naming neither cause nor path (autopsia,
  2026-08-24).
* **The original tree.** Under an editable install, a COPY of the repository (a CI checkout, a bisect
  worktree, a mutation sandbox) imports the original tree unless its conftest puts its own ``src`` first.
  A mutation sweep of such a copy killed 0 of 164 mutants, because the tests read the unmutated original
  (dash_app_core).

``assert_modules_resolve_to_checkout`` covers the first and the ordinary case of the second; checking a
SUBMODULE matters, because a namespace parent can still hand out correct submodules through the editable
finder. ``assert_a_copy_imports_itself`` is the teeth for the second: in an ordinary checkout the paths
coincide whether or not the fix is in place, so only a real copy can tell.

Usage::

    from py_ci_shared.checkout_resolution import assert_modules_resolve_to_checkout

    def test_the_suite_tests_this_checkout():
        assert_modules_resolve_to_checkout(REPO_ROOT, ["pkg", "pkg.core"])
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

_COPY_IGNORE = ("__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.egg-info", ".venv", "venv", "node_modules")


def module_resolution_problems(repo_root: Path, module_names: Iterable[str]) -> list[str]:
    """One message per module that is a namespace portion or resolves outside *repo_root*."""
    root = repo_root.resolve()
    problems = []
    for name in module_names:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if origin is None:
            problems.append(
                f"{name} has no __file__: it resolved to a namespace portion, not a real module. A stale editable "
                f"install is the usual cause; run `python -m pip install -e . --no-deps` from {root}."
            )
            continue
        resolved = Path(origin).resolve()
        if root not in resolved.parents:
            problems.append(f"{name} resolved to {resolved}, outside {root}: the tests are examining some other copy of the code.")
    return problems


def assert_modules_resolve_to_checkout(repo_root: Path, module_names: Iterable[str]) -> None:
    import pytest

    names = list(module_names)
    if not names:
        pytest.fail("no module names given, so nothing was checked")
    problems = module_resolution_problems(repo_root, names)
    if problems:
        pytest.fail("\n".join(problems))


def resolved_in_a_copy(repo_root: Path, package: str, workdir: Path, *, timeout: int = 300, pytest_args: Iterable[str] = ()) -> str:
    """Copy the repository to *workdir*, import *package* inside the copy's own test run, return its ``__file__``."""
    copy = workdir / "copy"
    shutil.copytree(repo_root, copy, ignore=shutil.ignore_patterns(*_COPY_IGNORE))
    tests = copy / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_zz_resolution_probe.py").write_text(
        f"import {package}\n\n\ndef test_probe():\n    print('RESOLVED:', {package}.__file__)\n",
        encoding="utf-8",
    )
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_zz_resolution_probe.py",
        "-q",
        "--no-header",
        "-s",
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
        *pytest_args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(copy), timeout=timeout)
    return next((line.partition(": ")[2].strip() for line in proc.stdout.splitlines() if line.startswith("RESOLVED:")), "")


def assert_a_copy_imports_itself(repo_root: Path, package: str, workdir: Path, *, timeout: int = 300, pytest_args: Iterable[str] = ()) -> None:
    """The teeth: a COPY of the repository must import its own package, not the original tree."""
    import pytest

    resolved = resolved_in_a_copy(repo_root, package, workdir, timeout=timeout, pytest_args=pytest_args)
    if not resolved:
        pytest.fail(f"the resolution probe did not run in the copy under {workdir}")
    if str((workdir / "copy").resolve()) not in str(Path(resolved).resolve()):
        pytest.fail(f"a COPY of this repository imported {resolved}: the original tree, not itself. Its tests would examine unmodified code.")
