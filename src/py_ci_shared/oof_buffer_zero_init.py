"""An out-of-fold buffer initialised with zeros/empty and filled per fold from a splitter that may skip rows.

``oof = np.zeros(n)`` then ``oof[va] = ...`` inside ``for tr, va in cv.split(X)`` is only right when every row is
validated exactly once. With a ``TimeSeriesSplit`` (the first block is never a validation fold), a group holdout or
any splitter handed in by the caller, the unvalidated rows keep ``0.0`` and a downstream model trains on them as if
they were predictions; with ``np.empty`` it trains on uninitialised memory. mlframe fixed it by hand in 324b0f8f3,
27f059ab4, 740c5a330, deb69ee3f and 4cccaff05.

Refined rule (the naive scan had 100% false positives on mlframe because it iterated locally built full-partition
``KFold`` objects): report only when (1) the buffer, named like ``oof``/``out_of_fold``/``holdout``, is ``np.empty``/``np.zeros``/``np.ones`` (also ``*_like``),
(2) it is filled by subscript assignment inside a loop over ``<splitter>.split(...)`` or over a function parameter
or attribute, where the splitter is NOT a ``KFold``/``StratifiedKFold``/``RepeatedKFold``/``LeaveOneOut``
constructed in the same function, and (3) the function never checks coverage (no ``isnan``/``coverage``/``covered``
mention) and the buffer is not later given ``.fill(...)``. ``np.empty`` is reported whatever the checks. The fix is
``np.full(shape, np.nan)``. Suppress with ``# oof-zero-ok: <reason>`` on the buffer line.

Usage in a consumer's meta test::

    from py_ci_shared.oof_buffer_zero_init import assert_oof_buffer_zero_init

    def test_oof_buffer_zero_init():
        assert_oof_buffer_zero_init("src", baseline_path="tests/baselines/oof_buffer_zero_init.json")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "SUPPRESSION", "assert_oof_buffer_zero_init", "find_oof_buffer_zero_init"]

RULE = "oof-buffer-zero-init"
SUPPRESSION = "# oof-zero-ok:"

_BUFFER_CTORS = frozenset({"zeros", "empty", "ones", "zeros_like", "empty_like", "ones_like"})
_FULL_PARTITION = frozenset({"KFold", "StratifiedKFold", "RepeatedKFold", "RepeatedStratifiedKFold", "LeaveOneOut"})
_UNWRAP = frozenset({"enumerate", "list", "tuple", "iter"})
_SPLITTERISH = re.compile(r"cv|split|fold", re.IGNORECASE)
_OOF_NAME = re.compile(r"oof|out_of_fold|holdout|hold_out", re.IGNORECASE)
_COVERAGE = re.compile(r"isnan|isfinite|coverage|covered|\.fill\(", re.IGNORECASE)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        if isinstance(node.func, ast.Name):
            return node.func.id
    return None


def _buffers(func: ast.AST) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for node in shared_walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = _call_name(node.value)
            if name in _BUFFER_CTORS and _OOF_NAME.search(node.targets[0].id):
                out[node.targets[0].id] = (node.lineno, name or "")
    return out


def _local_partition_names(func: ast.AST) -> set[str]:
    return {
        n.targets[0].id
        for n in shared_walk(func)
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and _call_name(n.value) in _FULL_PARTITION
    }


def _splitter_of(iterable: ast.expr) -> ast.expr:
    node = iterable
    while isinstance(node, ast.Call) and _call_name(node) in _UNWRAP and node.args:
        node = node.args[0]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"split", "get_n_splits"}:
        return node.func.value
    return node


def _is_fold_loop(loop: ast.AST, params: set[str]) -> bool:
    """A loop over ``<splitter>.split(...)`` or over a splitter-like parameter/attribute, unpacking (train, valid)."""
    assert isinstance(loop, (ast.For, ast.AsyncFor))
    node = loop.iter
    while isinstance(node, ast.Call) and _call_name(node) in _UNWRAP and node.args:
        node = node.args[0]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split":
        return True
    if isinstance(node, ast.Name):
        return node.id in params or bool(_SPLITTERISH.search(node.id))
    return isinstance(node, ast.Attribute) and bool(_SPLITTERISH.search(node.attr))


def _is_local_full_partition(splitter: ast.expr, local: set[str]) -> bool:
    if _call_name(splitter) in _FULL_PARTITION:
        return True
    return isinstance(splitter, ast.Name) and splitter.id in local


def _writes_subscript(loop: ast.AST, name: str) -> bool:
    for node in shared_walk(loop):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AugAssign) else []
        for target in targets:
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == name:
                return True
    return False


def _findings_in(tree: ast.Module, rel: str, source: str, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[int] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        buffers = _buffers(func)
        if not buffers:
            continue
        local = _local_partition_names(func)
        params = {a.arg for a in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)}
        covered = bool(_COVERAGE.search(ast.get_source_segment(source, func) or ""))
        for loop in (n for n in shared_walk(func) if isinstance(n, (ast.For, ast.AsyncFor))):
            if not _is_fold_loop(loop, params) or _is_local_full_partition(_splitter_of(loop.iter), local):
                continue
            for name, (line, ctor) in buffers.items():
                if line in seen or not _writes_subscript(loop, name):
                    continue
                if covered and not ctor.startswith("empty"):
                    continue
                seen.add(line)
                if 1 <= line <= len(lines) and SUPPRESSION in lines[line - 1]:
                    continue
                out.append(
                    Finding(
                        rel,
                        line,
                        RULE,
                        f"'{name}' starts as np.{ctor} and is filled per fold from a splitter that may not validate every row "
                        f"(loop line {loop.lineno}); initialise with np.full(shape, np.nan) so unvalidated rows stay NaN",
                    )
                )
    return out


def find_oof_buffer_zero_init(
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
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source, parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_oof_buffer_zero_init(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_oof_buffer_zero_init(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "initialise the buffer with np.full(shape, np.nan), or assert coverage, or `# oof-zero-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="oof_buffer_zero_init", refresh_command="PY_CI_SHARED_REFRESH=oof_buffer_zero_init")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} oof-buffer-zero-init finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
