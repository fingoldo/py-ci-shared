"""The trailing ``# vX.Y.Z`` comment of a py-ci-shared pin line; split out of ``adoption_matrix`` to keep it under the size budget."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ._core import CoreError, read_source

__all__ = ["COMMENT_TAG", "comment_mismatches", "with_comments"]

COMMENT_TAG = re.compile(r"^(v\d+\.\d+\.\d+)(?![\w.-])")
_TRAILING_COMMENT = re.compile(r"\s#\s*(\S.*?)\s*$")


def with_comments(pins: list[Any], root: Path) -> list[Any]:
    """Each pin with the trailing ``# ...`` comment of its source line (``uv.lock`` and line-less pins have none)."""
    cache: dict[str, list[str]] = {}
    out = []
    for pin in pins:
        if pin.line <= 0 or pin.location == "uv.lock":
            out.append(pin)
            continue
        if pin.path not in cache:
            try:
                cache[pin.path] = read_source(root / pin.path).splitlines()
            except CoreError:
                cache[pin.path] = []
        lines = cache[pin.path]
        m = _TRAILING_COMMENT.search(lines[pin.line - 1]) if pin.line <= len(lines) else None
        out.append(replace(pin, comment=m.group(1)) if m else pin)
    return out


def comment_mismatches(pins: list[Any], resolved: dict[str, Optional[str]], tag_commits: dict[str, Optional[str]], known_tags: bool) -> list[tuple[Any, str]]:
    """``(pin, why)`` for each fixed pin whose ``# vX.Y.Z`` comment names a tag whose commit (in *tag_commits*) is not the pinned commit."""
    out: list[tuple[Any, str]] = []
    for p in pins:
        tag, full = p.comment_tag, resolved.get(p.ref) if p.ref is not None else None
        if tag is None or p.moving or not full or tag not in tag_commits:
            continue
        commit = tag_commits[tag]
        if commit is None and known_tags:
            out.append((p, f"its comment names {tag}, which is not a release tag here"))
        elif commit is not None and commit != full:
            out.append((p, f"its comment says {tag} (commit {commit[:7]}) but the pin is {full[:7]}: write `master {full[:7]}` or pin the tag commit"))
    return out
