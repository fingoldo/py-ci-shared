"""A path built under gettempdir() with no per-user component (audit X-12): a predictable lock or cache name another local user can pre-create.

``tempfile.gettempdir()`` is one directory shared by every user on the machine. A lock or cache file named under it, such as
``os.path.join(tempfile.gettempdir(), sha256(path).lock)``, has a predictable name that another local user can create or symlink
first, so the owner's lock or cache can be hijacked. The fix is a per-user subdirectory, or ``mkdtemp``/``mkstemp``.

Reported: a join, ``Path(...)`` or ``/`` expression with a ``gettempdir()`` operand, unless the enclosing function (or the
module, for top-level code) mentions a per-user or unique-name source: ``getuid``, ``getlogin``, ``getpass``, ``USERNAME``,
``mkdtemp``, ``mkstemp``. Nearest correct code, not reported: ``os.path.join(tempfile.gettempdir(), f"app-{os.getuid()}", name)``
or a ``mkdtemp`` directory. A deliberate site is marked ``# tmp-path-ok: <reason>`` on the line.

Usage in a consumer's meta test::

    from py_ci_shared.predictable_tmp_path import assert_predictable_tmp_path

    def test_predictable_tmp_path():
        assert_predictable_tmp_path("src")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python
from ._core import walk as shared_walk

__all__ = ["RULE", "assert_predictable_tmp_path", "find_predictable_tmp_path"]

RULE = "predictable-tmp-path"
SUPPRESSION = "# tmp-path-ok:"
_PER_USER_MARKERS = ("getuid", "getlogin", "getpass", "USERNAME", "mkdtemp", "mkstemp")
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
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and (node.value == "/tmp" or node.value.startswith("/tmp/"))  # tmp-path-ok: the detector's own literal


def _builds_path(node: ast.AST) -> Optional[list[ast.AST]]:
    """The operands of a join/Path/``/``/``+``/f-string expression, or None when the node builds no path."""
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in _BUILDER_NAMES:
        return list(node.args)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return [node.left, node.right]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
    return None


def _per_user_scope_text(tree: ast.Module, source: str) -> dict[int, str]:
    """``id(node) -> source of its innermost enclosing function``; module-level code maps to the module-level statements only
    (a ``getlogin`` in one function does not make an unrelated module-level temp path per-user)."""
    out: dict[int, str] = {}
    module_stmts = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    module_text = "\n".join(ast.get_source_segment(source, s) or "" for s in module_stmts)
    for stmt in module_stmts:
        for sub in shared_walk(stmt):
            out[id(sub)] = module_text
    for scope in shared_walk(tree):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_source_segment(source, scope) or ""
            for sub in shared_walk(scope):
                out[id(sub)] = text
    return out


def _findings_in(tree: ast.Module, rel: str, source: str) -> list[Finding]:
    lines = source.splitlines()
    scope_text = _per_user_scope_text(tree, source)
    names = _tmp_names(tree)
    candidates: list[ast.AST] = []
    for node in shared_walk(tree):
        operands = _builds_path(node)
        if operands is not None and any(_is_tmp_source(sub, names) for op in operands for sub in shared_walk(op)):
            candidates.append(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/tmp/"):  # tmp-path-ok: the detector's own literal
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
        enclosing = scope_text.get(id(node), source)
        if any(marker in enclosing for marker in _PER_USER_MARKERS):
            continue
        out.append(Finding(rel, line_no, RULE, "path built under the shared temp directory with no per-user component: " + line.strip()))
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
    scan = scan_python(root, min_files=min_files, use_git=use_git)
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
