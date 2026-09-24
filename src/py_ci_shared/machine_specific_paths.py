"""Paths and connection strings that only exist on the machine that wrote them.

A path baked in from whoever's machine wrote the file breaks for everyone else, and usually not loudly: a model cache
pinned to ``D:\\.cache\\stanza`` makes every other machine silently re-download gigabytes into a different directory;
a script defaulting to another user's ``C:\\Users\\<name>\\...`` fails on the first open; a DSN with credentials
pasted into twenty scripts drifts the moment the canonical one changes (two of them already disagreed on the port).

Four rules, over string literals in ``.py`` files (docstrings and comments excluded) and over the non-comment text of
config files (``.yml``/``.yaml``/``.toml``/``.cfg``/``.ini``):

* ``user-home-path``: ``C:\\Users\\<name>``, ``/home/<name>/``, ``/Users/<name>/`` for a real-looking name
  (placeholders such as ``<user>``, ``{user}``, ``$USER``, ``runner``, ``runneradmin`` are not names);
* ``drive-path``: an absolute path on a non-system drive, ``D:\\...`` through ``Z:\\...``;
* ``model-cache-path``: a hardcoded ``.cache/huggingface|torch|stanza`` or ``stanza_resources`` directory, which
  bypasses ``HF_HOME``/``TORCH_HOME``/the project's resolver;
* ``db-url``: a database URL carrying inline credentials (``postgresql://user:pass@host``).

Markdown and test directories are not scanned by default (examples there are prose or subjects of a test). A line
carrying ``# machine-path-ok`` is an explicit opt-out, and *allow* takes regexes matched against the flagged text.

Usage::

    from py_ci_shared.machine_specific_paths import assert_no_machine_specific_paths

    def test_no_machine_specific_paths():
        assert_no_machine_specific_paths(REPO, baseline_path=HERE / "_machine_paths_baseline.json")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ParsedFile, SourceProblem
from ._gate_report import TextFile, line_has_marker, read_text_corpus, report, scan_tree, skip_set

__all__ = ["DEFAULT_CONFIG_PATTERNS", "REFRESH_FLAG", "RULES", "find_machine_specific_paths", "assert_no_machine_specific_paths"]

REFRESH_FLAG = "--refresh-machine-paths-baseline"
MARKER = "machine-path-ok"
DEFAULT_CONFIG_PATTERNS: tuple[str, ...] = ("*.yml", "*.yaml", "*.toml", "*.cfg", "*.ini")
PLACEHOLDER_USERS = frozenset(
    {"user", "username", "users", "you", "me", "name", "runner", "runneradmin", "public", "default", "all users", "someone", "example", "xxx", "foo", "shared"}
)
_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
RULES: dict[str, re.Pattern[str]] = {
    "user-home-path": re.compile(rf"(?i)(?:\b[a-z]:[\\/]+users[\\/]+({_NAME}))|(?:(?<![\w.~-])/(?:home|Users)/({_NAME})(?=/))"),
    "model-cache-path": re.compile(r"(?i:\.cache[\\/]+(?:huggingface|torch|stanza)\b)|(?:^|[\\/])stanza_resources(?![\w])"),
    "db-url": re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|mssql)(?:\+\w+)?://([^\s'\"/@:]+):([^\s'\"/@]+)@"),
    "drive-path": re.compile(r"(?<![A-Za-z0-9])[D-Zd-z]:[\\/]+[\w.$~-]"),
}
_PLACEHOLDER_SECRET = re.compile(r"[<>{}$%*]|^\.\.\.$")
_MAX_SHOWN = 100


def _docstring_ids(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


#: A regular expression's source (``r"\d:\d\d|\Z"``) can look like a drive path; such strings are not paths.
_REGEX_SOURCE = re.compile(r"\(\?[:P!=<]|\[\^|\\[dswDSW][+*?{\\]|\|\\Z")
_WORKFLOW_DIR = ".github/"


def _hits(text: str, allow: Sequence[re.Pattern[str]], *, workflow: bool = False) -> Iterator[tuple[int, str, str]]:
    """``(offset, rule, matched text)`` for every rule match in *text* that is not a placeholder or allowed.

    A drive-path match inside a more specific match (a user home or a model cache on that drive) is not repeated."""
    if _REGEX_SOURCE.search(text):
        return
    spans: list[tuple[int, int]] = []
    for rule, pattern in RULES.items():
        if rule == "db-url" and workflow:
            continue  # CI service containers are created by the workflow itself, with credentials it chose
        for m in pattern.finditer(text):
            if rule == "user-home-path":
                name = (m.group(1) or m.group(2) or "").lower()
                if name in PLACEHOLDER_USERS:
                    continue
            if rule == "db-url" and (_PLACEHOLDER_SECRET.search(m.group(2)) or m.group(2).lower() in ("password", "pass", "secret", "pwd")):
                continue
            if rule == "drive-path" and any(a <= m.start() < b + 4 for a, b in spans):
                continue
            end = text.find("\n", m.end())
            shown = text[m.start() : end if end != -1 else len(text)].strip()
            shown = re.split(r"['\"\s]", shown, maxsplit=1)[0] if rule != "db-url" else m.group(0)
            if any(a.search(shown) for a in allow):
                continue
            spans.append((m.start() - 3, m.start() + len(shown)))
            yield m.start(), rule, shown[:_MAX_SHOWN]


def _python_findings(parsed: ParsedFile, allow: Sequence[re.Pattern[str]]) -> list[Finding]:
    docstrings = _docstring_ids(parsed.tree)
    lines = parsed.source.splitlines()
    out: list[Finding] = []
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in docstrings:
            continue
        for offset, rule, shown in _hits(node.value, allow):
            line = node.lineno + node.value.count("\n", 0, offset)
            if line_has_marker(lines, line, MARKER) or line_has_marker(lines, node.lineno, MARKER):
                continue
            out.append(Finding(parsed.rel, line, rule, shown))
    return out


def _strip_config_comment(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith(("#", ";")):
        return ""
    cut = line.find(" #")
    return line if cut == -1 else line[:cut]


def _config_findings(tf: TextFile, allow: Sequence[re.Pattern[str]]) -> list[Finding]:
    out: list[Finding] = []
    lines = tf.text.splitlines()
    for number, raw in enumerate(lines, 1):
        if line_has_marker(lines, number, MARKER):
            continue
        for _, rule, shown in _hits(_strip_config_comment(raw), allow, workflow=tf.rel.startswith(_WORKFLOW_DIR)):
            out.append(Finding(tf.rel, number, rule, shown))
    return out


def _collect(
    root: Union[str, Path],
    *,
    config_patterns: Sequence[str],
    allow: Iterable[str],
    skip_dir_names: Iterable[str],
    include_tests: bool,
    use_git: Optional[bool],
) -> tuple[list[Finding], int, list[SourceProblem]]:
    skip = skip_set(skip_dir_names, include_tests=include_tests)
    allowed = [re.compile(a) for a in allow]
    scan = scan_tree(root, skip=skip, use_git=use_git)
    findings = [f for parsed in scan for f in _python_findings(parsed, allowed)]
    problems = list(scan.unparsed)
    count = scan.parsed_count
    if config_patterns:
        corpus = read_text_corpus(root, patterns=config_patterns, skip=skip, use_git=use_git)
        findings += [f for tf in corpus.files for f in _config_findings(tf, allowed)]
        problems += corpus.unreadable
        count += len(corpus.files)
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), count, problems


def find_machine_specific_paths(
    root: Union[str, Path],
    *,
    config_patterns: Sequence[str] = DEFAULT_CONFIG_PATTERNS,
    allow: Iterable[str] = (),
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every machine-specific path or credentialed DB URL under *root*, plus one ``unparsed-file`` finding per file
    that could not be read or parsed."""
    findings, _, problems = _collect(
        root, config_patterns=config_patterns, allow=allow, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    return findings + [p.to_finding() for p in problems]


def assert_no_machine_specific_paths(
    root: Union[str, Path],
    *,
    config_patterns: Sequence[str] = DEFAULT_CONFIG_PATTERNS,
    allow: Iterable[str] = (),
    skip_dir_names: Iterable[str] = (),
    include_tests: bool = False,
    min_files: int = 1,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding (new against *baseline_path* when given), on fewer than *min_files* files read, and on any
    unreadable or unparsable file. Refresh with ``--refresh-machine-paths-baseline``."""
    findings, count, problems = _collect(
        root, config_patterns=config_patterns, allow=allow, skip_dir_names=skip_dir_names, include_tests=include_tests, use_git=use_git
    )
    report(
        findings,
        gate="machine-specific-paths",
        flag=REFRESH_FLAG,
        guidance="resolve the location at runtime (env var, settings object, Path.home(), platformdirs) instead of pasting it",
        parsed_count=count,
        problems=problems,
        min_files=min_files,
        root=root,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
    )
