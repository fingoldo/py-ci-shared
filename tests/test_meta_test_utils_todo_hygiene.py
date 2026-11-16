"""PT-4 — TODO/FIXME/XXX/HACK hygiene, applied to a seeded tree (audit 44-T3).

The scanner is covered by ``test_meta_test_utils.py``. This file used to scan the installed pyutilz package, which
is a moving working tree locally and a pinned SHA in CI, so it turned red on another repository's edits. It now
seeds its own tree under ``tmp_path`` and checks the rule on that.

Every marker must carry an attribution: assignee in parens (``TODO(name): ...``) or ISO date
(``TODO 2026-04-28: ...``) or ``@assignee`` mention.
"""

from __future__ import annotations

import sys
from pathlib import Path

from py_ci_shared.meta_test_utils import (
    ATTRIBUTION_RE,
    scan_todo_markers,
)

_MARKERS = ("TODO", "FIXME", "XXX", "HACK")


def _seed(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text(body, encoding="utf-8")
    return root


def _bare_markers(root: Path) -> list[str]:
    out: list[str] = []
    for path, lineno, _kw, line in scan_todo_markers(root):
        if not ATTRIBUTION_RE.search(line):
            out.append(f"{path.relative_to(root).as_posix()}:{lineno}")
    return out


def test_a_bare_marker_in_the_seeded_tree_is_reported(tmp_path):
    root = _seed(tmp_path / "pkg", "x = 1  # TODO: remove this\n")
    assert _bare_markers(root) == ["mod.py:1"]


def test_an_attributed_marker_in_the_seeded_tree_is_not_reported(tmp_path):
    root = _seed(
        tmp_path / "pkg",
        "a = 1  # TODO(alice): remove\nb = 2  # FIXME 2026-04-28: check\nc = 3  # HACK @bob: temp\n",
    )
    assert _bare_markers(root) == []


def test_marker_count_summary_on_the_seeded_tree(tmp_path):  # audit-exempt: nondiscriminating_test -- advisory marker-count summary; the attribution tests above are the gate
    root = _seed(tmp_path / "pkg", "a = 1  # TODO(alice): x\nb = 2  # XXX: y\n")
    counts: dict[str, int] = {kw: 0 for kw in _MARKERS}
    total = 0
    for _, _, kw, _ in scan_todo_markers(root):
        counts[kw] += 1
        total += 1
    assert total == 2
    sys.stderr.write(f"\n[test_marker_count_summary_on_the_seeded_tree] {total} marker(s): " + ", ".join(f"{k}={v}" for k, v in counts.items() if v) + "\n")
