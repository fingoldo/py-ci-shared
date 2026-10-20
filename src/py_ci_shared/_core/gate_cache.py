"""A gate's PASSED result, remembered on disk until something in the repository changes.

A gate reads files of the repository it was pointed at, and a re-run on an unchanged tree gives the same verdict. The cache key is therefore everything
that decides a gate's verdict: the gate's module and entry, its keyword arguments and budget, this package's version, and the digest of every file under
the repository root (the ``.git`` directory and cache directories excluded). Any change to any of them is a miss, so a stale PASSED cannot be replayed.

Only PASSED is stored. A FAILED or ERROR result is always recomputed, and so is any run with ``refresh`` (it rewrites a baseline on purpose), and a gate
whose kwargs ask for the network (its answer can change without a file changing). ``PY_CI_SHARED_NO_CACHE=1`` disables the cache.

The cache lives in the temporary directory, one file per repository (keyed by its resolved path), never in the repository itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NO_CACHE_ENV = "PY_CI_SHARED_NO_CACHE"
_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv", "node_modules", ".py_ci_cache"})


def enabled() -> bool:
    return os.environ.get(NO_CACHE_ENV, "").strip() not in ("1", "true", "yes")


_TREE_MEMO: dict[str, tuple[tuple, str]] = {}


def _stat_signature(root: Path) -> tuple:
    """(relative path, size, mtime) of every file under *root*, skipping the caches and the git directory. Cheap: no file is read."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                entries.append((path.relative_to(root).as_posix(), -1, -1))
                continue
            entries.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    return tuple(entries)


def tree_digest(root: Path) -> str:
    """A digest of every file under *root* (relative path and content), skipping the caches and the git directory.

    The content is read only when the stat signature (paths, sizes, modification times) differs from the one this process last digested.
    Two gates in one run do not each read the whole tree. A file rewritten with the same size inside one timestamp tick is the one case
    this misses, the same racy case git's index has; the next change of its size or time corrects it.
    """
    key = root.resolve().as_posix()
    signature = _stat_signature(root)
    memo = _TREE_MEMO.get(key)
    if memo is not None and memo[0] == signature:
        return memo[1]
    digest = hashlib.sha256()
    for rel, _size, _mtime in signature:
        path = root / rel
        try:
            content = path.read_bytes()
        except OSError:
            content = b"<unreadable>"
        digest.update(rel.encode("utf-8") + b"\x00" + hashlib.sha256(content).digest())
    value = digest.hexdigest()
    _TREE_MEMO[key] = (signature, value)
    return value


def _kwargs_ask_for_network(kwargs: dict[str, Any]) -> bool:
    return any(key in kwargs and bool(kwargs[key]) for key in ("network", "online", "fetch_remote"))


def _canonical(value: Any) -> Any:
    """A JSON-ready form that does not depend on the process: sets are sorted (a set's iteration order changes with the hash seed), paths are text."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def key_for(repo_root: Path, module: str, entry: Optional[str], kwargs: dict[str, Any], budget: Optional[float], tree: str) -> Optional[str]:
    """The cache key, or None when this run must not be cached (network-dependent)."""
    if _kwargs_ask_for_network(kwargs):
        return None
    from .. import __version__ as package_version  # lazy: the package imports this module

    payload = json.dumps(
        {
            "module": module,
            "entry": entry,
            "kwargs": _canonical(kwargs),
            "budget": budget,
            "package": package_version,
            "tree": tree,
            "root": repo_root.as_posix(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _store_dir() -> Path:
    """The per-user cache directory. Under the home directory, not the shared temp directory: another local user must not be
    able to pre-create or plant an entry there (the predictable-temp-path defect class)."""
    path = Path.home() / ".cache" / "py_ci_shared" / "gate_cache"
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def _store_path(repo_root: Path) -> Path:
    tag = hashlib.sha256(repo_root.resolve().as_posix().encode("utf-8")).hexdigest()[:16]
    return _store_dir() / f"{tag}.json"


def lookup(repo_root: Path, key: str) -> bool:
    """Whether *key* is recorded as PASSED for this repository."""
    try:
        data = json.loads(_store_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("gate cache not readable, treated as empty: %s", type(exc).__name__)
        return False
    return key in data.get("passed", [])


def record(repo_root: Path, key: str) -> None:
    """Remember *key* as PASSED. A cache that cannot be written costs only the next run's time."""
    path = _store_path(repo_root)
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        passed = [k for k in data.get("passed", []) if isinstance(k, str)][-500:]
        if key not in passed:
            passed.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"passed": passed}), encoding="utf-8")
    except OSError as exc:
        logger.debug("gate cache not written: %s", type(exc).__name__)
