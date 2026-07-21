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

# A git URL dependency, PEP 508 direct-reference form: `name @ git+URL[@ref]`. Captures the
# WHOLE URL blob (ref, if any, still embedded) -- ref extraction is done separately in
# _extract_ref, since a regex alone can't reliably tell an auth '@' (`git+https://user@host/...`)
# apart from a ref '@' (`git+https://host/...@ref`) when only ONE '@' is present in the blob.
_GIT_DEP_RE = re.compile(r"^\s*[\"']?[\w.-]+\s*@\s*(git\+[^\s\"'#]+)", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _extract_ref(git_url: str) -> str | None:
    """Return the ref suffix of a ``git+URL[@ref]`` string, or ``None`` if no
    ref is present at all (the fully-unpinned, floats-on-whatever-HEAD-is case).

    An auth ``@`` (``git+https://user:pass@host/path``) always sits between
    ``://`` and the FIRST ``/`` that follows it (host/port can't contain a
    ``/``); any ``@`` after that first post-``://`` ``/`` is a ref separator,
    not auth. This correctly handles the no-ref case (whether or not auth is
    present) instead of assuming "one ``@`` present" always means "a ref is
    present" -- a bare ``git+https://host/path`` and a bare
    ``git+https://user@host/path`` (auth, no ref) both correctly resolve to
    "no ref", where a naive single-``@``-means-ref-separator regex would
    misparse the latter's auth ``@`` as if it introduced a ref.
    """
    scheme_end = git_url.find("://")
    if scheme_end == -1:
        # No scheme (e.g. a bare `git+ssh` shorthand this project doesn't use) --
        # fall back to "last '@', if any, is the ref separator".
        if "@" not in git_url:
            return None
        return git_url.rsplit("@", 1)[1]
    host_start = scheme_end + 3
    path_start = git_url.find("/", host_start)
    if path_start == -1:
        path_start = len(git_url)
    after_host = git_url[path_start:]
    if "@" not in after_host:
        return None
    return after_host.rsplit("@", 1)[1]


def find_unpinned_git_dependencies(pyproject_path: Path) -> list[str]:
    """Return every git-dependency line in ``pyproject_path`` whose ref is
    NOT a full 40-hex-character commit SHA (a branch name, a tag, a
    short/abbreviated SHA, or NO REF AT ALL all count as unpinned -- only
    the full SHA guarantees the exact commit, since a tag can be moved, a
    short SHA can become ambiguous as the repo grows, and no ref at all
    floats on whatever the default branch's HEAD happens to be at install
    time).

    Returns the raw ref string found for each violation (e.g. ``"master"``,
    ``"v1.2.0"``, or the literal string ``"<no ref>"`` when none is present
    at all), not line numbers -- pyproject.toml dependency arrays are
    typically short enough that this is enough to locate the line.
    """
    text = pyproject_path.read_text(encoding="utf-8")
    violations = []
    for m in _GIT_DEP_RE.finditer(text):
        ref = _extract_ref(m.group(1))
        if ref is None:
            violations.append("<no ref>")
        elif not _FULL_SHA_RE.match(ref):
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
            f"resolve to a different commit than the one actually developed/tested against:\n  " + "\n  ".join(violations)
        )
