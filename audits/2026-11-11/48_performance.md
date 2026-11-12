# 48 Performance (round 2026-11-11)

Read-only. No source or test file was edited, the full suite was not run, and no benchmark longer than a single git call
was run. Ids 48-F1 to 48-F11 (round 2026-11-07) and 48-F12 to 48-F17 (round 2026-11-10) are already in `audits/TRACKER.md`
and were not re-measured here. Machine: Windows 11, loaded, so wall-clock figures are loaded measurements.

## Already tracked

- 48-F12 (`baseline_trend.history`, one `git show` per commit): already tracked: 48-F12.
- 48-F13 (`hook_attestation._hash_at`, one `git show` per commit in range): already tracked: 48-F13.
- 48-F14 (floor-compatibility test re-parses modules): already tracked: 48-F14.
- 48-F15 (per-test git repository setup in test files): already tracked: 48-F15.
- 48-F16 (`closed_audit_rounds._declared_edit`, one log per changed file): already tracked: 48-F16.
- 48-F17 (xdist CPU sample, 0.5 s per session): already tracked: 48-F17.

## Scope and method

- Searched every `subprocess` and `run_git`/`_git` call site under `src/py_ci_shared` (about 105 sites) and checked whether each
  sits inside a loop, a per-file or per-worktree iteration, or a per-commit iteration.
- Listed module-level caches and `lru_cache`/`cache` uses to check for unbounded growth.
- Checked the closed-round, tracker-parity, worktree-hygiene, corpus, committed-line-endings and script-entry-point paths.
- Counted repository facts that drive scale: `git worktree list --porcelain` (60 entries, 59 linked), `.claude/worktrees`
  (19 directories), `git ls-tree -r origin/HEAD` (1,953 entries), `git rev-list --count --all` (828 commits).

## Findings

- 48-F18 P2: `worktree_hygiene.worktree_findings` repeats whole-tree and per-worktree git probes for each of the 59 linked
  worktrees.
  Evidence: `src/py_ci_shared/worktree_hygiene.py:362` loops `for worktree in registered_worktrees(repo)[1:]`. Per iteration it calls
  `unsaved_paths(repo, worktree, ref, reachable=reachable)` (line 366), which runs `_upstream_blobs(repo, ref)` (line 278, `git ls-tree -r`
  over the whole ref) and `_object_format(repo)` (line 280, `git rev-parse --show-object-format`), then `_status_entries` (`git status`)
  and, for staged entries, `_staged_blobs` (`git ls-files -s`). It then calls `_head_is_on_a_remote` (line 373), which spawns
  `git rev-parse HEAD` and `git branch -r --contains <head>` (lines 349-350). `reachable_objects` (the one `rev-list --objects`) is
  already hoisted out of the loop at line 359, so the same pattern was fixed for reachability but not for the upstream tree.
  Measurement: this checkout has 60 worktree entries, so the loop runs 59 times. `git ls-tree -r origin/HEAD` measured 236 ms
  (1,953 entries, loaded). The upstream tree is identical for every worktree, so about 59 x 0.24 s = about 14 s is repeated
  work. Adding the `rev-parse --show-object-format` spawn (about 0.08 s each) and the two `_head_is_on_a_remote` spawns (about
  0.17 s each) brings the per-scan spawn cost to roughly 25 to 30 s loaded. The `_upstream_blobs` cost grows with the tree
  size and the `_head_is_on_a_remote` cost with history length, so the repeated cost rises with both. The command is the
  `worktree_hygiene` CLI report (`src/py_ci_shared/cli.py` tool entry); it is not in the default gate path, so the impact is on
  the operator who runs the hygiene report, not on every push.
  Proposed disposition: compute `upstream = _upstream_blobs(repo, ref)` and `object_format = _object_format(repo)` once in
  `worktree_findings` and pass them into `unsaved_paths` (add optional keyword parameters, default None, so the public signature
  stays compatible). For `_head_is_on_a_remote`, run one `git branch -r --contains` per distinct HEAD, or one
  `git for-each-ref --contains <head> refs/remotes` per worktree, and memoise by head sha. Expected saving: about 58 `ls-tree`
  and 58 `rev-parse` spawns removed (about 19 s on this checkout, loaded), with the verdicts unchanged. Add a test that counts
  `ls-tree` spawns over a three-worktree fixture (expected 1, was 3).

## Non-findings checked

- `committed_line_endings`: already batches blob reads through one `git cat-file --batch` (line 105). It runs two `ls-files`
  listings over the same index (`--eol` and `-s`, lines 80 and 94). Each is a single spawn per gate call; not a finding.
- `closed_audit_rounds._removed_hunks` (lines 72-93): `lines[i + 1 :]` copies the rest of the diff once per hunk. Cost is
  O(hunks x diff lines) of pointer copies; for a rewrite of a closed round with a few hundred hunks it is milliseconds. Not a finding.
- `script_entry_points.py:401-402`: `next(f.rel for f in corpus.scan if f.path == path)` is a linear scan per entry point.
  Entry points are tens and the scan is hundreds of files; the per-entry child interpreter (line 407) dominates. Not a finding.
- `sibling_floor_skew._git_show_version` (line 253): one `git show` per sibling dependency pinned to a git ref. Bounded by the
  number of such pins, not by history length. Not a finding.
- `api_floor` (line 228) and `black_filtered_apply` (line 389): subprocess calls already batch their file arguments. Not a finding.
- `tracker_summary_parity._round_headings` (line 177): cache keyed by audits directory and invalidated by per-file stat. The
  dictionary has one entry per audits directory. Not a finding.
- `_core/corpus.py` and `_core/git.py`: the work-tree probe is latched per root (HOT-1, resolved). Not a finding.
- `code_audit/_base.py` source-line cache (`_SRC_LINES_CACHE`, line 30) and `comment_names_missing_symbol` cache: LRU bounded at
  20,000 entries. Not a finding.
- `field_text_agreement` (`lru_cache(maxsize=4096)`), `meta_test_utils` (`lru_cache(maxsize=8)`): bounded. Not a finding.

## Summary

| Id | Sev | Area | Measured or counted cost | Status |
|---|---|---|---|---|
| 48-S18 | P2 | `worktree_hygiene.worktree_findings`: `ls-tree -r` and `rev-parse` repeated per linked worktree | 59 linked worktrees; ls-tree 236 ms each; about 19 s repeated on this checkout (loaded) | proposed |

Counts: 1 new finding (0 P0, 0 P1, 1 P2, 0 P3). Already tracked: 6 (48-F12, 48-F13, 48-F14, 48-F15, 48-F16, 48-F17).

## Caveats

- The 59-worktree figure is this checkout's state at audit time; the saving scales with the number of linked worktrees.
- The per-spawn figures are single-run loaded measurements. The 19 s estimate multiplies one measured `ls-tree` by 58 repeats;
  it was not measured end to end on the full worktree set.
