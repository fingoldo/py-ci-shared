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
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

if TYPE_CHECKING:
    # Type-only: pydantic is a [dev] test dependency here (schema_cls is always a real
    # pydantic.BaseModel subclass at runtime), not a hard runtime import, matching this
    # module's own "dependency-light" convention documented above.
    from pydantic import BaseModel

DEFAULT_CFG_FUNCTION_NAMES: frozenset[str] = frozenset({"cfg", "_cfg"})


class CfgGetCall(NamedTuple):
    file: str
    abs_path: Path
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
            abs_path=path,
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


class ConstantResolver:
    """Resolves a default-expression AST node to a constant value by following
    same-file assignments, ``from X import Y`` chains, and ``module_alias.NAME``
    attribute access through ``root``'s own module/package layout -- caching
    parsed trees so the whole repo is parsed once regardless of how many call
    sites reference the same module.

    This is NOT a flat, repo-wide "search every file for this name" scan: a
    bare name is resolved relative to the SPECIFIC file that references it,
    following that file's own imports -- so when two different, unrelated
    files each define their OWN same-named constant with a DIFFERENT value
    (a real pattern seen in practice: six modules each defining their own
    ``BATCH_SIZE`` with six different values), a call site's actual import
    target resolves correctly instead of an arbitrary "whichever file's
    constant got scanned last" collision silently producing the wrong value.
    """

    def __init__(self, root: Path):
        self.root = root
        self._trees: dict[Path, Optional[ast.Module]] = {}

    def _tree(self, path: Path) -> Optional[ast.Module]:
        if path not in self._trees:
            try:
                self._trees[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                self._trees[path] = None
        return self._trees[path]

    def _module_file(self, dotted: str) -> Optional[Path]:
        parts = dotted.split(".")
        cand = self.root.joinpath(*parts).with_suffix(".py")
        if cand.exists():
            return cand
        cand = self.root.joinpath(*parts, "__init__.py")
        if cand.exists():
            return cand
        return None

    def _package_dir(self, dotted: str) -> Optional[Path]:
        d = self.root.joinpath(*dotted.split("."))
        return d if d.is_dir() else None

    def _const_in_file(self, path: Path, name: str) -> tuple[bool, Any]:
        """(found, value) for a bare top-level ``name = <literal>`` in ``path``."""
        tree = self._tree(path)
        if tree is None:
            return False, None
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name:
                try:
                    return True, ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    return False, None
        return False, None

    def _const_via_package(self, pkg_dir: Path, name: str) -> tuple[bool, Any]:
        """Search every direct ``.py`` child of a package dir for a UNIQUE top-level
        ``name = <literal>`` -- handles a package ``__init__.py`` that aggregates
        (``from .x import *``) without the constant being assigned in ``__init__.py``
        itself. Ambiguous (more than one distinct value found) resolves to unresolved,
        never picks one arbitrarily."""
        found: set[Any] = set()
        for py in pkg_dir.glob("*.py"):
            ok, val = self._const_in_file(py, name)
            if ok:
                try:
                    found.add(val)
                except TypeError:
                    found.add(repr(val))
        if len(found) == 1:
            return True, next(iter(found))
        return False, None

    def _imported_source(self, path: Path, name: str) -> Optional[str]:
        """If ``path`` does ``from MODULE import name [as alias]`` matching ``name``,
        return the dotted MODULE. Handles both ``from pkg import x`` (name IS the
        alias) and ``import pkg.mod as alias`` / ``import pkg.mod``."""
        tree = self._tree(path)
        if tree is None:
            return None
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        return node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name.split(".")[-1]) == name:
                        return alias.name
        return None

    def _const_from_file(self, path: Path, name: str, seen: set[Path]) -> tuple[bool, Any]:
        """Resolve a bare Name in the context of ``path``: same-file constant, else
        follow an import (possibly into a package, aggregated via star-imports)
        recursively. ``seen`` guards against an import cycle."""
        if path in seen:
            return False, None
        seen.add(path)
        ok, val = self._const_in_file(path, name)
        if ok:
            return True, val
        src_module = self._imported_source(path, name)
        if src_module is None:
            return False, None
        mod_file = self._module_file(src_module)
        if mod_file is not None:
            ok, val = self._const_from_file(mod_file, name, seen)
            if ok:
                return True, val
            if mod_file.name == "__init__.py":
                return self._const_via_package(mod_file.parent, name)
            return False, None
        pkg_dir = self._package_dir(src_module)
        if pkg_dir is not None:
            return self._const_via_package(pkg_dir, name)
        return False, None

    def resolve(self, node: Optional[ast.expr], current_file: Path) -> Any:
        """Resolve a default-expression AST node to a value: a literal via
        ``ast.literal_eval``, else a bare ``Name``/dotted ``Attribute`` chain
        resolved in the context of ``current_file`` (same-file constant, or
        followed through that file's own imports), else an ``_Unresolved``
        sentinel wrapping the source text (compared by that text, never
        silently treated as equal to a different unresolved expression)."""
        if node is None:
            return None
        src = ast.unparse(node)
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError, TypeError):
            pass
        if isinstance(node, ast.Name):
            ok, val = self._const_from_file(current_file, node.id, set())
            if ok:
                return val
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = node.value.id
            src_module = self._imported_source(current_file, base)
            if src_module is not None:
                mod_file = self._module_file(src_module)
                if mod_file is not None and mod_file.name == "__init__.py":
                    ok, val = self._const_via_package(mod_file.parent, node.attr)
                    if ok:
                        return val
                elif mod_file is not None:
                    ok, val = self._const_from_file(mod_file, node.attr, set())
                    if ok:
                        return val
                else:
                    pkg_dir = self._package_dir(src_module)
                    if pkg_dir is not None:
                        ok, val = self._const_via_package(pkg_dir, node.attr)
                        if ok:
                            return val
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


def schema_section_field_map(schema_cls: type[BaseModel]) -> dict[str, set[str]]:
    """Top-level section name -> its sub-model's real field names, for a 2-level
    Pydantic schema (``schema_cls.model_fields`` maps each section to a field whose
    ``.annotation`` is itself a Pydantic model with its own ``model_fields``)."""
    out: dict[str, set[str]] = {}
    for section, field in schema_cls.model_fields.items():
        sub_model = field.annotation
        assert sub_model is not None, f"schema field {section!r} has no annotation -- not a valid 2-level schema"
        out[section] = set(sub_model.model_fields.keys())
    return out


def schema_section_field_defaults(schema_cls: type[BaseModel]) -> dict[tuple[str, str], Any]:
    """(section, key) -> that field's own Pydantic default value, for the same
    2-level schema shape as ``schema_section_field_map``.

    A field declared with ``default_factory=...`` (the required style for a
    mutable default like a dict/list built by a callable, e.g.
    ``Field(default_factory=dict)``) has ``FieldInfo.default`` set to the
    ``PydanticUndefined`` sentinel, not the actual value -- calling the
    factory to get the real produced value avoids a spurious "schema
    default=PydanticUndefined" mismatch against every call site whose own
    ``.get(..., default=...)`` correctly mirrors what the factory produces
    (e.g. ``{}`` for ``default_factory=dict``).
    """
    out: dict[tuple[str, str], Any] = {}
    for section, field in schema_cls.model_fields.items():
        sub_model = field.annotation
        assert sub_model is not None, f"schema field {section!r} has no annotation -- not a valid 2-level schema"
        for key, sub_field in sub_model.model_fields.items():
            if sub_field.default_factory is not None:
                out[(section, key)] = sub_field.default_factory()  # type: ignore[call-arg]  # pydantic 2's default_factory is always zero-arg unless validate_default uses the 1-arg (data) form, not used by any schema this helper targets
            else:
                out[(section, key)] = sub_field.default
    return out


def assert_every_cfg_get_call_resolves_to_a_schema_field(
    root: Path,
    files: Iterable[Path],
    schema_cls: type[BaseModel],
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
    schema_cls: type[BaseModel],
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
    default_type_repr: Optional[str] = None,
) -> None:
    """Fail if two call sites reading the SAME ``(section, key)`` pass a different
    default/``type_`` -- two call sites silently disagreeing about what "the config
    value" resolves to when absent. Compared by RESOLVED value (literal-eval'd, or
    looked up against repo-wide module constants), not raw source text, so a bare
    ``24`` and a named constant that also equals ``24`` are correctly treated as
    agreeing.

    ``default_type_repr`` (e.g. ``"int"``) is the accessor's OWN default for its
    ``type_`` parameter (mirroring its real signature, e.g. ``def get(self, section,
    key, default=None, type_: type = int)``) -- a call site that OMITS ``type_``
    entirely is normalized to this value before comparing, so it's correctly treated
    as agreeing with a sibling call site that passes the SAME type explicitly
    (``type_=int``) instead of being flagged as a spurious "None vs 'int'" mismatch.
    Leave unset if every call site in this repo always passes ``type_`` explicitly.
    """
    import pytest

    calls = find_cfg_get_calls(root, files, cfg_function_names)
    by_pair: dict[tuple[str, str], list[CfgGetCall]] = {}
    for call in calls:
        by_pair.setdefault((call.section, call.key), []).append(call)

    resolver = ConstantResolver(root)
    bad = []
    for (section, key), pair_calls in by_pair.items():
        if len(pair_calls) < 2:
            continue
        resolvable_calls = [c for c in pair_calls if not isinstance(resolver.resolve(c.default_node, c.abs_path), _Unresolved)]
        if len(resolvable_calls) < 2:
            continue  # a genuinely dynamic (unresolvable) default at one site can't be compared at all -- not a divergence
        resolved_defaults = {to_hashable(resolver.resolve(c.default_node, c.abs_path)) for c in resolvable_calls}
        types = {(default_type_repr if c.type_src is None else c.type_src) for c in resolvable_calls}
        if len(resolved_defaults) > 1 or len(types) > 1:
            sites = ", ".join(f"{c.file}:{c.line}(default={ast.unparse(c.default_node) if c.default_node else None!r}, type_={c.type_src!r})" for c in resolvable_calls)
            bad.append(f"[{section}] {key}: divergent default/type_ across call sites: {sites}")
    if bad:
        pytest.fail("cfg().get(...) call sites reading the same (section, key) with divergent default/type_:\n  " + "\n  ".join(bad))


def assert_call_site_defaults_match_schema_defaults(
    root: Path,
    files: Iterable[Path],
    schema_cls: type[BaseModel],
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
    min_checked: int = 1,
    known_intentional_mismatches: Optional[dict[tuple[str, str], str]] = None,
) -> None:
    """Fail if a call site's own resolved default disagrees with the schema field's
    Pydantic default. Only call sites whose default expression actually resolves to a
    concrete value are checked (a genuinely dynamic fallback, e.g. derived from a CLI
    arg, is skipped, not failed). ``min_checked`` guards against a silently-broken
    resolver/call pattern matching nothing after a refactor -- raise it to the
    expected order of magnitude for your repo's call-site count.

    ``known_intentional_mismatches`` whitelists a ``(section, key)`` whose call-site
    default is DELIBERATELY different from the schema's normal-operation default --
    e.g. a safety-net fallback for the (in practice unreachable, since the schema
    always resolves a real value) case where config resolution itself fails, chosen
    to be the SAFEST/simplest value rather than mirroring the schema's normal value
    (``variant_count`` defaulting to 1, the single-call hot path, vs. the schema's
    normal 3). Applies to every call site reading that ``(section, key)``, matching
    ``known_indirect_readers``'s per-field (not per-call-site) whitelist granularity.
    """
    import pytest

    known_intentional_mismatches = known_intentional_mismatches or {}
    schema_defaults = schema_section_field_defaults(schema_cls)
    resolver = ConstantResolver(root)
    checked = 0
    mismatches = []
    for call in find_cfg_get_calls(root, files, cfg_function_names):
        if (call.section, call.key) in known_intentional_mismatches:
            continue
        resolved = resolver.resolve(call.default_node, call.abs_path)
        if isinstance(resolved, _Unresolved):
            continue
        checked += 1
        schema_default = schema_defaults.get((call.section, call.key), "<key not declared in schema>")
        if to_hashable(resolved) != to_hashable(schema_default):
            mismatches.append(f"{call.file}:{call.line}  [{call.section}].{call.key}  call-site default={resolved!r}  schema default={schema_default!r}")

    assert checked >= min_checked, f"only resolved {checked} call-site default(s) (expected >= {min_checked}) -- resolver or call pattern may be broken"
    if mismatches:
        pytest.fail("cfg().get(...) call-site default(s) disagree with the schema's own default:\n  " + "\n  ".join(mismatches))


class FrozenCliDefault(NamedTuple):
    file: str
    line: int
    var_name: str
    section: str
    key: str
    argparse_lines: tuple[int, ...]


def _module_scope_cfg_get_assignments(
    tree: ast.Module,
    cfg_function_names: frozenset[str],
) -> list[tuple[str, str, str, int]]:
    """``(var_name, section, key, line)`` for every top-level (module-scope, not inside any
    ``def``/``class``) ``X = cfg().get(section, key, ...)`` assignment -- ``tree.body`` only
    contains module-level statements, so this naturally excludes anything inside a function."""
    out: list[tuple[str, str, str, int]] = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "get"):
            continue
        if not _is_cfg_call(call.func.value, cfg_function_names):
            continue
        if len(call.args) < 2:
            continue
        section_node, key_node = call.args[0], call.args[1]
        if not (isinstance(section_node, ast.Constant) and isinstance(section_node.value, str)):
            continue
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        out.append((node.targets[0].id, section_node.value, key_node.value, node.lineno))
    return out


def _argparse_default_use_lines(tree: ast.Module, var_names: frozenset[str]) -> dict[str, list[int]]:
    """``var_name -> [line, ...]`` for every ``<parser>.add_argument(..., default=<Name>)`` call
    anywhere in the file whose ``default=`` value is a bare reference to one of ``var_names``."""
    out: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
            continue
        default_val = next((kw.value for kw in node.keywords if kw.arg == "default"), None)
        if isinstance(default_val, ast.Name) and default_val.id in var_names:
            out.setdefault(default_val.id, []).append(node.lineno)
    return out


def find_module_scope_frozen_cli_defaults(
    root: Path,
    files: Iterable[Path],
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
) -> list[FrozenCliDefault]:
    """Every module-scope ``X = cfg().get(...)`` assignment whose variable is later used as an
    ``argparse.add_argument(..., default=X)`` value in the same file.

    ``cfg().get(...)`` is normally re-read on every call (typically inside a hot loop or a
    per-invocation closure, e.g. ``make_n_workers()``-style patterns) so a live config edit takes
    effect within the config-reload interval. Reading it ONCE at module import time and feeding
    the result into an argparse default freezes that one CLI flag's effective value for the
    entire process lifetime (hours to days for a long-running scraper/service), silently breaking
    that specific knob's hot-reload guarantee while every sibling knob still honours it -- the
    bug is invisible in a diff (the code *looks* like every other ``cfg().get(...)`` call site)
    and only manifests as "I edited config.toml and nothing happened" for that one flag.
    """
    out: list[FrozenCliDefault] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        assignments = _module_scope_cfg_get_assignments(tree, cfg_function_names)
        if not assignments:
            continue
        var_names = frozenset(a[0] for a in assignments)
        argparse_uses = _argparse_default_use_lines(tree, var_names)
        for var_name, section, key, line in assignments:
            lines = argparse_uses.get(var_name)
            if lines:
                out.append(FrozenCliDefault(
                    file=path.relative_to(root).as_posix(),
                    line=line,
                    var_name=var_name,
                    section=section,
                    key=key,
                    argparse_lines=tuple(lines),
                ))
    return out


def assert_no_module_scope_frozen_cli_defaults(
    root: Path,
    files: Iterable[Path],
    cfg_function_names: frozenset[str] = DEFAULT_CFG_FUNCTION_NAMES,
    known_intentional_freezes: Optional[dict[tuple[str, str], str]] = None,
) -> None:
    """Fail if a module-scope ``cfg().get(...)`` read feeds an ``argparse`` default -- see
    ``find_module_scope_frozen_cli_defaults`` for why this specifically (as opposed to any other
    module-scope config read) breaks the project's hot-reload contract for that one CLI flag.

    ``known_intentional_freezes`` whitelists a ``(section, key)`` where reading once at import
    time (rather than resolving fresh per-invocation, e.g. via a closure like
    ``make_n_workers(cli_value)``) is a deliberate, reviewed choice -- with a reason string
    surfaced in the failure message convention used by this module's other ``known_*`` params.
    """
    import pytest

    known_intentional_freezes = known_intentional_freezes or {}
    bad = []
    for hit in find_module_scope_frozen_cli_defaults(root, files, cfg_function_names):
        if (hit.section, hit.key) in known_intentional_freezes:
            continue
        arg_lines = ", ".join(str(line) for line in hit.argparse_lines)
        bad.append(
            f"{hit.file}:{hit.line}  {hit.var_name} = cfg().get({hit.section!r}, {hit.key!r}, ...) at module scope, "
            f"used as an argparse default at line(s) {arg_lines} -- this CLI flag's effective value is frozen at "
            f"import time, not hot-reloadable like the rest of the project's config. Move the cfg().get(...) read "
            f"inside a per-invocation closure/function (default=None, resolve fresh where the value is actually "
            f"used), or pass ({hit.section!r}, {hit.key!r}) to known_intentional_freezes if this one is deliberate."
        )
    if bad:
        pytest.fail("Module-scope cfg().get(...) read(s) feeding an argparse default (frozen-at-import hot-reload gap):\n  " + "\n  ".join(bad))
