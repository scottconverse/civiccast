// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors

//! D4 install-side state establishment for CivicCast (Native):
//! `spec-installer-lifecycle.md` D1/D4 ("registers a LocalSystem service");
//! D4 ("Exact state inventory (files, registry keys, service, firewall
//! rules) is enumerated in the spec's implementation and asserted by the
//! proofs -- 'everything gone' means that inventory, bidirectionally.");
//! `spec-supervisor.md` AC-S1 ("installer/service creates the required
//! inbound rules (portal/API ports); proven by netsh dump...").
//!
//! ## Service registration: invoke the pywin32 seam, do not rebuild it
//!
//! `Cargo.toml` carries neither the `windows-service` crate nor `windows`/
//! `winapi` (checked before writing this module) -- per the task instruction
//! this would ordinarily mean STOP and report options rather than add a
//! dependency unilaterally. A third option was found that needs no new Rust
//! dependency at all: `civiccast/native/supervisor/service_host.py` already
//! defines the production `ServiceFramework` subclass
//! (`CivicCastSupervisorService`, with `_svc_name_`, `_svc_display_name_`,
//! `_svc_description_` set from `civiccast.native.supervisor.config`/
//! `civiccast.native.supervisor.service.SERVICE_DESCRIPTION`) and exposes
//! `main()`, which calls `win32serviceutil.HandleCommandLine`. That pywin32
//! entry point already implements exactly the registration contract D4/D5
//! need: `install` creates the service (LocalSystem by default when no
//! `--username` is given -- `.venv/Lib/site-packages/win32/lib/
//! win32serviceutil.py:846-862`); if the service already exists
//! (`ERROR_SERVICE_EXISTS`) it falls through to `update`
//! (`win32serviceutil.py:866-868`), i.e. install-or-update is ALREADY
//! idempotent (D5 Repair's "re-register service" and this task's "re-run
//! over existing state = clean replace" are satisfied by the seam itself,
//! not by logic this module adds). This module's job is therefore the same
//! shape as the D3 engine invocation already in `nsis-hooks-native.nsh`
//! (`"$INSTDIR\runtime\python.exe" -m civiccast.native.upgrade ...`): build
//! the argv for `python.exe -m civiccast.native.supervisor.service_host
//! install --startup auto` and run it, never re-typing the service's name/
//! display name/description in Rust (those live in the Python class and are
//! read automatically by pywin32's `InstallService`).
//!
//! **Recovery actions and service description are handled differently, per
//! the task's explicit "if the spec doesn't pin a value, STOP and report
//! rather than inventing" instruction:**
//! - **Description** is already pinned (`service.py:157-161`,
//!   `SERVICE_DESCRIPTION`) and applied automatically by the seam above --
//!   no gap.
//! - **Start type** is not literally pinned anywhere, but `spec-supervisor.md`
//!   AC1 ("Boot, no login: children ready in D6 order...") is only
//!   satisfiable by `SERVICE_AUTO_START` -- `SERVICE_DEMAND_START` would
//!   contradict an unconditional "boot, no login" acceptance criterion. This
//!   is a necessary derivation from a stated AC, not an invented value; see
//!   [`SERVICE_STARTUP_MODE`].
//! - **Recovery actions** (SCM restart-on-crash: delay, count, reset period)
//!   have NO pinned numeric parameters anywhere in the read set (grepped
//!   `.agent-runs/native-windows` for `ResetPeriod|SC_ACTION_RESTART|
//!   recovery action|restart delay` -- zero hits). `spec-supervisor.md` AC4
//!   ("Kill the SUPERVISOR mid-playout... SCM restarts it...") pins the
//!   qualitative behavior (restart-on-failure), but pywin32's
//!   `win32serviceutil` helpers never call `ChangeServiceConfig2` with
//!   `SERVICE_CONFIG_FAILURE_ACTIONS` (grepped
//!   `.venv/Lib/site-packages/win32/lib/win32serviceutil.py` -- zero hits),
//!   so AC4 requires an explicit `sc.exe failure` call -- the SCM default is
//!   NO recovery (a crashed service stays dead), which would fail AC4 at the
//!   lifecycle matrix. **RESOLVED (coder decision, 2026-07-29, same-day
//!   follow-up):** the numeric parameters are coder-pinnable implementation
//!   detail, not owner scope -- pinned to the industry-standard restart
//!   ladder 5 s / 10 s / 30 s with a daily failure-count reset, plus
//!   `failureflag 1` so nonzero-exit stops recover like crashes. See
//!   `service_failure_actions_command`/`service_failure_flag_command`.
//!
//! ## DatabaseUrl
//!
//! `civiccast.native.provision.models.resolve_database_url` is "the
//! documented seam" (task instruction) that produces the exact string value;
//! this module never reconstructs the `postgresql://...` format itself
//! (`build_database_url`'s percent-encoding is not duplicated here). What
//! this module owns is the D4 half: writing an ALREADY-RESOLVED value to
//! `HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl` and reading it back to
//! verify the write landed, the same style as `native_uninstall.rs`'s
//! `write_postclear_marker`. [`write_database_url`] is implemented and
//! unit-tested for its own contract (write + read-back verify, given a
//! value).
//!
//! **RESOLVED (WP2 provision-execution wiring, 2026-07-29): the caller now
//! exists.** [`run_native_provision`] shells to the journaled Python
//! provisioning engine (`civiccast.native.provision`, landed separately),
//! which generates the password, runs `run_provision` against its REAL
//! seams (real `initdb`, real filesystem under ProgramData), and hands the
//! resolved `DatabaseUrl` back through a SINGLE stdout marker line
//! ([`PROVISION_HANDOFF_MARKER_PREFIX`], parsed by
//! [`parse_provision_handoff`]) -- never printed by this process, never
//! placed on a second CLI subprocess's argv, and written to HKLM via an
//! IN-PROCESS call straight into the existing [`write_database_url`]. A
//! handoff-less success means the Python side took its no-op path (existing
//! cluster + existing registry value both already correct) and never
//! generated a password to begin with -- see
//! `civiccast.native.provision.__main__`'s `ProvisionCliAction` decision
//! matrix. Wired into `NSIS_HOOK_POSTINSTALL` between the D2
//! re-verification gates and D4 service/firewall registration (see
//! `nsis-hooks-native.nsh`); the STOP this doc previously recorded here is
//! closed. What remains genuinely open (documented in the WP2 evidence
//! file, not invented): where an extracted server-binaries pack physically
//! lands on disk has no earlier-established convention, so
//! `civiccast.native.provision.__main__.resolve_provision_paths` picks one
//! (`<install_root>\packs\native-server-binaries\...`) as a disclosed coder
//! decision, not a spec-pinned value.
//!
//! ## Firewall
//!
//! AC-S1's "portal/API ports" (plural) resolves to exactly ONE port, not two:
//! `main.rs`'s `OPERATOR_CONSOLE_URL`/`RESIDENT_PORTAL_URL`/`SERVICE_URL`/
//! `SERVICE_HEALTH_ADDR` and `civiccast.native.supervisor.core.py`'s
//! `control_plane_port` default all agree on `8000` -- the resident portal,
//! operator console, and control-plane API are the SAME FastAPI listener on
//! one port, distinguished by URL path, not port number. No second port
//! value appears anywhere in the read set. `netsh advfirewall firewall show
//! rule` returns exit code 0 whether or not a rule was found (existence is
//! signalled only by the "No rules match the specified criteria." string in
//! stdout, a documented `netsh.exe` quirk) -- [`classify_firewall_probe_output`]
//! classifies captured output rather than trusting the exit code, and fails
//! CLOSED (`Unknown` -> `Block`) on any output shape it does not recognize,
//! so an ambiguous probe can never silently skip OR silently duplicate the
//! rule.

use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use ed25519_dalek::VerifyingKey;
#[cfg(target_os = "windows")]
use winreg::enums::{HKEY_LOCAL_MACHINE, KEY_ALL_ACCESS, KEY_READ, KEY_SET_VALUE, KEY_WOW64_64KEY};
#[cfg(target_os = "windows")]
use winreg::RegKey;

// ---------------------------------------------------------------------------
// Identity constants
// ---------------------------------------------------------------------------

/// Mirrors `civiccast.native.supervisor.config.SERVICE_NAME` (owner-approved
/// 2026-07-20, per that module's docstring). Used ONLY for the D4 state
/// inventory (`native_uninstall.rs`) and evidence citation -- never passed as
/// a CLI argument to [`service_registration_command`], because pywin32 reads
/// the name from the Python service class itself. A drift-guard test below
/// reads `config.py`'s literal source so an edit to the Python constant
/// cannot silently diverge from this mirror without failing `cargo test`.
///
/// Not read by any live code path in THIS crate today (the POSTUNINSTALL
/// teardown that will consume it as a `sc delete <name>` argument is a later
/// work package, see `native_uninstall.rs`'s `NATIVE_D4_STATE_INVENTORY`
/// doc), so it is exempted from the unused-item lint rather than deleted --
/// the same convention `native_uninstall.rs`'s
/// `WSL_ARP_PROBE_LOADED_HIVES_ONLY` already uses for a documentation/
/// grep-seam constant.
#[allow(dead_code)]
pub const SERVICE_NAME: &str = "CivicCastSupervisor";

/// `spec-installer-lifecycle.md` D4 + the literal key `nsis-hooks-native.nsh`
/// already reads via `ReadRegStr $R2 HKLM "Software\CivicCast\Native"
/// "DatabaseUrl"` (same string, no leading backslash, as winreg expects).
pub const DATABASE_URL_KEY: &str = r"SOFTWARE\CivicCast\Native";
pub const DATABASE_URL_VALUE_NAME: &str = "DatabaseUrl";

/// See the module doc's "Firewall" section for the derivation.
pub const CONTROL_PLANE_PORT: u16 = 8000;
pub const FIREWALL_RULE_NAME: &str = "CivicCast (Native) Portal/API (TCP 8000)";

/// See the module doc's "Recovery actions and service description" section.
pub const SERVICE_STARTUP_MODE: &str = "auto";

/// `spec-installer-lifecycle.md` D4 -- the D3 fresh-install gate's
/// prior-version signal, written at the fully-successful end of
/// `NSIS_HOOK_POSTINSTALL` (see `nsis-hooks-bootstrap.nsh`). Tracked here
/// (alongside [`DATABASE_URL_VALUE_NAME`]) because both live under
/// [`DATABASE_URL_KEY`]. NOT a credential (it is a version string), so it is
/// deliberately outside [`CREDENTIAL_VALUE_NAMES`] and outside the teardown's
/// "clear credentials" step. The separate
/// `HKLM\SOFTWARE\CivicCast\ActiveRuntime` selector (owned by
/// `native_uninstall.rs`'s existing uninstall protocol) is left untouched by
/// everything in this module. The `CivicCast\Native` KEY itself is now
/// removed once empty (N-20, carried, rewalk-de3aaf6f) -- see
/// [`delete_native_key_if_empty`].
pub const INSTALLED_VERSION_VALUE_NAME: &str = "InstalledVersion";

/// `HKLM\SOFTWARE\CivicCast` -- the parent of [`DATABASE_URL_KEY`], and also
/// where the D7a Maintenance interlock blob lives. Same literal path,
/// duplicated for the same cross-language reason
/// [`PROVISION_HANDOFF_MARKER_PREFIX`] below is: Python and Rust cannot share
/// a literal across the process boundary. Must match
/// `civiccast.native.runtime_guard.MAINTENANCE_KEY` (and `SELECTOR_KEY`,
/// which is the same string under a different name in
/// `native_uninstall.rs`) exactly.
pub const CIVICCAST_ROOT_KEY: &str = r"SOFTWARE\CivicCast";

/// `civiccast.native.runtime_guard.MAINTENANCE_VALUE_NAME` -- the D7a
/// upgrade/provision interlock's REG_SZ value name under
/// [`CIVICCAST_ROOT_KEY`]. See [`delete_released_maintenance_blob`].
pub const MAINTENANCE_VALUE_NAME: &str = "Maintenance";

/// Win32 `ERROR_SERVICE_DOES_NOT_EXIST` (winerror.h). `sc.exe` returns this
/// exact value as its process exit code when the target service is not
/// registered; pywin32's `RemoveService`/`StopService` (via
/// `SmartOpenService`) raise a `win32service.error` carrying the same
/// `winerror`, which `win32serviceutil.HandleCommandLine` (`service_host.py`'s
/// `main`) returns unchanged as the process exit code -- so this ONE constant
/// classifies "already absent" for both the `sc.exe`-driven stop ([`
/// classify_service_stop_exit_code`]) and the pywin32-driven remove
/// ([`run_service_removal`]).
const ERROR_SERVICE_DOES_NOT_EXIST: i32 = 1060;

/// Win32 `ERROR_SERVICE_NOT_ACTIVE` (winerror.h): `sc.exe stop` returns this
/// exact value when the service exists but is already stopped -- also an
/// idempotent success for [`stop_native_service`].
const ERROR_SERVICE_NOT_ACTIVE: i32 = 1062;

/// Win32 `ERROR_SERVICE_CANNOT_ACCEPT_CTRL` (winerror.h, 1061): the service
/// cannot accept the control message RIGHT NOW -- for a stop control, the
/// overwhelmingly common cause is that a stop is ALREADY IN PROGRESS
/// (`SERVICE_STOP_PENDING`).
///
/// This is a WAIT signal, not a failure signal, and treating it as failure cost
/// a whole gauntlet run. Evidence (2026-07-31, gauntlet run 17): the supervisor
/// sat in `SERVICE_STOP_PENDING` with the SCM checkpoint pinned at 0x1. Every
/// subsequent `sc.exe stop` returned 1061, [`classify_service_stop_exit_code`]
/// mapped it to [`ServiceStopExitOutcome::Failed`], and
/// [`stop_native_service`] returned an error INSTANTLY -- without ever polling.
/// Repair reported exit 79, the uninstall teardown exit 82 (leaving the whole
/// program tree behind), and every later install refused with 120.
///
/// It is deliberately NOT mapped to [`ServiceStopExitOutcome::AlreadyDone`]:
/// "a stop is in progress" is not "the service is stopped". It maps to
/// [`ServiceStopExitOutcome::StopIssued`], whose contract is exactly right for
/// it -- the stop the caller wanted is underway, and
/// [`wait_for_service_stopped`]'s [`SERVICE_STOP_POLL_TIMEOUT_SECS`] poll of
/// live `sc.exe query` state decides whether it actually reached STOPPED. A
/// service genuinely wedged in
/// STOP_PENDING still fails, on real observed state, after a real wait.
const ERROR_SERVICE_CANNOT_ACCEPT_CTRL: i32 = 1061;

/// Win32 `ERROR_SERVICE_ALREADY_RUNNING` (winerror.h, 1056): `sc.exe start`
/// returns this exact value when the service is already started. An
/// idempotent SUCCESS for [`start_native_service`] -- D4 re-runs over an
/// already-live station (a repair install, a re-run of
/// `NSIS_HOOK_POSTINSTALL`) must not fail because the station was already up,
/// exactly as [`stop_native_service`] treats 1062 ("not started") as success
/// on the way down.
const ERROR_SERVICE_ALREADY_RUNNING: i32 = 1056;

// ---------------------------------------------------------------------------
// Service registration (pywin32 seam invocation)
// ---------------------------------------------------------------------------

/// Pure command construction: `$INSTDIR\runtime\python.exe -m
/// civiccast.native.supervisor.service_host --startup auto install`.
///
/// ARGUMENT ORDER IS LOAD-BEARING: pywin32's `win32serviceutil.
/// HandleCommandLine` parses OPTIONS BEFORE the command — its own usage
/// line is `service_host.py [options] install|update|remove|...`. The
/// original `install --startup auto` order made pywin32 print usage and
/// exit 1 on every live install (surfaced as the hook's exit 70 and the
/// silent-install modal in Sandbox matrix runs 3–5, 2026-07-30; the
/// corrected order was proven live in the run-5 guest: "Service
/// installed", exit 0). The shape-pinning unit test below asserts this
/// exact order so it can never silently flip back.
pub fn service_registration_command(install_root: &Path) -> (PathBuf, Vec<String>) {
    let python_exe = install_root.join("runtime").join("python.exe");
    let args = vec![
        "-m".to_string(),
        "civiccast.native.supervisor.service_host".to_string(),
        "--startup".to_string(),
        // NSIS keeps a durable prior-state snapshot until its final commit.
        // A reboot during activation/D4 must recover before candidate startup.
        if std::env::var_os("CIVICCAST_INSTALL_TRANSACTION").is_some() {
            "manual".to_string()
        } else {
            SERVICE_STARTUP_MODE.to_string()
        },
        "install".to_string(),
    ];
    (python_exe, args)
}

/// SCM failure actions (supervisor spec AC4: "SCM restarts it"). The SCM
/// default is NO recovery -- a crashed service stays dead -- so restart
/// actions must be configured explicitly. Values are the coder-pinned
/// standard: restart after 5 s / 10 s / 30 s, failure count resets daily.
/// Constructed for `sc.exe failure` (present on every supported Windows).
pub fn service_failure_actions_command() -> Vec<String> {
    vec![
        "failure".to_string(),
        SERVICE_NAME.to_string(),
        "reset=".to_string(),
        "86400".to_string(),
        "actions=".to_string(),
        "restart/5000/restart/10000/restart/30000".to_string(),
    ]
}

/// `sc.exe failureflag <name> 1`: also apply the failure actions when the
/// service stops with a nonzero exit code without crashing -- the supervisor
/// exits nonzero on fatal internal errors, and those must recover the same
/// way a crash does.
pub fn service_failure_flag_command() -> Vec<String> {
    vec![
        "failureflag".to_string(),
        SERVICE_NAME.to_string(),
        "1".to_string(),
    ]
}

/// Restores the `pythonservice.exe` site-packages member that pywin32's
/// `InstallService` unconditionally MOVES out of
/// `<install_root>\runtime\Lib\site-packages\win32\` into the payload root
/// (`<install_root>\runtime\`, `sys.exec_prefix` for this payload) as a side
/// effect of registering the service. The pack ships the exe at BOTH paths
/// so the service's registered binary path (the payload-root copy) is a
/// first-class manifest member that can never dangle -- but the move leaves
/// the site-packages member missing, which makes the very next D5
/// verification report a repair.
///
/// Task #50 (live Sandbox runs 12+13, row 3): `nsis-hooks-bootstrap.nsh`'s
/// `d4-service-registration` block already restores this member after the
/// D3 install/upgrade chain's own `--civiccast-register-native-service`
/// call -- but D5 Repair calls [`register_native_service`] directly,
/// in-process (`native_repair.rs::reregister_service_and_firewall`), with
/// NO NSIS hook anywhere in that path. Nothing restored the member after a
/// repair's unconditional re-registration, so every repair left the tree
/// mutated and the next verify reported exit 76 forever. Moving the restore
/// INSIDE [`register_native_service`] (this function is called from there)
/// means every caller -- the D3 install chain AND D5 repair -- gets it from
/// the single seam that actually performs the pywin32 move. The NSIS hook's
/// own restore is left in place (redundant but harmless).
///
/// A missing payload-root source is not a failure here, matching the NSIS
/// hook's own "WARNING ... NOT restored" breadcrumb-not-abort behavior: it
/// is a disclosed no-op (the next verify will report a repair, same as the
/// hook path already accepts), not a [`register_native_service`] failure. A
/// real copy failure (source exists, destination write fails) DOES fail,
/// via the same `Result<(), String>` / loud-`eprintln!` convention every
/// other step in [`register_native_service`] already uses -- no new exit
/// code is introduced; a copy failure surfaces through the CLI's existing
/// 70 (`run_native_service_registration_cli`, `main.rs`).
fn restore_service_host_site_packages_member(install_root: &Path) -> Result<(), String> {
    let runtime_dir = install_root.join("runtime");
    let source = runtime_dir.join("pythonservice.exe");
    let dest = runtime_dir
        .join("Lib")
        .join("site-packages")
        .join("win32")
        .join("pythonservice.exe");
    if !source.exists() {
        eprintln!(
            "CivicCast (Native) service registration: WARNING source {} missing; \
             site-packages service host member NOT restored (next D5 verify will \
             report a repair).",
            source.display()
        );
        return Ok(());
    }
    std::fs::copy(&source, &dest).map_err(|error| {
        format!(
            "Could not restore site-packages service host member ({} -> {}): {error}",
            source.display(),
            dest.display()
        )
    })?;
    Ok(())
}

/// Thin execution wrapper (untested directly, matching
/// `native_uninstall.rs`'s `write_postclear_marker`/`clear_postclear_marker`
/// convention -- the HARD RULE forbids unit-testing real SCM execution).
#[cfg(target_os = "windows")]
pub fn register_native_service(install_root: &Path) -> Result<(), String> {
    let (python_exe, args) = service_registration_command(install_root);
    run_and_check(
        &python_exe,
        &args,
        "CivicCast (Native) service registration",
    )?;
    restore_service_host_site_packages_member(install_root)?;
    // D3's health gate leaves the upgraded service running. Identity migration
    // requires STOPPED before its migration-only startup under the old account.
    stop_native_service()?;
    run_and_check(
        &python_exe,
        &[
            "-m".to_string(),
            "civiccast.native.install_service_identity".to_string(),
            "--install-root".to_string(),
            install_root.to_string_lossy().into_owned(),
        ],
        "CivicCast restricted service identity migration",
    )?;
    let sc_exe = Path::new("sc.exe");
    run_and_check(
        sc_exe,
        &service_failure_actions_command(),
        "CivicCast (Native) SCM failure-action configuration",
    )?;
    run_and_check(
        sc_exe,
        &service_failure_flag_command(),
        "CivicCast (Native) SCM failure-flag configuration",
    )
}

#[cfg(target_os = "windows")]
fn run_and_check(program: &Path, args: &[String], what: &str) -> Result<(), String> {
    let output = std::process::Command::new(program)
        .args(args)
        .output()
        .map_err(|error| format!("Could not run {what} ({}): {error}", program.display()))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(child_failure_message(
            what,
            output.status.code(),
            &String::from_utf8_lossy(&output.stdout),
            &String::from_utf8_lossy(&output.stderr),
        ))
    }
}

/// The installer's progress log keeps only the first 1024 characters of this
/// message (NSIS string limit), and a Python traceback puts its actual error
/// LAST. Lead with the final non-empty output line, then keep everything.
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub(crate) fn child_failure_message(
    what: &str,
    code: Option<i32>,
    stdout: &str,
    stderr: &str,
) -> String {
    let combined = format!("{stdout}{stderr}");
    let last_line = combined
        .lines()
        .rev()
        .map(str::trim)
        .find(|line| !line.is_empty());
    match last_line {
        Some(last) => format!("{what} failed (exit {code:?}): {last} | full output: {combined}"),
        None => format!("{what} failed (exit {code:?}): (no output)"),
    }
}

// ---------------------------------------------------------------------------
// Service stop / removal (BLOCKER + CRITICAL fixes: nothing previously ever
// stopped the LocalSystem service before a tree rebuild, and POSTUNINSTALL
// never removed the service/firewall/registry state the D4 establishment
// above creates). Both `stop_native_service` and `unregister_native_service`
// are idempotent by design -- D5 Repair, PREINSTALL-before-a-tree-rebuild,
// and POSTUNINSTALL teardown all call these against a machine that may
// already be in the target state.
// ---------------------------------------------------------------------------

/// Pure command construction for `sc.exe stop CivicCastSupervisor`. `sc.exe`'s
/// documented grammar is `sc <command> <servicename> [options]`
/// (`sc stop` takes no options), the SAME shape
/// [`service_failure_actions_command`]/[`service_failure_flag_command`] above
/// already use for other `sc.exe` subcommands.
pub fn service_stop_command() -> Vec<String> {
    vec!["stop".to_string(), SERVICE_NAME.to_string()]
}

/// Pure command construction for `sc.exe query CivicCastSupervisor`, used
/// only to poll for the STOPPED state after a stop has been issued (and, as a
/// defensive fallback in [`run_service_removal`], to check whether the
/// service still exists when the pywin32 removal path itself cannot run).
fn service_query_command() -> Vec<String> {
    vec!["query".to_string(), SERVICE_NAME.to_string()]
}

/// What issuing `sc.exe stop` accomplished, before any polling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ServiceStopExitOutcome {
    /// Exit 0: the stop was accepted; the service may still be STOP_PENDING
    /// and must be polled to a real STOPPED state (see [`wait_for_service_stopped`]).
    ///
    /// Also 1061 (`ERROR_SERVICE_CANNOT_ACCEPT_CTRL`): a stop is ALREADY in
    /// progress, which is the same obligation -- poll until the service really
    /// reaches STOPPED. See that constant for the run-17 evidence.
    StopIssued,
    /// 1060 (does not exist) or 1062 (not started): already in the target
    /// state -- idempotent success, no polling needed.
    AlreadyDone,
    /// Any other exit code: a real failure.
    Failed,
}

/// Pure classifier over `sc.exe stop`'s process exit code. Kept separate from
/// [`stop_native_service`] so the idempotency mapping (1060/1062 -> success)
/// can be unit-tested without a live `sc.exe` call, matching this module's
/// existing `classify_firewall_probe_output` convention.
fn classify_service_stop_exit_code(exit_code: Option<i32>) -> ServiceStopExitOutcome {
    match exit_code {
        Some(0) => ServiceStopExitOutcome::StopIssued,
        // 1061 == a stop is already in progress. Same obligation as exit 0:
        // proceed to the poll, do NOT declare failure without ever looking at
        // the service's real state (gauntlet run 17 -- see the constant).
        Some(code) if code == ERROR_SERVICE_CANNOT_ACCEPT_CTRL => ServiceStopExitOutcome::StopIssued,
        Some(code) if code == ERROR_SERVICE_DOES_NOT_EXIST || code == ERROR_SERVICE_NOT_ACTIVE => {
            ServiceStopExitOutcome::AlreadyDone
        }
        _ => ServiceStopExitOutcome::Failed,
    }
}

/// Parse `sc.exe query`'s captured stdout for a `STATE` line reporting
/// `STOPPED`. `sc.exe query` prints a block like:
/// ```text
/// SERVICE_NAME: CivicCastSupervisor
///         TYPE               : 10  WIN32_OWN_PROCESS
///         STATE              : 1  STOPPED
///         ...
/// ```
/// Matching is deliberately narrow (the trimmed line must START with
/// `STATE`) so the `STOP_PENDING` state -- a real, different state that does
/// NOT contain the substring "STOPPED" -- is never mistaken for it, and so no
/// other field (e.g. a hypothetical future line mentioning "stopped" in
/// prose) can produce a false positive.
fn service_query_reports_stopped(stdout: &str) -> bool {
    stdout.lines().any(|line| {
        let trimmed = line.trim_start();
        trimmed.starts_with("STATE") && trimmed.contains("STOPPED")
    })
}

/// G5: how long [`wait_for_service_stopped`] polls before giving up.
///
/// DERIVED, not picked: the Python supervisor's own `SvcStop` watchdog
/// (`civiccast/native/supervisor/service.py::SVC_STOP_WATCHDOG_SECONDS`)
/// force-exits a stuck stop at 150s -- so ANY legitimately slow stop the
/// supervisor eventually completes on its own can take up to 150s. The
/// PREVIOUS installer-side wait was 60s: 60 < 150 meant a stop the
/// supervisor's own watchdog would have let finish successfully still
/// failed HERE first, well before the watchdog ever got a chance to fire.
/// 180s is 150s plus headroom, so the installer's wait always outlasts the
/// supervisor's own worst-case bounded stop instead of racing it.
const SERVICE_STOP_POLL_TIMEOUT_SECS: u64 = 180;

/// Thin execution wrapper (untested directly -- same HARD RULE as
/// [`register_native_service`]): poll `sc.exe query` until the service
/// reports STOPPED, the [`SERVICE_STOP_POLL_TIMEOUT_SECS`] deadline elapses,
/// or the service itself disappears mid-poll (also treated as stopped -- a
/// concurrent removal is not a stop failure). Never a fixed sleep as the
/// only wait: each iteration re-queries live state and only sleeps between
/// queries.
#[cfg(target_os = "windows")]
fn wait_for_service_stopped() -> Result<(), String> {
    let deadline =
        std::time::Instant::now() + std::time::Duration::from_secs(SERVICE_STOP_POLL_TIMEOUT_SECS);
    loop {
        let output = std::process::Command::new("sc.exe")
            .args(service_query_command())
            .output()
            .map_err(|error| format!("Could not run sc.exe to query {SERVICE_NAME}: {error}"))?;
        if output.status.code() == Some(ERROR_SERVICE_DOES_NOT_EXIST) {
            return Ok(());
        }
        if service_query_reports_stopped(&String::from_utf8_lossy(&output.stdout)) {
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            return Err(format!(
                "Timed out after {SERVICE_STOP_POLL_TIMEOUT_SECS}s waiting for {SERVICE_NAME} to reach STOPPED state."
            ));
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

/// Stop the LocalSystem supervisor service via `sc.exe stop`, then poll until
/// it is genuinely STOPPED (never trusting `sc stop`'s immediate return,
/// which only means the stop request was accepted, not that the process tree
/// -- including the long-lived `postgres.exe` child
/// whose binary lives under `$INSTDIR`) has actually exited. Idempotent:
/// "service does not exist" (1060) and "service not started" (1062) are both
/// SUCCESS, matching D5 Repair's and POSTUNINSTALL teardown's need to call
/// this against a machine that may already be in the target state.
///
/// This is the CRITICAL-fix seam: callers that are about to delete/rebuild
/// `$INSTDIR\runtime` or `$INSTDIR\packs\...\payload` (PREINSTALL before a
/// tree rebuild, D5 Repair) MUST call this first, or the service's own
/// binaries (and its children's) can be deleted out from under a still-running
/// process.
#[cfg(target_os = "windows")]
pub fn stop_native_service() -> Result<(), String> {
    let output = std::process::Command::new("sc.exe")
        .args(service_stop_command())
        .output()
        .map_err(|error| format!("Could not run sc.exe to stop {SERVICE_NAME}: {error}"))?;
    match classify_service_stop_exit_code(output.status.code()) {
        ServiceStopExitOutcome::AlreadyDone => Ok(()),
        ServiceStopExitOutcome::StopIssued => wait_for_service_stopped(),
        ServiceStopExitOutcome::Failed => Err(format!(
            "Could not stop {SERVICE_NAME} (sc.exe stop exit {:?}): {}{}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )),
    }
}

// ---------------------------------------------------------------------------
// Service START (BLOCKER: nothing ever started the registered service).
//
// `service_registration_command` registers CivicCastSupervisor with
// `--startup auto`. That is a NEXT-BOOT instruction to the SCM and nothing
// else: it does not start the service now, and no `StartService`/`sc start`
// call existed anywhere under `src-tauri/src/` (grepped before writing this).
// A freshly installed station therefore had a correctly registered, correctly
// configured, entirely STOPPED service -- no postgres, no control
// plane on 127.0.0.1:8000, and so nothing behind the installer's "Open
// operator console" button -- until the operator happened to reboot.
//
// DELIBERATELY NOT PUT INSIDE `register_native_service`: that function is
// ALSO the D5 Repair path (`native_repair.rs::reregister_service_and_firewall`),
// and repair's own documented decision is that it must NOT restart a service
// it stopped to rebuild a tree ("starting a service an operator stopped on
// purpose is its own defect" -- see `ServiceQuiescenceAuthority::
// stopped_for_rebuild`, which prints an explicit NOTE saying the station was
// left down). Starting from inside `register_native_service` would silently
// make that NOTE a false statement. The start is therefore sequenced by the
// D4 INSTALL CLI (`main.rs::run_native_service_registration_cli`) only.
//
// Same pure/thin-wrapper split as the stop machinery above: command
// construction and exit-code classification are pure and unit-tested; the
// `sc.exe` execution and the poll loop are thin wrappers under the same HARD
// RULE that keeps live SCM calls out of unit tests.
// ---------------------------------------------------------------------------

/// The exit code `--civiccast-register-native-service` reports when the
/// service registered cleanly but could NOT be brought to RUNNING.
///
/// Deliberately distinct from that subcommand's existing registration-failure
/// code (70) so the installer log says which half failed.
///
/// **Moved 74 -> 83 by the installer-path audit (MA-28).** The comment this
/// replaces asserted "74 is free". It was not: 74 was already
/// [`crate::native_uninstall::TRANSFER_ACK_REQUIRED_EXIT_CODE`] ("an
/// ActiveRuntime transfer must be acknowledged") AND
/// `--civiccast-stage-packs`' required-pack-staging failure (`main.rs`). Three
/// unrelated meanings on one number, in a band whose entire purpose that same
/// comment states: "the exit code is the only signal a support log carries
/// about WHICH step failed". No live collision existed -- each caller branches
/// within one subcommand -- but the comment is what the next person picking a
/// code reads, so the code moved and the comment now says what is true.
///
/// 83 is genuinely free: this binary emits 0, 1, 64-82, and this module's own
/// 83/84. It stays clear of the NSIS-side 110-124 band in
/// `nsis-hooks-bootstrap.nsh`.
pub const SERVICE_START_FAILED_EXIT_CODE: i32 = 83;

/// The exit code `--civiccast-register-native-service` reports when the
/// service is RUNNING but the control plane is not actually SERVING.
///
/// **Installer-path audit BL-11.** Before this, nothing in the entire
/// elevated install chain ever contacted `/health`: `sc.exe query` reporting
/// `RUNNING` -- i.e. `pythonservice.exe` told the SCM it had started -- was
/// the only success signal the installer had. That says nothing about
/// Postgres being up, the control plane binding 8000, or the schema matching
/// the code. Gate A run 33681670855 is the exact shape: the service
/// registered, SCM said RUNNING, `/health` returned 200
/// `{"status":"degraded","schema":"behind"}`, the installer wrote
/// `InstalledVersion`, exited 0, and the wizard showed its success page over
/// a box serving 500s. PR #143 taught the HARNESS to read the body; it did
/// not teach the INSTALLER.
///
/// Distinct from [`SERVICE_START_FAILED_EXIT_CODE`] because the operator
/// remedy is completely different: 83 means the service will not run at all;
/// 84 means it runs and refuses to serve, which on this product almost always
/// means the database schema did not advance.
pub const SERVICE_NOT_SERVING_EXIT_CODE: i32 = 84;

/// Pure command construction for `sc.exe start CivicCastSupervisor` -- the
/// same `sc <command> <servicename>` grammar [`service_stop_command`] uses.
pub fn service_start_command() -> Vec<String> {
    vec!["start".to_string(), SERVICE_NAME.to_string()]
}

/// What issuing `sc.exe start` accomplished, before any polling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ServiceStartExitOutcome {
    /// Exit 0: the start was accepted. The service is very likely still
    /// `START_PENDING` and must be polled to a real RUNNING state (see
    /// [`wait_for_service_running`]).
    StartIssued,
    /// 1056: already running -- idempotent success, no polling needed.
    AlreadyRunning,
    /// Anything else, INCLUDING 1060 (does not exist). Unlike the stop path,
    /// "the service is not there" is not an idempotent success here: this runs
    /// immediately after `register_native_service` claimed to have created it,
    /// so its absence means the registration did not really take.
    Failed,
}

/// Pure classifier over `sc.exe start`'s process exit code, mirroring
/// [`classify_service_stop_exit_code`] so the idempotency mapping is testable
/// without a live `sc.exe`.
fn classify_service_start_exit_code(exit_code: Option<i32>) -> ServiceStartExitOutcome {
    match exit_code {
        Some(0) => ServiceStartExitOutcome::StartIssued,
        Some(code) if code == ERROR_SERVICE_ALREADY_RUNNING => {
            ServiceStartExitOutcome::AlreadyRunning
        }
        _ => ServiceStartExitOutcome::Failed,
    }
}

/// Parse `sc.exe query`'s captured stdout for a `STATE` line reporting
/// `RUNNING`. Deliberately as narrow as [`service_query_reports_stopped`]:
/// the trimmed line must START with `STATE`, and `START_PENDING` /
/// `STOP_PENDING` / `CONTINUE_PENDING` are all real, different states that do
/// not contain the substring `RUNNING`, so none of them can be mistaken for a
/// service that is actually up.
fn service_query_reports_running(stdout: &str) -> bool {
    stdout.lines().any(|line| {
        let trimmed = line.trim_start();
        trimmed.starts_with("STATE") && trimmed.contains("RUNNING")
    })
}

/// How long [`wait_for_service_running`] polls before giving up.
///
/// DERIVED, then coder-pinned (same treatment as this module's failure-action
/// ladder, and stated here rather than left as a bare number). Windows' own
/// SCM gives a service `ServicesPipeTimeout` -- 30 s by default -- to report
/// its first status before the SCM itself gives up with error 1053. The
/// CivicCastSupervisor host is embedded-Python (`pythonservice.exe`) importing
/// the whole `civiccast` package tree off a disk that was written seconds
/// earlier by this very install, with nothing warm in the page cache, so the
/// first RUNNING report is the slowest it will ever be at exactly this moment.
/// 120 s is 4x the SCM's own documented budget, so this wait always outlasts
/// the SCM's decision instead of racing it -- the same "never fail before the
/// authority that owns the timeout does" reasoning
/// [`SERVICE_STOP_POLL_TIMEOUT_SECS`] uses against the supervisor's own stop
/// watchdog.
const SERVICE_START_POLL_TIMEOUT_SECS: u64 = 120;

/// Thin execution wrapper (untested directly -- same HARD RULE as
/// [`register_native_service`]): poll `sc.exe query` until the service reports
/// RUNNING, reports STOPPED (the host started and immediately died -- fail
/// NOW rather than waiting out the whole deadline over a service that is
/// never coming up), disappears, or the
/// [`SERVICE_START_POLL_TIMEOUT_SECS`] deadline elapses. Never a fixed sleep
/// as the only wait: each iteration re-queries live state.
#[cfg(target_os = "windows")]
fn wait_for_service_running() -> Result<(), String> {
    let deadline =
        std::time::Instant::now() + std::time::Duration::from_secs(SERVICE_START_POLL_TIMEOUT_SECS);
    loop {
        let output = std::process::Command::new("sc.exe")
            .args(service_query_command())
            .output()
            .map_err(|error| format!("Could not run sc.exe to query {SERVICE_NAME}: {error}"))?;
        if output.status.code() == Some(ERROR_SERVICE_DOES_NOT_EXIST) {
            return Err(format!(
                "{SERVICE_NAME} disappeared while waiting for it to start -- the service \
                 registration did not take."
            ));
        }
        let stdout = String::from_utf8_lossy(&output.stdout);
        if service_query_reports_running(&stdout) {
            return Ok(());
        }
        if service_query_reports_stopped(&stdout) {
            return Err(format!(
                "{SERVICE_NAME} was started but fell straight back to STOPPED. The supervisor \
                 service host could not stay up; see %ProgramData%\\CivicCast\\logs and the \
                 Windows Application event log for the failure."
            ));
        }
        if std::time::Instant::now() >= deadline {
            return Err(format!(
                "Timed out after {SERVICE_START_POLL_TIMEOUT_SECS}s waiting for {SERVICE_NAME} \
                 to reach RUNNING state."
            ));
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

/// Start the LocalSystem supervisor service via `sc.exe start`, then poll
/// until it is genuinely RUNNING -- never trusting `sc start`'s immediate
/// return, which only means the start request was accepted.
///
/// Idempotent: an already-RUNNING service (1056) is SUCCESS. Fails LOUD
/// otherwise -- a service that registers but cannot start is an install
/// failure, not a warning, and the D4 CLI's caller
/// (`nsis-hooks-bootstrap.nsh`'s `d4-service-registration` block) already
/// aborts the whole install on any nonzero exit from that subcommand.
///
/// No reboot-pending exemption: `reboot_required` exists in this codebase only
/// as an installer-state field of the WSL lane (`main.rs`'s
/// `installer_state_reboot_required`, gated on `is_wsl_lane`). The native D4
/// chain has no such concept, and inventing one here would be inventing a
/// value the spec does not pin.
#[cfg(target_os = "windows")]
pub fn start_native_service() -> Result<(), String> {
    let output = std::process::Command::new("sc.exe")
        .args(service_start_command())
        .output()
        .map_err(|error| format!("Could not run sc.exe to start {SERVICE_NAME}: {error}"))?;
    match classify_service_start_exit_code(output.status.code()) {
        ServiceStartExitOutcome::AlreadyRunning => Ok(()),
        ServiceStartExitOutcome::StartIssued => wait_for_service_running(),
        ServiceStartExitOutcome::Failed => Err(format!(
            "Could not start {SERVICE_NAME} (sc.exe start exit {:?}): {}{}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )),
    }
}

// ---------------------------------------------------------------------------
// Control-plane READINESS gate (installer-path audit BL-11)
//
// Same pure/thin-wrapper split as the start machinery above: the body
// classification is pure and unit-tested; the socket read and the poll loop
// are thin wrappers.
// ---------------------------------------------------------------------------

/// What the control plane's own `/health` body says about whether the station
/// can actually serve.
///
/// `civiccast/app.py`'s `/health` docstring is explicit that HTTP 200 is
/// LIVENESS ONLY -- "always 200 while the process answers, in every schema
/// state" -- and that readiness is the body's `status` field, with `schema`
/// reporting migration currency. Every variant here is a distinct operator
/// situation with a distinct remedy, which is why this is an enum and not a
/// bool.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlPlaneReadiness {
    /// `status == "healthy"` AND `schema == "current"` AND the reported
    /// revision equals the reported head. The station can serve.
    Serving,
    /// The process answers, but the database is not at the schema this code
    /// expects. This is the Gate A run 33681670855 state.
    SchemaNotCurrent {
        schema: String,
        db_revision: String,
        expected_head: String,
    },
    /// The process answers and the schema is current, but readiness is not
    /// `healthy` for some other reason.
    NotHealthy { status: String },
    /// No 200, no parseable body, or a body missing the fields the contract
    /// requires. Fails CLOSED -- never treated as serving.
    Unreadable { detail: String },
}

/// Pull a `"key":"value"` string field out of a whitespace-stripped JSON body.
///
/// Deliberately the same primitive [`crate::health_response_is_ok`] already
/// uses (substring matching over a compacted body) rather than a new JSON
/// dependency: this crate parses `/health` in exactly one shape, from one
/// producer, and adding serde_json to the installer for four fields would be
/// a heavier change than the finding warrants.
fn compact_json_string_field(compact: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\":\"");
    let start = compact.find(&needle)? + needle.len();
    let rest = &compact[start..];
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

/// Compact a raw HTTP response into its body with all whitespace removed, or
/// `None` when it is not a 200.
fn compact_health_body(response: &str) -> Option<String> {
    if !(response.starts_with("HTTP/1.1 200 ") || response.starts_with("HTTP/1.0 200 ")) {
        return None;
    }
    let body = response.split_once("\r\n\r\n").map(|(_, body)| body)?;
    Some(body.chars().filter(|c| !c.is_whitespace()).collect())
}

/// Pure classification of a raw `/health` HTTP response. Unit-tested; no I/O.
pub fn classify_control_plane_readiness(response: &str) -> ControlPlaneReadiness {
    let Some(compact) = compact_health_body(response) else {
        return ControlPlaneReadiness::Unreadable {
            detail: "the control plane did not answer /health with HTTP 200".to_string(),
        };
    };
    let Some(status) = compact_json_string_field(&compact, "status") else {
        return ControlPlaneReadiness::Unreadable {
            detail: "the /health body carries no \"status\" field".to_string(),
        };
    };
    let Some(schema) = compact_json_string_field(&compact, "schema") else {
        return ControlPlaneReadiness::Unreadable {
            detail: "the /health body carries no \"schema\" field".to_string(),
        };
    };
    // These two are unconditional in app.py as of PR #143 -- their absence
    // means an older control plane than this installer ships, which is itself
    // a state the installer must not call success.
    let Some(db_revision) = compact_json_string_field(&compact, "schema_db_revision") else {
        return ControlPlaneReadiness::Unreadable {
            detail: "the /health body carries no \"schema_db_revision\" field".to_string(),
        };
    };
    let Some(expected_head) = compact_json_string_field(&compact, "schema_expected_head") else {
        return ControlPlaneReadiness::Unreadable {
            detail: "the /health body carries no \"schema_expected_head\" field".to_string(),
        };
    };
    // Judge the REVISIONS, not only the label. `schema == "current"` is
    // computed by the same process that reports the two revisions, so it adds
    // nothing on its own; requiring both means a control plane whose label and
    // revisions disagree cannot pass either.
    if schema != "current" || db_revision != expected_head {
        return ControlPlaneReadiness::SchemaNotCurrent {
            schema,
            db_revision,
            expected_head,
        };
    }
    if status != "healthy" {
        return ControlPlaneReadiness::NotHealthy { status };
    }
    ControlPlaneReadiness::Serving
}

/// The operator-facing sentence for a readiness outcome that is not serving.
///
/// Split out from the poll loop so the message contract is unit-testable and
/// so the NSIS side's own text (which cannot see this string) stays honest
/// about what the exit code means.
pub fn control_plane_readiness_failure_message(outcome: &ControlPlaneReadiness) -> String {
    match outcome {
        ControlPlaneReadiness::Serving => String::new(),
        ControlPlaneReadiness::SchemaNotCurrent {
            schema,
            db_revision,
            expected_head,
        } => format!(
            "The CivicCast (Native) service is running, but its database schema is not the one \
             this version needs (schema: {schema}; database revision {db_revision}, this build \
             expects {expected_head}). The station would answer its staff pages with errors. \
             Nothing was deleted -- your recordings, database and settings are intact. See \
             %ProgramData%\\CivicCast\\upgrade\\upgrade-engine.log and \
             %ProgramData%\\CivicCast\\install-progress.log, then re-run setup."
        ),
        ControlPlaneReadiness::NotHealthy { status } => format!(
            "The CivicCast (Native) service is running, but it reports itself as \"{status}\" \
             rather than healthy, so it is not ready to serve. See \
             %ProgramData%\\CivicCast\\logs and the Windows Application event log."
        ),
        ControlPlaneReadiness::Unreadable { detail } => format!(
            "The CivicCast (Native) service is running, but Windows could not confirm the \
             station is actually serving: {detail}. See %ProgramData%\\CivicCast\\logs and the \
             Windows Application event log."
        ),
    }
}

/// How long [`wait_for_control_plane_ready`] polls before giving up.
///
/// The service reaching SCM RUNNING only means `pythonservice.exe` started;
/// the supervisor then brings up Postgres and the control plane behind it,
/// which on a first boot includes creating the cluster's shared buffers and
/// the app's own startup work. This bound is deliberately the same order as
/// [`SERVICE_START_POLL_TIMEOUT_SECS`]'s own derivation: long enough that a
/// slow-but-healthy first boot is not failed, short enough that a silent
/// install cannot hang the operator's machine indefinitely.
pub const CONTROL_PLANE_READY_TIMEOUT_SECS: u64 = 180;

/// The `/health` endpoint the install chain probes. Same address `main.rs`'s
/// GUI-side probe uses; stated here so the elevated chain does not depend on
/// a constant defined for a different lane.
const CONTROL_PLANE_HEALTH_ADDR: &str = "127.0.0.1:8000";

/// One `/health` request over a bounded TCP connection. Returns the raw HTTP
/// response, or `None` when the socket could not be used at all.
#[cfg(target_os = "windows")]
fn read_control_plane_health_once() -> Option<String> {
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration;

    let address: SocketAddr = CONTROL_PLANE_HEALTH_ADDR.parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    stream
        .write_all(
            b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:8000\r\nConnection: close\r\n\r\n",
        )
        .ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    Some(response)
}

/// Poll `/health` until the control plane reports it can actually SERVE.
///
/// **Installer-path audit BL-11.** This is the gate the elevated install
/// chain never had. `start_native_service()` returning `Ok(())` proves the
/// SCM reached RUNNING and nothing more. The last observed outcome (not the
/// first, and not a generic timeout string) is what the operator is told, so
/// a schema-behind station says so by name instead of "the service did not
/// start".
#[cfg(target_os = "windows")]
pub fn wait_for_control_plane_ready() -> Result<(), String> {
    let deadline = std::time::Instant::now()
        + std::time::Duration::from_secs(CONTROL_PLANE_READY_TIMEOUT_SECS);
    let mut last = ControlPlaneReadiness::Unreadable {
        detail: "the control plane never answered /health on 127.0.0.1:8000".to_string(),
    };
    loop {
        if let Some(response) = read_control_plane_health_once() {
            let outcome = classify_control_plane_readiness(&response);
            if outcome == ControlPlaneReadiness::Serving {
                return Ok(());
            }
            last = outcome;
        }
        if std::time::Instant::now() >= deadline {
            return Err(control_plane_readiness_failure_message(&last));
        }
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
}

/// Production [`crate::native_pack_staging::TreeRebuildAuthority`]: the ONE
/// thing standing between `ensure_pack_extracted`'s destructive rebuild path
/// and deleting the CivicCastSupervisor service's own binaries (and its
/// long-lived `postgres.exe` child) out from under a
/// still-running process. This module's own [`stop_native_service`] doc
/// comment already named the obligation ("callers... MUST call this first");
/// this struct is what makes that obligation impossible to skip -- every
/// caller of `ensure_pack_extracted` must supply a `&dyn TreeRebuildAuthority`,
/// and this is the only implementation of it wired into the two production
/// CLIs (`--civiccast-stage-packs`, `--civiccast-repair`) in `main.rs`.
///
/// Stops the service AT MOST ONCE per instance: several required components
/// can each independently need a destructive rebuild in the same
/// stage/repair run (e.g. both `native-server-binaries` and
/// `native-app-payload` corrupt at once), and re-running `sc.exe stop`
/// against an already-stopped service once per component would be pure
/// noise (and, per [`stop_native_service`]'s own idempotency, harmless but
/// wasteful). The first call's outcome -- success, or the exact failure
/// string -- is captured in a `OnceCell` and replayed for every subsequent
/// [`authorize_rebuild`](TreeRebuildAuthority::authorize_rebuild) call on the
/// same instance.
#[cfg(target_os = "windows")]
#[derive(Default)]
pub struct ServiceQuiescenceAuthority {
    stop_outcome: std::cell::OnceCell<Result<(), String>>,
}

#[cfg(target_os = "windows")]
impl ServiceQuiescenceAuthority {
    pub fn new() -> Self {
        Self::default()
    }

    /// Whether a destructive rebuild actually asked for quiescence during this
    /// run -- i.e. whether we issued a stop.
    ///
    /// This matters to the operator, not just to us: nothing in the repair
    /// path starts the service again (`reregister_service_and_firewall`
    /// re-registers it, which is not a start), so a repair that had to rebuild
    /// a tree leaves the station down. Callers use this to say so plainly
    /// instead of returning success over a stopped station. Deliberately not
    /// an auto-restart: starting a service an operator stopped on purpose is
    /// its own defect.
    /// True only when a stop was attempted AND succeeded. A cached `Err` means
    /// the stop could not be confirmed, the rebuild was therefore refused, and
    /// nothing was torn down -- telling the operator "the service was stopped"
    /// in that case would be a false statement about the state of their
    /// station, on the one path where they most need an accurate one.
    pub fn stopped_for_rebuild(&self) -> bool {
        matches!(self.stop_outcome.get(), Some(Ok(())))
    }
}

#[cfg(target_os = "windows")]
impl crate::native_pack_staging::TreeRebuildAuthority for ServiceQuiescenceAuthority {
    fn authorize_rebuild(&self, component: &str) -> Result<(), String> {
        let outcome = self.stop_outcome.get_or_init(stop_native_service);
        outcome.clone().map_err(|error| {
            format!(
                "could not confirm {SERVICE_NAME} is stopped before rebuilding the extracted \
                 tree for {component} -- refusing to delete it while the service (and any \
                 long-lived postgres.exe child running out of that tree) may \
                 still be live: {error}. Stop the service manually (`sc.exe stop {SERVICE_NAME}`) \
                 and re-run, or investigate why sc.exe stop failed."
            )
        })
    }
}

/// Non-Windows fallback: there is no SCM to stop anything on, so this fails
/// CLOSED -- the same posture [`repair_selector`] and
/// `reregister_service_and_firewall`'s non-Windows counterparts already take
/// in `native_repair.rs`. This is never wired into a real code path outside
/// `cargo test`/cross-platform `cargo check` on this crate; the shipped
/// product is Windows-only.
#[cfg(not(target_os = "windows"))]
#[derive(Default)]
pub struct ServiceQuiescenceAuthority;

#[cfg(not(target_os = "windows"))]
impl ServiceQuiescenceAuthority {
    pub fn new() -> Self {
        Self::default()
    }
}

#[cfg(not(target_os = "windows"))]
impl crate::native_pack_staging::TreeRebuildAuthority for ServiceQuiescenceAuthority {
    fn authorize_rebuild(&self, _component: &str) -> Result<(), String> {
        Err("service quiescence cannot be confirmed on this platform (requires Windows SCM \
             access); refusing the destructive rebuild."
            .to_string())
    }
}

/// Pure command construction: `$INSTDIR\runtime\python.exe -m
/// civiccast.native.supervisor.service_host remove`.
///
/// **CRITICAL CONTRACT, load-bearing (same class of bug as
/// [`service_registration_command`]'s doc comment, which already cost this
/// project multiple live Sandbox test-run failures): pywin32's
/// `win32serviceutil.HandleCommandLine` parses `[options] command` --
/// OPTIONS BEFORE THE COMMAND, always.** `remove` takes no options today, so
/// the argv below is unambiguous -- but if a future change ever needs to pass
/// an option alongside `remove` (e.g. `--wait`), it MUST be inserted BEFORE
/// `"remove"` in this Vec, never after. Appending it after would silently
/// reproduce the exact failure mode `service_registration_command` already
/// hit live (pywin32 prints usage and exits 1 instead of running the
/// command).
pub fn service_removal_command(install_root: &Path) -> (PathBuf, Vec<String>) {
    let python_exe = install_root.join("runtime").join("python.exe");
    let args = vec![
        "-m".to_string(),
        "civiccast.native.supervisor.service_host".to_string(),
        "remove".to_string(),
    ];
    (python_exe, args)
}

/// Run the pywin32-driven removal command and classify its result.
/// Idempotent: `RemoveService`'s `SmartOpenService` raises
/// `ERROR_SERVICE_DOES_NOT_EXIST` (1060) when the service is already absent,
/// which `HandleCommandLine` (`service_host.py::main`) returns unchanged as
/// the process exit code -- treated as success, the SAME constant
/// [`stop_native_service`] treats as success for the analogous `sc.exe`
/// case.
///
/// Defensive fallback: if `python.exe` itself cannot be spawned (e.g. a
/// prior partial teardown already deleted `$INSTDIR\runtime`), this does NOT
/// immediately fail -- it falls back to an `sc.exe query` to check whether
/// the service is registered at all. If the service is genuinely absent, the
/// missing interpreter is moot and this is still success; only a still-
/// registered service with no interpreter available to remove it is a real,
/// reported failure.
#[cfg(target_os = "windows")]
fn run_service_removal(install_root: &Path) -> Result<(), String> {
    let (python_exe, args) = service_removal_command(install_root);
    let spawned = std::process::Command::new(&python_exe).args(&args).output();
    let output = match spawned {
        Ok(output) => output,
        Err(spawn_error) => {
            let query = std::process::Command::new("sc.exe")
                .args(service_query_command())
                .output()
                .map_err(|query_error| {
                    format!(
                        "Could not run {} to remove {SERVICE_NAME} ({spawn_error}), and could \
                         not fall back to sc.exe query to check whether it is still registered: \
                         {query_error}",
                        python_exe.display()
                    )
                })?;
            return if query.status.code() == Some(ERROR_SERVICE_DOES_NOT_EXIST) {
                Ok(())
            } else {
                Err(format!(
                    "Could not run {} to remove {SERVICE_NAME} ({spawn_error}), and sc.exe query \
                     reports it is STILL REGISTERED -- manual removal (`sc.exe delete \
                     {SERVICE_NAME}`) is required.",
                    python_exe.display()
                ))
            };
        }
    };
    if output.status.success() || output.status.code() == Some(ERROR_SERVICE_DOES_NOT_EXIST) {
        return Ok(());
    }
    Err(format!(
        "Could not remove {SERVICE_NAME} (exit {:?}): {}{}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    ))
}

/// Stop (via [`stop_native_service`]) then remove the LocalSystem supervisor
/// service. An already-absent service is success at both steps -- the whole
/// function is safe to call against a machine that has already been fully
/// torn down.
///
/// Not called by [`teardown_native_state`] below: that orchestrator needs
/// "stop" and "remove" as two SEPARATELY reported steps (one printed line
/// each), so it calls [`stop_native_service`] and [`run_service_removal`]
/// directly rather than through this combined convenience wrapper (calling
/// this AFTER an explicit stop would just re-run an already-idempotent stop
/// a second time, harmlessly, but with no second step to report). Kept as a
/// public building block per the fix's build-a-pure-command-builder-and-
/// runner contract, and exempted from the unused-item lint rather than
/// deleted -- the same documentation/grep-seam convention `SERVICE_NAME` and
/// `firewall_delete_rule_command` above already use. Untested directly (same
/// HARD RULE as `register_native_service`/`stop_native_service`: real SCM
/// execution is not unit-tested); its two building blocks
/// ([`stop_native_service`]'s classifier, [`service_removal_command`]'s
/// shape) are each tested independently above.
#[allow(dead_code)]
#[cfg(target_os = "windows")]
pub fn unregister_native_service(install_root: &Path) -> Result<(), String> {
    stop_native_service()?;
    run_service_removal(install_root)
}

// ---------------------------------------------------------------------------
// DatabaseUrl
// ---------------------------------------------------------------------------

/// Pure validation performed before every registry write: reject an empty/
/// blank value, a value carrying control characters (which cannot round-trip
/// through the registry the way this module expects), and any value that is
/// not the `postgresql://` scheme `build_database_url` always produces --
/// each is a defensive fail-loud check on the value handed in, not a
/// reconstruction of how that value was built.
pub fn validate_database_url_value(value: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err("DatabaseUrl value must not be empty or blank.".to_string());
    }
    if value.chars().any(|character| character.is_control()) {
        return Err("DatabaseUrl value must not contain control characters.".to_string());
    }
    if !value.starts_with("postgresql://") {
        return Err(format!(
            "DatabaseUrl value must be a postgresql:// URL (the exact scheme \
             build_database_url always produces), got: {value:?}"
        ));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Registry key ACL hardening (municipal-shared-PC security fix, 2026-07-30).
//
// Audit finding, independently re-verified with measured ACLs on a real
// Windows 11 box: `create_subkey_with_flags` below creates
// `HKLM\SOFTWARE\CivicCast\Native` with NO security descriptor of its own,
// so it INHERITS `HKLM\SOFTWARE`'s default DACL -- under which
// `BUILTIN\Users` has `ReadKey`. `DatabaseUrl` holds the plaintext
// `postgresql://user:PASSWORD@host/db` connection string, so any ordinary
// local user on the shared station can read the production PostgreSQL
// credential straight out of the registry. On a municipal shared PC "another
// user of the same machine" is a realistic threat, not a hypothetical one.
//
// WHO ACTUALLY NEEDS ACCESS (investigated before restricting, per the fix
// task's explicit caution -- this must not break the running product):
// * The only WRITER is this installer process itself, which already runs
//   elevated (NSIS install/repair always requests admin) -- confirmed by
//   `write_database_url`'s own caller chain (`run_native_provision`, invoked
//   from `NSIS_HOOK_POSTINSTALL`) and by `nsis-hooks-bootstrap.nsh`'s
//   `ReadRegStr`/`--civiccast-provision` call sites, all inside the
//   already-elevated installer.
// * The only READER found anywhere in this codebase is the SAME installer,
//   reading its own prior write back (`ReadRegStr $R2/$R3 HKLM
//   "Software\CivicCast\Native" "DatabaseUrl"` in `nsis-hooks-bootstrap.nsh`)
//   to decide NOOP/FAIL_LOUD/RUN before a repair or reinstall.
// * `civiccast.native.supervisor.service.py` (the LocalSystem supervisor
//   service, ADR-0021 -- see `native\supervisor\control_client.py`'s "the
//   supervisor runs as LocalSystem" comment) reads its PostgreSQL
//   credential from the `DATABASE_URL` environment variable, NOT from this
//   registry key directly (grepped `civiccast/native` for
//   `SOFTWARE.{0,3}CivicCast.{0,3}Native` and `winreg`/`QueryValueEx` against
//   this exact key: zero hits outside the installer/provisioning modules
//   already named above). There is therefore no non-admin, non-LocalSystem
//   runtime component that needs to read this key -- restricting it to
//   SYSTEM + Administrators cannot break the running product.
//
// FIX: restrict the key's own DACL to SYSTEM + BUILTIN\Administrators full
// control, PROTECTED (inheritance disabled) so `HKLM\SOFTWARE`'s
// world-readable default DACL can never flow back in on a future
// `RegOpenKey`/reboot/GPO refresh. Same SDDL shape the Python side already
// uses for its own SYSTEM+Administrators-only kernel objects
// (`civiccast.native.runtime_guard.MUTEX_SDDL` ==
// `"D:P(A;;GA;;;SY)(A;;GA;;;BA)"`), reused here for cross-language
// consistency of "what SYSTEM+Administrators-only means in this codebase".
//
// Applied in the code that CREATES the key
// (`write_database_url`, every call -- idempotent and self-healing rather
// than a separate script an operator has to remember to run), not as a
// standalone remediation tool.
//
// No `windows`/`winapi` crate was already a dependency of this crate (see
// the module doc's "Service registration" section for why one was avoided
// there); `RegSetKeySecurity`/`RegGetKeySecurity` and the SDDL conversion
// functions are not exposed by the `winreg` crate itself, so this is a
// disclosed coder decision to add `windows-sys` as a DIRECT dependency --
// pinned to 0.59, the exact version `winreg 0.55` already pulls in
// transitively (see `Cargo.lock`), so this resolves against an
// already-vendored version rather than fetching a new one.
// ---------------------------------------------------------------------------

/// SYSTEM + Administrators full control, station virtual account read-only,
/// inheritance disabled (`P`). The runtime reads its database credential here.
#[cfg(target_os = "windows")]
pub const SYSTEM_ADMIN_ONLY_SDDL: &str = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;KR;;;S-1-5-80-3505678282-1640658708-3289577692-12763909-1392209962)";

#[cfg(target_os = "windows")]
mod registry_acl {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Authorization::{
        ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
    };
    use windows_sys::Win32::Security::{
        DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR,
    };
    use windows_sys::Win32::System::Registry::{RegSetKeySecurity, HKEY};

    /// Parse `sddl` and apply it as `hkey`'s DACL, protected (no inherited
    /// ACEs). Real Win32 registry-security calls -- untested directly by
    /// this pure/thin-wrapper split's own convention (see
    /// `register_native_service`'s doc comment); exercised against a REAL
    /// (non-admin, HKCU-scoped) key in
    /// `tests::write_value_to_key_hardens_the_dacl_...` below, which drives
    /// this exact function through [`super::write_value_to_key`].
    pub fn harden_key_acl(hkey: HKEY, sddl: &str) -> Result<(), String> {
        let wide_sddl: Vec<u16> = sddl.encode_utf16().chain(std::iter::once(0)).collect();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let converted = unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide_sddl.as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        };
        if converted == 0 {
            return Err(format!(
                "ConvertStringSecurityDescriptorToSecurityDescriptorW failed for SDDL {sddl:?}: \
                 {}",
                std::io::Error::last_os_error()
            ));
        }
        let set_result = unsafe {
            RegSetKeySecurity(
                hkey,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor,
            )
        };
        unsafe {
            LocalFree(descriptor as _);
        }
        if set_result != 0 {
            return Err(format!(
                "RegSetKeySecurity failed (Win32 error {set_result}): could not restrict the \
                 registry key's DACL to {sddl:?}"
            ));
        }
        Ok(())
    }

    /// Read `hkey`'s current DACL back out as an SDDL string -- the
    /// readback proof `tests::write_value_to_key_hardens_the_dacl_...` below
    /// checks the SID markers against (same convention as
    /// `civiccast.native.win_probes.RuntimeOwnerMutex.read_dacl_sddl` on the
    /// Python side: readback normalizes `GENERIC_ALL` to the object-specific
    /// mask, so tests assert on SID markers/the `P` flag, never the literal
    /// rights string).
    #[cfg(test)]
    pub fn read_dacl_sddl(hkey: HKEY) -> Result<String, String> {
        use windows_sys::core::PWSTR;
        use windows_sys::Win32::Security::Authorization::ConvertSecurityDescriptorToStringSecurityDescriptorW;
        use windows_sys::Win32::System::Registry::RegGetKeySecurity;

        // A fresh key hardened to exactly two ACEs (SYSTEM + Administrators)
        // is well under 1 KiB; 4 KiB is generous headroom without needing a
        // two-call size-probe dance for a test-only helper.
        let mut buffer = vec![0u8; 4096];
        let mut needed: u32 = buffer.len() as u32;
        let get_result = unsafe {
            RegGetKeySecurity(
                hkey,
                DACL_SECURITY_INFORMATION,
                buffer.as_mut_ptr() as PSECURITY_DESCRIPTOR,
                &mut needed,
            )
        };
        if get_result != 0 {
            return Err(format!("RegGetKeySecurity failed (Win32 error {get_result})"));
        }
        let mut sddl_ptr: PWSTR = std::ptr::null_mut();
        let mut sddl_len: u32 = 0;
        let converted = unsafe {
            ConvertSecurityDescriptorToStringSecurityDescriptorW(
                buffer.as_mut_ptr() as PSECURITY_DESCRIPTOR,
                SDDL_REVISION_1,
                DACL_SECURITY_INFORMATION,
                &mut sddl_ptr,
                &mut sddl_len,
            )
        };
        if converted == 0 {
            return Err(format!(
                "ConvertSecurityDescriptorToStringSecurityDescriptorW failed: {}",
                std::io::Error::last_os_error()
            ));
        }
        let sddl = unsafe {
            let slice = std::slice::from_raw_parts(sddl_ptr, sddl_len as usize);
            String::from_utf16_lossy(slice)
        };
        unsafe {
            LocalFree(sddl_ptr as _);
        }
        Ok(sddl)
    }
}

/// Real logic for [`write_database_url`], parameterized over the predefined
/// registry root -- the SAME pure/thin-wrapper split this file already uses
/// for command construction, applied here so the ACL-hardening step is
/// exercised by a real (non-admin, non-HKLM) test rather than only ever
/// running for the first time against a shared municipal PC's real HKLM.
/// `write_database_url` is a one-line call of this against
/// `HKEY_LOCAL_MACHINE`/[`DATABASE_URL_KEY`]; tests call it against
/// `HKEY_CURRENT_USER` and a scratch subkey path instead.
#[cfg(target_os = "windows")]
fn write_value_to_key(
    root: winreg::HKEY,
    key_path: &str,
    value_name: &str,
    value: &str,
    sddl: &str,
    validate: fn(&str) -> Result<(), String>,
) -> Result<(), String> {
    // The validator is a PARAMETER, not a hardcoded call: this function is
    // shared by every value written under this key, and each caller names
    // the check its own value must pass rather than skipping one.
    validate(value)?;
    let root_key = RegKey::predef(root);
    // KEY_ALL_ACCESS (not just KEY_READ|KEY_SET_VALUE): setting a NEW security
    // descriptor via RegSetKeySecurity requires WRITE_DAC on the handle
    // itself, not merely ownership of the key -- Windows checks the handle's
    // granted access for this call, so a narrower open here would make
    // `harden_key_acl` below fail ACCESS_DENIED even for the key's own
    // creator/owner (confirmed empirically: the first cut of this fix did
    // exactly that and failed win32 error 5 in this file's own test).
    let (key, _) = root_key
        .create_subkey_with_flags(key_path, KEY_ALL_ACCESS | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not create/open {key_path}: {error}"))?;
    // Harden the DACL BEFORE writing the secret value, minimizing the window
    // in which a freshly (re)created key could still carry the parent's
    // inherited, world-readable default DACL while holding a credential. Run
    // on every write (idempotent/self-healing), not just first creation --
    // see the section doc above for who needs access and why this is safe.
    registry_acl::harden_key_acl(key.raw_handle(), sddl)
        .map_err(|error| format!("Could not harden {key_path}'s ACL: {error}"))?;
    key.set_value(value_name, &value)
        .map_err(|error| format!("Could not write {value_name}: {error}"))?;
    let persisted: String = key
        .get_value(value_name)
        .map_err(|error| format!("Could not verify {value_name} after writing: {error}"))?;
    if persisted != value {
        return Err(format!(
            "{value_name} verification mismatch after write: wrote a value but the read-back \
             did not match."
        ));
    }
    Ok(())
}

/// Write + read-back verify, mirroring `native_uninstall.rs`'s
/// `write_postclear_marker`. NOT wired into the live NSIS POSTINSTALL flow
/// this round -- see the module doc's "DatabaseUrl" section.
#[cfg(target_os = "windows")]
pub fn write_database_url(value: &str) -> Result<(), String> {
    write_value_to_key(
        HKEY_LOCAL_MACHINE,
        DATABASE_URL_KEY,
        DATABASE_URL_VALUE_NAME,
        value,
        SYSTEM_ADMIN_ONLY_SDDL,
        validate_database_url_value,
    )
}

/// Every value under [`DATABASE_URL_KEY`] that carries a LIVE SECRET.
///
/// `DatabaseUrl` embeds the PostgreSQL password for the `civiccast_svc` role.
/// Written through [`write_value_to_key`] with [`SYSTEM_ADMIN_ONLY_SDDL`],
/// and deleted by [`delete_native_credential_values`] -- a station that no
/// longer exists must not leave it behind.
///
/// RETIRED: this set used to also carry the installer-handoff setup nonce
/// (`SetupNonce`). The nonce/handoff mechanism was retired in favor of the
/// control plane admitting first setup purely by checking the request's peer
/// IP is loopback (`civiccast/installer/router.py`'s
/// `_require_local_setup_request`), so there is no longer a second
/// credential-shaped value under this key to track here.
pub const CREDENTIAL_VALUE_NAMES: &[&str] = &[DATABASE_URL_VALUE_NAME];

/// Real logic for the deletion counterparts below, parameterized over the
/// predefined registry root -- the SAME pure/thin-wrapper split
/// [`write_value_to_key`] above already uses, and for the same reason: the
/// deletion path is then exercised by a real (non-admin, HKCU-scoped)
/// registry round trip in this file's own tests instead of running for the
/// first time against a real municipal PC's HKLM during an uninstall.
///
/// Idempotent in both directions: a value that is already absent, or a key
/// that is already absent, is success (`false` = "was already absent"), never
/// an error. Deletes ONLY the named values -- never the `CivicCast\Native` key
/// itself (other product state may legitimately live there) and never
/// `HKLM\SOFTWARE\CivicCast\ActiveRuntime`, which is owned end-to-end by
/// `native_uninstall.rs`'s preflight/postclear protocol.
#[cfg(target_os = "windows")]
fn delete_values_from_key(
    root: winreg::HKEY,
    key_path: &str,
    value_names: &[&'static str],
) -> Result<Vec<(&'static str, bool)>, String> {
    let root_key = RegKey::predef(root);
    let key = match root_key.open_subkey_with_flags(key_path, KEY_SET_VALUE | KEY_WOW64_64KEY) {
        Ok(key) => key,
        // The whole key is absent: every named value is inherently absent too.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(value_names.iter().map(|name| (*name, false)).collect());
        }
        Err(error) => {
            return Err(format!(
                "Could not open {key_path} to delete Native registry values: {error}"
            ))
        }
    };
    let mut results = Vec::new();
    for name in value_names {
        match key.delete_value(name) {
            Ok(()) => results.push((*name, true)),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                results.push((*name, false))
            }
            Err(error) => return Err(format!("Could not delete {name}: {error}")),
        }
    }
    Ok(results)
}

/// SECURITY FIX (F-02, sandbox newcomer re-walk `dd7f835f`, 2026-08-01):
/// delete every credential-bearing value under [`DATABASE_URL_KEY`]. Wired
/// into [`teardown_native_state`] as the "clear credentials" step, so it runs
/// on EVERY ordinary uninstall.
///
/// This REVERSES the 2026-07-30 coordinator decision that preserved
/// `DatabaseUrl` across uninstall. That decision reasoned from data
/// preservation: uninstall keeps `%PROGRAMDATA%\CivicCast` (including the
/// PostgreSQL cluster), and `DatabaseUrl` is that cluster's credential, so
/// keeping the cluster meant keeping the credential. The re-walk read the
/// live password verbatim out of the registry of a machine the product had
/// already been uninstalled from. Preserving DATA was never a decision to
/// leave a live SECRET behind: data is preserved; credentials are not.
///
/// DISCLOSED CONSEQUENCE (not hidden by this change -- see the
/// `NATIVE_D4_STATE_INVENTORY` entry in `native_uninstall.rs`): a reinstall
/// over the preserved cluster can no longer reuse it, because the password
/// cannot be reconstructed from anything on disk.
/// `civiccast.native.provision.__main__`'s decision matrix classifies that as
/// `FAIL_LOUD_MISSING_REGISTRY` and refuses rather than silently regenerating
/// a password the cluster would reject -- which is the correct fail-closed
/// behavior for that state, but it does mean uninstall-then-reinstall over
/// preserved data needs operator recovery. Re-establishing a credential for a
/// surviving, product-owned cluster is a separate unit of work.
#[cfg(target_os = "windows")]
pub fn delete_native_credential_values() -> Result<Vec<(&'static str, bool)>, String> {
    delete_values_from_key(HKEY_LOCAL_MACHINE, DATABASE_URL_KEY, CREDENTIAL_VALUE_NAMES)
}

/// Every value under [`DATABASE_URL_KEY`] that CLAIMS A PRODUCT IS INSTALLED.
///
/// Today that is exactly `InstalledVersion` -- the D3 gate's "a product is
/// installed, and it is THIS version" signal, written at the fully-successful
/// end of `NSIS_HOOK_POSTINSTALL`.
pub const INSTALL_MARKER_VALUE_NAMES: &[&str] = &[INSTALLED_VERSION_VALUE_NAME];

/// F-01, UNINSTALLER HALF (sandbox newcomer re-walk `dd7f835f`, 2026-08-01):
/// delete every value that claims a product is installed. Wired into
/// [`teardown_native_state`] as the "clear install markers" step.
///
/// The re-walk uninstalled through the product's own uninstaller -- which
/// reported "Uninstall was completed successfully" -- and the post-uninstall
/// sweep found `InstalledVersion=1.0.0-rc15` still in the registry. The very
/// next install read it (`nsis-hooks-bootstrap.nsh`'s D3 fresh-install gate
/// requires BOTH `InstalledVersion` and `DatabaseUrl` to be absent), so the
/// gate did not fire, `step d3-engine: begin (old=1.0.0-rc15)` ran the UPGRADE
/// engine against a product that was not installed, and the run ended in a
/// rollback. Uninstall -> reinstall was broken by state the uninstaller left.
///
/// This REVERSES the other half of the 2026-07-30 preservation decision, whose
/// stated purpose was to let a reinstall be treated "as an upgrade over
/// surviving data rather than a first-ever install". The re-walk is the
/// counter-evidence: on a machine with no product installed, that treatment is
/// not an upgrade, it is a false one -- and it failed the install.
///
/// The ROUTING side (how much the D3 gate should trust a version marker at
/// all, and what it does when the two signals disagree) is a separate unit of
/// work in flight on its own branch. This function is only the uninstaller's
/// obligation: leave nothing behind that claims a product is installed.
#[cfg(target_os = "windows")]
pub fn delete_native_install_marker_values() -> Result<Vec<(&'static str, bool)>, String> {
    delete_values_from_key(
        HKEY_LOCAL_MACHINE,
        DATABASE_URL_KEY,
        INSTALL_MARKER_VALUE_NAMES,
    )
}

/// What [`delete_native_key_if_empty`] actually did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NativeKeyOutcome {
    /// The key existed, was empty, and was removed.
    Deleted,
    /// The key was already gone -- idempotent success.
    AlreadyAbsent,
    /// The key still has a subkey or a value this teardown does not own;
    /// left standing rather than forced.
    NotEmptySkipped,
}

/// N-20 (carried, rewalk-de3aaf6f): `HKLM\SOFTWARE\CivicCast\Native` itself
/// used to survive uninstall as an empty key -- [`delete_native_credential_values`]
/// and [`delete_native_install_marker_values`] clear every value ever written
/// under it (the complete, closed set: [`CREDENTIAL_VALUE_NAMES`] +
/// [`INSTALL_MARKER_VALUE_NAMES`]), but neither ever removed the key.
///
/// Ownership-verified before deleting, per this file's own established
/// caution around `CivicCast\Native` (see [`delete_values_from_key`]'s doc
/// comment, which used to justify never touching the key at all on the
/// theory that "other product state may legitimately live there"): this
/// function proves the key is actually empty -- zero subkeys, zero values --
/// immediately before removing it. If anything unaccounted-for is present
/// (a value this teardown does not know about, or a subkey), the key is left
/// standing and reported as skipped, never forced. Root-parameterized (same
/// pure/thin-wrapper split as [`delete_values_from_key`]) so the real logic
/// is exercised against a scratch HKCU key in tests instead of firing for the
/// first time against HKLM on a real machine.
#[cfg(target_os = "windows")]
fn native_key_outcome_from(root: winreg::HKEY, key_path: &str) -> Result<NativeKeyOutcome, String> {
    let root_key = RegKey::predef(root);
    let key = match root_key.open_subkey_with_flags(key_path, KEY_READ | KEY_WOW64_64KEY) {
        Ok(key) => key,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(NativeKeyOutcome::AlreadyAbsent);
        }
        Err(error) => {
            return Err(format!("Could not open {key_path} to check whether it is empty: {error}"));
        }
    };
    let has_subkeys = key.enum_keys().next().is_some();
    let has_values = key.enum_values().next().is_some();
    drop(key);
    if has_subkeys || has_values {
        return Ok(NativeKeyOutcome::NotEmptySkipped);
    }
    match root_key.delete_subkey_with_flags(key_path, KEY_WOW64_64KEY) {
        Ok(()) => Ok(NativeKeyOutcome::Deleted),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(NativeKeyOutcome::AlreadyAbsent),
        Err(error) => Err(format!("Could not delete empty {key_path}: {error}")),
    }
}

/// Production entry point: [`native_key_outcome_from`] against the real
/// [`DATABASE_URL_KEY`] under `HKEY_LOCAL_MACHINE`. Wired into
/// [`teardown_native_state`] as the "clear empty Native key" step, gated
/// (like every other registry-clearing step) on [`may_clear_registry_state`]
/// and run AFTER both value-clearing steps, so the key is only ever
/// considered for removal once this teardown has already cleared everything
/// it knows how to clear from underneath it.
#[cfg(target_os = "windows")]
pub fn delete_native_key_if_empty() -> Result<NativeKeyOutcome, String> {
    native_key_outcome_from(HKEY_LOCAL_MACHINE, DATABASE_URL_KEY)
}

/// What [`delete_released_maintenance_blob`] actually did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MaintenanceBlobOutcome {
    /// A `state: "released"` blob was found and deleted.
    Deleted,
    /// No `Maintenance` value (or no `CivicCast` key at all) was present --
    /// idempotent success.
    AlreadyAbsent,
    /// A blob is present but its `state` is not `"released"` (most often
    /// `"held"` -- some other operation may still depend on it). Left
    /// untouched.
    HeldOrUnknownLeftInPlace,
    /// A blob is present but is not valid JSON, or has no readable `state`
    /// field. Left untouched rather than guessed at (this file's fail-closed
    /// convention for every other registry read -- see
    /// `read_interlock`'s Python-side counterpart in
    /// `civiccast.native.win_probes`).
    UnreadableLeftInPlace,
}

/// N-20 (carried, rewalk-de3aaf6f): a stale, already-`released` D7a
/// Maintenance interlock blob (`civiccast.native.win_probes.MaintenanceRecord`,
/// serialized as JSON into the `Maintenance` REG_SZ value under
/// [`CIVICCAST_ROOT_KEY`] by `take_interlock`/`release_interlock`) used to
/// survive uninstall -- nothing in the uninstall path ever cleared it.
///
/// Conservative by construction: only a blob whose own `state` field reads
/// exactly `"released"` is deleted. A `"held"` interlock is left alone (some
/// other operation may still be relying on it -- deleting it out from under a
/// live upgrade/provision run would be the same class of hazard the D7a
/// interlock exists to prevent), and unreadable/malformed JSON is left alone
/// rather than guessed at. Root-parameterized for the same testability reason
/// as [`native_key_outcome_from`].
#[cfg(target_os = "windows")]
fn maintenance_blob_outcome_from(
    root: winreg::HKEY,
    key_path: &str,
) -> Result<MaintenanceBlobOutcome, String> {
    let root_key = RegKey::predef(root);
    let key = match root_key.open_subkey_with_flags(key_path, KEY_READ | KEY_SET_VALUE | KEY_WOW64_64KEY) {
        Ok(key) => key,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(MaintenanceBlobOutcome::AlreadyAbsent);
        }
        Err(error) => {
            return Err(format!(
                "Could not open {key_path} to check the Maintenance interlock: {error}"
            ));
        }
    };
    let raw: String = match key.get_value(MAINTENANCE_VALUE_NAME) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(MaintenanceBlobOutcome::AlreadyAbsent);
        }
        Err(error) => {
            return Err(format!("Could not read {MAINTENANCE_VALUE_NAME}: {error}"));
        }
    };
    let parsed: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(value) => value,
        Err(_) => return Ok(MaintenanceBlobOutcome::UnreadableLeftInPlace),
    };
    if parsed.get("state").and_then(serde_json::Value::as_str) != Some("released") {
        return Ok(MaintenanceBlobOutcome::HeldOrUnknownLeftInPlace);
    }
    match key.delete_value(MAINTENANCE_VALUE_NAME) {
        Ok(()) => Ok(MaintenanceBlobOutcome::Deleted),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(MaintenanceBlobOutcome::AlreadyAbsent),
        Err(error) => Err(format!("Could not delete {MAINTENANCE_VALUE_NAME}: {error}")),
    }
}

/// Production entry point: [`maintenance_blob_outcome_from`] against the real
/// [`CIVICCAST_ROOT_KEY`] under `HKEY_LOCAL_MACHINE`. Wired into
/// [`teardown_native_state`] as the "clear released maintenance interlock"
/// step, gated on [`may_clear_registry_state`] like every other
/// registry-clearing step. Never touches `ActiveRuntime` under the same key
/// -- that selector remains owned end-to-end by `native_uninstall.rs`'s
/// preflight/postclear protocol.
#[cfg(target_os = "windows")]
pub fn delete_released_maintenance_blob() -> Result<MaintenanceBlobOutcome, String> {
    maintenance_blob_outcome_from(HKEY_LOCAL_MACHINE, CIVICCAST_ROOT_KEY)
}

/// NEW Minor from the 2d1123a0 polish re-walk (`P01-postuninstall-2d1123a0.txt`):
/// N-20 correctly emptied `HKLM\SOFTWARE\CivicCast\Native` (removed once
/// empty) and cleared a released `Maintenance` blob under
/// `HKLM\SOFTWARE\CivicCast` -- but nothing ever removed the PARENT
/// `HKLM\SOFTWARE\CivicCast` key itself, so it survived uninstall, present
/// and now empty, once both of those were done. Post-uninstall sweep:
/// `HKLM\SOFTWARE\CivicCast\Native present: False`,
/// `Maintenance value present: False`, but
/// `HKLM\SOFTWARE\CivicCast key present: True`.
///
/// Same exact mechanism and safety discipline as [`delete_native_key_if_empty`]
/// one level down -- this is [`native_key_outcome_from`] against
/// [`CIVICCAST_ROOT_KEY`] itself, so the parent is independently
/// RE-VERIFIED empty (zero subkeys, zero values) with a FRESH read
/// immediately before it is deleted. This is exactly the safety property
/// that protects any OTHER CivicCast product or component (the WSL line, or
/// a future component) that might legitimately keep a subkey or a value
/// directly under `SOFTWARE\CivicCast`: if anything at all is still there --
/// including a `CivicCast\Native` subkey this run's own "clear empty Native
/// key" step declined to remove (`NotEmptySkipped`) -- this returns
/// `NotEmptySkipped` too and the parent is left standing, never forced.
///
/// Wired into [`teardown_native_state`] as the "clear empty CivicCast key"
/// step, the LAST teardown step, run only after the Native subkey removal
/// AND the Maintenance blob removal (both of which live directly under this
/// same parent) have already had their chance to empty out everything this
/// teardown knows how to clear from underneath it. Gated on
/// [`may_clear_registry_state`] like every other registry-clearing step.
#[cfg(target_os = "windows")]
pub fn delete_civiccast_root_key_if_empty() -> Result<NativeKeyOutcome, String> {
    native_key_outcome_from(HKEY_LOCAL_MACHINE, CIVICCAST_ROOT_KEY)
}

/// The typed PURGE seam: every value this installer writes under
/// [`DATABASE_URL_KEY`], credentials AND the `InstalledVersion` marker, in one
/// call. [`teardown_native_state`] no longer needs it (it drives the two
/// narrower steps above/below so it can report a precise per-step line), but a
/// future typed PURGE action that removes the cluster too wants the whole set
/// at once. Not called by any live path yet -- exempted from the unused-item
/// lint rather than deleted, the same documentation/grep-seam convention
/// [`SERVICE_NAME`] and `native_uninstall.rs`'s
/// `WSL_ARP_PROBE_LOADED_HIVES_ONLY` already use.
#[allow(dead_code)]
#[cfg(target_os = "windows")]
pub fn delete_native_registry_values() -> Result<Vec<(&'static str, bool)>, String> {
    delete_values_from_key(
        HKEY_LOCAL_MACHINE,
        DATABASE_URL_KEY,
        &[DATABASE_URL_VALUE_NAME, INSTALLED_VERSION_VALUE_NAME],
    )
}

// ---------------------------------------------------------------------------
// Live provisioning execution (WP2 provision-execution wiring): invoke the
// Python engine, do not rebuild it. See the module doc's "DatabaseUrl"
// section above for the full design.
// ---------------------------------------------------------------------------

/// The exact stdout marker line prefix
/// `civiccast.native.provision.__main__.HANDOFF_MARKER_PREFIX` prints the
/// resolved DatabaseUrl behind. Duplicated here (not imported -- Python and
/// Rust cannot share a literal across the process boundary) so both ends of
/// the handoff agree on the same one-line format; [`parse_provision_handoff`]
/// below is unit-tested against this exact prefix, mirroring
/// `test_provision_cli.py`'s own `parse_handoff_line` tests on the Python
/// side.
pub const PROVISION_HANDOFF_MARKER_PREFIX: &str = "CIVICCAST_DATABASE_URL=";

/// Pure command construction: `$INSTDIR\runtime\python.exe -m
/// civiccast.native.provision --install-root ... --owner-run-id ...
/// --pack-signing-key-id ... --pack-public-key-base64 ... --pack-product-version
/// ... --pack-compatible-core ... [--existing-database-url ...]` -- the same
/// invocation shape the D3 engine call and the D4 service/firewall
/// subcommands already use in `nsis-hooks-native.nsh`. `existing_database_url`
/// is omitted entirely (never passed as an empty string) when there is
/// nothing to pass, matching `civiccast.native.provision.__main__`'s own
/// `default=""` reading for "no registry value yet".
///
/// KNOWN, DELIBERATELY UNFIXED ARGV EXPOSURE (documented here per the
/// 2026-07-30 credential-hardening fix; not fixed in this change -- it needs
/// an interface change, which is a separate unit of work):
///
/// When `existing_database_url` is non-empty (a repair/reinstall over an
/// already-provisioned station), the CURRENT `DatabaseUrl` -- the live
/// PostgreSQL connection string, PASSWORD INCLUDED -- lands on the argv of
/// the `python.exe` child process this function's output is used to spawn
/// (`run_native_provision`, below). Any process enumeration API (Task
/// Manager's command-line column, `Get-CimInstance Win32_Process`, `wmic
/// process get commandline`, `NtQueryInformationProcess`, etc.) can read a
/// process's own argv for the lifetime of that process, so any local user
/// who can enumerate processes at the moment this child runs can read the
/// production database password -- the SAME class of exposure this
/// function's caller chain (`NSIS_HOOK_POSTINSTALL`, `nsis-hooks-bootstrap.nsh`)
/// already has ONE hop earlier: that hook places the identical value on the
/// installer EXE's own argv (`--existing-database-url "$R3"`) before this
/// function ever runs. Both hops share one fix shape: stop passing the
/// value as an argv token at all -- write it to the child's STDIN (the CLI
/// already reads argparse flags; a `--existing-database-url-stdin` flag
/// reading one line from stdin instead would need no other interface
/// change), or hand it across via a short-lived inherited pipe/handle
/// (`CreateProcess` with an explicit handle list), never a second temp file
/// (which reintroduces the same "is it ACL'd, is it cleaned up" class of
/// bug this fix pass is closing elsewhere). The window is short (this one
/// child process's lifetime, not persisted to disk), and requires a running
/// station's argv to be actively observed at the right moment, which is why
/// this is deliberately deferred rather than folded into this pass -- but it
/// is a real, confirmed gap, not a hypothetical one, and should not be
/// rediscovered from scratch.
#[allow(clippy::too_many_arguments)]
pub fn provision_command(
    install_root: &Path,
    owner_run_id: &str,
    pack_signing_key_id: &str,
    pack_public_key_base64: &str,
    pack_product_version: &str,
    pack_compatible_core: &str,
    existing_database_url: &str,
) -> (PathBuf, Vec<String>) {
    let python_exe = install_root.join("runtime").join("python.exe");
    let mut args = vec![
        "-m".to_string(),
        "civiccast.native.provision".to_string(),
        "--install-root".to_string(),
        install_root.display().to_string(),
        "--owner-run-id".to_string(),
        owner_run_id.to_string(),
        "--pack-signing-key-id".to_string(),
        pack_signing_key_id.to_string(),
        "--pack-public-key-base64".to_string(),
        pack_public_key_base64.to_string(),
        "--pack-product-version".to_string(),
        pack_product_version.to_string(),
        "--pack-compatible-core".to_string(),
        pack_compatible_core.to_string(),
    ];
    if !existing_database_url.is_empty() {
        args.push("--existing-database-url".to_string());
        args.push(existing_database_url.to_string());
    }
    (python_exe, args)
}

/// Find the ONE handoff line among arbitrary captured stdout. Mirrors
/// `civiccast.native.provision.__main__.parse_handoff_line` exactly: first
/// line starting with [`PROVISION_HANDOFF_MARKER_PREFIX`] wins, `None` if no
/// line matches.
pub fn parse_provision_handoff(captured_stdout: &str) -> Option<String> {
    captured_stdout
        .lines()
        .find_map(|line| line.strip_prefix(PROVISION_HANDOFF_MARKER_PREFIX))
        .map(|value| value.to_string())
}

/// Base64-encode an Ed25519 public key's raw 32 bytes (STANDARD alphabet,
/// matching `native_packs.rs`'s own `BASE64` usage) -- the ONE argv value
/// this module passes to the Python engine that is NOT credential-sensitive:
/// a public key is, by definition, safe on a command line or in a log.
pub fn encode_pack_public_key(public_key: &VerifyingKey) -> String {
    BASE64.encode(public_key.to_bytes())
}

/// What [`run_native_provision`] accomplished -- distinguishes a freshly
/// written registry value from a clean no-op so the caller's exit-code
/// mapping (and NSIS's `DetailPrint`) can say which happened without ever
/// needing the value itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProvisionOutcome {
    Provisioned,
    NoOp,
}

/// The exit code `--civiccast-provision` reports when this machine's runtime
/// ownership could not be established at all.
///
/// **Installer-path audit BL-13.** `claim_install_selector`'s
/// `LeaveUnprovable` outcome -- reached whenever the ActiveRuntime probe is
/// `Unreadable`, or the selector is `Absent` while the corroborated WSL
/// product evidence (`native_uninstall::corroborate_wsl_product_state`: the
/// per-user ARP probe, machine-wide ARP, the Lxss distro scan and the WSL
/// service presence) is `Present` or still `Unknown` -- used to print one
/// sentence and return `Ok`. That sentence itself says "The native runtime will not start
/// until an operator sets it". Exit 0 followed; the service registered,
/// `sc start` succeeded, the SCM reported RUNNING (the host process runs; the
/// guard blocks the control plane), and setup showed "installation complete"
/// over a station that can never serve.
///
/// Its own code rather than provisioning's generic 75 for the reason the
/// whole band exists: the exit code is the only signal a silent install's
/// support log carries about WHICH precondition failed, and the operator
/// remedy here (set ActiveRuntime, or fix the permission that made it
/// unreadable) shares nothing with a provisioning failure's.
///
/// Since beta.5.1 this is raised BEFORE the provisioning subprocess runs
/// (so the database configuration is untouched) and is accompanied by
/// `%ProgramData%\CivicCast\provision\ownership-observation.txt` (one
/// line, read back by the NSIS d4 step into `install-progress.log` and the
/// dialog) and `OWNERSHIP-RECOVERY.md` (every observation plus the remedy).
pub const SELECTOR_UNPROVABLE_EXIT_CODE: i32 = 85;

/// The exit code `--civiccast-provision` reports when the selector is absent
/// and ANOTHER CivicCast product is registered on this machine
/// (`SelectorClaimAction::LeaveOtherProductPresent`).
///
/// Round 3 of the beta.5 field defect (corrected report, 2026-09-09): the
/// refused box did not have an unreadable hive -- it had the old WSL-era
/// "CivicCast Installer" 3.0.0-beta1 ARP entry, and the decision table sent
/// that through the same exit 85 and the same "could not determine ...
/// permissions problem" text as the Unknown case. The remedy shares nothing
/// with 85's (uninstall that product or run the cutover from it -- never a
/// registry edit), so it gets its own code, for the reason the whole band
/// exists: the exit code is the only signal a silent install's support log
/// carries about WHICH precondition failed. 87 follows 86
/// (`UNKNOWN_CIVICCAST_FLAG_EXIT_CODE`).
pub const OTHER_PRODUCT_PRESENT_EXIT_CODE: i32 = 87;

/// The INSTALLER process exit code the NSIS d4 step maps CLI exit 85 to:
/// `CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP` in `nsis-hooks-bootstrap.nsh`.
/// `OWNERSHIP-RECOVERY.md` names it beside the CLI code, so it is a
/// constant pinned to the NSIS define by
/// `test_ownership_recovery_document_installer_exit_codes_are_pinned_to_the_nsis_defines`.
pub const SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE: i32 = 127;

/// The INSTALLER process exit code the NSIS d4 step maps CLI exit 87 to:
/// `CIVICCAST_EXIT_D4_OTHER_PRODUCT`. Round 4 (hostile review of round 3):
/// the recovery document used to say "installer exit 127" for BOTH
/// refusals, which was wrong for this one from the day 135 was defined.
pub const OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE: i32 = 135;

/// The INSTALLER exit code a refusal's d4 arm aborts with (`$0 == 87` ->
/// `CIVICCAST_EXIT_D4_OTHER_PRODUCT`, `$0 == 85` ->
/// `CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP`); `None` for anything that is not
/// a refusal, exactly as [`refusal_exit_code`].
///
/// Round 5 (delta review of round 4): an exhaustive `match` on the action,
/// not an `if`/`else` on the CLI code -- the old `else` handed every future
/// refusal 127, which is the very bug round 4 fixed, rebuilt by
/// construction. A new `SelectorClaimAction` variant is now a compile error
/// here until it is given its own installer code.
pub fn installer_exit_code_for(
    action: crate::native_uninstall::SelectorClaimAction,
) -> Option<i32> {
    use crate::native_uninstall::SelectorClaimAction;
    match action {
        SelectorClaimAction::LeaveOtherProductPresent => {
            Some(OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE)
        }
        SelectorClaimAction::LeaveUnprovable | SelectorClaimAction::LeaveUnreadable => {
            Some(SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE)
        }
        SelectorClaimAction::ClaimNative
        | SelectorClaimAction::AlreadyNative
        | SelectorClaimAction::LeaveWslOwnership => None,
    }
}

/// The exit code the binary reports for a `--civiccast-*` flag it does not
/// implement.
///
/// <installer-path-audit MA-22> `main()` used to fall through to the Tauri
/// GUI event loop for anything unmatched, and that loop never exits. Since
/// `NSIS_HOOK_PREINSTALL` invokes the OLD, already-installed binary's CLI
/// (`--civiccast-stop-native-service`) without checking its version first, a
/// future release that renames or removes a flag would make upgrading FROM
/// that version launch its GUI under `nsExec` with no timeout: an installer
/// alive with no children and no visible position in the chain -- the exact
/// run-3/run-4 hang shape. 86 continues the 83-85 band this batch opened.
pub const UNKNOWN_CIVICCAST_FLAG_EXIT_CODE: i32 = 86;

/// A provisioning failure that carries the exit code its caller must report.
///
/// Introduced for BL-13: before it, every failure inside
/// [`run_native_provision`] was an untyped `String` that `main.rs` mapped to
/// a single 75, so a new, genuinely different precondition failure had no way
/// to reach the operator as itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProvisionFailure {
    pub exit_code: i32,
    pub message: String,
}

impl ProvisionFailure {
    /// The generic provisioning failure code the CLI has always reported.
    pub const GENERIC_EXIT_CODE: i32 = 75;

    fn generic(message: String) -> Self {
        Self {
            exit_code: Self::GENERIC_EXIT_CODE,
            message,
        }
    }
}

impl std::fmt::Display for ProvisionFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

// ---------------------------------------------------------------------------
// Runtime-ownership observation report (beta.5.1 field defect)
// ---------------------------------------------------------------------------

/// File name of the ONE-LINE ownership observation the NSIS d4 step reads
/// back (`FileRead`) into `install-progress.log` and, on exit 85, into the
/// dialog. Lives beside the Python engine's own `PROVISION-RECOVERY.md`.
pub const OWNERSHIP_OBSERVATION_FILE_NAME: &str = "ownership-observation.txt";
/// File name of the operator recovery document written on the exit-85 path.
pub const OWNERSHIP_RECOVERY_DOCUMENT_FILE_NAME: &str = "OWNERSHIP-RECOVERY.md";
/// Upper bound on the one-line observation. `NSIS_MAX_STRLEN` is 1024 in
/// Tauri's NSIS 3.11 (measured with `makensis -HDRINFO`); the exit-85 and
/// exit-87 dialogs in `nsis-hooks-bootstrap.nsh` embed this line inside
/// their own static text (520/542 chars at runtime; 540/558 counted as the
/// source literal) and `CIVICCAST_ALERT` wraps that in a timestamp,
/// `ALERT: ` and a trailing CRLF (31 chars at runtime, 35 as source) in
/// the one string it writes to `install-progress.log`, so 420 keeps the
/// whole string within 1022 (558 + 420 + 35 = 1013 worst case; maximum
/// safe value of this constant for the current text: 429). Round 4:
/// `test_the_exit_85_and_87_dialogs_fit_the_nsis_string_budget_with_the_observation`
/// MEASURES both static strings under both counts on every run and names
/// the maximum safe value of this constant when it fails -- the round-3
/// text had been 564/584 and the 420 cap overflowed. Round 5: the test
/// also derives the limit (from `makensis` when present) and the log-line
/// overhead (from the macros) instead of carrying either as a number.
/// Raised from 360 in round 3 so the exit-87 lead -- user, product,
/// version, an `HKU\<SID>` key, InstallLocation AND UninstallString (351
/// chars with a 6-char account name, a real SID and real paths) -- survives
/// the truncation marker; the real truncation point is
/// [`OWNERSHIP_OBSERVATION_LINE_KEEP`] (420 - 48 = 372), and round 5 builds
/// the lead field by field (InstallLocation dropped first) so a long
/// account name cannot push it past that point.
/// The full observation list lives in the recovery document and the CLI's
/// stderr.
pub const OWNERSHIP_OBSERVATION_LINE_MAX_CHARS: usize = 420;

/// The marker appended when the observation line had to be cut; it points
/// at the recovery document, which always carries the whole observation.
const OWNERSHIP_OBSERVATION_TRUNCATION_MARKER: &str =
    " ... (full observation in OWNERSHIP-RECOVERY.md)";
// `OWNERSHIP_OBSERVATION_LINE_KEEP` would underflow if the cap were ever
// lowered below the marker; refuse to compile rather than panic at install
// time.
const _: () = assert!(OWNERSHIP_OBSERVATION_LINE_MAX_CHARS > 64);
const _: () = assert!(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER.len() <= 64);
/// The characters of a cut line that survive ahead of the marker -- the
/// REAL truncation point of the exit-87 lead (cap 420 - marker 48 = 372),
/// which is what the lead is built to fit under, not the cap itself.
pub const OWNERSHIP_OBSERVATION_LINE_KEEP: usize =
    OWNERSHIP_OBSERVATION_LINE_MAX_CHARS - OWNERSHIP_OBSERVATION_TRUNCATION_MARKER.len();

/// `<program_data>\CivicCast\provision` -- the SAME root the Python
/// provisioning engine writes its journal and `PROVISION-RECOVERY.md` to
/// (`civiccast.native.provision.__main__`, `PROGRAMDATA` env var with the
/// `C:\ProgramData` fallback).
fn ownership_report_root() -> PathBuf {
    let program_data = std::env::var("PROGRAMDATA")
        .ok()
        .map(PathBuf::from)
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    ownership_report_root_from(&program_data)
}

fn ownership_report_root_from(program_data: &Path) -> PathBuf {
    program_data.join("CivicCast").join("provision")
}

fn ownership_observation_path(report_root: &Path) -> PathBuf {
    report_root.join(OWNERSHIP_OBSERVATION_FILE_NAME)
}

fn ownership_recovery_document_path(report_root: &Path) -> PathBuf {
    report_root.join(OWNERSHIP_RECOVERY_DOCUMENT_FILE_NAME)
}

/// The one line NSIS reads back, folded to a single ASCII line and capped
/// at [`OWNERSHIP_OBSERVATION_LINE_MAX_CHARS`] (with a marker pointing at
/// the recovery document when it had to be cut). Evidence FIRST: when the
/// claim gathered evidence, the line is the verdict, the selector, and
/// [`WslPresenceEvidence::explain`] -- so the hive that could not be read
/// is the first thing after the verdict and survives the cap, rather than
/// trailing a full prose sentence that the cap would cut it out of. With
/// no evidence (the selector settled the question) the line is the claim's
/// own `detail` sentence. Pure; unit-tested.
///
/// [`WslPresenceEvidence::explain`]: crate::native_uninstall::WslPresenceEvidence::explain
pub fn ownership_observation_line(
    outcome: &crate::native_uninstall::SelectorClaimOutcome,
) -> String {
    use crate::native_uninstall::{SelectorClaimAction, WslProductVerdict};
    let inert = outcome.verdict == Some(WslProductVerdict::PresentInert);
    let verdict = match (outcome.action, &outcome.write_error) {
        (SelectorClaimAction::ClaimNative, None) => "ownership claimed (ActiveRuntime=native)",
        (SelectorClaimAction::ClaimNative, Some(_)) => "ownership claim WRITE FAILED",
        (SelectorClaimAction::AlreadyNative, _) => "ownership already native",
        (SelectorClaimAction::LeaveWslOwnership, _) => "ownership is WSL's; left unchanged",
        (SelectorClaimAction::LeaveOtherProductPresent, _) => {
            "another CivicCast product present; left unchanged"
        }
        (SelectorClaimAction::LeaveUnprovable, _) => "ownership NOT established; left unchanged",
        (SelectorClaimAction::LeaveUnreadable, _) => "ActiveRuntime unreadable; left unchanged",
    };
    let raw = match &outcome.evidence {
        Some(evidence) => {
            // Round 3: the Present refusal LEADS with the product it found
            // (user, name, version, key, InstallLocation, UninstallString) --
            // that sentence is what the dialog and the log must show first;
            // the inert-leftover claim leads with its WARNING for the same
            // reason. Both survive the cap; the per-source explanation
            // trails.
            let mut line = match outcome.action {
                SelectorClaimAction::LeaveOtherProductPresent => {
                    // Round 5 (delta review of round 4): the lead is built
                    // field by field. The account name appears three times
                    // in it, so a 16-char name with a real SID overran
                    // OWNERSHIP_OBSERVATION_LINE_KEEP and the cap cut the
                    // UninstallString in half. Drop InstallLocation -- a
                    // whole field, still in the recovery document -- first.
                    // Folding below never lengthens the text, so counting
                    // the unfolded lead is conservative.
                    let mut lead = evidence.describe_present_product();
                    if lead.chars().count() > OWNERSHIP_OBSERVATION_LINE_KEEP {
                        lead = evidence.describe_present_product_with(false);
                    }
                    format!(
                        "{lead}; {verdict}; selector {:?}; {}",
                        outcome.selector,
                        evidence.explain()
                    )
                }
                SelectorClaimAction::ClaimNative if inert => format!(
                    "{verdict}; {}; selector {:?}; {}",
                    inert_leftover_log_warning(evidence),
                    outcome.selector,
                    evidence.explain()
                ),
                _ => format!(
                    "{verdict}; selector {:?}; {}",
                    outcome.selector,
                    evidence.explain()
                ),
            };
            if let Some(error) = &outcome.write_error {
                line.push_str(&format!("; write error: {error}"));
            }
            line
        }
        None => outcome.detail.clone(),
    };
    let folded: String = raw
        .chars()
        .map(|c| match c {
            '\r' | '\n' | '\t' => ' ',
            c if c.is_ascii() => c,
            _ => '?',
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if folded.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS {
        return folded;
    }
    // Round 5: cut at the last "; " field boundary inside the budget (when
    // one exists in its second half) instead of mid-token, so what the
    // dialog shows is whole fields; the marker says where the rest is.
    // `folded` is ASCII by construction, so byte and char offsets agree.
    let mut cut: String = folded
        .chars()
        .take(OWNERSHIP_OBSERVATION_LINE_KEEP)
        .collect();
    if let Some(boundary) = cut.rfind("; ") {
        if boundary >= OWNERSHIP_OBSERVATION_LINE_KEEP / 2 {
            cut.truncate(boundary);
        }
    }
    cut.push_str(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER);
    cut
}

/// The one-line WARNING for the install log when native was claimed over an
/// inert WSL-product leftover: names the product, the user, and the
/// uninstall.exe to remove it with.
fn inert_leftover_log_warning(evidence: &crate::native_uninstall::WslPresenceEvidence) -> String {
    let (title, user, uninstall) = evidence
        .present_product()
        .map(|(_, product)| {
            (
                product.title(),
                product.user.clone(),
                product.uninstall_string.clone(),
            )
        })
        .unwrap_or_else(|| ("CivicCast Installer".to_string(), "(unknown)".to_string(), None));
    let at = uninstall
        .filter(|s| !s.trim().is_empty())
        .map(|path| format!(" (uninstall.exe at {path})"))
        .unwrap_or_default();
    format!(
        "WARNING: an old {title} registration remains for user {user} with no distro or \
         autostart; claimed native. Remove the leftover via Apps & Features{at}"
    )
}

/// The CLI exit code a refusal maps to (the recovery document names it).
/// Not a refusal -> `None`.
pub fn refusal_exit_code(action: crate::native_uninstall::SelectorClaimAction) -> Option<i32> {
    use crate::native_uninstall::SelectorClaimAction;
    if !action.is_refusal() {
        return None;
    }
    match action {
        SelectorClaimAction::LeaveOtherProductPresent => Some(OTHER_PRODUCT_PRESENT_EXIT_CODE),
        SelectorClaimAction::LeaveUnprovable | SelectorClaimAction::LeaveUnreadable => {
            Some(SELECTOR_UNPROVABLE_EXIT_CODE)
        }
        SelectorClaimAction::ClaimNative
        | SelectorClaimAction::AlreadyNative
        | SelectorClaimAction::LeaveWslOwnership => None,
    }
}

/// The operator-facing recovery document for a refused ownership claim:
/// the observation (every individual read, one per line; the ARP record of
/// any product found), what it means, and the exact remedy -- ONE remedy
/// per refusal. The `Present` document never carries the registry edit:
/// the value is not the problem, the other product is. Round 5 (delta
/// review of round 4): its "What it means" names the inert condition(s)
/// that actually failed ([`WslPresenceEvidence::unmet_inert_conditions`]),
/// and a machine-only (HKLM) entry -- which round 4's rule refuses for
/// good, and whose uninstaller may be gone -- gets the two remedies that
/// reach it: sign in as the owning account, or delete the stale key.
/// Round 6 (delta review of round 5): those remedies are offered only when
/// the per-machine probe found the entry and the per-user probe did not,
/// an `Unknown` per-user read is described as unread rather than absent,
/// the `reg delete` path is derived from [`WSL_ARP_KEY`], one command per
/// WOW64 view the entry was found in. Returns `None` for a non-refusal:
/// there is no document to write, and no exit code to fall back to (round
/// 5's `unwrap_or(85 / 127)` would have printed a refusal's codes over a
/// claim that succeeded). Pure; unit-tested.
///
/// [`WslPresenceEvidence::unmet_inert_conditions`]: crate::native_uninstall::WslPresenceEvidence::unmet_inert_conditions
/// [`WSL_ARP_KEY`]: crate::native_uninstall::WSL_ARP_KEY
pub fn ownership_recovery_document(
    outcome: &crate::native_uninstall::SelectorClaimOutcome,
    version: &str,
) -> Option<String> {
    use crate::native_uninstall::SelectorClaimAction;
    let present = outcome.action == SelectorClaimAction::LeaveOtherProductPresent;
    let exit_code = refusal_exit_code(outcome.action)?;
    let installer_exit_code = installer_exit_code_for(outcome.action)?;
    let mut doc = String::new();
    if present {
        doc.push_str("# CivicCast (Native) setup: another CivicCast product is installed\n\n");
    } else {
        doc.push_str(
            "# CivicCast (Native) setup: runtime ownership could not be established\n\n",
        );
    }
    doc.push_str(&format!(
        "Setup {version} stopped (exit {exit_code} / installer exit {installer_exit_code}) BEFORE provisioning the \
         PostgreSQL server: postgresql.conf, pg_hba.conf and your database credential were \
         not touched by this run. (On an upgrade, the program files under the install \
         directory had already been replaced before this check ran.)\n\n"
    ));
    doc.push_str("## What setup observed\n\n");
    doc.push_str(&format!("{}\n\n", outcome.detail));
    doc.push_str(&format!(
        "- `HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime` read as: {:?}\n",
        outcome.selector
    ));
    match &outcome.evidence {
        Some(evidence) => {
            doc.push_str(&format!(
                "- CivicCast WSL product evidence: user-ARP={:?}, machine-ARP={:?}, \
                 distro-scan={:?}, wsl-service={:?}, autostart={:?}, transfer-marker={:?}\n",
                evidence.user_arp,
                evidence.machine_arp,
                evidence.distro_registration,
                evidence.wsl_service,
                evidence.autostart,
                evidence.native_transfer_marker
            ));
            if let Some((observation, product)) = evidence.present_product() {
                doc.push_str(&format!(
                    "- Product found: registered at `{}\\{}` ({} view), for user `{}`\n",
                    observation.scope,
                    crate::native_uninstall::WSL_ARP_KEY,
                    observation.view,
                    product.user
                ));
                for (label, value) in [
                    ("DisplayName", &product.display_name),
                    ("DisplayVersion", &product.display_version),
                    ("Publisher", &product.publisher),
                    ("InstallLocation", &product.install_location),
                    ("UninstallString", &product.uninstall_string),
                ] {
                    doc.push_str(&format!(
                        "  - {label}: {}\n",
                        value.as_deref().unwrap_or("(not set)")
                    ));
                }
            }
            doc.push('\n');
            doc.push_str("Every individual read, in the order it was made:\n\n");
            if evidence.observations.is_empty() {
                doc.push_str("- (no individual observations were recorded)\n");
            }
            for line in evidence.observation_lines() {
                doc.push_str(&format!("- {line}\n"));
            }
            doc.push('\n');
        }
        None => doc.push_str(
            "- The WSL product was not probed: the selector's own state settled the question.\n\n",
        ),
    }
    doc.push_str("## What it means\n\n");
    if present {
        // MINOR 7 (round 6): point at the read that established presence.
        // "The Add/Remove Programs entry above" is only true when one was
        // captured; a distro-scan-only presence has no entry above.
        let evidence = outcome.evidence.as_ref();
        let found_as = if evidence.and_then(|e| e.present_product()).is_some() {
            "registered on this machine (the Add/Remove Programs entry above)"
        } else if evidence.map(|e| e.distro_registration)
            == Some(crate::native_uninstall::OtherProductState::Present)
        {
            "on this machine (a CivicCast distro is registered -- the `distro-scan=Present` \
             read above; no Add/Remove Programs entry was captured)"
        } else {
            "on this machine (the `Present` read in the evidence line above)"
        };
        doc.push_str(&format!(
            "Another CivicCast product is {found_as}, and `ActiveRuntime` -- the value the \
             LocalSystem supervisor's dual-runtime guard starts the control plane on -- names \
             no owner. Setup never takes a machine away from a CivicCast product that was \
             there first, so it stopped rather than finish an install whose station could \
             never serve. (If that product were only an inert leftover -- an Add/Remove \
             Programs entry in a loaded user hive, with no CivicCast distro, no autostart \
             entry, and a previous native install's hand-off marker set -- setup would have \
             claimed native with a warning instead.)\n\n"
        ));
        let unmet = evidence
            .map(|evidence| evidence.unmet_inert_conditions())
            .unwrap_or_default();
        if unmet.is_empty() {
            doc.push_str("What did not hold here: see the evidence line above.\n\n");
        } else {
            doc.push_str("What did not hold here:\n\n");
            for condition in &unmet {
                doc.push_str(&format!("- {condition}\n"));
            }
            doc.push('\n');
        }
    } else {
        doc.push_str(
            "The LocalSystem supervisor's dual-runtime guard only starts the control plane when \
             `ActiveRuntime` names a runtime. Setup writes `native` itself when no selector \
             exists and it can establish that the CivicCast WSL product is not on this \
             machine. It could not establish that here (an `unknown` above is a read that \
             failed for a reason other than \"not found\" -- the error kind and OS error code \
             are beside it; an `Unreadable` selector is a value that exists but is not \
             `native`/`wsl`), so it stopped rather than finish an install whose station could \
             never serve.\n\n",
        );
    }
    doc.push_str("## Remedy\n\n");
    if present {
        let (title, uninstall) = outcome
            .evidence
            .as_ref()
            .and_then(|e| e.present_product())
            .map(|(_, p)| (p.title(), p.uninstall_string.clone()))
            .unwrap_or_else(|| ("CivicCast Installer".to_string(), None));
        let or_run = uninstall
            .filter(|s| !s.trim().is_empty())
            .map(|command| format!(" (or run `{command}`)"))
            .unwrap_or_default();
        doc.push_str(&format!(
            "1. Uninstall '{title}' from Settings > Apps{or_run}, or -- if you still use that \
             product and want THIS machine to switch to the native product -- run \
             `civiccast-runtime cutover-to-native`.\n\n\
             2. Run setup again.\n\n"
        ));
        // Round 5: a machine-only entry is refused by the inert rule for as
        // long as its owner is signed out, and step 1 can be a dead end
        // (an abandoned key's uninstall.exe is usually gone). Name the two
        // paths that reach it, with the exact key and the WOW64 view the
        // probe found it in, BEFORE the prohibition below.
        //
        // Round 6 (delta review of round 5, MAJOR 2): this block is reached
        // only when the per-MACHINE probe found the entry (`machine_arp ==
        // Present`, with a machine-scope ARP record behind it) AND the
        // per-user probe did not. Round 5 gated on `user_arp != Present`
        // alone, so a presence established by the distro scan -- no ARP
        // entry anywhere -- was told its cause was a stale HKLM key and
        // handed an elevated `reg delete /f` for a key the evidence block
        // had just reported absent. And `Unknown` is never read as absence:
        // the per-user sentence says the probe could not read the hive.
        // MINOR 6: one command per WOW64 view the entry was found in.
        use crate::native_uninstall::OtherProductState;
        let machine_views: Vec<&'static str> = outcome
            .evidence
            .as_ref()
            .filter(|e| e.machine_arp == OtherProductState::Present)
            .filter(|e| e.user_arp != OtherProductState::Present)
            .map(|e| {
                e.observations
                    .iter()
                    .filter(|o| o.source == "machine-ARP" && o.state == OtherProductState::Present)
                    .filter(|o| o.product.is_some())
                    .map(|o| o.view)
                    .collect()
            })
            .unwrap_or_default();
        if !machine_views.is_empty() {
            let user_hive = match outcome.evidence.as_ref().map(|e| e.user_arp) {
                Some(OtherProductState::Unknown) => {
                    "no signed-in account's hive could be read (user-ARP=unknown)"
                }
                _ => "no signed-in account's hive carries it (user-ARP=absent)",
            };
            doc.push_str(&format!(
                "That entry is registered per-machine (HKLM) and {user_hive}, so setup could \
                 not prove it inert. When step 1 has nothing to run -- the uninstaller behind \
                 the entry is gone, or the product already is -- two paths reach this \
                 case:\n\n"
            ));
            doc.push_str(
                "- Sign in as the account that installed that product and run setup again \
                 from there. With that account's hive loaded, setup reads its distro and \
                 autostart state itself and claims native (with a warning) when the leftover \
                 is inert.\n",
            );
            let commands: Vec<String> = machine_views
                .iter()
                .map(|view| {
                    let reg_view = if *view == "32-bit" { "32" } else { "64" };
                    format!(
                        "reg delete \"HKLM\\{}\" /f /reg:{reg_view}",
                        crate::native_uninstall::WSL_ARP_KEY
                    )
                })
                .collect();
            let views_note = if commands.len() > 1 {
                " (the entry was found in both WOW64 views: run every command below)"
            } else {
                ""
            };
            doc.push_str(&format!(
                "- If the product is genuinely gone (no CivicCast distro in `wsl --list`, \
                 nothing left to uninstall), delete the stale registration from an \
                 ADMINISTRATOR command prompt{views_note}, then run setup again:\n\n\
                 ```\n{}\n```\n\n",
                commands.join("\n")
            ));
        }
        doc.push_str(
            "Do not edit `HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime` by hand for this case: \
             the other product, not the value, is what setup is refusing over.\n\n",
        );
    } else {
        doc.push_str(
            "1. If this machine has no CivicCast WSL product (the usual case), tell the guard \
             so from an ADMINISTRATOR PowerShell:\n\n\
             ```powershell\n\
             New-ItemProperty -Path 'HKLM:\\SOFTWARE\\CivicCast' -Name 'ActiveRuntime' \
             -PropertyType String -Value 'native' -Force\n\
             ```\n\n\
             2. Re-run setup. With the selector already `native`, the ownership step is a \
             no-op and provisioning proceeds.\n\n\
             3. If a `present` observation above names a real CivicCast WSL installation you \
             still use, do NOT set the value; uninstall that product first, or run \
             `civiccast-runtime cutover-to-native` from it, then re-run setup.\n\n",
        );
    }
    doc.push_str(
        "The same observation is in `%ProgramData%\\CivicCast\\install-progress.log` \
         (the `step d4-provision: runtime ownership:` line).\n\n\
         Follow-up (not in this release): a setup wizard page that asks the operator to \
         confirm native ownership when the evidence is merely inconclusive, instead of \
         stopping.\n",
    );
    Some(doc)
}

/// Thin writer (untested directly, matching this module's convention for
/// real filesystem/registry execution).
fn write_ownership_observation_line(
    report_root: &Path,
    outcome: &crate::native_uninstall::SelectorClaimOutcome,
) -> Result<(), String> {
    std::fs::create_dir_all(report_root)
        .map_err(|error| format!("could not create {}: {error}", report_root.display()))?;
    let path = ownership_observation_path(report_root);
    // Deliberately NO trailing newline: the NSIS side reads this with a bare
    // `FileRead` (Tauri's NSIS ships no `${TrimNewLines}`), which returns
    // everything up to the newline INCLUDING it; a newline-free file is the
    // only way the read-back lands in the dialog without a stray line break.
    std::fs::write(&path, ownership_observation_line(outcome))
        .map_err(|error| format!("could not write {}: {error}", path.display()))
}

/// Thin writer (untested directly, same convention). Returns the path it
/// wrote so the caller can name it.
fn write_ownership_recovery_document(
    report_root: &Path,
    outcome: &crate::native_uninstall::SelectorClaimOutcome,
) -> Result<PathBuf, String> {
    std::fs::create_dir_all(report_root)
        .map_err(|error| format!("could not create {}: {error}", report_root.display()))?;
    let path = ownership_recovery_document_path(report_root);
    // Round 6 (MINOR 5): only a refusal has a document; the caller already
    // gated on `refusal_exit_code`, so `None` here is a programming error
    // named as such, not a document with a borrowed exit code.
    let document = ownership_recovery_document(outcome, crate::CIVICCAST_VERSION)
        .ok_or_else(|| format!("{:?} is not a refusal: no recovery document", outcome.action))?;
    std::fs::write(&path, document)
        .map_err(|error| format!("could not write {}: {error}", path.display()))?;
    Ok(path)
}

/// Thin execution wrapper (untested directly, matching
/// `register_native_service`/`write_database_url`'s convention -- the HARD
/// RULE forbids unit-testing real SCM/registry/subprocess execution):
/// shells to the Python provisioning engine, captures its output WITHOUT
/// EVER PRINTING IT (on the success path, captured stdout carries the one
/// handoff line, which contains the generated password inside the
/// DatabaseUrl), parses that line if present, and writes it via the
/// EXISTING [`write_database_url`] as an IN-PROCESS function call -- never a
/// second CLI subprocess with the value on ITS argv. A handoff-less success
/// (exit 0, no marker line) means the Python side took its
/// `NOOP_REUSE_EXISTING` path and never generated a password at all.
#[cfg(target_os = "windows")]
pub fn run_native_provision(
    install_root: &Path,
    owner_run_id: &str,
    existing_database_url: &str,
) -> Result<ProvisionOutcome, ProvisionFailure> {
    let trust = crate::native_packs::embedded_pack_trust().map_err(ProvisionFailure::generic)?;
    // Chain G: claim the dual-runtime selector this install is earning the
    // right to. Nothing else in a native install ever wrote
    // HKLM\SOFTWARE\CivicCast\ActiveRuntime, so every native station came up
    // with selector=absent and the supervisor's guard had no authority basis
    // for a native start (`blocked_probe_unavailable` on any machine where
    // the WSL install-detection probe cannot answer). The decision is
    // conservative and unit-tested
    // (`native_uninstall::decide_install_selector_claim` over
    // `corroborate_wsl_product_state`): it writes in exactly ONE cell --
    // selector absent AND the CivicCast WSL product not on this machine --
    // and never overwrites a "wsl" or unreadable value. `detail` is this
    // installer's OWN sentence (never captured child output, so no
    // credential can ride it) and is printed on EVERY path, including the
    // ones that deliberately write nothing.
    //
    // ORDER (beta.5.1 field defect): this runs BEFORE the provisioning
    // subprocess. It used to run after, so a refused claim (exit 85) left
    // the Python engine's rewrite of postgresql.conf/pg_hba.conf and its
    // completed journal behind on a machine setup then declared unowned.
    // Now a refusal leaves the database configuration exactly as found.
    // The AlreadyNative path is still a no-op.
    let selector_claim = crate::native_uninstall::claim_install_selector();
    eprintln!("{}", selector_claim.detail);
    // Persist the observation for the NSIS d4 step (which reads the one-line
    // file into install-progress.log and the exit-85 dialog) on EVERY path,
    // so a successful corroborated claim is as auditable as a refusal.
    // Best-effort: a report that could not be written must not replace the
    // real outcome with a different failure.
    let report_root = ownership_report_root();
    if let Err(error) = write_ownership_observation_line(&report_root, &selector_claim) {
        eprintln!("Could not record the runtime-ownership observation: {error}");
    }
    if let Some(error) = selector_claim.write_error.clone() {
        // Fails LOUD: an install that finishes without the selector it
        // decided to claim produces a station whose control plane can never
        // start.
        return Err(ProvisionFailure::generic(format!(
            "CivicCast (Native) provisioning could not claim the dual-runtime selector, so the \
             station's control plane would never be authorized to start: {error}"
        )));
    }
    // <installer-path-audit BL-13> `write_error` is `Some` ONLY on
    // ClaimNative + a failed write. The refusals (selector Unreadable, or
    // Absent while another CivicCast product is registered, or Absent with
    // inconclusive evidence) used to print one sentence and return Ok. Exit
    // 0. The chain then registered the service, `sc start` succeeded, the
    // SCM reported RUNNING (the host process runs; the guard blocks the
    // control plane), and setup showed "installation complete" over a
    // station that can never start.
    //
    // Round 3 (corrected field report): the three refusals are distinct
    // actions with distinct exit codes and remedies. A real other-product
    // Present is 87 (uninstall it / cutover -- never a registry edit); an
    // inconclusive read or an unreadable selector stays 85.
    if let Some(exit_code) = refusal_exit_code(selector_claim.action) {
        match write_ownership_recovery_document(&report_root, &selector_claim) {
            Ok(path) => eprintln!(
                "Runtime-ownership recovery document written to {}",
                path.display()
            ),
            Err(error) => {
                eprintln!("Could not write the runtime-ownership recovery document: {error}")
            }
        }
        let remedy = match selector_claim.action {
            crate::native_uninstall::SelectorClaimAction::LeaveOtherProductPresent => {
                "Uninstall that product from Settings > Apps (its UninstallString is in the \
                 observation), or run `civiccast-runtime cutover-to-native` from it, and \
                 re-run setup."
            }
            _ => {
                "An administrator must set HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime to \
                 \"native\" (or resolve the failed read named in the observation) and re-run \
                 setup."
            }
        };
        return Err(ProvisionFailure {
            exit_code,
            message: format!(
                "CivicCast (Native) setup stopped before provisioning: the station's control \
                 plane would be blocked from starting and setup would otherwise have reported \
                 success over a station that can never serve. {} No database configuration \
                 was touched. The observation above is recorded in {} and the remedy in {}. \
                 {remedy}",
                selector_claim.detail,
                ownership_observation_path(&report_root).display(),
                ownership_recovery_document_path(&report_root).display(),
            ),
        });
    }
    let (python_exe, args) = provision_command(
        install_root,
        owner_run_id,
        &trust.key_id,
        &encode_pack_public_key(&trust.public_key),
        crate::CIVICCAST_VERSION,
        crate::CIVICCAST_VERSION,
        existing_database_url,
    );
    let output = std::process::Command::new(&python_exe)
        .args(&args)
        .output()
        .map_err(|error| {
            ProvisionFailure::generic(format!(
                "Could not run CivicCast (Native) provisioning ({}): {error}",
                python_exe.display()
            ))
        })?;
    if !output.status.success() {
        // Deliberately NOT forwarding captured stdout/stderr here (unlike
        // run_and_check's other callers): on the success path they can carry
        // the DatabaseUrl (and therefore the password) behind the handoff
        // marker, and this is a hard boundary rather than a conditional one
        // per the task's credential-sensitivity rule -- never assume a
        // failure path is safe to echo just because today's Python code
        // never prints the password on it. The real failure detail lives in
        // the provisioning journal/recovery document on disk, which is
        // ProgramData-ACL'd, not printed to an installer log a screenshot
        // could capture.
        return Err(ProvisionFailure::generic(format!(
            "CivicCast (Native) provisioning failed (exit {:?}); see the provisioning journal \
             and recovery document under %ProgramData%\\CivicCast\\provision for the failure \
             detail.",
            output.status.code()
        )));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    match parse_provision_handoff(&stdout) {
        Some(database_url) => {
            write_database_url(&database_url).map_err(ProvisionFailure::generic)?;
            Ok(ProvisionOutcome::Provisioned)
        }
        None => Ok(ProvisionOutcome::NoOp),
    }
}

// ---------------------------------------------------------------------------
// Firewall
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FirewallProbeState {
    Present,
    Absent,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FirewallRuleAction {
    Add,
    NoOp,
    Block,
}

/// Classifies captured `netsh advfirewall firewall show rule name=...`
/// output. `netsh.exe`'s exit code is 0 whether or not a matching rule was
/// found, so classification is entirely output-shape based; anything not
/// recognized fails CLOSED to `Unknown` (never guesses `Absent`, which would
/// risk creating a duplicate rule, and never guesses `Present`, which would
/// risk silently skipping a needed rule).
pub fn classify_firewall_probe_output(
    exit_status_success: bool,
    stdout: &str,
) -> FirewallProbeState {
    if stdout.contains("No rules match the specified criteria") {
        return FirewallProbeState::Absent;
    }
    if exit_status_success && stdout.contains("Rule Name:") {
        return FirewallProbeState::Present;
    }
    FirewallProbeState::Unknown
}

/// D5 Repair / this task's idempotency requirement, applied to a resource
/// (a firewall rule) where re-adding without a probe would silently create a
/// duplicate -- so, unlike the service (whose `install` seam already
/// self-heals to `update`), the safe idempotent action on `Present` is a
/// clean NO-OP, not a delete-then-recreate.
pub fn firewall_rule_action(state: FirewallProbeState) -> FirewallRuleAction {
    match state {
        FirewallProbeState::Absent => FirewallRuleAction::Add,
        FirewallProbeState::Present => FirewallRuleAction::NoOp,
        FirewallProbeState::Unknown => FirewallRuleAction::Block,
    }
}

pub fn firewall_show_rule_command() -> Vec<String> {
    vec![
        "advfirewall".to_string(),
        "firewall".to_string(),
        "show".to_string(),
        "rule".to_string(),
        format!("name={FIREWALL_RULE_NAME}"),
    ]
}

pub fn firewall_add_rule_command(install_root: &Path) -> Vec<String> {
    let program = install_root.join("runtime").join("python.exe");
    vec![
        "advfirewall".to_string(),
        "firewall".to_string(),
        "add".to_string(),
        "rule".to_string(),
        format!("name={FIREWALL_RULE_NAME}"),
        "dir=in".to_string(),
        "action=allow".to_string(),
        "protocol=TCP".to_string(),
        format!("localport={CONTROL_PLANE_PORT}"),
        format!("program={}", program.display()),
        "enable=yes".to_string(),
        "profile=any".to_string(),
    ]
}

/// Not invoked by any live code path yet -- the POSTUNINSTALL teardown that
/// will run this is a later work package (`native_uninstall.rs`'s
/// `NATIVE_D4_STATE_INVENTORY` names it as the firewall rule's `removed_by`
/// step). Exempted from the unused-item lint rather than deleted; exercised
/// by `firewall_delete_rule_command_targets_the_exact_rule_name` below.
#[allow(dead_code)]
pub fn firewall_delete_rule_command() -> Vec<String> {
    vec![
        "advfirewall".to_string(),
        "firewall".to_string(),
        "delete".to_string(),
        "rule".to_string(),
        format!("name={FIREWALL_RULE_NAME}"),
    ]
}

/// Thin execution wrapper (untested directly -- same HARD RULE as
/// [`register_native_service`]).
#[cfg(target_os = "windows")]
pub fn register_native_firewall_rule(install_root: &Path) -> Result<(), String> {
    let probe_output = std::process::Command::new("netsh.exe")
        .args(firewall_show_rule_command())
        .output()
        .map_err(|error| format!("Could not run netsh.exe to probe the firewall rule: {error}"))?;
    let state = classify_firewall_probe_output(
        probe_output.status.success(),
        &String::from_utf8_lossy(&probe_output.stdout),
    );
    match firewall_rule_action(state) {
        FirewallRuleAction::NoOp => Ok(()),
        FirewallRuleAction::Add => run_and_check(
            Path::new("netsh.exe"),
            &firewall_add_rule_command(install_root),
            "CivicCast (Native) firewall rule registration",
        ),
        FirewallRuleAction::Block => Err(format!(
            "Could not determine whether the {FIREWALL_RULE_NAME} firewall rule already \
             exists (ambiguous netsh output); refusing to risk creating a duplicate rule."
        )),
    }
}

/// POSTUNINSTALL teardown counterpart to [`register_native_firewall_rule`]:
/// probe first (the same fail-closed [`classify_firewall_probe_output`]
/// classifier), then delete only if `Present`, and no-op if `Absent` --
/// idempotent, and never guesses on an `Unknown` probe (fails loud rather
/// than silently leaving a stale rule behind or erroring on a rule that was
/// never there).
#[cfg(target_os = "windows")]
pub fn delete_native_firewall_rule() -> Result<(), String> {
    let probe_output = std::process::Command::new("netsh.exe")
        .args(firewall_show_rule_command())
        .output()
        .map_err(|error| format!("Could not run netsh.exe to probe the firewall rule: {error}"))?;
    let state = classify_firewall_probe_output(
        probe_output.status.success(),
        &String::from_utf8_lossy(&probe_output.stdout),
    );
    match state {
        FirewallProbeState::Absent => Ok(()),
        FirewallProbeState::Present => run_and_check(
            Path::new("netsh.exe"),
            &firewall_delete_rule_command(),
            "CivicCast (Native) firewall rule removal",
        ),
        FirewallProbeState::Unknown => Err(format!(
            "Could not determine whether the {FIREWALL_RULE_NAME} firewall rule exists \
             (ambiguous netsh output); refusing to guess whether deletion is needed."
        )),
    }
}

// ---------------------------------------------------------------------------
// D4 POSTUNINSTALL teardown orchestration: the single entry point the
// `--civiccast-teardown-native-state` CLI subcommand (main.rs) drives. Runs
// every step in order, continues past an individual step's failure (each
// step is independently idempotent and unrelated resources should not be
// left behind just because one step had a real problem), and reports a
// per-step outcome the CLI prints as one line each -- see
// `native_uninstall.rs`'s `NATIVE_D4_STATE_INVENTORY` for the bidirectional
// install/removal inventory this closes out.
// ---------------------------------------------------------------------------

/// One step's result from [`teardown_native_state`]: a fixed label (for the
/// CLI's one-line-per-step output), a human-readable detail (success message
/// or error text), and whether it is a REAL failure (as opposed to a
/// no-op/idempotent success).
#[derive(Debug, Clone)]
pub struct TeardownStepOutcome {
    pub label: &'static str,
    pub detail: String,
    pub failed: bool,
}

/// The ordered step labels [`teardown_native_state`] emits, and the ONE place
/// the SET of teardown steps is declared. Each step below indexes this table
/// rather than repeating a literal, so a step cannot be dropped from the
/// teardown without also dropping it here -- which the tests assert on.
pub const TEARDOWN_STEP_LABELS: &[&str] = &[
    "stop service",
    "remove service",
    "delete firewall rule",
    "clear credentials",
    "clear install markers",
    "clear empty Native key",
    "clear released maintenance interlock",
    "clear empty CivicCast key",
];

/// Run, in order, idempotently, continuing past individual absences: stop
/// the service -> remove the service -> delete the firewall rule -> clear the
/// credential-bearing registry values ([`CREDENTIAL_VALUE_NAMES`]) -> clear
/// the install-marker values -> remove the now-empty `CivicCast\Native` key
/// (N-20) -> clear a released Maintenance interlock blob (N-20) -> remove the
/// now-empty parent `CivicCast` key (2d1123a0 polish re-walk). Does NOT
/// touch the `ActiveRuntime` selector (owned end-to-end by
/// `native_uninstall.rs`'s existing uninstall protocol) and does NOT touch
/// the filesystem (`$INSTDIR\runtime`/`$INSTDIR\packs`/`$INSTDIR` removal is a
/// separate NSIS-side `RMDir /r` step, run only AFTER this teardown and the
/// existing selector bookkeeping both complete -- see
/// `nsis-hooks-bootstrap.nsh`'s `NSIS_HOOK_POSTUNINSTALL`).
#[cfg(target_os = "windows")]
pub fn teardown_native_state(install_root: &Path) -> Vec<TeardownStepOutcome> {
    let mut steps = Vec::new();

    steps.push(match stop_native_service() {
        Ok(()) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[0],
            detail: format!("{SERVICE_NAME} is stopped (or was already stopped/not installed)."),
            failed: false,
        },
        Err(error) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[0],
            detail: error,
            failed: true,
        },
    });

    steps.push(match run_service_removal(install_root) {
        Ok(()) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[1],
            detail: format!("{SERVICE_NAME} is unregistered (or was already absent)."),
            failed: false,
        },
        Err(error) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[1],
            detail: error,
            failed: true,
        },
    });

    steps.push(match delete_native_firewall_rule() {
        Ok(()) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[2],
            detail: format!("{FIREWALL_RULE_NAME} is absent (deleted or was already absent)."),
            failed: false,
        },
        Err(error) => TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[2],
            detail: error,
            failed: true,
        },
    });

    // SECURITY FIX (F-02, 2026-08-01 sandbox newcomer re-walk). See
    // `delete_native_credential_values`'s doc comment for what this reverses,
    // why, and the disclosed reinstall consequence. Reported per value
    // ("deleted" vs "was already absent") rather than as one opaque "done", so
    // the uninstall log can be read as PROOF the secret is gone rather than as
    // a claim that it is.
    //
    // GATED on the service having been confirmed stopped -- see
    // [`may_clear_registry_state`] for why this must not run on the abort path.
    steps.push(if may_clear_registry_state(&steps) {
        match delete_native_credential_values() {
            Ok(results) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[3],
                detail: format!("{DATABASE_URL_KEY}: {}", describe_deletions(&results)),
                failed: false,
            },
            Err(error) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[3],
                detail: error,
                failed: true,
            },
        }
    } else {
        TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[3],
            detail: REGISTRY_CLEAR_SKIPPED_DETAIL.to_string(),
            failed: false,
        }
    });

    // F-01, uninstaller half (2026-08-01 sandbox newcomer re-walk). See
    // `delete_native_install_marker_values`' doc comment. Same gate as the
    // credential clear above, for the same reason.
    steps.push(if may_clear_registry_state(&steps) {
        match delete_native_install_marker_values() {
            Ok(results) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[4],
                detail: format!("{DATABASE_URL_KEY}: {}", describe_deletions(&results)),
                failed: false,
            },
            Err(error) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[4],
                detail: error,
                failed: true,
            },
        }
    } else {
        TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[4],
            detail: REGISTRY_CLEAR_SKIPPED_DETAIL.to_string(),
            failed: false,
        }
    });

    // N-20 (carried, rewalk-de3aaf6f): the `CivicCast\Native` key survived
    // uninstall, empty, because nothing ever removed the KEY itself -- only
    // clearing every value ever written under it (both steps immediately
    // above). Runs AFTER both value-clearing steps so the key is only ever
    // considered once nothing this teardown knows how to clear is left in
    // it; [`delete_native_key_if_empty`] independently re-verifies emptiness
    // before deleting, so this ordering is a belt, not the only buckle. Same
    // gate as the two steps above, for the same reason (must not run on the
    // abort path, where the station is still installed).
    steps.push(if may_clear_registry_state(&steps) {
        match delete_native_key_if_empty() {
            Ok(NativeKeyOutcome::Deleted) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[5],
                detail: format!("{DATABASE_URL_KEY}: deleted (was empty)."),
                failed: false,
            },
            Ok(NativeKeyOutcome::AlreadyAbsent) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[5],
                detail: format!("{DATABASE_URL_KEY}: was already absent."),
                failed: false,
            },
            Ok(NativeKeyOutcome::NotEmptySkipped) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[5],
                detail: format!(
                    "{DATABASE_URL_KEY}: left in place -- it still has a subkey or a value \
                     this teardown does not own."
                ),
                failed: false,
            },
            Err(error) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[5],
                detail: error,
                failed: true,
            },
        }
    } else {
        TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[5],
            detail: REGISTRY_CLEAR_SKIPPED_DETAIL.to_string(),
            failed: false,
        }
    });

    // N-20 (carried, rewalk-de3aaf6f): a stale, already-released D7a
    // Maintenance interlock blob under `CivicCast` survived uninstall.
    // Ownership-verified: only a blob whose own `state` reads "released" is
    // removed; "held" or unreadable is left alone. Same gate as every other
    // registry-clearing step above.
    steps.push(if may_clear_registry_state(&steps) {
        match delete_released_maintenance_blob() {
            Ok(MaintenanceBlobOutcome::Deleted) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[6],
                detail: format!("{CIVICCAST_ROOT_KEY}: {MAINTENANCE_VALUE_NAME} deleted (was released)."),
                failed: false,
            },
            Ok(MaintenanceBlobOutcome::AlreadyAbsent) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[6],
                detail: format!("{CIVICCAST_ROOT_KEY}: {MAINTENANCE_VALUE_NAME} was already absent."),
                failed: false,
            },
            Ok(MaintenanceBlobOutcome::HeldOrUnknownLeftInPlace) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[6],
                detail: format!(
                    "{CIVICCAST_ROOT_KEY}: {MAINTENANCE_VALUE_NAME} left in place -- its state \
                     is not \"released\"."
                ),
                failed: false,
            },
            Ok(MaintenanceBlobOutcome::UnreadableLeftInPlace) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[6],
                detail: format!(
                    "{CIVICCAST_ROOT_KEY}: {MAINTENANCE_VALUE_NAME} left in place -- it could \
                     not be read as a valid interlock record."
                ),
                failed: false,
            },
            Err(error) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[6],
                detail: error,
                failed: true,
            },
        }
    } else {
        TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[6],
            detail: REGISTRY_CLEAR_SKIPPED_DETAIL.to_string(),
            failed: false,
        }
    });

    // NEW Minor (2d1123a0 polish re-walk, evidence
    // P01-postuninstall-2d1123a0.txt): the PARENT `SOFTWARE\CivicCast` key
    // itself survived uninstall, present and empty, once the Native subkey
    // (N-20, immediately above two steps ago) and the Maintenance blob
    // (N-20, immediately above) were both cleared. Runs LAST, after every
    // other step that can remove something living directly under
    // `CivicCast`, so the parent is only ever considered once nothing this
    // teardown knows how to clear is left in or under it;
    // [`delete_civiccast_root_key_if_empty`] independently re-verifies
    // emptiness (a fresh read, right before the delete) so this ordering is
    // a belt, not the only buckle -- and it is exactly what protects any
    // OTHER CivicCast product/component that might legitimately keep state
    // directly under this same shared parent key. Same gate as every other
    // registry-clearing step above (must not run on the abort path, where
    // the station is still installed).
    steps.push(if may_clear_registry_state(&steps) {
        match delete_civiccast_root_key_if_empty() {
            Ok(NativeKeyOutcome::Deleted) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[7],
                detail: format!("{CIVICCAST_ROOT_KEY}: deleted (was empty)."),
                failed: false,
            },
            Ok(NativeKeyOutcome::AlreadyAbsent) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[7],
                detail: format!("{CIVICCAST_ROOT_KEY}: was already absent."),
                failed: false,
            },
            Ok(NativeKeyOutcome::NotEmptySkipped) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[7],
                detail: format!(
                    "{CIVICCAST_ROOT_KEY}: left in place -- it still has a subkey or a value \
                     this teardown does not own."
                ),
                failed: false,
            },
            Err(error) => TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[7],
                detail: error,
                failed: true,
            },
        }
    } else {
        TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[7],
            detail: REGISTRY_CLEAR_SKIPPED_DETAIL.to_string(),
            failed: false,
        }
    });

    steps
}

/// Render a [`delete_values_from_key`] result as one honest per-value phrase.
/// "deleted" and "was already absent" are DIFFERENT facts and the uninstall
/// log says which one happened for each value.
fn describe_deletions(results: &[(&'static str, bool)]) -> String {
    results
        .iter()
        .map(|(name, deleted)| {
            format!(
                "{name} {}",
                if *deleted {
                    "deleted"
                } else {
                    "was already absent"
                }
            )
        })
        .collect::<Vec<_>>()
        .join(", ")
}

/// The detail a skipped registry-clearing step reports. Not a failure (the
/// run's exit code is already 82 from the stop-service step that caused it);
/// a factual statement of what was deliberately not done and why.
pub const REGISTRY_CLEAR_SKIPPED_DETAIL: &str =
    "SKIPPED: the supervisor service could not be confirmed stopped, so this uninstall will \
     abort with the machine left fully intact -- clearing machine-scoped registry state here \
     would leave a still-installed, still-running station without the values it needs.";

/// PURE. May this teardown run clear machine-scoped registry state?
///
/// Only when the service was confirmed STOPPED. A teardown whose stop-service
/// step failed returns [`TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE`] (82),
/// and `nsis-hooks-bootstrap.nsh`'s `NSIS_HOOK_PREUNINSTALL` then ABORTS the
/// whole uninstall before anything is deleted -- its own stated guarantee is
/// that "aborting from PREUNINSTALL leaves the machine FULLY INTACT: exe,
/// uninstaller, ARP entry, service, and trees all still present, so the
/// operator can stop the service and run Uninstall again".
///
/// Registry clearing would silently break that guarantee: the station would
/// still be installed and still running, but without its `DatabaseUrl`
/// credential (so the next service start could not reach its own database) and
/// without its `InstalledVersion` marker (so the next install would misroute
/// exactly the way F-01 documents). The machine would be left in a WORSE state
/// by an uninstall that reported it had changed nothing.
///
/// Deliberately keyed on the stop-service step alone, matching
/// [`service_stop_failed`]: a failed firewall-rule removal does not make
/// registry clearing unsafe.
pub fn may_clear_registry_state(steps_so_far: &[TeardownStepOutcome]) -> bool {
    !service_stop_failed(steps_so_far)
}

/// `true` only when every step in a [`teardown_native_state`] run either
/// succeeded or was a legitimate no-op -- never when any step is a real
/// failure. The `--civiccast-teardown-native-state` CLI (main.rs) maps this
/// to its exit code.
pub fn teardown_all_succeeded(steps: &[TeardownStepOutcome]) -> bool {
    steps.iter().all(|step| !step.failed)
}

/// `true` iff the **"stop service"** step specifically failed -- i.e. the
/// CivicCastSupervisor service could not be confirmed STOPPED (not
/// installed / already stopped / timed out / a raw `sc.exe` failure all fall
/// through [`stop_native_service`]'s `Err` branch and land here as this one
/// labeled step). Deliberately narrower than [`teardown_all_succeeded`]: a
/// "remove service" or "delete firewall rule" failure alone does NOT trip
/// this, because those failures do not make it unsafe to delete
/// `$INSTDIR`'s trees -- only an unconfirmed service stop does (its
/// `pythonservice.exe` and its long-lived `postgres.exe`
/// child run FROM those trees). See [`teardown_exit_code`], which is what
/// actually maps this to a distinct process exit code for the NSIS hook to
/// branch on.
pub fn service_stop_failed(steps: &[TeardownStepOutcome]) -> bool {
    steps
        .iter()
        .any(|step| step.label == "stop service" && step.failed)
}

/// CRITICAL fix (2026-07-30 adversarial review): the exit code
/// `--civiccast-teardown-native-state` (main.rs) reports for this run's
/// steps. Exit 0 only when every step succeeded or was a legitimate no-op
/// ([`teardown_all_succeeded`]). Any OTHER failure previously collapsed to
/// the single generic code 80, which `NSIS_HOOK_POSTUNINSTALL` could not use
/// to distinguish "the service might still be running" (unsafe to delete the
/// program tree out from under it) from "a firewall rule or registry value
/// could not be removed" (safe to delete the tree; refusing over that would
/// strand gigabytes of data for no safety reason). `TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE`
/// (82) is a code of its own, chosen from a gap in the CLI's existing 64-81
/// band (checked against every `Some(N)` / documented exit code across this
/// binary's CLI subcommands before picking it) and clear of the installer's
/// own NSIS-side 110-119 band (`nsis-hooks-bootstrap.nsh`). The NSIS macro
/// gates its recursive `RMDir /r` block on exactly this code -- see that
/// file's `NSIS_HOOK_POSTUNINSTALL`.
pub const TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE: i32 = 82;

pub fn teardown_exit_code(steps: &[TeardownStepOutcome]) -> i32 {
    if teardown_all_succeeded(steps) {
        0
    } else if service_stop_failed(steps) {
        TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE
    } else {
        80
    }
}

/// Control-plane readiness gate (installer-path audit BL-11).
///
/// The classifier is pure, so these are real behavioural tests of the gate
/// the elevated install chain never had -- not a lint over its source text.
/// Every body here is one the OLD chain accepted, because the OLD chain never
/// looked at a body at all: `sc.exe query` reporting RUNNING was the whole
/// contract.
#[cfg(test)]
mod control_plane_readiness_tests {
    use super::*;

    fn response(body: &str) -> String {
        format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{body}")
    }

    const SERVING_BODY: &str = r#"{"status":"healthy","version":"1.0.0-beta.2","schema":"current","schema_db_revision":"0087_retention_terms","schema_expected_head":"0087_retention_terms"}"#;

    #[test]
    fn a_healthy_current_body_is_serving() {
        assert_eq!(
            classify_control_plane_readiness(&response(SERVING_BODY)),
            ControlPlaneReadiness::Serving
        );
    }

    #[test]
    fn gate_a_run_33681670855s_own_body_is_not_serving() {
        // Verbatim shape from that run's summary.json.runtime_checks.health
        // snippet: a 200, a non-empty body, and a station serving 500s.
        let body = r#"{"status":"degraded","schema":"behind","schema_db_revision":"0082_x","schema_expected_head":"0087_y"}"#;
        let outcome = classify_control_plane_readiness(&response(body));
        assert_eq!(
            outcome,
            ControlPlaneReadiness::SchemaNotCurrent {
                schema: "behind".to_string(),
                db_revision: "0082_x".to_string(),
                expected_head: "0087_y".to_string(),
            }
        );
        let message = control_plane_readiness_failure_message(&outcome);
        assert!(message.contains("0082_x"), "{message}");
        assert!(message.contains("0087_y"), "{message}");
    }

    #[test]
    fn a_current_label_over_mismatched_revisions_is_still_not_serving() {
        // The label and the revisions come from the same process, so the
        // label alone proves nothing; requiring BOTH means a control plane
        // whose self-report is internally inconsistent cannot pass either.
        let body = r#"{"status":"healthy","schema":"current","schema_db_revision":"0082_x","schema_expected_head":"0087_y"}"#;
        assert!(matches!(
            classify_control_plane_readiness(&response(body)),
            ControlPlaneReadiness::SchemaNotCurrent { .. }
        ));
    }

    #[test]
    fn a_degraded_status_with_a_current_schema_is_not_serving() {
        let body = r#"{"status":"degraded","schema":"current","schema_db_revision":"0087_a","schema_expected_head":"0087_a"}"#;
        assert_eq!(
            classify_control_plane_readiness(&response(body)),
            ControlPlaneReadiness::NotHealthy {
                status: "degraded".to_string()
            }
        );
    }

    #[test]
    fn a_non_200_is_unreadable_not_serving() {
        let raw = "HTTP/1.1 503 Service Unavailable\r\n\r\n{}";
        assert!(matches!(
            classify_control_plane_readiness(raw),
            ControlPlaneReadiness::Unreadable { .. }
        ));
    }

    #[test]
    fn a_body_missing_the_revision_fields_fails_closed() {
        // An older control plane than this installer ships. PR #143 made both
        // fields unconditional; their absence is itself a state the installer
        // must not call success.
        let body = r#"{"status":"healthy","schema":"current"}"#;
        assert!(matches!(
            classify_control_plane_readiness(&response(body)),
            ControlPlaneReadiness::Unreadable { .. }
        ));
    }

    #[test]
    fn whitespace_and_field_order_do_not_change_the_verdict() {
        let body = "{\n  \"schema_expected_head\": \"0087_a\",\n  \"schema\": \"current\",\n  \"schema_db_revision\": \"0087_a\",\n  \"status\": \"healthy\"\n}";
        assert_eq!(
            classify_control_plane_readiness(&response(body)),
            ControlPlaneReadiness::Serving
        );
    }

    #[test]
    fn the_three_service_exit_codes_are_distinct_and_outside_the_nsis_band() {
        // Installer-path audit MA-28: 74 already meant three different things
        // when the comment beside SERVICE_START_FAILED_EXIT_CODE asserted
        // "74 is free". These three must never collide with each other, with
        // 74/75, or with the NSIS 110-127 band.
        let codes = [
            SERVICE_START_FAILED_EXIT_CODE,
            SERVICE_NOT_SERVING_EXIT_CODE,
            SELECTOR_UNPROVABLE_EXIT_CODE,
            OTHER_PRODUCT_PRESENT_EXIT_CODE,
        ];
        assert_eq!(OTHER_PRODUCT_PRESENT_EXIT_CODE, 87);
        for (index, code) in codes.iter().enumerate() {
            assert!(
                (83..=87).contains(code),
                "code {code} is outside the claimed 83-87 range"
            );
            assert_ne!(*code, ProvisionFailure::GENERIC_EXIT_CODE);
            assert_ne!(*code, UNKNOWN_CIVICCAST_FLAG_EXIT_CODE);
            assert_ne!(*code, crate::native_uninstall::TRANSFER_ACK_REQUIRED_EXIT_CODE);
            assert!(!(110..=127).contains(code), "{code} collides with the NSIS band");
            for other in codes.iter().skip(index + 1) {
                assert_ne!(code, other, "two service failures share exit code {code}");
            }
        }
    }

    #[test]
    fn a_provision_failure_carries_its_own_exit_code() {
        let generic = ProvisionFailure::generic("boom".to_string());
        assert_eq!(generic.exit_code, ProvisionFailure::GENERIC_EXIT_CODE);
        assert_eq!(generic.to_string(), "boom");
        let unprovable = ProvisionFailure {
            exit_code: SELECTOR_UNPROVABLE_EXIT_CODE,
            message: "no ownership".to_string(),
        };
        assert_ne!(unprovable.exit_code, generic.exit_code);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::SigningKey;

    #[test]
    fn a_failed_child_leads_with_its_final_error_line_before_the_full_output() {
        // RC4 Gate A run 41795787409: NSIS keeps only the first 1024 characters
        // of this message, so a Python traceback hid its own final exception.
        let traceback = format!(
            "Traceback (most recent call last):\n{}RuntimeError: Could not open C:\\x to grant restricted service access (Windows error 32)\n\n",
            "  File \"frame.py\", line 1, in <module>\n".repeat(40)
        );
        let message = child_failure_message("Service tool", Some(1), "", &traceback);
        assert!(message.starts_with(
            "Service tool failed (exit Some(1)): RuntimeError: Could not open C:\\x to grant restricted service access (Windows error 32)"
        ));
        assert!(message.len() < 1024 || message[..1024].contains("Windows error 32"));
        assert!(message.contains("Traceback (most recent call last):"));

        let quiet = child_failure_message("Service tool", Some(2), "", "  \n");
        assert_eq!(quiet, "Service tool failed (exit Some(2)): (no output)");
        let stdout_only = child_failure_message("Service tool", None, "last words\n", "");
        assert_eq!(
            stdout_only,
            "Service tool failed (exit None): last words | full output: last words\n"
        );
    }

    #[test]
    fn provision_command_invokes_the_embedded_interpreter_with_the_provisioning_module() {
        let install_root = Path::new(r"C:\Program Files\CivicCast Native");
        let (program, args) = provision_command(
            install_root,
            "run-1",
            "key-1",
            "AAAA",
            "1.0.0-rc15",
            "1.0.0-rc15",
            "",
        );

        assert_eq!(
            program,
            install_root.join("runtime").join("python.exe"),
            "must invoke the EMBEDDED interpreter, never a host Python"
        );
        assert_eq!(args[0], "-m");
        assert_eq!(args[1], "civiccast.native.provision");
        assert!(args.contains(&"--install-root".to_string()));
        assert!(args.contains(&install_root.display().to_string()));
        assert!(args.contains(&"--owner-run-id".to_string()));
        assert!(args.contains(&"run-1".to_string()));
        assert!(args.contains(&"--pack-signing-key-id".to_string()));
        assert!(args.contains(&"key-1".to_string()));
        assert!(args.contains(&"--pack-public-key-base64".to_string()));
        assert!(args.contains(&"AAAA".to_string()));
        assert!(args.contains(&"--pack-product-version".to_string()));
        assert!(args.contains(&"--pack-compatible-core".to_string()));
    }

    #[test]
    fn provision_command_omits_the_existing_database_url_flag_when_empty() {
        let (_, args) = provision_command(
            Path::new(r"C:\INSTDIR"),
            "run-1",
            "key-1",
            "AAAA",
            "1.0.0",
            "1.0.0",
            "",
        );
        assert!(
            !args.contains(&"--existing-database-url".to_string()),
            "an empty existing DatabaseUrl must not be passed at all, matching the Python CLI's \
             default=\"\" reading of \"no registry value yet\""
        );
    }

    #[test]
    fn provision_command_includes_the_existing_database_url_flag_when_present() {
        let (_, args) = provision_command(
            Path::new(r"C:\INSTDIR"),
            "run-1",
            "key-1",
            "AAAA",
            "1.0.0",
            "1.0.0",
            "postgresql://u:p@127.0.0.1:5432/civiccast",
        );
        let idx = args
            .iter()
            .position(|arg| arg == "--existing-database-url")
            .expect("--existing-database-url must be present");
        assert_eq!(args[idx + 1], "postgresql://u:p@127.0.0.1:5432/civiccast");
    }

    #[test]
    fn parse_provision_handoff_finds_the_marker_among_noise() {
        let captured = "some diagnostic line\nCIVICCAST_DATABASE_URL=postgresql://u:p@h:5432/db\ntrailing noise\n";
        assert_eq!(
            parse_provision_handoff(captured),
            Some("postgresql://u:p@h:5432/db".to_string())
        );
    }

    #[test]
    fn parse_provision_handoff_returns_none_when_absent() {
        assert_eq!(parse_provision_handoff("nothing here\nor here\n"), None);
        assert_eq!(parse_provision_handoff(""), None);
    }

    #[test]
    fn encode_pack_public_key_round_trips_the_raw_32_bytes() {
        let signing_key = SigningKey::from_bytes(&[11_u8; 32]);
        let public_key = signing_key.verifying_key();
        let encoded = encode_pack_public_key(&public_key);
        let decoded = BASE64.decode(&encoded).expect("must be valid base64");
        assert_eq!(decoded, public_key.to_bytes().to_vec());
    }

    #[test]
    fn service_registration_command_invokes_the_pywin32_seam_with_auto_startup() {
        let install_root = Path::new(r"C:\Program Files\CivicCast Native");
        let (program, args) = service_registration_command(install_root);

        assert_eq!(
            program,
            install_root.join("runtime").join("python.exe"),
            "must invoke the EMBEDDED interpreter, never a host Python"
        );
        assert_eq!(
            args,
            vec![
                "-m".to_string(),
                "civiccast.native.supervisor.service_host".to_string(),
                "--startup".to_string(),
                "auto".to_string(),
                "install".to_string(),
            ],
            "pywin32 HandleCommandLine requires options BEFORE the command \
             (usage: 'service_host.py [options] install'); the reversed \
             order fails live with usage + exit 1 (Sandbox runs 3-5)"
        );
    }

    #[test]
    fn service_registration_never_passes_username_or_password_so_pywin32_defaults_to_localsystem() {
        let (_, args) = service_registration_command(Path::new(r"C:\INSTDIR"));
        assert!(
            !args
                .iter()
                .any(|arg| arg == "--username" || arg == "--password"),
            "passing --username/--password would move the account off LocalSystem (D4)"
        );
    }

    fn scratch_dir(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "civiccast-native-service-registration-{name}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("create scratch dir");
        root
    }

    #[test]
    fn restore_service_host_site_packages_member_copies_the_moved_exe_back() {
        // Simulates exactly what pywin32's InstallService does to the
        // payload tree: it moves pythonservice.exe out of
        // runtime\Lib\site-packages\win32\ into the payload root
        // (runtime\), leaving the site-packages copy missing. Task #50 (live
        // Sandbox runs 12+13, row 3): D5 Repair calls register_native_service
        // in-process (native_repair.rs::reregister_service_and_firewall) with
        // NO NSIS hook in that path, so nothing restored the site-packages
        // member after a repair's re-registration -- every subsequent verify
        // reported 76 forever. This proves the extracted restore function
        // itself, independent of any NSIS hook.
        let install_root = scratch_dir("restore-copies-back");
        let runtime_dir = install_root.join("runtime");
        let win32_dir = runtime_dir.join("Lib").join("site-packages").join("win32");
        std::fs::create_dir_all(&win32_dir).expect("create site-packages/win32 dir");

        // The payload-root copy pywin32's move leaves behind -- give it
        // distinctive, non-empty content so a byte-identical assertion is
        // meaningful (not just "a file exists").
        let moved_exe_bytes = b"fake pythonservice.exe payload-root bytes after pywin32 move";
        std::fs::write(runtime_dir.join("pythonservice.exe"), moved_exe_bytes)
            .expect("write fake moved exe");

        // The site-packages member pywin32's move emptied out.
        let site_packages_dest = win32_dir.join("pythonservice.exe");
        assert!(
            !site_packages_dest.exists(),
            "precondition: site-packages member must start absent, matching the post-move state"
        );

        restore_service_host_site_packages_member(&install_root)
            .expect("restore must succeed when the payload-root source exists");

        let restored_bytes =
            std::fs::read(&site_packages_dest).expect("restored site-packages member must exist");
        assert_eq!(
            restored_bytes, moved_exe_bytes,
            "restored site-packages member must be byte-identical to the payload-root source"
        );

        let _ = std::fs::remove_dir_all(&install_root);
    }

    #[test]
    fn restore_service_host_site_packages_member_is_a_disclosed_noop_when_source_missing() {
        // Matches nsis-hooks-bootstrap.nsh's own "WARNING ... NOT restored"
        // breadcrumb-not-abort behavior: a missing payload-root source (e.g.
        // registration never actually ran the pywin32 move) must not fail
        // the overall registration -- it is a disclosed no-op, and the next
        // D5 verify will report a repair, same as the NSIS hook path.
        let install_root = scratch_dir("restore-missing-source-noop");
        std::fs::create_dir_all(install_root.join("runtime")).expect("create runtime dir");

        let result = restore_service_host_site_packages_member(&install_root);

        assert!(
            result.is_ok(),
            "a missing payload-root source must not fail registration: {result:?}"
        );
        assert!(
            !install_root
                .join("runtime")
                .join("Lib")
                .join("site-packages")
                .join("win32")
                .join("pythonservice.exe")
                .exists(),
            "no destination file should be created when the source is missing"
        );

        let _ = std::fs::remove_dir_all(&install_root);
    }

    #[test]
    fn restore_service_host_site_packages_member_overwrites_a_stale_existing_member() {
        // D5 Repair runs register_native_service against a tree that may
        // already have a STALE site-packages member (e.g. left over from a
        // previous registration cycle) -- the restore must overwrite it,
        // not skip because a file is already present at the destination.
        let install_root = scratch_dir("restore-overwrites-stale");
        let runtime_dir = install_root.join("runtime");
        let win32_dir = runtime_dir.join("Lib").join("site-packages").join("win32");
        std::fs::create_dir_all(&win32_dir).expect("create site-packages/win32 dir");

        std::fs::write(runtime_dir.join("pythonservice.exe"), b"fresh bytes")
            .expect("write fake moved exe");
        std::fs::write(win32_dir.join("pythonservice.exe"), b"stale bytes")
            .expect("write stale destination");

        restore_service_host_site_packages_member(&install_root).expect("restore must succeed");

        let restored_bytes = std::fs::read(win32_dir.join("pythonservice.exe"))
            .expect("restored site-packages member must exist");
        assert_eq!(restored_bytes, b"fresh bytes");

        let _ = std::fs::remove_dir_all(&install_root);
    }

    #[test]
    fn service_stop_command_matches_sc_exe_documented_grammar() {
        // sc.exe's documented usage is `sc <command> <servicename> [options]`
        // (Windows `sc.exe /?` and Microsoft Learn's "sc stop" reference) --
        // `stop` takes no options, so the argv is exactly command + name.
        assert_eq!(
            service_stop_command(),
            vec!["stop".to_string(), SERVICE_NAME.to_string()]
        );
    }

    #[test]
    fn service_removal_command_puts_no_options_after_the_command() {
        // pywin32's win32serviceutil.HandleCommandLine contract (see
        // service_registration_command's doc comment above): OPTIONS BEFORE
        // THE COMMAND, always -- reversing this order already cost this
        // project multiple live Sandbox test-run failures for `install`. This
        // asserts `remove` is the LAST argument, so nothing could ever be
        // appended after it by accident.
        let install_root = Path::new(r"C:\Program Files\CivicCast Native");
        let (program, args) = service_removal_command(install_root);
        assert_eq!(program, install_root.join("runtime").join("python.exe"));
        assert_eq!(
            args,
            vec![
                "-m".to_string(),
                "civiccast.native.supervisor.service_host".to_string(),
                "remove".to_string(),
            ]
        );
        assert_eq!(
            args.last().map(String::as_str),
            Some("remove"),
            "remove must be the LAST argv element -- any future option must be \
             inserted BEFORE it, never after"
        );
    }

    #[test]
    fn classify_service_stop_exit_code_maps_1060_and_1062_to_already_done() {
        // Win32 ERROR_SERVICE_DOES_NOT_EXIST (1060) and ERROR_SERVICE_NOT_ACTIVE
        // (1062) are sc.exe's exact process exit codes for "no such service"
        // and "service exists but is not running" -- both are idempotent
        // successes for a stop, not failures. Pure classifier, no live
        // sc.exe call.
        assert_eq!(
            classify_service_stop_exit_code(Some(1060)),
            ServiceStopExitOutcome::AlreadyDone
        );
        assert_eq!(
            classify_service_stop_exit_code(Some(1062)),
            ServiceStopExitOutcome::AlreadyDone
        );
    }

    #[test]
    fn classify_service_stop_exit_code_distinguishes_issued_from_failed() {
        assert_eq!(
            classify_service_stop_exit_code(Some(0)),
            ServiceStopExitOutcome::StopIssued
        );
        for other in [Some(1), Some(5), Some(1077), None] {
            assert_eq!(
                classify_service_stop_exit_code(other),
                ServiceStopExitOutcome::Failed,
                "exit code {other:?} must not be silently treated as success"
            );
        }
    }

    #[test]
    fn classify_service_stop_exit_code_treats_1061_as_a_stop_already_in_progress() {
        // Win32 ERROR_SERVICE_CANNOT_ACCEPT_CTRL (1061) is sc.exe stop's exit
        // code when a stop is ALREADY in progress (SERVICE_STOP_PENDING). That
        // is a WAIT signal, not a failure: it must route to StopIssued, whose
        // caller contract is "poll wait_for_service_stopped() until the service
        // reports a real STOPPED state (or the SERVICE_STOP_POLL_TIMEOUT_SECS
        // deadline elapses)".
        //
        // Gauntlet run 17 (2026-07-31) is what this test pins: the supervisor
        // wedged in STOP_PENDING, every sc.exe stop came back 1061, this
        // classifier called it Failed, and stop_native_service returned an
        // error WITHOUT EVER POLLING -- surfacing as repair exit 79, uninstall
        // teardown exit 82 with the whole tree left behind, and install
        // refusal 120 forever after.
        assert_eq!(
            classify_service_stop_exit_code(Some(1061)),
            ServiceStopExitOutcome::StopIssued,
            "1061 (a stop already in progress) must proceed to the STOPPED poll, not fail instantly"
        );
    }

    #[test]
    fn classify_service_stop_exit_code_does_not_call_1061_already_done() {
        // FALSIFICATION of the tempting over-fix. "A stop is in progress" is
        // NOT "the service is stopped": mapping 1061 to AlreadyDone would skip
        // wait_for_service_stopped() entirely and let a caller delete
        // $INSTDIR\runtime out from under a service that is still shutting
        // down -- the exact hazard stop_native_service exists to prevent.
        assert_ne!(
            classify_service_stop_exit_code(Some(1061)),
            ServiceStopExitOutcome::AlreadyDone,
            "1061 must never short-circuit the poll: an in-progress stop is not a finished one"
        );
        // And the two codes that DO mean "already in the target state" are
        // still the only ones that skip the poll.
        assert_eq!(
            classify_service_stop_exit_code(Some(ERROR_SERVICE_DOES_NOT_EXIST)),
            ServiceStopExitOutcome::AlreadyDone
        );
        assert_eq!(
            classify_service_stop_exit_code(Some(ERROR_SERVICE_NOT_ACTIVE)),
            ServiceStopExitOutcome::AlreadyDone
        );
    }

    #[test]
    fn service_stop_error_constants_carry_their_exact_win32_values() {
        // The classifier is only correct if these are the real winerror.h
        // numbers. Pinned literally so a typo cannot silently re-open run 17.
        assert_eq!(ERROR_SERVICE_DOES_NOT_EXIST, 1060);
        assert_eq!(ERROR_SERVICE_CANNOT_ACCEPT_CTRL, 1061);
        assert_eq!(ERROR_SERVICE_NOT_ACTIVE, 1062);
    }

    #[test]
    fn service_stop_poll_timeout_outlasts_the_supervisor_stop_watchdog() {
        // G5: wait_for_service_stopped() itself performs real sc.exe I/O and
        // is untested directly (this file's own HARD RULE), so this pins the
        // CONSTANT it is built from -- literally, so a future edit cannot
        // silently shrink it back below the supervisor's own bounded
        // worst-case stop time and reopen the exact race this fix closes.
        //
        // The old value (60s) was LESS than the Python supervisor's SvcStop
        // watchdog (civiccast/native/supervisor/service.py::
        // SVC_STOP_WATCHDOG_SECONDS = 150s): a legitimately slow stop the
        // supervisor's own watchdog would still have let finish successfully
        // instead failed HERE first. 180s must stay strictly greater than
        // that 150s so the installer's wait always outlasts it.
        assert_eq!(SERVICE_STOP_POLL_TIMEOUT_SECS, 180);
        const SUPERVISOR_STOP_WATCHDOG_SECS: u64 = 150; // mirrors service.py's SVC_STOP_WATCHDOG_SECONDS
        assert!(
            SERVICE_STOP_POLL_TIMEOUT_SECS > SUPERVISOR_STOP_WATCHDOG_SECS,
            "the installer's stop-wait ({SERVICE_STOP_POLL_TIMEOUT_SECS}s) must outlast the \
             supervisor's own bounded worst-case stop ({SUPERVISOR_STOP_WATCHDOG_SECS}s)"
        );
    }

    // --- Service START (BLOCKER: nothing ever started the registered
    // service; --startup auto only decides the NEXT boot) ---

    #[test]
    fn service_start_command_matches_sc_exe_documented_grammar() {
        // Same `sc <command> <servicename>` shape as the stop command above:
        // `sc start` takes the service name and no options.
        assert_eq!(
            service_start_command(),
            vec!["start".to_string(), SERVICE_NAME.to_string()]
        );
    }

    #[test]
    fn classify_service_start_exit_code_treats_1056_as_idempotent_success() {
        // Re-running D4 over an already-live station (repair install, a
        // re-run POSTINSTALL hook) must be success, not an install failure.
        assert_eq!(
            classify_service_start_exit_code(Some(ERROR_SERVICE_ALREADY_RUNNING)),
            ServiceStartExitOutcome::AlreadyRunning
        );
        assert_eq!(ERROR_SERVICE_ALREADY_RUNNING, 1056);
    }

    #[test]
    fn classify_service_start_exit_code_distinguishes_issued_from_failed() {
        assert_eq!(
            classify_service_start_exit_code(Some(0)),
            ServiceStartExitOutcome::StartIssued
        );
        for other in [Some(1), Some(5), Some(1053), None] {
            assert_eq!(
                classify_service_start_exit_code(other),
                ServiceStartExitOutcome::Failed,
                "unexpected classification for {other:?}"
            );
        }
    }

    #[test]
    fn a_missing_service_is_never_an_idempotent_start_success() {
        // The MIRROR of the stop path's mapping, and deliberately opposite:
        // on the way down "the service does not exist" is the target state;
        // here it means the registration this ran immediately after did not
        // really take, which must fail the install.
        assert_eq!(
            classify_service_start_exit_code(Some(ERROR_SERVICE_DOES_NOT_EXIST)),
            ServiceStartExitOutcome::Failed
        );
    }

    #[test]
    fn service_query_reports_running_recognizes_the_running_state_line() {
        let sample = "SERVICE_NAME: CivicCastSupervisor\r\n        TYPE               : 10  WIN32_OWN_PROCESS\r\n        STATE              : 4  RUNNING\r\n        WIN32_EXIT_CODE    : 0  (0x0)\r\n";
        assert!(service_query_reports_running(sample));
    }

    #[test]
    fn service_query_reports_running_rejects_pending_and_stopped_states() {
        for sample in [
            "SERVICE_NAME: CivicCastSupervisor\r\n        STATE              : 2  START_PENDING\r\n",
            "SERVICE_NAME: CivicCastSupervisor\r\n        STATE              : 3  STOP_PENDING\r\n",
            "SERVICE_NAME: CivicCastSupervisor\r\n        STATE              : 1  STOPPED\r\n",
        ] {
            assert!(
                !service_query_reports_running(sample),
                "a non-RUNNING state was mistaken for RUNNING: {sample:?}"
            );
        }
    }

    #[test]
    fn the_start_wait_outlasts_the_scm_s_own_service_start_budget() {
        // Windows' SCM gives a service `ServicesPipeTimeout` (30s by default)
        // to report its first status before giving up with error 1053. The
        // installer's wait must outlast the authority that owns the timeout,
        // the same rule SERVICE_STOP_POLL_TIMEOUT_SECS follows against the
        // supervisor's own stop watchdog.
        const SCM_SERVICES_PIPE_TIMEOUT_SECS: u64 = 30;
        assert_eq!(SERVICE_START_POLL_TIMEOUT_SECS, 120);
        assert!(SERVICE_START_POLL_TIMEOUT_SECS > SCM_SERVICES_PIPE_TIMEOUT_SECS);
    }

    #[test]
    fn the_start_failure_exit_code_is_distinct_and_outside_every_reserved_band() {
        // Distinct from the registration-failure code (70) so the installer
        // log says which half failed, and clear of the NSIS-side band.
        //
        // <installer-path-audit MA-28> Moved 74 -> 83. The comment this test
        // used to pin said "74 is free"; it was not -- 74 was already
        // native_uninstall::TRANSFER_ACK_REQUIRED_EXIT_CODE and
        // --civiccast-stage-packs' required-pack failure. The distinctness
        // this test really cares about is now asserted against the actual
        // occupants rather than against a literal.
        assert_eq!(SERVICE_START_FAILED_EXIT_CODE, 83);
        assert_ne!(SERVICE_START_FAILED_EXIT_CODE, 70);
        assert_ne!(
            SERVICE_START_FAILED_EXIT_CODE,
            crate::native_uninstall::TRANSFER_ACK_REQUIRED_EXIT_CODE
        );
        assert_ne!(
            SERVICE_START_FAILED_EXIT_CODE,
            TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE
        );
        // The NSIS band grew to 110-127 with BL-11/BL-13's new codes.
        assert!(!(110..=127).contains(&SERVICE_START_FAILED_EXIT_CODE));
    }

    #[test]
    fn service_query_reports_stopped_recognizes_the_stopped_state_line() {
        let sample = "SERVICE_NAME: CivicCastSupervisor\r\n        TYPE               : 10  WIN32_OWN_PROCESS\r\n        STATE              : 1  STOPPED\r\n        WIN32_EXIT_CODE    : 0  (0x0)\r\n";
        assert!(service_query_reports_stopped(sample));
    }

    #[test]
    fn service_query_reports_stopped_rejects_running_and_stop_pending() {
        let running = "SERVICE_NAME: CivicCastSupervisor\r\n        STATE              : 4  RUNNING\r\n";
        let stop_pending =
            "SERVICE_NAME: CivicCastSupervisor\r\n        STATE              : 3  STOP_PENDING\r\n";
        assert!(!service_query_reports_stopped(running));
        assert!(
            !service_query_reports_stopped(stop_pending),
            "STOP_PENDING must never be mistaken for STOPPED"
        );
    }

    /// Microsoft Learn, "Sc failure" (the canonical `sc.exe failure`
    /// reference; Windows Server 2012 R2/2012 archive page --
    /// https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc742019(v=ws.11)):
    ///
    ///   "reset= <ErrorFreePeriod> ... Specifies the length of the period
    ///   (in seconds) with no failures after which the failure count should
    ///   be reset to 0 (zero). Note that this parameter requires the
    ///   actions= parameter."
    ///
    ///   "actions= {"" | {[run/<MS>] | [restart/<MS>] | [reboot/<MS>]}[/...]
    ///   ... Specifies one or more failure actions and their delay times (in
    ///   milliseconds), separated by a forward slash (/). Valid actions are
    ///   run, restart, and reboot. ... You can specify up to three separate
    ///   actions with the actions= parameter, to be used the first, second,
    ///   and third times that a service fails."
    ///
    ///   "For each command-line option (parameter), the equal sign is part
    ///   of the option name."
    ///
    /// This does NOT compare the produced argv against a copy of the
    /// production literal -- that only proves the function returns whatever
    /// it returns, and silently follows it if the literal is ever edited
    /// wrong. Instead it PARSES the real argv `service_failure_actions_command`
    /// builds against the documented `sc failure` grammar quoted above, with
    /// the expected numbers independently derived from the stated intent
    /// (a one-day reset expressed in SECONDS; a 5s/10s/30s restart ladder
    /// expressed in MILLISECONDS -- two different documented units), so a
    /// malformed-but-parseable recovery configuration -- e.g. a delay
    /// expressed in the wrong unit, a dropped reset= pair, or an
    /// undocumented action keyword -- fails this test even though `sc.exe`
    /// itself would still accept it.
    #[test]
    fn failure_actions_command_matches_documented_sc_failure_grammar() {
        // Supervisor spec AC4: SCM must restart the killed service. SCM's
        // default is no recovery, so these explicit actions ARE the AC4
        // wiring; losing any piece silently reverts to stay-dead.
        let argv = service_failure_actions_command();

        // Fixed positional structure per the documented syntax: `failure
        // <ServiceName> reset= <ErrorFreePeriod> actions= <ActionsToken>`.
        assert_eq!(argv.len(), 6, "unexpected argv shape: {argv:?}");
        assert_eq!(argv[0], "failure");
        assert_eq!(argv[1], SERVICE_NAME);

        // "the equal sign is part of the option name" -- each flag carries
        // its own trailing '=' and is its own argv token; the doc's "a space
        // is required between an option and its value" is exactly what
        // putting the value in the NEXT, separate Vec entry encodes for
        // std::process::Command (which never inserts its own separators).
        assert_eq!(argv[2], "reset=");
        assert_eq!(argv[4], "actions=");

        // reset= is documented in SECONDS. AC4 resets the failure count once
        // a day; derive the expected value from that stated intent (24h *
        // 60m * 60s) instead of copying the production string.
        let reset_seconds: u64 = argv[3].parse().expect("reset= value must be an integer");
        let one_day_in_seconds: u64 = 24 * 60 * 60;
        assert_eq!(
            reset_seconds, one_day_in_seconds,
            "reset= must be a full day expressed in SECONDS per the documented unit"
        );

        // actions= is documented as up to three '/'-separated <action>/<MS>
        // tokens, where <MS> is MILLISECONDS -- a different unit than
        // reset=. Parse rather than string-compare so a unit mix-up is
        // caught even though the resulting string would still be accepted
        // by sc.exe.
        let tokens: Vec<&str> = argv[5].split('/').collect();
        assert_eq!(
            tokens.len() % 2,
            0,
            "actions= must be an even number of action/delay tokens: {tokens:?}"
        );
        let action_delay_pairs: Vec<(&str, u64)> = tokens
            .chunks(2)
            .map(|pair| {
                let delay: u64 = pair[1]
                    .parse()
                    .unwrap_or_else(|_| panic!("non-numeric delay in actions= token: {pair:?}"));
                (pair[0], delay)
            })
            .collect();
        assert!(
            action_delay_pairs.len() <= 3,
            "sc.exe failure documents AT MOST three actions: {action_delay_pairs:?}"
        );
        for (action, _) in &action_delay_pairs {
            assert!(
                ["run", "restart", "reboot"].contains(action),
                "{action:?} is not a documented sc.exe failure action"
            );
        }

        // AC4's actual recovery intent: restart at EVERY one of the (up to
        // three) documented failure slots, at an escalating 5s/10s/30s
        // ladder expressed in the documented MILLISECOND unit -- computed
        // here from the stated seconds, not copied from the production
        // string.
        let expected_ladder_ms: Vec<u64> = [5u64, 10, 30].iter().map(|s| s * 1000).collect();
        assert_eq!(
            action_delay_pairs
                .iter()
                .map(|(_, delay)| *delay)
                .collect::<Vec<_>>(),
            expected_ladder_ms,
            "restart ladder must be 5s/10s/30s expressed in milliseconds"
        );
        for (action, _) in &action_delay_pairs {
            assert_eq!(*action, "restart", "AC4 requires a RESTART at every rung");
        }
    }

    #[test]
    fn failure_flag_command_recovers_nonzero_exit_stops_like_crashes() {
        assert_eq!(
            service_failure_flag_command(),
            vec![
                "failureflag".to_string(),
                SERVICE_NAME.to_string(),
                "1".to_string()
            ]
        );
    }

    #[test]
    fn validate_database_url_value_rejects_empty_and_blank() {
        for bad in ["", "   ", "\t"] {
            assert!(
                validate_database_url_value(bad).is_err(),
                "{bad:?} must be rejected"
            );
        }
    }

    #[test]
    fn validate_database_url_value_rejects_control_characters() {
        assert!(validate_database_url_value("postgresql://x\n/db").is_err());
        assert!(validate_database_url_value("postgresql://x\0/db").is_err());
    }

    #[test]
    fn validate_database_url_value_rejects_non_postgresql_scheme() {
        for bad in ["mysql://x/db", "not a url at all", "http://127.0.0.1:8000/"] {
            assert!(
                validate_database_url_value(bad).is_err(),
                "{bad:?} must be rejected"
            );
        }
    }

    #[test]
    fn validate_database_url_value_accepts_a_well_formed_url() {
        validate_database_url_value("postgresql://civiccast:hunter2@127.0.0.1:5432/civiccast")
            .expect("well-formed postgresql:// value must be accepted");
    }

    #[test]
    fn classify_firewall_probe_output_reports_absent_on_the_no_rules_string() {
        assert_eq!(
            classify_firewall_probe_output(
                true,
                "\r\nNo rules match the specified criteria.\r\n\r\nOk.\r\n"
            ),
            FirewallProbeState::Absent
        );
    }

    #[test]
    fn classify_firewall_probe_output_reports_present_on_a_rule_name_row() {
        let sample = "\r\nRule Name:                            CivicCast (Native) Portal/API (TCP 8000)\r\n----------------------------------------------------------------------\r\nEnabled:                              Yes\r\n\r\nOk.\r\n";
        assert_eq!(
            classify_firewall_probe_output(true, sample),
            FirewallProbeState::Present
        );
    }

    #[test]
    fn classify_firewall_probe_output_fails_closed_to_unknown_on_unrecognized_shapes() {
        assert_eq!(
            classify_firewall_probe_output(false, ""),
            FirewallProbeState::Unknown
        );
        assert_eq!(
            classify_firewall_probe_output(true, "some unexpected localized text"),
            FirewallProbeState::Unknown
        );
        assert_eq!(
            classify_firewall_probe_output(true, ""),
            FirewallProbeState::Unknown
        );
    }

    #[test]
    fn firewall_rule_action_is_a_clean_noop_when_present_add_when_absent_block_when_unknown() {
        assert_eq!(
            firewall_rule_action(FirewallProbeState::Present),
            FirewallRuleAction::NoOp
        );
        assert_eq!(
            firewall_rule_action(FirewallProbeState::Absent),
            FirewallRuleAction::Add
        );
        assert_eq!(
            firewall_rule_action(FirewallProbeState::Unknown),
            FirewallRuleAction::Block
        );
    }

    #[test]
    fn firewall_show_rule_command_matches_the_exact_rule_name() {
        let args = firewall_show_rule_command();
        assert_eq!(
            args,
            vec![
                "advfirewall".to_string(),
                "firewall".to_string(),
                "show".to_string(),
                "rule".to_string(),
                format!("name={FIREWALL_RULE_NAME}"),
            ]
        );
    }

    #[test]
    fn firewall_add_rule_command_has_the_expected_shape_program_and_port() {
        let install_root = Path::new(r"C:\Program Files\CivicCast Native");
        let args = firewall_add_rule_command(install_root);
        let program = install_root.join("runtime").join("python.exe");

        assert_eq!(args[0], "advfirewall");
        assert_eq!(args[1], "firewall");
        assert_eq!(args[2], "add");
        assert_eq!(args[3], "rule");
        assert!(args.contains(&format!("name={FIREWALL_RULE_NAME}")));
        assert!(args.contains(&"dir=in".to_string()));
        assert!(args.contains(&"action=allow".to_string()));
        assert!(args.contains(&"protocol=TCP".to_string()));
        assert!(args.contains(&format!("localport={CONTROL_PLANE_PORT}")));
        assert!(args.contains(&format!("program={}", program.display())));
        assert!(args.contains(&"enable=yes".to_string()));
    }

    #[test]
    fn firewall_delete_rule_command_targets_the_exact_rule_name() {
        assert_eq!(
            firewall_delete_rule_command(),
            vec![
                "advfirewall".to_string(),
                "firewall".to_string(),
                "delete".to_string(),
                "rule".to_string(),
                format!("name={FIREWALL_RULE_NAME}"),
            ]
        );
    }

    /// Drift guard: `SERVICE_NAME` above is a Rust mirror of
    /// `civiccast.native.supervisor.config.SERVICE_NAME`. If that Python
    /// constant is ever edited without updating this file, this test fails
    /// `cargo test` rather than letting the two silently diverge.
    #[test]
    fn service_name_mirror_matches_the_python_source_of_truth() {
        let config_py = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../native/supervisor/config.py"
        );
        let source = std::fs::read_to_string(config_py)
            .unwrap_or_else(|error| panic!("could not read {config_py}: {error}"));
        let expected = format!("SERVICE_NAME = \"{SERVICE_NAME}\"");
        assert!(
            source.contains(&expected),
            "expected {config_py} to contain {expected:?} (SERVICE_NAME mirror drifted)"
        );
    }

    #[test]
    fn teardown_all_succeeded_is_true_only_when_no_step_failed() {
        let all_ok = vec![
            TeardownStepOutcome {
                label: "stop service",
                detail: "ok".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: "remove service",
                detail: "ok".to_string(),
                failed: false,
            },
        ];
        assert!(teardown_all_succeeded(&all_ok));

        let one_failed = vec![
            TeardownStepOutcome {
                label: "stop service",
                detail: "ok".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: "remove service",
                detail: "boom".to_string(),
                failed: true,
            },
        ];
        assert!(!teardown_all_succeeded(&one_failed));
    }

    /// CRITICAL fix pin (2026-07-30 adversarial review): the "stop service"
    /// step failing must map to the DISTINCT
    /// `TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE` (82), never the generic
    /// 80 -- that is the exact signal `NSIS_HOOK_POSTUNINSTALL` gates its
    /// recursive `RMDir /r` on. A "remove service" or "delete firewall rule"
    /// failure alone must still map to the generic 80 (over-refusing tree
    /// removal for those would strand gigabytes of data for no safety
    /// reason), and all-success must still map to 0.
    /// `stopped_for_rebuild` drives the repair CLI's operator notice ("the
    /// service was stopped and NOT restarted -- start it when the station
    /// should go back on air"). It must report a stop that SUCCEEDED, not
    /// merely one that was attempted: a failed stop means the rebuild was
    /// refused and nothing was torn down, so claiming the service was stopped
    /// would send an operator to restart something that never went down, while
    /// the real failure (an unrepairable tree) goes unexplained.
    #[cfg(target_os = "windows")]
    #[test]
    fn stopped_for_rebuild_reports_only_a_stop_that_succeeded() {
        let never_attempted = ServiceQuiescenceAuthority::new();
        assert!(
            !never_attempted.stopped_for_rebuild(),
            "an authority that was never consulted has stopped nothing"
        );

        let succeeded = ServiceQuiescenceAuthority::new();
        succeeded
            .stop_outcome
            .set(Ok(()))
            .expect("fresh authority cell is empty");
        assert!(succeeded.stopped_for_rebuild());

        let failed = ServiceQuiescenceAuthority::new();
        failed
            .stop_outcome
            .set(Err("sc.exe stop timed out after 180s".to_string()))
            .expect("fresh authority cell is empty");
        assert!(
            !failed.stopped_for_rebuild(),
            "a failed stop must not be reported to the operator as a stop"
        );
    }

    #[test]
    fn teardown_exit_code_distinguishes_service_stop_failure_from_generic_failure() {
        let all_ok = vec![
            TeardownStepOutcome {
                label: "stop service",
                detail: "ok".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: "remove service",
                detail: "ok".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: "delete firewall rule",
                detail: "ok".to_string(),
                failed: false,
            },
        ];
        assert_eq!(teardown_exit_code(&all_ok), 0);
        assert!(!service_stop_failed(&all_ok));

        let stop_failed = vec![
            TeardownStepOutcome {
                label: "stop service",
                detail: "Timed out after 180s waiting for CivicCastSupervisor to reach STOPPED state.".to_string(),
                failed: true,
            },
            TeardownStepOutcome {
                label: "remove service",
                detail: "ok".to_string(),
                failed: false,
            },
        ];
        assert!(service_stop_failed(&stop_failed));
        assert_eq!(
            teardown_exit_code(&stop_failed),
            TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE
        );
        assert_eq!(TEARDOWN_SERVICE_STOP_UNCONFIRMED_EXIT_CODE, 82);

        let other_step_failed = vec![
            TeardownStepOutcome {
                label: "stop service",
                detail: "ok".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: "delete firewall rule",
                detail: "boom".to_string(),
                failed: true,
            },
        ];
        assert!(!service_stop_failed(&other_step_failed));
        assert_eq!(teardown_exit_code(&other_step_failed), 80);
    }

    #[test]
    fn installed_version_value_name_is_distinct_from_database_url_value_name() {
        assert_ne!(INSTALLED_VERSION_VALUE_NAME, DATABASE_URL_VALUE_NAME);
    }

    /// Security-fix pin (2026-07-30, shared-municipal-PC credential
    /// hardening): a REAL Win32 registry-security round trip, run against a
    /// scratch `HKEY_CURRENT_USER` subkey (no admin rights needed -- HKCU is
    /// owned by the test's own account) rather than the real
    /// `HKLM\SOFTWARE\CivicCast\Native`, exercising the EXACT SAME
    /// `write_value_to_key` code path [`write_database_url`] calls in
    /// production (only the predefined root/path differ). This is the "the
    /// hardening code path is invoked when the location is created" proof
    /// the fix task asked for, plus a real DACL-content assertion (this
    /// platform permits it -- we are on Windows). See
    /// `write_value_to_key_without_hardening_leaves_the_default_dacl_in_place`
    /// below for the revert-and-fail proof this test can actually fail.
    ///
    /// PRIVILEGE TIER, stated explicitly per this project's documented
    /// non-admin-dev-box-vs-admin-CI split (two prior CI incidents on this
    /// exact mismatch): this test requires ONLY an ordinary, un-elevated
    /// user token, and behaves IDENTICALLY under both tiers. Confirmed by
    /// actually running it here under a verified NON-elevated Medium
    /// Integrity token (`whoami /groups` shows `BUILTIN\Administrators` as
    /// "Group used for deny only"; `net session` fails) -- green under that
    /// tier. It would ALSO pass under a full-admin/CI token: an elevated
    /// token has every right a Medium-IL owner already has here, and no
    /// assertion below depends on elevation being present. What makes this
    /// tier-independent is the SAME reason `RegSetKeySecurity` succeeds at
    /// all without admin rights: the object's OWNER (this test's own
    /// process, which just created the key) ALWAYS implicitly has
    /// `WRITE_DAC`/`READ_CONTROL` on it, regardless of the token's
    /// elevation/integrity level or whether `BUILTIN\Administrators` is
    /// enabled in that token -- that is a fixed Windows access-check rule,
    /// not a permission this test's account happens to have been granted.
    /// The REAL admin-gated path -- `write_database_url` against actual
    /// `HKLM\SOFTWARE\CivicCast\Native`, which DOES require elevation
    /// because `HKLM\SOFTWARE` denies an ordinary token `KEY_SET_VALUE`/
    /// `WRITE_DAC` in the first place -- is deliberately NOT exercised by
    /// any test in this file, matching the pre-existing "real SCM/registry
    /// execution is untested directly" HARD RULE this file already applies
    /// to `register_native_service`/`stop_native_service`/etc.; there is
    /// therefore no tier-dependent assertion anywhere in this change for CI
    /// and the dev box to disagree about.
    #[cfg(target_os = "windows")]
    #[test]
    fn write_value_to_key_hardens_the_dacl_and_round_trips_the_value() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\write_value_to_key_hardens_the_dacl_and_round_trips_the_value\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path); // best-effort cleanup of a prior aborted run

        let distinctive_value =
            "postgresql://civiccast_svc:TEST-ONLY-NOT-A-REAL-SECRET-b7f3@127.0.0.1:5432/civiccast";
        // write_value_to_key's OWN internal read-back check (over the handle
        // it opened BEFORE hardening the DACL, so that handle's already-
        // granted access is unaffected by the later restriction) already
        // proves the value round-trips through the real registry -- an
        // `Err` here would mean either the write or that internal
        // verification failed.
        write_value_to_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            "TestDatabaseUrl",
            distinctive_value,
            SYSTEM_ADMIN_ONLY_SDDL,
            validate_database_url_value,
        )
        .expect("write_value_to_key must succeed (write + its own read-back verify) against a scratch HKCU key");

        // Deliberately NOT re-opened with KEY_READ here: now that the DACL is
        // hardened to SYSTEM+Administrators only, a plain KEY_READ open by
        // this (ordinary, non-elevated) test account correctly FAILS
        // ACCESS_DENIED -- that failure is itself part of the proof the
        // hardening works, confirmed empirically while writing this test.
        // READ_CONTROL alone (0x00020000, a generic standard right every
        // object's OWNER retains regardless of what the DACL says -- this
        // test's own process created the key, so it is the owner) is enough
        // to read the DACL back for inspection without needing any ACE to
        // name this account explicitly.
        const READ_CONTROL: u32 = 0x0002_0000;
        let key = hkcu
            .open_subkey_with_flags(&scratch_path, READ_CONTROL)
            .expect("the key's OWNER must retain READ_CONTROL even after DACL hardening");

        let sddl = registry_acl::read_dacl_sddl(key.raw_handle())
            .expect("DACL must be readable back after hardening");
        assert!(
            sddl.starts_with("D:P"),
            "DACL must be PROTECTED (P) so no inherited ACE can flow back in: {sddl:?}"
        );
        assert!(sddl.contains(";;;SY)"), "DACL must grant SYSTEM: {sddl:?}");
        assert!(
            sddl.contains(";;;BA)"),
            "DACL must grant BUILTIN\\Administrators: {sddl:?}"
        );
        assert!(
            !sddl.contains(";;;AU)"),
            "DACL must NOT grant Authenticated Users: {sddl:?}"
        );
        assert!(
            !sddl.contains(";;;BU)"),
            "DACL must NOT grant BUILTIN\\Users: {sddl:?}"
        );
        assert!(
            !sddl.contains(";;;WD)"),
            "DACL must NOT grant Everyone/World: {sddl:?}"
        );

        drop(key);
        // Cleanup: DELETE is not one of the rights an owner implicitly
        // retains (only READ_CONTROL/WRITE_DAC are), so a plain
        // `delete_subkey_all` against the now SYSTEM+Administrators-only key
        // would itself fail ACCESS_DENIED for this ordinary test account.
        // Re-open with WRITE_DAC (an owner-guaranteed right, same as
        // READ_CONTROL above) and re-widen the DACL before deleting, so this
        // test does not leak a permanently-locked scratch key into the real
        // HKCU\Software\CivicCastAclHardenTest tree on every run.
        const WRITE_DAC: u32 = 0x0004_0000;
        let reopened_for_cleanup = hkcu
            .open_subkey_with_flags(&scratch_path, WRITE_DAC)
            .expect("the key's OWNER must retain WRITE_DAC even after DACL hardening");
        registry_acl::harden_key_acl(reopened_for_cleanup.raw_handle(), "D:(A;;GA;;;WD)")
            .expect("owner must be able to re-widen the DACL before cleanup");
        drop(reopened_for_cleanup);
        hkcu.delete_subkey_all(&scratch_path)
            .expect("test cleanup must succeed");
    }

    /// SECURITY FIX F-02 proof (2026-08-01 sandbox newcomer re-walk): the
    /// uninstall-side counterpart to
    /// `write_value_to_key_hardens_the_dacl_and_round_trips_the_value` above,
    /// against a REAL registry, through the EXACT `delete_values_from_key`
    /// code path [`delete_native_credential_values`] calls in production (only
    /// the predefined root/path differ -- the same HKCU-scoped, non-admin
    /// convention that test already established, for the same reason).
    ///
    /// Writes a DatabaseUrl-shaped value through the production writer,
    /// deletes it through the production deleter, and proves it is actually
    /// gone by reading it back. Then runs the deleter a second time to prove
    /// idempotency (already-absent is success, reported as "was already
    /// absent", not an error) -- the property the uninstall path depends on,
    /// since a station may be uninstalled after a failed install that never
    /// wrote the value.
    #[cfg(target_os = "windows")]
    #[test]
    fn delete_values_from_key_removes_the_credential_value_and_is_idempotent() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\delete_values_from_key_removes_the_credential_value_and_is_idempotent\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path);

        // Written through the PRODUCTION writer, so this test would also fail
        // if the write path stopped landing the value at all.
        write_value_to_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            DATABASE_URL_VALUE_NAME,
            "postgresql://civiccast_svc:TEST-ONLY-NOT-A-REAL-SECRET-9c21@127.0.0.1:5432/civiccast",
            "D:(A;;GA;;;WD)",
            validate_database_url_value,
        )
        .expect("scratch DatabaseUrl write must succeed");

        let first = delete_values_from_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            &[DATABASE_URL_VALUE_NAME],
        )
        .expect("deleting the credential value must succeed");
        assert_eq!(
            first,
            vec![(DATABASE_URL_VALUE_NAME, true)],
            "the credential value must be reported as ACTUALLY deleted"
        );

        // The real proof: read it back. A reported deletion that did not
        // happen is exactly the class of claim F-02 was.
        let key = hkcu
            .open_subkey_with_flags(&scratch_path, KEY_READ | KEY_WOW64_64KEY)
            .expect("the scratch key itself must survive -- only its VALUES are deleted");
        let readback: Result<String, _> = key.get_value(DATABASE_URL_VALUE_NAME);
        assert!(
            readback.is_err(),
            "{DATABASE_URL_VALUE_NAME} must be GONE from the registry after the teardown \
             deletion, not merely reported as deleted"
        );
        drop(key);

        let second = delete_values_from_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            &[DATABASE_URL_VALUE_NAME],
        )
        .expect("a second deletion over an already-absent value must be success, not an error");
        assert_eq!(
            second,
            vec![(DATABASE_URL_VALUE_NAME, false)],
            "an already-absent value must be reported as such, never as a fresh deletion"
        );

        // ...and an entirely absent KEY is idempotent success too.
        hkcu.delete_subkey_all(&scratch_path)
            .expect("test cleanup must succeed");
        let absent_key = delete_values_from_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            &[DATABASE_URL_VALUE_NAME],
        )
        .expect("an absent key must be idempotent success");
        assert!(absent_key.iter().all(|(_, deleted)| !deleted));
    }

    /// The abort path must leave the machine FULLY INTACT -- the guarantee
    /// `NSIS_HOOK_PREUNINSTALL` states in its own refusal branch. A teardown
    /// that could not confirm the service stopped returns 82, the uninstall
    /// aborts having removed nothing, and a station that is still installed and
    /// still RUNNING must not have had its credential or its version marker
    /// deleted out from under it on the way past.
    #[test]
    fn registry_state_is_never_cleared_when_the_service_could_not_be_confirmed_stopped() {
        let stopped = vec![TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[0],
            detail: "stopped".to_string(),
            failed: false,
        }];
        assert!(
            may_clear_registry_state(&stopped),
            "an ordinary uninstall, service confirmed stopped, must clear registry state"
        );

        let not_stopped = vec![TeardownStepOutcome {
            label: TEARDOWN_STEP_LABELS[0],
            detail: "timed out waiting for STOPPED".to_string(),
            failed: true,
        }];
        assert!(
            !may_clear_registry_state(&not_stopped),
            "a teardown that could not confirm the service stopped aborts the whole uninstall \
             with nothing removed -- it must not delete registry state on the way out"
        );

        // Narrow, matching service_stop_failed: an unrelated failure does not
        // make registry clearing unsafe.
        let firewall_failed = vec![
            TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[0],
                detail: "stopped".to_string(),
                failed: false,
            },
            TeardownStepOutcome {
                label: TEARDOWN_STEP_LABELS[2],
                detail: "netsh failed".to_string(),
                failed: true,
            },
        ];
        assert!(may_clear_registry_state(&firewall_failed));
    }

    /// The teardown orchestration must actually CONTAIN the credential-clearing
    /// step -- `delete_native_credential_values` existing but never being
    /// called is precisely the shape the pre-fix tree had (the function was
    /// implemented and tested, and no uninstall ever ran it).
    #[test]
    fn teardown_clears_credentials_after_the_service_and_firewall_are_gone() {
        assert_eq!(
            TEARDOWN_STEP_LABELS,
            &[
                "stop service",
                "remove service",
                "delete firewall rule",
                "clear credentials",
                "clear install markers",
                "clear empty Native key",
                "clear released maintenance interlock",
                "clear empty CivicCast key",
            ],
            "the credential clear, the install-marker clear, the empty-Native-key removal (N-20), \
             the released-Maintenance-blob removal (N-20) and the empty-parent-CivicCast-key \
             removal (2d1123a0 polish re-walk) must all be real, ordered teardown steps, and must \
             run AFTER the service that reads them is stopped and removed"
        );
        assert_eq!(
            TEARDOWN_STEP_LABELS.last(),
            Some(&"clear empty CivicCast key"),
            "the parent CivicCast key removal must be the LAST teardown step -- it must run \
             after both the Native subkey removal and the Maintenance blob removal, since both \
             of those live directly under the same parent key this step considers removing"
        );
        assert_eq!(
            CREDENTIAL_VALUE_NAMES,
            &[DATABASE_URL_VALUE_NAME],
            "every value under CivicCast\\Native that carries a live secret must be in the set \
             the teardown clears"
        );
        assert_eq!(
            INSTALL_MARKER_VALUE_NAMES,
            &[INSTALLED_VERSION_VALUE_NAME],
            "every value under CivicCast\\Native that claims a product is installed must be in \
             the set the teardown clears"
        );
        // The two sets are disjoint on purpose: they are cleared by separate,
        // separately-reported steps because they answer different questions
        // ("is a secret still here?" vs "does this machine still claim a
        // product is installed?"), and an uninstall log that collapses them
        // into one line cannot answer either.
        assert!(
            !CREDENTIAL_VALUE_NAMES
                .iter()
                .any(|name| INSTALL_MARKER_VALUE_NAMES.contains(name)),
            "a value must not be in both the credential set and the install-marker set"
        );
    }

    /// N-20 proof (carried, rewalk-de3aaf6f): once every value under
    /// `CivicCast\Native` is cleared, [`native_key_outcome_from`] must
    /// actually remove the now-empty key -- not just report it clear. This
    /// is the exact symptom the finding named: post-uninstall registry
    /// sweeps found `HKLM\SOFTWARE\CivicCast\Native` present and empty.
    #[cfg(target_os = "windows")]
    #[test]
    fn native_key_outcome_from_deletes_the_key_once_every_value_is_gone() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\native_key_outcome_from_deletes_the_key_once_every_value_is_gone\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path);

        // Absent key: idempotent success, nothing to delete.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            NativeKeyOutcome::AlreadyAbsent,
            "an entirely absent key must be reported as already absent, not an error"
        );

        // Write, then clear, both real values -- the SAME production path
        // `delete_native_credential_values`/`delete_native_install_marker_values`
        // use, so this test would also fail if that path stopped emptying
        // the key.
        write_value_to_key(
            HKEY_CURRENT_USER,
            &scratch_path,
            DATABASE_URL_VALUE_NAME,
            "postgresql://civiccast_svc:TEST-ONLY-NOT-A-REAL-SECRET-9c21@127.0.0.1:5432/civiccast",
            "D:(A;;GA;;;WD)",
            validate_database_url_value,
        )
        .expect("scratch DatabaseUrl write must succeed");

        // Not yet empty: must be left standing, never forced.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            NativeKeyOutcome::NotEmptySkipped,
            "a key that still has a value must be left standing, not deleted"
        );
        hkcu.open_subkey_with_flags(&scratch_path, KEY_READ | KEY_WOW64_64KEY)
            .expect("the key must still exist after a NotEmptySkipped outcome");

        delete_values_from_key(HKEY_CURRENT_USER, &scratch_path, &[DATABASE_URL_VALUE_NAME])
            .expect("clearing the one value must succeed");

        // Now genuinely empty: must be deleted, and proven gone by reading
        // it back, not merely reported as deleted.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            NativeKeyOutcome::Deleted,
            "an empty key must be reported as deleted"
        );
        let readback = hkcu.open_subkey_with_flags(&scratch_path, KEY_READ | KEY_WOW64_64KEY);
        assert!(
            readback.is_err(),
            "the key must be GONE from the registry, not merely reported as deleted"
        );

        // Idempotent: a second call over an already-gone key is success.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            NativeKeyOutcome::AlreadyAbsent,
            "a second call after deletion must be idempotent success"
        );

        let _ = hkcu.delete_subkey_all(&scratch_path);
    }

    /// N-20 proof, ownership guard: a subkey underneath (not just a value
    /// directly on it) must also block deletion -- "empty" means no
    /// subkeys AND no values, not just no values.
    #[cfg(target_os = "windows")]
    #[test]
    fn native_key_outcome_from_refuses_to_delete_a_key_with_a_subkey() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\native_key_outcome_from_refuses_to_delete_a_key_with_a_subkey\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path);
        let (_key, _) = hkcu
            .create_subkey_with_flags(&scratch_path, KEY_READ)
            .expect("scratch key must be creatable");
        hkcu.create_subkey_with_flags(format!("{scratch_path}\\SomethingElse"), KEY_READ)
            .expect("scratch subkey must be creatable");

        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            NativeKeyOutcome::NotEmptySkipped,
            "a key with a subkey (even with zero values of its own) must never be deleted"
        );
        hkcu.open_subkey_with_flags(&scratch_path, KEY_READ)
            .expect("the key must still exist");

        hkcu.delete_subkey_all(&scratch_path)
            .expect("test cleanup must succeed");
    }

    /// Finding proof (2d1123a0 polish re-walk, evidence
    /// `P01-postuninstall-2d1123a0.txt`): after N-20 removed the empty
    /// `CivicCast\Native` subkey and the released `Maintenance` blob, the
    /// PARENT `HKLM\SOFTWARE\CivicCast` key itself was left behind, present
    /// and now empty. [`delete_civiccast_root_key_if_empty`] is
    /// [`native_key_outcome_from`] against the real
    /// [`CIVICCAST_ROOT_KEY`], so proving the mechanism here against a
    /// scratch TWO-LEVEL hierarchy -- a parent key with a child subkey,
    /// exactly like `CivicCast` -> `CivicCast\Native` -- is the same proof
    /// as the real wiring, without ever touching HKLM.
    #[cfg(target_os = "windows")]
    #[test]
    fn civiccast_root_key_outcome_deletes_the_parent_once_the_native_subkey_and_every_value_are_gone(
    ) {
        use winreg::enums::HKEY_CURRENT_USER;

        let parent_path = format!(
            r"Software\CivicCastAclHardenTest\civiccast_root_key_outcome_deletes_the_parent\{}",
            std::process::id()
        );
        let child_path = format!(r"{parent_path}\Native");
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&parent_path);

        // Absent parent: idempotent success, nothing to delete.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::AlreadyAbsent,
            "an entirely absent parent key must be reported as already absent, not an error"
        );

        // Build the real shape: parent with a child subkey ("Native", holding
        // a value) AND a value of the parent's own (mirroring the
        // Maintenance blob).
        let (child_key, _) = hkcu
            .create_subkey_with_flags(&child_path, KEY_READ | KEY_SET_VALUE)
            .expect("scratch child key must be creatable");
        child_key
            .set_value(DATABASE_URL_VALUE_NAME, &"scratch-child-value")
            .expect("scratch child value write must succeed");
        drop(child_key);
        let (parent_key, _) = hkcu
            .create_subkey_with_flags(&parent_path, KEY_READ | KEY_SET_VALUE)
            .expect("scratch parent key must be creatable");
        parent_key
            .set_value(MAINTENANCE_VALUE_NAME, &"scratch-parent-value")
            .expect("scratch parent value write must succeed");
        drop(parent_key);

        // Still has a live child subkey AND a live value of its own: must be
        // left standing, never forced.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::NotEmptySkipped,
            "a parent with a live child subkey and a live value of its own must never be deleted"
        );
        hkcu.open_subkey_with_flags(&parent_path, KEY_READ | KEY_WOW64_64KEY)
            .expect("the parent key must still exist after a NotEmptySkipped outcome");

        // Clear the child subkey (mirrors delete_native_key_if_empty removing
        // the now-empty Native key) and the parent's own value (mirrors
        // delete_released_maintenance_blob) -- the same two steps that run
        // immediately before this one in teardown_native_state.
        hkcu.delete_subkey_all(&child_path)
            .expect("clearing the scratch child key must succeed");
        delete_values_from_key(HKEY_CURRENT_USER, &parent_path, &[MAINTENANCE_VALUE_NAME])
            .expect("clearing the scratch parent value must succeed");

        // Now genuinely empty: must be deleted, and proven gone by reading it
        // back, not merely reported as deleted -- this is the exact symptom
        // the finding named: the post-uninstall sweep found the parent key
        // "present: True" after everything under it was already gone.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::Deleted,
            "an empty parent key must be reported as deleted"
        );
        let readback = hkcu.open_subkey_with_flags(&parent_path, KEY_READ | KEY_WOW64_64KEY);
        assert!(
            readback.is_err(),
            "the parent key must be GONE from the registry, not merely reported as deleted"
        );

        // Idempotent: a second call over an already-gone key is success.
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::AlreadyAbsent,
            "a second call after deletion must be idempotent success"
        );

        let _ = hkcu.delete_subkey_all(&parent_path);
    }

    /// SAFETY property (2d1123a0 polish re-walk) -- the IMPORTANT half of
    /// this fix: if some OTHER CivicCast product or component (the WSL
    /// line, or a future component) legitimately keeps a value directly
    /// under `HKLM\SOFTWARE\CivicCast`, the new "clear empty CivicCast key"
    /// teardown step must NEVER delete the parent out from under it.
    /// [`native_key_outcome_from`]'s emptiness check is a FRESH read taken
    /// immediately before the delete, on the EXACT key path -- proven here
    /// against a value this teardown has never heard of (neither a
    /// credential/install-marker value nor the Maintenance blob), standing
    /// in for another product's own legitimate state.
    #[cfg(target_os = "windows")]
    #[test]
    fn civiccast_root_key_outcome_preserves_a_parent_that_still_carries_an_unrelated_value() {
        use winreg::enums::HKEY_CURRENT_USER;

        let parent_path = format!(
            r"Software\CivicCastAclHardenTest\civiccast_root_key_outcome_preserves_unrelated_value\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&parent_path);

        let (key, _) = hkcu
            .create_subkey_with_flags(&parent_path, KEY_READ | KEY_SET_VALUE)
            .expect("scratch parent key must be creatable");
        key.set_value("SomeOtherCivicCastComponentsOwnValue", &"do-not-touch")
            .expect("scratch unrelated value write must succeed");
        drop(key);

        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::NotEmptySkipped,
            "a parent key carrying a value this teardown does not own must be left standing, \
             never forced -- this is the exact hazard the fresh emptiness re-check exists to \
             prevent, and the reason it is safe for other CivicCast products/components to \
             share this same parent key"
        );

        let readback_key = hkcu
            .open_subkey_with_flags(&parent_path, KEY_READ | KEY_WOW64_64KEY)
            .expect("the parent key must still exist -- it must never be force-deleted");
        let still_there: String = readback_key
            .get_value("SomeOtherCivicCastComponentsOwnValue")
            .expect("the unrelated value must still be readable, completely untouched");
        assert_eq!(still_there, "do-not-touch");

        // A subkey (not just a value) belonging to something else must be
        // just as protective as a value -- same discipline as
        // native_key_outcome_from_refuses_to_delete_a_key_with_a_subkey
        // above, re-proven at the parent level.
        let other_component_subkey = format!("{parent_path}\\SomeOtherComponent");
        hkcu.delete_subkey_all(&parent_path)
            .expect("test reset must succeed");
        hkcu.create_subkey_with_flags(&parent_path, KEY_READ)
            .expect("scratch parent key must be recreatable");
        hkcu.create_subkey_with_flags(&other_component_subkey, KEY_READ)
            .expect("scratch other-component subkey must be creatable");
        assert_eq!(
            native_key_outcome_from(HKEY_CURRENT_USER, &parent_path).unwrap(),
            NativeKeyOutcome::NotEmptySkipped,
            "a parent key carrying an unrelated SUBKEY must also be left standing"
        );
        hkcu.open_subkey_with_flags(&other_component_subkey, KEY_READ)
            .expect("the other component's subkey must still exist -- untouched");

        let _ = hkcu.delete_subkey_all(&parent_path);
    }

    /// N-20 proof: the Maintenance interlock blob is only ever deleted when
    /// its own `state` field reads exactly "released" -- never when it is
    /// "held" (some other operation may depend on it), and never when it
    /// cannot be parsed at all.
    #[cfg(target_os = "windows")]
    #[test]
    fn maintenance_blob_outcome_from_only_deletes_a_released_record() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\maintenance_blob_outcome_from_only_deletes_a_released_record\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path);

        assert_eq!(
            maintenance_blob_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            MaintenanceBlobOutcome::AlreadyAbsent,
            "an entirely absent key must be reported as already absent"
        );

        let (key, _) = hkcu
            .create_subkey_with_flags(&scratch_path, KEY_READ | KEY_SET_VALUE)
            .expect("scratch key must be creatable");

        // A "held" record: some other operation may still depend on it.
        // Must be left completely alone.
        key.set_value(
            MAINTENANCE_VALUE_NAME,
            &r#"{"v":1,"state":"held","generation":3,"owner_run_id":"nsis-live-run","taken_utc":"2026-08-01T00:00:00+00:00","released_utc":null}"#,
        )
        .expect("scratch Maintenance write must succeed");
        assert_eq!(
            maintenance_blob_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            MaintenanceBlobOutcome::HeldOrUnknownLeftInPlace,
            "a held interlock must never be deleted"
        );
        let still_there: String = key
            .get_value(MAINTENANCE_VALUE_NAME)
            .expect("a held record must still be readable after the refused delete");
        assert!(still_there.contains("\"held\""));

        // Malformed JSON: must be left alone, not guessed at.
        key.set_value(MAINTENANCE_VALUE_NAME, &"not valid json")
            .expect("scratch Maintenance overwrite must succeed");
        assert_eq!(
            maintenance_blob_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            MaintenanceBlobOutcome::UnreadableLeftInPlace,
            "malformed JSON must never be deleted"
        );

        // A released record (the exact shape the finding named --
        // owner_run_id "nsis-917878", state "released"): must be deleted,
        // and proven gone by reading it back.
        key.set_value(
            MAINTENANCE_VALUE_NAME,
            &r#"{"v":1,"state":"released","generation":2,"owner_run_id":"nsis-917878","taken_utc":"2026-07-31T00:00:00+00:00","released_utc":"2026-07-31T00:05:00+00:00"}"#,
        )
        .expect("scratch Maintenance overwrite must succeed");
        assert_eq!(
            maintenance_blob_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            MaintenanceBlobOutcome::Deleted,
            "a released interlock must be deleted"
        );
        let readback: Result<String, _> = key.get_value(MAINTENANCE_VALUE_NAME);
        assert!(
            readback.is_err(),
            "the Maintenance value must be GONE, not merely reported as deleted"
        );

        // Idempotent: a second call is success.
        assert_eq!(
            maintenance_blob_outcome_from(HKEY_CURRENT_USER, &scratch_path).unwrap(),
            MaintenanceBlobOutcome::AlreadyAbsent,
            "a second call after deletion must be idempotent success"
        );

        drop(key);
        hkcu.delete_subkey_all(&scratch_path)
            .expect("test cleanup must succeed");
    }

    /// The revert-and-fail proof for the test above: with the hardening
    /// call removed, a freshly created scratch key keeps whatever DACL its
    /// PARENT (`Software\CivicCastAclHardenTest\...`, itself created with no
    /// explicit security descriptor, so it inherits HKCU's default -- which
    /// grants the owning user broadly, but critically is NOT the
    /// SYSTEM+Administrators-only shape this fix requires) hands it,
    /// so it never becomes `D:P(...)`. This function exists so a reviewer
    /// can literally run it against the un-hardened code (comment out the
    /// `harden_key_acl` call in `write_value_to_key`) to see the assertion
    /// above fail -- see the worker report for the pasted failing output;
    /// not run as part of the normal suite (it would require hand-editing
    /// production code to observe the negative), kept here as documentation
    /// of exactly what "hardened" means and how the positive test can fail.
    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "manual revert-and-fail proof only; see the doc comment"]
    fn write_value_to_key_without_hardening_leaves_the_default_dacl_in_place() {
        use winreg::enums::HKEY_CURRENT_USER;

        let scratch_path = format!(
            r"Software\CivicCastAclHardenTest\without_hardening\{}",
            std::process::id()
        );
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(&scratch_path);
        let (key, _) = hkcu
            .create_subkey_with_flags(&scratch_path, KEY_READ | KEY_SET_VALUE)
            .expect("scratch key must be creatable");
        let sddl = registry_acl::read_dacl_sddl(key.raw_handle())
            .expect("DACL must be readable even when un-hardened");
        assert!(
            sddl.starts_with("D:P"),
            "this assertion is EXPECTED TO FAIL on an un-hardened key: {sddl:?}"
        );
        drop(key);
        let _ = hkcu.delete_subkey_all(&scratch_path);
    }
}

#[cfg(test)]
mod runtime_ownership_report_tests {
    use super::*;
    use crate::native_uninstall::{
        ArpProductRecord, OtherProductState, ProbeObservation, SelectorClaimAction,
        SelectorClaimOutcome, SelectorState, WslPresenceEvidence, WslProductVerdict,
    };

    fn refused_outcome() -> SelectorClaimOutcome {
        let evidence = WslPresenceEvidence {
            user_arp: OtherProductState::Unknown,
            machine_arp: OtherProductState::Unknown,
            distro_registration: OtherProductState::Absent,
            wsl_service: OtherProductState::Present,
            autostart: OtherProductState::Absent,
            native_transfer_marker: OtherProductState::Absent,
            observations: vec![
                ProbeObservation::settled(
                    "user-ARP",
                    "HKCU".to_string(),
                    "64-bit",
                    OtherProductState::Absent,
                ),
                ProbeObservation {
                    source: "user-ARP",
                    scope: r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001".to_string(),
                    view: "64-bit",
                    state: OtherProductState::Unknown,
                    error_kind: Some("PermissionDenied".to_string()),
                    raw_code: Some(5),
                    product: None,
                },
            ],
        };
        SelectorClaimOutcome {
            action: SelectorClaimAction::LeaveUnprovable,
            selector: SelectorState::Absent,
            detail: format!(
                "ActiveRuntime was left unchanged: observed selector Absent; WSL product \
                 evidence: {}; combined WSL product state Unknown.",
                evidence.explain()
            ),
            evidence: Some(evidence),
            verdict: Some(WslProductVerdict::Unknown),
            write_error: None,
        }
    }

    /// The corrected field report's box, run through the real orchestration
    /// so the report tests see the detail the CLI would actually write.
    fn field_report_outcome(
        autostart: OtherProductState,
        transfer_marker: OtherProductState,
    ) -> SelectorClaimOutcome {
        let mut present = ProbeObservation::settled(
            "user-ARP",
            "HKCU".to_string(),
            "64-bit",
            OtherProductState::Present,
        );
        present.product = Some(ArpProductRecord {
            user: "tester".to_string(),
            display_name: Some("CivicCast Installer".to_string()),
            display_version: Some("3.0.0-beta1".to_string()),
            publisher: Some("civiccast".to_string()),
            install_location: Some(
                r"D:\Profiles\tester\AppData\Local\CivicCast Installer".to_string(),
            ),
            uninstall_string: Some(
                r"D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe".to_string(),
            ),
        });
        let evidence = WslPresenceEvidence {
            user_arp: OtherProductState::Present,
            machine_arp: OtherProductState::Absent,
            distro_registration: OtherProductState::Absent,
            wsl_service: OtherProductState::Present,
            autostart,
            native_transfer_marker: transfer_marker,
            observations: vec![present],
        };
        crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Absent,
            || evidence.clone(),
            || Ok(()),
        )
    }

    /// Round 3: the real-Present observation line LEADS with the product
    /// (user, name, version, key, InstallLocation, UninstallString) so the
    /// dialog's "What setup observed:" names it, and maps to exit 87.
    #[test]
    fn a_present_refusal_line_leads_with_the_product_and_maps_to_exit_87() {
        let outcome = field_report_outcome(OtherProductState::Present, OtherProductState::Absent);
        assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert_eq!(refusal_exit_code(outcome.action), Some(87));
        let line = ownership_observation_line(&outcome);
        assert!(
            line.starts_with(
                r"Setup found another CivicCast product installed for user tester: CivicCast Installer 3.0.0-beta1 (registered at HKCU\...\Uninstall\CivicCast Installer; InstallLocation D:\Profiles\tester\AppData\Local\CivicCast Installer; UninstallString D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe); another CivicCast product present; left unchanged"
            ),
            "{line}"
        );
        assert!(line.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS);
        assert!(line.is_ascii());

        // With a real HKEY_USERS SID in the scope (the elevated per-machine
        // case) the whole lead -- through the UninstallString -- must still
        // survive the cap: that is what the dialog shows.
        let mut elevated = outcome.clone();
        let evidence = elevated.evidence.as_mut().unwrap();
        evidence.observations[0].scope = r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001".to_string();
        let line = ownership_observation_line(&elevated);
        let lead_end = line.find("uninstall.exe)").expect(&line) + "uninstall.exe)".len();
        assert!(lead_end <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS - 64, "{line}");
        assert!(line.starts_with(r"Setup found another CivicCast product installed for user tester: CivicCast Installer 3.0.0-beta1 (registered at HKU\S-1-5-21-1111111111-2222222222-3333333333-1001\...\Uninstall\CivicCast Installer; InstallLocation "), "{line}");

        let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.starts_with("# CivicCast (Native) setup: another CivicCast product is installed"));
        assert!(doc.contains("stopped (exit 87 / installer exit 135)"), "{doc}");
        for expected in [
            "  - DisplayName: CivicCast Installer\n",
            "  - DisplayVersion: 3.0.0-beta1\n",
            "  - Publisher: civiccast\n",
            r"  - InstallLocation: D:\Profiles\tester\AppData\Local\CivicCast Installer",
            r"  - UninstallString: D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe",
            r"registered at `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\CivicCast Installer` (64-bit view), for user `tester`",
            "autostart=Present, transfer-marker=Absent",
            "1. Uninstall 'CivicCast Installer 3.0.0-beta1' from Settings > Apps (or run `D:\\Profiles\\tester\\AppData\\Local\\CivicCast Installer\\uninstall.exe`), or",
            "`civiccast-runtime cutover-to-native`",
            "2. Run setup again.",
            "Do not edit `HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime` by hand for this case",
        ] {
            assert!(doc.contains(expected), "missing {expected:?} in:\n{doc}");
        }
        assert!(
            !doc.contains("New-ItemProperty"),
            "a real Present must not carry the registry-edit remedy:\n{doc}"
        );
        assert!(!doc.contains("permissions problem"));
    }

    /// Round 5 (delta review of round 4): a Present-product outcome built
    /// from an arbitrary evidence shape and run through the real
    /// orchestration, so the document tests see the detail the CLI writes.
    fn present_outcome(
        observation: ProbeObservation,
        user_arp: OtherProductState,
        machine_arp: OtherProductState,
        distro: OtherProductState,
        autostart: OtherProductState,
        transfer_marker: OtherProductState,
    ) -> SelectorClaimOutcome {
        present_outcome_from(
            vec![observation],
            user_arp,
            machine_arp,
            distro,
            autostart,
            transfer_marker,
        )
    }

    /// [`present_outcome`] over several observations (round 6: an entry
    /// registered in both WOW64 views).
    fn present_outcome_from(
        observations: Vec<ProbeObservation>,
        user_arp: OtherProductState,
        machine_arp: OtherProductState,
        distro: OtherProductState,
        autostart: OtherProductState,
        transfer_marker: OtherProductState,
    ) -> SelectorClaimOutcome {
        let evidence = WslPresenceEvidence {
            user_arp,
            machine_arp,
            distro_registration: distro,
            wsl_service: OtherProductState::Present,
            autostart,
            native_transfer_marker: transfer_marker,
            observations,
        };
        crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Absent,
            || evidence.clone(),
            || Ok(()),
        )
    }

    /// The field report's per-user ARP entry for an arbitrary account name
    /// and hive scope (`HKCU` or `HKU\<SID>`); the two paths carry the name.
    fn user_arp_observation(user: &str, scope: &str) -> ProbeObservation {
        let mut present = ProbeObservation::settled(
            "user-ARP",
            scope.to_string(),
            "64-bit",
            OtherProductState::Present,
        );
        present.product = Some(ArpProductRecord {
            user: user.to_string(),
            display_name: Some("CivicCast Installer".to_string()),
            display_version: Some("3.0.0-beta1".to_string()),
            publisher: Some("civiccast".to_string()),
            install_location: Some(format!(
                r"D:\Profiles\{user}\AppData\Local\CivicCast Installer"
            )),
            uninstall_string: Some(format!(
                r"D:\Profiles\{user}\AppData\Local\CivicCast Installer\uninstall.exe"
            )),
        });
        present
    }

    /// A per-machine (HKLM) ARP entry in the given WOW64 view, with or
    /// without an UninstallString -- the abandoned-key shape.
    fn machine_arp_observation(
        view: &'static str,
        uninstall_string: Option<&str>,
    ) -> ProbeObservation {
        let mut hklm = ProbeObservation::settled(
            "machine-ARP",
            "HKLM".to_string(),
            view,
            OtherProductState::Present,
        );
        hklm.product = Some(ArpProductRecord {
            user: "(per-machine)".to_string(),
            display_name: Some("CivicCast Installer".to_string()),
            display_version: Some("3.0.0-beta1".to_string()),
            uninstall_string: uninstall_string.map(str::to_string),
            ..ArpProductRecord::default()
        });
        hklm
    }

    /// Round 5 (delta review of round 4, MAJOR 1): the "What it means"
    /// paragraph names the inert condition(s) that actually failed, derived
    /// from the evidence. Round 4 asserted the per-machine cause for EVERY
    /// Present, so a station with a genuinely live distro was told its
    /// owner was logged out.
    #[test]
    fn the_present_document_names_the_inert_condition_that_actually_failed() {
        use OtherProductState::{Absent, Present, Unknown};
        const PER_MACHINE: &str = "found only per-machine";
        const DISTRO_PRESENT: &str = "a CivicCast distro is registered (distro-scan=present)";
        const AUTOSTART_PRESENT: &str =
            "the CivicCast Autostart Run entry is set (autostart=present)";
        const MARKER_ABSENT: &str =
            "no previous native install's hand-off marker is set (transfer-marker=absent)";
        /// The negative only an `Absent` per-user read supports.
        const HIVE_CARRIES_NOT: &str = "no signed-in account's hive carries it";
        /// MINOR 7 (round 6): the opener's pointer at the ARP record.
        const ARP_ENTRY_ABOVE: &str = "the Add/Remove Programs entry above";

        // A live product in the operator's own loaded hive.
        let live = present_outcome(
            user_arp_observation("tester", "HKCU"),
            Present,
            Absent,
            Present,
            Present,
            Absent,
        );
        assert_eq!(live.action, SelectorClaimAction::LeaveOtherProductPresent);
        let doc = ownership_recovery_document(&live, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(!doc.contains(PER_MACHINE), "{doc}");
        assert!(!doc.contains("not logged in"), "{doc}");
        assert!(doc.contains("What did not hold here:"), "{doc}");
        for expected in [DISTRO_PRESENT, AUTOSTART_PRESENT, MARKER_ABSENT] {
            assert!(doc.contains(expected), "missing {expected:?} in:\n{doc}");
        }

        // Only the autostart read was inconclusive: exactly that is named.
        let unknown_autostart = present_outcome(
            user_arp_observation("tester", "HKCU"),
            Present,
            Absent,
            Absent,
            Unknown,
            Present,
        );
        let doc = ownership_recovery_document(&unknown_autostart, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(
            doc.contains("the autostart read was inconclusive (autostart=unknown)"),
            "{doc}"
        );
        for absent in [PER_MACHINE, DISTRO_PRESENT, AUTOSTART_PRESENT, MARKER_ABSENT] {
            assert!(!doc.contains(absent), "{absent:?} wrongly named in:\n{doc}");
        }

        // Machine-ARP only, otherwise the inert shape: the per-machine
        // sentence, and none of the others.
        let machine_only = present_outcome(
            machine_arp_observation("64-bit", None),
            Absent,
            Present,
            Absent,
            Absent,
            Present,
        );
        let doc = ownership_recovery_document(&machine_only, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(PER_MACHINE), "{doc}");
        assert!(doc.contains(HIVE_CARRIES_NOT), "{doc}");
        for absent in [DISTRO_PRESENT, AUTOSTART_PRESENT, MARKER_ABSENT, "was inconclusive ("] {
            assert!(!doc.contains(absent), "{absent:?} wrongly named in:\n{doc}");
        }
        // MINOR 7 (round 6): the opener names the ARP entry only when one
        // is above it.
        assert!(doc.contains(ARP_ENTRY_ABOVE), "{doc}");

        // Round 6 (delta review of round 5, MAJOR 2a): presence established
        // from the distro scan alone -- no ARP entry in ANY hive. Round 5
        // gated the per-machine sentence and the `reg delete` remedy on
        // `user_arp != Present` only, so this document asserted a stale
        // HKLM registration two lines under `machine-ARP=Absent` and handed
        // the operator an elevated delete for a key it had just reported
        // absent. The cause is the live distro, and only that.
        let distro_only = present_outcome(
            ProbeObservation::settled(
                "distro-scan",
                "HKU (hive enumeration)".to_string(),
                "n/a",
                Present,
            ),
            Absent,
            Absent,
            Present,
            Absent,
            Present,
        );
        assert_eq!(distro_only.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert!(distro_only.evidence.as_ref().unwrap().present_product().is_none());
        let doc = ownership_recovery_document(&distro_only, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(DISTRO_PRESENT), "{doc}");
        for absent in [
            PER_MACHINE,
            HIVE_CARRIES_NOT,
            "registered per-machine",
            "reg delete",
            "Sign in as the account",
            ARP_ENTRY_ABOVE,
        ] {
            assert!(!doc.contains(absent), "{absent:?} wrongly asserted in:\n{doc}");
        }
        assert!(
            doc.contains(
                "no Add/Remove Programs entry was found in any hive (user-ARP=absent, \
                 machine-ARP=absent)"
            ),
            "{doc}"
        );
        // MINOR 7: with no ARP entry above, the opener points at the read
        // that established presence instead.
        assert!(doc.contains("a CivicCast distro is registered -- the `distro-scan=Present` read above"), "{doc}");

        // Round 6 (MAJOR 2b): the per-user probe FAILED (user-ARP=unknown,
        // the state this PR was opened over) and HKLM carries the entry.
        // "No signed-in account's hive carries it" is not something setup
        // knows here; the document says the probe could not read it.
        let unknown_user = present_outcome(
            machine_arp_observation("64-bit", None),
            Unknown,
            Present,
            Absent,
            Absent,
            Present,
        );
        assert_eq!(unknown_user.action, SelectorClaimAction::LeaveOtherProductPresent);
        let doc = ownership_recovery_document(&unknown_user, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(!doc.contains(HIVE_CARRIES_NOT), "absence asserted from a failed probe:\n{doc}");
        assert!(!doc.contains(PER_MACHINE), "{doc}");
        assert!(
            doc.contains(
                "the entry was found per-machine (HKLM) and no signed-in account's hive could \
                 be read (user-ARP=unknown), so setup could not look for the owner's distro or \
                 autostart state"
            ),
            "{doc}"
        );
        assert!(
            doc.contains("no signed-in account's hive could be read (user-ARP=unknown)"),
            "{doc}"
        );
        // The two escape hatches still reach this case -- the key IS there.
        assert!(doc.contains("reg delete"), "{doc}");
        assert!(doc.contains("Sign in as the account"), "{doc}");

        // Round 6: the remaining (user-ARP, machine-ARP) shapes that reach a
        // Present verdict each get the sentence their evidence supports --
        // never a negative the probe did not establish.
        let distro_scan = || {
            ProbeObservation::settled(
                "distro-scan",
                "HKU (hive enumeration)".to_string(),
                "n/a",
                Present,
            )
        };
        for (user, machine, expected) in [
            (
                Absent,
                Unknown,
                "no signed-in account's hive carries the Add/Remove Programs entry \
                 (user-ARP=absent) and the per-machine read was inconclusive \
                 (machine-ARP=unknown)",
            ),
            (
                Unknown,
                Absent,
                "the per-user Add/Remove Programs probe could not read a hive \
                 (user-ARP=unknown) and no per-machine entry exists (machine-ARP=absent)",
            ),
            (
                Unknown,
                Unknown,
                "neither Add/Remove Programs probe could be read (user-ARP=unknown, \
                 machine-ARP=unknown)",
            ),
        ] {
            let outcome = present_outcome(distro_scan(), user, machine, Present, Absent, Present);
            assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
            let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
            assert!(doc.contains(expected), "user-ARP={user:?} machine-ARP={machine:?}:\n{doc}");
            for absent in [PER_MACHINE, HIVE_CARRIES_NOT, "reg delete", ARP_ENTRY_ABOVE] {
                assert!(!doc.contains(absent), "{absent:?} wrongly asserted in:\n{doc}");
            }
        }
    }

    /// Round 5 (delta review of round 4, MAJOR 2): the refusal class round
    /// 4 created -- a per-machine ARP leftover, post-cutover, with a dead or
    /// missing UninstallString -- has no remedy that reaches it unless the
    /// document offers the two that do: sign in as the owning account and
    /// re-run, or delete the stale HKLM key (exact path and view).
    #[test]
    fn a_machine_arp_only_refusal_offers_the_two_remedies_that_reach_it() {
        use crate::native_uninstall::WSL_ARP_KEY;
        use OtherProductState::{Absent, Present};
        const SIGN_IN: &str = "Sign in as the account that installed that product";
        // MINOR 4 (round 6): the command is derived from the probe's own
        // key constant, so renaming the key cannot leave the document
        // handing out the old path.
        let delete_64 = format!("reg delete \"HKLM\\{WSL_ARP_KEY}\" /f /reg:64");
        let delete_32 = format!("reg delete \"HKLM\\{WSL_ARP_KEY}\" /f /reg:32");
        assert!(WSL_ARP_KEY.ends_with(r"\Uninstall\CivicCast Installer"));

        let machine_only = present_outcome(
            machine_arp_observation("64-bit", None),
            Absent,
            Present,
            Absent,
            Absent,
            Present,
        );
        assert_eq!(machine_only.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert_eq!(refusal_exit_code(machine_only.action), Some(87));
        let doc = ownership_recovery_document(&machine_only, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(SIGN_IN), "{doc}");
        assert!(doc.contains(&delete_64), "{doc}");
        assert!(!doc.contains(&delete_32), "{doc}");
        // The round-3 remedies and the round-3 prohibition survive around them.
        assert!(doc.contains("1. Uninstall 'CivicCast Installer 3.0.0-beta1' from Settings > Apps, or"), "{doc}");
        assert!(doc.contains("`civiccast-runtime cutover-to-native`"), "{doc}");
        assert!(doc.contains("Do not edit `HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime` by hand for this case"), "{doc}");
        assert!(!doc.contains("New-ItemProperty"), "{doc}");
        // The remedies come before the prohibition, as steps.
        assert!(doc.find(SIGN_IN).unwrap() < doc.find("Do not edit").unwrap(), "{doc}");
        assert!(doc.find(&delete_64).unwrap() < doc.find("Do not edit").unwrap(), "{doc}");

        // Found in the 32-bit view: the command names that view.
        let wow = present_outcome(
            machine_arp_observation("32-bit", Some(r"C:\old\uninstall.exe")),
            Absent,
            Present,
            Absent,
            Absent,
            Present,
        );
        let doc = ownership_recovery_document(&wow, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(&delete_32), "{doc}");
        assert!(!doc.contains(&delete_64), "{doc}");
        assert!(doc.contains(r"(or run `C:\old\uninstall.exe`)"), "{doc}");

        // A per-user Present (the owner hive IS loaded) keeps the round-3
        // remedy alone: neither escape hatch is offered there.
        let per_user = field_report_outcome(Present, Absent);
        let doc = ownership_recovery_document(&per_user, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(!doc.contains("reg delete"), "{doc}");
        assert!(!doc.contains(SIGN_IN), "{doc}");

        // MINOR 6 (round 6): the entry registered in BOTH WOW64 views gets
        // one command per view in the same document, instead of the first
        // view now and the second after another refused run.
        let both_views = present_outcome_from(
            vec![
                machine_arp_observation("64-bit", None),
                machine_arp_observation("32-bit", None),
            ],
            Absent,
            Present,
            Absent,
            Absent,
            Present,
        );
        let doc = ownership_recovery_document(&both_views, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(&delete_64), "{doc}");
        assert!(doc.contains(&delete_32), "{doc}");
        assert!(doc.contains("both WOW64 views"), "{doc}");
        assert!(doc.find(&delete_32).unwrap() < doc.find("Do not edit").unwrap(), "{doc}");
    }

    /// Round 6 (delta review of round 5, MINOR 3): the `; ` boundary cut is
    /// the actual "never half a path" guarantee once the lead overruns the
    /// truncation point even WITHOUT InstallLocation (a ~49-char account
    /// name with the fixture's paths; sooner with a deep custom
    /// InstallLocation). Nothing pinned it: replacing the cut with a raw
    /// character cut at 372 left every test green. This fixture overruns by
    /// construction and asserts the text before the marker ends on the
    /// complete key field -- the UninstallString is removed whole, not
    /// halved.
    #[test]
    fn a_lead_that_overruns_without_install_location_is_cut_at_a_field_boundary() {
        use OtherProductState::{Absent, Present};
        const SID: &str = r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001";
        let user = "a".repeat(60);
        let outcome = present_outcome(
            user_arp_observation(&user, SID),
            Present,
            Absent,
            Present,
            Present,
            Absent,
        );
        assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
        let evidence = outcome.evidence.as_ref().unwrap();
        // The fixture must overrun even with InstallLocation dropped, or it
        // pins nothing.
        let without_location = evidence.describe_present_product_with(false);
        assert!(
            without_location.chars().count() > OWNERSHIP_OBSERVATION_LINE_KEEP,
            "fixture too short to reach the boundary cut: {} chars",
            without_location.chars().count()
        );
        let line = ownership_observation_line(&outcome);
        assert!(line.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS, "{line}");
        assert!(line.ends_with(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER), "{line}");
        let before_marker = line
            .strip_suffix(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER)
            .unwrap();
        // The cut landed on the "; " ahead of the UninstallString field: the
        // key is whole and no path fragment follows it.
        assert!(
            before_marker.ends_with(&format!("{SID}\\...\\Uninstall\\CivicCast Installer")),
            "cut did not land on a field boundary: {line}"
        );
        assert!(!before_marker.contains("UninstallString"), "{line}");
        assert!(!before_marker.contains(r"D:\Profiles"), "{line}");
        assert!(before_marker.chars().count() < OWNERSHIP_OBSERVATION_LINE_KEEP, "{line}");
        // The recovery document carries both paths whole.
        let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains(&format!(
            r"  - UninstallString: D:\Profiles\{user}\AppData\Local\CivicCast Installer\uninstall.exe"
        )));
    }

    /// Round 6 (delta review of round 5, MINOR 5): a non-refusal never gets
    /// a recovery document. Round 5's `unwrap_or(85 / 127)` fallbacks meant
    /// a future non-refusal action reaching the generator would print a
    /// refusal's exit codes over a claim that succeeded.
    #[test]
    fn a_non_refusal_never_gets_a_recovery_document() {
        use OtherProductState::{Absent, Present};
        let inert_claim = field_report_outcome(Absent, Present);
        assert_eq!(inert_claim.action, SelectorClaimAction::ClaimNative);
        let already_native = crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Native,
            || unreachable!(),
            || Ok(()),
        );
        assert_eq!(already_native.action, SelectorClaimAction::AlreadyNative);
        let wsl = crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Wsl,
            || unreachable!(),
            || Ok(()),
        );
        assert_eq!(wsl.action, SelectorClaimAction::LeaveWslOwnership);
        for outcome in [inert_claim, already_native, wsl] {
            assert!(!outcome.action.is_refusal());
            assert!(
                ownership_recovery_document(&outcome, "1.0.0-beta.5.1").is_none(),
                "{:?} was given a recovery document",
                outcome.action
            );
        }
    }

    /// Round 5 (pre-existing, now load-bearing): the exit-87 lead is built
    /// field by field and drops InstallLocation before the cap can cut a
    /// path in half. Every earlier fixture used a 6-char account name; a
    /// 16-char name (which appears three times in the lead) with a
    /// real-length SID measured 381 chars against a truncation point of
    /// 420 - 48 = 372 and was cut mid-UninstallString.
    #[test]
    fn a_long_username_never_cuts_the_exit_87_lead_mid_path() {
        use OtherProductState::{Absent, Present};
        const SID: &str = r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001";
        const LEAD_END: &str = "uninstall.exe)";
        for user in ["station-operator", &"a".repeat(32)] {
            assert!(user.len() >= 16);
            let outcome = present_outcome(
                user_arp_observation(user, SID),
                Present,
                Absent,
                Present,
                Present,
                Absent,
            );
            assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
            let line = ownership_observation_line(&outcome);
            assert!(line.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS, "{line}");
            let lead_end = line
                .find(LEAD_END)
                .unwrap_or_else(|| panic!("lead cut mid-path for user {user}: {line}"))
                + LEAD_END.len();
            assert!(
                line.starts_with(&format!(
                    "Setup found another CivicCast product installed for user {user}: \
                     CivicCast Installer 3.0.0-beta1 (registered at {SID}\\...\\Uninstall\\\
                     CivicCast Installer; "
                )),
                "{line}"
            );
            assert!(
                line[..lead_end].ends_with(&format!(
                    r"UninstallString D:\Profiles\{user}\AppData\Local\CivicCast Installer\uninstall.exe)"
                )),
                "{line}"
            );
            assert!(
                line[lead_end..].starts_with("; another CivicCast product present"),
                "{line}"
            );
            if line.contains(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER) {
                // Whatever was cut was cut at a field boundary.
                let before_marker = line.split(OWNERSHIP_OBSERVATION_TRUNCATION_MARKER).next().unwrap();
                assert!(!before_marker.ends_with(' '), "{line}");
                assert!(!before_marker.ends_with(';'), "{line}");
            }
            // The recovery document still has every field whole.
            let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
            assert!(doc.contains(&format!(
                r"  - InstallLocation: D:\Profiles\{user}\AppData\Local\CivicCast Installer"
            )));
        }

        // A short name still gets InstallLocation in the lead: nothing is
        // dropped that fits.
        let short = present_outcome(
            user_arp_observation("tester", SID),
            Present,
            Absent,
            Present,
            Present,
            Absent,
        );
        let line = ownership_observation_line(&short);
        assert!(
            line.contains(r"; InstallLocation D:\Profiles\tester\AppData\Local\CivicCast Installer; UninstallString "),
            "{line}"
        );
    }

    /// Round 4/5: the refusals map to DIFFERENT installer exit codes (the
    /// NSIS defines the d4 arms abort with) and, load-bearing, the recovery
    /// DOCUMENT of every refusal names the mapped code -- a literal put back
    /// into the format string fails here, not only in the policy test. The
    /// Python policy test pins the constants to the .nsh defines.
    #[test]
    fn each_refusal_maps_to_its_own_installer_exit_code() {
        assert_eq!(
            installer_exit_code_for(SelectorClaimAction::LeaveUnprovable),
            Some(SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE)
        );
        assert_eq!(
            installer_exit_code_for(SelectorClaimAction::LeaveUnreadable),
            Some(SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE)
        );
        assert_eq!(
            installer_exit_code_for(SelectorClaimAction::LeaveOtherProductPresent),
            Some(OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE)
        );
        for action in [
            SelectorClaimAction::ClaimNative,
            SelectorClaimAction::AlreadyNative,
            SelectorClaimAction::LeaveWslOwnership,
        ] {
            assert_eq!(installer_exit_code_for(action), None, "{action:?}");
            assert_eq!(refusal_exit_code(action), None, "{action:?}");
        }
        assert_ne!(
            SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE,
            OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE
        );
        assert!(
            !(83..=87).contains(&OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE),
            "an installer code must stay out of the CLI band"
        );

        let unreadable = crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Unreadable,
            || unreachable!(),
            || Ok(()),
        );
        let present = field_report_outcome(OtherProductState::Present, OtherProductState::Absent);
        let mut seen = std::collections::BTreeSet::new();
        for outcome in [refused_outcome(), unreadable, present] {
            assert!(outcome.action.is_refusal());
            let cli = refusal_exit_code(outcome.action).unwrap();
            let installer = installer_exit_code_for(outcome.action).unwrap();
            seen.insert(installer);
            let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
            assert!(
                doc.contains(&format!("stopped (exit {cli} / installer exit {installer})")),
                "{:?}: {doc}",
                outcome.action
            );
        }
        assert_eq!(seen.len(), 2, "127 and 135 both reached a document");
    }

    /// Round 3: the inert-leftover claim's line carries the WARNING (the
    /// product, the user, the uninstall.exe) right after the verdict, so
    /// install-progress.log cannot read it as a clean claim; it is not a
    /// refusal and writes no recovery document.
    #[test]
    fn an_inert_leftover_claim_line_carries_the_warning() {
        let outcome = field_report_outcome(OtherProductState::Absent, OtherProductState::Present);
        assert_eq!(outcome.action, SelectorClaimAction::ClaimNative);
        assert_eq!(outcome.verdict, Some(WslProductVerdict::PresentInert));
        assert_eq!(refusal_exit_code(outcome.action), None);
        let line = ownership_observation_line(&outcome);
        assert!(
            line.starts_with(
                r"ownership claimed (ActiveRuntime=native); WARNING: an old CivicCast Installer 3.0.0-beta1 registration remains for user tester with no distro or autostart; claimed native. Remove the leftover via Apps & Features (uninstall.exe at D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe); selector Absent"
            ),
            "{line}"
        );
        assert!(line.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS);
    }

    /// Round 3: an unreadable selector is its own refusal (exit 85, its own
    /// text) and the Unknown text no longer guesses at permissions.
    #[test]
    fn the_unreadable_and_unknown_refusals_keep_exit_85_and_the_registry_remedy() {
        let unreadable = crate::native_uninstall::claim_install_selector_with(
            || SelectorState::Unreadable,
            || unreachable!(),
            || Ok(()),
        );
        assert_eq!(unreadable.action, SelectorClaimAction::LeaveUnreadable);
        assert_eq!(refusal_exit_code(unreadable.action), Some(85));
        let line = ownership_observation_line(&unreadable);
        assert!(line.starts_with("HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime exists but could not be read"), "{line}");
        let doc = ownership_recovery_document(&unreadable, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains("stopped (exit 85 / installer exit 127)"));
        assert!(doc.contains("New-ItemProperty"));
        assert!(doc.contains("an `Unreadable` selector is a value that exists but is not `native`/`wsl`"));

        let unknown = refused_outcome();
        assert_eq!(refusal_exit_code(unknown.action), Some(85));
        let doc = ownership_recovery_document(&unknown, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains("New-ItemProperty"));
        assert!(doc.contains("autostart=Absent, transfer-marker=Absent"));
        assert!(!doc.contains("permissions problem"));
    }

    #[test]
    fn the_report_root_is_the_python_engines_provision_directory() {
        let root = ownership_report_root_from(Path::new(r"C:\ProgramData"));
        assert_eq!(root, PathBuf::from(r"C:\ProgramData\CivicCast\provision"));
        assert_eq!(
            ownership_observation_path(&root),
            PathBuf::from(r"C:\ProgramData\CivicCast\provision\ownership-observation.txt")
        );
        assert_eq!(
            ownership_recovery_document_path(&root),
            PathBuf::from(r"C:\ProgramData\CivicCast\provision\OWNERSHIP-RECOVERY.md")
        );
    }

    /// The line NSIS reads back is ONE line, ASCII, carries the per-hive
    /// observation, and never exceeds the cap the dialog can embed.
    #[test]
    fn the_observation_line_is_single_line_ascii_and_bounded() {
        let outcome = refused_outcome();
        let line = ownership_observation_line(&outcome);
        assert!(!line.contains('\n') && !line.contains('\r'));
        assert!(line.is_ascii());
        assert!(
            line.starts_with(
                "ownership NOT established; left unchanged; selector Absent; user-ARP=unknown ["
            ),
            "{line}"
        );
        assert!(line.contains("PermissionDenied, os error 5"), "{line}");
        assert!(line.contains("S-1-5-21-1111111111-2222222222-3333333333-1001"));
        assert!(
            line.contains("machine-ARP=unknown, distro-scan=absent, wsl-service=present"),
            "{line}"
        );
        assert!(line.chars().count() <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS);

        let mut claimed = outcome.clone();
        claimed.action = SelectorClaimAction::ClaimNative;
        assert!(ownership_observation_line(&claimed)
            .starts_with("ownership claimed (ActiveRuntime=native); selector Absent; "));
        let mut failed = claimed.clone();
        failed.write_error = Some("access denied".to_string());
        let failed_line = ownership_observation_line(&failed);
        assert!(failed_line.starts_with("ownership claim WRITE FAILED; "));
        assert!(
            failed_line.ends_with("; write error: access denied"),
            "{failed_line}"
        );

        let settled = SelectorClaimOutcome {
            action: SelectorClaimAction::AlreadyNative,
            selector: SelectorState::Native,
            evidence: None,
            verdict: None,
            detail: "ActiveRuntime already reads \"native\"; this install left it unchanged."
                .to_string(),
            write_error: None,
        };
        assert_eq!(ownership_observation_line(&settled), settled.detail);

        let mut long = outcome.clone();
        long.evidence = None;
        long.detail = format!("{}\r\n{}", "x".repeat(600), "tail \u{2014} unicode");
        let cut = ownership_observation_line(&long);
        assert_eq!(cut.chars().count(), OWNERSHIP_OBSERVATION_LINE_MAX_CHARS);
        assert!(cut.ends_with("(full observation in OWNERSHIP-RECOVERY.md)"));
        assert!(cut.is_ascii());
    }

    /// The recovery document names every individual read, the exact remedy
    /// command, the fact that the database configuration was not touched,
    /// and where else the observation lives.
    #[test]
    fn the_recovery_document_carries_observations_remedy_and_untouched_state() {
        let doc = ownership_recovery_document(&refused_outcome(), "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains("1.0.0-beta.5.1"));
        assert!(doc.contains("BEFORE provisioning"));
        assert!(doc.contains(
            "postgresql.conf, pg_hba.conf and your database credential were not touched"
        ));
        assert!(!doc.contains("nothing under"));
        assert!(!doc.contains("recordings and settings are exactly"));
        assert!(doc.contains(
            r"- user-ARP HKU\S-1-5-21-1111111111-2222222222-3333333333-1001 (64-bit view): unknown [PermissionDenied, os error 5]"
        ));
        assert!(doc.contains("- user-ARP HKCU (64-bit view): absent"));
        assert!(doc.contains(
            "New-ItemProperty -Path 'HKLM:\\SOFTWARE\\CivicCast' -Name 'ActiveRuntime' -PropertyType String -Value 'native' -Force"
        ));
        assert!(doc.contains("install-progress.log"));
        assert!(doc.contains("step d4-provision: runtime ownership:"));
        assert!(doc.contains("Follow-up (not in this release): a setup wizard page"));
    }

    #[test]
    fn a_selector_settled_outcome_documents_that_no_probe_ran() {
        let outcome = SelectorClaimOutcome {
            action: SelectorClaimAction::LeaveUnreadable,
            selector: SelectorState::Unreadable,
            evidence: None,
            verdict: None,
            detail: "ActiveRuntime could not be read.".to_string(),
            write_error: None,
        };
        let doc = ownership_recovery_document(&outcome, "1.0.0-beta.5.1").expect("a refusal writes a document");
        assert!(doc.contains("read as: Unreadable"));
        assert!(doc.contains("The WSL product was not probed"));
    }
}
