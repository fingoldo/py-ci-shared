"""Tests for py_ci_shared.patch_target_read_at_call: a patch of a name the module only re-exports is inert."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.patch_target_read_at_call import RULE, assert_patch_target_read_at_call, find_inert_patches

BOM = b"\xef\xbb\xbf"

CONFIG_BASE = b"LIMIT = 100\n"
REEXPORT = b"from config_base import LIMIT\n"

VIOLATION_SERVICE = b"""from reexport import LIMIT


def cap(n):
    return min(n, LIMIT)
"""

CLEAN_SERVICE = b"""import reexport


def cap(n):
    return min(n, reexport.LIMIT)
"""

TEST_PATCHES = b"""import reexport
import service


def test_cap_limits_to_the_configured_limit(monkeypatch):
    monkeypatch.setattr(reexport, "LIMIT", 5)
    assert service.cap(3) == 3
"""


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def _base(service: bytes, test: bytes = TEST_PATCHES) -> dict[str, bytes]:
    return {"config_base.py": CONFIG_BASE, "reexport.py": REEXPORT, "service.py": service, "test_service.py": test}


def _found(tmp_path: Path, files: dict[str, bytes], **kwargs):
    root = _corpus(tmp_path, files)
    return find_inert_patches([root], [root], use_git=False, **kwargs)


def test_reports_the_violation_once_at_the_patch_line(tmp_path):
    """The monkeypatch of the re-exported LIMIT is the one finding, at its own line; the service reading its own binding is why it changes nothing."""
    found = _found(tmp_path, _base(VIOLATION_SERVICE))
    assert [(f.path, f.line, f.rule) for f in found] == [("test_service.py", 6, RULE)]
    assert "patch of reexport.LIMIT changes nothing" in found[0].message


def test_clean_canary_is_not_reported_when_the_sibling_reads_the_module_attribute(tmp_path):
    """The sibling reads reexport.LIMIT at call time, so the same patch reaches the code under test and is live."""
    assert _found(tmp_path, _base(CLEAN_SERVICE)) == []


def test_a_bom_prefixed_file_gives_the_same_findings(tmp_path):
    """A byte-order mark on the test file changes neither the finding nor its line."""
    plain = _found(tmp_path / "plain", _base(VIOLATION_SERVICE))
    bommed = _found(tmp_path / "bom", _base(VIOLATION_SERVICE, test=BOM + TEST_PATCHES))
    assert plain and [(f.line, f.message) for f in bommed] == [(f.line, f.message) for f in plain]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    """A test file that cannot be parsed is an error naming it; allow_unparsed skips it and still judges the rest."""
    root = _corpus(tmp_path, {**_base(VIOLATION_SERVICE), "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_inert_patches([root], [root], use_git=False)
    assert len(find_inert_patches([root], [root], use_git=False, allow_unparsed=True)) == 1


def test_an_empty_test_corpus_fails_the_floor(tmp_path):
    """No test file parsed raises EmptyScanError rather than a clean pass."""
    root = _corpus(tmp_path, {"config_base.py": CONFIG_BASE, "reexport.py": REEXPORT})
    (tmp_path / "tests").mkdir()
    with pytest.raises(EmptyScanError):
        find_inert_patches([tmp_path / "tests"], [root], use_git=False)


def test_a_name_the_module_defines_is_not_reported(tmp_path):
    """A module that defines LIMIT itself, or both defines and imports it, is never inert: its own readers may use the patched global."""
    defined = {
        "config_base.py": CONFIG_BASE,
        "reexport.py": b"LIMIT = 100\n",
        "service.py": VIOLATION_SERVICE,
        "test_service.py": TEST_PATCHES,
    }
    assert _found(tmp_path / "defined", defined) == []
    read_bare = {**_base(VIOLATION_SERVICE), "reexport.py": b"from config_base import LIMIT\n\n\ndef limit_of():\n    return LIMIT\n"}
    assert _found(tmp_path / "read_bare", read_bare) == []
    rebound = {**_base(VIOLATION_SERVICE), "reexport.py": REEXPORT + b"\n\ndef bump():\n    global LIMIT\n    LIMIT = 1\n"}
    assert _found(tmp_path / "rebound", rebound) == []


def test_a_function_reading_the_name_through_the_module_keeps_the_patch_live(tmp_path):
    """A getattr(mod, NAME) read in any function of a scanned module keeps the patch live."""
    via_getattr = {**_base(VIOLATION_SERVICE), "service.py": b"import reexport\n\n\ndef cap(n):\n    return min(n, getattr(reexport, 'LIMIT'))\n"}
    assert _found(tmp_path / "getattr", via_getattr) == []


def test_mock_patch_forms_are_judged_like_monkeypatch(tmp_path):
    """unittest.mock.patch.object(mod, NAME) and patch("pkg.mod.NAME") are the same inert patch."""
    test = b"""from unittest import mock
from unittest.mock import patch

import reexport


def test_object_form():
    with mock.patch.object(reexport, "LIMIT", 5):
        pass


def test_dotted_string_form():
    with patch("reexport.LIMIT", 5):
        pass
"""
    found = _found(tmp_path, _base(VIOLATION_SERVICE, test=test))
    assert [f.line for f in found] == [8, 13]


def test_a_non_module_target_is_not_judged(tmp_path):
    """Patching an attribute of a class or an object that is not a first-party module is outside this gate."""
    test = b"import service\n\n\ndef test_x(monkeypatch):\n    monkeypatch.setattr(service.cap, 'LIMIT', 5)\n"
    assert _found(tmp_path, _base(VIOLATION_SERVICE, test=test)) == []


def test_the_violation_turns_green_when_the_production_code_reads_at_call_time(tmp_path):
    """Teeth, both ways: the violation's service reading its own binding is reported, and the clean form of the same service is not."""
    assert len(_found(tmp_path / "violation", _base(VIOLATION_SERVICE))) == 1
    assert _found(tmp_path / "clean", _base(CLEAN_SERVICE)) == []
    assert len(_found(tmp_path / "back", _base(VIOLATION_SERVICE))) == 1


def test_assert_fails_on_a_finding_and_a_baseline_accepts_only_what_it_recorded(tmp_path):
    """Without a baseline the entry point fails with the finding; a refreshed baseline accepts it, and a new inert patch still fails."""
    root = _corpus(tmp_path / "c", _base(VIOLATION_SERVICE))
    with pytest.raises(AssertionError, match=r"patch-target-read-at-call"):
        assert_patch_target_read_at_call([root / "test_service.py"], [root], use_git=False)
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_patch_target_read_at_call([root / "test_service.py"], [root], baseline_path=baseline, refresh=True, grow=True, use_git=False)
    assert_patch_target_read_at_call([root / "test_service.py"], [root], baseline_path=baseline, use_git=False)
    (root / "test_more.py").write_bytes(TEST_PATCHES.replace(b"test_cap_limits", b"test_more_limits"))
    with pytest.raises(pytest.fail.Exception, match=r"test_more\.py"):
        assert_patch_target_read_at_call([root / "test_service.py", root / "test_more.py"], [root], baseline_path=baseline, use_git=False)
