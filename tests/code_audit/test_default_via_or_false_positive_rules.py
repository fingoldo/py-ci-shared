"""Each rule added after the 2026-10-21 measurement on py-ci-shared: the shape it exempts is not flagged, and the real trap next to it still is."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.code_audit.default_via_or import scan_default_via_or_trap


def _flagged(tmp_path: Path, source: str) -> list[int]:
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    return [f.line for f in scan_default_via_or_trap(tmp_path)]


@pytest.mark.parametrize(
    "source",
    [
        'def f(names):\n    return ", ".join(names) or "none"\n',
        'def f(name):\n    return name or "<module>"\n',
        'def f(exc):\n    return exc.msg or f"{exc!r} has no message"\n',
    ],
    ids=["join-with-text-fallback", "name-with-label-fallback", "attribute-with-fstring-fallback"],
)
def test_a_text_fallback_is_not_a_trap(tmp_path, source):
    assert _flagged(tmp_path, source) == []


def test_a_numeric_default_is_still_a_trap(tmp_path):
    assert _flagged(tmp_path, "def f(cfg):\n    return cfg.get('n_jobs') or 4\n") == [2]


def test_two_booleans_joined_by_or_are_not_a_trap(tmp_path):
    source = "def f(a, b):\n    return isinstance(a, int) or isinstance(b, int)\n"
    assert _flagged(tmp_path, source) == []


def test_a_path_on_the_left_is_not_a_trap(tmp_path):
    source = "from pathlib import Path\n\n\ndef f(start: Path | None):\n    return (start or Path.cwd()).resolve()\n"
    assert _flagged(tmp_path, source) == []


def test_a_name_annotated_as_something_else_elsewhere_is_still_flagged(tmp_path):
    source = "from pathlib import Path\n\n\n" "def g(start: Path):\n    return start\n\n\n" "def f(start: int):\n    return start or 5\n"
    assert _flagged(tmp_path, source) == [9]


def test_regex_patterns_tried_in_turn_are_not_a_trap(tmp_path):
    source = 'import re\n\n\ndef f(spec):\n    return re.search(r">=(\\d+)", spec) or re.search(r"~=(\\d+)", spec)\n'
    assert _flagged(tmp_path, source) == []


def test_two_different_numeric_fields_chained_with_or_stay_flagged(tmp_path):
    """A confirmed real bug class: a legitimate 0.0 cost replaced by the other field."""
    source = 'def f(bundle):\n    return bundle.get("effective_cost_usd") or bundle.get("actual_cost_usd")\n'
    assert _flagged(tmp_path, source) != []


def test_the_same_key_read_twice_is_still_flagged(tmp_path):
    source = 'def f(spec):\n    return spec.get("rev") or spec.get("rev", 3)\n'
    assert _flagged(tmp_path, source) != []


# ---- 2026-11-05 glossum false positives: each shape below is a reviewed non-trap ----------


@pytest.mark.parametrize(
    "source",
    [
        # a getattr flag read with a boolean default is a boolean operand (flag or predicate)
        'def f(r):\n    return {"ok": getattr(r, "is_skip_notice", False) or len(r.errors) == 0}\n',
        'def f(r):\n    return {"ok": getattr(r, "is_skip_notice", False) or not has_fatal(r.errors)}\n',
        # the RHS reads the raw response that the LHS field was parsed from: the same payload
        'def f(row):\n    parsed = row.get("parsed") or safe_json_load(row.get("response"))\n',
        # a string constant as the getattr default is a text fallback
        'def f(coro_func, op):\n    label = op or getattr(coro_func, "__name__", "db_save")\n',
        # an f-string or string concatenation is a display default
        'def f(x):\n    return x["tag"] or "ZZZ" + x["headline"]\n',
    ],
)
def test_glossum_2026_11_05_reviewed_shapes_are_not_traps(tmp_path: Path, source: str):
    assert _flagged(tmp_path, source) == []


@pytest.mark.parametrize(
    "source",
    [
        # negative controls: the same shapes with a non-boolean getattr default, or another receiver, stay flagged
        'def f(r):\n    return {"ok": getattr(r, "count", 0) or len(r.errors) == 0}\n',
        'def f(row, other):\n    parsed = row.get("parsed") or make_payload(other.get("response"))\n',
    ],
)
def test_glossum_2026_11_05_negative_controls_stay_flagged(tmp_path: Path, source: str):
    assert _flagged(tmp_path, source) != []
