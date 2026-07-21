"""Shared checks for the "``cfg().get(section, key, default, type_)`` call-site
vs Pydantic schema" consistency pattern.

Generalizes a check independently built TWICE in this ecosystem (realtime_applications's
``test_config_field_consumption.py`` and production_scrapers's ``test_config_default_sync.py``),
each catching real, confirmed bugs: a call site reading a ``(section, key)`` pair that
doesn't exist in the schema (silently unreadable -- the config loader strips unknown
keys, so the call always falls through to its hardcoded default), a schema field with
zero reader anywhere (a decorative knob with no wiring), and two call sites reading the
SAME ``(section, key)`` with a different hardcoded default/type (a silent behavior
difference between two call sites claiming to read "the same" setting).

Both existing implementations use a `LiveConfig`-style accessor: ``cfg().get(section,
key, default, type_)`` (or a locally-bound name, ``_c = cfg(); _c.get(...)``), backed by
a 2-level Pydantic schema (a top-level model whose fields are themselves sub-models, one
per config section). This module factors out the AST call-site scanning and constant
resolution; each consuming repo supplies its own schema class and, where it has one, its
own whitelist of fields consumed a different way (not through a literal
``cfg().get(...)`` call the AST heuristic can see).

Deliberately dependency-light: ``pytest`` is imported lazily inside the ``assert_*``
functions, matching this package's other modules.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Optional

DEFAULT_CFG_FUNCTION_NAMES: frozenset[str] = frozenset({"cfg", "_cfg"})


class CfgGetCall(NamedTuple):
    file: str
    line: int
    section: str
    key: str
    default_node: Optional[ast.expr]
    type_src: Optional[str]


class _Unresolved:
    """Sentinel: a default expression that isn't a literal and doesn't resolve to a
    known constant -- compared by raw source text as a conservative fallback (never
    silently treated as equal to anything else unresolved)."""

    def __init__(self, src: str):
        self.src = src

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Unresolved) and self.src == other.src

    def __hash__(self) -> int:
        return hash(("_Unresolved", self.src))

    def __repr__(self) -> str:
        return self.src


def _is_cfg_call(node: ast.expr, cfg_function_names: frozenset[str]) -> bool:
    """True for a bare, no-argument call to something named in ``cfg_function_names``
    (optionally attribute-qualified, e.g. ``_pkg.cfg()``)."""
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in cfg_function_names
    if isinstance(node.func, ast.Attribute):
        return node.func.attr in cfg_function_names
    return False


def _arg_node(call: ast.Call, position: int, keyword: str) -> Optional[ast.expr]:
    if len(call.args) > position:
        return call.args[position]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _find_cfg_get_calls_in_file(path: Path, root: Path, cfg_function_names: frozenset[str]) -> list[CfgGetCall]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    # File-scoped "bind cfg() to a local name first" idiom: `_c = cfg(); _c.get(...)`.
    cfg_bound_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_cfg_call(node.value, cfg_function_names):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    cfg_bound_names.add(target.id)

    found: list[CfgGetCall] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        base = node.func.value
        is_direct_chain = _is_cfg_call(base, cfg_function_names)
        is_bound_name = isinstance(base, ast.Name) and base.id in cfg_bound_names
        if not (is_direct_chain or is_bound_name):
            continue
        if len(node.args) < 2:
            continue
        section_node, key_node = node.args[0], node.args[1]
        if not (isinstance(section_node, ast.Constant) and isinstance(section_node.value, str)):
            continue
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        type_node = _arg_node(node, 3, "type_")
        found.append(CfgGetCall(
            file=path.relative_to(root).as_posix(),
            line=node.lineno,
            section=section_node.value,
            key=key_node.value,
            default_node=_arg_node(node, 2, "default"),
            type_src=ast.unparse(type_node) if type_node is not None else None,
        ))
    return found


def find_cfg_get_calls(
    root: Path,
    files: Iterable[Path],
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
) -> list[CfgGetCall]:
    """Every ``cfg().get(section, key, ...)`` (or bound-name equivalent) call site
    across ``files``, with literal string ``section``/``key`` arguments."""
    calls: list[CfgGetCall] = []
    for path in files:
        calls.extend(_find_cfg_get_calls_in_file(path, root, cfg_function_names))
    return calls


def module_constants(files: Iterable[Path]) -> dict[str, object]:
    """name -> value for every simple top-level ``NAME = <literal>`` assignment
    across ``files`` -- lets a bare literal default and a named-constant default that
    resolves to the same value be recognized as agreeing, not flagged as a spurious
    divergence. Repo-wide and flat (not namespace-qualified): if two different files
    define the same constant name with different values, the later one parsed wins --
    a known, accepted imprecision (same tradeoff the originating implementation made),
    since a real repo-wide name COLLISION on a constant used as a config default would
    itself be surprising and worth surfacing separately.
    """
    consts: dict[str, object] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    consts[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    continue
    return consts


def resolve_default(node: Optional[ast.expr], constants: dict[str, object]) -> Any:
    """Resolve a default-expression AST node to a value: a literal via
    ``ast.literal_eval``, else a bare ``Name``/dotted ``Attribute`` chain looked up
    (by its final identifier) against ``constants``, else an ``_Unresolved`` sentinel
    wrapping the source text (compared by that text, never silently treated as equal
    to a different unresolved expression)."""
    if node is None:
        return None
    src = ast.unparse(node)
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass
    name = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    if name is not None and name in constants:
        return constants[name]
    return _Unresolved(src)


def to_hashable(value: Any) -> Any:
    """Recursively convert list/dict/set into a hashable, order-preserving (for
    lists) equivalent so resolved default values can live in a set/dict key."""
    if isinstance(value, list):
        return ("__list__", tuple(to_hashable(v) for v in value))
    if isinstance(value, dict):
        return ("__dict__", tuple(sorted((k, to_hashable(v)) for k, v in value.items())))
    if isinstance(value, set):
        return ("__set__", tuple(sorted(to_hashable(v) for v in value)))
    return value


def schema_section_field_map(schema_cls: type) -> dict[str, set[str]]:
    """Top-level section name -> its sub-model's real field names, for a 2-level
    Pydantic schema (``schema_cls.model_fields`` maps each section to a field whose
    ``.annotation`` is itself a Pydantic model with its own ``model_fields``)."""
    out: dict[str, set[str]] = {}
    for section, field in schema_cls.model_fields.items():
        sub_model = field.annotation
        out[section] = set(sub_model.model_fields.keys())
    return out


def schema_section_field_defaults(schema_cls: type) -> dict[tuple[str, str], Any]:
    """(section, key) -> that field's own Pydantic default value, for the same
    2-level schema shape as ``schema_section_field_map``."""
    out: dict[tuple[str, str], Any] = {}
    for section, field in schema_cls.model_fields.items():
        sub_model = field.annotation
        for key, sub_field in sub_model.model_fields.items():
            out[(section, key)] = sub_field.default
    return out


def assert_every_cfg_get_call_resolves_to_a_schema_field(
    root: Path,
    files: Iterable[Path],
    schema_cls: type,
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
) -> None:
    """Fail if any ``cfg().get(section, key, ...)`` call site reads a ``(section,
    key)`` pair that doesn't exist in ``schema_cls``'s schema -- silently unreadable
    forever, since an unknown key is stripped on load and the call always falls
    through to its own hardcoded default."""
    import pytest

    schema = schema_section_field_map(schema_cls)
    bad = []
    for call in find_cfg_get_calls(root, files, cfg_function_names):
        if call.section not in schema:
            bad.append(f"{call.file}:{call.line} -- unknown section {call.section!r}")
        elif call.key not in schema[call.section]:
            bad.append(f"{call.file}:{call.line} -- [{call.section}] has no field {call.key!r}")
    if bad:
        pytest.fail("cfg().get(...) call site(s) reading a config key that doesn't exist in the schema:\n  " + "\n  ".join(bad))


def assert_every_schema_field_has_a_reader(
    root: Path,
    files: Iterable[Path],
    schema_cls: type,
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
    known_indirect_readers: Optional[dict[tuple[str, str], str]] = None,
    known_unwired_gaps: Optional[dict[tuple[str, str], str]] = None,
) -> None:
    """Fail if any schema field has zero ``cfg().get(...)`` reader anywhere in
    ``files``, unless it's listed in ``known_indirect_readers`` (genuinely consumed a
    different way -- e.g. via a ``.snapshot()`` attribute access this AST heuristic
    can't see) or ``known_unwired_gaps`` (a real, tracked, not-yet-fixed gap -- reported
    to stderr, not failed). An unreferenced field is an undocumented decorative config
    knob an operator could set with zero effect."""
    import pytest

    known_indirect_readers = known_indirect_readers or {}
    known_unwired_gaps = known_unwired_gaps or {}
    schema = schema_section_field_map(schema_cls)
    read = {(c.section, c.key) for c in find_cfg_get_calls(root, files, cfg_function_names)}
    unread = []
    tracked_gaps_hit = []
    for section, keys in schema.items():
        for key in keys:
            pair = (section, key)
            if pair in read or pair in known_indirect_readers:
                continue
            if pair in known_unwired_gaps:
                tracked_gaps_hit.append(f"[{section}] {key}: {known_unwired_gaps[pair]}")
                continue
            unread.append(f"[{section}] {key}")
    if tracked_gaps_hit:
        import sys

        sys.stderr.write("\n[assert_every_schema_field_has_a_reader] known tracked gap(s), not failing but not fixed either:\n  " + "\n  ".join(tracked_gaps_hit) + "\n")
    if unread:
        pytest.fail(
            "Schema field(s) with zero cfg().get(...) reader anywhere in production code (decorative "
            "knob -- wire it in, or pass it to known_indirect_readers/known_unwired_gaps with a reason "
            "if it's genuinely consumed a different way or a tracked gap):\n  " + "\n  ".join(unread)
        )


def assert_no_divergent_cfg_get_call_site_defaults(
    root: Path,
    files: Iterable[Path],
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
) -> None:
    """Fail if two call sites reading the SAME ``(section, key)`` pass a different
    default/``type_`` -- two call sites silently disagreeing about what "the config
    value" resolves to when absent. Compared by RESOLVED value (literal-eval'd, or
    looked up against repo-wide module constants), not raw source text, so a bare
    ``24`` and a named constant that also equals ``24`` are correctly treated as
    agreeing."""
    import pytest

    calls = find_cfg_get_calls(root, files, cfg_function_names)
    by_pair: dict[tuple[str, str], list[CfgGetCall]] = {}
    for call in calls:
        by_pair.setdefault((call.section, call.key), []).append(call)

    constants = module_constants(files)
    bad = []
    for (section, key), pair_calls in by_pair.items():
        if len(pair_calls) < 2:
            continue
        resolved_defaults = {to_hashable(resolve_default(c.default_node, constants)) for c in pair_calls}
        types = {c.type_src for c in pair_calls}
        if len(resolved_defaults) > 1 or len(types) > 1:
            sites = ", ".join(f"{c.file}:{c.line}(default={ast.unparse(c.default_node) if c.default_node else None!r}, type_={c.type_src!r})" for c in pair_calls)
            bad.append(f"[{section}] {key}: divergent default/type_ across call sites: {sites}")
    if bad:
        pytest.fail("cfg().get(...) call sites reading the same (section, key) with divergent default/type_:\n  " + "\n  ".join(bad))


def assert_call_site_defaults_match_schema_defaults(
    root: Path,
    files: Iterable[Path],
    schema_cls: type,
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
    min_checked: int = 1,
) -> None:
    """Fail if a call site's own resolved default disagrees with the schema field's
    Pydantic default. Only call sites whose default expression actually resolves to a
    concrete value are checked (a genuinely dynamic fallback, e.g. derived from a CLI
    arg, is skipped, not failed). ``min_checked`` guards against a silently-broken
    resolver/call pattern matching nothing after a refactor -- raise it to the
    expected order of magnitude for your repo's call-site count.
    """
    import pytest

    schema_defaults = schema_section_field_defaults(schema_cls)
    constants = module_constants(files)
    checked = 0
    mismatches = []
    for call in find_cfg_get_calls(root, files, cfg_function_names):
        resolved = resolve_default(call.default_node, constants)
        if isinstance(resolved, _Unresolved):
            continue
        checked += 1
        schema_default = schema_defaults.get((call.section, call.key), "<key not declared in schema>")
        if to_hashable(resolved) != to_hashable(schema_default):
            mismatches.append(f"{call.file}:{call.line}  [{call.section}].{call.key}  call-site default={resolved!r}  schema default={schema_default!r}")

    assert checked >= min_checked, f"only resolved {checked} call-site default(s) (expected >= {min_checked}) -- resolver or call pattern may be broken"
    if mismatches:
        pytest.fail("cfg().get(...) call-site default(s) disagree with the schema's own default:\n  " + "\n  ".join(mismatches))
