"""A wrapper that copies some of a protocol's attributes from the callable it wraps must copy all the inner one has.

Dashboard audit 2026-10-03, P1: ``load_top_jobs`` wraps a ``@ttl_cached`` loader and re-exported its
``cache_clear`` (``load_top_jobs.cache_clear = _load_top_jobs_cached.cache_clear``) but not its ``refresh``. The warm
scheduler calls ``getattr(fn, "refresh", fn)``, so for this loader it fell back to an ordinary cached call that
never moved the entry's deadline, and the landing listing was expired for 50 s of every 200 s. Nothing failed: the
fallback is the scheduler's documented behaviour for a loader that has no ``refresh``.

The rule, per module: a statement ``W.a = I.a`` where ``a`` belongs to a protocol (default :data:`CACHED_LOADER`:
``cache_clear``, ``refresh``, ``has_value``) makes ``W`` a wrapper of ``I`` for that protocol. Every other attribute
of the protocol that ``I`` has must then be set on ``W`` in the same module (``W.b = ...`` with any value,
``setattr(W, "b", ...)``, or a decorator on ``W`` that provides it). What ``I`` has is read from the decorators on
its ``def`` through *providers* (decorator name -> attributes it sets; default :data:`DEFAULT_PROVIDERS`), plus any
``I.b`` the module reads. An ``I`` whose decorators are all unknown is taken to have the whole protocol: pass the
project's decorators in *providers* rather than let a wrapper go unchecked.

A deliberate omission goes in the baseline with its reason. The run-time complement is
:func:`py_ci_shared.protocol_attributes.assert_satisfies_protocol`, which a consumer calls at registration (the warm-target registry, a cache-clearer
list) with the object it is about to rely on.

Relation to :mod:`py_ci_shared.kwarg_forwarding`: that gate reports a wrapper that drops an ARGUMENT on the way to the
callable it wraps; this one reports a wrapper that drops an ATTRIBUTE of the protocol the caller reads off it.

Usage in a consumer's meta test::

    from py_ci_shared.wrapper_protocol_parity import assert_wrapper_protocol_parity

    def test_wrappers_keep_the_cached_loader_protocol():
        assert_wrapper_protocol_parity(
            "dashboard",
            providers={"ttl_cached": {"cache_clear", "refresh"}, "background_cached": {"cache_clear", "has_value"}},
        )
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, scan_python

__all__ = [
    "CACHED_LOADER",
    "DEFAULT_PROVIDERS",
    "RULE",
    "assert_wrapper_protocol_parity",
    "find_wrapper_protocol_parity",
]

RULE = "wrapper-protocol-parity"
CACHED_LOADER: frozenset[str] = frozenset({"cache_clear", "refresh", "has_value"})
#: Decorator (or factory) name -> the protocol attributes the object it returns carries.
DEFAULT_PROVIDERS: Mapping[str, frozenset[str]] = {
    "lru_cache": frozenset({"cache_clear"}),
    "cache": frozenset({"cache_clear"}),
    "ttl_cached": frozenset({"cache_clear", "refresh"}),
    "background_cached": frozenset({"cache_clear", "has_value"}),
}


def _name(node: ast.AST) -> Optional[str]:
    """``a.b.c`` -> ``"a.b.c"`` for a Name/Attribute chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _decorator_label(dec: ast.expr) -> str:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")


def _provided(labels: Iterable[str], providers: Mapping[str, frozenset[str]]) -> Optional[set[str]]:
    """Attributes the known decorators provide; None when none of *labels* is known."""
    known = [providers[label] for label in labels if label in providers]
    return set().union(*known) if known else None


def _decorators(tree: ast.Module) -> dict[str, list[str]]:
    """Name -> labels of the decorators (or decorator factories) that built it."""
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = [_decorator_label(d) for d in node.decorator_list]
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            # `loader = ttl_cached("k", 1.0)(_raw)`: the outer call's callee is a call of the decorator
            callee = node.value.func
            label = _decorator_label(callee) if isinstance(callee, (ast.Call, ast.Name, ast.Attribute)) else ""
            for t in node.targets:
                if isinstance(t, ast.Name) and label:
                    out.setdefault(t.id, []).append(label)
    return out


def _assign_facts(node: ast.Assign, set_on: dict[str, set[str]], copies: list[tuple[int, str, str, str]]) -> None:
    for target in node.targets:
        if not isinstance(target, ast.Attribute):
            continue
        owner = _name(target.value)
        if owner is None:
            continue
        set_on.setdefault(owner, set()).add(target.attr)
        if isinstance(node.value, ast.Attribute) and node.value.attr == target.attr:
            source = _name(node.value.value)
            if source is not None and source != owner:
                copies.append((node.lineno, owner, source, target.attr))


def _attribute_facts(tree: ast.Module) -> tuple[dict[str, set[str]], dict[str, set[str]], list[tuple[int, str, str, str]]]:
    """``(set_on, read_on, copies)``: attributes assigned to and read off each name, and ``W.a = I.a`` copies."""
    set_on: dict[str, set[str]] = {}
    read_on: dict[str, set[str]] = {}
    copies: list[tuple[int, str, str, str]] = []  # line, wrapper, inner, attribute
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            _assign_facts(node, set_on, copies)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr" and len(node.args) >= 2:
            owner, name_node = _name(node.args[0]), node.args[1]
            if owner is not None and isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
                set_on.setdefault(owner, set()).add(name_node.value)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            owner = _name(node.value)
            if owner is not None:
                read_on.setdefault(owner, set()).add(node.attr)
    return set_on, read_on, copies


def _findings_in(tree: ast.Module, rel: str, protocols: Iterable[frozenset[str]], providers: Mapping[str, frozenset[str]]) -> list[Finding]:
    decorators = _decorators(tree)
    set_on, read_on, copies = _attribute_facts(tree)
    out: list[Finding] = []
    reported: set[tuple[str, str, frozenset[str]]] = set()
    for line, wrapper, inner, attr in sorted(copies):
        for protocol in protocols:
            if attr not in protocol or (wrapper, inner, protocol) in reported:
                continue
            provided = _provided(decorators.get(inner.split(".")[-1], []), providers)
            inner_has = (set(protocol) if provided is None else provided | (read_on.get(inner, set()) & protocol)) & protocol
            wrapper_has = set_on.get(wrapper, set()) | (_provided(decorators.get(wrapper, []), providers) or set())
            missing = sorted(inner_has - wrapper_has)
            if missing:
                reported.add((wrapper, inner, protocol))
                out.append(
                    Finding(
                        rel,
                        line,
                        RULE,
                        f"{wrapper} copies {attr} from {inner} but not {', '.join(missing)}; a caller reading "
                        f"getattr({wrapper}, {missing[0]!r}, ...) gets its fallback instead of {inner}'s",
                    )
                )
    return out


def find_wrapper_protocol_parity(
    root: Union[str, Path],
    *,
    protocols: Iterable[Iterable[str]] = (CACHED_LOADER,),
    providers: Optional[Mapping[str, Iterable[str]]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every wrapper under *root* that copies part of a protocol from its inner callable, sorted by path and line.

    *providers* is merged over :data:`DEFAULT_PROVIDERS`. Raises ``EmptyScanError`` when fewer than *min_files*
    files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless *allow_unparsed*)."""
    sets = [frozenset(p) for p in protocols]
    merged = {**DEFAULT_PROVIDERS, **{k: frozenset(v) for k, v in (providers or {}).items()}}
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, sets, merged))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_wrapper_protocol_parity(
    root: Union[str, Path],
    *,
    protocols: Iterable[Iterable[str]] = (CACHED_LOADER,),
    providers: Optional[Mapping[str, Iterable[str]]] = None,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any wrapper missing protocol attributes its inner callable has, or with *baseline_path* on any finding
    the baseline does not accept."""
    found = find_wrapper_protocol_parity(root, protocols=protocols, providers=providers, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    guidance = "set the missing attributes on the wrapper (forwarding to the inner callable), or baseline the omission with its reason"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="wrapper_protocol_parity", refresh_command="PY_CI_SHARED_REFRESH=wrapper_protocol_parity")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} wrapper-protocol-parity finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
