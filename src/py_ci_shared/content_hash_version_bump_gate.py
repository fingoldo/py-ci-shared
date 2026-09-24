"""Shared harness for the "N files feed a version/cache-key constant that
must be bumped by hand whenever those files change" meta-test pattern.

A prompt-version constant, an API-schema version, a serialization-format
version, or a cache-key version often gates whether a persisted/cached
artifact is still valid for the CURRENT code -- but the discipline of
"bump the version whenever the source changes" is usually comment-only,
enforced by nothing. A source edit that forgets the bump silently reuses a
stale cached result under the OLD version, computed by code that no longer
matches. This module hashes the tracked source files, pins the hash to the
version constant's CURRENT value in a baseline JSON, and fails when the
content changed but the version did NOT. A version bump is accepted and the
baseline re-pinned automatically on a developer machine (bumping IS the
correct fix) -- but never in CI, where a bump the committed baseline does not
record fails until the re-pinned baseline is committed, and never to a version
that was already pinned to different content (reverting ``B`` back to ``A``
while keeping ``B``'s content would otherwise reuse ``A``'s cache entries). A
missing baseline fails; it is written only on an explicit refresh.

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

Deliberately dependency-light: ``pytest`` is imported
LAZILY inside the functions below, matching ``code_audit_meta.py``'s own
convention, so importing ``py_ci_shared`` itself never requires them.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ._core import atomic_write_text, dump_json, load_json, refresh_requested, register_refresh_options

REFRESH_FLAG = "--refresh-content-hash-version-baseline"
#: Version of the hash layout written into new baselines. A baseline without it holds a legacy hash.
HASH_FORMAT = 2


def register_refresh_option(parser) -> None:
    """Register ``--refresh-content-hash-version-baseline`` (and the shared ``--py-ci-refresh``) as flags.

    Same rationale as ``code_audit_meta.register_refresh_option``: pytest
    rejects unrecognized CLI options before test code runs, so every
    consuming repo's conftest.py must call this from its own
    ``pytest_addoption``. A thin wrapper over ``_core.register_refresh_options``.
    """
    register_refresh_options(parser, [REFRESH_FLAG], help_suffix="content-hash/version-bump baseline JSON (bootstrapping only)")


def _refresh_requested(request: Any = None) -> bool:
    """xdist-safe refresh detection (option via *request*, ``--py-ci-refresh``, ``PY_CI_SHARED_REFRESH``, argv)."""
    return refresh_requested(REFRESH_FLAG, request)


def _in_ci() -> bool:
    value = os.environ.get("CI", "")
    return value.strip().lower() not in ("", "0", "false", "no")


def _labels(paths: "list[Path]") -> list[str]:
    """Machine-independent names for *paths*: relative to their common directory (the name alone for one file)."""
    if len(paths) == 1:
        return [paths[0].name]
    try:
        common = Path(os.path.commonpath([str(p.resolve()) for p in paths]))
        return [p.resolve().relative_to(common).as_posix() for p in paths]
    except ValueError:  # different drives on Windows
        return [p.name for p in paths]


def content_hash(files: Iterable[Path]) -> str:
    """Combined sha256 of every file in ``files``, in the given (caller-declared,
    stable) order, normalizing CRLF -> LF first so a line-ending-only checkout
    difference never manufactures a spurious "content changed" verdict.

    Each file contributes its machine-independent label and its length before its bytes, so moving bytes from
    one file to the next (``"ab"+"c"`` vs ``"a"+"bc"``) or renaming a file changes the hash. An empty file list
    raises ``ValueError``: it would hash to a constant and pin nothing.
    """
    import hashlib

    paths = [Path(p) for p in files]
    if not paths:
        raise ValueError("content_hash() needs at least one file; an empty list hashes to a constant and tracks nothing")
    h = hashlib.sha256()
    for label, path in zip(_labels(paths), paths):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        h.update(f"{label}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()[:16]


def _legacy_content_hash(paths: "list[Path]") -> str:
    """The pre-``HASH_FORMAT`` hash (plain concatenation), read only to honour a baseline written with it."""
    import hashlib

    h = hashlib.sha256()
    for path in paths:
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def _write(baseline_path: Path, version: str, current_hash: str, history: "dict[str, str]") -> None:
    payload = {"version": version, "content_hash": current_hash, "hash_format": HASH_FORMAT, "history": {**history, version: current_hash}}
    atomic_write_text(baseline_path, dump_json(payload))


def assert_version_bumped_with_content(
    files: Iterable[Path],
    version: str,
    baseline_path: Path,
    *,
    request: Any = None,
    write_on_bump: Optional[bool] = None,
) -> None:
    """Fail if the combined content of ``files`` changed since the baseline
    was captured, but ``version`` did NOT also change -- the classic
    "forgot to bump the version" bug. Rewrites ``baseline_path`` and
    ``pytest.skip()``s when a refresh is requested (the
    ``--refresh-content-hash-version-baseline`` flag, ``--py-ci-refresh`` or
    ``PY_CI_SHARED_REFRESH=content-hash-version``); fails when it is missing.

    A version bump re-pins the baseline to the new (version, content_hash)
    pair when *write_on_bump* (default: not running in CI, i.e. env ``CI``
    unset) -- the normal "I bumped it" workflow on a developer machine, after
    which the re-pinned baseline is committed. In CI the bump fails until
    that committed baseline records it. A version already pinned to
    DIFFERENT content in the baseline's history is never accepted. Call this
    directly as the body of a ``test_*`` function.

    Args:
        files: every source file whose content should trigger a version
            bump if changed -- the caller decides what belongs in this set
            (e.g. every submodule that feeds a specific prompt/schema).
        version: the CURRENT value of the version constant this content is
            pinned to.
        baseline_path: where the baseline JSON lives (and gets written on
            refresh) -- conventionally a sibling ``_<name>_version_baseline.json``
            next to the test file.
        request: the pytest ``request`` fixture, for xdist-safe refresh detection.
        write_on_bump: whether an accepted bump rewrites the baseline; ``None`` means "unless in CI".
    """
    import pytest

    paths = [Path(p) for p in files]
    if not paths:
        pytest.fail("assert_version_bumped_with_content() was given no files -- it would pin a constant hash and track nothing")
    current_hash = content_hash(paths)

    history: dict[str, str] = {}
    if baseline_path.exists():
        loaded = load_json(baseline_path)
        if isinstance(loaded.get("history"), dict) and loaded.get("hash_format") == HASH_FORMAT:
            history = {str(k): str(v) for k, v in loaded["history"].items()}

    if _refresh_requested(request):
        _write(baseline_path, version, current_hash, history)
        pytest.skip(f"content-hash/version-bump baseline refreshed at {baseline_path.name} (version={version!r})")

    if not baseline_path.exists():
        pytest.fail(
            f"content-hash/version-bump baseline {baseline_path} does not exist, so nothing pins {version!r} to its "
            f"content. Create it deliberately with {REFRESH_FLAG} (or PY_CI_SHARED_REFRESH=content-hash-version) and commit it."
        )

    baseline: dict[str, Any] = load_json(baseline_path)
    baseline_version = baseline.get("version")
    baseline_hash = baseline.get("content_hash")
    legacy = baseline.get("hash_format") != HASH_FORMAT
    comparable_hash = _legacy_content_hash(paths) if legacy else current_hash

    if version != baseline_version:
        if version in history and history[version] != current_hash:
            pytest.fail(
                f"{version!r} was already pinned to different content (hash {history[version]}; now {current_hash}). "
                f"Going back to an old version string with new content reuses every cache entry keyed on it. "
                f"Bump to a version that has never been used."
            )
        should_write = (not _in_ci()) if write_on_bump is None else write_on_bump
        if not should_write:
            pytest.fail(
                f"{version!r} differs from the committed baseline's {baseline_version!r}: the bump was never re-pinned. "
                f"Run this test locally (outside CI) so it re-pins {baseline_path.name}, and commit that file."
            )
        # Deliberate bump -- accepted and re-pinned so the NEXT run compares against this new state.
        _write(baseline_path, version, current_hash, history)
        return

    if comparable_hash != baseline_hash:
        pytest.fail(
            f"Tracked source file(s) changed (content hash {baseline_hash} -> {comparable_hash}) but "
            f"{version!r} was NOT bumped. Any structural change to the tracked files must bump the "
            f"version constant so a cache/schema/prompt key keyed on it correctly invalidates stale "
            f"entries computed under the OLD content. If this change genuinely doesn't affect the "
            f"tracked behavior (e.g. a comment/docstring/formatting-only edit), refresh with: "
            f"pytest ... {REFRESH_FLAG}"
        )
