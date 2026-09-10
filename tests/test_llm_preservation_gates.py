"""Tests for the three LLM-output preservation gates: call archive, save-failure markers, dataclass case completeness.

Each is written against the shape that motivated it in glossum, on a tiny synthetic repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.dataclass_case_completeness import assert_every_dataclass_has_a_case, find_dataclasses
from py_ci_shared.llm_call_archive_gate import (
    assert_every_llm_call_is_archived,
    find_direct_sdk_calls,
    find_generate_calls_without_archive,
    find_unwrapped_providers,
)
from py_ci_shared.save_failure_markers import assert_markers_are_fatal, find_emitted_markers


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestDirectSdkCalls:
    def test_all_three_sdk_shapes_are_found(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "async def f(c, g):\n    await c.messages.create(model='m')\n    await c.chat.completions.create(model='m')\n    g.generate_content('p')\n")

        assert find_direct_sdk_calls(tmp_path, ["pkg"]) == {"pkg/a.py": [2, 3, 4]}

    def test_an_unrelated_create_is_not_reported(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "def f(db):\n    db.tables.create('t')\n")

        assert find_direct_sdk_calls(tmp_path, ["pkg"]) == {}


class TestUnwrappedProviders:
    def test_a_provider_class_built_directly_is_found(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "from pyutilz.llm.anthropic_provider import AnthropicProvider\np = AnthropicProvider(model='x')\n")

        assert find_unwrapped_providers(tmp_path, ["pkg"], provider_classes={"AnthropicProvider"}, wrapping_factories={"pkg/factory.py"}) == {"pkg/a.py": [2]}

    def test_the_unwrapping_factory_is_found_under_an_alias(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "from pyutilz.llm.factory import get_llm_provider as g\np = g('claude')\n")

        assert find_unwrapped_providers(tmp_path, ["pkg"], provider_classes=set(), wrapping_factories=set()) == {"pkg/a.py": [2]}

    def test_the_wrapping_factory_itself_and_its_callers_are_not_reported(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/factory.py", "from pyutilz.llm.factory import get_llm_provider as _raw\ndef get_llm_provider(n):\n    return wrap(_raw(n))\n")
        _write(tmp_path, "pkg/user.py", "from pkg.factory import get_llm_provider\np = get_llm_provider('claude')\n")

        assert find_unwrapped_providers(tmp_path, ["pkg"], provider_classes=set(), wrapping_factories={"pkg/factory.py"}) == {}


class TestGenerateCallsWithoutArchive:
    def test_a_module_calling_generate_that_never_names_the_archive_is_found(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "async def f(p):\n    return await p.generate_json('x')\n")

        assert find_generate_calls_without_archive(tmp_path, ["pkg"], archive_names={"get_llm_provider"}) == {"pkg/a.py": [2]}

    def test_a_module_that_names_the_archive_is_not(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/a.py", "from pkg.factory import get_llm_provider\nasync def f():\n    return await get_llm_provider('c').generate('x')\n")

        assert find_generate_calls_without_archive(tmp_path, ["pkg"], archive_names={"get_llm_provider"}) == {}


class TestTheArchiveGateAssertion:
    def _repo(self, tmp_path: Path) -> None:
        _write(tmp_path, "pkg/factory.py", "from pyutilz.llm.factory import get_llm_provider as _raw\n")
        _write(tmp_path, "pkg/direct.py", "async def f(c):\n    await c.messages.create(model='m')\n")

    def test_an_unlisted_bypass_fails(self, tmp_path: Path) -> None:
        self._repo(tmp_path)

        with pytest.raises(AssertionError, match="direct model-SDK call"):
            assert_every_llm_call_is_archived(tmp_path, ["pkg"], provider_classes=set(), wrapping_factories={"pkg/factory.py"})

    def test_a_listed_bypass_with_a_reason_passes(self, tmp_path: Path) -> None:
        self._repo(tmp_path)

        assert_every_llm_call_is_archived(
            tmp_path, ["pkg"], provider_classes=set(), wrapping_factories={"pkg/factory.py"}, allowed={"pkg/direct.py": "records every call itself"}
        )

    def test_a_stale_allowed_entry_fails(self, tmp_path: Path) -> None:
        self._repo(tmp_path)

        with pytest.raises(AssertionError, match="no longer match"):
            assert_every_llm_call_is_archived(
                tmp_path,
                ["pkg"],
                provider_classes=set(),
                wrapping_factories={"pkg/factory.py"},
                allowed={"pkg/direct.py": "records itself", "pkg/gone.py": "was removed"},
            )

    def test_an_allowed_entry_without_a_reason_fails(self, tmp_path: Path) -> None:
        self._repo(tmp_path)

        with pytest.raises(AssertionError, match="needs a reason"):
            assert_every_llm_call_is_archived(tmp_path, ["pkg"], provider_classes=set(), wrapping_factories={"pkg/factory.py"}, allowed={"pkg/direct.py": " "})


class TestSaveFailureMarkers:
    @staticmethod
    def _is_fatal(error: str) -> bool:
        return error.startswith(("widgets_save_failed", "gizmos_failed"))

    def test_both_emission_shapes_are_found_and_others_ignored(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "m.py",
            'result.errors.append(f"widgets_save_failed: {e}")\nawait loop(s, marker="gizmos_failed")\nlog.warning("x_failed: %s", e)\nraise RuntimeError("y_failed: z")\n',
        )

        assert set(find_emitted_markers(tmp_path)) == {"widgets_save_failed", "gizmos_failed"}

    def test_an_unrecognised_marker_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.py", 'errors.append(f"sprockets_failed: {e}")\n')

        with pytest.raises(AssertionError, match="sprockets_failed"):
            assert_markers_are_fatal(tmp_path, self._is_fatal)

    def test_a_non_fatal_entry_with_a_reason_passes_and_a_stale_one_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.py", 'errors.append(f"sprockets_failed: {e}")\n')

        assert_markers_are_fatal(tmp_path, self._is_fatal, non_fatal={"sprockets_failed": "regenerable for free"})
        with pytest.raises(AssertionError, match="stale exemption"):
            assert_markers_are_fatal(tmp_path, self._is_fatal, non_fatal={"sprockets_failed": "regenerable", "gone_failed": "was removed"})

    def test_a_non_fatal_entry_the_predicate_calls_fatal_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, "m.py", 'errors.append(f"widgets_save_failed: {e}")\n')

        with pytest.raises(AssertionError, match="treats them as fatal"):
            assert_markers_are_fatal(tmp_path, self._is_fatal, non_fatal={"widgets_save_failed": "not really"})


class TestDataclassCaseCompleteness:
    SOURCE = (
        "import dataclasses\nfrom dataclasses import dataclass\n\n"
        "@dataclass\nclass AVerdict:\n    x: int = 0\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass BVerdict:\n    y: int = 0\n\n"
        "class CVerdict:\n    pass\n\n"
        "@dataclass\nclass Helper:\n    z: int = 0\n"
    )

    def test_every_decorator_form_is_found_and_the_pattern_applies(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "v.py", self.SOURCE)

        assert set(find_dataclasses([path], r".*Verdict")) == {"AVerdict", "BVerdict"}

    def test_a_class_with_no_case_fails(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "v.py", self.SOURCE)

        with pytest.raises(AssertionError, match="BVerdict"):
            assert_every_dataclass_has_a_case([path], {"AVerdict"}, name_pattern=r".*Verdict")

    def test_an_exemption_with_a_reason_passes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "v.py", self.SOURCE)

        assert_every_dataclass_has_a_case([path], {"AVerdict"}, name_pattern=r".*Verdict", exempt={"BVerdict": "a single object, tested elsewhere"})

    def test_a_stale_case_or_exemption_fails(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "v.py", self.SOURCE)

        with pytest.raises(AssertionError, match="match no dataclass"):
            assert_every_dataclass_has_a_case([path], {"AVerdict", "BVerdict", "GoneVerdict"}, name_pattern=r".*Verdict")
