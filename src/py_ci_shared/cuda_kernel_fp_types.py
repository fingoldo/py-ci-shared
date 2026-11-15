"""A type-generic CUDA kernel spells every floating-point type through its placeholders, never as a bare ``double`` / ``float``.

Kernel source kept in a Python string (``cupy.RawKernel``/``RawModule``/``ElementwiseKernel``/``ReductionKernel``, or a constant named ``*_SRC``/``*_KERNEL``/``*_CODE``) that is
compiled once per element type uses ``{REAL}`` for values and ``{ACC}`` for sums and statistics. A bare ``double`` or ``float`` anywhere in such a source (a local, a
``__shared__`` array, a scalar parameter, a cast, a ``__device__`` helper) pins that part of the kernel to one type whatever the host compiles it for. That is the project contract
"no bare double or float in kernel source" (mlframe ``CUDA.md`` section 2.3). ``cuda_kernel_pointer_types`` sees only ``double*`` / ``float*`` parameters; this gate reads the whole text.

* ``cuda-bare-fp-type`` (hard): a source that contains ``{REAL}`` or ``{ACC}`` and also the word ``double`` or ``float`` (a call such as ``float(...)`` is not a type use and is
  skipped). One finding per source, at the first occurrence, listing every line. A fixed-type kernel (neither placeholder) is a different design and is reported only with
  ``include_untyped`` as ``cuda-bare-fp-type-untyped`` (advisory).
* ``cuda-unsuffixed-fp-literal`` (advisory, only with ``include_advisory``): a floating-point literal that does not round-trip through float32 (``0.1``, ``1e-9``; ``0.5``,
  ``1.0``, ``2.0`` are exact and the compiler folds them) in a statement that touches a ``{REAL}`` pointer or local and has no ``{ACC}``, and is not directly under an explicit cast.
  The literal is a ``double``, so the statement runs in FP64 (verified on NVRTC 12.9, ``x[i] * 0.1`` with a float ``x``: 4 ``.f64`` tokens in the PTX, against 0 for ``0.1f``;
  ``x[i] > 1e-9``: 3). On consumer parts FP64 runs at 1/32 rate, so this is a measured cost, hence advisory: it is a small effect, not a wrong answer.

Suppression: ``// fp-type-ok: <reason>`` anywhere in the kernel text (one reason per source), or ``# fp-type-ok: <reason>`` on the first or last line of the Python statement holding
the string. The reason is mandatory; an empty reason does not suppress. C comments are not scanned for types.

Provenance: the hard rule is the project contract (``CUDA.md`` 2.3); the FP64 cost is measured (above); the FP64 rate is NVIDIA's documented figure. Audit round 2026-11-16, finding 54-T1.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_fp_types import assert_cuda_kernel_fp_types

    def test_cuda_kernel_fp_types():
        assert_cuda_kernel_fp_types("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_kernel_integer_width import _Unit, _strip_comments, _units

__all__ = ["MARKER", "RULE_BARE", "RULE_BARE_UNTYPED", "RULE_LITERAL", "assert_cuda_kernel_fp_types", "find_cuda_kernel_fp_types"]

RULE_BARE = "cuda-bare-fp-type"
RULE_BARE_UNTYPED = "cuda-bare-fp-type-untyped"
RULE_LITERAL = "cuda-unsuffixed-fp-literal"
MARKER = "fp-type-ok:"
_PLACEHOLDER_USE = re.compile(r"\{(?:REAL|ACC)\}")
_BARE_TYPE = re.compile(r"\b(double|float)\b(?!\s*\()")
_MARK = re.compile(r"(?://|#)[^\n]*?\bfp-type-ok:[ \t]*(\S[^\n]*)")
_BRACED = re.compile(r"\{(\w+)\}")
_REAL_NAME = re.compile(r"@REAL@\s*(?:\*\s*)?(?:const\s+)?(?:__restrict__\s*)?(\w+)")
_LITERAL = re.compile(r"(?<![\w.@])(?:\d+\.\d*|\.\d+|\d+(?=[eE][+-]?\d))(?:[eE][+-]?\d+)?(?![\w.])")
_CAST_BEFORE = re.compile(r"\(\s*(?:@REAL@|@ACC@|float|double)\s*\)\s*$")


def _marked(unit: _Unit, py_lines: list[str]) -> bool:
    """A ``fp-type-ok:`` with a non-empty reason in the kernel text or on an end line of the holding statement."""
    if _MARK.search(unit.text):
        return True
    return any(0 < n <= len(py_lines) and _MARK.search(py_lines[n - 1]) for n in {unit.stmt_first, unit.stmt_last})


def _float32_exact(literal: str) -> bool:
    """True when the literal survives a round trip through float32 (``0.5``, ``1.0``), false for ``0.1`` and for values float32 cannot hold."""
    value = float(literal)
    try:
        return float(struct.unpack("f", struct.pack("f", value))[0]) == value
    except OverflowError:
        return False


def _literal_findings(code: str, rel: str, first_line: int) -> list[Finding]:
    text = _BRACED.sub(r"@\1@", code)
    real_names = set(_REAL_NAME.findall(text))
    out: list[Finding] = []
    for part in re.finditer(r"[^;{}]+", text):
        stmt = part.group(0)
        if "@ACC@" in stmt:
            continue
        touches = "@REAL@" in stmt or any(re.search(rf"\b{re.escape(n)}\b", stmt) for n in real_names)
        if not touches:
            continue
        for lit in _LITERAL.finditer(stmt):
            if _float32_exact(lit.group(0)) or _CAST_BEFORE.search(stmt[: lit.start()]):
                continue
            line = first_line + text.count("\n", 0, part.start() + lit.start())
            out.append(
                Finding(
                    rel,
                    line,
                    RULE_LITERAL,
                    f"literal `{lit.group(0)}` is a double: the statement touching a `{{REAL}}` value runs in FP64 (1/32 rate on consumer GPUs); write `{lit.group(0)}f` or `({{REAL}}){lit.group(0)}`, "
                    "or mark `// fp-type-ok: <reason>`",
                )
            )
    return out


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str], include_advisory: bool, include_untyped: bool) -> list[Finding]:
    code = _strip_comments(unit.text)
    typed = _PLACEHOLDER_USE.search(code) is not None
    if (not typed and not include_untyped) or _marked(unit, py_lines):
        return []
    hits = [(code.count("\n", 0, m.start()), m.group(1)) for m in _BARE_TYPE.finditer(code)]
    out: list[Finding] = []
    if hits:
        lines = sorted({unit.line + off for off, _ in hits})
        types = sorted({t for _, t in hits})
        rule = RULE_BARE if typed else RULE_BARE_UNTYPED
        what = "type-generic kernel (uses `{REAL}`/`{ACC}`)" if typed else "fixed-type kernel"
        out.append(
            Finding(
                rel,
                lines[0],
                rule,
                f"bare `{'`/`'.join(types)}` at line(s) {', '.join(map(str, lines))} in a {what}: spell values `{{REAL}}` and sums/statistics `{{ACC}}` so one source serves every dtype; "
                "mark `// fp-type-ok: <reason>` when the type is fixed on purpose",
            )
        )
    if typed and include_advisory:
        out.extend(_literal_findings(code, rel, unit.line))
    return out


def find_cuda_kernel_fp_types(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    include_untyped: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every bare ``double``/``float`` in a placeholder-typed CUDA kernel source under *root* (and unsuffixed FP literals with *include_advisory*).

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
            out.extend(_unit_findings(unit, parsed.rel, py_lines, include_advisory, include_untyped))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_kernel_fp_types(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    include_untyped: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any bare ``double``/``float`` in a type-generic kernel source (and on advisory shapes when asked).

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_kernel_fp_types(
        root, include_advisory=include_advisory, include_untyped=include_untyped, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "use {REAL} for values and {ACC} for sums in a type-generic kernel, `f`-suffix or cast literals; `// fp-type-ok: <reason>` when a fixed type is deliberate"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_kernel_fp_types", refresh_command="PY_CI_SHARED_REFRESH=cuda_kernel_fp_types")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA kernel floating-point type finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
