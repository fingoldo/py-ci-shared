"""route_literal_after_catchall: a literal route registered after a same-prefix parameter route is dead (Starlette matches in order)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError
from py_ci_shared.route_literal_after_catchall import (
    RULE,
    assert_route_literal_after_catchall,
    find_route_literal_after_catchall,
)

NL = chr(10)


def _write(root: Path, lines: list, rel: str = "mod.py") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NL.join(lines) + NL, encoding="utf-8")


def _find(tmp_path: Path, lines: list, router_names=("router",)):
    _write(tmp_path, lines)
    return find_route_literal_after_catchall(tmp_path, router_names=router_names, use_git=False)


INCIDENT = [
    "from fastapi import APIRouter",  # 1
    "router = APIRouter()",  # 2
    "",  # 3
    '@router.get("/words/{sense_id}")',  # 4
    "async def one(sense_id: str): ...",  # 5
    "",  # 6
    '@router.get("/words/search")',  # 7
    "async def search(): ...",  # 8
]


def test_the_literal_route_after_the_parameter_route_is_reported_at_its_decorator(tmp_path):
    found = _find(tmp_path, INCIDENT)
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 7, RULE)]
    assert "/words/search" in found[0].message and "/words/{sense_id}" in found[0].message


def test_the_reverse_order_is_clean(tmp_path):
    reversed_lines = INCIDENT[:3] + INCIDENT[6:8] + INCIDENT[3:5]
    assert _find(tmp_path, reversed_lines) == []


def test_a_path_converter_catch_all_is_not_a_same_segment_shadow(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "router = APIRouter()",
                '@router.get("/files/{path:path}")',
                "async def f(path: str): ...",
                '@router.get("/files/index")',
                "async def g(): ...",
            ],
        )
        == []
    )


def test_a_different_method_does_not_shadow(tmp_path):
    lines = list(INCIDENT)
    lines[6] = '@router.post("/words/search")'
    assert _find(tmp_path, lines) == []


def test_a_different_segment_count_does_not_shadow(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "router = APIRouter()",
                '@router.get("/words/{sense_id}")',
                "async def one(sense_id): ...",
                '@router.get("/words/search/deep")',
                "async def deep(): ...",
            ],
        )
        == []
    )


def test_another_router_object_is_not_checked_unless_named(tmp_path):
    lines = [line.replace("router.", "api.") for line in INCIDENT]
    assert _find(tmp_path, lines, router_names=("router",)) == []
    assert [f.line for f in _find(tmp_path, lines, router_names=("api",))] == [7]


def test_an_empty_router_names_is_a_usage_error(tmp_path):
    _write(tmp_path, INCIDENT)
    with pytest.raises(ValueError, match="router"):
        find_route_literal_after_catchall(tmp_path, router_names=(), use_git=False)


def test_a_reasoned_marker_accepts_the_literal_route(tmp_path):
    lines = list(INCIDENT)
    lines.insert(6, "# route-order-ok: /words/search is only reachable through the proxy, reviewed")
    assert _find(tmp_path, lines) == []


def test_a_marker_without_a_reason_is_a_finding(tmp_path):
    lines = list(INCIDENT)
    lines.insert(6, "# route-order-ok:")
    found = _find(tmp_path, lines)
    assert len(found) == 1 and "needs a reason" in found[0].message


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_route_literal_after_catchall(tmp_path, router_names=("router",), use_git=False)


def test_the_assert_raises_on_the_incident(tmp_path):
    _write(tmp_path, INCIDENT)
    with pytest.raises(AssertionError, match=r"mod\.py:7"):
        assert_route_literal_after_catchall(tmp_path, router_names=("router",), use_git=False)
