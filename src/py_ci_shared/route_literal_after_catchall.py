"""A literal route must not come after a same-shape parameter route that shadows it.

Starlette and FastAPI match routes in registration order. ``@router.get("/words/{sense_id}")`` declared before
``@router.get("/words/search")`` answers every request to ``/words/search`` with the catch-all and raises no error (glossum
2026-07-21 API audit finding 14).

Per router object and HTTP method, in source order, a later route is shadowed by an earlier one when both have the same
number of segments, at every segment the earlier one is either the same literal or a ``{param}`` (any later segment), and
at least one segment is a ``{param}`` earlier and a literal later. A ``{name:path}`` converter matches several segments,
so it is not compared segment-wise and is never the shadowing route.

Router objects are opt-in by name, because a router is only known by the variable it is bound to: pass
``router_names=("router",)`` (an empty tuple is a usage error, not a pass). Accepted deliberately with
``# route-order-ok: <reason>`` on the decorator's line or the line above; a marker with no reason is a finding.

Usage::

    from py_ci_shared.route_literal_after_catchall import assert_route_literal_after_catchall

    def test_routes_are_not_shadowed():
        assert_route_literal_after_catchall("mypackage", router_names=("router", "api"), min_files=20)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ._core import Finding, walk
from ._core.gate_contract import check_scan
from ._gate_report import line_has_marker, scan_tree, skip_set

__all__ = ["MARKER", "RULE", "assert_route_literal_after_catchall", "find_route_literal_after_catchall"]

RULE = "route-literal-after-catchall"
MARKER = "route-order-ok"
_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_PARAM = re.compile(r"\{[^{}:]+\}")


@dataclass(frozen=True)
class _Route:
    line: int
    owner: str
    method: str
    path: str
    segments: tuple[str, ...]

    @property
    def has_path_converter(self) -> bool:
        return ":path}" in self.path


def _route_of(dec: ast.expr, router_names: frozenset[str]) -> Optional[_Route]:
    """The route a ``@<router>.<method>("<path>")`` decorator declares, or None for any other decorator."""
    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
        return None
    owner = dec.func.value
    if not (isinstance(owner, ast.Name) and owner.id in router_names and dec.func.attr in _METHODS):
        return None
    if not (dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
        return None
    path = dec.args[0].value
    return _Route(dec.lineno, owner.id, dec.func.attr, path, tuple(path.strip("/").split("/")))


def _is_param(segment: str) -> bool:
    return bool(_PARAM.fullmatch(segment))


def _shadows(earlier: _Route, later: _Route) -> bool:
    """True when *earlier* answers every request that *later* was registered for."""
    if earlier.has_path_converter or len(earlier.segments) != len(later.segments):
        return False
    shadow_position = False
    for early_seg, late_seg in zip(earlier.segments, later.segments):
        if _is_param(early_seg):
            shadow_position = shadow_position or not _is_param(late_seg)
        elif _is_param(late_seg) or early_seg != late_seg:
            return False
    return shadow_position


def _marker_reason(lines: list[str], line: int) -> Optional[str]:
    for k in (line, line - 1):
        if line_has_marker(lines, k, MARKER):
            return lines[k - 1].split(MARKER, 1)[1].lstrip(" :\t-").strip()
    return None


def _routes(tree: ast.Module, router_names: frozenset[str]) -> list[_Route]:
    routes: list[_Route] = []
    for node in walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                route = _route_of(dec, router_names)
                if route is not None:
                    routes.append(route)
    return sorted(routes, key=lambda r: r.line)


def find_route_literal_after_catchall(
    root: Union[str, Path],
    *,
    router_names: Sequence[str],
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every route under *root* that a same-shape parameter route registered earlier shadows, sorted by path and line."""
    names = frozenset(router_names)
    if not names:
        raise ValueError("route_literal_after_catchall: pass the router variable names, e.g. router_names=('router',)")
    scan = scan_tree(root, skip=skip_set(skip_dir_names, include_tests=include_tests), use_git=use_git)
    check_scan(scan, min_files=min_files, allow_unparsed=allow_unparsed)
    out: list[Finding] = []
    for parsed in scan:
        lines = parsed.source.splitlines()
        routes = _routes(parsed.tree, names)
        for index, later in enumerate(routes):
            # Only the same router object and HTTP method can shadow each other.
            shadow = next(
                (e for e in routes[:index] if e.owner == later.owner and e.method == later.method and _shadows(e, later)),
                None,
            )
            if shadow is None:
                continue
            reason = _marker_reason(lines, later.line)
            if reason:
                continue
            suffix = "; the # route-order-ok marker needs a reason after it" if reason == "" else ""
            out.append(
                Finding(
                    parsed.rel,
                    later.line,
                    RULE,
                    f"`{later.method.upper()} {later.path}` is registered after `{shadow.path}`, which answers it first; move the literal above the parameter route{suffix}",
                )
            )
    return sorted(out, key=lambda f: (f.path, f.line))


def assert_route_literal_after_catchall(
    root: Union[str, Path],
    *,
    router_names: Sequence[str],
    skip_dir_names: tuple[str, ...] = (),
    include_tests: bool = False,
    min_files: int = 1,
    allow_unparsed: bool = False,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any route shadowed by an earlier same-shape parameter route."""
    found = find_route_literal_after_catchall(
        root,
        router_names=router_names,
        skip_dir_names=skip_dir_names,
        include_tests=include_tests,
        min_files=min_files,
        allow_unparsed=allow_unparsed,
        use_git=use_git,
    )
    if found:
        raise AssertionError(f"{len(found)} {RULE} finding(s):\n  " + "\n  ".join(f.render() for f in found))
