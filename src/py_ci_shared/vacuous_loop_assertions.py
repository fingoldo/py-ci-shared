"""Shared check: a ``for`` loop whose body is only conditional assertions, with nothing
elsewhere asserting the loop actually ran, is a test that passes on zero iterations.

WHERE THIS CAME FROM
---------------------
Two independent findings in the ``new_scraper`` monorepo's 2026-09-03 audit round, closed
2026-09-11-12, both the identical shape:

* ``test_audit_wave3_has_no_active_generated_is_dangerous`` walked a migration file's lines,
  and only asserted inside ``if "is_dangerous" in line and "GENERATED ALWAYS" in line:``. The
  real statement it existed to forbid, re-added wrapped across two lines, put one token on each
  line -- the condition was never true, the loop body never ran, and the test passed against the
  exact defect it was named for.
* ``_logger_print_string_literals``'s two consumers walked whatever an AST extractor yielded and
  asserted a banned glyph was absent from each item -- with nothing asserting the extractor
  yielded anything at all. Renaming the logger it looks for, or converting one call site to an
  f-string, makes the generator yield nothing and the ban lift silently.

CLAUDE.md already names the general template: "a per-line loop that can execute zero times."
This is that template, mechanically.

WHAT THIS CHECKS, AND WHAT IT CANNOT
-------------------------------------
For every ``for`` loop in a test file (scoped to test files by *file_filter*, default
``test_*.py``) whose body consists ENTIRELY of ``assert`` statements and/or ``if`` blocks that
themselves contain only ``assert`` statements (no side effect, no accumulation, nothing that
would make the loop's WORK visible if it ran zero times): the enclosing function must also
contain, outside the loop, one of:

* an ``assert`` on the loop's own iterable expression (or ``len(<iterable>)``), asserting it is
  non-empty, or
* an ``assert`` that mentions the loop variable's name (a weaker but common form: a test that
  captures matches into a list during the loop and asserts on the list afterward already has a
  floor, even though the loop body itself would satisfy the pattern above).

It is an AST walk, not a data-flow analysis, and it accepts textual overlap rather than proving
the floor assertion covers the SAME collection -- the same trade `effect_assertion_parity` states
about its own heuristic. A test with two unrelated loops and one unrelated non-emptiness assert
can slip past. What it catches is the total absence of any floor at all, which is what both
findings above were.

Usage::

    from py_ci_shared.vacuous_loop_assertions import assert_no_new_floorless_loop

    def test_no_new_floorless_loop():
        assert_no_new_floorless_loop(
            files=sorted((REPO / "tests").rglob("test_*.py")),
            repo_root=REPO,
            baseline_path=Path(__file__).with_name("_vacuous_loop_baseline.json"),
        )
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Union

from ._core import relative_posix, scan_python

__all__ = ["FloorlessLoop", "find_floorless_loops", "assert_no_new_floorless_loop"]


class FloorlessLoop:
    __slots__ = ("function", "lineno", "ordinal", "path")

    def __init__(self, path: str, function: str, lineno: int, ordinal: int = 1) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno
        # 1-based position among this function's floorless loops, in source order.
        self.ordinal = ordinal

    @property
    def key(self) -> str:
        # Keyed on the ordinal, not the line: a line number goes stale on any edit above the loop, which turned
        # every baselined loop into a "new" one plus a "stale" one without anything about the loop changing.
        return f"{self.path}::{self.function}::#{self.ordinal}"

    @property
    def scope(self) -> str:
        """``path::function``: what the ratchet counts against, for both the current and the line-numbered key form."""
        return f"{self.path}::{self.function}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"FloorlessLoop({self.key})"


_FAIL_CALLS = frozenset({"fail"})


def _is_check(s: ast.stmt) -> bool:
    """A statement that can only ever VERIFY: ``assert``, ``raise``, or a bare ``pytest.fail(...)``-style call."""
    if isinstance(s, (ast.Assert, ast.Raise)):
        return True
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
        func = s.value.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        return name in _FAIL_CALLS
    return False


def _only_checks(stmts: "list[ast.stmt]") -> "tuple[bool, bool]":
    """``(every statement verifies or is flow control, at least one verifies)`` for a block."""
    any_check = False
    for s in stmts:
        if _is_check(s):
            any_check = True
        elif isinstance(s, (ast.Pass, ast.Continue, ast.Break)):
            continue
        elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
            continue  # a docstring-like string statement
        elif isinstance(s, ast.If):
            body_ok, body_any = _only_checks(s.body)
            else_ok, else_any = _only_checks(s.orelse)
            if not (body_ok and else_ok):
                return False, False
            any_check = any_check or body_any or else_any
        elif isinstance(s, (ast.With, ast.AsyncWith)):  # `with subtests.test(...)`, `with pytest.raises(...)`
            ok, inner_any = _only_checks(s.body)
            if not ok:
                return False, False
            any_check = any_check or inner_any
        else:
            return False, False
    return True, any_check


def _is_assert_only(stmts: list[ast.stmt]) -> bool:
    """True if the block only verifies: ``assert``/``raise``/``pytest.fail``, guarded by ``if``, inside a ``with``
    (``subtests``), with ``continue``/``pass``/``break`` for flow. A body with no check at all (a bare ``pass``) is not
    this finding's shape: an empty loop body is a no-op the reader can already see."""
    if not stmts:
        return False
    ok, any_check = _only_checks(stmts)
    return ok and any_check


_Loop = Union[ast.For, ast.AsyncFor, ast.While]
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]


def _own_nodes(fn: _FuncDef) -> Iterator[ast.AST]:
    """Nodes of *fn*'s own body; a nested ``def``/``lambda``/``class`` is its own scope and is not entered."""
    scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack: list[ast.AST] = [n for n in fn.body if not isinstance(n, scoped)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, scoped))


def _enclosing_loops(fn: _FuncDef) -> "dict[int, frozenset[int]]":
    """``id(node) -> ids of the loops whose BODY contains it`` for every node of *fn*'s own body."""
    out: dict[int, frozenset[int]] = {}
    scoped = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

    def visit(node: ast.AST, loops: "frozenset[int]") -> None:
        out[id(node)] = loops
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            for child in ast.iter_child_nodes(node):
                in_body = any(child is s for s in [*node.body, *node.orelse])
                if not isinstance(child, scoped):
                    visit(child, loops | {id(node)} if in_body else loops)
            return
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, scoped):
                visit(child, loops)

    for stmt in fn.body:
        if not isinstance(stmt, scoped):
            visit(stmt, frozenset())
    return out


def _floor_exists(fn: ast.FunctionDef | ast.AsyncFunctionDef, loop: ast.For | ast.AsyncFor, enclosing: "dict[int, frozenset[int]] | None" = None) -> bool:
    """An `assert` OUTSIDE the loop that mentions the iterable's own source text, `len(...)` of
    it, or the loop variable's name -- see the module docstring for what this does and does not
    prove. An assert inside ANOTHER loop that does not also enclose this one is not a floor: that loop can run
    zero times too (two floorless ``for x in ...`` loops used to vouch for each other)."""
    enclosing = enclosing if enclosing is not None else _enclosing_loops(fn)
    allowed_loops = enclosing.get(id(loop), frozenset())
    try:
        iter_src = ast.unparse(loop.iter)
    except Exception:  # unparse is best-effort; missing it just narrows the check
        iter_src = None
    var_names = {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}

    loop_ids = {id(n) for n in ast.walk(loop)}
    for node in _own_nodes(fn):
        if id(node) in loop_ids or not isinstance(node, ast.Assert):
            continue
        if not enclosing.get(id(node), frozenset()) <= allowed_loops:
            continue
        try:
            test_src = ast.unparse(node.test)
        except Exception:  # unparse is best-effort, as above
            continue
        if iter_src and iter_src in test_src:
            return True
        if var_names & {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}:
            return True
    return False


def _iterates_a_nonempty_literal(loop) -> bool:
    """True when the loop's iterable is a literal collection with elements in it.

    `for x in ("a", "b"): assert x in thing` needs no floor: the iterable is written out at the
    loop, a reader can count it, and it cannot arrive empty however the rest of the file changes.
    Flagging it asks for an `assert ("a", "b")` whose answer is visible in the line above -- noise
    that trains the reader to add the assertion without thinking, which is the opposite of what this
    check is for.

    Only NON-EMPTY literals. `for x in []:` really is a loop that never runs, and the whole point of
    this check is that such a loop reads as if it verifies something.
    """
    iterable = loop.iter
    # `enumerate((...))` / `sorted([...])` and friends: the floor is still visible at the loop.
    if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name):
        if iterable.func.id in {"enumerate", "sorted", "reversed", "list", "tuple", "set"} and iterable.args:
            iterable = iterable.args[0]
    # `[*m]` is as empty as `m`: only an element that is not unpacked guarantees an iteration.
    return isinstance(iterable, (ast.Tuple, ast.List, ast.Set)) and any(not isinstance(e, ast.Starred) for e in iterable.elts)


def find_floorless_loops(
    files: Iterable[Path],
    repo_root: Path,
    *,
    allow_unparsed: bool = False,
) -> list[FloorlessLoop]:
    """Every floorless assert-only loop in *files*, each reported once, under the function that owns it (a loop in a
    nested function belongs to that function, not also to the outer one).

    Keys are relative to *repo_root* whatever the working directory (a relative path is resolved first), or the
    absolute POSIX path for a file outside it. An unreadable or unparsable file raises
    :class:`py_ci_shared._core.UnparsedFilesError` unless ``allow_unparsed=True``.
    """
    return _floorless_loops(files, repo_root, allow_unparsed=allow_unparsed)[0]


def _floorless_loops(files: Iterable[Path], repo_root: Path, *, allow_unparsed: bool = False) -> "tuple[list[FloorlessLoop], int]":
    """``(loops, parsed file count)`` for :func:`find_floorless_loops`."""
    scan = scan_python([Path(p).resolve() for p in files], min_files=0, root=Path(repo_root).resolve())
    if scan.unparsed and not allow_unparsed:
        scan.check_unparsed()
    out: list[FloorlessLoop] = []
    for parsed in scan:
        tree = parsed.tree
        rel = relative_posix(parsed.path, Path(repo_root).resolve())
        per_file: list[tuple[str, int]] = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            enclosing = _enclosing_loops(fn)
            for node in _own_nodes(fn):
                if not isinstance(node, (ast.For, ast.AsyncFor)):
                    continue
                if not _is_assert_only(node.body):
                    continue
                if _iterates_a_nonempty_literal(node):
                    continue
                if _floor_exists(fn, node, enclosing):
                    continue
                per_file.append((fn.name, node.lineno))
        seen: dict[str, int] = {}
        for name, lineno in sorted(per_file, key=lambda t: t[1]):
            seen[name] = seen.get(name, 0) + 1
            out.append(FloorlessLoop(rel, name, lineno, seen[name]))
    return out, scan.parsed_count


def _scope_of(key: str) -> str:
    """``path::function`` of a baseline key, whether it ends in ``::#<ordinal>`` or the older ``::<lineno>``."""
    return key.rsplit("::", 1)[0]


def assert_no_new_floorless_loop(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
    *,
    min_files: int = 1,
) -> None:
    """Fail on a floorless loop not already in *baseline_path*, on a test file that cannot be parsed, and on fewer
    than *min_files* parsed files. Ratchet, not a gate: the baseline records what was already true, and the list can
    only shrink from here."""
    accepted: dict[str, str] = json.loads(baseline_path.read_text(encoding="utf-8-sig")) if baseline_path.exists() else {}
    found, parsed_count = _floorless_loops(files, repo_root)
    if parsed_count < min_files:
        raise AssertionError(f"only {parsed_count} test file(s) parsed; expected at least {min_files}. The scan lost its subject.")
    # Counted per function: a function may keep as many floorless loops as the baseline lists for it. That is what
    # both key forms can express, and it survives edits that move a loop without adding one.
    allowed: dict[str, int] = {}
    for key in accepted:
        allowed[_scope_of(key)] = allowed.get(_scope_of(key), 0) + 1
    by_scope: dict[str, list[FloorlessLoop]] = {}
    for loop in found:
        by_scope.setdefault(loop.scope, []).append(loop)
    new = {loop.key: loop for scope, loops in by_scope.items() for loop in loops[allowed.get(scope, 0) :]}
    if new:
        lines = "\n  ".join(f"{loop.key} (line {loop.lineno})" for loop in sorted(new.values(), key=lambda loop: loop.key))
        raise AssertionError(
            f"{len(new)} loop(s) whose body is only conditional asserts, with nothing outside the "
            f"loop asserting it iterated at all -- zero matches is a silent pass, not a failure:\n  {lines}\n"
            "Add `assert <the collection>` (or `assert list(<generator>)`) before the loop, or if the "
            "loop's own emptiness IS the thing under test, record it in the baseline with a reason."
        )
    stale: list[str] = []
    for scope, n_allowed in allowed.items():
        n_found = len(by_scope.get(scope, ()))
        if n_allowed > n_found:
            stale.extend(sorted(k for k in accepted if _scope_of(k) == scope)[n_found:])
    stale.sort()
    if stale:
        raise AssertionError(f"these baseline entries no longer describe a floorless loop -- remove them: {stale}")
