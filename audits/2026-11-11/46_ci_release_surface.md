# 46_ci_release_surface

Round 2026-11-11, dimension 46 (CI and release surface). Read-only. Read first: audits/README.md, audits/TRACKER.md,
audits/2026-11-10/46_ci_release_surface.md (46-F1..46-F21, all already tracked). Nothing edited, pushed, tagged or released.

Scope read this round: `.github/scripts/release_guard.py` (full, 430 lines), `.github/workflows/release.yml` (full, 211 lines).
The other 13 workflow files and the constraints files were NOT re-read here; the 2026-11-10 report covers them.

## Already tracked (not re-reported)

- already tracked: 46-F15 (transient API error in ci-green wait loop). Code is in place: `_api_with_retries` (release_guard.py:277-290), `wait_for_ci` returns pending (release_guard.py:313-315).
- already tracked: 46-F16 (rollback skips the published-release check when the API is unreadable). Still present: `_release_names` returns None on OSError (release_guard.py:352-354), `rollback_problem` skips the check on None (release_guard.py:82).
- already tracked: 46-F17 (prepare/verify fall back to local tags when the API is unreadable). Still present: `_published_release` (release_guard.py:216-220). Failure mode: in verify, the tag being released is itself among the local tags, so `release_edits` raises "not ahead of the newest published release" (release_guard.py:201-202) and the message blames the version, not the API.
- already tracked: 46-F2 (cancelled self-ci), 46-F11 (ci_verdict accepts any event), 46-F12, 46-F9 (RELEASE_TOKEN is an environment secret per CLAUDE.md, accepted risk).
- already tracked: 46-F1..F21 rows in audits/TRACKER.md lines 1156-1169 and 29-35. No new finding overlaps them.

## Checked and clean

- release.yml: top-level `permissions: contents: read`; write scopes only on `publish` and `rollback` jobs (release.yml:123-124, 179-181).
- release.yml: `persist-credentials: false` on every checkout (lines 50, 131, 186). RELEASE_TOKEN reaches only the push steps (lines 145, 197) as a GIT_CONFIG header, not argv.
- release.yml: `inputs.rollback-to` and `github.ref_name` reach the shell only through `env:`, not `${{ }}` in `run:`.
- release.yml: `rollback` job is gated to `refs/heads/master` (line 176), so a dispatch from a feature branch cannot run its release_guard.py next to the token.
- Idempotency: `publish` skips the tag move when the major already points at the target (release.yml:149-152) and skips release creation when it exists (168-172).
- `ci_verdict` never returns green for a pending or failed run (release_guard.py:107-118): a green needs a `success` conclusion.
- `sha_problem` rejects a short SHA (release_guard.py:335-339), so a short SHA cannot silently find no run and pass.

## 46-S22 (P3) rollback and prepare read only the first 100 releases, so a target older than the newest 100 is refused as unpublished

- Evidence: `.github/scripts/release_guard.py:352` (`_release_names`: `releases?per_page=100`, no pagination) and `:217` (`_published_release`, same). The GitHub releases list is newest-first and is not paginated here, so a release outside the first page is absent from `published`.
- Failure scenario: `rollback --repo` (release_guard.py:82-84) is given an old good release, e.g. v1.5.0, that has fallen out of the newest 100. The check reports "has no GitHub release (its Release run failed or never ran)" and the emergency rollback is refused exactly when it is needed. This is a false red, not a fail-open: the failure is loud. `_published_release` would instead return a wrong "newest published" only if the 100 newest releases omit the highest version, which requires a back-port published after 100 newer releases; low risk.
- Proposed disposition: follow the `Link: rel="next"` header, or pass `?per_page=100&page=N` until an empty page, in both places (or share one helper). Add a unit test with a fake `_github` returning two pages where the target is only on page 2. Severity P3 because the emergency path still works with a more recent release.
- AST/offline checkable: yes (fake paginated `_github`).
- Disposition: RESOLVED.

## 46-S23 (P3) a client error from the GitHub API in ci-green exits with a Python traceback instead of a `::error::` verdict line

- Evidence: `.github/scripts/release_guard.py:282-284` (`_api_with_retries` re-raises any `HTTPError` with code < 500, which includes 401, 403 and 429 rate limit), and `:312-315` (`wait_for_ci` catches only `TransientApiError`, so the re-raised `HTTPError` escapes `main()` at line 414). The exit is rc 1 from an uncaught exception.
- Failure scenario: a token with the wrong scope, or a 403 rate-limit reply during verify, produces a traceback with no `::error::` line. The exit code is still non-zero, so the release is not published, and no false green results. The only cost is an unreadable message. This is the behaviour the 46-F15 disposition explicitly accepted ("a 4xx still raises"), so it is recorded here as a residue of that accepted design, not as a new defect in the fix.
- Proposed disposition: in `main()`, catch `urllib.error.HTTPError` around the `wait_for_ci` call and print `::error::GitHub API answered {code}: {reason}` and return 1. Test: fake `api` raising `HTTPError(403)` makes `main` return 1 and print the `::error::` line. Severity P3.
- AST/offline checkable: yes.
- Disposition: RESOLVED.

## Summary

- New findings: P0 0, P1 0, P2 0, P3 2 (46-F22, 46-F23).
- Already tracked: 21 (46-F1..46-F21), of which those touching this scope were re-checked in place.
- Scope limit: only release_guard.py and release.yml were read in full this round; the other workflows were not re-verified.
