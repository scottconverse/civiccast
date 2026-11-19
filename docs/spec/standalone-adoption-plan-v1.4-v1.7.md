# CivicCast Standalone Adoption Plan, v1.4-v1.7

Status: SUPERSEDED -- historical plan from the pre-reset v1.4-v1.7 numbering. The current release is v1.0.0-rc.6 (see docs/releases/v1.0.0-rc.6-verification.md); v1.0.0-rc18 belongs to the retired WSL2 line.
Created: 2026-05-30
Baseline at the time: v1.3.1 private beta Windows installer proof (historical).
Target: v1.7 announcement and early adoption readiness

## Purpose

This plan turns the current standalone CivicCast target list into four focused
development releases. It intentionally excludes CivicSuite integration, broad
governance/adoption logistics, and hardware work that CivicCast cannot directly
ship in software. It keeps the software work required to become a serious
open-source alternative for public meetings, community media, worship/nonprofit
streaming, and PEG-style channel operations.

The v1.7 bar is not "everything forever." The v1.7 bar is: a nontechnical
station can install CivicCast, prove core publication paths, operate meetings
and channel-style programming, recover from common failure modes, understand
legal/procurement posture, and invite early adopters without making claims the
software has not proven.

## Baseline: What v1.3.1 Already Proves

v1.3.1 is the private beta installer finish release.

- GitHub Release `.exe` is the preferred installer artifact.
- Checksum verification passed on the Windows tester.
- Windows installer, runtime launch, and first-run setup passed.
- First admin, recovery kit, storage bootstrap, private rehearsal, resident
  preview, backup/restore rehearsal, and support bundle passed.
- Provider lanes remain fail-closed unless proven.
- Public distribution, signing posture, live external-provider proof, granular
  roles, full restore, update/rollback, linear-channel operations, and connected
  TV platform support remain ahead.

## Release Strategy

The next four releases should each produce a release artifact, release notes,
proof evidence, and a clear claim boundary.

| Release | Public framing | Core outcome |
| --- | --- | --- |
| v1.4 | Operations beta | Provider proof workflow and role-aware operations |
| v1.5 | Resilience beta | Full restore, update/rollback, and stronger quality gates |
| v1.6 | Channel and CTV beta | industry-standard software channel operations and reference CTV support |
| v1.7 | Early adoption candidate | Public-facing docs, procurement/legal posture, support intake, and adoption-ready proof bundle |

## Non-Goals Across All Four Releases

- Hosted or managed CivicCast service.
- CivicSuite integration.
- Enterprise SSO.
- Claims of broad public fediverse interoperability before target-instance
  proof exists.
- Claims of SDI, DeckLink, or headend hardware compatibility before partner
  proof exists.
- Silent cloud fallback for AI or provider lanes.
- Rewriting working modules just to match the spec's future multi-repo topology.

## v1.4: Operations Beta

### Goal

Turn the v1.3.1 installer-first private beta into an operations beta by proving
selected external provider lanes and separating common station responsibilities
inside the operator console.

### Required Outcomes

1. Controlled provider proof lane.
   - Choose a first live-provider proof set from Internet Archive, local NAS,
     YouTube private/unlisted, email/webhook notifications, podcast feed
     discovery, and ActivityPub target-instance proof.
   - Record redacted durable evidence for each live pass.
   - Keep unproven providers out of release claims.

2. Provider proof workflow.
   - Setup and System Health guide operators from "credentials saved" to
     "controlled proof passed."
   - Include prove now, retry, skip, rotate, stale-proof warning, and redaction
     review paths.
   - Show plain-English operator errors.

3. Granular local roles.
   - Add setup/admin, meeting operator, records clerk, publish operator, and
     support/admin roles.
   - Enforce roles in backend routes and visible UI actions.
   - Keep read-only handoff views where helpful.

4. Observed beta walkthroughs.
   - Run at least one nontechnical operator walkthrough.
   - Run at least one technical-admin walkthrough.
   - Convert friction into product fixes, documentation fixes, or regression
     tests before release.

### Likely Work Areas

- `civiccast/auth/`
- `civiccast/installer/`
- `civiccast/publish/`
- `civiccast/schedule/`
- `civiccast/apps/portal-operator/`
- `civiccast/apps/portal-public/`
- `tests/installer/`
- `tests/publish/`
- operator Playwright tests
- docs and release evidence

### v1.4 Exit Criteria

- At least one external provider proof set passes from a controlled live run.
- Provider readiness remains fail-closed until proof evidence exists.
- Secret leakage review passes for provider proof and support artifacts.
- Backend role enforcement tests pass for touched staff APIs.
- Operator-console tests cover at least one restricted role workflow.
- Docs clearly distinguish credentials configured, proof pending, proof passed,
  skipped, failed, and stale proof states.

## v1.5: Resilience Beta

### Goal

Make CivicCast resilient enough for real station operation by proving full
restore, safe update, rollback, and broader quality gates.

### Required Outcomes

1. Full restore proof.
   - Backup DB, media, config, station profile, schedules, captions, records,
     publish state, and credential metadata.
   - Restore into an isolated station profile.
   - Verify restored admin state, portal state, media playback, captions,
     records, publish status, and provider readiness.
   - Clearly report anything intentionally excluded or requiring re-entry.

2. Safe update path.
   - Detect available update package or update source.
   - Run pre-update checks and backup/checkpoint.
   - Apply update.
   - Run post-update safe-to-broadcast proof.
   - Block broadcast when update proof fails.

3. Rollback path.
   - Select rollback asset.
   - Execute rollback.
   - Verify restored runtime and safe-to-broadcast state.
   - Preserve operator-readable logs and support bundle context.

4. Quality gate expansion.
   - Keep first-meeting journey green.
   - Add restore and rollback regression coverage.
   - Add provider proof workflow tests from v1.4 into the required gate.
   - Add accessibility and public-portal cross-browser/mobile checks where
     feasible.
   - Add secret-leakage scans for proof reports, restore reports, and support
     bundles.

### Likely Work Areas

- `civiccast/installer/`
- `civiccast/db/`
- `civiccast/assets/`
- `civiccast/publish/`
- `civiccast/apps/portal-operator/`
- `tests/installer/`
- `tests/integration/`
- release evidence and operator recovery docs

### v1.5 Exit Criteria

- Full restore into an isolated profile passes and is documented.
- Update apply proof passes or fails safely with clear operator messaging.
- Rollback proof passes from at least one controlled failed-update scenario.
- Support bundle captures restore/update/rollback context without secrets.
- Required quality gate list is updated and enforced for future releases.

## v1.6: Channel And CTV Beta

### Goal

Build the software pieces required for industry-standard channel operation and
connected TV reach, while leaving physical headend, SDI, DeckLink, and Comcast
delivery proof to partner validation.

### Required Outcomes

1. Linear channel profiles.
   - Support one or more station channels, such as public, education, and
     government channel equivalents.
   - Store channel identity, branding, programming rules, output settings, and
     fallback behavior.

2. Schedule-to-playout workflow.
   - Schedule live sources, file playback, slates, bulletin boards, reruns, and
     fallback blocks.
   - Handle gaps, underruns, and source failure with operator-visible states.
   - Produce now/next status per channel.

3. Channel proof logs.
   - Record what was scheduled, what actually played, and what failed over.
   - Export operator-readable and machine-readable proof logs.
   - Attach captions/sidecars where available.

4. Software outputs.
   - Support feasible software outputs such as HLS, RTMP, SRT, or NDI-style
     command planning where licensing permits.
   - Avoid claiming hardware compatibility until partner proof exists.

5. Reference CTV support.
   - Provide a stable public feed/API for live channels and VOD.
   - Build a shippable prototype that can inform later platform-specific apps.
   - Support live HLS, VOD playback, captions, station branding, predictable
     content IDs, and browse/search by meeting, series, date, body, or topic.
   - Document the path for later Fire TV, Apple TV, and Android TV work.

### Likely Work Areas

- `civiccast/cable/`
- `civiccast/live/`
- `civiccast/vod/`
- `civiccast/schedule/`
- `civiccast/cg/`
- `civiccast/apps/portal-public/`
- `civiccast/apps/`
- `tests/cable/`
- `tests/live/`
- `tests/vod/`
- channel operations docs

### v1.6 Exit Criteria

- At least one end-to-end channel schedule can play through live/file/slate or
  equivalent software states.
- Channel now/next and proof logs are visible to operators.
- Failure/fallback behavior is tested.
- Public feed/API can drive the reference CTV surface.
- Reference CTV app can browse and play at least live channel and VOD content
  from CivicCast test data.
- Documentation states exactly what is software-proven and what requires partner
  station hardware validation.

## v1.7: Early Adoption Candidate

### Goal

Make CivicCast ready to announce for early adopters without overclaiming:
downloadable, supportable, explainable, legally/procurement-legible, and backed
by a coherent proof bundle.

### Required Outcomes

1. Public release posture.
   - Decide public download path.
   - Resolve Windows signing or explicit unsigned-beta policy.
   - Provide a public install page that does not require private collaborator
     access.
   - Provide SmartScreen and WSL2 expectation language.

2. Support intake.
   - Provide a public support intake path for non-collaborators.
   - Define what logs/support bundles users should attach.
   - Define response expectations for early adopters.

3. Procurement and legal language.
   - Procurement language for CivicCast as open-source civic infrastructure.
   - Apache 2.0 and CC BY 4.0 explanation.
   - Data ownership and export language.
   - Public-records retention guidance with a clear non-legal-advice disclaimer.
   - Accessibility posture.
   - AI caption/summary disclaimer language.
   - Release policy and security reporting language.
   - Vendor replacement comparison language, especially industry-standard cost and
     lock-in framing.

4. Proof bundle.
   - Consolidate installer proof, provider proof, restore proof, update/rollback
     proof, channel/CTV proof, and known limitations.
   - Make proof easy to read from README, docs index, release notes, and release
     assets.

5. Early-adopter docs.
   - Quickstart.
   - Operator manual.
   - Technical admin manual.
   - Recovery guide.
   - Provider setup guide.
   - Channel operations guide.
   - Reference CTV guide.
   - Known limitations and support boundaries.

### Likely Work Areas

- `README.md`
- `CHANGELOG.md`
- `docs/`
- `docs/spec/`
- `docs/releases/`
- `docs/tester/`
- `civiccast/installer/`
- public/operator docs surfaces
- release scripts and evidence checks

### v1.7 Exit Criteria

- Public download and trust posture are documented and tested.
- Early adopters can install without private repo access.
- Release notes and known limits avoid unproven claims.
- Proof bundle is complete enough for a technical station partner to evaluate.
- Procurement/legal docs are plain-English and institution-readable.
- Support intake works for someone outside the core repo.
- v1.7 can be announced as an early adoption candidate, not as a finished
  enterprise appliance replacement.

## Cross-Release Quality Bar

Every release should include:

- Updated release plan.
- Release evidence page.
- Known limitations update.
- Secret leakage scan over evidence and support artifacts.
- Targeted backend tests.
- Relevant Playwright/operator journey tests.
- Docs link check or equivalent manual proof.
- Installer smoke rerun if installer, runtime bootstrap, first-admin setup, or
  packaging changes.
- Clear release claim boundary.

## Suggested Agent Pipeline Run Order

1. `2026-05-30-standalone-adoption-roadmap`
   - Scope: convert this plan into validated pipeline manifests and release
     scope locks.
   - Output: accepted roadmap, v1.4 manifest, and release-level scope locks.

2. v1.4 implementation run.
   - Scope: provider proof workflow and role-aware operations.

3. v1.5 implementation run.
   - Scope: full restore, update/rollback, and quality gates.

4. v1.6 implementation run.
   - Scope: channel operations and reference CTV.

5. v1.7 implementation run.
   - Scope: public release posture, docs, support intake, and proof bundle.

## Immediate Next Actions

1. Review this plan and decide whether v1.3.2 remains separate or folds into
   v1.7 public release posture.
2. Complete and validate the Agent Pipeline manifest for the v1.4 implementation
   run.
3. Select the first live provider proof set for v1.4.
4. Decide which role gets implemented first and which workflow proves it.
5. Identify the smallest isolated restore target for v1.5 planning.
6. Identify Longmont Public Media channel-operation assumptions that can be
   represented in software before hardware validation.
