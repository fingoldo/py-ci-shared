# 42_core_baseline_semantics

Round 2026-11-07, dimension 42. Every finding is OPEN until its Disposition line says otherwise. Repro scripts: scratchpad audit42/t1.py, t2.py. Checked and sound: BOM/CRLF/NUL/undecodable handling in source.py, ls-files retry and CorpusError, shrink_only logic, atomic writes, symlink skip.

## 42-F1 (P1) DEFAULT_EXCLUDE drops any directory named build/dist/venv/site-packages anywhere in the tree, so a real package directory such as src/pkg/build is outside every gate's corpus in git mode

- Evidence: corpus.py iter_files: skip.intersection(parts) over every component of the root-relative path. Repro (scratchpad audit42/t1.py): git init, files src/pkg/build/m.py and src/pkg/ok.py; [p.name for p in iter_files(g)] printed ['ok.py'].
- Failure scenario: A project with a legitimate subpackage named build, dist, venv or node_modules has all its code silently unscanned by nearly every gate, with no unparsed finding; min_files still passes because other files parse.
- Proposed fix: Apply the name list only to the first component below root, or in git mode only to untracked (--others) files, since a tracked file is corpus by definition.
- AST-checkable: True; FP risk: med
- Disposition: RESOLVED 3ba0809: iter_files keeps TRACKED files under build/dist/venv/node_modules/site-packages in git mode (one extra git ls-files --cached only when such a path is listed); dot-dirs and __pycache__ stay excluded; test TestTrackedDirectoryNamedLikeBuildOutput failed before the fix. Corpus counts unchanged on pcs (838) and pyutilz (616).

## 42-F2 (P1) scan_roots_excluding matches the exclude fragments against the ABSOLUTE posix path (the ARCH-14 defect again): a checkout under a directory containing a fragment parses zero files

- Evidence: scan.py scan_roots_excluding: any(fragment in p.as_posix() ...) with p = root/rel. Repro: root /tmp/x/my_tests_dir/proj/a.py with exclude ['tests'] -> scan_roots_excluding([r], ['tests']).parsed_count printed 0.
- Failure scenario: drifted_duplicate_functions, epsilon_padded_denominators, hash_fed_by_array_copy, latched_availability_flags and others scan nothing when the repo lives under a matching directory; the result is a pass unless the caller remembers check_floor (scan_python does not raise).
- Proposed fix: Match fragments against p.relative_to(root).as_posix(), and call result.assert_ok() inside the helper.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED 3ba0809: scan_roots_excluding matches root-relative paths; test_scan_roots_excluding_matches_fragments_against_the_root_relative_path failed before. The helper-level assert_ok was NOT added: the assert_* entry points already call check_floor/unparsed, and the find_* callers would turn red.

## 42-F3 (P2) line_has_marker treats text inside a multi-line string literal as a trailing comment, so a '# <gate>-ok: reason' line in a docstring or triple-quoted string suppresses a finding

- Evidence: _gate_report.py:109-125 _comment_of sees one physical line, with no tokenizer state. Repro: source 'x = """a\n# foo-ok: y\n"""' -> line_has_marker(lines, 2, 'foo-ok:') is True (a string-only line). Per-gate regexes such as atomic_write_staging.py:43 run on the raw line with the same weakness.
- Failure scenario: A finding on line N is suppressed by a docstring example or fixture string mentioning the marker on that physical line.
- Proposed fix: Resolve markers from tokenize COMMENT tokens (one shared comments_by_line(source) per file) and make line_has_marker and the per-gate regexes use it.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED 3ba0809: line_has_marker ignores lines inside a multi-line string (tokenize, only when the text parses as Python). The per-gate regex markers (atomic_write_staging etc.) run on raw lines and are NOT changed: DEFERRED, waits on moving them to a shared comments_by_line.

## 42-F4 (P2) baseline_ratchet.Baseline.enforce exits 0 on stale entries and is key-only: a fixed violation leaves a slot that a later regression of the same key takes for free

- Evidence: baseline_ratchet.py:130,154-157: stale is only printed, then return 0. Repro (audit42/t2.py): accepted {a.py,b.py}, found {a.py} -> prints 'no longer violate ... prune' and returns 0; found {a.py,b.py} afterwards -> 'no new violations', 0. _text_ratchet.enforce_findings (line 84) uses this class.
- Failure scenario: b.py is cleaned, nobody runs the refresh because exit 0 hid it, months later b.py regresses under the same key and is accepted; the shrink-only rule is only as strong as the stale failure.
- Proposed fix: Return 1 on stale (like raise_for_pytest default), or at least when the key reappears.
- AST-checkable: False; FP risk: low
- Disposition: RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": baseline_ratchet.Baseline.enforce returns 1 on a stale entry (prune message names the refresh command), so the slot a fixed violation leaves cannot be taken back by a regression; tests test_a_fixed_violation_cannot_be_taken_back_for_free and test_paid_debt_fails_until_the_baseline_is_refreshed failed before. CHANGELOG Behaviour change entry; stale_baseline_sweep measured 0 stale entries in the six consumers it could run (pyutilz, llm_bench, algopacksimple, dash_app_core, flutter_uptime_monitor, claude-usage-notifier); mlframe, glossum_backend_scripts, autopsia (timeout at 1500 s) and social (collection errors) not measured.

## 42-F5 (P2) _core Baseline.enforce BaselineOutcome.ok ignores stale entries and surplus counts; callers that test only .ok accept up to the old count

- Evidence: baseline.py:229-231 ok = not (new or unjustified or missing). Repro (t1.py): baseline count 3, one finding -> ok True, stale ['k','k']; then 3 findings -> ok True. Callers testing only .ok: unexecuted_function_bodies.py:493, mutation_teeth.py:913.
- Failure scenario: After a partial paydown the gate keeps free slots; new violations pass until someone refreshes.
- Proposed fix: Make ok include stale (or add ok_strict) and migrate the .ok-only callers.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": BaselineOutcome.ok includes stale entries and surplus counts, new BaselineOutcome.blocking keeps the old meaning; mutation_teeth is strict through the property with no edit, unexecuted_function_bodies uses blocking to avoid a duplicate message; tests in tests/test_baseline_stale_strict.py and TestMultiset::test_a_surplus_count_cannot_be_refilled_for_free failed before. Same sweep result as 42-F4.

## 42-F6 (P3) parse and bytes caches trust a stat signature (mtime_ns, size, ino) for files older than 3s: a same-size rewrite with a preserved mtime serves the old tree

- Evidence: source.py:93-111, 206-215; the docstring promises an edited file is re-parsed however quickly it was rewritten. Repro: write 'x=11', utime to 100s ago, parse; rewrite 'x=22' (same size), os.utime back; parse_source returned 'x=11' while the disk held x=22.
- Failure scenario: A long-lived process (watcher, IDE integration, run after cp -p / rsync -t / tar restore) gates stale source and passes a broken file.
- Proposed fix: Add st_ctime_ns to the signature (utime cannot preserve it) and correct the docstring.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED 3ba0809 on POSIX: stat signature gains st_ctime_ns (test skipped on Windows). On Windows ctime is the creation time, so the gap remains there; documented in the code and CHANGELOG.

## 42-F7 (P3) List-form baseline entries are joined with '|', colliding with a plain string key containing '|'

- Evidence: baseline.py:102. Repro: JSON [["a","b"],"a|b"] loads as Counter({'a|b': 2}).
- Failure scenario: Two distinct accepted entries merge into one count; a pair can accept a literal key.
- Proposed fix: Use an unambiguous separator or reject a string containing it.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED 3ba0809: a list baseline holding a pair and the string it joins to raises BaselineError; test_a_pair_and_a_string_that_join_alike_are_refused.

## 42-F8 (P3) Duplicate JSON keys in a baseline resolve last-wins silently, so a bad merge can raise a count unnoticed

- Evidence: baseline.py:88 json.loads without object_pairs_hook. Repro: {"k":{"count":1},"k":{"count":9}} loads as k=9.
- Failure scenario: A merge or hand edit leaving two entries raises the allowed count with no growth refusal.
- Proposed fix: Parse with an object_pairs_hook that raises BaselineError on a duplicate key.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED 3ba0809: duplicate JSON keys raise BaselineError (Behaviour change entry); test_a_duplicate_json_key_is_refused.

## 42-F9 (P3) iter_files default pattern '*.py' is case-sensitive and ignores .pyi/.pyw although source.py decodes them as Python

- Evidence: corpus.py _matches uses fnmatchcase; source.py _PYTHON_SUFFIXES = .py .pyi .pyw. Repro: dir with U.PY and s.pyi -> scan_python(..., use_git=False).parsed_count == 0.
- Failure scenario: A file named X.PY (imports fine on Windows and macOS) or a stub is never scanned and produces no finding.
- Proposed fix: Match the suffix case-insensitively; scan stubs where gates parse them.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED by the commit "Stale baseline entries and surplus counts fail; include_stubs opt-in for runtime-hazard gates; fail-closed predicates exempt from silent_default_return; stale_baseline_sweep (audits 42-F4, 42-F5, 42-F9, 41-F7)": iter_files/scan_python take include_stubs=False (default unchanged; True matches .py/.pyi/.pyw case-insensitively). Opted in: unsafe_deserialization, predictable_tmp_path, systemexit_in_library, swallowed_exceptions, naive_utcnow (runtime-hazard gates). Deliberately NOT opted in: module-identity and graph gates (import_cycles, unresolved_imports, private_imports: x.py and x.pyi are one module), api_floor and signature/definition gates (a stub is not runtime code). No consumer checkout holds a .pyi/.pyw/.PY file, so the before/after delta on pcs and pyutilz and the other consumers is zero; tests/test_corpus_include_stubs.py shows each opted-in gate finding a violation in U.PY and w.pyw.

## 42-F10 (P3) Finding.key omits the line, so replacing one violation by another with an identical message in the same file passes the count check

- Evidence: findings.py:27 key = rule::path::message; the baseline is a multiset of keys. A gate whose message does not name the symbol cannot tell a swap from no change.
- Failure scenario: Fix violation A, add violation B elsewhere in the file: counts equal, gate green, the ratchet did not shrink.
- Proposed fix: Put the enclosing qualified name or normalized statement into the message; add a meta-test flagging messages without it.
- AST-checkable: False; FP risk: high
- Disposition: DEFERRED: needs enclosing-symbol text in many gates' messages (fp_risk high); belongs with a meta-test design, not a point fix.

## 42-F11 (P3) git_listing returns None (silent walk fallback) for dubious ownership, and the walk and git modes disagree on nested repositories

- Evidence: corpus.py git_listing docstring and _walk: only a .git FILE prunes a directory; a nested clone with a .git directory is dropped by git mode (listed as a bare directory, not is_file) but descended by the walk.
- Failure scenario: On a runner with safe.directory unset the corpus widens to ignored files and nested clones; findings differ between machines.
- Proposed fix: Raise CorpusError on dubious ownership when .git exists at root; make _walk prune any directory containing .git.
- AST-checkable: True; FP risk: low
- Disposition: RESOLVED: Code commit: "corpus: raise on dubious ownership, prune nested .git directories in the walk, slice relative paths (audits 42-F11, 48-F3)". Reproduced first: with GIT_TEST_ASSUME_DIFFERENT_OWNER=1 git refused a repo (rev-parse exit 128, 'dubious ownership'), git_listing returned None and iter_files listed a.py, ignored_out.py and the nested clone's vend/b.py where git mode listed only a.py. git_listing now raises CorpusError naming safe.directory (use_git=False still walks on purpose), and _walk prunes a child directory holding a .git DIRECTORY as it already pruned a .git FILE. Consumer measurement (read only): pyutilz 758 git / 764 walk files, llm_bench 174/199, glossum_backend_scripts 3150/3188, algopacksimple 176/213, py-ci-shared 1892/1879, mlframe 9879/40738: no repo has a nested .git directory or a dubious-ownership refusal, the walk membership is identical before and after, so no consumer corpus changes. Tests: nested clone, root-with-own-.git, dubious ownership raises, use_git=False unaffected, plain directory unaffected. CHANGELOG Behaviour change entries carry 'What to do:'.

