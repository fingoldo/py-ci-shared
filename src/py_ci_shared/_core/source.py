"""Reading and parsing source files: one decoder, one parser, one cache.

Two defects were repeated across ~40 gates (audit 2026-09-24 MP-1, MP-2, TZ-4, ARCH-17):

* ``path.read_text(encoding="utf-8")`` keeps a UTF-8 BOM as U+FEFF, ``ast.parse`` rejects it, and the gate's
  ``except SyntaxError: continue`` then dropped the file. Every file saved by a Windows editor with a BOM passed
  every AST gate vacuously.
* Any other decode or syntax error was skipped the same way, so a file using syntax newer than the running
  interpreter was never checked at all.

Here a ``.py`` file is decoded exactly as the interpreter would decode it (``tokenize.detect_encoding``: BOM,
PEP 263 cookie, UTF-8 default), any other file as ``utf-8-sig``, and every failure is a typed exception carrying
the path and line. Whether an unparsable file fails the gate is then a decision made once, in ``scan``.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import threading
import time
import tokenize
from pathlib import Path
from typing import Optional, Union

from .errors import SourceParseError, SourceReadError

PathLike = Union[str, "os.PathLike[str]"]

_PYTHON_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})

# resolved path -> (content digest, source, tree). One entry per path: a changed file replaces its entry
# rather than accumulating stale ones, so the cache is bounded by the corpus size. Keyed on the bytes, not on
# mtime and size: a same-size rewrite inside one mtime tick would otherwise serve the old tree.
_CACHE: dict[str, tuple[bytes, str, ast.Module]] = {}
_LOCK = threading.Lock()

# path as given -> (stat signature, time it was read, cache key, digest). A stable file is recognised by its stat alone, without reading, hashing or resolving it again:
# a gate suite asks for each of a few thousand files tens of times. The signature is trusted only when the file's mtime is older than the moment it was read by more than
# ``_RACY_NS`` (the rule git applies to its index): a file rewritten inside the timestamp granularity of the read would otherwise keep its old signature, and falls to the
# digest comparison below instead.
_FAST: dict[str, tuple[tuple[int, int, int], int, str, bytes]] = {}
_RACY_NS = 3_000_000_000
_RESOLVED: dict[str, Path] = {}

# module text -> tree, for gates handed a source STRING rather than a path (the same module is judged by many gates in one run). Only module-sized text is kept: a comment
# body or a one-line annotation is parsed afresh, so the table is bounded by the corpus and never fills with snippets.
_TEXT_TREES: dict[str, ast.Module] = {}
_TEXT_MIN_CHARS = 1000


def resolve_cached(path: PathLike) -> Path:
    """``Path(path).resolve()`` remembered for the process (cleared with the parse cache): resolving one path costs a few system calls, and gates resolve each file dozens of times."""
    spelled = os.fspath(path)
    key = spelled if os.path.isabs(spelled) else f"{os.getcwd()}|{spelled}"  # a relative path resolves against the working directory
    with _LOCK:
        hit = _RESOLVED.get(key)
        if hit is None:
            hit = _RESOLVED[key] = Path(os.path.abspath(spelled)).resolve()  # anchor first: Windows resolve() can leave a relative, missing path relative
    return hit


# path as given -> (stat signature, time it was read, bytes). One process-wide copy of a file's bytes for every reader (parse, text gates, content hashing): a gate suite used to open each file ~14 times,
# and an open is the slow part on NTFS with a virus scanner (audit 2026-11-04 35-F3). Trusted under the same racy-timestamp rule as ``_FAST``; files over ``_BYTES_MAX_FILE`` are never kept and
# the whole table is dropped when it passes ``_BYTES_MAX_TOTAL``, so memory is bounded whatever the corpus.
_BYTES: dict[str, tuple[tuple[int, int, int], int, bytes]] = {}
_BYTES_TOTAL = 0
_BYTES_MAX_FILE = 4_000_000
_BYTES_MAX_TOTAL = 400_000_000


def read_bytes_cached(path: PathLike) -> bytes:
    """``Path(path).read_bytes()`` served from a process-wide cache while the file's stat signature (mtime, size, inode) is unchanged and old enough to trust.

    Raises ``OSError`` exactly as ``read_bytes`` does. A file modified in the last few seconds is read from disk every time, so an edit-then-rescan never sees stale bytes.
    """
    spath = os.fspath(path)
    try:
        st = os.stat(spath)
    except OSError:  # best-effort: the retry below re-raises the real OSError, nothing is swallowed
        return Path(spath).read_bytes()
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    cached = _cached_bytes(spath, sig, st.st_mtime_ns)
    if cached is not None:
        return cached
    raw = Path(spath).read_bytes()
    read_ns = time.time_ns()
    if len(raw) <= _BYTES_MAX_FILE:
        _store_bytes(spath, sig, read_ns, raw)
    return raw


def _cached_bytes(spath: str, sig: tuple[int, int, int], mtime_ns: int) -> Optional[bytes]:
    with _LOCK:
        hit = _BYTES.get(spath)
        if hit is not None and hit[0] == sig and hit[1] - mtime_ns > _RACY_NS:
            return hit[2]
    return None


def _store_bytes(spath: str, sig: tuple[int, int, int], read_ns: int, raw: bytes) -> None:
    global _BYTES_TOTAL
    with _LOCK:
        old = _BYTES.pop(spath, None)
        if old is not None:
            _BYTES_TOTAL -= len(old[2])
        if _BYTES_TOTAL + len(raw) > _BYTES_MAX_TOTAL:
            _BYTES.clear()
            _BYTES_TOTAL = 0
        _BYTES[spath] = (sig, read_ns, raw)
        _BYTES_TOTAL += len(raw)


def _decode(path: Path, raw: bytes) -> str:
    if path.suffix.lower() in _PYTHON_SUFFIXES:
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        except SyntaxError as exc:  # an unknown or conflicting coding cookie, or (3.12+) a NUL byte
            if "null bytes" in (exc.msg or ""):
                raise SourceParseError(path, exc.msg, exc.lineno) from exc
            raise SourceReadError(path, f"bad encoding declaration: {exc.msg}", exc.lineno) from exc
    else:
        encoding = "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        line = raw[: getattr(exc, "start", 0)].count(b"\n") + 1 if isinstance(exc, UnicodeDecodeError) else None
        raise SourceReadError(path, f"not valid {encoding}: {exc}", line) from exc
    # detect_encoding reports "utf-8-sig" only when the BOM is present; a BOM after a cookie is still stripped.
    return text[1:] if text.startswith("\ufeff") else text


def read_source(path: PathLike) -> str:
    """The text of *path*, decoded the way Python would decode it, BOM stripped.

    Raises :class:`SourceReadError` for a missing/unreadable file or undecodable bytes; never returns a lossy
    decode (no ``errors="ignore"``/``"replace"``), because a gate reading mojibake reports on text that is not
    in the file.
    """
    p = Path(path)
    try:
        raw = read_bytes_cached(p)
    except OSError as exc:
        raise SourceReadError(p, f"cannot read: {exc.strerror or exc}") from exc
    return _decode(p, raw)


#: Bytes probed for a NUL when deciding whether a file is binary; the same window git uses.
_BINARY_PROBE_BYTES = 8000


def read_text_if_not_binary(path: PathLike) -> Optional[str]:
    """The decoded text of *path*, or ``None`` when its first bytes hold a NUL (a binary file: an image, an archive, a profile).

    Binary files are not text and not unreadable text, so a gate reading a text corpus skips them instead of reporting them.
    Undecodable text still raises :class:`SourceReadError`. Shares the bytes cache with :func:`read_source`: one open per file.
    """
    p = Path(path)
    try:
        raw = read_bytes_cached(p)
    except OSError as exc:
        raise SourceReadError(p, f"cannot read: {exc.strerror or exc}") from exc
    if b"\x00" in raw[:_BINARY_PROBE_BYTES]:
        return None
    return _decode(p, raw)


def _parse_text(path: Path, text: str) -> ast.Module:
    try:
        return ast.parse(text, filename=str(path), type_comments=False)
    except SyntaxError as exc:
        raise SourceParseError(path, exc.msg or "invalid syntax", exc.lineno) from exc
    except ValueError as exc:  # "source code string cannot contain null bytes" before 3.12
        raise SourceParseError(path, str(exc)) from exc


def _fast_lookup(spath: str, sig: "Optional[tuple[int, int, int]]", mtime_ns: int) -> "Optional[tuple[str, ast.Module]]":
    """The cached ``(source, tree)`` of a file whose stat signature is unchanged and old enough to trust (see ``_RACY_NS``), else ``None``."""
    if sig is None:
        return None
    with _LOCK:
        fast = _FAST.get(spath)
        if fast is None or fast[0] != sig or fast[1] - mtime_ns <= _RACY_NS:
            return None
        hit = _CACHE.get(fast[2])
        if hit is not None and hit[0] == fast[3]:
            return hit[1], hit[2]
    return None


def _fast_store(spath: str, sig: "Optional[tuple[int, int, int]]", read_ns: int, key: str, digest: bytes) -> None:
    """Remember the stat signature a file was read under."""
    if sig is not None:
        with _LOCK:
            _FAST[spath] = (sig, read_ns, key, digest)


def parse_source(path: PathLike) -> tuple[str, ast.Module]:
    """``(source, tree)`` for *path*, from the in-process cache when the file is unchanged.

    The cache key is (resolved path, digest of the file's bytes): a gate suite that runs forty gates over one
    package parses it once, and an edited file is re-parsed however quickly it was rewritten. The returned tree is
    SHARED between callers; treat it as read-only.
    """
    p = Path(path)
    spath = os.fspath(path)
    try:
        st = os.stat(spath)
    except OSError:
        st = None
    sig = (st.st_mtime_ns, st.st_size, st.st_ino) if st is not None else None
    stable = _fast_lookup(spath, sig, st.st_mtime_ns if st is not None else 0)
    if stable is not None:
        return stable
    try:
        raw = read_bytes_cached(spath)
        read_ns = time.time_ns()
        key = str(resolve_cached(spath))
    except OSError as exc:
        raise SourceReadError(p, f"cannot read: {exc.strerror or exc}") from exc
    digest = hashlib.blake2b(raw, digest_size=16).digest()
    _fast_store(spath, sig, read_ns, key, digest)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == digest:
            return hit[1], hit[2]
        text = _decode(p, raw)
        tree = _parse_text(p, text)
        _CACHE[key] = (digest, text, tree)
    return text, tree


def parse_text(text: str, filename: str = "<unknown>") -> ast.Module:
    """The AST of module *text*, shared between callers while the text is the same (module-sized text only); the tree is READ-ONLY.

    The string counterpart of :func:`parse_source` for a gate that is handed source text. It raises ``SyntaxError`` / ``ValueError`` exactly as ``ast.parse`` does, so a caller keeps
    its own decision about an unparsable text. *filename* is only for the error message and the nodes' ``filename``; a text that was already parsed returns the first tree.
    """
    if len(text) < _TEXT_MIN_CHARS:
        return ast.parse(text, filename=filename)
    with _LOCK:
        hit = _TEXT_TREES.get(text)
    if hit is not None:
        return hit
    tree = ast.parse(text, filename=filename)
    with _LOCK:
        return _TEXT_TREES.setdefault(text, tree)


def parse_file(path: PathLike) -> ast.Module:
    """The AST of *path* (cached; see :func:`parse_source`). Raises ``SourceReadError``/``SourceParseError``."""
    return parse_source(path)[1]


def clear_parse_cache() -> None:
    """Drop every cached tree and what was derived from it (frees memory in long-lived processes; correctness never depends on it)."""
    from . import corpus, node_index

    global _BYTES_TOTAL
    with _LOCK:
        _BYTES.clear()
        _BYTES_TOTAL = 0
        _CACHE.clear()
        _FAST.clear()
        _RESOLVED.clear()
        _TEXT_TREES.clear()
    node_index.clear()
    corpus.clear_listing_cache()


def parse_cache_size() -> int:
    with _LOCK:
        return len(_CACHE)


def indent_width(line: str) -> int:
    """Leading whitespace width of *line*, counting spaces and tabs as one each."""
    return len(line) - len(line.lstrip())
