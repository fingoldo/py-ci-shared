"""The release guard reads every page of the releases listing, so an old release is not mistaken for unpublished (audit 46-F22)."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

GUARD_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release_guard.py"


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("release_guard_pages", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("release_guard_pages", module)
    spec.loader.exec_module(module)
    return module


def _paged_api(pages: list[list[str]]):
    """A GitHub-like releases endpoint: page N of *pages* (1-based), empty past the last page; a path without ``page`` is page 1."""

    def api(path: str) -> list[dict[str, str]]:
        match = re.search(r"[?&]page=(\d+)", path)
        page = int(match.group(1)) if match else 1
        return [{"tag_name": tag} for tag in (pages[page - 1] if page <= len(pages) else [])]

    return api


def test_a_release_on_a_later_page_is_listed(monkeypatch) -> None:
    guard = _load_guard()
    first_page = [f"v0.0.{n}" for n in range(100)]
    monkeypatch.setattr(guard, "_github", lambda token: _paged_api([first_page, ["v1.0.0"]]))
    names = guard._release_names(argparse.Namespace(repo="owner/name", repo_dir="."))
    assert "v1.0.0" in names
    assert len(names) == 101


def test_a_listing_past_the_page_cap_is_unreadable_not_partial(monkeypatch) -> None:
    guard = _load_guard()
    full_page = [f"v0.{n}.0" for n in range(100)]
    monkeypatch.setattr(guard, "_github", lambda token: _paged_api([full_page] * (guard._MAX_RELEASE_PAGES + 1)))
    with pytest.raises(OSError):
        guard._release_tag_names(_paged_api([full_page] * (guard._MAX_RELEASE_PAGES + 1)), "owner/name")
