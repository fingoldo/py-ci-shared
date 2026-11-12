"""A path built under gettempdir() with no per-user component (audit X-12): a predictable lock or cache name another local user can pre-create.

``tempfile.gettempdir()`` is one directory shared by every user on the machine. A lock or cache file named under it, such as
``os.path.join(tempfile.gettempdir(), sha256(path).lock)``, has a predictable name that another local user can create or symlink
first, so the owner's lock or cache can be hijacked. The fix is a per-user subdirectory, or ``mkdtemp``/``mkstemp``.

Reported: a join, ``Path(...)`` or ``/`` expression with a ``gettempdir()`` operand, unless a per-user value (``getuid``, ``getlogin``, ``getpass``, ``USERNAME``) flows into the path expression itself
(directly or through a name assigned from one), or the enclosing function (module, for top-level code) calls ``mkdtemp``/``mkstemp``.
A bare ``tempfile.mktemp()`` is always reported (a race by design). A merely unused ``uid = os.getuid()`` does not exempt. Nearest correct code, not reported: ``os.path.join(tempfile.gettempdir(), f"app-{os.getuid()}", name)``
or a ``mkdtemp`` directory. A deliberate site is marked ``# tmp-path-ok: <reason>`` on the line.

Usage in a consumer's meta test::

    from py_ci_shared.predictable_tmp_path import assert_predictable_tmp_path

    def test_predictable_tmp_path():
        assert_predictable_tmp_path("src")
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk
from ._core.scopes import Scope, iter_scopes
from ._core.unparse import unparse_stable

__all__ = ["RULE", "assert_predictable_tmp_path", "find_predictable_tmp_path"]

RULE = "predictable-tmp-path"
SUPPRESSION = "# tmp-path-ok:"
_USER_MARKERS = ("getuid", "getlogin", "getpass", "USERNAME")
_UNIQUE_MARKERS = ("mkdtemp", "mkstemp")
_BUILDER_NAMES = frozenset({"join", "Path", "PurePath"})


def _is_gettempdir(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return getattr(func, "attr", None) == "gettempdir" or getattr(func, "id", None) == "gettempdir"


def _tmp_names(tree: ast.Module) -> set[str]:
    """Names assigned exactly once, to the shared temp directory (``t = tempfile.gettempdir()``)."""
    counts: dict[str, int] = {}
    tmp: set[str] = set()
    for node in shared_walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    counts[target.id] = counts.get(target.id, 0) + 1
                    if _is_tmp_source(node.value, set()):
                        tmp.add(target.id)
    return {name for name in tmp if counts[name] == 1}


def _is_tmp_source(node: ast.AST, names: set[str]) -> bool:
    """The shared temp directory itself: ``gettempdir()``, ``tempfile.tempdir``, a name bound to one, or a literal ``/tmp`` path."""
    if _is_gettempdir(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr == "tempdir" and getattr(node.value, "id", "") == "tempfile"
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    return node.value == "/tmp" or node.value.startswith("/tmp/")  # tmp-path-ok: the detector's own literal


def _builds_path(node: ast.AST) -> Optional[list[ast.AST]]:
    """The operands of a join/Path/``/``/``+``/f-string expression, or None when the node builds no path."""
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in _BUILDER_NAMES:
        return list(node.args)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" and isinstance(node.func.value, ast.Constant):
        return [*node.args, *(k.value for k in node.keywords)]  # "{}/a.lock".format(gettempdir())
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return [node.left, node.right]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
    return None


def _owner_map(tree: ast.Module) -> dict[int, Optional[ast.AST]]:
    """``id(node) -> innermost enclosing function def, or None for module-level code``, read from the shared scope index.

    A class body belongs to the function or module it is written in, and a lambda body to the scope around the lambda: the index
    gives each scope its own nodes, so a class takes its parent's owner and the lambda bodies (which the index does not enter) are
    filled in from the lambda itself."""

    def owner(scope: "Scope") -> Optional[ast.AST]:
        if scope.node is None:
            return None
        if isinstance(scope.node, ast.ClassDef):
            return owner(scope.parent) if scope.parent is not None else None
        return scope.node

    out: dict[int, Optional[ast.AST]] = {}
    for scope in iter_scopes(tree):
        value = owner(scope)
        for node in scope.nodes:
            out[id(node)] = value
        if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[id(scope.node)] = scope.node
    for lam in [node for node in shared_walk(tree) if isinstance(node, ast.Lambda)]:
        for sub in shared_walk(lam):
            out[id(sub)] = out.get(id(lam))
    return out


def _enclosing_text_of(tree: ast.Module, source: str) -> Callable[[ast.AST], str]:
    """A function ``node -> source of its innermost enclosing function``; module-level code gets the module-level statements only
    (a ``getlogin`` in one function does not make an unrelated module-level temp path per-user). One pass records each node's scope;
    a function's source is cut only when a candidate asks for it, so a file with many functions and few candidates costs no slicing."""
    scope_of = _owner_map(tree)
    module_stmts = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    texts: dict[int, str] = {}

    def text_of(node: ast.AST) -> str:
        if id(node) not in scope_of:
            return source
        scope = scope_of[id(node)]
        key = id(scope) if scope is not None else 0
        if key not in texts:
            if scope is not None:
                texts[key] = ast.get_source_segment(source, scope) or ""
            else:
                texts[key] = "\n".join(ast.get_source_segment(source, s) or "" for s in module_stmts)
        return texts[key]

    return text_of


def _has_user_marker(node: ast.AST) -> bool:
    """True when a marker is in the node's source. Every marker is a name, an attribute, a keyword or a string literal, so the
    cheap pass over those words runs first and the unparse only for a node that could hold one."""
    words: list[str] = []
    for sub in shared_walk(node):
        if isinstance(sub, ast.Name):
            words.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            words.append(sub.attr)
        elif isinstance(sub, (ast.keyword, ast.arg)) and sub.arg:
            words.append(sub.arg)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)):
            words.append(sub.value if isinstance(sub.value, str) else sub.value.decode("latin-1"))
    blob = "\n".join(words)
    if not any(marker in blob for marker in _USER_MARKERS):
        return False
    return any(marker in unparse_stable(node) for marker in _USER_MARKERS)


def _user_names(tree: ast.Module) -> set[str]:
    """Names assigned from an expression that holds a per-user value (``uid = os.getuid()``)."""
    out: set[str] = set()
    for node in shared_walk(tree):
        if isinstance(node, ast.Assign) and _has_user_marker(node.value):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _is_mktemp(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "mktemp"


def _findings_in(tree: ast.Module, rel: str, source: str) -> list[Finding]:
    lines = source.splitlines()
    enclosing_text = _enclosing_text_of(tree, source)
    names = _tmp_names(tree)
    user_names = _user_names(tree)
    candidates: list[ast.AST] = []
    for node in shared_walk(tree):
        operands = _builds_path(node)
        if operands is not None and any(_is_tmp_source(sub, names) for op in operands for sub in shared_walk(op)):
            candidates.append(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/tmp/"):  # tmp-path-ok: the detector's own literal
            candidates.append(node)
        elif _is_mktemp(node):
            candidates.append(node)
    inner = {id(sub) for c in candidates for sub in shared_walk(c) if sub is not c}
    out: list[Finding] = []
    for node in candidates:
        if id(node) in inner:
            continue  # `Path(gettempdir()) / "x"`: one finding for the whole expression, not one per nested builder
        line_no = getattr(node, "lineno", 0)
        line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
        if SUPPRESSION in line:
            continue
        enclosing = enclosing_text(node)
        if not _is_mktemp(node) and (
            any(marker in enclosing for marker in _UNIQUE_MARKERS)
            or _has_user_marker(node)
            or any(isinstance(sub, ast.Name) and sub.id in user_names for sub in shared_walk(node))
        ):
            continue
        out.append(
            Finding(
                rel,
                line_no,
                RULE,
                (
                    "mktemp() is racy by design: use mkstemp/mkdtemp: "
                    if _is_mktemp(node)
                    else "path built under the shared temp directory with no per-user component: "
                )
                + line.strip(),
            )
        )
    return out


def find_predictable_tmp_path(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git, include_stubs=True)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_predictable_tmp_path(
    root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_predictable_tmp_path(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "use a per-user subdirectory (os.getuid(), getpass) or mkdtemp/mkstemp, or mark a deliberate site # tmp-path-ok: <reason>"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="predictable_tmp_path", refresh_command="PY_CI_SHARED_REFRESH=predictable_tmp_path")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} predictable-tmp-path finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
