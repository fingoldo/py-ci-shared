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

__all__ = [
    "exception_handlers",
    "function_parameters",
    "imported_top_level",
    "parse",
]


def parse(path: Path | str) -> ast.Module:
    """The parse tree of *path*, read as UTF-8."""
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def function_parameters(path: Path | str, function: str) -> set[str]:
    """Every parameter name of *function*, including keyword-only, `*args` and `**kwargs`.

    Raises `AssertionError` if the function is absent: a renamed target must fail the gate rather
    than quietly satisfy `"x" not in set()`.
    """
    for node in ast.walk(parse(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            a = node.args
            names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
            names |= {p.arg for p in (a.vararg, a.kwarg) if p is not None}
            return names
    raise AssertionError(f"{Path(path).name} has no function named {function!r} -- the gate needs re-pointing, not deleting")


def imported_top_level(path: Path | str) -> set[str]:
    """Top-level package names this module imports, at MODULE scope only.

    Module scope is the distinction that matters for a whole class of findings: an import inside a
    function does not run when the module is imported, so "does importing this pull in X" is answered
    by this and not by a search for the name.
    """
    names: set[str] = set()
    for node in parse(path).body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def exception_handlers(path: Path | str, exception: str) -> int:
    """How many `except` clauses in *path* catch *exception* by that name.

    Counts the clause, not the word: the comment recording why a handler was removed mentions the
    exception, and a grep counts that comment.
    """
    found = 0
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        if any(isinstance(c, ast.Name) and c.id == exception for c in caught):
            found += 1
    return found
