"""Dated lag waivers for consumer-pins: accepted lag is told apart from neglect (audit 47-F1).

Six of thirteen consumers were 3 to 12 releases behind a tolerance of 2, so consumer-pins stayed red and a real moving or unresolvable pin could hide in it."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from py_ci_shared._core import CoreError
from py_ci_shared.adoption_matrix import Waiver, load_waivers, waived_allowance

REPO = Path(__file__).resolve().parents[1]
RELEASES = ["v1.22.8", "v1.22.9", "v1.23.0", "v1.23.1", "v1.23.2"]
W = Waiver("app", "v1.22.9", date(2026, 12, 1), "waits on the app repo")


def test_no_waiver_keeps_the_base_tolerance():
    assert waived_allowance(None, RELEASES, 2, date(2026, 11, 7)) == (2, "")


def test_a_live_waiver_widens_the_tolerance_to_the_releases_after_oldest():
    allow, note = waived_allowance(W, RELEASES, 2, date(2026, 12, 1))  # the last day counts
    assert allow == 3 and "waived until 2026-12-01" in note and "v1.22.9" in note


def test_a_waiver_never_narrows_the_base_tolerance():
    assert waived_allowance(Waiver("app", "v1.23.1", date(2026, 12, 1), "r"), RELEASES, 2, date(2026, 11, 7))[0] == 2


def test_an_expired_waiver_restores_the_base_tolerance_and_says_so():
    allow, note = waived_allowance(W, RELEASES, 2, date(2026, 12, 2))
    assert allow == 2 and "expired on 2026-12-01" in note


def test_a_waiver_for_a_tag_that_does_not_exist_grants_nothing():
    allow, note = waived_allowance(Waiver("app", "v1.9.9", date(2026, 12, 1), "r"), RELEASES, 2, date(2026, 11, 7))
    assert allow == 2 and "grants nothing" in note


@pytest.mark.parametrize(
    "body",
    [
        '[[waiver]]\nname = "a"\n',
        '[[waiver]]\nname = "a"\noldest = "v1"\nuntil = "2026-12-01"\nreason = "r"\n',
        '[[waiver]]\nname = "a"\noldest = "v1.2.3"\nuntil = "soon"\nreason = "r"\n',
    ],
)
def test_a_malformed_waiver_is_refused(tmp_path: Path, body: str):
    path = tmp_path / "w.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(CoreError):
        load_waivers(path)


def test_the_committed_waivers_load_and_consumer_pins_passes_them():
    waivers = load_waivers(REPO / "configs" / "pin_waivers.toml")
    consumers = (REPO / "configs" / "consumers.toml").read_text(encoding="utf-8")
    assert waivers and all(f'name = "{n}"' in consumers for n in waivers), "a waiver names a consumer that is not in consumers.toml"
    assert "--waivers configs/pin_waivers.toml" in (REPO / ".github" / "workflows" / "consumer-pins.yml").read_text(encoding="utf-8")
