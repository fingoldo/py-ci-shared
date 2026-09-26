"""xfail and skip may mark a limit outside the repository, never park a bug inside it.

An ``xfail`` turns a visible failure into a permanent quiet one, and a non-strict one also stops noticing when the bug
is fixed. The rule the fleet follows is that an ``xfail`` needs a reason naming what makes the failure impossible to
fix here -- a third-party library, the OS, the hardware, a tracked upstream issue -- and ``strict=True`` so a fix
turns it red. Per test file:

* ``xfail-not-strict``: ``pytest.mark.xfail(...)`` (decorator, ``pytestmark``, ``pytest.param(marks=...)``) without
  ``strict=True``, unless the repository sets ``xfail_strict = true`` (read from ``pyproject.toml``, ``pytest.ini``,
  ``setup.cfg`` or ``tox.ini`` under *repo_root*, or passed as ``xfail_strict=``); ``strict=False`` counts too. An
  xfail whose reason names an external component is exempt: a live model or network may pass by chance;
* ``no-reason``: an xfail or an unconditional ``pytest.mark.skip`` / ``pytest.skip()`` / ``pytest.xfail()`` with no
  reason text;
* ``untracked-reason``: an xfail (marker or ``pytest.xfail("...")``) whose reason names no external component
  (:data:`EXTERNAL_WORDS`, plus the caller's ``external=``) and no tracked issue (``#123``, ``ABC-123``, an URL).
  Free text is a judgement, so this rule is what the baseline ratchets: the count may only fall. Only the literal
  words are read (f-string parts included); a reason computed from names, or built around a variable that holds the
  reason (``XFAIL_REASON + ...``, ``f"{gap} ..."``), is not judged.

``skipif`` (a condition on the environment) and ``importorskip`` are the right tools and are not judged.
"""

from __future__ import annotations

import ast
import configparser
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional, Union

from ._core import DEFAULT_EXCLUDE, Finding, ImportAliases, ScanResult, scan_python
from ._core.node_index import walk as _fast_walk
from ._gate_run import enforce_findings
from ._toml_compat import tomllib

__all__ = ["EXTERNAL_WORDS", "REFRESH_FLAG", "assert_no_xfail_to_defer", "find_xfail_to_defer", "read_xfail_strict"]

REFRESH_FLAG = "--refresh-xfail-baseline"
GATE = "xfail"
RULE_NOT_STRICT = "xfail-not-strict"
RULE_NO_REASON = "no-reason"
RULE_UNTRACKED = "untracked-reason"
#: Words that name something outside the repository: libraries, platforms, hardware, upstream.
EXTERNAL_WORDS = frozenset(
    {"upstream", "third-party", "third party", "vendor", "bug in", "not supported by", "unsupported by", "platform", "os", "hardware"}
    | {"windows", "win32", "macos", "darwin", "linux", "posix", "wsl", "gpu", "cuda", "cupy", "rocm", "mps", "cpu", "python 3", "cpython", "pypy"}
    | {"numba", "numpy", "pandas", "polars", "pyarrow", "arrow", "scipy", "sklearn", "scikit-learn", "torch", "pytorch", "tensorflow", "jax"}
    | {"lightgbm", "xgboost", "catboost", "plotly", "kaleido", "matplotlib", "psycopg", "sqlalchemy", "asyncpg", "postgres", "sqlite", "redis"}
    | {"pydantic", "fastapi", "httpx", "requests", "aiohttp", "playwright", "selenium", "chromedriver", "stanza", "spacy", "transformers"}
    | {"openai", "anthropic", "openrouter", "flutter", "dart", "node", "docker", "git", "pytest", "hypothesis", "mypy", "ruff", "black"}
    | {"llm", "deepseek", "gpt", "claude", "gemini", "host", "machine", "runner", "network", "timing"}
)
_TRACKED = re.compile(r"#\d+|\b[A-Z][A-Z0-9]+-\d+\b|https?://|\bissue\b|\bgh-\d+", re.IGNORECASE)
_MARK_NAMES = {"xfail": "xfail", "skip": "skip"}
_REASON_VARIABLE = re.compile(r"reason|xfail|gap|why|msg|message|note", re.IGNORECASE)


def read_xfail_strict(repo_root: Union[str, Path]) -> bool:
    """``xfail_strict`` from the repository's pytest configuration (pyproject, pytest.ini, setup.cfg, tox.ini)."""
    root = Path(repo_root)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = {}
        value = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("xfail_strict")
        if value is not None:
            return value is True or str(value).lower() == "true"
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        path = root / name
        if path.is_file():
            parser = configparser.ConfigParser()
            try:
                parser.read(path, encoding="utf-8")
            except configparser.Error:
                continue
            if parser.has_option(section, "xfail_strict"):
                return parser.get(section, "xfail_strict").strip().lower() in ("true", "1", "yes")
    return False


def _mark_kind(node: ast.expr, aliases: ImportAliases) -> Optional[str]:
    """``"xfail"``/``"skip"`` for ``pytest.mark.xfail`` / ``pytest.mark.skip`` (called or bare), else None."""
    target = node.func if isinstance(node, ast.Call) else node
    name = aliases.qualified_name(target) or ""
    if name.startswith("pytest.mark."):
        return _MARK_NAMES.get(name.rsplit(".", 1)[-1])
    return None


def _reason_node(call: Optional[ast.Call], positional_reason: bool) -> Optional[ast.expr]:
    """The reason expression: ``reason=``/``msg=``, or the first positional argument of the imperative forms."""
    if call is None:
        return None
    for kw in call.keywords:
        if kw.arg in ("reason", "msg"):
            return kw.value
    if positional_reason and call.args:
        return call.args[0]
    return None


def _reason(call: Optional[ast.Call], positional_reason: bool) -> Optional[str]:
    """The literal text of the reason (every string constant in it, f-string parts included); ``""`` for an empty
    literal, None when there is no reason at all."""
    node = _reason_node(call, positional_reason)
    if node is None:
        return None
    return " ".join(str(n.value) for n in _fast_walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str))


def _reason_is_computed(call: Optional[ast.Call], positional_reason: bool) -> bool:
    """A reason built at runtime from names (``reason=gap``): its words are not in the source, so it is not judged."""
    node = _reason_node(call, positional_reason)
    if node is None or isinstance(node, ast.Constant):
        return False
    if not _reason(call, positional_reason):
        return True
    # ``XFAIL_REASON + f" | {counts}"``, ``f"{gap} [still open]"``: the words live in a variable defined elsewhere
    idents = [n.id if isinstance(n, ast.Name) else n.attr for n in _fast_walk(node) if isinstance(n, (ast.Name, ast.Attribute))]
    return any(_REASON_VARIABLE.search(i) for i in idents)


def _strict(call: Optional[ast.Call]) -> Optional[bool]:
    for kw in (call.keywords if call is not None else []):
        if kw.arg == "strict":
            return bool(kw.value.value) if isinstance(kw.value, ast.Constant) else True
    return None


def _is_tracked(reason: str, external: frozenset[str]) -> bool:
    text = reason.lower()
    return bool(_TRACKED.search(reason)) or any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) for w in external)


def _judge_xfail(call: Optional[ast.Call], *, imperative: bool, repo_strict: bool, external: frozenset[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    reason = _reason(call, positional_reason=imperative)
    computed = _reason_is_computed(call, positional_reason=imperative)
    external_limit = bool(reason) and not computed and _is_tracked(reason or "", external)
    if not imperative and not external_limit:  # an external, nondeterministic limit (a live LLM) may pass by chance
        strict = _strict(call)
        if strict is False or (strict is None and not repo_strict):
            out.append((RULE_NOT_STRICT, "xfail without strict=True: a fix would stay green and unnoticed"))
    if computed:
        return out
    if not reason or not reason.strip():
        out.append((RULE_NO_REASON, "xfail with no reason"))
    elif not _is_tracked(reason, external):
        out.append((RULE_UNTRACKED, f"xfail reason names no external component or tracked issue: {reason[:100]!r}"))
    return out


def _is_conditional(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """An imperative ``pytest.skip()`` inside an ``if``/``except`` is a condition on the environment."""
    cur = parents.get(id(node))
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        if isinstance(cur, (ast.If, ast.ExceptHandler, ast.IfExp, ast.Try)):
            return True
        cur = parents.get(id(cur))
    return False


def find_xfail_to_defer(
    tests_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    repo_root: Optional[Union[str, Path]] = None,
    xfail_strict: Optional[bool] = None,
    external: Iterable[str] = (),
    exclude_parts: Iterable[str] = (),
    use_git: Optional[bool] = None,
) -> tuple[list[Finding], ScanResult]:
    """``(findings, scan)`` over the test files under *tests_root*.

    *xfail_strict* defaults to the repository's ini (``repo_root`` defaults to the parent of a directory *tests_root*).
    """
    if xfail_strict is None:
        base = Path(repo_root) if repo_root is not None else (Path(tests_root).parent if isinstance(tests_root, (str, Path)) else Path("."))
        xfail_strict = read_xfail_strict(base)
    ext = EXTERNAL_WORDS | frozenset(w.lower() for w in external)
    scan = scan_python(tests_root, exclude=DEFAULT_EXCLUDE | frozenset(exclude_parts), use_git=use_git)
    findings: list[Finding] = []
    for f in scan:
        aliases = ImportAliases.from_tree(f.tree)
        parents = {id(c): n for n in _fast_walk(f.tree) for c in ast.iter_child_nodes(n)}
        for node in _fast_walk(f.tree):
            if isinstance(node, ast.Call) and isinstance(parents.get(id(node)), ast.Attribute):
                continue  # ``pytest.mark.xfail(...).with_args`` and similar: the outer call is judged
            results = _node_findings(node, aliases, parents, xfail_strict, ext)
            findings.extend(Finding(f.rel, getattr(node, "lineno", 1), rule, message) for rule, message in results)
    findings.sort(key=lambda x: (x.path, x.line, x.rule))
    return findings, scan


def _node_findings(node: ast.AST, aliases: ImportAliases, parents: dict[int, ast.AST], repo_strict: bool, ext: frozenset[str]) -> list[tuple[str, str]]:
    if isinstance(node, ast.Call):
        name = aliases.qualified_name(node.func) or ""
        if name == "pytest.xfail":
            return _judge_xfail(node, imperative=True, repo_strict=repo_strict, external=ext)
        if name == "pytest.skip" and not _is_conditional(node, parents) and not _reason(node, positional_reason=True):
            return [(RULE_NO_REASON, "pytest.skip() with no reason")]
    if not isinstance(node, (ast.Call, ast.Attribute)):
        return []
    parent = parents.get(id(node))
    if isinstance(node, ast.Attribute) and isinstance(parent, ast.Call) and parent.func is node:
        return []  # the Call node carries the arguments
    kind = _mark_kind(node, aliases)  # type: ignore[arg-type]
    call = node if isinstance(node, ast.Call) else None
    if kind == "xfail":
        return _judge_xfail(call, imperative=False, repo_strict=repo_strict, external=ext)
    if kind == "skip" and not _reason(call, positional_reason=True):
        return [(RULE_NO_REASON, "pytest.mark.skip with no reason")]
    return []


def assert_no_xfail_to_defer(
    tests_root: Union[str, Path, Iterable[Union[str, Path]]],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
    xfail_strict: Optional[bool] = None,
    external: Iterable[str] = (),
    exclude_parts: Iterable[str] = (),
    min_files: int = 1,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on a finding not accepted by *baseline_path* (the free-text rule is meant to be ratcheted). Missing
    baseline fails; refresh with ``REFRESH_FLAG`` or ``PY_CI_SHARED_REFRESH=xfail``."""
    findings, scan = find_xfail_to_defer(
        tests_root, repo_root=repo_root, xfail_strict=xfail_strict, external=external, exclude_parts=exclude_parts, use_git=use_git
    )
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="fix the bug instead; an xfail needs strict=True and a reason naming the external limit or a tracked issue",
    )
