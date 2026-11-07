"""A function that takes its own seed but seeds a callee with an int literal: the caller's seed never arrives.

``def split(X, y, random_state=None): return train_test_split(X, y, random_state=42)`` accepts the knob, documents it,
and discards it: every call is the same split, whatever the caller passed, so a seed sweep (the standard way to measure
variance across runs) measures nothing, a "different seed" ensemble member is a clone of the first, and the confidence
interval built from those runs is exactly zero wide. The reverse bug, an unseeded callee, is caught by the failure
being visible; this one is silent because every run is perfectly reproducible, just not with the caller's seed.

Reported, inside a function that has its OWN ``random_state``/``seed``/``rng`` parameter (seed keywords: random_state, seed, random_seed, rng, rand_state, seed_value; a local name assigned once to an int counts as a literal) (an enclosing function's
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
from ._core.unparse import unparse_stable
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, child_nodes, nodes_of, scan_python

__all__ = ["DEFAULT_EXCLUDE_FRAGMENTS", "RULE", "assert_hardcoded_seed_in_library", "find_hardcoded_seed_in_library"]

RULE = "hardcoded-seed-in-library"
SUPPRESSION = "# seed-ok:"
DEFAULT_EXCLUDE_FRAGMENTS = ("tests/", "_benchmarks/")

_SEED_PARAM_RE = re.compile(r"(?:^|_)(?:seed|random_state|rng|rs)s?(?:$|_)")
_SEED_KEYWORDS = frozenset({"random_state", "seed", "random_seed", "rng", "rand_state", "seed_value"})
_SEEDERS = frozenset(
    {
        "numpy.random.default_rng",
        "numpy.random.RandomState",
        "numpy.random.seed",
        "numpy.random.SeedSequence",
        "numpy.random.PCG64",
        "numpy.random.PCG64DXSM",
        "numpy.random.MT19937",
        "numpy.random.Philox",
        "numpy.random.SFC64",
        "random.seed",
        "random.Random",
        "torch.manual_seed",
        "torch.random.manual_seed",
        "torch.cuda.manual_seed",
        "torch.cuda.manual_seed_all",
        "tensorflow.random.set_seed",
        "tensorflow.set_random_seed",
        "keras.utils.set_random_seed",
        "tensorflow.keras.utils.set_random_seed",
        "transformers.set_seed",
        "pytorch_lightning.seed_everything",
        "lightning.seed_everything",
        "lightning.pytorch.seed_everything",
    }
)
_SEED_METHODS = frozenset({"manual_seed"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _int_value(node: ast.AST, consts: "dict[str, int]") -> Optional[int]:
    """The int *node* evaluates to when it is a literal, a folded ``+ - * // %`` of literals, or a module constant bound once to one."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, int) and not isinstance(node.value, bool) else None
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _int_value(node.operand, consts)
        return None if inner is None else (-inner if isinstance(node.op, ast.USub) else inner)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)):
        left, right = _int_value(node.left, consts), _int_value(node.right, consts)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return None if right == 0 else (left // right if isinstance(node.op, ast.FloorDiv) else left % right)
    return None


def _module_int_constants(tree: ast.Module) -> "dict[str, int]":
    """Module-level names assigned exactly once, to an int (``SEED = 42``)."""
    counts: dict[str, int] = {}
    values: dict[str, ast.expr] = {}
    for stmt in tree.body:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets, value = list(stmt.targets), stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                counts[target.id] = counts.get(target.id, 0) + 1
                values[target.id] = value
    out: dict[str, int] = {}
    for name, value in values.items():
        if counts[name] == 1:
            folded = _int_value(value, out)
            if folded is not None:
                out[name] = folded
    return out


def _suppressed(lines: list[str], node: ast.AST) -> bool:
    lo = getattr(node, "lineno", 0)
    hi = getattr(node, "end_lineno", None) or lo
    return any(SUPPRESSION in lines[i] for i in range(max(lo - 1, 0), min(hi, len(lines))))


def _own_params(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> set[str]:
    a = func.args
    return {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs) if _SEED_PARAM_RE.search(p.arg)}


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


def _follows_seed_return(child: ast.AST, parent: ast.AST, params: set[str]) -> bool:
    """*child* sits in a statement list after ``if <seed> is not None: ... return``: the fall-through is the no-seed default."""
    for field in ("body", "orelse", "finalbody"):
        block = getattr(parent, field, None)
        if not isinstance(block, list):
            continue
        for index, stmt in enumerate(block):
            if stmt is child:
                for earlier in block[:index]:
                    if (
                        isinstance(earlier, ast.If)
                        and _tests_param(earlier.test, params) == "set"
                        and earlier.body
                        and isinstance(earlier.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))
                    ):
                        return True
    return False


def _is_default_for_missing_seed(call: ast.Call, parents: dict[int, ast.AST], params: set[str]) -> bool:
    """A literal seed that only applies when the caller supplied none (``if rng is None: rng = default_rng(0)``,
    ``rng or default_rng(0)``, ``x if seed is not None else default_rng(0)``) does not drop the caller's seed."""
    child: ast.AST = call
    parent = parents.get(id(child))
    while parent is not None:
        if _follows_seed_return(child, parent, params):
            return True
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


def _seed_description(call: ast.Call, aliases: ImportAliases, consts: "Optional[dict[str, int]]" = None) -> str:
    """How *call* hard-codes a seed (``seed=0``, ``default_rng(0)``, ``**{"random_state": 7}``), or an empty string when it does not."""
    known = consts or {}
    what = ""
    for keyword in call.keywords:
        if keyword.arg in _SEED_KEYWORDS and _int_value(keyword.value, known) is not None:
            what = f"{keyword.arg}={unparse_stable(keyword.value)}"
        elif keyword.arg is None and isinstance(keyword.value, ast.Dict):
            for key, val in zip(keyword.value.keys, keyword.value.values):
                if isinstance(key, ast.Constant) and key.value in _SEED_KEYWORDS and _int_value(val, known) is not None:
                    what = f"{key.value}={unparse_stable(val)}"
    seeds_by_call = aliases.qualified_name(call) in _SEEDERS or (isinstance(call.func, ast.Attribute) and call.func.attr in _SEED_METHODS)
    if not what and seeds_by_call and call.args and _int_value(call.args[0], known) is not None:
        what = f"{unparse_stable(call.func)}({unparse_stable(call.args[0])})"
    return what


def _scan_local_bindings(func: ast.AST) -> "tuple[dict[str, int], dict[str, ast.expr]]":
    """``(rebind counts, single-assignment values)`` of the names *func* binds outside nested scopes; a rebind by anything but a plain assignment counts double."""
    counts: dict[str, int] = {}
    values: dict[str, ast.expr] = {}
    stack = list(child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    counts[t.id] = counts.get(t.id, 0) + 1
                    values[t.id] = node.value
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.With)):
            for sub in nodes_of(node.target if hasattr(node, "target") else node, ast.Name):
                if isinstance(sub.ctx, ast.Store):
                    counts[sub.id] = counts.get(sub.id, 0) + 2  # rebound by something we do not fold: never constant
        stack.extend(child_nodes(node))
    return counts, values


def _local_int_constants(func: ast.AST, inherited: "Optional[dict[str, int]]") -> "dict[str, int]":
    """*inherited* plus the names *func* itself assigns exactly once, to an int (``s = 42`` then ``default_rng(s)``)."""
    counts, values = _scan_local_bindings(func)
    params = {a.arg for a in nodes_of(func.args, ast.arg)} if isinstance(func, _FUNCTIONS) else set()
    out = {k: v for k, v in (inherited or {}).items() if k not in counts and k not in params}
    for name, value in values.items():
        if counts[name] == 1 and name not in params:
            folded = _int_value(value, out)
            if folded is not None:
                out[name] = folded
    return out


def _own_calls(func: ast.AST) -> Iterator[tuple[ast.Call, dict[int, ast.AST]]]:
    """Each call of *func* outside nested functions, classes and lambdas, with the parent map built so far (the call's own chain is complete)."""
    parents: dict[int, ast.AST] = {}
    stack = list(child_nodes(func))
    for child in stack:
        parents[id(child)] = func
    while stack:
        node = stack.pop()
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            continue  # judged as their own scope (a lambda is part of the function that writes it)
        for child in child_nodes(node):
            parents[id(child)] = node
            stack.append(child)
        if isinstance(node, ast.Call):
            yield node, parents


def _check_seeds(
    func: ast.AST, qualname: str, params: list[str], rel: str, aliases: ImportAliases, lines: list[str], consts: "Optional[dict[str, int]]" = None
) -> list[Finding]:
    """The findings of one function that takes its own seed parameter(s) *params*."""
    out: list[Finding] = []
    consts = _local_int_constants(func, consts)
    for node, parents in _own_calls(func):
        if _is_default_for_missing_seed(node, parents, set(params)):
            continue
        what = _seed_description(node, aliases, consts)
        if what and not _suppressed(lines, node):
            out.append(Finding(rel, node.lineno, RULE, f"{qualname}: {what} hard-codes a seed in a function that takes its own {'/'.join(params)}"))
    return out


def _visit_seed_scopes(
    node: ast.AST, prefix: str, inherited: frozenset[str], rel: str, aliases: ImportAliases, lines: list[str], consts: "Optional[dict[str, int]]" = None
) -> list[Finding]:
    """Walk classes and functions below *node*, checking each function that has (or inherits) a seed parameter."""
    out: list[Finding] = []
    for child in child_nodes(node):
        if isinstance(child, ast.ClassDef):
            out.extend(_visit_seed_scopes(child, f"{prefix}{child.name}.", frozenset(), rel, aliases, lines, consts))
        elif isinstance(child, _FUNCTIONS):
            params = frozenset(_own_params(child)) | inherited
            qualname = f"{prefix}{child.name}"
            if params:
                out.extend(_check_seeds(child, qualname, sorted(params), rel, aliases, lines, consts))
            out.extend(_visit_seed_scopes(child, f"{qualname}.<locals>.", params, rel, aliases, lines, consts))
        else:
            out.extend(_visit_seed_scopes(child, prefix, inherited, rel, aliases, lines, consts))
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    return _visit_seed_scopes(tree, "", frozenset(), rel, aliases, lines or [], _module_int_constants(tree))


def find_hardcoded_seed_in_library(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = DEFAULT_EXCLUDE_FRAGMENTS,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments matched against the POSIX relative path (``tests`` matches ``x.py`` and
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
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree, scoped=True), parsed.source.splitlines()))
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
