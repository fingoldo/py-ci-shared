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

__all__ = ["assert_every_dataclass_has_a_case", "find_dataclasses"]


def _is_dataclass_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == "dataclass"
    return isinstance(target, ast.Attribute) and target.attr == "dataclass"


def find_dataclasses(paths: Iterable[Path], name_pattern: str = r".*") -> dict[str, str]:
    """``{class name: "path:line"}`` for every ``@dataclass`` class whose name fully matches ``name_pattern``."""
    rx = re.compile(name_pattern)
    out: dict[str, str] = {}
    for path in sorted(paths):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and rx.fullmatch(node.name) and any(_is_dataclass_decorator(d) for d in node.decorator_list):
                out[node.name] = f"{path.as_posix()}:{node.lineno}"
    return out


def assert_every_dataclass_has_a_case(
    paths: Sequence[Path],
    covered: Iterable[str],
    *,
    name_pattern: str = r".*",
    exempt: Mapping[str, str] = {},
) -> None:
    """Fail unless every matching dataclass is in ``covered`` or in ``exempt`` with a reason."""
    declared = find_dataclasses(paths, name_pattern)
    covered_set, exempt_set = set(covered), set(exempt)
    missing = {name: where for name, where in declared.items() if name not in covered_set | exempt_set}
    blank = [name for name, reason in exempt.items() if not reason.strip()]
    stale = sorted((covered_set | exempt_set) - set(declared))
    messages = []
    if missing:
        messages.append(f"these dataclasses have no case; add one, or an exemption with the reason: {missing}")
    if blank:
        messages.append(f"every exemption needs a reason: {blank}")
    if stale:
        messages.append(f"these covered or exempt names match no dataclass any more; drop them: {stale}")
    if messages:
        raise AssertionError("\n".join(messages))
