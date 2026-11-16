"""44-T7: ``adoption_sha_ref.main()`` and the multi-pin loop (report-only gate, 47-F10).

The fake resolver stands for a py-ci-shared checkout whose only release tag is v1.0.0.
"""

from __future__ import annotations

from py_ci_shared import adoption_matrix
from py_ci_shared import adoption_sha_ref as mod

BETWEEN = "a" * 40
RELEASE = "b" * 40
USES = "    uses: fingoldo/py-ci-shared/.github/workflows/ruff-blocking.yml@"


class _FakeResolver:
    def __init__(self, repo) -> None:
        pass

    def releases(self) -> list[str]:
        return ["v1.0.0"]

    def __call__(self, tag: str):
        return RELEASE if tag == "v1.0.0" else None

    def file_text(self, sha: str, path: str) -> str:
        # The reusable workflow at a between-release SHA, as RefResolver reads it: it declares the py-ci-shared-ref input,
        # so a pin to it without the input is the finding under test (a workflow without the input is skipped by design).
        return "on:\n  workflow_call:\n    inputs:\n      py-ci-shared-ref:\n        type: string\n"


def _run_main(monkeypatch, capsys, tmp_path, workflow: str) -> tuple[int, str]:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "consumer.yml").write_text(workflow, encoding="utf-8")
    monkeypatch.setattr(adoption_matrix, "RefResolver", _FakeResolver)
    monkeypatch.setattr(adoption_matrix, "load_repo_list", lambda path: [("pub", tmp_path)])
    rc = mod.main(["--repos-file", "repos.toml", "--resolve-in", str(tmp_path)])
    return rc, capsys.readouterr().out


def test_main_reports_the_between_release_pin_and_exits_0(monkeypatch, capsys, tmp_path):
    workflow = "jobs:\n  lint:\n" + USES + BETWEEN + "\n"
    rc, out = _run_main(monkeypatch, capsys, tmp_path, workflow)
    assert rc == 0
    assert f"::warning title=uses-sha-without-ref-input::pub .github/workflows/consumer.yml:3: {BETWEEN[:12]}\n" in out
    assert "uses-sha-without-ref-input: 1 pin(s) to a between-release SHA without py-ci-shared-ref\n" in out


def test_main_reports_zero_pins_when_every_pin_is_a_release(monkeypatch, capsys, tmp_path):
    workflow = "jobs:\n  lint:\n" + USES + RELEASE + "\n"
    rc, out = _run_main(monkeypatch, capsys, tmp_path, workflow)
    assert rc == 0
    assert "::warning" not in out
    assert "uses-sha-without-ref-input: 0 pin(s) to a between-release SHA" in out


def test_a_release_pin_before_a_between_release_pin_on_one_line_does_not_hide_the_second():
    line = "    uses: fingoldo/py-ci-shared/.github/workflows/a.yml@" + RELEASE + " uses: fingoldo/py-ci-shared/.github/workflows/b.yml@" + BETWEEN
    assert mod.sha_pins_without_ref_input(line + "\n", lambda sha: sha == RELEASE) == [(1, BETWEEN)]


def test_a_local_or_ownerless_uses_is_not_reported(tmp_path):
    workflow = "jobs:\n  a:\n    uses: /py-ci-shared/.github/workflows/ruff-blocking.yml@" + BETWEEN + "\n"
    assert mod.sha_pins_without_ref_input(workflow, lambda sha: False) == []
