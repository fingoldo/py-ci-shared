"""Constructor parameters nothing ever reads: a knob that is accepted, stored, documented and ignored.

An estimator's ``__init__`` parameter is a public promise. When the value is stored on the instance and never read, the
parameter still shows up in ``get_params()``, in a repr and in a grid search, so tuning or disabling it changes nothing
and reports no error - the shipped instance was five ``moe_*`` parameters whose gate was configured from a different
object entirely, one of them documented as "Default ON ... never worse than the lag failsafe".

A parameter counts as read when it is used in the ``__init__`` body beyond a plain store (validated, combined, passed
on), or when the attribute it was stored into - under whatever name, ``self._p = p`` included - is read anywhere in the
scanned package, through any receiver, since a wrapper commonly reads ``est.p`` off the estimator it holds.

Usage from a repository's meta tests::

    from py_ci_shared.unread_init_params import assert_no_unread_init_params

    def test_no_unread_constructor_parameters():
        assert_no_unread_init_params(files=PACKAGE_FILES, repo_root=REPO_ROOT, allowlist={"random_state": "sklearn clone"})
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

from ._core import relative_posix, scan_python

__all__ = ["UnreadParam", "find_unread_init_params", "assert_no_unread_init_params"]

_SELF_NAMES = frozenset({"self", "cls"})


class UnreadParam:
    """One accepted-and-ignored parameter: where its class is, and what it is called."""

    __slots__ = ("cls", "lineno", "param", "path")

    def __init__(self, path: str, cls: str, param: str, lineno: int) -> None:
        self.path = path
        self.cls = cls
        self.param = param
        self.lineno = lineno

    def __repr__(self) -> str:
        return f"{self.path}:{self.lineno} {self.cls}.{self.param}"


def _init_of(node: ast.ClassDef) -> ast.FunctionDef | None:
    """The class's own ``__init__``, or None."""
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
            return item  # type: ignore[return-value]
    return None


def _param_names(init: ast.FunctionDef) -> list[str]:
    """The declared parameter names, without ``self`` and without ``*args`` / ``**kwargs``."""
    a = init.args
    names = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return [n for n in names if n not in _SELF_NAMES]


def _store_targets(init: ast.FunctionDef, param: str) -> set[str] | None:
    """The attribute names ``param`` is plainly stored into, or None when the body does anything else with it.

    The attribute is often renamed on the way in (``self._sampler = sampler``), so the stored-under name is what the rest
    of the package reads - checking the parameter's own name there would report every renamed store as dead.
    """
    uses = sum(1 for n in ast.walk(init) if isinstance(n, ast.Name) and n.id == param and isinstance(n.ctx, ast.Load))
    stored: set[str] = set()
    plain_stores = 0
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Name) and value.id == param):
            continue
        if all(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in _SELF_NAMES for t in targets):
            stored.update(t.attr for t in targets if isinstance(t, ast.Attribute))  # `self.a = self.b = p` stores twice
            plain_stores += 1
    return stored if uses and uses == plain_stores else None


def _declaration_strings(tree: ast.Module) -> set[int]:
    """``id()`` of string constants that DECLARE names rather than read them: ``__slots__``/``__all__`` entries and
    docstrings. ``__slots__ = ("alpha",)`` makes ``alpha`` storable, it does not read it."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any(isinstance(t, ast.Name) and t.id in ("__slots__", "__all__") for t in targets):
                out.update(id(n) for n in ast.walk(node.value) if isinstance(n, ast.Constant))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _read_attributes(trees: Iterable[ast.Module]) -> set[str]:
    """Attribute names read anywhere: ``x.p`` in a load context, ``getattr(x, "p")``, and any string literal used as a key.

    The string literals are deliberately generous: a parameter named in ``get_params`` lists, in a ``__getstate__`` key set
    or in a config-copy dict is being used, and no repository should have to allowlist those one by one.
    """
    names: set[str] = set()
    for tree in trees:
        declarations = _declaration_strings(tree)
        for node in ast.walk(tree):
            if id(node) in declarations:
                continue
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                names.add(node.attr)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr" and node.args[1:]:
                first = node.args[1]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
    return names


def find_unread_init_params(files: Iterable[Path], repo_root: Path, *, allow_unparsed: bool = False) -> list[UnreadParam]:
    """Every ``__init__`` parameter that is only stored on the instance and read nowhere in the scanned files.

    A file that cannot be read or parsed raises :class:`py_ci_shared._core.UnparsedFilesError`: its reads are unknown, so
    every parameter it reads would be reported unread. ``allow_unparsed=True`` skips such files instead. Paths outside
    *repo_root* are reported by their absolute POSIX path rather than raising.
    """
    scan = scan_python([Path(p) for p in files], min_files=0, root=repo_root)
    if scan.unparsed and not allow_unparsed:
        scan.check_unparsed()
    parsed: list[tuple[str, ast.Module]] = [(relative_posix(f.path, repo_root), f.tree) for f in scan]
    read = _read_attributes(tree for _p, tree in parsed)
    out: list[UnreadParam] = []
    for rel, tree in parsed:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            init = _init_of(node)
            if init is None:
                continue
            for param in _param_names(init):
                stored = _store_targets(init, param)
                if stored is not None and not (stored & read):
                    out.append(UnreadParam(rel, node.name, param, init.lineno))
    return sorted(out, key=lambda u: (u.path, u.cls, u.param))


def assert_no_unread_init_params(files: Iterable[Path], repo_root: Path, allowlist: Mapping[str, str] | None = None, min_files: int = 1) -> None:
    """Fail on a constructor parameter that nothing reads.

    ``allowlist`` maps a parameter name to the reason it is accepted unread (a sklearn meta-parameter consumed by
    ``get_params`` alone, say); an empty reason is rejected, and an entry with nothing left to excuse must be removed.
    """
    files = list(files)
    scan = scan_python([Path(p) for p in files], min_files=min_files, root=repo_root)
    if scan.parsed_count < min_files:
        raise AssertionError(f"scanned only {scan.parsed_count} parsed files (< {min_files}); the scan lost its subject")
    scan.check_unparsed()
    allowlist = dict(allowlist or {})
    empty = sorted(k for k, v in allowlist.items() if not str(v).strip())
    if empty:
        raise AssertionError(f"allowlisted parameters need a reason: {empty}")
    unread = find_unread_init_params(files, repo_root)
    bad = [u for u in unread if u.param not in allowlist]
    stale = sorted(set(allowlist) - {u.param for u in unread})
    msgs = []
    if bad:
        msgs.append(
            "constructor parameters that nothing reads (they still appear in get_params, in a repr and in a grid "
            "search, so setting them changes nothing and reports nothing): " + "; ".join(map(repr, bad))
        )
    if stale:
        msgs.append(f"allowlisted parameters that are read after all: {stale}")
    if msgs:
        raise AssertionError("\n".join(msgs))
