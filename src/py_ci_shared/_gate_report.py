"""Shared plumbing for the small single-rule gates: corpus scanning, text corpora, and the fail-or-ratchet report.

Every gate built on this module reports the same way: a floor on PARSED (or read) files, every unparsable or
unreadable file as a failure, then its findings either raw (no baseline) or against a :class:`Baseline` that
must exist, is a multiset, and is rewritten only on an explicit refresh.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import (
    DEFAULT_EXCLUDE,
    Baseline,
    Finding,
    ScanResult,
    SourceError,
    SourceProblem,
    iter_files,
    read_source,
    refresh_requested,
    relative_posix,
    scan_python,
)

PathLike = Union[str, "os.PathLike[str]"]

__all__ = ["TextFile", "TextCorpus", "scan_tree", "read_text_corpus", "report", "enclosing_functions", "line_has_marker", "skip_set"]

#: Directory names that hold tests. Gates whose literals are legitimately the SUBJECT of a test skip these by default.
TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test", "testing"})


def skip_set(skip_dir_names: Iterable[str] = (), *, include_tests: bool = True) -> frozenset[str]:
    """``DEFAULT_EXCLUDE`` plus the caller's names, plus the test directory names unless *include_tests*."""
    extra = frozenset(skip_dir_names)
    return DEFAULT_EXCLUDE | extra | (frozenset() if include_tests else TEST_DIR_NAMES)


def scan_tree(root: PathLike, *, skip: Iterable[str], use_git: Optional[bool] = None, patterns: Sequence[str] = ("*.py",)) -> ScanResult:
    """``scan_python`` over *root*, skipping *skip* directory names, and also skipping ``test_*.py`` files when the
    caller excluded the test directories (a test module living next to the code is still a test)."""
    skip = frozenset(skip)
    scan = scan_python(root, min_files=0, patterns=patterns, exclude=skip, use_git=use_git)
    if TEST_DIR_NAMES <= skip:
        scan.files = [f for f in scan.files if not _is_test_file(f.rel)]
        scan.unparsed = [p for p in scan.unparsed if not _is_test_file(p.rel)]
    return scan


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


@dataclass(frozen=True)
class TextFile:
    path: Path
    rel: str
    text: str


@dataclass
class TextCorpus:
    """Non-Python files read as text: what was read, and what could not be."""

    root: Path
    files: list[TextFile] = field(default_factory=list)
    unreadable: list[SourceProblem] = field(default_factory=list)


def read_text_corpus(root: PathLike, *, patterns: Sequence[str], skip: Iterable[str], use_git: Optional[bool] = None) -> TextCorpus:
    """Every file under *root* matching *patterns*, decoded with the BOM stripped; undecodable files are kept as problems."""
    base = Path(root)
    corpus = TextCorpus(base)
    for path in iter_files(base, patterns, exclude=frozenset(skip), use_git=use_git):
        rel = relative_posix(path, base)
        try:
            corpus.files.append(TextFile(path, rel, read_source(path)))
        except SourceError as exc:
            corpus.unreadable.append(SourceProblem(exc.path, rel, exc.line or 1, exc.kind, exc.message))
    return corpus


def line_has_marker(source_lines: Sequence[str], line: int, marker: str) -> bool:
    """True when the 1-based *line* of the source carries ``# <marker>`` (an explicit, reviewable opt-out)."""
    if not marker or not 0 < line <= len(source_lines):
        return False
    text = source_lines[line - 1]
    return "#" in text and marker in text[text.index("#") :]


def enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """``{id(node): qualified function name}`` for every node inside a function (innermost function wins)."""
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = f"{name}.{child.name}" if name else child.name
                out[id(child)] = name or "<module>"
                visit(child, inner)
            elif isinstance(child, ast.ClassDef):
                out[id(child)] = name or "<module>"
                visit(child, f"{name}.{child.name}" if name else child.name)
            else:
                if name:
                    out[id(child)] = name
                visit(child, name)

    visit(tree, "")
    return out


def report(
    findings: Sequence[Finding],
    *,
    gate: str,
    flag: str,
    guidance: str,
    parsed_count: int,
    problems: Sequence[SourceProblem] = (),
    min_files: int = 1,
    root: object = None,
    baseline_path: Optional[PathLike] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    allow_unparsed: bool = False,
) -> None:
    """Fail on a broken walk, then on findings (raw, or new against *baseline_path*).

    The floor counts files that were actually read and parsed; an unparsable file fails unless *allow_unparsed*.
    A baseline is never written from a broken walk. *flag* is the refresh flag, e.g. ``--refresh-x-baseline``.
    """
    import pytest

    broken: list[str] = []
    if parsed_count < min_files:
        where = f" under {root}" if root is not None else ""
        broken.append(f"{gate}: only {parsed_count} file(s) parsed{where}; expected at least {min_files}. The gate is not reaching the code.")
    if problems and not allow_unparsed:
        broken.append(
            f"{gate}: {len(problems)} file(s) could not be read or parsed, so nothing vouches for them:\n    " + "\n    ".join(p.render() for p in problems)
        )
    if baseline_path is None:
        if findings:
            broken.append(f"{gate}: {len(findings)} finding(s). {guidance}\n    " + "\n    ".join(f.render() for f in findings))
        if broken:
            pytest.fail("\n".join(broken), pytrace=False)
        return
    if broken:
        pytest.fail("\n".join(broken), pytrace=False)
    short = flag.lstrip("-")
    if short.startswith("refresh-"):
        short = short[len("refresh-") :]
    if short.endswith("-baseline"):
        short = short[: -len("-baseline")]
    do_refresh = refresh if refresh is not None else refresh_requested(flag, request)
    baseline = Baseline(baseline_path, gate=gate, refresh_command=f"pytest {flag} (or PY_CI_SHARED_REFRESH={short})")
    baseline.enforce(findings, refresh=do_refresh, guidance=guidance, request=request).raise_for_pytest()
