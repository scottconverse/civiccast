# Station operations implementation — October 13, 2026

> Historical implementation record. The checkpoints below describe work at that time.
> These features subsequently shipped in Beta 16 and remain in the current
> release; use the [RC8 verification record](../releases/v1.0.0-rc.8-verification.md)
> for that package identity and its results. Earlier unreleased/pending statements
> are historical, not current outstanding tasks.

Status: complete for the four selected implementation goals; unreleased.
Branch: `codex/station-operations-1-0`.
Baseline: corrected Beta 15, `431585942c9ced93e730b1e6b506c707a6adecb8`.
Engagement: finite program; only the following four owner-selected goals.

1. Ordinary scheduled station backup and restoration: database, configuration,
   portable credentials and selected media; configurable destinations, bounded
   storage, progress, and operator instructions. Separate from installer rollback.
2. Supported network access: listening address and public URLs, HTTPS through a
   supported reverse proxy, and separation of resident and staff access.
3. Staff accounts and sessions: add accounts, assign roles, disable access,
   manage/revoke sessions, and enforce expiry including the existing first admin.
4. Replace the beta-only LocalSystem service identity with a restricted service
   account, preserving existing installations, data, credentials and recovery.

Completion requires working production integration, affected documentation and
focused executable evidence for each requirement. Code existence alone is not
completion. No outside station/hardware qualification, soak, additional product
goals, publication or production cutover is included.

## Current requirement status

| Goal | Implemented behavior and evidence | Remaining |
| --- | --- | --- |
| Station backup/restore | Encrypted database/configuration/credential/media archive; selected destinations and retained-count limit; progress UI, scheduled-passphrase storage, offline CLI and guide. 21 focused checks, real PostgreSQL 17.10 roundtrip with restored media references and credentials, a real scheduler-created verified archive, and actual encrypted PostgreSQL backup creation/verification by the restricted service. | None within the selected source implementation scope. |
| Network access | Loopback listener/port, public URL, generated Caddy HTTPS configuration, separate public/staff hosts and staff CIDRs. 54 focused checks, actual Caddy configuration/routing checks, and the consolidated affected Python run passed. | Public DNS/firewall/CA deployment remains an operator configuration task. |
| Staff accounts/sessions | Account creation, roles, disable/reset, session list/revocation and 12-hour expiry including first administrator; dedicated staff sign-in and administration screens. 38 backend and 20 UI checks; TypeScript and the consolidated affected Python run pass. Migrated first-admin login works through the actual restricted service. | None within the selected source implementation scope. |
| Restricted service identity | Virtual account, profile/keyring migration, runtime/data/registry/control permissions and identity-preserving installer restoration. Actual SCM migration preserved profile hashes, started under NT SERVICE, served healthy API/current schema and completed encrypted backup. Protected installer journals retain their boundary. Final installer/lifecycle corrections: 14 checks passed. | None within the selected source implementation scope. No production cutover authorized. |

Runtime receipts live under
`.agent-runs/native-windows/station-operations-1-0/evidence/`:
`pg17-roundtrip-receipt.json`, `scheduled-backup-live.json`, and
`caddy-loopback-check.json`. The generated manual, built-in Help and API reference
are updated for this unreleased development edition; published Beta 15 is unchanged.

## Completion evidence and boundaries

- The consolidated affected Python check passed: `186 passed in 26.53s`.
  Three defects found by actual SCM/API execution were then corrected and their
  affected modules passed: `14 passed in 3.20s`. These counts overlap; do not sum
  them as a distinct-test total.
- Actual Windows SCM migration/startup/backup evidence is in
  `evidence/scm-identity/receipt.md`, with original failures and executed scripts.
  The restricted-account service created an encrypted PostgreSQL backup; direct
  archive inspection confirmed the migrated synthetic credential was included.
  The guest service stopped and the guest closed. The host station stayed running
  as LocalSystem throughout.
- Backup restoration, scheduler operation, Caddy routing, staff authorization,
  frontend compilation and installer Rust compilation have their scoped evidence
  recorded above and in the checkpoints below. The generated manual, built-in
  Help and API documentation describe the development edition.
- This is implementation verification using current source and the installed
  runtime in isolation, not a rebuilt or published installer. No broadcast-load
  test, new soak, production cutover, public DNS/firewall deployment, or outside
  hardware qualification was included. No such work is added as a goal here.
- The runtime check repaired the empty registry-child ACL left by the failed
  earlier candidate before exercising the corrected helper. The receipt preserves
  that distinction rather than claiming a pristine fresh-install result.

The earlier checkpoints below are chronological history, not remaining work.

## Work ownership

- Backup service/router/worker/CLI/screen: remaining_software worker.
- Staff accounts/session backend and screen: deferred_playout worker.
- Network settings/router/Caddy configuration/screen: gate_05_qa_walkthrough worker.
- Windows identity, shared application/UI/supervisor wiring and integration: parent.

Critical triggers: credentials/authentication, backup and restore of persisted data,
network trust boundaries and Windows service installation. Use existing tests with
focused additions; no generalized harness, broad audit or repeated broad suite.

## Initial evidence and decisions

- Repository clean at baseline; origin/main matches the recorded commit.
- Existing service-security baseline: `python -m pytest
  tests/native/test_admin_recovery.py tests/native/test_pgdata_acl.py
  tests/native/test_supervisor_control_client.py -q --tb=short`: 53 passed.
- Existing DR baseline: `python -m pytest tests/dr/test_backup.py
  tests/dr/test_restore_drill.py -q`: 23 passed.
- Credential-bearing backups must be encrypted and portable across machines;
  copying machine-protected blobs alone is insufficient.
- Account migration must preserve OS-keyring secrets and profile-based station
  state before changing the Windows service identity.
- Public access uses a supported HTTPS reverse-proxy configuration with separate
  public/staff hosts; the application stays loopback-bound by default.

At this initial checkpoint no item was complete. Published Beta 15 and the running
station remain unchanged; the current status table above supersedes checkpoint status.

## Implementation checkpoint

- Added a machine-DPAPI station credential vault with portable in-memory export
  for encrypted backups. Namespace scope is limited to the four CivicCast
  keyring services; unrelated Windows credentials are excluded.
- Private credential files now grant the restricted service read/write access,
  while keeping SYSTEM, administrators and the writing principal access. Actual
  Windows DPAPI/ACL tests plus existing private-key/admin recovery checks passed:
  41 tests. The missing service grant was first reproduced as a failing test.
- Added verified, non-destructive profile copying and migration-only SCM startup
  that returns before database or station startup. Focused credential/profile
  checks: 8 passed; startup isolation was first demonstrated failing.
- Still required: installer orchestration and ACL migration, registry access,
  rollback identity preservation, normal runtime vault activation, shared app/UI
  and supervisor integration, and integrated verification of all four workflows.
- Existing install-transaction.ps1 explicitly assumes LocalSystem in snapshot
  and restoration; it must preserve the actual supported service identity.

### Subsequent integration

- Added installer orchestration for migration-only startup, confirmed export,
  product-tree ACLs and virtual-account configuration. Updated installer
  restoration to preserve its saved LocalSystem or virtual-service identity.
  Four isolated SCM/registry decision tests passed; these do not prove actual
  installed service startup.
- Windows LookupAccountName confirms the calculated virtual-account SID.
- Wired network/backup API routes and console navigation, native app vault
  activation, scheduled-backup lifecycle and the Typer backup command group.
- Added installed-path backup factory with separately protected scheduled
  passphrase. Real temporary SQLite backup plus DPAPI integration passed.
  Backup worker is completing dynamic configuration discovery and service-env
  restoration; auth worker is integrating staff sign-in and administration.
- Added specific service identity authorization for its own control pipe.
  Service-identity/authorization checks: 15 passed (denial reproduced first).
- Native security regression set: 99 passed, one legacy-session backup assertion
  corrected by auth worker to compare exact pre-reset bytes; the corrected case
  passed. No broad suite or installer build was run.
- Host free RAM measured about 3.3 GiB; Sandbox was not started. Installed-account
  and upgrade verification is still pending. No production station change.

### Focused integration review

- Auth worker completed accounts/session administration and the dedicated staff
  sign-in flow: 38 focused backend checks and 20 focused portal checks passed.
- Operator frontend production build passed after screen integration.
- Independent service-migration review found two retry/path defects: stale
  credential snapshots on retry and unchecked redirected destination parents.
  Both corrected; vault checks (5) and migration checks (5) passed.
- Actual disposable Windows registry-key check confirms the service gets KEY_READ
  and only SYSTEM/administrators retain full access. The scratch key was removed.
- Installer ACL integration now protects the runtime boundary against inherited
  data-directory Modify access and grants the station's configured media,
  recordings and backup directories. Six focused installer-decision checks pass.
- Supervisor identity constant assertion updated for the explicitly authorized
  service SID; its focused check passes. Caddy executable verification, remaining
  backup integration and actual SCM startup/upgrade verification are outstanding.

### Integration continuation

- Actual Caddy loopback routing passed: public index 200, public staff path 404,
  staff fixture 200, staff setup path 403. Receipt and generated configurations
  are under `.agent-runs/native-windows/station-operations-1-0/evidence/`.
  This verifies proxy routing, not application authentication.
- Offline restore now starts only the managed PostgreSQL cluster while holding
  the station mutex, and stops it on success or failure; external targets do not
  invoke the managed server lifecycle. Backup/runtime focused checks: 15 passed.
- Installer migration timeout now waits for STOPPED before restoring normal
  service settings; if stopping also times out, migration-only mode is retained.
  The missing wait was reproduced first. Installer-identity checks: 9 passed.
- `cargo check --locked -j 2` passed in 2m 04s, with four warnings in untouched
  acquisition/catalog/native-pack code. This is compilation, not an installer run.
- Six affected manual source chapters and service mutex comments are updated.
  Generated manual packaging and actual SCM migration/startup remain pending.
- The host's installed CivicCastSupervisor remains Running as LocalSystem.
  No host service or production configuration change was made.

- Late durable-storage activation now starts/rebinds the backup worker and
  shutdown stops the current instance. Related lifecycle checks: 32 passed.
- Media restoration now remaps the documented asset/watch-folder database
  references and station storage settings. Windows traversal and relative
  target validation defects found in focused review were corrected; 21 backup
  and runtime checks passed. Real PostgreSQL roundtrip remains in progress.
- Generated the combined manual and built-in Help, checked their hash/content,
  and regenerated API schema/reference/TypeScript types. TypeScript passed.
- Actual Windows ACL regression exposed a recursive grant overriding protected
  installer recovery directories. Restricted recursive grants to runtime-owned
  directories; the ACL regression and all 10 installer-identity checks pass.
- First 4 GiB Sandbox attempt stopped at PostgreSQL initdb because the normal
  Microsoft VC runtime prerequisite was absent. No migration occurred in that
  attempt. Its bounded repeat includes the existing installed redistributable.

- Real PostgreSQL 17.10 roundtrip passed: preexisting target objects replaced,
  asset/watch-folder/configuration path mappings verified, media bytes and
  portable vault credentials restored, and managed database stopped after
  restore. A Sol reviewer inspected the executed script/output; no repeat run.
- Actual scheduled-backup worker created and authenticated one archive in
  30.67 seconds, persisted its attempt date, and its thread stopped cleanly.
  The earlier scheduler-check script left its SQLite fixture connection open
  during cleanup; that script-only error was corrected and its scratch removed.
- Sandbox verification has not yet proved service migration. A later attempt
  identified the prerequisite installer stalled inside the VC runtime MSI;
  the next isolated check uses already-installed runtime DLLs app-locally.
  This is not recorded as a CivicCast product failure.

- Consolidated affected Python run: `186 passed in 26.53s`, captured in
  `evidence/affected-python-final.txt`. Changed Python files pass Ruff after
  three mechanical import/comparison-style corrections. Actual service-account
  startup remains under investigation in the existing disposable guest.

- The live SCM check reached migration and exposed Win32 error 1057: virtual
  account configuration passed an empty password instead of the required NULL.
  Corrected `ChangeServiceConfig` to use `None`. A focused regression first
  failed (`1 failed, 10 deselected in 1.91s`); the affected installer module then
  passed (`11 passed in 1.39s`). Actual guest retry remains in progress.
  Microsoft documents this contract at
  https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-changeserviceconfigw.

- Same-guest retry exposed a second actual Windows defect: registry root ACLs
  lacked container inheritance, leaving the service's PythonClass child with an
  empty DACL and preventing startup. The disposable-key regression reproduced
  this (`2 failed, 9 deselected in 1.47s`). Added inheritable SYSTEM/admin full
  access and service read access; `11 passed in 1.30s`. The guest retains the
  failed candidate evidence and repairs that damaged ACL before retrying the
  corrected migration. Host service and registry remain unchanged.

- Corrected migration now preserves the profile, reads the migrated vault, and
  starts the real SCM service under `NT SERVICE\CivicCastSupervisor`; API health
  reports normal mode and current database schema. Its backup API exposed a
  binding gap before AI activation. Backups now bind for supervised installations
  as well as activated native stations. The lifecycle regression failed first
  (`1 failed, 2 passed in 3.67s`), then passed (`3 passed in 2.91s`). The same
  guest is checking actual encrypted backup creation under the restricted account.
