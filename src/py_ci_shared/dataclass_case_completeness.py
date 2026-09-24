"""Shared check: every dataclass of a given kind has a test case, or an exemption with a reason.

WHERE THIS CAME FROM
--------------------
glossum's verdict round-trip test drove every optional field of a verdict dataclass through its real parser.
It first covered 2 of 21 verdict classes, and none of the three that week's defects were in; a field declared
on a dataclass that no parser path filled was exactly how answers the model gave were discarded. What made
the test worth having was the companion check: a NEW verdict class fails until it gets a case.

WHAT THIS CHECKS
----------------
:func:`find_dataclasses` lists the classes decorated with ``@dataclass`` (or ``@dataclasses.dataclass``, with
or without arguments) in the given files whose names match a pattern, by AST, without importing anything.
:func:`assert_every_dataclass_has_a_case` fails for such a class that is neither covered nor exempt, for an
exemption without a reason, and for a covered or exempt name that no longer exists, so a list cannot rot.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence

from ._core import ImportAliases, ScanResult, scan_python

__all__ = ["assert_every_dataclass_has_a_case", "find_dataclass_sites", "find_dataclasses"]


def _is_dataclass_decorator(node: ast.expr, aliases: ImportAliases | None = None) -> bool:
    """``@dataclass``, ``@dataclasses.dataclass`` (with or without arguments), and any alias of either
    (``from dataclasses import dataclass as dc``, ``import dataclasses as dcs``, ``pydantic.dataclasses.dataclass``)."""
    target = node.func if isinstance(node, ast.Call) else node
    qualified = (aliases or ImportAliases()).qualified_name(target)
    if qualified is None:
        return False
    return qualified == "dataclass" or qualified.endswith(".dataclass")


def _scan(paths: Iterable[Path]) -> ScanResult:
    return scan_python([Path(p) for p in paths])


def _sites(scan: ScanResult, name_pattern: str) -> list[tuple[str, Path, int]]:
    rx = re.compile(name_pattern)
    out: list[tuple[str, Path, int]] = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        out.extend(
            (node.name, parsed.path, node.lineno)
            for node in ast.walk(parsed.tree)
            if isinstance(node, ast.ClassDef) and rx.fullmatch(node.name) and any(_is_dataclass_decorator(d, aliases) for d in node.decorator_list)
        )
    return out


def find_dataclass_sites(paths: Iterable[Path], name_pattern: str = r".*") -> list[tuple[str, str]]:
    """``[(class name, "path:line")]`` for every matching ``@dataclass`` class, same-named classes kept apart."""
    return [(name, f"{path.as_posix()}:{line}") for name, path, line in _sites(_scan(paths), name_pattern)]


def find_dataclasses(paths: Iterable[Path], name_pattern: str = r".*") -> dict[str, str]:
    """``{class name: "path:line"}`` for every ``@dataclass`` class whose name fully matches ``name_pattern``.

    Two classes with one name keep the first site here; :func:`find_dataclass_sites` lists both, and
    :func:`assert_every_dataclass_has_a_case` requires each to be named by path.
    """
    out: dict[str, str] = {}
    for name, where in find_dataclass_sites(paths, name_pattern):
        out.setdefault(name, where)
    return out


def _names(entry: str, path: Path, name: str) -> bool:
    """Whether a covered/exempt *entry* names the class *name* declared in *path*: ``Name`` or ``<path suffix>::Name``."""
    if "::" not in entry:
        return entry == name
    where, _, cls = entry.rpartition("::")
    posix = path.as_posix()
    return cls == name and (posix == where or posix.endswith("/" + where.lstrip("/")))


def assert_every_dataclass_has_a_case(
    paths: Sequence[Path],
    covered: Iterable[str],
    *,
    name_pattern: str = r".*",
    exempt: Mapping[str, str] = {},
    min_files: int = 1,
) -> None:
    """Fail unless every matching dataclass is in ``covered`` or in ``exempt`` with a reason.

    An entry is the class name, or ``path::Name`` (any trailing part of the path) when two files declare a class of
    that name: a bare name is then ambiguous and fails. Also fails when fewer than *min_files* files parsed and when
    any file could not be parsed.
    """
    scan = _scan(paths)
    scan.min_files = min_files
    messages: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        messages.append(str(exc))
    if scan.unparsed:
        messages.append("these files could not be parsed, so their dataclasses were not checked: " + "; ".join(p.render() for p in scan.unparsed))
    sites = _sites(scan, name_pattern)
    per_name: dict[str, int] = {}
    for name, _, _ in sites:
        per_name[name] = per_name.get(name, 0) + 1
    entries = list(covered) + list(exempt)
    missing: dict[str, str] = {}
    ambiguous: set[str] = set()
    used: set[str] = set()
    for name, path, line in sites:
        hits = [e for e in entries if _names(e, path, name)]
        qualified = [e for e in hits if "::" in e]
        if per_name[name] > 1 and not qualified:
            if hits:
                ambiguous.add(name)
            else:
                missing[f"{path.as_posix()}::{name}"] = f"{path.as_posix()}:{line}"
            used.update(hits)
            continue
        if not hits:
            missing[name] = f"{path.as_posix()}:{line}"
        used.update(hits)
    blank = [name for name, reason in exempt.items() if not reason.strip()]
    stale = sorted(set(entries) - used)
    if missing:
        messages.append(f"these dataclasses have no case; add one, or an exemption with the reason: {missing}")
    if ambiguous:
        messages.append(f"these names are declared by more than one dataclass; name each as 'path::Name': {sorted(ambiguous)}")
    if blank:
        messages.append(f"every exemption needs a reason: {blank}")
    if stale:
        messages.append(f"these covered or exempt names match no dataclass any more; drop them: {stale}")
    if messages:
        raise AssertionError("\n".join(messages))
