"""Code that swaps ``sys.stdout``/``sys.stderr`` must restore them only while its own stream is still installed.

The usual silencer saves the current stream, installs its own, and puts the saved one back in ``finally``. The put-back
is unconditional, and that is the bug: by the time it runs, the saved stream may be closed. Two ways it happens:

* Another swap overlapped this one (a second thread, an interleaved generator, an ``ExitStack``). The inner block's exit
  reinstalls the outer block's devnull, which the outer block then closes.
* Under pytest, the saved stream was a ``capsys`` CaptureIO. The test that owned it ends and pytest closes it, and a
  late restore (from a thread, a generator, a callback) makes that closed object ``sys.stderr`` for the rest of the
  process.

From then on every print raises "I/O operation on closed file". In mlframe the test harness prints at every test's
setup, so after one such leak every later test on the xdist worker errored before running: 13 of 23 in one rerun, none
of them related to the test that leaked.

``contextlib.redirect_stdout`` and ``redirect_stderr`` restore unconditionally as well.

The safe shape restores only if its own stream is still the one installed::

    sys.stderr = devnull
    try:
        ...
    finally:
        if sys.stderr is devnull:
            sys.stderr = old

The check is per function: an assignment to ``sys.stdout``/``sys.stderr`` is reported when the enclosing function (or
module, at top level) never compares that stream by identity. Every ``contextlib.redirect_stdout``/``redirect_stderr``
call is reported. The parse cannot tell a helper that owns the stream for the whole process (a CLI entry point
installing a line-buffered wrapper once) from a silencer; list such sites in ``allow``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path

from ._core import DEFAULT_EXCLUDE, ScanResult, iter_files, scan_python

__all__ = [
    "Finding",
    "assert_standard_streams_restored_only_if_still_ours",
    "find_unconditional_stream_restores",
]

_STREAMS = ("stdout", "stderr")
_REDIRECTS = frozenset({"redirect_stdout", "redirect_stderr"})


class Finding:
    """One unsafe swap: where it is and what it does."""

    def __init__(self, path: Path, lineno: int, what: str) -> None:
        """Record the site."""
        self.path = path
        self.lineno = lineno
        self.what = what

    def __str__(self) -> str:
        """Render as ``path:line  what``, the form an editor can jump to."""
        return f"{self.path.as_posix()}:{self.lineno}  {self.what}"


def _sys_stream(node: ast.AST) -> str:
    """``"stdout"``/``"stderr"`` when ``node`` is ``sys.stdout``/``sys.stderr``, else ""."""
    if isinstance(node, ast.Attribute) and node.attr in _STREAMS and isinstance(node.value, ast.Name) and node.value.id == "sys":
        return node.attr
    return ""


def _identity_checked(scope: ast.AST, stream: str) -> bool:
    """Whether ``scope`` compares ``sys.<stream>`` with ``is`` / ``is not`` anywhere."""
    for node in ast.walk(scope):
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
            if _sys_stream(node.left) == stream or any(_sys_stream(c) == stream for c in node.comparators):
                return True
    return False


def _own_nodes(scope: ast.AST) -> Iterable[ast.AST]:
    """Nodes of ``scope`` that are not inside a nested function, so each assignment is judged by its own function."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _module_findings(path: Path, tree: ast.Module) -> list[Finding]:
    findings: list[Finding] = []
    scopes: list[ast.AST] = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        for node in _own_nodes(scope):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                for element in target.elts if isinstance(target, ast.Tuple) else [target]:
                    stream = _sys_stream(element)
                    if stream and not _identity_checked(scope, stream):
                        findings.append(Finding(path, getattr(node, "lineno", 0), f"assigns sys.{stream} with no check that its own stream is still installed"))
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name in _REDIRECTS:
                    findings.append(Finding(path, getattr(node, "lineno", 0), f"contextlib.{name} restores unconditionally"))
    return sorted(findings, key=lambda f: (f.lineno, f.what))


def _scan(roots: Sequence[Path], exclude: Iterable[str]) -> ScanResult:
    excluded = tuple(exclude)
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in iter_files(Path(root), ("*.py",), exclude=DEFAULT_EXCLUDE) if not any(fragment in p.as_posix() for fragment in excluded))
    return scan_python(files)


def _findings(scan: ScanResult) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in scan:
        findings.extend(_module_findings(parsed.path, parsed.tree))
    return findings


def find_unconditional_stream_restores(roots: Sequence[Path], exclude: Iterable[str] = ()) -> list[Finding]:
    """Scan every ``*.py`` under ``roots`` and return each unsafe standard-stream swap.

    ``exclude`` holds path fragments to skip, matched against the POSIX form of each file's path. A missing root raises
    ``py_ci_shared._core.CorpusError``; files that cannot be parsed are not in this list, and
    :func:`assert_standard_streams_restored_only_if_still_ours` fails on them.
    """
    return _findings(_scan(roots, exclude))


def assert_standard_streams_restored_only_if_still_ours(
    roots: Sequence[Path],
    exclude: Iterable[str] = (),
    allow: Iterable[str] = (),
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
) -> None:
    """Raise ``AssertionError`` listing every unsafe swap of ``sys.stdout``/``sys.stderr`` not explicitly allowed.

    ``allow`` holds ``path:line`` strings for sites a reader has judged safe, such as an entry point that installs a
    stream once for the life of the process. Also fails when fewer than *min_files* files parsed (a typo'd root is not a
    clean tree) and, unless *allow_unparsed*, when any file could not be parsed.
    """
    allowed = {entry.strip() for entry in allow if entry.strip()}
    scan = _scan(roots, exclude)
    scan.min_files = min_files
    newline = chr(10)
    problems: list[str] = []
    try:
        scan.check_floor()
    except AssertionError as exc:
        problems.append(str(exc))
    if scan.unparsed and not allow_unparsed:
        problems.append(
            f"{len(scan.unparsed)} file(s) could not be parsed, so they were not checked:" + "".join(f"{newline}  {u.render()}" for u in scan.unparsed)
        )
    findings = [f for f in _findings(scan) if f"{f.path.as_posix()}:{f.lineno}" not in allowed]
    if findings:
        listing = (newline + "  ").join(str(f) for f in findings)
        problems.append(
            newline.join(
                [
                    f"{len(findings)} swap(s) of sys.stdout/sys.stderr that can restore a closed stream.",
                    "  Restore only while your own stream is still installed, or every later print raises",
                    "  'I/O operation on closed file':",
                    "      if sys.stderr is devnull:",
                    "          sys.stderr = old",
                    f"  {listing}",
                ]
            )
        )
    if problems:
        raise AssertionError(newline.join(problems))
