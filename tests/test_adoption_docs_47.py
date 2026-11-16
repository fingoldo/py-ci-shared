"""47-T2 (waiver file makes no unchecked claim), 47-T11 (README has an adoption path and no stale example)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_the_waiver_header_claims_no_tolerance_status_it_cannot_check():
    assert "every pin is within the tolerance" not in (REPO / "configs" / "pin_waivers.toml").read_text(encoding="utf-8")


def test_readme_has_an_adoption_path_and_no_stale_example():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## Adopting in a new repo" in readme
    assert 'pip install "py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared.git"\n' not in readme
    assert "Use the moving @v1 tag" not in readme
    assert "rev: v1.17.0" not in readme
    assert "rev: v1.17.0" not in (REPO / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
