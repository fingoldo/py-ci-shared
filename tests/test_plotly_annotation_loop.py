from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.plotly_annotation_loop import RULE, SLOW_METHODS, assert_no_plotly_annotation_loops, find_plotly_annotation_loops


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


HEADER = "import plotly.graph_objects as go\n"


def _write(root: Path, rel: str, body: str, *, header: str = HEADER, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + (header + textwrap.dedent(body)).encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path, **kw: object) -> list[tuple[int, str]]:
    return [(f.line, f.message) for f in find_plotly_annotation_loops(root, use_git=False, **kw) if f.rule == RULE]  # type: ignore[arg-type]


@pytest.mark.parametrize("method", sorted(SLOW_METHODS))
def test_each_slow_method_in_a_for_loop_is_flagged(tmp_path: Path, method: str) -> None:
    _write(tmp_path, "m.py", f"def f(fig, cells):\n    for c in cells:\n        fig.{method}(x=c)\n")
    assert _found(tmp_path) == [(4, f"f: fig.{method}() runs once per loop item (quadratic); batch into one layout assignment")]


@pytest.mark.parametrize(
    "body",
    [
        "def f(fig, n):\n    i = 0\n    while i < n:\n        fig.add_annotation(x=i)\n        i += 1\n",
        "def f(fig, cells):\n    [fig.add_annotation(x=c) for c in cells]\n",
        "def f(fig, rows):\n    for r in rows:\n        if r:\n            with ctx():\n                fig.add_shape(x=r)\n",
        "def f(fig, n):\n    for i in range(n):\n        fig.add_hline(y=i)\n",
        "def f(fig):\n    for i in range(400):\n        fig.add_annotation(x=i)\n",
        "import plotly.express as px\ndef f(cells):\n    fig = px.imshow(cells)\n    for c in cells:\n        fig.add_annotation(x=c)\n",
    ],
)
def test_loop_shapes_are_flagged(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert len(_found(tmp_path)) == 1


@pytest.mark.parametrize(
    "body",
    [
        "def f(fig):\n    for v in (1.0, -1.0):\n        fig.add_hline(y=v)\n",
        "def f(fig, sym, v):\n    for i, x in enumerate((v, -v) if sym else (v,)):\n        fig.add_vline(x=x)\n",
        "def f(fig):\n    for i in range(3):\n        fig.add_vrect(x0=i, x1=i + 1)\n",
        "def f(fig, cells):\n    batch = [go.layout.Annotation(x=c) for c in cells]\n    fig.layout.annotations = fig.layout.annotations + tuple(batch)\n",
        "def f(fig, cells):\n    for c in cells:\n        fig.add_trace(go.Scatter(x=[c]))\n",
        "def f(fig, cells):\n    for c in cells:\n        fig.add_annotation(x=c)  # plotly-loop-ok: at most two legend labels\n",
        "def f(fig, cells):\n    for c in cells:\n        def g():\n            fig.add_annotation(x=c)\n",
        "def f(fig):\n    fig.add_annotation(x=1)\n",
    ],
)
def test_negative_controls(tmp_path: Path, body: str) -> None:
    _write(tmp_path, "m.py", body)
    assert _found(tmp_path) == []


def test_matplotlib_only_files_are_not_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "import matplotlib.pyplot as plt\ndef f(ax, cells):\n    for c in cells:\n        ax.add_annotation(c)\n", header="")
    assert _found(tmp_path) == []


def test_small_loop_threshold_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(fig):\n    for i in range(8):\n        fig.add_hline(y=i)\n")
    assert _found(tmp_path) == []
    assert len(_found(tmp_path, small_loop=4)) == 1


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/h.py", "def f(fig, cells):\n    for c in cells:\n        fig.add_annotation(x=c)\n")
    assert _found(tmp_path) == []
    assert len(_found(tmp_path, include_tests=True)) == 1


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(fig, cells):\n    for c in cells:\n        fig.add_annotation(x=c)\n", bom=True)
    assert len(_found(tmp_path)) == 1


def test_unparsable_and_empty_corpus_fail(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_plotly_annotation_loops(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    assert [f.rule for f in find_plotly_annotation_loops(tmp_path, use_git=False)] == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_no_plotly_annotation_loops(tmp_path, use_git=False)


def test_assert_raw_and_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f(fig):\n    fig.add_annotation(x=1)\n")
    assert_no_plotly_annotation_loops(src, use_git=False)
    _write(src, "m.py", "def f(fig, cells):\n    for c in cells:\n        fig.add_annotation(x=c)\n")
    with pytest.raises(pytest.fail.Exception, match="quadratic"):
        assert_no_plotly_annotation_loops(src, use_git=False)
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_no_plotly_annotation_loops(src, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_no_plotly_annotation_loops(src, baseline_path=baseline, refresh=True, use_git=False)
    assert_no_plotly_annotation_loops(src, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f(fig, cells):\n    for c in cells:\n        fig.add_annotation(x=c)\n    for c in cells:\n        fig.add_annotation(x=c)\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_no_plotly_annotation_loops(src, baseline_path=baseline, refresh=False, use_git=False)
