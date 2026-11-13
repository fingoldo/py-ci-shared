"""The CUDA kernel source strings of a package, found by AST, so an offline compile check builds every kernel the code can build.

An offline NVRTC/ptxas check needs the kernel text before it can compile anything. Kernels live in Python string constants that each module fills in its own way (``{REAL}`` placeholders, ``%NAME%`` templates,
``.replace`` or ``render`` calls, f-strings, sums of sources), so the text is read from the syntax tree, never by importing the module: no import side effects, no GPU needed, and the same corpus on a machine
without CUDA.

* :func:`find_cuda_kernel_sources` returns ``{"<key base path>::NAME": source}`` for every assignment whose value holds a string literal containing ``__global__``: a plain constant, a literal wrapped in a call
  (``render(...)``, ``.replace(...)``), a concatenation, an f-string (each interpolation becomes the placeholder ``{__FSTRING__}``), a class attribute (``self.SRC = ...``, keyed by the attribute name) and an
  assignment inside a function (several modules build their source at call time). When the value holds more than one kernel literal (a dict of kernels, a sum of sources) each gets its own key: ``NAME``,
  ``NAME#2``, ``NAME#3``, so none is lost to the first.
* :func:`module_str_constant` reads one module-level string constant by name (a ``__device__`` helper that production compiles in front of a kernel).

Not found: a kernel assembled from non-literal parts only, and a source in a file under an *exclude* path part (benchmarks, prototypes).

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_sources import find_cuda_kernel_sources, module_str_constant

    SOURCES = find_cuda_kernel_sources("src/pkg", exclude=("_benchmarks",), key_base="src", min_files=300)
    helper = module_str_constant("src/pkg/feature/_device_helpers.py", "HELPERS_SRC")
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import nodes_of, parse_file, scan_python

__all__ = ["find_cuda_kernel_sources", "module_str_constant"]

_MARK = "__global__"


def _target_name(node: "ast.Assign | ast.AnnAssign") -> "Optional[str]":
    """The assigned name: the first plain name target, else the first attribute name (``self.SRC = ...`` gives ``SRC``)."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return next((t.id for t in targets if isinstance(t, ast.Name)), None) or next((t.attr for t in targets if isinstance(t, ast.Attribute)), None)


def _kernel_literals(value: ast.AST) -> "list[str]":
    """Every string literal in *value* that holds a kernel: constants as written, f-strings with each interpolation replaced by ``{__FSTRING__}``."""
    literals: "list[str]" = []
    for sub in nodes_of(value, ast.Constant, ast.JoinedStr):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and _MARK in sub.value:
            literals.append(sub.value)
        elif isinstance(sub, ast.JoinedStr):
            joined = "".join(str(p.value) if isinstance(p, ast.Constant) else "{__FSTRING__}" for p in sub.values)
            if _MARK in joined:
                literals.append(joined)
    return literals


def find_cuda_kernel_sources(
    root: Union[str, Path],
    *,
    exclude: "Iterable[str]" = (),
    key_base: "Optional[Union[str, Path]]" = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "dict[str, str]":
    """``{"<path>::NAME": source}`` for every kernel-bearing assignment under *root*, in path order.

    The path in a key is relative to *key_base* (default: *root*), posix separators; the same name twice in a file keeps the first source. *exclude* names path parts (``("_benchmarks",)``) out of scope.
    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): an extractor that cannot read the code
    must not report it as kernel-free.
    """
    base = Path(root) if key_base is None else Path(key_base)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    skip = tuple(exclude)
    found: "dict[str, str]" = {}
    for parsed in scan:
        if _MARK not in parsed.source or any(part in parsed.rel.split("/") for part in skip):
            continue
        shown = (Path(root) / parsed.rel).resolve().relative_to(base.resolve()).as_posix()
        for node in nodes_of(parsed.tree, ast.Assign, ast.AnnAssign):
            if node.value is None:
                continue
            name = _target_name(node)
            literals = _kernel_literals(node.value)
            if name and literals:
                for i, literal in enumerate(literals):
                    found.setdefault(f"{shown}::{name}" + (f"#{i + 1}" if i else ""), literal)
    return dict(sorted(found.items()))


def module_str_constant(path: Union[str, Path], name: str) -> str:
    """The module-level string constant *name* of the file *path*, read by AST.

    Raises ``KeyError`` naming the file and the constant when it is not assigned a plain string at module level (an annotated assignment counts, a computed value does not).
    """
    file = Path(path)
    for node in parse_file(file).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value.value
    raise KeyError(f"{file}::{name}")
