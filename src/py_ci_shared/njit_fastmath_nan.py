"""A numba kernel compiled with NaN/inf-assuming ``fastmath`` whose own body produces or tests NaN/inf.

``@njit(fastmath=True)`` (or a flag set containing ``nnan``, ``ninf`` or ``fast``) lets LLVM assume no NaN and no
infinity exists. In a kernel that then asks ``np.isnan(v)`` / ``np.isfinite(v)`` / ``v != v`` or hands back
``np.nan`` as a sentinel, the optimiser may fold the test to a constant or reorder the comparison: a column of NaN
passes the finiteness guard, ``min``/``argmax`` over NaN rows are arbitrary and the result differs between numba
versions and from the numpy twin. Nothing raises; only the selection or score is wrong. mlframe fixed this by hand
in 1768521c7, ed2bdda1a and e1828f6bd.

What is reported: a function decorated with ``njit``/``jit``/``vectorize``/``guvectorize`` (resolved through
imports of ``numba``) whose ``fastmath`` keyword is ``True`` or a flag collection with ``nnan``/``ninf``/``fast``,
and whose own body (nested functions included, callees excluded) mentions ``isnan``/``isfinite``/``isinf``,
``nan``/``inf`` attributes, ``float("nan")``/``float("inf")`` or a self-comparison ``x != x``. The flag value may be
a literal, a set/list/tuple literal or a module-level constant naming one. A NaN-safe set such as
``{"nsz", "arcp", "contract", "afn", "reassoc"}`` is the fix and is never reported.

Suppress a reviewed kernel with ``# fastmath-nan-ok: <reason>`` on the decorator or ``def`` line.

Usage in a consumer's meta test::

    from py_ci_shared.njit_fastmath_nan import assert_njit_fastmath_nan

    def test_njit_fastmath_nan():
        assert_njit_fastmath_nan("src", baseline_path="tests/baselines/njit_fastmath_nan.json")
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "SUPPRESSION", "assert_njit_fastmath_nan", "find_njit_fastmath_nan"]

RULE = "njit-fastmath-nan"
SUPPRESSION = "# fastmath-nan-ok:"

_DECORATORS = frozenset({"njit", "jit", "vectorize", "guvectorize", "cfunc", "stencil"})
_NAN_FLAGS = frozenset({"nnan", "ninf", "fast"})
_NAN_TESTS = frozenset({"isnan", "isfinite", "isinf", "nan", "inf", "NaN", "Inf", "NAN", "INF"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = node.value
    return out


def _is_full_fastmath(value: ast.expr, constants: dict[str, ast.expr], depth: int = 0) -> bool:
    if isinstance(value, ast.Constant):
        return value.value is True
    if isinstance(value, ast.Name) and depth < 4 and value.id in constants:
        return _is_full_fastmath(constants[value.id], constants, depth + 1)
    if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
        return any(isinstance(e, ast.Constant) and e.value in _NAN_FLAGS for e in value.elts)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in {"set", "frozenset"} and value.args:
        return _is_full_fastmath(value.args[0], constants, depth + 1)
    return False


def _numba_call(dec: ast.expr, aliases: ImportAliases) -> Optional[ast.Call]:
    """The decorator call when it is ``numba.<njit|jit|...>(...)`` with arguments, else ``None``."""
    if not isinstance(dec, ast.Call):
        return None
    name = aliases.qualified_name(dec)
    if name is None or not name.startswith("numba.") or name.rsplit(".", 1)[-1] not in _DECORATORS:
        return None
    return dec


def _is_float_literal_call(node: ast.Call) -> bool:
    if not (isinstance(node.func, ast.Name) and node.func.id == "float" and node.args):
        return False
    arg = node.args[0]
    return isinstance(arg, ast.Constant) and str(arg.value).lstrip("+-").lower() in {"nan", "inf", "infinity"}


def _is_self_comparison(node: ast.Compare) -> bool:
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.NotEq):
        return False
    left, right = node.left, node.comparators[0]
    return isinstance(left, ast.Name) and isinstance(right, ast.Name) and left.id == right.id


def _nan_mention(func: ast.AST) -> Optional[ast.AST]:
    for node in shared_walk(func):
        if isinstance(node, ast.Attribute) and node.attr in _NAN_TESTS:
            return node
        if isinstance(node, ast.Compare) and _is_self_comparison(node):
            return node
        if isinstance(node, ast.Call) and _is_float_literal_call(node):
            return node
    return None


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    constants = _module_constants(tree)
    out: list[Finding] = []
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        for dec in func.decorator_list:
            call = _numba_call(dec, aliases)
            if call is None:
                continue
            flag = next((kw.value for kw in call.keywords if kw.arg == "fastmath"), None)
            if flag is None or not _is_full_fastmath(flag, constants):
                continue
            hit = _nan_mention(func)
            if hit is None:
                continue
            span = range(min(dec.lineno, func.lineno), func.lineno + 1)
            if any(SUPPRESSION in lines[i - 1] for i in span if 1 <= i <= len(lines)):
                continue
            out.append(
                Finding(
                    rel,
                    func.lineno,
                    RULE,
                    f"'{func.name}' is compiled with NaN/inf-assuming fastmath but handles NaN/inf itself (line {getattr(hit, 'lineno', func.lineno)}): "
                    "use a NaN-safe flag set (nsz, arcp, contract, afn, reassoc) or drop fastmath",
                )
            )
            break
    return out


def find_njit_fastmath_nan(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_njit_fastmath_nan(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_njit_fastmath_nan(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "replace fastmath=True by a NaN-safe flag set ({'nsz','arcp','contract','afn','reassoc'}), or `# fastmath-nan-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="njit_fastmath_nan", refresh_command="PY_CI_SHARED_REFRESH=njit_fastmath_nan")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} njit-fastmath-nan finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
