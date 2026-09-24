"""Tests for atomic_write_staging: seeded staging bugs, their fixed forms, and the np.save suffix behaviour itself."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from py_ci_shared.atomic_write_staging import RULE_NPSAVE, RULE_REWRITE, assert_atomic_write_staging, find_atomic_write_staging


def _found(tmp_path: Path, body: str, **kw: object) -> list[tuple[str, int]]:
    (tmp_path / "m.py").write_text(textwrap.dedent(body), encoding="utf-8")
    findings, _ = find_atomic_write_staging(tmp_path, use_git=False, **kw)  # type: ignore[arg-type]
    return [(f.rule, f.line) for f in findings]


class TestTheNumpyBehaviour:
    def test_np_save_appends_the_suffix_the_rule_is_about(self, tmp_path):
        np = pytest.importorskip("numpy")
        np.save(str(tmp_path / "x.npy.123.partial"), np.zeros(1))
        assert sorted(p.name for p in tmp_path.iterdir()) == ["x.npy.123.partial.npy"]
        with pytest.raises(OSError):
            os.replace(tmp_path / "x.npy.123.partial", tmp_path / "x.npy")


class TestNpsaveSuffix:
    @pytest.mark.parametrize(
        "staging",
        [
            'f"{path}.{os.getpid()}.partial"',
            'path.with_name(path.name + ".tmp")',
            'str(path) + ".tmp"',
            '"cache.npy.partial"',
            'path.with_suffix(".tmp")',
        ],
    )
    def test_a_staging_name_without_the_suffix_is_reported(self, tmp_path, staging):
        body = f"import os\nimport numpy as np\ndef save(path, a):\n    tmp = {staging}\n    np.save(tmp, a)\n    os.replace(tmp, path)\n"
        assert _found(tmp_path, body) == [(RULE_NPSAVE, 5)]

    @pytest.mark.parametrize(
        "staging",
        ['path.with_name(f"{path.stem}.{os.getpid()}.partial.npy")', 'f"{path}.tmp.npy"', 'path.with_suffix(".tmp.npy")'],
    )
    def test_a_staging_name_that_keeps_the_suffix_is_clean(self, tmp_path, staging):
        body = f"import os\nimport numpy as np\ndef save(path, a):\n    tmp = {staging}\n    np.save(tmp, a)\n    os.replace(tmp, path)\n"
        assert _found(tmp_path, body) == []

    def test_savez_wants_npz_and_aliases_and_rename_forms_are_resolved(self, tmp_path):
        body = """
            import shutil
            from numpy import savez_compressed as sz
            def save(path, a):
                tmp = f"{path}.part.npy"
                sz(tmp, a=a)
                shutil.move(tmp, path)
            def save2(path, a):
                import numpy
                tmp = path.with_suffix(".tmp")
                numpy.save(tmp, a)
                tmp.replace(path)
        """
        assert _found(tmp_path, body) == [(RULE_NPSAVE, 6), (RULE_NPSAVE, 11)]

    def test_no_rename_a_file_object_or_an_unreadable_suffix_is_not_judged(self, tmp_path):
        body = """
            import os
            import numpy as np
            def no_rename(path, a):
                np.save(f"{path}.partial", a)
            def handle(path, a):
                tmp = f"{path}.partial"
                with open(tmp, "wb") as fh:
                    np.save(fh, a)
                os.replace(tmp, path)
            def opened(path, a):
                fh = open(f"{path}.partial", "wb")
                np.save(fh, a)
                os.replace(fh, path)
            def unknown(path, a, make):
                tmp = make(path)
                np.save(tmp, a)
                os.replace(tmp, path)
        """
        assert _found(tmp_path, body) == []


class TestInPlaceRewrite:
    @pytest.mark.parametrize(
        "write",
        ["cache_file.write_text(data)", "self.baseline_path.write_bytes(data)", 'open(cache_file, "w").write(data)', 'open(p / "cache.json", mode="wb")'],
    )
    def test_a_cache_or_baseline_rewritten_in_place_is_reported(self, tmp_path, write):
        body = f"def store(self, cache_file, p, data):\n    {write}\n"
        assert _found(tmp_path, body) == [(RULE_REWRITE, 2)]

    def test_the_name_is_read_through_an_assignment(self, tmp_path):
        body = 'def store(root, data):\n    dest = root / "vocab_cache" / "x.json"\n    dest.write_text(data)\n'
        assert _found(tmp_path, body) == [(RULE_REWRITE, 3)]

    def test_a_staged_write_reading_opening_other_files_and_the_marker_are_clean(self, tmp_path):
        body = """
            import os
            def staged(cache_file, data):
                tmp = cache_file.with_suffix(".tmp")
                tmp.write_text(data)
                os.replace(tmp, cache_file)
            def reads(cache_file):
                return open(cache_file, "r").read()
            def report(out, data):
                out.write_text(data)
            def marked(cache_file, data):
                cache_file.write_text(data)  # atomic-ok: single-process scratch file
        """
        assert _found(tmp_path, body) == []

    def test_rules_can_be_selected_and_dev_directories_are_out_of_scope(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "s.py").write_text("def f(cache, d):\n    cache.write_text(d)\n", encoding="utf-8")
        body = "def f(cache, d):\n    cache.write_text(d)\n"
        assert _found(tmp_path, body) == [(RULE_REWRITE, 2)]
        assert _found(tmp_path, body, rules=[RULE_NPSAVE]) == []
        assert len(find_atomic_write_staging(tmp_path, exclude_parts=(), use_git=False)[0]) == 2


class TestCorpusAndBaseline:
    def test_bom_unparsable_and_empty(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "m.py").write_bytes(b"\xef\xbb\xbfdef f(cache, d):\n    cache.write_text(d)\n")
        assert [f.line for f in find_atomic_write_staging(tmp_path / "a", use_git=False)[0]] == [2]
        (tmp_path / "a" / "bad.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
            assert_atomic_write_staging(tmp_path / "a", use_git=False)
        (tmp_path / "e").mkdir()
        with pytest.raises(pytest.fail.Exception, match="parsed"):
            assert_atomic_write_staging(tmp_path / "e", use_git=False)

    def test_the_baseline_ratchet(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "m.py").write_text("def f(cache, d):\n    cache.write_text(d)\n", encoding="utf-8")
        bl = tmp_path / "bl.json"
        with pytest.raises(pytest.fail.Exception, match="does not exist"):
            assert_atomic_write_staging(src, baseline_path=bl, use_git=False)
        with pytest.raises(pytest.skip.Exception):
            assert_atomic_write_staging(src, baseline_path=bl, refresh=True, use_git=False)
        assert len(json.loads(bl.read_text(encoding="utf-8"))["entries"]) == 1
        assert_atomic_write_staging(src, baseline_path=bl, use_git=False)
        (src / "n.py").write_text("def g(cache, d):\n    cache.write_bytes(d)\n", encoding="utf-8")
        with pytest.raises(pytest.fail.Exception, match="in-place-rewrite"):
            assert_atomic_write_staging(src, baseline_path=bl, use_git=False)


class TestAListOfDirectories:
    def test_two_directories_report_what_two_calls_report(self, tmp_path):
        dirs = [tmp_path / "a", tmp_path / "b"]
        for d in dirs:
            d.mkdir()
            (d / f"{d.name}.py").write_text("def store(self, cache_file, data):\n    cache_file.write_text(data)\n", encoding="utf-8")
        together, scan = find_atomic_write_staging(dirs, use_git=False)
        apart = [f for d in dirs for f in find_atomic_write_staging(d, use_git=False)[0]]
        assert [(f.path, f.rule, f.line) for f in together] == [("a.py", RULE_REWRITE, 2), ("b.py", RULE_REWRITE, 2)]
        assert [(f.path, f.rule, f.line) for f in apart] == [(f.path, f.rule, f.line) for f in together]
        assert len(scan.files) == 2
