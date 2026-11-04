"""Tests for py_ci_shared.caller_frame_mutation."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.caller_frame_mutation import (
    RULE,
    CallerFrameMutationWarning,
    assert_caller_frame_mutation,
    find_caller_frame_mutation,
)

BOM = b"\xef\xbb\xbf"

VIOLATION = 'class Selector:\n    def transform(self, X):\n        X["total"] = X["a"] + X["b"]\n        return X\n'


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _lines(tmp_path: Path, source: str, **kw) -> list[int]:
    root = _corpus(tmp_path, {"m.py": source.encode()})
    return [f.line for f in find_caller_frame_mutation(root, use_git=False, **kw)]


def _fn(body: str, params: str = "df", doc: str = "", name: str = "f") -> str:
    head = f"def {name}({params}):\n"
    if doc:
        head += f'    """{doc}"""\n'
    return head + "".join(f"    {line}\n" for line in body.splitlines())


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    (finding,) = find_caller_frame_mutation(root, use_git=False)
    assert (finding.path, finding.line, finding.rule) == ("m.py", 3, RULE)
    assert "'transform'" in finding.message and "'X'" in finding.message and "item assignment" in finding.message


@pytest.mark.parametrize(
    "body, shape",
    [
        ('df["a"] = 1', "item assignment"),
        ('df["a"] += 1', "item assignment"),
        ('df.loc[df["a"] > 0, "b"] = 1', "item assignment"),
        ("df.iloc[0, 1] = 1", "item assignment"),
        ('df.at[0, "a"] = 1', "item assignment"),
        ('del df["a"]', "del"),
        ('df.columns = ["a", "b"]', ".columns assignment"),
        ("df.index = range(3)", ".index assignment"),
        ('df.drop(columns=["a"], inplace=True)', ".drop(inplace=True)"),
        ('df.rename(columns={"a": "b"}, inplace=True)', ".rename(inplace=True)"),
        ('df["a"].fillna(0, inplace=True)', ".fillna(inplace=True)"),
    ],
)
def test_every_mutation_shape_is_reported_with_its_name(tmp_path, body, shape):
    root = _corpus(tmp_path, {"m.py": _fn(body).encode()})
    (finding,) = find_caller_frame_mutation(root, use_git=False)
    assert finding.line == 2 and shape in finding.message


@pytest.mark.parametrize("param", ["X", "df", "data", "frame", "X_train", "X_val", "train_df", "pandas_df"])
def test_default_frame_parameter_names(tmp_path, param):
    assert _lines(tmp_path, _fn(f'{param}["a"] = 1', params=param)) == [2]


@pytest.mark.parametrize("param", ["y", "arr", "xs", "dataset", "self"])
def test_other_parameter_names_are_not_frames(tmp_path, param):
    assert _lines(tmp_path, _fn(f'{param}["a"] = 1', params=param)) == []


def test_custom_frame_params(tmp_path):
    assert _lines(tmp_path, _fn('table["a"] = 1', params="table")) == []
    assert _lines(tmp_path, _fn('table["a"] = 1', params="table"), frame_params=["table"]) == [2]


def test_negative_control_nearest_correct_code_is_not_reported(tmp_path):
    assert _lines(tmp_path, _fn('df = df.copy()\ndf["a"] = 1')) == []
    assert _lines(tmp_path, _fn('df = df.assign(a=1)\ndf["b"] = 2')) == []
    assert _lines(tmp_path, _fn('out = df.copy()\nout["a"] = 1\nout.drop(columns=["b"], inplace=True)')) == []
    assert _lines(tmp_path, _fn('return df.drop(columns=["a"], inplace=False)')) == []
    assert _lines(tmp_path, _fn('return df.drop(columns=["a"])')) == []
    assert _lines(tmp_path, _fn('x = df["a"]\nreturn x.sum()')) == []
    assert _lines(tmp_path, _fn("df.sort_values('a')\ndf.columns.tolist()")) == []


def test_a_rebinding_anywhere_in_the_function_exempts_the_parameter(tmp_path):
    assert _lines(tmp_path, _fn('df["a"] = 1\ndf = df.copy()')) == []
    assert _lines(tmp_path, _fn('for df in dfs:\n    df["a"] = 1')) == []


def test_a_shadowing_local_in_a_nested_function_is_only_reported_for_its_own_function(tmp_path):
    src = 'def outer(df):\n    def inner(df):\n        df["a"] = 1\n    return inner\n'
    assert _lines(tmp_path, src) == [3]


def test_a_mutation_in_a_nested_function_of_a_frame_parameter_is_reported_once(tmp_path):
    src = 'def outer(X):\n    def inner():\n        X["a"] = 1\n    return inner\n'
    assert _lines(tmp_path, src) == [3]


def test_name_and_docstring_exemptions(tmp_path):
    body = 'df["a"] = 1'
    assert _lines(tmp_path, _fn(body, name="fill_inplace")) == []
    assert _lines(tmp_path, _fn(body, name="do_in_place")) == []
    assert _lines(tmp_path, _fn(body, name="mutate_frame")) == []
    assert _lines(tmp_path, _fn(body, doc="Fills the frame IN PLACE.")) == []
    assert _lines(tmp_path, _fn(body, doc="This mutates df.")) == []
    assert _lines(tmp_path, _fn(body, doc="Returns a new frame.")) == [3]
    assert _lines(tmp_path, _fn(body, name="fill_inplace"), exempt_name_parts=["nope"]) == [2]
    assert _lines(tmp_path, _fn(body, doc="in place"), docstring_phrases=["nope"]) == [3]


def test_marker_and_allowed_entry_suppress(tmp_path):
    marked = _fn('df["a"] = 1  # frame-mutation-ok: cleaner')
    assert _lines(tmp_path, marked) == []
    two = _fn('df["a"] = 1  # frame-mutation-ok: cleaner\ndf["b"] = 2')
    assert _lines(tmp_path, two) == [3]
    assert _lines(tmp_path, _fn('df["a"] = 1'), allowed={"m.py::f": "cleaner"}) == []
    assert _lines(tmp_path, _fn('df["a"] = 1'), allowed={"m.py::g": "x"}) == [2]
    root = _corpus(tmp_path / "other", {"bench/b.py": _fn('df["a"] = 1').encode()})
    assert find_caller_frame_mutation(root, use_git=False, exclude=["bench/"]) == []


def test_a_bom_prefixed_and_a_crlf_file_are_reported_like_the_plain_one(tmp_path):
    root = _corpus(tmp_path, {"bom.py": BOM + VIOLATION.encode(), "crlf.py": VIOLATION.replace("\n", "\r\n").encode()})
    assert sorted((f.path, f.line) for f in find_caller_frame_mutation(root, use_git=False)) == [("bom.py", 3), ("crlf.py", 3)]


def test_the_default_mode_warns_and_never_fails(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    with pytest.warns(CallerFrameMutationWarning, match=r"m\.py:3"):
        assert_caller_frame_mutation(root, use_git=False)


def test_a_clean_corpus_emits_no_warning(tmp_path):
    root = _corpus(tmp_path, {"m.py": b"x = 1\n"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert_caller_frame_mutation(root, use_git=False)


def test_non_advisory_mode_fails_and_names_the_site(tmp_path):
    root = _corpus(tmp_path, {"m.py": VIOLATION.encode()})
    with pytest.raises(AssertionError, match=r"m\.py:3"):
        assert_caller_frame_mutation(root, use_git=False, advisory=False)


def test_non_advisory_baseline_accepts_known_findings_and_rejects_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _corpus(tmp_path / "src", {"m.py": VIOLATION.encode()})
    base = tmp_path / "baseline.json"
    with pytest.raises(pytest.fail.Exception):
        assert_caller_frame_mutation(root, use_git=False, advisory=False, baseline_path=base)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):
        assert_caller_frame_mutation(root, use_git=False, advisory=False, baseline_path=base, refresh=True)
    assert_caller_frame_mutation(root, use_git=False, advisory=False, baseline_path=base)
    (root / "n.py").write_bytes(VIOLATION.encode())
    with pytest.raises(pytest.fail.Exception):
        assert_caller_frame_mutation(root, use_git=False, advisory=False, baseline_path=base)


def test_a_broken_walk_fails_even_in_advisory_mode(tmp_path):
    root = _corpus(tmp_path, {"ok.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        assert_caller_frame_mutation(root, use_git=False)
    assert find_caller_frame_mutation(root, use_git=False, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_caller_frame_mutation(tmp_path, use_git=False)
