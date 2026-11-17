"""Doc and monitor-config parity fixed from audits/2026-11-16/43_registry_docs_parity.md."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _unreleased() -> str:
    """The notes of the next release: its ``## Unreleased`` heading, or once prepared, the heading of the version in pyproject."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    heading = re.search(r"^## (?:Unreleased[^\n]*|" + re.escape(version) + r")\s*$", text, re.M)
    start = heading.start()
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_43_t1_core_only_find_spec_change_has_behaviour_change_entry() -> None:
    section = _unreleased()
    bullets = [b for b in section.split("\n- ") if "ci_install_covers_entry_imports" in b]
    assert any("Behaviour change" in b and "find_spec" in b and "What to do:" in b for b in bullets)


def test_43_t2_claude_md_names_the_file_that_holds_exempt_and_parses() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "`tests/test_gate_teeth.py`" not in text.split("## Running tests")[0].replace("`tests/test_gate_teeth.py` the tests", "")
    assert "`parses=False` in `tests/_gate_teeth_canaries.py`" in text
    assert "`EXEMPT`\n  entry in `tests/_gate_teeth_canaries.py`" in text


def test_43_t3_corpus_drift_has_no_dead_mlframe_branch() -> None:
    text = (ROOT / ".github" / "workflows" / "corpus-drift.yml").read_text(encoding="utf-8")
    assert "mlframe" not in text


def test_43_t4_changelog_records_mlframe_leaving_consumers_toml() -> None:
    section = _unreleased()
    assert "mlframe" in section
    assert "configs/consumers.toml" in section
    assert "accept: true" in section


def test_43_t6_new_gate_docs_name_the_changelog_step() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").split("## Running tests")[0]
    assert "CHANGELOG" in claude
    cli = (ROOT / "src" / "py_ci_shared" / "cli.py").read_text(encoding="utf-8")
    assert "CHANGELOG" in cli.split("Every generated test and the canary fail")[1].split("\n")[0]
