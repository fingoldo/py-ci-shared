"""Write-then-rename staging that does not stage what it renames, and cache/baseline files rewritten in place.

Two rules, per function:

* ``npsave-suffix``: ``np.save(tmp, a)`` appends ``.npy`` to a path that does not already end in it (``np.savez`` and
  ``np.savez_compressed`` append ``.npz``), so a later ``os.replace(tmp, final)`` renames a file that does not exist.
  The shipped instance staged ``x.npy.<pid>.partial``, the rename raised, a swallowed ``OSError`` hid it, and the cache
  never landed: every process re-encoded for two days and 88 orphan ``*.partial.npy`` files (21.9 GB) piled up. The
  staging path is resolved through the function's own assignments (string literal, f-string, ``with_suffix``,
  ``with_name``, ``/``); a path whose suffix cannot be read is not judged, and neither is a file object.
* ``in-place-rewrite``: ``write_text``/``write_bytes``/``open(..., "w")`` on a path whose expression names a cache or
  a baseline (``cache``/``baseline`` in an identifier or literal), in a function that never renames anything into
  place. A reader (another process, a concurrent test) can see the file truncated, and a crash mid-write leaves it
  corrupt; write a temporary sibling and ``os.replace`` it (``_core.atomic_write_text`` does this). A call whose line
  carries ``# atomic-ok: <reason>`` is exempt.

Existing findings are ratcheted by the baseline.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ParsedFile, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["REFRESH_FLAG", "assert_atomic_write_staging", "find_atomic_write_staging"]

REFRESH_FLAG = "--refresh-atomic-write-staging-baseline"
GATE = "atomic-write-staging"
RULE_NPSAVE = "npsave-suffix"
RULE_REWRITE = "in-place-rewrite"
_DEFAULT_EXCLUDE = frozenset({"tests", "test", "testing", "benchmarks", "_benchmarks", "bench", "examples", "scripts", "probes"})
_SAVERS = {"numpy.save": ".npy", "numpy.savez": ".npz", "numpy.savez_compressed": ".npz"}
_RENAMERS = frozenset({"os.replace", "os.rename", "shutil.move"})
_RENAME_METHODS = frozenset({"replace", "rename"})
_CACHE_WORD = re.compile(r"cache|baseline", re.IGNORECASE)
_MARKER = re.compile(r"#\s*atomic-ok:\s*\S")
_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _suffix_of(expr: ast.expr, assigned: dict[str, ast.expr], depth: int = 0) -> Optional[str]:
    """The literal tail of a path expression (``".partial"``, ``"x.npy"``), or None when it cannot be read."""
    if depth > 5:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr) and expr.values:
        last = expr.values[-1]
        return last.value if isinstance(last, ast.Constant) and isinstance(last.value, str) else None
    if isinstance(expr, ast.Name) and expr.id in assigned:
        return _suffix_of(assigned[expr.id], assigned, depth + 1)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Div, ast.Add)):
        return _suffix_of(expr.right, assigned, depth + 1)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.args:
        if expr.func.attr in ("with_suffix", "with_name"):
            return _suffix_of(expr.args[0], assigned, depth + 1)
        if expr.func.attr == "format" and isinstance(expr.func.value, ast.Constant):
            return str(expr.func.value.value)
    if isinstance(expr, ast.Call) and _call_is(expr, ("str", "Path", "os.fspath", "pathlib.Path")) and expr.args:
        return _suffix_of(expr.args[0], assigned, depth + 1)
    return None


def _call_is(call: ast.Call, names: Iterable[str]) -> bool:
    func = call.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else (f"{func.value.id}.{func.attr}" if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else "")
    )
    return name in names


def _own_nodes(fn: _FunctionNode) -> list[ast.AST]:
    """Nodes of *fn* without descending into nested functions or classes (they are judged on their own)."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)))
    return out


def _assignments(nodes: list[ast.AST]) -> tuple[dict[str, ast.expr], set[str]]:
    """``(name -> last assigned value, names bound as file objects by with/open)``."""
    assigned: dict[str, ast.expr] = {}
    handles: set[str] = set()
    for node in sorted((n for n in nodes if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset)):  # type: ignore[attr-defined]
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigned[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigned[node.target.id] = node.value
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    handles.add(item.optional_vars.id)
    return assigned, handles


def _renamed_sources(nodes: list[ast.AST], aliases: ImportAliases) -> set[str]:
    out: set[str] = set()
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        if aliases.qualified_name(n.func) in _RENAMERS and n.args:
            out.add(ast.unparse(n.args[0]))
        elif isinstance(n.func, ast.Attribute) and n.func.attr in _RENAME_METHODS and n.args and not isinstance(n.func.value, ast.Constant):
            out.add(ast.unparse(n.func.value))
    return out


def _npsave_findings(f: ParsedFile, fn: _FunctionNode, nodes: list[ast.AST], aliases: ImportAliases) -> list[Finding]:
    assigned, handles = _assignments(nodes)
    renamed = _renamed_sources(nodes, aliases)
    out: list[Finding] = []
    for n in nodes:
        if not (isinstance(n, ast.Call) and n.args):
            continue
        want = _SAVERS.get(aliases.qualified_name(n.func) or "")
        target = n.args[0]
        if want is None or ast.unparse(target) not in renamed:
            continue
        if isinstance(target, ast.Name) and (target.id in handles or _is_open_call(assigned.get(target.id))):
            continue
        tail = _suffix_of(target, assigned)
        if tail is None or tail.endswith(want):
            continue
        message = f"{fn.name}: np.save-family writes `{ast.unparse(target)}` + '{want}' (its path ends {tail[-24:]!r}), then renames `{ast.unparse(target)}`"
        out.append(Finding(f.rel, n.lineno, RULE_NPSAVE, message))
    return out


def _is_open_call(expr: Optional[ast.expr]) -> bool:
    return isinstance(expr, ast.Call) and _call_is(expr, ("open", "io.open"))


def _write_target(call: ast.Call) -> Optional[ast.expr]:
    """The path a text/bytes rewrite goes to: ``p.write_text(...)`` -> ``p``; ``open(p, "w")`` -> ``p``."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
        return func.value
    if _call_is(call, ("open", "io.open")) and call.args:
        mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "w" in mode.value:
            return call.args[0]
    return None


def _names_cache(expr: ast.expr, assigned: dict[str, ast.expr]) -> bool:
    text = ast.unparse(expr)
    if isinstance(expr, ast.Name) and expr.id in assigned:
        text += " " + ast.unparse(assigned[expr.id])
    return bool(_CACHE_WORD.search(text))


def _rewrite_findings(f: ParsedFile, fn: _FunctionNode, nodes: list[ast.AST], aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    if _renamed_sources(nodes, aliases):
        return []  # the function stages and renames: the write is (at least meant to be) the staging half
    assigned, _ = _assignments(nodes)
    out: list[Finding] = []
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        target = _write_target(n)
        if target is None or not _names_cache(target, assigned):
            continue
        if 1 <= n.lineno <= len(lines) and _MARKER.search(lines[n.lineno - 1]):
            continue
        out.append(Finding(f.rel, n.lineno, RULE_REWRITE, f"{fn.name}: rewrites `{ast.unparse(target)}` in place; write a sibling and os.replace it"))
    return out


def find_atomic_write_staging(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    rules: Iterable[str] = (RULE_NPSAVE, RULE_REWRITE),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` over the production files under *root*."""
    wanted = set(rules)
    scan = scan_python(root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        aliases = ImportAliases.from_tree(f.tree)
        lines = f.source.splitlines()
        for fn in (n for n in _fast_walk(f.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            nodes = _own_nodes(fn)
            if RULE_NPSAVE in wanted:
                findings.extend(_npsave_findings(f, fn, nodes, aliases))
            if RULE_REWRITE in wanted:
                findings.extend(_rewrite_findings(f, fn, nodes, aliases, lines))
    findings.sort(key=lambda x: (x.path, x.line))
    return findings, scan


def assert_atomic_write_staging(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    exclude_parts: Iterable[str] = _DEFAULT_EXCLUDE,
    rules: Iterable[str] = (RULE_NPSAVE, RULE_REWRITE),
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding not accepted by *baseline_path*. Missing baseline fails; refresh with ``REFRESH_FLAG`` or
    ``PY_CI_SHARED_REFRESH=atomic-write-staging``."""
    findings, scan = find_atomic_write_staging(root, exclude_parts=exclude_parts, rules=rules, use_git=use_git)
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="stage to a sibling whose name keeps the saver's suffix and os.replace it into place",
    )
