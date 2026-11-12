# Dimension 41: gate false positives (round 2026-11-11)

Read-only audit of `src/py_ci_shared` and `tests`. Read first: `audits/README.md`, `audits/TRACKER.md`, and the
round 2026-11-10 report `audits/2026-11-10/41_gate_false_positives.md` (plus the 2026-11-07 tracker rows 41-F1 to
41-F14). No file under `src/` or `tests/` was edited. The one probe was run from the scratchpad with `python -I`
against `src/` on a synthetic snippet.

Numbering note: tracker rows already use `41-F1` to `41-F14` (2026-11-07 round) and `41-F1` to `41-F4` (2026-11-10
round) for different findings. New findings here start at `41-F15` to avoid collision.

## Already tracked (not re-reported)

- `printed_advice` treats the noun "supply" as advice: tracker row 41-F1 of round 2026-11-10 (already tracked: 41-F1, RESOLVED 3ba0809 per tracker). Re-verified: the bare `supply \w+` branch is still in `ADVICE_RE`, so the RESOLVED claim should be checked against the current source; not counted as new.
- `systemexit_in_library` attributes `os._exit()` to SystemExit: tracker row 41-F2 of round 2026-11-10 (already tracked: 41-F2, RESOLVED).
- `secret_assertion_operands` literal-name and bound-name forms: already tracked as 37-F22 and 41-F11 (2026-11-07 round); the substring matching below is a different defect.

## 41-S15 (P2) secret_assertion_operands matches secret words as substrings, so ordinary env names are flagged

- Location: `src/py_ci_shared/secret_assertion_operands.py:70`, `SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW(OR)?D|DSN|CREDENTIAL|AUTH|COOKIE|PRIVATE)", re.IGNORECASE)`, used by `_secret_name` (line 84) through `.search`, which matches anywhere in the name.
- Evidence (probe, `offenders_in_source` on a test module):
  - `assert os.environ.get("AUTHOR_NAME") == "x"` flagged as `os.environ.get('AUTHOR_NAME')`
  - `assert os.getenv("MONKEY_MODE") == "1"` flagged as `os.getenv('MONKEY_MODE')`
  - `assert os.environ.get("KEYBOARD_LAYOUT") == "us"` flagged as `os.environ.get('KEYBOARD_LAYOUT')`
- Scenario: a test asserts the value of a non-secret setting whose name contains "AUTH", "KEY" or "DSN" as a substring (for example `AUTHORIZATION_MODE`, `KEYBOARD_LAYOUT`, `MONKEY_PATCH`, `PRIVATE_NETWORK_ONLY` is correctly flagged, but `DSNS_COUNT` or `OAUTHLIB_INSECURE` is not a secret). The gate reports the assertion as leaking a secret, the author has to rename the env var, add an allowlist line, or drop the assert. The `_harmless_env_read` path (line 116) is supposed to make such non-secret literal reads harmless, and it is defeated by the same substring test.
- Correct behaviour: match whole underscore-delimited words (split the name on `_` and compare tokens, allowing a plural `S`), so `AUTH`, `KEY`, `TOKEN` etc. match `MY_API_KEY` and `AUTH_TOKEN` but not `AUTHOR_NAME`, `MONKEY_MODE` or `KEYBOARD_LAYOUT`. The `SESSION`/`PWD` exclusion comment at line 68 shows the module already intends word-level judgement.
- Proposed disposition: RESOLVED-by-fix (proposed). Replace the `.search` on the raw name with a token check, keep `PASSWORD`/`PASSWD`/`PASSWORDS` and `CREDENTIALS` as tokens, and add a test in `tests/` that `assert os.environ.get("AUTHOR_NAME") == "x"` and `MONKEY_MODE` yield no offenders while `assert os.environ.get("API_KEY") == "x"` and `AUTH_TOKEN` still do. Not applied, because this auditor is read-only. Note the `_URL_NAME` pattern `^DATABASE` and `_UR[LI]$` also match by substring at the start/end and should get the same token treatment (`DATABASE_*` is correct as a token; `URLENCODED_MODE` is not a URL, but `_UR[LI]$` does not match it, so this part is fine).
- Severity note: P2. It fails a gate on correct test code with a workaround (rename or allowlist) that is cheap but teaches the wrong lesson, and the defect is in a fail-closed secret gate, so over-matching is the safe direction but still produces noise on common names.

---

## Probes that came back clean (no finding)

- `_harmless_env_read` treats a literal non-secret name such as `HOME` as harmless (checked by reading; the module's documented pytest behaviour is consistent with the code).

## Summary

One new finding in dimension 41 (41-F15, P2). Already tracked and not re-counted: 2 items of round 2026-11-10 (41-F1, 41-F2). Probing was bounded to the secret-assertion gate and the two prior-round findings; the remaining regex-based gates were not exhaustively probed in this pass.

Counts by severity (new findings): P0 0, P1 0, P2 1, P3 0.
