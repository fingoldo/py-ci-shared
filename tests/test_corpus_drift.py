"""corpus_drift: the snapshot comparison (jump, blind, errored and the quiet cases), the finder binding, the CLI."""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from py_ci_shared import corpus_drift
from py_ci_shared.corpus_drift import NON_CORPUS, Drift, bind, compare, count_of, finders, main, render_table, snapshot, unbound_finders


def _snap(counts: dict[str, int], errors: Optional[dict[str, str]] = None, repo: str = "mlframe") -> dict[str, Any]:
    return {"schema": 1, "repos": {repo: {"commit": "abc", "counts": counts, "errors": errors or {}, "seconds": {}}}}


def _kinds(drifts: list[Drift]) -> list[tuple[str, str]]:
    return [(d.finder, d.kind) for d in drifts]


@pytest.mark.parametrize(
    "before, after, kind",
    [
        (10, 16, "jump"),  # +6 > 5 and +60% > 20%
        (0, 6, "jump"),  # from zero: +6 > 5
        (10, 15, None),  # +5 is not MORE than 5
        (100, 106, None),  # +6 but only +6%
        (100, 121, "jump"),  # +21 and +21%
        (100, 120, None),  # exactly +20% is not more than 20%
        (3, 0, "blind"),
        (0, 0, None),
        (10, 2, None),  # a drop that keeps some findings is a fix, not blindness
    ],
)
def test_thresholds_need_both_the_relative_and_the_absolute_jump(before, after, kind):
    drifts = compare(_snap({"g.find_x": before}), _snap({"g.find_x": after}))
    assert _kinds(drifts) == ([("g.find_x", kind)] if kind else [])
    assert all(d.failing for d in drifts)


def test_thresholds_are_configurable():
    prev, cur = _snap({"g.find_x": 10}), _snap({"g.find_x": 13})
    assert compare(prev, cur) == []
    assert _kinds(compare(prev, cur, pct=0.1, absolute=2)) == [("g.find_x", "jump")]


def test_a_finder_that_starts_to_raise_fails_and_one_that_always_raised_does_not():
    prev = _snap({"g.find_x": 4}, {"g.find_old": "SyntaxError: x"})
    cur = _snap({}, {"g.find_x": "UnparsedFilesError: 1 file", "g.find_old": "SyntaxError: x"})
    (d,) = compare(prev, cur)
    assert (d.finder, d.kind, d.before, d.after, d.failing) == ("g.find_x", "errored", 4, None, True)
    assert "UnparsedFilesError" in d.detail


def test_new_gone_and_recovered_finders_are_reported_but_do_not_fail():
    prev = _snap({"g.find_gone": 3}, {"g.find_back": "ValueError: v"})
    cur = _snap({"g.find_new": 40, "g.find_back": 2})
    drifts = compare(prev, cur)
    assert sorted(_kinds(drifts)) == [("g.find_back", "recovered"), ("g.find_gone", "gone"), ("g.find_new", "new")]
    assert not any(d.failing for d in drifts)


def test_a_repo_missing_from_the_previous_snapshot_is_not_compared():
    assert compare(_snap({"g.find_x": 0}, repo="old"), _snap({"g.find_x": 50}, repo="added")) == []


def test_the_table_puts_failures_first_and_names_the_numbers():
    prev = {"repos": {**_snap({"a.find_x": 2, "b.find_y": 10})["repos"], **_snap({"c.find_z": 1}, repo="pyutilz")["repos"]}}
    cur = {"repos": {**_snap({"a.find_x": 0, "b.find_y": 30, "n.find_n": 1})["repos"], **_snap({"c.find_z": 1}, repo="pyutilz")["repos"]}}
    text = render_table(compare(prev, cur), pct=0.2, absolute=5)
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "verdict" not in ln]
    assert rows == [
        "| **BLIND** | mlframe | `a.find_x` | 2 | 0 | dropped to zero: the finder may no longer reach the code |",
        "| **JUMP** | mlframe | `b.find_y` | 10 | 30 | +20 (+200%) |",
        "| new | mlframe | `n.find_n` | - | 1 |  |",
    ]
    assert "No change" in render_table([], pct=0.2, absolute=5)


@pytest.mark.parametrize(
    "result, n",
    [(None, 0), ([], 0), ([1, 2], 2), ({"a": 1}, 1), (7, 7), (([1, 2, 3], {"x": 1}), 3), ((1, 2), 2), (True, 1)],
)
def test_count_of_every_result_shape(result, n):
    assert count_of(result) == n


def test_every_finder_is_bindable_or_listed_with_a_reason():
    assert unbound_finders() == []
    assert all(len(reason) >= 20 for reason in NON_CORPUS.values())


def test_no_listed_entry_is_stale_or_needless():
    """An entry must name a finder (or a module with finders), and at least one of them must really be unbindable."""
    found = finders()
    for key in NON_CORPUS:
        covered = [fn for name, fn in found if name == key or name.split(".", 1)[0] == key]
        assert covered, f"NON_CORPUS[{key!r}] names no registered finder"
        assert any(n not in corpus_drift.BINDINGS for fn in covered for n in corpus_drift._required(fn)), f"{key} is bindable; run it instead"


def test_bind_maps_names_and_counts_unparsed_files_rather_than_raising(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1\n")

    def finder(files, repo_root, *, allow_unparsed=False, min_files=1):
        return files, repo_root, allow_unparsed

    kwargs = bind(finder, tmp_path)
    assert kwargs is not None
    assert [p.name for p in kwargs["files"]] == ["a.py"] and kwargs["repo_root"] == tmp_path and kwargs["allow_unparsed"] is True
    assert "min_files" not in kwargs

    def needs_tests(tests_dir):
        return tests_dir

    assert bind(needs_tests, tmp_path) is None  # no tests/ here: not applicable, not an error
    (tmp_path / "tests").mkdir()
    assert bind(needs_tests, tmp_path) == {"tests_dir": tmp_path / "tests"}


def test_snapshot_counts_records_errors_and_skips_inapplicable_finders(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1\n")

    def two(root):
        return [1, 2]

    def boom(root):
        raise ValueError("bad input\nsecond line")

    def needs_tests(tests_dir):
        return []

    data = snapshot([("r", tmp_path)], run=[("m.find_two", two), ("m.find_boom", boom), ("m.find_t", needs_tests)])
    repo = data["repos"]["r"]
    assert repo["counts"] == {"m.find_two": 2} and repo["errors"] == {"m.find_boom": "ValueError: bad input"}
    assert set(repo["seconds"]) == {"m.find_two", "m.find_boom"} and data["schema"] == 1


def test_a_real_gate_runs_through_the_generic_binding(tmp_path):
    (tmp_path / "a.py").write_bytes(b"import datetime\nnow = datetime.datetime.utcnow()\n")
    run = [(k, fn) for k, fn in corpus_drift.runnable_finders() if k == "naive_utcnow.find_naive_utcnow"]
    assert len(run) == 1
    assert snapshot([("r", tmp_path)], run=run)["repos"]["r"]["counts"] == {"naive_utcnow.find_naive_utcnow": 1}


def test_cli_snapshot_then_compare(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_bytes(b"x = 1\n")
    repos = tmp_path / "repos.toml"
    repos.write_bytes(f'[[repo]]\nname = "r"\npath = "{root.as_posix()}"\n'.encode())
    monkeypatch.setattr(corpus_drift, "runnable_finders", lambda: [("m.find_x", lambda root: [1, 2, 3])])
    cur = tmp_path / "cur.json"
    assert main(["snapshot", "--repos-file", str(repos), "--output", str(cur)]) == 0
    assert json.loads(cur.read_bytes())["repos"]["r"]["counts"] == {"m.find_x": 3}
    out = tmp_path / "drift.md"
    assert main(["compare", str(tmp_path / "missing.json"), str(cur), "--output", str(out)]) == 0
    assert "No previous snapshot" in out.read_text(encoding="utf-8")
    prev = tmp_path / "prev.json"
    prev.write_bytes(json.dumps(_snap({"m.find_x": 0}, repo="r")).encode("utf-8"))
    assert main(["compare", str(prev), str(cur)]) == 0  # +3 is under the absolute threshold
    assert main(["compare", str(prev), str(cur), "--absolute", "2"]) == 1
    assert "**JUMP**" in capsys.readouterr().out
    prev.write_bytes(json.dumps(_snap({"m.find_x": 3}, repo="r")).encode("utf-8"))
    assert main(["compare", str(prev), str(cur)]) == 0
    capsys.readouterr()


def test_cli_snapshot_refuses_an_empty_repo_list(tmp_path, capsys):
    repos = tmp_path / "repos.toml"
    repos.write_bytes(b"")
    assert main(["snapshot", "--repos-file", str(repos), "--output", str(tmp_path / "x.json")]) == 2
    capsys.readouterr()
