"""Flat top-level scripts: an entry point imported by bare name registers itself in sys.modules, and no script puts the cwd on sys.path.

WHY. A script run as ``python x.py`` is ``__main__``. A sibling's ``import x`` does not find it in ``sys.modules``, so it executes the file a
SECOND time, with its own copy of every module-level object: an event set in one copy is unset in the other, and when the sibling is still
on its first import line the second pass asks it for names it has not bound yet (``ImportError: cannot import name ... from partially
initialized module``). Under pytest every module is imported AS a module, never as ``__main__``, so no ordinary test can see it. One line fixes
it, placed before the sibling imports::

    if __name__ == "__main__":
        import sys as _sys
        _sys.modules.setdefault("x", _sys.modules["__main__"])

Rules (all parse-only; *script_dirs* are the directories that hold the flat scripts, each started with its own directory on ``sys.path``):

* ``unregistered-entry-point``: a module with a top-level ``if __name__ == "__main__":`` that another script imports by bare name, at ANY
  depth (a function-local import starts the second execution on the first call), and that has no ``sys.modules.setdefault("<its name>",
  sys.modules["__main__"])`` inside an ``if __name__ == "__main__":`` block.
* ``cwd-on-sys-path``: ``sys.path.insert(0, ".")``, ``sys.path.append("")``, any relative literal, or a value derived from
  ``os.getcwd()``/``Path.cwd()`` (followed through simple name assignments). A path built from ``__file__`` is fine. Run from another directory
  or by a scheduler, the working directory is wherever the shell was, and the import binds a same-named module from there.
* ``module-name-collision``: two *script_dirs* holding a module with the same stem. A bare ``import x`` cannot say which one is meant, so the
  gate reports it instead of guessing.

``double_execution_findings`` is the behavioural half, opt-in because it starts one interpreter per exposed entry point: it loads the file as
``__main__`` without running its work block and asserts ``importlib.import_module(name) is sys.modules["__main__"]``.

Usage::

    from py_ci_shared.script_entry_points import assert_script_entry_points

    def test_script_entry_points():
        assert_script_entry_points(ROOT, [".", "additional_jobs"], probe_dirs=["probes"])
"""

from __future__ import annotations

import ast
import concurrent.futures
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult, scan_python
from ._core.gate_contract import check_scan

__all__ = [
    "RULE_COLLISION",
    "RULE_CWD",
    "RULE_UNREGISTERED",
    "assert_script_entry_points",
    "double_execution_findings",
    "find_script_entry_points",
]

RULE_UNREGISTERED = "unregistered-entry-point"
RULE_CWD = "cwd-on-sys-path"
RULE_COLLISION = "module-name-collision"
RULE_DOUBLE = "double-execution"

PathLike = Union[str, "os.PathLike[str]"]

_IGNORED_STEMS = frozenset({"__init__", "__main__"})
_CWD_CALLS = frozenset({"getcwd", "getcwdb", "cwd"})


# ---------------------------------------------------------------------------------------------------------------------------------------
# Structural facts about one file
# ---------------------------------------------------------------------------------------------------------------------------------------


def _is_main_test(test: ast.expr) -> bool:
    """``__name__ == "__main__"`` in either operand order."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return False
    operands = [test.left, *test.comparators]
    has_name = any(isinstance(o, ast.Name) and o.id == "__name__" for o in operands)
    has_main = any(isinstance(o, ast.Constant) and o.value == "__main__" for o in operands)
    return has_name and has_main


def _main_blocks(tree: ast.Module) -> list[ast.If]:
    return [n for n in tree.body if isinstance(n, ast.If) and _is_main_test(n.test)]


def _registers_itself(block: ast.If, name: str) -> bool:
    """The block calls ``<x>.modules.setdefault("<name>", <y>["__main__"])``, whatever ``sys`` was imported as."""
    for node in ast.walk(block):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "modules") or len(node.args) < 2:
            continue
        key, value = node.args[0], node.args[1]
        if not (isinstance(key, ast.Constant) and key.value == name):
            continue
        if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant) and value.slice.value == "__main__":
            return True
    return False


def _imported_names(tree: ast.Module, known: frozenset[str]) -> dict[str, int]:
    """``{module name: first line}`` of every absolute import of a *known* module, at ANY depth."""
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    found.setdefault(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in known:
            found.setdefault(node.module, node.lineno)
    return found


def _names_where(tree: ast.AST, tainted: Callable[[ast.expr, set[str]], bool]) -> set[str]:
    """Names assigned (anywhere) from an expression for which ``tainted(expr, names_so_far)`` holds, to a fixed point."""
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and tainted(node.value, names):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in names:
                        names.add(target.id)
                        changed = True
    return names


def _reads_file(expr: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and (n.id == "__file__" or n.id in names) for n in ast.walk(expr))


def _is_relative_literal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return not (value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":"))


def _reads_cwd(expr: ast.AST, names: set[str]) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Name) and n.id in names:
            return True
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr in _CWD_CALLS) or (isinstance(f, ast.Name) and f.id in _CWD_CALLS):
                return True
        if isinstance(n, ast.Constant) and _is_relative_literal(n.value):
            return True
    return False


def _cwd_path_edits(tree: ast.Module, aliases: ImportAliases) -> list[tuple[int, str]]:
    """``(line, rendered value)`` of each ``sys.path.insert/append`` whose value depends on the working directory and not on ``__file__``."""
    from_file = _names_where(tree, _reads_file)
    from_cwd = _names_where(tree, lambda expr, names: _reads_cwd(expr, names) and not _reads_file(expr, from_file))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or aliases.qualified_name(node) not in ("sys.path.insert", "sys.path.append"):
            continue
        if not node.args:
            continue
        arg = node.args[-1]
        if _reads_file(arg, from_file):
            continue
        if _reads_cwd(arg, from_cwd):
            out.append((node.lineno, ast.unparse(node)))
    return sorted(out)


# ---------------------------------------------------------------------------------------------------------------------------------------
# The structural gate
# ---------------------------------------------------------------------------------------------------------------------------------------


def _resolve_dirs(root: Path, dirs: Iterable[PathLike]) -> list[Path]:
    out = []
    for d in dirs:
        p = Path(d)
        out.append(p if p.is_absolute() else root / p)
    return out


def _flat_py_files(directory: Path) -> list[Path]:
    """Top-level ``*.py`` files only: a flat script directory's subdirectories are packages or data, not scripts."""
    return sorted(directory.glob("*.py")) if directory.is_dir() else []


class _Corpus:
    """The parsed script and probe files, keyed by path, plus which directory each came from."""

    def __init__(self, root: Path, script_dirs: Sequence[PathLike], probe_dirs: Sequence[PathLike], min_files: int) -> None:
        if not script_dirs:
            raise ValueError("script_entry_points needs at least one script directory: pass the directories that hold the flat scripts")
        self.root = root
        self.script_files = [p for d in _resolve_dirs(root, script_dirs) for p in _flat_py_files(d)]
        script_set = {p.resolve() for p in self.script_files}
        self.probe_files = [p for d in _resolve_dirs(root, probe_dirs) for p in _flat_py_files(d) if p.resolve() not in script_set]
        for d in _resolve_dirs(root, [*script_dirs, *probe_dirs]):
            if not d.is_dir():
                raise FileNotFoundError(f"script directory does not exist: {d}. Pass directories relative to the root {root} or absolute ones")
        self.scan: ScanResult = scan_python([*self.script_files, *self.probe_files], min_files=min_files, root=root)
        self.script_set = script_set


def _collisions(by_name: dict[str, list[ParsedFile]]) -> list[Finding]:
    out: list[Finding] = []
    for name, group in sorted(by_name.items()):
        if len(group) > 1:
            where = ", ".join(g.rel for g in group)
            msg = f"module name `{name}` is defined by more than one script directory ({where}); a bare `import {name}` cannot say which. Rename one of them"
            out.extend(Finding(g.rel, 1, RULE_COLLISION, msg) for g in group)
    return out


def _unregistered(entry_points: dict[str, tuple[ParsedFile, list[ast.If]]], importers: dict[str, list[tuple[str, int]]]) -> list[Finding]:
    out: list[Finding] = []
    for name, (parsed, blocks) in sorted(entry_points.items()):
        if importers[name] and not any(_registers_itself(b, name) for b in blocks):
            who = ", ".join(f"{rel}:{line}" for rel, line in sorted(importers[name]))
            out.append(
                Finding(
                    parsed.rel,
                    blocks[0].lineno,
                    RULE_UNREGISTERED,
                    f'`{name}` is a script (`if __name__ == "__main__"`) that is imported by bare name ({who}) but does not register itself; run as a '
                    f'script the import executes it a second time with its own globals. Add `sys.modules.setdefault("{name}", sys.modules["__main__"])` '
                    'inside an `if __name__ == "__main__":` block before those imports',
                )
            )
    return out


def _structural(corpus: _Corpus) -> tuple[list[Finding], dict[str, Path], dict[str, tuple[ParsedFile, list[ast.If]]]]:
    """(findings, name -> path of every script module, entry points by name)."""
    findings: list[Finding] = []
    by_name: dict[str, list[ParsedFile]] = {}
    scripts = [f for f in corpus.scan if f.path.resolve() in corpus.script_set]
    for parsed in scripts:
        if parsed.path.stem not in _IGNORED_STEMS:
            by_name.setdefault(parsed.path.stem, []).append(parsed)

    findings.extend(_collisions(by_name))
    unique = {name: group[0] for name, group in by_name.items() if len(group) == 1}
    known = frozenset(unique)
    entry_points = {name: (f, blocks) for name, f in unique.items() if (blocks := _main_blocks(f.tree))}

    importers: dict[str, list[tuple[str, int]]] = {name: [] for name in entry_points}
    for parsed in scripts:
        own = parsed.path.stem
        for imported, line in _imported_names(parsed.tree, known).items():
            if imported in importers and imported != own:
                importers[imported].append((parsed.rel, line))

    findings.extend(_unregistered(entry_points, importers))
    findings.extend(
        Finding(
            parsed.rel,
            line,
            RULE_CWD,
            f"`{rendered}` puts the working directory on sys.path; derive it from `__file__` instead (`Path(__file__).resolve().parent`)",
        )
        for parsed in corpus.scan
        for line, rendered in _cwd_path_edits(parsed.tree, ImportAliases.from_tree(parsed.tree))
    )
    return findings, {n: f.path for n, f in unique.items()}, entry_points


def find_script_entry_points(
    root: PathLike,
    script_dirs: Sequence[PathLike],
    *,
    probe_dirs: Sequence[PathLike] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """Every finding for the flat scripts in *script_dirs* (relative to *root*, or absolute), sorted by path and line.

    *probe_dirs* hold further scripts that are checked for ``cwd-on-sys-path`` only: a probe is its own process, so its import of an entry point
    is not a second execution of it, and it takes no part in entry-point or name-collision detection. Only the top-level ``*.py`` of each directory is read. Raises ``EmptyScanError`` when fewer than
    *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*).
    """
    corpus = _Corpus(Path(root), script_dirs, probe_dirs, min_files)
    check_scan(corpus.scan, min_files=min_files, allow_unparsed=allow_unparsed)
    findings, _, _ = _structural(corpus)
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule))


def assert_script_entry_points(
    root: PathLike,
    script_dirs: Sequence[PathLike],
    *,
    probe_dirs: Sequence[PathLike] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Fail on any finding."""
    found = find_script_entry_points(root, script_dirs, probe_dirs=probe_dirs, min_files=min_files, allow_unparsed=allow_unparsed)
    if found:
        raise AssertionError(
            f"{len(found)} script-entry-point finding(s). Register every imported entry point with `sys.modules.setdefault`, derive sys.path "
            "edits from `__file__`, and give every script module a unique name:\n  " + "\n  ".join(f.render() for f in found)
        )


# ---------------------------------------------------------------------------------------------------------------------------------------
# The behavioural half
# ---------------------------------------------------------------------------------------------------------------------------------------

#: Loads the file as ``__main__`` the way a scheduler does and runs everything at module level except the ``if __name__ == "__main__":`` blocks
#: that do the work. In the block that registers, only imports and the registering statement are kept. Then asks for the module by its own name:
#: the answer must be the same object, or a second execution happened.
_DOUBLE_EXEC = """
import ast, importlib, os, sys, types
path, name, dirs = sys.argv[1], sys.argv[2], sys.argv[3:]
for d in reversed(dirs):
    sys.path.insert(0, d)
sys.path.insert(0, os.path.dirname(path))
with open(path, encoding="utf-8-sig") as fh:
    tree = ast.parse(fh.read())

def is_main(node):
    if not isinstance(node, ast.If):
        return False
    t = node.test
    return (isinstance(t, ast.Compare) and any(isinstance(o, ast.Name) and o.id == "__name__" for o in [t.left, *t.comparators])
            and any(isinstance(o, ast.Constant) and o.value == "__main__" for o in [t.left, *t.comparators]))

def registers(stmt):
    return "setdefault" in ast.dump(stmt)

body = []
for node in tree.body:
    if not is_main(node):
        body.append(node)
    elif registers(node):
        node.body = [s for s in node.body if isinstance(s, (ast.Import, ast.ImportFrom)) or registers(s)] or [ast.Pass()]
        body.append(node)
tree.body = body
module = types.ModuleType("__main__")
module.__file__ = path
sys.modules["__main__"] = module
exec(compile(tree, path, "exec"), module.__dict__)
second = importlib.import_module(name)
print("SAME", second is module)
"""


def _tail(text: str, limit: int = 800) -> str:
    text = "\n".join(line for line in text.splitlines() if line.strip())
    return text[-limit:]


def double_execution_findings(
    root: PathLike,
    script_dirs: Sequence[PathLike],
    *,
    skip: Optional[Mapping[str, str]] = None,
    timeout: float = 120.0,
    env: Optional[Mapping[str, str]] = None,
    python: Optional[str] = None,
    max_workers: int = 8,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> list[Finding]:
    """For each entry point that a sibling imports by name, load it as ``__main__`` in a fresh interpreter (its work block removed) and report
    one that is executed again by ``importlib.import_module(name)``.

    *skip* maps an entry-point name to the reason it is not started (a module that does something irreversible at import); an empty reason, or a
    name that is not an exposed entry point, raises ``ValueError``, so a skip cannot rot. *env* is added to the environment of each child (the
    credentials a module reads at import). A child past *timeout* seconds, or one that fails, is a finding naming the file. The cwd of each
    child is *root*; the entry's own directory, then all *script_dirs*, are put on its ``sys.path``, as a scheduler started in each would.
    """
    base = Path(root)
    corpus = _Corpus(base, script_dirs, (), min_files)
    check_scan(corpus.scan, min_files=min_files, allow_unparsed=allow_unparsed)
    _, paths, entry_points = _structural(corpus)
    imported_by = _exposed(corpus, set(entry_points))
    skips = dict(skip or {})
    for name, reason in skips.items():
        if not str(reason).strip():
            raise ValueError(f"skip[{name!r}] has no reason: say why the entry point cannot be started, or remove the skip")
        if name not in imported_by:
            raise ValueError(f"skip[{name!r}] names no entry point that a sibling imports by name; remove the stale skip. Exposed: {sorted(imported_by)}")
    names = sorted(n for n in imported_by if n not in skips)
    dirs = [str(d.resolve()) for d in _resolve_dirs(base, script_dirs)]
    child_env = {**os.environ, **(dict(env) if env else {})}
    exe = python or sys.executable

    def run(name: str) -> Optional[Finding]:
        path = paths[name]
        rel = next(f.rel for f in corpus.scan if f.path == path)
        try:
            proc = subprocess.run(
                [exe, "-c", _DOUBLE_EXEC, str(path), name, *dirs],
                cwd=str(base),
                env=child_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return Finding(rel, 1, RULE_DOUBLE, f"`{name}` did not finish loading as __main__ within {timeout:g}s. Raise the timeout or skip it with a reason")
        if "SAME True" in proc.stdout:
            return None
        return Finding(
            rel,
            1,
            RULE_DOUBLE,
            f"`import {name}` after loading the file as __main__ started a second execution (exit {proc.returncode}); register it with "
            f'`sys.modules.setdefault("{name}", sys.modules["__main__"])` before the sibling imports: {_tail(proc.stdout + proc.stderr)}',
        )

    if not names:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(names)))) as pool:
        results = list(pool.map(run, names))
    return sorted((f for f in results if f is not None), key=lambda f: (f.path, f.line))


def _exposed(corpus: _Corpus, entry_names: set[str]) -> dict[str, list[str]]:
    """``{entry point: importers}`` for the entry points some other file imports by name."""
    known = frozenset(p.stem for p in corpus.script_files if p.stem not in _IGNORED_STEMS)
    out: dict[str, list[str]] = {}
    for parsed in corpus.scan:
        if parsed.path.resolve() not in corpus.script_set:
            continue
        for imported in _imported_names(parsed.tree, known):
            if imported in entry_names and imported != parsed.path.stem:
                out.setdefault(imported, []).append(parsed.rel)
    return out
