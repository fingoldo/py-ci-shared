"""Copies of one function that have drifted apart.

A helper copy-pasted into several modules keeps working, so nothing forces the copies to stay in step. A
fix then reaches whichever copies the author happened to open. Eight modules of one package each carried
their own ``_fit_baseline_predict``; four had been corrected to return out-of-fold predictions and four had
not, because the correction was propagated by hand. The un-corrected copies fitted and predicted on the
same rows, so the residual they implied understated the true one -- and every band, quantile and
top-K-hardest selection derived from it was drawn on a distorted signal. Measured: mean |residual| 0.2092
in-sample against 0.2968 out-of-fold, with 244 of 400 rows landing in a different quintile band.

Two restrictions keep this from drowning in ordinary polymorphism, both calibrated on a ~3500-module
repository where the unrestricted rule reported 250 groups and this one reports 14:

* **Module-level functions only.** Methods sharing a name and signature are an interface, not a copy:
  ``predict``, ``forward`` and ``__repr__`` alone accounted for most of the noise.
* **High similarity, but not identity.** Identical copies are duplication, which other tools already report,
  and unrelated functions that merely share a name are not copies at all. What is dangerous is the pair that
  is almost the same -- one was fixed, the other was not.

Similarity is computed on the AST dump with the docstring dropped, so reformatting and re-wording do not
register while a changed statement does.
"""

from __future__ import annotations

import ast
import difflib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Union

from ._core import DEFAULT_EXCLUDE, ScanResult, iter_files, scan_python

__all__ = [
    "DriftGroup",
    "assert_no_drifted_duplicate_functions",
    "find_drifted_duplicate_functions",
]

# Below this the two bodies are different implementations rather than copies that moved apart.
_DEFAULT_SIMILARITY = 0.90


class DriftGroup:
    """One function name whose module-level copies are near-identical but not identical."""

    def __init__(self, name: str, sites: list[tuple[Path, int]], similarity: float, variants: int) -> None:
        """Record the group."""
        self.name = name
        self.sites = sites
        self.similarity = similarity
        self.variants = variants

    def __str__(self) -> str:
        """Render the name, the spread, and every site."""
        newline = chr(10)
        where = newline.join(f"        {p.as_posix()}:{line}" for p, line in self.sites)
        head = f"{self.name}: {len(self.sites)} copies in {self.variants} variants, similarity {self.similarity:.3f}"
        return head + newline + where


_FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _body_dump(fn: _FunctionNode) -> str:
    """The function's defaults and body as an AST dump, with a leading docstring dropped.

    Defaults are part of what is compared, not of the grouping key: ``g(x, n=5)`` and ``g(x, n=10)`` are copies of
    one function whose default drifted.
    """
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    defaults = ast.dump(ast.Tuple(elts=[*fn.args.defaults, *[d for d in fn.args.kw_defaults if d is not None]], ctx=ast.Load()))
    kind = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    return kind + defaults + ast.dump(ast.Module(body=body, type_ignores=[]))


def _signature_key(fn: _FunctionNode) -> str:
    """Parameter names and kinds only (no defaults, no annotations)."""
    a = fn.args
    parts = [f"/{p.arg}" for p in a.posonlyargs] + [p.arg for p in a.args]
    parts += [f"*{a.vararg.arg}"] if a.vararg else (["*"] if a.kwonlyargs else [])
    parts += [f"{p.arg}=" for p in a.kwonlyargs]
    parts += [f"**{a.kwarg.arg}"] if a.kwarg else []
    return ",".join(parts)


def _module_level_functions(tree: ast.Module) -> Iterable[_FunctionNode]:
    """Functions defined at module scope, including those inside a module-level ``if``/``try``/``with`` (the
    ``try: from fast import f`` / ``except ImportError: def f(...)`` fallback), but not methods or nested defs."""
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
        elif isinstance(node, _TRY_TYPES):
            stack.extend(getattr(node, "body", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))


_TRY_TYPES: tuple[type, ...] = (ast.Try,) + ((getattr(ast, "TryStar"),) if hasattr(ast, "TryStar") else ())


def _scan(roots: Sequence[Path], exclude: Iterable[str]) -> ScanResult:
    excluded = tuple(exclude)
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE) if not any(fragment in p.as_posix() for fragment in excluded))
    return scan_python(files)


def _groups_from(scan: ScanResult, similarity: float) -> list[DriftGroup]:
    groups: dict[tuple[str, str], list[tuple[Path, int, str]]] = defaultdict(list)
    for parsed in scan:
        for node in _module_level_functions(parsed.tree):
            groups[(node.name, _signature_key(node))].append((parsed.path, node.lineno, _body_dump(node)))

    found: list[DriftGroup] = []
    for (name, _signature), members in groups.items():
        if len(members) < 2:
            continue
        dumps = [dump for _, _, dump in members]
        if len(set(dumps)) == 1:
            continue
        best = max(difflib.SequenceMatcher(None, dumps[i], dumps[j]).ratio() for i in range(len(dumps)) for j in range(i + 1, len(dumps)))
        if best >= similarity:
            found.append(DriftGroup(name, [(p, line) for p, line, _ in members], best, len(set(dumps))))
    return sorted(found, key=lambda g: (-g.similarity, g.name))


def find_drifted_duplicate_functions(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    similarity: float = _DEFAULT_SIMILARITY,
) -> list[DriftGroup]:
    """Return every module-level function name whose copies are near-identical but not identical.

    Copies are grouped by name AND parameter names/kinds: a same-named function taking different arguments is a
    different function, not a copy that drifted. A changed default is drift, not a different signature.
    Files that cannot be parsed are not reported here; :func:`assert_no_drifted_duplicate_functions` fails on them.
    """
    return _groups_from(_scan(roots, exclude), similarity)


def assert_no_drifted_duplicate_functions(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    similarity: float = _DEFAULT_SIMILARITY,
    allow: Iterable[str] = (),
    *,
    min_files: int = 1,
) -> None:
    """Raise ``AssertionError`` listing every drifted group that is not explicitly allowed.

    ``allow`` holds function names a reader has judged to be legitimately separate. Prefer consolidating:
    a shared implementation the copies import is what stops the next fix reaching only some of them. An ``allow``
    entry that no longer names a drifted group fails too, as do unparsable files and a scan with fewer than
    *min_files* parsed files.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    scan = _scan(roots, exclude)
    scan.min_files = min_files
    every = _groups_from(scan, similarity)
    found = [g for g in every if g.name not in allowed]
    stale = sorted(allowed - {g.name for g in every})
    newline = chr(10)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so they were not compared:" + "".join(f"{newline}  {p.render()}" for p in scan.unparsed)
        )
    if found:
        listing = newline.join(str(g) for g in found)
        problems.append(
            newline.join(
                [
                    f"{len(found)} function(s) exist as near-identical copies that have drifted apart.",
                    "  Nothing forces copies to stay in step, so a fix reaches whichever ones the author opened.",
                    "  Consolidate into one implementation the others import, or add the name to `allow` with a reason.",
                    listing,
                ]
            )
        )
    if stale:
        problems.append(f"{len(stale)} `allow` entr(ies) no longer name a drifted group; remove them: {stale}")
    if problems:
        raise AssertionError(newline.join(problems))
