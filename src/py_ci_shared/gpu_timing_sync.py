"""Shared check: a wall-clock measurement taken around GPU work must synchronize the device
before the timer stops.

A CUDA launch is asynchronous: it returns to Python as soon as the work is *queued*, not when it
completes. A ``t0 = perf_counter(); kernel(...); elapsed = perf_counter() - t0`` sequence therefore
measures launch overhead, not compute. The measured under-count is not a rounding error -- on a
cupy 4000x4000 float32 matmul it was 0.0366 ms unsynchronized vs 69.42 ms synchronized, a 1894x
gap. When such a number is then persisted (a kernel-tuning cache, a chosen-backend record) the
wrong backend is locked in across sessions and across projects, long after the run that measured
it is gone.

Two shapes are flagged, both requiring the absence of a device synchronization AFTER the last timed work
inside the timed region (a synchronize before the kernel waits for nothing being measured):

1. A *direct* GPU call in the timed region -- a callee whose dotted name is rooted at ``cuda``,
   ``cupy``, ``cp``, ``torch.cuda``, ``numba.cuda``, ``cudf``, ``cuml``, or a numba-cuda kernel
   launch (``kern[blocks, threads](...)``, a call on a subscript).

2. An *injected* callable in the timed region -- ``fn(*args)`` where ``fn`` is a parameter of an
   enclosing function -- inside a module that is demonstrably GPU-aware. This is the benchmark-
   harness shape: the harness cannot see what it times, so a rule keyed on the callee name never
   fires, yet a GPU-timing harness that never synchronizes mis-measures every GPU backend handed
   to it. Shape 1 alone misses exactly the case with the largest blast radius.

Entry points mirror this package's other checks: ``find_unsynchronized_gpu_timings`` returns
findings, ``assert_no_unsynchronized_gpu_timings`` fails a pytest run on any that is not allowed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple, Optional

from ._core import ImportAliases, ScanResult, scan_python

# Suppression marker for a DELIBERATE launch-latency (async) measurement, placed as a comment
# anywhere in the timed region. Such a measurement is legitimate but must say so: an unlabelled
# unsynchronized timer is indistinguishable from the bug.
SUPPRESSION_MARKER = "gpu-timing-async-intentional"

# Callee roots that are unambiguously device work when called.
_GPU_ROOTS = frozenset({"cuda", "cupy", "cp", "cudf", "cuml"})
_GPU_DOTTED_PREFIXES = ("torch.cuda.", "numba.cuda.")

# Timer readers whose result is a wall-clock instant.
_TIMER_ATTRS = frozenset({"perf_counter", "perf_counter_ns", "time", "time_ns", "monotonic", "monotonic_ns", "process_time", "default_timer"})
_TIMER_NAMES = frozenset({"timer", "perf_counter", "monotonic", "clock", "_timer", "default_timer"})
#: Fully qualified timers, matched through the module's imports (``from time import perf_counter as pc``).
_TIMER_QUALIFIED = frozenset(
    {
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.process_time",
        "timeit.default_timer",
    }
)

# Text-level prefilter: a call to any timer name above, or an import from time/timeit (which is how an aliased timer
# arrives). A file it does not match cannot contain a timed region, so it is not scanned; a match only means "look".
_TIMER_CALL_TEXT_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_TIMER_ATTRS | _TIMER_NAMES, key=len, reverse=True)) + r")\s*\(|\bfrom\s+(?:time|timeit)\s+import\b|\bimport\s+(?:time|timeit)\b"
)

# The words that make a name a device synchronization. A project's own wrapper (``_gpu_sync``,
# ``synchronize_gpu_if_available``, ``ev.synchronize``) is the normal way this is spelled, and an unrecognized sync name
# would produce a false POSITIVE on correct code, so any ``synchroniz*``/``synchronis*`` word counts. A bare ``sync``
# word counts only alone or next to a device word: ``_sync_to_disk`` is a file flush, not a device barrier.
_SYNC_WORD_RE = re.compile(r"(?i)synchroni[sz]")
_DEVICE_WORDS = frozenset({"gpu", "gpus", "cuda", "device", "devices", "stream", "streams", "torch", "cp", "cupy", "all", "event", "ev"})

# Evidence that a module deals with GPUs at all, gating shape 2. Matched as whole words against the
# file's source text, so an unrelated module that merely spells "group" is not GPU-aware.
_GPU_AWARE_RE = re.compile(r"(?i)\b(?:cupy|gputil|pynvml|nvidia_smi|cuda|gpu)\b")


class GpuTimingFinding(NamedTuple):
    """One timed region that measures GPU work without synchronizing the device first."""

    path: Path
    lineno: int
    function: str
    shape: str  # "direct-gpu-call" or "injected-callable-in-gpu-module"
    detail: str

    @property
    def key(self) -> str:
        """Stable ``<file>::<function>`` identity for allowlisting, independent of line drift."""
        return f"{self.path.as_posix()}::{self.function}"


def _dotted_name(node: ast.AST) -> Optional[str]:
    """``"cp.cuda.Stream"`` for an attribute/name chain, else None."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _is_timer_read(node: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if aliases is not None:
        qualified = aliases.qualified_name(func)
        if qualified in _TIMER_QUALIFIED:
            return True
    if isinstance(func, ast.Attribute):
        return func.attr in _TIMER_ATTRS
    if isinstance(func, ast.Name):
        return func.id in _TIMER_NAMES
    return False


def _contains_timer_read(node: ast.AST, aliases: Optional[ImportAliases] = None) -> bool:
    return any(_is_timer_read(sub, aliases) for sub in ast.walk(node))


def _gpu_call_name(node: ast.Call) -> Optional[str]:
    """The callee's name when the call is device work, else None."""
    if isinstance(node.func, ast.Subscript):
        # numba-cuda kernel launch: kern[blocks, threads](...)
        inner = _dotted_name(node.func.value)
        return f"{inner}[...]" if inner else "<kernel>[...]"
    name = _dotted_name(node.func)
    if not name:
        return None
    root = name.split(".")[0]
    if root in _GPU_ROOTS and "." in name:
        return name
    if name.startswith(_GPU_DOTTED_PREFIXES):
        return name
    return None


def _is_sync_name(name: str) -> bool:
    if _SYNC_WORD_RE.search(name):
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    return "sync" in words and (len(words) == 1 or bool(_DEVICE_WORDS.intersection(words)))


# A device-to-host copy blocks until the device has finished the work it reads, so it ends a timed region the way a
# synchronize does (``cp.asnumpy(d_R)`` after the matmul).
_BLOCKING_COPIES = frozenset({"asnumpy"})


def _is_sync_call(node: ast.Call) -> bool:
    """Judged on the called name itself, so a call earlier in the chain does not hide it
    (``torch.cuda.current_stream().synchronize()``)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in _BLOCKING_COPIES and _gpu_call_name(node):
            return True
        return _is_sync_name(func.attr)
    if isinstance(func, ast.Name):
        return _is_sync_name(func.id)
    return False


def _param_names(func: ast.AST) -> set[str]:
    names: set[str] = set()
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return names
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    return names


def _timed_regions(body: list[ast.stmt], aliases: Optional[ImportAliases] = None) -> list[tuple[int, list[ast.stmt]]]:
    """``(start_lineno, statements)`` for every ``t0 = <timer>()`` ... ``<timer>()`` window in ``body``.

    The window closes at the first later statement that reads the timer again -- that read is the
    stop, and the statement carrying it is included, because the stop and the last timed work often
    share a line (``local.append(timer() - t0)``).
    """
    regions: list[tuple[int, list[ast.stmt]]] = []
    for i, stmt in enumerate(body):
        if not (isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None and _is_timer_read(stmt.value, aliases)):
            continue
        end = len(body)
        for j in range(i + 1, len(body)):
            if _contains_timer_read(body[j], aliases):
                end = j + 1
                break
        regions.append((stmt.lineno, body[i + 1 : end]))
    return regions


def _region_is_suppressed(region: list[ast.stmt], start_lineno: int, source_lines: list[str]) -> bool:
    end = max((getattr(s, "end_lineno", None) or s.lineno) for s in region) if region else start_lineno
    return any(SUPPRESSION_MARKER in line for line in source_lines[start_lineno - 1 : end])


def _scan_functions(tree: ast.AST, gpu_aware: bool, source_lines: list[str], path: Path, aliases: Optional[ImportAliases] = None) -> Iterator[GpuTimingFinding]:
    """Walk the module body (as ``<module>``, a benchmark script's timed loop) and every function, carrying the
    parameter names of its lexically enclosing functions.

    The enclosing names matter because a nested helper (``def _run(...)`` inside
    ``def time_backend(fn, ...)``) calls the OUTER function's parameters, and it is those that make
    the timed call injected/opaque.
    """

    def visit(node: ast.AST, visible_params: set[str], func_name: Optional[str]) -> Iterator[GpuTimingFinding]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = visible_params | _param_names(child)
                yield from _scan_one_function(child, params, gpu_aware, source_lines, path, aliases)
                yield from visit(child, params, child.name)
            else:
                yield from visit(child, visible_params, func_name)

    if isinstance(tree, ast.Module):
        yield from _scan_one_function(tree, set(), gpu_aware, source_lines, path, aliases)
    yield from visit(tree, set(), None)


def _position(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _end(node: ast.AST) -> tuple[int, int]:
    """Where a call finishes: ``cp.cuda.Device().synchronize()`` ends after the ``cp.cuda.Device()`` it contains."""
    line = getattr(node, "end_lineno", None) or getattr(node, "lineno", None) or 0
    col = getattr(node, "end_col_offset", None) or 0
    return int(line), int(col)


def _scan_one_function(
    func: ast.AST, visible_params: set[str], gpu_aware: bool, source_lines: list[str], path: Path, aliases: Optional[ImportAliases] = None
) -> Iterator[GpuTimingFinding]:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        return
    name = func.name if not isinstance(func, ast.Module) else "<module>"
    # Only this function's OWN statement blocks: a nested def is scanned separately, with its own
    # (wider) parameter scope, so scanning it here too would double-report.
    for block in _own_stmt_blocks(func):
        for start_lineno, region in _timed_regions(block, aliases):
            calls = sorted((c for stmt in region for c in ast.walk(stmt) if isinstance(c, ast.Call)), key=_position)
            if _region_is_suppressed(region, start_lineno, source_lines):
                continue
            gpu_calls = [c for c in calls if _gpu_call_name(c) and not _is_sync_call(c)]
            # The injected timer itself is excluded: an injectable ``timer`` parameter is how the
            # clock is read, not the work being clocked, so counting it would flag every
            # timer-overridable measurement in the file.
            injected = [
                c for c in calls if isinstance(c.func, ast.Name) and c.func.id in visible_params and not _is_timer_read(c, aliases) and not _is_sync_call(c)
            ]
            work = gpu_calls or (injected if gpu_aware else [])
            if not work:
                continue
            # A synchronize only counts when it runs AFTER the last timed work: one before the kernel waits for
            # nothing that is being measured.
            last_work = max(_end(c) for c in work)
            if any(_is_sync_call(c) and _end(c) > last_work for c in calls):
                continue
            if gpu_calls:
                yield GpuTimingFinding(
                    path, start_lineno, name, "direct-gpu-call", f"times {_gpu_call_name(gpu_calls[0])}() with no device synchronize before the timer stop"
                )
                continue
            first = injected[0].func
            first_name = first.id if isinstance(first, ast.Name) else "<callable>"
            yield GpuTimingFinding(
                path,
                start_lineno,
                name,
                "injected-callable-in-gpu-module",
                f"times caller-supplied {first_name}() in a GPU-aware module with no device synchronize before the timer stop",
            )


def _own_stmt_blocks(func: ast.AST) -> Iterator[list[ast.stmt]]:
    """Every statement list under ``func`` (so a timed region nested in a ``for``/``with``/``try`` is seen as its own
    block), stopping at any nested function or class."""
    stack: list[ast.AST] = [func]
    seen_root = False
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and seen_root:
            continue
        seen_root = True
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and all(isinstance(s, ast.stmt) for s in block):
                yield block
        # Statement blocks only hang off statements (and except handlers / match cases), so the walk never needs to
        # descend into expressions -- which is where almost all of a module's nodes are.
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            stack.extend(child for child in getattr(node, field, None) or () if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))


def _scan(files: Iterable[Path], root: Optional[Path]) -> tuple[list[GpuTimingFinding], ScanResult]:
    scan = scan_python([Path(p) for p in files], root=root)
    findings: list[GpuTimingFinding] = []
    for parsed in scan:
        source = parsed.source
        if not _TIMER_CALL_TEXT_RE.search(source):
            continue
        rel = Path(parsed.rel) if root is not None else parsed.path
        aliases = ImportAliases.from_tree(parsed.tree)
        findings.extend(_scan_functions(parsed.tree, bool(_GPU_AWARE_RE.search(source)), source.splitlines(), rel, aliases))
    return sorted(set(findings), key=lambda f: (f.path.as_posix(), f.lineno)), scan


def find_unsynchronized_gpu_timings(files: Iterable[Path], root: Optional[Path] = None) -> list[GpuTimingFinding]:
    """Every timed region across ``files`` that measures GPU work without synchronizing first.

    ``root``, when given, makes each finding's path relative to it so allowlist keys stay stable
    across checkouts. Files that cannot be parsed are not in this list; :func:`assert_no_unsynchronized_gpu_timings`
    fails on them.
    """
    return _scan(files, root)[0]


def assert_no_unsynchronized_gpu_timings(files: Iterable[Path], root: Optional[Path] = None, allowlist: frozenset = frozenset(), *, min_files: int = 1) -> None:
    """Fail the calling pytest run on any unsynchronized GPU timing outside ``allowlist``, on any file that could not be
    parsed, and when fewer than *min_files* files parsed.

    ``allowlist`` holds ``"<relative/path.py>::<function>"`` keys (see ``GpuTimingFinding.key``);
    prefer the in-source ``gpu-timing-async-intentional`` marker, which keeps the justification next
    to the measurement it excuses instead of in a distant list.
    """
    import pytest

    found, scan = _scan(files, root)
    scan.min_files = min_files
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed:
        problems.append(f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:\n" + "\n".join(f"  {u.render()}" for u in scan.unparsed))
    findings = [f for f in found if f.key not in allowlist]
    if findings:
        lines = [f"  {f.path.as_posix()}:{f.lineno} in {f.function}() [{f.shape}] -- {f.detail}" for f in findings]
        problems.append(
            f"{len(findings)} GPU timing measurement(s) taken without a device synchronize -- the timer stops at kernel LAUNCH, not "
            f"completion, so the number is a phantom (measured up to ~1900x under-count) and poisons anything that persists it:\n" + "\n".join(lines)
        )
    if problems:
        pytest.fail("\n".join(problems))
