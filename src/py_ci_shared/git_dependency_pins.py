"""Shared check: every git-URL dependency in pyproject.toml is pinned to a
full commit SHA, not floating on a branch/tag.

Generalizes a 2026-07-21 audit finding (S-04): a ``py-ci-shared`` git
dependency in one consuming repo floated on the default branch with no
commit pin, unlike a sibling ``pyutilz`` git dependency in the same file
which WAS pinned -- and the adjacent comment incorrectly claimed it
mirrored that pattern. A floating git dependency means every fresh
``pip install`` (a new dev machine, a CI runner, a Docker rebuild) can
silently pull in a DIFFERENT commit than the one actually developed/tested
against -- the exact reproducibility guarantee a version pin exists to
provide, undone by one un-pinned line.

Usage (in a consuming repo's test suite)::

    from pathlib import Path
    from py_ci_shared.git_dependency_pins import assert_all_git_dependencies_pinned

    def test_all_git_dependencies_pinned():
        assert_all_git_dependencies_pinned(Path(__file__).resolve().parents[2] / "pyproject.toml")

Deliberately dependency-light: ``tomllib``/``pytest`` are imported lazily,
matching this package's other modules.
"""

from __future__ import annotations

import re
from pathlib import Path

# A git URL dependency, PEP 508 direct-reference form: `name @ git+https://host/path@ref`.
# Captures the ref (everything after the LAST '@') so `git+https://user@host/path@ref`
# (a URL with embedded auth) doesn't get its host-auth '@' mistaken for the ref separator.
_GIT_DEP_RE = re.compile(r"^\s*[\"']?[\w.-]+\s*@\s*git\+[^\s\"']+@([^\s\"'#]+)", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def find_unpinned_git_dependencies(pyproject_path: Path) -> list[str]:
    """Return every git-dependency line in ``pyproject_path`` whose ref is
    NOT a full 40-hex-character commit SHA (a branch name, a tag, or a
    short/abbreviated SHA all count as unpinned -- only the full SHA
    guarantees the exact commit, since a tag can be moved and a short SHA
    can become ambiguous as the repo grows).

    Returns the raw ref string found for each violation (e.g. ``"master"``,
    ``"v1.2.0"``), not line numbers -- pyproject.toml dependency arrays are
    typically short enough that the ref itself is enough to locate the line.
    """
    text = pyproject_path.read_text(encoding="utf-8")
    violations = []
    for m in _GIT_DEP_RE.finditer(text):
        ref = m.group(1)
        if not _FULL_SHA_RE.match(ref):
            violations.append(ref)
    return violations


def assert_all_git_dependencies_pinned(pyproject_path: Path) -> None:
    """Fail if any git-URL dependency in ``pyproject_path`` isn't pinned to
    a full 40-hex-character commit SHA. Call this directly as the body of
    a ``test_*`` function -- no baseline/refresh mechanism, unlike the
    code-audit/LOC-budget helpers in this package, since an unpinned git
    dependency is unconditionally wrong (there's no legitimate "grandfathered"
    case for a reproducibility guarantee).
    """
    import pytest

    violations = find_unpinned_git_dependencies(pyproject_path)
    if violations:
        pytest.fail(
            f"{len(violations)} git-URL dependenc{'y is' if len(violations) == 1 else 'ies are'} "
            f"not pinned to a full commit SHA in {pyproject_path} -- a fresh install can silently "
            f"resolve to a different commit than the one actually developed/tested against:\n  "
            + "\n  ".join(violations)
        )
