"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Optional

from ._base import walk_tree, Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, walk_nodes

__all__ = ["scan_console_unicode"]

logger = logging.getLogger(__name__)

# One warning per unreadable file for the life of the process: the walk below revisits the same
# parent __init__.py for every module in a package, so an unguarded warning would repeat per file.
_UNREADABLE_INIT_WARNED: set = set()

# --- non-ASCII console/log output -----------------------------------------

_LOG_METHOD_NAMES = {"info", "warning", "error", "critical", "debug", "exception", "log"}


def _is_sys_stream(node: ast.AST, name: str) -> bool:
    """True for ``sys.<name>`` (``name`` is ``stdout`` or ``stderr``)."""
    return isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.value, ast.Name) and node.value.id == "sys"


def _console_stream(call: ast.Call) -> Optional[str]:
    """``"stdout"`` or ``"stderr"`` for a call that writes to the console, else ``None``.

    Covers ``print`` (stdout unless ``file=sys.stderr``), logging methods (the default handler writes
    to stderr), ``sys.stdout.write``/``sys.stderr.write``, ``click.echo`` (stderr with ``err=True``)
    and ``Console().print`` (rich, stdout).
    """
    func = call.func
    if isinstance(func, ast.Name) and func.id == "print":
        file_kw = next((kw.value for kw in call.keywords if kw.arg == "file"), None)
        return "stderr" if file_kw is not None and _is_sys_stream(file_kw, "stderr") else "stdout"
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr in _LOG_METHOD_NAMES:
        return "stderr"
    if func.attr == "write" and (_is_sys_stream(func.value, "stdout") or _is_sys_stream(func.value, "stderr")):
        return "stdout" if _is_sys_stream(func.value, "stdout") else "stderr"
    if func.attr == "echo" and isinstance(func.value, ast.Name) and func.value.id == "click":
        err = next((kw.value for kw in call.keywords if kw.arg == "err"), None)
        return "stderr" if isinstance(err, ast.Constant) and err.value is True else "stdout"
    if func.attr == "print" and isinstance(func.value, ast.Call) and isinstance(func.value.func, (ast.Name, ast.Attribute)):
        callee = func.value.func.id if isinstance(func.value.func, ast.Name) else func.value.func.attr
        return "stdout" if callee == "Console" else None
    return None


def _literal_text(node: ast.AST) -> list[str]:
    """Every string literal that reaches the console from one argument expression: a plain or f-string
    constant, both operands of ``+`` or ``%`` (``'-> ' + x``, ``'-> %s' % x``), and the receiver of
    ``.format`` (``'-> {}'.format(x)``). A name or call result contributes nothing: its text is not known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _literal_text(node.left) + _literal_text(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _literal_text(node.func.value)
    return []


def _str_arg_values(call: ast.Call) -> list[str]:
    """Every string literal that reaches the console from a call's arguments, in order: each positional
    argument, and the ``sep=`` and ``end=`` keywords that print writes between and after them.

    All of them, not just the first: `print("done", "->")` and `logger.info("x %s", "->")` reach
    the console exactly as `print("->")` does, and raise the same UnicodeEncodeError on cp1251.
    """
    out: list[str] = []
    for arg in call.args:
        out.extend(_literal_text(arg))
    for kw in call.keywords:
        if kw.arg in ("sep", "end"):
            out.extend(_literal_text(kw.value))
    return out


def _has_non_ascii(s: str) -> bool:
    """True if ``s`` contains any character outside the printable-ASCII range."""
    return any(ord(c) > 127 for c in s)


def _live_reconfigured_streams(tree: ast.AST) -> frozenset[str]:
    """The streams (``"stdout"``/``"stderr"``) that the module really reconfigures to an encoding.

    A structural test: a ``sys.<stream>.reconfigure(...)`` call with an ``encoding=`` keyword, found in the
    AST, so a comment, a string literal or a ``reconfigure(line_buffering=...)`` never counts. A call inside
    a ``def`` whose name nothing in the module references (never called, never passed on) does not run,
    so it does not count either. Calls at module level, under ``if __name__ == "__main__":``, or inside a
    called ``main()`` do count: they run before the print/log calls that follow them.
    """
    referenced = {n.id for n in walk_nodes(tree) if isinstance(n, ast.Name)} | {n.attr for n in walk_nodes(tree) if isinstance(n, ast.Attribute)}
    dead: set[int] = set()
    for node in walk_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in referenced:
            dead.update(id(inner) for inner in walk_nodes(node))
    streams: set[str] = set()
    for node in walk_nodes(tree):
        if id(node) in dead or not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "reconfigure" or not any(kw.arg == "encoding" for kw in node.keywords):
            continue
        for stream in ("stdout", "stderr"):
            if _is_sys_stream(node.func.value, stream):
                streams.add(stream)
    return frozenset(streams)


def _has_stdio_utf8_reconfigure(source: str) -> bool:
    """True if ``source`` really reconfigures stdout or stderr to an encoding (see
    :func:`_live_reconfigured_streams`). A file that already forces UTF-8 stdio at its own entry point
    cannot hit the ``UnicodeEncodeError`` the scanner warns about, so flagging its literals is a false
    positive. Unparsable source reconfigures nothing."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return bool(_live_reconfigured_streams(tree))


def _read_init_source(init_file: Path) -> str:
    """The file's text, or "" when it cannot be read - warned about once per path per process.

    An unreadable ``__init__.py`` is indistinguishable from one that does not reconfigure stdio,
    which silently disables this check for the whole package below it. The caller revisits the same
    parent for every module in a package, hence the once-per-path guard.
    """
    try:
        return init_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if init_file not in _UNREADABLE_INIT_WARNED:
            _UNREADABLE_INIT_WARNED.add(init_file)
            logger.warning("Cannot read %s (%s); treating the package as not reconfiguring stdio.", init_file, e)
        return ""


def _ancestor_package_has_stdio_utf8_reconfigure(py: Path, root: Path, memo: Optional[dict[Path, bool]] = None) -> bool:
    """True if any ``__init__.py`` from ``py``'s own package up through ``root`` reconfigures
    stdio to UTF-8. A package ``__init__.py`` runs on the FIRST ``import <package>`` regardless
    of entry point (CLI, an API server under uvicorn, a test runner, a script that merely
    imports the package) -- so a reconfigure call there protects every module beneath it, the
    same way it protects a script's own top-level call, just triggered on import instead of on
    ``__main__``. Checked once per package directory (not every ancestor __init__.py needs its
    own separate call; the outermost one that runs first is what matters), stopping at the
    first ``__init__.py`` found reconfiguring, or at ``root`` if none do."""
    root = root.resolve()
    chain: list[Path] = []
    current = py.parent
    result = False
    while True:
        if memo is not None and current in memo:
            result = memo[current]
            break
        chain.append(current)
        init_file = current / "__init__.py"
        if init_file.is_file() and _has_stdio_utf8_reconfigure(_read_init_source(init_file)):
            result = True
            break
        if current.resolve() == root or current.parent == current:
            break
        current = current.parent
    if memo is not None:
        # every directory walked so far reaches the same first reconfiguring __init__ (or the same stop), so one answer covers the chain
        for directory in chain:
            memo[directory] = result
    return result


def scan_console_unicode(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find ``print(...)`` / ``logger.X(...)`` calls whose first string-literal argument contains a
    non-ASCII character.

    On Windows, default stdout/stderr encoding is ``cp1251``/``cp1252``; printing a fancy Unicode
    arrow, checkmark, or any non-Latin character crashes with ``UnicodeEncodeError`` -- silently
    fine on Linux/macOS dev machines, guaranteed broken on Windows. Skips files that already call
    ``sys.stdout.reconfigure(encoding=...)`` / ``sys.stderr.reconfigure(encoding=...)`` themselves
    (see :func:`_has_stdio_utf8_reconfigure`) -- the established fix for this exact failure mode,
    confirmed as this codebase's own convention across dozens of scripts -- OR whose enclosing
    PACKAGE already does so in an ``__init__.py`` up the directory chain (see
    :func:`_ancestor_package_has_stdio_utf8_reconfigure`): a library module imported as part of a
    package is exactly as protected as a script with its own top-level call, just via the
    package's own import-time guard instead of a per-file one.
    """
    findings: list[Finding] = []
    memo: dict[Path, bool] = {}
    for py in _iter_py_files(root, exclude_dirs):
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        if _ancestor_package_has_stdio_utf8_reconfigure(py, root, memo):
            continue
        own_streams = _live_reconfigured_streams(tree)
        rel = py.relative_to(root).as_posix()
        for node in walk_tree(tree):
            if not isinstance(node, ast.Call):
                continue
            stream = _console_stream(node)
            if stream is None or stream in own_streams:
                continue  # not a console write, or this module already forces UTF-8 on the stream it writes to
            if not any(_has_non_ascii(literal) for literal in _str_arg_values(node)):
                continue
            findings.append(
                Finding(
                    check="console_unicode",
                    severity="P2",
                    file=rel,
                    line=node.lineno,
                    snippet=_line_text(src_lines, node.lineno),
                    detail=(
                        "print()/logger.*() call emits a non-ASCII string literal; crashes with "
                        "UnicodeEncodeError on Windows cp1251/cp1252 stdout. Replace fancy characters "
                        "(arrows, checkmarks, em-dash) with ASCII equivalents."
                    ),
                )
            )
    return findings
