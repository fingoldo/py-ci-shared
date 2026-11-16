"""49-T10: a gate's message names its baseline by its repo-relative posix path, never the machine's absolute path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared import _text_ratchet


def test_text_ratchet_message_names_the_baseline_repo_relative(tmp_path: Path) -> None:
    directory = tmp_path / "tool" / "meta" / "baselines"
    directory.mkdir(parents=True)
    (directory / "demo_gate.json").write_text(json.dumps({"accepted": {"gone.sh:1": "old"}}), encoding="utf-8")
    with pytest.raises(AssertionError) as info:
        _text_ratchet.enforce_findings(
            "demo_gate",
            {"other.sh:1": "x"},
            repo_root=tmp_path,
            baseline_dir=_text_ratchet.DEFAULT_BASELINE_DIR,
            label="demo: check",
            guidance="g",
        )
    message = str(info.value)
    assert str(tmp_path) not in message
    assert "\\" not in message.split("\n")[0] or "tool/meta/baselines/demo_gate.json" in message
    assert "tool/meta/baselines/demo_gate.json" in message
