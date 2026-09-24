"""Unit tests for the shared marker-registration check, on real scratch trees (no mocking)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from py_ci_shared.pytest_markers import (
    assert_markers_registered,
    conftest_markers,
    find_unregistered_markers,
    ini_markers,
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
        repo = _tree(tmp_path, {"pyproject.toml": "[tool.pytest.options]\nmarkers = ['slow: moved']\n", "tests/test_a.py": "def test_x():\n    pass\n"})

        with pytest.raises(pytest.fail.Exception, match="expected registered"):
            assert_markers_registered(repo, expect_registered=("slow",))

    def test_a_clean_tree_passes(self, tmp_path):
        repo = _tree(tmp_path, {"pyproject.toml": _PYPROJECT, "tests/test_a.py": "import pytest\n@pytest.mark.slow\ndef test_x():\n    pass\n"})

        assert_markers_registered(repo, expect_registered=("slow", "integration"))


class TestAuditRegressions:
    _USE = "import pytest\n@pytest.mark.alpha\n@pytest.mark.beta\n@pytest.mark.gamma\n@pytest.mark.delta\ndef test_x():\n    pass\n"

    def test_native_toml_tables_and_pytest_toml_register(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "pyproject.toml": "[tool.pytest]\nmarkers = ['alpha: native pytest 9 table']\n",
                "pytest.toml": "[pytest]\nmarkers = ['beta: pytest.toml']\n",
                "tests/test_a.py": "import pytest\n@pytest.mark.alpha\n@pytest.mark.beta\ndef test_x():\n    pass\n",
            },
        )
        assert find_unregistered_markers(repo) == {}
        (repo / "pytest.toml").unlink()
        assert find_unregistered_markers(repo) == {"beta": ["tests/test_a.py"]}

    def test_the_root_conftest_and_non_literal_registrations_count(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "conftest.py": 'def pytest_configure(config):\n    config.addinivalue_line("markers", "alpha: root conftest")\n',
                "tests/conftest.py": (
                    'MARKERS = ("beta: from a constant", "gamma(n): too")\n'
                    'DELTA = "delta: named constant"\n'
                    "def pytest_configure(config):\n"
                    "    for line in MARKERS:\n"
                    '        config.addinivalue_line("markers", line)\n'
                    '    config.addinivalue_line("markers", DELTA)\n'
                ),
                "tests/test_a.py": self._USE,
            },
        )
        assert find_unregistered_markers(repo) == {}

    def test_an_unresolvable_registration_is_named_in_the_failure(self, tmp_path):
        repo = _tree(
            tmp_path,
            {
                "tests/conftest.py": "def pytest_configure(config):\n    for line in load():\n        config.addinivalue_line('markers', line)\n",
                "tests/test_a.py": "import pytest\n@pytest.mark.alpha\ndef test_x():\n    pass\n",
            },
        )
        with pytest.raises(pytest.fail.Exception, match=r"could not be read statically(.|\n)*conftest.py:3"):
            assert_markers_registered(repo)

    def test_a_duplicate_markers_option_in_the_pytest_section_is_surfaced(self, tmp_path):
        repo = _tree(tmp_path, {"tox.ini": "[pytest]\nmarkers =\n    alpha: a\nmarkers =\n    beta: b\n", "tests/test_a.py": "def test_x():\n    pass\n"})
        with pytest.raises(ValueError, match=re.escape("tox.ini")):
            ini_markers(repo)

    def test_a_duplicate_in_another_tool_s_section_does_not_hide_the_markers(self, tmp_path):
        repo = _tree(tmp_path, {"tox.ini": "[testenv]\ndeps = a\ndeps = b\n[pytest]\nmarkers =\n    alpha: a\n"})
        assert ini_markers(repo) == {"alpha"}

    def test_bom_and_unparsable_conftests(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_bytes(b'\xef\xbb\xbfdef pytest_configure(config):\n    config.addinivalue_line("markers", "alpha: a")\n')
        assert conftest_markers(tests) == {"alpha"}
        (tests / "sub").mkdir()
        (tests / "sub" / "conftest.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="could not be parsed"):
            conftest_markers(tests)
