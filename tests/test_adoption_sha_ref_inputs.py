"""47-T5 (a pinned workflow that does not declare py-ci-shared-ref is not a warning), 47-T6 (positional repos, no required --repos-file)."""

from __future__ import annotations

from py_ci_shared import adoption_sha_ref as mod

BETWEEN = "a" * 40


def _workflow() -> str:
    return f"jobs:\n  lint:\n    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@{BETWEEN}\n"


def test_a_pinned_workflow_that_declares_no_ref_input_is_not_reported():
    assert mod.sha_pins_without_ref_input(_workflow(), lambda sha: False, declares_input=lambda name, sha: False) == []


def test_a_pinned_workflow_that_declares_the_input_is_still_reported():
    assert mod.sha_pins_without_ref_input(_workflow(), lambda sha: False, declares_input=lambda name, sha: True) == [(3, BETWEEN)]


def test_the_declaration_is_read_from_the_workflow_text():
    declared = "on:\n  workflow_call:\n    inputs:\n      py-ci-shared-ref:\n        description: x\n"
    assert mod.declares_ref_input(declared)
    assert not mod.declares_ref_input("on:\n  workflow_call:\n    inputs: {}\n")
    assert mod.declares_ref_input(None)  # unreadable at that SHA: keep the warning


def test_positional_repo_roots_need_no_repos_file(tmp_path, capsys):
    root = tmp_path / "consumer"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(_workflow(), encoding="utf-8")
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    assert mod.main([str(root), "--resolve-in", str(nogit)]) == 0
    assert "uses-sha-without-ref-input: 1 pin(s)" in capsys.readouterr().out
