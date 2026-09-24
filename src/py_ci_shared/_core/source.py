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
import tokenize
from pathlib import Path
from typing import Union

from .errors import SourceParseError, SourceReadError

PathLike = Union[str, "os.PathLike[str]"]

_PYTHON_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})

# resolved path -> (content digest, source, tree). One entry per path: a changed file replaces its entry
# rather than accumulating stale ones, so the cache is bounded by the corpus size. Keyed on the bytes, not on
# mtime and size: a same-size rewrite inside one mtime tick would otherwise serve the old tree.
_CACHE: dict[str, tuple[bytes, str, ast.Module]] = {}
_LOCK = threading.Lock()


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
        raw = p.read_bytes()
    except OSError as exc:
        raise SourceReadError(p, f"cannot read: {exc.strerror or exc}") from exc
    return _decode(p, raw)


def _parse_text(path: Path, text: str) -> ast.Module:
    try:
        return ast.parse(text, filename=str(path), type_comments=False)
    except SyntaxError as exc:
        raise SourceParseError(path, exc.msg or "invalid syntax", exc.lineno) from exc
    except ValueError as exc:  # "source code string cannot contain null bytes" before 3.12
        raise SourceParseError(path, str(exc)) from exc


def parse_source(path: PathLike) -> tuple[str, ast.Module]:
    """``(source, tree)`` for *path*, from the in-process cache when the file is unchanged.

    The cache key is (resolved path, digest of the file's bytes): a gate suite that runs forty gates over one
    package parses it once, and an edited file is re-parsed however quickly it was rewritten. The returned tree is SHARED between callers; treat it as read-only.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
        key = str(p.resolve())
    except OSError as exc:
        raise SourceReadError(p, f"cannot read: {exc.strerror or exc}") from exc
    digest = hashlib.blake2b(raw, digest_size=16).digest()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None and hit[0] == digest:
        return hit[1], hit[2]
    text = _decode(p, raw)
    tree = _parse_text(p, text)
    with _LOCK:
        _CACHE[key] = (digest, text, tree)
    return text, tree


def parse_file(path: PathLike) -> ast.Module:
    """The AST of *path* (cached; see :func:`parse_source`). Raises ``SourceReadError``/``SourceParseError``."""
    return parse_source(path)[1]


def clear_parse_cache() -> None:
    """Drop every cached tree (frees memory in long-lived processes; correctness never depends on it)."""
    with _LOCK:
        _CACHE.clear()


def parse_cache_size() -> int:
    with _LOCK:
        return len(_CACHE)
