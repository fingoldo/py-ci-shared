"""Unit tests for audit_path_references: code never pins an open audit round's path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.audit_path_references import assert_no_open_round_paths, find_open_round_literals, open_round_names


@pytest.fixture
def audits(tmp_path: Path) -> Path:
    a = tmp_path / "audits"
    (a / "2026-01-01").mkdir(parents=True)
    (a / "implemented" / "2025-12-01").mkdir(parents=True)
    (a / "notes").mkdir()
    return a


def test_only_dated_open_rounds_count(audits):
    assert open_round_names([audits]) == {"2026-01-01"}


def test_a_pinned_open_round_is_reported_and_a_docstring_or_closed_round_is_not(tmp_path, audits):
    code = tmp_path / "claims.py"
    code.write_text(
        '"""Docstrings may name audits/2026-01-01/01.md freely."""\n'
        "from pathlib import Path\n"
        'A = Path("audits") / "2026-01-01" / "01.md"\n'
        'B = "dashboard/audits/2026-01-01/03.md"\n'
        'C = Path("audits") / "implemented" / "2025-12-01" / "01.md"\n'
        'D = "2026-01-01 was a Thursday"\n',
        encoding="utf-8",
    )
    problems = find_open_round_literals([code], [audits], root=tmp_path)
    assert [p.split(":")[1] for p in problems] == ["3", "4"]


def test_another_projects_open_round_of_the_same_date_does_not_make_ours_open(tmp_path, audits):
    """production_scrapers pinned its own CLOSED `implemented/2026-09-01`, and dashboard happened to have an
    open round of that date; the first version reported it."""
    ours = tmp_path / "ours" / "audits"
    (ours / "implemented" / "2026-01-01").mkdir(parents=True)
    code = tmp_path / "c.py"
    code.write_text(
        'from pathlib import Path\nX = Path("audits") / "implemented" / "2026-01-01"\nY = "2026-01-01"\nZ = "dashboard/audits/2026-01-01/x.md"\n',
        encoding="utf-8",
    )
    problems = find_open_round_literals([code], [ours], other_audits=[audits], root=tmp_path)
    assert [p.split(":")[1] for p in problems] == ["4"]


def test_an_implemented_path_string_is_not_an_open_round(tmp_path, audits):
    code = tmp_path / "c.py"
    code.write_text('X = "audits/implemented/2026-01-01/x.md"\n', encoding="utf-8")
    assert find_open_round_literals([code], [audits]) == []


def test_no_open_rounds_means_nothing_to_pin(tmp_path):
    (tmp_path / "audits" / "implemented").mkdir(parents=True)
    code = tmp_path / "c.py"
    code.write_text('X = "audits/2026-01-01/x.md"\n', encoding="utf-8")
    assert find_open_round_literals([code], [tmp_path / "audits"]) == []


def test_a_date_used_as_data_is_not_a_path(tmp_path, audits):
    """Four dashboard tests carried an open round's date as a value: a card's last-activity day, a

    fixture's observation day, a chart x value, a `computed_at`. A folder name is also an ordinary date.
    """
    code = tmp_path / "c.py"
    code.write_text(
        'OBSERVED_ON = "2026-01-01"\n'
        "def t():\n"
        '    assert totals == (7, "2026-01-01")\n'
        '    assert facts["computed_at"] == "2026-01-01"\n'
        '    return {m: [("2026-01-01", 1.0)] for m in ("a",)}\n',
        encoding="utf-8",
    )
    assert find_open_round_literals([code], [audits]) == []


def test_a_bare_name_used_as_a_path_is_still_reported(tmp_path, audits):
    """The bare-name half has to keep its teeth: a round resolved by name, or beside an audit file."""
    chain = tmp_path / "chain.py"
    chain.write_text('from pathlib import Path\nP = Path("x") / "2026-01-01" / "03_performance.md"\n', encoding="utf-8")
    resolver = tmp_path / "resolver.py"
    resolver.write_text('X = audit_file("2026-01-01", "03_performance.md")\n', encoding="utf-8")
    beside = tmp_path / "beside.py"
    beside.write_text('X = read("2026-01-01", "03_performance.md")\n', encoding="utf-8")
    for f in (chain, resolver, beside):
        assert len(find_open_round_literals([f], [audits])) >= 1, f


def test_assert(tmp_path, audits):
    code = tmp_path / "c.py"
    code.write_text('X = "audits/2026-01-01/x.md"\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="open audit round"):
        assert_no_open_round_paths([code], [audits])


def test_two_files_with_the_same_literal_are_two_keys_even_without_root(tmp_path, audits):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    for f in (a, b):
        f.write_text('X = "audits/2026-01-01/x.md"\n', encoding="utf-8")
    only_a = [f"{a.resolve().as_posix()}:audits/2026-01-01/x.md"]
    with pytest.raises(pytest.fail.Exception, match=r"1 literal path") as exc:
        assert_no_open_round_paths([a, b], [audits], known=only_a)
    assert "b.py" in str(exc.value) and "a.py:1" not in str(exc.value)
    assert_no_open_round_paths([a, b], [audits], known=[*only_a, f"{b.resolve().as_posix()}:audits/2026-01-01/x.md"])


@pytest.mark.parametrize(
    "line",
    [
        'X = os.path.join(ROOT, "audits", "2026-01-01", "x")',
        'X = Path(ROOT, "audits", "2026-01-01")',
        'X = f"{R}/2026-01-01/x.md"',
        'X = ROOT + "/audits/" + "2026-01-01" + "/x.md"',
        'X = f"{R}/audits/2026-01-01"',
    ],
)
def test_a_round_built_in_pieces_is_reported(tmp_path, audits, line):
    code = tmp_path / "c.py"
    code.write_text("import os\nfrom pathlib import Path\n" + line + "\n", encoding="utf-8")
    problems = find_open_round_literals([code], [audits], root=tmp_path)
    assert problems and all(p.startswith("c.py:3:") for p in problems)


@pytest.mark.parametrize(
    "line",
    [
        'X = os.path.join(ROOT, "audits", "implemented", "2026-01-01", "x")',
        'X = f"{R}/implemented/2026-01-01/x.md"',
        'X = f"range {a}: 2026-01-01/2026-02-01"',
        'X = "day " + "2026-01-01"',
        'X = os.path.join(ROOT, "audits", "2025-12-01", "x")',
    ],
)
def test_a_closed_or_data_round_built_in_pieces_is_not(tmp_path, audits, line):
    code = tmp_path / "c.py"
    code.write_text("import os\n" + line + "\n", encoding="utf-8")
    assert find_open_round_literals([code], [audits], root=tmp_path) == []


def test_an_unparsable_file_is_reported_and_fails_the_assert(tmp_path, audits):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    nul = tmp_path / "nul.py"
    nul.write_bytes(b"x = 1\x00\n")
    good = tmp_path / "good.py"
    good.write_text("X = 1\n", encoding="utf-8")
    problems = find_open_round_literals([bad, nul, good], [audits], root=tmp_path)
    assert [p.split(":")[0] for p in problems] == ["bad.py", "nul.py"] and all("unparsable" in p for p in problems)
    with pytest.raises(pytest.fail.Exception, match="could not be parsed"):
        assert_no_open_round_paths([bad, good], [audits], root=tmp_path)
    assert_no_open_round_paths([good], [audits], root=tmp_path)
    with pytest.raises(pytest.fail.Exception, match="only 0 file"):
        assert_no_open_round_paths([bad], [audits], root=tmp_path, min_files=1)
