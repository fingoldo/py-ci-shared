"""Scanner tests for network_timeout, split verbatim out of the former tests/test_code_audit.py."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit import (
    scan_missing_network_timeout,
)

from ._helpers import _write

# ---- missing_network_timeout -------------------------------------------


def test_missing_network_timeout_flags_bare_get(tmp_path: Path):
    _write(
        tmp_path,
        "bad.py",
        """
import requests

def f():
    return requests.get("http://example.com")
""",
    )
    findings = scan_missing_network_timeout(tmp_path)
    assert len(findings) == 1
    assert findings[0].check == "missing_network_timeout"


def test_missing_network_timeout_with_timeout_kwarg_is_clean(tmp_path: Path):
    _write(
        tmp_path,
        "ok.py",
        """
import requests

def f():
    return requests.get("http://example.com", timeout=5)
""",
    )
    findings = scan_missing_network_timeout(tmp_path)
    assert findings == []


# ---- F194: a directly imported urlopen ----------------------------------------------


def test_network_timeout_sees_a_directly_imported_urlopen(tmp_path: Path):
    _write(
        tmp_path,
        "a.py",
        """
from urllib.request import urlopen

def f(u):
    return urlopen(u).read()
""",
    )
    assert len(scan_missing_network_timeout(tmp_path)) == 1


def test_network_timeout_ignores_a_local_function_named_get(tmp_path: Path):
    _write(
        tmp_path,
        "a.py",
        """
def get(u):
    return u

def f(u):
    return get(u)
""",
    )
    assert scan_missing_network_timeout(tmp_path) == []


# ---- 40-T5: sessions, import aliases, timeout=None, per-function preconfigured set ----


def _flagged(tmp_path: Path, body: str) -> list:
    _write(tmp_path, "m.py", body)
    return scan_missing_network_timeout(tmp_path)


def test_40_t5_session_built_without_timeout_is_flagged(tmp_path: Path):
    assert len(_flagged(tmp_path, "import requests\ndef f(url):\n    s = requests.Session()\n    return s.get(url)\n")) == 1


def test_40_t5_self_session_built_in_init_is_flagged(tmp_path: Path):
    body = (
        "import requests\nclass C:\n    def __init__(self):\n        self.session = requests.Session()\n"
        "    def go(self, url):\n        return self.session.get(url)\n"
    )
    assert len(_flagged(tmp_path, body)) == 1


def test_40_t5_import_alias_is_resolved(tmp_path: Path):
    assert len(_flagged(tmp_path, "import requests as rq\ndef f(url):\n    return rq.get(url)\n")) == 1


def test_40_t5_direct_session_import_call_is_flagged(tmp_path: Path):
    assert len(_flagged(tmp_path, "from requests import Session\ndef f(url):\n    return Session().get(url)\n")) == 1


def test_40_t5_timeout_none_is_missing(tmp_path: Path):
    assert len(_flagged(tmp_path, "import requests\ndef f(url):\n    return requests.get(url, timeout=None)\n")) == 1


def test_40_t5_async_client_context_is_flagged(tmp_path: Path):
    body = "import httpx\nasync def f(url):\n    async with httpx.AsyncClient() as c:\n" "        return await c.get(url)\n"
    assert len(_flagged(tmp_path, body)) == 1


def test_40_t5_preconfigured_client_is_per_function(tmp_path: Path):
    body = (
        "import httpx\nimport requests\n"
        "def a(url):\n    c = httpx.Client(timeout=5)\n    return c.get(url)\n"
        "def b(url):\n    c = requests.Session()\n    return c.get(url)\n"
    )
    findings = _flagged(tmp_path, body)
    assert len(findings) == 1 and findings[0].line == 8, findings


def test_40_t5_preconfigured_session_is_not_flagged(tmp_path: Path):
    body = "import requests\ndef f(url):\n    s = requests.Session(timeout=5)\n    return s.get(url)\n"
    assert _flagged(tmp_path, body) == []
