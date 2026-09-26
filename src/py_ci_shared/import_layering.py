"""Shared check: declared architectural layers are not violated by an import.

``scan_import_cycles``-style tooling proves there is no cycle. It does not prove that the layer
which is supposed to be *reusable* stays reusable. A package extracted "for reuse" that imports the
product's own providers and screens is only reusable in the product it came from, and the fact is
invisible until someone tries the second consumer.

Found on 2026-09-02 as glossum P07-5: twelve files under ``lib/core/landing/`` -- the directory whose
whole purpose is to be product-neutral -- imported ``../../providers/theme_provider.dart`` and
``../../screens/landing/widgets/language_selector.dart``. The same round found the check that was
supposed to enforce this (``check-shared-purity.sh``) referenced in two documents and absent from
the repository (C02-8, C03-19), which is its own lesson: a rule nobody can run is a rule nobody has.

A rule is ``from_glob !-> to_glob`` with an optional reason and an allowlist of specific files. Both
relative (``../../providers/x.dart``) and absolute (``package:app/providers/x.dart``) imports are
resolved to a repo-relative path, so a rule cannot be evaded by changing import style.

Dart/TS/JS import lines are read with regexes after comments are removed (an import named in a ``//`` or
``/* */`` comment is not an import). Python files are read with ``ast``, since a Python import names a MODULE, not a
path: ``from ..providers import p`` and ``from pkg.providers import p`` are resolved to the repo-relative file.

Usage::

    from py_ci_shared.import_layering import LayerRule, assert_layering

    def test_core_does_not_import_the_product():
        assert_layering(
            REPO,
            rules=[
                LayerRule("lib/core/**", ["lib/providers/**", "lib/screens/**"],
                          reason="core/ is the product-neutral layer other apps consume"),
            ],
        )
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ._core import DEFAULT_EXCLUDE, SourceError, iter_files, parse_source, read_source, relative_posix
from ._core.node_index import walk as _fast_walk

# Dart: `import '...'` / `export '...'`; TS/JS: `import {x} from '...'`, `export * from '...'`,
# `require('...')`; Python-style `from x import y` is matched by the same `from` form when the
# module is quoted (a bare Python module name is not a path and is skipped downstream anyway).
_IMPORT_RES = (
    re.compile(r"""^\s*(?:import|export)\s+['"]([^'"]+)['"]"""),
    re.compile(r"""\bfrom\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\(['"]([^'"]+)['"]\)"""),
)
_SOURCE_SUFFIXES = (".dart", ".py", ".ts", ".tsx", ".js", ".mjs")


@dataclass(frozen=True)
class LayerRule:
    """``from_glob`` must not import anything matching one of ``forbidden``."""

    from_glob: str
    forbidden: Sequence[str]
    reason: str = ""
    allow_files: Sequence[str] = field(default_factory=tuple)
    advisory: bool = False


def _repo_relative_target(importer: Path, raw: str, repo_root: Path, package_roots: dict[str, str]) -> "str | None":
    """Resolve an import string to a repo-relative POSIX path, or None when it is external."""
    if raw.startswith("dart:") or raw.startswith("http"):
        return None
    if raw.startswith("package:"):
        rest = raw[len("package:") :]
        pkg, _, tail = rest.partition("/")
        root = package_roots.get(pkg)
        if root is None:
            return None
        return f"{root}/{tail}"
    if raw.startswith("."):
        try:
            resolved = (importer.parent / raw).resolve()
            return resolved.relative_to(repo_root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
    if "/" in raw and not raw.startswith("/"):
        # A bare relative path (Dart allows `import 'widgets/x.dart';` inside the same directory).
        candidate = importer.parent / raw
        if candidate.exists():
            try:
                return candidate.resolve().relative_to(repo_root.resolve()).as_posix()
            except (ValueError, OSError):
                return None
    return None


def _strip_comments(text: str) -> str:
    """*text* with ``//`` line comments and ``/* */`` block comments removed outside string literals; newlines kept."""
    out: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote or ch == "\n":
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            stop = n if end == -1 else end + 2
            out.append("\n" * text.count("\n", i, stop))
            i = stop
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _python_module_file(module: str, base_dirs: Sequence[Path]) -> "Path | None":
    """The file a dotted *module* names under the first base dir holding it (``a/b.py`` or ``a/b/__init__.py``)."""
    parts = [part for part in module.split(".") if part]
    for base in base_dirs:
        for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
            if parts and candidate.is_file():
                return candidate
    return None


def _python_targets(path: Path, repo_root: Path, package_roots: "dict[str, str]") -> Iterator[tuple[int, str]]:
    """``(line, repo-relative target)`` for every import in a Python file that resolves inside the repository: the
    submodule an imported name is, when it is one, else the module imported from. Raises ``SourceError``."""
    _source, tree = parse_source(path)
    for node in _fast_walk(tree):
        if isinstance(node, ast.Import):
            requests: list[tuple[int, str, list[str]]] = [(0, alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            requests = [(node.level, node.module or "", [alias.name for alias in node.names if alias.name != "*"])]
        else:
            continue
        seen: set[str] = set()
        for level, module, names in requests:
            if level:
                anchor = path.parent
                for _ in range(level - 1):
                    anchor = anchor.parent
                bases = [anchor]
            else:
                head, _, rest = module.partition(".")
                if head in package_roots:
                    bases = [repo_root / package_roots[head]]
                    module = rest
                else:
                    bases = [repo_root, repo_root / "src"]
            found = [f for f in (_python_module_file(f"{module}.{n}" if module else n, bases) for n in names) if f is not None]
            if not found:
                own = _python_module_file(module, bases) if module else next((b / "__init__.py" for b in bases if (b / "__init__.py").is_file()), None)
                if own is None and not level and module and not any((b / module.split(".")[0]).exists() for b in bases):
                    continue  # a third-party or stdlib module: not a path in this repository
                found = [own if own is not None else bases[0].joinpath(*module.split(".")).with_suffix(".py")]
            for target in found:
                rel = relative_posix(target, repo_root)
                if rel not in seen:
                    seen.add(rel)
                    yield node.lineno, rel


def _text_targets(path: Path, repo_root: Path, package_roots: "dict[str, str]") -> Iterator[tuple[int, str]]:
    text = _strip_comments(read_source(path))
    for i, line in enumerate(text.splitlines(), start=1):
        raw = None
        for pattern in _IMPORT_RES:
            m = pattern.search(line)
            if m:
                raw = m.group(1)
                break
        if raw is None:
            continue
        target = _repo_relative_target(path, raw, repo_root, package_roots)
        if target is not None:
            yield i, target


def find_layering_violations(
    repo_root: Path,
    rules: Iterable[LayerRule],
    *,
    package_roots: "dict[str, str] | None" = None,
    source_suffixes: Sequence[str] = _SOURCE_SUFFIXES,
) -> list[str]:
    """Return one problem string per import that crosses a declared layer boundary.

    ``package_roots`` maps a package name to the repo-relative directory its ``package:`` imports
    resolve to (e.g. ``{"glossum": "lib"}``), so ``package:glossum/providers/x.dart`` is checked by
    the same rule as ``../../providers/x.dart``.
    """
    package_roots = dict(package_roots or {})
    rules = list(rules)
    problems: list[str] = []
    examined = 0

    for path in iter_files(repo_root, tuple(f"*{suffix}" for suffix in source_suffixes), exclude=DEFAULT_EXCLUDE | {".dart_tool"}):
        rel = relative_posix(path, repo_root)
        applicable = [r for r in rules if fnmatch.fnmatch(rel, r.from_glob)]
        if not applicable:
            continue
        examined += 1
        try:
            targets = list(_python_targets(path, repo_root, package_roots) if path.suffix == ".py" else _text_targets(path, repo_root, package_roots))
        except SourceError as exc:
            problems.append(
                f"{rel}:{exc.line or 1}: cannot be {'parsed' if exc.kind == 'unparsable' else 'read'}, so its imports were not checked: {exc.message}"
            )
            continue
        for i, target in targets:
            for rule in applicable:
                if rel in rule.allow_files:
                    continue
                for forbidden in rule.forbidden:
                    if fnmatch.fnmatch(target, forbidden):
                        tag = "ADVISORY " if rule.advisory else ""
                        because = f" ({rule.reason})" if rule.reason else ""
                        problems.append(f"{tag}{rel}:{i}: imports {target} - {rule.from_glob} must not depend " f"on {forbidden}{because}.")
                        break
    if rules and examined == 0:
        problems.append(
            f"no source file under {repo_root} matched any rule's from_glob "
            f"({[r.from_glob for r in rules]}) - this check examined nothing, which reads as a pass."
        )
    return problems


def assert_layering(
    repo_root: Path,
    rules: Iterable[LayerRule],
    *,
    package_roots: "dict[str, str] | None" = None,
) -> None:
    """Fail on any non-advisory layering violation; print advisory ones."""
    import pytest

    problems = find_layering_violations(repo_root, rules, package_roots=package_roots)
    blocking = [p for p in problems if not p.startswith("ADVISORY ")]
    for p in problems:
        if p.startswith("ADVISORY "):
            print(p)
    if blocking:
        pytest.fail(f"{len(blocking)} layering violation(s):\n  " + "\n  ".join(blocking))
