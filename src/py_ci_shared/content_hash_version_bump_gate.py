"""Shared harness for the "N files feed a version/cache-key constant that
must be bumped by hand whenever those files change" meta-test pattern.

A prompt-version constant, an API-schema version, a serialization-format
version, or a cache-key version often gates whether a persisted/cached
artifact is still valid for the CURRENT code -- but the discipline of
"bump the version whenever the source changes" is usually comment-only,
enforced by nothing. A source edit that forgets the bump silently reuses a
stale cached result under the OLD version, computed by code that no longer
matches. This module hashes the tracked source files, pins the hash to the
version constant's CURRENT value in a baseline JSON, and fails only when
the content changed but the version did NOT -- a version bump is always
self-certifying (accepted and re-pinned automatically), since bumping IS
the correct fix.

Mirrors ``code_audit_meta.py``'s/``loc_budget.py``'s exact API shape (lazy
imports, a ``--refresh-*`` CLI flag via ``register_refresh_option``,
``assert_*`` as the test body) so a project already using either pattern
recognizes this one immediately.

Usage (in a consuming repo's own ``tests/test_meta/test_*_version_bump.py``)::

    from pathlib import Path
    from py_ci_shared.content_hash_version_bump_gate import assert_version_bumped_with_content
    from myproject.prompt_version import USER_PROMPT_VERSION

    _SOURCE_FILES = [Path(__file__).resolve().parents[2] / "prompt_builder" / f for f in (
        "word_count.py", "truncation.py", "user_prompt.py",
    )]

    def test_user_prompt_version_bumped_when_prompt_builder_changes():
        assert_version_bumped_with_content(
            files=_SOURCE_FILES,
            version=USER_PROMPT_VERSION,
            baseline_path=Path(__file__).resolve().parent / "_user_prompt_version_baseline.json",
        )

And in the same directory's ``conftest.py`` (or the repo's root conftest.py)::

    from py_ci_shared.content_hash_version_bump_gate import register_refresh_option

    def pytest_addoption(parser):
        register_refresh_option(parser)

Deliberately dependency-light: ``pytest`` and ``orjson`` are imported
LAZILY inside the functions below, matching ``code_audit_meta.py``'s own
convention, so importing ``py_ci_shared`` itself never requires them.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

REFRESH_FLAG = "--refresh-content-hash-version-baseline"


def register_refresh_option(parser) -> None:
    """Register ``--refresh-content-hash-version-baseline`` as a no-op boolean flag.

    Same rationale as ``code_audit_meta.register_refresh_option``: pytest
    rejects unrecognized CLI options before test code runs, so every
    consuming repo's conftest.py must call this from its own
    ``pytest_addoption``.
    """
    try:
        parser.addoption(
            REFRESH_FLAG,
            action="store_true",
            default=False,
            help="rewrite a content-hash/version-bump baseline JSON instead of comparing (bootstrapping only)",
        )
    except ValueError:
        pass  # already registered (e.g. a repo with more than one conftest.py in the chain)


def _refresh_requested() -> bool:
    import sys

    return REFRESH_FLAG in sys.argv


def content_hash(files: Iterable[Path]) -> str:
    """Combined sha256 of every file in ``files``, in the given (caller-declared,
    stable) order, normalizing CRLF -> LF first so a line-ending-only checkout
    difference never manufactures a spurious "content changed" verdict."""
    import hashlib

    h = hashlib.sha256()
    for path in files:
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def assert_version_bumped_with_content(
    files: Iterable[Path],
    version: str,
    baseline_path: Path,
) -> None:
    """Fail if the combined content of ``files`` changed since the baseline
    was captured, but ``version`` did NOT also change -- the classic
    "forgot to bump the version" bug. Seeds/refreshes ``baseline_path``
    (first run, or the ``--refresh-content-hash-version-baseline`` flag)
    and ``pytest.skip()``s that run instead of comparing.

    A version bump is self-certifying: if ``version`` differs from the
    baseline's pinned value, that's accepted as a DELIBERATE bump (bumping
    the version is the whole point of this check existing) and the
    baseline is silently re-pinned to the new (version, content_hash) pair
    -- no separate flag needed for the normal "I bumped it, tests should
    pass" workflow. Call this directly as the body of a ``test_*``
    function.

    Args:
        files: every source file whose content should trigger a version
            bump if changed -- the caller decides what belongs in this set
            (e.g. every submodule that feeds a specific prompt/schema).
        version: the CURRENT value of the version constant this content is
            pinned to.
        baseline_path: where the baseline JSON lives (and gets written on
            refresh) -- conventionally a sibling ``_<name>_version_baseline.json``
            next to the test file.
    """
    import orjson
    import pytest

    current_hash = content_hash(files)

    if _refresh_requested() or not baseline_path.exists():
        baseline_path.write_text(
            orjson.dumps({"version": version, "content_hash": current_hash}, option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )
        pytest.skip(f"content-hash/version-bump baseline refreshed at {baseline_path.name} (version={version!r})")

    baseline: dict[str, str] = orjson.loads(baseline_path.read_bytes())
    baseline_version = baseline.get("version")
    baseline_hash = baseline.get("content_hash")

    if version != baseline_version:
        # Deliberate bump -- self-certifying. Accept and re-pin the
        # baseline so the NEXT run compares against this new state.
        baseline_path.write_text(
            orjson.dumps({"version": version, "content_hash": current_hash}, option=orjson.OPT_INDENT_2).decode("utf-8"),
            encoding="utf-8",
        )
        return

    if current_hash != baseline_hash:
        pytest.fail(
            f"Tracked source file(s) changed (content hash {baseline_hash} -> {current_hash}) but "
            f"{version!r} was NOT bumped. Any structural change to the tracked files must bump the "
            f"version constant so a cache/schema/prompt key keyed on it correctly invalidates stale "
            f"entries computed under the OLD content. If this change genuinely doesn't affect the "
            f"tracked behavior (e.g. a comment/docstring/formatting-only edit), refresh with: "
            f"pytest ... {REFRESH_FLAG}"
        )
