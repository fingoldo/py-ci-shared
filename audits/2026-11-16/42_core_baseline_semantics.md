# 42 core_baseline_semantics: round 2026-11-16 (T)

Commit audited: 48e49b1. Scope: `_core/baseline.py`, `_core/findings.py`, `_core/corpus.py` (key spelling), `baseline_ratchet.py`, `_text_ratchet.py`,
`meta_test_utils.findings_ratchet`, `baseline_hygiene.py` (read for the note rules). Started from the 2026-11-11 file and TRACKER rows 42-S15..S19, 42-R12..R14, 42-F1..F11.
Experiments: `C:\Users\Admin\AppData\Local\Temp\claude\838757565bfd39b2\374112a1-797e-4d89-b4ed-a53928a70eeb\scratchpad\audit_T\42\e1.py` and `e2.py` (run with `python`; they write only to a temp dir).
Not covered: the per-gate scanners that build message text (whether each embeds a line number in its message), POSIX file modes (host is Windows), the consumers' real baselines.

## 42-T1 (P2) A growing refresh of a `_core` Baseline writes an EMPTY note, and `enforce` accepts it; only 3 modules pass `new_note`
- **Evidence:** `_core/baseline.py:302` `new_note=""` default; `:345` writes `notes.get(k) or self.new_note`; `is_unjustified("")` is False (`:58`).
  `grep new_note= src`: only `drifted_duplicate_literals.py:509`, `statement_real_engine_coverage.py:269`, `env_write_restore.py:250` pass one; about 76 other modules construct `Baseline(path, gate=...)` with the default.
  Run (e1.py E6/E7): `C(path, gate="c2").regenerate(["k","k"], grow=True)` writes `{"count": 2, "note": ""}`; then `enforce(["k","k"])` gives `c2: clean (2 accepted)`, `unjustified=[]`.
  The module docstring (`_core/baseline.py:10-13`) says a refresh cannot be used as "accept everything". 42-S16 closed exactly this hole for `baseline_ratchet` only (it writes the marker); the `_core` class was not covered.
  Only `baseline_hygiene` (opt-in per consumer) rejects a blank note.
- **Impact:** `PY_CI_SHARED_REFRESH_ALLOW_GROW=1` (or a `--py-ci-refresh-grow` flag) turns any set of new violations into silently accepted entries with no reason, for nearly every gate.
- **Proposed fix:** make `Baseline.new_note` default to `UNJUSTIFIED_MARKER + ": a refresh recorded this finding; replace this with the reason it is kept"`. BEHAVIOUR CHANGE (a grow refresh now leaves the gate red until notes are written; existing blank notes stay green). What to do: write a reason into each marked entry.
- **Disposition:** OPEN

## 42-T2 (P3) A real key starting with `_` is dropped as metadata by `baseline_ratchet` (bare form) and `meta_test_utils` (any form), but kept by `_core`
- **Evidence:** e2.py F1. `baseline_ratchet.py:97` filters `k.startswith("_")` on the bare `{key: note}` form; `meta_test_utils.py` `_baseline_entry_keys` filters after unwrapping `accepted`/`entries`. `_core` drops only the four names in `_META_KEYS` (`:127`).
  Output: `M.findings_ratchet(["_core/x.py: bad"], accepted-file)` returns new=`['_core/x.py: bad']` though accepted; `R.load()` of the bare file returns `{}` and `enforce` reports it as a new violation; `C.load()` of the same file keeps it.
  Repo-relative keys such as `_core/...` or `_private.py` are ordinary here.
- **Impact:** loud false red (no way to accept the entry in `meta_test_utils`; in `baseline_ratchet` only by switching to the `accepted` wrapper). The same file is read differently by two readers.
- **Proposed fix:** share `_core.baseline._META_KEYS` in both readers (drop only those names). ADDITIVE (accepts more).
- **Disposition:** OPEN

## 42-T3 (P3) `meta_test_utils.findings_ratchet` reads the baseline more loosely than every other reader
- **Evidence:** `meta_test_utils.py:645` `json.loads(read_text(encoding="utf-8"))`. e1.py E1: a BOM-prefixed file raises a bare `JSONDecodeError` (`baseline_ratchet` and `_core` read with `utf-8-sig`). e1.py E3 / e2.py F4: duplicate keys are last-wins (`{"a":"x","a":"y"}` returns `([], [])`; a duplicated top-level `accepted` silently uses the last); the guard `_reject_duplicate_keys` exists in `_core.baseline` but is not used. Notes are ignored, so a `NEEDS-JUSTIFICATION` entry passes. An empty file gives a bare `JSONDecodeError` without the file name (e1 E2).
- **Impact:** a Windows editor that saves a BOM turns the test into a crash with no file name; a bad merge raises or hides a count unnoticed.
- **Proposed fix:** call `_core.baseline.load_json` (BOM, duplicate keys, named error) and fail on marked notes. BEHAVIOUR CHANGE for the duplicate-key and marker cases; the BOM case is ADDITIVE.
- **Disposition:** OPEN

## 42-T4 (P3) `baseline_ratchet` does not see the marker or a blank note in the `{"count","note"}` entry shape
- **Evidence:** e2.py F8: `{"accepted": {"k": {"note": "NEEDS-JUSTIFICATION"}}}` gives `enforce` exit 0. `baseline_ratchet.load` returns the dict value unchanged and `is_unjustified(dict)` is False (`baseline_ratchet.py:152`), while `_core._entry` unwraps `value["note"]`. A null or int note (e1 E5b/E5c) and an empty string (E5) are also accepted, exit 0.
- **Impact:** a hand-edited entry in the `_core` shape inside a `baseline_ratchet` file bypasses the justification check. Needs a hand edit, so low.
- **Proposed fix:** in `load`, normalise each value to a note string (dict to `note`, non-str rejected with `ValueError`) and treat a blank note as unjustified. BEHAVIOUR CHANGE for blank notes (What to do: add a reason).
- **Disposition:** OPEN

## 42-T5 (P3) The `gate` and `schema` headers of a `_core` baseline are written but never checked on load
- **Evidence:** e2.py F2: gate A's file copied to gate B's path; `Baseline(b.json, gate="B").enforce(["k"])` gives `B: clean (1 accepted)`. `_core/baseline.py:311-319` ignores `data["gate"]` and `schema`. A schema of 2 would also be read as 1.
- **Impact:** two gates sharing one key space is possible through a wrong `baseline_path=` or a copy-paste; keys such as bare paths are then accepted by the wrong gate. No two shipped modules share a path (grep of `gate="..."`: the only repeats, `audit-wave-filenames` and `deferred-debt`, are within one module each).
- **Proposed fix:** in `load`, raise `BaselineError` when a present `gate` differs from `self.gate` (skip when absent) and when `schema` is above `SCHEMA_VERSION`. BEHAVIOUR CHANGE (a mis-pointed file turns red; What to do: fix the path or the header).
- **Disposition:** OPEN

## Verified correct (re-demonstrated, no finding)
- Windows path spelling: `Finding("src\\a.py")` keys differently from `src/a.py` (e1 E9), but every shipped producer goes through `corpus.py` `.as_posix()` (`corpus.py:175,309-315`), so keys are POSIX on both OSes. The one non-posix producer, `vulture_warn.py:63` `os.path.relpath`, is advisory (no baseline).
- Missing, empty, corrupt, BOM: `_core` missing is a failure with the refresh command; an empty file raises `BaselineError` naming the file; a BOM is read (`utf-8-sig`, e2 F3); a refresh over an unreadable file raises and leaves it untouched (e2 F10). `baseline_ratchet` reads a BOM (e1 E1).
- Stale entries and surplus counts fail in both (e2 F6: count 3 vs 1 found lists 2 stale). Output is sorted; `dump_json` uses `sort_keys`; writes use mkstemp + fsync + `os.replace` with a Windows retry.
- The marker cannot be written back as accepted by `baseline_ratchet.regenerate` (F7: grow refresh then `enforce` exits 1 with the marker listed). `is_unjustified` strips leading whitespace but is case- and spelling-exact.
- Empty-scan guard (`baseline_ratchet.py:132`) fires only when accepted is non-empty; `_core` has no such guard because every accepted entry becomes stale and fails. Both already dispositioned (42-S18, 42-F5).

## Considered and not raised
- Missing `baseline_ratchet` file reads as empty (42-S17), empty baseline with empty scan (42-S18), refresh race (42-S19), key without line (42-F10): WON'T FIX earlier; no new evidence.
- Ordinal keys `k #2`, `k #3` (`_text_ratchet.record_finding`): deleting the first of three identical lines leaves `#3` stale and the run fails loudly, so no silent pass (e2 F5).
- mkstemp creates files mode 0600, so a refreshed baseline on POSIX could lose its 0644: NOT REPRODUCED (Windows host; git stores only the exec bit, so no committed effect).
- Case differences in keys on a case-insensitive filesystem: keys come from the directory listing's spelling, so the same tree gives the same key; a hand-typed lower-case key fails loudly as new plus stale.
- Duplicate `gate=` names across modules: both repeats are inside one module.

## Counts
P0 0, P1 0, P2 1, P3 4.

| id | sev | title |
|---|---|---|
| 42-T1 | P2 | grow refresh of a `_core` Baseline writes a blank note that enforce accepts |
| 42-T2 | P3 | `_`-prefixed real keys dropped as metadata by `baseline_ratchet` bare form and `meta_test_utils` |
| 42-T3 | P3 | `findings_ratchet` ignores BOM, duplicate keys, marker |
| 42-T4 | P3 | `baseline_ratchet` misses the marker or a blank note in dict or non-str notes |
| 42-T5 | P3 | `gate` and `schema` headers of a `_core` baseline unchecked |
