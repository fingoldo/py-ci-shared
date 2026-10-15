"""A code-audit scanner that raises must fail the gate, not pass it as "no findings" (audit S-01)."""

from pathlib import Path

import pytest


def test_crashed_scanner_fails_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pyutilz.dev.code_audit import registry

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
