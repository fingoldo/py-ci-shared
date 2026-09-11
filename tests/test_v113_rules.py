"""Tests for the rules added in v1.13.0: private meta-test imports, import side effects, drained code-audit entries."""

from __future__ import annotations

import ast
import json
import re
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.import_side_effects import (
    ENV_REFRESH_FLAG,
    assert_imports_have_no_side_effects,
    assert_no_new_import_time_env_mutations,
    find_import_time_env_mutations,
    import_time_env_mutations,
    probe_import_side_effects,
)
from py_ci_shared.meta_private_imports import assert_no_private_meta_imports, private_meta_imports


class TestPrivateMetaImports:
    def _meta(self, tmp_path, body):
        (tmp_path / "test_a.py").write_text(textwrap.dedent(body), encoding="utf-8")
        return tmp_path

    def test_the_last_segment_is_judged_by_default(self, tmp_path):
        meta = self._meta(tmp_path, "from pkg.mod import _helper, public\nfrom pkg._impl import Public\nimport pkg.__about__\nfrom other import _x\nfrom . import _local\n")
        _parsed, found = private_meta_imports(sorted(meta.glob("test_*.py")), ("pkg",))
        assert found == {"test_a::pkg.mod._helper"}

    def test_any_segment_judges_the_module_path_too(self, tmp_path):
        meta = self._meta(tmp_path, "from pkg._impl import Public\nimport pkg._impl.sub\n")
        _parsed, found = private_meta_imports(sorted(meta.glob("test_*.py")), ("pkg",), any_segment=True)
        assert found == {"test_a::pkg._impl.Public", "test_a::pkg._impl.sub"}

    def test_a_private_import_fails_and_a_permitted_one_passes(self, tmp_path):
        meta = self._meta(tmp_path, "from pkg.mod import _helper\n")
        with pytest.raises(pytest.fail.Exception, match=re.escape("test_a::pkg.mod._helper")):
            assert_no_private_meta_imports(meta, ("pkg",))
        assert_no_private_meta_imports(meta, ("pkg",), permitted={"test_a::pkg.mod._helper"})

    def test_a_stale_permitted_entry_fails(self, tmp_path):
        meta = self._meta(tmp_path, "from pkg.mod import public\n")
        with pytest.raises(pytest.fail.Exception, match="no longer match"):
            assert_no_private_meta_imports(meta, ("pkg",), permitted={"test_a::pkg.mod._gone"})

    def test_an_empty_directory_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_no_private_meta_imports(tmp_path, ("pkg",))


_ENV_SAMPLE = """
import os
from os import environ

os.environ["A"] = "1"
os.environ.setdefault("B", "")
environ.update(C="3")
del os.environ["D"]
os.putenv("E", "5")

if True:
    os.environ["F"] = "6"

def f():
    os.environ["SAFE"] = "ok"

class K:
    def m(self):
        os.environ.setdefault("ALSO_SAFE", "ok")

x = os.environ.get("READ_ONLY")
"""


class TestImportTimeEnvScan:
    def test_it_sees_import_time_writes_and_ignores_callables_and_reads(self):
        lines = [line for line, _ in import_time_env_mutations(ast.parse(_ENV_SAMPLE))]
        assert lines == [5, 6, 7, 8, 9, 12]

    def test_keys_carry_the_statement_not_the_line(self, tmp_path):
        (tmp_path / "test_x.py").write_text('import os\nos.environ["A"] = "1"\nos.environ["A"] = "1"\n', encoding="utf-8")
        before = find_import_time_env_mutations(tmp_path)[1]
        (tmp_path / "test_x.py").write_text('import os\n# moved\n\nos.environ["A"] = "1"\nos.environ["A"] = "1"\n', encoding="utf-8")
        after = find_import_time_env_mutations(tmp_path)[1]
        assert before == after == ["test_x.py::os.environ['A'] = '1'", "test_x.py::os.environ['A'] = '1'#1"]

    def test_a_new_site_fails_without_a_baseline(self, tmp_path):
        (tmp_path / "test_x.py").write_text('import os\nos.environ["A"] = "1"\n', encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="IMPORT time"):
            assert_no_new_import_time_env_mutations(tmp_path)

    def test_a_baselined_site_passes_and_a_stale_entry_fails(self, tmp_path):
        (tmp_path / "test_x.py").write_text('import os\nos.environ["A"] = "1"\n', encoding="utf-8")
        baseline = tmp_path / "_b.json"
        baseline.write_text(json.dumps(["test_x.py::os.environ['A'] = '1'"]), encoding="utf-8")
        assert_no_new_import_time_env_mutations(tmp_path, baseline)
        (tmp_path / "test_x.py").write_text("import os\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="no longer reproduced"):
            assert_no_new_import_time_env_mutations(tmp_path, baseline)

    def test_the_refresh_flag_rewrites_the_baseline(self, tmp_path, monkeypatch):
        (tmp_path / "test_x.py").write_text('import os\nos.environ["A"] = "1"\n', encoding="utf-8")
        baseline = tmp_path / "_b.json"
        monkeypatch.setattr(sys, "argv", ["pytest", ENV_REFRESH_FLAG])
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_import_time_env_mutations(tmp_path, baseline)
        assert json.loads(baseline.read_text(encoding="utf-8")) == ["test_x.py::os.environ['A'] = '1'"]

    def test_an_empty_root_fails(self, tmp_path):
        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_no_new_import_time_env_mutations(tmp_path)


class TestImportProbe:
    @pytest.fixture
    def pkg_dir(self, tmp_path):
        mods = {
            "clean_mod": "X = 1\n",
            "net_mod": "import socket\nsocket.socket()\n",
            "swallowing_mod": "import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n",
            "env_mod": 'import os\nos.environ["PROBE_TOUCHED"] = "1"\n',
            "db_mod": 'try:\n    open("data.sqlite")\nexcept OSError:\n    pass\n',
            "thirdparty_dep": "import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n",
            "firstparty_mod": "import thirdparty_dep\nX = 1\n",
        }
        for name, body in mods.items():
            (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
        return tmp_path

    def _env(self, pkg_dir):
        import os

        return {**os.environ, "PYTHONPATH": str(pkg_dir)}

    def test_a_clean_module_passes(self, pkg_dir):
        assert_imports_have_no_side_effects(["clean_mod"], env=self._env(pkg_dir))

    def test_a_socket_fails(self, pkg_dir):
        with pytest.raises(pytest.fail.Exception, match="net_mod"):
            assert_imports_have_no_side_effects(["net_mod"], env=self._env(pkg_dir))

    def test_a_swallowed_socket_still_fails(self, pkg_dir):
        result = probe_import_side_effects(["swallowing_mod"], env=self._env(pkg_dir))
        assert result.violations and not result.failed
        with pytest.raises(pytest.fail.Exception, match=re.escape("socket.socket")):
            assert_imports_have_no_side_effects(["swallowing_mod"], env=self._env(pkg_dir))

    def test_a_dependency_s_side_effect_is_attributed_to_it_and_first_party_skips_it(self, pkg_dir):
        result = probe_import_side_effects(["firstparty_mod"], env=self._env(pkg_dir))
        assert [origin for _what, origin in result.violations] == ["thirdparty_dep"]
        with pytest.raises(pytest.fail.Exception, match=re.escape("(from thirdparty_dep)")):
            assert_imports_have_no_side_effects(["firstparty_mod"], env=self._env(pkg_dir))
        assert_imports_have_no_side_effects(["firstparty_mod"], first_party=("firstparty_mod",), env=self._env(pkg_dir))
        with pytest.raises(pytest.fail.Exception, match="swallowing_mod"):
            assert_imports_have_no_side_effects(["swallowing_mod"], first_party=("swallowing_mod",), env=self._env(pkg_dir))

    def test_an_environment_write_fails_only_when_blocked_and_not_allowed(self, pkg_dir):
        assert_imports_have_no_side_effects(["env_mod"], env=self._env(pkg_dir))
        with pytest.raises(pytest.fail.Exception, match="PROBE_TOUCHED"):
            assert_imports_have_no_side_effects(["env_mod"], block_environ=True, env=self._env(pkg_dir))
        assert_imports_have_no_side_effects(["env_mod"], block_environ=True, allowed_env_keys={"PROBE_TOUCHED"}, env=self._env(pkg_dir))

    def test_a_forbidden_open_fails(self, pkg_dir):
        assert_imports_have_no_side_effects(["db_mod"], env=self._env(pkg_dir))
        with pytest.raises(pytest.fail.Exception, match=re.escape("data.sqlite")):
            assert_imports_have_no_side_effects(["db_mod"], forbid_open_tokens=(".sqlite",), env=self._env(pkg_dir))

    def test_an_unimportable_target_fails_unless_tolerated(self, pkg_dir):
        with pytest.raises(pytest.fail.Exception, match="could not be imported"):
            assert_imports_have_no_side_effects(["clean_mod", "no_such_mod"], env=self._env(pkg_dir))
        assert_imports_have_no_side_effects(["clean_mod", "no_such_mod"], tolerate_unimportable=True, env=self._env(pkg_dir))
        with pytest.raises(pytest.fail.Exception, match="nothing was probed"):
            assert_imports_have_no_side_effects(["no_such_mod"], tolerate_unimportable=True, env=self._env(pkg_dir))

    def test_isolation_probes_each_target_in_its_own_interpreter(self, pkg_dir):
        result = probe_import_side_effects(["clean_mod", "net_mod"], isolate=True, env=self._env(pkg_dir))
        assert len(result.failed) == 1 and result.failed[0].startswith("net_mod")


class TestDrainedCodeAuditEntries:
    @pytest.fixture
    def audit(self, monkeypatch):
        code_audit = pytest.importorskip("pyutilz.dev.code_audit")
        findings: list = []
        monkeypatch.setattr(code_audit, "run_all", lambda root, checks=None, exclude_dirs=frozenset(): list(findings))
        return findings

    def _finding(self, snippet):
        return SimpleNamespace(check="c", file="m.py", line=1, snippet=snippet, severity="low", detail="d")

    def test_a_drained_entry_fails_by_default(self, audit, tmp_path):
        from py_ci_shared.code_audit_meta import assert_no_new_code_audit_findings

        baseline = tmp_path / "_b.json"
        audit.append(self._finding("x = 1"))
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(tmp_path, baseline)
        assert_no_new_code_audit_findings(tmp_path, baseline)
        audit.clear()
        with pytest.raises(pytest.fail.Exception, match="no longer match a finding"):
            assert_no_new_code_audit_findings(tmp_path, baseline)
        assert_no_new_code_audit_findings(tmp_path, baseline, fail_on_drained=False)


class TestIniMarkers:
    def test_markers_in_pytest_ini_count_as_registered(self, tmp_path):
        from py_ci_shared.pytest_markers import assert_markers_registered, ini_markers

        (tmp_path / "pytest.ini").write_text(
            "[pytest]\nmarkers =\n    browser: drives a real browser. Measured: 29 such tests,\n        31 s of the run.\n    real_database(reason): opts out\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("import pytest\n\n@pytest.mark.browser\n@pytest.mark.real_database\ndef test_x():\n    pass\n", encoding="utf-8")
        assert {"browser", "real_database"} <= ini_markers(tmp_path)
        assert_markers_registered(tmp_path, expect_registered=("browser", "real_database"))

    def test_setup_cfg_uses_its_own_section(self, tmp_path):
        from py_ci_shared.pytest_markers import ini_markers

        (tmp_path / "setup.cfg").write_text("[tool:pytest]\nmarkers =\n    slow: slow\n[pytest]\nmarkers =\n    wrong: not read here\n", encoding="utf-8")
        assert ini_markers(tmp_path) == {"slow"}
