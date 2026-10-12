from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import clear_parse_cache
from py_ci_shared.llm_output_caps import (
    assert_each_known_cap_states_its_reason,
    assert_known_caps_only_shrink,
    assert_no_new_output_caps,
    numeric_output_caps,
)

NAME_HINTS = ("CAP_TOKENS", "MAX_OUTPUT_TOKENS", "_default_max_tokens")
DEFAULT_HINTS = ("max_tokens", "max_completion_tokens", "max_output_tokens")


def _write(root: Path, text: str, rel: str = "m.py") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    clear_parse_cache()  # a same-size rewrite within one mtime tick must not reuse the cached tree


def _found(root: Path) -> set[tuple[str, str, int]]:
    return numeric_output_caps([root], repo_root=root, name_hints=NAME_HINTS, default_hints=DEFAULT_HINTS)


def test_a_planted_positive_cap_is_found(tmp_path: Path) -> None:
    _write(tmp_path, "CAP_TOKENS = 4096\n")
    assert _found(tmp_path) == {("m.py", "CAP_TOKENS", 4096)}


def test_an_annotated_assignment_is_found(tmp_path: Path) -> None:
    _write(tmp_path, "class C:\n    _default_max_tokens: int = 8192\n")
    assert _found(tmp_path) == {("m.py", "_default_max_tokens", 8192)}


@pytest.mark.parametrize("text", ["CAP_TOKENS = 0\n", "CAP_TOKENS = None\n", "CAP_TOKENS = True\n", "CAP_TOKENS = limit\n", "CAP_TOKENS = -1\n"])
def test_zero_none_bool_names_and_negatives_are_not_caps(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    assert _found(tmp_path) == set()


def test_a_positive_default_parameter_is_found_positional_and_keyword_only(tmp_path: Path) -> None:
    _write(tmp_path, "def call(prompt, max_tokens=4096, *, max_output_tokens=2048, other=5):\n    pass\n")
    assert _found(tmp_path) == {("m.py", "max_tokens", 4096), ("m.py", "max_output_tokens", 2048)}


def test_a_zero_default_parameter_is_the_unset_marker_not_a_cap(tmp_path: Path) -> None:
    _write(tmp_path, "def call(prompt, max_tokens=0):\n    pass\n")
    assert _found(tmp_path) == set()


def test_a_name_without_a_hint_is_not_a_cap(tmp_path: Path) -> None:
    """Mutation kill: dropping the name-hint check makes this positive int literal a cap and fails here."""
    _write(tmp_path, "BATCH_SIZE = 4096\n")
    assert _found(tmp_path) == set()


def test_a_parameter_outside_the_default_hints_is_not_a_cap(tmp_path: Path) -> None:
    _write(tmp_path, "def call(prompt, max_tokens_per_chunk=4096):\n    pass\n")
    assert _found(tmp_path) == set()


def test_a_known_cap_passes() -> None:
    cap = ("m.py", "CAP_TOKENS", 4096)
    assert_no_new_output_caps({cap}, {cap: "legacy batch ceiling, delete once the catalogue ceiling is wired"})


def test_a_new_cap_fails_and_names_it() -> None:
    with pytest.raises(AssertionError, match="CAP_TOKENS"):
        assert_no_new_output_caps({("m.py", "CAP_TOKENS", 4096)}, {})


def test_a_removed_known_cap_fails_the_shrink_test() -> None:
    cap = ("m.py", "CAP_TOKENS", 4096)
    with pytest.raises(AssertionError, match="CAP_TOKENS"):
        assert_known_caps_only_shrink(set(), {cap: "reason"})


def test_a_still_present_known_cap_passes_the_shrink_test() -> None:
    cap = ("m.py", "CAP_TOKENS", 4096)
    assert_known_caps_only_shrink({cap}, {cap: "reason"})


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_empty_reason_fails(reason: str) -> None:
    with pytest.raises(AssertionError, match="no reason"):
        assert_each_known_cap_states_its_reason({("m.py", "CAP_TOKENS", 4096): reason})


def test_a_stated_reason_passes() -> None:
    assert_each_known_cap_states_its_reason({("m.py", "CAP_TOKENS", 4096): "why it exists"})


def test_an_unparsable_file_is_an_error_not_a_skip(tmp_path: Path) -> None:
    _write(tmp_path, "def broken(:\n")
    with pytest.raises(Exception, match=r"m.py"):
        _found(tmp_path)
