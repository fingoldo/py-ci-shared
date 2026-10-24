from __future__ import annotations
from pathlib import Path
from typing import Any
from py_ci_shared.code_audit import register_scanner
from py_ci_shared.code_audit.broad_except import scan_broad_except_swallows
from py_ci_shared.code_audit.registry import _SCANNERS, run_all, run_all_checked


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _boom_scanner(root: Path, exclude_dirs: Any = None) -> list:
    raise RuntimeError("pathological file")


def test_s01_crashed_scanner_is_reported_by_run_all_checked(tmp_path: Path) -> None:
    _write(tmp_path, "bad.py", "def f(items=[]):\n    items.append(1)\n")
    register_scanner("boom_probe_s01", _boom_scanner, allow_override=True)
    try:
        findings, failed = run_all_checked(tmp_path, checks=["boom_probe_s01", "mutable_default"], parallel=False)
    finally:
        _SCANNERS.pop("boom_probe_s01", None)
    assert failed == {"boom_probe_s01": "RuntimeError: pathological file"}
    assert [f.check for f in findings] == ["mutable_default"]


def test_s01_clean_run_reports_no_failed_scanner(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    findings, failed = run_all_checked(tmp_path, checks=["mutable_default", "bare_except"], parallel=False)
    assert findings == []
    assert failed == {}


def test_s01_run_all_keeps_its_list_return_for_existing_callers(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    register_scanner("boom_probe_s01b", _boom_scanner, allow_override=True)
    try:
        assert run_all(tmp_path, checks=["boom_probe_s01b"], parallel=False) == []
    finally:
        _SCANNERS.pop("boom_probe_s01b", None)


def test_s14_debug_only_handler_returning_default_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad.py",
        "import logging\nlogger = logging.getLogger(__name__)\n\n"
        "def f():\n    try:\n        probe()\n    except Exception as e:\n        logger.debug('probe failed: %s', e)\n        return False\n",
    )
    findings = scan_broad_except_swallows(tmp_path)
    assert len(findings) == 1
    assert findings[0].line == 7


def test_s14_debug_only_handler_assigning_default_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad.py",
        "import logging\nlogger = logging.getLogger(__name__)\n\n"
        "def f(out):\n    try:\n        probe()\n    except Exception as e:\n        logger.debug('probe failed: %s', e)\n        out['k'] = None\n",
    )
    assert len(scan_broad_except_swallows(tmp_path)) == 1


def test_s14_debug_only_handler_returning_structured_error_is_not_flagged(tmp_path: Path) -> None:
    """A structured error return is an escalation, so the debug-only exemption still applies."""
    _write(
        tmp_path,
        "ok.py",
        "import logging\nlogger = logging.getLogger(__name__)\n\n"
        "def f():\n    try:\n        probe()\n    except Exception as e:\n        logger.debug('probe failed: %s', e)\n        return {'ok': False, 'error': str(e)}\n",
    )
    assert scan_broad_except_swallows(tmp_path) == []
