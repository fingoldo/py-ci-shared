"""The cold nested run switches off each plugin only where nothing in the tree can reach what that plugin provides.

Disabling a plugin that some test uses would turn that test into an error, which every mutant then looks like a
kill of, so each condition is the whole point and each branch of it is pinned here. The dash switch and the
py_ci_shared switch are independent: a tree can lose one, both, or neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import _mutation_runner as runner

_GATES_TABLE = '[tool.py_ci_shared]\ncheck = "on"\n'
_DASH_SWITCH = "no:dash"
_PLUGIN_SWITCH = "no:py_ci_shared"


@pytest.fixture
def dash_installed(monkeypatch):
    monkeypatch.setattr(runner, "_dash_plugin_installed", lambda: True)


@pytest.fixture
def dash_absent(monkeypatch):
    monkeypatch.setattr(runner, "_dash_plugin_installed", lambda: False)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    runner._tree_mentions_dash.cache_clear()
    return root


def test_a_tree_that_never_names_dash_disables_the_plugin(tmp_path, dash_installed):
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    assert 1 == 1\n"})
    assert _DASH_SWITCH in runner._plugin_isolation_flags(root)


def test_a_test_that_uses_a_dash_fixture_keeps_the_plugin(tmp_path, dash_installed):
    root = _tree(tmp_path, {"tests/test_ui.py": "def test_ui(dash_duo):\n    assert dash_duo is not None\n"})
    assert _DASH_SWITCH not in runner._plugin_isolation_flags(root)


def test_a_conftest_that_names_dash_keeps_the_plugin(tmp_path, dash_installed):
    root = _tree(tmp_path, {"tests/conftest.py": "import dash\n", "tests/test_m.py": "def test_m():\n    pass\n"})
    assert _DASH_SWITCH not in runner._plugin_isolation_flags(root)


def test_no_plugin_installed_means_no_dash_flag(tmp_path, dash_absent):
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    pass\n"})
    assert _DASH_SWITCH not in runner._plugin_isolation_flags(root)


def test_a_tree_without_gates_disables_the_py_ci_shared_plugin(tmp_path, dash_absent):
    """Without a ``[tool.py_ci_shared]`` table there are no gate items, so the plugin is only startup cost (measured 14.7 s vs 28.9 s)."""
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    pass\n"})
    assert _PLUGIN_SWITCH in runner._plugin_isolation_flags(root)


def test_a_tree_that_declares_gates_keeps_the_py_ci_shared_plugin(tmp_path, dash_absent):
    """With gates declared the plugin generates their items, so a nested run must keep it to see the same gate verdicts."""
    root = _tree(tmp_path, {"pyproject.toml": _GATES_TABLE, "tests/test_m.py": "def test_m():\n    pass\n"})
    assert _PLUGIN_SWITCH not in runner._plugin_isolation_flags(root)


def test_the_gates_table_is_read_from_the_nearest_pyproject_above(tmp_path, dash_absent):
    """A tree under a subdirectory with no pyproject of its own inherits the table of the nearest one above it."""
    _tree(tmp_path, {"pyproject.toml": _GATES_TABLE})
    nested = _tree(tmp_path / "pkg", {"tests/test_m.py": "def test_m():\n    pass\n"})
    assert runner._gates_configured(nested) is True


def test_an_unreadable_pyproject_keeps_the_plugin_and_says_so(tmp_path, dash_absent, caplog):
    """A pyproject that does not parse is read as declaring gates, so a broken file cannot turn a gate off, and the substitution is logged."""
    root = _tree(tmp_path, {"pyproject.toml": "[tool.py_ci_shared\nthis is not toml", "tests/test_m.py": "def test_m():\n    pass\n"})
    with caplog.at_level("WARNING"):
        flags = runner._plugin_isolation_flags(root)
    assert _PLUGIN_SWITCH not in flags
    assert any("unreadable" in record.getMessage() for record in caplog.records)
