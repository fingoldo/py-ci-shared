"""On-disk memo of full code-audit runs, shared by every gate in every process of a test session.

Several gates scan the same production tree, and under pytest-xdist each worker used to repeat the whole scan. The key covers
everything a result depends on: the scanned root and its check selection, the excluded directories, the CONTENT of every file under
the root (scanners read ``.sql``, tests and docs too, not only ``.py``), and the content of the whole ``py_ci_shared`` package (the
scanners use ``_core`` and other gate modules, not only ``code_audit/``). A hit is therefore a result the current tree and scanner code
would reproduce. The signature is a hash of contents, not of modification times, so a fresh checkout (every mtime new) still hits
an entry written by an earlier run of the same files, which is what lets a CI job restore this directory between runs. The cache is an optimisation: any read or write failure falls back to a
fresh scan.

The directory lives under the user's home, not the shared temp directory, so another local user cannot pre-create or plant an
entry there (the predictable-temp-path class of defect).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from collections.abc import Iterable

from .._core.baseline import replace_file
from ._base import Finding

logger = logging.getLogger(__name__)

#: The whole package, not only ``code_audit``: scanners import ``_core`` and other gate modules, and a change there changes findings.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent
#: A file untouched for this long cannot still be mid-write, so its content hash may be remembered for the process (same idea as ``_core.source``).
_SETTLED_NS = 3_000_000_000
_DIGESTS: dict[tuple[str, int, int], str] = {}


def _file_digest(full: Path, stat: os.stat_result) -> Optional[str]:
    """SHA-256 of one file's bytes; remembered per (path, mtime, size) once the file is old enough not to be mid-write. None when unreadable."""
    key = (str(full), stat.st_mtime_ns, stat.st_size)
    cached = _DIGESTS.get(key)
    if cached is not None:
        return cached
    try:
        digest = hashlib.sha256(full.read_bytes()).hexdigest()
    except OSError:
        return None
    if time.time_ns() - stat.st_mtime_ns > _SETTLED_NS:
        _DIGESTS[key] = digest
    return digest


def _signature(root: Path, exclude_dirs: frozenset[str]) -> str:
    """One digest over (relative path, content hash) of every regular file under *root* outside *exclude_dirs*, in sorted order."""
    entries: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                stat = full.stat()
            except OSError:
                continue
            digest = _file_digest(full, stat)
            if digest is not None:
                entries.append((full.relative_to(root).as_posix(), digest))
    entries.sort()
    return hashlib.sha256(repr(entries).encode("utf-8")).hexdigest()


def cache_key(root: Path, selected: Iterable[str], exclude_dirs: frozenset[str], consumer_roots: Iterable[Path] = ()) -> tuple[str, tuple]:
    """A hex digest for the run, and the content signature it was computed from (stored alongside, compared on read).

    ``consumer_roots`` are extra trees a scanner may read (see ``scan_possibly_dead_import``): their file
    contents join the signature, so editing a test that consumes a re-export invalidates the cached run.
    """
    consumers = tuple(sorted(str(c.resolve()) for c in consumer_roots))
    signature = (
        _signature(root, exclude_dirs),
        _signature(_PACKAGE_DIR, frozenset({"__pycache__"})),
        tuple((c, _signature(Path(c), exclude_dirs)) for c in consumers),
    )
    description = (str(root.resolve()), tuple(sorted(selected)), tuple(sorted(exclude_dirs)), consumers)
    digest = hashlib.sha256(repr(description).encode("utf-8")).hexdigest()
    return digest, signature


def _cache_dir() -> Path:
    """The per-user memo directory, created on first use and private to its owner on POSIX."""
    path = Path.home() / ".cache" / "pyutilz-code-audit"
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def load(digest: str, signature: tuple) -> Optional[tuple[list[Finding], dict[str, str]]]:
    """The stored ``(findings, failed)`` for *digest* when its signature still matches, else None."""
    try:
        with open(_cache_dir() / f"{digest}.json", "r", encoding="utf-8") as handle:
            document = json.load(handle)
        if document["signature"] != _as_json(signature):
            return None
        findings = [Finding(**entry) for entry in document["findings"]]
        failed = dict(document["failed"])
    except (OSError, ValueError, KeyError, TypeError):  # best-effort: an unreadable or foreign entry only costs a fresh scan
        return None
    return findings, failed


def store(digest: str, signature: tuple, findings: list[Finding], failed: dict[str, str]) -> None:
    """Write the result for *digest* as JSON; a failure to write only costs the next run its scan."""
    document = {
        "signature": _as_json(signature),
        "findings": [{name: getattr(finding, name) for name in _FINDING_FIELDS} for finding in findings],
        "failed": dict(failed),
    }
    try:
        target = _cache_dir() / f"{digest}.json"
        tmp = target.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        replace_file(tmp, target)
    except OSError as exc:
        logger.warning("code-audit scan cache not written (%s); the next run scans again", exc)


_FINDING_FIELDS = ("check", "severity", "file", "line", "snippet", "detail")


def _as_json(value: object) -> object:
    """Tuples become lists, so a signature read back from JSON compares equal to the one computed in memory."""
    if isinstance(value, (tuple, list)):
        return [_as_json(item) for item in value]
    return value
