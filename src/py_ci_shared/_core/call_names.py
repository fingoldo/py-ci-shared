"""The one copy of "what is this call's callee called": the bare name of a ``Name`` and the final attribute of an ``Attribute``.

Gates used to carry a private ``_call_name`` each; a fix to the resolution (a new node shape) then landed in one copy only. The variants
differ only in the argument (a call or its ``func``) and in the miss value (``None`` or ``""``), so there is one function per shape.
"""

from __future__ import annotations

import ast
from typing import Optional

__all__ = ["call_name", "call_name_or_none", "func_name", "func_name_or_none"]


def func_name_or_none(func: Optional[ast.AST]) -> Optional[str]:
    """``Name.id`` or ``Attribute.attr`` of a callee expression, else None (a subscript, a call result, a lambda...)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def func_name(func: Optional[ast.AST]) -> str:
    """Like :func:`func_name_or_none`, with ``""`` for a miss."""
    return func_name_or_none(func) or ""


def call_name_or_none(node: ast.AST) -> Optional[str]:
    """The callee name of a ``Call`` (any other node has no ``func`` and gives None)."""
    return func_name_or_none(getattr(node, "func", None))


def call_name(node: ast.AST) -> str:
    """Like :func:`call_name_or_none`, with ``""`` for a miss."""
    return func_name(getattr(node, "func", None))
