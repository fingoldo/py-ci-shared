"""Scanner tests for credential_logging, split verbatim out of the former tests/test_code_audit.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.code_audit import (
    scan_credential_shaped_log_args,
)

from ._helpers import _write

# ---- credential_shaped_log_arg ---------------------------------------------


def test_credential_shaped_log_arg_unredacted_flagged(tmp_path: Path):
    _write(
        tmp_path,
        "bad.py",
        """
import logging
logger = logging.getLogger(__name__)

def f(proxy):
    logger.info(proxy)
""",
    )
    findings = scan_credential_shaped_log_args(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "P2"


def test_credential_shaped_log_arg_redacted_is_clean(tmp_path: Path):
    _write(
        tmp_path,
        "ok.py",
        """
import logging
logger = logging.getLogger(__name__)

def f(proxy):
    redacted = proxy.split("@")[1]
    logger.info(redacted)
""",
    )
    findings = scan_credential_shaped_log_args(tmp_path)
    assert findings == []


# ---- 40-T4: f-string, .format, dict-key and extra= forms; wider vocabulary ----


def _findings_for(tmp_path: Path, body: str) -> list:
    _write(tmp_path, "m.py", body)
    return scan_credential_shaped_log_args(tmp_path)


_HEAD = "import logging\nlog = logging.getLogger(__name__)\n"


def test_40_t4_fstring_argument_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(token):\n    log.info(f"token={token}")\n')) == 1


def test_40_t4_str_format_argument_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(token):\n    log.info("t={}".format(token))\n')) == 1


def test_40_t4_percent_format_argument_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(token):\n    log.info("t=%s" % token)\n')) == 1


def test_40_t4_subscript_key_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(d):\n    log.info("t=%s", d["password"])\n')) == 1


def test_40_t4_get_key_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(d):\n    log.info("t=%s", d.get("api_key"))\n')) == 1


def test_40_t4_extra_dict_key_is_flagged(tmp_path: Path):
    assert len(_findings_for(tmp_path, _HEAD + 'def f(token):\n    log.info("x", extra={"token": token})\n')) == 1


def test_40_t4_widened_vocabulary_is_flagged(tmp_path: Path):
    for name in ("pwd", "jwt", "private_key", "authorization", "tokens", "authToken", "apiKey"):
        assert len(_findings_for(tmp_path, _HEAD + f'def f({name}):\n    log.info("v=%s", {name})\n')) == 1, name


def test_40_t4_comment_in_window_no_longer_exempts(tmp_path: Path):
    body = _HEAD + 'def f(token):\n    # note: we do not mask this one, yet\n    log.info("t=%s", token)\n'
    assert len(_findings_for(tmp_path, body)) == 1


def test_40_t4_redaction_in_the_call_itself_exempts(tmp_path: Path):
    assert _findings_for(tmp_path, _HEAD + 'def f(token):\n    log.info(f"t={redact(token)}")\n') == []


def test_40_t4_plain_message_with_credential_word_is_not_flagged(tmp_path: Path):
    assert _findings_for(tmp_path, _HEAD + 'def f(n):\n    log.info("password reset sent to %s", n)\n') == []


def _log_of(tmp_path: Path, name: str):
    _write(
        tmp_path,
        "mod.py",
        f"""
import logging
logger = logging.getLogger(__name__)

def f({name}):
    logger.info("value %s", {name})
""",
    )
    return scan_credential_shaped_log_args(tmp_path)


@pytest.mark.parametrize(
    "name",
    ["max_tokens", "input_tokens", "total_prompt_tokens", "total_completion_tokens", "reasoning_tokens", "end_token", "num_tokens", "token_count", "maxTokens"],
)
def test_a_token_count_or_delimiter_name_is_not_a_credential(tmp_path: Path, name: str):
    assert _log_of(tmp_path, name) == []


@pytest.mark.parametrize("name", ["token", "auth_token", "api_token", "access_token", "refresh_token", "bearer_token", "tokens"])
def test_a_bare_token_name_is_still_a_credential(tmp_path: Path, name: str):
    assert len(_log_of(tmp_path, name)) == 1
