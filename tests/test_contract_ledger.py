"""Behavioural tests for ``contract_ledger``: each check must fail on the gap it guards and pass on a complete ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared.contract_ledger import (
    assert_documents_identical,
    assert_fixture_fresh,
    assert_ledger_covers,
    changed_files,
    document_diff,
    fixture_problems,
    ledger_entry_problems,
    ledger_gaps,
    load_ledger,
    missing_test_references,
    promise_message_ids,
    snapshot_files,
    store_writers,
    value_mismatches,
)


def test_ledger_gaps_names_every_observed_value_without_an_entry() -> None:
    entries = {"context_sex": {"status": "mapped"}}
    assert ledger_gaps(["context_sex", "finding_pair_sex", "present_absent_conflict"], entries) == [
        "finding_pair_sex",
        "present_absent_conflict",
    ]


def test_ledger_gaps_is_empty_when_every_value_is_recorded() -> None:
    entries = {"context_sex": {"status": "mapped"}, "finding_pair_sex": {"status": "mapped"}}
    assert ledger_gaps(["context_sex"], entries) == []


def test_ledger_gaps_compares_status_codes_as_strings() -> None:
    assert ledger_gaps([503, 404], {"503": {"status": "mapped"}}) == ["404"]


def test_entry_problems_flag_unmapped_without_reason_and_unknown_status() -> None:
    entries = {
        "a": {"status": "unmapped", "reason": "   "},
        "b": {"status": "maybe"},
        "c": {"status": "unmapped", "reason": "client has no branch for it yet"},
        "d": {"status": "mapped"},
    }
    problems = ledger_entry_problems(entries)
    assert any(p.startswith("a: unmapped without a reason") for p in problems)
    assert any(p.startswith("b: status") for p in problems)
    assert not any(p.startswith(("c:", "d:")) for p in problems)


def test_entry_problems_require_declared_fields() -> None:
    entries = {"503": {"status": "mapped"}}
    assert ledger_entry_problems(entries, required=["retry_after"]) == ["503: missing required field 'retry_after'"]


def test_assert_ledger_covers_fails_with_the_missing_value_named() -> None:
    with pytest.raises(pytest.fail.Exception, match="finding_pair_sex"):
        assert_ledger_covers(["context_sex", "finding_pair_sex"], {"context_sex": {"status": "mapped"}}, name="kind")


def test_assert_ledger_covers_passes_on_a_complete_ledger() -> None:
    assert_ledger_covers(["context_sex"], {"context_sex": {"status": "mapped"}}, name="kind")


def test_fixture_problems_reports_mismatch_and_missing_keys() -> None:
    problems = fixture_problems(
        fixture={"version": "1", "schema": "x"},
        live={"version": "2", "schema": "x"},
        keys=["version", "schema", "extra"],
    )
    assert problems == [
        "version: fixture has '1', live has '2'",
        "extra: missing from the committed fixture",
    ]


def test_assert_fixture_fresh_fails_rather_than_skipping() -> None:
    with pytest.raises(pytest.fail.Exception, match="stale"):
        assert_fixture_fresh(fixture={"v": "1"}, live={"v": "2"}, keys=["v"], regenerate_hint="rerun the generator")


def test_load_ledger_fails_loudly_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="contract ledger missing"):
        load_ledger(tmp_path / "absent.json")


def test_load_ledger_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"literals": {"x": {"status": "mapped"}}}), encoding="utf-8")
    assert load_ledger(path)["literals"]["x"]["status"] == "mapped"


def test_changed_files_reports_new_and_grown_files_only() -> None:
    before = {"a": 10, "b": 5}
    after = {"a": 10, "b": 9, "c": 1}
    assert changed_files(before, after) == ["b", "c"]


def test_snapshot_files_skips_named_directories(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "x.jsonl").write_text("hello", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "y.pyc").write_bytes(b"1")
    assert snapshot_files(tmp_path, skip_dirs=["__pycache__"]) == {"keep/x.jsonl": 5}


def test_snapshot_files_of_a_missing_root_is_empty(tmp_path: Path) -> None:
    assert snapshot_files(tmp_path / "absent") == {}


def test_store_writers_finds_the_file_a_probe_appends_to(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("unchanged", encoding="utf-8")

    def probe() -> None:
        with (tmp_path / "feedback.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"complaint": "x"}\n')

    writers = store_writers(run_probe=probe, roots=[tmp_path])
    assert writers == [f"{tmp_path}/feedback.jsonl"]


def test_store_writers_is_empty_when_the_probe_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert store_writers(run_probe=lambda: None, roots=[tmp_path]) == []


def test_document_diff_names_a_property_added_to_an_existing_schema() -> None:
    fixture = {"components": {"schemas": {"Foo": {"properties": {"a": {"type": "string"}}}}}}
    live = {"components": {"schemas": {"Foo": {"properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}, "Bar": {}}}}
    rows = document_diff(fixture, live)
    assert [(kind, path) for kind, path, _ in rows] == [("added", "components.schemas.Bar"), ("added", "components.schemas.Foo.properties.b")]


def test_document_diff_reports_removed_and_changed_paths_and_nothing_for_equal_documents() -> None:
    assert document_diff({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 2]}) == []
    rows = document_diff({"a": 1, "gone": 2, "l": [1]}, {"a": 3, "l": [1, 2]})
    assert [(kind, path) for kind, path, _ in rows] == [("changed", "a"), ("removed", "gone"), ("changed", "l")]


def test_assert_documents_identical_passes_for_equal_documents_regardless_of_key_order() -> None:
    assert_documents_identical(fixture={"a": 1, "b": {"x": 1, "y": 2}}, live={"b": {"y": 2, "x": 1}, "a": 1}, regenerate_hint="regen")


def test_assert_documents_identical_fails_naming_the_added_property_and_the_hint() -> None:
    with pytest.raises(pytest.fail.Exception) as raised:
        assert_documents_identical(fixture={"s": {"properties": {}}}, live={"s": {"properties": {"new_field": {}}}}, regenerate_hint="run the generator")
    message = str(raised.value)
    assert "added s.properties.new_field" in message and "run the generator" in message


def test_assert_documents_identical_truncates_a_long_diff_but_counts_all_of_it() -> None:
    with pytest.raises(pytest.fail.Exception) as raised:
        assert_documents_identical(fixture={}, live={f"k{i}": i for i in range(50)}, regenerate_hint="h", max_lines=5)
    assert "50 place(s)" in str(raised.value) and "(+45 more)" in str(raised.value)


def test_promise_message_ids_matches_whole_words_only() -> None:
    catalogue = {
        "a": "We will retry shortly",
        "b": "Retrying in 3s",
        "c": "this willow is fine",
        "d": "nothing was changed",
        "e": "it does this AUTOMATICALLY",
        "f": "a pre-retry step",
    }
    assert promise_message_ids(catalogue, ["will", "retrying", "automatically", "retry"]) == ["a", "b", "e"]


def test_missing_test_references_flags_missing_file_missing_function_and_incomplete_reference(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text("def test_present():\n    pass\n\nasync def test_async():\n    pass\n\nclass T:\n    def test_nested(self):\n        pass\n", encoding="utf-8")
    refs = {
        "ok": {"path": "test_x.py", "name": "test_present"},
        "ok_async": {"path": "test_x.py", "name": "test_async"},
        "no_func": {"path": "test_x.py", "name": "test_absent"},
        "nested_is_not_top_level": {"path": "test_x.py", "name": "test_nested"},
        "no_file": {"path": "nope.py", "name": "test_present"},
        "incomplete": {"path": "test_x.py"},
    }
    problems = missing_test_references(refs, tmp_path)
    assert [p.split(":")[0] for p in problems] == ["incomplete", "nested_is_not_top_level", "no_file", "no_func"]


def test_value_mismatches_compares_numbers_by_tolerance_and_reports_both_directions() -> None:
    assert value_mismatches({"a": 2 / 3, "b": "x"}, {"a": 0.6666666666666666, "b": "x"}) == []
    problems = value_mismatches({"a": 1.0, "b": "x", "gone": 1}, {"a": 2.0, "b": "x", "extra": 5})
    assert [p.split(":")[0] for p in problems] == ["a", "extra", "gone"]
    assert value_mismatches({"flag": True}, {"flag": 1}) != []
