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

from ._core import DEFAULT_EXCLUDE, SourceReadError, iter_files, read_source, relative_posix

_FLAG = re.compile(r"`(--[a-z][a-z0-9-]{2,})`")
_IDENT = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
DEFAULT_CORPUS_SUFFIXES: tuple[str, ...] = (".py", ".pyi", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sql", ".txt", ".json", ".sh", ".ps1")
DEFAULT_EXTERNAL_COMMANDS: frozenset[str] = frozenset(
    {"pip", "pip3", "uv", "uvx", "pipx", "git", "gh", "npm", "npx", "yarn", "docker", "conda", "brew", "apt", "apt-get", "curl", "wget"}
)
_PYTHONS = frozenset({"python", "python3", "py"})
#: ``<runner> [options] <program> ...``: the runner's own flags are external, the program decides the rest.
_RUNNERS: dict[tuple[str, ...], bool] = {
    ("uv", "run"): False,
    ("poetry", "run"): False,
    ("pdm", "run"): False,
    ("hatch", "run"): False,
    ("uvx",): True,
    ("npx",): True,
    ("pipx", "run"): True,
}
#: Runner options that take a separate value (``uv run --with rich python x.py``).
_RUNNER_VALUE_OPTIONS = frozenset(
    {"--with", "--python", "-p", "--extra", "--group", "--directory", "--project", "--package", "--from", "--env-file", "--index", "--spec"}
)


def _without_html_comments(text: str) -> str:
    """*text* with every HTML comment blanked, newlines kept so line numbers still match the file."""
    return _HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _program(words: list[str]) -> tuple[str, bool, int]:
    """``(program, runs_a_tool, index of the program word)`` for a command line, looking through runners.

    ``uv run python tool.py --x`` runs the project's own ``tool.py``, so ``--x`` is ours; ``uvx ruff --fix`` and
    ``npx prettier --write`` run a separately installed tool, whose flags are not. ``python -m pip`` is pip.
    """
    i = 0
    tool = False
    for runner, runs_tool in _RUNNERS.items():
        if tuple(words[: len(runner)]) == runner:
            i = len(runner)
            tool = runs_tool
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in _RUNNER_VALUE_OPTIONS else 1
            break
    if i >= len(words):
        return (words[0] if words else ""), False, 0
    program = words[i]
    if program in _PYTHONS and i + 2 < len(words) and words[i + 1] == "-m":
        return words[i + 2], False, i + 2
    return program, tool, i


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
        program, runs_tool, at = _program(words)
        if at > 0 and not (runs_tool or program in external):
            flags.update(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})", " ".join(words[:at])))  # the runner's own flags
        elif runs_tool or program in external:
            flags.update(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]{2,})", line))
    return flags


def default_corpus_files(repo_root: Path) -> list[Path]:
    """Every source or config file git would commit under *repo_root*: tracked, plus untracked-but-not-ignored.

    An ``rglob`` read every gitignored file too, and glossum keeps an 861 MB Wiktextract dump under a
    gitignored ``data/``: joining it into the corpus raised MemoryError on every run. Ignored data is not
    where a document's identifiers are defined. Untracked files still count, so a new module is found
    before it is added. The listing is ``git ls-files -z`` (non-ASCII names are not quoted away) through
    ``_core.iter_files``, which falls back to a pruned walk when git is unavailable.
    """
    return iter_files(repo_root, tuple(f"*{suffix}" for suffix in DEFAULT_CORPUS_SUFFIXES), exclude=DEFAULT_EXCLUDE)


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

    *doc_files* defaults to the tracked Markdown; *corpus_files* to :func:`default_corpus_files`.
    *exclude_docs* are repo-relative POSIX paths or folder prefixes ending in ``/``.
    """
    root = repo_root.resolve()
    docs = list(doc_files) if doc_files is not None else tracked_markdown_files(root)
    if corpus_files is None:
        corpus_files = default_corpus_files(root)
    corpus = "\n".join(p.read_text(encoding="utf-8-sig", errors="replace") for p in corpus_files)
    skipped = set(exclude_docs)
    ignored = set(ignore)
    problems: list[str] = []
    for doc in docs:
        rel = relative_posix(doc.resolve(), root)
        if rel in skipped or any(rel.startswith(s) for s in skipped if s.endswith("/")):
            continue
        try:
            text = read_source(doc)
        except SourceReadError as exc:
            problems.append(f"{rel}:{exc.line or 1}: unreadable, so its names were not checked: {exc.message}")
            continue
        lines = _without_html_comments(text).split("\n")
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
