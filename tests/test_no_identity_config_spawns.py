"""No test spawns `git config user.*`: the commit identity is the session's (tests/conftest.py GIT_ISOLATION) (audit 48-F15).

The one allowed occurrence sets a non-ASCII author name that the test asserts on, so it is data, not setup.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
IDENTITY_SPAWN = re.compile(r'"config", "user\.(email|name)"')
ALLOWED = {"test_stale_comment_age.py"}  # the author-name data of test_many_candidates_are_batched_and_all_dated


def identity_spawns(text: str) -> int:
    return len(IDENTITY_SPAWN.findall(text))


def test_no_test_file_spawns_identity_config_outside_the_one_allowed_case() -> None:
    offenders = {}
    for path in sorted(TESTS.glob("*.py")):
        if path.name in ALLOWED or path.name == Path(__file__).name:
            continue
        count = identity_spawns(path.read_text(encoding="utf-8"))
        if count:
            offenders[path.name] = count
    assert offenders == {}, offenders


def test_the_counter_sees_a_spawn_and_ignores_a_functional_config() -> None:
    """Negative control (48-F15): before the change this exact spawn was in the fixtures; functional config is not identity."""
    assert identity_spawns('subprocess.run(["git", "config", "user.email", "t@example.com"])') == 1
    assert identity_spawns('_git(repo, "config", "core.autocrlf", "false")') == 0
