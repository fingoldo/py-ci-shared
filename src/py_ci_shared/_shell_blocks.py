"""A small shell block parser and the ``run:`` script extractor of GitHub workflow files.

Gates that ask "does this block of a shell script end in ``exit 0``" used to match each line against a regex and stop at the first
``fi``/``done``/``}`` they met, which cannot tell which block a line is in and cannot tell a ``run: |`` step from the step after it (audit 41-F12).
This module reads a script into :class:`Statement` records, each with the id of the BODY it sits in, so "same block" is an equality test:

* ``if``/``elif``/``else``/``fi``, ``for``/``while``/``until`` ... ``do``/``done``, ``case`` ... ``in`` ... ``;;`` ... ``esac``,
  ``{ ... }`` groups, ``( ... )`` subshells and function bodies each open a body; a condition (the words between ``if`` and ``then``) belongs to
  the body that encloses the ``if``;
* a command list is split at ``;``, ``&&``, ``||``, ``|``, ``&`` and newlines outside quotes, ``$(...)``, backticks and heredoc bodies;
  a statement that follows ``&&`` or ``||`` is marked ``conditional``;
* a backslash newline and a newline inside an open quote join the physical lines; ``#`` comments are dropped.

Approximation: this is a lexer for the shapes guard scripts use, not a POSIX shell; ``eval``, ``source``d helpers and aliases are not followed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional

from ._core.source import indent_width

__all__ = ["Script", "Statement", "parse_shell", "workflow_run_scripts"]

TOP_LEVEL = 0


@dataclass(frozen=True)
class Statement:
    """One simple command: its first physical line (0-based in the script), its text, the body it sits in, and where it can run."""

    line: int
    text: str
    block: int
    conditional: bool
    in_function: bool

    @property
    def head(self) -> str:
        """The command word, past ``!`` and leading ``NAME=value`` assignments (``""`` for a pure assignment)."""
        for word in self.text.split():
            if word == "!" or re.match(r"^[A-Za-z_]\w*=", word):
                continue
            return word
        return ""


@dataclass(frozen=True)
class Script:
    """A shell (or python) script found inside a file: *first_line* is the 0-based file line of ``lines[0]``."""

    first_line: int
    lines: "list[str]"
    shell: str = "bash"


# ---------------------------------------------------------------------------------------------------------------------------------------------
# lexer
# ---------------------------------------------------------------------------------------------------------------------------------------------


class _Lexer:
    """Words and separators of one logical line. Quotes, ``$(...)``, backticks and ``((...))`` stay inside their word."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.cur: "list[str]" = []
        self.par = 0
        self.tokens: "list[tuple[str, str]]" = []

    def flush(self) -> None:
        if self.cur:
            self.tokens.append(("word", "".join(self.cur)))
            self.cur = []

    def sep(self, value: str) -> None:
        self.flush()
        self.tokens.append(("sep", value))

    def run(self) -> "list[tuple[str, str]]":
        while self.i < len(self.text):
            self.i = self.step()
        self.flush()
        return self.tokens

    def step(self) -> int:
        text, i = self.text, self.i
        c = text[i]
        if self.par > 0:
            return self.in_substitution(c)
        if c in "'\"":
            return self.quoted(c)
        if c == "\\":
            self.cur.append(text[i : i + 2])
            return i + 2
        if c == "$" and text[i + 1 : i + 2] == "(":
            self.cur.append("$(")
            self.par = 1
            return i + 2
        if c == "`":
            end = text.find("`", i + 1)
            end = len(text) - 1 if end < 0 else end
            self.cur.append(text[i : end + 1])
            return end + 1
        if c == "#" and not self.cur:
            return len(text)
        if c.isspace():
            self.flush()
            if c == "\n":
                self.sep("\n")
            return i + 1
        if c in ";&|()":
            return self.operator(c)
        self.cur.append(c)
        return i + 1

    def quoted(self, quote: str) -> int:
        text, i = self.text, self.i
        j = i + 1
        while j < len(text) and text[j] != quote:
            j += 2 if (quote == '"' and text[j] == "\\") else 1
        self.cur.append(text[i : j + 1])
        return j + 1

    def in_substitution(self, c: str) -> int:
        if c in "'\"":
            return self.quoted(c)
        self.cur.append(c)
        if c == "(":
            self.par += 1
        elif c == ")":
            self.par -= 1
        return self.i + 1

    def operator(self, c: str) -> int:
        text, i = self.text, self.i
        two = text[i : i + 2]
        if c in "();":
            return self.group_or_semicolon(c, two)
        redirect = bool(self.cur) and self.cur[-1].endswith(">")
        if two in ("&&", "||"):
            self.sep(two)
            return i + 2
        if redirect or (c == "&" and text[i + 1 : i + 2] == ">"):
            self.cur.append(c)
            return i + 1
        self.sep(c)
        return i + 1

    def group_or_semicolon(self, c: str, two: str) -> int:
        i = self.i
        if c == ";":
            self.sep(two if two == ";;" else ";")
            return i + (2 if two == ";;" else 1)
        if c == ")":
            self.sep(")")
            return i + 1
        if two == "((" or (self.cur and self.cur[-1] in ("<", ">")):
            self.cur.append(two if two == "((" else "(")
            self.par = 2 if two == "((" else 1
            return i + len(self.cur[-1])
        if self.cur:
            self.cur.append("()" if two == "()" else "(")
            return i + (2 if two == "()" else 1)
        self.sep("(")
        return i + 1


_HEREDOC = re.compile(r"<<(-?)\s*(?:\"([^\"]+)\"|'([^']+)'|\\?([A-Za-z_]\w*))")


def _quote_state(text: str, quote: str = "") -> str:
    """The unclosed quote (``'``, ``"`` or ``""``) at the end of *text*, scanning on from the quote state *quote*."""
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        i += 1
    return quote


def _quote_open(text: str) -> bool:
    """True when *text* ends inside an unclosed ' or \" quote (backslash escapes outside single quotes)."""
    return bool(_quote_state(text))


def _heredoc_delimiters(text: str) -> "list[tuple[str, bool]]":
    return [(m.group(2) or m.group(3) or m.group(4), m.group(1) == "-") for m in _HEREDOC.finditer(text) if text[m.start() - 1 : m.start()] != "<"]


def logical_lines(lines: "list[str]") -> Iterator[tuple[int, str]]:
    """``(first physical index, text)`` per logical line: continuations and open quotes joined, heredoc bodies skipped."""
    i = 0
    while i < len(lines):
        start = i
        # pieces are collected and joined once: a quote left open over a long file joins every line after it, and joining
        # by repeated concatenation copied the whole text per line (quadratic in the file size)
        parts = [lines[i]]
        quote = _quote_state(lines[i])
        ends_backslash = lines[i].endswith("\\")
        while (ends_backslash or quote) and i + 1 < len(lines):
            i += 1
            if ends_backslash:
                # a continuation drops the backslash, which changes the scan, so the joined text is scanned afresh
                joined = "".join(parts)[:-1] + lines[i]
                parts = [joined]
                quote = _quote_state(joined)
                ends_backslash = joined.endswith("\\")
            else:
                # inside an open quote the next line continues the scan from where the last one stopped
                parts.append("\n" + lines[i])
                quote = _quote_state(lines[i], quote)
                ends_backslash = lines[i].endswith("\\")
        i += 1
        text = "".join(parts)
        yield start, text
        for delim, strip_tabs in _heredoc_delimiters(text):
            while i < len(lines) and (lines[i].lstrip("\t") if strip_tabs else lines[i]).rstrip() != delim:
                i += 1
            i += 1


# ---------------------------------------------------------------------------------------------------------------------------------------------
# block builder
# ---------------------------------------------------------------------------------------------------------------------------------------------


@dataclass
class _Frame:
    kind: str
    body: Optional[int] = None
    func: bool = False


_OPENS_WITH_CONDITION = frozenset({"if", "while", "until"})
_CLOSERS = frozenset({"fi", "done", "}"})


class _Builder:
    def __init__(self) -> None:
        self.stack: "list[_Frame]" = []
        self.statements: "list[Statement]" = []
        self.next_block = 1
        self.pending_func = False

    def new_body(self) -> int:
        self.next_block += 1
        return self.next_block - 1

    def current_block(self) -> int:
        for frame in reversed(self.stack):
            if frame.body is not None:
                return frame.body
        return TOP_LEVEL

    def push(self, kind: str, body: bool = False) -> None:
        func = kind == "brace" and self.pending_func
        self.pending_func = self.pending_func and not func
        self.stack.append(_Frame(kind, self.new_body() if body else None, func))

    def pop(self) -> None:
        if self.stack:
            self.stack.pop()

    def pop_through(self, kind: str) -> None:
        while self.stack and self.stack.pop().kind != kind:
            pass

    def keyword(self, words: "list[str]") -> "Optional[list[str]]":
        """Apply the structural keyword at the head of *words*; the words left, or None when the head is no keyword."""
        head, rest = words[0], words[1:]
        if head in _OPENS_WITH_CONDITION:
            self.push(head)
        elif head in ("then", "do", "else"):
            self.set_body()
        elif head == "elif":
            self.set_body(clear=True)
        elif head == "esac":
            self.pop_through("case")  # the last clause may have no ;;
        elif head in _CLOSERS:
            self.pop()
        elif head in ("for", "select"):
            self.push("loop")
            return []
        elif head == "case":
            self.push("case")
            return rest[rest.index("in") + 1 :] if "in" in rest else []
        elif head == "{":
            self.push("brace", body=True)
        else:
            return self.function_header(words)
        return rest

    def function_header(self, words: "list[str]") -> "Optional[list[str]]":
        head = words[0]
        if head == "function" and len(words) > 1:
            self.pending_func = True
            return words[3:] if words[2:3] == ["()"] else words[2:]
        if head.endswith("()") and len(head) > 2:
            self.pending_func = True
            return words[1:]
        if words[1:2] == ["()"]:
            self.pending_func = True
            return words[2:]
        return None

    def set_body(self, clear: bool = False) -> None:
        if self.stack:
            self.stack[-1].body = None if clear else self.new_body()

    def emit(self, line: int, words: "list[str]", conditional: bool) -> None:
        if not words:
            return
        in_function = any(f.func for f in self.stack)
        self.statements.append(Statement(line, " ".join(words), self.current_block(), conditional, in_function))

    def segment(self, line: int, words: "list[str]", sep: str, conditional: bool) -> None:
        while words:
            if self.stack and self.stack[-1].kind == "case":
                self.case_pattern(words, sep)
                return
            rest = self.keyword(words)
            if rest is None:
                break
            words = rest
        else:
            words = []
        if self.stack and self.stack[-1].kind == "case":
            self.case_pattern(words, sep)
            return
        if words == ["}"] or words == [")"]:
            return
        self.emit(line, words, conditional)

    def case_pattern(self, words: "list[str]", sep: str) -> None:
        if words and words[0] == "esac":
            self.pop_through("case")
        elif sep == ")":
            self.stack.append(_Frame("clause", self.new_body()))


def _close_after(builder: _Builder, sep: str) -> None:
    top = builder.stack[-1] if builder.stack else None
    if sep == ";;" and top is not None and top.kind == "clause":
        builder.pop()
    elif sep == ")" and top is not None and top.kind == "sub":
        builder.pop()
    elif sep == "(":
        builder.stack.append(_Frame("sub", builder.new_body()))


def parse_shell(lines: "list[str]") -> "list[Statement]":
    """The simple commands of a shell script in source order, each with the body it sits in (see the module docstring)."""
    builder = _Builder()
    for start, text in logical_lines(lines):
        words: "list[str]" = []
        conditional = False
        for kind, value in _Lexer(text).run():
            if kind == "word":
                words.append(value)
                continue
            if value == ")" and builder.stack and builder.stack[-1].kind == "sub" and not words:
                builder.pop()
            else:
                builder.segment(start, words, value, conditional)
                _close_after(builder, value)
            conditional = value in ("&&", "||")
            words = []
        builder.segment(start, words, "\n", conditional)
    return builder.statements


# ---------------------------------------------------------------------------------------------------------------------------------------------
# workflow run: scripts
# ---------------------------------------------------------------------------------------------------------------------------------------------

_RUN_KEY = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:[ \t]*(?P<value>.*?)\s*$")
_SHELL_KEY = re.compile(r"^\s*(?:-\s+)?shell:[ \t]*(?P<value>[^\s#]+)")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _block_body(lines: "list[str]", key_line: int, key_col: int) -> "tuple[int, list[str]]":
    """The body lines of the block scalar whose key sits at *key_line*/*key_col*, de-indented by their common indent."""
    end = key_line + 1
    while end < len(lines) and (not lines[end].strip() or indent_width(lines[end]) > key_col):
        end += 1
    while end > key_line + 1 and not lines[end - 1].strip():
        end -= 1
    body = lines[key_line + 1 : end]
    widths = [indent_width(x) for x in body if x.strip()]
    cut = min(widths) if widths else 0
    return key_line + 1, [x[cut:] if x.strip() else "" for x in body]


def _step_shell(lines: "list[str]", key_line: int, key_col: int) -> str:
    """The ``shell:`` value among the keys of the step that holds the ``run:`` at *key_line* (``bash`` when there is none)."""
    for backward, step in ((True, range(key_line - 1, -1, -1)), (False, range(key_line + 1, len(lines)))):
        for j in step:
            if not lines[j].strip():
                continue
            width = indent_width(lines[j])
            dash = lines[j].lstrip().startswith("- ")
            if dash and not backward:
                break  # the next step starts here
            if width < key_col and not (dash and width + 2 == key_col):
                break
            m = _SHELL_KEY.match(lines[j]) if width == key_col or dash else None
            if m:
                return m.group("value").strip("'\"")
            if dash:
                break
    return "bash"


def workflow_run_scripts(text: str) -> "list[Script]":
    """Every ``run:`` script of a workflow or composite-action file, one per step: inline values and ``|``/``>`` block scalars.

    A script keeps the file line of its first line, so a gate can report a position in the file; text outside ``run:`` values is not a script.
    """
    lines = text.splitlines()
    scripts: "list[Script]" = []
    i = 0
    while i < len(lines):
        m = _RUN_KEY.match(lines[i])
        if not m:
            i += 1
            continue
        col = len(lines[i]) - len(lines[i].lstrip()) + (2 if lines[i].lstrip().startswith("- ") else 0)
        value = m.group("value")
        shell = _step_shell(lines, i, col)
        if value[:1] in ("|", ">"):
            first, body = _block_body(lines, i, col)
            scripts.append(Script(first, body, shell))
            i = first + len(body)
        else:
            if value:
                scripts.append(Script(i, [_unquote(value)], shell))
            i += 1
    return scripts
