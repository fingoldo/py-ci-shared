"""``getattr(cfg, "field", <literal>)`` whose literal disagrees with the field's own default.

A config object is read defensively so a duck-typed stand-in still works, and the literal in the ``getattr`` becomes that
stand-in's effective default. When it disagrees with the schema's, the same setting means two things depending on which
object the caller passed: the shipped instance read ``reject_on_alpha_drift`` as False at the gate while the config
declared True, so a duck-typed config silently kept drifting specs the real one dropped.

The check resolves each site's field against the schema classes the caller supplies, compares the literal with the
field's declared default, and reports the mismatches. A field that no schema declares is ignored: the receiver is named
by convention, and the alternative would be a false report for every unrelated ``getattr(obj, "x", 0)``.

Usage from a repository's meta tests::

    from py_ci_shared.config_getattr_default_parity import assert_getattr_defaults_match_schema

    def test_getattr_defaults_match_the_config():
        assert_getattr_defaults_match_schema(files=SRC_FILES, repo_root=REPO_ROOT, schema_classes=[MyConfig], allowed={})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._core import ScanResult, UnparsedFilesError, scan_python

if TYPE_CHECKING:  # pydantic is a test-time dependency here, as in this package's other schema checks
    from pydantic import BaseModel

__all__ = [
    "GetattrDefault",
    "schema_field_defaults",
    "find_getattr_default_mismatches",
    "assert_getattr_defaults_match_schema",
    "DEFAULT_RECEIVER_NAMES",
    "DEFAULT_RECEIVER_SUFFIXES",
]

DEFAULT_RECEIVER_NAMES: frozenset[str] = frozenset({"config", "cfg", "self.config", "self.cfg", "self._config"})
DEFAULT_RECEIVER_SUFFIXES: tuple[str, ...] = ("_config", "_cfg", ".config", ".cfg")


class GetattrDefault:
    """One ``getattr`` site: where it is, which field, what it falls back to, and what the schema declares."""

    __slots__ = ("declared", "field", "lineno", "literal", "path")

    def __init__(self, path: str, lineno: int, field: str, literal: Any, declared: Any) -> None:
        self.path = path
        self.lineno = lineno
        self.field = field
        self.literal = literal
        self.declared = declared

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.field}={self.literal!r} vs the config's {self.declared!r}"


def _receiver_name(node: ast.expr) -> str | None:
    """``cfg`` / ``self.config`` / ``x_config`` spelled back out, or None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _is_config_receiver(name: str | None, receiver_names: frozenset[str], receiver_suffixes: Sequence[str]) -> bool:
    """True for a receiver that reads as one of the caller's config objects, by exact name or by suffix.

    A repository with several config classes passes the names and suffixes of the one it is checking, so a field name two
    configs share (``enabled``, ``random_state``) is not read off the wrong schema.
    """
    if name is None:
        return False
    return name in receiver_names or (bool(receiver_suffixes) and name.endswith(tuple(receiver_suffixes)))


def schema_field_defaults(schema_classes: Sequence[type["BaseModel"]]) -> dict[str, Any]:
    """``field name -> declared default`` across the given pydantic models; a field two models disagree on is dropped.

    Dropping the disagreements is what keeps a shared name such as ``random_state`` from being reported against whichever
    model happened to be listed first, and a required field (one with no default at all) is skipped.
    """
    seen: dict[str, Any] = {}
    conflicting: set[str] = set()
    for cls in schema_classes:
        for name, field in cls.model_fields.items():
            default = field.default
            if field.is_required() or type(default).__name__ == "PydanticUndefinedType":
                continue  # no declared default (required, or supplied by a default_factory) for a call site to contradict
            if name in seen and seen[name] != default:
                conflicting.add(name)
            seen[name] = default
    return {k: v for k, v in seen.items() if k not in conflicting}


def find_getattr_default_mismatches(
    files: Iterable[Path],
    repo_root: Path,
    schema_classes: Sequence[type["BaseModel"]],
    receiver_names: frozenset[str] = DEFAULT_RECEIVER_NAMES,
    receiver_suffixes: Sequence[str] = DEFAULT_RECEIVER_SUFFIXES,
) -> list[GetattrDefault]:
    """Every ``getattr(<config>, "<field>", <literal>)`` whose literal differs from the field's declared default.

    A file that cannot be read or parsed raises ``_core.UnparsedFilesError`` instead of being skipped.
    """
    scan = _scan(files, repo_root)
    if scan.unparsed:
        raise UnparsedFilesError(_unparsed_message(scan))
    return _mismatches(scan, schema_classes, receiver_names, receiver_suffixes)


def _scan(files: Iterable[Path], repo_root: Path) -> ScanResult:
    return scan_python([Path(p) for p in files], root=Path(repo_root), min_files=0)


def _unparsed_message(scan: ScanResult) -> str:
    return "could not read or parse, so their getattr fallbacks were not checked:\n  " + "\n  ".join(p.render() for p in scan.unparsed)


def _mismatches(
    scan: ScanResult, schema_classes: Sequence[type["BaseModel"]], receiver_names: frozenset[str], receiver_suffixes: Sequence[str]
) -> list[GetattrDefault]:
    declared = schema_field_defaults(schema_classes)
    out: list[GetattrDefault] = []
    for parsed in scan:
        tree, rel = parsed.tree, parsed.rel
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr" and len(node.args) == 3):
                continue
            receiver, name_node, default_node = node.args
            if not _is_config_receiver(_receiver_name(receiver), receiver_names, receiver_suffixes):
                continue
            if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
                continue
            field = name_node.value
            if field not in declared:
                continue
            try:
                literal = ast.literal_eval(default_node)
            except (ValueError, SyntaxError, TypeError):
                continue  # a computed fallback is not a competing default
            if literal != declared[field] or type(literal) is not type(declared[field]):
                out.append(GetattrDefault(rel, node.lineno, field, literal, declared[field]))
    return sorted(out, key=lambda g: (g.path, g.lineno, g.field))


def assert_getattr_defaults_match_schema(
    files: Iterable[Path],
    repo_root: Path,
    schema_classes: Sequence[type["BaseModel"]],
    allowed: Mapping[str, str] | None = None,
    min_files: int = 1,
    receiver_names: frozenset[str] = DEFAULT_RECEIVER_NAMES,
    receiver_suffixes: Sequence[str] = DEFAULT_RECEIVER_SUFFIXES,
) -> None:
    """Fail on a ``getattr`` fallback that contradicts the config's own default.

    ``allowed`` maps a field name to the reason its sites may differ (a deliberately stricter fallback for a duck-typed
    caller, say); an empty reason is rejected, and an entry with nothing left to excuse must be removed.
    """
    scan = _scan(files, repo_root)
    if scan.parsed_count < min_files:
        raise AssertionError(f"parsed only {scan.parsed_count} files (< {min_files}); the scan lost its subject")
    allowed = dict(allowed or {})
    empty = sorted(k for k, v in allowed.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowed fields need a reason: {empty}")
    found = _mismatches(scan, schema_classes, receiver_names, receiver_suffixes)
    bad = [g for g in found if g.field not in allowed]
    stale = sorted(set(allowed) - {g.field for g in found})
    msgs = []
    if scan.unparsed:
        msgs.append(_unparsed_message(scan))
    if bad:
        msgs.append(
            "getattr fallbacks that contradict the config's own default (a duck-typed config silently gets the "
            "other behaviour): " + "; ".join(map(repr, bad))
        )
    if stale:
        msgs.append(f"allowed fields whose sites now agree with the config: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
