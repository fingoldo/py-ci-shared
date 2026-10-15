"""A blocking call made while a lock is held: every other thread that wants that lock waits for the I/O to finish.

A ``with <lock>:`` block whose body opens a connection, sends a request, runs a subprocess, sleeps or writes a file turns the
lock into a queue in front of the network. One slow peer then stalls every caller, and a peer that never answers stalls them
forever. The shape that prompted the rule: a connection pool handed out under its lock, so the pool's ``connect`` ran with
every other worker blocked behind it.

Flagged: a ``with`` item whose expression is lock-like, and a call inside its body that is one of:

* ``connect``, ``connect_*``, ``getconn``, ``urlopen`` (by name, on any object);
* anything resolved under ``requests``, ``httpx``, ``socket`` or ``subprocess``;
* ``time.sleep``;
* ``open`` (or ``.open``) with a constant mode that writes (``w``, ``a``, ``x`` or ``+``).

A lock-like expression is a name or attribute ending in ``_lock``, ``Lock``, ``RLock``, ``Condition`` (or equal to ``lock``,
``rlock``, ``condition``), a name the module binds to ``threading.Lock()``/``RLock()``/``Condition()``, or such a constructor
written inline. A call in a nested ``def`` or ``lambda`` is not executed by the block and is not a finding.

Not judged: a lock acquired by a method call (``with pool.acquire():``) and an I/O call hidden behind another function. Those
are left to review, so the gate errs toward silence.

Usage::

    from py_ci_shared.lock_held_across_io import assert_no_lock_held_across_io

    def test_no_lock_held_across_io():
        assert_no_lock_held_across_io(_py_files(REPO / "src"))
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, scan_python

__all__ = ["RULE", "assert_no_lock_held_across_io", "find_lock_held_across_io", "is_lock_like", "module_lock_names"]

RULE = "lock-held-across-io"
LOCK_CONSTRUCTORS = frozenset({"Lock", "RLock", "Condition"})
_IO_MODULES = frozenset({"requests", "httpx", "socket", "subprocess"})
_IO_NAMES = frozenset({"connect", "getconn", "urlopen"})
_WRITE_MODE_CHARS = frozenset("wax+")
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_WITH = (ast.With, ast.AsyncWith)


def _last_segment(node: ast.AST) -> str:
    """``self._lock`` -> ``_lock``; ``threading.Lock()`` -> ``Lock``; anything else -> empty."""
    if isinstance(node, ast.Call):
        return _last_segment(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _named_like_a_lock(segment: str) -> bool:
    return segment in {"lock", "rlock", "condition"} or segment.endswith(("_lock", "_rlock", "_condition", "Lock", "RLock", "Condition"))


def module_lock_names(tree: ast.Module) -> frozenset[str]:
    """Attribute and variable names bound anywhere in the module to a Lock, RLock or Condition constructor."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _last_segment(node.value) in LOCK_CONSTRUCTORS and isinstance(node.value, ast.Call):
            names.update(_last_segment(t) for t in node.targets if _last_segment(t))
    return frozenset(names)


def is_lock_like(expr: ast.AST, lock_names: frozenset[str]) -> bool:
    """True when a ``with`` item's expression names a lock (see the module docstring)."""
    if isinstance(expr, ast.Call) and _last_segment(expr.func) in LOCK_CONSTRUCTORS:
        return True
    segment = _last_segment(expr)
    return bool(segment) and (segment in lock_names or _named_like_a_lock(segment))


def _open_writes(call: ast.Call) -> bool:
    """``open(path, "w")`` or ``open(path, mode="ab")``: a constant mode with a write flag."""
    mode: Optional[ast.AST] = call.args[1] if len(call.args) >= 2 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and bool(_WRITE_MODE_CHARS & set(mode.value))


def io_label(call: ast.Call, aliases: ImportAliases) -> Optional[str]:
    """The call's dotted name when it is one of the blocking calls, else ``None``."""
    name = aliases.qualified_name(call.func) or _last_segment(call.func)
    last = _last_segment(call.func)
    if last == "open":
        return f"{name}(write mode)" if _open_writes(call) else None
    if name == "time.sleep":
        return name
    if name.split(".", 1)[0] in _IO_MODULES and "." in name:
        return name
    if last in _IO_NAMES or last.startswith("connect_"):
        return name
    return None


def _calls_under(body: list[ast.stmt]) -> Iterator[ast.Call]:
    """Every call the block executes: nested ``def``, ``class`` and ``lambda`` bodies are other scopes and are skipped."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


_REVIEWED_MARKER = "# lock-io-ok:"


def _reviewed_io(source_lines: list[str], lineno: int) -> bool:
    """True when the call's own line carries ``# lock-io-ok: <reason>`` with a non-empty reason.

    The reason is the reviewed decision: a site where the I/O must stay under the lock (a cross-process file lock
    whose append would tear without it) says so next to the call, and the gate accepts only that explicit form.
    """
    line = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
    marker = line.find(_REVIEWED_MARKER)
    return marker >= 0 and bool(line[marker + len(_REVIEWED_MARKER) :].strip())


def _findings_in(parsed: ParsedFile) -> list[Finding]:
    aliases = ImportAliases.from_tree(parsed.tree)
    source_lines = parsed.source.splitlines()
    lock_names = module_lock_names(parsed.tree)
    out: list[Finding] = []
    reported: set[int] = set()
    for node in ast.walk(parsed.tree):
        if not isinstance(node, _WITH):
            continue
        held = [ast.unparse(item.context_expr) for item in node.items if is_lock_like(item.context_expr, lock_names)]
        if not held:
            continue
        for call in _calls_under(node.body):
            label = io_label(call, aliases)
            if label is None or id(call) in reported or _reviewed_io(source_lines, call.lineno):
                continue
            reported.add(id(call))
            out.append(
                Finding(
                    parsed.rel,
                    call.lineno,
                    RULE,
                    f"{label} runs while lock {held[0]} is held: every thread waiting for the lock waits for this I/O; "
                    "move the call out of the with-block and keep only the state change under the lock",
                )
            )
    return out


def find_lock_held_across_io(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every blocking call made under a held lock in *files*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed, and ``UnparsedFilesError`` for a file that cannot be read
    or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(list(files), min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_no_lock_held_across_io(
    files: Iterable[Union[str, Path]],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any blocking call made while a lock is held (see the module docstring)."""
    found = find_lock_held_across_io(files, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(
            f"{len(found)} lock-held-across-io finding(s): a blocking call under a lock stalls every thread that needs the lock:\n  "
            + "\n  ".join(f.render() for f in found)
        )
