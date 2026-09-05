"""Unit tests for the resource-release check. Real files on disk, no mocking, matching this
package's convention.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.resource_release_paths import (
    assert_released_on_every_path,
    find_unprotected_releases,
    subjects,
)


def _module(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_HAPPY_PATH_ONLY = """
    async def main():
        eng = create_async_engine(URL)
        rows = await eng.fetch("select 1")
        await eng.dispose()
"""

_IN_FINALLY = """
    async def main():
        eng = create_async_engine(URL)
        try:
            rows = await eng.fetch("select 1")
        finally:
            await eng.dispose()
"""

_IN_WITH = """
    async def main():
        async with disposing(create_async_engine(URL)) as eng:
            await eng.dispose()
"""

_NEVER_RELEASED = """
    def build():
        return create_async_engine(URL)
"""


class TestFindUnprotectedReleases:
    def test_a_release_on_the_success_path_only_is_reported(self, tmp_path):
        path = _module(tmp_path, "bad.py", _HAPPY_PATH_ONLY)

        assert find_unprotected_releases(path, "dispose") == [5]

    def test_a_release_in_a_finally_is_accepted(self, tmp_path):
        path = _module(tmp_path, "good.py", _IN_FINALLY)

        assert find_unprotected_releases(path, "dispose") == []

    def test_a_release_inside_a_with_is_accepted(self, tmp_path):
        path = _module(tmp_path, "ctx.py", _IN_WITH)

        assert find_unprotected_releases(path, "dispose") == []

    def test_a_module_that_never_releases_is_not_reported(self, tmp_path):
        """Deliberate: some hand the object to a caller that owns its lifecycle. What this
        catches is the shape that says "I own this" and then honours it only on success."""
        path = _module(tmp_path, "handoff.py", _NEVER_RELEASED)

        assert find_unprotected_releases(path, "dispose") == []

    def test_an_unparsable_file_is_skipped_rather_than_crashing(self, tmp_path):
        path = _module(tmp_path, "broken.py", "def (:\n")

        assert find_unprotected_releases(path, "dispose") == []


class TestSubjects:
    def test_only_modules_constructing_the_resource_are_subjects(self, tmp_path):
        bad = _module(tmp_path, "bad.py", _HAPPY_PATH_ONLY)
        unrelated = _module(tmp_path, "other.py", "def f():\n    return 1\n")

        assert subjects([bad, unrelated], ["create_async_engine"]) == [bad]


class TestAssertReleasedOnEveryPath:
    def test_it_fails_on_an_unprotected_release(self, tmp_path):
        path = _module(tmp_path, "bad.py", _HAPPY_PATH_ONLY)

        with pytest.raises(pytest.fail.Exception, match="success path"):
            assert_released_on_every_path(
                files=[path], constructors=["create_async_engine"], release="dispose", repo_root=tmp_path,
            )

    def test_it_passes_when_every_release_is_protected(self, tmp_path):
        path = _module(tmp_path, "good.py", _IN_FINALLY)

        assert_released_on_every_path(
            files=[path], constructors=["create_async_engine"], release="dispose", repo_root=tmp_path,
        )

    def test_it_fails_when_the_scan_has_no_subject(self, tmp_path):
        """The gate must not become the thing it checks for: an empty selection is a
        failure, not a pass."""
        path = _module(tmp_path, "other.py", "def f():\n    return 1\n")

        with pytest.raises(pytest.fail.Exception, match="lost its subject"):
            assert_released_on_every_path(
                files=[path], constructors=["create_async_engine"], release="dispose",
                repo_root=tmp_path, min_subjects=1,
            )

    def test_an_ignored_file_is_not_reported(self, tmp_path):
        bad = _module(tmp_path, "bad.py", _HAPPY_PATH_ONLY)
        good = _module(tmp_path, "good.py", _IN_FINALLY)

        assert_released_on_every_path(
            files=[bad, good], constructors=["create_async_engine"], release="dispose",
            repo_root=tmp_path, ignore=[bad],
        )
