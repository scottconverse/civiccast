# CivicCast CI And Proof Matrix

Status: HISTORICAL claim-to-evidence map for the retired WSL2-line v3.0.0-beta1 release. It is not the current release map; the current line is v1.0.0-rc.6 (see `docs/releases/v1.0.0-rc.6-verification.md`).

This matrix explains what kind of proof backs each release claim. It separates
automated CI, local developer verification, release artifact proof, clean
Windows tester proof, and work that still needs real-world evidence.

## Evidence Types

| Evidence type | Meaning |
| --- | --- |
| GitHub Actions | Runs in GitHub CI from repository workflows. |
| Local verification | Ran on the release-owner development machine before release. |
| Release artifact proof | Built or verified from tagged release artifacts. |
| Clean Windows tester proof | Ran from published release assets on a separate Windows tester machine. |
| Manual human test | Needs a person with no project context to run and report confusion. |
| External provider proof | Needs real credentials and a redacted live round trip. |

## Current Matrix

| Claim | Current evidence | Status |
| --- | --- | --- |
| Source tests run on normal development changes | `.github/workflows/ci-test.yml` | Automated CI lane exists |
| Lint and policy checks run in CI | `.github/workflows/ci-lint.yml`, `.github/workflows/ci-docs.yml` | Automated CI lane exists |
| Operator frontend builds | `.github/workflows/ci-operator-build.yml`; PR #173 operator build check | Automated CI lane plus current beta-baseline PR check |
| Accessibility checks have a CI lane | `.github/workflows/ci-a11y.yml` | Automated CI lane exists |
| Release artifacts can be built | `.github/workflows/native-beta-candidate-artifacts.yml` and `.github/workflows/publish-staged-kit.yml`; v2.0.8 GitHub Release assets | Automated lane plus published release assets |
| Cleanroom E2E has a CI lane | **NO LANE IN THIS REPOSITORY.** `ci-cleanroom-e2e.yml` was the Docker/Linux full-install gate and did not come across with the retired lane. | The native line has no automated full-install e2e gate. `vm-cleanroom-release.yml` is not a substitute -- see its row. |
| VM cleanroom release has a CI lane | `.github/workflows/vm-cleanroom-release.yml` | Lane exists but is `workflow_dispatch`-only, targets a `self-hosted, linux` runner, and `scripts/run_vm_cleanroom_release.py` computes an install PLAN rather than performing an install. It has never run in this repository. |
| AI release proof has a CI lane | `.github/workflows/ai-release-proof.yml` | Automated lane exists; hardware-specific performance still needs field proof |
| Caption runtime benchmark has a CI lane | `.github/workflows/benchmark-caption-runtime.yml` | Automated lane exists |
| External provider proof has a CI lane | `.github/workflows/external-provider-proof.yml` | Harness exists; live provider credentials/proofs are station-specific |
| Loudness compliance has a CI lane | `.github/workflows/loudness-compliance.yml` | Automated lane exists |
| Six-hour and nightly soak lanes exist | `.github/workflows/six-hour-soak.yml`, `.github/workflows/nightly-publish-soak.yml` | Automated lanes exist; production operations still need field evidence |
| PDF/A verification has a CI lane | `.github/workflows/verapdf.yml` | Automated lane exists |
| v3.0.0-beta1 merge baseline is integrated | PR #173 merged `5dadf2cc` into `main` as `650b1add`; PR #174 merged baseline docs as `3d0ecaca`; `docs/releases/evidence/v3.0.0-beta1-merge-baseline-2026-06-19.md` | Merged baseline; later beta proof and legal-cleanup work is tracked in release evidence and PR history |
| v3.0.0-beta1 version identity is aligned | `_version.py`, installer package metadata, generated OpenAPI docs, changelog, docs index, and the v3.0.0-beta1 verification record (a pre-reset record not in this repository); `check_release_identity.py` release gate | Release-prep gate |
| v3.0.0-beta1 4-hour finish-line soak | Tester branch `tester/v3.0-finish-line-4h-soak`, nonce `SOAK-4H-3.0-S21-R8-20260619-5dadf2cc`; final result commit `ae8c8fa6`; result JSON `tester-handoff/v3.0/results/result-20260619T191211Z-SOAK-4H-3.0-S21-R8-20260619-5dadf2cc.json` | PASS on one clean Windows tester machine |
| v3.0.0-beta1 GStreamer live engine CI check | PR #173 `GStreamer live engine` check on `5dadf2cc` | PASS in GitHub Actions |
| v3.0.0-beta1 cleanroom install gate | PR #173 `Cleanroom (Docker, full install gate)` check on `5dadf2cc` | PASS in GitHub Actions |
| v2.0.8 installer/sample-source fix works in focused tests | `tests/installer/test_source_setup_api.py`, `tests/installer/test_rehearsal_orchestration.py`, `tests/installer/test_installer_api.py` | Passed before release |
| v2.0.8 Windows installer artifact exists | GitHub Release `v2.0.8` asset `civiccast-2.0.8-windows-setup.exe` | Published and hash-verified |
| v2.0.8 clean Windows local new-user path can work | Tester branch result `20260603-200609-msi-3fe0ee8.md` | PASS on one real Windows tester machine |
| v2.0.9 local channel egress FileSink and loopback SRT can work | Tester branch result `20260606-053115-msi-egress-final-12508dd.md`; the v2.0.9 verification record (pre-reset, not in this repository) | PASS on one real Windows tester machine |
| v2.0.10 egress continuity survives an adversarial software headend under network impairment | Tester branch result `20260606-193046-MSI-egress-e2-headend-45624b6.md`; the v2.0.10 verification record (pre-reset, not in this repository) | PASS on one real Windows tester machine |
| Multiple normal users can complete setup without coaching | Not yet recorded | Needs manual human testing |
| Optional Internet Archive upload works for a real station | Setup/proof workflow exists; no v3.0.0-beta1 live provider result yet | Needs external provider proof |
| Optional YouTube proof works for a real station | Setup/proof workflow exists; no v3.0.0-beta1 live provider result yet | Needs external provider proof |
| Optional subscriber-notice delivery works for a real station | Setup/proof workflow exists; no v3.0.0-beta1 live provider result yet | Needs external provider proof |
| Optional Cloudflare R2 proof works for a real station | Setup/proof workflow exists; no v3.0.0-beta1 live provider result yet | Needs external provider proof |
| Live hardware capture path works on station equipment | Not yet recorded for v3.0.0-beta1 | Needs hardware proof |
| Downstream cable headend accepts egress output | Not yet recorded for v3.0.0-beta1 | Needs station/headend proof |
| App-store or OTT/mobile distribution is ready | Not claimed | Out of scope |

## How To Read The Matrix

A CI lane means the project has a repeatable automated check. It does not
automatically mean the exact published installer has been proven by a human.

A tester PASS means the exact release artifact worked on one tester machine.
It does not automatically mean every Windows machine or every user will succeed.

An external provider proof means CivicCast used real provider credentials or a
real provider account and recorded redacted evidence of the round trip. Mocked
tests and local-only demos do not count as public-provider proof.

## Release Language Rule

Use the narrowest true claim:

- Say "CI lane exists" when only automation exists.
- Say "passed local verification" when the release owner ran the check locally.
- Say "published and hash-verified" when the release asset was built and
  checked.
- Say "demonstrated on one clean Windows tester machine" for the v2.0.8
  Windows install PASS, the v2.0.9 local egress PASS, the v2.0.10
  adversarial software-headend PASS, or the v3.0.0-beta1 4-hour finish-line
  tester PASS, naming which path was tested.
- Say "validated across testers" only after multiple independent human tester
  reports exist.
