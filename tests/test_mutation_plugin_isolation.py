"""The cold nested run disables the dash plugin only where nothing in the tree can reach what it provides.

Disabling a plugin that some test uses would turn that test into an error, which every mutant then looks like a
kill of, so the condition is the whole point and each branch of it is pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import _mutation_runner as runner


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
    assert runner._plugin_isolation_flags(root) == ["-p", "no:dash"]


def test_a_test_that_uses_a_dash_fixture_keeps_the_plugin(tmp_path, dash_installed):
    root = _tree(tmp_path, {"tests/test_ui.py": "def test_ui(dash_duo):\n    assert dash_duo is not None\n"})
    assert runner._plugin_isolation_flags(root) == []


def test_a_conftest_that_names_dash_keeps_the_plugin(tmp_path, dash_installed):
    root = _tree(tmp_path, {"tests/conftest.py": "import dash\n", "tests/test_m.py": "def test_m():\n    pass\n"})
    assert runner._plugin_isolation_flags(root) == []


def test_no_plugin_installed_means_no_flag(tmp_path, dash_absent):
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    pass\n"})
    assert runner._plugin_isolation_flags(root) == []
