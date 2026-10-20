"""A consumer's name becomes a directory and its repo becomes part of a URL and an API path, so both are validated at load time."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from py_ci_shared._consumers import Consumer, checkout, load_consumers
from py_ci_shared._core import CoreError


def _config(tmp_path: Path, name: str, repo: str) -> Path:
    path = tmp_path / "consumers.toml"
    path.write_text(f'[[consumer]]\nname = "{name}"\nrepo = "{repo}"\nbranch = "main"\n', encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["../escape", "a/b", "a" + chr(92) + "b", "..", ".hidden", "", "a b"])
def test_a_name_that_could_leave_the_checkout_root_is_rejected(tmp_path, name):
    with pytest.raises(CoreError):
        load_consumers(_config(tmp_path, name.replace(chr(92), chr(92) * 2), "o/r"))


@pytest.mark.parametrize("repo", ["o/r?x", "o/r#frag", "o/r name", "o/..", "../r", "o/r%2fx"])
def test_a_repo_that_could_change_the_url_path_or_query_is_rejected(tmp_path, repo):
    with pytest.raises(CoreError):
        load_consumers(_config(tmp_path, "ok", repo))


def test_an_ordinary_consumer_still_loads(tmp_path):
    (c,) = load_consumers(_config(tmp_path, "dash_app_core", "fingoldo/dash.app-core"))

    assert c.name == "dash_app_core" and c.url == "https://github.com/fingoldo/dash.app-core.git"


def test_a_hand_built_consumer_with_an_escaping_name_is_not_cloned(tmp_path):
    cloned: list[Path] = []

    def clone(consumer: Consumer, dest: Path, token: Optional[str]) -> Optional[str]:
        cloned.append(dest)
        return None

    evil = Consumer(name="../escape", repo="o/r", branch="main", private=False)
    result = checkout([evil], tmp_path / "dest", token=None, clone=clone)

    assert cloned == [] and len(result.failed) == 1 and "outside" in result.failed[0][1]
