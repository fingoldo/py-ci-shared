"""A digest or cache-key builder whose ``except`` handler returns a literal constant: every failing input shares one key.

A key function has one job, to map distinct inputs to distinct keys. Wrapping its serialisation in
``try/except`` and returning a constant on failure breaks that job exactly when it matters::

    def config_digest(config):
        try:
            return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        except TypeError:
            return "uncached"

Every config with a non-serialisable value (a callable, a numpy scalar, a set) now has the key ``"uncached"``, so two
DIFFERENT configurations collide: the second run is served the first run's cached model, fitted with other settings,
and nothing raises, nothing logs, and the result looks like a cache hit. The shipped instance was a configuration
digest that fell back to the constant ``"uncached"`` on a serialisation error and collided distinct configs.

Reported: a function whose name matches ``digest``, ``fingerprint``, ``cache_key``, ``cache_id``, ``hash_of``,
``signature``, ``make_/build_/compute_/derive_/generate_<...>key`` or ``key_builder`` (case-insensitive), or that is
decorated with a ``key_builder`` / ``cache_key`` decorator, and that has an ``except`` handler which returns a literal
(``str``/``bytes``/number/``None``/bool, a tuple or list of literals, an f-string without a substitution, an UPPERCASE
module constant bound to one) or assigns one to a name the function returns. Not reported: a handler that re-raises, or
that builds a distinct fallback key from the input (``return "fallback:" + repr(config)``,
``return hashlib.sha256(repr(x).encode()).hexdigest()``).

``None`` is the usual "uncacheable, skip the cache" sentinel, so a handler that returns ``None`` is accepted when the
function says so: a return annotation naming ``None``/``Optional`` or a docstring mentioning ``None``. An undocumented
``return None`` is reported, since a caller that uses the result as a dict key then shares one key across failures.
Suppress a deliberate one with ``# key-fallback-ok: <reason>`` on the ``return`` (or the ``except``/``def``) line. A
non-key function whose name matches (an email ``signature`` that falls back to ``""``) is the other expected noise.

Usage in a consumer's meta test::

    from py_ci_shared.constant_fallback_cache_key import assert_constant_fallback_cache_key

    def test_no_constant_fallback_cache_key():
        assert_constant_fallback_cache_key("src")
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Union

from ._core import Baseline, Finding, ImportAliases, scan_python

__all__ = ["RULE", "assert_constant_fallback_cache_key", "find_constant_fallback_cache_key"]

RULE = "constant-fallback-cache-key"
SUPPRESSION = "# key-fallback-ok:"

_NAME = re.compile(r"digest|fingerprint|cache_key|cache_id|hash_of|signature|(?:make|build|compute|derive|generate)_\w*key|key_builder", re.IGNORECASE)
_DECORATOR = re.compile(r"key_builder|cache_key|keybuilder", re.IGNORECASE)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_literal(node: Optional[ast.AST], constants: dict[str, ast.AST], depth: int = 0) -> bool:
    if node is None:
        return True  # a bare `return` returns None
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_literal(e, constants, depth) for e in node.elts)
    if isinstance(node, ast.JoinedStr):
        return not any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.Name) and node.id.isupper() and node.id in constants and depth < 3:
        return _is_literal(constants[node.id], constants, depth + 1)
    return False


def _module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def _decorator_text(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""


def _suppressed(lines: list[str], ranges: list[tuple[int, int]]) -> bool:
    return any(SUPPRESSION in lines[i] for lo, hi in ranges for i in range(max(lo - 1, 0), min(hi, len(lines))))


def _own(scope: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, (*_FUNCTIONS, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))
    return out


def _findings_in(tree: ast.Module, rel: str, aliases: ImportAliases, lines: Optional[list[str]] = None) -> list[Finding]:
    """The findings in one parsed file. ``lines`` are its source lines, for the suppression comment."""
    lines = lines or []
    out: list[Finding] = []
    constants = _module_constants(tree)

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, _FUNCTIONS):
                if _NAME.search(child.name) or any(_DECORATOR.search(_decorator_text(d)) for d in child.decorator_list):
                    check(child, f"{prefix}{child.name}")
                visit(child, f"{prefix}{child.name}.<locals>.")
            else:
                visit(child, prefix)

    def check(func: Union[ast.FunctionDef, ast.AsyncFunctionDef], qualname: str) -> None:
        own = _own(func)
        annotation = ast.unparse(func.returns) if func.returns is not None else ""
        none_is_documented = "None" in annotation or "Optional" in annotation or "None" in (ast.get_docstring(func) or "")
        returned = {n.value.id for n in own if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)}
        for node in own:
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                for inner in _own(handler):
                    shown = ""
                    if not isinstance(inner, (ast.Return, ast.Assign)):
                        continue
                    if isinstance(inner, ast.Return) and _is_literal(inner.value, constants):
                        shown = ast.unparse(inner.value) if inner.value is not None else "None"
                    elif (
                        isinstance(inner, ast.Assign)
                        and len(inner.targets) == 1
                        and isinstance(inner.targets[0], ast.Name)
                        and inner.targets[0].id in returned
                        and _is_literal(inner.value, constants)
                    ):
                        shown = ast.unparse(inner.value)
                    if not shown or (shown == "None" and none_is_documented) or shown in ("True", "False"):  # a key is never a bool: a predicate, not a builder
                        continue
                    ranges = [
                        (inner.lineno, inner.end_lineno or inner.lineno),
                        (handler.lineno, handler.lineno),
                        (func.lineno, max(func.body[0].lineno - 1, func.lineno)),
                    ]
                    if not _suppressed(lines, ranges):
                        out.append(
                            Finding(rel, inner.lineno, RULE, f"{qualname}: except handler returns the constant {shown}, so every failing input shares one key")
                        )

    visit(tree, "")
    return out


def find_constant_fallback_cache_key(
    root: Union[str, Path],
    *,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> list[Finding]:
    """Every finding under *root*, sorted by path and line.

    *exclude* holds path fragments (matched against the POSIX relative path) of files to skip. Raises ``EmptyScanError``
    when fewer than *min_files* files parsed and ``UnparsedFilesError`` for a file that cannot be read or parsed (unless
    *allow_unparsed*): a gate that cannot read the code must not pass it.
    """
    skipped = tuple(exclude)
    scan = scan_python(root, min_files=min_files, use_git=use_git)
    scan.assert_ok(allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        if any(fragment in parsed.rel for fragment in skipped):
            continue
        out.extend(_findings_in(parsed.tree, parsed.rel, ImportAliases.from_tree(parsed.tree), parsed.source.splitlines()))
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_constant_fallback_cache_key(
    root: Union[str, Path],
    *,
    baseline_path: Optional[Union[str, Path]] = None,
    refresh: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
    exclude: Iterable[str] = (),
) -> None:
    """Fail on any finding, or with *baseline_path* on any finding the baseline does not accept."""
    found = find_constant_fallback_cache_key(root, min_files=min_files, allow_unparsed=allow_unparsed, use_git=use_git, exclude=exclude)
    guidance = "re-raise, or build a distinct fallback key from the input (repr/pickle digest); `# key-fallback-ok: <reason>` if None means 'do not cache'"
    if baseline_path is not None:
        baseline = Baseline(baseline_path, gate="constant_fallback_cache_key", refresh_command="PY_CI_SHARED_REFRESH=constant_fallback_cache_key")
        baseline.enforce(found, refresh=refresh, guidance=guidance).raise_for_pytest()
        return
    if found:
        raise AssertionError(f"{len(found)} constant-fallback-cache-key finding(s); {guidance}:\n  " + "\n  ".join(f.render() for f in found))
