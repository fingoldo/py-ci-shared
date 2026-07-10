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
88-col default).

Without --write and without --check: prints a unified diff of what WOULD
change for each listed FILE and exits 0.
With --write: rewrites each listed FILE in place.
With --check <roots>: each root may be a DIRECTORY (recursively discovers *.py under it,
skipping .git/.venv/.pytest_cache/build/dist/__pycache__/legacy, plus any names in the
EXTRA_EXCLUDED_DIRS env var) or an individual .py FILE path (included as-is, no directory
walk) -- the latter is what a pre-commit hook passes (a list of changed files, not
directories). Reports which files still have non-excluded-class Black findings, and exits 1
if any do (0 if the tree/file-set is fully filtered-Black-clean).
"""
import sys
import os
import shlex
import re
import subprocess
import difflib
import pathlib

EXCLUDED_DIR_NAMES = {".git", ".venv", ".pytest_cache", "build", "dist", "__pycache__", "legacy", "benchmarks", "_benchmarks", "profiling"}
# A consuming repo with its own dir-name to skip (rare) can add it without forking this file.
if os.environ.get("EXTRA_EXCLUDED_DIRS"):
    EXCLUDED_DIR_NAMES |= set(os.environ["EXTRA_EXCLUDED_DIRS"].split(","))

# Override how `black` is invoked via the BLACK_CMD env var (space-separated),
# e.g. BLACK_CMD="uvx black" for projects that run Black through uv's tool
# cache rather than a pip-installed environment. Defaults to `python -m black`.
_BLACK_CMD = shlex.split(os.environ["BLACK_CMD"]) if os.environ.get("BLACK_CMD") else [sys.executable, "-m", "black"]


def discover_py_files(roots):
    """Accepts a mix of directory roots (recursively walked) and individual .py file paths
    (included as-is) -- the latter is what pre-commit passes (a list of changed files, not
    directories), so --check must handle both, not just the directory-walk case the CLI was
    originally documented for."""
    files = []
    for root in roots:
        p = pathlib.Path(root)
        if p.is_file():
            if p.suffix == ".py" and not any(part in EXCLUDED_DIR_NAMES for part in p.parts):
                files.append(str(p))
            continue
        for sub in p.rglob("*.py"):
            if any(part in EXCLUDED_DIR_NAMES for part in sub.parts):
                continue
            files.append(str(sub))
    return sorted(files)


def run_black_stdin(src: str, config_path: str) -> str:
    proc = subprocess.run(
        [*_BLACK_CMD, "-q", "--config", config_path, "-"],
        input=src.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode not in (0,):
        raise RuntimeError(f"black failed: {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout.decode("utf-8")


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


def _triple_quote_state_before_each_line(lines):
    """For each line in ``lines``, whether we are INSIDE a triple-quoted string at that line's
    start, tracked via a running parity toggle across the whole file (not just one hunk). Needed
    because a single hunk's hand-picked hasattr-style local odd/even delimiter count is wrong
    when a hunk happens to contain BOTH a closing delimiter (from a string opened earlier) and an
    unrelated opening delimiter (for a string that closes later) -- their counts can sum to even
    even though the hunk is unsafe to normalize on its own. Global state removes the ambiguity.
    """
    state = []
    inside = False
    for line in lines:
        state.append(inside)
        n = line.count('"""') + line.count("'''")
        if n % 2:
            inside = not inside
    return state


def _touches_multiline_string(lines, state_before_block):
    """True if ``lines`` (a slice starting where ``state_before_block`` applies) either starts
    already inside a triple-quoted string, or contains an odd number of delimiters (so it ends
    inside/outside inconsistently with its own local content) -- either way, a fragment whose
    string boundaries cannot be resolved from this slice alone. See
    ``_triple_quote_state_before_each_line`` for why a local odd/even count on the slice alone is
    insufficient.
    """
    if state_before_block:
        return True
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
                elif _touches_multiline_string(s_old, orig_string_state[i1 + si1]) or _touches_multiline_string(s_new, fmt_string_state[j1 + sj1]):
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
    formatted = run_black_stdin(orig, config_path)
    result = filtered_apply(orig, formatted)
    return result != orig, orig, result


def main():
    args = sys.argv[1:]
    write = "--write" in args
    check = "--check" in args
    args = [a for a in args if a not in ("--write", "--check")]
    config_path = None
    if "--config" in args:
        idx = args.index("--config")
        config_path = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]
    if not config_path:
        raise SystemExit("--config <path-to-pyproject.toml> is required")

    if check:
        files = discover_py_files(args)
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
