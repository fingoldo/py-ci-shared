"""One rendering of an AST node for finding messages, identical on every supported interpreter.

A finding's baseline key is ``rule::path::message``, and many messages quote the offending node. ``ast.unparse`` is not
stable across versions: before 3.11 it wrote ``lambda : 1`` (a space before the colon when there are no arguments) and
parenthesised a tuple target (``for (a, b) in c``, ``[x for (a, b) in c]``, ``(a, b) = c``), which 3.11+ writes as
``lambda: 1``, ``for a, b in c``, ``a, b = c``. A baseline refreshed on one leg then went red on another with no code
change. :func:`unparse_stable` renders the 3.11+ shape everywhere; every gate that puts a node in a message uses it
(``tests/test_unparse_stable.py`` bans the bare call in ``src``).
"""

from __future__ import annotations

import ast
import sys
from typing import Any

__all__ = ["unparse_stable"]

if sys.version_info >= (3, 11):

    def unparse_stable(node: ast.AST) -> str:
        """``ast.unparse(node)``; on 3.11+ that is already the canonical shape. ``ast.dump`` when ``ast.unparse`` is absent."""
        return ast.unparse(node) if hasattr(ast, "unparse") else ast.dump(node)

else:
    _Unparser: Any = ast._Unparser  # type: ignore[attr-defined]
    _Precedence: Any = ast._Precedence  # type: ignore[attr-defined]

    class _StableUnparser(_Unparser):  # type: ignore[misc]
        """3.9/3.10 unparser made to write what 3.11+ writes for a lambda without arguments and for tuple targets."""

        def _bare_target(self, target: ast.AST) -> None:
            """A non-empty tuple target without its parentheses (``a, b`` / ``a,``); any other target as usual."""
            if isinstance(target, ast.Tuple) and target.elts:
                self.items_view(self.traverse, target.elts)
            else:
                self.traverse(target)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            with self.require_parens(_Precedence.TEST, node):
                self.write("lambda")
                state: Any = self
                saved, state._source = state._source, []
                self.traverse(node.args)
                written, state._source = state._source, saved
                if written:
                    self.write(" " + "".join(written))
                self.write(": ")
                self.set_precedence(_Precedence.TEST, node.body)
                self.traverse(node.body)

        def _for_helper(self, fill: str, node: Any) -> None:
            self.fill(fill)
            self._bare_target(node.target)
            self.write(" in ")
            self.traverse(node.iter)
            with self.block(extra=self.get_type_comment(node)):
                self.traverse(node.body)
            if node.orelse:
                self.fill("else")
                with self.block():
                    self.traverse(node.orelse)

        def visit_comprehension(self, node: ast.comprehension) -> None:
            self.write(" async for " if node.is_async else " for ")
            self._bare_target(node.target)
            self.write(" in ")
            self.set_precedence(_Precedence.TEST.next(), node.iter, *node.ifs)
            self.traverse(node.iter)
            for if_clause in node.ifs:
                self.write(" if ")
                self.traverse(if_clause)

        def visit_Assign(self, node: ast.Assign) -> None:
            self.fill()
            for target in node.targets:
                self._bare_target(target)
                self.write(" = ")
            self.traverse(node.value)
            type_comment = self.get_type_comment(node)
            if type_comment:
                self.write(type_comment)

    def unparse_stable(node: ast.AST) -> str:
        """``ast.unparse(node)`` rendered the way 3.11+ does (see the module docstring); ``ast.dump`` when it is absent."""
        if not hasattr(ast, "unparse"):
            return ast.dump(node)
        return str(_StableUnparser().visit(node))
