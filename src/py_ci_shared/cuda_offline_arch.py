"""The generic body of an offline per-architecture kernel test: which architectures, whether the toolchain is there, and what a ptxas result must satisfy.

``cuda_offline_report`` holds the engine (NVRTC to PTX, ``ptxas -v``, occupancy). A consumer's offline test still needs the glue every second consumer would copy (mlframe
``test_cuda_kernels_offline_per_arch.py``, audit 2026-11-16 finding 55-T5): the architecture list from an environment variable, skip-or-fail on a missing toolchain, and the
resource assertions on one build. This module is that glue as pure functions over strings, so it runs and is tested without a GPU. The kernel sources and their recipes, the spill
exemptions with their measured numbers and the architecture default stay in the consumer.

* :func:`offline_archs` - ``MYPKG_OFFLINE_ARCHS`` as a tuple (comma list, ``all``, or the default).
* :func:`toolchain_problem` - ``None`` when ptxas and cupy's NVRTC binding are present, else the reason; the consumer skips, or fails when its REQUIRE variable is ``1`` (:func:`require_enabled`).
* :func:`resource_problems` - for one build's PTX and ``ptxas -v`` report: an entry without a resource block, a spill store or load in an entry (unless *allow_spill*) or in a ``__noinline__`` helper,
  a warning outside *warning_allowlist*, a ``__launch_bounds__`` the allocator did not honour (``launch_bounds_violations``). Returns messages; an empty list is a pass.

Usage in a consumer's test::

    from py_ci_shared.cuda_offline_arch import offline_archs, require_enabled, resource_problems, toolchain_problem

    archs = offline_archs("MLFRAME_OFFLINE_ARCHS", default=(61, 120), every=(61, 70, 75, 86, 89, 90, 120))
    why = toolchain_problem()
    if why:
        (pytest.fail if require_enabled("MLFRAME_REQUIRE_OFFLINE") else pytest.skip)(why)
    ptx, report = build_ptx_and_report(source, options, arch, find_ptxas(), tmp_path)
    assert not resource_problems(ptx, report, arch=arch, allow_spill=variant_id in SPILL_EXEMPT)
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Optional

from .cuda_offline_report import find_ptxas, launch_bounds_violations

__all__ = ["offline_archs", "require_enabled", "resource_problems", "toolchain_problem"]

_BLOCK = re.compile(r"Function properties for (\w+)\s+(\d+) bytes stack frame, (\d+) bytes spill stores, (\d+) bytes spill loads")


def offline_archs(env_var: str, *, default: "Iterable[int]", every: "Iterable[int]", environ: "Optional[Mapping[str, str]]" = None) -> "tuple[int, ...]":
    """Compute capabilities to check: *env_var* as a comma list of integers, ``all`` for *every*, else *default*. A non-integer entry raises ``ValueError`` naming the variable."""
    raw = (environ if environ is not None else os.environ).get(env_var, "").strip().lower()
    if not raw:
        return tuple(default)
    if raw == "all":
        return tuple(every)
    try:
        return tuple(int(part) for part in raw.split(",") if part.strip())
    except ValueError:
        raise ValueError(f"{env_var}={raw!r}: expected a comma list of integers (61,120) or 'all'") from None


def require_enabled(env_var: str, *, environ: "Optional[Mapping[str, str]]" = None) -> bool:
    """True when *env_var* is ``1``: the CI image has the toolkit, so a missing toolchain must fail instead of skipping."""
    return (environ if environ is not None else os.environ).get(env_var, "").strip() == "1"


def toolchain_problem() -> Optional[str]:
    """``None`` when the toolkit's ptxas and cupy's NVRTC binding are both available, else the reason."""
    if find_ptxas() is None:
        return "no ptxas on PATH or under CUDA_PATH"
    try:
        import cupy_backends.cuda.libs.nvrtc  # noqa: F401
    except ImportError as exc:
        return f"cupy's NVRTC binding is not importable: {exc}"
    return None


def resource_problems(
    ptx: str,
    report: str,
    *,
    arch: int,
    allow_spill: bool = False,
    warning_allowlist: "Iterable[str]" = (),
) -> "list[str]":
    """What is wrong with one build, one message per problem (empty means the build is clean)."""
    entries = re.findall(r"\.entry\s+(\w+)", ptx)
    if not entries:
        return [f"sm_{arch}: no .entry in the PTX"]
    props = {m[0]: m[1:] for m in _BLOCK.findall(report)}
    problems: "list[str]" = []
    missing = [n for n in entries if n not in props]
    if missing:
        problems.append(f"sm_{arch}: entries without a ptxas resource block: {missing}")
    if not allow_spill:
        spills = {n: props[n] for n in entries if n in props and (props[n][1] != "0" or props[n][2] != "0")}
        if spills:
            problems.append(f"sm_{arch}: register spill (stack, spill st, spill ld) per entry: {spills}")
        helpers = {n: v for n, v in props.items() if n not in entries and (v[1] != "0" or v[2] != "0")}
        if helpers:
            problems.append(f"sm_{arch}: register spill (stack, spill st, spill ld) in a device helper: {helpers}")
    allowed = tuple(warning_allowlist)
    warnings = [ln for ln in report.splitlines() if "warning" in ln.lower() and not any(k in ln for k in allowed)]
    if warnings:
        problems.append(f"sm_{arch}: unlisted NVRTC or ptxas warnings: {warnings[:3]}")
    bad = launch_bounds_violations(ptx, report)
    if bad:
        problems.append(f"sm_{arch}: __launch_bounds__ not honoured (entry, registers, cap): {bad}")
    return problems
