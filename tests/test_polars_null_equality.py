from __future__ import annotations

import textwrap
import warnings
from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.polars_null_equality import (
    RULE_COLUMNS,
    RULE_MINMAX,
    RULE_NONE,
    PolarsNullEqualityWarning,
    assert_polars_null_equality,
    find_polars_null_equality,
)

HEADER = "import numpy as np\nimport polars as pl\nimport polars.selectors as cs\n"


def _write(root: Path, rel: str, body: str, *, header: str = HEADER, bom: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + (header + textwrap.dedent(body)).encode("utf-8"))

    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _rules(root: Path, **kw: object) -> list[str]:
    return [f.rule for f in find_polars_null_equality(root, use_git=False, **kw)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("ok = df.select(pl.col('a').min() == pl.col('a').max())", RULE_MINMAX),
        ("ok = df.select(cs.numeric().max() != cs.numeric().min())", RULE_MINMAX),
        ("ok = df.get_column('a').min() == df.get_column('a').max()", RULE_MINMAX),
        ("s = df.get_column('a')\n    ok = s.min() == s.max()", RULE_MINMAX),
        ("s = pl.Series([1, None])\n    ok = s.min() == s.max()", RULE_MINMAX),
        ("out = df.filter(pl.col('a') == None)", RULE_NONE),
        ("out = df.filter(None != pl.col('a'))", RULE_NONE),
        ("out = df.filter(pl.col('a').eq(None))", RULE_NONE),
        ("out = df.filter(pl.col('a') == pl.col('b'))", RULE_COLUMNS),
        ("out = df.filter(pl.col('a').cast(pl.Int64).ne(pl.col('b')))", RULE_COLUMNS),
        ("ok = df.select(pl.col('a').min() == pl.col('b').max())", RULE_COLUMNS),
    ],
)
def test_each_rule_fires(tmp_path: Path, line: str, rule: str) -> None:
    _write(tmp_path, "m.py", f"def f(df):\n    {line}\n")
    assert _rules(tmp_path) == [rule]


@pytest.mark.parametrize(
    "line",
    [
        "arr = np.asarray(x)\n    ok = arr.min() == arr.max()",
        "ok = df[c].min() == df[c].max()",
        "ok = df.select(pl.col('a').min().eq_missing(pl.col('a').max()))",
        "out = df.filter(pl.col('a').is_null())",
        "out = df.filter(pl.col('a') == 3)",
        "out = df.filter(pl.col('a').eq_missing(pl.col('b')))",
        "out = df.filter(pl.col('a') == pl.col('b'))  # null-eq-ok: nulls are filtered upstream",
        "s = df.get_column('a')\n    s = s.fill_nan(None)\n    t = np.zeros(3)\n    ok = t.min() == t.max()",
    ],
)
def test_negative_controls(tmp_path: Path, line: str) -> None:
    _write(tmp_path, "m.py", f"def f(df, x, c):\n    {line}\n")
    assert _rules(tmp_path) == []


def test_files_without_polars_are_not_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(pl):\n    return pl.col('a') == None\n", header="")
    assert _rules(tmp_path) == []


def test_tests_skipped_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "tests/h.py", "def f(df):\n    return df.filter(pl.col('a') == None)\n")
    assert _rules(tmp_path) == []
    assert _rules(tmp_path, include_tests=True) == [RULE_NONE]


def test_bom_file_is_checked(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(df):\n    return df.filter(pl.col('a') == None)\n", bom=True)
    assert _rules(tmp_path) == [RULE_NONE]


def test_advisory_warns_and_passes_strict_fails(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(df):\n    return df.filter(pl.col('a') == None)\n")
    with pytest.warns(PolarsNullEqualityWarning, match="is always null"):
        assert_polars_null_equality(tmp_path, use_git=False)
    with pytest.raises(pytest.fail.Exception, match=r"\[polars-compare-none\]"):
        assert_polars_null_equality(tmp_path, advisory=False, use_git=False)


def test_clean_corpus_emits_no_warning(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(df):\n    return df.filter(pl.col('a').is_null())\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert_polars_null_equality(tmp_path, use_git=False)


def test_broken_walk_fails_even_when_advisory(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_polars_null_equality(tmp_path, use_git=False)
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    assert _rules(tmp_path) == ["unparsed-file"]
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_polars_null_equality(tmp_path, use_git=False)


def test_strict_mode_baseline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src, "m.py", "def f(df):\n    return df.filter(pl.col('a') == None)\n")
    baseline = tmp_path / "b.json"
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_polars_null_equality(src, advisory=False, baseline_path=baseline, refresh=False, use_git=False)
    with pytest.raises(pytest.skip.Exception):
        assert_polars_null_equality(src, advisory=False, baseline_path=baseline, refresh=True, use_git=False)
    assert_polars_null_equality(src, advisory=False, baseline_path=baseline, refresh=False, use_git=False)
    _write(src, "m.py", "def f(df):\n    return df.filter(pl.col('a') == None), df.filter(pl.col('a') == None)\n")
    with pytest.raises(pytest.fail.Exception, match="1 new finding"):
        assert_polars_null_equality(src, advisory=False, baseline_path=baseline, refresh=False, use_git=False)
