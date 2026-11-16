"""The statement nodes that are a ``try``: ``ast.Try`` and, on Python 3.11+, ``ast.TryStar`` (``except*``, PEP 654).

``ast.TryStar`` is a sibling class of ``ast.Try``, not a subclass, so an ``isinstance(node, ast.Try)`` test never matches an
exception-group handler. Use ``TRY_NODES`` in every ``isinstance`` test. The static type says ``ast.Try``: both classes
carry ``body``, ``handlers``, ``orelse`` and ``finalbody``, so narrowing to ``ast.Try`` is the right shape for the checkers.
"""

from __future__ import annotations

import ast
from typing import cast

_TRY_CLASSES: tuple[type, ...] = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)  # type: ignore[attr-defined]

TRY_NODES = cast("tuple[type[ast.Try], ...]", _TRY_CLASSES)
