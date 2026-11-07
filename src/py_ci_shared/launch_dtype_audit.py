"""Launch-time audit of CUDA kernel arguments: every array bound to a pointer parameter must have that pointer's element type.

mlframe 6e2cc9c97: ``row_argmax`` declared ``const double*`` while the resident operands were float32; the kernel read twice the bytes the buffer holds (wrong indices, then an illegal address, then ``Fatal Python error:
Aborted`` in the next ``cp.argsort``). The static gate :mod:`py_ci_shared.cuda_kernel_pointer_types` flags that exact declaration. It cannot see a kernel whose source is a ``{REAL}`` template: the declaration is
fine, and the defect is the buffer the host hands the instance compiled for the other type. mlframe's ``kernel_arg_dtype_audit`` caught that at launch time (it wraps ``cupy.RawKernel``) but lived in one repository,
imported cupy at module level and needed a GPU to be exercised at all. This is the shared form.

Everything that decides a verdict is plain Python over C signature text and dtype names, so it runs on a CPU-only runner: arguments are duck-typed (an array has ``dtype`` and ``shape``; a scalar is a
:class:`numbers.Number`), cupy is imported only inside :func:`audit_cupy` and numpy is never imported. A consumer tests its launch code with a fake kernel object (``def fake(grid, block, args): ...``), and its GPU
job, if it has one, runs the same wrapper around the real ``RawKernel``.

Pieces:

* :func:`kernel_parameters(source, name, defines=None)` parses ``__global__ void name(...)`` into :class:`Param` records. ``defines={"REAL": "float"}`` fills ``{REAL}`` placeholders (and ``typedef``/``#define`` names the
  source declares) before parsing, which is what makes a templated kernel auditable: pass the SAME substitution that was used to compile.
* :func:`check_launch_args(params, args)` returns a :class:`Mismatch` for each pointer bound to an array of another dtype (or a strided view), each scalar of another kind or a narrower width, and an array-count mismatch.
* :func:`audited_kernel(kernel, source, name, defines=None, strict=True)` wraps a callable ``kernel(grid, block, args, **kw)``: a mismatch raises :class:`LaunchDtypeError` BEFORE the launch (``strict``) or is
  recorded on ``.mismatches`` and the launch goes ahead. Any other attribute is the wrapped kernel's.
* :func:`assert_launch_dtypes(source, name, args, defines=None)` is the same check as one assertion, for a test that builds the argument list on the host and has no kernel at all.
* :func:`audit_cupy()` is a context manager replacing ``cupy.RawKernel``/``RawModule`` with auditing twins while a package compiles its kernels (the mlframe ``install()``); ``python -m py_ci_shared.launch_dtype_audit
  script.py [args]`` runs a script under it and exits 1 on a mismatch.

Not judged: an unresolved macro or typedef parameter (skipped, never guessed), struct parameters, device-side indexing, and whether the SIZE argument matches the buffer length.

Usage in a consumer's test::

    from py_ci_shared.launch_dtype_audit import assert_launch_dtypes

    def test_row_argmax_binds_float32_operands_to_the_float_instance():
        assert_launch_dtypes(ROW_ARGMAX_SRC, "row_argmax", (x_float32, out_int32, np.int32(n)), defines={"REAL": "float"})
"""

from __future__ import annotations

import argparse
import contextlib
import numbers
import re
import runpy
import sys
import traceback
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Optional

__all__ = [
    "AuditedKernel",
    "LaunchDtypeError",
    "Mismatch",
    "Param",
    "assert_launch_dtypes",
    "audit_cupy",
    "audited_kernel",
    "check_launch_args",
    "kernel_parameters",
    "main",
]

_DTYPE_OF_C = {
    "double": "float64",
    "float": "float32",
    "half": "float16",
    "int": "int32",
    "unsigned int": "uint32",
    "unsigned": "uint32",
    "long long": "int64",
    "unsigned long long": "uint64",
    "signed char": "int8",
    "char": "int8",
    "unsigned char": "uint8",
    "short": "int16",
    "unsigned short": "uint16",
    "long": "int32",
    "bool": "bool",
    "int8_t": "int8",
    "int16_t": "int16",
    "int32_t": "int32",
    "int64_t": "int64",
    "uint8_t": "uint8",
    "uint16_t": "uint16",
    "uint32_t": "uint32",
    "uint64_t": "uint64",
    "size_t": "uint64",
}
_SIGNATURE = re.compile(r"(?:extern\s+\"C\"\s+)?__global__\s+(?:__launch_bounds__\([^)]*\)\s+)?void\s+(\w+)\s*\((.*?)\)\s*\{", re.S)
_QUALIFIERS = re.compile(r"\b(const|__restrict__|restrict|volatile|__restrict)\b")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_TYPEDEF = re.compile(r"typedef\s+((?:unsigned\s+|signed\s+)?[A-Za-z_][\w ]*?)\s+(\w+)\s*;")
_DEFINE = re.compile(r"^[ \t]*#\s*define\s+(\w+)[ \t]+([A-Za-z_][\w ]*?)[ \t]*$", re.M)


@dataclass(frozen=True)
class Param:
    """One kernel parameter: pointer or scalar, and the dtype name its C type maps to (None when it is a macro or a struct)."""

    position: int
    is_pointer: bool
    ctype: str
    dtype: Optional[str]


@dataclass(frozen=True)
class Mismatch:
    """One launch-argument problem; ``key`` is stable and hashable for de-duplication."""

    kernel: str
    position: int
    expected: str
    got: str

    @property
    def key(self) -> "tuple[str, int, str, str]":
        return (self.kernel, self.position, self.expected, self.got)

    def render(self) -> str:
        return f"kernel {self.kernel} argument #{self.position}: expects {self.expected}, got {self.got}"


class LaunchDtypeError(AssertionError):
    """A kernel was launched (or would be) with an argument whose dtype differs from its declared parameter type."""


def _fill_placeholders(source: str, defines: Optional[Mapping[str, str]]) -> str:
    if not defines:
        return source
    return _PLACEHOLDER.sub(lambda m: str(defines.get(m.group(1), m.group(0))), source)


def _aliases(source: str, defines: Optional[Mapping[str, str]]) -> "dict[str, str]":
    """``{macro or typedef name: C type}`` the source (or the caller) declares."""
    out = {name: ctype for ctype, name in _TYPEDEF.findall(source)}
    out.update({name: ctype for name, ctype in _DEFINE.findall(source)})
    out.update({k: str(v) for k, v in (defines or {}).items()})
    return out


def _dtype_of(ctype: str, aliases: Mapping[str, str]) -> Optional[str]:
    text = re.sub(r"/\*.*?\*/", " ", ctype, flags=re.S)
    text = " ".join(_QUALIFIERS.sub(" ", text).replace("*", " ").split()).replace("long long int", "long long")
    for _ in range(4):  # an alias may name another alias
        if text in _DTYPE_OF_C or text not in aliases:
            break
        text = " ".join(aliases[text].split())
    return _DTYPE_OF_C.get(text)


def _params_of(raw_params: str, aliases: Mapping[str, str]) -> "list[Param]":
    out: "list[Param]" = []
    text = re.sub(r"//[^\n]*", "", raw_params)
    for raw in (piece.strip() for piece in text.split(",")):
        if not raw or raw == "void":
            continue
        body = re.sub(r"\b\w+\s*$", "", raw.replace("*", " * ")).strip()
        out.append(Param(len(out), "*" in raw, body, _dtype_of(body, aliases)))
    return out


def kernel_parameters(source: str, name: str, defines: Optional[Mapping[str, str]] = None) -> "Optional[list[Param]]":
    """The parameters of kernel *name* in *source* after ``{PLACEHOLDER}`` substitution by *defines*, or None when no such kernel is declared."""
    filled = _fill_placeholders(source, defines)
    aliases = _aliases(filled, defines)
    for match in _SIGNATURE.finditer(filled):
        if match.group(1) == name:
            return _params_of(match.group(2), aliases)
    return None


def _is_scalar(arg: Any) -> bool:
    return isinstance(arg, numbers.Number)


def _arg_dtype(arg: Any) -> str:
    dtype = getattr(arg, "dtype", None)
    if dtype is not None:
        return str(dtype)
    return {bool: "bool", int: "int", float: "float"}.get(type(arg), type(arg).__name__)


def _kind(dtype: str) -> str:
    return "b" if dtype == "bool" else "f" if dtype.startswith("float") else "u" if dtype.startswith("uint") else "i" if dtype.startswith("int") else "?"


def _width(dtype: str) -> int:
    digits = re.sub(r"\D", "", dtype)
    return int(digits) if digits else 0


def _pointer_problem(arg: Any, param: Param) -> "Optional[tuple[str, str]]":
    if _is_scalar(arg) or not hasattr(arg, "dtype"):
        return (f"an array of {param.dtype}", f"a scalar {_arg_dtype(arg)}" if _is_scalar(arg) else f"a {type(arg).__name__}")
    if str(arg.dtype) != param.dtype:
        return (f"{param.dtype}*", f"a {arg.dtype} array")
    flags = getattr(arg, "flags", None)
    if flags is not None and not (getattr(flags, "c_contiguous", True) or getattr(flags, "f_contiguous", True)):
        return ("a contiguous buffer", "a strided view")
    return None


def _scalar_problem(arg: Any, param: Param) -> "Optional[tuple[str, str]]":
    if not _is_scalar(arg):
        return (f"scalar {param.dtype}", f"a {type(arg).__name__}") if hasattr(arg, "dtype") and getattr(arg, "shape", ()) != () else None
    got, want = _arg_dtype(arg), str(param.dtype)
    if got in ("int", "float", "bool"):  # python scalars carry no width: only the kind can be wrong
        return (f"scalar {want}", f"a python {got}") if (got == "float" and _kind(want) != "f") else None
    if _kind(got) != _kind(want) and not (_kind(got) in "iu" and _kind(want) in "iu"):
        return (f"scalar {want}", f"{got}")
    return (f"scalar {want}", f"the narrower {got}") if _width(got) < _width(want) else None


def check_launch_args(params: "Sequence[Param]", args: Sequence[Any], kernel: str = "<kernel>") -> "list[Mismatch]":
    """Every mismatch between *args* and the declared *params*; a parameter whose type is unresolved is skipped."""
    out: "list[Mismatch]" = []
    if len(args) != len(params):
        out.append(Mismatch(kernel, -1, f"{len(params)} argument(s)", f"{len(args)}"))
    for param, arg in zip(params, args):
        if param.dtype is None:
            continue
        problem = _pointer_problem(arg, param) if param.is_pointer else _scalar_problem(arg, param)
        if problem is not None:
            out.append(Mismatch(kernel, param.position, problem[0], problem[1]))
    return out


def assert_launch_dtypes(source: str, name: str, args: Sequence[Any], defines: Optional[Mapping[str, str]] = None) -> None:
    """Fail when *args* do not fit the declared signature of kernel *name* in *source* (``{PLACEHOLDER}``s filled from *defines*); no kernel is run."""
    params = kernel_parameters(source, name, defines)
    if params is None:
        raise LaunchDtypeError(f"kernel {name!r} is not declared with `__global__ void {name}(...)` in the given source")
    found = check_launch_args(params, args, name)
    if found:
        raise LaunchDtypeError("launch arguments do not match the declared kernel signature:\n  " + "\n  ".join(m.render() for m in found))


class AuditedKernel:
    """A kernel handle that checks the argument list before launching; every other attribute is the wrapped kernel's."""

    def __init__(self, kernel: Callable[..., Any], name: str, params: "Optional[list[Param]]", strict: bool = True) -> None:
        object.__setattr__(self, "_kernel", kernel)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_params", params)
        object.__setattr__(self, "_strict", strict)
        object.__setattr__(self, "mismatches", {})

    def __call__(self, grid: Any, block: Any, args: Sequence[Any], *rest: Any, **kwargs: Any) -> Any:
        if self._params is not None:
            found = check_launch_args(self._params, args, self._name)
            for m in found:
                self.mismatches.setdefault(m.key, "".join(traceback.format_stack(limit=6)[:-1]))
            if found and self._strict:
                raise LaunchDtypeError("launch refused, arguments do not match the declared kernel signature:\n  " + "\n  ".join(m.render() for m in found))
        return self._kernel(grid, block, args, *rest, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._kernel, item)


def audited_kernel(
    kernel: Callable[..., Any],
    source: str,
    name: str,
    defines: Optional[Mapping[str, str]] = None,
    *,
    strict: bool = True,
) -> AuditedKernel:
    """Wrap *kernel* so each call is checked against kernel *name*'s signature in *source* (``{PLACEHOLDER}``s filled from *defines*)."""
    params = kernel_parameters(source, name, defines)
    if params is None:
        raise LaunchDtypeError(f"kernel {name!r} is not declared with `__global__ void {name}(...)` in the given source")
    return AuditedKernel(kernel, name, params, strict)


def _module_code(args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
    code = kwargs.get("code", args[0] if args else "")
    return code if isinstance(code, str) else ""


@contextlib.contextmanager
def audit_cupy(*, strict: bool = False, cupy_module: Any = None) -> "Iterator[dict[tuple[str, int, str, str], str]]":
    """Replace ``cupy.RawKernel``/``RawModule`` with auditing twins for the block; yields the live ``{mismatch key: stack}`` dict.

    *strict* raises at the launch instead of recording. *cupy_module* is a namespace with ``RawKernel`` and ``RawModule`` (a fake in a CPU-only test); by default cupy is imported here, never at module import.
    Kernels compiled before the block are not wrapped: enter it before the package builds its kernels. Substituted ``{REAL}`` source is what cupy receives, so the wrapper sees concrete types.
    """
    cp: Any = cupy_module if cupy_module is not None else __import__("cupy")
    real_kernel, real_module = cp.RawKernel, cp.RawModule
    found: "dict[tuple[str, int, str, str], str]" = {}

    def wrap(kernel: Any, name: str, source: str) -> AuditedKernel:
        audited = AuditedKernel(kernel, name, kernel_parameters(source, name), strict)
        object.__setattr__(audited, "mismatches", found)
        return audited

    def raw_kernel(source: str, name: str, *args: Any, **kwargs: Any) -> AuditedKernel:
        return wrap(real_kernel(source, name, *args, **kwargs), name, source)

    class _Module:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._module = real_module(*args, **kwargs)
            self._code = _module_code(args, kwargs)

        def get_function(self, name: str) -> AuditedKernel:
            return wrap(self._module.get_function(name), name, self._code)

        def __getattr__(self, item: str) -> Any:
            return getattr(self._module, item)

    cp.RawKernel, cp.RawModule = raw_kernel, _Module
    try:
        yield found
    finally:
        cp.RawKernel, cp.RawModule = real_kernel, real_module


def main(argv: "Optional[list[str]]" = None) -> int:
    """``python -m py_ci_shared.launch_dtype_audit script.py [args...]``: run the script with cupy's kernel classes audited; exit 1 when a mismatch was recorded."""
    parser = argparse.ArgumentParser(description="Run a script with cupy.RawKernel/RawModule launches checked against their declared pointer types")
    parser.add_argument("script", help="Python file to run")
    parser.add_argument("script_args", nargs=argparse.REMAINDER, help="arguments handed to the script")
    ns = parser.parse_args(argv)
    sys.argv = [ns.script, *ns.script_args]
    with audit_cupy() as found:
        runpy.run_path(ns.script, run_name="__main__")
    lines = [f"ARG_DTYPE_MISMATCHES {len(found)}"]
    for (name, position, expected, got), stack in sorted(found.items()):
        lines.append(f"MISMATCH kernel={name} arg#{position}: expects {expected}, got {got}\n{stack}")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
