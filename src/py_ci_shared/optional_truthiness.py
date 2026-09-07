"""Shared check: an optional parameter tested for truth rather than for absence.

A parameter annotated ``int | None`` (or ``float | None``, ``str | None``, ``Optional[int]``)
has THREE states its author cares about: absent, zero-ish, and set. Writing ``if limit:``
collapses the first two, and the collapse is silent -- the code reads as though it handles
the default, and it does, by treating a deliberate 0 as "not given".

Two real instances, both from glossum:

* ``mwe_importer.import_file(limit: int | None)`` guarded its read with ``if limit and
  lines_read >= limit``. ``--limit 0`` therefore skipped the guard and read a multi-GB dump
  to EOF -- the opposite of what the caller asked for (2026-09-05 wave, 01-F4).
* ``word_selector`` scored difficulty with ``if sense.frequency_rank:``, so rank 0 -- the
  single most frequent word in the corpus, a legitimate value -- was treated as "unknown"
  and given the neutral 0.5 instead of the near-zero it had earned (2026-08-02 wave).

The check is deliberately limited to NUMERIC and string optionals. A ``list | None`` or a
``dict | None`` tested for truth is usually intentional ("empty or missing, same thing"),
and reporting those would drown the signal.

Usage::

    from py_ci_shared.optional_truthiness import assert_optionals_test_for_none

    def test_no_optional_is_tested_for_truth():
        assert_optionals_test_for_none(files=sorted(PKG.rglob("*.py")), repo_root=REPO)
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

# Types where 0 is a value a caller can legitimately mean, distinct from absence.
#
# Numbers only, and the members are matched EXACTLY. `str | None` was included at first and
# produced 159 findings on one repo, nearly all of them `if lang:` -- an empty language code
# is not a meaningful value, so collapsing it with absence is correct there. Substring
# matching also mis-read `dict[str, int] | None` as an optional int. Both were noise around
# a signal worth keeping sharp.
_MEANINGFUL_FALSY = frozenset({"int", "float", "Decimal"})


def _union_members(annotation: ast.AST) -> list[str]:
    """The parts of ``A | B | None`` / ``Optional[A]`` / ``Union[A, None]``, as source text."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _union_members(annotation.left) + _union_members(annotation.right)
    if isinstance(annotation, ast.Subscript):
        base = ast.unparse(annotation.value).split(".")[-1]
        if base in ("Optional", "Union"):
            inner = annotation.slice
            members = (
                [m for e in inner.elts for m in _union_members(e)]
                if isinstance(inner, ast.Tuple)
                else _union_members(inner)
            )
            # Optional[X] means X | None: the None is implicit and has to be added, or the
            # whole spelling reads as non-optional.
            return members + ["None"] if base == "Optional" else members
        return [ast.unparse(annotation)]          # dict[str, int] stays one opaque member
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return ["None"]
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # A string annotation ("int | None"): parse it rather than matching substrings.
        try:
            return _union_members(ast.parse(annotation.value, mode="eval").body)
        except SyntaxError:
            return [annotation.value]
    return [ast.unparse(annotation)]


def _optional_of_meaningful_falsy(annotation: ast.AST | None) -> bool:
    """True for ``int | None``, ``Optional[float]``, ``Union[Decimal, None]``."""
    if annotation is None:
        return False
    members = [m.strip().split(".")[-1] for m in _union_members(annotation)]
    return "None" in members and any(m in _MEANINGFUL_FALSY for m in members)


def _optional_params(fn: ast.AST) -> set[str]:
    args = fn.args
    every = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    return {a.arg for a in every if _optional_of_meaningful_falsy(a.annotation)}


def find_truthiness_tests(path: Path) -> list[str]:
    """Report ``if param:`` / ``param and ...`` where *param* is an optional number."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []

    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        optional = _optional_params(fn)
        if not optional:
            continue
        for node in ast.walk(fn):
            tested: list[ast.AST] = []
            if isinstance(node, ast.If):
                tested = [node.test]
            elif isinstance(node, ast.BoolOp):
                tested = list(node.values)
            for expr in tested:
                if isinstance(expr, ast.Name) and expr.id in optional:
                    out.append(
                        f"{path.name}:{expr.lineno}: `{expr.id}` is an optional number tested for "
                        f"TRUTH; 0 is a value a caller can mean, and this reads it as absent. Use "
                        f"`{expr.id} is not None`."
                    )
    return sorted(set(out))


def assert_optionals_test_for_none(
    *,
    files: Iterable[Path],
    repo_root: Path,
    baseline: Iterable[str] = (),
    min_subjects: int = 1,
) -> None:
    """Fail on a new optional-number parameter tested for truth.

    ``baseline`` takes already-reviewed findings verbatim, so the check can be adopted on a
    tree that has some, without either failing every commit or hiding the backlog.
    """
    import pytest

    accepted = set(baseline)
    paths = [p for p in files]
    if len(paths) < min_subjects:
        pytest.fail(f"only {len(paths)} file(s) scanned -- expected at least {min_subjects}; the scan lost its subject")

    problems = [p for path in paths for p in find_truthiness_tests(path) if p not in accepted]
    if problems:
        pytest.fail(f"{len(problems)} optional(s) tested for truth rather than for None:\n  " + "\n  ".join(problems))
