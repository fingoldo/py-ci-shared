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
from collections.abc import Iterable
from pathlib import Path

__all__ = ["FloorlessLoop", "find_floorless_loops", "assert_no_new_floorless_loop"]


class FloorlessLoop:
    __slots__ = ("path", "function", "lineno")

    def __init__(self, path: str, function: str, lineno: int) -> None:
        self.path = path
        self.function = function
        self.lineno = lineno

    @property
    def key(self) -> str:
        return f"{self.path}::{self.function}::{self.lineno}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"FloorlessLoop({self.key})"


def _is_assert_only(stmts: list[ast.stmt]) -> bool:
    """True if every statement is an `assert`, or an `if` whose own branches are assert-only.
    A single `pass`/docstring-only body is not assert-only -- an empty loop body is not this
    finding's shape, it is a no-op the reader can already see."""
    if not stmts:
        return False
    for s in stmts:
        if isinstance(s, ast.Assert):
            continue
        if isinstance(s, ast.If) and _is_assert_only(s.body) and _is_assert_only(s.orelse or [ast.Assert(test=ast.Constant(value=True))]):
            continue
        return False
    return True


def _floor_exists(fn: ast.FunctionDef | ast.AsyncFunctionDef, loop: ast.For | ast.AsyncFor) -> bool:
    """An `assert` OUTSIDE the loop that mentions the iterable's own source text, `len(...)` of
    it, or the loop variable's name -- see the module docstring for what this does and does not
    prove."""
    try:
        iter_src = ast.unparse(loop.iter)
    except Exception:  # noqa: BLE001 -- unparse is best-effort; missing it just narrows the check
        iter_src = None
    var_names = {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}

    loop_ids = {id(n) for n in ast.walk(loop)}
    for node in ast.walk(fn):
        if id(node) in loop_ids or not isinstance(node, ast.Assert):
            continue
        try:
            test_src = ast.unparse(node.test)
        except Exception:  # noqa: BLE001
            continue
        if iter_src and iter_src in test_src:
            return True
        if var_names & {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}:
            return True
    return False


def find_floorless_loops(
    files: Iterable[Path],
    repo_root: Path,
) -> list[FloorlessLoop]:
    out: list[FloorlessLoop] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(repo_root).as_posix() if path.is_absolute() else path.as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, (ast.For, ast.AsyncFor)):
                    continue
                if not _is_assert_only(node.body):
                    continue
                if _floor_exists(fn, node):
                    continue
                out.append(FloorlessLoop(rel, fn.name, node.lineno))
    return out


def assert_no_new_floorless_loop(
    files: Iterable[Path],
    repo_root: Path,
    baseline_path: Path,
) -> None:
    """Fail on a floorless loop not already in *baseline_path*. Ratchet, not a gate: the
    baseline records what was already true, and the list can only shrink from here."""
    accepted: dict[str, str] = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    found = find_floorless_loops(files, repo_root)
    new = {loop.key: loop for loop in found if loop.key not in accepted}
    if new:
        lines = "\n  ".join(f"{loop.key}" for loop in sorted(new.values(), key=lambda loop: loop.key))
        raise AssertionError(
            f"{len(new)} loop(s) whose body is only conditional asserts, with nothing outside the "
            f"loop asserting it iterated at all -- zero matches is a silent pass, not a failure:\n  {lines}\n"
            "Add `assert <the collection>` (or `assert list(<generator>)`) before the loop, or if the "
            "loop's own emptiness IS the thing under test, record it in the baseline with a reason."
        )
    stale = sorted(k for k in accepted if k not in {loop.key for loop in found})
    if stale:
        raise AssertionError(f"these baseline entries no longer describe a floorless loop -- remove them: {stale}")
