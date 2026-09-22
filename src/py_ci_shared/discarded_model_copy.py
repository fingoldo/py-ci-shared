"""A config rebuilt with ``model_copy(update=...)`` into a local must reach something, or the override is lost.

``cfg2 = cfg.model_copy(update={"enabled": True})`` makes a new object; the caller's ``cfg`` is untouched. When the local
copy is never returned, stored on an object or container, or passed to a call, the effective config exists only inside
that function and every later consumer reads the original. The shipped instance was discovery auto-enabled on a copy
that post-processing never saw, so the cross-target ensemble and the lag failsafe were skipped.

The check flags a function-scope ``name = <expr>.model_copy(update=...)`` whose ``name`` is afterwards neither returned
(or yielded), nor assigned into an attribute or subscript, nor used as a call argument. A copy intentionally scoped to one
statement is listed in ``allowed`` as ``function`` with a reason.

Usage from a repository's meta tests::

    from py_ci_shared.discarded_model_copy import assert_no_discarded_model_copy

    def test_no_discarded_model_copy():
        assert_no_discarded_model_copy(files=..., repo_root=REPO_ROOT, allowed={})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

__all__ = ["DiscardedCopy", "find_discarded_model_copies", "assert_no_discarded_model_copy"]


class DiscardedCopy:
    """One finding: the file, the function and the line of the copy nobody receives."""

    __slots__ = ("function", "lineno", "name", "path")

    def __init__(self, path: str, function: str, lineno: int, name: str) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        self.name = name

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.function}(): {self.name} = ...model_copy(update=...) reaches nothing"


def _is_update_copy(node: ast.AST) -> bool:
    """``<expr>.model_copy(update=...)``."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "model_copy"
            and any(k.arg == "update" for k in node.keywords))


def _mentions(node: ast.AST | None, name: str) -> bool:
    """Whether ``name`` is loaded anywhere under ``node``."""
    return node is not None and any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load) for n in ast.walk(node))


def _hands_on(node: ast.AST, name: str) -> bool:
    """Whether this one node returns/yields ``name``, stores it into an attribute or subscript, or passes it to a call."""
    if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
        return _mentions(node.value, name)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in targets) and _mentions(node.value, name)
    if isinstance(node, ast.Call):
        if any(_mentions(a, name) for a in node.args) or any(_mentions(k.value, name) for k in node.keywords):
            return True
        # ``name.method(...)`` hands the copy to its own method (e.g. ``cfg2.apply()``): the object is used.
        return isinstance(node.func, ast.Attribute) and node.func.attr != "model_copy" and _mentions(node.func.value, name)
    return False


def _escapes(func: ast.AST, name: str, _seen: frozenset[str] = frozenset()) -> bool:
    """Whether ``name`` (or a local it is aliased to) reaches anything inside ``func`` (see :func:`_hands_on`)."""
    seen = _seen | {name}
    for node in ast.walk(func):
        if _hands_on(node, name):
            return True
        # ``alias = name`` (or ``alias = name if c else other``) forwards the question to the alias.
        if isinstance(node, ast.Assign) and not _is_update_copy(node.value) and _mentions(node.value, name):
            if any(isinstance(t, ast.Name) and t.id not in seen and _escapes(func, t.id, seen) for t in node.targets):
                return True
    return False


def find_discarded_model_copies(files: Iterable[Path], repo_root: Path) -> list[DiscardedCopy]:
    """Every local ``model_copy(update=...)`` result that is never returned, stored or passed on."""
    out: list[DiscardedCopy] = []
    root = Path(repo_root).resolve()
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = Path(path).resolve().relative_to(root).as_posix()
        for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and _is_update_copy(node.value):
                    out.extend(DiscardedCopy(rel, func.name, node.lineno, t.id) for t in node.targets
                               if isinstance(t, ast.Name) and not _escapes(func, t.id))
    return sorted(out, key=lambda d: (d.path, d.lineno))


def assert_no_discarded_model_copy(files: Iterable[Path], repo_root: Path, allowed: Mapping[str, str], min_files: int = 1) -> None:
    """Fail on a discarded ``model_copy(update=...)`` outside ``allowed`` (function name -> reason); stale entries fail too."""
    files = list(files)
    if len(files) < min_files:
        raise AssertionError(f"scanned only {len(files)} files (< {min_files}); the scan lost its subject")
    empty = sorted(k for k, v in allowed.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowed entries need a reason: {empty}")
    found = find_discarded_model_copies(files, repo_root)
    bad = [d for d in found if d.function not in allowed]
    stale = sorted(set(allowed) - {d.function for d in found})
    msgs = []
    if bad:
        msgs.append("config copies built with model_copy(update=...) that no caller ever receives (the override is lost): "
                    + "; ".join(map(repr, bad)))
    if stale:
        msgs.append(f"allowed entries with no discarded copy left: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
