"""Tests for py_ci_shared.required_check_contexts, through an injected fake API (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._consumers import Consumer
from py_ci_shared.ci_health import ApiError
from py_ci_shared.required_check_contexts import check_repo, main


def _api(protection=None, runs_by_sha=None, statuses=None, error=None, shas=("h1", "h0")):
    def get(path: str):
        if "/protection/" in path:
            if error is not None:
                raise error
            return protection
        if "/commits?sha=" in path:
            return [{"sha": s} for s in shas]
        sha = path.split("/commits/")[1].split("/")[0]
        if path.endswith("/status"):
            return {"statuses": [{"context": c} for c in (statuses or {}).get(sha, [])]}
        return {"check_runs": [{"name": n} for n in (runs_by_sha or {}).get(sha, [])]}

    return get


C = Consumer("pyutilz", "fingoldo/pyutilz", "master")


def test_a_required_context_nothing_produces_is_missing() -> None:
    api = _api({"contexts": ["black / black"], "checks": [{"context": "mypy-full / mypy-full"}]}, {"h1": ["black / black"]})
    r = check_repo(C, api)
    assert (r.state, r.missing) == ("missing", ["mypy-full / mypy-full"])


def test_contexts_from_an_older_commit_or_a_commit_status_count() -> None:
    api = _api({"contexts": ["black / black", "mypy-full / mypy-full", "ext"]}, {"h1": ["black / black"], "h0": ["mypy-full / mypy-full"]}, {"h1": ["ext"]})
    assert check_repo(C, api).state == "ok"


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (ApiError("gh api x: HTTP 404", status=404), "unprotected"),
        (ApiError("gh api x: gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"), "unreadable"),
        (ApiError("GET x: HTTP 403 Forbidden", status=403), "unreadable"),
        (ApiError("GET x: URLError: timed out"), "error"),
    ],
)
def test_unreadable_protection_is_classified_never_passed(error: ApiError, state: str) -> None:
    assert check_repo(C, _api(error=error)).state == state


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    cfg = tmp_path / "consumers.toml"
    cfg.write_text('[[consumer]]\nname = "pyutilz"\nrepo = "fingoldo/pyutilz"\nbranch = "master"\n')
    assert main([str(cfg)], api=_api({"contexts": ["a"]}, {"h1": ["a"]})) == 0
    assert main([str(cfg)], api=_api({"contexts": ["a", "gone"]}, {"h1": ["a"]})) == 1
    assert "required context(s) no check on the 5 newest commits produced: gone" in capsys.readouterr().out
    locked = _api(error=ApiError("HTTP 403", status=403))
    assert main([str(cfg)], api=locked) == 0
    assert "::warning::fingoldo/pyutilz@master: branch protection unreadable" in capsys.readouterr().out
    assert main([str(cfg), "--strict"], api=locked) == 1
