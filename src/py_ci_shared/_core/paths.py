"""Case-exact path existence: ``Path.exists()`` is case-INSENSITIVE on Windows and macOS, so a link, comment path or entry
point spelled ``REAL.md`` for a file named ``real.md`` is green on the owner's machine and dead on the Linux runner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

__all__ = ["exists_exact_case"]


def exists_exact_case(path: Union[str, "os.PathLike[str]"], root: Optional[Union[str, "os.PathLike[str]"]] = None) -> bool:
    """True when *path* exists AND every component below *root* is spelled exactly as the directory listing spells it.

    *root* (default: *path*'s parent, i.e. only the last component is compared) is taken as given and not compared, so a
    checkout whose own directory name differs in case from how the caller spelled it does not matter."""
    target = Path(path)
    if not target.exists():
        return False
    stop = Path(root) if root is not None else target.parent
    try:
        parts = target.relative_to(stop).parts
    except ValueError:
        parts = (target.name,)
        stop = target.parent
    current = stop
    for part in parts:
        if part in (".", ""):
            continue
        if part == "..":
            current = current / part
            continue
        try:
            names = os.listdir(current if str(current) else ".")
        except OSError:  # best-effort: an unlistable dir only skips a case-spelling refinement, exists() already said yes
            return True  # cannot list (permissions): exists() already said yes, do not invent a finding
        if part not in names:
            return False
        current = current / part
    return True
