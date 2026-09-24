"""Row selections that silently lose the order of the index they were asked for.

``frame.iloc[idx]`` returns rows in ``idx`` order; the polars twin written as ``mask = np.zeros(n, bool); mask[idx] = True;
frame.filter(mask)`` returns them in FRAME order. The two branches agree only while ``idx`` is sorted, so a shuffled or
time-reversed index gives the polars path predictions aligned to the wrong targets with no error: one audit found the
OOF holdout off by 39.0 on reversed timestamps, and four sibling helpers with the same shape.

The scan flags a function that selects rows both positionally with an index (``.iloc[idx]``, ``.take(idx)``,
``.gather(idx)``) and by a boolean mask built from that same index (``m[idx] = True``, ``np.isin(x, idx)``,
``pl.col(c).is_in(idx)``) used as ``.filter(m)``, ``frame[m]`` or ``.loc[m]``: the two branches return the same rows in
different orders unless ``idx`` is sorted.

A mask that is meant to keep frame order (a set membership with no positional twin) is legitimate; list it in the
repository's allow table with the reason.

Usage from a repository's meta tests::

    from py_ci_shared.order_losing_filters import find_order_losing_filters

    def test_no_order_losing_row_filters():
        found = {f.key for f in find_order_losing_filters(SOURCE_FILES, REPO_ROOT)}
        assert found <= set(ALLOWED), sorted(found - set(ALLOWED))
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

__all__ = ["OrderLosingFilter", "find_order_losing_filters"]

_MEMBERSHIP_CALLS = frozenset({"isin", "is_in", "in1d"})


class OrderLosingFilter:
    """One row selection by an index-derived mask: where it is and which mask it used."""

    __slots__ = ("key", "kind", "lineno", "mask", "path", "scope")

    def __init__(self, path: str, scope: str, lineno: int, mask: str, kind: str) -> None:
        self.path = path
        self.scope = scope
        self.lineno = lineno
        self.mask = mask
        self.kind = kind
        self.key = f"{path}::{scope}::{mask}"

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.scope}: {self.kind} ({self.mask})"


def _names_in(node: ast.AST) -> set[str]:
    """Every bare name read inside ``node``."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _index_masks(func: ast.AST) -> dict[str, set[str]]:
    """``{mask name: the index names it was built from}`` for boolean masks built from an index array inside ``func``."""
    masks: dict[str, set[str]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        # m[idx] = True
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and not isinstance(t.slice, (ast.Constant, ast.Slice)):
                    masks.setdefault(t.value.id, set()).update(_names_in(t.slice))
        # m = np.isin(x, idx) / m = col.is_in(idx) / m = np.in1d(x, idx)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call):
            call = node.value
            if (getattr(call.func, "attr", None) or getattr(call.func, "id", None)) in _MEMBERSHIP_CALLS and call.args:
                masks.setdefault(node.targets[0].id, set()).update(_names_in(call.args[-1]))
    return masks


def _positional_indices(func: ast.AST) -> set[str]:
    """Index names ``func`` selects rows with positionally: ``.iloc[i]``, ``.take(i)``, ``.gather(i)``."""
    out: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Subscript) and getattr(node.value, "attr", None) == "iloc":
            out |= _names_in(node.slice)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in ("take", "gather") and node.args:
            out |= _names_in(node.args[0])
    return out


def _selections(func: ast.AST, masks: dict[str, set[str]]):
    """``(lineno, mask)`` for every row selection by one of ``masks`` (``.filter(m)``, ``x[m]``, ``x.loc[m]``)."""
    def mask_of(arg: ast.AST):
        """The mask name ``arg`` is, or wraps in a call such as ``pl.Series(mask)``; None otherwise."""
        if isinstance(arg, ast.Call) and len(arg.args) == 1:
            arg = arg.args[0]
        return arg.id if isinstance(arg, ast.Name) and arg.id in masks else None

    for node in ast.walk(func):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "filter" and node.args and mask_of(node.args[0]):
            yield node.lineno, mask_of(node.args[0])
        if isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Store) and mask_of(node.slice):
            yield node.lineno, mask_of(node.slice)


def _functions(tree: ast.Module):
    """``(qualified name, node)`` for every function, with its enclosing classes and functions."""
    def walk(node: ast.AST, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield name, child
                yield from walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)
    yield from walk(tree, "")


def find_order_losing_filters(files: Iterable[Path], repo_root: Path) -> list[OrderLosingFilter]:
    """Every row selection by a mask built from an index the same function also selects positionally, keyed ``path::function::mask``."""
    out: list[OrderLosingFilter] = []
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
        seen: set[tuple[str, str]] = set()
        for scope, func in _functions(tree):
            positional = _positional_indices(func)
            masks = {m: idx for m, idx in _index_masks(func).items() if idx & positional}
            for lineno, mask in _selections(func, masks):
                if (scope, mask) in seen:
                    continue
                seen.add((scope, mask))
                out.append(OrderLosingFilter(rel, scope, lineno, mask, "a mask built from an index that also selects rows positionally keeps frame order, not index order"))
    return out
