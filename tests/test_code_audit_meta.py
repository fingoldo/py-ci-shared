"""Unit tests for the code-audit baseline meta-test harness.

Exercises assert_no_new_code_audit_findings() and register_refresh_option()
against a real (tiny, hand-written) scratch source tree -- no mocking of
pyutilz.dev.code_audit itself, since the whole point is verifying the
seed/compare/refresh/report cycle around it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import orjson
import pytest

from py_ci_shared.code_audit_meta import (
    REFRESH_FLAG,
    assert_no_new_code_audit_findings,
    register_refresh_option,
)


def _write_mutable_default_module(root: Path) -> None:
    (root / "bad.py").write_text(
        "def f(items=[]):\n    items.append(1)\n    return items\n",
        encoding="utf-8",
    )


class TestAssertNoNewCodeAuditFindings:
    def test_first_run_seeds_baseline_and_skips(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        assert baseline.exists()
        seeded = orjson.loads(baseline.read_bytes())
        assert any("mutable_default" in k for k in seeded)

    def test_unchanged_tree_passes_after_seeding(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        # No pytest.fail/skip on the second call -- returning normally is the pass.
        assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

    def test_new_finding_fails(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        baseline = tmp_path / "_code_audit_baseline.json"
        (src / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        # Introduce a new offender after the baseline was seeded clean.
        _write_mutable_default_module(src)

        with pytest.raises(pytest.fail.Exception, match="new static-analysis finding"):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

    def test_drained_finding_does_not_fail(self, tmp_path):
        """A finding present in the baseline but no longer in the current
        scan (the bug got fixed) must not fail the test -- only NET-NEW
        findings are gated."""
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        (src / "bad.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

    def test_refresh_flag_reseeds_even_with_existing_baseline(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        baseline = tmp_path / "_code_audit_baseline.json"
        baseline.write_text("[]", encoding="utf-8")

        _write_mutable_default_module(src)
        monkeypatch.setattr(sys, "argv", [*sys.argv, REFRESH_FLAG])

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        seeded = orjson.loads(baseline.read_bytes())
        assert any("mutable_default" in k for k in seeded)

    def test_refresh_flag_detected_via_request_even_when_argv_is_bare(self, tmp_path, monkeypatch):
        """Regression: pytest-xdist workers run with sys.argv == ['-c'] (execnet
        bootstraps them, it does not re-exec the original command line), so a
        sys.argv-only check silently never refreshes under `-n`. Passing the
        pytest `request` fixture must detect the flag via `request.config.getoption`
        instead, which xdist reconstructs correctly per-worker."""
        src = tmp_path / "src"
        src.mkdir()
        baseline = tmp_path / "_code_audit_baseline.json"
        baseline.write_text("[]", encoding="utf-8")

        _write_mutable_default_module(src)
        monkeypatch.setattr(sys, "argv", ["-c"])  # simulates an xdist worker's argv

        class _FakeConfig:
            def getoption(self, name, default=None):
                return name == REFRESH_FLAG

        class _FakeRequest:
            config = _FakeConfig()

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline, request=_FakeRequest())

        seeded = orjson.loads(baseline.read_bytes())
        assert any("mutable_default" in k for k in seeded)

    def test_exclude_dirs_merged_with_defaults(self, tmp_path):
        """A caller-supplied exclude_dirs must not disable the built-in
        cache/vcs exclusions (__pycache__, .git, etc.) -- they merge."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "__pycache__").mkdir()
        (src / "__pycache__" / "junk.py").write_text("def f(items=[]): items.append(1)\n", encoding="utf-8")
        (src / "legacy").mkdir()
        (src / "legacy" / "old.py").write_text("def g(items=[]): items.append(1)\n", encoding="utf-8")
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline, exclude_dirs=frozenset({"legacy"}))

        seeded = orjson.loads(baseline.read_bytes())
        assert seeded == [], "both __pycache__ (default) and legacy (caller-supplied) must be excluded"


class TestRegisterRefreshOption:
    """Uses pytest's own ``Parser`` (not stdlib ``argparse.ArgumentParser`` --
    ``addoption`` is a pytest-specific method with argparse-like semantics
    but its own conflict handling, which is exactly what's under test)."""

    def _make_parser(self):
        from _pytest.config.argparsing import Parser

        return Parser()

    def test_registers_without_raising_on_fresh_parser(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        args = parser.parse([REFRESH_FLAG])
        assert args.refresh_code_audit_baseline is True

    def test_double_registration_is_a_noop_not_a_crash(self):
        parser = self._make_parser()
        register_refresh_option(parser)
        register_refresh_option(parser)  # must not raise (pytest.Parser raises ValueError on conflict)


class TestKeysSurviveRelocation:
    """The key format is relocation-proof: unrelated edits above a finding must not
    re-report it as new, nor report the old entry as drained.

    The line-numbered format made the baseline a line-number ratchet. Inserting a comment
    above a known finding produced BOTH a "new finding" failure and a "finding drained"
    note in the same run -- the second of which is a false all-clear, since it claims work
    was completed that was not.
    """

    def test_inserting_lines_above_a_finding_reports_nothing(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)
        seeded = orjson.loads(baseline.read_bytes())

        # Push the flagged def down by three lines without touching it.
        original = (src / "bad.py").read_text(encoding="utf-8")
        (src / "bad.py").write_text("# padding\n# padding\n# padding\n" + original, encoding="utf-8")

        # Passes: returning normally is the assertion.
        assert_no_new_code_audit_findings(root=src, baseline_path=baseline)
        # And the baseline was not rewritten behind our back.
        assert orjson.loads(baseline.read_bytes()) == seeded

    def test_changing_the_flagged_line_itself_is_still_reported(self, tmp_path):
        """The negative control: relocation-proof must not mean change-blind."""
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        # Same defect, different text on the flagged line -> a different finding.
        (src / "bad.py").write_text(
            "def f(other_name={}):\n    return other_name\n", encoding="utf-8"
        )
        with pytest.raises(pytest.fail.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

    def test_two_identical_snippets_in_one_file_get_distinct_keys(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "twice.py").write_text(
            "def a(items=[]):\n    return items\n\n\ndef b(items=[]):\n    return items\n",
            encoding="utf-8",
        )
        baseline = tmp_path / "_code_audit_baseline.json"
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        seeded = orjson.loads(baseline.read_bytes())
        mutable = [k for k in seeded if "mutable_default" in k]
        assert len(mutable) == len(set(mutable)) == 2, (
            "identical snippets must not collapse to one key, or fixing one of them "
            "would go unnoticed"
        )


class TestLegacyBaselineMigration:
    """A baseline written in the old format keeps gating until it is refreshed."""

    def test_legacy_keys_are_still_honoured(self, tmp_path, capsys):
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"

        # Hand-write a legacy baseline: check::file:line.
        baseline.write_text(
            orjson.dumps(["mutable_default::bad.py:1"], option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )
        # Must NOT fail: the finding is known, just recorded the old way.
        assert_no_new_code_audit_findings(root=src, baseline_path=baseline)
        assert "legacy line-numbered key format" in capsys.readouterr().err

    def test_refreshing_a_legacy_baseline_writes_the_new_format(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        _write_mutable_default_module(src)
        baseline = tmp_path / "_code_audit_baseline.json"
        baseline.write_text(
            orjson.dumps(["mutable_default::bad.py:1"], option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )

        monkeypatch.setattr(sys, "argv", ["pytest", REFRESH_FLAG])
        with pytest.raises(pytest.skip.Exception):
            assert_no_new_code_audit_findings(root=src, baseline_path=baseline)

        refreshed = orjson.loads(baseline.read_bytes())
        assert refreshed and all(k.count("::") == 2 for k in refreshed)
