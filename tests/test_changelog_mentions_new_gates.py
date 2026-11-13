"""Every module first shipped in the next release is named in the CHANGELOG's Unreleased section (audit 43-F9).

A module can be registered, shipped in a CI job and documented in the README and still have no changelog entry, so a consumer
reading the release notes never learns it exists. The check runs from the registry's ``since`` against the version in the package.
"""

from __future__ import annotations

from pathlib import Path

from py_ci_shared import __version__
from py_ci_shared._toml_compat import tomllib
from py_ci_shared.registry import REGISTRY_FILE

REPO = Path(__file__).resolve().parents[1]


def _unreleased_section() -> str:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = "## Unreleased" if "## Unreleased" in text else f"## {__version__}"  # prepare renames Unreleased to the version
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_every_module_first_shipped_in_the_next_release_is_in_the_unreleased_section() -> None:
    registry = tomllib.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    section = _unreleased_section()
    new = sorted(gate["name"] for gate in registry.get("gate", []) if gate.get("since") == __version__)
    assert new, "the registry names no module for the next release; the check would pass vacuously"
    missing = [name for name in new if name not in section]
    assert missing == [], f"not named in the CHANGELOG Unreleased section: {missing}"
