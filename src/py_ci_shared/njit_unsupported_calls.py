"""A call inside a compiled kernel body that one numba/torch.jit build accepts and another rejects.

``np.median`` inside an ``@njit`` function compiled on a developer machine and raised ``TypingError`` on the Linux CI
numba, cascading into dozens of failures; ``typing.cast`` is no runtime operation numba can type; ``logging`` and
``warnings.warn`` are not available in nopython mode. The defect is invisible until the kernel is first called on
the failing build. mlframe kept this as a local meta-test (``test_no_njit_unsupported_numpy_reduction``,
``test_no_cast_in_compiled_functions``); this gate shares it.

What is reported: a call, in the body of a function decorated with ``numba.njit``/``jit``/``vectorize``/
``guvectorize``/``cfunc`` or ``torch.jit.script``, whose resolved name is in :data:`DEFAULT_UNSUPPORTED`
(``typing.cast``, ``numpy.median``, ``numpy.nanmedian``, ``warnings.warn``) or that is a method call on a logger
(``logger``/``log``/``logging``/``_logger`` receivers). Pass *extra* (``{"qualified.name": reason}``) to add repo
specific entries, or *unsupported* to replace the table (a pinned numba that supports ``np.median`` drops it).
Suppress a reviewed call with ``# njit-ok: <reason>``.

Usage in a consumer's meta test::

    from py_ci_shared.njit_unsupported_calls import assert_njit_unsupported_calls

    def test_njit_unsupported_calls():
        assert_njit_unsupported_calls("src")
"""

from __future__ import annotations

import ast
from ._core.unparse import unparse_stable
from collections.abc import Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from ._core import walk as shared_walk

__all__ = ["DEFAULT_UNSUPPORTED", "RULE", "SUPPRESSION", "assert_njit_unsupported_calls", "find_njit_unsupported_calls"]

RULE = "njit-unsupported-call"
SUPPRESSION = "# njit-ok:"

DEFAULT_UNSUPPORTED: Mapping[str, str] = {
    "typing.cast": "typing.cast is not a typed operation in nopython mode",
    "numpy.median": "np.median raised TypingError on the CI numba build",
    "numpy.nanmedian": "np.nanmedian raised TypingError on the CI numba build",
    "warnings.warn": "warnings.warn is unavailable in nopython mode",
}
_COMPILERS = frozenset({"njit", "jit", "vectorize", "guvectorize", "cfunc", "stencil"})
_LOGGER_NAMES = frozenset({"logger", "log", "logging", "_logger", "_log", "LOGGER"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _compiled(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], aliases: ImportAliases) -> bool:
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if not isinstance(target, (ast.Name, ast.Attribute)):
            continue
        name = aliases.qualified_name(target) or ""
        if (name.startswith("numba.") and name.rsplit(".", 1)[-1] in _COMPILERS) or name == "torch.jit.script":
            return True
    return False


def _is_logger_call(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in _LOGGER_METHODS and isinstance(func.value, ast.Name) and func.value.id in _LOGGER_NAMES


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: list[str], table: Mapping[str, str]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[int] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS) and _compiled(n, aliases)):
        for call in (n for n in shared_walk(func) if isinstance(n, ast.Call)):
            name = aliases.qualified_name(call) or ""
            reason = table.get(name) or ("logging inside a compiled kernel is unavailable" if _is_logger_call(call) else None)
            if reason is None or call.lineno in seen:
                continue
            seen.add(call.lineno)
            if 1 <= call.lineno <= len(lines) and SUPPRESSION in lines[call.lineno - 1]:
                continue
            shown = name or unparse_stable(call.func)
            out.append(Finding(rel, call.lineno, RULE, f"'{shown}' inside compiled '{func.name}': {reason}"))
    return out


def find_njit_unsupported_calls(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    unsupported: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    table = {**(DEFAULT_UNSUPPORTED if unsupported is None else unsupported), **(extra or {})}
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines(), table))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_njit_unsupported_calls(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    unsupported: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_njit_unsupported_calls(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, unsupported=unsupported, extra=extra)
    guidance = "move the call out of the compiled kernel (or replace it with a hand-written loop), or `# njit-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="njit_unsupported_calls", refresh_command="PY_CI_SHARED_REFRESH=njit_unsupported_calls")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} njit-unsupported-call finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
