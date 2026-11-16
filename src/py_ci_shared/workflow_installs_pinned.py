"""A CI step that clones or pip-installs a first-party repo from git must pin a commit.

A floating ``git clone`` or ``pip install "x @ git+https://..."`` means the same workflow gets different dependency code
on different runs (mlframe X_CICD_DEPENDENCIES-1: sixteen pyutilz pins named two commits across nine workflows).

Rules, per workflow file under the given directory:

* ``git clone <url>`` inside a ``run:`` block needs, somewhere in that same block, a ``checkout`` of a 40-hex SHA or a
  ``${VAR}`` reference. Multi-line ``run: |`` blocks are read as one block.
* ``git+https://...`` in a line needs ``@<40-hex>`` or ``@${VAR}`` directly after the repository path.

A line accepted on purpose carries ``# unpinned-ok: <reason>``. A marker with no reason is a finding.

Usage::

    from py_ci_shared.workflow_installs_pinned import assert_workflow_installs_pinned

    def test_workflows_install_pinned_git_deps():
        assert_workflow_installs_pinned(".github/workflows")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._core import EmptyScanError, Finding

__all__ = ["MARKER", "RULE", "assert_workflow_installs_pinned", "find_workflow_installs_pinned"]

RULE = "workflow-installs-pinned"
MARKER = "unpinned-ok"

#: A YAML key whose value is a command: ``run: cmd``, or any ``key: |`` / ``key: >`` block scalar (``install-command: |``).
_RUN_KEY = re.compile(r"^(\s*)(?:-\s+)?([\w-]+):\s*(.*)$")
_BLOCK_START = re.compile(r"^[|>][-+0-9]*\s*$")
_CLONE = re.compile(r"\bgit\s+clone\b")
_GIT_URL = re.compile(r'git\+https?://[^\s"\']+')
_PINNED_URL = re.compile(r"@(?:[0-9a-f]{40}\b|\$\{?[A-Za-z_]\w*\}?)")
_PINNED_CHECKOUT = re.compile(r"checkout\s+(?:[0-9a-f]{40}\b|\$\{?[A-Za-z_]\w*\}?)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _run_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """``(first, last)`` 0-based line indexes of each ``run:`` block; an inline ``run: cmd`` is one line."""
    blocks: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        m = _RUN_KEY.match(line)
        if not m or line.lstrip().startswith("#"):
            continue
        key_indent = len(m.group(1))
        value = m.group(3).strip()
        if not _BLOCK_START.match(value):
            if m.group(2) == "run" or value:
                blocks.append((i, i))
            continue
        last = i
        for j in range(i + 1, len(lines)):
            if lines[j].strip() and _indent(lines[j]) <= key_indent:
                break
            if lines[j].strip():
                last = j
        blocks.append((i, last))
    return blocks


def _marker_reason(lines: list[str], index: int) -> Optional[str]:
    """The reason after ``# unpinned-ok:`` on line *index* or the one above; "" when the marker has no reason."""
    for k in (index, index - 1):
        if 0 <= k < len(lines) and "#" in lines[k] and MARKER in lines[k]:
            return lines[k].split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def _problem(line: str, block: str) -> Optional[str]:
    """Why *line* installs from git unpinned (its command *block* is the evidence for a clone), or None."""
    if _CLONE.search(line) and not _PINNED_CHECKOUT.search(block):
        return "`git clone` with no `checkout` of a 40-hex SHA or ${VAR} in the same run block"
    for url in _GIT_URL.findall(line):
        if not _PINNED_URL.search(url):
            return f"`{url.split('@')[0]}` installs from git with no pinned commit after the repository path"
    return None


def _check_file(path: Path, label: str) -> list[Finding]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    block_of: dict[int, tuple[int, int]] = {}
    for first, last in _run_blocks(lines):
        for k in range(first, last + 1):
            block_of.setdefault(k, (first, last))
    out: list[Finding] = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        first, last = block_of.get(i, (i, i))
        problem = _problem(line, "\n".join(lines[first : last + 1]))
        if problem is None:
            continue
        reason = _marker_reason(lines, i)
        if reason:
            continue
        suffix = "; the # unpinned-ok marker needs a reason after it" if reason == "" else ""
        out.append(Finding(label, i + 1, RULE, f"{problem}; pin a commit{suffix}"))
    return out


def find_workflow_installs_pinned(workflows_dir: Union[str, Path], *, min_files: int = 1) -> list[Finding]:
    """Every unpinned first-party git clone or install in the workflow files of *workflows_dir*, sorted by file and line.

    Raises ``EmptyScanError`` when fewer than *min_files* workflow files exist: an empty directory is a wrong path, not a pass."""
    root = Path(workflows_dir)
    files = sorted(p for p in root.glob("*.y*ml") if p.is_file())
    if len(files) < min_files:
        raise EmptyScanError(f"workflow_installs_pinned: found {len(files)} workflow file(s) under {root}, need {min_files}")
    out: list[Finding] = []
    for path in files:
        out.extend(_check_file(path, path.name))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_workflow_installs_pinned(workflows_dir: Union[str, Path], *, min_files: int = 1) -> None:
    """Fail on any unpinned first-party git clone or install in a workflow."""
    found = find_workflow_installs_pinned(workflows_dir, min_files=min_files)
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
