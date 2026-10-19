"""No LLM call may carry a numeric output or reasoning cap that someone chose by eye.

The model's own catalogue ceiling bounds the answer; a number of ours cuts a paid answer off mid-way. A repo adopts this
gate by writing its own ratchet test against three helpers, so a new cap fails by name and the known ones may only shrink:

* :func:`numeric_output_caps` finds, under the given roots, every name containing one of *name_hints* assigned a positive
  int literal (``CAP_TOKENS = 4096``, ``self_cap: int = 8192``), and every parameter named one of *default_hints* whose
  default is a positive int literal (``def call(prompt, max_tokens=4096)``). ``0`` (the unset marker of ``max_tokens``)
  and ``None`` are never caps. A file that does not parse raises ``SourceParseError``: it is never skipped.
* :func:`assert_no_new_output_caps` fails on a cap found but not on the known list.
* :func:`assert_known_caps_only_shrink` fails on a known cap that is no longer found, so the list keeps shrinking.
* :func:`assert_each_known_cap_states_its_reason` fails on a known cap whose reason is blank.

Usage from a repo's own test::

    from py_ci_shared.llm_output_caps import KnownCap, assert_each_known_cap_states_its_reason, assert_known_caps_only_shrink, assert_no_new_output_caps, numeric_output_caps

    ROOTS = (REPO / "src" / "pkg" / "llm",)
    KNOWN_CAPS: KnownCap = {}
    NAME_HINTS = ("CAP_TOKENS", "MAX_OUTPUT_TOKENS", "max_completion_tokens")
    DEFAULT_HINTS = ("max_tokens", "max_completion_tokens", "max_output_tokens")

    def _found():
        return numeric_output_caps(ROOTS, repo_root=REPO, name_hints=NAME_HINTS, default_hints=DEFAULT_HINTS)

    def test_no_new_output_cap():
        assert_no_new_output_caps(_found(), KNOWN_CAPS)

    def test_known_caps_only_shrink():
        assert_known_caps_only_shrink(_found(), KNOWN_CAPS)

    def test_each_known_cap_states_its_reason():
        assert_each_known_cap_states_its_reason(KNOWN_CAPS)

Opt-in: nothing runs this gate on its own. A repo that does not call the helpers is not checked.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Optional, Union

from ._core import walk as shared_walk
from ._core.source import parse_file

__all__ = [
    "Cap",
    "KnownCap",
    "numeric_output_caps",
    "assert_no_new_output_caps",
    "assert_known_caps_only_shrink",
    "assert_each_known_cap_states_its_reason",
]

#: ``(repo-relative posix path, name, value)`` of one cap.
Cap = tuple[str, str, int]
#: The ratchet list: ``{cap: reason}``. Each reason says why the cap exists and that it must go. Shrink only.
KnownCap = dict[Cap, str]

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _positive_int(node: Optional[ast.expr]) -> Optional[int]:
    """The value of a positive int literal (``bool`` excluded), else ``None``: ``0``, ``None`` and names are not caps."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool) and node.value > 0:
        return node.value
    return None


def _assigned_names(node: ast.AST) -> Iterator[tuple[str, Optional[ast.expr]]]:
    """``(name, value)`` for each plain-name target of an assignment or annotated assignment."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                yield target.id, node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        yield node.target.id, node.value


def _parameter_defaults(node: ast.AST) -> Iterator[tuple[str, ast.expr]]:
    """``(name, default)`` for each parameter of a function that has a default."""
    if not isinstance(node, _FUNCTION_NODES):
        return
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    for arg, default in zip(positional[len(positional) - len(args.defaults) :], args.defaults):
        yield arg.arg, default
    for kwarg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        if kw_default is not None:
            yield kwarg.arg, kw_default


def _caps_in(tree: ast.Module, rel: str, name_hints: Sequence[str], default_hints: Sequence[str]) -> Iterator[Cap]:
    for node in shared_walk(tree):
        for name, value in _assigned_names(node):
            literal = _positive_int(value)
            if literal is not None and any(hint in name for hint in name_hints):
                yield rel, name, literal
        for name, default in _parameter_defaults(node):
            literal = _positive_int(default)
            if literal is not None and name in default_hints:
                yield rel, name, literal


def numeric_output_caps(
    roots: Iterable[Union[str, Path]],
    *,
    repo_root: Union[str, Path],
    name_hints: Sequence[str],
    default_hints: Sequence[str],
) -> set[Cap]:
    """Every numeric output cap under *roots*, keyed by its path relative to *repo_root*.

    *name_hints* match a substring of an assigned name (``CAP_TOKENS`` matches ``PER_ITEM_CAP_TOKENS``); *default_hints*
    must equal a parameter name. Only ``.py`` files are read, and ``__pycache__`` is skipped.
    """
    repo = Path(repo_root)
    found: set[Cap] = set()
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(repo).as_posix()
            tree = parse_file(path)
            found.update(_caps_in(tree, rel, tuple(name_hints), tuple(default_hints)))
    return found


def assert_no_new_output_caps(found: Iterable[Cap], known: Mapping[Cap, str]) -> None:
    """Raise ``AssertionError`` naming every cap in *found* that *known* does not list."""
    new = sorted(set(found) - set(known))
    if new:
        raise AssertionError(f"new numeric output cap(s) on an LLM call (use the model's ceiling, never a number of ours): {new}")


def assert_known_caps_only_shrink(found: Iterable[Cap], known: Mapping[Cap, str]) -> None:
    """Raise ``AssertionError`` naming every cap in *known* that *found* no longer contains."""
    gone = sorted(set(known) - set(found))
    if gone:
        raise AssertionError(f"these caps are gone: delete their KNOWN_CAPS entries so the list keeps shrinking: {gone}")


def assert_each_known_cap_states_its_reason(known: Mapping[Cap, str]) -> None:
    """Raise ``AssertionError`` naming every known cap whose reason is empty or only whitespace."""
    blank = sorted(cap for cap, reason in known.items() if not reason.strip())
    if blank:
        raise AssertionError(f"KNOWN_CAPS entries with no reason (every cap states why it exists): {blank}")
