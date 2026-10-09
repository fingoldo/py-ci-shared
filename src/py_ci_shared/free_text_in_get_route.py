"""A GET route must not take free text in its path or its query string; text a person typed belongs in a POST body.

A URL is written to every access log, proxy and CDN on the way, kept in browser history and sent in a ``Referer``. A chief
complaint, a diagnosis, a procedure name or a search phrase that a clinician or a customer typed is exactly what ends up there
when the route reads it as ``GET /lookup/{term}`` or ``GET /rates?chief_complaint=...``. The same lookup as ``POST /lookup`` with
the text in the body leaves no trace in any of those places. Three clinical lookups in one backend shipped as GET for months
before anyone looked at what their access logs held.

Flagged: a function decorated ``<anything>.get("/path", ...)`` (FastAPI and Starlette routers) with a parameter that carries free
text, which is a parameter

* whose name is a free-text name (``term``, ``query``, ``q``, ``text``, ``search``, ``condition``, ``complaint``,
  ``chief_complaint``, ``symptoms``, ``description``, ``note``, ``comment``, ``message``, ``prompt``, ``question``; configurable), or
* declared ``str`` with ``Query(max_length=N)``/``Path(max_length=N)`` (also inside ``Annotated``) where ``N`` is at least
  ``min_free_text_length`` (100), a bound only a sentence needs, unless its name says it is an identifier (``id``, ``*_id``,
  ``uuid``, ``slug``, ``curie``, ``code``, ``token``, ``key``).

Both the ``{name}`` placeholders of the path and the function's own parameters are looked at; a ``Depends``/``Body``/``Header``/
``Cookie`` parameter or a non-``str`` annotation is not text in the URL. The nearest correct code is the ``POST`` with a body
model, or a ``GET`` taking an identifier.

A route that must stay GET (a link people share, a client that has not moved yet) is accepted through the baseline, which can
only shrink: ``assert_free_text_in_get_route(root, baseline_path=...)``.

Usage::

    from py_ci_shared.free_text_in_get_route import assert_free_text_in_get_route

    def test_no_free_text_in_get_routes():
        assert_free_text_in_get_route(REPO / "src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["DEFAULT_FREE_TEXT_NAMES", "RULE", "assert_free_text_in_get_route", "find_free_text_in_get_route"]

RULE = "free-text-in-get-route"

DEFAULT_FREE_TEXT_NAMES = frozenset(
    {
        "term",
        "query",
        "q",
        "text",
        "search",
        "condition",
        "complaint",
        "chief_complaint",
        "symptom",
        "symptoms",
        "description",
        "note",
        "notes",
        "comment",
        "comments",
        "message",
        "prompt",
        "question",
    }
)
_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}")
_IDENTIFIER_NAME = re.compile(r"(?i)(?:^|_)(?:id|ids|uuid|slug|curie|code|token|key)$")
_NOT_URL_PARAMS = frozenset({"Depends", "Body", "Header", "Cookie", "Form", "File", "Security"})
_BOUND_FACTORIES = frozenset({"Query", "Path"})


def _callee_name(node: ast.AST) -> str:
    """``Query`` for ``Query(...)`` and ``fastapi.Query(...)``; empty for anything that is not a plain call target."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _route_path(decorator: ast.AST) -> Optional[str]:
    """The literal path of ``@x.get("/path", ...)`` / ``@x.get(path="/path")``; ``None`` for any other decorator."""
    if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "get"):
        return None
    candidates = list(decorator.args[:1]) + [kw.value for kw in decorator.keywords if kw.arg == "path"]
    for node in candidates:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/"):
            return node.value
    return None


def _is_text_annotation(annotation: Optional[ast.AST]) -> bool:
    """``str``, ``Optional[str]``, ``str | None`` and ``Annotated[str, ...]``; no annotation means FastAPI's default, ``str``."""
    if annotation is None:
        return True
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.replace(" ", "") in {"str", "Optional[str]", "str|None", "None|str"}
    text = ast.unparse(annotation).replace(" ", "")
    if text in {"str", "Optional[str]", "typing.Optional[str]", "str|None", "None|str"}:
        return True
    if text.startswith(("Annotated[", "typing.Annotated[", "typing_extensions.Annotated[")) and isinstance(annotation, ast.Subscript):
        inner = annotation.slice
        first = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
        return _is_text_annotation(first)
    return False


def _max_length(annotation: Optional[ast.AST], default: Optional[ast.AST]) -> Optional[int]:
    """A ``max_length=N`` given to ``Query``/``Path`` as the default or inside ``Annotated[...]``."""
    nodes = []
    if default is not None:
        nodes.append(default)
    if annotation is not None:
        nodes.extend(n for n in ast.walk(annotation) if isinstance(n, ast.Call))
    for node in nodes:
        if isinstance(node, ast.Call) and _callee_name(node) in _BOUND_FACTORIES:
            for kw in node.keywords:
                if kw.arg == "max_length" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                    return int(kw.value.value)
    return None


def _is_non_url_source(annotation: Optional[ast.AST], default: Optional[ast.AST]) -> bool:
    """A parameter FastAPI reads from the body, a header, a cookie or a dependency is not text in the URL."""
    if default is not None and _callee_name(default) in _NOT_URL_PARAMS:
        return True
    if annotation is not None:
        return any(isinstance(n, ast.Call) and _callee_name(n) in _NOT_URL_PARAMS for n in ast.walk(annotation))
    return False


def _parameters(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> list[tuple[str, Optional[ast.AST], Optional[ast.AST]]]:
    """``(name, annotation, default)`` for every positional and keyword-only parameter of *func*."""
    args = func.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[Optional[ast.AST]] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    out: list[tuple[str, Optional[ast.AST], Optional[ast.AST]]] = [(a.arg, a.annotation, d) for a, d in zip(positional, defaults)]
    out.extend((a.arg, a.annotation, d) for a, d in zip(args.kwonlyargs, args.kw_defaults))
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, names: frozenset[str], min_length: int) -> list[Finding]:
    """The free-text parameters of every GET route in one parsed file."""
    del aliases  # route decorators are matched by shape (``<x>.get("/path")``), not by an imported name
    out: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            path = _route_path(decorator)
            if path is None:
                continue
            route_line = decorator.lineno
            in_path = set(_PATH_PARAM.findall(path))
            seen: set[str] = set()
            for name, annotation, default in _parameters(node):
                if name in seen or _is_non_url_source(annotation, default):
                    continue
                if not (_is_text_annotation(annotation) or name in in_path):
                    continue
                bound = _max_length(annotation, default)
                by_name = name.lower() in names
                by_bound = bound is not None and bound >= min_length and not _IDENTIFIER_NAME.search(name)
                if not (by_name or by_bound):
                    continue
                seen.add(name)
                where = "path" if name in in_path else "query string"
                why = "is named like free text" if by_name else f"accepts up to {bound} characters"
                out.append(
                    Finding(
                        rel,
                        route_line,
                        RULE,
                        f"GET {path}: parameter {name!r} {why} and travels in the {where}; text a person typed belongs in a POST body, "
                        "out of URLs, access logs and proxies",
                    )
                )
    return out


def find_free_text_in_get_route(
    root: Union[str, Path],
    *,
    free_text_names: Iterable[str] = DEFAULT_FREE_TEXT_NAMES,
    min_free_text_length: int = 100,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every free-text GET parameter under *root*, sorted by path and line.

    Raises ``EmptyScanError`` when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that
    cannot be read or parsed (unless *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    names = frozenset(n.lower() for n in free_text_names)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), names, min_free_text_length))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_free_text_in_get_route(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    grow: Optional[bool] = None,
    free_text_names: Iterable[str] = DEFAULT_FREE_TEXT_NAMES,
    min_free_text_length: int = 100,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_free_text_in_get_route(
        root, free_text_names=free_text_names, min_free_text_length=min_free_text_length, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git
    )
    guidance = "move the text into a POST body model (keep the GET only if people share the link, and baseline it)"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="free_text_in_get_route", refresh_command="PY_CI_SHARED_REFRESH=free_text_in_get_route")
        baseline.enforce(found, refresh=refresh, grow=grow, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} free-text-in-get-route finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
