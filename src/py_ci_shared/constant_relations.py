"""Shared check: a declared relation between numeric constants and config defaults, possibly in different packages, holds.

Motivating case (upwork dashboard, 2026-10): the Cover Letter button waits ``dashboard.data._LETTER_TIMEOUT_S`` for a
best-of-N run, which needs up to ``[llm] call_timeout_sec`` for the slowest variant plus ``[evaluator]
judge_call_timeout_sec`` for the judge. Another session raised ``call_timeout_sec`` 300 -> 900 in
``live_config``; the dashboard kept killing billed letters at 900 s while they needed 1200 s. The relation lived in
one dashboard test that the author of the other package never ran. A relation that spans packages belongs in one
registry that every package's CI evaluates.

The registry is TOML::

    [names]                              # optional: references a dotted name cannot spell
    llm_live = "toml:realtime_applications/config.toml#llm.call_timeout_sec"

    [[relation]]
    check = "dashboard.data._LETTER_TIMEOUT_S >= live_config.models.AppConfig.llm.call_timeout_sec + llm_judge"
    reason = "a best-of-N letter waits for the slowest variant, then for the judge"

A reference is resolved as:

* a dotted name: the longest importable module prefix, then attribute by attribute. A step onto a pydantic model
  (v2 ``model_fields`` or v1 ``__fields__``) or a dataclass reads that field's DEFAULT (``default_factory`` called),
  so ``AppConfig.llm.call_timeout_sec`` is the compiled default however the model nests its sections;
* ``toml:<path relative to root>#<dotted.key>``: a value in a TOML file;
* ``toml-attr:<module.ATTR>#<dotted.key>``: a value in a TOML document held in a Python string constant.

The expression is parsed by ``ast`` and WALKED, never evaluated: only numbers, names, ``+ - * /``, unary minus,
parentheses and the comparisons ``< <= > >= == !=`` are accepted. Every reference must resolve to an int or float;
a reference that no longer resolves is a finding, so renaming a constant cannot silently drop its relation.

How this differs from its neighbours:

* ``prose_numeric_claims`` checks a number typed into PROSE against a count the repo computes; it is about
  documentation. This module checks code against code: two values that are both computed, in different places,
  and must stay ordered.
* ``config_drift_check`` reports when the SAME field (``line-length``...) differs between repos' ``pyproject.toml``,
  as an informational report. This module checks declared inequalities between DIFFERENT values, and fails.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import operator
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ._toml_compat import tomllib

_COMPARE: dict[type, tuple[str, Callable[[float, float], bool]]] = {
    ast.Lt: ("<", operator.lt),
    ast.LtE: ("<=", operator.le),
    ast.Gt: (">", operator.gt),
    ast.GtE: (">=", operator.ge),
    ast.Eq: ("==", operator.eq),
    ast.NotEq: ("!=", operator.ne),
}
_BINARY: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


class RelationError(Exception):
    """A relation that cannot be checked: bad grammar, an unresolvable reference, a non-numeric value."""


@dataclass(frozen=True)
class Relation:
    """One declared relation: *check* is ``<expr> <cmp> <expr> [<cmp> <expr> ...]``, *reason* says why it must hold."""

    check: str
    reason: str
    names: Mapping[str, str] = dataclasses.field(default_factory=dict)


def load_relations(registry: Path) -> list[Relation]:
    """The relations of a TOML registry (see the module docstring). A relation without ``check`` or ``reason`` raises."""
    doc = tomllib.loads(Path(registry).read_text(encoding="utf-8-sig"))
    names = doc.get("names", {})
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        raise RelationError(f"{registry}: [names] must map names to reference strings")
    out = []
    for i, entry in enumerate(doc.get("relation", []), 1):
        check, reason = entry.get("check"), entry.get("reason")
        if not isinstance(check, str) or not check.strip():
            raise RelationError(f"{registry}: relation #{i} has no `check` expression")
        if not isinstance(reason, str) or not reason.strip():
            raise RelationError(f"{registry}: relation #{i} ({check}) has no `reason`; say why it must hold")
        out.append(Relation(check, reason, names))
    return out


def _dotted(node: ast.expr) -> Optional[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _field_default(obj: Any, name: str) -> tuple[bool, Any]:
    """(True, default) when *obj* is a pydantic model or dataclass (class or instance) declaring field *name*."""
    cls = obj if isinstance(obj, type) else type(obj)
    fields = getattr(cls, "model_fields", None)
    if isinstance(fields, dict) and name in fields:
        if not isinstance(obj, type):
            return True, getattr(obj, name)
        info = fields[name]
        factory = getattr(info, "default_factory", None)
        default = getattr(info, "default", None)
        if factory is not None and type(default).__name__ == "PydanticUndefinedType":
            return True, factory()
        return True, default
    v1 = getattr(cls, "__fields__", None)
    if isinstance(v1, dict) and name in v1 and isinstance(obj, type):
        info = v1[name]
        factory = getattr(info, "default_factory", None)
        return True, factory() if factory is not None else getattr(info, "default", None)
    if dataclasses.is_dataclass(cls) and isinstance(obj, type):
        for f in dataclasses.fields(cls):
            if f.name == name:
                if f.default is not dataclasses.MISSING:
                    return True, f.default
                if f.default_factory is not dataclasses.MISSING:
                    return True, f.default_factory()
                raise RelationError(f"dataclass field {cls.__name__}.{name} has no default")
    return False, None


def _resolve_dotted(ref: str) -> Any:
    parts = ref.split(".")
    module = None
    rest: list[str] = []
    for cut in range(len(parts), 0, -1):
        target = ".".join(parts[:cut])
        try:
            module = importlib.import_module(target)
        except ModuleNotFoundError as exc:
            # Only "this prefix is not a module" moves on; a module that exists and fails on import is a real error.
            if exc.name and (target == exc.name or target.startswith(exc.name + ".")):
                continue
            raise RelationError(f"{ref}: importing {target} failed: {exc}") from exc
        except Exception as exc:
            raise RelationError(f"{ref}: importing {target} failed: {type(exc).__name__}: {exc}") from exc
        rest = parts[cut:]
        break
    if module is None:
        raise RelationError(f"{ref}: no importable module prefix")
    obj: Any = module
    walked = module.__name__
    for name in rest:
        found, value = _field_default(obj, name)
        if not found:
            if not hasattr(obj, name):
                raise RelationError(f"{ref}: {walked} has no attribute or field `{name}` (renamed or removed?)")
            value = getattr(obj, name)
        obj, walked = value, f"{walked}.{name}"
    return obj


def _toml_key(doc: Mapping[str, Any], key: str, ref: str) -> Any:
    node: Any = doc
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise RelationError(f"{ref}: key `{key}` is not in the TOML document")
        node = node[part]
    return node


def resolve_reference(ref: str, *, root: Optional[Path] = None) -> Any:
    """The value *ref* names; see the module docstring for the three forms. Raises `RelationError`."""
    for prefix in ("toml:", "toml-attr:"):
        if ref.startswith(prefix):
            target, sep, key = ref[len(prefix) :].partition("#")
            if not sep or not key:
                raise RelationError(f"{ref}: a TOML reference needs `#<dotted.key>`")
            if prefix == "toml:":
                path = Path(root or ".") / target
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except OSError as exc:
                    raise RelationError(f"{ref}: cannot read {path}: {exc}") from exc
            else:
                text = _resolve_dotted(target)
                if not isinstance(text, str):
                    raise RelationError(f"{ref}: {target} is a {type(text).__name__}, not a TOML string")
            try:
                doc = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                raise RelationError(f"{ref}: not valid TOML: {exc}") from exc
            return _toml_key(doc, key, ref)
    return _resolve_dotted(ref)


def _number(value: Any, ref: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RelationError(f"{ref} = {value!r} is a {type(value).__name__}, not a number")
    return value


class _Evaluator:
    def __init__(self, names: Mapping[str, str], root: Optional[Path]) -> None:
        self.names = names
        self.root = root
        self.values: dict[str, float] = {}

    def value(self, node: ast.expr) -> float:
        if isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = self.value(node.left), self.value(node.right)
            try:
                return _BINARY[type(node.op)](left, right)
            except ZeroDivisionError as exc:
                raise RelationError(f"division by zero in `{ast.unparse(node)}`") from exc
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = self.value(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        dotted = _dotted(node)
        if dotted is not None:
            if dotted not in self.values:
                ref = self.names.get(dotted, dotted)
                self.values[dotted] = _number(resolve_reference(ref, root=self.root), dotted)
            return self.values[dotted]
        raise RelationError(f"`{ast.unparse(node)}` is outside the grammar (numbers, names, + - * /, comparisons)")


def evaluate(relation: Relation, *, root: Optional[Path] = None) -> tuple[bool, str]:
    """(holds, rendering) for one relation; the rendering names every side's value and every reference's value."""
    try:
        tree = ast.parse(relation.check.strip(), mode="eval").body
    except SyntaxError as exc:
        raise RelationError(f"`{relation.check}` does not parse: {exc.msg}") from exc
    if not isinstance(tree, ast.Compare) or not all(type(op) in _COMPARE for op in tree.ops):
        raise RelationError(f"`{relation.check}` is not a comparison (< <= > >= == !=)")
    ev = _Evaluator(relation.names, root)
    sides = [tree.left, *tree.comparators]
    values = [ev.value(side) for side in sides]
    holds = True
    rendered = [f"{ast.unparse(sides[0])} = {values[0]:g}"]
    for i, op in enumerate(tree.ops):
        symbol, fn = _COMPARE[type(op)]
        holds &= fn(values[i], values[i + 1])
        rendered.append(f"{symbol} {ast.unparse(sides[i + 1])} = {values[i + 1]:g}")
    refs = ", ".join(f"{k}={v:g}" for k, v in ev.values.items())
    return holds, " ".join(rendered) + (f"  [{refs}]" if refs else "")


RelationSource = Union[Path, str, Iterable[Relation]]


def find_constant_relation_problems(
    relations: RelationSource,
    *,
    root: Optional[Path] = None,
    sys_path: Sequence[Union[str, Path]] = (),
    min_relations: int = 1,
) -> list[str]:
    """One line per relation that fails, cannot be resolved, or is outside the grammar.

    *relations* is a registry path or `Relation` objects. *root* anchors ``toml:`` paths (default: the registry's
    directory). *sys_path* entries are prepended for the imports (a monorepo's package directories). Fewer than
    *min_relations* relations is itself a finding: an emptied registry checks nothing.
    """
    if isinstance(relations, (str, Path)):
        registry = Path(relations)
        root = root if root is not None else registry.parent
        try:
            items = load_relations(registry)
        except (OSError, RelationError, tomllib.TOMLDecodeError) as exc:
            return [f"{registry}: unreadable relation registry: {exc}"]
    else:
        items = list(relations)
    problems: list[str] = []
    if len(items) < min_relations:
        problems.append(f"only {len(items)} relation(s) declared, expected at least {min_relations}: the registry checks nothing")
    added = [str(p) for p in sys_path if str(p) not in sys.path]
    sys.path[:0] = added
    try:
        for rel in items:
            try:
                holds, rendered = evaluate(rel, root=root)
            except RelationError as exc:
                problems.append(f"UNRESOLVED `{rel.check}` ({rel.reason}): {exc}")
                continue
            if not holds:
                problems.append(f"VIOLATED `{rel.check}`: {rendered}. Why it must hold: {rel.reason}")
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
    return problems


def assert_constant_relations(
    relations: RelationSource,
    *,
    root: Optional[Path] = None,
    sys_path: Sequence[Union[str, Path]] = (),
    min_relations: int = 1,
) -> None:
    """Fail on any declared relation that is violated or cannot be resolved; see `find_constant_relation_problems`."""
    import pytest

    problems = find_constant_relation_problems(relations, root=root, sys_path=sys_path, min_relations=min_relations)
    if problems:
        pytest.fail(f"{len(problems)} declared constant relation(s) do not hold:\n  " + "\n  ".join(problems))
