# CivicCast v1.7 Early Adoption Candidate Plan

Status: SUPERSEDED — historical plan from the pre-reset "v1.7" numbering. The current release is v1.0.0-rc.6 (see docs/releases/v1.0.0-rc.6-verification.md).
Created: 2026-05-31
Baseline: v1.6.0 channel and CTV beta

## Goal

v1.7 makes CivicCast ready to announce to early adopters without overclaiming.
The release should be downloadable, supportable, explainable, legally and
procurement-legible, and backed by a coherent proof bundle.

This is not a Roku release, not a hardware-output release, and not a finished
enterprise appliance replacement. The v1.7 claim is early adoption readiness for
software-owned CivicCast workflows.

## Required Outcomes

1. Public release posture.
   - Public download path is GitHub Releases.
   - Windows setup guidance explains checksum verification, SmartScreen,
     unsigned-beta status, and WSL2 expectations.
   - Source ZIPs are clearly separated from installer artifacts.

2. Support intake.
   - Non-collaborators know where to ask for help.
   - Bug reports identify the version, operating system, screen, System Health
     state, and support bundle guidance.
   - Security reports use private email, not public issues.
   - Support expectations are clear for early adopters.

3. Procurement and legal posture.
   - Procurement language explains CivicCast as open-source civic
     infrastructure.
   - Apache-2.0 code and CC BY 4.0 documentation licensing are explained.
   - Data ownership and export posture are plain-English.
   - Public-records retention guidance is framed as not legal advice.
   - Accessibility posture and AI caption/summary disclaimers are explicit.
   - industry-standard comparison language focuses on cost, ownership, and
     lock-in without claiming unproven one-for-one parity.

4. Proof bundle.
   - Evidence from v1.3.1 installer proof, v1.4 operations proof, v1.5
     resilience proof, and v1.6 channel/CTV proof is consolidated.
   - Known limits are easy to find.
   - Future hardware/platform proof remains separate from current release
     claims.

5. Quality gate.
   - A v1.7 adoption-readiness policy check blocks missing docs, draft-only
     docs, or obvious overclaims.
   - Release identity, generated API docs, policy checks, and targeted doc tests
     pass before merge/tag.

## Non-Goals

- Roku Channel Store publication or platform certification.
- Fire TV, Apple TV, Android TV, or DRM implementation.
- SDI, DeckLink, Comcast, or physical headend delivery proof.
- Hosted or managed CivicCast service.
- Legal advice, procurement certification, or records-retention certification.
- Hiding unfinished work with marketing copy.

## Work Slices

### Slice 1: Adoption Scaffold And Policy Gate

- Add this release plan.
- Add early-adopter quickstart, support intake, procurement/legal posture,
  release policy, and v1.7 proof-bundle documents.
- Add policy enforcement and tests for the v1.7 readiness docs.
- Run audit-lite and commit/push.

### Slice 2: Public-Facing Update

- Update the public holding-page draft for the v1.7 early adoption candidate.
- Update GitHub Discussion seeds for early adopters, support, and partner proof.
- Update README and docs index so early-adopter materials are discoverable.
- Run audit-lite and commit/push.

### Slice 3: Release Identity And Verification

- Bump runtime, generated API docs, and installer metadata to `1.7.0`.
- Add `docs/releases/v1.7.0-verification.md`.
- Run targeted release identity, generated artifact, policy, docs, and frontend
  checks.
- Run audit-lite and commit/push.

### Release Close-Out

- Run audit-full.
- Fix all findings when found.
- Merge to `main`, tag `v1.7.0`, and push to GitHub only after the audit is
  clean or a human REQUIRE blocker is explicit.

## Exit Criteria

- Public download, trust, support, procurement/legal, release policy, and proof
  bundle docs exist and are linked from the docs index.
- Early adopters can understand how to install from GitHub Releases without
  private repository access.
- Support and security intake routes are clear.
- Release notes and public-facing docs avoid unproven hardware, platform,
  compliance, or parity claims.
- `scripts/policy/check_v17_adoption_gate.py` passes locally and through
  `scripts/policy/run_all.py`.
