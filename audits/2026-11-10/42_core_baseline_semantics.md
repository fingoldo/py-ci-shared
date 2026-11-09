# 42_core_baseline_semantics

Round 2026-11-10, dimension 42 (core baseline semantics: Baseline, shrink-only ratchets, keys, refresh, stale detection, key stability). Read-only audit; no file under `src/` or `tests/` was edited. Findings already in `audits/TRACKER.md` and `audits/2026-11-07/42_core_baseline_semantics.md` (42-F1 to 42-F11) are not re-reported. Repro script: scratchpad `audit42b/r1.py` (run with `PYTHONPATH` set to `src/`, in a temp directory outside the repo).

Scope read: `src/py_ci_shared/_core/baseline.py`, `_core/refresh.py`, `_core/findings.py`, `baseline_ratchet.py`. `baseline_hygiene.py`, `baseline_trend.py`, `stale_baseline_sweep.py` were not traced line by line in this round.

Checked and sound: `shrink_only` (drops absent or zero keys, lowers counts, grows only past `slack`); `write_ratchet` (the removals are written before the growth refusal is raised; a refused seeding leaves no file); `BaselineOutcome.ok`/`blocking` (stale and surplus fail); the multiset enforce loop; `_parse_list` plain-string vs pair clash; `_count` positive-int check; atomic write; refresh-option resolution order.

## 42-F12 (P2) A baseline in list form, or with a non-object `accepted`, reads as empty in baseline_ratchet, so an empty scan passes

- Where: `src/py_ci_shared/baseline_ratchet.py:70-82` (`Baseline.load`). The root is returned as `{}` when it is not a dict, and a non-dict `accepted` returns `{}`.
- Evidence (repro `r1.py`, case A): a file holding `["a.py", "b.py"]`, then `enforce({}, label=..., guidance=...)` returned `0` with the message "no new violations (0 accepted, baselined)". `load()` returned `{}`. Case A2, `{"accepted": ["a.py", "b.py"]}`, returns `0` the same way.
- Scenario: a consumer hand-writes or regenerates its ratchet baseline as a JSON list (the shape `_core` accepts). The baseline's entries are silently ignored. The fail-on-empty-scan guard in `enforce` (line 124) is keyed on `accepted`, which is now empty, so it does not fire. A rule whose violations have all been fixed, or whose scan broke, passes forever. A non-empty scan is still reported as new violations, but the reader is told the wrong thing: the baseline is "0 accepted".
- Proposed disposition: RESOLVED with fix (proposed). `load()` raises a `ValueError`-style error naming the file when the root is not an object, or `accepted` is present and not an object. `enforce` then prints the unreadable-baseline message and returns 1. Add a test with a list root and a list `accepted` that fails before the fix.

## 42-F13 (P2) A refresh after a rename deletes the human note of the renamed entry before the growth refusal, and the re-added entry comes back with no note

- Where: `src/py_ci_shared/_core/baseline.py:244-256` (`write_ratchet`) together with `Baseline.regenerate` (line 330-352). The shrink write removes the old key and the new key is refused.
- Evidence (repro `r1.py`, case C): a `_core` baseline `{"rule::src/a.py::msg": {count 1, note "why"}}`. The source file is renamed to `src/b.py`, and `enforce(["rule::src/b.py::msg"], refresh=True)` runs without the grow opt-in. Result: `ok=False`, `new=['rule::src/b.py::msg']`, and the message "refresh wrote only the removals ... it refused 1 addition". The file on disk is now empty: `load()` returns `(Counter(), {})`. The note "why" is gone.
- Scenario: a rename is the common case of a key change, and it is the only way the note is lost. Once the owner re-runs the refresh with `PY_CI_SHARED_REFRESH_ALLOW_GROW=1`, the entry is accepted with `new_note` (possibly the NEEDS-JUSTIFICATION marker), and the justification the owner wrote is not recoverable from the repo except through git history. The growth refusal names the new key but not the lost note.
- Proposed disposition: DEFERRED. The shrink-only rule and the key-by-path design are intentional (see 42-F10 on keys). The fix is small but changes the refresh contract, so it needs an owner decision. Recommendation: when a refresh refuses a key and removes a key whose note is non-empty in the same write, keep the removed entries' notes in the growth message (`old key  note: ...`) so the owner can carry the note to the new key. Owner to confirm the shape before implementation.

## 42-F14 (P3) baseline_ratchet.Baseline.load uses plain json.load, so a duplicate key is last-wins silently; 42-F8 fixed this only in _core

- Where: `src/py_ci_shared/baseline_ratchet.py:76-82` (`json.load(handle)`). `_core/baseline.py` `load_json` uses `_reject_duplicate_keys` (42-F8 fix); this module does not.
- Evidence (repro `r1.py`, case B): `{"accepted": {"a.py": "good reason", "a.py": "x"}}` loads as `{'a.py': 'x'}`. The first note is lost with no error.
- Impact is limited: ratchet keys are a set (not counts), so a duplicate can only change a note, and a duplicate left after a merge makes the reader trust the wrong justification. Still, it is the same defect class as 42-F8 in the sibling reader.
- Proposed disposition: RESOLVED with fix (proposed). Route `Baseline.load` through the `_core` duplicate-key-rejecting loader and add a test beside `test_a_duplicate_json_key_is_refused`.

## Summary

Three new findings in scope: 0 P0, 0 P1, 2 P2, 1 P3.

| Id | Sev | Finding | Proposed disposition |
|---|---|---|---|
| 42-F12 | P2 | List-form or non-object-`accepted` ratchet baseline reads as empty; empty scan exits 0 | RESOLVED with fix (proposed) |
| 42-F13 | P2 | Refresh after a rename deletes the entry's note before the growth refusal | DEFERRED (owner to confirm the refusal message shape) |
| 42-F14 | P3 | ratchet `load` is last-wins on duplicate keys; 42-F8 covered `_core` only | RESOLVED with fix (proposed) |

Counts: 3 new findings, of which 2 proposed RESOLVED with fix, 1 DEFERRED, 0 WON'T FIX. No finding was re-reported from the TRACKER or the 2026-11-07 round.

Not verified in this round: `baseline_hygiene.py`, `baseline_trend.py`, `stale_baseline_sweep.py`; the key stability of gate messages that embed line numbers (42-F10 already records that design choice).
