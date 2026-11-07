"""A numba kernel that has a pure-Python twin, with no test that runs the two on the same input.

mlframe 1b56cfb90: the pure-Python frac-diff fallback divided by ``weights[0]`` at every step while the njit kernel multiplied by ``1.0 / weights[0]``; 205 of 400 elements differed and the
recurrence carried the error forward, on every machine without numba. The twin is not the compiled function's ``py_func`` there: it is a hand-written loop behind ``if _HAS_NUMBA:``. Nothing
checked that the two agree. ``py_ci_shared.njit_twin_parity.assert_njit_twin_parity`` is the runtime check; this gate is the inventory that says where it is owed.

An obligation exists only where the code itself says a second implementation of the arithmetic exists. Every other ``@njit`` function is left alone (mlframe has 294 njit files and only 8 test files that
touch ``py_func``; an inventory of all of them is noise that nobody pays down). Rules, by how the twin shows itself:

* ``njit-flag-fallback-untested``: a plain function with an ``if``/conditional expression on a numba flag (``_HAS_NUMBA``, ``_NUMBA_AVAILABLE``, ``numba_available()`` ...) that also calls an ``@njit`` function of the
  corpus: the other arm is the fallback (the frac-diff shape). Tested when one test file refers to the function (or a kernel it calls) and either toggles the flag by name or compares through ``py_func``.
* ``njit-twin-name-untested``: an ``@njit`` function and a plain function of the same module whose names differ by a twin word (``foo`` / ``foo_py``, ``_foo_python``, ``py_foo``, ``foo_numpy``, ``foo_reference``, ``foo_ref``,
  ``foo_fallback``, ``foo_pure``; or ``foo_njit`` / ``foo`` when ``foo`` does not call the kernel). Tested when one test file refers to both names, or reaches the kernel through ``py_func``.
* ``njit-redefined-twin-untested``: one name defined twice at module level (typically under ``if HAS_NUMBA:`` / ``else:``), once with ``@njit`` and once without.
* ``njit-parity-marker-untested``: an ``@njit`` def carrying ``# parity-test: <what is compared>``, the author's own declaration that a twin exists. The obligation is the one the marker states: a test reaches the function
  through ``py_func`` or ``assert_njit_twin_parity``.

A test file counts as evidence when its path has a ``tests``/``test`` component or is named ``test_*.py``/``*_test.py``/``conftest.py``. The evidence for ``py_func`` is an attribute ``X.py_func``, ``getattr(X, "py_func")``
or an ``assert_njit_twin_parity(X, ...)`` call. ``NUMBA_DISABLE_JIT`` alone is not parity: it runs one implementation.

Deliberately not reported: ``k = njit(...)(getattr(f, "py_func", f))`` (the twin IS the same code, parity holds by construction), a module whose ``njit`` is an identity fallback decorator, and any njit function with no twin signal.

Suppression: ``# parity-ok: <reason>`` on the def line, a decorator line or the line above (for a dispatcher: the def line or the flag test line).

Usage in a consumer's meta test::

    from py_ci_shared.njit_twin_untested import assert_njit_twin_untested

    def test_njit_twins_have_parity_tests():
        assert_njit_twin_untested("src", tests_root="tests", baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ParsedFile, iter_scopes, scan_python, walk
from ._core.call_names import call_name_or_none, func_name_or_none

__all__ = [
    "MARKER",
    "RULE_FLAG",
    "RULE_MARKER",
    "RULE_NAME",
    "RULE_REDEFINED",
    "RULES",
    "WAIVER",
    "assert_njit_twin_untested",
    "find_njit_twin_untested",
]

RULE_FLAG = "njit-flag-fallback-untested"
RULE_NAME = "njit-twin-name-untested"
RULE_REDEFINED = "njit-redefined-twin-untested"
RULE_MARKER = "njit-parity-marker-untested"
RULES = (RULE_FLAG, RULE_NAME, RULE_REDEFINED, RULE_MARKER)
MARKER = "parity-test:"
WAIVER = "parity-ok:"
SKIP_DIRS = frozenset({"_benchmarks", "legacy", "benchmarks", "profiling", "build", "docs"})
_JIT_NAMES = frozenset({"njit", "jit"})
_TWIN_WORDS = ("py", "python", "pure", "numpy", "reference", "ref", "fallback", "slow", "naive")
_KERNEL_WORDS = ("njit", "numba", "jit", "nb")
_FLAG = re.compile(r"^_*(?:(?:has|have|use|with|enable|is)_*numba\w*|numba_*(?:available|ok|enabled|installed|present|usable)\w*)$", re.IGNORECASE)
_PARITY_CALL = "assert_njit_twin_parity"
_Func = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _is_jit_decorator(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return func_name_or_none(target) in _JIT_NAMES


def _is_njit(fn: _Func) -> bool:
    return any(_is_jit_decorator(d) for d in fn.decorator_list)


def _norm(name: str) -> str:
    return name.strip("_").lower()


def _twin_names(njit_name: str) -> "set[str]":
    """Normalized names a plain twin of *njit_name* would carry."""
    stem = _norm(njit_name)
    out = {f"{stem}_{w}" for w in _TWIN_WORDS} | {f"{w}_{stem}" for w in _TWIN_WORDS}
    for word in _KERNEL_WORDS:
        if stem.endswith(f"_{word}"):
            out.add(stem[: -len(word) - 1])
    return out


def _marked(lines: "list[str]", numbers: "list[int]", marker: str) -> bool:
    for number in numbers:
        if 0 < number <= len(lines):
            line = lines[number - 1]
            if "#" in line and marker in line[line.index("#") :]:
                return True
    return False


def _marker_lines(fn: _Func) -> "list[int]":
    """The def line, each decorator line, and the line above the first of them."""
    first = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
    return [first - 1, fn.lineno] + [d.lineno for d in fn.decorator_list]


def _module_defs(body: "list[ast.stmt]") -> "list[_Func]":
    """Functions defined at module level, including under ``if``/``try``/``with`` arms (``if HAS_NUMBA: ... else: ...``)."""
    out: "list[_Func]" = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(stmt)
        elif isinstance(stmt, (ast.If, ast.With, ast.Try)):
            arms = [stmt.body, getattr(stmt, "orelse", [])] + [h.body for h in getattr(stmt, "handlers", [])] + [getattr(stmt, "finalbody", [])]
            for arm in arms:
                out.extend(_module_defs(arm))
    return out


def _kernel_assign_names(tree: ast.Module) -> "set[str]":
    """``k = njit(...)(f)`` and ``k = njit(f)`` bind a kernel with no ``def`` of its own."""
    out: "set[str]" = set()
    for node in walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        inner = node.value.func
        if _is_jit_decorator(inner) or call_name_or_none(node.value) in _JIT_NAMES:
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _names_in(node: ast.AST) -> "set[str]":
    out: "set[str]" = set()
    for sub in walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


@dataclass
class _Evidence:
    """What one test file proves: names it reaches through ``py_func``/the helper, and every name or string it mentions."""

    parity: "set[str]" = field(default_factory=set)
    mentioned: "set[str]" = field(default_factory=set)


def _py_func_targets(node: ast.AST) -> "set[str]":
    if isinstance(node, ast.Attribute) and node.attr == "py_func":
        return _names_in(node.value)
    if isinstance(node, ast.Call) and call_name_or_none(node) == "getattr" and len(node.args) >= 2:
        key = node.args[1]
        if isinstance(key, ast.Constant) and key.value == "py_func":
            return _names_in(node.args[0])
    if isinstance(node, ast.Call) and call_name_or_none(node) == _PARITY_CALL:
        return _names_in(node.args[0]) if node.args else set()
    return set()


def _evidence_of(tree: ast.Module) -> _Evidence:
    ev = _Evidence()
    for node in walk(tree):
        ev.parity |= _py_func_targets(node)
        if isinstance(node, ast.Name):
            ev.mentioned.add(node.id)
        elif isinstance(node, ast.Attribute):
            ev.mentioned.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 80:
            ev.mentioned.add(node.value)
        elif isinstance(node, ast.alias):
            ev.mentioned.add(node.name.split(".")[-1])
    return ev


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return bool({"tests", "test"} & set(parts[:-1])) or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


@dataclass(frozen=True)
class _Obligation:
    path: str
    line: int
    rule: str
    message: str
    subject: str
    kernels: "frozenset[str]" = frozenset()
    twin: str = ""
    flags: "frozenset[str]" = frozenset()


def _met(ob: _Obligation, ev: _Evidence) -> bool:
    reach = {ob.subject} | set(ob.kernels)
    if reach & ev.parity:
        return True
    seen = reach & ev.mentioned
    if ob.rule == RULE_NAME:
        return bool(seen) and ob.twin in ev.mentioned and ob.subject in ev.mentioned
    if ob.rule == RULE_FLAG:
        return bool(seen) and bool(ob.flags & ev.mentioned)
    if ob.rule == RULE_REDEFINED:
        return ob.subject in ev.mentioned and any(_FLAG.match(m) for m in ev.mentioned)
    return False


def _flag_obligations(parsed: ParsedFile, kernels: "set[str]", lines: "list[str]") -> "list[_Obligation]":
    out: "list[_Obligation]" = []
    for scope in iter_scopes(parsed.tree):
        fn = scope.node
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or _is_njit(fn):
            continue
        tests = [n.test for n in scope.nodes if isinstance(n, (ast.If, ast.IfExp))]
        flags = {name for test in tests for name in _names_in(test) if _FLAG.match(name)}
        called = {c for c in (call_name_or_none(n) for n in scope.nodes if isinstance(n, ast.Call)) if c in kernels}
        flag_lines = [t.lineno for t in tests if any(_FLAG.match(n) for n in _names_in(t))]
        if flags and called and not _marked(lines, [fn.lineno, *flag_lines], WAIVER):
            msg = f"`{fn.name}` branches on {', '.join(sorted(flags))} and calls the njit kernel {', '.join(sorted(called))}: the other arm is a second implementation, and no test compares the two"
            out.append(_Obligation(parsed.rel, fn.lineno, RULE_FLAG, msg, fn.name, frozenset(called), "", frozenset(flags)))
    return out


def _name_obligations(parsed: ParsedFile, lines: "list[str]") -> "list[_Obligation]":
    defs = _module_defs(parsed.tree.body)
    plain = {_norm(d.name): d for d in defs if not _is_njit(d)}
    out: "list[_Obligation]" = []
    for fn in (d for d in defs if _is_njit(d)):
        if _marked(lines, _marker_lines(fn), WAIVER):
            continue
        for twin_norm in sorted(_twin_names(fn.name) & set(plain)):
            twin = plain[twin_norm]
            if fn.name in _names_in(twin):  # a wrapper that calls the kernel is not a second implementation
                continue
            msg = f"njit `{fn.name}` has the plain twin `{twin.name}` in the same module, and no test compares the two"
            out.append(_Obligation(parsed.rel, fn.lineno, RULE_NAME, msg, fn.name, frozenset(), twin.name))
    return out


def _redefined_obligations(parsed: ParsedFile, lines: "list[str]") -> "list[_Obligation]":
    by_name: "dict[str, list[_Func]]" = {}
    for fn in _module_defs(parsed.tree.body):
        by_name.setdefault(fn.name, []).append(fn)
    out: "list[_Obligation]" = []
    for name, group in sorted(by_name.items()):
        jitted = [f for f in group if _is_njit(f)]
        if not jitted or len(jitted) == len(group) or _marked(lines, _marker_lines(jitted[0]), WAIVER):
            continue
        msg = f"`{name}` is defined both with @njit and as plain Python in one module, and no test compares the two"
        out.append(_Obligation(parsed.rel, jitted[0].lineno, RULE_REDEFINED, msg, name))
    return out


def _marker_obligations(parsed: ParsedFile, lines: "list[str]") -> "list[_Obligation]":
    out: "list[_Obligation]" = []
    for fn in _module_defs(parsed.tree.body):
        if _is_njit(fn) and _marked(lines, _marker_lines(fn), MARKER) and not _marked(lines, _marker_lines(fn), WAIVER):
            msg = f"njit `{fn.name}` is declared to have a pure-Python twin (`# {MARKER}`), and no test compares it through py_func or assert_njit_twin_parity"
            out.append(_Obligation(parsed.rel, fn.lineno, RULE_MARKER, msg, fn.name))
    return out


def _kernel_names(files: "list[ParsedFile]") -> "set[str]":
    names: "set[str]" = set()
    for parsed in files:
        names |= {fn.name for fn in _all_defs(parsed.tree) if _is_njit(fn)} | _kernel_assign_names(parsed.tree)
    return names


def _all_defs(tree: ast.Module) -> "list[_Func]":
    return [n for n in walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _obligations(files: "list[ParsedFile]") -> "list[_Obligation]":
    kernels = _kernel_names(files)
    out: "list[_Obligation]" = []
    for parsed in files:
        lines = parsed.source.splitlines()
        out += _flag_obligations(parsed, kernels, lines) + _name_obligations(parsed, lines)
        out += _redefined_obligations(parsed, lines) + _marker_obligations(parsed, lines)
    return out


def find_njit_twin_untested(
    root: Union[str, Path],
    *,
    tests_root: Optional[Union[str, Path]] = None,
    skip_dirs: "frozenset[str]" = SKIP_DIRS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every njit function with a pure-Python twin and no test that compares them, sorted by path and line.

    Test files are the ones under *tests_root* (when given) and, in *root* itself, the ones whose path says test. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and
    ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    tests = list(scan.files)
    if tests_root is not None:
        extra = scan_python(tests_root, min_files=0, use_git=use_git)
        extra.assert_ok(allow_unparsed=allow_unparsed)
        tests = [p for p in scan.files if _is_test_path(p.rel)] + list(extra.files)
    else:
        tests = [p for p in scan.files if _is_test_path(p.rel)]
    sources = [p for p in scan.files if not _is_test_path(p.rel) and not (skip_dirs & set(p.rel.split("/")))]
    evidence = [_evidence_of(p.tree) for p in tests]
    out = [Finding(o.path, o.line, o.rule, o.message) for o in _obligations(sources) if not any(_met(o, ev) for ev in evidence)]
    return sorted(out, key=lambda f: (f.path, f.line, f.rule, f.message))


def assert_njit_twin_untested(
    root: Union[str, Path],
    *,
    tests_root: Optional[Union[str, Path]] = None,
    skip_dirs: "frozenset[str]" = SKIP_DIRS,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any njit function with an untested pure-Python twin, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_njit_twin_untested(root, tests_root=tests_root, skip_dirs=skip_dirs, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = (
        "add a test that runs both on the same seeded input (`py_ci_shared.njit_twin_parity.assert_njit_twin_parity(kernel, inputs_factory)`, or toggle the numba flag and compare), "
        f"or mark the def `# {WAIVER} <reason>`"
    )
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="njit_twin_untested", refresh_command="PY_CI_SHARED_REFRESH=njit_twin_untested")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} njit function(s) with an untested pure-Python twin; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
