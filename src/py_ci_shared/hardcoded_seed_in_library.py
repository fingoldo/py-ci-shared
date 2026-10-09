"""A function that takes its own seed but seeds a callee with an int literal: the caller's seed never arrives.

``def split(X, y, random_state=None): return train_test_split(X, y, random_state=42)`` accepts the knob, documents it,
and discards it: every call is the same split, whatever the caller passed, so a seed sweep (the standard way to measure
variance across runs) measures nothing, a "different seed" ensemble member is a clone of the first, and the confidence
interval built from those runs is exactly zero wide. The reverse bug, an unseeded callee, is caught by the failure
being visible; this one is silent because every run is perfectly reproducible, just not with the caller's seed.

Reported, inside a function that has its OWN ``random_state``/``seed``/``rng`` parameter (an enclosing function's
parameter counts for a nested function):

* a call with a ``random_state=<int literal>`` or ``seed=<int literal>`` keyword;
* ``np.random.default_rng(<int>)``, ``np.random.RandomState(<int>)``, ``np.random.seed(<int>)``, ``random.seed(<int>)``,
  ``torch.manual_seed(<int>)`` (aliases and ``from numpy.random import default_rng`` included).

A literal that only applies when the caller supplied no seed is not reported: ``if rng is None: rng = default_rng(0)``,
``rng or default_rng(0)`` and ``default_rng(0) if seed is None else default_rng(seed)`` all keep the caller's seed.
A function without such a parameter may hard-code a seed (that is a decision, not a dropped argument), so nothing is
reported there. ``tests/`` and ``_benchmarks/`` paths are skipped by default (``exclude``): a test pins its own seed
on purpose. Known false-positive shape: a deliberately fixed seed for an unrelated purpose inside a seeded function (a
fixed validation split next to a caller-seeded model); write ``# seed-ok: <reason>`` on the statement.

Usage in a consumer's meta test::

    from py_ci_shared.hardcoded_seed_in_library import assert_hardcoded_seed_in_library

    def test_no_hardcoded_seed_in_library():
        assert_hardcoded_seed_in_library("src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["DEFAULT_EXCLUDE_FRAGMENTS", "RULE", "assert_hardcoded_seed_in_library", "find_hardcoded_seed_in_library"]

RULE = "hardcoded-seed-in-library"
SUPPRESSION = "# seed-ok:"
DEFAULT_EXCLUDE_FRAGMENTS = ("tests/", "_benchmarks/")

_SEED_PARAMS = frozenset({"random_state", "seed", "rng"})
_SEED_KEYWORDS = frozenset({"random_state", "seed"})
_SEEDERS = frozenset({"numpy.random.default_rng", "numpy.random.RandomState", "numpy.random.seed", "random.seed", "torch.manual_seed", "random.Random"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _int_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _suppressed(lines: list[str], node: ast.AST) -> bool:
    lo = getattr(node, "lineno", 0)
    hi = getattr(node, "end_lineno", None) or lo
    return any(SUPPRESSION in lines[i] for i in range(max(lo - 1, 0), min(hi, len(lines))))


def _own_params(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> set[str]:
    a = func.args
    return {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs) if p.arg in _SEED_PARAMS}


def _tests_param(test: ast.AST, params: set[str]) -> Optional[str]:
    """``"unset"`` when *test* is true for an absent seed (``p is None``, ``not p``), ``"set"`` for the negation, else None."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.left, ast.Name) and test.left.id in params:
        right = test.comparators[0]
        if isinstance(right, ast.Constant) and right.value is None:
            return "unset" if isinstance(test.ops[0], ast.Is) else "set" if isinstance(test.ops[0], ast.IsNot) else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name) and test.operand.id in params:
        return "unset"
    if isinstance(test, ast.Name) and test.id in params:
        return "set"
    return None


def _is_default_for_missing_seed(call: ast.Call, parents: dict[int, ast.AST], params: set[str]) -> bool:
    """A literal seed that only applies when the caller supplied none (``if rng is None: rng = default_rng(0)``,
    ``rng or default_rng(0)``, ``x if seed is not None else default_rng(0)``) does not drop the caller's seed."""
    child: ast.AST = call
    parent = parents.get(id(child))
    while parent is not None:
        if isinstance(parent, (ast.If, ast.IfExp)):
            state = _tests_param(parent.test, params)
            in_body = any(child is s for s in (parent.body if isinstance(parent.body, list) else [parent.body]))
            in_else = any(child is s for s in (parent.orelse if isinstance(parent.orelse, list) else [parent.orelse]))
            if (state == "unset" and in_body) or (state == "set" and in_else):
                return True
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or) and parent.values and child is not parent.values[0]:
            first = parent.values[0]
            if isinstance(first, ast.Name) and first.id in params:
                return True
        child, parent = parent, parents.get(id(parent))
    return False


def _seed_description(call: ast.Call, aliases: ImportAliases) -> str:
    """How *call* hard-codes a seed (``seed=0``, ``default_rng(0)``), or an empty string when it does not."""
    what = ""
    for keyword in call.keywords:
        if keyword.arg in _SEED_KEYWORDS and _int_literal(keyword.value):
            what = f"{keyword.arg}={ast.unparse(keyword.value)}"
    if not what and (aliases.qualified_name(call) in _SEEDERS) and call.args and _int_literal(call.args[0]):
        what = f"{ast.unparse(call.func)}({ast.unparse(call.args[0])})"
    return what


def _own_calls(func: ast.AST) -> Iterator[tuple[ast.Call, dict[int, ast.AST]]]:
    """Each call of *func* outside nested functions, classes and lambdas, with the parent map built so far (the call's own chain is complete)."""
    parents: dict[int, ast.AST] = {}
    stack = list(ast.iter_child_nodes(func))
    for child in stack:
        parents[id(child)] = func
    while stack:
        node = stack.pop()
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
            continue  # judged as their own scope
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            stack.append(child)
        if isinstance(node, ast.Call):
            yield node, parents


def _check_seeds(func: ast.AST, qualname: str, params: list[str], rel: str, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    """The findings of one function that takes its own seed parameter(s) *params*."""
    out: list[Finding] = []
    for node, parents in _own_calls(func):
        if _is_default_for_missing_seed(node, parents, set(params)):
            continue
        what = _seed_description(node, aliases)
        if what and not _suppressed(lines, node):
            out.append(Finding(rel, node.lineno, RULE, f"{qualname}: {what} hard-codes a seed in a function that takes its own {'/'.join(params)}"))
    return out


def _visit_seed_scopes(node: ast.AST, prefix: str, inherited: frozenset[str], rel: str, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    """Walk classes and functions below *node*, checking each function that has (or inherits) a seed parameter."""
    out: list[Finding] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            out.extend(_visit_seed_scopes(child, f"{prefix}{child.name}.", frozenset(), rel, aliases, lines))
        elif isinstance(child, _FUNCTIONS):
            params = frozenset(_own_params(child)) | inherited
            qualname = f"{prefix}{child.name}"
            if params:
                out.extend(_check_seeds(child, qualname, sorted(params), rel, aliases, lines))
            out.extend(_visit_seed_scopes(child, f"{qualname}.<locals>.", params, rel, aliases, lines))
        else:
            out.extend(_visit_seed_scopes(child, prefix, inherited, rel, aliases, lines))
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    return _visit_seed_scopes(tree, "", frozenset(), rel, aliases, lines or [])


def find_hardcoded_seed_in_library(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = DEFAULT_EXCLUDE_FRAGMENTS,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments matched against the POSIX relative path (``tests/`` matches ``tests/x.py`` and
    ``pkg/tests/x.py``, not ``contests/x.py``); the default skips ``tests/`` and ``_benchmarks/``. Raises ``EmptyScanError``
    when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(f"/{fragment}" in f"/{parsed.rel}" for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_hardcoded_seed_in_library(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = DEFAULT_EXCLUDE_FRAGMENTS,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_hardcoded_seed_in_library(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = "pass the function's own seed on (random_state=random_state), or `# seed-ok: <reason>` for a deliberately fixed one"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="hardcoded_seed_in_library", refresh_command="PY_CI_SHARED_REFRESH=hardcoded_seed_in_library")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} hardcoded-seed-in-library finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
