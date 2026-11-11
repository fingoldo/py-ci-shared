"""Runner and CLI outcomes (audit 2026-11-07 49-F3/F4/F5/F7/F8/F9/F11): crash location, CLI refresh hint, REFRESHED, skip = "fail", PASS notes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from py_ci_shared import cli
from py_ci_shared._core import runner
from py_ci_shared._core.baseline import Baseline
from py_ci_shared._core.config import ConfigError, GateRun, RepoConfig, load_config
from py_ci_shared._core.outcome import add_note, kind_of, refreshed_skip, unevaluated_skip
from py_ci_shared._core.runner import ERROR, FAILED, PASSED, REFRESHED_STATUS, SKIPPED, GateResult, run_gate

SRC = Path(__file__).resolve().parents[1] / "src" / "py_ci_shared"
LONG_FN = "def f(x):\n" + "".join(f"    x = x + {i}\n" for i in range(5)) + "    return x\n"
FL = '[tool.py_ci_shared.gates.function_length]\nfiles = ["pkg/*.py"]\nroot = "."\nbaseline_path = "b.json"\nmin_functions = 1\nlimit = 3\n'


def _repo(tmp_path: Path, table: str, files: "dict[str, str] | None" = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_bytes(("[project]\nname = 'demo'\nversion = '0.1.0'\n\n" + table).encode())
    for rel, text in (files or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(text.encode())
    return tmp_path


def _fake(monkeypatch, tmp_path: Path, func, *, skip: str = "ok") -> "tuple[RepoConfig, GateRun]":
    monkeypatch.setattr(runner, "resolve_gate", lambda run: (func, 5.0))
    run = GateRun(name="fake", module="naive_utcnow", entry=None)
    return RepoConfig(repo_root=tmp_path, gates=(run,), skip=skip), run


def _boom() -> None:
    raise KeyError("x")


class TestF3CrashDiagnostics:
    def test_missing_required_arguments_name_the_table_and_the_parameter(self, tmp_path):
        repo = _repo(tmp_path, '[tool.py_ci_shared]\nenable = ["identity_comparisons"]\n')
        config = load_config(repo)
        assert config is not None
        result = run_gate(config, config.gate("identity_comparisons"))
        assert result.status == ERROR
        assert "[tool.py_ci_shared.gates.identity_comparisons]" in result.message and "'files'" in result.message
        assert "TypeError" not in result.message

    def test_a_crash_inside_a_gate_names_its_file_and_line(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PY_CI_SHARED_TRACEBACK", raising=False)
        config, run = _fake(monkeypatch, tmp_path, _boom)
        result = run_gate(config, run)
        assert result.status == ERROR and result.message.startswith("KeyError: 'x'")
        assert re.search(r"\(at .*test_diagnostics_runner_outcomes\.py:\d+;", result.message)
        assert "Traceback" not in result.message

    def test_the_env_switch_prints_the_whole_traceback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PY_CI_SHARED_TRACEBACK", "1")
        config, run = _fake(monkeypatch, tmp_path, _boom)
        assert "Traceback (most recent call last)" in run_gate(config, run).message


class TestF4CliRefreshHint:
    def test_the_cli_names_its_own_command_not_a_pytest_flag(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("PY_CI_SHARED_CLI", raising=False)
        repo = _repo(tmp_path, FL, {"pkg/m.py": LONG_FN})
        assert cli.main(["run-all", "--repo", str(repo)]) == 1
        out = capsys.readouterr().out
        assert "py-ci-shared refresh function_length" in out and "--refresh-function-length-baseline" not in out

    def test_under_pytest_the_flag_is_still_named_with_the_env_form(self, tmp_path, monkeypatch):
        from py_ci_shared.function_length import REFRESH_FLAG, assert_functions_do_not_grow

        monkeypatch.delenv("PY_CI_SHARED_CLI", raising=False)
        _repo(tmp_path, "", {"pkg/m.py": LONG_FN})
        with pytest.raises(BaseException) as info:
            assert_functions_do_not_grow([tmp_path / "pkg" / "m.py"], tmp_path, tmp_path / "b.json", limit=3, min_functions=1)
        assert REFRESH_FLAG in str(info.value) and "PY_CI_SHARED_REFRESH=function_length" in str(info.value)

    def test_a_baseline_hint_uses_the_cli_form_only_under_the_cli(self, tmp_path, monkeypatch):
        base = Baseline(tmp_path / "b.json", gate="my-gate", refresh_command="pytest --refresh-my-gate-baseline")
        monkeypatch.delenv("PY_CI_SHARED_CLI", raising=False)
        assert base.refresh_text == "pytest --refresh-my-gate-baseline"
        monkeypatch.setenv("PY_CI_SHARED_CLI", "1")
        assert "py-ci-shared refresh my-gate" in base.refresh_text
        custom = Baseline(tmp_path / "b.json", gate="my-gate", refresh_command="python tool/regen.py")
        assert custom.refresh_text == "python tool/regen.py"


class TestF5Refreshed:
    def test_a_refresh_is_reported_with_entry_counts_and_not_as_skipped(self, tmp_path, capsys):
        repo = _repo(tmp_path, FL, {"pkg/m.py": LONG_FN})
        assert cli.main(["refresh", "function_length", "--grow", "--repo", str(repo)]) == 0
        out = capsys.readouterr().out
        # 49-F8: a baseline written for the first time has no "before", so it is reported as created, never as "+0 -0".
        assert out.startswith("REFRESH function_length") and "created b.json with 1 entries" in out and "+0 -0" not in out
        assert "0 skipped" in out and "1 refreshed" in out and "SKIP " not in out
        (repo / "pkg" / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
        assert cli.main(["refresh", "function_length", "--repo", str(repo)]) == 0
        assert "+0 -1 entries" in capsys.readouterr().out
        assert json.loads((repo / "b.json").read_text(encoding="utf-8")) == {}

    def test_a_tagged_skip_is_refreshed_and_a_plain_skip_stays_skipped(self, tmp_path, monkeypatch):
        config, run = _fake(monkeypatch, tmp_path, lambda: refreshed_skip("wrote it"))
        assert run_gate(config, run).status == REFRESHED_STATUS
        config, run = _fake(monkeypatch, tmp_path, lambda: pytest.skip("not today"))
        result = run_gate(config, run)
        assert result.status == SKIPPED and result.message == "skipped: not today"


class TestF7SkipFail:
    @staticmethod
    def _unevaluated() -> None:
        unevaluated_skip("CHANGELOG.md has no section matching section_pattern")

    def test_default_keeps_the_skip_green_with_a_visible_reason(self, tmp_path, monkeypatch, capsys):
        config, run = _fake(monkeypatch, tmp_path, self._unevaluated)
        result = run_gate(config, run)
        assert result.status == SKIPPED and result.message.startswith("skipped: CHANGELOG.md has no section")
        assert cli._report([result], "warn") == 0
        assert "skipped: CHANGELOG.md has no section" in capsys.readouterr().out

    def test_skip_fail_turns_it_into_a_failure_and_exit_1(self, tmp_path, monkeypatch):
        config, run = _fake(monkeypatch, tmp_path, self._unevaluated, skip="fail")
        result = run_gate(config, run)
        assert result.status == FAILED and 'skip = "fail"' in result.message
        assert cli._report([result], "warn") == 1

    def test_the_real_gate_skips_through_the_tagged_channel(self, tmp_path):
        from py_ci_shared.changelog_promise_parity import assert_changelog_bullets_satisfy_pattern

        log = tmp_path / "CHANGELOG.md"
        log.write_text("# Log\n\n## Next\n\n- thing\n", encoding="utf-8")
        with pytest.raises(BaseException) as info:
            assert_changelog_bullets_satisfy_pattern(log, re.compile("x"), section_pattern=re.compile("^## Unreleased", re.M))
        assert kind_of(info.value) == "unevaluated"

    @pytest.mark.parametrize("value, ok", [("fail", True), ("ok", True), ("loud", False)])
    def test_the_config_key_is_validated(self, tmp_path, value, ok):
        repo = _repo(tmp_path, f'[tool.py_ci_shared]\nenable = ["naive_utcnow"]\nskip = "{value}"\n')
        if ok:
            config = load_config(repo)
            assert config is not None and config.skip == value
        else:
            with pytest.raises(ConfigError, match="skip"):
                load_config(repo)


class TestF9Report:
    def test_budget_row_is_on_stdout_next_to_its_gate(self, capsys):
        slow = GateResult("slow", PASSED, "", seconds=30.0, budget_s=3.0)
        cli._report([slow], "warn")
        captured = capsys.readouterr()
        assert "BUDGET slow took" in captured.out and "BUDGET" not in captured.err

    def test_summary_counts_cached_and_refreshed_only_when_present(self):
        plain = [GateResult("a", PASSED, "", 0.1, 1.0)]
        assert cli._summary_line(plain) == "1 passed, 0 failed, 0 skipped, 0 error"
        mixed = [GateResult("a", PASSED, "cached: x", 0.1, 1.0, cached=True), GateResult("b", REFRESHED_STATUS, "", 0.1, 1.0)]
        assert cli._summary_line(mixed) == "1 passed, 0 failed, 0 skipped, 0 error, 1 refreshed (1 cached)"


class TestF11PassNotes:
    def test_a_pass_carries_the_note_a_gate_added(self, tmp_path, monkeypatch, capsys):
        config, run = _fake(monkeypatch, tmp_path, lambda: add_note("sampled 10 of 90 mutants"))
        result = run_gate(config, run)
        assert result.status == PASSED and result.note == "sampled 10 of 90 mutants"
        cli._report([result], "warn")
        assert "note: sampled 10 of 90 mutants" in capsys.readouterr().out

    def test_a_note_outside_a_run_is_ignored_and_does_not_leak_into_the_next_run(self, tmp_path, monkeypatch):
        add_note("stray")
        config, run = _fake(monkeypatch, tmp_path, lambda: None)
        assert run_gate(config, run).note == ""


class TestF8StaleLineNumbers:
    def test_no_log_message_hard_codes_its_own_line_number(self):
        """A literal 'x.py line N' in a message goes stale at the next edit (10 of 13 were wrong)."""
        pattern = re.compile(r"\b\w+\.py line \d+:")
        offenders = [
            f"{path.name}:{i}"
            for path in sorted(SRC.rglob("*.py"))
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
        assert offenders == []

    def test_handlers_that_already_print_their_error_do_not_also_warn(self):
        assert "substituted a fallback" not in (SRC / "cli.py").read_text(encoding="utf-8")

    def test_fallback_warnings_do_not_hard_code_a_line_number(self):
        """A hard-coded ``handler at line N`` in a message drifts on the first edit above it (49-F1); the logger name already names the module."""
        offenders = [
            f"{path.name}:{i}"
            for path in sorted(SRC.rglob("*.py"))
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"handler at line \d+", line)
        ]
        assert offenders == []


def test_a_sub_second_budget_is_not_printed_as_zero_seconds():
    """A 0.3 s budget must read as 0.3 s in the verdict, not as 0 s (49-F4)."""
    from py_ci_shared._core.runner import GateResult, budget_verdict

    verdict = budget_verdict(GateResult("fast_gate", "PASSED", "", seconds=0.6, budget_s=0.3), "warn")
    assert verdict is not None and "over its 0.3s budget" in verdict


def test_a_misconfigured_gate_is_reported_in_its_row_not_as_a_gate_less_warning(tmp_path, monkeypatch, caplog):
    """49-F5: the warning named no gate and repeated the ERROR row; the row carries the message, so the log stays quiet."""
    import logging

    config = RepoConfig(repo_root=tmp_path, gates=(GateRun(name="g", module="x", entry=None, kwargs={}),))

    def misconfigured(run):
        raise ConfigError("missing kwarg 'root'")

    monkeypatch.setattr(runner, "resolve_gate", misconfigured)
    with caplog.at_level(logging.WARNING):
        result = run_gate(config, config.gates[0])
    assert result.status == ERROR and "missing kwarg 'root'" in result.message
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
