# 33_existing_gate_quality

Round 2026-11-04. Every finding is OPEN until its Disposition line says otherwise.

## 33-F1 (P1) ImportAliases is one flat per-module map, last import wins and unimported names resolve to themselves: a stray same-named import anywhere hides every use; a parameter/local named like a library is judged as that library

- Evidence: src/py_ci_shared/_core/aliases.py:62-104 (from_tree collects every Import/ImportFrom in the tree into one dict; qualified_name returns mapping.get(id, id)). Ran in scratchpad c4.py through unsafe_deserialization.find_unsafe_deserialization and naive_utcnow.find_naive_utcnow: (a) 'import pickle' at top + 'def g(): from safe import pickle' -> pickle.loads in f() is NOT reported (expected 1 finding). (b) 'def f(pickle, b): return pickle.loads(b)' (parameter) -> reported (FP). (c) 'pickle = safe.Loader()' local rebind -> reported (FP). (d) 'p = pickle; p.loads(b)' and 'L = pickle.loads; L(b)' -> not reported. (e) 'def f(arrow=datetime.datetime): arrow.utcnow()' -> not reported because naive_utcnow exempts any head named arrow/pendulum even when it is not an import (naive_utcnow.py:77-79).
- Failure scenario: Every alias-resolving gate (unsafe_deserialization, naive_utcnow, stdlib_json_ban, hardcoded_seed_in_library, swallowed_exceptions, module_reload_safety, ~30 users) goes silent for a whole module as soon as any function-local or later import re-binds the same short name, and reports clean code whose parameter shadows a library name. A one-line 'from x import json' in a helper function disables the json ban for the file.
- Proposed fix: Make ImportAliases scope-aware: record (scope node, name) bindings, resolve a Name by walking enclosing function/class scopes, treat parameters/assignments/for-targets/with-targets/walrus as shadows that return None unless the RHS is itself a resolvable alias (one-hop alias propagation for 'p = pickle' and 'L = pickle.loads'). Return None, not the bare name, for a Name that is neither imported nor a module global, and make the aware-library exemption in naive_utcnow require is_imported().
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. `ImportAliases.from_tree(tree, scoped=True)` is a new opt-in scope-aware mode (imports bind their own scope, parameters and non-import rebinds shadow and resolve to None, `p = pickle` / `L = pickle.loads` resolve one hop, class bodies are invisible to methods, a try/except-ImportError fallback is not a shadow); the flat default is unchanged so the other ~57 callers keep working. Migrated: unsafe_deserialization, naive_utcnow, stdlib_json_ban, hardcoded_seed_in_library, swallowed_exceptions, module_reload_safety (their test files pass). naive_utcnow's aware-library exemption now requires the head to be imported. Tests: tests/test_core_scoped_imports.py. The remaining flat users can opt in one by one; not forced here because each flips findings (CHANGELOG Behaviour change).

## 33-F2 (P2) Text-ratchet gates silently skip any file that is not valid UTF-8 (skip_as_pass, wallclock_test_waits, guard_without_fixture, git_child_env, dup_baseline_percent_key)

- Evidence: src/py_ci_shared/_text_ratchet.py:34-41 docstring and body: text_files() returns read_text_corpus(...).files only; corpus.unreadable (built in _gate_report.py:94-104) is dropped. Ran c3.py: tool/a.sh = b'# caf\xe9\n' + (echo SKIPPED; exit 0 block) -> find_skip_as_pass == {} ; tests/a.py = b'# caf\xe9\nimport time\ntime.sleep(1)\n' -> find_wallclock_test_waits == {}. Same file with ASCII comment is reported. A UTF-16 script is also skipped. The canaries mark these gates parses=False ('regex over text, nothing to defeat', tests/test_gate_teeth.py:500-502), so no test covers it; ddl_lock_safety does have an undecodable-bytes test.
- Failure scenario: One latin-1 accent in a comment (common in Windows-edited scripts) removes the entire file from the guard: a new sleep-based test or a SKIPPED-then-exit-0 script passes the ratchet. The AST gates fail loudly on unreadable files (UnparsedFilesError); these do not, so the same defect class is handled inconsistently.
- Proposed fix: Have text_files() return (files, unreadable) or raise; ratchet gates convert each unreadable file into a finding keyed '<rel>: unreadable' (like machine_specific_paths does with problems) and add an undecodable-bytes canary variant for the parses=False text gates.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. `_text_ratchet.text_scan()` returns the unreadable files as `<rel>: unreadable` findings; skip_as_pass, wallclock_test_waits (test files only), git_child_env (test files only) and guard_without_fixture use it. dup_baseline_percent_key was REJECTED as part of this finding: it reads one JSON baseline, not a text corpus, and does not go through text_files. Tests: tests/test_text_ratchet_gaps.py.

## 33-F3 (P2) Text-ratchet findings are keyed by 'path: normalized line', so N identical lines in one file collapse to one key and a baseline entry for one hides every later copy

- Evidence: skip_as_pass._skip_lines (skip_as_pass.py:77-85) and wallclock_test_waits.find_wallclock_test_waits (wallclock_test_waits.py:100-108) write found[f'{rel}: {normalize_line(line)}'] into a dict. Ran c3.py: three identical SKIPPED/exit-0 blocks -> 1 key; three 'time.sleep(1)' lines in tests/a.py -> 1 key. Sequence test: baseline accepts the single existing block, a second identical block is appended, assert_no_new_skip_as_pass(d) prints 'no new violations (1 accepted, baselined)' and passes.
- Failure scenario: A test file with an accepted 'time.sleep(1)' gets ten more copies pasted in; the ratchet reports zero new violations. This defeats the 'can only shrink' guarantee for exactly the highest-volume case (copy-pasted waits).
- Proposed fix: Key on rel + normalized line + occurrence ordinal ('...#2'), or count per key in the baseline the way fail_open_handlers does (counted per key).
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. `_text_ratchet.record_finding()` numbers repeated keys (`key`, `key #2`, ...) in skip_as_pass, wallclock_test_waits and git_child_env; a baseline accepting the first copy no longer hides pasted ones (test: a second block fails with `#2`). CHANGELOG Behaviour change: refresh baselines.

## 33-F4 (P2) Case-mismatched paths pass on Windows (case-insensitive FS) in every exists()-based reference gate; fail on Linux CI

- Evidence: phantom_markdown_links: README.md containing '[a](REAL.md)' with real.md on disk -> no finding (b10.py 'case mismatch'). comment_repo_paths: '# see tool/EXISTS.sh' with tool/exists.sh on disk -> no finding. grep shows 80 modules call .exists()/.is_file(); only ci_workflow_paths.py:71 mentions the Windows case-insensitivity trap.
- Failure scenario: The owner develops on Windows; a doc link, comment path, entry point or workflow path with the wrong case is green locally and in the pre-push gate, then 404s on GitHub and breaks on Linux runners.
- Proposed fix: Add one _core helper exists_exact_case(path) that compares the final component (and each parent) against os.listdir/scandir names, and use it in the reference-checking gates (phantom_markdown_links, comment_repo_paths, phantom_code_references, entry_points_resolvable, doc_identifier_parity, docs_inventory_parity, audit_path_references). Add a meta test that bans bare Path.exists() in modules tagged as reference gates.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED for the reference gates that test file existence: `_core.exists_exact_case(path, root)` (new `_core/paths.py`) compares every component below the root against the directory listing; used by phantom_markdown_links, comment_repo_paths and docs_inventory_parity. phantom_code_references, entry_points_resolvable, doc_identifier_parity and audit_path_references do not test the existence of a referenced path with exists()/is_file() (grep: they compare identifiers or list directory names), so they need no change. The proposed meta test banning bare Path.exists() was not added: too many legitimate uses (80 modules) and no reliable way to tag 'reference gates'. Tests: tests/test_core_paths.py (the wrong-case cases fail before the fix on Windows/macOS; on a case-sensitive filesystem they already agreed).

## 33-F5 (P2) swallowed_exceptions does not see contextlib.suppress(Exception/OSError), the exact semantic twin of 'except: pass'

- Evidence: Ran b2/b3: 'with contextlib.suppress(Exception): g()', 'from contextlib import suppress as sp; with sp(Exception)' and 'with contextlib.suppress(OSError): np.save(...); os.replace(...)' all produce no findings, while 'except Exception: pass' does. swallowed_exceptions.py has no reference to suppress (grep).
- Failure scenario: The shipped incident the gate was written for (np.save appends .npy, os.replace raises, swallowed) re-occurs verbatim as suppress(OSError) and passes the gate; bandit B110 also does not cover it.
- Proposed fix: In _Visitor add visit_With/AsyncWith: a with-item whose qualified name is contextlib.suppress and whose args include a broad/IO type (reuse _caught logic on the argument nodes) is judged like a silent handler, honouring the same benign-body and swallow-ok exemptions.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. swallowed_exceptions visits `with`/`async with`: `contextlib.suppress(Exception|BaseException|OSError|IOError|PermissionError|shutil.Error)` (any alias) is judged like the equivalent silent handler with the same benign-body and swallow-ok exemptions. Tests: tests/test_swallowed_exceptions_gaps.py.

## 33-F6 (P2) unsafe_deserialization: any earlier sha256/hmac/verify call in the function counts as verification of an unrelated payload

- Evidence: Ran b3.py: 'def f(p, b): hashlib.sha256(b"unrelated").hexdigest(); return pickle.loads(b)' -> no finding; moving the sha256 after the loads -> finding. unsafe_deserialization.py:343-362 (_is_verifying_call/_unverified_load) matches by call name and position only, not by data flow to the loaded value.
- Failure scenario: A function that logs a hash of anything (cache key, request id) and then unpickles a network payload is treated as signed. The only requirement to defeat the gate is to call hashlib once before the load.
- Proposed fix: Require the verified value to be the loads argument (same Name, or the bytes object the hash/hmac call consumed) or a hmac.compare_digest/verify(...) whose result guards the load; otherwise report.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. unsafe_deserialization: an earlier hash/verify/producer call now counts only when it touches the loaded value (a name connected to the load argument by assignment, e.g. `data = fh.read()`); `hashlib.sha256(b'unrelated')` does not; a verification call with no data at all (`self._check()`) still counts because it cannot be judged. Tests: tests/test_unsafe_deserialization_gaps.py.

## 33-F7 (P2) unsafe_deserialization coverage holes: pandas.read_pickle, pickle.Unpickler(fh).load() used directly, class-body and lambda call sites, marshal/shelve/jsonpickle/keras load_model, ruamel typ='unsafe'

- Evidence: Ran b2/b3: pd.read_pickle(p) -> []; 'pickle.Unpickler(fh).load()' -> []; 'class A: d = pickle.loads(b"x")' -> []; 'f = lambda b: pickle.loads(b)' -> []; marshal.loads, shelve.open, jsonpickle.decode, keras.models.load_model, YAML(typ='unsafe') -> []. Positive controls pickle/dill/cloudpickle/joblib/torch/yaml/np.load were flagged. torch.load(p, weights_only=w) with a variable is silently accepted.
- Failure scenario: read_pickle is the single most common pandas pickle path and is a plain RCE sink; a module-level or class-level load is run at import time with no scope for the verify check to apply.
- Proposed fix: Add pandas.read_pickle / pandas.io.pickle.read_pickle, pickle.Unpickler(...).load as a call, marshal.load(s), shelve.open, jsonpickle.decode, ruamel YAML(typ='unsafe'|'base' check); iterate module/class/lambda scopes in _check_pickle_loads (currently only FunctionDef and module); treat non-literal weights_only as unverified.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED except one item. Added pandas.read_pickle, pickle.Unpickler(fh).load(), marshal.load(s), shelve.open, jsonpickle.decode, ruamel YAML(typ='unsafe'), keras load_model(safe_mode=False), non-literal torch.load weights_only, and class-body and lambda scopes. Keras load_model without safe_mode is not flagged (keras 3 defaults to safe_mode=True, keras 2 has no such argument, so a default call is not decidable).

## 33-F8 (P2) rollback_then_continue treats ANY assignment in the handler as an accumulator reset, so 'msg = str(e); session.rollback()' and 'errors = errors + 1' exempt the shipped bug shape

- Evidence: Ran c2.py with a session receiver: handler 'session.rollback()' -> finding; 'session.rollback(); n_failed += 1' -> finding; 'msg = str(e); session.rollback()' -> [] ; 'session.rollback(); errors = errors + 1' -> []. Docstring (rollback_then_continue.py module doc) says exempt = 'no assignment (an accumulator reset)'. A handler-local capture of the exception text or a counter increment is not a reset of the batch. Also: rollback via helper ('_safe_rollback(session)') and 'rb = session.rollback; rb()' are not seen.
- Failure scenario: The two corpus importers that reported thousands of imported senses that never reached the DB would have been exempt as soon as the handler also assigned 'last_error = str(e)'.
- Proposed fix: Count an assignment as a reset only when its target is (or aliases) the loop's accumulator/batch (a name that is .append()/.add()-ed to or initialised before the loop) and its value is an empty literal/constructor or the name rebinding; exclude Name = f(e)/BinOp-with-self increments.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. A handler assignment is a reset only for a constant, an empty literal or a no-argument constructor; `msg = str(e)` and `errors = errors + 1` no longer exempt. Rollback helpers named *rollback* are seen. NOT done: `rb = session.rollback; rb()` (the alias form); rare and the aliasing would need the scope-aware resolver here. Tests added to tests/test_rollback_then_continue.py.

## 33-F9 (P2) fail_open_handlers admit_on_error only recognises list.append of the loop element

- Evidence: Ran c/b6: 'except Exception: out.append(x)' -> finding; 'out += [x]', 'out.extend([x])', 'out.add(x)' (set), 'out[x] = True', 'yield x' in the handler -> no findings (b6 'fo admit via ...'). 'ok = True' assigned in the handler then 'return ok' is also not seen by gate_returns_true. finite_guarded_reject is only found inside loops (reject_candidate with 'return True' in a plain function is not flagged) and needs math.isfinite spelled as attribute: 'from math import isfinite as fin' variant not tested positive.
- Failure scenario: A selection gate rewritten from append to a set or generator keeps the same fail-open semantics but leaves the gate's blast radius unmonitored.
- Proposed fix: Generalise admit_on_error: in an except handler inside a loop, any of append/add/extend/insert/setitem/yield/AugAssign whose value mentions the loop target; track variable-then-return for gate_returns_true.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except two items. admit_on_error now sees append/add/extend/insert/appendleft, `out += [x]`, `out[x] = ...` and yield; gate_returns_true also sees `ok = True` in the handler with `return ok`. NOT done: finite_guarded_reject for a plain-function `return True` rejection (no stable shape to match without false positives) and the `from math import isfinite as fin` alias (the gate matches on call names, not on ImportAliases).

## 33-F10 (P2) hardcoded_seed_in_library misses a module constant seed, expression seeds, seeds in dict literals, and only recognises params literally named random_state/seed/rng; flags the 'only when caller gave none' else-branch

- Evidence: Ran b4.py. Not reported: 'SEED=42; def f(X, random_state=None): return train_test_split(X, random_state=SEED)'; 'seed=40+2'; '**{"random_state": 7}'; 'p = {"random_state": 7}; Model(**p)'; param names rs / rng_seed / seeds; hardcode inside a lambda. Reported (FP): 'def f(seed=None): if seed is not None: return g(seed=seed); return g(seed=7)'. Documented exemptions only cover 'if rng is None', 'or', and conditional expression.
- Failure scenario: The most frequent real-world spelling (a named SEED constant) defeats the check; a seed sweep still measures nothing. The else-path FP pushes authors to '# noqa' markers.
- Proposed fix: Resolve module-level int constants (Name bound once to an int literal) and constant-fold simple BinOps; add dict literal / dict() keys random_state|seed; accept parameters matching (^|_)(seed|random_state|rng|rs)(s)?($|_); treat a literal in the else/fall-through arm of 'if <seedparam> is not None: ... return' as the default case.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. Module constants bound once to an int, constant-folded `+ - * // %`, `**{'random_state': 7}`, parameter names matching `(^|_)(seed|random_state|rng|rs)s?($|_)`, lambdas, and the fall-through after `if <seed> is not None: ... return` is the no-seed default. NOT done: `p = {'random_state': 7}; Model(**p)` (a dict held in a variable).

## 33-F11 (P2) ci_workflow_timeout_gate accepts any value of timeout-minutes: 0, 360 (the default), 99999, negative, non-numeric

- Evidence: Ran b9.py with find_jobs_missing_timeout on one-job workflows: 'timeout-minutes: 0' -> [], '360' -> [], '99999' -> [], '-5' -> [], 'abc' -> []. Only absent/null is reported ('timeout-minutes:' null -> ['a']).
- Failure scenario: The gate exists to prevent a 6-hour hang; 'timeout-minutes: 360' or 99999 reintroduces it and satisfies the gate. 0 or a string make GitHub reject or ignore the workflow.
- Proposed fix: Require an int (or a ${{ }} expression) with 1 <= value <= a configurable cap (default 120); report values >= 360 as 'equals the platform default'.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. JobTimeout.timeout_problem(): 0, negative, non-integer, >= 360 and > max_minutes (default 120, configurable on find_jobs_missing_timeout and assert_all_jobs_have_timeout) count as missing; `${{ }}` expressions pass. CHANGELOG Behaviour change.

## 33-F12 (P2) wallclock_test_waits detects only the literal 'time.sleep(' and non-const Dart delays are missed, in BOTH text and ast mode

- Evidence: Ran b1.py. tests/test_a.py with 'from time import sleep; sleep(1)', 'import time as t; t.sleep(1)', 'asyncio.sleep(1)', 'ev.wait(5)', 'time.sleep (1)', and a line-continued 'time.\\\n sleep(1)' -> {} in text and ast mode. Dart 'await Future.delayed(Duration(milliseconds: 50))' (no const) -> {}. A top-level test_a.py outside a tests/ dir is not scanned (is_wallclock_test_file requires a test/tests directory part) although _text_ratchet.is_test_path treats test_*.py as a test.
- Failure scenario: The ast mode that is supposed to be the precise one does not use ImportAliases, so renaming the import evades it; the text regex has the same hole. Tests that use asyncio.sleep or threading waits re-introduce flake sources.
- Proposed fix: In ast mode resolve through ImportAliases (time.sleep, asyncio.sleep, trio.sleep, anyio.sleep); make the Dart pattern accept optional const; use is_test_path for the file filter.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED except one item. ast mode resolves through ImportAliases (renamed imports, `from time import sleep`) and sees asyncio/trio/anyio sleeps (`sleep(0)` is a yield and is not a wait); Dart `Future.delayed(Duration(...))` without const; spaced `time . sleep (`. REJECTED: scanning a `test_*.py` outside a test directory. The module documents that those are exploratory scripts and an existing test pins it; changing it is an owner decision, not a defect.

## 33-F13 (P2) machine_specific_paths db-url rule only knows postgres/mongodb schemes; mysql/redis/amqp credentials, string concatenation and non-default config files are not scanned

- Evidence: Ran b4.py: 'mysql://USERINFO@h/db', 'redis://USERINFO@h:6379/0', 'amqp://USERINFO@h/' -> []; 'postgresql+psycopg2://USERINFO@h/db', 'mongodb+srv://USERINFO@h/' -> reported. 'D:' + '\\data' -> []; b'D:\\data' (bytes) -> []; .env, .json, .sh containing D:\\data or C:\\Users\\bob -> [] (default config_patterns are .yml/.yaml/.toml/.cfg/.ini only); UNC path '\\\\srv\\share' -> [].
- Failure scenario: A pasted Redis or MySQL DSN with a password lands in the repo; .env files and shell scripts, where such paths and DSNs actually live, are out of scope by default.
- Proposed fix: Generalise db-url to '<scheme>(+driver)?://USERINFO@' for any scheme with a non-placeholder password; add .env, .json, .sh, .ps1 to DEFAULT_CONFIG_PATTERNS; fold constant BinOp(Add) strings before matching; include bytes constants.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except UNC paths. db-url now matches any scheme with a non-placeholder password (the original already covered postgres/mysql/redis/mssql; amqp and https did not), string concatenation of constants and bytes constants are folded, and `.env`, `.json`, `.sh`, `.ps1` are config files by default. WON'T FIX: UNC paths (`\\srv\share`), a share name is not machine-specific by itself and the default would fire on legitimate network paths in docs.

## 33-F14 (P2) latched_availability_flags decides 'availability flag' by 7 upper-case substrings; _HAVE_GPU, _CUDA_OK, _USE_NUMBA, _HAS_CUPY, _ENABLED, _OK, _GPU are not flagged, nor is a class-attribute or lru_cache latch

- Evidence: latched_availability_flags.py:44 _FLAG_MARKERS = (AVAILABLE, FAILED, USABLE, SUPPORTED, PRESENT, WORKS, BROKEN). Ran c6.py: the canary shape with the flag renamed to _HAVE_GPU / _USE_NUMBA / _CUDA_OK / _GPU / _NO_GPU / _DISABLED -> [] ; with _SEED_GPU_AVAILABLE, bare except, BaseException and async def -> finding. 'cls._available = False' in a classmethod and an @lru_cache(None) probe that 'return False' in 'except Exception' -> [].
- Failure scenario: The three instances the gate was written for were named *_AVAILABLE; the same defect under the equally common HAVE_/HAS_/USE_/OK naming is invisible, and so is the functools.cache spelling of the same latch.
- Proposed fix: Broaden markers (HAVE, HAS_, _OK, USE_, ENABLED, DISABLED, GPU, CUDA...), or drop the name heuristic when the try body contains a probe call and the except assigns a bool constant to a global. Add a rule for a cached function whose broad except returns a falsy constant.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except the class-attribute latch. Flag names are matched by whole underscore words as well (HAVE, HAS, USE, OK, ENABLED, DISABLED, GPU, CUDA, NUMBA, READY, NO), and a functools.cache/lru_cache probe whose broad except returns a falsy constant is a finding. NOT done: `cls._available = False` in a classmethod.

## 33-F15 (P2) optional/ratcheted-config gates that detect the setting by name only: sentinel_or_fallback and hardcoded_token_ceilings miss indirection and assignment forms

- Evidence: sentinel_or_fallback (b5): x.MAX_TOKENS or 30, x.timeout_s or 30, get_timeout() or 30, walrus, 'x.seed if x.seed else 5', os.environ.get('TIMEOUT') or '30' -> []; multi-line BoolOp with '# falsy-ok' on the closing line is reported because only node.lineno is checked (sentinel_or_fallback.py:78); test_a.py at the root is scanned while tests/ is excluded. hardcoded_token_ceilings (c5/b8): 'max_tokens = cfg.max_tokens or 4096', 'return cfg.max_tokens or 4096' (explicitly named in the module docstring as flagged) -> []; 'self.max_tokens = 4096', 'MAX_TOKENS = 4096', 'CAP=4096; create(max_tokens=CAP)', '2**12', '4096.0', setdefault('max_tokens', 4096), camelCase maxTokens, max_new_tokens, num_predict -> []; only kwarg, dict-literal, param default, .get/getattr default are caught.
- Failure scenario: The documented example 'cfg.max_tokens or 4096' is not caught outside a keyword argument, so the exact incident class (a defaulted capability number) survives; a named constant removes the literal from the call site.
- Proposed fix: hardcoded_token_ceilings: also flag Assign/AnnAssign/AugAssign targets named like the settings (including attributes and UPPER_CASE module constants) whose value is an int literal or '<x> or <int>', resolve single-assignment module int constants at call sites, add HF/ollama names. sentinel_or_fallback: compare names case-insensitively with suffix variants, accept the marker anywhere in node.lineno..end_lineno, and use is_test_path.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except two items. sentinel_or_fallback: names match case-insensitively, camelCase and without a unit suffix, walrus operands, `x.seed if x.seed else 5`, the marker anywhere in the expression's lines, root-level test modules skipped like tests/. hardcoded_token_ceilings: `cfg.max_tokens or 4096` anywhere (assignment, return), assignments to such names, once-bound module constants at call sites, `2**12`, `4096.0`, setdefault, camelCase, max_new_tokens and num_predict. NOT done: `get_timeout() or 30` and `os.environ.get('TIMEOUT') or '30'` with a non-int fallback in hardcoded_token_ceilings (the env form is caught in sentinel_or_fallback only).

## 33-F16 (P2) discarded_model_copy ignores the bare statement 'cfg.model_copy(update=...)' and tuple targets

- Evidence: Ran b5: 'def f(cfg): cfg.model_copy(update={"a": 1})' (result dropped) -> []; 'c2, x = cfg.model_copy(update=...), 1' -> []; 'dataclasses.replace(cfg, a=1)' assigned and never used -> []; module-level assignment -> []. The documented single-assignment form is caught (including async def and the 'cfg = cfg.model_copy(...)' rebind).
- Failure scenario: The fully discarded call is the purest form of the shipped bug (override applied to a throwaway) and is not reported at all.
- Proposed fix: Also flag Expr(Call(model_copy|copy(update=)|dataclasses.replace)) statements, unpack tuple targets, and module scope.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. A bare `cfg.model_copy(update=...)` statement, tuple targets, `dataclasses.replace(...)` and module scope are covered.

## 33-F17 (P2) env_flag_parsing misses the dominant two-step idiom and aliasing: v = os.environ.get('X'); if v == '1'

- Evidence: Ran b5: 'v = os.environ.get("MY_F"); if v == "1"' -> []; bool(os.environ.get(...)) -> []; walrus '(v := os.environ.get(..)) == "1"' -> []; 'env = os.environ; env.get(..) == "1"' -> []; lowercase prefix 'my_f' -> []; name via constant N='MY_F' -> []. FP: 'os.environ.get("MY_DIR") in ("a","b")' (a backend selector, not a boolean) and 'os.environ.get(..) or "a"' are reported as boolean flags.
- Failure scenario: Most hand-rolled flag parsing assigns the read first; those sites never enter the ratchet, so the 'one shared parser' guarantee covers only inline reads.
- Proposed fix: Track names assigned from an env read (single assignment per function) into boolean contexts; resolve constant names; treat environ aliases; restrict the membership/or contexts to literals that look boolean (0/1/true/false/yes/no/on/off).
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. Two-step reads (`v = os.environ.get(..); v == '1'`, single assignment per file), walrus, `bool(read)`, `env = os.environ` aliases, constant names, case-insensitive prefix; membership only for boolean-looking literals and `read or 'default'` no longer reported.

## 33-F18 (P2) predictable_tmp_path: gettempdir stored in a variable, concatenated, formatted or read as tempfile.tempdir / '/tmp/...' is not reported; any getlogin anywhere in the module suppresses; duplicate finding per site

- Evidence: Ran b4: 't = tempfile.gettempdir(); os.path.join(t, "x.lock")', f'{tempfile.gettempdir()}/x.lock', tempfile.gettempdir() + '/x.lock', os.path.join(tempfile.tempdir, ..), '/tmp/x.lock', Path('/tmp') / 'x.lock' -> all []. 'U = os.getlogin()' at module level plus an unrelated join(gettempdir(), ..) in another function -> [] suppressed (module-level mention of a per-user source exempts every function). 'Path(gettempdir()) / "x.lock"' yields two identical findings for line 4.
- Failure scenario: The idiom 'tmp = tempfile.gettempdir(); lock = os.path.join(tmp, name)' is the common spelling and evades; duplicates make baseline counts unstable.
- Proposed fix: Track Name bound once to gettempdir()/tempdir/'/tmp' constants within the scope; handle JoinedStr and Add; scope the per-user mitigation to the enclosing function only (module-level only for module-level code); dedupe by (line, col).
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED. One finding per outermost expression, gettempdir stored in a once-assigned variable, f-strings and `+`, `tempfile.tempdir`, `/tmp` literals, and the per-user marker is scoped to the enclosing function (module-level code is judged against module-level statements only).

## 33-F19 (P2) ddl_lock_safety rule 2 covers only CHECK/FOREIGN KEY; ADD PRIMARY KEY/UNIQUE and SET NOT NULL (full scans/index builds under ACCESS EXCLUSIVE) pass; SET LOCAL lock_timeout before a COMMIT still counts

- Evidence: Ran b7.py with SET lock_timeout='1s' first: 'ALTER TABLE t ADD PRIMARY KEY (a)', 'ADD CONSTRAINT c UNIQUE (a)', 'ALTER COLUMN a SET NOT NULL' -> no findings (CHECK/FK without NOT VALID are found; ALTER COLUMN TYPE is found as ddl-table-rewrite). 'BEGIN; SET LOCAL lock_timeout=...; COMMIT; ALTER TABLE ...' -> [] although SET LOCAL ended at COMMIT.
- Failure scenario: A hand-applied migration adds a primary key or SET NOT NULL to a hot table and blocks all writers for the scan; the gate says the file is lock-safe.
- Proposed fix: Add ddl-set-not-null (unless preceded by a validated CHECK ... IS NOT NULL), ddl-add-unique/primary-key (require CREATE UNIQUE INDEX CONCURRENTLY + USING INDEX), and end the SET LOCAL scope at COMMIT/ROLLBACK.
- AST-checkable: False; FP risk: med
- Disposition: RESOLVED. ADD PRIMARY KEY/UNIQUE (without USING INDEX) and ALTER COLUMN SET NOT NULL are reported under the constraint rule (tiny_tables apply); SET LOCAL lock_timeout stops counting at COMMIT/END/ROLLBACK while a session-level SET survives. NOT done: the exemption 'SET NOT NULL preceded by a validated CHECK ... IS NOT NULL' (the author can use tiny_tables or the baseline).

## 33-F20 (P3) skip_as_pass: bare 'exit', lower-case 'skipped', 'sys.exit()' and 'raise SystemExit(0)' are not recognised; tools/ and .github/scripts are outside SKIP_DIRS

- Evidence: Ran b1.py: shell 'echo "SKIPPED: none"\n exit' -> {}; 'echo "skipped: none"; exit 0' -> {}; python 'print("SKIPPED"); sys.exit()' -> {}; 'raise SystemExit(0)' -> {}; logging.warning('SKIPPED') variant -> {}; file tools/a.sh and .github/scripts/a.sh with 'echo SKIPPED; exit 0' -> {}. CRLF and ps1 variants are detected. _EXIT_ZERO regex skip_as_pass.py:36 requires an explicit 0.
- Failure scenario: A guard script that prints a skip and falls off with plain 'exit' (status of the last command, usually 0) or sys.exit() reads as pass.
- Proposed fix: Case-insensitive SKIPPED; treat bare exit / sys.exit() / SystemExit(0)/(None) as zero; make SKIP_DIRS configurable and include tools/ and .github/scripts by default.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. Case-insensitive skipped, bare `exit`, `sys.exit()`/`exit()`/`quit()`/`SystemExit(0|None)`, and tools/ and .github/ added to SKIP_DIRS. logging.warning('SKIPPED') is not covered (a logging call is not a print and the rule is about printed skips).

## 33-F21 (P3) swallowed_exceptions blind spots: handler nested in a finally is exempt regardless of its call, 'close'/'emit'/'__exit__' scopes are exempt even when they do real work, class bodies work but tests/scripts dirs are excluded by default

- Evidence: Ran b3: 'finally: try: c.commit() except Exception: pass' -> [] (swallowed_exceptions.py _judge: self.in_finally short-circuits for any call); 'def close(self): try: self.flush_to_disk(); self.save() except Exception: pass' -> [] (scope in _CLEANUP_SCOPES). scripts/ and tests/ top-level dirs excluded by default (b2: 'scripts/a.py' -> []) though pkg/tools/a.py is scanned, so the exclusion is name-based and inconsistent.
- Failure scenario: A commit/save failure swallowed in a finally or close() loses data silently, the exact class the gate names.
- Proposed fix: Apply the release-method whitelist (_is_best_effort) inside finally/cleanup scopes instead of exempting wholesale; document or normalise the default exclude list.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except the exclusion list. A handler in a `finally`, `close`, `aclose`, `__exit__` or `__aexit__` is judged by the release whitelist like any other (only `__del__` and `emit` stay exempt). WON'T FIX: normalising the default exclude list (tests, scripts, ... are directory names a consumer overrides with exclude_parts; making pkg/tools consistent would turn consumers red for a naming accident).

## 33-F22 (P3) identity_comparisons flags a local that shadows the module-level string, a name later rebound to a non-string, and conditionally bound names; misses str()/join/dedent-built constants

- Evidence: Ran b5: 'SQL="a"; def f(x): SQL = object(); return x is SQL' -> reported (FP); 'SQL="a"; SQL=object(); x is SQL' -> reported (FP); try/except and if-guarded bindings -> reported; 'SQL = " ".join([...])', 'textwrap.dedent("a")', 'str("a")' constants compared by 'is' -> [] ; walrus-bound module name -> [].
- Failure scenario: Noise pushes authors to add ignores; real identity compares on dedented SQL constants (the original incident used SQL text) slip by.
- Proposed fix: Use scope-aware binding (the scopes helper in _core/scopes.py) so a function-local rebind shadows; treat module names with any non-str assignment as unknown; treat dedent/join/format/str of constants as string-valued.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED partly. A module name also assigned a non-string in the same scope is no longer a string constant; `str(...)`, `' '.join(...)`, `.format`, `textwrap.dedent(...)` built constants count. NOT done: a function-local rebind shadowing the module constant (needs the scoped resolver in this gate's name table) and walrus-bound names.

## 33-F23 (P3) lock_held_across_io: cursor.execute / Path.write_text under a lock, asyncio.sleep under async with lock, lock.acquire()/release() pairs and self._mutex are not judged

- Evidence: Ran b5: 'with lock: cur.execute("select 1")' -> []; 'with lock: p.write_text("a")' -> []; 'async with lock: await asyncio.sleep(1)' -> []; 'lock.acquire(); time.sleep(1); lock.release()' -> []; 'with self._mutex: time.sleep(1)' -> []. open(...,'w'), requests, sleep, urlopen, aliased imports are caught.
- Failure scenario: The motivating shape (connection pool under its lock) generalises to cursor.execute/commit under a lock, which is the most common DB-pool bug and is silent.
- Proposed fix: Add DB-API names (execute, executemany, commit, fetch*) on receivers named cur/cursor/conn/session, Path.write_text/write_bytes, asyncio.sleep; accept names matching (lock|mutex|sem)$ case-insensitively.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED except lock.acquire()/release() pairs. cursor/conn/session execute/commit/fetch* round trips, write_text/write_bytes, asyncio.sleep under `async with`, and mutex/semaphore/camelCase lock names (a name like `clock` or `block` is not a lock). NOT done: acquire()/release() pairs (needs flow analysis of the region between the calls).

## 33-F24 (P3) vacuous_loop_assertions: unittest self.assert* in an if inside a loop over an empty iterable is not recognised; while loops not judged

- Evidence: Ran b11: 'class T(TestCase): def test_a(self): for x in []: if x: self.assertTrue(x)' -> []; 'while xs: x = xs.pop(); if x: assert x > 1' -> []. 'for ... if ...: assert' and 'pytest.fail' / 'raise AssertionError' variants are reported.
- Failure scenario: Fleet code written in unittest style keeps the zero-iteration blind spot the gate was created for.
- Proposed fix: Treat calls to self.assert*/self.fail and ... as assertion statements; extend to while-loops whose condition is an iterable truthiness test.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED partly. unittest `self.assert*`/`self.fail` are checks, and count as floors. REJECTED: while loops. The reported shape is a loop whose body is only checks; a `while` body that pops/assigns is not that shape and a while condition is not an iterable whose emptiness a floor can assert.

## 33-F25 (P3) workflow_runner_labels (regex over text) flags the label inside run: strings and name: fields and misses upper-case spellings

- Evidence: Ran b9: 'steps: - run: echo ubuntu-latest' under runs-on: ubuntu-24.04 -> reported (FP); 'name: ubuntu-latest check' -> reported (FP); 'runs-on: Ubuntu-Latest' -> not reported. A marker comment on the line above (not the same line) does not exempt (consistent with docs).
- Failure scenario: FP forces '# moving-label-ok' on non-runner lines; case variants of the label (accepted by GitHub) evade.
- Proposed fix: Parse YAML (as ci_default_branch_never_cancelled does) and inspect runs-on and matrix values only; compare case-insensitively.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED. A runner label inside a `run:`/`name:`/`if:`/`shell:`/`id:`/`description:` value or a `|`/`>` block under one is text and no longer reported; comparison is case-insensitive (`Ubuntu-Latest`). The gate stays line-based (no YAML parse), consistent with its siblings.

## 33-F26 (P3) phantom_markdown_links ignores HTML <img src>/<a href>, multi-line link text and anchors; comment_repo_paths ignores trailing comments after shell code and Makefile/ps1/go/sql

- Evidence: Ran b10: '<img src="missing.png">' and '<a href="missing.md">' -> []; '[a\nb](missing.md)' -> []; '[a](real.md#nope)' -> []. comment_repo_paths: 'echo x  # see tool/missing.sh' in .sh -> [], Makefile/.ps1/.go/.sql comments -> [], 'tool\\missing.sh' backslash -> []. Code fences and inline code are correctly skipped; CRLF and BOM correct.
- Failure scenario: READMEs with centered HTML logos or screenshot tags can point at deleted assets without the gate noticing.
- Proposed fix: Add an HTML attribute pass for src/href with the same resolution rules, allow newlines in link text, optional heading-anchor check; add trailing-comment support for shell and the missing comment syntaxes.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED partly. phantom_markdown_links reads HTML `src`/`href` attributes (img, a, source, video, audio, link, script, iframe) and link text wrapped over lines. NOT done: heading-anchor existence (`real.md#nope`; needs the renderer's slug rules, the module deliberately drops fragments) and the comment_repo_paths shell trailing comment/Makefile/ps1/go/sql syntaxes and backslash paths. The latter are WON'T FIX here: each syntax is a lexer of its own and the gate's roots-and-markers contract is documented.

## 33-F27 (P3) epsilon_padded_denominators misses a named EPS constant, np.finfo(...).eps, a power stored in a variable first, floor division and x ** -1

- Evidence: Ran b8: 'k / (r**d + 1e-12)' -> finding; 'EPS = 1e-12; k / (r**d + EPS)' -> []; 'r**d + np.finfo(float).eps' -> []; 'p = r**d; k / (p + 1e-12)' -> []; 'k // (r**d + 1e-12)' -> []; '(r**d + 1e-12) ** -1' -> []; 'np.exp(-x) + 1e-12' -> [].
- Failure scenario: Named epsilon constants are the usual style in numeric modules; the gate's own incident would be spelled that way after a refactor.
- Proposed fix: Resolve module-level float constants < 1e-3, treat np.finfo(..).eps, fold single-assignment locals, include FloorDiv and Pow(-1) and exp(-x).
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED partly. Module constants bound once to a small number, `np.finfo(..).eps` / `.epsilon`, `//` and `(x + eps) ** -1`. NOT done: a power stored in a local first (`p = r**d; k / (p + eps)`) and `np.exp(-x) + eps`, which need local data flow and a notion of 'shrinking' for exp.

## 33-F28 (P3) no_xfail_to_defer reports each decorator-xfail twice; reason checks do not apply to skip with a reason; unread_function_params flags interface-required overrides

- Evidence: Ran b8: '@pytest.mark.xfail' bare -> lines [2, 2] (xfail-not-strict and no-reason both on the same line, so baseline keys differ but counts double); '@pytest.mark.skip(reason="broken, fix later")' and pytest.skip('bug 123, fix later') -> [] (module doc: only no-reason is judged for skip). unread_function_params: 'class A: def fit(self, X, sample_weight=None): return self' -> reported (FP for an sklearn-API override whose base signature requires the knob).
- Failure scenario: The skip path is the cheapest way to park a bug with a 'fix later' reason; override FPs accumulate in the baseline.
- Proposed fix: Apply untracked-reason to unconditional skip markers too; exempt methods named fit/predict/transform/score... that override a base class listed in bases or decorated with @override.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED partly. REJECTED: the 'twice' report; `xfail-not-strict` and `no-reason` on one line are two different defects with their own baseline keys, by design. RESOLVED: an unconditional `pytest.mark.skip` / `pytest.skip` reason must name an external component or a tracked issue (`untracked-reason`), so 'fix later' is a finding. NOT done: unread_function_params exemption for interface-required overrides (a base-class signature is not visible to a parse-only check; use the existing exemption mechanism).

## 33-F29 (P3) Smaller detector gaps: single_shot_timing_assertion (datetime.now diffs, benchmark-fixture .elapsed), hash_fed_by_array_copy (xxhash, crc32, variable-then-hash, memoryview), gpu_timing_sync (jax without block_until_ready; .cpu() flagged), order_losing_filters (polars is_in with .take form, helper-split mask), polars_null_equality (df['a'] == df['b'], lit(None), is_in([None]))

- Evidence: Ran b11/b6: shot 'datetime.datetime.now() - t0' and 'assert timer.elapsed < 5.0' -> []; hashcopy xxhash.xxh64(a.tobytes()), zlib.crc32, 'b = a.tobytes(); sha256(b)' -> []; gpu 'jax.jit(g)(x)' timed without block_until_ready -> [], 'm(x.cuda()).cpu()' (implicit sync) -> reported; olf 'mask built in helper mk()' -> []; pl 'pl.col(a) != pl.lit(None)', 'is_in([None])' and subscript-based 'df["a"] == df["b"]' -> [].
- Failure scenario: Each is a documented-shape sibling that falls just outside the matcher; individually low impact.
- Proposed fix: Extend the matcher tables per gate; for gpu_timing_sync treat .cpu()/.item()/.numpy() as syncs.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED partly. hash_fed_by_array_copy: crc32/adler32/xxhash sinks and `b = a.tobytes(); sha256(b)`; gpu_timing_sync: `.cpu()/.item()/.numpy()/.tolist()` end a timed region. NOT done: single_shot_timing_assertion datetime.now diffs and benchmark `.elapsed`, jax without block_until_ready, order_losing_filters helper-split mask, polars_null_equality `lit(None)`/`is_in([None])`/subscript equality; each is a new matcher family with its own false-positive study, left for a dedicated round.

## 33-F30 (P3) Teeth/canary corpus: 117 of 146 canaries have a single violation file with one seeded shape, and the teeth check only requires 'raises and names the seed token'

- Evidence: tests/test_gate_teeth.py module docstring and runner: violation/ must make the assert_* raise with Canary.token in the message; bom/ same; clean/ must pass. Count over tests/canary: 146 gate dirs, 117 with exactly 1 violation file, median 6 lines; e.g. tests/canary/swallowed_exceptions/violation/seed.py.canary is the single shape 'try: os.replace(a,b) except OSError: pass'. A detector neutered to match only OSError (dropping bare/Exception/BaseException/tuple branches in _caught, swallowed_exceptions.py:88-97) still passes the canary; only the 20 unit tests in tests/test_swallowed_exceptions.py would catch it. 10 gates (ci_default_branch_never_cancelled, ci_pin_version_skew, gate_integrity, hook_hygiene, test_partition_reachability ...) have 0 violation files in violation/ (they use support/ fixtures or code-built corpora) and 77 gate modules have no canary dir at all (src modules minus tests/canary names, includes source_text_ban, stale_comment_age, worker_budget, ...).
- Failure scenario: For gates with thin unit tests the canary is the only teeth check and cannot detect partial neutering (one of N shapes); 'bypass' regressions like F1 (alias resolution) would not be seen since every canary uses the canonical import spelling.
- Proposed fix: Add a canary 'variants/' folder per gate: one violation file per documented shape and per spelling (aliased import, from-import, nested def, async def), and have test_gate_teeth assert the exact finding count (len(findings) == number of seeded sites) rather than raise-and-token. Extend teeth_sweep to run partial-neuter mutations (drop one branch of the matcher).
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED partly. `Canary.tokens` makes the teeth check require EVERY seeded shape to be named by the failure (violation and BOM variants). swallowed_exceptions (six shapes: OSError, bare, Exception, BaseException, tuple, suppress) and unsafe_deserialization (six sinks) are converted. NOT done: the other ~115 single-shape canaries, the per-gate `variants/` folders and partial-neuter mutation in teeth_sweep (mutation machinery is out of scope for this change); add `tokens=` per gate as its unit tests are touched.

## 33-F31 (P3) Several gate unit tests assert only 'some finding contains substring' (any(... in v for v in problems.values())) so extra/duplicate findings and wrong counts pass

- Evidence: tests/test_dart_scanners.py lines 165-176, 232-278, 326, 362, 410, 443-444: assert any('onDoubleTap' in v for v in problems.values()) etc.; tests/test_vacuous_loop_assertions.py:54 'assert found'; tests/code_audit/test_per_call_state_on_shared_instance.py:125, test_redundant_test_fit.py:56, test_registry_invariants.py:82,177: bare 'assert findings'. Real duplicates exist in shipped gates and would not be caught: predictable_tmp_path reports 'Path(gettempdir()) / x' twice and no_xfail_to_defer reports a bare xfail twice on the same line (see F18/F28).
- Failure scenario: A regression that doubles findings, emits an extra false positive on the clean sibling, or matches the wrong line is invisible; with ratchet baselines keyed by counts it silently changes the allowed total.
- Proposed fix: Assert equality on the full sorted (line, rule) list for each seeded fixture (assert [(f.line, f.rule) for f in findings] == [(3, 'x')]) and add a no-extra-findings assertion on the control file; add a meta test banning 'assert any(' / bare 'assert findings' in tests/test_<gate>.py.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED partly. tests/test_dart_scanners.py: all 27 `assert any(token in v ...)` became `assert sum(1 for v in ... if token in v) == 1`, and tests/test_weak_assertion_ban.py bans `assert any(` and bare `assert findings`/`assert found` in the converted files (STRICT_FILES, extend as files are converted). The remaining cited bare `assert findings` lines are followed by exact assertions on the same findings (test_per_call_state_on_shared_instance, test_registry_invariants) or sit in fixture text (test_vacuous_loop_assertions:54), so they are not weak. The duplicate-reporting examples (predictable_tmp_path) are fixed under 33-F18 with tests that pin the exact lines.

## 33-F32 (P3) line_has_marker accepts the opt-out marker anywhere after the first '#' on the line, including inside a string literal

- Evidence: src/py_ci_shared/_gate_report.py:107-112: 'return "#" in text and marker in text[text.index("#"):]'. A line such as x = "#"; open('a.sh','w')  # unrelated, or call('# lf-ok') passes as an explicit opt-out; the first '#' may be inside an earlier string.
- Failure scenario: A literal containing '# lf-ok' or '# machine-path-ok' (for example a test fixture string or a docstring example) silently exempts the line; low probability but undetectable.
- Proposed fix: Use tokenize to find the real COMMENT token on the line (gates already use the tokenizer in comment_repo_paths) and test the marker against that.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED. `line_has_marker` finds the marker only in the real trailing comment (a `#` inside a quoted string is skipped; an unterminated quote falls back to the first `#`, as before). Tests: tests/test_gate_report_marker.py. CHANGELOG Behaviour change.
