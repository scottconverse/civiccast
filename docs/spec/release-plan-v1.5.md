Status: SUPERSEDED -- historical plan from the pre-reset "v1.5" numbering. The current release is v1.0.0-rc.6 (see docs/releases/v1.0.0-rc.6-verification.md); v1.0.0-rc18 belongs to the retired WSL2 line and its record is in the archived repository.

## Goal

Make CivicCast resilient enough for real station operation by proving full
restore, safe update, rollback, and stronger quality gates without overstating
what has been packaged or field-proven.

## Required Outcomes

1. Full restore proof.
   - Backup database, media, config, station profile, schedules, captions,
     records, publish state, provider readiness, and credential metadata.
   - Restore into an isolated station profile.
   - Verify restored admin state, portal state, media playback metadata,
     captions, records, publish status, and provider readiness.
   - Report intentionally excluded items, including provider secret values,
     bearer tokens, admin password plaintext, and recovery code plaintext.

2. Safe update path.
   - Detect available update package or source.
   - Run pre-update checks and backup/checkpoint.
   - Apply update only inside an operator-visible maintenance window.
   - Run post-update safe-to-broadcast proof.
   - Block broadcast when update proof fails.

3. Rollback path.
   - Select rollback asset.
   - Execute rollback.
   - Verify restored runtime and safe-to-broadcast state.
   - Preserve operator-readable logs and support-bundle context.

4. Quality gate expansion.
   - Keep the first-meeting journey green.
   - Add restore and rollback regression coverage.
   - Add provider proof workflow checks from v1.4 to the required gate.
   - Add public-portal accessibility and responsive checks where feasible.
   - Add secret-leakage scans for proof reports, restore reports, and support
     bundles.

## First Slice

The first v1.5 slice turns restore readiness from a single checksum note into a
structured proof checklist. The restore API now reports each required surface,
the operator UI shows the checklist, and the support bundle carries the restore
context without secret values.

## Active Quality Gate

The v1.5 resilience gate is enforced by
`scripts/policy/check_v15_resilience_gate.py` and included in
`scripts/policy/run_all.py`. It checks that restore, update preflight,
maintenance-window, rollback artifact, rollback rehearsal, controlled
failed-update rehearsal, post-update proof, provider proof, and support-bundle
API surfaces remain in the generated OpenAPI contract, that
`UpdateRollbackStatus` exposes the resilience proof fields, and that committed
JSON proof artifacts under release evidence or tester handoff paths do not
contain raw secret-like values.

## Exit Criteria

- Full restore into an isolated profile passes and is documented.
- Update apply proof passes or fails safely with clear operator messaging.
- Rollback proof passes from at least one controlled failed-update scenario.
- Support bundle captures restore/update/rollback context without secrets.
- Required quality gate list is updated and enforced for future releases.
