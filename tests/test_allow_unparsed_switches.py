"""``allow_unparsed`` on the two gates that read source text: the default keeps the behaviour they always had, and the
switch reports or skips an unreadable file as it says it does.

A file is unreadable when its bytes are not valid UTF-8, which ``read_source`` raises as a SourceError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import SourceError
from py_ci_shared.stale_comment_age import _candidates
from py_ci_shared.test_partition_reachability import _read, find_permanent_skips

UNREADABLE = b"# TODO fix this later\nvalue = '\xff\xfe'\n"


def test_stale_comment_age_default_still_scans_an_unreadable_file_with_a_lossy_decode(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_bytes(UNREADABLE)
    assert _candidates(path, False), "the comment in an unreadable file must still be checked"


def test_stale_comment_age_strict_reports_an_unreadable_file_and_checks_nothing_in_it(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_bytes(UNREADABLE)
    problems: list[str] = []
    assert _candidates(path, False, problems) == {}
    assert len(problems) == 1 and str(path) in problems[0] and "not checked" in problems[0]


def test_partition_reachability_default_fails_on_an_unreadable_spec_file(tmp_path: Path) -> None:
    (tmp_path / "a.spec.ts").write_bytes(UNREADABLE)
    with pytest.raises(SourceError):
        find_permanent_skips([tmp_path])


def test_partition_reachability_allow_unparsed_skips_the_unreadable_spec_file(tmp_path: Path) -> None:
    (tmp_path / "a.spec.ts").write_bytes(UNREADABLE)
    (tmp_path / "b.spec.ts").write_text("test.skip('x', () => {});\n", encoding="utf-8")
    assert find_permanent_skips([tmp_path], allow_unparsed=True) == ["b.spec.ts:1: test.skip('x', () => {});"]


def test_partition_reachability_read_skips_an_unreadable_runner_only_when_allowed(tmp_path: Path) -> None:
    bad = tmp_path / "runner.yml"
    bad.write_bytes(UNREADABLE)
    with pytest.raises(SourceError):
        _read([bad])
    assert _read([bad], allow_unparsed=True) == ""
