"""Tests for py_ci_shared.env_write_restore: the seeded leak, the shapes that restore, and the gate's failure modes."""

from __future__ import annotations

from pathlib import Path

import pytest

import py_ci_shared.env_write_restore as gate
from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.env_write_restore import assert_env_write_restore, find_env_write_restore

BOM = b"\xef\xbb\xbf"
CANARY = Path(__file__).resolve().parent / "canary" / "env_write_restore"


def _canary(tmp_path: Path, case: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for p in (CANARY / case).rglob("*.canary"):
        (tmp_path / p.name[: -len(".canary")]).write_bytes(p.read_bytes())
    return tmp_path


def _one(tmp_path: Path, src: str) -> list:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "m.py").write_bytes(src.encode())
    return find_env_write_restore(tmp_path, use_git=False)


def test_reports_the_seeded_leak(tmp_path):
    found = find_env_write_restore(_canary(tmp_path, "violation"), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("seed.py", 5, gate.RULE)]
    assert found[0].message == "test_leaks_a_setting_into_every_later_test: os.environ['MLFRAME_FE_GPU_STRICT'] = '1'"
    with pytest.raises(AssertionError, match=r"1 env-write-restore finding"):
        assert_env_write_restore(tmp_path, use_git=False)


def test_negative_control_every_restoring_shape_is_not_reported(tmp_path):
    # monkeypatch, patch.dict, try/finally and a yield-fixture that restores: the nearest correct code for each leak
    assert find_env_write_restore(_canary(tmp_path, "clean"), use_git=False) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = find_env_write_restore(_canary(tmp_path / "a", "violation"), use_git=False)
    bom = find_env_write_restore(_canary(tmp_path / "b", "bom"), use_git=False)
    assert bom and [(f.line, f.message) for f in bom] == [(f.line, f.message) for f in plain]


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _canary(tmp_path, "unparsable")
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_env_write_restore(root, use_git=False, min_files=0)
    assert find_env_write_restore(root, use_git=False, allow_unparsed=True, min_files=0) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_env_write_restore(tmp_path, use_git=False)


@pytest.mark.parametrize(
    "stmt",
    [
        'os.environ["K"] = "1"',
        'os.environ["K"] += "x"',
        'del os.environ["K"]',
        'os.environ.update({"K": "1"})',
        'os.environ.setdefault("K", "1")',
        'os.environ.pop("K", None)',
        'os.putenv("K", "1")',
    ],
)
def test_every_write_form_is_reported(tmp_path, stmt):
    found = _one(tmp_path, f"import os\n\ndef test_x():\n    {stmt}\n")
    assert len(found) == 1 and found[0].line == 4


def test_an_aliased_environ_is_still_the_environment(tmp_path):
    found = _one(tmp_path, 'from os import environ as E\n\ndef test_x():\n    E["K"] = "1"\n')
    assert len(found) == 1


def test_a_copy_of_the_environment_is_not_the_environment(tmp_path):
    assert _one(tmp_path, 'import os\n\ndef test_x():\n    env = os.environ.copy()\n    env["K"] = "1"\n') == []


def test_a_write_just_before_a_try_whose_finally_restores_is_accepted(tmp_path):
    src = (
        "import os\n\ndef test_x():\n    old = os.environ.get('K')\n    os.environ['K'] = '1'\n    try:\n        pass\n"
        "    finally:\n        os.environ.pop('K', None)\n"
    )
    assert _one(tmp_path, src) == []


def test_a_try_whose_finally_does_not_restore_is_not_a_restoration(tmp_path):
    src = "import os\n\ndef test_x():\n    os.environ['K'] = '1'\n    try:\n        pass\n    finally:\n        print('done')\n"
    assert len(_one(tmp_path, src)) == 1


def test_a_yield_fixture_that_never_restores_is_reported(tmp_path):
    src = "import os\nimport pytest\n\n@pytest.fixture\ndef strict():\n    os.environ['K'] = '1'\n    yield\n"
    assert len(_one(tmp_path, src)) == 1


def test_a_yield_fixture_that_restores_after_the_yield_is_accepted(tmp_path):
    src = "import os\nimport pytest\n\n@pytest.fixture\ndef strict():\n    os.environ['K'] = '1'\n    yield\n    os.environ.pop('K', None)\n"
    assert _one(tmp_path, src) == []


def test_setup_with_a_restoring_teardown_in_the_same_class_is_accepted(tmp_path):
    src = (
        "import os\n\nclass TestA:\n    def setup_method(self):\n        os.environ['K'] = '1'\n"
        "    def teardown_method(self):\n        os.environ.pop('K', None)\n"
    )
    assert _one(tmp_path, src) == []
    assert len(_one(tmp_path / "b", "import os\n\nclass TestA:\n    def setup_method(self):\n        os.environ['K'] = '1'\n")) == 1


def test_a_function_that_registers_a_finalizer_or_is_named_as_a_restorer_is_accepted(tmp_path):
    fin = "import os\n\ndef test_x(request):\n    os.environ['K'] = '1'\n    request.addfinalizer(lambda: None)\n"
    assert _one(tmp_path / "a", fin) == []
    assert _one(tmp_path / "b", "import os\n\ndef _restore_env():\n    os.environ['K'] = 'old'\n") == []


def test_a_nested_function_is_its_own_scope(tmp_path):
    src = "import os\n\ndef test_x():\n    def helper():\n        os.environ['K'] = '1'\n    helper()\n"
    found = _one(tmp_path, src)
    assert [f.message.split(":")[0] for f in found] == ["test_x.helper"]


def test_a_baseline_accepts_known_sites_rejects_new_ones_and_flags_stale_entries(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.py").write_text("import os\n\ndef test_a():\n    os.environ['K'] = '1'\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):  # a refresh that grows from nothing reports the seeding and skips
        assert_env_write_restore(root, baseline_path=baseline, refresh=True, grow=True, use_git=False)
    assert_env_write_restore(root, baseline_path=baseline, use_git=False)  # the recorded site is accepted

    (root / "b.py").write_text("import os\n\ndef test_b():\n    os.environ['J'] = '1'\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="new finding"):
        assert_env_write_restore(root, baseline_path=baseline, use_git=False)

    (root / "b.py").unlink()
    (root / "a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"stale|no longer"):
        assert_env_write_restore(root, baseline_path=baseline, use_git=False)


def test_a_missing_baseline_fails_instead_of_accepting_everything(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="does not exist"):
        assert_env_write_restore(root, baseline_path=tmp_path / "nope.json", use_git=False)
