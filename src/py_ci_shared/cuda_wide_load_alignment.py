"""A kernel that reads a byte array through a wider pointer must declare the pitch its rows are padded to (CUDA.md 19.1).

A wide load (a 4-byte ``unsigned int``, an ``int4``) from a byte array faults when its address is not a multiple of the width. The host pads each row to a multiple of the width, and that
padding is the contract; the kernel can only honour it if the pitch is a parameter. A kernel that casts ``const unsigned char*`` to a wider type and takes no stride, pitch, pad or ``ld``
argument hides that contract in the host code.

* ``cuda-wide-load-alignment`` (hard): inside a CUDA kernel source (the text from :func:`py_ci_shared.cuda_kernel_sources.find_cuda_kernel_sources`), a C-style cast ``(T*)`` or
  ``reinterpret_cast<T*>(...)`` to a type wider than a byte, whose operand is a byte pointer plus an offset (``codes + row * n``; a bare base such as ``(const int*)a`` is not reported). The byte pointer is a parameter or local declared ``char``, ``signed char``, ``unsigned char``, ``int8_t`` or
  ``uint8_t``), in a kernel whose signature declares no pitch parameter. A parameter whose name holds ``stride``, ``pitch``, ``pad`` or ``ld`` as a word counts (``n_pad``, ``ld_codes``).

Not reported: a cast of a word array (``const unsigned int*`` already wide), a cast from a byte to a byte, and a cast on a line that carries ``// align-ok: <reason>`` with a non-empty reason.

Limits, stated because a gate that misses is worse than a gate that says so: the byte operand is matched by name within the cast's statement, so a byte pointer reached only through another
variable is not followed; the pitch test is by name, so a parameter named otherwise but holding a pitch is reported (suppress with the reason). Findings are reported with the kernel's
key (``path::NAME``) and the line within the kernel text.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_wide_load_alignment import assert_cuda_wide_load_alignment

    def test_wide_loads_declare_their_pitch():
        assert_cuda_wide_load_alignment("src", min_files=50, exclude=("_benchmarks",))
"""

from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Iterable
from typing import Optional, Union

from ._core import Finding
from .cuda_kernel_sources import find_cuda_kernel_sources

__all__ = ["MARKER", "RULE", "assert_cuda_wide_load_alignment", "find_cuda_wide_load_alignment"]

RULE = "cuda-wide-load-alignment"
MARKER = "align-ok:"

_BYTE_TYPE = r"(?:unsigned\s+char|signed\s+char|char|uint8_t|int8_t)"
_KERNEL_SIGNATURE = re.compile(r"__global__\s+[\w\s]*?\b(\w+)\s*\(([^)]*)\)\s*\{")
_BYTE_DECL = re.compile(r"\b" + _BYTE_TYPE + r"\s*\*\s*(?:const\s+)?(\w+)")
_C_CAST = re.compile(r"\(\s*((?:const\s+)?[\w ]+?)\s*\*\s*\)\s*")
_REINTERPRET = re.compile(r"reinterpret_cast\s*<\s*((?:const\s+)?[\w ]+?)\s*\*\s*>\s*\(")
_PITCH_NAME = re.compile(r"(?i)stride|pitch|pad|(?:^|_)ld(?:_|\d|$)")
_MARKER_WITH_REASON = re.compile(re.escape(MARKER) + r"\s*[^\s#]")


def _normal(type_text: str) -> str:
    return " ".join(type_text.split())


def _is_wide(type_text: str) -> bool:
    """A cast target wider than a byte; the qualifier ``const`` does not change the width."""
    base = " ".join(w for w in _normal(type_text).split() if w != "const")
    return base != "" and base not in {"char", "unsigned char", "signed char", "uint8_t", "int8_t", "void"}


def _params(signature: str) -> "list[tuple[str, str]]":
    """``(declared type, name)`` of each parameter of a kernel signature."""
    out: "list[tuple[str, str]]" = []
    for chunk in signature.split(","):
        chunk = chunk.strip()
        match = re.match(r"^(.*?)(\w+)$", chunk, re.S)
        if match and match.group(1).strip():
            out.append((match.group(1).strip(), match.group(2)))
    return out


def _operand(text: str, start: int) -> str:
    """The rest of the statement from *start* (the end of the cast), up to the first ``;`` or 160 characters."""
    end = text.find(";", start)
    stop = len(text) if end < 0 else end
    return text[start : min(stop, start + 160)]


def _findings(key: str, source: str) -> "list[Finding]":
    out: "list[Finding]" = []
    lines = source.splitlines()
    for sig in _KERNEL_SIGNATURE.finditer(source):
        kernel, params_text = sig.group(1), sig.group(2)
        params = _params(params_text)
        if any(_PITCH_NAME.search(name) for _type, name in params):
            continue  # the pitch is a parameter: the host contract is on the signature
        body_start = sig.end()
        depth, pos = 1, body_start
        while pos < len(source) and depth:
            depth += {"{": 1, "}": -1}.get(source[pos], 0)
            pos += 1
        body = source[body_start:pos]
        byte_names = {name for type_text, name in params if "*" in type_text and re.search(_BYTE_TYPE, type_text)}
        byte_names |= set(_BYTE_DECL.findall(body))
        if not byte_names:
            continue
        for cast in [*_C_CAST.finditer(body), *_REINTERPRET.finditer(body)]:
            if not _is_wide(cast.group(1)):
                continue
            operand = _operand(body, cast.end())
            # narrowed after measurement (4 of the first 5 corpus hits were false): only a byte ARRAY addressed by a byte OFFSET is a misaligned-load risk.
            # A bare base such as ``(const unsigned int*)ybits`` followed by word indexing is aligned whenever the base is, and is not reported.
            if not any(re.match(r"\s*\(?\s*" + re.escape(name) + r"\s*\+", operand) for name in byte_names):
                continue
            absolute = body_start + cast.start()
            line_no = source.count("\n", 0, absolute) + 1
            text_line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            if "//" in text_line and _MARKER_WITH_REASON.search(text_line[text_line.index("//") :]):
                continue
            out.append(
                Finding(
                    key,
                    line_no,
                    RULE,
                    f"kernel {kernel} reads a byte array through a {_normal(cast.group(1))}* load and declares no pitch parameter (stride, pitch, pad or ld): add the padded row pitch to the signature, or mark the cast `// {MARKER} <reason>` (CUDA.md 19.1)",
                )
            )
    return out


def find_cuda_wide_load_alignment(
    root: Union[str, Path],
    *,
    exclude: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every wide byte-pointer load in a kernel under *root* that declares no pitch parameter, sorted by key and line."""
    sources = find_cuda_kernel_sources(root, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    out: "list[Finding]" = []
    for key, source in sources.items():
        out.extend(_findings(key, source))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_wide_load_alignment(
    root: Union[str, Path],
    *,
    exclude: Iterable[str] = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any wide byte-pointer load without a declared pitch. Hard: no baseline."""
    found = find_cuda_wide_load_alignment(root, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    if found:
        raise AssertionError(f"{len(found)} wide byte load(s) without a declared pitch:\n  " + "\n  ".join(f.render() for f in found))
