"""``if few distinct values: y = y.astype(int)`` that turns a fractional target into a handful of integer classes.

``if y.nunique() <= 32: y = y.astype(np.int64)`` is meant for integer-valued targets with few levels (class labels).
On a fractional target with few distinct values (0.25, 0.5, 0.75) the cast truncates every value to 0 and collapses
the target to one class: no exception, only a degenerate model. mlframe had this copy-pasted to 19 sites before
test_no_truncating_y_discretisation found them.

What is reported: an ``if`` whose test mentions a distinct count (``nunique``, ``unique``, ``distinct`` in a call or
name) of a bare name ``y`` and whose body assigns ``y.astype(<int dtype>)`` (``int``, ``np.intN``, ``"intN"``,
``"uintN"``) to the same name or another one, when neither the test nor the body shows an integrality check
(``round``, ``floor``, ``ceil``, ``is_integer``, ``issubdtype``/``integer`` dtype checks, ``isclose``, ``allclose``, ``% 1``, ``fix``, ``trunc``). Suppress
with ``# truncating-ok: <reason>`` on the ``astype`` line.

Usage in a consumer's meta test::

    from py_ci_shared.truncating_discretisation import assert_truncating_discretisation

    def test_truncating_discretisation():
        assert_truncating_discretisation("src")
"""

from __future__ import annotations

import ast
from ._core.unparse import unparse_stable
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "SUPPRESSION", "assert_truncating_discretisation", "find_truncating_discretisation"]

RULE = "truncating-discretisation"
SUPPRESSION = "# truncating-ok:"

_DISTINCT = re.compile(r"nunique|unique|distinct|n_classes|n_levels", re.IGNORECASE)
_INTEGRALITY = re.compile(r"round|floor|ceil|is_integer|isclose|allclose|% ?1\b|\bfix\b|trunc|isin\(|issubdtype|integer", re.IGNORECASE)
_INT_DTYPE = re.compile(r"^u?int(8|16|32|64)?$|^(np|numpy)\.u?int(8|16|32|64)?$")


def _tested_names(test: ast.expr) -> set[str]:
    """Bare names appearing under a distinct-count spelling inside an ``if`` test."""
    names: set[str] = set()
    for node in shared_walk(test):
        if isinstance(node, ast.Call):
            func = node.func
            label = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if _DISTINCT.search(label):
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    names.add(func.value.id)
                names.update(a.id for a in node.args if isinstance(a, ast.Name))
    return names


def _int_cast_target(node: ast.AST) -> Optional[ast.Name]:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "astype" and node.args):
        return None
    receiver = node.func.value
    if not isinstance(receiver, ast.Name):
        return None
    arg = node.args[0]
    text = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else unparse_stable(arg)
    return receiver if _INT_DTYPE.match(text) else None


def _findings_in(tree: ast.Module, rel: str, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for node in (n for n in shared_walk(tree) if isinstance(n, ast.If)):
        tested = _tested_names(node.test)
        if not tested:
            continue
        guard_text = " ".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
        if _INTEGRALITY.search(guard_text):
            continue
        for sub in (n for n in shared_walk(node) if isinstance(n, ast.Call)):
            receiver = _int_cast_target(sub)
            if receiver is None or receiver.id not in tested:
                continue
            if 1 <= sub.lineno <= len(lines) and SUPPRESSION in lines[sub.lineno - 1]:
                continue
            out.append(
                Finding(
                    rel,
                    sub.lineno,
                    RULE,
                    f"'{receiver.id}' is cast to an integer dtype because it has few distinct values; a fractional target collapses to one class "
                    "(check np.all(y == np.round(y)) first)",
                )
            )
    return out


def find_truncating_discretisation(
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
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines()))
    return sorted(set(out), key=lambda f: (f.path, f.line))


def assert_truncating_discretisation(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_truncating_discretisation(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "cast only after proving the values are integral (np.all(y == np.round(y))), or `# truncating-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="truncating_discretisation", refresh_command="PY_CI_SHARED_REFRESH=truncating_discretisation")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} truncating-discretisation finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
