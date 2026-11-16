"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs.

Shared-gate counterpart: py_ci_shared.swallowed_exceptions reports a silent handler on a broad or I/O
catch in production code only. This scanner also covers tests and scripts and every bare/BaseException
handler that does not re-raise, whatever its body, so it stays.
"""

from __future__ import annotations

import ast
from .._core.ast_try import TRY_NODES
from pathlib import Path

from ._base import walk_tree, Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, is_line_exempt

__all__ = ["scan_bare_except"]

# --- bare `except:` / `except BaseException:` ----------------------------


def _names_base_exception(node: ast.expr) -> bool:
    """True for ``BaseException`` or ``builtins.BaseException``."""
    if isinstance(node, ast.Name):
        return node.id == "BaseException"
    return isinstance(node, ast.Attribute) and node.attr == "BaseException" and isinstance(node.value, ast.Name) and node.value.id == "builtins"


def _is_bare_except(handler: ast.ExceptHandler) -> bool:
    """``handler.type`` is None for bare ``except:``; or ``BaseException`` (alone, dotted, or in a tuple) for the
    equivalent dangerous form.

    EXCEPTION: a handler whose LAST top-level statement is a bare ``raise`` re-raises on every path, so it is
    legitimate -- phase-tracking context managers, request-scope cleanup, and similar patterns that audit EVERY
    exit path. A ``raise`` under an ``if`` or inside a nested ``def`` runs on one path only and does not exempt.
    """
    if handler.type is not None:
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if not any(_names_base_exception(t) for t in types):
            return False
    last = handler.body[-1] if handler.body else None
    if isinstance(last, ast.Raise) and last.exc is None:
        return False
    return True


def scan_bare_except(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find ``except:`` (bare) and ``except BaseException:`` clauses.

    Bare ``except:`` swallows EVERYTHING -- ``KeyboardInterrupt`` (the user can't Ctrl-C),
    ``SystemExit`` (forks misbehave), and ``MemoryError`` (debugger gets confused). It also masks
    bugs in the try-block by catching them as if they were expected. ``except Exception:`` is the
    safe equivalent in nearly every case. A handler that immediately re-raises (bare ``raise``) is
    not flagged -- auditing every exit path via ``except BaseException: ... raise`` is legitimate.

    Distinct from ``scan_broad_except_swallows``, which targets ``except Exception:`` handlers
    that swallow silently -- this scanner targets the strictly WIDER (and never intentional)
    ``except:``/``except BaseException:`` shape.
    """
    findings: list[Finding] = []
    for py in _iter_py_files(root, exclude_dirs):
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        for node in walk_tree(tree):
            if not isinstance(node, TRY_NODES):
                continue
            for handler in node.handlers:
                if not _is_bare_except(handler) or is_line_exempt(src_lines, handler.lineno, "bare_except"):
                    continue
                findings.append(
                    Finding(
                        check="bare_except",
                        severity="P1",
                        file=rel,
                        line=handler.lineno,
                        snippet=_line_text(src_lines, handler.lineno),
                        detail=(
                            "bare `except:` (or `except BaseException:`) swallows KeyboardInterrupt/"
                            "SystemExit and masks real bugs. Replace with `except Exception:` or a "
                            "narrower specific exception type."
                        ),
                    )
                )
    return findings
