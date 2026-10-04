"""Named function parameters that the body never reads: an accepted, documented, ignored promise.

A function that takes ``sample_weight``, ``random_state``, ``seed``, ``n_jobs``, ``verbose``, ``timeout`` or ``device`` and
never looks at it still advertises the knob in its signature, its docstring and every caller's code, so setting it
changes nothing and raises nothing. These are the names where a silent no-op is a real defect rather than lint:

* ``sample_weight`` ignored: the fit silently treats a weighted problem as unweighted, and the metrics computed from
  that model are for a different objective than the caller asked for;
* ``random_state``/``seed`` ignored: two runs that were pinned to the same seed differ, and a reproducibility claim is
  false (a splitter took ``random_state`` and shuffled with the global generator);
* ``n_jobs``/``timeout`` ignored: the parallelism or deadline the caller configured is not applied;
* ``verbose``/``device`` ignored: the run is silent, or on the wrong device, against the caller's setting.

``unread_init_params`` is the sibling for constructor parameters (``__init__`` is therefore skipped here: a parameter
there counts as read through the attribute it is stored in, which that gate follows).

A parameter counts as read when its name is loaded anywhere in the function body, nested functions and f-strings
included (``seed = check(seed)`` and ``seed += 1`` read it). A body that calls ``locals()`` or ``vars()`` counts as
reading everything. Not reported: functions whose body is only a docstring, ``pass``, ``...`` or
``raise NotImplementedError``; ``@abstractmethod`` and ``@overload`` definitions; methods of ``Protocol`` classes;
methods that override a base signature, recognised by a ``super().<same name>`` call or an ``@override`` decorator.

Known false-positive shape: a callback or hook whose signature is fixed by a framework and which ignores one of these
names without saying so (an override without ``super()`` or ``@override``). Prefix the parameter with an underscore,
mark the override with ``@override``, or write ``# unused-ok: <reason>`` on the ``def`` header.

Usage in a consumer's meta test::

    from py_ci_shared.unread_function_params import assert_unread_function_params

    def test_no_unread_function_params():
        assert_unread_function_params("src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["NAMED_PARAMS", "RULE", "assert_unread_function_params", "find_unread_function_params"]

RULE = "unread-function-param"
SUPPRESSION = "# unused-ok:"
NAMED_PARAMS = frozenset({"sample_weight", "sample_weights", "weights", "random_state", "seed", "n_jobs", "verbose", "timeout", "device"})

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_MARKER_DECORATORS = frozenset({"abstractmethod", "abstractproperty", "abstractclassmethod", "abstractstaticmethod", "overload", "override"})


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""


def _is_stub(body: list[ast.stmt]) -> bool:
    """Only a docstring, ``pass``, ``...`` or ``raise NotImplementedError``."""
    statements = (
        body[1:] if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str) else body
    )
    if not statements:
        return True
    for stmt in statements:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
            continue
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                continue
        return False
    return True


def _calls_super_method(func: ast.AST, name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.value, ast.Call):
            callee = node.value.func
            if isinstance(callee, ast.Name) and callee.id == "super":
                return True
    return False


def _reads(func: ast.AST) -> tuple[set[str], bool]:
    """Names loaded in the function (parameters excluded from consideration by the caller) and whether it uses ``locals()``."""
    loaded: set[str] = set()
    dynamic = False
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
                if node.id in ("locals", "vars"):
                    dynamic = True
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            loaded.add(node.target.id)
    return loaded, dynamic


def _is_protocol(node: ast.ClassDef, aliases: ImportAliases) -> bool:
    return any((aliases.qualified_name(b) or "").rsplit(".", 1)[-1] == "Protocol" for b in node.bases)


def _suppressed(lines: list[str], func: ast.AST) -> bool:
    body = getattr(func, "body", [])
    lo = getattr(func, "lineno", 0)
    hi = (body[0].lineno - 1) if body else lo
    return any(SUPPRESSION in lines[i] for i in range(max(lo - 1, 0), min(max(hi, lo), len(lines))))


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    lines = lines or []
    out: list[Finding] = []

    def visit(node: ast.AST, prefix: str, in_protocol: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", _is_protocol(child, aliases))
            elif isinstance(child, _FUNCTIONS):
                check(child, prefix, in_protocol)
                visit(child, f"{prefix}{child.name}.<locals>.", False)
            else:
                visit(child, prefix, in_protocol)

    def check(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], prefix: str, in_protocol: bool) -> None:
        a = func.args
        params = [p for p in (*a.posonlyargs, *a.args, *a.kwonlyargs) if p.arg in NAMED_PARAMS]
        if not params or func.name == "__init__" or in_protocol or _is_stub(func.body):
            return
        if any(_decorator_name(d) in _MARKER_DECORATORS for d in func.decorator_list) or _calls_super_method(func, func.name):
            return
        loaded, dynamic = _reads(func)
        if dynamic or _suppressed(lines, func):
            return
        out.extend(
            Finding(rel, param.lineno, RULE, f"{prefix}{func.name}: parameter '{param.arg}' is accepted but never read")
            for param in params
            if param.arg not in loaded
        )

    visit(tree, "", False)
    return out


def find_unread_function_params(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments (matched against the POSIX relative path) of files to skip. Raises ``EmptyScanError``
    when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_unread_function_params(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_unread_function_params(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = "use the parameter, drop it, prefix it with an underscore, mark an override with @override, or `# unused-ok: <reason>` on the def header"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="unread_function_params", refresh_command="PY_CI_SHARED_REFRESH=unread_function_params")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} unread-function-param finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
