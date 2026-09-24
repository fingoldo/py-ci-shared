"""`llm_call_archive_gate`: a missing scanned dir, a BOM or broken file, newer SDK shapes and module-alias factories."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import CorpusError
from py_ci_shared.llm_call_archive_gate import (
    assert_every_llm_call_is_archived,
    find_direct_sdk_calls,
    find_unwrapped_providers,
    python_files,
)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _assert(root: Path, scanned, **kwargs):
    return assert_every_llm_call_is_archived(root, scanned, provider_classes=["ClaudeProvider"], wrapping_factories=["src/factory.py"], **kwargs)


class TestTheCorpus:
    def test_a_missing_scanned_dir_fails(self, tmp_path):
        _write(tmp_path, "src/a.py", "x = 1\n")
        with pytest.raises(CorpusError):
            python_files(tmp_path, ["srcx"])
        _assert(tmp_path, ["src"])
        with pytest.raises(AssertionError, match="does not exist"):
            _assert(tmp_path, ["src", "srcx"])

    def test_an_empty_scan_fails(self, tmp_path):
        (tmp_path / "src").mkdir()
        with pytest.raises(AssertionError, match="examined nothing"):
            _assert(tmp_path, ["src"])

    def test_a_bom_file_is_read_and_a_broken_one_is_reported(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "bom.py").write_bytes(b"\xef\xbb\xbfclient.messages.create(model='m')\n")
        assert find_direct_sdk_calls(tmp_path, ["src"]) == {"src/bom.py": [1]}
        _write(tmp_path, "src/broken.py", "def f(:\n")
        with pytest.raises(AssertionError, match=r"broken.py"):
            _assert(tmp_path, ["src"], allowed={"src/bom.py": "records the answer itself"})


class TestTheShapes:
    @pytest.mark.parametrize(
        "call",
        [
            "client.responses.create(model='m')",
            "client.messages.stream(model='m')",
            "client.chat.completions.parse(model='m')",
            "m.generate_content_async('x')",
        ],
    )
    def test_newer_sdk_payload_calls_are_direct_calls(self, tmp_path, call):
        _write(tmp_path, "src/a.py", f"{call}\n")
        assert find_direct_sdk_calls(tmp_path, ["src"]) == {"src/a.py": [1]}

    def test_an_unrelated_parse_or_stream_is_not(self, tmp_path):
        _write(tmp_path, "src/a.py", "json.parse(x)\nrequests.stream(u)\nparser.completions_x.parse(y)\n")
        assert find_direct_sdk_calls(tmp_path, ["src"]) == {}

    @pytest.mark.parametrize(
        "source",
        [
            "import pyutilz.llm.factory as f\np = f.get_llm_provider('claude')\n",
            "from pyutilz.llm import factory\np = factory.get_llm_provider('claude')\n",
            "import pyutilz.llm.factory\np = pyutilz.llm.factory.get_llm_provider('claude')\n",
            "from pyutilz.llm.factory import get_llm_provider as g\np = g('claude')\n",
        ],
    )
    def test_the_unwrapping_factory_is_found_through_any_import(self, tmp_path, source):
        _write(tmp_path, "src/a.py", source)
        assert find_unwrapped_providers(tmp_path, ["src"], provider_classes=[], wrapping_factories=[]) == {"src/a.py": [2]}

    def test_a_same_named_factory_from_elsewhere_is_not(self, tmp_path):
        _write(tmp_path, "src/a.py", "import myapp.factory as f\np = f.get_llm_provider('claude')\n")
        assert find_unwrapped_providers(tmp_path, ["src"], provider_classes=[], wrapping_factories=[]) == {}
