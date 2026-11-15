"""CUDA ``__syncthreads()`` under thread-dependent control flow.

``__syncthreads()`` is only defined when every thread of the block reaches it (NVIDIA programming guide: in conditional code, only if the condition is identical across the block).
Kernel source kept in a Python string (see ``cuda_kernel_pointer_types`` for what counts as one) is checked for:

* ``cuda-barrier-in-divergent-branch`` (hard): a barrier (``__syncthreads``, ``__syncthreads_count/and/or``) inside an ``if``/``else``, loop or ``switch`` whose condition depends on
  ``threadIdx``. A ``for (r = tid; r < n; r += blockDim.x)`` loop counts: its trip count differs per thread. NVIDIA-DOCUMENTED, not measured here.
* ``cuda-barrier-after-divergent-return`` (advisory): a barrier met after a ``return`` that only some threads take (``if (t >= K) return;``). Formally the same undefined category,
  but the hardware counts exited threads as arrived, so this is reported only with ``include_advisory``; ``compute-sanitizer --tool synccheck`` is the dynamic authority.
  Audit 2026-11-16, finding 54-T7: ``row_attention_stage4_fused`` has three such barriers (latent: its only launch uses exactly K threads).

Thread dependence is the scoped taint of ``cuda_warp_intrinsics`` (``_Walker``). Pinned by tests: conditions on ``blockIdx`` (any member, ``blockIdx.y`` is not a variable ``y``),
kernel parameters, constants, and memory read at a block-uniform address are uniform; a loop with a thread-independent bound is uniform; a variable declared inside a branch or
loop does not inherit that branch's class. Conservative by design (a miss over a false positive): a condition made only of the warp index (``threadIdx.x >> 5``) is not reported
(it is block-divergent in fact, but whole warps are uniform and the mask/hardware behaviour differs); ``break``/``continue`` under a thread-dependent condition, results of atomics,
shared-memory values written by other threads and function-like macros are treated as uniform; a kernel using a construct the reader cannot parse is skipped.

Suppression: ``// barrier-ok: <reason>`` on the barrier line or the line above, or on the line (or the one above) of the controlling condition or ``return``, inside the kernel
string (for example "the launch block equals K, so the guard is vacuous"); ``# barrier-ok: <reason>`` on the first or last line of the Python statement holding the string.
An empty reason does not suppress.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_barrier_divergence import assert_cuda_barrier_divergence

    def test_cuda_barrier_divergence():
        assert_cuda_barrier_divergence("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _Unit, _units
from .cuda_warp_intrinsics import (
    THREAD,
    _Call,
    _Func,
    _prepare_unit,
    line_offset,
    marked_lines,
    python_marked,
    walk_functions,
)

__all__ = ["MARKER", "RULE_BRANCH", "RULE_RETURN", "assert_cuda_barrier_divergence", "find_cuda_barrier_divergence"]

RULE_BRANCH = "cuda-barrier-in-divergent-branch"
RULE_RETURN = "cuda-barrier-after-divergent-return"
MARKER = "barrier-ok:"
_BARRIERS = frozenset({"__syncthreads", "__syncthreads_count", "__syncthreads_and", "__syncthreads_or"})


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str], include_advisory: bool) -> list[Finding]:
    if python_marked(unit, py_lines, MARKER):
        return []
    prep = _prepare_unit(unit.text)
    marked = marked_lines(unit.text, MARKER)
    out: list[Finding] = []

    def add(rule: str, off: int, message: str, cause_off: int) -> None:
        line, cause = line_offset(prep, off), line_offset(prep, cause_off)
        if any(n in marked or n - 1 in marked for n in (line, cause)):
            return
        out.append(Finding(rel, unit.line + line, rule, message.format(cause=unit.line + cause)))

    def make(func: _Func) -> Callable[[_Call], None]:
        def on_call(call: _Call) -> None:
            if call.name not in _BARRIERS:
                return
            divergent = [c for c in call.ctrl if c[0] == THREAD]
            if divergent:
                add(
                    RULE_BRANCH,
                    call.off,
                    f"`{call.name}()` in kernel `{func.name}` is inside control flow that depends on threadIdx (condition at line {{cause}}): threads that skip it deadlock or corrupt the block; "
                    "compute a predicate and keep the barrier outside the branch",
                    divergent[-1][1],
                )
            elif include_advisory and call.exit_off is not None:
                add(
                    RULE_RETURN,
                    call.off,
                    f"`{call.name}()` in kernel `{func.name}` follows a thread-dependent `return` (line {{cause}}): exited threads never reach it, which is undefined when the block is wider than the guard; "
                    "predicate the work instead of returning, or mark `// barrier-ok: <why the guard is vacuous>`",
                    call.exit_off,
                )

        return on_call

    walk_functions(prep, ("__global__",), make)
    return out


def find_cuda_barrier_divergence(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every barrier in thread-dependent control flow (and, with *include_advisory*, after a thread-dependent return) in a CUDA kernel string under *root*.

    Sorted by path and line. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed
    (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        py_lines = parsed.source.splitlines()
        aliases = ImportAliases.from_tree(parsed.tree)
        for unit in _units(parsed.tree, aliases):
            out.extend(_unit_findings(unit, parsed.rel, py_lines, include_advisory))
    return sorted(set(out), key=lambda f: (f.path, f.line, f.rule, f.message))


def assert_cuda_barrier_divergence(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any ``__syncthreads()`` inside thread-dependent control flow (and after a thread-dependent return with *include_advisory*).

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_barrier_divergence(root, include_advisory=include_advisory, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "keep every `__syncthreads()` where all threads of the block arrive (predicate the work, do not branch around the barrier); `// barrier-ok: <reason>` when the launch makes the guard vacuous"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_barrier_divergence", refresh_command="PY_CI_SHARED_REFRESH=cuda_barrier_divergence")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA barrier-divergence finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
