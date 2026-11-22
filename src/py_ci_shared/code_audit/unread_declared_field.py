"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from .._core.unparse import unparse_stable
from ._base import Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, walk_tree, is_line_exempt

__all__ = ["scan_unread_declared_fields"]

logger = logging.getLogger(__name__)

# --- a dataclass field that nothing reads -----------------------------------------------------------
#
# A spec field declared with a default and then never read is a promise the code does not keep: a caller sets
# ``CeilingTarget(tolerance=0.001)`` and the shortfall caveat still fires at a hard-coded 0.01 (mlframe audit 04-F16).
# The reference test is corpus-wide and textual: a field counts as read when ``.<name>`` appears anywhere in the scanned
# tree, or ``getattr(x, "<name>")``. Writes such as ``obj.name = v`` also count, so a field that is only ever assigned is
# a false negative, never a false positive.
#
# A tree that reads its dataclasses generically (``asdict``, ``astuple``, ``fields``, ``__dict__``) reads every field
# through that path, so nothing is flagged there: the scanner cannot see which fields that path consumes.

_GENERIC_READ_RE = re.compile(r"\b(?:asdict|astuple|fields)\s*\(|__dict__")
_ATTR_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\b")
_GETATTR_RE = re.compile(r"""getattr\s*\([^,]+,\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")
_CHECK = "unread_declared_field"


def _is_dataclass(cls: ast.ClassDef) -> bool:
    """True when the class carries a ``@dataclass`` or ``@dataclasses.dataclass`` decorator, with or without arguments."""
    for dec in cls.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


def _declared_fields(cls: ast.ClassDef) -> list[tuple[str, ast.AnnAssign]]:
    """``(name, declaration)`` for the annotated, public, instance fields of a dataclass body (``ClassVar`` and private names excluded)."""
    fields: list[tuple[str, ast.AnnAssign]] = []
    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        if stmt.target.id.startswith("_") or "ClassVar" in unparse_stable(stmt.annotation):
            continue
        fields.append((stmt.target.id, stmt))
    return fields


def _corpus_reads(py_files: list[Path]) -> tuple[set[str], bool]:
    """``(attribute names read anywhere in the tree, whether the tree reads dataclasses generically)``."""
    read: set[str] = set()
    generic = False
    for py in py_files:
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("unread_declared_field: %s could not be read and was not scanned: %s", py, exc)
            continue
        read.update(_ATTR_RE.findall(text))
        read.update(_GETATTR_RE.findall(text))
        generic = generic or _GENERIC_READ_RE.search(text) is not None
    return read, generic


def scan_unread_declared_fields(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find public dataclass fields whose name is never read as ``.<name>`` anywhere in the scanned tree.

    A field read only through a generic path (``asdict`` / ``astuple`` / ``fields`` / ``__dict__`` anywhere in the tree) is
    not reported at all. Suppress one field with ``# audit-exempt: unread_declared_field -- <reason of 19+ chars>``.
    Severity P2: the declared knob silently does nothing, which is a documentation-versus-behaviour gap, not a crash.
    """
    findings: list[Finding] = []
    py_files = list(_iter_py_files(root, exclude_dirs))
    read, generic = _corpus_reads(py_files)
    if generic:
        return findings

    for py in py_files:
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        for node in walk_tree(tree):
            if not isinstance(node, ast.ClassDef) or not _is_dataclass(node):
                continue
            for name, field in _declared_fields(node):
                if name in read or is_line_exempt(src_lines, field.lineno, _CHECK):
                    continue
                findings.append(
                    Finding(
                        check=_CHECK,
                        severity="P2",
                        file=rel,
                        line=field.lineno,
                        snippet=_line_text(src_lines, field.lineno),
                        detail=(
                            f"dataclass `{node.name}` declares field `{name}`, but no `.{name}` is read anywhere in the scanned tree. "
                            f"Either the knob does nothing (wire it into the code it is meant to govern, or remove it), or the read "
                            f"goes through a path this scanner cannot see."
                        ),
                    )
                )
    return findings
