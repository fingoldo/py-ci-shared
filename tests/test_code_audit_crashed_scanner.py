"""A code-audit scanner that raises must fail the gate, not pass it as "no findings" (audit S-01)."""

from pathlib import Path

import pytest


def test_crashed_scanner_fails_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from py_ci_shared.code_audit import registry

    from py_ci_shared import code_audit_meta

    def crashed(root, checks=None, exclude_dirs=frozenset(), parallel=True):
        return [], {"planted_scanner": "RuntimeError: boom"}

    monkeypatch.setattr(registry, "run_all_checked", crashed)
    with pytest.raises(pytest.fail.Exception, match="planted_scanner"):
        code_audit_meta.assert_no_new_code_audit_findings(
            root=tmp_path,
            baseline_path=tmp_path / "baseline.json",
            request=None,
        )


def test_a_pyutilz_without_run_all_checked_falls_back_with_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A consumer pinned to a pyutilz that predates run_all_checked failed with ImportError on every code-audit test
    # (CI run 38698274157); it must keep working through run_all and be told the crash check is off.
    from py_ci_shared.code_audit import registry

    from py_ci_shared import code_audit_meta

    calls = []

    def old_run_all(root, checks=None, exclude_dirs=frozenset(), parallel=True):
        calls.append(root)
        return []

    monkeypatch.delattr(registry, "run_all_checked", raising=False)
    monkeypatch.setattr(registry, "run_all", old_run_all)
    monkeypatch.setenv("PY_CI_SHARED_REFRESH", "code-audit")
    with pytest.warns(UserWarning, match="run_all_checked"), pytest.raises(pytest.skip.Exception):
        code_audit_meta.assert_no_new_code_audit_findings(root=tmp_path, baseline_path=tmp_path / "baseline.json", request=None)
    assert calls == [tmp_path]
