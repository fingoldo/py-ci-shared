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


_QUIET = ("hypothesispytest", "benchmark", "anyio")


@pytest.fixture
def quiet_plugins_installed(monkeypatch):
    """All three optional plugins are present, so the flags depend only on what the tree names."""
    monkeypatch.setattr(runner, "_installed_plugin_names", lambda: frozenset(_QUIET))


def _flags(root: Path) -> "list[str]":
    """The isolation flags for *root* with the per-tree caches cleared."""
    runner._tree_mentions_dash.cache_clear()
    runner._plugins_named_in.cache_clear()
    return runner._plugin_isolation_flags(root)


def test_a_tree_that_names_none_of_the_optional_plugins_switches_all_three_off(tmp_path, dash_absent, quiet_plugins_installed):
    """With nothing naming hypothesis, benchmark or anyio, a nested run starts without them."""
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    assert 1 == 1\n"})
    flags = _flags(root)
    assert all(f"no:{name}" in flags for name in _QUIET)


@pytest.mark.parametrize(
    ("source", "kept"),
    [
        ("from hypothesis import given\n\n\ndef test_m():\n    pass\n", "hypothesispytest"),
        ("def test_m(benchmark):\n    benchmark(len, [1])\n", "benchmark"),
        ("import pytest\n\n\n@pytest.mark.anyio\nasync def test_m():\n    pass\n", "anyio"),
        ("import trio\n\n\ndef test_m():\n    pass\n", "anyio"),
    ],
)
def test_a_plugin_the_tree_names_stays_on_and_the_others_go(tmp_path, dash_absent, quiet_plugins_installed, source, kept):
    """A test that names a plugin keeps it; the two it does not name are still switched off."""
    root = _tree(tmp_path, {"tests/test_m.py": source})
    flags = _flags(root)
    assert f"no:{kept}" not in flags
    assert all(f"no:{name}" in flags for name in _QUIET if name != kept)


def test_an_option_of_a_plugin_in_the_pytest_configuration_keeps_it(tmp_path, dash_absent, quiet_plugins_installed):
    """``addopts`` naming ``--benchmark-disable`` is rejected as an unknown option once the plugin is off, so the plugin stays on."""
    root = _tree(tmp_path, {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = "--benchmark-disable"\n', "tests/test_m.py": "def test_m():\n    pass\n"})
    flags = _flags(root)
    assert "no:benchmark" not in flags
    assert "no:hypothesispytest" in flags and "no:anyio" in flags


def test_a_plugin_that_is_not_installed_gets_no_switch(tmp_path, dash_absent, monkeypatch):
    """Only plugins registered in the interpreter get a switch."""
    monkeypatch.setattr(runner, "_installed_plugin_names", lambda: frozenset({"benchmark"}))
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    pass\n"})
    flags = _flags(root)
    assert "no:benchmark" in flags
    assert "no:hypothesispytest" not in flags and "no:anyio" not in flags


def test_an_unreadable_test_file_keeps_every_optional_plugin_and_says_so(tmp_path, dash_absent, quiet_plugins_installed, monkeypatch, caplog):
    """A file that cannot be read is unknown, so none of the three is switched off, and the substitution is logged at WARNING."""
    root = _tree(tmp_path, {"tests/test_m.py": "def test_m():\n    pass\n"})
    real = Path.read_text

    def failing(self, *args, **kwargs):
        """Fail for the test file, as a permission error would."""
        if self.name == "test_m.py":
            raise PermissionError("no")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing)
    with caplog.at_level("WARNING"):
        flags = _flags(root)
    assert not any(f"no:{name}" in flags for name in _QUIET)
    assert any("optional plugins" in record.getMessage() for record in caplog.records)
