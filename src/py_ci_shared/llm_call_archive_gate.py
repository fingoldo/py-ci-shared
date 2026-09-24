"""Shared check: every paid LLM call goes through the place that keeps its raw answer.

WHERE THIS CAME FROM
--------------------
glossum archived the model's raw response on one path only (its translation retry loop). Validators,
example generation, the content translator and the legacy generators left it in a log rotated after two
weeks and in ring-buffer tables that delete old rows; a truncated answer was raised away before anything
kept it. autopsia's extraction path kept no raw text at all. Both fixes have the same shape: one choke
point every call passes through (a wrapped provider, see ``pyutilz.dev.attempt_archive``), and a check
that nothing goes around it.

WHAT THIS CHECKS
----------------
Three ways a call escapes the choke point, each an AST walk over the given source directories:

* :func:`find_direct_sdk_calls` -- a call shaped like a model SDK's payload method
  (``.messages.create``, ``.chat.completions.create``, ``.generate_content``) made directly.
* :func:`find_unwrapped_providers` -- a provider constructed from its class, or through a factory that
  does not wrap it, anywhere but the wrapping factory module(s).
* :func:`find_generate_calls_without_archive` -- a module that calls ``generate*`` on something and never
  names the archive. File-level and cheap; a pass is not proof, which is why the first two exist.

:func:`assert_every_llm_call_is_archived` runs all three against an ``allowed`` mapping of file -> reason,
and also fails for an allowed entry that no longer matches anything, so an exemption cannot outlive its cause.
"""

from __future__ import annotations

import ast
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence
from typing import Optional

from ._core import DEFAULT_EXCLUDE, CorpusError, ImportAliases, SourceError, iter_files, parse_source, relative_posix

__all__ = [
    "DEFAULT_GENERATE_METHODS",
    "DEFAULT_SDK_METHODS",
    "assert_every_llm_call_is_archived",
    "find_direct_sdk_calls",
    "find_generate_calls_without_archive",
    "find_unwrapped_providers",
    "python_files",
]

# (owner attribute, method): ``client.messages.create`` is ("messages", "create"); ``None`` matches any owner.
# OpenAI's Responses API (``client.responses.create``), structured output (``chat.completions.parse``) and Anthropic's
# streaming (``messages.stream``) are payload calls as much as ``create``.
DEFAULT_SDK_METHODS: frozenset[tuple[str | None, str]] = frozenset(
    {
        ("messages", "create"),
        ("messages", "stream"),
        ("completions", "create"),
        ("completions", "parse"),
        ("responses", "create"),
        ("responses", "stream"),
        (None, "generate_content"),
        (None, "generate_content_async"),
    }
)
DEFAULT_GENERATE_METHODS: frozenset[str] = frozenset({"generate", "generate_json", "generate_stream", "generate_batch"})
#: Kept for callers that imported it; enumeration uses ``_core.DEFAULT_EXCLUDE`` (a superset).
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules", ".tox", "build", "dist"})


def python_files(repo_root: Path, scanned: Sequence[str]) -> list[Path]:
    """Every ``*.py`` under the scanned directories of ``repo_root``, sorted, skipping caches and venvs.

    A scanned entry that does not exist raises ``py_ci_shared._core.CorpusError``: a typo (``srcx``) must not read as a
    tree with no LLM calls.
    """
    out: list[Path] = []
    for top in scanned:
        base = repo_root / top
        if base.is_file() and base.suffix == ".py":
            out.append(base)
            continue
        out.extend(iter_files(base, ("*.py",), exclude=DEFAULT_EXCLUDE | _SKIP_DIRS))
    return sorted(set(out))


def _rel(path: Path, repo_root: Path) -> str:
    return relative_posix(path, repo_root)


def _parsed(repo_root: Path, scanned: Sequence[str], unparsed: Optional[list[str]] = None) -> list[tuple[Path, str, ast.Module]]:
    """``(path, source, tree)`` for every scanned file, parsed once through the shared ``_core`` cache (BOM-safe).
    A file that cannot be parsed is appended to *unparsed* (``rel:line: why``) instead of raising."""
    out: list[tuple[Path, str, ast.Module]] = []
    for path in python_files(repo_root, scanned):
        try:
            source, tree = parse_source(path)
        except SourceError as exc:
            if unparsed is not None:
                unparsed.append(f"{_rel(path, repo_root)}:{exc.line or 1}: {exc.kind}: {exc.message}")
            continue
        out.append((path, source, tree))
    return out


def _tree_of(source_or_tree: "str | ast.Module") -> ast.Module:
    return source_or_tree if isinstance(source_or_tree, ast.Module) else ast.parse(source_or_tree)


def _sdk_call_lines(source: "str | ast.Module", sdk_methods: frozenset[tuple[str | None, str]]) -> list[int]:
    lines = []
    for node in ast.walk(_tree_of(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value.attr if isinstance(node.func.value, ast.Attribute) else None
        if (owner, node.func.attr) in sdk_methods or (None, node.func.attr) in sdk_methods:
            lines.append(node.lineno)
    return sorted(lines)


def find_direct_sdk_calls(
    repo_root: Path, scanned: Sequence[str], *, sdk_methods: frozenset[tuple[str | None, str]] = DEFAULT_SDK_METHODS
) -> dict[str, list[int]]:
    """``{file: [lines]}`` of direct model-SDK payload calls."""
    found = {}
    for path, _source, tree in _parsed(repo_root, scanned):
        if lines := _sdk_call_lines(tree, sdk_methods):
            found[_rel(path, repo_root)] = lines
    return found


def _unwrapped_provider_lines(
    source: "str | ast.Module", provider_classes: frozenset[str], unwrapping_factory_modules: Sequence[str], factory_names: Sequence[str]
) -> list[int]:
    """Lines constructing a provider: a provider class called by name, or a factory from an unwrapping module reached
    through any import spelling (``from m import get_llm_provider as g``, ``import m.factory as f; f.get_llm_provider()``,
    ``from pyutilz.llm import factory; factory.get_llm_provider()``)."""
    tree = _tree_of(source)
    aliases = ImportAliases.from_tree(tree)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        qualified = aliases.qualified_name(func) or ""
        module, _, last = qualified.rpartition(".")
        via_factory = last in factory_names and bool(module) and any(module == m or module.startswith(m + ".") for m in unwrapping_factory_modules)
        if name in provider_classes or via_factory:
            lines.append(node.lineno)
    return sorted(lines)


def find_unwrapped_providers(
    repo_root: Path,
    scanned: Sequence[str],
    *,
    provider_classes: Iterable[str],
    wrapping_factories: Iterable[str],
    unwrapping_factory_modules: Sequence[str] = ("pyutilz.llm",),
    factory_names: Sequence[str] = ("get_llm_provider",),
) -> dict[str, list[int]]:
    """``{file: [lines]}`` constructing a provider outside the wrapping factory module(s).

    A provider class called by name counts, and so does a factory imported from a module that returns
    providers unwrapped (``pyutilz.llm.factory.get_llm_provider``, by default), under any alias.
    """
    classes, factories = frozenset(provider_classes), set(wrapping_factories)
    found = {}
    for path, _source, tree in _parsed(repo_root, scanned):
        rel = _rel(path, repo_root)
        if rel in factories:
            continue
        if lines := _unwrapped_provider_lines(tree, classes, unwrapping_factory_modules, factory_names):
            found[rel] = lines
    return found


def _generate_call_lines(source: "str | ast.Module", methods: frozenset[str]) -> list[int]:
    return sorted(
        node.lineno for node in ast.walk(_tree_of(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in methods
    )


def _names_in(source: "str | ast.Module") -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree_of(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
    return names


def find_generate_calls_without_archive(
    repo_root: Path,
    scanned: Sequence[str],
    *,
    archive_names: Iterable[str],
    generate_methods: frozenset[str] = DEFAULT_GENERATE_METHODS,
) -> dict[str, list[int]]:
    """``{file: [lines]}`` for modules that call ``generate*`` and never name any of ``archive_names``.

    ``archive_names`` are the identifiers that mean "this goes through the archive" in the caller's repo: the
    wrapping factory, ``archive_provider``, the wrapped client's class. A module calling ``.generate`` on a
    provider it received from the wrapping factory names the factory; one that received it as a parameter
    belongs in ``allowed`` with the reason its caller wraps it.
    """
    markers = set(archive_names)
    found = {}
    for path, _source, tree in _parsed(repo_root, scanned):
        lines = _generate_call_lines(tree, generate_methods)
        if lines and not (markers & _names_in(tree)):
            found[_rel(path, repo_root)] = lines
    return found


def assert_every_llm_call_is_archived(
    repo_root: Path,
    scanned: Sequence[str],
    *,
    provider_classes: Iterable[str],
    wrapping_factories: Iterable[str],
    allowed: Mapping[str, str] = {},
    archive_names: Iterable[str] | None = None,
    sdk_methods: frozenset[tuple[str | None, str]] = DEFAULT_SDK_METHODS,
    unwrapping_factory_modules: Sequence[str] = ("pyutilz.llm",),
    factory_names: Sequence[str] = ("get_llm_provider",),
    min_files: int = 1,
) -> None:
    """Fail for any call path that escapes the archive, for any ``allowed`` entry that no longer escapes, for a
    scanned path that does not exist, for an unparsable file, and when fewer than *min_files* files parsed.

    ``allowed`` maps a repo-relative file to the reason its direct calls are archived anyway (it records
    them itself) or may bypass the archive. ``archive_names`` switches on the file-level ``generate*`` check.
    """
    blank = [f for f, reason in allowed.items() if not reason.strip()]
    if blank:
        raise AssertionError(f"every allowed entry needs a reason: {blank}")
    unparsed: list[str] = []
    try:
        parsed = _parsed(repo_root, scanned, unparsed)
    except CorpusError as exc:
        raise AssertionError(f"a scanned path does not exist, so nothing under it was checked: {exc}") from exc
    if len(parsed) < min_files:
        raise AssertionError(f"only {len(parsed)} file(s) parsed under {list(scanned)}; expected at least {min_files} -- the scan examined nothing")
    if unparsed:
        raise AssertionError("these files could not be parsed, so their LLM calls are unknown:\n  " + "\n  ".join(unparsed))
    problems: dict[str, dict[str, list[int]]] = {
        "direct model-SDK call": find_direct_sdk_calls(repo_root, scanned, sdk_methods=sdk_methods),
        "provider built outside the wrapping factory": find_unwrapped_providers(
            repo_root,
            scanned,
            provider_classes=provider_classes,
            wrapping_factories=wrapping_factories,
            unwrapping_factory_modules=unwrapping_factory_modules,
            factory_names=factory_names,
        ),
    }
    if archive_names is not None:
        problems["generate* call in a module that never names the archive"] = find_generate_calls_without_archive(
            repo_root, scanned, archive_names=archive_names
        )
    matched = {f for found in problems.values() for f in found}
    offenders = {kind: {f: lines for f, lines in found.items() if f not in allowed} for kind, found in problems.items()}
    offenders = {kind: found for kind, found in offenders.items() if found}
    stale = sorted(set(allowed) - matched)
    messages = []
    if offenders:
        messages.append(
            "these paths make a paid LLM call that nothing archives; route them through the wrapping factory, or "
            f"list the file in `allowed` with the reason its answer is kept: {offenders}"
        )
    if stale:
        messages.append(f"these allowed entries no longer match any call; drop them so the exemption cannot outlive its cause: {stale}")
    if messages:
        raise AssertionError("\n".join(messages))
