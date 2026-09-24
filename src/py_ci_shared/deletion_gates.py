"""Assert that something an audit deliberately REMOVED has not come back.

A mutation sweep cannot check a deletion. Removing code that is already unreachable changes no
behaviour, so reintroducing "the defect" proves nothing -- which is why deletions get recorded as
NOT MUTABLE and then go unwatched. The absence is still checkable, in the other direction.

WHY THIS IS A SHARED MODULE. It is not hypothetical bookkeeping. On 2026-09-07 a repository's master
went red for six tests with `TypeError: scraper_bootstrap() got an unexpected keyword argument
'sql_file'`: one session deleted the parameter after checking it had no callers, and another,
branched before that, added tests asserting its behaviour. Neither change touched the other's lines,
so the merge was conflict-free and the result was broken. A gate on the deletion fails in whichever
session commits second, at commit time.

The same shape covers a WON'T FIX whose reason is a mechanical fact -- a finding ruled out because an
import is function-local, say, is silently reopened by an ordinary tidy-up that hoists it.

EVERYTHING HERE PARSES, NOTHING GREPS. A deletion is nearly always documented in a comment right
where it happened, so a substring search finds the explanation and reports it as the violation. That
mistake was made three separate times in one audit round, including by the script written to catch
it.

Usage, from a repository's own meta-test::

    from py_ci_shared.deletion_gates import function_parameters, imported_top_level, exception_handlers

    def test_the_parameter_stays_deleted():
        assert "sql_file" not in function_parameters(SRC / "_orchestration.py", "scraper_bootstrap")

Each helper raises rather than returning a falsy default when the thing it names is missing: a gate
whose target was renamed must fail loudly, not pass by finding nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Union
from collections.abc import Iterator

from ._core import parse_file

__all__ = [
    "exception_handlers",
    "function_parameters",
    "imported_top_level",
    "parse",
]

_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def parse(path: Path | str) -> ast.Module:
    """The parse tree of *path*, decoded as the interpreter would (BOM stripped, PEP 263 honoured).

    Raises ``py_ci_shared._core.SourceReadError``/``SourceParseError`` (both carry the path and line). The tree is
    shared with other gates through the parse cache; treat it as read-only.
    """
    return parse_file(path)


def _functions(tree: ast.Module) -> Iterator[tuple[str, _FunctionNode]]:
    def visit(node: ast.AST, prefix: str) -> Iterator[tuple[str, _FunctionNode]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                yield qual, child
                yield from visit(child, f"{qual}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, f"{prefix}{child.name}.")
            else:
                yield from visit(child, prefix)

    return visit(tree, "")


def function_parameters(path: Path | str, function: str) -> set[str]:
    """Every parameter name of *function*, including keyword-only, `*args` and `**kwargs`.

    *function* is a bare name (``run``) or a qualified one (``Runner.run``, ``outer.<locals>.inner``). A bare name
    that names functions with different qualified names in the file (a module ``run`` and a method ``A.run``)
    raises: the gate must say which one it watches. Several definitions under ONE qualified name (a property
    getter/setter, ``typing.overload`` stubs) answer with the union of their parameters.

    Raises `AssertionError` if the function is absent: a renamed target must fail the gate rather
    than quietly satisfy `"x" not in set()`.
    """
    candidates = [(qual, node) for qual, node in _functions(parse(path)) if (qual == function if "." in function else node.name == function)]
    quals = sorted({qual for qual, _ in candidates})
    if not candidates:
        raise AssertionError(f"{Path(path).name} has no function named {function!r} -- the gate needs re-pointing, not deleting")
    if len(quals) > 1:
        raise AssertionError(f"{Path(path).name}: {function!r} is ambiguous, it names {quals}; pass the qualified name")
    names: set[str] = set()
    for _, node in candidates:
        a = node.args
        names |= {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
        names |= {p.arg for p in (a.vararg, a.kwarg) if p is not None}
    return names


def _module_scope(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements that run when the module is imported: the body, and the bodies of module-level ``if``/``try``/
    ``with``/loops, but not function or class bodies."""
    stack = list(body)
    while stack:
        node = stack.pop(0)
        yield node
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
        elif isinstance(node, _TRY_TYPES):
            stack.extend(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))


def imported_top_level(path: Path | str) -> set[str]:
    """Top-level package names this module imports, at MODULE scope only.

    Module scope is the distinction that matters for a whole class of findings: an import inside a
    function does not run when the module is imported, so "does importing this pull in X" is answered
    by this and not by a search for the name. Imports under a module-level ``try``/``if`` run at import time and
    count. A relative import (``from .helpers import y``) names the module's own package, not a top-level
    package, and is not reported.
    """
    names: set[str] = set()
    for node in _module_scope(parse(path).body):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def exception_handlers(path: Path | str, exception: str) -> int:
    """How many `except` clauses in *path* catch *exception* by that name.

    Counts the clause, not the word: the comment recording why a handler was removed mentions the
    exception, and a grep counts that comment. *exception* may be bare (``Timeout`` also matches
    ``except requests.Timeout``) or dotted (``requests.Timeout`` matches only that spelling).
    """
    found = 0
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        dotted = [_dotted(c) for c in caught]
        if any(d == exception or ("." not in exception and d.rsplit(".", 1)[-1] == exception) for d in dotted if d):
            found += 1
    return found
