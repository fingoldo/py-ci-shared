"""A tracked Python file under an ``env``, ``venv``, ``build`` or ``dist`` directory is invisible to code_audit.

The code_audit scanners prune those directory names with no git knowledge, so a tracked ``src/pkg/env/`` (an RL
``env`` package, a config ``env`` package) is never read by any of them. The shared core keeps such a package, so the
two families disagree about what the corpus is (40-T1). This gate reports every tracked file that the code_audit walk
would skip, so the package is either renamed or the disagreement is removed deliberately.

Usage in a consumer's meta test::

    from py_ci_shared.tracked_env_packages import assert_tracked_env_packages

    def test_tracked_env_packages():
        assert_tracked_env_packages("src")
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ._core import Finding, scan_python

__all__ = ["RULE", "assert_tracked_env_packages", "find_tracked_env_packages"]

RULE = "tracked-env-packages"

# The directory names code_audit's _DEFAULT_EXCLUDE_DIRS prunes (code_audit/_base.py).
PRUNED_DIR_NAMES = frozenset({"build", "dist", "env", ".env", "venv"})


def _pruned_segment(rel: str) -> Optional[str]:
    """The first directory component of *rel* that code_audit would prune, or None."""
    for part in rel.split("/")[:-1]:
        if part in PRUNED_DIR_NAMES:
            return part
    return None


def find_tracked_env_packages(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every Python file under *root* whose directory path code_audit would prune, sorted by path.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        segment = _pruned_segment(parsed.rel)
        if segment is not None:
            out.append(
                Finding(
                    parsed.rel,
                    1,
                    RULE,
                    f"tracked Python under '{segment}/', which the code_audit scanners skip; rename the package or move it out of '{segment}/'",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_tracked_env_packages(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any tracked Python file that code_audit would prune. There is no baseline: the gate is hard."""
    found = find_tracked_env_packages(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(f"{len(found)} tracked-env-packages finding(s):\n  " + "\n  ".join(f.render() for f in found))
