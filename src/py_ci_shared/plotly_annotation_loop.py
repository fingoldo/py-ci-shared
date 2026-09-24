"""plotly ``fig.add_annotation``/``add_shape`` called once per item in a loop: O(n^2) in the number of items.

``fig.add_annotation(...)`` does ``layout.annotations = layout.annotations + (new,)``, which re-validates the WHOLE
growing tuple on every call, so a per-item loop is quadratic. ``add_shape`` and the ``add_vline``/``add_hline``/
``add_vrect``/``add_hrect`` helpers built on it behave the same. Measured: 697 ``add_annotation`` calls cost 389 s,
43% of an 895 s profile (a heatmap's per-cell text and a network diagram's per-edge arrows); batching them into one
``fig.layout.annotations = fig.layout.annotations + tuple(batch)`` was 534x faster at n=400 with identical output. A
``_MAX_*`` cap on the loop treats the symptom; batching removes the cause. matplotlib's ``ax.text``/``ax.annotate`` are
O(1) appends and are not affected.

Flagged, in files that import plotly: one of those calls inside a ``for``/``while`` body or a comprehension (in the
same function). A loop over a small literal (a literal list/tuple/set, a conditional choosing between such literals,
or ``range(k)`` with ``k <= small_loop``, default 10) is not flagged: a handful of reference lines is not a hot path.
A line carrying ``# plotly-loop-ok`` is an explicit opt-out.

Usage::

    from py_ci_shared.plotly_annotation_loop import assert_no_plotly_annotation_loops

    def test_no_per_item_plotly_annotations():
        assert_no_plotly_annotation_loops(REPO / "src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE", "REFRESH_FLAG", "SLOW_METHODS", "find_plotly_annotation_loops", "assert_no_plotly_annotation_loops"]

RULE = "plotly-annotation-in-loop"
REFRESH_FLAG = "--refresh-plotly-annotation-loop-baseline"
MARKER = "plotly-loop-ok"
SLOW_METHODS = frozenset({"add_annotation", "add_shape", "add_vline", "add_hline", "add_vrect", "add_hrect"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _imports_plotly(aliases: ImportAliases) -> bool:
    return any(target == "plotly" or target.startswith("plotly.") for target in aliases.mapping.values())


def _small_literal(iterable: ast.expr, limit: int) -> bool:
    if isinstance(iterable, (ast.List, ast.Tuple, ast.Set)):
        return len(iterable.elts) <= limit
    if isinstance(iterable, ast.IfExp):
        return _small_literal(iterable.body, limit) and _small_literal(iterable.orelse, limit)
    if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name) and iterable.func.id in ("range", "enumerate", "zip"):
        if iterable.func.id == "range":
            bounds = [a.value for a in iterable.args if isinstance(a, ast.Constant) and isinstance(a.value, int)]
            return len(bounds) == len(iterable.args) and bool(bounds) and max(bounds) <= limit
        return bool(iterable.args) and all(_small_literal(a, limit) for a in iterable.args)
    return False


def _own(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _looped_calls(tree: ast.AST, limit: int) -> Iterator[ast.Call]:
    """Slow plotly calls that run once per item of a loop or comprehension in their own function."""
    seen: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if _small_literal(node.iter, limit):
                continue
            region: list[ast.AST] = list(node.body)
        elif isinstance(node, ast.While):
            region = list(node.body)
        elif isinstance(node, _COMPREHENSIONS):
            if all(_small_literal(g.iter, limit) for g in node.generators):
                continue
            region = [node.elt] if not isinstance(node, ast.DictComp) else [node.key, node.value]
        else:
            continue
        for sub in _own(region):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in SLOW_METHODS and id(sub) not in seen:
                seen.add(id(sub))
                yield sub


def _file_findings(parsed: ParsedFile, limit: int) -> list[Finding]:
    if not _imports_plotly(ImportAliases.from_tree(parsed.tree)):
        return []
    lines = parsed.source.splitlines()
    functions = enclosing_functions(parsed.tree)
    out: list[Finding] = []
    for call in _looped_calls(parsed.tree, limit):
        if line_has_marker(lines, call.lineno, MARKER):
            continue
        method = call.func.attr if isinstance(call.func, ast.Attribute) else "?"
        where = functions.get(id(call), "<module>")
        receiver = ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else "?"
        out.append(
            Finding(parsed.rel, call.lineno, RULE, f"{where}: {receiver}.{method}() runs once per loop item (quadratic); batch into one layout assignment")
        )
    return out


def _collect(
    root: Union[str, Path], *, small_loop: int, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed, small_loop)]
    return sorted(findings, key=lambda f: (f.path, f.line)), scan


def find_plotly_annotation_loops(
    root: Union[str, Path], *, small_loop: int = 10, skip_dir_names: Iterable[str] = (), include_tests: bool = False, use_git: Optional[bool] = None
) -> list[Finding]:
    """Every per-item plotly annotation/shape call under *root*, plus one ``unparsed-file`` finding per unparsable file."""
    findings, scan = _collect(root, small_loop=small_loop, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_plotly_annotation_loops(
    root: Union[str, Path],
    *,
    small_loop: int = 10,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a per-item plotly annotation/shape call (new against *baseline_path* when given), on fewer than
    *min_files* parsed files, and on any unparsable file. Refresh with ``--refresh-plotly-annotation-loop-baseline``."""
    findings, scan = _collect(root, small_loop=small_loop, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    report(
        findings,
        gate="plotly-annotation-loop",
        flag=REFRESH_FLAG,
        guidance="build go.layout.Annotation/Shape objects in the loop and assign them once: fig.layout.annotations += tuple(batch)",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
