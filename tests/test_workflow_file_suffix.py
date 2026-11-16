"""T2 (audit 44): every workflow file this repo ships ends in ``.yml``.

The workflow tests glob ``*.yml``; GitHub also runs ``*.yaml``, so a ``.yaml`` workflow would escape the SHA-pin,
moving-label, install-command and RELEASE_TOKEN checks without any red. This test makes that file red instead.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


def _non_yml_workflows(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("*.y*ml") if p.suffix != ".yml")


def test_no_workflow_uses_the_yaml_suffix():
    assert _non_yml_workflows(WORKFLOWS) == []
