"""`import_side_effects`: what runs at import time, however `environ` is spelled, and whose side effect a probe records."""

from __future__ import annotations

import ast
import json
import os
import sys

import pytest

from py_ci_shared.import_side_effects import (
    ENV_REFRESH_FLAG,
    assert_imports_have_no_side_effects,
    assert_no_new_import_time_env_mutations,
    find_import_time_env_mutations,
    import_time_env_mutations,
    probe_import_side_effects,
)


@pytest.fixture(autouse=True)
def _refresh_may_grow(monkeypatch):
    """These tests seed and rewrite baselines; the shrink-only default has its own tests (test_core_baseline.py::TestShrinkOnlyRefresh)."""
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")


def _lines(source: str) -> list[int]:
    return [line for line, _ in import_time_env_mutations(ast.parse(source))]


class TestWhatRunsAtImport:
    def test_a_class_body_decorator_and_default_run_at_import(self):
        source = (
            "import os\n"
            "class T:\n"
            "    os.environ['A'] = '1'\n"
            "    def m(self):\n"
            "        os.environ['SAFE'] = '1'\n"
            "@register(os.environ.setdefault('B', ''))\n"
            "def f(x=os.environ.pop('C', None)):\n"
            "    os.environ['ALSO_SAFE'] = '1'\n"
            "g = lambda y=os.environ.setdefault('D', ''): os.environ.setdefault('LAMBDA_BODY', '')\n"
            "class U(Base, flag=os.environ.setdefault('E', '')):\n"
            "    pass\n"
        )
        assert _lines(source) == [3, 6, 7, 9, 10]

    @pytest.mark.parametrize(
        "source,expected",
        [
            ("from os import environ as E\nE['A'] = '1'\n", [2]),
            ("import os as o\no.environ.setdefault('A', '')\n", [2]),
            ("from os import putenv as p\np('A', '1')\n", [2]),
            ("from os import environ as E\nx = E.get('A')\n", []),
        ],
    )
    def test_an_aliased_environ_is_resolved(self, source, expected):
        assert _lines(source) == expected


class TestTheScan:
    def test_a_bom_test_file_is_scanned(self, tmp_path):
        (tmp_path / "test_bom.py").write_bytes(b"\xef\xbb\xbfimport os\nos.environ['A'] = '1'\n")
        assert find_import_time_env_mutations(tmp_path) == (1, ["test_bom.py::os.environ['A'] = '1'"])

    def test_an_unparsable_test_file_fails_the_gate(self, tmp_path):
        (tmp_path / "test_ok.py").write_text("x = 1\n", encoding="utf-8")
        assert_no_new_import_time_env_mutations(tmp_path)
        (tmp_path / "test_broken.py").write_text("def f(:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"test_broken.py"):
            assert_no_new_import_time_env_mutations(tmp_path)

    def test_a_missing_baseline_fails(self, tmp_path):
        (tmp_path / "test_ok.py").write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_no_new_import_time_env_mutations(tmp_path, tmp_path / "missing.json")

    def test_the_refresh_reaches_an_xdist_worker_through_the_environment(self, tmp_path, monkeypatch):
        (tmp_path / "test_x.py").write_text('import os\nos.environ["A"] = "1"\n', encoding="utf-8")
        baseline = tmp_path / "_b.json"
        monkeypatch.setattr(sys, "argv", ["-c"])
        with pytest.raises(pytest.fail.Exception):
            assert_no_new_import_time_env_mutations(tmp_path, baseline)
        monkeypatch.setenv("PY_CI_SHARED_REFRESH", ENV_REFRESH_FLAG)
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_import_time_env_mutations(tmp_path, baseline)
        monkeypatch.delenv("PY_CI_SHARED_REFRESH")
        assert json.loads(baseline.read_text(encoding="utf-8")) == ["test_x.py::os.environ['A'] = '1'"]
        assert_no_new_import_time_env_mutations(tmp_path, baseline)


class TestFirstPartyAttribution:
    @pytest.fixture
    def pkg_dir(self, tmp_path):
        mods = {
            "fakereq": "def get():\n    import socket\n    socket.socket()\n",
            "fp_calls": "import fakereq\ntry:\n    fakereq.get()\nexcept Exception:\n    pass\n",
            "dep_with_effect": "import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n",
            "fp_imports": "import dep_with_effect\nX = 1\n",
        }
        for name, body in mods.items():
            (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
        return tmp_path

    def _env(self, pkg_dir):
        return {**os.environ, "PYTHONPATH": str(pkg_dir)}

    def test_a_first_party_call_through_a_dependency_counts(self, pkg_dir):
        result = probe_import_side_effects(["fp_calls"], env=self._env(pkg_dir))
        assert [origin for _what, origin in result.violations] == ["fakereq"]
        assert result.chains == [["fakereq", "fp_calls"]]
        with pytest.raises(pytest.fail.Exception, match=r"\(from fp_calls\)"):
            assert_imports_have_no_side_effects(["fp_calls"], first_party=("fp_calls",), env=self._env(pkg_dir))

    def test_a_dependency_s_own_import_time_effect_does_not(self, pkg_dir):
        result = probe_import_side_effects(["fp_imports"], env=self._env(pkg_dir))
        assert result.chains == [["dep_with_effect"]]
        assert_imports_have_no_side_effects(["fp_imports"], first_party=("fp_imports",), env=self._env(pkg_dir))
