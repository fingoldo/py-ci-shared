"""47-F10: a consumer pinned to a between-release SHA of the reusable workflows, with no py-ci-shared-ref input, is reported."""

from __future__ import annotations

from py_ci_shared import adoption_sha_ref as mod

BETWEEN = "a" * 40
RELEASE = "b" * 40


def _workflow(ref_input: str | None = None, sha: str = BETWEEN) -> str:
    lines = [
        "jobs:",
        "  lint:",
        "    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@" + sha,
    ]
    if ref_input is not None:
        lines += ["    with:", f"      py-ci-shared-ref: {ref_input}"]
    return "\n".join(lines) + "\n"


def test_a_between_release_sha_without_the_input_is_reported_at_its_line():
    found = mod.sha_pins_without_ref_input(_workflow(), lambda sha: False)
    assert found == [(3, BETWEEN)]


def test_the_same_sha_passed_as_the_input_is_not_reported():
    assert mod.sha_pins_without_ref_input(_workflow(ref_input=BETWEEN), lambda sha: False) == []


def test_a_release_tag_commit_needs_no_input_and_is_not_reported():
    assert mod.sha_pins_without_ref_input(_workflow(sha=RELEASE), lambda sha: sha == RELEASE) == []


def test_scan_repos_names_the_repo_and_file(tmp_path):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "consumer.yml").write_text(_workflow(), encoding="utf-8")
    findings = mod.scan_repos([("pub", tmp_path)], lambda sha: False)
    assert findings == [f"pub .github/workflows/consumer.yml:3: {BETWEEN[:12]}"]
