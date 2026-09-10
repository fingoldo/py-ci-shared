"""Tests for prompt_field_parity, each on the shape that motivated it in glossum, in a tiny synthetic repo."""

from __future__ import annotations

import re
from pathlib import Path

from py_ci_shared.prompt_field_parity import (
    accessor_keys,
    consumed_names,
    declared_scalar_fields,
    invisible_keys,
    keys_in_schema,
    keys_in_source,
    persisted_names_sql,
    persisted_names_writer_keys,
    prompt_keys,
    structural_names_prompted,
    undemonstrated_fields,
    unconsumed_prompt_keys,
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
        schema = {"type": "object", "properties": {"claim": {"type": "string"}, "moderator": {"type": "object", "properties": {"value": {"type": "string"}}}}, "required": ["claim"]}

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
        _write(tmp_path, "pkg/models.py", "class Verdict:\n    productivity: str | None = None\n    ipa_correct: bool | None = None\n    items: list[str] = []\n")
        ddl = _write(tmp_path, "schema.sql", "CREATE TABLE t (\n    id SERIAL,\n    validation_ipa_correct BOOLEAN\n);\n")
        declared = declared_scalar_fields([tmp_path / "pkg"])
        persisted = persisted_names_sql([ddl], [])

        found = unpersisted_prompt_fields({"productivity": ["p.py"], "ipa_correct": ["p.py"], "items": ["p.py"]}, declared, persisted, column_prefixes=("validation_",))

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
