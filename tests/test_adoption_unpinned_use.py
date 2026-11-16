"""47-T7 (unpinned use fails), 47-T9 (pin-comment wording names the commit, not `master`), 47-T10 (one line per root cause)."""

from __future__ import annotations

from types import SimpleNamespace

from py_ci_shared._adoption_pin_comments import comment_mismatches
from py_ci_shared.adoption_matrix import RepoReport, _finding_lines, find_pins

SHA = "a" * 40
URL = "git+https://github.com/fingoldo/py-ci-shared.git"


def test_a_repo_using_modules_with_no_pin_fails_as_unpinned_use(tmp_path):
    report = RepoReport(name="finance", root=tmp_path, modules={"black_filtered_apply": ["pyproject.toml"]})
    assert report.failing
    assert "unpinned-use" in [f.rule for f in report.findings()]


def test_a_pinned_repo_is_not_unpinned_use(tmp_path):
    (tmp_path / "requirements-dev.txt").write_bytes(f"py-ci-shared @ {URL}@{SHA}\n".encode())
    report = RepoReport(name="ok", root=tmp_path, pins=find_pins(tmp_path), modules={"black_filtered_apply": ["pyproject.toml"]})
    assert "unpinned-use" not in [f.rule for f in report.findings()]


def test_the_comment_mismatch_names_the_tag_commit_and_not_master():
    pin = SimpleNamespace(comment_tag="v1.2.3", moving=False, ref="x")
    (why,) = [w for _p, w in comment_mismatches([pin], {"x": "b" * 40}, {"v1.2.3": "c" * 40}, True)]
    assert "c" * 7 in why
    assert "master" not in why


def test_findings_on_one_root_cause_render_as_one_line(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name in ("ruff-blocking.yml", "mypy-full.yml"):
        (wf / name).write_text(f"    uses: fingoldo/py-ci-shared/.github/workflows/{name}@{SHA}\n", encoding="utf-8")
    report = RepoReport(name="c", root=tmp_path, pins=find_pins(tmp_path), allow_behind=2, behind={SHA: (3, "v1.23.11")})
    lines = _finding_lines([report])
    stale = [line for line in lines if "stale-pin" in line]
    assert len(stale) == 1
    assert "ruff-blocking.yml" in stale[0] and "mypy-full.yml" in stale[0]
