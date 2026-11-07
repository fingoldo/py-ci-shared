"""A thread pool whose workers can reach a numba ``parallel=True`` / ``prange`` kernel must pass one process-wide guard.

numba's default threading layer (``workqueue``) is not safe to enter from several Python threads at once. Linux mostly tolerates it; macOS aborts the process with ``Fatal Python error: Aborted`` and
no Python traceback. mlframe's first three-OS CI run crashed 92 xdist workers that way (several pool threads stopped at one ``parallel=True`` call site), and 29 unguarded paths had to be taken to
0 by hand. The check lived only in mlframe's own meta tests; this is its shared form.

The scan is a name-based call graph over the corpus: a function that starts a pool is followed up to ``max_depth`` call hops (default 6: the crash that motivated the check was four hops from the pool,
and CI then found a fifth-hop path, so a depth fitted to the last known bug finds only that bug) through plain calls, functions handed to a call (``pool.map(work, xs)``, ``Thread(target=work)``, ``delayed(work)``) and the members of a module-level
``{"name": fn}`` dispatch table the function mentions. A call lexically inside ``with <guard>():`` is not followed (holding the guard around a call covers everything below it). Rules, by how the
pool starts:

* ``nested-parallel-via-executor``: ``ThreadPoolExecutor(...)`` / ``ThreadPool(...)``.
* ``nested-parallel-via-thread``: ``threading.Thread(...)``.
* ``nested-parallel-via-joblib``: ``joblib.Parallel(..., backend="threading")`` or ``prefer="threads"``.

A kernel is a function decorated with a call named ``njit``/``jit``/``vectorize``/``guvectorize``/``stencil`` carrying ``parallel=True``. The finding sits on the pool line and names the kernel and the hops.

Suppression: ``# parallel-guarded: <reason>`` on the pool line or the ``def`` line of the function that starts it (a one-worker watchdog whose caller blocks on the future cannot race; say so).

Limits: calls are matched by name, so a common method name defined in several modules can add a false path (at most two definitions per name are followed, as in the original check); a kernel called through
an attribute of an object the scan cannot see is missed. The guard's own implementation, and whether it is held at exactly the right call, are not judged.

Usage in a consumer's meta test::

    from py_ci_shared.nested_numba_parallel_guard import assert_nested_numba_parallel_guard

    def test_no_unguarded_nested_parallel():
        assert_nested_numba_parallel_guard("src", guard_name="parallel_kernel_entry", baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = [
    "DEFAULT_GUARD",
    "MARKER",
    "RULE_EXECUTOR",
    "RULE_JOBLIB",
    "RULE_THREAD",
    "RULES",
    "assert_nested_numba_parallel_guard",
    "find_nested_numba_parallel_guard",
]

RULE_EXECUTOR = "nested-parallel-via-executor"
RULE_THREAD = "nested-parallel-via-thread"
RULE_JOBLIB = "nested-parallel-via-joblib"
RULES = (RULE_EXECUTOR, RULE_THREAD, RULE_JOBLIB)
DEFAULT_GUARD = "parallel_kernel_entry"
MARKER = "parallel-guarded:"
SKIP_DIRS = frozenset({"_benchmarks", "legacy", "benchmarks", "profiling", "tests", "test"})
_JIT_NAMES = frozenset({"njit", "jit", "vectorize", "guvectorize", "stencil"})
_EXECUTORS = frozenset({"ThreadPoolExecutor", "ThreadPool"})
_FOLLOWED_DEFINITIONS = 2


def _call_name(func: ast.AST) -> str:
    return getattr(func, "id", None) or getattr(func, "attr", None) or ""


def _is_parallel_true(call: ast.Call) -> bool:
    return any(k.arg == "parallel" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)


def _is_kernel(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    return any(isinstance(d, ast.Call) and _call_name(d.func) in _JIT_NAMES and _is_parallel_true(d) for d in fn.decorator_list)


def _guarded_calls(fn: ast.AST, guard: str) -> "set[int]":
    """``id()`` of every call lexically inside a ``with <guard>():`` block of *fn*."""
    out: "set[int]" = set()
    for node in ast.walk(fn):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if any(isinstance(i.context_expr, ast.Call) and _call_name(i.context_expr.func) == guard for i in node.items):
            out.update(id(c) for c in ast.walk(node) if isinstance(c, ast.Call))
    return out


def _joblib_threads(call: ast.Call) -> bool:
    if _call_name(call.func) != "Parallel":
        return False
    return any(
        isinstance(k.value, ast.Constant) and ((k.arg == "backend" and k.value.value == "threading") or (k.arg == "prefer" and k.value.value == "threads"))
        for k in call.keywords
    )


def _pool_rule(call: ast.Call) -> Optional[str]:
    name = _call_name(call.func)
    if name in _EXECUTORS:
        return RULE_EXECUTOR
    if name == "Thread":
        return RULE_THREAD
    return RULE_JOBLIB if _joblib_threads(call) else None


def _callees(fn: ast.AST, guard: str) -> "set[str]":
    """Names called in *fn* outside the guard, plus bare names handed to any call (`pool.map(work, xs)`, `Thread(target=work)`, `delayed(work)`): a pool worker calls them."""
    guarded = _guarded_calls(fn, guard)
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
    out = {_call_name(c.func) for c in calls if id(c) not in guarded}
    for c in calls:
        out |= {a.id for a in c.args if isinstance(a, ast.Name)}
        out |= {k.value.id for k in c.keywords if isinstance(k.value, ast.Name)}
    out.discard("")
    return out


def _dispatch_tables(tree: ast.AST) -> "dict[str, set[str]]":
    """``{table: function names it holds}`` for ``NAME = {"key": fn, ...}`` literals: a lookup then a call hides the callee from a plain call graph."""
    tables: "dict[str, set[str]]" = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            members = {v.id for v in node.value.values if isinstance(v, ast.Name)}
            for target in node.targets:
                if members and isinstance(target, ast.Name):
                    tables.setdefault(target.id, set()).update(members)
    return tables


def _marked(lines: "list[str]", numbers: "list[int]") -> bool:
    for number in numbers:
        if 0 < number <= len(lines):
            line = lines[number - 1]
            if "#" in line and MARKER in line[line.index("#") :]:
                return True
    return False


@dataclass
class _Graph:
    guard: str
    kernels: "set[str]" = field(default_factory=set)
    calls: "dict[tuple[str, str], set[str]]" = field(default_factory=dict)
    where: "dict[str, list[str]]" = field(default_factory=dict)
    pools: "list[tuple[str, str, int, str]]" = field(default_factory=list)

    def add_function(self, rel: str, fn: "ast.FunctionDef | ast.AsyncFunctionDef", tables: "dict[str, set[str]]", lines: "list[str]") -> None:
        self.where.setdefault(fn.name, []).append(rel)
        edges = _callees(fn, self.guard)
        if not _guarded_calls(fn, self.guard):
            mentioned = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            for table, members in tables.items():
                if table in mentioned:
                    edges |= members
        self.calls[(rel, fn.name)] = edges
        if _is_kernel(fn):
            self.kernels.add(fn.name)
        for call in ast.walk(fn):
            rule = _pool_rule(call) if isinstance(call, ast.Call) else None
            if rule is not None and not _marked(lines, [call.lineno, fn.lineno]):  # type: ignore[attr-defined]
                self.pools.append((rel, fn.name, call.lineno, rule))  # type: ignore[attr-defined]

    def reachable(self, rel: str, name: str, depth: int) -> "dict[str, str]":
        """``{kernel: hop trail}`` of every kernel an unguarded path from this function reaches within *depth* hops."""
        found: "dict[str, str]" = {}
        seen: "set[tuple[str, str]]" = set()
        stack: "list[tuple[str, str, int, tuple[str, ...]]]" = [(rel, name, depth, (name,))]
        while stack:
            r, f, left, trail = stack.pop()
            if (r, f) in seen or left == 0:
                continue
            seen.add((r, f))
            for callee in sorted(self.calls.get((r, f), ())):
                if callee in self.kernels:
                    found.setdefault(callee, " -> ".join((*trail, callee)))
                for other in self.where.get(callee, ())[:_FOLLOWED_DEFINITIONS]:
                    stack.append((other, callee, left - 1, (*trail, callee)))
        return found


def _build(scan: "object", guard: str, skip_dirs: "frozenset[str]") -> _Graph:
    graph = _Graph(guard)
    for parsed in scan:  # type: ignore[attr-defined]
        if skip_dirs & set(parsed.rel.split("/")):
            continue
        tables = _dispatch_tables(parsed.tree)
        lines = parsed.source.splitlines()
        for node in ast.walk(parsed.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                graph.add_function(parsed.rel, node, tables, lines)
    return graph


def find_nested_numba_parallel_guard(
    root: Union[str, Path],
    *,
    guard_name: str = DEFAULT_GUARD,
    max_depth: int = 6,
    skip_dirs: "frozenset[str]" = SKIP_DIRS,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every thread-pool start under *root* with an unguarded path to a ``parallel=True`` kernel, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code
    must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    graph = _build(scan, guard_name, frozenset(skip_dirs))
    out: "list[Finding]" = []
    for rel, fname, line, rule in graph.pools:
        for kernel, trail in sorted(graph.reachable(rel, fname, max_depth).items()):
            out.append(
                Finding(
                    rel,
                    line,
                    rule,
                    f"`{fname}` starts a thread pool and reaches the parallel=True kernel `{kernel}` ({trail}) without `with {guard_name}():`; two threads in one prange region abort the process on macOS",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line, f.rule, f.message))


def assert_nested_numba_parallel_guard(
    root: Union[str, Path],
    *,
    guard_name: str = DEFAULT_GUARD,
    max_depth: int = 6,
    skip_dirs: "frozenset[str]" = SKIP_DIRS,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any unguarded pool-to-kernel path, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_nested_numba_parallel_guard(
        root, guard_name=guard_name, max_depth=max_depth, skip_dirs=skip_dirs, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = f"hold `with {guard_name}():` around the kernel call (preferably at the shared dispatcher), or mark the pool `# parallel-guarded: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="nested_numba_parallel_guard", refresh_command="PY_CI_SHARED_REFRESH=nested_numba_parallel_guard")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} unguarded pool-to-parallel-kernel path(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
