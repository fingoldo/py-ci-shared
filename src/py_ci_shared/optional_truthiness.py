"""Shared check: an optional parameter tested for truth rather than for absence.

A parameter annotated ``int | None`` (or ``float | None``, ``str | None``, ``Optional[int]``)
has THREE states its author cares about: absent, zero-ish, and set. Writing ``if limit:``
collapses the first two, and the collapse is silent -- the code reads as though it handles
the default, and it does, by treating a deliberate 0 as "not given".

Two real instances, both from glossum:

* ``mwe_importer.import_file(limit: int | None)`` guarded its read with ``if limit and
  lines_read >= limit``. ``--limit 0`` therefore skipped the guard and read a multi-GB dump
  to EOF -- the opposite of what the caller asked for (2026-09-05 wave, 01-F4).
* ``word_selector`` scored difficulty with ``if sense.frequency_rank:``, so rank 0 -- the
  single most frequent word in the corpus, a legitimate value -- was treated as "unknown"
  and given the neutral 0.5 instead of the near-zero it had earned (2026-08-02 wave).

The check is deliberately limited to NUMERIC and string optionals. A ``list | None`` or a
``dict | None`` tested for truth is usually intentional ("empty or missing, same thing"),
and reporting those would drown the signal.

Usage::

    from py_ci_shared.optional_truthiness import assert_optionals_test_for_none

    def test_no_optional_is_tested_for_truth():
        assert_optionals_test_for_none(files=sorted(PKG.rglob("*.py")), repo_root=REPO)
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import SourceError, parse_file, relative_posix

# Types where 0 is a value a caller can legitimately mean, distinct from absence.
#
# Numbers only, and the members are matched EXACTLY. `str | None` was included at first and
# produced 159 findings on one repo, nearly all of them `if lang:` -- an empty language code
# is not a meaningful value, so collapsing it with absence is correct there. Substring
# matching also mis-read `dict[str, int] | None` as an optional int. Both were noise around
# a signal worth keeping sharp.
_MEANINGFUL_FALSY = frozenset({"int", "float", "Decimal"})

_FuncLike = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda]
_LINE = re.compile(r"^(?P<path>.*?):\d+: ")


def _union_members(annotation: ast.AST) -> list[str]:
    """The parts of ``A | B | None`` / ``Optional[A]`` / ``Union[A, None]``, as source text. ``Annotated[T, ...]`` is ``T``."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _union_members(annotation.left) + _union_members(annotation.right)
    if isinstance(annotation, ast.Subscript):
        base = ast.unparse(annotation.value).split(".")[-1]
        inner = annotation.slice
        if base == "Annotated":
            first = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
            return _union_members(first)
        if base in ("Optional", "Union"):
            members = [m for e in inner.elts for m in _union_members(e)] if isinstance(inner, ast.Tuple) else _union_members(inner)
            # Optional[X] means X | None: the None is implicit and has to be added, or the
            # whole spelling reads as non-optional.
            return [*members, "None"] if base == "Optional" else members
        return [ast.unparse(annotation)]  # dict[str, int] stays one opaque member
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return ["None"]
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # A string annotation ("int | None"): parse it rather than matching substrings.
        try:
            return _union_members(ast.parse(annotation.value, mode="eval").body)
        except SyntaxError:
            return [annotation.value]
    return [ast.unparse(annotation)]


def _optional_of_meaningful_falsy(annotation: ast.AST | None) -> bool:
    """True for ``int | None``, ``Optional[float]``, ``Union[Decimal, None]``, ``Annotated[int | None, ...]``."""
    if annotation is None:
        return False
    members = [m.strip().split(".")[-1] for m in _union_members(annotation)]
    return "None" in members and any(m in _MEANINGFUL_FALSY for m in members)


def _all_params(fn: _FuncLike) -> "list[ast.arg]":
    args = fn.args
    extra = [a for a in (args.vararg, args.kwarg) if a is not None]
    return list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + extra


def _optional_params(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> set[str]:
    return {a.arg for a in _all_params(fn) if _optional_of_meaningful_falsy(a.annotation)}


def _own_nodes(fn: _FuncLike) -> Iterator[ast.AST]:
    """Nodes of *fn*'s body, not descending into nested functions, lambdas or classes."""
    scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack: list[ast.AST] = [n for n in (fn.body if isinstance(fn.body, list) else [fn.body]) if not isinstance(n, scoped)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, scoped))


def _nested(fn: ast.AST) -> Iterator[_FuncLike]:
    """Functions and lambdas defined directly inside *fn* (through any non-function nesting, classes included)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
            continue
        stack.extend(ast.iter_child_nodes(node))


def _truth_tested(expr: ast.AST) -> Iterator[ast.AST]:
    """Operands *expr* reads for truth: itself, through ``not`` and ``and``/``or``."""
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        yield from _truth_tested(expr.operand)
    elif isinstance(expr, ast.BoolOp):
        for value in expr.values:
            yield from _truth_tested(value)
    else:
        yield expr


def _tests_in(node: ast.AST) -> "list[ast.AST]":
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return [node.test]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    if isinstance(node, ast.comprehension):
        return list(node.ifs)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return [node.operand]
    return []


def _message(rel: str, line: int, name: str) -> str:
    return (
        f"{rel}:{line}: `{name}` is an optional number tested for "
        f"TRUTH; 0 is a value a caller can mean, and this reads it as absent. Use "
        f"`{name} is not None`."
    )


def _scan_function(fn: _FuncLike, inherited: "frozenset[str]", rel: str, out: "list[str]") -> None:
    own = {a.arg for a in _all_params(fn)}
    optional = (set(inherited) - own) | (_optional_params(fn) if not isinstance(fn, ast.Lambda) else set())
    seen: set[int] = set()
    if optional:
        for node in _own_nodes(fn):
            for tested in _tests_in(node):
                for expr in _truth_tested(tested):
                    if isinstance(expr, ast.Name) and expr.id in optional and id(expr) not in seen:
                        seen.add(id(expr))
                        out.append(_message(rel, expr.lineno, expr.id))
    for inner in _nested(fn):
        _scan_function(inner, frozenset(optional), rel, out)


def find_truthiness_tests(path: Path, *, repo_root: Optional[Path] = None) -> list[str]:
    """Report ``if param:`` / ``not param`` / ``param and ...`` (in ``if``, ``while``, ``assert``, a conditional
    expression or a comprehension filter) where *param* is an optional number.

    Paths are relative to *repo_root* when given (the file name otherwise, as before). A file that cannot be read
    or parsed is reported as ``<path>:<line>: unparsable: ...`` rather than skipped. Identical findings are all kept.
    """
    rel = relative_posix(path, repo_root) if repo_root is not None else path.name
    try:
        tree = parse_file(path)
    except SourceError as exc:
        return [f"{rel}:{exc.line or 1}: {exc.kind}: {exc.message}"]
    out: list[str] = []
    for node in tree.body:
        for fn in [node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else _nested(node):
            _scan_function(fn, frozenset(), rel, out)
    return sorted(out)


def finding_key(finding: str) -> str:
    """A finding without its line number, so an edit above it does not invalidate a baseline entry."""
    return _LINE.sub(lambda m: f"{m.group('path')}: ", finding, count=1)


def assert_optionals_test_for_none(
    *,
    files: Iterable[Path],
    repo_root: Path,
    baseline: Iterable[str] = (),
    min_subjects: int = 1,
) -> None:
    """Fail on a new optional-number parameter tested for truth, on a baseline entry nothing matches, or on an
    unparsable file.

    ``baseline`` takes already-reviewed findings verbatim (with or without the line number: entries are compared
    without it, as a multiset, with paths relative to *repo_root*), so the check can be adopted on a tree that has
    some, without either failing every commit or hiding the backlog.
    """
    import pytest

    paths = list(files)
    findings = [p for path in paths for p in find_truthiness_tests(path, repo_root=repo_root)]
    unparsed = [f for f in findings if ": unparsable: " in f or ": unreadable: " in f]
    parsed = len(paths) - len({f.split(":", 1)[0] for f in unparsed})
    if parsed < min_subjects:
        pytest.fail(f"only {parsed} file(s) scanned -- expected at least {min_subjects}; the scan lost its subject")

    budget = Counter(finding_key(b) for b in baseline)
    problems: list[str] = []
    new: list[str] = []
    for finding in findings:
        if finding in unparsed:
            continue
        key = finding_key(finding)
        if budget[key] > 0:
            budget[key] -= 1
        else:
            new.append(finding)
    stale = sorted(budget.elements())
    if unparsed:
        problems.append(f"{len(unparsed)} file(s) could not be parsed, so they were not checked:\n  " + "\n  ".join(unparsed))
    if new:
        problems.append(f"{len(new)} optional(s) tested for truth rather than for None:\n  " + "\n  ".join(new))
    if stale:
        problems.append(f"{len(stale)} baseline entr(ies) no longer found -- remove them:\n  " + "\n  ".join(stale))
    if problems:
        pytest.fail("\n".join(problems))
