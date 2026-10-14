"""An nginx block that sets its own ``add_header`` repeats every header its enclosing blocks set.

nginx inherits ``add_header`` from the enclosing level ONLY when the current level declares none. A ``server`` block that
sets ``Content-Security-Policy``, ``X-Content-Type-Options`` and ``Strict-Transport-Security`` loses all three in a
``location /robots.txt { add_header Cache-Control no-cache; }``: that one header replaces the whole inherited set, the
config still passes ``nginx -t``, and the response quietly ships without its security headers (a Flutter app's deploy
template, 2026-10). An ``if`` block inside a location follows the same rule.

Rules:

* ``dropped-header``: a block (``server``, ``location``, ``if``, ``limit_except``) with at least one ``add_header`` must
  set every header name that an ``add_header`` in an enclosing block sets (names compared case-insensitively). The
  inherited set is the nearest enclosing level that declares any.
* ``unresolved-include``: an ``include`` inside a block where ``add_header`` matters (the block or an enclosing one sets
  one) may add or drop headers the gate cannot see. It is reported, unless ``resolve_includes=True`` and the included
  file(s) can be read relative to the conf file, in which case their directives are read in place.
* ``missing-required``: with *required*, every ``server`` and ``location`` must end up with each named header (its own
  set when it declares any ``add_header``, else the inherited one).

The parser reads ``#`` comments, single- and double-quoted strings (spaces, ``;`` and braces inside them), ``${VAR}``
envsubst placeholders of ``.template`` files and nested blocks. Unbalanced braces or a directive without its ``;`` is an
``unparsed-file``, never a pass. Directives with free-form bodies (``map``, ``types``, ``upstream``, ``geo``...) are not
descended into.

Usage in a consumer's meta test::

    from py_ci_shared.nginx_location_headers import assert_nginx_location_headers

    def test_nginx_headers():
        assert_nginx_location_headers(
            [ROOT / "deploy" / "nginx.conf.template"],
            required=("Content-Security-Policy", "X-Content-Type-Options", "Strict-Transport-Security"),
        )
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ._core import Finding, ParsedFile, ScanResult, SourceError, SourceProblem, iter_files, read_source, relative_posix
from ._gate_run import enforce_findings

__all__ = [
    "RULE_DROPPED",
    "RULE_INCLUDE",
    "RULE_REQUIRED",
    "NginxParseError",
    "assert_nginx_location_headers",
    "find_nginx_header_problems",
    "parse_nginx",
]

GATE = "nginx_location_headers"
REFRESH_FLAG = "--refresh-nginx-location-headers-baseline"
RULE_DROPPED = "dropped-header"
RULE_INCLUDE = "unresolved-include"
RULE_REQUIRED = "missing-required"
PATTERNS = ("*.conf", "*.conf.template")
#: Blocks whose ``add_header`` set is a level of nginx's inheritance; anything else is not descended into.
_LEVELS = frozenset({"http", "server", "location", "if", "limit_except"})
#: Blocks that serve a response and must carry the *required* headers.
_SERVING = frozenset({"server", "location"})
PathLike = Union[str, "os.PathLike[str]"]


class NginxParseError(ValueError):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.line = line


@dataclass
class Directive:
    name: str
    args: list[str]
    line: int
    block: Optional[list["Directive"]] = None

    def label(self) -> str:
        return " ".join([self.name, *self.args]).strip()


@dataclass
class _Lexer:
    text: str
    pos: int = 0
    line: int = 1
    tokens: list[tuple[str, int, bool]] = field(default_factory=list)  # (value, line, is_word)

    def run(self) -> list[tuple[str, int, bool]]:
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch == "\n":
                self.line += 1
                self.pos += 1
            elif ch.isspace():
                self.pos += 1
            elif ch == "#":
                end = text.find("\n", self.pos)
                self.pos = n if end < 0 else end
            elif ch in "{};":
                self.tokens.append((ch, self.line, False))
                self.pos += 1
            else:
                self.tokens.append((self._word(), self.line, True))
        return self.tokens

    def _quoted(self, quote: str) -> str:
        start_line, out = self.line, []
        self.pos += 1
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                out.append(self.text[self.pos + 1])
                self.pos += 2
                continue
            if ch == "\n":
                self.line += 1
            if ch == quote:
                self.pos += 1
                return "".join(out)
            out.append(ch)
            self.pos += 1
        raise NginxParseError(f"unterminated {quote} string", start_line)

    def _word(self) -> str:
        text = self.text
        out: list[str] = []
        while self.pos < len(text):
            ch = text[self.pos]
            if ch in "\"'" and not out:
                return self._quoted(ch)
            if ch.isspace() or ch in "{};":
                break
            if ch == "$" and text.startswith("${", self.pos):
                end = text.find("}", self.pos)
                if end < 0:
                    raise NginxParseError("unterminated ${ placeholder", self.line)
                out.append(text[self.pos : end + 1])
                self.pos = end + 1
                continue
            if ch == "\\" and self.pos + 1 < len(text):
                out.append(text[self.pos : self.pos + 2])
                self.pos += 2
                continue
            out.append(ch)
            self.pos += 1
        return "".join(out)


def parse_nginx(text: str) -> list[Directive]:
    """The directives of an nginx config, blocks nested. Raises :class:`NginxParseError` on unbalanced braces, a stray
    ``;``/``{``, an unterminated string, or a directive missing its ``;`` at the end of the file."""
    stack: list[list[Directive]] = [[]]
    opened: list[int] = []
    words: list[tuple[str, int]] = []
    for value, line, is_word in _Lexer(text).run():
        if is_word:
            words.append((value, line))
        elif value == ";":
            if not words:
                raise NginxParseError("';' with no directive before it", line)
            stack[-1].append(Directive(words[0][0], [w for w, _ in words[1:]], words[0][1]))
            words = []
        elif value == "{":
            if not words:
                raise NginxParseError("'{' with no block name before it", line)
            body: list[Directive] = []
            stack[-1].append(Directive(words[0][0], [w for w, _ in words[1:]], words[0][1], block=body))
            stack.append(body)
            opened.append(line)
            words = []
        else:
            _close(stack, opened, words, line)
    if words:
        raise NginxParseError(f"directive {words[0][0]!r} has no ';' at the end of the file", words[0][1])
    if opened:
        raise NginxParseError("block opened here is never closed", opened[-1])
    return stack[0]


def _close(stack: list[list[Directive]], opened: list[int], words: list[tuple[str, int]], line: int) -> None:
    if words:
        raise NginxParseError(f"directive {words[0][0]!r} has no ';' before '}}'", words[0][1])
    if len(stack) == 1:
        raise NginxParseError("'}' closes no open block", line)
    stack.pop()
    opened.pop()


@dataclass
class _Ctx:
    rel: str
    conf: Path
    required: dict[str, str]
    resolve_includes: bool
    findings: list[Finding] = field(default_factory=list)


def _expand(directives: list[Directive], ctx: _Ctx, depth: int = 0) -> tuple[list[Directive], list[Directive]]:
    """``(directives with readable includes spliced in, includes that stay unresolved)``."""
    out: list[Directive] = []
    unresolved: list[Directive] = []
    for d in directives:
        if d.name != "include" or d.block is not None:
            out.append(d)
            continue
        targets: list[Path] = []
        if ctx.resolve_includes and d.args and depth < 8:
            pattern = d.args[0]
            base = Path(pattern) if os.path.isabs(pattern) else ctx.conf.parent / pattern
            targets = sorted(base.parent.glob(base.name)) if any(c in base.name for c in "*?[") else ([base] if base.is_file() else [])
        spliced = False
        for target in targets:
            try:
                inner = parse_nginx(read_source(target))
            except (SourceError, NginxParseError):
                continue
            nested, still = _expand(inner, ctx, depth + 1)
            out.extend(nested)
            unresolved.extend(still)
            spliced = True
        if not spliced:
            unresolved.append(d)
    return out, unresolved


def _where(d: Directive) -> str:
    return f"`{d.label()}` (line {d.line})"


def _check_dropped(block: Directive, own: dict[str, str], inherited: dict[str, str], inherited_from: Directive, ctx: _Ctx) -> None:
    dropped = [inherited[k] for k in inherited if k not in own]
    if dropped:
        message = (
            f"{_where(block)} sets add_header {', '.join(own.values())}, so it no longer inherits "
            f"{', '.join(dropped)} from {_where(inherited_from)}; repeat them in this block"
        )
        ctx.findings.append(Finding(ctx.rel, block.line, RULE_DROPPED, message, key=f"{RULE_DROPPED}::{ctx.rel}::{block.label()}::{','.join(dropped)}"))


def _walk(block: Directive, inherited: dict[str, str], inherited_from: Optional[Directive], ctx: _Ctx) -> None:
    body, unresolved = _expand(block.block or [], ctx)
    own: dict[str, str] = {}
    for d in body:
        if d.name == "add_header" and d.args:
            own.setdefault(d.args[0].lower(), d.args[0])
    if own or inherited:
        for inc in unresolved:
            message = f"{_where(block)} has `{inc.label()}`, which may set or drop add_header lines the gate cannot read"
            ctx.findings.append(Finding(ctx.rel, inc.line, RULE_INCLUDE, message, key=f"{RULE_INCLUDE}::{ctx.rel}::{block.label()}::{inc.label()}"))
    if own and inherited_from is not None:
        _check_dropped(block, own, inherited, inherited_from, ctx)
    effective, source = (own, block) if own else (inherited, inherited_from)
    if ctx.required and block.name in _SERVING:
        missing = [name for key, name in ctx.required.items() if key not in effective]
        if missing:
            message = f"{_where(block)} serves responses without {', '.join(missing)}"
            ctx.findings.append(Finding(ctx.rel, block.line, RULE_REQUIRED, message, key=f"{RULE_REQUIRED}::{ctx.rel}::{block.label()}::{','.join(missing)}"))
    for child in body:
        if child.block is not None and child.name in _LEVELS:
            _walk(child, effective, source, ctx)


def _entries(conf_paths: Union[PathLike, Iterable[PathLike]], use_git: Optional[bool]) -> list[tuple[Path, str]]:
    raw = [conf_paths] if isinstance(conf_paths, (str, os.PathLike)) else list(conf_paths)
    out: list[tuple[Path, str]] = []
    for entry in (Path(e) for e in raw):
        if entry.is_dir():
            out += [(p, relative_posix(p, entry)) for p in iter_files(entry, PATTERNS, use_git=use_git)]
        else:
            out.append((entry, entry.as_posix()))
    return out


def _scan(
    conf_paths: Union[PathLike, Iterable[PathLike]], required: Iterable[str], resolve_includes: bool, use_git: Optional[bool]
) -> tuple[list[Finding], ScanResult]:
    scan = ScanResult(root=None, min_files=0)
    findings: list[Finding] = []
    wanted = {name.lower(): name for name in required}
    for path, rel in _entries(conf_paths, use_git):
        try:
            text = read_source(path)
            tree = parse_nginx(text)
        except SourceError as exc:
            scan.unparsed.append(SourceProblem(path, rel, exc.line or 1, exc.kind, exc.message))
            continue
        except NginxParseError as exc:
            scan.unparsed.append(SourceProblem(path, rel, exc.line, "unparsable", str(exc)))
            continue
        scan.files.append(ParsedFile(path, rel, ast.Module(body=[], type_ignores=[]), text))
        ctx = _Ctx(rel, path, wanted, resolve_includes)
        _walk(Directive("main", [], 1, block=tree), {}, None, ctx)
        findings.extend(ctx.findings)
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule)), scan


def find_nginx_header_problems(
    conf_paths: Union[PathLike, Iterable[PathLike]],
    *,
    required: Iterable[str] = (),
    resolve_includes: bool = False,
    use_git: Optional[bool] = None,
) -> list[Finding]:
    """Every dropped, unresolved or missing header in the nginx configs *conf_paths* (files, or directories searched for
    ``*.conf``/``*.conf.template``), plus one ``unparsed-file`` finding per config that could not be read or parsed."""
    findings, scan = _scan(conf_paths, required, resolve_includes, use_git)
    return sorted(findings + scan.unparsed_findings(), key=lambda f: (f.path, f.line, f.rule))


def assert_nginx_location_headers(
    conf_paths: Union[PathLike, Iterable[PathLike]],
    *,
    required: Iterable[str] = (),
    resolve_includes: bool = False,
    baseline_path: Optional[Union[str, Path]] = None,
    min_files: int = 1,
    allow_unparsed: bool = False,
    refresh: Optional[bool] = None,
    request: Optional[Any] = None,
    use_git: Optional[bool] = None,
) -> None:
    """Fail on any finding (or one *baseline_path* does not accept), on fewer than *min_files* configs parsed, and on a
    config that could not be read or parsed unless *allow_unparsed*."""
    findings, scan = _scan(conf_paths, required, resolve_includes, use_git)
    if allow_unparsed:
        scan.unparsed = []
    enforce_findings(
        findings,
        scan,
        gate=GATE,
        flag=REFRESH_FLAG,
        min_files=min_files,
        baseline_path=baseline_path,
        refresh=refresh,
        request=request,
        guidance="repeat every inherited add_header in the block that sets its own (nginx drops the inherited set)",
    )
