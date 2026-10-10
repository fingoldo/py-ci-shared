"""A cast/copy call inside a loop whose argument never changes across the whole enclosing function.

``np.ascontiguousarray(x, dtype=...)``, ``np.asarray(x, ...)``, ``np.array(x, ...)``, ``np.asfortranarray(x, ...)``
and ``x.astype(...)`` each do real work when ``x`` is not already in the target dtype/layout: a dtype conversion
or a C/Fortran-contiguity fix is a full copy of the array. Spelling the call on a loop's first iteration is
correct; spelling it again on iteration 2, 3, ... N for an ``x`` that is the SAME object every time (a function
parameter or a value assigned once before the loop and never touched again) repeats that copy for free -- the
2026-10-10 mlframe fuzz-profiling round found exactly this shape: a (N, K) ground-truth matrix recast to int64
once PER BASELINE in a loop that scores several baselines against the same matrix, 5-8x the necessary copies.

What is reported, per function: an assignment ``target = <cast_call>(x, ...)`` (or ``target = x.astype(...)``)
that sits inside a ``for``/``while`` loop, where ``x`` is a bare name that is NEVER an assignment target anywhere
else in the SAME function -- not via ``=``, ``+=``, a ``for``/``with`` binding, or a comprehension target. A name
that fails that test (reassigned somewhere in the function, including as a ``for``-loop unpacking target) is
treated as potentially loop-variant and NOT reported, even where it happens to hold the same value on every
loop iteration in practice -- proving that in general requires tracking the VALUES bound by an outer loop across
iterations, not just syntactic reassignment, which this gate does not attempt (see the known-gap note below).

Known gap (false negative): the exact mlframe case above is NOT caught by this rule as written, because the
cast's argument was bound by `for split_name, y, p in [("val", val_y, vp), ("test", test_y, tp)]:` -- a for-loop
target, which this gate conservatively treats as "may vary" even though ``val_y``/``test_y`` themselves never
change across the OUTER loop's iterations. Catching that shape needs tracking whether a for-loop's iterable is a
literal sequence of tuples whose elements are themselves outer-loop-invariant names; left as a follow-up rather
than risking false positives by guessing at the general case.

Known false-positive shape: a parameter that genuinely differs in dtype/layout across calls to the function (so
the cast is NOT redundant within a single call, just looks syntactically unconditional) is still correct to flag
HERE, since within one call of the function the parameter value is constant across the loop's iterations by
definition -- the cast only ever needs to run once per call regardless. Suppress a deliberate per-iteration
recompute (e.g. a defensive re-copy guarding against upstream aliasing) with ``# loop-invariant-ok: <reason>``.

Usage in a consumer's meta test::

    from py_ci_shared.loop_invariant_cast import assert_loop_invariant_cast

    def test_no_loop_invariant_casts():
        assert_loop_invariant_cast("src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["RULE", "assert_loop_invariant_cast", "find_loop_invariant_cast"]

RULE = "loop-invariant-cast"
SUPPRESSION = "# loop-invariant-ok:"

_CAST_FUNCS = frozenset(
    {
        "numpy.ascontiguousarray",
        "numpy.asfortranarray",
        "numpy.asarray",
        "numpy.array",
        "numpy.copy",
    }
)
_CAST_METHODS = frozenset({"astype", "copy"})
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _targets_of(node: ast.AST) -> Iterator[str]:
    """Every name a single assignment-like target binds, unpacking tuples/lists/starred targets."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            yield from _targets_of(elt)
    elif isinstance(node, ast.Starred):
        yield from _targets_of(node.value)


def _bound_names_of_node(node: ast.AST) -> Iterator[str]:
    """Every name a single statement/expression binds, one node kind at a time (helper for :func:`_reassigned_names`
    -- split out so that function's per-kind dispatch stays under the cyclomatic-complexity ceiling)."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _targets_of(target)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        yield from _targets_of(node.target)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        yield from _targets_of(node.target)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                yield from _targets_of(item.optional_vars)
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        for gen in node.generators:
            yield from _targets_of(gen.target)


def _reassigned_names(scope: ast.AST) -> set[str]:
    """Every name bound anywhere inside *scope* (a function) by ``=``, ``+=``, ``for``, ``with`` or a comprehension.

    Does not descend into nested function/lambda bodies -- a name reassigned only inside a nested closure does not
    make the OUTER occurrence loop-variant; nested defs are rare for the cast-call shape this gate targets and
    excluding them keeps the walk a single pass without a separate scope stack.
    """
    out: set[str] = set()
    for node in ast.walk(scope):
        out.update(_bound_names_of_node(node))
    return out


def _cast_argument(call: ast.Call, aliases: ImportAliases) -> Optional[ast.Name]:
    """The bare-name argument of a recognised cast call, or ``None`` when the call does not match / the
    argument is not a plain name (an expression has no single reassignment target to check)."""
    qualified = aliases.qualified_name(call)
    if qualified in _CAST_FUNCS:
        if call.args and isinstance(call.args[0], ast.Name):
            return call.args[0]
        return None
    if isinstance(call.func, ast.Attribute) and call.func.attr in _CAST_METHODS and isinstance(call.func.value, ast.Name):
        receiver = call.func.value
        # ``copy.copy(x)`` / ``cupy.copy(x)`` parses identically to ``obj.copy()`` at the attribute-access level,
        # but the receiver is a MODULE (the thing being copied is the call's ARGUMENT, not the receiver) -- an
        # import alias as receiver means this is a namespace function call, not an instance method, so the
        # receiver itself is not "the argument being recast" and must not be reported (mlframe
        # ``_pipeline_helpers_apply.py`` false-positive caught during this rule's own first real-corpus run:
        # ``import copy as _cp; ... _cp.copy(_v)`` flagged ``_cp`` instead of the actually-varying ``_v``).
        if aliases.is_imported(receiver.id):
            return None
        # A genuine instance ``.copy()`` (ndarray/dict/pandas) never takes a positional argument (pandas' only
        # parameter, ``deep``, is keyword-only by convention); a positional arg here means this is some other
        # ``.copy(x)``-shaped API this gate does not recognise, so skip rather than risk mis-attributing the
        # per-iteration-varying argument to the receiver the way the ``_cp.copy(_v)`` case did.
        if call.func.attr == "copy" and call.args:
            return None
        return receiver
    return None


def _enclosing_loop(node: ast.AST, parents: dict[int, ast.AST], func_id: int) -> Optional[ast.AST]:
    """The nearest ``for``/``while`` ancestor of *node* within the function at *func_id*, or ``None``."""
    cur = parents.get(id(node))
    while cur is not None and id(cur) != func_id:
        if isinstance(cur, _LOOPS):
            return cur
        cur = parents.get(id(cur))
    return None


def _suppressed(lines: list[str], line: int) -> bool:
    if 1 <= line <= len(lines):
        return SUPPRESSION in lines[line - 1]
    return False


# Calls known to mutate their argument in place rather than return a new object. Narrow and evidence-driven
# (``.attr`` spelling only, matched regardless of receiver): widen only on a confirmed false positive, the same
# way this set grew from zero after this rule's own second real-corpus run (``rng.shuffle(rep_shuffled)`` /
# ``rng_m.shuffle(shuffled)`` in mlframe's dynamic-cluster-discovery null-permutation kernels -- a fresh
# ``.copy()`` per loop iteration specifically so each iteration's in-place ``shuffle()`` has its own array).
_INPLACE_MUTATOR_METHODS = frozenset({"shuffle"})


def _mutated_in_loop(name: str, loop: ast.AST) -> bool:
    """``name[...] = ...`` / ``name[...] op= ...``, or ``name`` passed to a known in-place mutator (``.shuffle()``),
    anywhere inside *loop* -- the array is used as PRIVATE MUTABLE SCRATCH (e.g. an in-place Fisher-Yates shuffle
    of a per-iteration ``.copy()``), not a redundant re-cast. This is the false positive this rule's own first
    real-corpus run turned up: several mlframe permutation-test kernels copy ``classes_y`` once per ``prange``
    iteration specifically so each iteration can shuffle its own copy in place without racing the others --
    hoisting that copy out of the loop would share one mutable array across iterations and corrupt the parallel
    shuffle, not just fail to speed anything up.
    """
    for node in ast.walk(loop):
        target = None
        if isinstance(node, (ast.Assign,)):
            for t in node.targets:
                if isinstance(t, ast.Subscript):
                    target = t
                    break
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
            target = node.target
        if target is not None and isinstance(target.value, ast.Name) and target.value.id == name:
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _INPLACE_MUTATOR_METHODS
            and any(isinstance(a, ast.Name) and a.id == name for a in node.args)
        ):
            return True
    return False


def _function_findings(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], rel: str, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(func):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    reassigned = _reassigned_names(func)
    out: list[Finding] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        arg = _cast_argument(call, aliases)
        if arg is None or arg.id in reassigned:
            continue
        loop = _enclosing_loop(node, parents, id(func))
        if loop is None:
            continue
        if _mutated_in_loop(node.targets[0].id, loop):
            continue
        if _suppressed(lines, node.lineno):
            continue
        shown = ast.unparse(call)
        shown = shown if len(shown) <= 100 else shown[:97] + "..."
        out.append(
            Finding(
                rel,
                node.lineno,
                RULE,
                f"'{arg.id}' is never reassigned in this function but is recast on every loop iteration: {shown}",
            )
        )
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for func in (n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS)):
        out.extend(_function_findings(func, rel, aliases, lines))
    return out


def find_loop_invariant_cast(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments (matched against the POSIX relative path) of files to skip. Raises
    ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot
    be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
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


def assert_loop_invariant_cast(
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
    found = find_loop_invariant_cast(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = "hoist the cast above the loop (compute it once); or `# loop-invariant-ok: <reason>` for a deliberate per-iteration re-copy"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="loop_invariant_cast", refresh_command="PY_CI_SHARED_REFRESH=loop_invariant_cast")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} loop-invariant-cast finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
