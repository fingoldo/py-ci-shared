"""Tests for py_ci_shared.ci_pin_version_skew."""

from __future__ import annotations

from pathlib import Path

import pytest

import py_ci_shared
from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.ci_pin_version_skew import assert_installed_matches_ci_pin, find_pin_skew_findings, installed_py_ci_shared_version

SHA = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret
PIP = 'pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git@%s"'
USES = "uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@%s"


def _wf(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_bytes(body.encode("utf-8"))
    return tmp_path


def _rules(found) -> list[str]:
    return [f.rule for f in found]


def test_installed_older_than_the_pin_is_reported_at_the_pin_line(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"jobs:\n  t:\n    steps:\n      - run: {PIP % SHA}  # v1.20.0\n"})
    (found,) = find_pin_skew_findings(root, installed_version="1.18.0")
    assert (found.path, found.line, found.rule) == (".github/workflows/a-ci.yml", 4, "ci-pin-installed-older")
    assert "1.18.0" in found.message and "1.20.0" in found.message and "Upgrade" in found.message


def test_installed_newer_is_a_finding_that_tolerate_ahead_downgrades(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"steps:\n  - {USES % SHA}  # v1.18.0\n"})
    (found,) = find_pin_skew_findings(root, installed_version="1.21.1")
    assert (found.line, found.rule) == (2, "ci-pin-installed-newer")
    assert "tolerate_ahead=True" in found.message and "Bump" in found.message
    assert find_pin_skew_findings(root, installed_version="1.21.1", tolerate_ahead=True) == []


def test_tolerate_ahead_never_tolerates_installed_older(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.20.0\n"})
    assert _rules(find_pin_skew_findings(root, installed_version="1.19.9", tolerate_ahead=True)) == ["ci-pin-installed-older"]


def test_equal_versions_are_clean_and_a_v_prefix_or_dev_suffix_is_read(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- run: {PIP % SHA}  # v1.18.0\n- {USES % SHA}  # 1.18.0\n"})
    assert find_pin_skew_findings(root, installed_version="1.18.0") == []
    assert find_pin_skew_findings(root, installed_version="1.18.0.dev3") == []


def test_numeric_not_lexical_version_order(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.9.0\n"})
    assert _rules(find_pin_skew_findings(root, installed_version="1.10.0")) == ["ci-pin-installed-newer"]


def test_a_sha_pin_without_a_version_comment_is_reported(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- run: {PIP % SHA}\n"})
    (found,) = find_pin_skew_findings(root, installed_version="1.18.0")
    assert (found.rule, found.line) == ("ci-pin-no-version", 1)
    assert "Append" in found.message


def test_mixed_pins_are_reported_once_naming_every_pin(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.18.0\n", "b-ci.yml": f"- run: {PIP % SHA}  # v1.19.0\n"})
    found = find_pin_skew_findings(root, installed_version="1.19.0", tolerate_ahead=True)
    (mixed,) = [f for f in found if f.rule == "ci-pin-mixed"]
    assert "a-ci.yml:1 = 1.18.0" in mixed.message and "b-ci.yml:1 = 1.19.0" in mixed.message and "Move" in mixed.message


def test_release_tag_ref_is_a_pin_but_moving_tag_and_branch_are_not(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": "- uses: fingoldo/py-ci-shared/.github/workflows/x.yml@v1\n- uses: fingoldo/py-ci-shared/.github/workflows/y.yml@main\n"})
    assert find_pin_skew_findings(root, installed_version="9.9.9") == []
    root2 = _wf(tmp_path / "t", {"a-ci.yml": "- uses: fingoldo/py-ci-shared/.github/workflows/x.yml@v1.18.0\n"})
    assert _rules(find_pin_skew_findings(root2, installed_version="1.21.1")) == ["ci-pin-installed-newer"]


def test_other_repos_and_whole_line_comments_are_not_pins(tmp_path):
    body = f"# {PIP % SHA}  # v1.0.0\n- uses: actions/checkout@{SHA}  # v4.1.0\n- uses: fingoldo/other-repo/.github/workflows/x.yml@{SHA}  # v1.0.0\n"
    assert find_pin_skew_findings(_wf(tmp_path, {"a-ci.yml": body}), installed_version="1.18.0") == []


def test_lag_behind_known_releases_uses_max_lag(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.18.0\n"})
    releases = ["v1.18.0", "v1.19.0", "v1.20.0"]
    assert find_pin_skew_findings(root, installed_version="1.18.0", known_releases=releases) == []  # 2 behind, tolerated
    found = find_pin_skew_findings(root, installed_version="1.18.0", known_releases=[*releases, "v1.21.0"])
    assert _rules(found) == ["ci-pin-lags-release"] and "3 releases behind 1.21.0" in found[0].message
    assert find_pin_skew_findings(root, installed_version="1.18.0", known_releases=[*releases, "v1.21.0"], max_lag=3) == []


def test_unusable_installed_version_is_a_finding_not_a_pass(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.18.0\n"})
    assert _rules(find_pin_skew_findings(root, installed_version="unknown")) == ["ci-pin-installed-unknown"]


def test_default_installed_version_is_the_imported_package(tmp_path):
    assert installed_py_ci_shared_version() == py_ci_shared.__version__
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v{py_ci_shared.__version__}\n"})
    assert find_pin_skew_findings(root) == []


def test_a_bom_prefixed_workflow_is_read_like_the_plain_one(tmp_path):
    body = f"- {USES % SHA}  # v1.20.0\n"
    plain = find_pin_skew_findings(_wf(tmp_path / "p", {"a-ci.yml": body}), installed_version="1.18.0")
    bom = find_pin_skew_findings(_wf(tmp_path / "b", {"a-ci.yml": "\ufeff" + body}), installed_version="1.18.0")
    assert plain and bom == plain


def test_an_undecodable_workflow_fails_by_name_unless_allowed(tmp_path):
    root = _wf(tmp_path, {"ok-ci.yml": f"- {USES % SHA}  # v1.18.0\n"})
    (root / ".github" / "workflows" / "bad-ci.yml").write_bytes(b"\xff\xfe\x00bad\x80")
    with pytest.raises(UnparsedFilesError, match=r"bad-ci.yml"):
        find_pin_skew_findings(root, installed_version="1.18.0")
    assert find_pin_skew_findings(root, installed_version="1.18.0", allow_unparsed=True) == []


def test_no_workflow_files_fails_the_floor(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    with pytest.raises(EmptyScanError):
        find_pin_skew_findings(tmp_path, installed_version="1.18.0")


def test_assert_entry_raises_with_the_rendered_findings(tmp_path):
    root = _wf(tmp_path, {"a-ci.yml": f"- {USES % SHA}  # v1.20.0\n"})
    with pytest.raises(AssertionError, match=r"a-ci\.yml:1: \[ci-pin-installed-older\]"):
        assert_installed_matches_ci_pin(root, installed_version="1.18.0")
    assert_installed_matches_ci_pin(root, installed_version="1.20.0")


def test_the_ref_input_of_the_reusable_workflows_is_a_pin_too(tmp_path):
    body = f"- {USES % SHA}  # v1.20.0" + chr(10) + f"      py-ci-shared-ref: {SHA}  # v1.18.0" + chr(10)
    found = find_pin_skew_findings(_wf(tmp_path, {"a-ci.yml": body}), installed_version="1.20.0")
    assert sorted((f.line, f.rule) for f in found) == [(1, "ci-pin-mixed"), (2, "ci-pin-installed-newer")]
