"""Opt-in: no module imports the standard-library ``json``; JSON goes through ``orjson`` (or a project shim over it).

stdlib ``json`` was measured 2-3x slower than ``orjson`` on a clinical knowledge-base rebuild's JSONL shape, in several
hot-path call sites nobody had flagged before profiling; the project then banned it everywhere, behind a shim with
``json``-compatible signatures. A single stray ``import json`` puts the slow parser back on a path nothing flags.

This is opt-in: a project that has adopted ``orjson`` calls it; nothing runs it by default. Flagged: ``import json``
(or ``json.decoder``, any alias), ``from json import ...`` (or ``json.*``), ``importlib.import_module("json")`` and
``__import__("json")``. A relative ``from . import json`` names the project's own module and is not flagged.

Exceptions are explicit: *allow* maps a root-relative path or glob (the shim itself, a call site that needs
``json.loads(strict=False)``, which orjson cannot do) to the reason it may import stdlib json. An entry with no reason
fails, and so does an entry that no longer matches any importing file. A line carrying ``# stdlib-json-ok`` is a
per-line opt-out.

Usage::

    from py_ci_shared.stdlib_json_ban import assert_no_stdlib_json

    def test_no_stdlib_json():
        assert_no_stdlib_json(REPO, allow={"autopsia/_json.py": "the orjson shim itself"})
"""

from __future__ import annotations

import ast
import fnmatch
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ImportAliases, ParsedFile, ScanResult
from ._gate_report import line_has_marker, report, scan_tree, skip_set

__all__ = ["RULE", "REFRESH_FLAG", "find_stdlib_json_imports", "assert_no_stdlib_json"]

RULE = "stdlib-json-import"
REFRESH_FLAG = "--refresh-stdlib-json-baseline"
MARKER = "stdlib-json-ok"


def _is_json(module: Optional[str]) -> bool:
    return module is not None and (module == "json" or module.startswith("json."))


def _imports(tree: ast.Module) -> list[tuple[int, str]]:
    aliases = ImportAliases.from_tree(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, f"import {a.name}" + (f" as {a.asname}" if a.asname else "")) for a in node.names if _is_json(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _is_json(node.module):
            out.append((node.lineno, f"from {node.module} import {', '.join(a.name for a in node.names)}"))
        elif isinstance(node, ast.Call) and node.args:
            name = aliases.qualified_name(node) or ""
            arg = node.args[0]
            if name in ("importlib.import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _is_json(arg.value):
                out.append((node.lineno, f"{name}({arg.value!r})"))
    return sorted(out)


def _allowed(rel: str, allow: Mapping[str, str]) -> Optional[str]:
    return next((pattern for pattern in allow if rel == pattern or fnmatch.fnmatchcase(rel, pattern)), None)


def _scan_findings(scan: ScanResult, allow: Mapping[str, str]) -> tuple[list[Finding], set[str]]:
    findings: list[Finding] = []
    used: set[str] = set()
    for parsed in scan:
        hits = _file_hits(parsed)
        pattern = _allowed(parsed.rel, allow)
        if pattern is not None and hits:
            used.add(pattern)
            continue
        findings += [Finding(parsed.rel, line, RULE, f"imports stdlib json: `{text}`") for line, text in hits]
    return findings, used


def _file_hits(parsed: ParsedFile) -> list[tuple[int, str]]:
    lines = parsed.source.splitlines()
    return [(line, text) for line, text in _imports(parsed.tree) if not line_has_marker(lines, line, MARKER)]


def _collect(
    root: Union[str, Path], *, allow: Mapping[str, str], skip_dir_names: Iterable[str], include_tests: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult, list[str]]:
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    findings, used = _scan_findings(scan, allow)
    problems = sorted(f"allow entry {p!r} has no reason" for p, reason in allow.items() if not str(reason).strip())
    problems += sorted(f"allow entry {p!r} no longer matches a file that imports stdlib json; remove it" for p in allow if p not in used)
    return sorted(findings, key=lambda f: (f.path, f.line)), scan, problems


def find_stdlib_json_imports(
    root: Union[str, Path],
    *,
    allow: Optional[Mapping[str, str]] = None,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = True,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every stdlib ``json`` import under *root* outside *allow*, plus one ``unparsed-file`` finding per unparsable file."""
    findings, scan, _ = _collect(root, allow=allow or {}, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    return findings + scan.unparsed_findings()


def assert_no_stdlib_json(
    root: Union[str, Path],
    *,
    allow: Optional[Mapping[str, str]] = None,
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = True,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a stdlib ``json`` import (new against *baseline_path* when given), on an *allow* entry without a reason
    or matching nothing, on fewer than *min_files* parsed files, and on any unparsable file."""
    import pytest

    findings, scan, problems = _collect(root, allow=allow or {}, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git)
    if problems:
        pytest.fail("stdlib-json-ban:\n    " + "\n    ".join(problems), pytrace=False)
    report(
        findings,
        gate="stdlib-json-ban",
        flag=REFRESH_FLAG,
        guidance="use orjson (or the project's orjson-backed shim) instead of stdlib json",
        parsed_count=scan.parsed_count,
        problems=scan.unparsed,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
