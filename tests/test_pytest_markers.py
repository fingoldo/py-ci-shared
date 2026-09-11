"""Unit tests for the shared marker-registration check, on real scratch trees (no mocking)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.pytest_markers import (
    assert_markers_registered,
    conftest_markers,
    find_unregistered_markers,
    pyproject_markers,
)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


_PYPROJECT = '[tool.pytest.ini_options]\nmarkers = ["slow: long", "xdist_like(name): grouped", "integration"]\n'


class TestRegistrationSources:
    def test_pyproject_entries_strip_the_description_and_an_argument_suffix(self, tmp_path):
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT})

        assert pyproject_markers(repo / "pyproject.toml") == {"slow", "xdist_like", "integration"}

    def test_every_conftest_counts_not_only_the_top_one(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "tests/conftest.py": 'def pytest_configure(config):\n    config.addinivalue_line("markers", "fuzz: gated")\n',
                "tests/deep/conftest.py": 'def pytest_configure(config):\n    config.addinivalue_line("markers", "gpu(n): needs a card")\n',
            },
        )

        assert conftest_markers(repo / "tests") == {"fuzz", "gpu"}


class TestUnregistered:
    def test_a_misspelled_marker_is_reported_with_its_file(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "pyproject.toml": _PYPROJECT,
                "tests/test_a.py": "import pytest\n\n@pytest.mark.slwo\ndef test_x():\n    pass\n",
                "tests/test_b.py": "import pytest\n\n@pytest.mark.slow\n@pytest.mark.parametrize('a', [1])\ndef test_y(a):\n    pass\n",
            },
        )

        assert find_unregistered_markers(repo) == {"slwo": ["tests/test_a.py"]}

    def test_a_docstring_snippet_counts(self, tmp_path):
        """mlframe S27: an unregistered marker that only appeared in a docstring was pasted into code later."""
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT, "tests/test_c.py": '"""Use @pytest.mark.no_xdist_parallel here."""\n'})

        assert "no_xdist_parallel" in find_unregistered_markers(repo)

    def test_pytestmark_list_form_and_plugin_builtins(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "pyproject.toml": _PYPROJECT,
                "tests/test_d.py": "import pytest\npytestmark = [pytest.mark.asyncio, pytest.mark.xdist_group('g'), pytest.mark.integration]\n",
            },
        )

        assert find_unregistered_markers(repo) == {}

    def test_extra_registered_covers_a_plugin_marker(self, tmp_path):
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT, "tests/test_e.py": "import pytest\n@pytest.mark.benchmark\ndef test_z():\n    pass\n"})

        assert find_unregistered_markers(repo, extra_registered=("benchmark",)) == {}


class TestAssertion:
    def test_it_fails_on_an_unregistered_marker(self, tmp_path):
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT, "tests/test_a.py": "import pytest\n@pytest.mark.slwo\ndef test_x():\n    pass\n"})

        with pytest.raises(pytest.fail.Exception, match="slwo"):
            assert_markers_registered(repo)

    def test_it_fails_when_the_parser_cannot_see_an_expected_registration(self, tmp_path):
        """The guard every local copy kept as its own smoke test: a moved pyproject table must not read as clean."""
        repo = _tree(tmp_path, {"pyproject.toml": "[tool.pytest]\nmarkers = ['slow: moved']\n", "tests/test_a.py": "def test_x():\n    pass\n"})

        with pytest.raises(pytest.fail.Exception, match="expected registered"):
            assert_markers_registered(repo, expect_registered=("slow",))

    def test_a_clean_tree_passes(self, tmp_path):
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT, "tests/test_a.py": "import pytest\n@pytest.mark.slow\ndef test_x():\n    pass\n"})

        assert_markers_registered(repo, expect_registered=("slow", "integration"))
