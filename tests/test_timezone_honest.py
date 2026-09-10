"""Unit tests for the timezone-honest check. Real files on disk and a real ruff, same no-mocking convention."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.timezone_honest import assert_timezone_honest, dtz_findings, excluded_code_dirs, timezone_problems

_NAIVE = "import datetime\nx = datetime.datetime.now()\n"
_HONEST = "import datetime\nx = datetime.datetime.now(datetime.UTC)\n"


def _tree(root: Path, files: dict[str, str], *, ruff_config: str = "") -> Path:
    (root / "pyproject.toml").write_text(f"[tool.ruff]\n{ruff_config}\n", encoding="utf-8")
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


class TestTheBlindSpot:
    """The failure this module exists for: a directory the ruff config excludes, scanned as `.`."""

    def test_an_excluded_directory_with_code_must_be_scanned(self, tmp_path):
        _tree(tmp_path, {"app.py": _HONEST, "scripts/job.py": _HONEST}, ruff_config='exclude = ["scripts"]')
        problems = timezone_problems(tmp_path, scan_paths=(".",))
        assert len(problems) == 1 and "['scripts']" in problems[0]

    def test_scanning_it_explicitly_finds_what_dot_misses(self, tmp_path):
        """The CQ-39 measurement, reproduced: `.` reports nothing, the explicit path reports the finding."""
        _tree(tmp_path, {"app.py": _HONEST, "scripts/job.py": _NAIVE}, ruff_config='exclude = ["scripts"]')
        assert dtz_findings(tmp_path, (".",)) == set()
        assert dtz_findings(tmp_path, (".", "scripts")) == {("scripts/job.py", "DTZ005")}

    def test_force_exclude_does_not_reopen_the_blind_spot(self, tmp_path):
        """With `force-exclude = true`, ruff drops excluded directories even when they are passed
        explicitly. `--no-force-exclude` is the only thing standing between that setting and a scan
        that silently skips `scripts` again."""
        _tree(tmp_path, {"scripts/job.py": _NAIVE}, ruff_config='exclude = ["scripts"]\nforce-exclude = true')
        assert dtz_findings(tmp_path, ("scripts",)) == {("scripts/job.py", "DTZ005")}

    def test_a_directory_declared_not_code_is_accepted(self, tmp_path):
        _tree(tmp_path, {"app.py": _HONEST, "fixtures/sample.py": _HONEST}, ruff_config='exclude = ["fixtures"]')
        assert timezone_problems(tmp_path, scan_paths=(".",), not_code=("fixtures",)) == []

    @pytest.mark.parametrize("name", ["*.md", ".venv", "build", "docs"])
    def test_patterns_environments_and_code_free_directories_are_not_demanded(self, name, tmp_path):
        files = {"app.py": _HONEST}
        if not name.startswith("*") and name != "docs":
            files[f"{name}/lib.py"] = _NAIVE  # a real .py in an environment or artefact directory
        _tree(tmp_path, files, ruff_config=f'exclude = ["{name}"]')
        if name == "docs":
            (tmp_path / "docs").mkdir()
            (tmp_path / "docs" / "index.md").write_text("prose\n", encoding="utf-8")
        assert excluded_code_dirs(tmp_path) == []


class TestAllowances:
    def test_an_allowed_finding_with_a_reason_passes(self, tmp_path):
        _tree(tmp_path, {"app.py": _NAIVE})
        assert timezone_problems(tmp_path, allowed={("app.py", "DTZ005"): "mirrors the browser's local clock"}) == []

    def test_an_allowance_must_say_why(self, tmp_path):
        _tree(tmp_path, {"app.py": _NAIVE})
        problems = timezone_problems(tmp_path, allowed={("app.py", "DTZ005"): "  "})
        assert len(problems) == 1 and "no reason" in problems[0]

    def test_a_stale_allowance_fails(self, tmp_path):
        """Headroom nobody is using gets used by the next finding."""
        _tree(tmp_path, {"app.py": _HONEST})
        problems = timezone_problems(tmp_path, allowed={("app.py", "DTZ005"): "was here once"})
        assert len(problems) == 1 and "no longer match" in problems[0]

    def test_an_allowance_is_by_file_and_rule_not_by_count(self, tmp_path):
        """A second finding in another file must not ride on the first one's allowance."""
        _tree(tmp_path, {"app.py": _NAIVE, "other.py": _NAIVE})
        problems = timezone_problems(tmp_path, allowed={("app.py", "DTZ005"): "reason"})
        assert len(problems) == 1 and "other.py" in problems[0]


class TestScope:
    def test_tests_are_out_of_scope(self, tmp_path):
        _tree(tmp_path, {"app.py": _HONEST, "tests/test_app.py": "import datetime\nx = datetime.datetime(2026, 1, 1)\n"})
        assert timezone_problems(tmp_path) == []

    def test_a_naive_call_in_production_code_is_found(self, tmp_path):
        _tree(tmp_path, {"app.py": _NAIVE})
        problems = timezone_problems(tmp_path)
        assert len(problems) == 1 and "('app.py', 'DTZ005')" in problems[0]


class TestARuffThatDidNotLookIsAnErrorNotACleanResult:
    """An empty result from a ruff that saw nothing would pass every check above for free."""

    def test_a_scan_path_that_does_not_exist_is_refused(self, tmp_path):
        """Measured before this guard existed: ruff given a missing directory prints `Failed to
        lint`, then `All checks passed!`, and exits 0. A typo in `scan_paths` was a clean result.
        The first draft of this test assumed ruff would fail here, and it passed the typo instead."""
        _tree(tmp_path, {"app.py": _NAIVE})
        with pytest.raises(RuntimeError, match="do not exist"):
            dtz_findings(tmp_path, ("script",))

    def test_an_invalid_ruff_config_is_refused(self, tmp_path):
        """ruff exits 2 on a config it cannot read, which must not read as zero findings."""
        _tree(tmp_path, {"app.py": _NAIVE}, ruff_config='line-length = "not-a-number"')
        with pytest.raises(RuntimeError, match="ruff exited 2"):
            dtz_findings(tmp_path, (".",))


def test_the_entry_point_reports_every_problem_at_once(tmp_path):
    _tree(tmp_path, {"app.py": _NAIVE, "scripts/job.py": _HONEST}, ruff_config='exclude = ["scripts"]')
    # BaseException: `pytest.fail` raises `Failed`, which derives from it so an `except Exception`
    # cannot swallow a failed assertion.
    with pytest.raises(BaseException) as excinfo:
        assert_timezone_honest(tmp_path, allowed={("gone.py", "DTZ011"): "stale"})
    message = str(excinfo.value)
    assert "3 timezone problem(s)" in message
    assert "['scripts']" in message and "('app.py', 'DTZ005')" in message and "gone.py" in message
