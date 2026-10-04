"""A test that writes SQL must reach only a throwaway server and be run by an ``embedded_postgres run`` hook.

WHERE THIS CAME FROM
---------------------
2026-09-08, the ``new_scraper`` monorepo: a test that INSERTs and DELETEs rows ran against the production database and
left 24 ``~01test`` rows in the send ledger. The first fix wired the same test into the LIVE pre-push hook, which would
have deleted from production on every push. The repository then gained a loopback-only DSN accessor
(``throwaway_dsn``), and a local meta test that read the hook config. This is that meta test as a shared gate.

WHAT IS REPORTED
-----------------
A test file is *destructive* when BOTH hold:

(a) a string literal (or the constant part of an f-string; never a comment or a docstring) holds a SQL write verb:
    ``INSERT INTO``, ``UPDATE .. SET``, ``DELETE FROM``, ``TRUNCATE``, ``DROP TABLE|SCHEMA``, ``ALTER TABLE``,
    ``CREATE [UNIQUE|TEMP|..] TABLE|INDEX``;
(b) the file reaches a database: it calls a connect function (``psycopg2.connect`` and friends), carries a configured
    database marker (default ``real_database``, ``integration``), or obtains a DSN (calls a DSN accessor, or reads a
    ``DATABASE``-style environment variable).

A destructive file must (1) get its DSN from a configured throwaway accessor (default ``throwaway_dsn``) and from no
production source (a ``live_dsn``-style accessor, an environment variable matching the deny pattern), and (2) be named
by at least one pre-commit hook or workflow step, EVERY one of which starts ``python -m py_ci_shared.embedded_postgres
run`` before pytest. A file named by no entry never runs, which ``marker_runner_coverage`` reports for marked tests;
here it is reported too, because the runner is what makes the file safe. Unit tests that only hand a fake cursor SQL
text open no connection, carry no marker and read no DSN, so they are not destructive.

A test that is provably transaction-rollback only goes in ``allow`` with a reason; an allowlist entry that no longer
matches a destructive file is itself reported, so the list cannot rot.

Several test roots (one per package) and config files at the repository root are the normal monorepo case: pass the
roots as a list and ``repo_root`` (inferred from the config paths when omitted). A hook entry's ``cd dir &&`` and a
workflow step's ``working-directory`` are applied before its pytest paths are matched against the files.

Usage in a consumer's meta test::

    from py_ci_shared.destructive_tests_throwaway_only import assert_destructive_tests_are_throwaway_only

    def test_destructive_tests_are_throwaway_only():
        assert_destructive_tests_are_throwaway_only(
            [PACKAGE / "tests"],
            hook_config_paths=[REPO_ROOT / ".pre-commit-config.yaml", REPO_ROOT / ".github" / "workflows"],
            repo_root=REPO_ROOT,
        )
"""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, CorpusError, Finding, ImportAliases, ParsedFile, ScanResult, read_source, scan_python
from ._core.gate_contract import check_scan
from .marker_runner_coverage import expression_selects, keyword_selects
from .marker_runner_coverage import runners as _pytest_runners

__all__ = [
    "DEFAULT_CONNECT_CALLS",
    "DEFAULT_DB_MARKERS",
    "DEFAULT_FORBIDDEN_ACCESSORS",
    "DEFAULT_FORBIDDEN_ENV_PATTERN",
    "DEFAULT_RUNNER_PATTERN",
    "DEFAULT_THROWAWAY_ACCESSORS",
    "RULE",
    "RULE_NEVER_RUN",
    "RULE_NO_ACCESSOR",
    "RULE_PRODUCTION_DSN",
    "RULE_RUNNER",
    "RULE_STALE_ALLOW",
    "assert_destructive_tests_are_throwaway_only",
    "find_destructive_tests_throwaway_only",
]

RULE = "destructive-tests-throwaway-only"
RULE_PRODUCTION_DSN = "destructive-test-production-dsn"
RULE_NO_ACCESSOR = "destructive-test-no-throwaway-accessor"
RULE_RUNNER = "destructive-test-non-throwaway-runner"
RULE_NEVER_RUN = "destructive-test-never-run"
RULE_STALE_ALLOW = "destructive-test-allowlist-stale"

DEFAULT_THROWAWAY_ACCESSORS = ("throwaway_dsn",)
DEFAULT_FORBIDDEN_ACCESSORS = ("live_dsn", "dsn_is_configured")
DEFAULT_DB_MARKERS = ("real_database", "integration")
#: Whole-constant, upper-case names read as environment keys of a real server. The throwaway variable (``RA_THROWAWAY_PG_DSN``)
#: carries none of these words on purpose, so reading it directly is a separate matter: it skips the loopback guard, which
#: the accessor requirement catches.
DEFAULT_FORBIDDEN_ENV_PATTERN = r"^[A-Z0-9_]*(?:DATABASE|POSTGRES|SUPABASE|PGHOST|DB_URL|DB_DSN)[A-Z0-9_]*$"
#: Calls that open a connection to a server. ``sqlite3.connect`` is absent: an in-memory SQLite file is its own throwaway.
DEFAULT_CONNECT_CALLS = (
    "psycopg2.connect",
    "psycopg2.pool.SimpleConnectionPool",
    "psycopg2.pool.ThreadedConnectionPool",
    "psycopg.connect",
    "psycopg.Connection.connect",
    "psycopg.AsyncConnection.connect",
    "psycopg_pool.ConnectionPool",
    "psycopg_pool.AsyncConnectionPool",
    "asyncpg.connect",
    "asyncpg.create_pool",
    "aiopg.connect",
    "aiopg.create_pool",
    "pymysql.connect",
    "mysql.connector.connect",
    "pyodbc.connect",
    "sqlalchemy.create_engine",
    "sqlalchemy.ext.asyncio.create_async_engine",
)
TEST_PATTERNS = ("test_*.py", "*_test.py")

_IDENT = r"""[\w."\[\]]+"""
_WRITE_VERBS = re.compile(
    r"\bINSERT\s+INTO\b"
    rf"|\bUPDATE\s+{_IDENT}\s+SET\s+{_IDENT}\s*="
    r"|\bDELETE\s+FROM\b"
    r"|\bTRUNCATE\s+TABLE\b"
    rf"|\bTRUNCATE\s+{_IDENT}\s*(?:;|,|\bCASCADE\b|\bRESTART\b|\bCONTINUE\b|$)"
    r"|\bDROP\s+(?:TABLE|SCHEMA)\b"
    r"|\bALTER\s+TABLE\b"
    r"|\bCREATE\s+(?:(?:UNIQUE|TEMP|TEMPORARY|UNLOGGED|OR\s+REPLACE)\s+)*(?:TABLE|INDEX)\b",
    re.IGNORECASE,
)
#: The harness command: `python -m py_ci_shared.embedded_postgres run ...` (or the file path form). A quote may sit between.
DEFAULT_RUNNER_PATTERN = r"\bembedded_postgres(?:\.py)?[\"']?\s+run\b"
_PYTEST_WORD = re.compile(r"(?<![\w./\\-])pytest(?![\w./\\-])")
_CD = re.compile(r"\bcd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)")
_SEGMENT = re.compile(r"[^&;|\n]+")


@dataclass(frozen=True)
class _Entry:
    """One command a hook or workflow step runs: where it is and what one pytest invocation in it names."""

    label: str
    paths: tuple[str, ...]  # repo-root-relative, normalised; a pathless invocation contributes none (it names no file)
    throwaway: bool
    expression: Optional[str] = None  # the invocation's `-m` (or the project's addopts one); a deselected file is not run by it
    keyword: Optional[str] = None


@dataclass
class _Facts:
    first_write_line: int = 0
    connects: bool = False
    markers: set[str] = field(default_factory=set)
    dsn_evidence: bool = False
    throwaway_calls: int = 0
    production: list[tuple[int, str]] = field(default_factory=list)

    @property
    def destructive(self) -> bool:
        return bool(self.first_write_line) and (self.connects or bool(self.markers) or self.dsn_evidence)


def _normalise(path: str) -> str:
    cleaned = path.strip("\"'").replace("\\", "/").split("::", 1)[0]
    return posixpath.normpath(cleaned) if cleaned else "."


# ---------------------------------------------------------------------------------------------------------------------
# test file facts


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _string_literals(tree: ast.Module) -> Iterable[tuple[int, str]]:
    """``(line, text)`` of every string literal that is not a docstring; an f-string yields its constant parts joined, so a
    ``DELETE FROM {table}`` still reads as one statement."""
    skip = _docstring_ids(tree)
    inside_fstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inside_fstring.update(id(v) for v in node.values)
            parts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            yield node.lineno, " ".join(parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and id(node) not in inside_fstring:
            yield node.lineno, node.value


def _marker_names(node: ast.AST, aliases: ImportAliases) -> Optional[str]:
    qualified = aliases.qualified_name(node) if isinstance(node, ast.Attribute) else None
    if qualified and qualified.startswith("pytest.mark.") and qualified.count(".") == 2:
        return qualified.split(".")[2]
    return None


def _callee_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _env_key(node: ast.AST, aliases: ImportAliases) -> Optional[tuple[int, str]]:
    """``(line, key)`` when *node* reads an environment-style mapping with a constant string key: ``environ["K"]``,
    ``environ.get("K")``, ``getenv("K")``, or the same on any other mapping (a dotenv dict). A store is not a read."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return node.lineno, key.value
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        qualified = aliases.qualified_name(node) or ""
        if qualified in ("os.getenv", "os.environ.get") or (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            return node.lineno, node.args[0].value
    return None


def _facts(
    parsed: ParsedFile,
    *,
    accessors: frozenset[str],
    forbidden: frozenset[str],
    markers: frozenset[str],
    connects: frozenset[str],
    env_pattern: "re.Pattern[str]",
) -> _Facts:
    facts = _Facts()
    aliases = ImportAliases.from_tree(parsed.tree)
    for line, text in _string_literals(parsed.tree):
        if _WRITE_VERBS.search(" ".join(text.split())) and (not facts.first_write_line or line < facts.first_write_line):
            facts.first_write_line = line
    for node in ast.walk(parsed.tree):
        marker = _marker_names(node, aliases)
        if marker in markers:
            facts.markers.add(str(marker))
        if isinstance(node, ast.Call):
            _call_facts(node, aliases, facts, accessors=accessors, forbidden=forbidden, connects=connects)
        if isinstance(node, ast.arg) and node.arg in accessors:  # a fixture request: the conftest owns the DSN
            facts.throwaway_calls += 1
            facts.dsn_evidence = True
        read = _env_key(node, aliases)
        if read is not None and env_pattern.match(read[1]):
            facts.production.append((read[0], f"reads `{read[1]}` itself"))
            facts.dsn_evidence = True
    return facts


def _call_facts(
    node: ast.Call, aliases: ImportAliases, facts: _Facts, *, accessors: frozenset[str], forbidden: frozenset[str], connects: frozenset[str]
) -> None:
    name = _callee_name(node)
    if (aliases.qualified_name(node) or "") in connects:
        facts.connects = True
    if name in accessors or (name == "usefixtures" and any(isinstance(a, ast.Constant) and a.value in accessors for a in node.args)):
        facts.throwaway_calls += 1
        facts.dsn_evidence = True
    elif name in forbidden:
        facts.production.append((node.lineno, f"calls `{name}()`, the production DSN"))
        facts.dsn_evidence = True


# ---------------------------------------------------------------------------------------------------------------------
# hook and workflow entries


def _config_files(paths: Iterable[Union[str, Path]]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found = sorted([*path.glob("*.yml"), *path.glob("*.yaml")])
            if not found:
                raise CorpusError(f"hook config directory {path} holds no .yml/.yaml file; pass the workflows directory or the file")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise CorpusError(f"hook config path does not exist: {path}. Pass .pre-commit-config.yaml and the workflow files.")
    return files


def _inferred_root(config: Path) -> Path:
    parent = config.resolve().parent
    return parent.parents[1] if parent.name == "workflows" and parent.parent.name == ".github" else parent


def _commands(path: Path) -> list[tuple[str, str]]:
    """``(label, command)`` for every hook of a pre-commit config and every ``run`` step of a workflow."""
    import yaml

    try:
        data = yaml.safe_load(read_source(path)) or {}
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path}: not valid YAML ({type(exc).__name__}), so the gate cannot tell what runs the tests") from None
    if not isinstance(data, dict) or not ("repos" in data or "jobs" in data):
        raise CorpusError(f"{path}: neither a pre-commit config (`repos`) nor a workflow (`jobs`); the gate cannot read it")
    found: list[tuple[str, str]] = []
    for repo in data.get("repos") or []:
        for hook in repo.get("hooks") or []:
            entry = str(hook.get("entry") or "")
            if entry:
                found.append((f"{path.name}::{hook.get('id')}", " ".join([entry, *map(str, hook.get("args") or [])])))
    workflow_dir = str((((data.get("defaults") or {}).get("run")) or {}).get("working-directory") or "")
    for job_id, job in (data.get("jobs") or {}).items():
        job = job or {}
        job_dir = str((((job.get("defaults") or {}).get("run")) or {}).get("working-directory") or "") or workflow_dir
        for number, step in enumerate(job.get("steps") or [], start=1):
            run = str(step.get("run") or "")
            if run.strip():
                directory = str(step.get("working-directory") or "") or job_dir
                label = f"{path.name}::{job_id}::{step.get('name') or number}"
                found.append((label, f"cd '{directory}' && {run}" if directory else run))
    return found


def _entries(commands: Iterable[tuple[str, str]], *, addopts: str, runner_pattern: "re.Pattern[str]") -> list[_Entry]:
    """One entry per shell segment that runs pytest. The segment decides ``throwaway`` for its own paths, so a command that
    runs one pytest inside the harness and another outside it is judged per invocation, not by its first."""
    out: list[_Entry] = []
    for label, command in commands:
        if "pytest" not in command:
            continue
        for segment in _SEGMENT.finditer(command):
            text = segment.group(0)
            word = _PYTEST_WORD.search(text)
            if word is None:
                continue
            cds = [m.group(1).strip("\"'") for m in _CD.finditer(command[: segment.start()])]
            cwd = _normalise(cds[-1]) if cds else ""
            embedded = runner_pattern.search(text, 0, word.start()) is not None
            for invocation in _pytest_runners([(label, text)], addopts=addopts):
                paths = tuple(_normalise(posixpath.join(cwd, p) if cwd else p) for p in invocation.paths)
                out.append(_Entry(label, paths, embedded, invocation.expression, invocation.keyword))
    return out


def _names(entry: _Entry, file: str, markers: "set[str]") -> bool:
    """The invocation spells the file or a directory above it AND its ``-m`` selects the file's database markers (so a unit
    job's ``-m "not integration"`` over ``tests/`` does not run an ``integration`` file). A pathless one collects by
    discovery and names nothing. An expression pytest would reject selects nothing."""
    if not any(p == "." or p == file or file.startswith(p + "/") for p in entry.paths):
        return False
    try:
        return expression_selects(entry.expression, markers) and keyword_selects(entry.keyword, [*file.split("/"), *sorted(markers)])
    except ValueError:
        return False


# ---------------------------------------------------------------------------------------------------------------------
# the gate


def _merged_scan(roots: Sequence[Path], *, min_files: int, allow_unparsed: bool, use_git: Optional[bool]) -> ScanResult:
    merged = ScanResult(root=None, min_files=min_files)
    for root in roots:
        part = scan_python(root, min_files=0, patterns=TEST_PATTERNS, use_git=use_git)
        merged.files.extend(part.files)
        merged.unparsed.extend(part.unparsed)
    check_scan(merged, min_files=min_files, allow_unparsed=allow_unparsed)
    return merged


def _repo_relative(parsed: ParsedFile, repo_root: Path) -> str:
    try:
        return parsed.path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        raise CorpusError(f"{parsed.path} is not under repo_root {repo_root}; pass the repository root that holds the hook configs") from None


def _allow_key(rel: str, allow: Mapping[str, str]) -> Optional[str]:
    return next((key for key in allow if rel == key or rel.endswith("/" + key)), None)


def _file_findings(rel: str, facts: _Facts, entries: Sequence[_Entry], accessor_names: str) -> list[Finding]:
    """The findings of one destructive test file: where its DSN comes from, and what runs it."""
    line = facts.first_write_line
    out = [
        Finding(rel, at, RULE_PRODUCTION_DSN, f"writes SQL and {what}; a writing test reads its DSN only through {accessor_names}")
        for at, what in facts.production
    ]
    if not facts.throwaway_calls and not facts.production:
        out.append(Finding(rel, line, RULE_NO_ACCESSOR, f"writes SQL against a database but never calls {accessor_names}"))
    naming = [e for e in entries if _names(e, rel, facts.markers)]
    if not naming:
        out.append(Finding(rel, line, RULE_NEVER_RUN, "writes SQL and no hook or workflow step names it, so it never runs on a throwaway server"))
    out.extend(
        Finding(
            rel, line, RULE_RUNNER, f"writes SQL but `{entry}` runs it without `python -m py_ci_shared.embedded_postgres run`, so it can reach a real server"
        )
        for entry in sorted({e.label for e in naming if not e.throwaway})
    )
    return out


def _reasoned(mapping: Optional[Mapping[str, str]], name: str, what: str) -> dict[str, str]:
    checked = dict(mapping or {})
    for key, reason in checked.items():
        if not str(reason).strip():
            raise ValueError(f"{name}[{key!r}] has no reason; say {what}")
    return checked


def _resolve_root(hook_config_paths: Sequence[Union[str, Path]], repo_root: Union[str, Path, None]) -> Path:
    if repo_root is not None:
        return Path(repo_root)
    inferred = {_inferred_root(Path(p)) for p in hook_config_paths}
    if len(inferred) != 1:
        raise ValueError(f"the config paths imply different repository roots ({sorted(map(str, inferred))}); pass repo_root")
    return inferred.pop()


def find_destructive_tests_throwaway_only(
    tests_root: Union[str, Path, Sequence[Union[str, Path]]],
    *,
    hook_config_paths: Sequence[Union[str, Path]],
    repo_root: Union[str, Path, None] = None,
    throwaway_accessors: Sequence[str] = DEFAULT_THROWAWAY_ACCESSORS,
    forbidden_accessors: Sequence[str] = DEFAULT_FORBIDDEN_ACCESSORS,
    forbidden_env_pattern: str = DEFAULT_FORBIDDEN_ENV_PATTERN,
    db_markers: Sequence[str] = DEFAULT_DB_MARKERS,
    connect_calls: Sequence[str] = DEFAULT_CONNECT_CALLS,
    allow: Optional[Mapping[str, str]] = None,
    allow_runners: Optional[Mapping[str, str]] = None,
    runner_pattern: str = DEFAULT_RUNNER_PATTERN,
    addopts: str = "",
    min_files: int = 1,
    min_destructive_files: int = 0,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every finding for the destructive test files under *tests_root* (one directory or several), sorted by path and line.

    *hook_config_paths* are the pre-commit config and the workflow files (a directory stands for its ``*.yml``); they sit at
    the repository root while the test roots are nested, so *repo_root* anchors both (default: inferred from the first
    config). *allow* maps a test file (repo-relative, or a path suffix) to the reason it is rollback-only. Raises
    ``EmptyScanError`` below *min_files* parsed test files, ``UnparsedFilesError`` for a test file that cannot be parsed
    (unless *allow_unparsed*), ``CorpusError`` for a config that is missing or unreadable, and ``AssertionError`` when
    fewer than *min_destructive_files* files qualify (a scan that matched nothing proves nothing).
    """
    if not hook_config_paths:
        raise ValueError("hook_config_paths is empty: without the pre-commit config and workflows no test can be shown to run on a throwaway server")
    allow = _reasoned(allow, "allow", "why this test cannot reach a real server")
    allow_runners = _reasoned(allow_runners, "allow_runners", "what disposable server that step starts")
    roots = [Path(tests_root)] if isinstance(tests_root, (str, Path)) else [Path(r) for r in tests_root]
    root_path = _resolve_root(hook_config_paths, repo_root)
    scan = _merged_scan(roots, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git)
    configs = _config_files(hook_config_paths)
    entries = _entries((command for config in configs for command in _commands(config)), addopts=addopts, runner_pattern=re.compile(runner_pattern))
    entries = [e if not any(key in e.label for key in allow_runners) else replace(e, throwaway=True) for e in entries]
    env_pattern = re.compile(forbidden_env_pattern)
    findings: list[Finding] = []
    destructive = 0
    used_allow: set[str] = set()
    for parsed in scan:
        facts = _facts(
            parsed,
            accessors=frozenset(throwaway_accessors),
            forbidden=frozenset(forbidden_accessors),
            markers=frozenset(db_markers),
            connects=frozenset(connect_calls),
            env_pattern=env_pattern,
        )
        if not facts.destructive:
            continue
        rel = _repo_relative(parsed, root_path)
        destructive += 1
        allowed = _allow_key(rel, allow)
        if allowed is not None:
            used_allow.add(allowed)
            continue
        findings.extend(_file_findings(rel, facts, entries, "/".join(throwaway_accessors)))
    findings.extend(
        Finding(key, 1, RULE_STALE_ALLOW, f"allowlisted ({allow[key]!r}) but no destructive test file matches it any more; remove the entry")
        for key in sorted(set(allow) - used_allow)
    )
    if destructive < min_destructive_files:
        raise AssertionError(
            f"only {destructive} destructive test file(s) found; expected at least {min_destructive_files}. The scan is not reaching the tests."
        )
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule))


def assert_destructive_tests_are_throwaway_only(
    tests_root: Union[str, Path, Sequence[Union[str, Path]]],
    *,
    hook_config_paths: Sequence[Union[str, Path]],
    repo_root: Union[str, Path, None] = None,
    throwaway_accessors: Sequence[str] = DEFAULT_THROWAWAY_ACCESSORS,
    forbidden_accessors: Sequence[str] = DEFAULT_FORBIDDEN_ACCESSORS,
    forbidden_env_pattern: str = DEFAULT_FORBIDDEN_ENV_PATTERN,
    db_markers: Sequence[str] = DEFAULT_DB_MARKERS,
    connect_calls: Sequence[str] = DEFAULT_CONNECT_CALLS,
    allow: Optional[Mapping[str, str]] = None,
    allow_runners: Optional[Mapping[str, str]] = None,
    runner_pattern: str = DEFAULT_RUNNER_PATTERN,
    addopts: str = "",
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    min_destructive_files: int = 0,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_destructive_tests_throwaway_only(
        tests_root,
        hook_config_paths=hook_config_paths,
        repo_root=repo_root,
        throwaway_accessors=throwaway_accessors,
        forbidden_accessors=forbidden_accessors,
        forbidden_env_pattern=forbidden_env_pattern,
        db_markers=db_markers,
        connect_calls=connect_calls,
        allow=allow,
        allow_runners=allow_runners,
        runner_pattern=runner_pattern,
        addopts=addopts,
        min_files=min_files,
        min_destructive_files=min_destructive_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    guidance = (
        "a test that writes SQL reads its DSN only through the throwaway accessor and runs only under "
        "`python -m py_ci_shared.embedded_postgres run --env VAR -- python -m pytest <file>`; nothing deletes from production"
    )
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="destructive_tests_throwaway_only", refresh_command="PY_CI_SHARED_REFRESH=destructive_tests_throwaway_only")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} destructive test(s) that could reach a real database; {guidance}:\n  " + "\n  ".join(f.render() for f in found))
