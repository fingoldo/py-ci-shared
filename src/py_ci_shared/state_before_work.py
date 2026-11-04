"""A schedule stamp or a progress counter is written immediately before the fallible work it gates.

``self.last_full_scan = time.monotonic()`` followed by the query: when the query fails (or finds nothing) the scheduler already recorded "done",
so the next attempt waits a full interval and nothing alerts; ``processed += 1`` followed by the write counts a batch whose every write fails as
progress. The stamp or counter belongs AFTER the work succeeded.

Reported, inside any statement list (function body, loop, ``if``, ``try`` body, ...):

* ``stamp``: an assignment to a name or attribute matching *stamp_regex* (default ``(last|next)_(full|sweep|run|scan|cycle|refresh)``), whose
  value is a clock call (``time.time()``, ``time.monotonic()``, ``datetime.now()``, ``utcnow()``, ``perf_counter()``...), when the NEXT statement
  makes a call that is not a clock, logging or ``print`` call;
* ``counter``: an augmented assignment ``x += ...`` to a name or attribute matching *counter_regex* (default
  ``processed|written|inserted|saved|sent``) when the NEXT statement calls a write-like method (*work_names*: ``write``, ``insert``, ``flush``,
  ``commit``, ``execute``, ``save``, ``send``, ``put``, ``upsert``).

Not reported: query-then-stamp; a stamp whose target is also assigned in an ``except`` or ``finally`` of the same function (restored on failure);
a stamp followed only by logging or clock calls; a line carrying ``# stamp-first-ok: <reason>``.

Usage::

    from py_ci_shared.state_before_work import assert_state_before_work

    def test_state_is_written_after_the_work():
        assert_state_before_work(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = ["RULE", "assert_state_before_work", "find_state_before_work"]

RULE = "state-before-work"
MARKER = "stamp-first-ok"
DEFAULT_STAMP_REGEX = r"(last|next)_(full|sweep|run|scan|cycle|refresh)"
DEFAULT_COUNTER_REGEX = r"processed|written|inserted|saved|sent"
DEFAULT_WORK_NAMES = frozenset({"write", "insert", "flush", "commit", "execute", "executemany", "save", "send", "put", "upsert"})
_CLOCKS = frozenset({"time", "monotonic", "perf_counter", "time_ns", "monotonic_ns", "now", "utcnow", "today", "timestamp", "fromtimestamp"})
_NOISE = frozenset({"print", "debug", "info", "warning", "warn", "error", "exception", "log", "isoformat", "strftime", "float", "int", "str", "round"})


def _final(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _target_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_clock_value(value: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and _final(n.func) in _CLOCKS for n in ast.walk(value))


def _calls(stmt: ast.stmt) -> list:
    out: list = []
    stack: list = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _restored(fn: ast.AST, name: str) -> bool:
    """*name* is assigned inside an ``except``/``finally`` of *fn*: the stamp is put back on failure."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            for part in [*node.handlers, *[ast.Module(body=node.finalbody, type_ignores=[])]]:
                for sub in ast.walk(part):
                    targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target] if isinstance(sub, (ast.AugAssign, ast.AnnAssign)) else []
                    if any(_target_name(t) == name for t in targets):
                        return True
    return False


def _statement_lists(tree: ast.Module) -> Iterable:
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack: list = [fn]
        while stack:
            node = stack.pop()
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    yield fn, block
            for handler in getattr(node, "handlers", []):
                yield fn, handler.body
                stack.append(handler)
            stack.extend(
                c for c in ast.iter_child_nodes(node) if isinstance(c, ast.stmt) and not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            )


def _classify(cur: ast.stmt, stamp: "re.Pattern[str]", counter: "re.Pattern[str]") -> tuple:
    if isinstance(cur, ast.Assign) and len(cur.targets) == 1 and _is_clock_value(cur.value):
        name = _target_name(cur.targets[0])
        return ("stamp", name) if name and stamp.search(name) else (None, None)
    if isinstance(cur, ast.AugAssign) and isinstance(cur.op, ast.Add):
        name = _target_name(cur.target)
        return ("counter", name) if name and counter.search(name) else (None, None)
    return None, None


def _findings_in(parsed_tree: ast.Module, rel: str, lines: list, stamp: "re.Pattern[str]", counter: "re.Pattern[str]", work: frozenset) -> list:
    out: list = []
    seen: set = set()
    for fn, block in _statement_lists(parsed_tree):
        for cur, nxt in zip(block, block[1:]):
            if id(cur) in seen:
                continue
            kind, name = _classify(cur, stamp, counter)
            if kind is None or name is None:
                continue
            nxt_calls = _calls(nxt)
            if kind == "stamp":
                risky = [c for c in nxt_calls if _final(c.func) not in _CLOCKS and _final(c.func) not in _NOISE]
                if not risky or _restored(fn, name):
                    continue
                what = f"{_final(risky[0].func)}(...)"
            else:
                risky = [c for c in nxt_calls if _final(c.func) in work]
                if not risky:
                    continue
                what = f"{_final(risky[0].func)}(...)"
            if any(MARKER in lines[n - 1] for n in (cur.lineno, cur.lineno - 1) if 0 < n <= len(lines)):
                continue
            seen.add(id(cur))
            out.append(
                Finding(
                    rel,
                    cur.lineno,
                    RULE,
                    f"{fn.name}: {kind} {name} is written before {what} (line {nxt.lineno}); if that work fails or finds nothing the state already says it "
                    "succeeded. Write it after the work",
                )
            )
    return out


def find_state_before_work(
    root: Union[str, Path],
    *,
    stamp_regex: str = DEFAULT_STAMP_REGEX,
    counter_regex: str = DEFAULT_COUNTER_REGEX,
    work_names: Iterable[str] = DEFAULT_WORK_NAMES,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every stamp or counter written just before its work under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    stamp, counter, work = re.compile(stamp_regex), re.compile(counter_regex), frozenset(work_names)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, parsed.source.splitlines(), stamp, counter, work))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_state_before_work(
    root: Union[str, Path],
    *,
    stamp_regex: str = DEFAULT_STAMP_REGEX,
    counter_regex: str = DEFAULT_COUNTER_REGEX,
    work_names: Iterable[str] = DEFAULT_WORK_NAMES,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any stamp or counter written before its work, or with *baseline_path* on any the baseline does not accept."""
    found = find_state_before_work(
        root,
        stamp_regex=stamp_regex,
        counter_regex=counter_regex,
        work_names=work_names,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = "write the stamp or counter after the work succeeded (or restore it in except/finally)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="state_before_work", refresh_command="PY_CI_SHARED_REFRESH=state_before_work")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} state-before-work finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
