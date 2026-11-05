"""CUDA kernel pointer parameters are type-templated, not a bare ``double*`` / ``float*``: a float32 buffer bound to a ``double*`` is read past its end.

Kernel source kept in a Python string (``cupy.RawKernel``/``RawModule``/``ElementwiseKernel``/``ReductionKernel``, or a constant named ``*_SRC``/``*_KERNEL``/``*_CODE`` holding
``__global__``) declares its pointer types in C. When the host hands such a kernel a buffer of another element type, the kernel reads or writes the wrong number of bytes:

* ``cuda-bare-double-pointer`` (hard): a ``__global__`` function with a ``double*`` parameter and no ``{REAL}`` placeholder among its parameters. A float32 array bound to
  ``const double* x`` is read twice past its end: wrong values, and an illegal memory access when the array ends a mapped region, which poisons the CUDA context for the rest of the
  process (the next thrust call aborts the interpreter). Template the value type (``{REAL}`` for values, ``{ACC}`` for sums and statistics, one compiled module per type, the type chosen
  from the launched buffer dtype), or state why the kernel is float64 on purpose.
* ``cuda-bare-float-pointer`` (advisory): the same for ``float*``. A float64 buffer bound to ``float*`` is read as half its data (wrong values, no out-of-bounds read), so it is reported
  only with ``include_advisory``.
* ``cuda-type-probe-not-launched`` (hard): a function that picks the kernel type with ``"float" if X.dtype == ... else "double"`` where ``X`` is never a launch argument by name in the
  module. Probing the dtype of an array that is cast before the launch compiles a ``double`` kernel for float32 buffers, the same out-of-bounds read the template exists to prevent. The
  launch can sit in another method than the probe, so launch arguments are collected module-wide.

Suppression: ``// dtype-ok: <reason>`` on the kernel signature line (or the one above) inside the kernel string, or ``# dtype-ok: <reason>`` on the first or last line of the Python statement
holding the string or on the probe line. The reason is the review: why the buffer is always the declared type (a measured no-gain, a timestamp that needs float64, a dtype-specific
sibling of a float32 kernel selected by the launched buffer). C comments are not scanned for signatures.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_pointer_types import assert_cuda_kernel_pointer_types

    def test_cuda_kernel_pointer_types():
        assert_cuda_kernel_pointer_types("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, nodes_of, scan_python
from .cuda_kernel_integer_width import _Unit, _strip_comments, _units

__all__ = ["MARKER", "RULE_DOUBLE", "RULE_FLOAT", "RULE_PROBE", "assert_cuda_kernel_pointer_types", "find_cuda_kernel_pointer_types"]

RULE_DOUBLE = "cuda-bare-double-pointer"
RULE_FLOAT = "cuda-bare-float-pointer"
RULE_PROBE = "cuda-type-probe-not-launched"
MARKER = "dtype-ok:"
_KERNEL = re.compile(r"__global__\s+(?:\w+\s+)*?void\s+(\w+)\s*\((.*?)\)\s*\{", re.DOTALL)
_DOUBLE_PTR = re.compile(r"\bdouble\s*\*")
_FLOAT_PTR = re.compile(r"\bfloat\s*\*")
_TEMPLATED = ("{REAL}",)


def _param_names(params: str, pattern: "re.Pattern[str]") -> list[str]:
    """The names of the parameters of one kernel signature that match *pattern* (``const double* __restrict__ x`` gives ``x``)."""
    names = []
    for part in params.split(","):
        if pattern.search(part):
            words = re.findall(r"\w+", re.sub(r"__restrict__|\bconst\b", " ", part))
            names.append(words[-1] if words else "?")
    return names


def _marked(unit: _Unit, kernel_lines: list[str], offset: int, py_lines: list[str]) -> bool:
    """``// dtype-ok:`` on the signature line or the one above, in the kernel text; ``# dtype-ok:`` on the statement's ends."""
    for index in (offset, offset - 1):
        if 0 <= index < len(kernel_lines) and "//" in kernel_lines[index] and MARKER in kernel_lines[index][kernel_lines[index].index("//") :]:
            return True
    for number in {unit.stmt_first, unit.stmt_last, unit.line}:
        if 0 < number <= len(py_lines):
            line = py_lines[number - 1]
            if "#" in line and MARKER in line[line.index("#") :]:
                return True
    return False


def _unit_findings(unit: _Unit, rel: str, py_lines: list[str], include_float: bool) -> list[Finding]:
    code = _strip_comments(unit.text)
    kernel_lines = unit.text.split("\n")
    out: list[Finding] = []
    for match in _KERNEL.finditer(code):
        name, params = match.group(1), match.group(2)
        if any(token in params for token in _TEMPLATED):
            continue
        offset = code.count("\n", 0, match.start())
        for rule, pattern, ctype in ((RULE_DOUBLE, _DOUBLE_PTR, "double"), (RULE_FLOAT, _FLOAT_PTR, "float")):
            if rule == RULE_FLOAT and not include_float:
                continue
            names = _param_names(params, pattern)
            if names and not _marked(unit, kernel_lines, offset, py_lines):
                out.append(
                    Finding(
                        rel,
                        unit.line + offset,
                        rule,
                        f"kernel `{name}` takes `{ctype}*` parameter(s) {', '.join(names)} with no `{{REAL}}` template: a buffer of another element type is read past its end or as the wrong values; "
                        "template the value type or mark `// dtype-ok: <reason>`",
                    )
                )
    return out


def _probe_findings(tree: ast.Module, rel: str, py_lines: list[str]) -> list[Finding]:
    """``"float" if X.dtype == <float32> else "double"`` selectors whose ``X`` the module never passes to a launch tuple by name."""
    named = {
        ast.unparse(el)
        for call in nodes_of(tree, ast.Call)
        for arg in call.args
        if isinstance(arg, ast.Tuple)
        for el in arg.elts
        if isinstance(el, (ast.Name, ast.Attribute))
    }
    out: list[Finding] = []
    for fn in nodes_of(tree, ast.FunctionDef, ast.AsyncFunctionDef):
        for node in nodes_of(fn, ast.IfExp):
            if not (
                isinstance(node, ast.IfExp)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Attribute)
                and node.test.left.attr == "dtype"
                and isinstance(node.body, ast.Constant)
                and node.body.value == "float"
                and isinstance(node.orelse, ast.Constant)
                and node.orelse.value == "double"
            ):
                continue
            probed = ast.unparse(node.test.left.value)
            line = py_lines[node.lineno - 1] if 0 < node.lineno <= len(py_lines) else ""
            if probed in named or ("#" in line and MARKER in line[line.index("#") :]):
                continue
            out.append(
                Finding(
                    rel,
                    node.lineno,
                    RULE_PROBE,
                    f"in `{fn.name}` the kernel type follows `{probed}.dtype` but `{probed}` is never a launch argument by name: probe the buffer that is launched, not an array cast before the launch",
                )
            )
    return out


def find_cuda_kernel_pointer_types(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every untemplated pointer parameter in a CUDA kernel string and every kernel-type probe off the launched buffer under *root*.

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
        out.extend(_probe_findings(parsed.tree, parsed.rel, py_lines))
    return sorted(out, key=lambda f: (f.path, f.line, f.rule))


def assert_cuda_kernel_pointer_types(
    root: Union[str, Path],
    *,
    include_advisory: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any untemplated ``double*`` kernel parameter or off-buffer type probe (and on ``float*`` with *include_advisory*).

    With *baseline_path*, fail only on findings the baseline does not accept.
    """
    found = find_cuda_kernel_pointer_types(root, include_advisory=include_advisory, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "template the value type on {REAL} (sums and statistics on {ACC}) and compile one module per type from the launched buffer dtype; `// dtype-ok: <reason>` when float64 is deliberate"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_kernel_pointer_types", refresh_command="PY_CI_SHARED_REFRESH=cuda_kernel_pointer_types")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} CUDA kernel pointer-type finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
