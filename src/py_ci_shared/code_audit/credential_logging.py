"""(internal) part of pyutilz.dev.code_audit; see package __init__ for docs."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

from .._core.unparse import unparse_stable
from ._base import walk_tree, Finding, _DEFAULT_EXCLUDE_DIRS, _iter_py_files, _line_text, _read_src_lines, _safe_parse, walk_nodes, is_line_exempt

__all__ = ["scan_credential_shaped_log_args"]

# --- credential-shaped value reaching a logger call ------------------------
#
# Class of bug found repeatedly in the 2026-07-21 security audit: web.py's report_params()
# logged full proxies/headers at INFO with NO redaction gate, even though the very same file's
# handle_blocking() (a few lines away) explicitly does `.split("@")[1]` before logging a proxy
# URL -- proof the author knew the risk in one place but not the other (Critical). logginglib.py's
# `special_vars` were excluded from one log dict key but copied unredacted into another
# ("relocation, not redaction" -- Medium).

# Segment boundaries, not `\b`: `_` is a word character, so `\bpassword\b` never matched inside
# `db_password` -- and snake_case compounds (`db_password`, `auth_token`, `user_api_key`,
# `proxy_url`) are how credentials are actually named. `bypass` still does not match, because
# the character before `pass` there is alphanumeric.
_CREDENTIAL_NAME_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])("
    r"proxy|proxies|password|passwords|passwd|pass|pwd|auth|authorization|token|tokens|jwt|"
    r"credential|credentials|secret|secrets|api_key|apikey|api_keys|private_key|cookie|cookies"
    r")(?:[^A-Za-z0-9]|$)"
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_REDACT_HINT_RE = re.compile(r"(?i)redact|mask|sanitiz|\.split\(.@.\)|scrub")
# How many lines above a log call are searched for an assignment of a redacted copy (code only).
_REDACT_ASSIGN_WINDOW = 4


def _is_credential_name(name: str) -> bool:
    """Vocabulary match on a name, after splitting camelCase (``apiKey`` -> ``api_Key``)."""
    return bool(_CREDENTIAL_NAME_RE.search(_CAMEL_BOUNDARY.sub("_", name)))


def _credential_names_in(arg: ast.AST) -> set[str]:
    """Credential-shaped names reachable from one logged argument expression.

    Covers a bare Name or attribute, a name inside an f-string or a ``.format``/``%`` operand (any
    Name/Attribute in the expression tree), a string key read by subscript (``d["password"]``) or
    ``d.get("api_key")``, and a string key of a dict literal (``extra={"token": t}``). A plain
    string literal that is only a message is never inspected: ``"password reset sent"`` is prose.
    """
    out: set[str] = set()
    for node in walk_nodes(arg):
        keys: Sequence[ast.AST | None] = ()  # the key expressions this node contributes (string keys only)
        if isinstance(node, ast.Name) and _is_credential_name(node.id):
            out.add(node.id)
        elif isinstance(node, ast.Attribute) and _is_credential_name(node.attr):
            out.add(node.attr)
        elif isinstance(node, ast.Subscript):
            keys = [node.slice]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            keys = node.args[:1]
        elif isinstance(node, ast.Dict):
            keys = list(node.keys)
        for key in keys:
            text = _str_constant(key)
            if text is not None and _is_credential_name(text):
                out.add(text)
    return out


def _str_constant(node: ast.AST | None) -> str | None:
    """The value of a string-literal node, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _redacting_assign_lines(tree: ast.AST) -> set[int]:
    """Line numbers of assignments whose value is a redaction call or split (code, never comments)."""
    return {node.lineno for node in walk_tree(tree) if isinstance(node, ast.Assign) and _REDACT_HINT_RE.search(unparse_stable(node.value))}


def _has_redaction_hint(call: ast.Call, redacting_lines: set[int]) -> bool:
    """Redaction evidence for a log call: the call's own arguments, or a redacting assignment in the few lines
    above it. Comments and string prose are not code, so they never count."""
    own = [unparse_stable(a) for a in call.args] + [unparse_stable(k.value) for k in call.keywords]
    if any(_REDACT_HINT_RE.search(text) for text in own):
        return True
    return any(call.lineno - _REDACT_ASSIGN_WINDOW <= line < call.lineno for line in redacting_lines)


_LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})


def _is_logger_receiver(receiver: ast.expr) -> bool:
    """Whether ``receiver`` in ``<receiver>.info(...)`` names a logger.

    Same rule as ``log_throttle._is_log_call``: any name ENDING in `log`/`logger`, and attribute
    receivers too. Requiring the literal names `logger`/`log`/`logging` skipped `self.logger`,
    `self._log` and `LOGGER` -- and this scanner is the security-adjacent one of the pair.
    """
    if isinstance(receiver, ast.Name):
        name = receiver.id
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr
    else:
        return False
    low = name.lower()
    return low.endswith("log") or low.endswith("logger") or low == "logging"


def scan_credential_shaped_log_args(
    root: Path,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDE_DIRS,
) -> list[Finding]:
    """Find ``logger.<level>(...)`` calls whose arguments include a variable/attribute whose
    NAME matches a credential-shaped vocabulary (proxy, password, token, auth, secret, api_key,
    cookie, credential), with no redaction hint (``.split("@")``, a name/call containing
    redact/mask/sanitize/scrub) anywhere in the same source line.

    The credential vocabulary is matched on SEGMENT boundaries, so snake_case compounds
    (``db_password``, ``auth_token``, ``user_api_key``) match as well as bare names, and the logger
    receiver is recognised by suffix (``self.logger``, ``self._log``, ``LOGGER``), not by an exact
    name.

    Deliberately name-based (not value/type-based -- statically impossible in general) and
    therefore prone to false positives on legitimately-safe values whose name happens to match
    (e.g. ``token_type``) -- use the baseline mechanism liberally.

    Severity: P2 (a real, if noisy, security-adjacent signal -- credential-shaped values in log
    output are exactly the shape of two confirmed findings in the 2026-07-21 audit).
    """
    findings: list[Finding] = []
    for py in _iter_py_files(root, exclude_dirs):
        tree = _safe_parse(py)
        if tree is None:
            continue
        src_lines = _read_src_lines(py)
        rel = py.relative_to(root).as_posix()
        redacting_lines = _redacting_assign_lines(tree)
        for node in walk_tree(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS):
                continue
            if not _is_logger_receiver(node.func.value):
                continue
            credential_names: set[str] = set()
            for a in list(node.args) + [kw.value for kw in node.keywords]:
                credential_names |= _credential_names_in(a)
            if not credential_names:
                continue
            line_text = _line_text(src_lines, node.lineno)
            if _has_redaction_hint(node, redacting_lines) or is_line_exempt(src_lines, node.lineno, "credential_shaped_log_arg"):
                continue
            names = ", ".join(sorted(credential_names))
            findings.append(
                Finding(
                    check="credential_shaped_log_arg",
                    severity="P2",
                    file=rel,
                    line=node.lineno,
                    snippet=line_text,
                    detail=(
                        f"logger call passes credential-shaped value(s) ({names}) with no redaction "
                        "hint nearby (.split('@'), redact/mask/sanitize) -- confirm this doesn't leak "
                        "a plaintext credential into logs."
                    ),
                )
            )
    return findings
