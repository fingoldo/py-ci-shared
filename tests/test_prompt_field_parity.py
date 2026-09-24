"""Tests for prompt_field_parity, each on the shape that motivated it in glossum, in a tiny synthetic repo."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared._core import UnparsedFilesError
from py_ci_shared.prompt_field_parity import (
    accessor_keys,
    consumed_names,
    ddl_columns,
    declared_scalar_fields,
    invisible_keys,
    keys_in_schema,
    keys_in_source,
    persisted_names_sql,
    persisted_names_writer_keys,
    prompt_keys,
    string_literals,
    structural_names_prompted,
    unconsumed_prompt_keys,
    undemonstrated_fields,
    unpersisted_prompt_fields,
)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


PROMPT = 'PROMPT = """\nReturn: {"word": "...", "verdict": "confirmed", "ipa_correct": true|false, "properties": {}}\n"""\n'


class TestWhereFieldsComeFrom:
    def test_a_field_in_a_prose_fence_is_extracted_and_structural_names_are_not(self) -> None:
        """The motivating shape: ipa_correct asked for inside a prompt's JSON fence, read by nothing."""
        assert keys_in_source(PROMPT) == {"word", "verdict", "ipa_correct"}

    def test_a_json_schema_dict_gives_its_property_names_not_its_vocabulary(self) -> None:
        schema = {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "moderator": {"type": "object", "properties": {"value": {"type": "string"}}}},
            "required": ["claim"],
        }

        assert keys_in_schema(schema) == {"claim", "moderator", "value"}

    def test_a_plain_example_payload_gives_every_key(self) -> None:
        assert keys_in_schema({"claim": "...", "items": [{"subject": "x"}]}) == {"claim", "items", "subject"}

    def test_files_and_schemas_combine_per_source(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "prompts/p.py", PROMPT)

        found = prompt_keys([path.parent], schemas={"SWEEP_SCHEMA": {"properties": {"claim": {}}}})

        assert found["ipa_correct"] == {"p.py"} and found["claim"] == {"SWEEP_SCHEMA"}


class TestWhatCountsAsReading:
    def test_benchmark_and_gold_modules_are_not_consumers(self, tmp_path: Path) -> None:
        """A grader reading the raw dict scores the model; it does not consume the field."""
        _write(tmp_path, "pkg/benchmark_ranker/grade.py", 'x = item.get("similarity_type_correct")\n')
        _write(tmp_path, "pkg/_gold_checks.py", 'y = item.get("gloss_correct")\n')
        _write(tmp_path, "pkg/saver.py", 'z = item.get("ipa_correct")\n')

        names = consumed_names([tmp_path / "pkg"], exclude_parts={"benchmark_ranker"}, exclude_name_fragments=("_gold_",))

        assert "ipa_correct" in names and not ({"similarity_type_correct", "gloss_correct"} & names)

    def test_a_run_time_key_is_cleared_only_by_a_real_accessor(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/ff.py", 'ipa = _ff_get_ol(ff, "ipa")\n')
        built = accessor_keys([tmp_path / "pkg"], re.compile(r'_ff_get_(\w+)\(\s*\w+\s*,\s*["\'](\w+)["\']'))

        found = unconsumed_prompt_keys({"ol_ipa": ["p.py"], "target_register": ["p.py"]}, consumed=set(), built_at_run_time=built)

        assert found == {"target_register": ["p.py"]}

    def test_a_verdict_field_is_never_cleared_through_a_run_time_key(self) -> None:
        """Stripping target_ from target_gender_correct leaves the consumed gender_correct; that must not clear it."""
        found = unconsumed_prompt_keys({"target_gender_correct": ["p.py"]}, consumed={"gender_correct"}, built_at_run_time={"target_gender_correct"})

        assert found == {"target_gender_correct": ["p.py"]}


class TestWhatCountsAsStored:
    def test_a_declared_field_that_reaches_no_column_is_reported(self, tmp_path: Path) -> None:
        """The productivity shape: parsed onto an object, written to no column."""
        _write(
            tmp_path, "pkg/models.py", "class Verdict:\n    productivity: str | None = None\n    ipa_correct: bool | None = None\n    items: list[str] = []\n"
        )
        ddl = _write(tmp_path, "schema.sql", "CREATE TABLE t (\n    id SERIAL,\n    validation_ipa_correct BOOLEAN\n);\n")
        declared = declared_scalar_fields([tmp_path / "pkg"])
        persisted = persisted_names_sql([ddl], [])

        found = unpersisted_prompt_fields(
            {"productivity": ["p.py"], "ipa_correct": ["p.py"], "items": ["p.py"]}, declared, persisted, column_prefixes=("validation_",)
        )

        assert declared == {"productivity", "ipa_correct"}
        assert found == {"productivity": ["p.py"]}

    def test_a_file_store_counts_the_keys_its_writer_writes(self, tmp_path: Path) -> None:
        _write(tmp_path, "store/writer.py", 'row = {"claim": c}\nextra = dict(moderator=m)\n')

        assert {"claim", "moderator"} <= persisted_names_writer_keys([tmp_path / "store"])


class TestTheGatesOwnBlindSpots:
    def test_keys_the_regex_cannot_see_are_reported(self) -> None:
        assert invisible_keys('PROMPT = """{"Severity": "low", "ok": 1, "fine_key": 2}"""\n') == {"Severity", "ok"}

    def test_a_field_described_but_never_demonstrated_is_reported(self) -> None:
        assert undemonstrated_fields('P = """Say whether `gloss_correct` holds.\n{"ipa_correct": true}"""\n') == {"gloss_correct"}

    def test_a_structural_name_the_prompt_asks_for_is_reported(self) -> None:
        assert structural_names_prompted(['P = """{"description": "fill this in"}"""\n']) == {"description"}


class TestAuditRegressions:
    def test_defs_names_are_definitions_not_fields(self) -> None:
        schema = {
            "type": "object",
            "properties": {"outer": {"$ref": "#/$defs/Inner"}},
            "$defs": {"Inner": {"type": "object", "properties": {"leaf": {"type": "string"}}}},
            "definitions": {"Legacy": {"type": "object", "properties": {"old_leaf": {"type": "integer"}}}},
        }
        assert keys_in_schema(schema) == {"outer", "leaf", "old_leaf"}

    def test_ddl_columns_of_any_type_and_quoted_names(self, tmp_path: Path) -> None:
        ddl = _write(
            tmp_path,
            "schema.sql",
            'CREATE TABLE t (\n  id SERIAL PRIMARY KEY,\n  score INT NOT NULL,\n  "Flag Col" BOOL,\n  ratio FLOAT,\n'
            "  code CHAR(2),\n  price NUMERIC(10, 2),\n  CONSTRAINT pk PRIMARY KEY (id)\n);\n"
            "ALTER TABLE t ADD COLUMN IF NOT EXISTS extra TINYINT;\n-- CREATE TABLE ghost (commented_out INT);\n",
        )
        assert persisted_names_sql([ddl], []) == {"id", "score", "flag col", "ratio", "code", "price", "extra"}
        assert ddl_columns("SELECT a FROM b") == set()

    def test_an_unparsable_prompt_module_raises_instead_of_contributing_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "prompts/ok.py", PROMPT)
        _write(tmp_path, "prompts/newer.py", 'PROMPT = """{"lost_field": 1}"""\ndef (:\n')
        with pytest.raises(UnparsedFilesError, match=re.escape("newer.py")):
            prompt_keys([tmp_path / "prompts"])
        with pytest.raises(UnparsedFilesError):
            consumed_names([tmp_path / "prompts"])
        with pytest.raises(SyntaxError):
            string_literals("def (:\n", strict=True)
        assert string_literals("def (:\n", strict=False) == []

    def test_an_unparsable_source_string_raises_by_default(self) -> None:
        with pytest.raises(SyntaxError):
            string_literals("def (:\n")
        with pytest.raises(SyntaxError):
            keys_in_source('X = """{"field_a": 1}"""\ndef (:\n')
        assert keys_in_source('X = """{"field_a": 1}"""\n') == {"field_a"}

    def test_non_utf8_files_raise_a_located_error_not_a_bare_decode_error(self, tmp_path: Path) -> None:
        (tmp_path / "w").mkdir()
        (tmp_path / "w" / "writer.py").write_bytes(b"row = {'claim': 1}  # \xff\n")
        with pytest.raises(UnparsedFilesError, match=re.escape("writer.py")):
            persisted_names_writer_keys([tmp_path / "w"])
        with pytest.raises(UnparsedFilesError, match=re.escape("writer.py")):
            accessor_keys([tmp_path / "w"], re.compile(r"(x)_(y)"))

    def test_a_bom_prompt_module_is_read(self, tmp_path: Path) -> None:
        (tmp_path / "p").mkdir()
        (tmp_path / "p" / "prompt.py").write_bytes(b"\xef\xbb\xbf" + PROMPT.encode("utf-8"))
        assert "ipa_correct" in prompt_keys([tmp_path / "p"])

    def test_abstract_and_optional_containers_are_not_scalars(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "pkg/models.py",
            "from typing import Optional, Sequence, Mapping, Annotated\n"
            "class V:\n    tags: Sequence[str] = ()\n    extra: Mapping[str, int] = {}\n    kinds: frozenset[str] = frozenset()\n"
            "    maybe: Optional[list] = None\n    ann: Annotated[list[int], 'x'] = []\n    score: Optional[int] = None\n    listing_id: int = 0\n",
        )
        assert declared_scalar_fields([tmp_path / "pkg"]) == {"score", "listing_id"}
