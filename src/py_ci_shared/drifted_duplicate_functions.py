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


def _body_dump(fn: ast.FunctionDef) -> str:
    """The function body as an AST dump, with a leading docstring dropped."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def find_drifted_duplicate_functions(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    similarity: float = _DEFAULT_SIMILARITY,
) -> list[DriftGroup]:
    """Return every module-level function name whose copies are near-identical but not identical.

    Copies are grouped by name AND signature: a same-named function taking different arguments is a
    different function, not a copy that drifted.
    """
    excluded = tuple(exclude)
    groups: dict[tuple[str, str], list[tuple[Path, int, str]]] = defaultdict(list)
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if any(fragment in path.as_posix() for fragment in excluded):
                continue
            try:
                tree = ast.parse(path.read_bytes().decode("utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    groups[(node.name, ast.dump(node.args))].append((path, node.lineno, _body_dump(node)))

    found: list[DriftGroup] = []
    for (name, _signature), members in groups.items():
        if len(members) < 2:
            continue
        dumps = [dump for _, _, dump in members]
        if len(set(dumps)) == 1:
            continue
        best = max(
            difflib.SequenceMatcher(None, dumps[i], dumps[j]).ratio()
            for i in range(len(dumps))
            for j in range(i + 1, len(dumps))
        )
        if best >= similarity:
            found.append(DriftGroup(name, [(p, line) for p, line, _ in members], best, len(set(dumps))))
    return sorted(found, key=lambda g: (-g.similarity, g.name))


def assert_no_drifted_duplicate_functions(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    similarity: float = _DEFAULT_SIMILARITY,
    allow: Iterable[str] = (),
) -> None:
    """Raise ``AssertionError`` listing every drifted group that is not explicitly allowed.

    ``allow`` holds function names a reader has judged to be legitimately separate. Prefer consolidating:
    a shared implementation the copies import is what stops the next fix reaching only some of them.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    found = [g for g in find_drifted_duplicate_functions(roots, exclude, similarity) if g.name not in allowed]
    if not found:
        return
    newline = chr(10)
    listing = newline.join(str(g) for g in found)
    raise AssertionError(
        newline.join(
            [
                f"{len(found)} function(s) exist as near-identical copies that have drifted apart.",
                "  Nothing forces copies to stay in step, so a fix reaches whichever ones the author opened.",
                "  Consolidate into one implementation the others import, or add the name to `allow` with a reason.",
                listing,
            ]
        )
    )
