"""Unit tests for the ``silent_default_return`` rule of fail_open_handlers: a handler that only returns a default and logs nothing."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.fail_open_handlers import find_fail_open_handlers


def _rules(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "mod.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return sorted(h.rule for h in find_fail_open_handlers([p], tmp_path))


@pytest.mark.parametrize("handler", ["return None", "return default", "return failed()", "return []", "return"])
def test_silent_default_return_is_found(tmp_path, handler):
    body = f"""
        def load(text, default):
            try:
                return int(text)
            except ValueError:
                {handler}
    """
    assert "silent_default_return" in _rules(tmp_path, body)


@pytest.mark.parametrize(
    "handler_body",
    [
        "log.warning('bad %r', text)\n        return None",
        "log.debug('bad %r', text)\n        return None",
        "raise ValueError(text)",
    ],
)
def test_handler_that_logs_or_raises_is_not_silent(tmp_path, handler_body):
    body = f"""
        import logging
        log = logging.getLogger(__name__)

        def load(text):
            try:
                return int(text)
            except ValueError:
                {handler_body}
    """
    assert "silent_default_return" not in _rules(tmp_path, body)


def test_best_effort_marker_on_the_except_line_honours_the_fallback(tmp_path):
    body = """
        def load(text):
            try:
                return int(text)
            except ValueError:  # best-effort: the caller re-reads the value on the next pass
                return None
    """
    assert "silent_default_return" not in _rules(tmp_path, body)


def test_handler_already_reported_by_another_rule_is_not_reported_twice(tmp_path):
    body = """
        def spec_is_ok(spec):
            try:
                return evaluate(spec)
            except Exception:
                return True
    """
    assert _rules(tmp_path, body) == ["gate_returns_true"]


def test_handler_with_an_assignment_before_return_is_not_this_rule(tmp_path):
    body = """
        def load(text):
            try:
                return int(text)
            except ValueError:
                value = 0
                return value
    """
    assert "silent_default_return" not in _rules(tmp_path, body)
