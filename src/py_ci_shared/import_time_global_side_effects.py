"""Module-level statements that change process-global state for everything imported after them.

Found in the Glossum backend (2026-10-16): ``scripts/lang_onboarding/test_unimorph_lemma_grammar.py`` was a command-line
script whose top level ran ``warnings.filterwarnings("ignore")``, ``logging.disable(logging.WARNING)`` and
``sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)``. One test imported it for a helper, so collecting that test
ran the statements in every pytest/xdist worker: INFO and WARNING records were dropped process-wide, log-capture tests
passed alone and failed in full parallel runs (78 unit tests red on main, a day to find). The fix moved the statements
into a function called from the ``if __name__ == "__main__":`` block.

A statement is *module-level* when it runs on import: the top level of the module, inside a module-level ``if``, ``try``,
``with``, ``for``/``while`` or class body, but not inside a function or lambda, and not in the body of an
``if __name__ == "__main__":`` block. A module that has such a block is not exempt by that alone: the statements outside
the guard still run on import. The finding key is ``path::shape`` (no line number), so a baseline survives edits.

Shapes (imports and aliases resolve through ``_core.ImportAliases``: ``import logging as lg``, ``from logging import disable``):

``logging-disable``
    ``logging.disable(...)``.
``logging-basicconfig-force``
    ``logging.basicConfig(..., force=True)`` (``force`` anything but a literal false). A plain ``basicConfig`` is a no-op
    once the root logger has a handler, which pytest's logging plugin guarantees during collection, so it is not reported
    (see the measurement in ``audits/2026-10-16/import_time_global_side_effects.md``).
``root-logger``
    Handler or level surgery on the root logger: ``logging.getLogger()`` / ``getLogger("")`` / ``getLogger("root")`` /
    ``logging.root`` (or a name bound to one at module level) with ``.setLevel``, ``.addHandler``, ``.removeHandler``,
    ``.handlers.<mutator>(...)``, an assignment to ``.handlers`` / ``.level`` / ``.disabled``.
``blanket-warnings-filter``
    ``warnings.filterwarnings("ignore")`` or ``warnings.simplefilter("ignore")`` with no category narrower than
    ``Warning`` and no message or module pattern. A narrow filter (``category=DeprecationWarning``, ``module="x"``) is fine,
    and so is anything inside ``with warnings.catch_warnings():``, which restores the filters on exit.
``sys.stdout-assign`` / ``sys.stderr-assign`` / ``sys.stdin-assign``
    An assignment to ``sys.stdout`` / ``sys.stderr`` / ``sys.stdin``.
``os-chdir``, ``locale-setlocale``, ``signal-signal``
    ``os.chdir(...)``, ``locale.setlocale(...)``, ``signal.signal(...)``.

Not reported: ``sys.stdout.reconfigure(...)`` (it keeps the object), ``faulthandler.enable``, environment edits.

Scope. ``root`` alone checks every module under it. ``imported_by`` (a tests directory) narrows the check to modules a
file under it imports, statically (``import x.y``, ``from x.y import z``, relative imports too), resolved to a file under
``root`` and followed through the module-level imports of those files: the precise form for a repo's scripts directory,
where hundreds of one-off scripts legitimately configure logging and nothing imports them. A module is matched by its
dotted path from ``root``'s own name (``scripts.lang_onboarding.x``) or by any trailing part of it (``lang_onboarding.x``,
``x``), so a ``sys.path`` insert in the importer still resolves.

Usage in a consumer's meta test::

    from py_ci_shared.import_time_global_side_effects import assert_import_time_global_side_effects

    def test_import_time_global_side_effects():
        assert_import_time_global_side_effects("scripts", imported_by="tests", min_files=10)
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, ParsedFile, child_nodes, scan_python
from ._core import walk as shared_walk
from ._core.aliases import resolve_relative

__all__ = ["RULE", "assert_import_time_global_side_effects", "find_import_time_global_side_effects"]

RULE = "import-time-global-side-effects"
_STREAMS = ("stdout", "stderr", "stdin")
_ROOT_CALLS = frozenset({"setLevel", "addHandler", "removeHandler"})
_HANDLER_MUTATORS = frozenset({"clear", "pop", "remove", "append", "extend", "insert"})
_ROOT_ATTRS = frozenset({"handlers", "level", "disabled"})
_PLAIN_CALLS = {"os.chdir": "os-chdir", "locale.setlocale": "locale-setlocale", "signal.signal": "signal-signal", "logging.disable": "logging-disable"}
_Loc = tuple[int, str, str]  # line, shape, message


def _is_main_guard(test: ast.AST) -> bool:
    """``__name__ == "__main__"`` (either order)."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    return any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)


def _sub_bodies(stmt: ast.stmt, safe_warnings: bool) -> Optional[list[tuple[list[ast.stmt], bool]]]:
    """The statement lists a compound statement runs on import, each with its ``catch_warnings`` state; None for a simple statement."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    if isinstance(stmt, ast.If):
        parts = [stmt.orelse] if _is_main_guard(stmt.test) else [stmt.body, stmt.orelse]
        return [(p, safe_warnings) for p in parts]
    if isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
        parts = [stmt.body, stmt.orelse, stmt.finalbody, *(h.body for h in stmt.handlers)]  # type: ignore[attr-defined]
        return [(p, safe_warnings) for p in parts]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return [(stmt.body, safe_warnings or any(_final(i.context_expr) == "catch_warnings" for i in stmt.items))]
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return [(stmt.body, safe_warnings), (stmt.orelse, safe_warnings)]
    if isinstance(stmt, ast.ClassDef):
        return [(stmt.body, safe_warnings)]
    if type(stmt).__name__ == "Match":
        return [(case.body, safe_warnings) for case in stmt.cases]  # type: ignore[attr-defined]
    return None


def _import_time_statements(body: list[ast.stmt], safe_warnings: bool = False) -> Iterator[tuple[ast.stmt, bool]]:
    """Every simple statement that runs on import, with whether it sits inside ``warnings.catch_warnings()``."""
    for stmt in body:
        nested = _sub_bodies(stmt, safe_warnings)
        if nested is None:
            yield stmt, safe_warnings
        for part, scoped in nested or []:
            yield from _import_time_statements(part, scoped)


def _final(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _walk(node: ast.AST) -> Iterator[ast.AST]:
    """*node* and everything under it that runs when the statement does (not lambda bodies)."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(c for c in child_nodes(current) if not isinstance(c, ast.Lambda))


def _arg(call: ast.Call, index: int, name: str) -> Optional[ast.AST]:
    if index < len(call.args):
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_str(node: Optional[ast.AST], value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _blanket_warnings(call: ast.Call, qualified: str, aliases: ImportAliases) -> bool:
    """``filterwarnings("ignore")`` / ``simplefilter("ignore")`` with nothing narrowing it."""
    simple = qualified == "warnings.simplefilter"
    if not _is_str(_arg(call, 0, "action"), "ignore"):
        return False
    if simple:
        category, narrowing = _arg(call, 1, "category"), [_arg(call, 2, "lineno")]
    else:
        category = _arg(call, 2, "category")
        narrowing = [_arg(call, 1, "message"), _arg(call, 3, "module"), _arg(call, 4, "lineno")]
    for extra in narrowing:
        if extra is None or _is_str(extra, "") or (isinstance(extra, ast.Constant) and extra.value == 0):
            continue
        return False
    if category is None:
        return True
    return aliases.qualified_name(category) in ("Warning", "builtins.Warning")


def _is_root_logger(node: ast.AST, aliases: ImportAliases, bound: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in bound
    if isinstance(node, ast.Call) and aliases.qualified_name(node) == "logging.getLogger":
        arg = _arg(node, 0, "name")
        return arg is None or (isinstance(arg, ast.Constant) and arg.value in (None, "", "root"))
    return aliases.qualified_name(node) == "logging.root"


def _root_logger_names(tree: ast.Module, aliases: ImportAliases) -> set[str]:
    bound: set[str] = set()
    for stmt, _ in _import_time_statements(tree.body):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None and _is_root_logger(stmt.value, aliases, set()):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            bound |= {t.id for t in targets if isinstance(t, ast.Name)}
    return bound


def _sys_stream(target: ast.AST, aliases: ImportAliases) -> Optional[str]:
    q = aliases.qualified_name(target) if isinstance(target, (ast.Attribute, ast.Name)) else None
    if q and q.startswith("sys.") and q[4:] in _STREAMS:
        return q[4:]
    return None


def _root_assignment(target: ast.AST, aliases: ImportAliases, bound: set[str]) -> bool:
    if isinstance(target, ast.Subscript):
        target = target.value
    return isinstance(target, ast.Attribute) and target.attr in _ROOT_ATTRS and _is_root_logger(target.value, aliases, bound)


def _locations(stmt: ast.stmt, in_catch: bool, aliases: ImportAliases, bound: set[str]) -> Iterator[_Loc]:
    for node in _walk(stmt):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in (leaf for t in targets for leaf in shared_walk(t) if isinstance(leaf, (ast.Attribute, ast.Name, ast.Subscript))):
                stream = _sys_stream(target, aliases)
                if stream:
                    yield node.lineno, f"sys.{stream}-assign", f"assigns sys.{stream}"
                elif _root_assignment(target, aliases, bound):
                    yield node.lineno, "root-logger", "assigns root logger state"
        elif isinstance(node, ast.Call):
            yield from _call_locations(node, in_catch, aliases, bound)


def _call_locations(call: ast.Call, in_catch: bool, aliases: ImportAliases, bound: set[str]) -> Iterator[_Loc]:
    qualified = aliases.qualified_name(call) or ""
    if qualified in _PLAIN_CALLS:
        yield call.lineno, _PLAIN_CALLS[qualified], f"calls {qualified}(...)"
    elif qualified == "logging.basicConfig":
        force = next((kw.value for kw in call.keywords if kw.arg == "force"), None)
        if force is not None and not (isinstance(force, ast.Constant) and not force.value):
            yield call.lineno, "logging-basicconfig-force", "calls logging.basicConfig(force=...), which replaces the root handlers"
    elif qualified in ("warnings.filterwarnings", "warnings.simplefilter"):
        if not in_catch and _blanket_warnings(call, qualified, aliases):
            yield call.lineno, "blanket-warnings-filter", f"calls {qualified}('ignore') with no narrowing category, module or message"
    elif isinstance(call.func, ast.Attribute):
        func = call.func
        if func.attr in _ROOT_CALLS and _is_root_logger(func.value, aliases, bound):
            yield call.lineno, "root-logger", f"calls root logger .{func.attr}(...)"
        elif func.attr in _HANDLER_MUTATORS and isinstance(func.value, ast.Attribute) and func.value.attr == "handlers":
            if _is_root_logger(func.value.value, aliases, bound):
                yield call.lineno, "root-logger", f"mutates root logger .handlers via .{func.attr}(...)"


def _scan_file(parsed: ParsedFile) -> list[Finding]:
    aliases = ImportAliases.from_tree(parsed.tree)
    bound = _root_logger_names(parsed.tree, aliases)
    out: list[Finding] = []
    for stmt, in_catch in _import_time_statements(parsed.tree.body):
        for line, shape, message in _locations(stmt, in_catch, aliases, bound):
            out.append(
                Finding(
                    parsed.rel, line, RULE, f"{message} at import time; move it into a function called from the `__main__` block", key=f"{parsed.rel}::{shape}"
                )
            )
    return out


# --- imported_by: which modules does the importer import? ---------------------------------------------------------------


def _package_of(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1])


def _imported_names(tree: ast.AST, package: str, *, module_level_only: bool) -> set[str]:
    """Dotted modules (and ``module.name`` for ``from module import name``) a tree imports, relative imports resolved."""
    out: set[str] = set()
    if module_level_only and isinstance(tree, ast.Module):
        nodes: list[ast.AST] = [n for stmt, _ in _import_time_statements(tree.body) for n in _walk(stmt)]
    else:
        nodes = list(shared_walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(node.module, node.level, package or None)
            if base is None:
                continue
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names if a.name != "*"}
    return out


def _names_of(rel: str, root_name: str) -> list[str]:
    """Every dotted name a file under the root answers to: from the root's name, and each trailing part."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    full = [root_name, *parts] if root_name else parts
    return [".".join(full[i:]) for i in range(len(full)) if full[i:]] if parts else [root_name]


def _imported_files(files: list[ParsedFile], importers: list[ParsedFile], root_name: str) -> set[str]:
    by_name: dict[str, set[str]] = {}
    for f in files:
        for name in _names_of(f.rel, root_name):
            by_name.setdefault(name, set()).add(f.rel)
    by_rel = {f.rel: f for f in files}
    pending = [name for p in importers for name in _imported_names(p.tree, _package_of(p.rel), module_level_only=False)]
    reached: set[str] = set()
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pieces = name.split(".")
        for end in range(1, len(pieces) + 1):  # importing a.b.c also runs a and a.b
            for rel in by_name.get(".".join(pieces[:end]), ()):
                if rel not in reached:
                    reached.add(rel)
                    package = ".".join(filter(None, [root_name, _package_of(rel)]))
                    pending.extend(_imported_names(by_rel[rel].tree, package, module_level_only=True))
                    pending.extend(_imported_names(by_rel[rel].tree, _package_of(rel), module_level_only=True))
    return reached


def find_import_time_global_side_effects(
    root: Union[str, Path],
    *,
    imported_by: Optional[Union[str, Path]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line; with *imported_by*, only in modules that a file under it imports.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed under *root* (or none under *imported_by*) and
    ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*).
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    files = list(scan)
    if imported_by is not None:
        importer_scan = scan_python(imported_by, min_files=0, use_git=use_git)
        importer_scan.assert_ok(allow_unparsed=allow_unparsed)  # names an unparsable test file before the floor does
        importer_scan.min_files = 1
        importer_scan.check_floor()
        reached = _imported_files(files, list(importer_scan), Path(root).resolve().name)
        files = [f for f in files if f.rel in reached]
    out = [finding for f in files for finding in _scan_file(f)]
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_import_time_global_side_effects(
    root: Union[str, Path],
    *,
    imported_by: Optional[Union[str, Path]] = None,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the shrink-only baseline does not accept."""
    found = find_import_time_global_side_effects(root, imported_by=imported_by, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = 'move the statement into a function called from the `if __name__ == "__main__":` block; an importing test runs it in every worker'
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="import_time_global_side_effects", refresh_command="PY_CI_SHARED_REFRESH=import_time_global_side_effects")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} import-time-global-side-effects finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
