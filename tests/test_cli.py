"""``py-ci-shared``: the umbrella CLI over [tool.py_ci_shared], and the config loader and runner behind it."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Union

import pytest

from py_ci_shared import __version__, cli, registry
from py_ci_shared._core.config import ConfigError, load_config, resolve_kwargs
from py_ci_shared._core.refresh import ENV_VAR
from py_ci_shared._core.runner import ERROR, FAILED, PASSED, GateResult, budget_verdict, run_gate

CLEAN = "SQL = 'SELECT 1'\n\n\ndef f(x):\n    return x == SQL\n"
DIRTY = "SQL = 'SELECT 1'\n\n\ndef f(x):\n    return x is SQL\n"


def _repo(tmp_path: Path, table: str, files: "dict[str, str] | None" = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_bytes(("[project]\nname = 'demo'\nversion = '0.1.0'\n\n" + table).encode())
    for rel, text in (files or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    return tmp_path


IDENTITY = '[tool.py_ci_shared.gates.identity_comparisons]\nfiles = ["pkg/**/*.py"]\n'


class TestLoadConfig:
    def test_no_table_is_none_so_the_plugin_stays_silent(self, tmp_path):
        assert load_config(_repo(tmp_path, "")) is None

    def test_enable_and_tables_become_runs_with_reserved_keys_split_off(self, tmp_path):
        repo = _repo(
            tmp_path,
            '[tool.py_ci_shared]\nenable = ["naive_utcnow"]\nbudget = "fail"\n'
            '[tool.py_ci_shared.gates.rounds]\nmodule = "audit_round_format"\nentry = "assert_rounds_countable"\n'
            'budget_s = 5\naudits_dir = "audits"\n'
            '[tool.py_ci_shared.gates.off]\nmodule = "naive_utcnow"\nenabled = false\n',
        )
        config = load_config(repo)
        assert config is not None and config.budget == "fail"
        assert [(r.name, r.module, r.entry) for r in config.gates] == [
            ("naive_utcnow", "naive_utcnow", None),
            ("rounds", "audit_round_format", "assert_rounds_countable"),
        ]
        rounds = config.gate("rounds")
        assert rounds.kwargs == {"audits_dir": "audits"} and rounds.budget_s == 5.0

    @pytest.mark.parametrize(
        "table, needle",
        [
            ('[tool.py_ci_shared]\nbudget = "loud"\n', "budget"),
            ("[tool.py_ci_shared]\nenabel = []\n", "unknown key"),
            ('[tool.py_ci_shared]\nenable = ["x"]\n[tool.py_ci_shared.gates.x]\nroot = "."\n', "both"),
            ("[tool.py_ci_shared\n", "not valid TOML"),
        ],
    )
    def test_malformed_tables_fail_loudly(self, tmp_path, table, needle):
        with pytest.raises(ConfigError, match=needle):
            load_config(_repo(tmp_path, table))


class TestResolveKwargs:
    def test_paths_follow_the_annotation_and_globs_expand(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "x.py").write_text("", encoding="utf-8")
        (tmp_path / "a" / "y.py").write_text("", encoding="utf-8")

        def gate(files: "list[Path]", out_path: "Path", label: str, repo_root: "Path") -> None: ...

        kwargs = resolve_kwargs(gate, {"files": ["a/*.py"], "out_path": "b.json", "label": "a/*.py"}, tmp_path)
        assert kwargs["files"] == [tmp_path / "a" / "x.py", tmp_path / "a" / "y.py"]
        assert kwargs["out_path"] == tmp_path / "b.json"
        assert kwargs["label"] == "a/*.py", "a str-annotated value must not become a path"
        assert kwargs["repo_root"] == tmp_path, "an omitted repo_root is filled in"

    def test_a_one_or_many_parameter_keeps_a_plain_string_as_one_directory(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")

        def gate(tests_root: "Union[str, Path, Iterable[Union[str, Path]]]", files: "list[Path]") -> None: ...

        kwargs = resolve_kwargs(gate, {"tests_root": "tests", "files": "tests"}, tmp_path)
        assert kwargs["tests_root"] == tmp_path / "tests", "a directory given as a string is one path, not a one-file list"
        assert kwargs["files"] == [tmp_path / "tests"], "a list-only parameter still gets a list"
        kwargs = resolve_kwargs(gate, {"tests_root": "tests/*.py"}, tmp_path)
        assert kwargs["tests_root"] == [tmp_path / "tests" / "test_a.py"], "a glob still expands"

    def test_the_real_gate_reads_a_tests_root_string_as_a_directory(self, tmp_path):
        from py_ci_shared.no_xfail_to_defer import assert_no_xfail_to_defer

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        assert_no_xfail_to_defer(**resolve_kwargs(assert_no_xfail_to_defer, {"tests_root": "tests"}, tmp_path))

    def test_names_decide_when_there_is_no_annotation(self, tmp_path):
        def gate(md_files, tests_dir, pattern):  # type: ignore[no-untyped-def]
            ...

        kwargs = resolve_kwargs(gate, {"md_files": ["README.md"], "tests_dir": "tests", "pattern": "x"}, tmp_path)
        assert kwargs == {"md_files": [tmp_path / "README.md"], "tests_dir": tmp_path / "tests", "pattern": "x"}

    def test_an_unknown_key_names_the_signature(self, tmp_path):
        def gate(root: "Path") -> None: ...

        with pytest.raises(ConfigError, match=r"takes no argument\(s\) \['rot'\]"):
            resolve_kwargs(gate, {"rot": "."}, tmp_path)


class TestRunGate:
    def test_pass_fail_and_error_are_told_apart(self, tmp_path):
        repo = _repo(tmp_path, IDENTITY + '[tool.py_ci_shared.gates.bad]\nmodule = "identity_comparisons"\nnope = 1\n', {"pkg/m.py": DIRTY})
        config = load_config(repo)
        assert config is not None
        failed = run_gate(config, config.gate("identity_comparisons"))
        assert failed.status == FAILED and "m.py:5" in failed.message
        error = run_gate(config, config.gate("bad"))
        assert error.status == ERROR and "nope" in error.message
        (repo / "pkg" / "m.py").write_text(CLEAN, encoding="utf-8")
        assert run_gate(config, config.gate("identity_comparisons")).status == PASSED

    def test_an_unregistered_module_and_a_wrong_entry_are_config_errors(self, tmp_path):
        repo = _repo(
            tmp_path,
            '[tool.py_ci_shared.gates.ghost]\nroot = "."\n[tool.py_ci_shared.gates.wrong]\nmodule = "naive_utcnow"\nentry = "assert_nothing"\n'
            '[tool.py_ci_shared.gates.lib]\nmodule = "dart_scanners"\n',
        )
        config = load_config(repo)
        assert config is not None
        results = {r.name: run_gate(config, r) for r in config.gates}
        assert all(r.status == ERROR for r in results.values())
        assert "unknown py-ci-shared module" in results["ghost"].message
        assert "assert_nothing" in results["wrong"].message
        assert "library" in results["lib"].message

    def test_refresh_sets_the_env_only_for_the_call_and_the_cwd_is_restored(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        repo = _repo(tmp_path, '[tool.py_ci_shared.gates.loc_budget]\nfiles = ["pkg/*.py"]\nroot = "."\nbaseline_path = "b.json"\n', {"pkg/m.py": CLEAN})
        config = load_config(repo)
        assert config is not None
        cwd = os.getcwd()
        missing = run_gate(config, config.gate("loc_budget"))
        assert missing.status == FAILED and not (repo / "b.json").exists(), "a missing baseline fails and is not written"
        run_gate(config, config.gate("loc_budget"), refresh=True)
        assert json.loads((repo / "b.json").read_text(encoding="utf-8")) == {}
        assert os.environ.get(ENV_VAR) is None and os.getcwd() == cwd
        assert run_gate(config, config.gate("loc_budget")).status == PASSED

    def test_the_budget_verdict(self):
        slow = GateResult("g", PASSED, "", seconds=3.0, budget_s=1.0)
        assert budget_verdict(slow, "off") is None
        assert "over its 1s budget" in (budget_verdict(slow, "warn") or "")
        assert budget_verdict(GateResult("g", PASSED, "", 0.5, 1.0), "fail") is None


class TestCommandLine:
    def test_run_all_exit_codes(self, tmp_path, capsys):
        repo = _repo(tmp_path, IDENTITY, {"pkg/m.py": DIRTY})
        assert cli.main(["run-all", "--repo", str(repo)]) == 1
        assert "FAIL  identity_comparisons" in capsys.readouterr().out
        (repo / "pkg" / "m.py").write_text(CLEAN, encoding="utf-8")
        assert cli.main(["run-all", "--repo", str(repo)]) == 0
        assert cli.main(["run", "nothing_enabled", "--repo", str(repo)]) == 2
        assert cli.main(["run-all", "--repo", str(_repo(tmp_path / "bare", ""))]) == 2

    def test_budget_fail_mode_fails_a_passing_gate_that_ran_too_long(self, tmp_path, capsys):
        repo = _repo(tmp_path, '[tool.py_ci_shared]\nbudget = "fail"\n' + IDENTITY + "budget_s = 0.000001\n", {"pkg/m.py": CLEAN})
        assert cli.main(["run-all", "--repo", str(repo)]) == 1
        assert "BUDGET" in capsys.readouterr().err

    def test_list_and_the_markdown_catalogue(self, capsys):
        assert cli.main(["list"]) == 0
        out = capsys.readouterr().out
        assert all(spec.name in out for spec in registry.GATES)
        assert cli.main(["list", "--markdown"]) == 0
        assert capsys.readouterr().out.strip() == registry.render_catalogue()

    def test_config_path_points_at_the_shipped_file(self, capsys):
        assert cli.main(["config-path", "ruff-base"]) == 0
        path = Path(capsys.readouterr().out.strip())
        assert path.name == "ruff-base.toml" and path.is_file()

    def test_tool_forwards_to_a_module_main(self, capsys):
        with pytest.raises(SystemExit) as info:
            cli.main(["tool", "worktree_hygiene", "--help"])
        assert info.value.code == 0
        assert "usage" in capsys.readouterr().out.lower()
        assert cli.main(["tool", "naive_utcnow"]) == 2, "a module without a main is a usage error"

    def test_version(self, capsys):
        assert cli.main(["version"]) == 0
        assert capsys.readouterr().out.strip() == __version__
