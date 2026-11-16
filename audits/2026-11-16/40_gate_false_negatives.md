# 40 gate false negatives: round 2026-11-16 (T)

Commit audited: `48e49b1` on master (other sessions' uncommitted edits were in the tree; the uncommitted 40-S5 `_unparsable_findings` fix is live in it). Interpreter: Python 3.14.3, Windows.

## Scope and method

Read first: `audits/2026-11-11/40_gate_false_negatives.md`, `audits/2026-11-10/40_*.md`, the 2026-11-07 rows 40-F1..F13 and the TRACKER rows 40-*. Nothing already dispositioned is re-proposed (list at the end).

26 gates were attacked (none of the CUDA/GPU/njit family): `bare_except`, `broad_except_swallow`, `swallowed_exceptions`, `hardcoded_seed_in_library`, `naive_utcnow`, `systemexit_in_library`, `unsafe_deserialization`, `predictable_tmp_path`, `rollback_then_continue`, `module_reload_safety`, `possibly_unbound_local`, `lock_held_across_io`, `stdlib_json_ban`, `sentinel_or_fallback`, `check_then_act_outside_lock`, `env_flag_parsing`, `mutable_default`, `missing_network_timeout`, `credential_shaped_log_arg`, `console_unicode`, `fail_open_handlers`, `identity_comparisons`, `no_xfail_to_defer`, `skip_as_pass`, `wallclock_test_waits`, `git_child_env`, `guard_without_fixture`, `repo_hygiene`. About 520 adversarial inputs (aliased imports, nested def/class/lambda, async, f-string, walrus, match, `except*`, 3.12 generics, line continuation, CRLF+BOM, lone-CR line ends, NUL byte, latin-1 cookie, a package directory named `build`/`env`/`scripts`/`testing`, an extensionless script, `.PY`, a suppression marker on the wrong line). Each case is a file written to a fresh temp dir and run through the gate's public `find_*`/`scan_*` function; the harness prints `PASSED(MISS)` when a real defect produced 0 findings.

Scripts and raw outputs (scratch, not in the repo): `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\40\` : `h1.py` (harness), `b1.py`/`b1.out` (first pass, partly invalid: `find_swallowed_exceptions` and `find_sentinel_or_fallback` return a tuple so every case counted as flagged; redone in `b5.py`), `b2.py`, `b3.py`, `b5.py`, `b7.py`, `b8.py` (per-gate cases), `g1.py` (git-mode corpus), `b4.py` (floor-less text ratchets), `b6.py` (file-level cases), `b9.py` (repo_hygiene), `e1.py`/`e1.out` (pass-by-emptiness sweep: every `find_/assert_/collect_` taking one path, called with a missing dir, an empty dir and a dir with no `.py`).

Each case was then checked against the gate's docstring. A miss the docstring states as out of scope is not raised (see "Considered and not raised"); a miss that contradicts the docstring, or that is a realistic shape of the defect the gate exists for, is a finding.

Not covered: CUDA/GPU/njit gates (dimension 53), the dart/nginx/sql/workflow text gates beyond the emptiness sweep, mutation tooling, `run-all` process-pool paths.

## 40-T1 (P2) code_audit scanners prune tracked `build`/`dist`/`env`/`venv` directories that the shared core keeps
- **Evidence:** `code_audit/_base.py:108-131` (`_DEFAULT_EXCLUDE_DIRS` holds `build`, `dist`, `env`, `.env`, `venv`) and `_base.py:231-258` (`_iter_py_files`, an `os.walk` that prunes those names with no git knowledge). The shared core deliberately keeps a TRACKED `build/`/`dist/`/`venv/` package (`_core/corpus.py` `_SOFT_EXCLUDE` and `iter_files`, comment "A tracked `src/pkg/build/m.py` is a real package"), but code_audit does not, and `env` is not even in the core's list. Demonstration (`g1.py`): a git repo with `src/app/build/m.py`, `src/app/env/m.py`, `src/app/ok/m.py`, `src/app/dist.py`, each holding a bare `except:` and `datetime.datetime.utcnow()`, all four `git add`ed:
  ```
  tracked: ['src/app/build/m.py', 'src/app/dist.py', 'src/app/env/m.py', 'src/app/ok/m.py']
  naive_utcnow (core): build/m.py, dist.py, env/m.py, ok/m.py   -> 4 findings
  bare_except (code_audit): ['src/app/dist.py', 'src/app/ok/m.py']   -> build/ and env/ missed
  ```
  All 105 registered code_audit scanners share `_iter_py_files`, so every one of them skips a tracked `src/pkg/env/` (an RL `env` package, a config `env` package) or `src/pkg/build/` (a build-tool package).
- **Impact:** 105 scanners are silent on a whole production subpackage whose name is `env`/`build`/`dist`/`venv`, while the 60-odd core gates read it: the two families disagree about what the corpus is. No consumer tracked such a directory when measured (`git ls-files` on pyutilz, mlframe, llm_bench: none), so this is latent.
- **Proposed fix:** in `_iter_py_files`, in a git work tree keep a tracked file under a `_SOFT_EXCLUDE` name exactly as `_core.corpus.iter_files` does (reuse `iter_files`, which the code_audit walk was written to avoid for speed; or one `git ls-files --cached` for the pruned names only). Drop `env`/`.env` from the untracked-prune set only when tracked. BEHAVIOUR CHANGE (new findings on a repo that tracks such a directory).
- **Disposition:** BUILT, HARD: `tracked_env_packages` (a new gate, `src/py_ci_shared/tracked_env_packages.py`) reports every tracked Python file under an env, venv, build or dist directory, which the code_audit walk prunes. The code_audit pruning stays for speed, as the owner's cost decision chose. Canaries and tests `tests/test_tracked_env_packages.py`. Behaviour change: a consumer that tracks such a package gets a finding, which it renames or moves.

## 40-T2 (P2) four text-ratchet gates have no file floor, so a wrong or empty root passes
- **Evidence:** `skip_as_pass.py:151-165` (`find_skip_as_pass`, `assert_no_new_skip_as_pass(repo_root, baseline_dir)`), `wallclock_test_waits.py:93-124`, `git_child_env.py:170-189`, `guard_without_fixture.py:41-63`. None takes `min_files`; they call `text_scan(root, ..., under=...)`, which keeps only files whose FIRST path component is in `under` (`_text_ratchet.py:46-47`), and `enforce_findings` with no accepted violations returns "no new violations". `b4.py`, on a repo with `scripts/check.sh` (prints SKIPPED, exits 0), `tests/test_a.py` (a `sleep(5)` and an unscrubbed `git` spawn):
  ```
  --- root = real repo root
  skip_as_pass: ['scripts/check.sh: echo "SKIPPED: no X"']
  git_child_env: ["tests/test_a.py: subprocess.run(['git','status'])"]
  --- root = WRONG (repo/src)
  skip_as_pass: no new violations (0 accepted, baselined)    -> assert PASSED silently
  wallclock_test_waits: no new violations ...                -> PASSED silently
  git_child_env: no new violations ...                       -> PASSED silently
  ```
  The `e1.py` sweep reproduces it for an empty directory and one with no `.py` (`assert_no_new_skip_as_pass`, `assert_no_new_wallclock_test_waits`, `assert_no_new_unscrubbed_git_spawns`, `assert_every_guard_named_by_a_test`: `PASS(no raise)`). Every other corpus gate has `min_files` and the entry contract test enforces it (`CLAUDE.md`, "Adding a gate"); these four are text-corpus gates the contract does not cover.
- **Impact:** `repo_root=Path(__file__).parent` after a test file is moved, or `REPO/"src"`, makes the gate green forever with no signal.
- **Proposed fix:** add `min_files: int = 1` to the four `assert_*` and `find_*`, counted over the files that survive the `under`/test-path filter (a repo with no `scripts/` at all sets `min_files=0` explicitly). BEHAVIOUR CHANGE (a repo with none of the scanned directories turns red until it sets `min_files=0`); CHANGELOG entry with `What to do:`.
- **Disposition:** FIXED in this commit. text_ratchet require_text_floor raises CorpusError below min_files (default 1) for the four text ratchets; min_files=0 opts out. Red run: tests/test_gate_fn_2026_11_16.py (ImportError, then the T2 cases). changelog Behaviour change (1).

## 40-T3 (P2) `repo_hygiene.assert_repo_hygiene`: a mistyped `workflows_dir` passes (floor defaults to 0)
- **Evidence:** `repo_hygiene.py:296-300` (`find_unguarded_numeric_gates` returns `[]` when `workflows_dir` is not a directory) and `repo_hygiene.py:335-344,376-379` (`min_files: int = 0`; the `workflows_dir` branch only reports "no workflow files" when the directory EXISTS). `b9.py`, in a git repo with a numeric-compare workflow at `.github/workflows/ci.yml`:
  ```
  real dir: ['ci.yml:8: `if [ $COUNT -gt 3 ]; then exit 1; fi` compares $COUNT numerically without proving it is non-empty first ...']
  typo dir (.github/workflow): []
  assert_repo_hygiene(d, workflows_dir=d/".github/workflow") -> PASSED
  assert_repo_hygiene(d, workflows_dir=d/".github/workflows") -> Failed 1 repo hygiene problem(s)
  ```
- **Impact:** the unguarded-numeric-gate check, the one that exists because a gate "reports success exactly when its input broke", is switched off by a one-letter typo in a path argument.
- **Proposed fix:** when `workflows_dir is not None` and it is not a directory, add a problem ("workflows_dir does not exist"). ADDITIVE for any caller whose path is right; BEHAVIOUR CHANGE only for a caller already passing a wrong path (which is the bug).
- **Disposition:** FIXED in this commit. repo_hygiene.find_workflows_dir_problems reports a workflows_dir that does not exist; assert_repo_hygiene includes it. changelog Behaviour change (1).

## 40-T4 (P2) `credential_shaped_log_arg` sees only a bare name or attribute passed to a logger
- **Evidence:** `code_audit/credential_logging.py:20-33` (`_CREDENTIAL_NAME_RE`, `_looks_credential_shaped` accepts only `ast.Name`/`ast.Attribute`), `:50-58` (`_is_logger_receiver`: name must end in `log`/`logger`), `:84-87` (positional args and keyword values only), `:89-91` (a 3-line window containing `mask`/`redact`/`scrub` anywhere exempts). `b5.py`, all returned 0 findings:
  - `log.info(f"token={token}")`, `log.info("t={}".format(token))` (the two most common forms: a JoinedStr/Call argument is never inspected);
  - `log.info("t=%s", d["password"])`, `log.info("t=%s", d.get("api_key"))`, `log.info("x", extra={"token": token})`;
  - `log.info("h=%s", headers)`, `log.info("dsn=%s", dsn)`;
  - names outside the regex: `pwd`, `jwt`, `private_key`, `authorization`, `tokens` (plural), `authToken`/`apiKey` (camelCase);
  - receiver `lg.info(token)` and `_L.info(token)` (not ending in `log`/`logger`);
  - a comment `# note: we do not mask this one, yet` three lines above the call exempts it.
  Control `log.info("t=%s", token)` is flagged.
- **Impact:** this is the only gate for credentials reaching logs; the f-string spelling is the dominant one in modern code, so the P2 finding class it was written for passes whenever the author formats the message.
- **Proposed fix:** walk the argument expressions (`ast.walk` over each arg, including `JoinedStr` values, `.format` args, `Subscript` constant keys, `.get("key")`, dict-literal keys in `extra=`); widen the vocabulary (`pwd`, `jwt`, `private_key`, `authorization`, plural, camelCase split via the `_CAMEL` helper `sentinel_or_fallback.py` already has); take the redaction hint from the call's own arguments or a name assigned from a redact call, not any text in a 3-line window. BEHAVIOUR CHANGE (new findings; baseline refresh).
- **Disposition:** RESOLVED in the commit whose subject starts `40-T4:`. `credential_logging.py` walks every logged argument (f-strings, `.format`/`%`, subscript and `.get` string keys, `extra=` dict keys), widens the vocabulary (camelCase split, plurals, `pwd`, `jwt`, `private_key`, `authorization`), and takes the redaction hint from the call's own arguments or a code assignment in the 4 lines above, never from comments. Red run before the fix: 8 of 12 cases failed (`tests/code_audit/test_credential_logging.py::test_40_t4_*`). Changelog Behaviour change (40-T4). No repo-local baseline holds this check's keys; consumers refresh theirs with the review the changelog asks for.

## 40-T5 (P2) `missing_network_timeout` ignores sessions, import aliases and `timeout=None`
- **Evidence:** `code_audit/network_timeout.py:23-30,83-98`. A call is judged only when its ROOT name is in `{"requests","grequests","httpx","urllib","urlopen"}` or a variable assigned from a call that itself carried `timeout=` (`_timeout_preconfigured_vars`: the logic is inverted for sessions, a session built WITHOUT a timeout is never flagged). `b5.py`, 0 findings for each:
  ```
  s = requests.Session(); s.get(url)            self.session.get(url) (session built in __init__)
  import requests as rq; rq.get(url)            from requests import Session; Session().get(url)
  requests.get(url, timeout=None)  (infinite)   requests.get(url, timeout=0)
  async with httpx.AsyncClient() as c: await c.get(url)
  import urllib.request as ur; ur.urlopen(url)  (flagged: urlopen is special-cased)
  ```
  Also the preconfigured set is file-wide by variable name: `c = httpx.Client(timeout=5)` in `a()` exempts `c = requests.Session(); c.get(url)` in `b()`.
- **Impact:** `requests.Session().get()` with no timeout hangs forever and is the usual production shape; the 2026-07-21 audit's "most High-finding-dense domain" is only half covered.
- **Proposed fix:** resolve receivers through `ImportAliases` (`requests.Session()`, `httpx.Client()` assigned to a name or `self.x` WITHOUT `timeout=` make that name a flagged receiver), treat `timeout=None` as missing, scope the preconfigured set per function. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit whose subject starts `40-T5:`. `network_timeout.py` resolves callees through `ImportAliases`, judges session and client receivers by their own binding (per function, `self.x` file-wide), and treats `timeout=None` as missing. Red run before the fix: 7 of 12 cases failed (`tests/code_audit/test_network_timeout.py::test_40_t5_*`). Changelog Behaviour change (40-T5).

## 40-T6 (P2) `console_unicode`: any mention of `reconfigure(encoding` exempts the whole module
- **Evidence:** `code_audit/console_unicode.py:53-64`, a substring test on the raw source: `"reconfigure(encoding" in source and (".stdout.reconfigure(encoding" in source or ".stderr.reconfigure(encoding" in source)`. `b5.py`, each file below then has `print('→ done')` and returns 0 findings (the bare `print('→ done')` control is flagged):
  ```
  # TODO: sys.stdout.reconfigure(encoding='utf-8')        (a comment)
  X = 'sys.stdout.reconfigure(encoding)'                  (a string)
  sys.stderr.reconfigure(encoding='utf-8')                (stderr only, printing to stdout)
  def unused(): sys.stdout.reconfigure(encoding='utf-8')  (never called)
  ```
  The scanner also reads only string-literal and f-string positional args (`_str_arg_values`): `print('→ ' + x)`, `print('→ %s' % x)`, `print('→ {}'.format(x))`, `print(a, sep=' → ')`, `print(a, end='→')`, `sys.stdout.write('→')`, `click.echo('→')`, `Console().print('→')`, `raise ValueError('→ bad')` all return 0.
- **Impact:** a Windows cp1251/cp1252 console `UnicodeEncodeError` (the failure the scanner names) reaches users through the unscanned spellings, and a commented-out reconfigure silences the module.
- **Proposed fix:** make the reconfigure test an AST test (a real `<x>.stdout.reconfigure(encoding=...)` call, stdout for print/stdout.write, stderr for stderr output); extend `_str_arg_values` to `BinOp(Mod/Add)` and `.format` receivers and to `sep=`/`end=`; add `sys.stdout.write`/`click.echo`. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit whose subject starts `40-T6:`. The reconfigure exemption is an AST test (`_live_reconfigured_streams`: a real `sys.<stream>.reconfigure(encoding=...)` call, not inside an unreferenced def), matched per stream to each console write; print argument forms widened (`+`, `%`, `.format`, `sep=`/`end=`), plus `sys.stdout.write`, `click.echo`, `Console().print`. Red run before the fix: 6 of 16 cases failed (`tests/code_audit/test_console_unicode.py::test_40_t6_*`). Changelog Behaviour change (40-T6). A package `__init__.py` reconfigure still exempts all of its submodules, as before.

## 40-T7 (P2) `bare_except`/`broad_except_swallow`: a handler that re-raises on ONE path counts as re-raising; `except*` and tuples are not seen
- **Evidence:** `code_audit/bare_except.py:20-33` (`_is_bare_except`: any bare `raise` anywhere under the handler, including under an `if` or in a nested `def`, returns False; a tuple type returns False; only `ast.Name` `BaseException`), `:62` (`isinstance(node, ast.Try)`: `ast.TryStar` is skipped). `broad_except.py:34-40` (`_has_raise`) is the same any-raise walk. `b1.py`/`b2.py`:
  ```
  except* BaseException: pass                      -> 0 (bare_except), 0 (broad: except* Exception: pass)
  except (BaseException, ValueError): pass         -> 0
  except builtins.BaseException: pass              -> 0
  except:  if x: raise                             -> 0   (swallows whenever x is false)
  except:  def h(): raise ; pass                   -> 0   (the raise never runs)
  ```
  The docstring (`bare_except.py:49-52`) promises only a handler that "immediately re-raises (bare `raise`)".
- **Impact:** `except: if debug: raise` is a common shape and swallows `KeyboardInterrupt`/`SystemExit` in the default branch, and neither bare_except nor broad_except nor (per its own rule) swallowed_exceptions reports it.
- **Proposed fix:** exempt only a handler whose LAST top-level statement is a bare `raise` or whose `raise` is not under a conditional/nested def (reuse the `no raise reachable` logic `swallowed_exceptions` has); visit `ast.TryStar` (the other 8 modules already use `getattr(ast, "TryStar", ...)`); treat a tuple containing `BaseException` or `builtins.BaseException` as bare. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in this commit. A re-raise exempts a bare_except handler only as its last top-level statement; a tuple or dotted `BaseException` counts as bare; broad_except treats a conditional re-raise as a swallowing path. `except*` was already visited through `TRY_NODES`. Red tests: `tests/test_gate_fn_2026_11_16.py` (t7). Changelog Behaviour change (40-T7).

## 40-T8 (P3) `mutable_default` visits only `def`, and recognises few mutable defaults
- **Evidence:** `code_audit/mutable_defaults.py:56-90` (`_MUTABLE_FACTORIES`: defaultdict, OrderedDict, Counter, ChainMap, deque, ndarray, DataFrame, Series) and `:440-470` (only `FunctionDef`/`AsyncFunctionDef`). `b5.py`, 0 findings: `f = lambda x=[]: x`; `def f(x=[0]*3)`; `def f(x=[]+[])`; `def f(x=[i for i in r])`, `{k: 1 for k in ..}`, `{k for k in ..}`; `def f(x=bytearray())`; `np.zeros(3)`, `np.array([1])`; `array.array('i')`; `io.StringIO()`; `([],)`; a user class instance `C()`. Control `[]`, `set()`, `defaultdict(list)`, `collections.deque()`, `pd.DataFrame()` are flagged.
- **Impact:** same shared-state-across-calls bug, different spelling.
- **Proposed fix:** visit `ast.Lambda`; treat a `ListComp`/`DictComp`/`SetComp`, a `BinOp` with a list/dict/set operand, `bytearray`, `array.array`, `StringIO`/`BytesIO` and numpy constructors (`array`, `zeros`, `ones`, `empty`) as mutable. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in this commit. `mutable_default` visits lambdas, recognises list/dict/set comprehensions, a list or set operand of a BinOp, `bytearray` and the numpy constructors (`array`, `zeros`, `ones`, `empty`). Red tests: `tests/test_gate_fn_2026_11_16.py` (t8). Changelog Behaviour change (40-T8).

## 40-T9 (P3) `wallclock_test_waits` default text mode misses `from time import sleep`
- **Evidence:** `wallclock_test_waits.py:39-43` (`WAIT_PATTERNS` match the literal text `time.sleep(` / `asyncio.sleep(`); the docstring says the alias-proof rule exists only under `python_mode="ast"`. `b4.py`: `tests/test_a.py` with `from time import sleep` and `sleep(5)`: `find_wallclock_test_waits(d)` -> `[]`; `python_mode="ast"` -> `['tests/test_a.py: sleep(5)']`.
- **Impact:** every consumer baseline was recorded in text mode, so the default (the one that is enabled) never sees the alias; the ast mode is opt-in and a baseline refresh under it would add keys.
- **Proposed fix:** default to the AST rule for files that parse and keep the text rule only as the fallback for an unparsable file (the ast mode already falls back). BEHAVIOUR CHANGE (baseline keys can change).
- **Disposition:** RESOLVED in this commit. `find_wallclock_test_waits` and `assert_no_new_wallclock_test_waits` default to `python_mode="ast"`. This repo's baseline is unchanged (its one key is a real `time.sleep` call). Consumer baselines migrate per the changelog Behaviour change (40-T9): new keys get a note, keys no longer found are removed. Red test: `tests/test_gate_fn_2026_11_16.py` (t9).

## 40-T10 (P3) `skip_as_pass` recognises only a message that begins with the word "skipped"
- **Evidence:** `skip_as_pass.py:53` (`_PRINTS_SKIPPED` ends with `(?i:skipped)\b`). `b4.py`: a script with `echo "SKIPPED: no X"` then `exit 0` is flagged; the same script with `echo "SKIP: no X"` or `echo "Skipping the check"` followed by `exit 0` is not (`scripts/check2.sh`, `check3.sh`: not in the findings).
- **Impact:** "Skipping ..." is the commonest wording of the pattern the gate bans (a check that did nothing and exits 0).
- **Proposed fix:** accept `skip(?:ped|ping|s)?\b` and `SKIP:`. BEHAVIOUR CHANGE.
- **Disposition:** FIXED in this commit. _PRINTS_SKIPPED and the _may_print_skipped prefilter accept skip, skipping and skips. changelog Behaviour change (2).

## 40-T11 (P3) `unsafe_deserialization`: any `save`/`dump` call, or any call whose name contains `verif`, exempts a later `pickle.loads`
- **Evidence:** `unsafe_deserialization.py:107-108` (`_PRODUCERS = {"dump","dumps","save","to_pickle"}`, `_VERIFY = sha256|hmac|verif|sidecar`), used by `_verified_by_enclosing`/`_is_verifying_call` (`:442`, `:526`). `b2.py`, 0 findings each, though the loaded bytes are the request body:
  ```
  def f(req, store): store.save(req); return pickle.loads(req.body)
  def f(req): verify_user(req); return pickle.loads(req.body)
  def f(req): log = pickle.dumps(1); return pickle.loads(req.body)
  ```
  (`k = hashlib.sha256(b"x").hexdigest(); pickle.loads(req.body)` is also exempt.) The docstring calls the rule "advisory-grade by construction" for hashes computed elsewhere; these exemptions need no data flow at all.
- **Impact:** the P-class "downloaded bytes are code" passes with an unrelated call in the same function.
- **Proposed fix:** require the verify/produce call to receive the loaded variable (same name among its arguments), the way `_core` taint helpers in `provenance_flow` do. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the "unrelated verify or produce call" tests. `_names_of` now yields dotted chains and `_touches` requires the verifier's arguments to share one with the loaded value or with a name tied to it by assignment. Red run: 2 of the 4 unrelated cases exempted the load (`store.save(req)`, `verify_user(req)`); the other two were already flagged. Changelog Behaviour change (40-T11).

## 40-T12 (P3) `possibly_unbound_local`: walrus binding under a boolean chain, and `with` blocks that swallow
- **Evidence:** `possibly_unbound_local.py` docstring: "a name bound ... by a `with` is treated as bound afterwards". `b3.py`, 0 findings: `if c and (y := g()): pass` then `return y`; `with contextlib.suppress(Exception): y = g()` then `return y`; `with pytest.raises(ValueError): y = g()` then `return y`. (`if/elif` without else, `try/except pass`, `match` without wildcard, async, class method are flagged.)
- **Impact:** `suppress` and `pytest.raises` exist to swallow; the name is unbound exactly then.
- **Proposed fix:** treat a `with` whose item is `suppress(...)`/`pytest.raises`/`pytest.warns`-free swallowing context as "maybe"; bind a walrus only on the path that evaluates it. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the walrus and swallowing-with tests. A walrus under `and`/`or` or in a conditional expression is bound only on the path that evaluates it (`_split_stores`); `suppress`/`pytest.raises`/`assertRaises` blocks merge their body with the skipped path; the scope pre-check now admits walrus and swallowing blocks. Red run: 5 of the new cases gave 0 findings. Changelog Behaviour change (40-T12). Note: `test_a_baseline_accepts_known_findings_and_fails_new_ones` fails independently of this change (baseline entries stay NEEDS-JUSTIFICATION after refresh); not touched here.

## 40-T13 (P3) `lock_held_across_io`: import aliases, subscripted locks, `os.system`, `aiohttp`
- **Evidence:** `lock_held_across_io.py:105-150` (`is_thread_lock`, `io_label`). `b3.py`, 0 findings: `from urllib.request import urlopen as uo` then `with self._lock: uo(url)` (the docstring says urlopen is matched by name; an alias breaks it); `from threading import Lock as L2; lk = L2()` then `with lk: time.sleep(1)`; `with self._locks[k]: time.sleep(1)`; `with self._lock: os.system("ls")`; `async with self._lock:` around `aiohttp.ClientSession().get(...)`; `with self._guard:`. Flagged: `requests.get`, `urllib.request.urlopen`, `subprocess.run`, `time.sleep`, write-mode `open`, `asyncio.sleep` under an async lock.
- **Proposed fix:** resolve the callee through `ImportAliases.qualified_name`; add `os.system`/`os.popen`, `aiohttp`; accept a `Subscript` whose base is lock-like. Explicit `acquire()`/`release()` stays out of scope (documented). BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the alias, subscript and os.system/aiohttp tests. `io_label` judges the resolved callee's last segment, `_module_lock_names` resolves lock constructors through `ImportAliases`, `_last_segment` looks through a subscript, and the plural lock-family names count. Red run: 5 of 5 new cases gave 0 findings. Changelog Behaviour change (40-T13). Explicit `acquire()`/`release()` stays out of scope.

## 40-T14 (P3) `sentinel_or_fallback` default names omit common siblings
- **Evidence:** `sentinel_or_fallback.py:36-39` (`DEFAULT_SENTINEL_NAMES`). `b5.py`: `x.max_new_tokens or 100` returns 0 findings (`max_tokens`, `max_output_tokens`, `timeout`, `seed`, `n_jobs`, `retries`, `temperature` or-forms are flagged). Other zero-is-a-value settings absent: `max_new_tokens`, `num_workers`, `n_estimators`, `max_depth`, `top_n`, `min_samples`, `learning_rate`.
- **Proposed fix:** add the names; the `names=` argument stays. BEHAVIOUR CHANGE.
- **Disposition:** FIXED in this commit. Seven names added to DEFAULT_SENTINEL_NAMES. changelog Behaviour change (2).

## 40-T15 (P3) `env_flag_parsing`: `from os import environ as E`, and `int(...)` compared with a number
- **Evidence:** `env_flag_parsing.py:68` (`self.environ_names: set[str] = {"environ"}`: only the literal name and `env = os.environ` assignments are tracked, an import alias is not) and the 40-F10 `bool(int(...))` rule only. `b5.py`, 0 findings: `from os import environ as E; if E.get("APP_X") == "1"`; `int(os.getenv("APP_X","0")) > 0`; `int(os.getenv("APP_X","0")) == 1`; `{"1": True}.get(os.getenv("APP_X"), False)`. Flagged: plain `from os import environ`, `from os import getenv`, `import os as _os`, walrus, constant key, f-string key, `in os.environ`.
- **Proposed fix:** seed `environ_names` from `ImportAliases` (any alias of `os.environ`); treat an `int(<env read>)` operand of a `Compare` as a flag read. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the alias and int-compare tests. `_Ctx` seeds `environ_names` and `getenv_names` from `ImportAliases`; `int(<read>)` compared with a number is a flag read, and that int() operand is not a numeric use. Red run: 4 of 4 new cases gave 0 findings. Changelog Behaviour change (40-T15).

## 40-T16 (P3) `fail_open_handlers.finite_guarded_reject` sees only a `continue` or a call named `reject*`
- **Evidence:** `fail_open_handlers.py:420-440` (`_is_finite_test`, `_is_finite_guarded_reject`: "whose body rejects (`continue`, or a call naming `reject`)"). `b8.py`: control (`for x in xs: if math.isfinite(x) and x > thr: continue`) is flagged; `if math.isfinite(x) and x > thr: return False`, and `... : raise ValueError("too big")` in a predicate both return 0, though NaN/inf skip the rejection identically.
- **Proposed fix:** count `return False/None/0`, `raise`, `break` and `.remove/.discard` as rejects. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED. finite_guarded_reject counts `return` of False/None/0 (or bare), `raise`, `break`, `continue`, `.remove`/`.discard` and `reject*` calls as rejects. Red tests: `tests/test_fail_open_handlers.py::test_a_finite_guarded_reject_counts_return_raise_break_and_remove` (6 cases, 6 failed before the fix). CHANGELOG: Behaviour change (40-T16). Commit: see git log (`40-T16`).

## 40-T17 (P3) `no_xfail_to_defer`: `skipif(True, reason="bug")` is an unconditional skip the gate does not judge
- **Evidence:** docstring "`skipif` (a condition on the environment) ... not judged". `b7.py`: `@pytest.mark.skipif(True, reason="bug")` and `@pytest.mark.skipif(1 == 1, reason="bug in code")` -> 0 findings, while `@pytest.mark.skip(reason="TODO fix later")` and `pytest.skip("flaky bug")` are flagged. `skipif` with a constant-true condition is `skip` by another spelling.
- **Proposed fix:** evaluate a literal/constant condition (`True`, `1`, `1 == 1`) and judge it as `skip`. BEHAVIOUR CHANGE.
- **Disposition:** FIXED in this commit. no_xfail_to_defer judges pytest.mark.skipif with a constant-true condition (literal, or a comparison of two literals) as an unconditional skip; environment conditions stay unjudged. changelog Behaviour change (2).

## 40-T18 (P3) extensionless Python scripts and an upper-case `.PY` are not scanned by code_audit
- **Evidence:** `code_audit/_base.py:107` (`_PY_EXTS = (".py",)`, `name.endswith`, case-sensitive) and `_core` `scan_python` pattern `("*.py",)`. `b6.py`, a tree with `bin/deploy` (`#!/usr/bin/env python3`, `datetime.utcnow()` and a bare `except:`) and `upper.PY` (same body):
  ```
  bare_except: ['cr.py', 'latin.py']              -> neither bin/deploy nor upper.PY
  naive_utcnow (core): ... upper.PY:2 (found, include_stubs), bin/deploy not scanned
  run_all_checked files: ['cr.py', 'latin.py', 'nul.py', 'syn.py']
  ```
  The core finds `upper.PY` only for gates that pass `include_stubs=True`; `hardcoded_seed_in_library` and `possibly_unbound_local` do not.
- **Impact:** CLI scripts without an extension (`bin/*`, `scripts/deploy`) are real shipped Python.
- **Proposed fix:** case-fold the extension in code_audit; optionally treat an extensionless file with a `python` shebang as a source file in `iter_files`. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED (732c2d4): the extensionless-script half is built as a shebang rule in code_audit (extensionless files with a python shebang are scanned), and the upper-case `.PY` half was fixed earlier in this round.

## 40-T19 (P3) `systemexit_in_library` does not see an import-time exit in a library module
- **Evidence:** `systemexit_in_library.py` docstring: "inside a function". `b2.py`, 0 findings: a module with no `__main__` guard containing `if not sys.argv: sys.exit(1)` at module level; `lambda: sys.exit(1)`; `os.abort()`; `class A: sys.exit(1)`. Importing such a module from a worker thread or a test runner kills the importer, the failure the gate's docstring describes (a helper reachable from a thread).
- **Proposed fix:** for a module with no `__main__` guard report an exit at module or class-body level and in a lambda; add `os.abort`. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the import-time tests. Owner decision (this round): a library module is a module with no `__main__` guard that is not a `__main__.py`; an exit at module or class level, or in a lambda, runs at import and is a hard finding; `os.abort()` is added. Red run: 4 of 4 import-time cases gave 0 findings. Changelog Behaviour change (40-T19).

## 40-T20 (P3) `hardcoded_seed_in_library`: unfolded constant shapes and unrecognised parameter names
- **Evidence:** `hardcoded_seed_in_library.py:81-110` (`_int_value` folds `+ - * // %`, not `**`) and `:49` (`_SEED_KEYWORDS`). `b2.py` (inside a function that has its own `seed=None`), 0 findings: `M(random_state=2**5)`, `M(random_state=np.int64(3))`, `random.Random("abc")`; and in a function whose seed parameter is `random_generator`, `rs`, `seeds` or arrives via `**kwargs`: `M(random_state=3)`. Flagged: `default_rng(42)`, `RandomState(42)`, aliased `dr(7)`, `np.random.seed(0)`, `R(random_state=0)`, `**{"random_state": 1}`, lambdas, nested defs, methods, async, a `random_state=hex` literal.
- **Proposed fix:** fold `**` and `<<`, accept `np.int64/int32(<int>)`, a string seed, and extend the "own seed parameter" test to `random_generator`, `generator`, `rs`, `seeds`. BEHAVIOUR CHANGE.
- **Disposition:** RESOLVED in the commit that adds the folded-seed tests. `**` and `<<` fold (exponent 0 to 64), `np.<int type>(<int>)` unwraps, a string-literal seed to a seeder is a finding, and `random_generator`/`generator` join the seed parameter names. Red run: 7 of 8 new cases gave 0 findings; `rs` was already recognised, so the audit's `rs` case was not a miss in the tree. `**kwargs` seeds are not added. Changelog Behaviour change (40-T20).

## Pass-by-emptiness sweep (summary)

`e1.py` called 400-odd public `find_/assert_/collect_` functions that take one path with a missing directory, an empty directory and a directory with no `.py`. A missing root raises `CorpusError` for every `scan_python`-based gate. What passes silently is in 40-T2 (four text ratchets, empty and no-`.py` root) and 40-T3 (`repo_hygiene` mistyped `workflows_dir`). Every other `PASS(no raise)` row in `e1.out` is a `find_*` (no floor by contract) whose `assert_*` wrapper enforces `min_files`/`min_workflows` (verified in source for `workflow_runner_labels`, `coverage_config_parity`, `alembic_concurrently`, `ci_install_covers_conftest`, `pytest_addopts_path_runs`, `phantom_code_references` `assert_no_stale_absolute_line_citations`, `sibling_floor_skew`), or a gate whose subject is a file that may legitimately be absent. A config glob that matches nothing raises `ConfigError` (`_core/config.py:266-269`).

## Considered and not raised

| candidate | evidence that rejected it |
|---|---|
| `hardcoded_seed_in_library` ignores `default_rng(42)` in a function with no seed parameter | docstring: "a function without such a parameter may hard-code a seed (that is a decision)". My first run lacked the parameter; with `seed=None` it is flagged. |
| `predictable_tmp_path` ignores literal `/tmp/x`, `/var/tmp`, `/dev/shm`, `C:/Temp` | the rule is "a path built under `gettempdir()`" (docstring lines 3-9); literals are `machine_specific_paths` territory. |
| `rollback_then_continue` baseline `add`+`commit` loop passes | docstring exempts a try whose body commits its own item, and says "false negatives are accepted". The 2026-11-07 40-F6 row covers receiver names. |
| `check_then_act_outside_lock` misses `set.add`/`list.append`/`setdefault` acts, lazy singletons, `try/except KeyError`, a module that starts threads but builds no Lock | already tracked 40-F7 (2026-11-07, RESOLVED partial); the no-Lock case belongs to `module_cache_thread_safety` by docstring. |
| `swallowed_exceptions` skips directories named `scripts`, `testing`, `examples`, `benchmarks`, `probes`, `bench` anywhere in the path (mlframe ships `src/mlframe/testing/parametric.py`) | `_DEFAULT_EXCLUDE` (`swallowed_exceptions.py:56`) and the docstring "test, bench and script directories excluded"; configurable via `exclude_parts`. Same for `sentinel_or_fallback`, `hardcoded_seed_in_library` (`tests/`, `_benchmarks/`). |
| `swallowed_exceptions` ignores `sqlite3.Error`, `requests.RequestException` | already WON'T FIX in 40-F2 (2026-11-07). |
| `unsafe_deserialization` ignores `eval`, `__import__("pickle")`, `getattr(pickle, "loads")`, keras `load_model`, `sklearn.externals.joblib` | out of the documented five shapes; keras and the sklearn alias were WON'T FIX in 40-F8. |
| `module_reload_safety` ignores `sys.modules.update`, `exec_module` | `exec_module` WON'T FIX in 40-F11; `update` is the same family and contrived. |
| `stdlib_json_ban` ignores `importlib.import_module(name_var)` and `importlib.__import__` | dynamic name; the gate lists literal `import_module("json")` and `__import__("json")` and flags them (verified). |
| `fail_open_handlers` `admit_on_error` in a `while` loop; `gate_swallows_error` for `verify_all`, `except KeyError`, `x = 1` | docstring ties the rules to "the loop's own element", gate-named functions and the listed exception types. |
| `possibly_unbound_local`: loop variable after an empty loop, `while c:` body, `del y` then read, nested `def`/decorator/default reads | docstring: "the loop is assumed to run", "a nested def ... runs later"; decorator/default reads and `del` are contrived. |
| `lock_held_across_io`: explicit `acquire()`/`release()` | docstring: "Not judged: a lock acquired by a method call". |
| `identity_comparisons` for `x is _D["k"]`, `any(x is s for s in (SQL,))` | docstring: rule is a NAME bound to a string at module/class level; the `any(... is ...)` over a tuple was flagged only by name, not literal. |
| `no_xfail_to_defer` ignores `collect_ignore` and `addopts --deselect` | outside the file-marker scope the docstring defines; no test text to judge. |
| symlinked package directories | `_core/corpus.py` `_is_regular_file` deliberately drops symlinks (comment explains the machine-dependent findings); the target is scanned when it is inside the root. |
| latin-1 file with a valid PEP 263 cookie, NUL byte, syntax error, lone-CR line endings, BOM+CRLF, form feed, 3.12 generics, `match`, walrus, line continuation | all handled: core gates report `unparsable`, code_audit now reports `unparsable` for NUL and syntax errors (the live 40-S5 fix, `b6.py`: `[('unparsable','nul.py'),('unparsable','syn.py')]`), BOM/CR/cookie files parse and are scanned. |
| `unsafe_deserialization` / `naive_utcnow` / `systemexit` hit in a `build/` directory | the core keeps a TRACKED `build/` package (`_SOFT_EXCLUDE`, verified in `g1.py`); an untracked or non-git `build/` is skipped by design. Only code_audit disagrees (40-T1). |
| suppression comment on the wrong line | `# swallow-ok` is accepted on the except line, the line above or the first body line; `# audit-exempt: <other check>` does not suppress `bare_except` (verified). `# seed-ok:` on a different statement does not suppress (verified, flagged). |
| `run_all_checked` fail-open for an unparsable file | 2026-11-11 40-S5, RESOLVED in the tree (re-verified in `b6.py`). |

## Summary

| id | sev | title |
|---|---|---|
| 40-T1 | P2 | code_audit scanners prune tracked build/dist/env/venv directories that the shared core keeps |
| 40-T2 | P2 | four text-ratchet gates have no file floor, so a wrong or empty root passes |
| 40-T3 | P2 | repo_hygiene.assert_repo_hygiene: a mistyped workflows_dir passes (floor defaults to 0) |
| 40-T4 | P2 | credential_shaped_log_arg sees only a bare name or attribute passed to a logger |
| 40-T5 | P2 | missing_network_timeout ignores sessions, import aliases and timeout=None |
| 40-T6 | P2 | console_unicode: any mention of `reconfigure(encoding` exempts the whole module |
| 40-T7 | P2 | bare_except/broad_except_swallow: a one-path re-raise exempts the handler; except* and tuples not seen |
| 40-T8 | P3 | mutable_default visits only def and knows few mutable defaults |
| 40-T9 | P3 | wallclock_test_waits default text mode misses `from time import sleep` |
| 40-T10 | P3 | skip_as_pass recognises only a message beginning with "skipped" |
| 40-T11 | P3 | unsafe_deserialization: unrelated save()/verify*() call exempts a later pickle.loads |
| 40-T12 | P3 | possibly_unbound_local: walrus under a boolean chain, swallowing `with` blocks |
| 40-T13 | P3 | lock_held_across_io: aliases, subscripted locks, os.system, aiohttp |
| 40-T14 | P3 | sentinel_or_fallback default names omit max_new_tokens and siblings |
| 40-T15 | P3 | env_flag_parsing: `from os import environ as E`, int(env) compared with a number |
| 40-T16 | P3 | fail_open_handlers.finite_guarded_reject sees only continue or a reject-named call |
| 40-T17 | P3 | no_xfail_to_defer: skipif(True, reason=...) is an unjudged unconditional skip |
| 40-T18 | P3 | extensionless Python scripts and upper-case .PY not scanned by code_audit |
| 40-T19 | P3 | systemexit_in_library does not see an import-time exit in a library module |
| 40-T20 | P3 | hardcoded_seed_in_library: unfolded `**`/numpy-int seeds and unrecognised seed parameter names |

Counts: P0 0, P1 0, P2 7, P3 13.
