"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from ._base import walk_tree, Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, child_nodes
from .._core.aliases import ImportAliases

__all__ = ["scan_missing_network_timeout"]

# --- network call with no timeout= ----------------------------------------
#
# Class of bug found repeatedly in the 2026-07-21 web/cloud audit (the single most
# High-finding-dense domain in the whole audit): web.py's urllib.request.urlopen() calls in
# get_external_ip()/get_ipinfo() had no timeout, and download_in_parallel()'s grequests.get()
# call had none either -- a single unresponsive URL could hang the whole batch/process
# indefinitely on a real network failure (not a hypothetical -- both were flagged High).


# Attrs of a network call that accept timeout= as a kwarg.
_NETWORK_CALL_ATTRS = frozenset(
    {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "request",
        "urlopen",
    }
)
_NETWORK_MODULE_ROOTS = frozenset({"requests", "grequests", "httpx", "urllib"})
# Constructors whose instances carry a timeout that every method call inherits.
_SESSION_CONSTRUCTORS = frozenset({"requests.Session", "requests.session", "requests.sessions.Session", "httpx.Client", "httpx.AsyncClient"})


def _has_timeout(call: ast.Call) -> bool:
    """A real ``timeout=`` keyword. ``timeout=None`` means no timeout at all, so it does not count."""
    for kw in call.keywords:
        if kw.arg == "timeout":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def _has_unknown_kwargs(call: ast.Call) -> bool:
    """``**kwargs`` may carry a timeout; the scanner cannot tell, so it does not judge the call."""
    return any(kw.arg is None for kw in call.keywords)


def _receiver_key(expr: ast.expr) -> Optional[str]:
    """``name`` or dotted ``self.session`` for a receiver expression, else ``None``."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        inner = _receiver_key(expr.value)
        return f"{inner}.{expr.attr}" if inner is not None else None
    return None


def _session_ctor_binding(value: ast.expr, aliases: ImportAliases) -> Optional[bool]:
    """For ``x = <call>``: ``True`` if the call carries a real timeout, ``False`` if it is a session
    constructor without one, ``None`` when the value says nothing about a timeout."""
    if not isinstance(value, ast.Call):
        return None
    if aliases.qualified_name(value.func) in _SESSION_CONSTRUCTORS:
        return _has_timeout(value)
    if _has_timeout(value):
        return True  # a helper that builds a configured client: same-file heuristic, as before
    return None


def _bindings_in(nodes: list[ast.AST], aliases: ImportAliases) -> dict[str, bool]:
    """``{receiver key -> has timeout}`` for every assignment or ``with ... as`` among ``nodes``."""
    out: dict[str, bool] = {}
    for node in nodes:
        pairs: list[tuple[ast.expr, Optional[ast.expr]]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t, node.value) for t in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            pairs = [(node.optional_vars, node.context_expr)]
        for target, value in pairs:
            key = _receiver_key(target)
            verdict = _session_ctor_binding(value, aliases) if value is not None else None
            if key is not None and verdict is not None:
                out[key] = verdict
    return out


def _scope_tables(tree: ast.Module, aliases: ImportAliases) -> tuple[dict[int, dict[str, bool]], dict[int, ast.AST], dict[str, bool], dict[str, bool]]:
    """Per-function binding tables, the scope each call sits in, the module table, and the file-wide
    attribute table (``self.session`` is bound in ``__init__`` and used in another method)."""
    walked = list(walk_tree(tree))
    functions = [n for n in walked if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    per_function: dict[int, dict[str, bool]] = {}
    call_scope: dict[int, ast.AST] = {}
    for func in functions:  # BFS order: an inner function comes after its outer one, so it wins below
        inner = list(walk_tree(func))
        per_function[id(func)] = _bindings_in(inner, aliases)
        for node in inner:
            if isinstance(node, ast.Call):
                call_scope[id(node)] = func
    module_table = {k: v for k, v in _bindings_in(_top_level(tree), aliases).items() if "." not in k}
    attr_table = {k: v for k, v in _bindings_in(walked, aliases).items() if "." in k}
    return per_function, call_scope, module_table, attr_table


_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _top_level(tree: ast.Module) -> list[ast.AST]:
    """Nodes of the module outside any def, class or lambda body (a manual walk, so bodies can be pruned)."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, _SCOPE_NODES):
            stack.extend(child_nodes(node))
    return out


def _receiver_has_no_timeout(recv: ast.expr, call: ast.Call, scope_tables: tuple) -> bool:
    """Whether a method call on a bound receiver is a session built without a timeout (and the call itself sets none)."""
    per_function, call_scope, module_table, attr_table = scope_tables
    key = _receiver_key(recv)
    if key is None:
        return False
    if "." in key:
        verdict = attr_table.get(key)
    else:
        scope = call_scope.get(id(call))
        table = per_function.get(id(scope), {}) if scope is not None else {}
        verdict = table[key] if key in table else module_table.get(key)
    return verdict is False


def _network_verdict(node: ast.Call, aliases: ImportAliases, scope_tables: tuple) -> Optional[str]:
    """A label naming the call when it is a network call with no timeout, else ``None``."""
    if _has_unknown_kwargs(node) or _has_timeout(node):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _NETWORK_CALL_ATTRS:
        if isinstance(func.value, ast.Call) and aliases.qualified_name(func.value.func) in _SESSION_CONSTRUCTORS:
            return f"{aliases.qualified_name(func.value.func)}(...).{func.attr}" if not _has_timeout(func.value) else None
        qualified = aliases.qualified_name(func)
        if qualified is not None and qualified.split(".")[0] in _NETWORK_MODULE_ROOTS:
            return qualified
        if _receiver_has_no_timeout(func.value, node, scope_tables):
            return f"{_receiver_key(func.value)}.{func.attr}"
        return None
    if isinstance(func, ast.Name):
        qualified = aliases.qualified_name(func)
        if qualified is not None and qualified.split(".")[0] in _NETWORK_MODULE_ROOTS and qualified.rsplit(".", 1)[-1] in _NETWORK_CALL_ATTRS:
            return qualified
    return None


def scan_missing_network_timeout(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find HTTP/socket calls (``requests.get/post/...``, ``urllib.request.urlopen``,
    ``grequests.get/post``, ``httpx.get/post``, and the methods of a ``requests.Session`` or
    ``httpx.Client`` built without a timeout) that have no ``timeout=`` keyword, or ``timeout=None``
    -- a call that can hang forever on a real network failure instead of raising after a bounded wait.

    Callees are resolved through the module's import aliases (``import requests as rq``,
    ``from requests import Session``). A session receiver is judged by its own binding in the same
    function (or at module level, or a ``self.x`` attribute anywhere in the file); a binding built
    with a real ``timeout=`` exempts its calls, and it is per function, not file-wide.

    Severity: P2 (a real, if narrow, hang risk -- not a crash, so lower than a correctness bug).
    """
    findings: list[Finding] = []
    for py in _iter_py_files(root, exclude_dirs):
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        aliases = ImportAliases.from_tree(tree)
        scope_tables = _scope_tables(tree, aliases)
        for node in walk_tree(tree):
            if not isinstance(node, ast.Call):
                continue
            label = _network_verdict(node, aliases, scope_tables)
            if label is None:
                continue
            findings.append(
                Finding(
                    check="missing_network_timeout",
                    severity="P2",
                    file=rel,
                    line=node.lineno,
                    snippet=_line_text(src_lines, node.lineno),
                    detail=(
                        f"`{label}(...)` has no `timeout=` -- can hang "
                        "indefinitely on an unresponsive server/black-holed connection instead of "
                        "raising after a bounded wait."
                    ),
                )
            )
    return findings
