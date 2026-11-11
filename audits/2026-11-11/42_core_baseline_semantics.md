# 42 - Core baseline semantics (round 2026-11-11)

Read-only audit of `src/py_ci_shared/_core/baseline.py`, `src/py_ci_shared/baseline_ratchet.py`, and the
raw baseline readers `src/py_ci_shared/meta_test_utils.py` and `src/py_ci_shared/baseline_hygiene.py`.
Already-tracked items were checked against `audits/TRACKER.md` (42-F1..F14, RESOLVED or OPEN) before
being reported.

## Verified as correct (no finding)

- Missing file in `_core.Baseline.enforce` is a failure (`_core/baseline.py:382-386`).
- Unreadable, empty, or BOM-only file: `load_json` raises `BaselineError` (`_core/baseline.py:117-124`).
  Empty file is not read as empty.
- Duplicate keys rejected in both loaders (`_core/baseline.py:107-114`, `baseline_ratchet.py:87`).
- Non-object or non-dict `accepted` rejected in `baseline_ratchet.py:88-94`.
- Refresh is shrink-only: `shrink_only` (`_core/baseline.py:193-215`) keeps the old count when growth is
  refused, and `write_ratchet` writes the removals then raises `BaselineGrowthError`.
- Atomic write: mkstemp in the target directory and `os.replace` (`_core/baseline.py:82-99`).
- Unjustified marker is checked on every normal run in both modules
  (`_core/baseline.py:396-405`, `baseline_ratchet.py:151-158`).
- Notes survive a refresh in both modules (`_core/baseline.py:345`, `baseline_ratchet.py:177`).

## Already tracked

- Stale entries and surplus counts in `_core`: 42-F4, 42-F5 (RESOLVED).
- Key-only `baseline_ratchet.enforce`: covered by 42-F4 (RESOLVED per tracker). Not re-proposed.
- List-shaped and non-object baselines in `baseline_ratchet`: 42-F12 (RESOLVED).
- Duplicate-key last-wins in `baseline_ratchet`: 42-F14 (RESOLVED).
- Human note lost on rename before growth refusal: 42-F13 (OPEN, tracked; not re-proposed).

## New findings

## 42-S15 (P3) `meta_test_utils` reads the file's top-level keys, not its entries

- Evidence: `src/py_ci_shared/meta_test_utils.py:629`
  `known = set(json.loads(baseline_path.read_text(encoding="utf-8")))`.
  This is a set of the top-level keys of the JSON object. It is not the entry map. It does not unwrap
  `accepted`, `entries`, or the `_core` `{"schema", "gate", "entries"}` shape, and does not drop `_comment`
  metadata keys.
- Failure scenario: a baseline in the `_core` schema format
  `{"schema": 1, "gate": "x", "entries": {...}}` gives `known = {"schema", "gate", "entries"}`. Every real
  finding is reported as `new`, so the check fails on every run. Once the real violations are cleared, the
  phantom keys `schema`, `gate`, `entries` are reported as `drained` and the prune advice is wrong.
  A `_comment` key in a `{"_comment": ..., "accepted": {...}}` file is likewise treated as an entry.
- Failure is loud, not a silent pass. Severity is P3 because the caller set is unverified; if the
  `_core` shape is used with this helper, it is a permanent false failure.
- Disposition: RESOLVED. Waits on a caller inventory of `meta_test_utils.ratchet_diff`-style helpers. If
  any consumer uses the `_core` or `accepted` shape, RESOLVED-by-fix: parse through `_json_entries`
  (`baseline_hygiene.py:99`) or `_core.baseline._parse_entries`, and pin with a test on each shape.

## 42-S16 (P2) Growth opt-in in `baseline_ratchet` accepts new entries with the scanner's sentence as the note, not the unjustified marker

- Evidence: `src/py_ci_shared/baseline_ratchet.py:176-177`
  `return self._render({key: previous.get(key, found[key]) for key in kept})`.
  A new key gets `found[key]`, the scanner's description, as its note. `_core` writes `new_note`, and
  `_core/baseline.py:345` uses `self.new_note` for the same case.
- Failure scenario: a consumer runs `regenerate(found, grow=True)` (or sets
  `PY_CI_SHARED_REFRESH_ALLOW_GROW=1`) to accept a new violation. The entry is written with a
  non-marker note, so `enforce` (`baseline_ratchet.py:151`) never flags it as unjustified. The growth is
  accepted with no human reason recorded, which the module docstring (line 16-17) says must not happen.
  The growth is deliberate, so this is a silent-acceptance gap, not a bypass.
- Disposition: RESOLVED-by-fix proposed: write `NEEDS-JUSTIFICATION: <found description>` as the note for
  keys with no previous entry, so `enforce` rejects them until a human writes the reason. Needs a test
  that a grown entry fails `enforce` until its note is edited. Not verified by running; verified by reading
  lines 176-177 and 151-158.

## 42-S17 (P3) A missing `baseline_ratchet` file reads as an empty baseline, unlike `_core`

- Evidence: `src/py_ci_shared/baseline_ratchet.py:83-84` returns `{}` when the file does not exist.
  `_core/baseline.py:382-386` treats the same case as a failure (`missing=True`).
- Failure scenario: someone deletes `tool/meta/baselines/<name>.json`. If the scan is clean, `enforce`
  prints "no new violations (0 accepted, baselined)" and exits 0. If the scan finds anything, the run fails,
  so only the clean-scan case passes silently. The deletion is not reported.
- Disposition: WON'T FIX for now. The docstring at line 78 states the empty-mapping behaviour is
  intended ("nothing is recorded yet"). Revisit if a consumer reports a deleted baseline going unnoticed.

## 42-S18 (P3) `baseline_ratchet` empty-scan guard does not fire when the baseline is also empty

- Evidence: `src/py_ci_shared/baseline_ratchet.py:131` guards with `fail_on_empty_scan and not found and accepted`.
  With `accepted == {}` and `min_found=0` (default, `run_rules` line 215), an empty scan passes.
- Failure scenario: a rule whose glob stopped matching, against a baseline that records zero entries,
  passes forever. A later real violation is still caught, so the exposure is a dead rule, not a missed
  violation in the current tree.
- Disposition: WON'T FIX. The floor is opt-in by design (`min_found`, documented at lines 117-125). Consumers
  who want it set `min_found`. Recommend noting in the README that `min_found` is the only guard for an
  empty baseline.

## 42-S19 (P3) Concurrent refreshes race on the read-modify-write of the baseline

- Evidence: `_core/baseline.py:341-348` loads, computes, then writes; `baseline_ratchet.py:173-179`
  does the same. Nothing locks the file between the load and the `atomic_write_text`.
- Failure scenario: two refreshes of the same baseline run at once. Last writer wins. Each was shrink-only
  against its own read, so the result is a superset of stale entries (the loser's removal is lost). The
  next normal run fails loudly with "no longer found; prune", so no violation is silently accepted.
  Growth cannot be lost into the file either, since each writer's additions are refused unless opted in.
- Disposition: WON'T FIX. Refresh is a developer or single CI step; the failure mode is loud and
  self-correcting on the next run. Revisit only if refresh is ever run in parallel by a matrix job.

## Counts

- P0: 0. P1: 0. P2: 1 (42-F16). P3: 4 (42-F15, 42-F17, 42-F18, 42-F19).
- Already tracked and not re-proposed: 14 (42-F1 through 42-F14, all in TRACKER.md).
