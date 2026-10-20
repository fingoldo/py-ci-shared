# Audit: deserialization, untrusted repo content, paths, symlinks, archives, regex DoS

Read-only audit of `src/py_ci_shared` in the main tree (`C:\Users\Admin\Machine learning\py-ci-shared`). The
`.claude/worktrees/*` copies were not read. No source or test file was edited. Reproducers lived in the session
scratchpad (`redos2.py`, `redos3.py`, `redos4.py`, `evilrepo/`), never in the repo. Timings are CPython 3.14 on
Windows 11, single run each.

Scope: pickle / yaml / json / marshal / eval / exec / importlib of data-named modules; reads of untrusted repo
content (workflow YAML, pyproject, TOML, baselines, Markdown trackers); consumer-supplied paths; symlinks; zip and
tar extraction; regex backtracking in gate parsers.

Severity counts: P0 0, P1 0, P2 4, Low 8.

## Findings

### DS-1 (P2) -- `_EMPTY_CATCH` regex backtracks exponentially, so the Dart empty-catch gate can hang

**Disposition:** RESOLVED -- the Dart empty-catch body now has one parse per position (disjoint branches, a comment runs to the end of its line); tests/test_regex_backtracking_regressions.py.

**Evidence.** `dart_scanners.py:694`:

```
_EMPTY_CATCH = re.compile(r"catch\s*\(\s*_\s*(?:,\s*_\s*)?\)\s*\{(?P<body>(?:[^{}]|//[^\n]*)*?)\}", re.DOTALL)
```

The body alternation `[^{}]` and `//[^\n]*` overlap: the `/` characters of a `//` comment can be consumed either by
`[^{}]` one character at a time or by the comment branch, with `[^\n]*` ending at any point. Each `//` therefore
doubles the number of parse paths. When the body contains a `{` before any `}` the match fails and the engine
explores all of them. `scan_empty_catch` (`dart_scanners.py:702`) calls `finditer` on the whole file, so the cost lands
on every Dart file a consumer ships.

Measured: `catch (_) {` + `"// x " * n` + `{`:

| n | time |
|---|------|
| 4 | 0.0005 s |
| 6 | 0.015 s |
| 8 | 0.57 s |
| 10 | more than 20 s (killed) |

**Failure scenario.** A consumer Dart file contains `catch (_) {`, about 10 to 20 `//` comment lines, then a nested
block (`if (...) {`) before the closing brace. That is an ordinary non-empty catch. The gate runs for minutes to hours
with no timeout inside the scanner, and the CI job hangs. A PR from a fork can supply the file.

**Fix.** Make the branches disjoint so there is one parse per position: `(?:[^{}/]|/(?!/)|//[^\n]*)*?`. An
equivalent, simpler fix is to strip comments first, then match `catch` bodies with `[^{}]*`. Add a regression test
with the 20-comment input that asserts it returns in under 1 s.

---

### DS-2 (P2) -- consumer `name` is used unvalidated as a clone destination, so a checkout can write outside `dest`

**Disposition:** RESOLVED -- load_consumers rejects names and repos with separators, dot components, ? or #; checkout refuses a target outside dest.

**Evidence.** `_consumers.py:125` builds `target = dest / consumer.name`. `load_consumers` (`_consumers.py:61`) only
checks that `name` is present and unique. It does not reject `/`, `\`, `..` or absolute paths. `checkout` then
calls `_git_clone(consumer, target, ...)`, which runs `git clone ... <target>`.

**Failure scenario.** A `consumers.toml` entry with `name = "../../../../home/runner/.config/x"` (or a Windows
`..\\..\\` form) makes CI clone a consumer repo to an arbitrary directory on the runner. The write is limited to
directories git can create (a non-empty existing target fails). The config file is repo-owned, so a reviewed PR is
the expected source. The CI job still runs with the consumer read token. Impact is a write outside the workspace on a
shared runner.

**Fix.** In `load_consumers`, reject any name that is not `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)`. As
defence in depth, in `checkout` assert `target.resolve().parent == dest.resolve()` before cloning.

---

### DS-3 (P2) -- tracked symlinks are read as corpus files, so scanners read host files outside the checkout

**Disposition:** RESOLVED -- iter_files skips symlinks; tests/test_core_corpus_symlinks.py.

**Evidence.** `_core/corpus.py:149` keeps a listed path when `p.is_file()`. `Path.is_file()` follows symlinks. Both
collection modes feed it: `git ls-files` (`git_listing`) lists a symlink entry (mode 120000), and the fallback
`_walk` (`corpus.py:106-107`, `os.walk(..., followlinks=False)`) lists symlinked files in `filenames`. Nothing
compares the mode or `is_symlink()`. `read_source` (`_core/source.py:84`) then reads the target with `read_bytes`.
`committed_line_endings.py:46` already skips mode 120000, so the corpus path is the inconsistent one.

**Failure scenario.** A PR to a public consumer adds `src/x.py -> /home/runner/.../secret.env` (or a Windows path).
Gates parse or read the target, and any gate that echoes a snippet or a parse message writes the target's text into
the CI log or an uploaded report. Results also depend on files outside the repo, which breaks reproducibility.

**Fix.** In `iter_files` after `is_file()`, skip `p.is_symlink()`, or resolve the target and require
`target.is_relative_to(base.resolve())`. Add a meta test that commits a symlink to `/etc/hosts` and asserts it is not
in `iter_files`.

---

### DS-4 (P2) -- `checkout_resolution` copies symlink targets from the host into the probe workdir

**Disposition:** RESOLVED -- the resolution probe copies with symlinks=True; tests/test_checkout_resolution_symlinks.py.

**Evidence.** `checkout_resolution.py:82` calls `shutil.copytree(repo_root, copy, ignore=...)` with the default
`symlinks=False`. That copies the contents of each symlink's target, so a repo symlink to `~/.ssh` or a home directory
ends up as real files under `workdir/copy`, where pytest then runs and may print them.

**Failure scenario.** A consumer repo contains `docs/keys -> /home/runner/.ssh`. The module-resolution probe copies
the key files into the workdir. They appear in pytest output, and if the workdir is uploaded as an artifact they leave
the runner.

**Fix.** Use `symlinks=True`, which copies links as links, or pass `ignore` callable that drops any entry where
`os.path.islink(...)` is true.

---

### DS-5 (Low) -- `_expand` lets a config item escape the repo root (absolute path or `../` glob)

**Disposition:** RESOLVED -- a config corpus entry that resolves outside the repository root raises ConfigError; tests/test_core_config_containment.py.

**Evidence.** `_core/config.py:253-262`. A non-glob item returns `repo_root / item` unchecked. An absolute item
replaces `repo_root`, and `..` escapes it. A glob uses `_glob.glob(escaped_root + "/" + item)`, which also follows
`..`.

**Failure scenario.** `[tool.py_ci_shared] corpus = ["/etc"]` or `["../../other/**/*.py"]` makes a gate read files
outside the checkout. The config is repo-owned, so this is low, but it is a silent non-hermetic input.

**Fix.** Resolve each candidate and require `is_relative_to(repo_root.resolve())`, raising `ConfigError` otherwise.

---

### DS-6 (Low) -- `black_filtered_apply` passes repo filenames as bare argv, so a name starting with `-` becomes an option

**Disposition:** RESOLVED -- black receives the file names after --; tests/test_audit_20261020_hardening.py.

**Evidence.** `black_filtered_apply.py:353`:
`subprocess.run([*_BLACK_CMD, "--check", "--config", config_path, *batch], ...)`. `batch` holds repo paths. No `--`
separator is used.

**Failure scenario.** A tracked file named `-c` or `--fast-something` changes black's parsing. The argv is a list, so no
shell injection is possible, but the wrong option is applied and a gate verdict can change.

**Fix.** Insert `"--"` before `*batch` if black's click parser accepts it, or pass `./`-prefixed relative names.

---

### DS-7 (Low) -- quadratic backtracking in the requirement-rest regex

**Disposition:** RESOLVED -- the PEP 508 rest group is taken greedily and the specifier pattern has one split per position; measured quadratic at 40000 spaces before, milliseconds after.

**Evidence.** `_ci_install_parts.py:290`:
`re.fullmatch(r"(?:[<>=!~]=?[^,]*,?\s*)*|\(.*\)|", rest)`. `[^,]*` and `\s*` both match spaces, so the outer `*`
has O(n) splits per iteration.

Measured: `"<" + " " * 2000 + ",x"` takes 0.135 s. The cost is quadratic, not exponential.

**Failure scenario.** A requirements line of tens of thousands of characters costs tens of seconds in the install-cover
gate. Not a hang at realistic line lengths.

**Fix.** Use `(?:[<>=!~]=?[^,]*(?:,|$)\s*)*` or drop the `\s*` and strip the rest first.

---

### DS-8 (Low) -- quadratic backtracking in `external_fact_tables._URL`

**Disposition:** RESOLVED -- the bare-domain URL pattern starts only at a token boundary, so it is linear.

**Evidence.** `external_fact_tables.py:67`:
`\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/[^\s)\]>,]*`. The group `(?:\.[a-z0-9-]+)*` and the trailing
`\.[a-z]{2,}` overlap on the last label.

Measured: `"a." * 3000 + "!"` takes 0.95 s (6000 characters). Quadratic.

**Failure scenario.** A Markdown or docs file with one very long dotted token without whitespace slows the
external-fact gate. Low impact.

**Fix.** Anchor the label boundary: `\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/`, which needs a `/` after the last label and
removes the overlap.

---

### DS-9 (Low) -- `edge_function_hygiene._EMPTY_LEFT_RE` is quadratic on long whitespace runs

**Disposition:** RESOLVED -- one whitespace quantifier per position around the closing parentheses.

**Evidence.** `edge_function_hygiene.py:83`: `\s*\)*\s*$`. Two adjacent `\s*` around `\)*`.

Measured: `"null" + " " * 5000 + "x"` takes 0.31 s.

**Failure scenario.** A minified or generated file with a long whitespace run after `null` costs about a second per
occurrence.

**Fix.** Use `\s*(?:\)\s*)*$`, which has one whitespace quantifier per position.

---

### DS-10 (Low) -- `is_whole_file_read` lets non-`SyntaxError` parse failures escape the gate

**Disposition:** RESOLVED -- is_whole_file_read falls back to the text test on RecursionError, MemoryError and ValueError.

**Evidence.** `audit_round_format.py:377` calls `ast.parse(expr.strip(), mode="eval")` and catches only `SyntaxError`.
Measured on 3.14: `"-" * 200000 + "1"` raises `MemoryError`, and `"a" + ".b" * 100000` raises `RecursionError`.

**Failure scenario.** A consumer's verifier test contains a huge expression. The meta test crashes with an internal
error instead of returning a verdict, so the check is skipped by a traceback rather than reported.

**Fix.** Catch `(SyntaxError, RecursionError, MemoryError, ValueError)` and fall back to the string test.

---

### DS-11 (Low) -- `install_safe_hook` writes through a symlinked hook path

**Disposition:** RESOLVED -- install_safe_hook refuses to patch a symlinked hook.

**Evidence.** `install_safe_hook.py:88`: `hook_path.write_bytes(...)` with no `is_symlink()` check.

**Failure scenario.** A hook path that is a symlink (for example in a shared `core.hooksPath`) redirects the write to
the link target. The caller is a developer's own hook setup, so the impact is small.

**Fix.** Refuse to patch when `hook_path.is_symlink()`, or write via `atomic_write_text` on the resolved path after
checking it is inside the repo.

---

### DS-12 (Low, latent) -- `consumer_import_census._extract_ref` calls `tarfile.extractall` without a filter

**Disposition:** RESOLVED -- consumer_import_census rejects members that climb out of the destination and uses filter='data' on Python 3.12 and later.

**Evidence.** `consumer_import_census.py:213-215`. The member filter keeps only regular files and directories, and the
tar comes from `git archive`. I tested that `git archive` refuses a tree entry named `../ESCAPED.txt` with
`invalid path`, so the path-traversal route is closed by git today. The extraction itself still trusts member names.

**Failure scenario.** None today. A future switch to a different archive source, or a git version that emits `..`,
would let member names write outside `dest`.

**Fix.** Pass `filter="data"` on Python 3.12 and later, or reject any member whose normalised name starts with `..` or
`/`.

---

## Checked and cleared

* **No `eval`, `exec`, `pickle`, `marshal`, `shelve`, `joblib.load` or `torch.load` in the package.** The package does
  not deserialize untrusted objects. `unsafe_deserialization.py` is a scanner for consumers and is not itself unsafe.
* **YAML.** Every `yaml` call in the package uses `safe_load` or a `SafeLoader` subclass
  (`ci_default_branch_never_cancelled.py:404`, `ci_install_covers_conftest.py:205`, and the rest). The `_LineLoader`
  subclasses only add a constructor for line numbers.
* **TOML.** `tomllib`, with `tomli` as the fallback (`_toml_compat.py`), in every reader. No `eval` of values.
* **zip extraction** (`embedded_postgres.py:341`, `:382`). The member list is prefix-filtered, but
  `zipfile.extractall` strips `..` and absolute components itself, so this is not a zip-slip path. The timezone and
  pginstall unpacks are fixed-wheel, pip-downloaded content.
* **`shell=True`.** No occurrence in the package. Every subprocess call is an argv list.
* **Consumer URL and token.** `Consumer.url` is `https://github.com/{repo}.git`, with a fixed host. `repo` is checked for
  exactly one `/`. The token is sent only as the `http.https://github.com/` extraheader, so it cannot reach another host.
* **Imports of repo-owned modules** (`cli.py:122`, `corpus_drift.py:180`, `checkout_resolution.py:49`,
  `constant_relations.py:150`, `gate_population_canary.load_gate`). These run code from the repo being checked, which is
  the design. The name in `cli` is validated against the registry first (`registry.by_name`).
* **Baseline and cache JSON** (`_core/baseline.py:83`, `mutation_teeth._load_cached_run`). Parsed as plain data, with
  malformed entries treated as a miss. No object construction.
* **Atomic writes.** `_core/baseline.atomic_write_text` uses `mkstemp` in the target directory and `os.replace`.
  `embedded_postgres` removes only paths it created or that are not symlinks.
* **Other regexes checked and linear or bounded.** `_TEST_PATH`, `_BACKTICK_PATH_RE` (`audit_disposition_parity`),
  `_EXPR` (`ci_default_branch_never_cancelled`), `_ADD_COLUMN`, `_STRING_LITERAL` (`sql_verify`), `_KEY_VALUE`
  (`embedded_postgres`), `_MD_LINK_RE`, `_CODE_SPAN_RE`, `black_filtered_apply` string regexes and the `_DISPOSITION_RE`
  family. Alternation branches in each are disjoint, or the quantifiers are bounded by a delimiter.
  `_BACKTICK_PATH_RE` timed at 0.007 s on a crafted 800-character line.

## Method note

Reading the package plus targeted timing of the regexes that had overlapping alternation or nested quantifiers. Only
DS-1 was confirmed as exponential. DS-7 to DS-9 were measured as quadratic. Line numbers are from the main tree at
the time of the audit.
