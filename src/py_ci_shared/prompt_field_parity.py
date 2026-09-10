"""Shared check: a field a prompt or schema asks the model for is read by something, and stored.

WHERE THIS CAME FROM
--------------------
glossum's enrichment validator asked the model for ``ipa_correct`` on three surfaces, explained in prose why
it mattered, and no dataclass, parser or saver held it: the model paid output tokens to answer on every run
and the answer was discarded. Every existing gate started from the DATACLASS and traced forward, so a field
that never became a dataclass attribute was invisible to all of them. The prompt is the contract; this reads
the contract. The second half caught ``productivity``, which was parsed onto an object and then written to
no column -- consumed, by "does the name appear somewhere", and stored nowhere.

WHAT THIS PROVIDES
------------------
Building blocks, parameterised so a repository with file storage and dict schemas (autopsia) uses the same
checks as one with SQL and prose prompts (glossum):

* Where fields come from: :func:`keys_in_source` (JSON-shaped keys in a module's string literals) and
  :func:`keys_in_schema` (keys taken directly from a dict schema, e.g. one passed as ``json_schema=``).
  :func:`prompt_keys` combines them per file.
* What counts as a consumer: :func:`consumed_names`, with benchmark/gold modules excluded by path part or
  file-name fragment -- a grader reading raw output scores the model, it does not consume the field.
* Keys built at run time: :func:`accessor_keys`, from a caller-supplied accessor call pattern.
* What counts as stored: :func:`persisted_names_sql` (DDL columns and SQL bind names) and
  :func:`persisted_names_writer_keys` (dict keys written by the given writer modules, for file stores).
* The two findings: :func:`unconsumed_prompt_keys` and :func:`unpersisted_prompt_fields`.
* The gate's own blind spots, as checks: :func:`invisible_keys`, :func:`undemonstrated_fields`,
  :func:`structural_names_prompted`.

Baselining is the caller's (``pyutilz.dev.meta_test_utils.findings_ratchet`` or ``baseline_ratchet``): some
entries are fields a model MAY volunteer and the pipeline reasonably ignores, and sorting those from real
losses is per-field work. What must not happen is a NEW unread field appearing silently.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from re import Pattern
from typing import Any

__all__ = [
    "DEFAULT_KEY",
    "DEFAULT_STRUCTURAL",
    "accessor_keys",
    "consumed_names",
    "declared_scalar_fields",
    "invisible_keys",
    "keys_in_schema",
    "keys_in_source",
    "persisted_names_sql",
    "persisted_names_writer_keys",
    "prompt_keys",
    "string_literals",
    "structural_names_prompted",
    "undemonstrated_fields",
    "unconsumed_prompt_keys",
    "unpersisted_prompt_fields",
]

#: A JSON key inside a prompt's schema fence: ``"field_name":``. Lowercase start and three characters keep
#: prose and CamelCase tag names out; :func:`invisible_keys` reports what this cannot see.
DEFAULT_KEY: Pattern[str] = re.compile(r'"([a-z][a-z0-9_]{2,})"\s*:')
#: Keys of a JSON-schema's own vocabulary, or of the request envelope, rather than model-emitted content. Keep
#: this to names no prompt asks the model to FILL: :func:`structural_names_prompted` fails if one is.
DEFAULT_STRUCTURAL: frozenset[str] = frozenset({"properties", "required", "description", "content", "model", "messages", "schema", "format", "enum", "default"})
_SCHEMA_VOCABULARY = frozenset(
    {"type", "properties", "required", "items", "enum", "description", "additionalProperties", "$schema", "$ref", "anyOf", "oneOf", "allOf",
     "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "pattern", "default", "title", "format", "$defs", "definitions",
     "const", "nullable", "examples"}
)
_ANY_KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
_BIND = re.compile(r":([a-z][a-z0-9_]{2,})\b")
_NAME = re.compile(r"[a-z][a-z0-9_]{2,}")
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules"})


def _py_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(f for f in p.rglob("*.py") if not (_SKIP_DIRS & set(f.parts)))
    return sorted(set(out))


def string_literals(src: str) -> list[str]:
    """Every string constant in a module's source; an unparsable module yields none."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def keys_in_source(src: str, *, key: Pattern[str] = DEFAULT_KEY, structural: Iterable[str] = DEFAULT_STRUCTURAL) -> set[str]:
    """JSON-shaped keys a module's string literals ask the model to emit, minus structural names."""
    skip = set(structural)
    return {k for lit in string_literals(src) for k in key.findall(lit) if k not in skip}


_JSON_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_SCHEMA_MARKERS = ("properties", "$schema", "$ref", "anyOf", "oneOf", "allOf")


def _is_schema_node(node: Mapping[Any, Any]) -> bool:
    """Whether a dict is a JSON-schema node rather than an example payload: a schema marker, or a JSON ``type``."""
    kind = node.get("type")
    return any(m in node for m in _SCHEMA_MARKERS) or kind in _JSON_TYPES or (isinstance(kind, list) and bool(kind) and set(kind) <= _JSON_TYPES)


def keys_in_schema(schema: Any) -> set[str]:
    """Field names taken directly from a dict schema, recursively.

    A JSON-schema node gives its ``properties`` names, and its vocabulary (``type``, ``items``, ``required``,
    ...) is not reported. A dict that is not a schema node -- an example payload such as ``{"claim": "...",
    "items": [...]}`` -- gives every key, since there ``items`` is a field the model fills.
    """
    found: set[str] = set()
    if isinstance(schema, Mapping):
        if _is_schema_node(schema):
            props = schema.get("properties")
            if isinstance(props, Mapping):
                found.update(str(k) for k in props)
                for sub in props.values():
                    found |= keys_in_schema(sub)
            for k, v in schema.items():
                if k != "properties" and str(k) in _SCHEMA_VOCABULARY:
                    found |= keys_in_schema(v)
        else:
            for k, v in schema.items():
                found.add(str(k))
                found |= keys_in_schema(v)
    elif isinstance(schema, (list, tuple)):
        for item in schema:
            found |= keys_in_schema(item)
    return found


def prompt_keys(
    prompt_files: Iterable[Path],
    *,
    schemas: Mapping[str, Any] = {},
    key: Pattern[str] = DEFAULT_KEY,
    structural: Iterable[str] = DEFAULT_STRUCTURAL,
) -> dict[str, set[str]]:
    """``{field: {source, ...}}`` over prompt modules (by literal scan) and named dict ``schemas``."""
    out: dict[str, set[str]] = {}
    for path in _py_files(prompt_files):
        for k in keys_in_source(path.read_text(encoding="utf-8"), key=key, structural=structural):
            out.setdefault(k, set()).add(path.name)
    skip = set(structural)
    for name, schema in schemas.items():
        for k in keys_in_schema(schema) - skip:
            out.setdefault(k, set()).add(name)
    return out


def consumed_names(
    source_roots: Iterable[Path],
    *,
    exclude: Iterable[Path] = (),
    exclude_parts: Iterable[str] = (),
    exclude_name_fragments: Iterable[str] = (),
) -> set[str]:
    """Every name code could read a field through: annotated attributes, attribute access, string literals
    shaped like a name (``.get("x")``, allowlists), and ``:bind`` parameters inside SQL literals.

    Generous on purpose: the question is "is it read at all". ``exclude`` drops the prompt modules themselves;
    ``exclude_parts`` / ``exclude_name_fragments`` drop benchmark and gold modules, which read raw output to
    score it rather than consume it.
    """
    excluded = {Path(p).resolve() for p in exclude}
    parts, fragments = set(exclude_parts), tuple(exclude_name_fragments)
    names: set[str] = set()
    for path in _py_files(source_roots):
        resolved = path.resolve()
        if resolved in excluded or any(e in resolved.parents for e in excluded):
            continue
        if parts & set(path.parts) or (fragments and any(f in path.name for f in fragments)):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _NAME.fullmatch(node.value):
                    names.add(node.value)
                names.update(_BIND.findall(node.value))
    return names


def accessor_keys(source_roots: Iterable[Path], accessor: Pattern[str]) -> set[str]:
    """Keys code builds at run time: ``accessor`` must capture ``(prefix, field)``, giving ``prefix_field``."""
    keys: set[str] = set()
    for path in _py_files(source_roots):
        keys.update(f"{prefix}_{field}" for prefix, field in accessor.findall(path.read_text(encoding="utf-8")))
    return keys


def unconsumed_prompt_keys(
    keys: Mapping[str, Iterable[str]],
    consumed: set[str],
    *,
    built_at_run_time: set[str] = frozenset(),  # type: ignore[assignment]
    verdict_suffixes: Sequence[str] = ("_correct", "_correct_reason"),
) -> dict[str, list[str]]:
    """``{field: [sources]}`` for prompt fields nothing reads.

    A key the code builds at run time counts as read only when a real accessor call builds it -- never
    because its remainder after a prefix exists somewhere. Verdict fields (``*_correct``) are named exactly by
    a validator and are never cleared that way: stripping ``target_`` off ``target_gender_correct`` leaves the
    unrelated, consumed ``gender_correct``.
    """
    out: dict[str, list[str]] = {}
    for field, sources in sorted(keys.items()):
        if field in consumed:
            continue
        if not field.endswith(tuple(verdict_suffixes)) and field in built_at_run_time:
            continue
        out[field] = sorted(sources)
    return out


_DDL_COLUMN = re.compile(r"^\s+(\w+)\s+(?:TEXT|BOOLEAN|INTEGER|SMALLINT|BIGINT|REAL|DOUBLE|NUMERIC|JSONB?|TIMESTAMP|DATE|UUID|SERIAL|BIGSERIAL|VARCHAR)", re.MULTILINE | re.IGNORECASE)
_ADD_COLUMN = re.compile(r"ADD COLUMN(?:\s+IF NOT EXISTS)?\s+(\w+)", re.IGNORECASE)
_DICT_KEY = re.compile(r'["\']([a-z][a-z0-9_]{2,})["\']\s*:')


def persisted_names_sql(ddl_files: Iterable[Path], writer_roots: Iterable[Path]) -> set[str]:
    """Names that reach a SQL database: DDL column names, and ``:bind`` names and dict keys in the writers."""
    names: set[str] = set()
    for f in ddl_files:
        ddl = Path(f).read_text(encoding="utf-8")
        names.update(_DDL_COLUMN.findall(ddl))
        names.update(_ADD_COLUMN.findall(ddl))
    for path in _py_files(writer_roots):
        src = path.read_text(encoding="utf-8")
        names.update(_BIND.findall(src))
        names.update(_DICT_KEY.findall(src))
    return {n.lower() for n in names}


def persisted_names_writer_keys(writer_roots: Iterable[Path]) -> set[str]:
    """Names that reach a file store: dict keys written by the writer modules, as literals or ``dict(k=...)``."""
    names: set[str] = set()
    for path in _py_files(writer_roots):
        src = path.read_text(encoding="utf-8")
        names.update(_DICT_KEY.findall(src))
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
                names.update(kw.arg for kw in node.keywords if kw.arg)
    return names


_CONTAINER_PREFIXES = ("list", "dict", "set", "tuple", "List", "Dict", "Set", "Tuple")
_CONTAINER_FRAGMENTS = ("list[", "dict[", "List[", "Dict[")


def _scalar_annotations(cls: ast.ClassDef) -> list[tuple[str, str]]:
    """``(name, annotation)`` for each annotated attribute of ``cls`` that is not a container."""
    out = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann = ast.unparse(stmt.annotation) if stmt.annotation else ""
            if not (ann.startswith(_CONTAINER_PREFIXES) or any(c in ann for c in _CONTAINER_FRAGMENTS)):
                out.append((stmt.target.id, ann))
    return out


def declared_scalar_fields(source_roots: Iterable[Path]) -> set[str]:
    """Names declared as an annotated, non-container field on a class.

    The narrowing that makes the persistence check usable: a prompt key someone declared as a scalar field is
    the shape that goes missing, while section CONTAINERS (``antonyms: list[...]``, a nested verdict object)
    reach storage through their own fields and would bury the real findings.
    """
    class_names: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for path in _py_files(source_roots):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_names.add(node.name)
                candidates.extend(_scalar_annotations(node))
    out: set[str] = set()
    for name, ann in candidates:
        bare = ann.replace('"', "").replace("'", "").replace("| None", "").replace("None |", "").strip()
        if bare.startswith("Optional[") and bare.endswith("]"):
            bare = bare[len("Optional[") : -1].strip()
        if bare not in class_names:
            out.add(name)
    return out


def unpersisted_prompt_fields(
    keys: Mapping[str, Iterable[str]],
    declared: set[str],
    persisted: set[str],
    *,
    column_prefixes: Sequence[str] = (),
    dynamic_prefixes: Sequence[str] = (),
) -> dict[str, list[str]]:
    """``{field: [sources]}`` for prompt fields declared as a scalar field and stored under no name.

    ``column_prefixes`` covers a naming convention for verdict columns (glossum stores ``x_correct`` as
    ``validation_x_correct``). This is a name-level check: a value stored under an unrelated name is a known
    false positive, and a name that exists in storage for a different table is a known false NEGATIVE --
    only a runtime sweep (``pyutilz.dev.persistence_sweep``) sees which row a value really reached.
    """
    out: dict[str, list[str]] = {}
    for field, sources in sorted(keys.items()):
        if field not in declared:
            continue
        if field in persisted or any(f"{p}{field}" in persisted for p in column_prefixes):
            continue
        if any(field.startswith(p) and field[len(p) :] in persisted for p in dynamic_prefixes):
            continue
        out[field] = sorted(sources)
    return out


def invisible_keys(src: str, *, key: Pattern[str] = DEFAULT_KEY) -> set[str]:
    """Keys in a module's literals that ``key`` cannot see (too short, upper-case start): the gate's blind spot."""
    return {k for lit in string_literals(src) for k in _ANY_KEY.findall(lit) if not key.fullmatch(f'"{k}":')}


def undemonstrated_fields(src: str, *, described: Pattern[str] = re.compile(r"`([a-z][a-z0-9_]*_correct)`"), key: Pattern[str] = DEFAULT_KEY) -> set[str]:
    """Fields a prompt describes in prose (backticked, by default ``*_correct``) but never shows in a JSON example.

    Such a field is invisible to a fence-reading gate, and the model has no example of how to emit it.
    """
    lits = string_literals(src)
    return {m for lit in lits for m in described.findall(lit)} - {k for lit in lits for k in key.findall(lit)}


def structural_names_prompted(sources: Iterable[str], *, structural: Iterable[str] = DEFAULT_STRUCTURAL, key: Pattern[str] = DEFAULT_KEY) -> set[str]:
    """Structural names that a prompt actually asks the model to fill: each is a permanent blind spot."""
    emitted = {k for src in sources for lit in string_literals(src) for k in key.findall(lit)}
    return set(structural) & emitted
