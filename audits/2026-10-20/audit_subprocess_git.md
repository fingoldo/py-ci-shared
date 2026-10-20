# Audit: subprocess, git, shell, env, temp files, timeouts (py-ci-shared)

Date: 2026-10-20. Mode: read-only static review of `src/py_ci_shared`. No source or test file was edited and no
test or probe was executed; every finding is from reading the code. Items marked "unverified" need a run on Windows
before a fix is merged.

Scope searched: every `subprocess.run/Popen/call/check_*`, `os.system/popen`, `shell=True` (none found), `GIT_*`
environment handling, `tempfile.*`, `shutil.rmtree/copytree/unlink`, `shutil.which`, `cwd=`, and `timeout=`.

Summary: P0 = 0, P1 = 1, P2 = 4, Low = 9.

---

## Findings

### SUB-1 (P1) -- bare executable names are resolved from the current directory first

**Disposition:** RESOLVED -- _core/git.py sets NoDefaultCurrentDirectoryInExePath at import, which Windows reads at call time; the test asserts the variable, the Windows lookup itself was not run here.

- Sites: `_core/git.py:121` (`run_git` runs `"git"`, every git call in the package goes through it); `_mutation_runner.py:128`
  (`"taskkill"`); `setup_env.py:56`, `:61`, `:89`, `:91` (`"setx"`, `"launchctl"`); `guard_population.py:179`
  (`shutil.which("bash") or "bash"`); `ci_health.py:377` (`shutil.which("gh")`).
- Failure scenario: `subprocess.run(["git", ...])` on Windows passes no application name to CreateProcess, so Win32
  searches the directory of the calling application and then the current directory before PATH. `shutil.which` on
  Windows also checks the current directory first. A gate run from a consumer or PR checkout (cwd = untrusted repo)
  whose root contains `git.exe` or `taskkill.exe` runs that file with the gate's environment, which holds
  `CONSUMER_READ_TOKEN`, `GITHUB_TOKEN` and similar. The planted binary is found before the real one.
- Fix: resolve the executable once through PATH entries only (skip `""` and `"."`), then pass that absolute path to
  `subprocess`. Alternatively set `NoDefaultCurrentDirectoryInExePath=1` in `os.environ` at the start of each entry
  point (`_core/git.py` import time is the earliest common point). Add a canary test on Windows that plants `git.exe`
  in cwd.

### SUB-2 (P2) -- test and mutation child processes inherit a hook's `GIT_DIR`/`GIT_INDEX_FILE`

**Disposition:** RESOLVED -- the nested pytest environment and the teeth sweep scrub GIT_DIR, GIT_INDEX_FILE and GIT_WORK_TREE through git_env.

- Sites: `teeth_sweep.py:195` (`run_with_deadline(..., env=None, ...)`, the whole suite); `_mutation_runner.py:33-45`
  (`_pytest_env()` copies all of `os.environ` except `PYTEST_ADDOPTS`), used at `_mutation_runner.py:159-160` (`_run_pytest`)
  and `:276-290` (`_WarmRunner.start`).
- Failure scenario: a sweep or mutation run started from a pre-commit or pre-push hook inherits `GIT_DIR`,
  `GIT_INDEX_FILE` and `GIT_WORK_TREE` from git. Any test that spawns `git` then operates on the committing repository,
  not the sandbox. The verdict flips ("killed" for a mutant the test could not see, or "survived" when git fails), and the
  result depends on whether the run was started from a hook. `git_child_env.py` names this exact defect class, but the
  mutation path is not covered.
- Fix: build the env with `py_ci_shared._core.git.git_env(...)` in `_pytest_env()` and pass
  `env=git_env()` at `teeth_sweep.py:195`.

### SUB-3 (P2) -- `tzdata` is installed unpinned, and `pgserver` has no hash check

**Disposition:** DEFERRED -- the downloads are now bounded by timeouts; pinning tzdata and checking wheel digests needs the real versions and SHA-256 values, which cannot be written down without fetching them.

- Site: `embedded_postgres.py:337`: `pip download tzdata --no-deps --only-binary=:all: -d tmp -q` with no version.
  `fetch()` (`embedded_postgres.py:347` onward) downloads `pgserver=={version}` (pinned to `0.1.4`) with no `--require-hashes`
  and no digest check before `zipfile` extracts it and copies its binaries into the cache that `initdb`/`pg_ctl` run from.
- Failure scenario: whoever controls the index or a compromised upload of `tzdata` gets its wheel's zone files copied
  and later read by the server. The `pgserver` binaries are executed by the test run. Nothing verifies the bytes. The
  download also has no timeout, so a stalled index hangs the caller.
- Fix: pin `tzdata==<version>` and check the wheel SHA-256 against a constant in the module before extracting; add
  `timeout=` to both `subprocess.run` calls.

### SUB-4 (P2) -- `BLACK_CMD` is split with POSIX `shlex`, which breaks Windows paths

**Disposition:** RESOLVED -- BLACK_CMD is split with posix=False on Windows; tests/test_audit_20261020_subprocess.py.

- Site: `black_filtered_apply.py:63`: `shlex.split(os.environ["BLACK_CMD"])`. `shlex.split` defaults to POSIX mode, so
  backslashes are escape characters.
- Failure scenario: `BLACK_CMD='C:\tools\venv\Scripts\black.exe'` becomes `C:toolsvenvScriptsblack.exe`. The executable
  is not found, `subprocess.run` raises `FileNotFoundError`, and the gate reports a tooling failure for a correct setting.
  On Windows the only working form is to quote or use forward slashes, which nothing documents.
- Fix: `shlex.split(value, posix=(os.name != "nt"))`, or accept a JSON list. Add a test with a backslash path.

### SUB-5 (P2) -- several subprocess calls have no timeout, so a hung tool hangs the hook or gate

**Disposition:** RESOLVED -- every listed subprocess call carries a timeout; warn-only runners report skipped on expiry, mypy_gate fails, black raises, the embedded server and downloads are bounded.

- Sites: `advisory_warn.py:49` and `:58` (ruff, pip-audit); `bandit_warn.py:98`; `format_warn.py:93`;
  `vulture_warn.py:93`; `mypy_gate.py:94`; `black_filtered_apply.py:113` and `:353`; `embedded_postgres.py:337` and the
  `pgserver` download in `fetch()`.
- Failure scenario: a dependency lookup in `pip-audit` (network) or a stuck `black` stdin read blocks the commit forever
  with no output. The rest of the code bounds nearly every other call (`_core/git.py`, `api_floor.py:166`,
  `pinned_tool_versions.py:80`), so this is an inconsistent gap rather than a design choice.
- Fix: a `timeout=` on each call, and on expiry print a warning and return "skipped" for the warn-only tools
  (`advisory_warn`, `bandit_warn`, `format_warn`, `vulture_warn`), which are documented as never blocking.

### SUB-6 (Low) -- consumer `name` is used as a path component without validation

**Disposition:** RESOLVED -- same fix as DS-2.

- Site: `_consumers.py:125` (`target = dest / consumer.name`); `load_consumers` (`_consumers.py:59-84`) checks only that
  the field is present and unique.
- Failure scenario: `name = "../../x"` in `configs/consumers.toml` clones into a path outside `--dest`. The clone
  succeeds only if the target is absent or empty. The config is committed to this repo, so the author is trusted; the
  risk is an accidental name, not an attacker.
- Fix: reject names that are not `[A-Za-z0-9._-]+` and not `.`/`..` in `load_consumers`.

### SUB-7 (Low) -- `PY_CI_SHARED_GIT_TIMEOUT_S` accepts `inf` and `nan`

**Disposition:** RESOLVED -- PY_CI_SHARED_GIT_TIMEOUT_S must be finite and positive, otherwise GitError.

- Site: `_core/git.py:94-100` (`float(raw)`; the `value <= 0` check does not reject `inf` or `nan`).
- Failure scenario: `PY_CI_SHARED_GIT_TIMEOUT_S=inf` reaches `subprocess.run(timeout=inf)`, which raises `OverflowError`
  (or behaves as an immediate timeout for `nan`). Either escapes the documented `GitError` contract.
- Fix: `if not math.isfinite(value) or value <= 0: raise GitError(...)`.

### SUB-8 (Low) -- `_reap_orphans` does not check ownership, despite its docstring

**Disposition:** RESOLVED -- _reap_orphans skips directories owned by another uid.

- Site: `embedded_postgres.py:93-114`. The docstring says "other users are left alone", but the code only checks that the
  pid in `owner.pid` is gone (`psutil.pid_exists`).
- Failure scenario: a `pg-embedded-*` directory owned by another user whose recorded owner pid is dead is
  stopped with `pg_ctl -m immediate stop` and deleted. The deletion usually fails on the other user's 0700 directory
  (`ignore_errors=True`), so the practical effect is small, but the stated guarantee is false.
- Fix: skip directories whose `st_uid` is not the current uid (POSIX), and correct the docstring if that is not wanted.

### SUB-9 (Low) -- a repo's own guard scripts run as shell commands

**Disposition:** RESOLVED -- the README states that guard_population runs repository shell on whatever code it is given, so it must not run on untrusted code with secrets.

- Site: `guard_population.py:217-219`: `subprocess.run([_bash(), "-c", command], cwd=repo_root, timeout=120)`, where the
  command is parsed from the `check*.sh` file in the repository being checked.
- Failure scenario: a gate run on a PR from a fork under a privileged token runs whatever shell the PR puts in
  `tool/**/check*.sh`. This is the purpose of the gate, so it is accepted; the risk is only that it is run on untrusted
  code with secrets in the environment.
- Fix (documentation): state in the README that this gate must not run on untrusted code with secrets. No code change.

### SUB-10 (Low) -- `python -m` and `-c` put the working directory first on `sys.path`

**Disposition:** RESOLVED -- ruff, bandit, vulture, pip-audit and black run with python -I; mypy is left out because a repository-local mypy plugin must be importable from the working directory.

- Sites: `api_floor.py:165`, `ignore_ratchet.py:87` (`cwd=root`, `-m ruff`), `timezone_honest.py:114` (`cwd=root`, `-m ruff`),
  `mypy_gate.py:94`, `bandit_warn.py`/`vulture_warn.py`/`format_warn.py` (`sys.executable -m`, process cwd).
- Failure scenario: a repository containing `ruff/`, `mypy/` or `black/` at its root shadows the real tool when the gate
  runs with cwd = that repository, and the shadow runs with the gate's environment.
- Fix: pass `-I` (isolated mode) to these child interpreters, or set `cwd` to a directory the repo does not control.

### SUB-11 (Low) -- `setup_env` writes values that can break the target format

**Disposition:** RESOLVED -- setup_env refuses values with a line break or, for setx, 1024 characters or more.

- Sites: `setup_env.py:101-103` (`_environment_d_value` does not handle a newline in the path; a POSIX directory name
  may contain `\n`, which writes a second variable into `50-py-ci-shared.conf`); `setup_env.py:56` (`setx` silently
  truncates values longer than 1024 characters).
- Failure scenario: a clone under a directory containing a newline writes an extra `environment.d` line. A long
  path on Windows is truncated, so `PY_CI_SHARED_DIR` points at the wrong place and ruff silently falls back to the
  unmerged base config.
- Fix: refuse values containing `\n` or `\r`, and check `len(value) < 1024` before `setx`, printing an error.

### SUB-12 (Low) -- hook file is rewritten non-atomically

**Disposition:** RESOLVED -- install_safe_hook writes the patched hook beside the original and replaces it.

- Site: `install_safe_hook.py:88`: `hook_path.write_bytes(...)`. An interrupted write leaves a truncated hook that git
  runs for every commit.
- Fix: write to a temp file in the same directory, then `os.replace` (the pattern `_core/baseline.py:63-69` already uses).

### SUB-13 (Low) -- the clone token is in the child environment

**Disposition:** WON'T FIX -- the clone token already travels in GIT_CONFIG_* and never in argv; hiding it from the child environment needs a credential helper file with its own lifecycle, for a token that git itself must read.

- Site: `_consumers.py:93-98`. The token is passed through `GIT_CONFIG_VALUE_<n>`, not argv, so it does not show in `ps`
  output. It is still readable from `/proc/<pid>/environ` by the same user, and it appears in headers if the caller
  sets `GIT_TRACE_CURL`.
- Fix: pass the header through a `GIT_ASKPASS`-style helper file with mode 0600, or leave it as is and note it.

### SUB-14 (Low) -- captured child output is unbounded

**Disposition:** DEFERRED -- bounding the captured child output of the mutation runner means replacing communicate() with a bounded reader in the worker protocol, a redesign of _mutation_runner.

- Site: `_mutation_runner.py:159-170` and `:276-283` (`stdout=PIPE` with `communicate` and no size cap).
- Failure scenario: a mutant that makes the suite print in a loop grows the parent's memory without limit until the
  timeout fires.
- Fix: read through a bounded buffer and keep only the tail.

---

## Checked and found safe

- **No `shell=True`, `os.system` or `os.popen` anywhere in the package.** All subprocess calls use list argv.
- **Token never in argv.** `_consumers.py:93-98` passes the basic-auth header through `GIT_CONFIG_*`; `GitError` messages
  carry only `args` and stderr, not the environment.
- **`REPO_LOCATION_VARS` scrub** (`_core/git.py:38-59, 75-80`) covers the variables that retarget git (`GIT_DIR`,
  `GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_OBJECT_DIRECTORY`, `GIT_COMMON_DIR`, `GIT_QUARANTINE_PATH` and others).
  Case-insensitive via `.upper()`. `inherit_location=True` is used only by `install_safe_hook`, as documented.
- **Git argv injection**: `_consumers.py:99` puts `--branch` before the value and builds the URL as
  `https://github.com/{repo}.git`; `repo` is checked for exactly one `/`, and the URL never starts with `-`.
- **Atomic writes**: `_core/baseline.py:63-69` uses `mkstemp` in the target directory and `os.replace`.
- **Temp directories**: `embedded_postgres.py:125` and `mutation_teeth.py` use `mkdtemp`; the gate cache
  (`_core/gate_cache.py:109-112`) lives under `~/.cache` with mode 0700, not in shared `/tmp`.
- **Process-tree kill** (`_mutation_runner.py:117-149`): POSIX `killpg` runs on a child that is still unreaped, so its
  pid cannot have been reused. Windows `taskkill` is guarded by `poll() is None`.
- **`_run_probe_in_a_copy`** (`checkout_resolution.py:78-103`): the copy path is fixed under the caller's `workdir`, and
  `copytree` ignores symlinks by default.
- **`setup_env` plist and shell profile**: XML escaping for `&<>` in element text is sufficient; the profile export uses
  `shlex.quote`.
- **`safe_precommit`** copies unrestored patches to `.git/safe-precommit-unrestored/`; nothing is silently dropped.
- **Consumer URL**: `_consumers.py:87-107` runs `git clone` with `--depth 1 --single-branch`, `GIT_TERMINAL_PROMPT=0`,
  and a 600 s timeout.
- **`api_floor` and `ci_install_covers_entry_imports`**: the file list is passed after `-c <script>`, so a file named
  `-x` is read as data, not as a Python option.
- **`pinned_tool_versions.py:79-81`** (`nosec B603`): fixed argv plus `sys.executable`, with a 60 s timeout.

## Not covered in depth

- `mutation_teeth.py` and `_mutation_sweep.py` (sandbox copy, symlink handling with `symlinks=False`, which follows
  symlinked files into the sandbox).
- `script_entry_points.py` and `import_side_effects.py` execute repository modules by design; their env handling was
  read but not traced.
- `ci_health.py` `gh api` path construction (the path is internal; no user input was traced to it).
- No code was executed, so F1 and F4 are reasoned from documented behaviour, not reproduced.
