"""Runs the documented clang-tidy pass (CUDA.md section 17) over the CUDA kernel strings of a package.

clang-tidy cannot see inside a Python string, so each kernel is extracted (``cuda_kernel_sources.find_cuda_kernel_sources``), its placeholders are replaced by plausible literals
(``{REAL}`` -> ``float``, ``{ACC}`` -> ``double``, a ``%NAME%`` / ``{NAME}`` that names a size -> ``256``, or whatever ``--define NAME=VALUE`` says), and one ``.cu`` file per kernel is written to a
scratch directory. A placeholder that cannot be resolved is REPORTED as a skipped kernel naming the placeholder (the audit found a file failing on ``const 1* cols``); nothing is dropped silently.

Check set (CUDA.md 17): bugprone-integer-division, bugprone-sizeof-expression, clang-analyzer-deadcode.DeadStores, clang-analyzer-core.*, bugprone-suspicious-missing-comma,
bugprone-signed-char-misuse and bugprone-narrowing-conversions with the idiomatic ``blockIdx``/``threadIdx``/``blockDim``/``gridDim`` assignments filtered out (the audit measured 24 of 29
warnings as that noise). Findings print as ``file::kernel: check: message``.

Needs no GPU. clang-tidy is looked up on PATH, in ``CLANG_TIDY``, then in the Visual Studio LLVM (``VC/Tools/Llvm``). When it is absent the run SKIPS LOUDLY: exit code 3 and a message that
lists where it looked; ``--require`` turns that into exit 4 (a failure). Exit 0 = ran and clean, 1 = findings or clang-tidy errors on a kernel, 2 = bad usage.

Usage::

    python -m py_ci_shared.cuda_clang_tidy src/pkg --exclude _benchmarks --arch sm_61 --require
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence
from typing import Optional, Union

from .cuda_kernel_sources import find_cuda_kernel_sources

__all__ = ["CHECKS", "TidyReport", "find_clang_tidy", "find_cuda_clang_tidy", "main", "resolve_placeholders", "EXIT_SKIPPED", "EXIT_REQUIRED_MISSING"]

CHECKS = (
    "-*,bugprone-narrowing-conversions,bugprone-integer-division,bugprone-sizeof-expression,clang-analyzer-deadcode.DeadStores,"
    "clang-analyzer-core.*,bugprone-suspicious-missing-comma,bugprone-signed-char-misuse"
)
EXIT_SKIPPED = 3
EXIT_REQUIRED_MISSING = 4

_PLACEHOLDER = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%|\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SIZE_HINT = re.compile(r"THREAD|BLOCK|TPB|TILE|WARP|WIDTH|SIZE|DIM|BINS|NT$|^K$|^N$|MAX|CHUNK|UNROLL|STRIDE", re.IGNORECASE)
_DTYPES = {"REAL": "float", "ACC": "double", "T": "float", "DTYPE": "float", "REAL_T": "float", "ACC_T": "double"}
_BUILTIN_NOISE = re.compile(r"\b(blockIdx|threadIdx|blockDim|gridDim)\b")
_DIAG = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<sev>warning|error): (?P<msg>.*?)(?: \[(?P<check>[\w.,-]+)\])?$")


def resolve_placeholders(source: str, defines: "Optional[dict[str, str]]" = None) -> "tuple[str, list[str]]":
    """``(text, unresolved)``: *source* with each placeholder replaced; *unresolved* lists the names that have no plausible literal."""
    defs = dict(defines or {})
    unresolved: "list[str]" = []

    def sub(m: "re.Match[str]") -> str:
        name = m.group(1) or m.group(2)
        if name in defs:
            return defs[name]
        if name in _DTYPES:
            return _DTYPES[name]
        shouty = name.isupper() or name.startswith("__")
        if m.group(1) is None and not shouty:
            return m.group(0)  # a lowercase {identifier} is C++ text, not a placeholder
        if _SIZE_HINT.search(name):
            return "256"
        if name not in unresolved:
            unresolved.append(name)
        return m.group(0)

    return _PLACEHOLDER.sub(sub, source), unresolved


def _vs_llvm_candidates() -> "list[Path]":
    found: "list[Path]" = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            vs = Path(base) / "Microsoft Visual Studio"
            if vs.is_dir():
                found.extend(sorted(vs.glob("*/*/VC/Tools/Llvm/bin/clang-tidy.exe"), reverse=True))
                found.extend(sorted(vs.glob("*/*/VC/Tools/Llvm/x64/bin/clang-tidy.exe"), reverse=True))
    return found


def find_clang_tidy() -> "tuple[Optional[str], list[str]]":
    """``(path or None, places tried)``: PATH, then ``CLANG_TIDY``, then the Visual Studio LLVM."""
    tried: "list[str]" = []
    on_path = shutil.which("clang-tidy")
    tried.append("PATH (clang-tidy)")
    if on_path:
        return on_path, tried
    env = os.environ.get("CLANG_TIDY")
    tried.append(f"CLANG_TIDY={env!r}")
    if env and Path(env).is_file():
        return env, tried
    cands = _vs_llvm_candidates()
    tried.append("Visual Studio VC/Tools/Llvm (" + (", ".join(str(c) for c in cands) or "none found") + ")")
    return (str(cands[0]) if cands else None), tried


def _cuda_path() -> "Optional[str]":
    for key in ("CUDA_PATH", "CUDA_HOME"):
        if os.environ.get(key) and Path(os.environ[key]).is_dir():
            return os.environ[key]
    root = Path("C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA")
    if root.is_dir():
        vers = sorted(p for p in root.iterdir() if p.is_dir())
        if vers:
            return str(vers[-1])
    return None


@dataclass
class TidyReport:
    """Result of one pass: findings (``file::kernel: check: message``), skipped kernels (with the placeholder), kernels checked, and tool errors."""

    findings: "list[str]" = field(default_factory=list)
    skipped: "list[str]" = field(default_factory=list)
    errors: "list[str]" = field(default_factory=list)
    checked: int = 0
    available: bool = True
    tried: "list[str]" = field(default_factory=list)


def _safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key.replace("::", "__")) + ".cu"


def _parse_output(text: str, key: str, name: str, src_lines: "list[str]") -> "list[str]":
    out: "list[str]" = []
    for raw in text.splitlines():
        m = _DIAG.match(raw.strip())
        if not m or not m.group("check") or Path(m.group("file")).name != name:
            continue
        check = m.group("check")
        line_no = int(m.group("line"))
        src = src_lines[line_no - 1] if 0 < line_no <= len(src_lines) else ""
        if check == "bugprone-narrowing-conversions" and _BUILTIN_NOISE.search(src):
            continue
        item = f"{key}: {check}: {m.group('msg')} (line {line_no} of the extracted kernel)"
        if item not in out:
            out.append(item)
    return out


def find_cuda_clang_tidy(
    root: "Union[str, Path]",
    *,
    exclude: "Sequence[str]" = (),
    key_base: "Optional[Union[str, Path]]" = None,
    arch: str = "sm_61",
    defines: "Optional[dict[str, str]]" = None,
    clang_tidy: Optional[str] = None,
    cuda_path: Optional[str] = None,
    keep_dir: "Optional[Union[str, Path]]" = None,
    limit: Optional[int] = None,
    only: Optional[str] = None,
    use_git: Optional[bool] = None,
    timeout: float = 600.0,
) -> TidyReport:
    """Extract, de-template and clang-tidy every kernel under *root*; see the module docstring. ``available`` is False (and nothing runs) when clang-tidy is not found."""
    exe, tried = (clang_tidy, ["explicit"]) if clang_tidy else find_clang_tidy()
    report = TidyReport(tried=tried)
    if not exe:
        report.available = False
        return report
    sources = find_cuda_kernel_sources(root, exclude=exclude, key_base=key_base, use_git=use_git)
    if only:
        sources = {k: v for k, v in sources.items() if only in k}
    if limit is not None:
        sources = dict(list(sources.items())[:limit])
    cuda = cuda_path or _cuda_path()
    out_dir = Path(keep_dir) if keep_dir else Path(tempfile.mkdtemp(prefix="cuda_clang_tidy_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for key, src in sources.items():
            text, unresolved = resolve_placeholders(src, defines)
            if unresolved:
                report.skipped.append(f"{key}: SKIPPED, unresolved placeholder(s) {', '.join(unresolved)} (pass --define NAME=VALUE)")
                continue
            name = _safe_name(key)
            (out_dir / name).write_bytes(text.encode("utf-8"))
            cmd = [exe, str(out_dir / name), f"--checks={CHECKS}", "--header-filter=^$", "--", f"--cuda-gpu-arch={arch}", "-x", "cuda", "-w"]
            if cuda:
                cmd.append(f"--cuda-path={cuda}")
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
            report.checked += 1
            found = _parse_output(proc.stdout, key, name, text.splitlines())
            report.findings.extend(found)
            both = proc.stdout + proc.stderr
            if proc.returncode != 0 and not found and re.search(r"\berror\b", both):
                first = next((ln for ln in both.splitlines() if "error" in ln), "").strip()
                report.errors.append(f"{key}: clang-tidy could not compile the kernel: {first}")
    finally:
        if not keep_dir:
            shutil.rmtree(out_dir, ignore_errors=True)
    return report


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    """Run clang-tidy over the CUDA kernel strings under root and return the exit status."""
    ap = argparse.ArgumentParser(prog="python -m py_ci_shared.cuda_clang_tidy", description="clang-tidy pass over CUDA kernel strings (CUDA.md 17)")
    ap.add_argument("root")
    ap.add_argument("--exclude", action="append", default=[], help="path part to skip (repeatable)")
    ap.add_argument("--arch", default="sm_61")
    ap.add_argument("--define", action="append", default=[], help="NAME=VALUE for a placeholder (repeatable)")
    ap.add_argument("--clang-tidy", dest="clang_tidy")
    ap.add_argument("--cuda-path")
    ap.add_argument("--keep-dir", help="keep the extracted .cu files here")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="substring of the kernel key")
    ap.add_argument("--require", action="store_true", help="a missing clang-tidy is a failure (exit 4) instead of a loud skip (exit 3)")
    args = ap.parse_args(argv)
    defines = {}
    for d in args.define:
        if "=" not in d:
            print(f"cuda_clang_tidy: --define needs NAME=VALUE, got {d!r}", file=sys.stderr)  # noqa: T201
            return 2
        k, v = d.split("=", 1)
        defines[k] = v
    rep = find_cuda_clang_tidy(
        args.root, exclude=args.exclude, arch=args.arch, defines=defines, clang_tidy=args.clang_tidy, cuda_path=args.cuda_path, keep_dir=args.keep_dir, limit=args.limit, only=args.only
    )
    if not rep.available:
        tag = "FAILED" if args.require else "SKIPPED (NOT A PASS)"
        print(f"cuda_clang_tidy: {tag}: clang-tidy not found; no kernel was analysed. Looked in: " + "; ".join(rep.tried), file=sys.stderr)  # noqa: T201
        return EXIT_REQUIRED_MISSING if args.require else EXIT_SKIPPED
    for line in rep.skipped + rep.errors + rep.findings:
        print(line)  # noqa: T201
    print(f"cuda_clang_tidy: {rep.checked} kernel(s) checked, {len(rep.findings)} finding(s), {len(rep.skipped)} skipped, {len(rep.errors)} compile error(s)")  # noqa: T201
    return 1 if (rep.findings or rep.errors) else 0


if __name__ == "__main__":
    from ._core.console import safe_stdio

    safe_stdio()
    sys.exit(main())
