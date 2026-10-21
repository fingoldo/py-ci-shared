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


_TREE_DIGESTS: dict[str, str] = {}


def _file_stat(entry: os.DirEntry) -> tuple[int, int]:
    """(size, modification time) of *entry*, or (-1, -1) when it cannot be stat'ed (a broken link, a file that vanished)."""
    try:
        st = entry.stat()
    except OSError:  # best-effort: a file that vanished or is a broken link gets a fixed marker, so the digest still changes with it
        return -1, -1
    return st.st_size, st.st_mtime_ns


def _stat_entries(path: str, rel_dir: str, out: list) -> None:
    """Append the (relative path, size, mtime) of every file under *path*, in os.walk order (sorted, top-down, no linked directories)."""
    try:
        entries = list(os.scandir(path))
    except OSError:  # best-effort: a directory that cannot be listed contributes no files, the same as os.walk
        return
    # os.walk classifies with is_dir() (following links): a link to a directory is a directory, never a file.
    dirs = sorted((e for e in entries if e.is_dir() and e.name not in _SKIP_DIRS), key=lambda e: e.name)
    files = sorted((e for e in entries if not e.is_dir()), key=lambda e: e.name)
    for e in files:
        size, mtime = _file_stat(e)
        out.append((rel_dir + e.name, size, mtime))
    for e in dirs:
        if not e.is_symlink():  # os.walk does not descend into linked directories
            _stat_entries(e.path, rel_dir + e.name + "/", out)


def _stat_signature(root: Path) -> tuple:
    """(relative path, size, mtime) of every file under *root*, skipping the caches and the git directory. Cheap: no file is read.

    Walks with os.scandir, whose entries carry the size and time from the directory listing on Windows, so a file costs no extra
    system call; the entries and their order are exactly those of the os.walk version this replaced, so existing cache keys stay valid.
    """
    out: list = []
    _stat_entries(os.fspath(root), "", out)
    return tuple(out)


def tree_digest(root: Path) -> str:
    """A digest of every file under *root* (relative path and content), skipping the caches and the git directory.

    Computed once per process and root: every gate of one run shares it, so the tree is walked and read once, not once per gate.
    A file changed later in the same process is not seen until :func:`forget_tree_digests` is called, which is the cost of
    sharing; code that rewrites files under a repository root in the same process must call it.
    """
    key = root.resolve().as_posix()
    known = _TREE_DIGESTS.get(key)
    if known is not None:
        return known
    digest = hashlib.sha256()
    for rel, _size, _mtime in _stat_signature(root):
        path = root / rel
        try:
            content = path.read_bytes()
        except OSError:
            content = b"<unreadable>"
        digest.update(rel.encode("utf-8") + b"\x00" + hashlib.sha256(content).digest())
    value = digest.hexdigest()
    _TREE_DIGESTS[key] = value
    return value


def forget_tree_digests() -> None:
    """Drop the tree digests this process remembers, so the next gate walks and reads the tree again."""
    _TREE_DIGESTS.clear()


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
