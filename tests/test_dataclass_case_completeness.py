"""`dataclass_case_completeness` finds every dataclass however it is decorated, and a covered list cannot rot."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from py_ci_shared.dataclass_case_completeness import assert_every_dataclass_has_a_case, find_dataclass_sites, find_dataclasses


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestFind:
    @pytest.mark.parametrize(
        "header,decorator",
        [
            ("from dataclasses import dataclass", "@dataclass"),
            ("from dataclasses import dataclass", "@dataclass(frozen=True)"),
            ("import dataclasses", "@dataclasses.dataclass"),
            ("from dataclasses import dataclass as dc", "@dc"),
            ("from dataclasses import dataclass as dc", "@dc(slots=True)"),
            ("import dataclasses as dcs", "@dcs.dataclass"),
        ],
    )
    def test_every_spelling_is_found(self, tmp_path, header, decorator):
        p = _write(tmp_path, "m.py", f"{header}\n\n{decorator}\nclass FooVerdict:\n    x: int = 0\n")
        assert list(find_dataclasses([p])) == ["FooVerdict"]

    def test_an_unrelated_decorator_is_not_a_dataclass(self, tmp_path):
        p = _write(tmp_path, "m.py", "from functools import total_ordering as dc\n\n@dc\nclass FooVerdict:\n    pass\n")
        assert find_dataclasses([p]) == {}

    def test_a_bom_file_is_read(self, tmp_path):
        p = tmp_path / "bom.py"
        p.write_bytes(codecs.BOM_UTF8 + b"from dataclasses import dataclass\n\n@dataclass\nclass A:\n    x: int = 0\n")
        assert list(find_dataclasses([p])) == ["A"]

    def test_same_named_classes_are_kept_apart(self, tmp_path):
        a = _write(tmp_path, "a/m.py", "from dataclasses import dataclass\n\n@dataclass\nclass V:\n    x: int = 0\n")
        b = _write(tmp_path, "b/m.py", "from dataclasses import dataclass\n\n@dataclass\nclass V:\n    y: int = 0\n")
        assert [name for name, _ in find_dataclass_sites([a, b])] == ["V", "V"]


class TestAssert:
    def test_empty_inputs_fail(self):
        with pytest.raises(AssertionError, match="parsed"):
            assert_every_dataclass_has_a_case([], [])

    def test_a_covered_class_passes_and_an_uncovered_one_fails(self, tmp_path):
        p = _write(tmp_path, "m.py", "from dataclasses import dataclass as dc\n\n@dc\nclass A:\n    x: int = 0\n")
        assert_every_dataclass_has_a_case([p], ["A"])
        with pytest.raises(AssertionError, match="no case"):
            assert_every_dataclass_has_a_case([p], [])

    def test_an_unparsable_file_fails(self, tmp_path):
        good = _write(tmp_path, "m.py", "from dataclasses import dataclass\n\n@dataclass\nclass A:\n    x: int = 0\n")
        bad = _write(tmp_path, "bad.py", "class (:\n")
        assert_every_dataclass_has_a_case([good], ["A"])
        with pytest.raises(AssertionError, match=r"bad.py"):
            assert_every_dataclass_has_a_case([good, bad], ["A"])

    def test_same_named_classes_need_one_entry_each(self, tmp_path):
        a = _write(tmp_path, "a/m.py", "from dataclasses import dataclass\n\n@dataclass\nclass V:\n    x: int = 0\n")
        b = _write(tmp_path, "b/m.py", "from dataclasses import dataclass\n\n@dataclass\nclass V:\n    y: int = 0\n")
        with pytest.raises(AssertionError, match="more than one dataclass"):
            assert_every_dataclass_has_a_case([a, b], ["V"])
        with pytest.raises(AssertionError, match=r"b/m.py::V"):
            assert_every_dataclass_has_a_case([a, b], ["a/m.py::V"])
        assert_every_dataclass_has_a_case([a, b], ["a/m.py::V"], exempt={"b/m.py::V": "legacy shape"})

    def test_stale_and_blank_entries_fail(self, tmp_path):
        p = _write(tmp_path, "m.py", "from dataclasses import dataclass\n\n@dataclass\nclass A:\n    x: int = 0\n")
        with pytest.raises(AssertionError, match="drop them"):
            assert_every_dataclass_has_a_case([p], ["A", "Gone"])
        with pytest.raises(AssertionError, match="needs a reason"):
            assert_every_dataclass_has_a_case([p], [], exempt={"A": " "})
