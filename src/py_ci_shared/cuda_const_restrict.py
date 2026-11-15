"""A CUDA kernel pointer parameter the kernel never writes through is declared ``const`` (and, where it does not alias, ``__restrict__``).

``const T* __restrict__ p`` lets the compiler route loads through the read-only data cache (``__ldg``) and reorder them freely; the NVIDIA CUDA C++ Best Practices Guide documents both. The gain
is not a number, so the rule is advisory in nature. Audit 2026-11-16, report 54, gate G7.

* ``cuda-pointer-param-not-const``: a ``__global__`` function with a single-level pointer parameter whose pointee is not ``const`` (``int* c``, ``float* __restrict__ x``; ``int* const p`` is still
  a non-const pointee) and that the body only reads through, as ``p[...]``. The ``__restrict__`` keyword is read from the signature so the message suggests ``const T* __restrict__``; it is not a rule of
  its own (the spec defines none).

A parameter counts as written, or as escaping (and is not reported), when any use of its name is: a subscript chain followed by ``=`` or a compound assignment or ``++``/``--``; preceded by ``++``/``--``;
preceded by a unary ``&`` (an atomic argument, ``&p[i]``, also through a cast ``(unsigned long long*)&p[i]``); bound to a reference; or any bare use of the name that is not a subscript (``p + i``,
``(c ? p : q)``, a call argument, ``*p``): an alias that the gate cannot follow. Subscripts are matched with balanced brackets, so ``a[b[i]] = x`` writes ``a`` and only reads ``b``. A parameter with no
use at all is not reported. The prototype history in the report (15 hits, then 5, then 1) came from exactly these cases, and each is pinned by a test and by the canary.

Suppression: ``// const-ok: <reason>`` (or ``/* const-ok: <reason> */``) from the line above the signature to the end of the kernel; an empty reason does not suppress.

Measured (the audit's prototype over mlframe): 1032 pointer parameters, 306 without ``const``, 1 never written: ``compute_mi_from_classes_cuda`` (``int* joint_counts``), a true hit.

Usage in a consumer's meta test::

    from py_ci_shared.cuda_const_restrict import assert_cuda_const_restrict

    def test_read_only_pointer_params_are_const():
        assert_cuda_const_restrict("src", min_files=50, baseline_path="the consumer's own baseline file")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python
from .cuda_atomic_determinism import Func, functions, match, prepare
from .cuda_kernel_integer_width import _units

__all__ = ["MARKER", "RULE", "assert_cuda_const_restrict", "find_cuda_const_restrict"]

RULE = "cuda-pointer-param-not-const"
MARKER = "const-ok:"

_WRITE_AFTER = re.compile(r"\s*(?:(?:\+|-|\*|/|%|&|\||\^|<<|>>)?=(?!=)|\+\+|--)")
_OPERAND_END = re.compile(r"[\w\]]$")


def _pointer_params(params: str) -> "list[tuple[str, bool]]":
    """``(name, restrict)`` for every single-level, non-const-pointee pointer parameter."""
    out = []
    for part in params.split(","):
        if part.count("*") != 1:
            continue
        head, _star, tail = part.partition("*")
        if re.search(r"\bconst\b", head):
            continue
        words = re.findall(r"[A-Za-z_]\w*", tail)
        words = [w for w in words if w not in ("const", "volatile", "__restrict__", "restrict")]
        if len(words) == 1:
            out.append((words[0], "__restrict__" in tail))
    return out


def _address_or_prefix_write(before: str) -> bool:
    """True when the text before a use makes it a write or an escape: ``++p[i]``, ``&p[i]`` (also after a cast), or a reference binding."""
    if before.endswith(("++", "--")):
        return True
    if before.endswith("&") and not before.endswith("&&") and not _OPERAND_END.search(before[:-1].rstrip()):
        return True  # &p[i]: an atomic target or an escaping address, also (T*)&p[i]
    return bool(re.search(r"&\s*\w+\s*=$", before))


def _after_subscripts(body: str, index: int) -> "Optional[str]":
    """The text after the whole ``[..][..]`` chain starting at *index* (balanced brackets, so ``a[b[i]]`` ends at the outer bracket); ``None`` when unbalanced."""
    while True:
        close = match(body, index, "[", "]")
        if close < 0:
            return None
        rest = body[close + 1 :]
        if rest.lstrip()[:1] != "[":
            return rest
        index = close + 1 + (len(rest) - len(rest.lstrip()))


def _only_read(name: str, body: str) -> bool:
    """True when *name* occurs in *body* and every occurrence is a subscript chain that is only read."""
    seen = False
    for use in re.finditer(rf"(?<![\w.>]){re.escape(name)}\b", body):
        seen = True
        tail = body[use.end() :].lstrip()
        if tail[:1] != "[" or _address_or_prefix_write(body[: use.start()].rstrip()):
            return False  # a bare use is an alias or an escape
        rest = _after_subscripts(body, body.index("[", use.end()))
        if rest is None or _WRITE_AFTER.match(rest):
            return False
    return seen


def _noted(raw_lines: "list[str]", first: int, last: int) -> bool:
    text = "\n".join(raw_lines[max(first, 0) : last + 1])
    return any(f.group(1).strip() for f in re.finditer(r"(?://|/\*)[^\n]*?const-ok:[ \t]*([^\s*/][^\n]*)", text))


def _kernel_findings(code: str, raw_lines: "list[str]", func: Func, rel: str, base_line: int) -> "list[Finding]":
    if not func.is_global:
        return []
    first = code.count("\n", 0, func.sig_start)
    last = code.count("\n", 0, func.body_end)
    if _noted(raw_lines, first - 1, last):
        return []
    body = code[func.body_start : func.body_end + 1]
    out = []
    for name, restrict in _pointer_params(func.params):
        if _only_read(name, body):
            fix = "const T* __restrict__" if restrict else "const T*"
            out.append(
                Finding(
                    rel,
                    base_line + first,
                    RULE,
                    f"kernel `{func.name}` never writes through pointer parameter `{name}`: declare it `{fix} {name}` (read-only cache, the compiler may reorder its loads) or mark `// const-ok: <reason>`",
                )
            )
    return out


def find_cuda_const_restrict(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> "list[Finding]":
    """Every read-only, non-const pointer parameter of a ``__global__`` function in a kernel string under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code
    must not pass it.
    """
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: "list[Finding]" = []
    for parsed in scan:
        aliases = ImportAliases.from_tree(parsed.tree)
        for unit in _units(parsed.tree, aliases):
            if "__global__" not in unit.text:
                continue
            code = prepare(unit.text)
            raw_lines = unit.text.split("\n")
            for func in functions(code):
                out.extend(_kernel_findings(code, raw_lines, func, parsed.rel, unit.line))
    return sorted(out, key=lambda f: (f.path, f.line, f.message))


def assert_cuda_const_restrict(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any read-only kernel pointer parameter that is not ``const``, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_cuda_const_restrict(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "declare the parameter `const T* __restrict__` (when it does not alias an output), or mark the kernel `// const-ok: <reason>`"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="cuda_const_restrict", refresh_command="PY_CI_SHARED_REFRESH=cuda_const_restrict")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} read-only kernel pointer parameter(s) not declared const; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
