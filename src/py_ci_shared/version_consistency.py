"""Shared check: a package reports one version wherever it states one.

A version lives in several places at once -- ``[project].version`` in pyproject, a ``__version__`` in the
package's ``__init__`` or a ``version.py``, sometimes re-exported -- and each is edited by hand. They drift
without anything failing: the release is tagged from one, ``pip show`` reads another, a bug report quotes a
third. mlframe, realtime_applications and autopsia each carried a copy of this comparison.

Sources are read three ways so no repo needs a shim: pyproject by TOML, files by a ``__version__ = "..."``
regex (no import, so a broken package still reports), and modules by import (for a re-export that only
exists at runtime). ``normalize_separators`` compares ``2026-04-19`` equal to ``2026.04.19`` for a
calendar-version repo whose two sources use different separators by convention.

Usage::

    from py_ci_shared.version_consistency import assert_versions_agree

    def test_version_is_consistent():
        assert_versions_agree(REPO_ROOT, files=["src/pkg/__init__.py"], modules=["pkg"])
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Iterable
from pathlib import Path

from ._toml_compat import tomllib

_VERSION_RE = re.compile(r"""^__version__\s*(?::\s*str\s*)?=\s*["']([^"']+)["']""", re.MULTILINE)


def version_sources(repo_root: Path, *, files: Iterable[str] = (), modules: Iterable[str] = (), pyproject: bool = True) -> dict[str, str]:
    """``{source label: version}`` for every source that states one."""
    out: dict[str, str] = {}
    toml = repo_root / "pyproject.toml"
    if pyproject and toml.is_file():
        with toml.open("rb") as fh:
            v = tomllib.load(fh).get("project", {}).get("version")
        if isinstance(v, str):
            out["pyproject.toml [project].version"] = v
    for rel in files:
        path = repo_root / rel
        if path.is_file():
            m = _VERSION_RE.search(path.read_text(encoding="utf-8", errors="replace"))
            if m:
                out[f"{rel} __version__"] = m.group(1)
    for name in modules:
        v = getattr(importlib.import_module(name), "__version__", None)
        if isinstance(v, str):
            out[f"{name}.__version__"] = v
    return out


def _norm(v: str, normalize_separators: bool) -> str:
    return re.sub(r"[-._]", ".", v.strip()) if normalize_separators else v.strip()


def assert_versions_agree(
    repo_root: Path,
    *,
    files: Iterable[str] = (),
    modules: Iterable[str] = (),
    pyproject: bool = True,
    normalize_separators: bool = False,
    min_sources: int = 2,
) -> None:
    """Fail when the sources disagree, or when fewer than *min_sources* state a version at all.

    ``min_sources`` is the guard against a comparison of one: a regex that stopped matching leaves a single
    source, which always "agrees" with itself.
    """
    import pytest

    sources = version_sources(repo_root, files=files, modules=modules, pyproject=pyproject)
    if len(sources) < min_sources:
        pytest.fail(f"only {len(sources)} version source(s) found ({sorted(sources)}); expected at least {min_sources}, so nothing is being compared")
    if len({_norm(v, normalize_separators) for v in sources.values()}) > 1:
        pytest.fail("the version disagrees across its sources:\n  " + "\n  ".join(f"{k} = {v!r}" for k, v in sources.items()))
