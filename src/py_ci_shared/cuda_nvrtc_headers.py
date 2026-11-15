"""A CUDA kernel compiled by NVRTC (through cupy, with no ``#include``) does not know host-header macros such as ``INFINITY`` or ``FLT_MAX``: they are undefined there.

NVRTC compiles kernel text without the C library headers. Verified on this host (NVRTC 12.9 through ``cupy.cuda.nvrtc``, compile only, no launch, ``compute_61``), each of these
gives ``identifier "..." is undefined``: ``INFINITY``, ``NAN``, ``HUGE_VAL``, ``HUGE_VALF``, ``FLT_MAX``, ``FLT_MIN``, ``FLT_EPSILON``, ``DBL_MAX``, ``DBL_MIN``, ``DBL_EPSILON``,
``INT_MAX``, ``INT_MIN``, ``UINT_MAX``, ``LONG_MAX``, ``LLONG_MAX``, ``ULLONG_MAX``, ``SIZE_MAX`` and ``CUDART_INF_F`` (the ``CUDART_*`` family needs
``#include <math_constants.h>``). The documented workaround ``__int_as_float(0x7f800000)`` compiles. CPU-only CI never compiles the source, so the error first appears on the GPU machine.

* ``cuda-nvrtc-undefined-macro`` (hard): one of the identifiers above used in a kernel source that does not ``#define`` it, and any ``CUDART_*`` constant in a source without
  ``#include <math_constants.h>``. The message gives the replacement (``-INFINITY`` is ``__int_as_float(0xff800000)``, ``FLT_MAX`` is ``__int_as_float(0x7f7fffff)``, ``INT_MAX`` is
  ``2147483647``, and so on). A ``#include`` of a C header (``<cfloat>``, ``<climits>``) is not accepted as a fix: whether the build has an include path is not visible here.
* Comments and string literals are not scanned.

Suppression: ``// nvrtc-ok: <reason>`` anywhere in the kernel text (a build that passes ``-include`` or a header path), or ``# nvrtc-ok: <reason>`` on the first or last line of the
Python statement holding the string. The reason is mandatory; an empty reason does not suppress.

Provenance: measured on NVRTC 12.9 (above); audit round 2026-11-16, finding 54-T3 (mlframe ``CUDA.md`` names only ``INFINITY``).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_nvrtc_headers import assert_cuda_nvrtc_headers

    def test_cuda_nvrtc_headers():
        assert_cuda_nvrtc_headers("src", min_files=50)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _Unit, _strip_comments, _units

__all__ = ["FIXES", "MARKER", "RULE_MACRO", "assert_cuda_nvrtc_headers", "find_cuda_nvrtc_headers"]

RULE_MACRO = "cuda-nvrtc-undefined-macro"
MARKER = "nvrtc-ok:"
_INF = "`__int_as_float(0x7f800000)` (negative: `0xff800000`)"
_LL = "`9223372036854775807LL`"
FIXES = {
    "INFINITY": _INF,
    "HUGE_VALF": _INF,
    "NAN": "`__int_as_float(0x7fc00000)`",
    "HUGE_VAL": "`__longlong_as_double(0x7ff0000000000000LL)`",
    "FLT_MAX": "`__int_as_float(0x7f7fffff)`",
    "FLT_MIN": "`1.17549435e-38f`",
    "FLT_EPSILON": "`1.1920929e-7f`",
    "DBL_MAX": "`__longlong_as_double(0x7fefffffffffffffLL)`",
    "DBL_MIN": "`2.2250738585072014e-308`",
    "DBL_EPSILON": "`2.220446049250313e-16`",
    "INT_MAX": "`2147483647`",
    "INT_MIN": "`(-2147483647 - 1)`",
    "UINT_MAX": "`4294967295u`",
    "LONG_MAX": _LL,
    "LLONG_MAX": _LL,
    "ULLONG_MAX": "`18446744073709551615ULL`",
    "SIZE_MAX": "`18446744073709551615ULL`",
}
_IDENT = re.compile(r"\b(" + "|".join(sorted(FIXES, key=len, reverse=True)) + r"|CUDART_\w+)\b")
_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)", re.MULTILINE)
_MATH_CONSTANTS = re.compile(r"^[ \t]*#[ \t]*include[ \t]*[<\"]math_constants\.h[>\"]", re.MULTILINE)
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_MARK = re.compile(r"(?://|#)[^\n]*?\bnvrtc-ok:[ \t]*(\S[^\n]*)")


def _marked(unit: _Unit, py_lines: list[str]) -> bool:
    if _MARK.search(unit.text):
        return True
    return any(0 < n <= len(py_lines) and _MARK.search(py_lines[n - 1]) for n in {unit.stmt_first, unit.stmt_last})


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str]) -> list[Finding]:
    code = _STRING.sub('""', _strip_comments(unit.text))
    defined = set(_DEFINE.findall(code))
    has_constants = _MATH_CONSTANTS.search(code) is not None
    seen: set[str] = set()
    out: list[Finding] = []
    for m in _IDENT.finditer(code):
        name = m.group(1)
        if name in seen or name in defined or (name.startswith("CUDART_") and has_constants):
            continue
        seen.add(name)
        fix = FIXES.get(name, "`#include <math_constants.h>` in the kernel source")
        out.append(
            Finding(
                rel,
                unit.line + code.count("\n", 0, m.start()),
                RULE_MACRO,
                f"`{name}` is undefined under NVRTC (no C headers are included): use {fix}, or mark `// nvrtc-ok: <reason>` when the build passes `-include` or a header path",
            )
        )
    return [] if out and _marked(unit, py_lines) else out


def find_cuda_nvrtc_headers(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every NVRTC-undefined host macro used in a CUDA kernel source under *root*.

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
            out.extend(_unit_findings(unit, parsed.rel, py_lines))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_nvrtc_headers(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any host-header macro (``INFINITY``, ``FLT_MAX``, ``INT_MAX``, ...) used in a kernel source NVRTC compiles without headers.

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_nvrtc_headers(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "NVRTC has no C headers: use a bit pattern such as __int_as_float(0x7f800000) or a numeric literal; `// nvrtc-ok: <reason>` when the build supplies the header"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_nvrtc_headers", refresh_command="PY_CI_SHARED_REFRESH=cuda_nvrtc_headers")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA NVRTC undefined-macro finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
