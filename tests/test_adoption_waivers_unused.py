"""A waiver that applies to no scanned consumer, or a name listed twice, is reported instead of silently dropped (audit 47-F15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core.errors import CoreError
from py_ci_shared._adoption_waivers import load_waivers
from py_ci_shared.adoption_matrix import main

ROW = '[[waiver]]\nname = "{name}"\noldest = "v1.0.0"\nuntil = "2099-01-01"\nreason = "lagging, being bumped"\n'


def test_a_name_listed_twice_is_refused_not_replaced(tmp_path: Path) -> None:
    """Negative control (47-F15): the old loader replaced the first row with the second without a word."""
    path = tmp_path / "w.toml"
    path.write_text(ROW.format(name="app") + "\n" + ROW.format(name="app"), encoding="utf-8")
    with pytest.raises(CoreError, match="listed twice"):
        load_waivers(path)


def test_a_waiver_for_no_scanned_consumer_fails_the_run(tmp_path: Path, capsys) -> None:
    consumer = tmp_path / "app"
    consumer.mkdir()
    (consumer / "requirements.txt").write_text("requests==2.0\n", encoding="utf-8")
    waivers = tmp_path / "w.toml"
    waivers.write_text(ROW.format(name="ghost"), encoding="utf-8")
    assert main([str(consumer), "--no-local-copies", "--waivers", str(waivers)]) == 1
    assert "waiver(s) for no scanned consumer, so they apply to nothing: ghost" in capsys.readouterr().err


def test_advisory_mode_names_it_without_failing(tmp_path: Path, capsys) -> None:
    consumer = tmp_path / "app"
    consumer.mkdir()
    (consumer / "requirements.txt").write_text("requests==2.0\n", encoding="utf-8")
    waivers = tmp_path / "w.toml"
    waivers.write_text(ROW.format(name="ghost"), encoding="utf-8")
    main([str(consumer), "--no-local-copies", "--waivers", str(waivers), "--advisory"])
    assert "waiver(s) for no scanned consumer" not in capsys.readouterr().err
