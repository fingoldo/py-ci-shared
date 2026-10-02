# 2026-10-03 audit, agent 10: code added since v1.17.0

Scope: `git log v1.17.0..e53587c` (33 commits). Worktree detached at e53587c. Every finding below was reproduced by
running the shipped code. Reproducer scripts and fixture trees are in the session scratchpad (`scratchpad/aud10/`).
Interpreters: CPython 3.14 (default) and a scratch CPython 3.9.25 venv (tomli, pyyaml, pytest, pydantic installed).
Real-consumer runs used the local clones under `C:\Users\Admin\Machine learning\`, read-only.

Severity counts: High 3, Med 11, Low 8 (22 findings).

---

### N-1 (High) -- worktree_hygiene: an untracked nested git repository is silently counted as saved

Evidence: `src/py_ci_shared/worktree_hygiene.py:249-265`. `git status --porcelain --untracked-files=all` reports an
untracked directory that is itself a git repo as one entry `?? nested/`. It is not `!!`, so the `_files_under` branch
(line 255) does not run. `candidate.is_file()` is False, so line 261 `continue`s. Nothing inside it is judged.

Fixture: a repo with one commit, `origin/HEAD` set, an untracked `loose.txt`, and `nested/` containing `git init` +
`notes.txt` ("precious work").
```
?? loose.txt
?? nested/
unsaved_paths(repo, repo, "HEAD") -> ['loose.txt']
```
Impact: this verdict decides whether a worktree is deleted. A scratch clone, a vendored checkout or a sub-project
initialised inside a worktree is unsaved work, and it is reported as nothing to lose. The repo's own rule says
"unknown = finding", and this case fails open.
Proposed fix: for an untracked (`??`) entry that is a directory, walk it with `_files_under` (as for `!!`), or report the
directory itself as unsaved when it contains `.git`. Add a regression test with exactly this fixture.

### N-2 (High) -- function_complexity: unparsable files are dropped silently (fail-open), unlike complexity_ratchet

Evidence: `src/py_ci_shared/function_complexity.py:53-59, 103-117`. `function_complexities` builds a `ScanResult` and
never reads `scan.unparsed`, and `assert_complexity_does_not_grow` never reports it. Fixture: `fc/ok.py` with 60 trivial
functions, `fc/broken.py` = `def g(:` with an `if` body, and baseline `{}`:
```
py_ci_shared.function_complexity PASSED
py_ci_shared.complexity_ratchet Failed 1 complexity problem(s) (base.json, limit 10):
  broken.py:1: unparsable, so its functions were not measured: invalid syntax
```
The result is the same on 3.9 and 3.14. A refresh also runs over the unparsed file, so its baselined entries are dropped
as "shrink".
Impact: the wrapper is the registered gate (`registry.toml`, README row 183, "measured by complexity_ratchet"), but it
does not carry the sibling's unparsed check. Any file the parser cannot read is waved through, and a refresh drops its
entries.
Proposed fix: collect `scan.unparsed` in `function_complexities` and fail on it in the assert (also refuse to refresh while
any are unparsed), as `complexity_ratchet.py:163-166` does. Add `allow_unparsed` per the CLAUDE.md contract.

### N-3 (High) -- unresolved_imports: ModuleIndex mutates the shared tree_memo set, so later indexes see stale names

Evidence: `src/py_ci_shared/unresolved_imports.py:165` stores the set returned by `tree_memo(...)` in `self._names`
without copying it. Lines 142-146 then mutate it in place (`self._names.setdefault(parent, set()).add(parts[depth])`):
the submodule names are written into the memoised `_bound_names` of the package `__init__`. That set is keyed on the cached
tree, so every later `ModuleIndex` in the process that reads the unchanged `__init__.py` inherits them.
Fixture `r1/pkg/__init__.py` (`X = 1`), `r1/pkg/sub.py`, `r1/user.py` = `from pkg import sub`:
```
first index, pkg names: ['X', 'sub']
after deleting pkg/sub.py, pkg names: ['X', 'sub']
findings: []
fresh process equivalent, pkg names: ['X']
findings: ["r1/user.py:1: 'pkg' does not define 'sub' (module scope)"]
```
Impact: this is a false negative in any process that builds more than one index: a long pytest session, two
`assert_all_from_imports_resolve` calls with different `package_roots`, or corpus_drift running over several repos that
share a file. Names from one index's roots leak into another, so the cache changes the verdict. That contradicts
node_index's docstring ("correctness never depends on it").
Proposed fix: `self._names[dotted] = set(tree_memo(...))`, or make `_bound_names` return a frozenset and copy at the
mutation sites. Add a test that builds two indexes in one process with and without a submodule.

### N-4 (Med) -- ci_install_covers_conftest: `dynamic = ["dependencies"]` is read as "no dependencies" (false ci-install-missing)

Evidence: `src/py_ci_shared/_ci_install_parts.py:312-324` (`add_project`) reads only `project["dependencies"]` and
ignores `project["dynamic"]`. Fixture: pyproject `dynamic = ["dependencies"]` with
`[tool.setuptools.dynamic] dependencies = {file = ["requirements.txt"]}` and `requirements.txt` = `requests`. The workflow
runs `pip install -e .` then `python -m pytest tests`, and `tests/conftest.py` imports `requests`:
```
FINDING .github/workflows/ci.yml:8: [ci-install-missing] job 'test' runs pytest, but its install steps never install 'requests' ... ModuleNotFoundError at collection
```
Impact: a confident, wrong claim. The job does install requests. Many setuptools projects use dynamic dependencies.
Proposed fix: when `"dependencies"` (or `"optional-dependencies"` for an extra) is in `project["dynamic"]`, read
`[tool.setuptools.dynamic]` `file =` lists through `requirements_file`, else append to `provided.unresolved` so the
finding becomes `ci-install-unevaluated`.

### N-5 (Med) -- ci_install_covers_conftest: the finding's rule (and baseline key) depends on the interpreter running the gate

Evidence: `src/py_ci_shared/ci_install_covers_conftest.py:749-766` uses `installed_map()` / `installed_dist()` (the running
interpreter's metadata) to choose between `ci-install-missing` and `ci-install-unmapped`, and the key embeds both the rule
and the dist name (line 800). Fixture: `tests/conftest.py` = `import git`, job `pip install -e .` + pytest:
```
installed_map()['git'] here: ('gitpython',)
this env  : ['ci-install-missing::.github/workflows/ci.yml::test::gitpython']
other env : ['ci-install-unmapped::.github/workflows/ci.yml::test::git']   (installed_map patched to {})
```
The N-4 fixture shows the same thing without patching: CPython 3.14 (requests installed) reports
`ci-install-missing::...::requests`, and the 3.9 venv reports `ci-install-unmapped::...::requests`.
Impact: a baseline written on a developer machine does not match in CI, or the reverse. The same gap shows as one new
finding and one stale entry, so the result depends on which interpreter runs the gate.
Proposed fix: key on the import name (`imp.top`) rather than the resolved distribution, and drop the rule from the key, or
make the mapping deterministic: the built-in table plus caller aliases only, with installed metadata used as a hint in the
message.

### N-6 (Med) -- ci_health: a billing-blocked newest run hides a code failure that has been red for weeks

Evidence: `src/py_ci_shared/ci_health.py:136-167, 187`. `billing` comes only from the newest run, and when it is set the
whole streak is `"billing"`, which `over_threshold` (line 196) never counts. Fixture: failures on 09-20, 09-25 and 09-30, plus
a billing-blocked failure on 10-02 (`billing_run_ids=[4]`):
```
[('billing', 13.0, 4)] exit: 0
```
Impact: a workflow red for 13 days because of the code is reported as a billing issue and exits 0. Once billing is fixed,
a code failure that is already long past the threshold is still unreported until another run happens.
Proposed fix: assess the streak without the billing-blocked runs (treat them like `cancelled`). Mark the workflow
`billing` only when every failure in the streak was refused for billing, and otherwise report `red` with the days counted
from the first non-billing failure.

### N-7 (Med) -- ci_health `--only` with a name that matches no consumer exits 0 with an empty report

Evidence: `src/py_ci_shared/ci_health.py:391-392`. Run `main([... "--only", "mlframee"], api=<raises if called>)`:
```
| repo | workflow | status | ... |
0 workflow(s) red for more than 2 days; 0 blocked by billing.
--only typo exit: 0
```
Impact: a typo, or a consumer renamed in consumers.toml, gives a green check that checked nothing. Elsewhere the repo
treats this as a lost subject and fails; `_consumers.main` already fails on "nothing was cloned".
Proposed fix: fail (exit 2) when `--only` names a consumer that is not in the file, or when the consumer list ends up
empty.

### N-8 (Med) -- adoption_matrix: a pin to a tag or SHA that does not exist passes with `--resolve-in`

Evidence: `src/py_ci_shared/adoption_matrix.py:754-761`. `releases_behind` is only consulted when
`report.resolved[p.ref]` is truthy, and an unresolvable ref gets no finding. Fixture: an upstream repo with tags
v1.1.0..v1.4.0, and a consumer `requirements-dev.txt` =
`py-ci-shared @ git+https://github.com/fingoldo/py-ci-shared@v1.9.9`:
```
pins: [('v1.9.9', False)] resolved: {'v1.9.9': None} behind: {} failing: False
[]
```
Impact: consumer-pins.yml (`--resolve-in . --allow-behind 2`) cannot tell a typo'd or force-deleted pin from a current
one. The consumer's install fails, but this gate stays green. The summary cell shows `?`, and that does not fail.
Proposed fix: when a resolver is given and a fixed pin's ref resolves to None, add an `unresolvable-pin` finding and make
`failing` true.

### N-9 (Med) -- corpus_drift.compare: a repo missing from tonight's snapshot, and a finder that errors from its first night, never fail

Evidence: `src/py_ci_shared/corpus_drift.py:256-285`. `compare` iterates only `cur["repos"]`, and `_pair` returns None
for `errored` when `before is None`:
```
missing repo + error: [Drift(repo='pyutilz', finder='b.find_new', before=0, after=None, kind='errored', ...)]
   (mlframe, present last night with find_x=12, is absent tonight: no drift at all)
errors every night since it was added: []
```
Impact: a corpus repo that drops out (a private corpus consumer skipped for lack of a token, which `_consumers` only
warns about, or a repos.toml edit) stops being measured with no failure. A newly added finder that raises on real code
on its first night never shows as failing, because "errored" needs an earlier count.
Proposed fix: emit a failing `gone`-repo drift for every repo in `prev` that is absent from `cur`. Treat
`now_err is not None and before is None and not was_err` as failing `errored` (new finder that errors), and report
persistent errors on every run (informational at least).

### N-10 (Med) -- standard_stream_restore: any unrelated `is` comparison exempts the function; aliases and setattr are invisible

Evidence: `src/py_ci_shared/standard_stream_restore.py:65-78, 91-105`. `_identity_checked` accepts any `is`/`is not`
compare with `sys.<stream>` anywhere in the scope, nested functions included. `_sys_stream` matches only the literal
name `sys`. Fixture `ss/a.py` with four unconditional restores:
`if sys.stderr is None: return` followed by an unconditional `finally: sys.stderr = old`; `import sys as _sys` with
`_sys.stdout = devnull ... _sys.stdout = old`; `setattr(sys, "stdout", old)`; and a nested `def inner(): return
sys.stderr is devnull` next to an unconditional restore.
```
(find_unconditional_stream_restores([ss]) printed nothing)
done
```
Impact: four false negatives of the exact bug the gate exists for (the leaked closed stream that errored 13 of 23
mlframe tests).
Proposed fix: require the identity check to compare the stream with the name the function itself assigned, inside the
same function (excluding nested defs) and guarding the restore. Resolve `sys` through `ImportAliases`, and treat
`setattr(sys, "stdout"|"stderr", ...)` as an assignment.

### N-11 (Med) -- standard_stream_restore: false positives on real consumers (process-lifetime wrappers, guard via a loop variable)

Evidence (real code, read-only run over five clones): mlframe 13 findings, pyutilz 1, llm_bench 0,
glossum_backend_scripts 30, noema_app 0. Two FP classes:
* `glossum_backend_scripts/_audits/aggregate.py:3` `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` at module level
  in a CLI script, with no restore anywhere. Most of the 30 glossum hits are this shape. With nothing restored, there
  is no closed stream to restore. The gate still flags every such assignment, and `allow` takes `path:line`, which breaks
  on every edit above the line.
* `mlframe/tests/conftest.py:1088`: `sys.stdout, sys.stderr = _before`, guarded by `if _leaked`, where `_leaked` is
  computed by `s is not b` over `sys.stdout`/`sys.stderr` bound to loop variables. This is the identity check the gate
  asks for, but `_sys_stream` only recognises the literal `sys.stdout is X`.
Impact: about 44 findings across two consumers that would have to be allow-listed by line number. That adds noise and
churn, and the allow list then hides real regressions on those lines.
Proposed fix: report only assignments that RESTORE a previously saved value (the right-hand side is a name bound earlier
in the scope from `sys.<stream>`). Accept `allow` entries keyed by `path::function`, not `path:line`.

### N-12 (Med) -- nondiscriminating_shapes: `"exists"`/`"supported"` env-probe words exempt data-decided late skips; any other assert disarms median-roundtrip

Evidence: `src/py_ci_shared/nondiscriminating_shapes.py:69, 79` (new `_ENV_PARTS` entries) and `:144-156`
(`_median_is_the_only_check`).
```
out_file.exists()   []            out_file.is_file()  ['late-skip']
result.supported    []            result.accepted     ['late-skip']
test_roundtrip_with_trivial_guard (assert out is not None; assert np.median(np.abs(out - x)) < 1e-3) -> []
median control (median assert alone) -> ['median-roundtrip']
```
Impact: `out = run_pipeline(); if not out.exists(): pytest.skip()` is the late-skip shape (the code under test decided to
skip). It is exempt only because the identifier contains "exists". Any trivial companion assert (`is not None`,
`len(x) > 0`) turns off the median detector, though neither checks per-element values.
Proposed fix: treat `exists`/`supported` as environment probes only when the receiver was NOT assigned from a computing
call earlier in the function. In `_median_is_the_only_check`, count only companion checks that compare values per
element (`assert_allclose`, `assert_array_equal`, `np.all(...)`, `max(...)`), not every `assert`.

### N-13 (Med) -- Version and release state drift: HEAD declares 1.19.0 (already tagged) while CHANGELOG and registry say 1.20.0; the guard test passes

Evidence: `pyproject.toml:7` `version = "1.19.0"`, `src/py_ci_shared/__init__.py:18` `__version__ = "1.19.0"`. The tag
`v1.19.0` is at fe366ce, and HEAD is 3 commits past it. `CHANGELOG.md:5` heads `## 1.20.0`, and `registry.toml:128,137`
have `since = "1.20.0"` (ci_health, ci_install_covers_conftest). CLAUDE.md says the version "is the NEXT release
tag". `tests/test_release_version.py` → `8 passed` (log `scratchpad/aud10/relver.log`), because
`test_the_version_is_at_least_the_newest_release_tag` (line 43-49) uses `>=`, and
`test_a_tagged_version_points_at_an_ancestor_of_head` only checks ancestry.
Impact: tagging v1.20.0 from this HEAD fails release.yml's "Tag equals the declared version" step. The test that should
catch a stale version after a release cannot fail while HEAD is past the tag. Nothing ties `since` to the version.
Proposed fix: bump to 1.20.0. In the test, require `VERSION > newest` whenever `v{VERSION}` is tagged and HEAD is not that
tag's commit. Add an inventory assertion `since <= version`.

### N-14 (Med) -- release.yml: no check that the pushed tag is the newest release before moving `v1`

Evidence: `.github/workflows/release.yml:92-104`. The step does `git tag -f "$major" "$TAG^{commit}"` and
`git push -f origin refs/tags/$major` unconditionally. `verify` checks tag == declared version and that it is on master,
but not that it is >= every existing `vX.Y.Z`. Line 63 runs only three test files, while CLAUDE.md says the workflow
"runs the tests".
Impact: pushing a back-port or hotfix tag (e.g. v1.17.1 after v1.19.0, legal by this workflow if master once declared
it) force-moves `v1` backwards for all 13 consumers on `@v1`. The "release checks" do not run the suite, so a tag on a
commit whose self-ci is red is still published.
Proposed fix: in `verify`, fail unless `$TAG` sorts highest among `git tag --list 'v*.*.*'` (or skip moving the major
tag when it is not the highest). Require a successful self-ci run for the tagged SHA (`gh api .../commits/$SHA/check-runs`),
or run the full suite, and correct the CLAUDE.md wording.

### N-15 (Low) -- _core.node_index keeps every tree it ever indexed alive (unbounded growth)

Evidence: `src/py_ci_shared/_core/node_index.py:28-31, 70-71, 108-109`. Entries are keyed by `id(tree)` and hold a
strong reference. The parse cache replaces a changed file's tree, but the index entry for the old tree stays.
```
parse cache entries: 1  node_index entries: 50      (one file rewritten 50 times)
after 50 ad-hoc ast.parse trees: 100
```
Impact: memory grows without bound in long sessions (watch mode, the mutation harness, corpus_drift over several repos)
and with callers that pass ad-hoc trees. Correctness is unaffected, because the `hit[0] is tree` check guards against id
reuse.
Proposed fix: use a `weakref.WeakKeyDictionary` keyed on the tree (ast nodes are weak-referenceable), or drop a file's
entries when `parse_source` replaces its tree.

### N-16 (Low) -- randomly_seed_guard: a reseeder that already carries `__wrapped__` is never bounded

Evidence: `src/py_ci_shared/randomly_seed_guard.py:54`. `r if hasattr(r, "__wrapped__") else _bounded(r)` uses
`__wrapped__` as the "already ours" marker, and every `functools.wraps`-decorated function has that attribute. Fixture:
two reseeders that raise outside [0, 2**32), one plain and one wrapped with `functools.wraps`:
```
bounded ok 5
raw RAISED Seed must be between 0 and 2**32 - 1
```
Impact: a third-party reseeder that is decorated (lru_cache, a registry decorator) still gets the raw seed. That is the
original failure this module exists to stop.
Proposed fix: mark the wrapper with a private attribute (`bounded._py_ci_bounded = True`) and test for that.

### N-17 (Low) -- nondiscriminating_shapes: an unknown `extra_shapes` slug is silently ignored; `nonempty-only-assert` misses `!= 0` and bare `len()`

Evidence: `src/py_ci_shared/nondiscriminating_shapes.py:254-275, 164-181`.
```
control (assert len(r) > 0): ['nonempty-only-assert']   typo extra_shapes=["nonempty_only_assert"]: []
assert len(rows) != 0 -> []      assert len(rows) -> []
```
Impact: a typo in a consumer's opt-in disables the shape without any error. The two most common spellings of the same
non-emptiness assertion are not detected.
Proposed fix: raise `ValueError` for a slug not in `OPT_IN_SHAPES`. Accept `len(x) != 0`, `len(x)` and `x` (bare
truthiness of a collection-named variable is optional) as non-emptiness forms.

### N-18 (Low) -- config_getattr_default_parity: a declared pydantic field is reported as "undeclared" when dataclass_classes is given; a non-dataclass is silently ignored

Evidence: `src/py_ci_shared/config_getattr_default_parity.py:105-115, 124-128, 198-201`. Required and `default_factory`
fields are dropped from `declared` (line 110-111), and `_judge_default` then reports them as UNDECLARED whenever any
dataclass is passed. Fixture: `Model(name: str, seeds = Field(default_factory=list))`, `dataclass_classes=[Other]`:
```
with dataclass: [m.py:2 seeds=[]: no dataclass here declares `seeds`, m.py:3 name='x': no dataclass here declares `name`, m.py:4 typo_feild=...]
non-dataclass passed: []
```
Impact: false "typo" reports for real pydantic fields. A caller who passes a plain class (or a pydantic model) in
`dataclass_classes` silently loses the typo check.
Proposed fix: build the set of declared names from every pydantic field (required or not) as well as the dataclass
fields, and use it for the UNDECLARED test. Raise `TypeError` for a non-dataclass entry in `dataclass_classes`.

### N-19 (Low) -- Baseline refresh on a corrupt baseline says "does not exist"

Evidence: `src/py_ci_shared/_core/baseline.py:273-277`. A `BaselineError` sets `previous_counts = None`, and
`write_ratchet` then renders the seeding message. `bad_baseline.json` = `{not json`, refresh with one finding:
```
False | g: baseline bad_baseline.json does not exist, and seeding it would accept 1 current finding(s): ...
```
Impact: the message points the reader the wrong way, typically after a merge conflict in the baseline file.
`complexity_ratchet.write_complexity_baseline` / `function_complexity.write_complexity_baseline` (`json.loads` with no
handling, lines 139 / 83) crash with a raw `JSONDecodeError` in the same case.
Proposed fix: pass the parse error through and say "baseline X is unreadable (...); fix or delete it". Wrap the two
`json.loads` in the same handling.

### N-20 (Low) -- ci_install_covers_conftest: `pytest_plugins` and `-p` plugins are not required; non-recognised runners pass silently

Evidence: `src/py_ci_shared/_ci_install_parts.py:579-620` collects only `import` statements. `pytest_plugins = [...]` in a
conftest (and `-p name` in the pytest args) also load modules at collection. In the N-4 fixture, `tests/conftest.py`
also has `pytest_plugins = ["hypothesis_plugin_dist_x"]` and produced no finding (only `requests` was reported). A job
whose only step is `make test` or `tox -e py` (`ci_install_covers_conftest.py:364-386`) is not recognised as running
pytest and is skipped without any note.
Impact: false negatives for the exact collection-time `ModuleNotFoundError` the gate targets.
Proposed fix: treat string literals in a module-level `pytest_plugins` and `-p` values as conftest imports. List jobs
that run `make`/`tox`/`nox` and no recognised pytest call as "not evaluated" in the gate output.

### N-21 (Low) -- stale_comment_age early warning fails the test under `-W error`

Evidence: `src/py_ci_shared/stale_comment_age.py:459-461` emits `warnings.warn(..., UserWarning)`. The module and
function docstrings say the advisories "never fail the test". Fixture: a TODO committed 2026-09-08,
`now`=2026-10-03, `max_age_days=30, warn_days=7`, with `warnings.simplefilter("error")`:
```
RAISED UserWarning stale-comment early warning: lib/a.py:1: TODO goes stale on 2026-10-08 (25 of 30 days) ...
```
Impact: a consumer with `filterwarnings = ["error"]` gets a red test five days early. None of the five local clones
sets a blanket `error` today (mlframe errors only on its own DeprecationWarnings), so this is latent.
Proposed fix: use a dedicated `PyCiSharedAdvisory(UserWarning)` subclass and document it, or report through
`terminalreporter` / `print`. At minimum, state the `-W error` interaction in the docstring.

### N-22 (Low) -- Docs drift: new options not in README

Evidence: `README.md` has no mention of `warn_days` / `find_comments_going_stale` (stale_comment_age row 256, section
around line 850) or of `extra_shapes` / `nonempty-only-assert` (section at line 916). `grep -n "warn_days\|extra_shapes"
README.md` returns nothing. Both are documented only in module docstrings.
Impact: consumers cannot find the opt-ins from the catalogue.
Proposed fix: add one line each to the catalogue sections.

---

## Checked and not reported

* Python 3.9: every module in scope imports under CPython 3.9.25 (scratch venv with the declared deps), and the
  reproducers for N-2, N-4, N-6/7 and N-17 give the same verdicts there (N-5 differs, as described in N-5).
* ci_install_covers_conftest on real consumers: mlframe 1 finding (`gpu-matrix.yml`, unevaluated: `$CUDA_EXTRA`),
  pyutilz 2 (`numba-coverage.yml:115`, `publish.yml:64`, which install `-e ".[all,dev]"`, while `tests/conftest.py:153`
  imports `py_ci_shared` under `sys.version_info >= (3, 9)`). Both pyutilz findings look like true positives of the class
  the gate was written for. llm_bench, glossum_backend_scripts and noema_app had 0, with no unreadable files.
* complexity_ratchet's McCabe rules (try/else, match with an irrefutable last case, nested defs, loops' orelse) match
  ruff's C901 algorithm as read; no divergence found.
* `shrink_only`/`write_ratchet` semantics (drop, lower, refuse growth, seeding) behave as documented, apart from N-19.

Side effects: the only run inside the worktree was `tests/test_release_version.py`, which may have created or updated
the git-ignored `.pytest_cache/` and `__pycache__/`. No tracked file was modified.
