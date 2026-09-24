"""Log and error messages that tell the reader to do something: each needs a test that does it and sees the promised effect.

A message such as "reduce cv_folds or supply more groups" or "pass it via config.x" is an instruction the operator will
follow. When the parameter it names was renamed, is not reachable from the public API, or does not change the behaviour
it promises, the advice sends the reader in circles, and nothing catches it: the message is only read when something has
already gone wrong. One audit found two such messages, one naming a knob with no working path.

The scan finds every string literal passed to a logging call, ``warnings.warn``, ``print`` or an exception constructor
whose text matches :data:`ADVICE_RE`, keyed ``<path>::<enclosing function>#<n>`` so a key survives line moves. A
repository keeps a table from each key to the test that performs the advice (or a reason it needs none), and a meta test
fails on an unregistered or a stale key.

Usage from a repository's meta tests::

    from py_ci_shared.printed_advice import find_printed_advice

    def test_every_printed_advice_has_a_test():
        found = {a.key for a in find_printed_advice(SOURCE_FILES, REPO_ROOT)}
        assert found == set(PRINTED_ADVICE_TESTS)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

__all__ = ["ADVICE_RE", "PrintedAdvice", "find_printed_advice"]

ADVICE_RE = re.compile(
    r"pass (?:it|them) via|\bset \w+\s*=|\b(?:increase|decrease|raise|lower|reduce|disable|enable) (?:`|')?\w+(?:`|')? (?:to|or|and|if|so|for|\()"
    r"|\bsupply (?:a |an |more )?\w+|\bpass \w+=|\buse \w+=",
    re.IGNORECASE,
)
_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})


class PrintedAdvice:
    """One advising message: where it is, its stable key, and the text that matched."""

    __slots__ = ("key", "lineno", "path", "phrase", "scope")

    def __init__(self, path: str, scope: str, lineno: int, phrase: str, key: str) -> None:
        self.path = path
        self.scope = scope
        self.lineno = lineno
        self.phrase = phrase
        self.key = key

    def __repr__(self) -> str:
        return f"{self.key} (line {self.lineno}) advises '{self.phrase}'"


def _message_call(node: ast.Call) -> bool:
    """A call whose string arguments reach a reader: logging, ``warnings.warn``, ``print`` or an ``...Error``/``...Warning``."""
    name = getattr(node.func, "attr", getattr(node.func, "id", "")) or ""
    return name in _LOG_METHODS or name == "print" or name.endswith(("Error", "Warning", "Exception"))


def _text_of(arg: ast.AST) -> str:
    """The literal text of a string argument: constants, f-string constant parts and implicit or ``+`` concatenation."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in arg.values)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        return _text_of(arg.left) + _text_of(arg.right)
    return ""


def _scopes(tree: ast.Module) -> dict[int, str]:
    """``id(call node) -> qualified name of the innermost enclosing function or class`` (``<module>`` at top level)."""
    out: dict[int, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, child.name if scope == "<module>" else f"{scope}.{child.name}")
            else:
                if isinstance(child, ast.Call):
                    out[id(child)] = scope
                visit(child, scope)

    visit(tree, "<module>")
    return out


def find_printed_advice(files: Iterable[Path], repo_root: Path) -> list[PrintedAdvice]:
    """Every message literal that advises an action, in file order, keyed ``<path>::<scope>#<n>``."""
    out: list[PrintedAdvice] = []
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
        scopes = _scopes(tree)
        seen: dict[str, int] = {}
        calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call) and _message_call(n)), key=lambda n: (n.lineno, n.col_offset))
        for call in calls:
            text = " ".join(_text_of(a) for a in [*call.args, *(k.value for k in call.keywords)])
            m = ADVICE_RE.search(text)
            if not m:
                continue
            scope = scopes.get(id(call), "<module>")
            seen[scope] = seen.get(scope, 0) + 1
            out.append(PrintedAdvice(rel, scope, call.lineno, m.group(0), f"{rel}::{scope}#{seen[scope]}"))
    return out
