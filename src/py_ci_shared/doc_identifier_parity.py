"""Shared check: a flag or identifier a document names in backticks must exist somewhere in the code.

A README that tells the reader to pass ``--grammar`` when the script only declares ``--no-grammar``, or names a
``feature_grammar_annotations`` setting that nothing defines, reads as authoritative and is wrong. glossum's
2026-09-01 audit (12-H2, 12-L1, 12-L7, 12-L8, 12-M3, 12-L6) found exactly these, each a backticked token in a
document with no occurrence anywhere in the source.

Two token shapes, both backticked, so prose in code font does not qualify:

* a long flag, ``--word`` with at least three characters after the dashes;
* a snake_case identifier with at least two underscores.

A token is reported when it occurs in none of the corpus files (source, config, tests) as a substring. Substring,
not word boundary, on purpose: a near miss is reported only when the cited string is genuinely absent.

Fenced code blocks are read (that is where command examples live); HTML comments are stripped (audit annotations
live there). Three things are not reported:

* documents the caller excludes: a changelog names symbols that have since been renamed, and a design document
  names fields that do not exist yet;
* a flag the document itself shows belonging to another tool: when a fenced line in the same document starts with
  an external command (pip, uv, git, ...) and carries the flag, the flag is that tool's for the whole document;
* anything under a caller-supplied ignore list.

Usage::

    from py_ci_shared.doc_identifier_parity import assert_doc_identifiers_exist

    def test_docs_name_real_flags_and_identifiers():
        assert_doc_identifiers_exist(REPO, exclude_docs={"CHANGELOG.md"})
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from py_ci_shared.phantom_markdown_links import tracked_markdown_files

_FLAG = re.compile(r"`(--[a-z][a-z0-9-]{2,})`")
_IDENT = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
DEFAULT_CORPUS_SUFFIXES: tuple[str, ...] = (".py", ".pyi", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sql", ".txt", ".json", ".sh", ".ps1")
DEFAULT_EXTERNAL_COMMANDS: frozenset[str] = frozenset(
    {"pip", "pip3", "uv", "uvx", "pipx", "git", "gh", "npm", "npx", "yarn", "docker", "conda", "brew", "apt", "apt-get", "curl", "wget"}
)


def _without_html_comments(text: str) -> str:
    """*text* with every HTML comment blanked, newlines kept so line numbers still match the file."""
    return _HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _external_flags(lines: list[str], external: frozenset[str]) -> set[str]:
    """Flags this document shows on a fenced line that runs an external command (``pip install --x``)."""
    flags: set[str] = set()
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            continue
        words = line.strip().lstrip("$> ").split()
        head = words[1] if len(words) > 2 and words[0] in {"python", "python3", "py"} and words[1] == "-m" else None
        command = words[2] if head == "-m" else (words[0] if words else "")
        if command in external:
            flags.update(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})", line))
    return flags


def find_absent_doc_identifiers(
    repo_root: Path,
    *,
    doc_files: Iterable[Path] | None = None,
    corpus_files: Iterable[Path] | None = None,
    exclude_docs: Iterable[str] = (),
    ignore: Iterable[str] = (),
    external_commands: frozenset[str] = DEFAULT_EXTERNAL_COMMANDS,
) -> list[str]:
    """``doc:line: `token` ...`` for each backticked flag or identifier that occurs in no corpus file.

    *doc_files* defaults to the tracked Markdown; *corpus_files* to every file under *repo_root* with a source or
    config suffix, outside hidden and virtualenv folders. *exclude_docs* are repo-relative POSIX paths or folder
    prefixes ending in ``/``.
    """
    root = repo_root.resolve()
    docs = list(doc_files) if doc_files is not None else tracked_markdown_files(root)
    if corpus_files is None:
        corpus_files = [
            p
            for p in root.rglob("*")
            if p.suffix in DEFAULT_CORPUS_SUFFIXES
            and p.is_file()
            and not any(part.startswith(".") or part in {"node_modules", "venv", "__pycache__"} for part in p.relative_to(root).parts)
        ]
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in corpus_files)
    skipped = set(exclude_docs)
    ignored = set(ignore)
    problems: list[str] = []
    for doc in docs:
        rel = doc.resolve().relative_to(root).as_posix()
        if rel in skipped or any(rel.startswith(s) for s in skipped if s.endswith("/")):
            continue
        lines = _without_html_comments(doc.read_text(encoding="utf-8", errors="replace")).split("\n")
        foreign = _external_flags(lines, external_commands)
        for lineno, line in enumerate(lines, start=1):
            for token in _FLAG.findall(line) + _IDENT.findall(line):
                if token in ignored or token in foreign or token in corpus:
                    continue
                problems.append(f"{rel}:{lineno}: `{token}` occurs nowhere in the source, config or tests")
    return problems


def assert_doc_identifiers_exist(repo_root: Path, **kwargs: object) -> None:
    """Fail on any backticked flag or identifier a document names that the code does not contain."""
    import pytest

    problems = find_absent_doc_identifiers(repo_root, **kwargs)  # type: ignore[arg-type]
    if problems:
        pytest.fail(f"{len(problems)} name(s) in the docs that the code does not contain:\n  " + "\n  ".join(problems))
