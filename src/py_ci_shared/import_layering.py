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

Deliberately regex-based, no language parser: an import line is one of a handful of shapes in every
language this account ships (Dart ``import '...'``, Python ``from x import``, TS ``from '...'``).

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

import fnmatch
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

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

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file() or path.suffix not in source_suffixes:
            continue
        rel = path.relative_to(repo_root).as_posix()
        if "/.dart_tool/" in f"/{rel}" or "/node_modules/" in f"/{rel}" or "/build/" in f"/{rel}":
            continue
        applicable = [r for r in rules if fnmatch.fnmatch(rel, r.from_glob)]
        if not applicable:
            continue
        examined += 1
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, start=1):
            raw = None
            for pattern in _IMPORT_RES:
                m = pattern.search(line)
                if m:
                    raw = m.group(1)
                    break
            if raw is None:
                continue
            target = _repo_relative_target(path, raw, repo_root, package_roots)
            if target is None:
                continue
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
