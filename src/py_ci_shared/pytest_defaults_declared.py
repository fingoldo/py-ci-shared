"""A consumer's pytest ``addopts`` must carry the mandatory flags, so a bare ``pytest`` run, an IDE run and a CI step get them too.

The flags are read from ``[tool.pytest.ini_options] addopts`` in the repo's ``pyproject.toml``. The wrapper ``py-ci-shared tool pytest_run``
adds them only when it is invoked, so a run started any other way would miss them. Required:

* ``--durations=<n>``: the slow tests are the first thing to read in a long run;
* ``-p no:cacheprovider``: no ``.pytest_cache`` writes into a checkout that other runs read.

A repo with no ``[tool.pytest.ini_options]`` table, or no ``pyproject.toml`` at all, is a finding: the flags are not declared. The
worker count is not checked here, because it is chosen from free RAM by the ``py_ci_shared`` pytest plugin at run time.

Usage in a consumer's meta test::

    from py_ci_shared.pytest_defaults_declared import assert_pytest_defaults_declared

    def test_pytest_defaults_declared():
        assert_pytest_defaults_declared(".")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Union

from ._core import EmptyScanError, Finding, UnparsedFilesError
from ._toml_compat import tomllib

__all__ = ["RULE", "assert_pytest_defaults_declared", "find_pytest_defaults_declared"]

RULE = "pytest-defaults-declared"
_DURATIONS = re.compile(r"--durations=\d+")
_NO_CACHE = re.compile(r"-p\s+no:cacheprovider|-p=no:cacheprovider|-pno:cacheprovider")


def _addopts(pyproject: dict[str, Any]) -> Optional[str]:
    """The ``addopts`` string of the pytest table, joined when written as a list, or None when the table is absent."""
    table = pyproject.get("tool", {}).get("pytest", {}).get("ini_options")
    if table is None:
        return None
    value = table.get("addopts", "")
    return " ".join(map(str, value)) if isinstance(value, list) else str(value)


def find_pytest_defaults_declared(root: Union[str, Path], *, min_files: int = 1, use_git: Optional[bool] = None) -> list[Finding]:
    """One finding per mandatory flag missing from the pytest ``addopts`` under *root*, or one when the table is absent.

    *min_files* and *use_git* are accepted for the common gate signature; the check reads one file, so they do not change it.
    Raises ``EmptyScanError`` when *root* has no ``pyproject.toml`` at all.
    """
    del min_files, use_git  # the gate reads one file; the signature matches the other gates
    path = Path(root) / "pyproject.toml"
    if not path.is_file():
        raise EmptyScanError(f"pytest_defaults_declared: no pyproject.toml under {Path(root)}")
    text = path.read_text(encoding="utf-8-sig")
    try:
        pyproject = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise UnparsedFilesError(f"pyproject.toml could not be parsed, so its pytest flags are unknown: {exc}") from exc
    addopts = _addopts(pyproject)
    if addopts is None:
        return [Finding("pyproject.toml", 1, RULE, "no [tool.pytest.ini_options] table: the mandatory pytest flags are not declared")]
    out: list[Finding] = []
    line = next((i for i, text_line in enumerate(text.splitlines(), 1) if "addopts" in text_line), 1)
    if not _DURATIONS.search(addopts):
        out.append(Finding("pyproject.toml", line, RULE, "addopts has no --durations=<n>: the slow tests of a bare pytest run go unreported"))
    if not _NO_CACHE.search(addopts):
        out.append(Finding("pyproject.toml", line, RULE, "addopts lacks -p no:cacheprovider: a bare pytest run writes .pytest_cache into the checkout"))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_pytest_defaults_declared(root: Union[str, Path], *, min_files: int = 1, use_git: Optional[bool] = None) -> None:
    """Fail on any mandatory pytest flag that the repo's pyproject ``addopts`` does not declare."""
    found = find_pytest_defaults_declared(root, min_files=min_files, use_git=use_git)
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s); add the flags to [tool.pytest.ini_options] addopts:\n  " + "\n  ".join(f.render() for f in found))
