"""Advisory: polars comparisons whose null handling silently changes the answer.

In polars, ``==`` propagates nulls and ``min()``/``max()`` skip them. So a constant-column check written as
``s.min() == s.max()`` returns null, not True, for an all-null column (it is not detected as constant) and True for
``[1, null]`` (a column with a missing value reads as constant), and ``pl.col("a") == pl.col("b")`` drops every row where
either side is null instead of matching null to null. ``eq_missing``/``ne_missing`` treat null as a value.

Three rules, in files that import polars:

* ``polars-minmax-constant``: ``X.min() == X.max()`` (or ``!=``, either order) on the same receiver, when ``X`` is
  visibly polars: a chain starting at any polars callable (``pl.col``, ``pl.Series``, ``cs.numeric()``...), a
  ``.get_column(...)``/``.to_series()`` chain, or a name assigned once from one in the same function (numpy arrays
  and pandas columns skip NaN the same way, but are not polars and are left alone: a file importing polars also
  handles plenty of numpy);
* ``polars-compare-none``: ``pl.col(...) == None`` / ``!= None`` / ``.eq(None)``: always null, never True; the intent is
  ``is_null()``/``is_not_null()``;
* ``polars-null-propagating-eq``: ``==``/``!=``/``.eq()``/``.ne()`` between two ``pl.col(...)`` expressions, where a
  null on either side drops the row.

The first two are almost always bugs; the third is sometimes intended, which is why the gate is advisory by default:
``assert_polars_null_equality`` warns (``PolarsNullEqualityWarning``) instead of failing, unless ``advisory=False``. A
broken walk (no files, unparsable files) always fails. A line carrying ``# null-eq-ok`` is an opt-out.

Usage::

    from py_ci_shared.polars_null_equality import assert_polars_null_equality

    def test_polars_null_equality_advisory():
        assert_polars_null_equality(REPO / "src")
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import enclosing_functions, line_has_marker, report, scan_tree, skip_set

__all__ = [
    "RULE_MINMAX",
    "RULE_NONE",
    "RULE_COLUMNS",
    "REFRESH_FLAG",
    "PolarsNullEqualityWarning",
    "find_polars_null_equality",
    "assert_polars_null_equality",
]

RULE_MINMAX = "polars-minmax-constant"
RULE_NONE = "polars-compare-none"
RULE_COLUMNS = "polars-null-propagating-eq"
REFRESH_FLAG = "--refresh-polars-null-equality-baseline"
MARKER = "null-eq-ok"


class PolarsNullEqualityWarning(UserWarning):
    """A polars comparison whose null handling may change the answer."""


def _imports_polars(aliases: ImportAliases) -> bool:
    return any(target == "polars" or target.startswith("polars.") for target in aliases.mapping.values())


def _method_call(node: ast.AST, name: str) -> Optional[ast.expr]:
    """The receiver of ``receiver.name()`` (no arguments), else ``None``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name and not node.args:
        return node.func.value
    return None


def _is_col(node: ast.AST, aliases: ImportAliases) -> bool:
    """``pl.col(...)`` (however polars is imported), possibly followed by method calls (``pl.col("a").cast(...)``)."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and not (aliases.qualified_name(node) or "").startswith("polars."):
        node = node.func.value
    return isinstance(node, ast.Call) and (aliases.qualified_name(node) or "") in ("polars.col", "polars.functions.col")


_POLARS_ACCESSORS = frozenset({"get_column", "to_series", "drop_nulls", "fill_null"})


def _is_polars_value(node: ast.AST, aliases: ImportAliases, local: dict[str, ast.expr], depth: int = 0) -> bool:
    """Is *node* visibly a polars expression or Series (see the module docstring)?"""
    if isinstance(node, ast.Name) and depth < 3 and node.id in local:
        return _is_polars_value(local[node.id], aliases, local, depth + 1)
    while isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Call):
            qualified = aliases.qualified_name(node) or ""
            if qualified == "polars" or qualified.startswith("polars."):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in _POLARS_ACCESSORS:
                return True
            node = node.func
        else:
            node = node.value
    return False


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _compare_findings(node: ast.Compare, aliases: ImportAliases, local: dict[str, ast.expr]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    left = node.left
    for op, right in zip(node.ops, node.comparators):
        if isinstance(op, (ast.Eq, ast.NotEq)):
            recv_l = _method_call(left, "min") or _method_call(left, "max")
            recv_r = _method_call(right, "max") or _method_call(right, "min")
            same = recv_l is not None and recv_r is not None and ast.dump(recv_l) == ast.dump(recv_r) and ast.dump(left) != ast.dump(right)
            if same and recv_l is not None and _is_polars_value(recv_l, aliases, local):
                out.append(
                    (
                        RULE_MINMAX,
                        f"`{ast.unparse(node)}` skips nulls: an all-null column is not constant, [1, null] is; count nulls or compare with eq_missing",
                    )
                )
            elif (_is_col(left, aliases) and _is_none(right)) or (_is_none(left) and _is_col(right, aliases)):
                out.append((RULE_NONE, f"`{ast.unparse(node)}` is always null; use is_null()/is_not_null()"))
            elif _is_col(left, aliases) and _is_col(right, aliases):
                out.append((RULE_COLUMNS, f"`{ast.unparse(node)}` drops rows where either side is null; use eq_missing/ne_missing if null should match null"))
        left = right
    return out


def _call_findings(node: ast.Call, aliases: ImportAliases) -> list[tuple[str, str]]:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in ("eq", "ne") or len(node.args) != 1 or not _is_col(func.value, aliases):
        return []
    arg = node.args[0]
    if _is_none(arg):
        return [(RULE_NONE, f"`{ast.unparse(node)}` is always null; use is_null()/is_not_null()")]
    if _is_col(arg, aliases):
        return [(RULE_COLUMNS, f"`{ast.unparse(node)}` drops rows where either side is null; use {func.attr}_missing if null should match null")]
    return []


def _single_assignments(tree: ast.Module) -> dict[int, dict[str, ast.expr]]:
    """``{id(function): {name: value}}`` for names assigned exactly once in a function (a rebound name is ambiguous)."""
    out: dict[int, dict[str, ast.expr]] = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seen: dict[str, list[ast.expr]] = {}
        for n in ast.walk(func):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                seen.setdefault(n.targets[0].id, []).append(n.value)
        out[id(func)] = {k: v[0] for k, v in seen.items() if len(v) == 1}
    return out


def _scope_ids(tree: ast.Module) -> dict[int, int]:
    """``{id(node): id(innermost enclosing function)}`` (0 at module scope)."""
    out: dict[int, int] = {}

    def visit(node: ast.AST, scope: int) -> None:
        for child in ast.iter_child_nodes(node):
            out[id(child)] = scope
            visit(child, id(child) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope)

    visit(tree, 0)
    return out


def _file_findings(parsed: ParsedFile) -> list[Finding]:
    aliases = ImportAliases.from_tree(parsed.tree)
    if not _imports_polars(aliases):
        return []
    lines = parsed.source.splitlines()
    functions = enclosing_functions(parsed.tree)
    out: list[Finding] = []
    local_by_scope = _single_assignments(parsed.tree)
    scope_of = _scope_ids(parsed.tree)
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Compare):
            hits = _compare_findings(node, aliases, local_by_scope.get(scope_of.get(id(node), 0), {}))
        elif isinstance(node, ast.Call):
            hits = _call_findings(node, aliases)
        else:
            continue
        if hits and line_has_marker(lines, node.lineno, MARKER):
            continue
        where = functions.get(id(node), "<module>")
        out += [Finding(parsed.rel, node.lineno, rule, f"{where}: {message}") for rule, message in hits]
    return out


def _collect(root: Union[str, Path], *, skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]) -> tuple[list[Finding], ScanResult]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings = [f for parsed in scan for f in _file_findings(parsed)]
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), scan


def find_polars_null_equality(
    root: Union[str, Path], *, skip_dir_names: Iterable[str] = (), include_tests: bool = False, use_git: Optional[bool] = None
) -> list[Finding]:
    """Every null-sensitive polars comparison under *root*, plus one ``unparsed-file`` finding per unparsable file."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_polars_null_equality(
    root: Union[str, Path],
    *,
    advisory: bool = True,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Warn on each finding (``advisory=True``, the default) or fail on it (new against *baseline_path* when given).
    Fewer than *min_files* parsed files, or any unparsable file, fails either way."""
    findings, scan = _collect(root, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    if advisory:
        for f in findings:
            warnings.warn(f.render(), PolarsNullEqualityWarning, stacklevel=2)
        findings = []
    report(
        findings,
        gate="polars-null-equality",
        flag=REFRESH_FLAG,
        guidance="null handling differs between ==/min/max and eq_missing; decide which one the check means",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=None if advisory else baseline_path,
        refresh=refresh,
        request=request,
    )
