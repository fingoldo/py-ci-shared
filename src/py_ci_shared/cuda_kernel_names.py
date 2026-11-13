"""Every ``__global__`` kernel name written in a source file appears in a kernel source the offline compile check built for that file, so a dropped kernel is a finding, not a pass.

An offline compile check (NVRTC then ptxas on the kernel sources found in the code) is only as complete as its extractor. Kernels written inline in a ``RawKernel`` call, assigned to an attribute, or the second and later
literals of one assignment are easy to lose, and a file-level coverage check passes as soon as ONE kernel of the file was extracted.

* ``cuda-kernel-not-compiled``: a ``__global__`` function name that occurs in a file's text but in none of the sources the consumer's check compiled for that file. The consumer passes ``compiled``, a mapping from a file's
  path relative to *root* (posix separators) to the kernel source texts built for it. A template spelling such as ``bin_codes_TYPENAME`` (an upper-case run of 4 or more letters inside the name) matches a compiled name that
  starts with its fixed prefix (at least 5 characters), since the check builds the concrete names.

Not reported: a file without ``__global__`` text, a path excluded by *exclude* (benchmarks, prototypes), and a kernel name that a compiled source of the same file contains.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_kernel_names import assert_cuda_kernel_names

    def test_every_global_kernel_name_is_compiled():
        compiled = {}
        for variant in all_variants():
            compiled.setdefault(variant.path, []).append(variant.source)
        assert_cuda_kernel_names("src/mlframe", compiled=compiled, exclude=("_benchmarks",), min_files=300)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = ["RULE", "assert_cuda_kernel_names", "find_cuda_kernel_names"]

RULE = "cuda-kernel-not-compiled"
_NAME = re.compile(r"__global__\s+(?:void\s+)?(?:__launch_bounds__\([^)]*\)\s*)?(?:void\s+)?(\w+)\s*\(")
_TEMPLATE_TOKEN = re.compile(r"[A-Z]{4,}")
_MIN_PREFIX = 5


def _prefix(name: str) -> str:
    """The fixed part of a kernel name spelled with a template token (``bin_codes_TYPENAME`` -> ``bin_codes_``); the name itself when it has none."""
    match = _TEMPLATE_TOKEN.search(name)
    return name[: match.start()] if match else name


def _compiled_names(sources: Iterable[str]) -> "set[str]":
    return {name for source in sources for name in _NAME.findall(source)}


def _covered(name: str, have: "set[str]") -> bool:
    if name in have:
        return True
    prefix = _prefix(name)
    return prefix != name and len(prefix) >= _MIN_PREFIX and any(h.startswith(prefix) for h in have)


def find_cuda_kernel_names(
    root: Union[str, Path],
    *,
    compiled: "Mapping[str, Iterable[str]]",
    exclude: "Iterable[str]" = (),
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every ``__global__`` kernel name under *root* that no source in *compiled* (file path relative to *root* -> kernel source texts) contains, sorted by path and line.

    *exclude* names path parts (``("_benchmarks",)``) whose files are out of scope. Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or
    parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    skip = tuple(exclude)
    have_by_file = {path: _compiled_names(sources) for path, sources in compiled.items()}
    out: "list[Finding]" = []
    for parsed in scan:
        if "__global__" not in parsed.source or any(part in parsed.rel.split("/") for part in skip):
            continue
        have = have_by_file.get(parsed.rel, set())
        lines = parsed.source.splitlines()
        for name in sorted(set(_NAME.findall(parsed.source)) - {"void"}):
            if _covered(name, have):
                continue
            line = next(
                (i for i, text in enumerate(lines, 1) if re.search(rf"\b{re.escape(name)}\s*\(", text) and "__global__" in " ".join(lines[max(0, i - 3) : i])),
                1,
            )
            out.append(
                Finding(
                    parsed.rel,
                    line,
                    RULE,
                    f"the kernel {name} is written in this file but no source the offline compile check built for it contains it: extend the extractor or the recipe so it is compiled",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_cuda_kernel_names(
    root: Union[str, Path],
    *,
    compiled: "Mapping[str, Iterable[str]]",
    exclude: "Iterable[str]" = (),
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any kernel name no compiled source contains, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_kernel_names(root, compiled=compiled, exclude=exclude, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "make the offline compile check build the kernel (extractor or recipe), or exclude the path with the reason it is out of scope"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_kernel_names", refresh_command="PY_CI_SHARED_REFRESH=cuda_kernel_names")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} kernel name(s) never compiled offline; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
