"""A ``*_memo`` / ``*_cache`` / ``*_fn`` parameter with a ``None`` default that no production call site ever passes.

A performance or injection hook is added with a default and unit-tested with a value; production never passes it, so the slow default path runs
for months (a ``split_buyer_info`` memo set existed, was tested, and neither production caller handed it over). The parameter is READ, so
``unread_function_params`` stays silent; what is missing is a non-``None`` argument at the call sites.

Reported, per function: a parameter whose name matches *param_regex* (default ``(_memo|_cache|_fn)$``) with a default of ``None``, when the
function has at least one non-test call site in the scanned corpus and EVERY such call omits the parameter or passes the literal ``None``.
Calls are matched by the callee's final name (``f(...)``, ``obj.f(...)``) and bound positionally and by keyword (``self``/``cls`` skipped for a
method).

Not reported: a function with no non-test call site (``uncalled_functions`` owns that); a call using ``*args``/``**kwargs`` (unknown, counts as
wired); a function name defined more than once in the corpus (calls cannot be attributed); test files unless *include_tests*; a ``def`` line
carrying ``# unwired-ok: <reason>`` (on the line or above).

Usage::

    from py_ci_shared.optional_collaborator_wired import assert_optional_collaborator_wired

    def test_optional_collaborators_are_wired():
        assert_optional_collaborator_wired(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = ["RULE", "assert_optional_collaborator_wired", "find_unwired_optional_collaborators"]

RULE = "optional-collaborator-unwired"
MARKER = "unwired-ok"
DEFAULT_PARAM_REGEX = r"(_memo|_cache|_fn)$"


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def _callee(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _none_default_params(fn: Any, regex: "re.Pattern[str]", is_method: bool) -> dict:
    """``{param: positional index in a call}`` for each matching parameter whose default is the literal ``None``."""
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    defaults: dict = {}
    for arg, default in zip(positional[len(positional) - len(a.defaults) :], a.defaults):
        defaults[arg.arg] = default
    for arg, kw_default in zip(a.kwonlyargs, a.kw_defaults):
        if kw_default is not None:
            defaults[arg.arg] = kw_default
    offset = 1 if is_method and positional and positional[0].arg in {"self", "cls"} else 0
    out: dict = {}
    for name, default in defaults.items():
        if regex.search(name) and isinstance(default, ast.Constant) and default.value is None:
            index = next((i - offset for i, p in enumerate(positional) if p.arg == name), None)
            out[name] = index
    return out


def _passes_value(call: ast.Call, param: str, index: Optional[int]) -> bool:
    """The call supplies a non-None value for *param* (or might: it uses ``*args``/``**kwargs``)."""
    if any(isinstance(a, ast.Starred) for a in call.args) or any(kw.arg is None for kw in call.keywords):
        return True
    for kw in call.keywords:
        if kw.arg == param:
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    if index is not None and 0 <= index < len(call.args):
        arg = call.args[index]
        return not (isinstance(arg, ast.Constant) and arg.value is None)
    return False


def _index(files: list) -> tuple:
    """``(defs, calls)``: functions and call nodes of *files*, each keyed by final name."""
    defs: dict = {}
    calls: dict = {}
    for parsed in files:
        classes = {id(m) for c in ast.walk(parsed.tree) if isinstance(c, ast.ClassDef) for m in c.body}
        for node in ast.walk(parsed.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append((parsed, node, id(node) in classes))
            elif isinstance(node, ast.Call) and (name := _callee(node.func)) is not None:
                calls.setdefault(name, []).append(node)
    return defs, calls


def find_unwired_optional_collaborators(
    root: Union[str, Path],
    *,
    param_regex: str = DEFAULT_PARAM_REGEX,
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every optional collaborator no production call passes, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    regex = re.compile(param_regex)
    files = [p for p in scan if include_tests or not _is_test_path(p.rel)]
    defs, calls = _index(files)
    out: list[Finding] = []
    for name, entries in defs.items():
        if len(entries) != 1:
            continue
        parsed, fn, is_method = entries[0]
        params = _none_default_params(fn, regex, is_method)
        site_calls = [c for c in calls.get(name, []) if c is not fn]
        if not params or not site_calls:
            continue
        lines = parsed.source.splitlines()
        if any(0 < n <= len(lines) and MARKER in lines[n - 1] for n in (fn.lineno, fn.lineno - 1)):
            continue
        for param, index in params.items():
            if not any(_passes_value(c, param, index) for c in site_calls):
                out.append(
                    Finding(
                        parsed.rel,
                        fn.lineno,
                        RULE,
                        f"{name}: parameter {param}=None is never passed a value by any of its {len(site_calls)} non-test call(s); "
                        "wire it at the call site or remove it",
                    )
                )
    return sorted(out, key=lambda f: (f.path, f.line, f.message))


def assert_optional_collaborator_wired(
    root: Union[str, Path],
    *,
    param_regex: str = DEFAULT_PARAM_REGEX,
    include_tests: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unwired optional collaborator, or with *baseline_path* on any the baseline does not accept."""
    found = find_unwired_optional_collaborators(
        root, param_regex=param_regex, include_tests=include_tests, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "pass the memo/cache/hook at a production call site, or delete the parameter"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="optional_collaborator_wired", refresh_command="PY_CI_SHARED_REFRESH=optional_collaborator_wired")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} optional-collaborator-unwired finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
