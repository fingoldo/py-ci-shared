"""Scan a Python corpus once: parsed files, unparsed files as findings, and a floor on what PARSED.

Before this module a gate's floor (``min_files``) counted the files it was GIVEN, so a corpus where every file
failed to parse still met the floor and passed with zero checks run (audit 2026-09-24 systemic item 2). Here the
floor counts parsed files, and every unreadable/unparsable file is kept in ``ScanResult.unparsed`` for the gate
to report.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .corpus import DEFAULT_EXCLUDE, iter_files, relative_posix
from .errors import CorpusError, EmptyScanError, SourceError, UnparsedFilesError
from .findings import UNPARSED_RULE, Finding
from .source import parse_source

PathLike = Union[str, "os.PathLike[str]"]


@dataclass(frozen=True)
class ParsedFile:
    path: Path
    rel: str
    tree: ast.Module
    source: str

    def __iter__(self) -> Iterator[object]:  # allows ``for path, tree, source in result``-style unpacking
        return iter((self.path, self.tree, self.source))


@dataclass(frozen=True)
class SourceProblem:
    """A file that could not be read or parsed. ``kind`` is ``"unreadable"`` or ``"unparsable"``."""

    path: Path
    rel: str
    line: int
    kind: str
    message: str

    def to_finding(self, rule: str = UNPARSED_RULE) -> Finding:
        return Finding(self.rel, self.line, rule, f"{self.kind}: {self.message}")

    def render(self) -> str:
        return f"{self.rel}:{self.line}: {self.kind}: {self.message}"


@dataclass
class ScanResult:
    root: Optional[Path]
    files: list[ParsedFile] = field(default_factory=list)
    unparsed: list[SourceProblem] = field(default_factory=list)
    min_files: int = 1

    def __iter__(self) -> Iterator[ParsedFile]:
        return iter(self.files)

    @property
    def parsed_count(self) -> int:
        return len(self.files)

    def unparsed_findings(self, rule: str = UNPARSED_RULE) -> list[Finding]:
        return [p.to_finding(rule) for p in self.unparsed]

    def check_floor(self) -> None:
        """Raise :class:`EmptyScanError` when fewer than ``min_files`` files PARSED."""
        if self.parsed_count < self.min_files:
            where = f" under {self.root}" if self.root is not None else ""
            extra = f" ({len(self.unparsed)} more could not be parsed)" if self.unparsed else ""
            raise EmptyScanError(
                f"only {self.parsed_count} file(s) parsed{where}{extra}; expected at least {self.min_files}. "
                "The gate is not reaching the code (wrong root, over-broad exclude, or unparsable files)."
            )

    def check_unparsed(self) -> None:
        """Raise :class:`UnparsedFilesError` listing every file the gate could not check."""
        if self.unparsed:
            raise UnparsedFilesError(
                f"{len(self.unparsed)} file(s) could not be read or parsed, so no gate can vouch for them. "
                "Fix the file (or the interpreter version running the gate):\n  " + "\n  ".join(p.render() for p in self.unparsed)
            )

    def assert_ok(self, *, allow_unparsed: bool = False) -> None:
        """The floor, then (unless *allow_unparsed*) the unparsed list."""
        self.check_floor()
        if not allow_unparsed:
            self.check_unparsed()


def _problem(exc: SourceError, rel: str) -> SourceProblem:
    return SourceProblem(exc.path, rel, exc.line or 1, exc.kind, exc.message)


def scan_python(
    files_or_root: Union[PathLike, Iterable[PathLike]],
    *,
    min_files: int = 1,
    root: Optional[PathLike] = None,
    patterns: Sequence[str] = ("*.py",),
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
    include_untracked: bool = True,
    use_git: Optional[bool] = None,
) -> ScanResult:
    """Parse every Python file in a corpus.

    *files_or_root* is a directory (enumerated with :func:`iter_files`), or an iterable whose entries are files or
    directories; each directory entry is enumerated with :func:`iter_files` under the same *patterns* and *exclude*.
    A missing entry raises :class:`CorpusError`. *root* is what ``rel`` paths are relative to: defaults to the
    directory (for a directory entry of an iterable, to that entry, so a list of directories reports the same paths
    as one call per directory); an explicit file with no *root* gets its absolute POSIX path. This function does
    NOT raise on the floor; call ``result.check_floor()``/``assert_ok()`` (a gate usually wants to report its
    findings and the floor together).
    """
    rel_root: Optional[Path] = Path(root) if root is not None else None
    pairs: list[tuple[Path, Optional[Path]]] = []
    if isinstance(files_or_root, (str, os.PathLike)):
        base = Path(files_or_root)
        if rel_root is None:
            rel_root = base
        pairs = [(p, rel_root) for p in iter_files(base, patterns, exclude=exclude, include_untracked=include_untracked, use_git=use_git)]
    else:
        seen: set[Path] = set()
        for entry in (Path(e) for e in files_or_root):
            new_pairs: list[tuple[Path, Optional[Path]]]
            if entry.is_dir():
                entry_root = rel_root if rel_root is not None else entry
                found = iter_files(entry, patterns, exclude=exclude, include_untracked=include_untracked, use_git=use_git)
                new_pairs = [(p, entry_root) for p in found]
            elif entry.exists():
                new_pairs = [(entry, rel_root)]
            else:
                raise CorpusError(f"corpus entry does not exist: {entry}")
            for found_path, found_base in new_pairs:
                if found_path not in seen:
                    seen.add(found_path)
                    pairs.append((found_path, found_base))
        pairs.sort(key=lambda pr: pr[0])
    result = ScanResult(root=rel_root, min_files=min_files)
    for path, rel_base in pairs:
        item = _parse_one(path, relative_posix(path, rel_base))
        if isinstance(item, ParsedFile):
            result.files.append(item)
        else:
            result.unparsed.append(item)
    return result


def _parse_one(path: Path, rel: str) -> Union[ParsedFile, SourceProblem]:
    try:
        source, tree = parse_source(path)
    except SourceError as exc:
        return _problem(exc, rel)
    return ParsedFile(path, rel, tree, source)
