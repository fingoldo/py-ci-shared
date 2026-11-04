"""A function that mutates a DataFrame parameter in place without copying it first.

``X["a"] = ...``, ``X.columns = ...`` or ``X.drop(columns=..., inplace=True)`` on a frame the caller handed in
silently rewrites the caller's data: renamed columns, filled NaN, dropped columns. A second call or a sibling model
then sees a changed input and train/predict column parity breaks. mlframe hand-fixed it in a37146d65 ("stop renaming
the caller's frame" in fitted selectors' transform), 676e5ed36 and 6945ad7c0, and keeps a helper
(``_restore_caller_frame_columns``) that exists only to undo a mutation made by ``fit``.

ADVISORY by design: deliberate in-place cleaners are a legitimate convention in some code bases, so
``assert_caller_frame_mutation`` WARNS (``CallerFrameMutationWarning``) for each finding by default, like
``polars_null_equality``; pass ``advisory=False`` to fail (or, with *baseline_path*, to ratchet) instead. A broken
walk (too few files, an unparsable file) fails either way.

What is reported: inside a function, a parameter whose name matches *frame_params* (fnmatch patterns; default
``X``, ``df``, ``data``, ``frame``, ``X_*``, ``*_df``) and that the function never rebinds (no ``X = X.copy()``),
mutated by

* a subscript store, augmented store or ``del`` on it: ``X["a"] = v``, ``X.loc[m, "a"] = v``, ``del X["a"]``;
* an assignment to its ``columns`` or ``index`` attribute;
* a method call with ``inplace=True`` on it or on a part of it: ``X.drop(columns=c, inplace=True)``,
  ``X["a"].fillna(0, inplace=True)``.

Not reported: a function whose name contains one of *exempt_name_parts* (``inplace``, ``in_place``, ``mutate``) or
whose docstring contains one of *docstring_phrases* (``in place``, ``in-place``, ``inplace``, ``mutates``,
``modifies``); ``self``/``cls``; a parameter rebound anywhere in the function. A reviewed exception is
``# frame-mutation-ok: <reason>`` on the mutating line, or an *allowed* entry ``{"path::function": reason}``.

Usage in a consumer's meta test::

    from py_ci_shared.caller_frame_mutation import assert_caller_frame_mutation

    def test_caller_frame_mutation_advisory():
        assert_caller_frame_mutation("src")
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Collection, Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = [
    "DEFAULT_DOCSTRING_PHRASES",
    "DEFAULT_EXEMPT_NAME_PARTS",
    "DEFAULT_FRAME_PARAMS",
    "RULE",
    "SUPPRESSION",
    "CallerFrameMutationWarning",
    "assert_caller_frame_mutation",
    "find_caller_frame_mutation",
]

RULE = "caller-frame-mutation"
SUPPRESSION = "# frame-mutation-ok:"

DEFAULT_FRAME_PARAMS = ("X", "df", "data", "frame", "X_*", "*_df")
DEFAULT_EXEMPT_NAME_PARTS = ("inplace", "in_place", "mutate")
DEFAULT_DOCSTRING_PHRASES = ("in place", "in-place", "inplace", "mutates", "modifies")
_INDEXERS = frozenset({"loc", "iloc", "at", "iat"})
_MUTATED_ATTRIBUTES = frozenset({"columns", "index"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class CallerFrameMutationWarning(UserWarning):
    """One function mutating a frame it was handed, reported in advisory mode."""


def _params(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> list[str]:
    a = func.args
    names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return [n for n in names if n not in ("self", "cls")]


def _root_name(node: ast.AST) -> Optional[str]:
    """The bare name at the base of an attribute/subscript chain (``X["a"].str`` -> ``X``)."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _stored_subscript_base(target: ast.AST) -> Optional[str]:
    """``X`` for ``X[...]`` and ``X.loc[...]``-style stores, else ``None``."""
    if not isinstance(target, ast.Subscript):
        return None
    base = target.value
    if isinstance(base, ast.Attribute) and base.attr in _INDEXERS:
        base = base.value
    return base.id if isinstance(base, ast.Name) else None


def _mutations(node: ast.AST) -> list[tuple[str, int, str]]:
    """``(parameter, line, shape)`` for each mutation the single node performs."""
    out: list[tuple[str, int, str]] = []
    line: int = getattr(node, "lineno", 0)
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Delete):
        for t in node.targets:
            name = _stored_subscript_base(t)
            if name is not None:
                out.append((name, line, "del"))
    for target in targets:
        out.extend(_target_mutation(target, line))
    if isinstance(node, ast.Call):
        out.extend(_inplace_mutation(node, line))
    return out


def _target_mutation(target: ast.expr, line: int) -> list[tuple[str, int, str]]:
    name = _stored_subscript_base(target)
    if name is not None:
        return [(name, line, "item assignment")]
    if isinstance(target, ast.Attribute) and target.attr in _MUTATED_ATTRIBUTES and isinstance(target.value, ast.Name):
        return [(target.value.id, line, f".{target.attr} assignment")]
    return []


def _inplace_mutation(node: ast.Call, line: int) -> list[tuple[str, int, str]]:
    if not isinstance(node.func, ast.Attribute):
        return []
    flagged = any(kw.arg == "inplace" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords)
    root = _root_name(node.func.value) if flagged else None
    return [(root, line, f".{node.func.attr}(inplace=True)")] if root is not None else []


def _rebound(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store) for n in shared_walk(func))


def _exempt(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], name_parts: Collection[str], phrases: Collection[str]) -> bool:
    lowered = func.name.lower()
    if any(part in lowered for part in name_parts):
        return True
    doc = (ast.get_docstring(func) or "").lower()
    return any(phrase in doc for phrase in phrases)


def _findings_in(
    tree: ast.Module,
    rel: str,
    lines: list[str],
    frame_params: Collection[str],
    allowed: Mapping[str, str],
    name_parts: Collection[str],
    phrases: Collection[str],
) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for func in (n for n in shared_walk(tree) if isinstance(n, _FUNCTIONS)):
        if f"{rel}::{func.name}" in allowed or _exempt(func, name_parts, phrases):
            continue
        mine = {p for p in _params(func) if any(fnmatchcase(p, pat) for pat in frame_params) and not _rebound(func, p)}
        if not mine:
            continue
        for node in shared_walk(func):
            for param, line, shape in _mutations(node):
                if param not in mine or (line, param) in seen:
                    continue
                seen.add((line, param))
                if 1 <= line <= len(lines) and SUPPRESSION in lines[line - 1]:
                    continue
                out.append(
                    Finding(
                        rel,
                        line,
                        RULE,
                        f"'{func.name}' mutates its parameter '{param}' in place ({shape}) without copying it first: the caller's frame changes",
                    )
                )
    return out


def find_caller_frame_mutation(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    frame_params: Collection[str] = DEFAULT_FRAME_PARAMS,
    allowed: Optional[Mapping[str, str]] = None,
    exempt_name_parts: Collection[str] = DEFAULT_EXEMPT_NAME_PARTS,
    docstring_phrases: Collection[str] = DEFAULT_DOCSTRING_PHRASES,
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines(), frame_params, allowed or {}, exempt_name_parts, docstring_phrases))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_caller_frame_mutation(
    root: Union[str, Path],
    *,
    advisory: bool = True,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    frame_params: Collection[str] = DEFAULT_FRAME_PARAMS,
    allowed: Optional[Mapping[str, str]] = None,
    exempt_name_parts: Collection[str] = DEFAULT_EXEMPT_NAME_PARTS,
    docstring_phrases: Collection[str] = DEFAULT_DOCSTRING_PHRASES,
    exclude: Iterable[str] = (),
) -> None:
    """Warn on each finding (``advisory=True``, the default); with ``advisory=False`` fail on any finding, or on any
    finding *baseline_path* does not accept. A walk that parses too few files or hits an unparsable file fails either way."""
    found = find_caller_frame_mutation(
        root,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
        frame_params=frame_params,
        allowed=allowed,
        exempt_name_parts=exempt_name_parts,
        docstring_phrases=docstring_phrases,
        exclude=exclude,
    )
    if advisory:
        for f in found:
            warnings.warn(f.render(), CallerFrameMutationWarning, stacklevel=2)
        return
    guidance = "copy the frame first (`X = X.copy()`), document the in-place contract in the docstring, or `# frame-mutation-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="caller_frame_mutation", refresh_command="PY_CI_SHARED_REFRESH=caller_frame_mutation")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} caller-frame-mutation finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
