"""49-T7, 49-T11: capped lists say how many were left out; entry counts agree with their noun."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core.baseline import Baseline
from py_ci_shared._core.config import ConfigError, load_config
from py_ci_shared._core.console import capped
from py_ci_shared._core.outcome import entries_word


def test_capped_keeps_short_lists_whole() -> None:
    assert capped(["a", "b"], 20) == ["a", "b"]


def test_capped_says_how_many_it_left_out() -> None:
    items = [f"f{i}.md" for i in range(25)]
    shown = capped(items, 20)
    assert shown[:20] == items[:20]
    assert shown[-1] == "... and 5 more"


def test_entries_word_agrees_with_the_count() -> None:
    assert entries_word(1, count=True) == "1 entry"
    assert entries_word(0, count=True) == "0 entries"
    assert entries_word(2) == "entries"


def test_missing_baseline_names_grow_when_seeding_would_accept_findings(tmp_path: Path) -> None:
    outcome = Baseline(tmp_path / "demo_gate.json", gate="demo_gate").enforce(["src/a.py::big"])
    assert outcome.missing
    assert "(add --grow: seeding accepts the 1 current finding(s))" in outcome.message


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("loc_budget", 'Add "loc_budget" to enable = [...]'),
        ("naive_utcnw", 'Did you mean "naive_utcnow"?'),
        ("bandit_warn", "is a command-line tool, not a gate: run it as `py-ci-shared tool bandit_warn`"),
        ("pytest_run", "is a command-line tool, not a gate"),
    ],
)
def test_not_enabled_message_says_how_to_fix_it(tmp_path, asked: str, expected: str) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.py_ci_shared]\nenable = ["naive_utcnow"]\n', encoding="utf-8")
    config = load_config(tmp_path)
    assert config is not None
    with pytest.raises(ConfigError) as info:
        config.gate(asked)
    assert expected in str(info.value.args[0])
