"""A comment that names a repo path under a tooling root (``tool/``, ``scripts/``, ``.githooks/``...) names one that exists.

A Flutter app's ``pubspec.yaml`` said ``# bumped by tool/check-core-pin.sh``; the script had never been committed under
that name, so a reader looking for the guard found nothing and nobody noticed for months. A path in a comment is a claim
a machine can check: does ``<repo root>/<path>`` exist?

What is read as a comment:

* ``#`` lines (whole-line, after indentation; a ``#!`` shebang is skipped) in ``.sh``/``.bash``/``.yaml``/``.yml``/
  ``.toml``/``.cfg``/``.ini`` files, ``Dockerfile*`` and extensionless files under a tooling root that start with ``#!``;
* ``//`` and ``///`` lines in ``.dart``/``.js``/``.ts`` files;
* Python comment tokens and bare string statements (docstrings), read through the tokenizer and ``ast``, so a ``#``
  inside a string is not a comment. A ``.py`` file that does not parse is reported, never skipped.

A path token starts at a tooling root (:data:`DEFAULT_ROOTS`) not preceded by a path character, e.g. ``tool/x.sh`` but
not ``lib/tool/x.sh``; trailing ``.``, ``,``, ``:``, ``-`` are stripped. A token followed by a glob or template character
(``*``, ``<``, ``{``, ``$``) is a pattern, not a path, and is skipped, and so is a line in the past tense about a file
("was", "used to", "renamed", "removed", "formerly", "deleted", "retired", "no longer"): history may name what is gone.

Usage in a consumer's meta test::

    from py_ci_shared.comment_repo_paths import assert_comment_repo_paths

    def test_comment_repo_paths():
        assert_comment_repo_paths(REPO_ROOT)
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ParsedFile, ScanResult, SourceError, SourceProblem, iter_files, parse_source, read_source, relative_posix
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings

__all__ = ["DEFAULT_ROOTS", "REFRESH_FLAG", "RULE", "assert_comment_repo_paths", "find_comment_repo_path_problems"]

RULE = "comment-repo-path"
GATE = "comment_repo_paths"
REFRESH_FLAG = "--refresh-comment-repo-paths-baseline"
DEFAULT_ROOTS: tuple[str, ...] = ("tool", "tools", "scripts", ".githooks", ".github/scripts")
_HASH_SUFFIXES = frozenset({".sh", ".bash", ".yaml", ".yml", ".toml", ".cfg", ".ini"})
_SLASH_SUFFIXES = frozenset({".dart", ".js", ".ts"})
_PATTERNS = ("*.py", "*.sh", "*.bash", "*.yaml", "*.yml", "*.toml", "*.cfg", "*.ini", "Dockerfile*", "*.dart", "*.js", "*.ts")
#: Directories a Flutter/Node/Python checkout generates; never a source of claims.
_EXCLUDE = DEFAULT_EXCLUDE | frozenset({".dart_tool", ".pub-cache", "env", ".env"})
_HISTORICAL = re.compile(r"\b(?:was|used\s+to|renamed|removed|formerly|deleted|retired|no\s+longer)\b", re.IGNORECASE)
_PATTERN_NEXT = frozenset("*<{$")
_STRIP = ".,:;-"
_EMPTY_TREE = ast.Module(body=[], type_ignores=[])


def _token_re(roots: Sequence[str]) -> "re.Pattern[str]":
    alts = "|".join(re.escape(r.strip("/")) for r in sorted(roots, key=len, reverse=True))
    return re.compile(r"(?<![\w/.$-])(?:" + alts + r")/[\w.-]+(?:/[\w.-]+)*")


def _python_comments(source: str, tree: ast.Module) -> list[tuple[int, str]]:
    lines = source.splitlines()
    out: dict[int, str] = {}
    for node in _fast_walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                out[lineno] = lines[lineno - 1] if lineno - 1 < len(lines) else ""
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            out.setdefault(tok.start[0], tok.string)
    return sorted(out.items())


def _text_comments(source: str, markers: tuple[str, ...]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(markers) and not stripped.startswith("#!"):
            out.append((lineno, stripped))
    return out


def _markers(path: Path, rel: str, source: str, roots: Sequence[str]) -> Optional[tuple[str, ...]]:
    """The comment markers of *path*'s language, or None when it is not a file this gate reads."""
    if path.suffix in _HASH_SUFFIXES or path.name.startswith("Dockerfile"):
        return ("#",)
    if path.suffix in _SLASH_SUFFIXES:
        return ("//",)
    if not path.suffix and any(rel.startswith(r.strip("/") + "/") for r in roots) and source.startswith("#!"):
        return ("#",)
    return None


def _paths_in(text: str, pattern: "re.Pattern[str]") -> list[str]:
    if _HISTORICAL.search(text):
        return []
    out: list[str] = []
    for m in pattern.finditer(text):
        if m.end() < len(text) and text[m.end()] in _PATTERN_NEXT:
            continue
        token = m.group(0).rstrip(_STRIP)
        if "/" in token and token.split("/", 1)[1]:
            out.append(token)
    return out


def _candidates(repo_root: Path, roots: Sequence[str], use_git: Optional[bool]) -> list[Path]:
    patterns = _PATTERNS + tuple(f"{r.strip('/')}/*" for r in roots)
    return iter_files(repo_root, patterns, exclude=_EXCLUDE, use_git=use_git)


def _relative_roots(repo_root: Path, roots: Sequence[Any]) -> list[str]:
    """The roots as repo-relative strings: a config table's root arrives as a path under the repo root (see ``config.resolve_kwargs``)."""
    out: list[str] = []
    for root in roots:
        if isinstance(root, Path):
            root = root.relative_to(repo_root).as_posix() if root.is_absolute() else root.as_posix()
            root = "" if root == "." else root
        out.append(str(root))
    return out


def _scan(repo_root: Path, files: Optional[Iterable[Path]], roots: Sequence[str], use_git: Optional[bool]) -> tuple[list[Finding], ScanResult]:
    roots = _relative_roots(repo_root, roots)
    pattern = _token_re(roots)
    paths = [Path(p) for p in files] if files is not None else _candidates(repo_root, roots, use_git)
    scan = ScanResult(root=repo_root, min_files=0)
    findings: list[Finding] = []
    for path in paths:
        rel = relative_posix(path, repo_root)
        try:
            if path.suffix == ".py":
                source, tree = parse_source(path)
                lines = _python_comments(source, tree)
            else:
                source = read_source(path)
                markers = _markers(path, rel, source, roots)
                if markers is None:
                    continue
                tree = _EMPTY_TREE
                lines = _text_comments(source, markers)
        except SourceError as exc:
            scan.unparsed.append(SourceProblem(path, rel, (exc.line if exc.line is not None else 1), exc.kind, exc.message))
            continue
        scan.files.append(ParsedFile(path, rel, tree, source))
        for lineno, text in lines:
            for token in _paths_in(text, pattern):
                if not (repo_root / token).exists():
                    message = f"names `{token}`, which does not exist under the repo root"
                    findings.append(Finding(rel, lineno, RULE, message, key=f"{RULE}::{rel}::{token}"))
    return sorted(findings, key=lambda f: (f.path, f.line, f.message)), scan


def find_comment_repo_path_problems(
    repo_root: Union[str, Path],
    files: Optional[Iterable[Path]] = None,
    roots: Sequence[str] = DEFAULT_ROOTS,
    *,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every comment path under *roots* that names nothing under *repo_root*, plus one ``unparsed-file`` finding per file
    that could not be read or parsed. *files* defaults to every commentable file git lists under *repo_root* (a pruned
    walk outside git)."""
    root = Path(repo_root)
    findings, scan = _scan(root, files, roots, use_git)
    return sorted(findings + scan.unparsed_findings(), key=lambda f: (f.path, f.line, f.message))


def assert_comment_repo_paths(
    repo_root: Union[str, Path],
    *,
    files: Optional[Iterable[Path]] = None,
    roots: Sequence[str] = DEFAULT_ROOTS,
    baseline_path: Optional[Union[str, Path]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a comment naming a missing tooling path (or, with *baseline_path*, on one the baseline does not accept),
    on fewer than *min_files* files read, and on a file that could not be read or parsed unless *allow_unparsed*."""
    findings, scan = _scan(Path(repo_root), files, roots, use_git)
    if allow_unparsed:
        scan.unparsed = []
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="point the comment at the file's real path, or drop the reference if the file is gone",
    )
