"""``ci-green`` turns a GitHub client error into a verdict line and exit 1, not a traceback (audit 46-F23)."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path
from typing import Any

GUARD_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release_guard.py"
SHA = "a" * 40


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("release_guard_ci_green", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("release_guard_ci_green", module)
    spec.loader.exec_module(module)
    return module


def _refusing_api(path: str) -> Any:
    raise urllib.error.HTTPError(f"https://api.github.com/{path}", 404, "Not Found", None, None)


def test_a_client_error_prints_a_verdict_line_and_exits_1(monkeypatch, capsys) -> None:
    guard = _load_guard()
    monkeypatch.setattr(guard, "_github", lambda token: _refusing_api)
    code = guard.main(["ci-green", "--repo", "owner/name", "--sha", SHA])
    out = capsys.readouterr().out
    assert code == 1
    assert "::error::GitHub API refused the request for owner/name (404)" in out
    assert "Traceback" not in out


def test_a_green_run_still_exits_0(monkeypatch, capsys) -> None:
    """Negative control on the same path: the new branch must not turn a green verdict red."""
    guard = _load_guard()
    green = {"workflow_runs": [{"id": 1, "head_sha": SHA, "status": "completed", "conclusion": "success", "html_url": "u"}]}
    monkeypatch.setattr(guard, "_github", lambda token: (lambda path: green if "runs" in path else []))
    monkeypatch.setattr(guard, "_warn_failed_legs", lambda api, args: None)
    assert guard.main(["ci-green", "--repo", "owner/name", "--sha", SHA]) == 0
