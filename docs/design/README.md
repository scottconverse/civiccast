# Native-Windows execution specs

> **Status boundary (October 11, 2026):** This directory contains design and
> recovery records whose decision states are dated in their individual files.
> They describe design targets and owner decisions; they are not a report of
> what the current package ships. Use the [RC7 verification
> record](../releases/v1.0.0-rc.7-verification.md) and current source for
> shipped behavior. Later dated amendments remain in force where explicitly
> recorded; this notice does not reopen or replace owner decisions.

## 2026-07-25 recovery execution

The owner-directed recovery is governed operationally by
[`spec-native-beta-recovery.md`](spec-native-beta-recovery.md). It preserves the
complete scope and sequences it into remotely checkpointed work packages.
[`plan-sub-300mb-bootstrap.md`](plan-sub-300mb-bootstrap.md) records an
owner-approved D2 architecture amendment (2026-07-29) that keeps the NSIS
bootstrap below 300,000,000 bytes without removing the legally required
offline caption model. Its owner-approved design is not proof that the planned
model-acquisition path is present in the Beta 20 public asset set. The inherited 2026-07-24 coder
handoff, including its lessons learned, is a historical record that is not included in this repository.

Specifications for the remaining program work (charter §7–§8,
civiccast-audit-control). Written 2026-07-17 while the design-tier coder model
was available; revised same night per the auditor's design review
(audit-control `reviews/2026-07-17-specs-design-review.md`, SDR-001…SDR-011).

## Decision states (SDR-010)

Every spec carries a decision-state header. The ladder:
**Proposed** (coder-drafted) → **Auditor-reviewed** (design review reconciled)
→ **Owner-approved** (Scott has accepted the owner-risk items). A decision is
settled only at Owner-approved; below that, implementation sessions treat the
spec as the current best plan and surface conflicts rather than silently
diverging — but "do not reopen" applies only to Owner-approved decisions.

### Owner-acceptance register (decisions the specs deliberately do NOT settle)

This register preserves the dated owner-acceptance record; it is not a current
claims-verification contract. In particular, the extra external authority-record
resolution requirement for `ci_safe: false` claims was removed by owner decision
on 2026-08-07. See the current note in
[`claims-schema.json`](../claims/claims-schema.json) and the verifier's module
documentation. Do not infer that removed requirement from the July design text.
The WSL-line sunset row below was later resolved: the owner retired the WSL2
lane on 2026-08-19. See the current repository policy in [`BRANCHES.md`](../../BRANCHES.md).

| Item | Where | What the owner is accepting |
|---|---|---|
| LocalSystem for the beta | supervisor D4 | deferred least-privilege; security risk window — **ACCEPTED by owner 2026-07-18** (in-session, decision page item 3; time-boxed to beta) |
| OpenH264 / FFmpeg build licensing posture | packaging D3 | binary distribution + patent stance — **ACCEPTED by owner 2026-07-18** (in-session, decision page item 5; owner explicitly waived the pre-read of the memo) |
| JetStream discard at migration | migration D6 | in-flight event loss, AFTER the stream-catalog inventory — **PARKED by owner 2026-07-18**: decision correctly waits for the D6 inventory when a migration is scheduled (inventory-first per SDR-010) |
| Migration downtime window | migration D1 | station-offline freeze during migration — **PARKED by owner 2026-07-18** until a migration is scheduled |
| ADR-0021 merge (supersedes ADR-0003) | docs/adr/0021 | rung-3 dual review: auditor half PASSED + **owner ACCEPTED 2026-07-18**; owner authorized the merge to main (PR #297), executed on his instruction |
| WSL-side guard patch landing | guard D6 | rc-line change; prerequisite to any co-install — **PARKED by owner 2026-07-18** until back at the lab (needs live bidirectional-refusal proof) |
| Claims trust-root pin | claims D6 | **ACCEPTED by owner 2026-07-18** (decision page item 2): the refreshed Beelink-key pin (allowed_signers blob 0ddf3eb4) + roles + rotation procedure; owner_accepted flipped true in the same commit |
| Authority-record format v1 | claims D5/D6; audit-control AUTHORITY_RECORDS.md | auditor-ratified at audit-control 05f78d89; **owner ACCEPTED 2026-07-18** (decision page item 4) — dual review complete |
| WSL-line sunset | parallel-ship decision | FUTURE owner call on adoption/field evidence — no spec pre-decides it |

## Execution order (POST-FABLE-PLAYBOOK.md carries the worker briefs)

1. `spec-claims-evidence-rule.md` + ADR 0021 — charter gate 3
2. `spec-dual-runtime-guard.md` — charter gate 4; bidirectional refusal is a
   prerequisite to ANY side-by-side install (clean-machine native installs
   don't wait)
3. `spec-supervisor.md` — charter gate 5 (decision-gate half PASSED; Python
   worker retained)
4. `spec-packaging-closure.md` — feeds installer
5. `spec-installer-lifecycle.md` — produces the beta artifact
6. `spec-migration-contract.md` — migration rehearsal and software contract

## How every slice runs (unchanged program mechanics)

Branch `claude/<slice-id>` → implement per spec → commit-bound evidence under
`.agent-runs/native-windows/<slice-id>/evidence/` → PR to the integration branch
→ CI at head, with material proofs bound to the tested build → independent
review of the integrated candidate reported directly to Scott. No canonical
verdict file or per-slice `AUDIT_PASS` token is required to integrate. Severity
calibration: functional defects block; prose nits batch. Testing policy: max 4h
soaks and software-only on the dev box. Prohibited without Scott's explicit
approval: `main`, tags, releases, and rc-line actions.

## Gotcha bank (hard-won; read before touching Windows plumbing)

- PS 5.1 reads BOM-less UTF-8 scripts as ANSI: keep `.ps1` ASCII-only, or write BOM.
- `2>$null`/`2>&1` on native exes in PS 5.1 under `$ErrorActionPreference=Stop`
  turns stderr into a terminating NativeCommandError.
- Win11 SCM does not emit per-service 7036 events at boot; prove service start
  via in-process identity logging + Security 4624 (excluding UMFD-*/DWM-*
  virtual accounts) + NTFS timestamps.
- `git hash-object <file>` (named-file) APPLIES clean filters, same as `--path`;
  only `--no-filters` hashes raw bytes (committed control test exists).
- uv-managed Pythons under `AppData\Roaming\uv` may be invisible to services if
  installed from an MSIX-contained shell; install self-contained runtimes with
  `UV_PYTHON_INSTALL_DIR` outside the user profile.
- The bounded head+tail media fingerprint (`build_media_manifest`) does NOT
  detect middle-of-file corruption (auditor-executed falsification) — it is a
  drift monitor, never a copy-integrity proof.
- ci-test.yml triggers on PRs to `main` and `program/**` only.
- codex CLI: global flags before subcommand — `codex exec -s danger-full-access resume --last -`.
