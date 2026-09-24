"""Apply Black's reformatting to a file, EXCEPT for opcodes matching excluded
classes: blank-line insertion (pure or amid other lines), arg/import-list
EXPLOSION (packed multi-item line -> one-item-per-line), and semicolon-joined
compound-statement splitting (``a = 1; b = 2`` -> one statement per line --
consuming projects keep this compact, see their pyproject.toml's E701/E702
ignore comments). Everything else Black wants to change (collapses, quote
style, blank-line removal, docstring reflow, redundant-paren removal,
whitespace reflow) is applied normally.

Shared across projects via the py-ci-shared package. See
https://github.com/fingoldo/py-ci-shared for the rationale behind the two
excluded classes (a human decision, not configurable via any stock Black
flag).

Usage:
    python -m py_ci_shared.black_filtered_apply --config pyproject.toml [--write] <file.py> [file2.py ...]
    python -m py_ci_shared.black_filtered_apply --config pyproject.toml --check <root_dir_or_file> [... ...]

--config is required (never rely on directory walk-up -- a file processed
from a scratch/CI checkout dir must not silently fall back to Black's
88-col default). ``--config=PATH`` works too. --check and --write are mutually exclusive.
Each file is piped to Black with ``--stdin-filename``, so Black's own force-exclude and
.pyi mode apply to it exactly as they would to a path argument.

Without --write and without --check: prints a unified diff of what WOULD
change for each listed FILE and exits 0.
With --write: rewrites each listed FILE in place.
With --check <roots>: each root may be a DIRECTORY (recursively discovers *.py under it,
skipping .git/.venv/.pytest_cache/build/dist/__pycache__/legacy, plus any names in the
EXTRA_EXCLUDED_DIRS env var) or an individual .py FILE path (included as-is, no directory
walk) -- the latter is what a pre-commit hook passes (a list of changed files, not
directories). Excluded names are matched against path parts RELATIVE to the root, so a
checkout that itself lives under a directory named ``build`` is still scanned. A root that
does not exist, or a directory root holding no .py file, is an error rather than a clean run.
Reports which files still have non-excluded-class Black findings, and exits 1
if any do (0 if the tree/file-set is fully filtered-Black-clean).
"""

import sys
import os
import io
import shlex
import re
import subprocess
import difflib
import pathlib
import tokenize

from py_ci_shared._core import CorpusError, iter_files

EXCLUDED_DIR_NAMES = {".git", ".venv", ".pytest_cache", "build", "dist", "__pycache__", "legacy", "benchmarks", "_benchmarks", "profiling"}
# A consuming repo with its own dir-name to skip (rare) can add it without forking this file.
if os.environ.get("EXTRA_EXCLUDED_DIRS"):
    EXCLUDED_DIR_NAMES |= set(os.environ["EXTRA_EXCLUDED_DIRS"].split(","))

# Override how `black` is invoked via the BLACK_CMD env var (space-separated),
# e.g. BLACK_CMD="uvx black" for projects that run Black through uv's tool
# cache rather than a pip-installed environment. Defaults to `python -m black`.
_BLACK_CMD = shlex.split(os.environ["BLACK_CMD"]) if os.environ.get("BLACK_CMD") else [sys.executable, "-m", "black"]


class DiscoveryError(Exception):
    """A --check root is missing, or a directory root holds nothing to check."""


def _relative_parts(p: pathlib.Path):
    """Parts of an explicit file path that the exclusion applies to: relative to the CWD when the path is under it."""
    if p.is_absolute():
        try:
            return p.resolve().relative_to(pathlib.Path.cwd().resolve()).parts
        except ValueError:
            return p.parts[-1:]
    return p.parts


def discover_py_files(roots):
    """Accepts a mix of directory roots (recursively walked) and individual .py file paths
    (included as-is) -- the latter is what pre-commit passes (a list of changed files, not
    directories), so --check must handle both, not just the directory-walk case the CLI was
    originally documented for.

    Raises :class:`DiscoveryError` for a root that does not exist and for a directory root
    with no .py file under it: both would otherwise print "All 0 files ... clean".
    """
    files = []
    for root in roots:
        p = pathlib.Path(root)
        if p.is_file():
            if p.suffix == ".py" and not any(part in EXCLUDED_DIR_NAMES for part in _relative_parts(p)):
                files.append(str(p))
            continue
        try:
            found = iter_files(p, ("*.py",), exclude=EXCLUDED_DIR_NAMES)
        except CorpusError as exc:
            raise DiscoveryError(str(exc)) from exc
        if not found:
            raise DiscoveryError(f"no .py files under {p} (after excluding {sorted(EXCLUDED_DIR_NAMES)}); nothing would be checked")
        files.extend(str(sub) for sub in found)
    return sorted(files)


def run_black_stdin(src: str, config_path: str, filename=None) -> str:
    """Black's output for *src*. With *filename*, Black sees the path (``--stdin-filename``): its force-exclude
    and .pyi mode apply, and an excluded file comes back unchanged."""
    cmd = [*_BLACK_CMD, "-q", "--config", config_path]
    if filename:
        cmd += ["--stdin-filename", str(filename)]
    proc = subprocess.run(
        [*cmd, "-"],
        input=src.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode not in (0,):
        raise RuntimeError(f"black failed: {proc.stderr.decode('utf-8', 'replace')}")
    out = proc.stdout.decode("utf-8")
    if filename and ((not out and src) or out.replace("\r\n", "\n") == src.replace("\r\n", "\n")):
        # Force-excluded: Black echoes the source (through a text-mode stdout, so CRLF on Windows) or prints nothing.
        return src
    return out


def _swap_single_to_double_quotes(s: str) -> str:
    """Best-effort normalize simple 'x' string literals to "x" so quote-style
    changes (which Black applies independently of explosion/collapse) don't
    mask an explosion/collapse comparison. Skips triple-quoted strings and
    literals that already contain a double quote (Black wouldn't requote
    those either, so leaving them alone is correct)."""

    def repl(m):
        inner = m.group(1)
        if '"' in inner:
            return m.group(0)
        return '"' + inner + '"'

    return re.sub(r"'((?:[^'\\]|\\.)*)'", repl, s)


# Triple-quoted alternatives are listed FIRST so a leading `"""`/`'''` (optionally r-prefixed,
# as in a CUDA RawKernel source literal) is matched as one atomic multi-line span rather than the
# single-quote alternatives matching its first two quote characters as an empty string. Their
# content is intentionally allowed to cross newlines and contain arbitrary punctuation -- unlike
# the single-quote case below, a triple-quoted string's start/end delimiter is unambiguous, so
# there is no risk of scanning past it into unrelated code.
#
# The single- or double-quoted alternatives are restricted to NOT cross a newline (the
# [^"\\\n] / [^'\\\n] classes exclude \n explicitly -- character classes match it by default).
# Without that exclusion, an apostrophe in a comment (e.g. "# don't do this") has no closing
# quote on its own line, so the regex would keep scanning past the newline looking for one and
# could match all the way to an UNRELATED quote several lines down, "protecting" (and so
# preserving verbatim, unstripped) a huge, wrong span of comments/code as if it were one string
# literal's content -- confirmed against pyutilz's own source during this fix's own regression
# testing.
#
# A prior version of this regex had no triple-quoted alternative at all (the header comment
# claimed they were "handled separately", which was never actually implemented). Multi-line
# raw-string call arguments (e.g. ``cp.RawKernel(r"""...""", "name")``) then had their internal
# whitespace/commas stripped as if structural, which garbled the before/after comparison enough
# that a genuine call-arg-list explosion of that RawKernel(...) call went undetected and got
# silently APPLIED instead of rejected -- confirmed against mlframe's own
# _plugin_mi_classif_batch_cuda_resident during this fix's regression testing.
_STRING_LITERAL_RE = re.compile(r'r?"""(?:.|\n)*?"""' r"|r?'''(?:.|\n)*?'''" r'|"(?:[^"\\\n]|\\.)*"' r"|'(?:[^'\\\n]|\\.)*'")


def norm(s: str) -> str:
    """Normalize a line for explosion/collapse comparison: strip whitespace, commas, parens,
    and semicolons -- EXCEPT inside string literals, where a comma/paren/space is real content,
    not structural formatting. Without this exclusion, two blocks whose string CONTENTS differ
    (e.g. ``foo("a, b")`` vs ``foo("a", "b")``) but happen to normalize to the same bag of
    characters once literal commas are also stripped could be misclassified as an explosion/
    collapse of the SAME call and have a genuine Black fix silently rejected (or the reverse: a
    real explosion masked by string content padding out the character counts to match).
    ';' is stripped too (outside strings) so semicolon-joined compound statements (``a = 1; b =
    2``, explicitly kept as intentional compact style) normalize the same as Black's
    one-statement-per-line split, and get caught by the explosion detector below like any other
    packed line.
    """
    s = _swap_single_to_double_quotes(s)
    out = []
    pos = 0
    for m in _STRING_LITERAL_RE.finditer(s):
        out.append(re.sub(r"[\s,();]+", "", s[pos : m.start()]))
        out.append(m.group(0))  # string literal kept verbatim, including internal punctuation
        pos = m.end()
    out.append(re.sub(r"[\s,();]+", "", s[pos:]))
    return "".join(out)


def is_all_blank(lines):
    return all(line.strip() == "" for line in lines) and len(lines) > 0


def _parity_state(lines):
    state = []
    inside = False
    for line in lines:
        state.append(inside)
        n = line.count('"""') + line.count("'''")
        if n % 2:
            inside = not inside
    state.append(inside)
    return state


_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _triple_quote_state_before_each_line(lines):
    """For each line in ``lines`` (plus one entry for the end of the file), whether we are INSIDE a
    multi-line string at that line's start.

    Read from the tokenizer's STRING spans, so a delimiter that is itself string content
    (``Q = '<three double quotes>'``) does not flip the state: a file-wide delimiter-parity toggle read that line as
    opening a string, marked every later line as inside one, and so rejected every later fix while
    --check passed unformatted code. A file that does not tokenize falls back to the parity toggle.
    Global (file-wide) state is still what a hunk is judged by: a single hunk may hold the end of one
    string and the start of another.
    """
    state = [False] * (len(lines) + 1)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO("".join(lines)).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return _parity_state(lines)
    spans = []
    fstring_starts = []
    for tok in tokens:
        if tok.type == tokenize.STRING and tok.start[0] < tok.end[0]:
            spans.append((tok.start[0], tok.end[0]))
        elif _FSTRING_START is not None and tok.type == _FSTRING_START:
            fstring_starts.append(tok.start[0])
        elif _FSTRING_END is not None and tok.type == _FSTRING_END and fstring_starts:
            start = fstring_starts.pop()
            if start < tok.end[0]:
                spans.append((start, tok.end[0]))
    for start_row, end_row in spans:
        # Lines are 1-based in tokens; the lines after the opening one, up to and including the closing one, start inside.
        for row in range(start_row + 1, min(end_row, len(lines)) + 1):
            state[row - 1] = True
    return state


def _touches_multiline_string(lines, state_before_block, state_after_block=None):
    """True if ``lines`` (a slice starting where ``state_before_block`` applies) starts inside a
    multi-line string, or ends inside one (the line after it starts inside) -- either way a fragment
    whose string boundaries cannot be resolved from this slice alone. Without *state_after_block*
    (older callers) an odd local delimiter count stands in for "ends inside".
    """
    if state_before_block:
        return True
    if state_after_block is not None:
        return bool(state_after_block)
    text = "".join(lines)
    return (text.count('"""') + text.count("'''")) % 2 == 1


def looks_like_import_or_call_list(old_block, new_block):
    """True if old_block and new_block are the SAME tokens (ignoring whitespace/
    commas/parens) but spread over a DIFFERENT number of lines -- the arg/import
    explosion (or collapse) pattern. Returns 'explode' / 'collapse' / None.
    """
    old_txt = norm("".join(old_block))
    new_txt = norm("".join(new_block))
    if old_txt != new_txt or old_txt == "":
        return None
    if len(new_block) > len(old_block):
        return "explode"
    if len(new_block) < len(old_block):
        return "collapse"
    return None


def filtered_apply(orig: str, formatted: str) -> str:
    orig_lines = orig.splitlines(keepends=True)
    fmt_lines = formatted.splitlines(keepends=True)
    orig_string_state = _triple_quote_state_before_each_line(orig_lines)
    fmt_string_state = _triple_quote_state_before_each_line(fmt_lines)
    sm = difflib.SequenceMatcher(None, orig_lines, fmt_lines, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_block = orig_lines[i1:i2]
        new_block = fmt_lines[j1:j2]
        if tag == "equal":
            out.extend(new_block)
            continue
        if tag == "insert":
            # pure blank-line insertion -> reject (keep nothing, i.e. don't insert)
            if is_all_blank(new_block):
                continue
            out.extend(new_block)
            continue
        if tag == "delete":
            # pure blank-line removal -> accept (i.e. actually remove: emit nothing)
            if is_all_blank(old_block):
                continue
            # non-blank deletion with nothing replacing it: accept Black's change
            continue
        if tag == "replace":
            # Never short-circuit on the whole replace block: it may bundle an
            # explosion-like change with an unrelated adjacent one (e.g. Black
            # merges a semicolon-split and a nearby quote-style fix into one
            # hunk because they're on consecutive lines). Always re-diff at a
            # finer grain so only the excluded sub-part gets rejected.
            sub_sm = difflib.SequenceMatcher(None, old_block, new_block, autojunk=False)
            sub_out = []
            for stag, si1, si2, sj1, sj2 in sub_sm.get_opcodes():
                s_old = old_block[si1:si2]
                s_new = new_block[sj1:sj2]
                if stag == "equal":
                    sub_out.extend(s_new)
                elif stag == "insert" and is_all_blank(s_new):
                    continue
                elif stag == "delete" and is_all_blank(s_old):
                    continue
                elif _touches_multiline_string(s_old, orig_string_state[i1 + si1], orig_string_state[i1 + si2]) or _touches_multiline_string(
                    s_new, fmt_string_state[j1 + sj1], fmt_string_state[j1 + sj2]
                ):
                    # A fragment of a multi-line triple-quoted string; norm() cannot reliably
                    # classify it (see _touches_multiline_string). Reject conservatively rather
                    # than risk silently applying a misclassified explosion.
                    sub_out.extend(s_old)
                else:
                    sub_kind = looks_like_import_or_call_list(s_old, s_new)
                    if sub_kind == "explode":
                        sub_out.extend(s_old)
                    else:
                        sub_out.extend(s_new)
            out.extend(sub_out)
            continue
    return "".join(out)


def process_one(path, config_path):
    """Returns (changed: bool, orig: str, result: str)."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        orig = f.read()
    formatted = run_black_stdin(orig, config_path, filename=path)
    result = filtered_apply(orig, formatted)
    return result != orig, orig, result


def _pop_config(args):
    config_path = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--config":
            if i + 1 >= len(args):
                raise SystemExit("--config needs a value: --config <path-to-pyproject.toml>")
            config_path = args[i + 1]
            i += 2
            continue
        if a.startswith("--config="):
            config_path = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return config_path, rest


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in args
    check = "--check" in args
    if write and check:
        raise SystemExit("--check and --write are mutually exclusive: --check never rewrites files")
    args = [a for a in args if a not in ("--write", "--check")]
    config_path, args = _pop_config(args)
    if not config_path:
        raise SystemExit("--config <path-to-pyproject.toml> is required")

    if check:
        if not args:
            raise SystemExit("--check needs at least one directory or file to check")
        try:
            files = discover_py_files(args)
        except DiscoveryError as exc:
            raise SystemExit(f"--check: {exc}")
        changed_files = []
        for path in files:
            try:
                changed, _, _ = process_one(path, config_path)
            except Exception as e:
                # Broadened from RuntimeError-only: process_one's own file read (encoding="utf-8")
                # can raise UnicodeDecodeError, and any other per-file I/O/subprocess hiccup should
                # not abort the whole --check run -- report it and keep going, same as the
                # RuntimeError case black itself raises.
                print(f"ERROR: {path}: {type(e).__name__}: {e}", file=sys.stderr)
                changed = True
            if changed:
                changed_files.append(path)
        if changed_files:
            print(f"{len(changed_files)}/{len(files)} files have non-excluded-class Black findings:")
            for p in changed_files:
                print(f"  {p}")
            raise SystemExit(1)
        print(f"All {len(files)} files are filtered-Black-clean.")
        return

    files = args
    for path in files:
        changed, orig, result = process_one(path, config_path)
        if not changed:
            print(f"UNCHANGED: {path}")
            continue
        if write:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(result)
            print(f"WROTE: {path}")
        else:
            diff = difflib.unified_diff(
                orig.splitlines(keepends=True),
                result.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
            )
            sys.stdout.writelines(diff)


if __name__ == "__main__":
    main()
