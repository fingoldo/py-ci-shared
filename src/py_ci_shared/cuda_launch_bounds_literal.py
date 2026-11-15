"""``__launch_bounds__(threads, minBlocks)`` must not hard-code ``minBlocks`` as a number tuned on one card.

Threads per SM are 2048, 1536 or 1024 by architecture, so a literal ``minBlocks`` that fits one card asks for more resident threads than another allows; the hint is then ignored with a ptxas warning.
``minBlocks`` has to come from a macro derived from ``__CUDA_ARCH__`` (mlframe: ``MLF_THREADS_PER_SM / 128``). The host-side launch-geometry gate does not see kernel source, so this reads the text of
the files. Origin: mlframe ``test_cuda_kernels_offline_per_arch.py::test_no_kernel_hardcodes_the_min_blocks_of_launch_bounds`` (audit 2026-11-16, finding 55-T5); the 1024-thread rule below is the
smallest threads-per-SM of the architectures in ``cuda_offline_report.ARCH_LIMITS`` (sm_75).

* ``cuda-literal-min-blocks`` (hard): a line holding ``__launch_bounds__(<name or number>, <integer literal>)`` whose threads x blocks exceeds *max_resident_threads* (default 1024), or whose threads are not
  a literal (then it cannot be shown to fit). A pair that fits even the smallest SM (``(128, 4)``) is portable and not reported. Comment lines (``#``, ``//``) are skipped.

Suppression: ``// launch-bounds-ok: <reason>`` (or ``# ...``) on the line; an empty reason suppresses nothing.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_launch_bounds_literal import assert_cuda_launch_bounds_literal

    def test_no_kernel_hardcodes_min_blocks():
        assert_cuda_launch_bounds_literal("src/mlframe", exclude=("_benchmarks", "kernel_prototypes"), min_files=300)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import DEFAULT_EXCLUDE, Baseline, Finding, scan_python

__all__ = ["MARKER", "RULE", "assert_cuda_launch_bounds_literal", "find_cuda_launch_bounds_literal"]

RULE = "cuda-literal-min-blocks"
MARKER = "launch-bounds-ok:"
_LITERAL = re.compile(r"__launch_bounds__\(\s*(\w+)\s*,\s*(\d+)\s*\)")


def _suppressed(line: str) -> bool:
    if MARKER not in line:
        return False
    return bool(line.split(MARKER, 1)[1].strip())


def find_cuda_launch_bounds_literal(
    root: Union[str, Path],
    *,
    exclude: "Iterable[str]" = (),
    max_resident_threads: int = 1024,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every ``__launch_bounds__(T, <literal>)`` under *root* that does not fit *max_resident_threads*, sorted by path and line.

    Raises ``EmptyScanError`` below *min_files* parsed files and ``UnparsedFilesError`` for a file that cannot be parsed (unless *allow_unparsed*).
    """
    scan = scan_python(root, min_files=min_files, exclude=set(DEFAULT_EXCLUDE) | set(exclude), use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        if "__launch_bounds__" not in parsed.source:
            continue
        for number, line in enumerate(parsed.source.splitlines(), 1):
            if "__launch_bounds__" not in line or line.lstrip().startswith(("#", "//")):
                continue
            match = _LITERAL.search(line)
            if match is None or _suppressed(line):
                continue
            threads, blocks = match.group(1), int(match.group(2))
            if threads.isdigit() and int(threads) * blocks <= max_resident_threads:
                continue
            out.append(
                Finding(
                    parsed.rel,
                    number,
                    RULE,
                    f"`{match.group(0)}` hard-codes minBlocks={blocks}: threads per SM differ by architecture (2048, 1536, 1024) and a hint above the limit is ignored with a ptxas warning; "
                    f"derive it from a macro over __CUDA_ARCH__, or mark `// launch-bounds-ok: <reason>`",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_launch_bounds_literal(
    root: Union[str, Path],
    *,
    exclude: "Iterable[str]" = (),
    max_resident_threads: int = 1024,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any literal ``minBlocks`` that does not fit every architecture, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_launch_bounds_literal(
        root, exclude=exclude, max_resident_threads=max_resident_threads, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "derive minBlocks from a macro over __CUDA_ARCH__, or mark the line `// launch-bounds-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_launch_bounds_literal", refresh_command="PY_CI_SHARED_REFRESH=cuda_launch_bounds_literal")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} literal minBlocks in __launch_bounds__; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
