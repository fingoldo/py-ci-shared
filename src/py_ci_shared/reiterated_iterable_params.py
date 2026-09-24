"""A parameter typed ``Iterable`` (or ``Iterator``/``Generator``) consumed more than once.

``Iterable[...]`` invites a caller to pass a generator, and a generator is exhausted by its first pass: the second
``for`` over it sees nothing and raises nothing. The shipped instance: an insert helper iterated its ``columns:
Iterable`` parameter three times (validate, placeholders, column list); a real generator produced an empty
``INSERT INTO t () VALUES ()``, a broad ``except`` reported "0 rows inserted", and the failure was indistinguishable
from an empty input. ``list``/``Sequence``/``tuple`` annotations guarantee re-iterability and are not checked.

A parameter is flagged when some path through the function body consumes it twice or more without rebinding it
(``p = list(p)`` anywhere in the function exempts it). Consumption is: ``for x in p``, a comprehension over ``p``,
``list/tuple/set/frozenset/sorted/sum/min/max/any/all/dict/enumerate/zip/map/filter/iter(p)``, ``sep.join(p)``,
``x.extend(p)``/``x.update(p)``, ``x in p``, ``*p`` and ``yield from p``. Paths are followed through ``if``/``else``
(the larger branch counts, not both), ``try`` and early ``return``/``raise`` (a branch that returns does not add to
the code after it); a consumption inside a loop body, or in a nested comprehension's inner ``for``, runs once per
outer item and counts as repeated.

Usage::

    from py_ci_shared.reiterated_iterable_params import assert_no_reiterated_iterable_params

    def test_iterable_params_are_consumed_once():
        assert_no_reiterated_iterable_params(REPO / "src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE", "REFRESH_FLAG", "ONE_SHOT_TYPES", "find_reiterated_iterable_params", "assert_no_reiterated_iterable_params"]

RULE = "reiterated-iterable-param"
REFRESH_FLAG = "--refresh-reiterated-iterables-baseline"
MARKER = "reiterable-ok"
ONE_SHOT_TYPES = frozenset({"Iterable", "Iterator", "Generator", "AsyncIterable", "AsyncIterator", "AsyncGenerator"})
_CONSUMERS = frozenset({"list", "tuple", "set", "frozenset", "sorted", "sum", "min", "max", "any", "all", "dict", "enumerate", "zip", "map", "filter", "iter"})
_METHOD_CONSUMERS = frozenset({"join", "extend", "update", "union", "intersection", "difference", "issubset", "issuperset", "fromkeys"})
_UNREACHABLE = -(10**6)
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _is_one_shot(ann: Optional[ast.expr], aliases: ImportAliases) -> bool:
    """Does the annotation accept a one-shot iterable (``Iterable[...]``, ``Optional[Iterator]``, ``"Iterable[str]"``...)?"""
    if ann is None:
        return False
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            return _is_one_shot(ast.parse(ann.value, mode="eval").body, aliases)
        except SyntaxError:
            return False
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _is_one_shot(ann.left, aliases) or _is_one_shot(ann.right, aliases)
    if isinstance(ann, ast.Subscript):
        head = (aliases.qualified_name(ann.value) or "").rsplit(".", 1)[-1]
        if head in ("Optional", "Union"):
            inner = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            return any(_is_one_shot(e, aliases) for e in inner)
        return _is_one_shot(ann.value, aliases)
    if isinstance(ann, (ast.Name, ast.Attribute)):
        return (aliases.qualified_name(ann) or "").rsplit(".", 1)[-1] in ONE_SHOT_TYPES
    return False


def _is_p(node: Optional[ast.AST], name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _expr_count(node: Optional[ast.AST], name: str) -> int:
    """Consumptions of *name* inside one expression (or simple statement), not descending into nested scopes."""
    if node is None:
        return 0
    total = 0
    stack: list[ast.AST] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        total += _node_count(n, name)
        stack.extend(ast.iter_child_nodes(n))
    return total


def _node_count(n: ast.AST, name: str) -> int:
    """Consumptions of *name* by the node *n* itself (its children are counted separately)."""
    if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return sum(1 if i == 0 else 2 for i, gen in enumerate(n.generators) if _is_p(gen.iter, name))
    if isinstance(n, ast.Call):
        func = n.func
        args = [*n.args, *(k.value for k in n.keywords)]
        consumer = (isinstance(func, ast.Name) and func.id in _CONSUMERS) or (isinstance(func, ast.Attribute) and func.attr in _METHOD_CONSUMERS)
        return 1 if consumer and any(_is_p(a, name) for a in args) else 0
    if isinstance(n, ast.Compare):
        return sum(1 for op, right in zip(n.ops, n.comparators) if isinstance(op, (ast.In, ast.NotIn)) and _is_p(right, name))
    if isinstance(n, (ast.Starred, ast.YieldFrom)) and _is_p(n.value, name):
        return 1
    return 0


def _best(*values: int) -> int:
    return max(values)


class _Counter:
    """Max consumptions of one name along any path: ``walk`` returns ``(falls through, ended by return/raise)``."""

    def __init__(self, name: str) -> None:
        self.name = name

    def walk(self, body: Sequence[ast.stmt]) -> tuple[int, int]:
        fall, term = 0, _UNREACHABLE
        for stmt in body:
            if fall == _UNREACHABLE:
                break
            f, t = self.stmt(stmt)
            term = _best(term, fall + t if t != _UNREACHABLE else _UNREACHABLE)
            fall = fall + f if f != _UNREACHABLE else _UNREACHABLE
        return fall, term

    def _branches(self, *bodies: Sequence[ast.stmt]) -> tuple[int, int]:
        results = [self.walk(b) for b in bodies]
        return _best(*(f for f, _ in results)), _best(*(t for _, t in results))

    def stmt(self, s: ast.stmt) -> tuple[int, int]:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return 0, _UNREACHABLE
        if isinstance(s, (ast.Return, ast.Raise)):
            return _UNREACHABLE, _expr_count(s, self.name)
        if isinstance(s, ast.If):
            c = _expr_count(s.test, self.name)
            f, t = self._branches(s.body, s.orelse)
            return (c + f if f != _UNREACHABLE else _UNREACHABLE), (c + t if t != _UNREACHABLE else _UNREACHABLE)
        if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            c = _expr_count(s.iter if not isinstance(s, ast.While) else s.test, self.name)
            if not isinstance(s, ast.While) and _is_p(s.iter, self.name):
                c += 1
            bf, bt = self.walk(s.body)
            of, ot = self.walk(s.orelse)
            repeated = 2 * max(bf, 0) if max(bf, 0) else 0
            return c + repeated + max(of, 0), _best(
                bt + c + max(bf, 0) if bt != _UNREACHABLE else _UNREACHABLE, ot + c + repeated if ot != _UNREACHABLE else _UNREACHABLE
            )
        if isinstance(s, (ast.With, ast.AsyncWith)):
            c = sum(_expr_count(i.context_expr, self.name) for i in s.items)
            f, t = self.walk(s.body)
            return (c + f if f != _UNREACHABLE else _UNREACHABLE), (c + t if t != _UNREACHABLE else _UNREACHABLE)
        if isinstance(s, ast.Try) or type(s).__name__ == "TryStar":
            body_f, body_t = self.walk(getattr(s, "body"))
            handler_f, handler_t = self._branches(getattr(s, "orelse"), *[h.body for h in getattr(s, "handlers")])
            final_f, _ = self.walk(getattr(s, "finalbody"))
            fall = max(body_f, 0) + max(handler_f, 0) + max(final_f, 0)
            return fall, _best(body_t, handler_t + max(body_f, 0) if handler_t != _UNREACHABLE else _UNREACHABLE)
        if type(s).__name__ == "Match":
            c = _expr_count(getattr(s, "subject"), self.name)
            f, t = self._branches(*[case.body for case in getattr(s, "cases")])
            return (c + f if f != _UNREACHABLE else _UNREACHABLE), (c + t if t != _UNREACHABLE else _UNREACHABLE)
        return _expr_count(s, self.name), _UNREACHABLE


def _rebound(func: _FunctionNode, name: str) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Assign) and any(_is_p(t, name) or (isinstance(t, ast.Tuple) and any(_is_p(e, name) for e in t.elts)) for t in n.targets):
            return True
        if isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)) and _is_p(n.target, name):
            return True
        if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)) and _is_p(n.target, name):
            return True
    return False


def _file_findings(parsed: ParsedFile) -> list[Finding]:
    aliases = ImportAliases.from_tree(parsed.tree)
    lines = parsed.source.splitlines()
    names = enclosing_functions(parsed.tree)
    out: list[Finding] = []
    for func in ast.walk(parsed.tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = func.args
        for param in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if not _is_one_shot(param.annotation, aliases) or _rebound(func, param.arg) or line_has_marker(lines, func.lineno, MARKER):
                continue
            fall, term = _Counter(param.arg).walk(func.body)
            count = max(fall, term)
            if count >= 2:
                owner = names.get(id(func), "<module>")
                qual = func.name if owner == "<module>" else f"{owner}.{func.name}"
                shown = ast.unparse(param.annotation) if param.annotation is not None else "?"
                out.append(Finding(parsed.rel, func.lineno, RULE, f"{qual}({param.arg}: {shown}) is consumed {count} times on one path"))
    return out


def _collect(root: Union[str, Path], *, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_reiterated_iterable_params(
    root: Union[str, Path], *, skip_dir_names: Iterable[str] = (), include_tests: bool = False, use_git: Optional[bool] = None
) -> list[Finding]:
    """Every ``Iterable``-typed parameter consumed twice on one path under *root*, plus one ``unparsed-file`` finding
    per unparsable file."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_reiterated_iterable_params(
    root: Union[str, Path],
    *,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a re-consumed one-shot parameter (new against *baseline_path* when given), on fewer than *min_files*
    parsed files, and on any unparsable file. Refresh with ``--refresh-reiterated-iterables-baseline``."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="reiterated-iterable-params",
        flag=REFRESH_FLAG,
        guidance="materialise first (`p = list(p)`), or narrow the annotation to Sequence/list/tuple",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
