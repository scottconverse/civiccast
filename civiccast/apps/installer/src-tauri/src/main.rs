// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod acquisition_catalog;
mod acquisition_state;
mod component_acquisition;
mod hardware_inventory;
mod native_activation;
mod native_distribution;
mod native_install_verify;
mod native_pack_staging;
mod native_packs;
mod native_repair;
mod native_service_registration;
mod native_uninstall;
mod webview_runtime;

use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::Manager;

const OPERATOR_CONSOLE_URL: &str = "http://127.0.0.1:8000/operator/";
const RESIDENT_PORTAL_URL: &str = "http://127.0.0.1:8000/";
const SERVICE_URL: &str = "http://127.0.0.1:8000";
const SERVICE_HEALTH_ADDR: &str = "127.0.0.1:8000";
const RUNTIME_HOST_MUTEX_ADDR: &str = "127.0.0.1:38474";
const CIVICCAST_VERSION: &str = "1.0.0-rc.6";
// Must match the reviewed runtime pack, not the older model acquisition version.
const NATIVE_OLLAMA_VERSION: &str = "0.40.13";
const INSTALLER_SHUTDOWN_MARKER: &str = "shutdown-request";


fn is_runtime_bootstrap_lane(lane_id: &str) -> bool {
    matches!(
        lane_id,
        "runtime" | "ffmpeg" | "storage" | "service" | "dashboard"
    )
}

fn installer_state_root() -> Result<PathBuf, String> {
    #[cfg(target_os = "windows")]
    {
        // Windows package identity can redirect every AppData path, even when
        // it was resolved through a known-folder API or written as an absolute
        // path. Keep installer state directly under the current user's profile,
        // which remains stable whether CivicCast is opened directly or by a
        // packaged desktop automation host.
        return std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .ok_or_else(|| "USERPROFILE is not set.".to_string())
            .map(|profile| profile.join(".civiccast"));
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                std::env::var_os("HOME")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join(".local")
                    .join("state")
            })
            .join("civiccast"))
    }
}

fn installer_state_path() -> Result<PathBuf, String> {
    Ok(installer_state_root()?.join("installer-state.json"))
}

/// The native runtime host's own diagnostic log (`runtime_host_log`'s
/// output). The retired WSL product's headless-bootstrap and WSL2/Ubuntu
/// bootstrap logs used to be candidates here too, but both those scripts
/// were removed with the WSL lane, and this is now the only per-user-root
/// log `open_installer_log` / `newest_installer_log_path` has to serve. See
/// [`installer_progress_log_path`] for the OTHER log this command serves --
/// the elevated NSIS installer's own step-by-step transcript, which lives
/// under a different (per-machine) root entirely.
fn installer_log_candidates(root: &Path) -> [PathBuf; 1] {
    [root.join("runtime-host.log")]
}

/// Where the ELEVATED NSIS installer writes its own step-by-step transcript
/// (`nsis-hooks-bootstrap.nsh`'s `CIVICCAST_STEP`/`CIVICCAST_FAIL` macros,
/// which `FileOpen $2 "$COMMONPROGRAMDATA\CivicCast\install-progress.log" a`
/// opens directly): `<program_data_root>\CivicCast\install-progress.log`,
/// the SAME per-machine writable root [`acquisition_download_root_from`]
/// already derives for the GUI's own component downloads. Pure aside from
/// the caller's own root resolution -- mirrors that function's `_from`
/// split so the mapping is unit-testable without touching `%PROGRAMDATA%`.
///
/// This is the log the download screen's own copy means by "installer log"
/// (bug fix, field report 2026-08-28, candidate 9d4477b): the elevated
/// install phase that writes it runs and completes BEFORE the GUI's
/// acquisition/download screen ever exists, while `runtime-host.log`
/// (`installer_log_candidates`'s only prior candidate) is written by the
/// native service, which has not started yet at that point -- so on a fresh
/// install `newest_installer_log_path` previously had nothing to find and
/// `open_installer_log` failed every single time the button that told the
/// operator to use it was actually visible.
fn installer_progress_log_path_from(program_data_root: &Path) -> PathBuf {
    acquisition_download_root_from(program_data_root).join("install-progress.log")
}

fn installer_progress_log_path() -> PathBuf {
    let program_data = std::env::var("PROGRAMDATA")
        .ok()
        .map(PathBuf::from)
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    installer_progress_log_path_from(&program_data)
}

/// Picks whichever candidate exists on disk AND was modified most recently
/// -- pure aside from the two filesystem reads per candidate (`is_file`,
/// `metadata().modified()`), factored out so [`newest_installer_log_path`]'s
/// selection logic is unit-testable against real (temp-directory) files
/// without needing to fake `%PROGRAMDATA%`/`%USERPROFILE%` themselves.
fn newest_existing_log_path(candidates: &[PathBuf]) -> Result<PathBuf, String> {
    candidates
        .iter()
        .filter(|path| path.is_file())
        .max_by_key(|path| {
            fs::metadata(path)
                .and_then(|metadata| metadata.modified())
                .ok()
        })
        .cloned()
        .ok_or_else(|| {
            let checked: Vec<String> = candidates.iter().map(|path| path.display().to_string()).collect();
            format!(
                "No CivicCast installer log exists yet. Checked: {}.",
                checked.join(", ")
            )
        })
}

/// The two independently-written logs an operator's "Open installer log"
/// click might mean, newest-first: the elevated NSIS installer's own
/// step-by-step transcript ([`installer_progress_log_path`], which exists
/// as soon as the elevated install phase ran) and the native runtime
/// host's diagnostic log (`installer_log_candidates`, which only exists
/// once the native service has actually started). Missing an
/// `installer_state_root()` (e.g. `USERPROFILE` unset) drops the second
/// candidate rather than failing the whole lookup -- the first is always
/// resolvable and is usually the one that matters on a fresh install.
fn newest_installer_log_path() -> Result<PathBuf, String> {
    let mut candidates = vec![installer_progress_log_path()];
    if let Ok(root) = installer_state_root() {
        candidates.extend(installer_log_candidates(&root));
    }
    newest_existing_log_path(&candidates)
}

fn installer_shutdown_marker_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            push_unique_path(&mut paths, parent.join(INSTALLER_SHUTDOWN_MARKER));
        }
    }

    #[cfg(target_os = "windows")]
    {
        if let Ok(root) = installer_state_root() {
            push_unique_path(&mut paths, root.join(INSTALLER_SHUTDOWN_MARKER));
        }
    }
    paths
}

fn remove_stale_shutdown_markers() {
    for path in installer_shutdown_marker_paths() {
        if path.exists() {
            let _ = fs::remove_file(path);
        }
    }
}

fn launch_shutdown_marker_watcher() {
    thread::spawn(|| loop {
        if installer_shutdown_marker_paths()
            .iter()
            .any(|path| path.exists())
        {
            std::process::exit(0);
        }
        thread::sleep(Duration::from_millis(250));
    });
}

fn push_unique_path(paths: &mut Vec<PathBuf>, path: PathBuf) {
    if !paths.iter().any(|candidate| candidate == &path) {
        paths.push(path);
    }
}

fn current_user_installer_state_candidate_paths(
    primary: PathBuf,
    user_profile: Option<PathBuf>,
) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    push_unique_path(&mut paths, primary);

    #[cfg(target_os = "windows")]
    if let Some(user_profile) = user_profile {
        push_unique_path(
            &mut paths,
            user_profile
                .join("AppData")
                .join("Local")
                .join("CivicCast")
                .join("installer-state.json"),
        );
    }

    paths
}

fn installer_state_candidate_paths() -> Result<Vec<PathBuf>, String> {
    let primary = installer_state_path()?;

    #[cfg(target_os = "windows")]
    return Ok(current_user_installer_state_candidate_paths(
        primary,
        std::env::var_os("USERPROFILE").map(PathBuf::from),
    ));

    #[cfg(not(target_os = "windows"))]
    return Ok(current_user_installer_state_candidate_paths(primary, None));
}

fn select_installer_state_candidate(
    primary: PathBuf,
    primary_exists: bool,
    mut existing: Vec<(SystemTime, PathBuf)>,
) -> Option<PathBuf> {
    if primary_exists {
        return Some(primary);
    }
    existing.sort_by(|left, right| right.0.cmp(&left.0));
    existing.into_iter().map(|(_, path)| path).next()
}

fn newest_existing_installer_state_path() -> Result<Option<PathBuf>, String> {
    let candidates = installer_state_candidate_paths()?;
    let Some(primary) = candidates.first().cloned() else {
        return Ok(None);
    };
    let primary_exists = primary.is_file();
    let existing: Vec<(SystemTime, PathBuf)> = candidates
        .into_iter()
        .skip(1)
        .filter_map(|path| {
            let modified = fs::metadata(&path).ok()?.modified().unwrap_or(UNIX_EPOCH);
            Some((modified, path))
        })
        .collect();
    Ok(select_installer_state_candidate(
        primary,
        primary_exists,
        existing,
    ))
}


fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

fn normalize_installer_state_text(raw: String) -> String {
    raw.strip_prefix('\u{feff}')
        .map(str::to_owned)
        .unwrap_or(raw)
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}





fn write_installer_state(
    lane_id: &str,
    status: &str,
    message: &str,
    reboot_required: bool,
) -> Result<(), String> {
    write_installer_state_with_operator_url(
        lane_id,
        status,
        message,
        reboot_required,
        OPERATOR_CONSOLE_URL,
    )
}

fn write_installer_state_with_operator_url(
    lane_id: &str,
    status: &str,
    message: &str,
    reboot_required: bool,
    operator_console_url: &str,
) -> Result<(), String> {
    let path = installer_state_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Could not create installer state directory: {error}"))?;
    }
    // The component-download store (acquisition_state.rs) is written into by
    // the engine's ProgressObserver independently of every lane-transition
    // call site below -- splicing its current snapshot in here, on every
    // installer-state write, means callers throughout this file never need
    // to know or care about acquisition progress to keep it current. `None`
    // (nothing has ever been touched) omits the field entirely, matching
    // InstallerProgress.acquisition's documented "absence means no
    // acquisition activity" contract in src/types.ts.
    let acquisition_field = acquisition_state::snapshot_json()
        .map(|json| format!(",\n  \"acquisition\": {json}"))
        .unwrap_or_default();
    let payload = format!(
        "{{\n  \"schema_version\": 1,\n  \"current_lane_id\": \"{}\",\n  \"status\": \"{}\",\n  \"message\": \"{}\",\n  \"reboot_required\": {},\n  \"updated_at_unix\": {},\n  \"service_url\": \"{}\",\n  \"operator_console_url\": \"{}\",\n  \"resident_portal_url\": \"{}\"{}\n}}\n",
        json_escape(lane_id),
        json_escape(status),
        json_escape(message),
        if reboot_required { "true" } else { "false" },
        unix_timestamp(),
        SERVICE_URL,
        json_escape(operator_console_url),
        RESIDENT_PORTAL_URL,
        acquisition_field
    );
    fs::write(&path, payload)
        .map_err(|error| format!("Could not write installer state file: {error}"))
}

fn health_response_is_ok(
    response: &str,
    expected_instance_id: Option<&str>,
    expected_runtime_build_id: Option<&str>,
) -> bool {
    if !(response.starts_with("HTTP/1.1 200 ") || response.starts_with("HTTP/1.0 200 ")) {
        return false;
    }
    let body = response
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or_default();
    let compact: String = body
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    if !compact.contains(&format!("\"version\":\"{CIVICCAST_VERSION}\"")) {
        return false;
    }
    // <installer-path-audit MA-07> 200 + a matching version string is
    // LIVENESS, not readiness -- civiccast/app.py's own /health docstring
    // says so ("always 200 while the process answers, in every schema
    // state"). PR #143 fixed exactly this shape in the Gate A harness and
    // left it in the product: this probe is what decides whether the
    // installer's lane is "ready", and it would declare a station ready over
    // a schema-behind or schema-ahead database whose staff endpoints 500.
    // Both fields have been unconditional in the body since PR #143.
    if !compact.contains("\"status\":\"healthy\"") {
        return false;
    }
    if !compact.contains("\"schema\":\"current\"") {
        return false;
    }
    if let Some(expected) = expected_instance_id {
        if !compact.contains(&format!("\"bootstrap_instance_id\":\"{expected}\"")) {
            return false;
        }
    }
    if let Some(expected) = expected_runtime_build_id {
        if !compact.contains(&format!("\"runtime_build_id\":\"{expected}\"")) {
            return false;
        }
    }
    true
}

fn service_health_reachable_once(
    expected_instance_id: Option<&str>,
    expected_runtime_build_id: Option<&str>,
) -> bool {
    let Ok(address) = SERVICE_HEALTH_ADDR.parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_secs(2)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:8000\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && health_response_is_ok(&response, expected_instance_id, expected_runtime_build_id)
}

/// True when the persisted installer state already reports the runtime ready.
fn installer_state_is_ready(raw: &str) -> bool {
    raw.split_whitespace()
        .collect::<String>()
        .contains("\"status\":\"ready\"")
}

/// True when the persisted state is waiting on a real Windows restart.
fn installer_state_reboot_required(raw: &str) -> bool {
    raw.split_whitespace()
        .collect::<String>()
        .contains("\"reboot_required\":true")
}

/// Read a top-level string field out of the persisted state JSON.
fn installer_state_string_field(raw: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let key_at = raw.find(&needle)?;
    let after = &raw[key_at + needle.len()..];
    let colon = after.find(':')?;
    let rest = after[colon + 1..].trim_start();
    let rest = rest.strip_prefix('"')?;
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

fn bundled_runtime_build_id(resources_root: &Path) -> Result<String, String> {
    let manifest_path = resources_root.join("bootstrap-manifest.json");
    let raw = fs::read_to_string(&manifest_path).map_err(|error| {
        format!(
            "The installer package is missing its runtime identity manifest {}: {error}",
            manifest_path.display()
        )
    })?;
    let build_id = installer_state_string_field(&raw, "runtime_build_id")
        .filter(|value| value.len() == 64 && value.chars().all(|ch| ch.is_ascii_hexdigit()))
        .ok_or_else(|| {
            format!(
                "The installer package has an invalid runtime build identity in {}.",
                manifest_path.display()
            )
        })?;
    Ok(build_id.to_ascii_lowercase())
}

fn runtime_build_id_from_manifest_path(manifest_path: PathBuf) -> Option<String> {
    manifest_path
        .parent()
        .and_then(|root| bundled_runtime_build_id(root).ok())
}

fn headless_bundled_runtime_build_id() -> Option<String> {
    headless_resource_dir("bootstrap-manifest.json").and_then(runtime_build_id_from_manifest_path)
}

fn app_bundled_runtime_build_id(app: &tauri::AppHandle) -> Option<String> {
    resource_dir(app, "bootstrap-manifest.json").and_then(runtime_build_id_from_manifest_path)
}



#[derive(Debug, PartialEq, Eq)]
enum RuntimeStateTransition {
    None,
    MarkReady,
    MarkUnavailable,
}

/// Convert persisted installer state plus live service health into an honest
/// UI transition. A real reboot-pending state is never touched: an older
/// service can still answer while the current install genuinely needs reboot.
fn runtime_state_transition(raw: &str, service_healthy: bool) -> RuntimeStateTransition {
    if installer_state_reboot_required(raw) {
        return RuntimeStateTransition::None;
    }
    match (installer_state_is_ready(raw), service_healthy) {
        (false, true) => RuntimeStateTransition::MarkReady,
        (true, false) => RuntimeStateTransition::MarkUnavailable,
        _ => RuntimeStateTransition::None,
    }
}

/// Poll `/health` for up to 10 seconds, one probe per second, and succeed as
/// soon as one answers. Used after (re)starting the native runtime host to
/// confirm the service actually came up before declaring the lane ready.
fn wait_for_service_health_after_runtime_start(
    expected_instance_id: Option<&str>,
    expected_runtime_build_id: Option<&str>,
) -> Result<(), String> {
    for _ in 0..10 {
        if service_health_reachable_once(expected_instance_id, expected_runtime_build_id) {
            return Ok(());
        }
        thread::sleep(Duration::from_secs(1));
    }
    Err(format!(
        "CivicCast's runtime host started, but Windows could not verify the {CIVICCAST_VERSION} service at {SERVICE_URL}/health. Try again. If it repeats, save the runtime host log and send it to support."
    ))
}

#[cfg(target_os = "windows")]
fn hide_windows_command(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(target_os = "windows")]
fn runtime_host_log(message: &str) {
    let Ok(root) = installer_state_root() else {
        return;
    };
    let _ = fs::create_dir_all(&root);
    let path = root.join("runtime-host.log");
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .and_then(|mut file| writeln!(file, "{} {message}", unix_timestamp()));
}



#[cfg(target_os = "windows")]
fn acquire_runtime_host_lifetime_guard(address: &str) -> Option<TcpListener> {
    // A repair install terminates the previous background host immediately
    // before the newly installed GUI starts. Windows can keep the old host's
    // loopback guard occupied for a brief overlap; an immediate one-shot bind
    // makes the replacement silently exit before it can take over health
    // monitoring. Wait out that bounded handoff race. If a healthy host
    // continues to own the guard, this duplicate exits harmlessly after the
    // budget expires.
    const ATTEMPTS: u32 = 40;
    const RETRY_DELAY: Duration = Duration::from_millis(250);
    for attempt in 0..ATTEMPTS {
        if let Ok(listener) = TcpListener::bind(address) {
            return Some(listener);
        }
        if attempt + 1 < ATTEMPTS {
            thread::sleep(RETRY_DELAY);
        }
    }
    None
}

/// Watch the native `CivicCastSupervisor` Windows service's health for as
/// long as this process lives, logging transitions for support diagnostics.
///
/// The retired WSL product used this loop to ALSO spawn and monitor a
/// companion `wsl.exe` process (the WSL distro is not itself a Windows
/// process the OS can supervise) and to shell into that distro to restart
/// `civiccast.service` on repeated health failures. The native product has
/// no companion process: `CivicCastSupervisor` is a real Windows service,
/// and its own SCM restart-on-failure actions (5s/10s/30s ladder, see
/// `native_service_registration::service_failure_actions_command`) already
/// recover it. This loop's job for native is honest observation only -- it
/// does not attempt to start, repair, or recover the service itself.
#[cfg(target_os = "windows")]
fn run_civiccast_runtime_host() -> i32 {
    // Holding this loopback listener is a process-lifetime singleton. A second
    // logon/start request exits harmlessly while the existing owner continues.
    let _lifetime_guard = match acquire_runtime_host_lifetime_guard(RUNTIME_HOST_MUTEX_ADDR) {
        Some(listener) => listener,
        None => return 0,
    };
    runtime_host_log("Runtime host started.");

    let mut health_failures = 0_u32;
    loop {
        if installer_shutdown_marker_paths()
            .iter()
            .any(|path| path.exists())
        {
            runtime_host_log("Runtime host stopped by uninstall request.");
            return 0;
        }

        if service_health_reachable_once(None, None) {
            if health_failures > 0 {
                runtime_host_log("CivicCast service health recovered.");
            }
            health_failures = 0;
        } else {
            health_failures = health_failures.saturating_add(1);
            if health_failures == 3 {
                runtime_host_log(
                    "CivicCast service remained unhealthy; the Windows service manager's own restart-on-failure actions apply.",
                );
            }
        }
        thread::sleep(Duration::from_secs(5));
    }
}

#[cfg(not(target_os = "windows"))]
fn run_civiccast_runtime_host() -> i32 {
    1
}

#[cfg(target_os = "windows")]
fn runtime_host_executable_candidates(
    current_executable: PathBuf,
    resource_dir: Option<PathBuf>,
) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let (Some(resource_dir), Some(file_name)) = (resource_dir, current_executable.file_name()) {
        if let Some(install_dir) = resource_dir.parent() {
            let physical_executable = install_dir.join(file_name);
            if physical_executable != current_executable {
                candidates.push(physical_executable);
            }
        }
    }
    candidates.push(current_executable);
    candidates
}

#[cfg(target_os = "windows")]
fn launch_runtime_host_process(app: &tauri::AppHandle) -> Result<(), String> {
    let current_executable = std::env::current_exe()
        .map_err(|error| format!("Could not locate the CivicCast executable: {error}"))?;
    let resource_dir = app.path().resource_dir().ok();
    let mut failures = Vec::new();
    for executable in runtime_host_executable_candidates(current_executable, resource_dir) {
        let mut command = Command::new(&executable);
        command.arg("--civiccast-runtime-host");
        hide_windows_command(&mut command);
        match command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(_) => return Ok(()),
            Err(error) => failures.push(format!("{}: {error}", executable.display())),
        }
    }
    Err(format!(
        "Could not start the CivicCast runtime host: {}",
        failures.join("; ")
    ))
}

#[cfg(not(target_os = "windows"))]
fn launch_runtime_host_process(_app: &tauri::AppHandle) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(target_os = "windows")]
    use std::hash::{BuildHasher, Hasher};
    #[cfg(target_os = "windows")]
    use std::io::{Seek, SeekFrom};

    #[cfg(target_os = "windows")]
    fn windows_process_is_running(pid: u32) -> Result<bool, String> {
        let status = Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                &format!(
                    "if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"
                ),
            ])
            .status()
            .map_err(|error| format!("could not inspect Windows process {pid}: {error}"))?;
        match status.code() {
            Some(0) => Ok(true),
            Some(1) => Ok(false),
            Some(code) => Err(format!(
                "Windows process inspection for PID {pid} returned unexpected exit code {code}"
            )),
            None => Err(format!(
                "Windows process inspection for PID {pid} ended without an exit code"
            )),
        }
    }

    #[cfg(target_os = "windows")]
    fn kill_windows_process_bounded(pid: u32) {
        if matches!(windows_process_is_running(pid), Ok(false)) {
            return;
        }
        if let Ok(mut killer) = Command::new("taskkill.exe")
            .args(["/F", "/PID", &pid.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            if !wait_for_child_exit_bounded(&mut killer, Duration::from_secs(3)) {
                let _ = killer.kill();
                let _ = wait_for_child_exit_bounded(&mut killer, Duration::from_secs(2));
            }
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while std::time::Instant::now() < deadline {
            match windows_process_is_running(pid) {
                Ok(false) => return,
                Ok(true) => thread::sleep(Duration::from_millis(50)),
                Err(_) => return,
            }
        }
    }

    #[cfg(target_os = "windows")]
    struct NativeOllamaShutdownFixture {
        child: Child,
        descendant_pid: Option<u32>,
        stdout_path: PathBuf,
        stderr_path: PathBuf,
    }

    #[cfg(target_os = "windows")]
    impl NativeOllamaShutdownFixture {
        fn diagnostics(&self) -> String {
            fn tail(path: &Path) -> String {
                let mut file = match fs::File::open(path) {
                    Ok(file) => file,
                    Err(error) => return format!("<unavailable: {error}>"),
                };
                let length = match file.metadata() {
                    Ok(metadata) => metadata.len(),
                    Err(error) => return format!("<metadata unavailable: {error}>"),
                };
                let tail_length = length.min(8 * 1024);
                if let Err(error) = file.seek(SeekFrom::End(-(tail_length as i64))) {
                    return format!("<seek failed: {error}>");
                }
                let mut bytes = Vec::with_capacity(tail_length as usize);
                if let Err(error) = file.take(8 * 1024).read_to_end(&mut bytes) {
                    return format!("<read failed: {error}>");
                }
                String::from_utf8_lossy(&bytes).trim().to_string()
            }

            format!(
                "stdout={:?}; stderr={:?}",
                tail(&self.stdout_path),
                tail(&self.stderr_path)
            )
        }

        fn setup_failure(&mut self, context: impl std::fmt::Display) -> ! {
            panic!("{context}; {}", self.diagnostics());
        }
    }

    #[cfg(target_os = "windows")]
    impl Drop for NativeOllamaShutdownFixture {
        fn drop(&mut self) {
            // A failed readiness handshake must not let the fixture's intentionally
            // hung process tree leak into a later test or the runner cleanup phase.
            if !matches!(self.child.try_wait(), Ok(Some(_))) {
                terminate_native_ollama_process_tree(&mut self.child);
            }
            if let Some(pid) = self.descendant_pid.take() {
                kill_windows_process_bounded(pid);
            }
            let _ = fs::remove_file(&self.stdout_path);
            let _ = fs::remove_file(&self.stderr_path);
        }
    }

    #[test]
    fn native_ollama_cli_version_accepts_captured_no_daemon_output() {
        // Actual fresh LocalSystem RC1 installation output, before a daemon exists.
        let captured = "Warning: could not connect to a running Ollama instance\nWarning: client version is 0.40.13\n";
        assert!(native_ollama_cli_version_matches(0, captured));
        assert!(native_ollama_cli_version_matches(
            0,
            &captured.replace('\n', "\r\n")
        ));
        assert!(native_ollama_cli_version_matches(
            0,
            "ollama version is 0.40.13\n"
        ));
    }

    #[test]
    fn native_ollama_cli_version_refuses_wrong_or_unrelated_output() {
        for output in [
            "ollama version is 0.30.6",
            "ollama version is 0.40.130",
            "unrelated output mentioning 0.40.13",
            "Warning: could not connect to a running Ollama instance",
            "Warning: client version is 0.40.13",
            "Warning: could not connect to a running Ollama instance\nWarning: client version is 0.30.6",
            "ollama version is 0.40.13\nunrelated output",
            "",
        ] {
            assert!(!native_ollama_cli_version_matches(0, output), "{output:?}");
        }
        for code in [-1, 1, 67] {
            assert!(!native_ollama_cli_version_matches(
                code,
                "ollama version is 0.40.13"
            ));
            assert!(!native_ollama_cli_version_matches(code, "Warning: could not connect to a running Ollama instance\nWarning: client version is 0.40.13"));
        }
    }

    #[test]
    fn native_ollama_daemon_version_still_requires_the_exact_server_response() {
        validate_native_ollama_version_response(r#"{"version":"0.40.13"}"#).unwrap();
        for body in [
            r#"{"version":"0.30.6"}"#,
            r#"{"version":"0.40.130"}"#,
            r#"{"client_version":"0.40.13"}"#,
            "Warning: could not connect to a running Ollama instance\nWarning: client version is 0.40.13",
        ] {
            assert!(validate_native_ollama_version_response(body).is_err(), "{body:?}");
        }
    }

    #[test]
    fn native_model_self_test_response_fails_closed() {
        let valid = r#"{
            "model":"gemma4:12b",
            "response":"CIVICCAST_OK",
            "done":true
        }"#;
        validate_native_model_response(valid, "gemma4:12b", "CIVICCAST_OK")
            .expect("exact completed response");

        for (body, label) in [
            (
                r#"{"model":"gemma4:e4b","response":"CIVICCAST_OK","done":true}"#,
                "wrong model",
            ),
            (
                r#"{"model":"gemma4:12b","response":"","done":true}"#,
                "empty response",
            ),
            (
                r#"{"model":"gemma4:12b","response":"something else","done":true}"#,
                "missing marker",
            ),
            (
                r#"{"model":"gemma4:12b","response":"CIVICCAST_OK","done":false}"#,
                "unfinished response",
            ),
            ("not json", "malformed response"),
        ] {
            assert!(
                validate_native_model_response(body, "gemma4:12b", "CIVICCAST_OK").is_err(),
                "{label} must fail"
            );
        }
    }

    #[cfg(target_os = "windows")]
    /// Gate A clean lane, 2026-10-02: a 60 s readiness wait failed the signed
    /// beta.10 installer on a clean VM where Ollama took 61 s to finish its
    /// GPU discovery pass. The wait must stay comfortably above that
    /// measurement (and never fall back below the old 60 s limit), and it must
    /// not exceed the 300 s inference request timeout it sits in front of.
    #[cfg(target_os = "windows")]
    #[test]
    fn native_ai_self_test_readiness_wait_outlasts_slow_gpu_discovery() {
        assert!(
            NATIVE_AI_SELF_TEST_READY_TIMEOUT >= Duration::from_secs(240),
            "readiness wait {:?} is too short: a clean Windows Sandbox needed 61 s",
            NATIVE_AI_SELF_TEST_READY_TIMEOUT
        );
        assert!(
            NATIVE_AI_SELF_TEST_READY_TIMEOUT <= Duration::from_secs(300),
            "readiness wait {:?} must stay bounded by the 300 s request timeout",
            NATIVE_AI_SELF_TEST_READY_TIMEOUT
        );
    }

    #[test]
    fn native_ollama_shutdown_is_bounded_and_kills_the_descendant_tree() {
        let fixture_started = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or_default();
        let fixture_id = format!(
            "civiccast-ollama-shutdown-{}-{}",
            std::process::id(),
            fixture_started
        );
        let mut nonce_hasher = std::collections::hash_map::RandomState::new().build_hasher();
        nonce_hasher.write_u32(std::process::id());
        nonce_hasher.write_u128(fixture_started);
        let receipt_nonce = format!("{:016x}", nonce_hasher.finish());
        let stdout_path = std::env::temp_dir().join(format!("{fixture_id}.stdout.log"));
        let stderr_path = std::env::temp_dir().join(format!("{fixture_id}.stderr.log"));
        let stdout = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&stdout_path)
            .expect("create fixture stdout log");
        let stderr = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&stderr_path)
            .expect("create fixture stderr log");
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind fixture readiness listener");
        listener
            .set_nonblocking(true)
            .expect("make fixture readiness listener nonblocking");
        let port = listener
            .local_addr()
            .expect("read fixture readiness listener address")
            .port();
        let script = format!(
            r#"
$ErrorActionPreference = 'Stop'
$descendant = $null
$client = $null
try {{
    $descendant = Start-Process powershell.exe -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 300') -PassThru -ErrorAction Stop
    $client = [System.Net.Sockets.TcpClient]::new('127.0.0.1', {port})
    $writer = [System.IO.StreamWriter]::new($client.GetStream(), [System.Text.Encoding]::ASCII, 1024, $true)
    try {{
        $writer.WriteLine('{receipt_nonce}:' + $descendant.Id)
        $writer.Flush()
    }} finally {{
        $writer.Dispose()
    }}
    $client.Dispose()
    $client = $null
    Start-Sleep -Seconds 300
}} catch {{
    if ($null -ne $descendant) {{
        Stop-Process -Id $descendant.Id -Force -ErrorAction SilentlyContinue
    }}
    [Console]::Error.WriteLine("fixture readiness failed: $($_.Exception.Message)")
    exit 1
}} finally {{
    if ($null -ne $client) {{
        $client.Dispose()
    }}
}}
"#
        );
        let child = Command::new("powershell.exe")
            .args(["-NoProfile", "-Command", &script])
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr))
            .spawn()
            .expect("start hung fixture");
        let mut fixture = NativeOllamaShutdownFixture {
            child,
            descendant_pid: None,
            stdout_path,
            stderr_path,
        };
        // Setup budget only — the product claim below stays bounded at 12 s.
        // 8 s flaked on loaded CI runners (PR #367 run 31152959917): two cold
        // powershell.exe starts plus a TCP round-trip while sibling tests are
        // spawning their own PowerShell probes can exceed it. The descendant
        // sleeps 300 s, so a slow setup can never let it exit on its own
        // before the kill assertion runs.
        let deadline = std::time::Instant::now() + Duration::from_secs(60);
        let descendant_pid = loop {
            match listener.accept() {
                Ok((mut receipt_socket, _)) => {
                    receipt_socket.set_nonblocking(false).unwrap_or_else(|error| {
                        fixture.setup_failure(format!(
                            "fixture readiness receipt socket could not enter blocking mode: {error}"
                        ))
                    });
                    receipt_socket
                        .set_read_timeout(Some(Duration::from_secs(10)))
                        .expect("bound fixture readiness receipt read");
                    let mut receipt = String::new();
                    receipt_socket
                        .read_to_string(&mut receipt)
                        .unwrap_or_else(|error| {
                            fixture.setup_failure(format!(
                                "fixture readiness receipt could not be read: {error}"
                            ))
                        });
                    let Some((observed_nonce, pid_text)) = receipt.trim().split_once(':') else {
                        fixture.setup_failure(format!(
                            "fixture readiness receipt was malformed: {receipt:?}"
                        ));
                    };
                    if observed_nonce != receipt_nonce {
                        fixture.setup_failure(format!(
                            "fixture readiness receipt nonce did not match this test run: {receipt:?}"
                        ));
                    }
                    match pid_text.parse::<u32>() {
                        Ok(pid) if pid != 0 => break pid,
                        Ok(_) | Err(_) => fixture.setup_failure(format!(
                            "fixture readiness receipt did not contain one nonzero descendant PID: {receipt:?}"
                        )),
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    match fixture.child.try_wait() {
                        Ok(Some(status)) => fixture.setup_failure(format!(
                            "fixture parent exited before readiness receipt with status {status}"
                        )),
                        Ok(None) if std::time::Instant::now() < deadline => {
                            thread::sleep(Duration::from_millis(25));
                        }
                        Ok(None) => fixture.setup_failure(
                            "fixture readiness receipt timed out before the parent exited",
                        ),
                        Err(error) => fixture.setup_failure(format!(
                            "fixture parent could not be inspected before readiness: {error}"
                        )),
                    }
                }
                Err(error) => fixture.setup_failure(format!(
                    "fixture readiness listener failed before receipt: {error}"
                )),
            }
        };
        fixture.descendant_pid = Some(descendant_pid);
        match fixture.child.try_wait() {
            Ok(None) => {}
            Ok(Some(status)) => fixture.setup_failure(format!(
                "fixture parent exited after readiness receipt with status {status}"
            )),
            Err(error) => fixture.setup_failure(format!(
                "fixture parent could not be inspected after readiness receipt: {error}"
            )),
        }
        match windows_process_is_running(descendant_pid) {
            Ok(true) => {}
            Ok(false) => fixture.setup_failure(
                "authenticated fixture descendant was already absent before termination",
            ),
            Err(error) => fixture.setup_failure(error),
        }
        let started = std::time::Instant::now();

        terminate_native_ollama_process_tree(&mut fixture.child);

        assert!(
            started.elapsed() < Duration::from_secs(12),
            "native AI shutdown must never wait indefinitely"
        );
        assert!(
            fixture.child.try_wait().expect("inspect fixture").is_some(),
            "hung fixture must be reaped"
        );
        let descendant_still_running = windows_process_is_running(descendant_pid)
            .expect("inspect descendant after native AI shutdown");
        assert!(
            !descendant_still_running,
            "taskkill /T must not leave the model runner descendant alive"
        );
        fixture.descendant_pid = None;
    }

    #[test]
    fn native_caption_self_test_output_fails_closed() {
        validate_native_caption_output(
            0,
            "And so my fellow Americans, ask not what your country can do for you.",
        )
        .expect("real expected transcript");

        for (exit_code, output) in [
            (1, "fellow Americans country"),
            (0, "fellow Americans"),
            (0, "country"),
            (0, ""),
        ] {
            assert!(
                validate_native_caption_output(exit_code, output).is_err(),
                "incomplete or failed caption inference must fail"
            );
        }
    }

    fn caption_self_test_temp_root(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "civiccast-caption-self-test-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("create caption self-test root");
        root
    }

    #[test]
    fn native_caption_self_test_prefers_large_v3_when_it_is_staged() {
        // Owner decision (2026-08-07, ratified): large-v3 is optional, the
        // caption FLOOR tier is mandatory -- but the self-test must still
        // prefer large-v3 (the higher-quality tier) whenever it is actually
        // staged, never silently prefer the floor tier over an installed
        // large-v3.
        let staging = caption_self_test_temp_root("prefers-large-v3");
        std::fs::create_dir_all(
            staging.join("components/captions-large-v3/models/faster-whisper-large-v3"),
        )
        .expect("stage large-v3 model root");

        let (program, arguments) =
            native_caption_inference_command(&staging).expect("caption command");

        assert_eq!(
            program,
            staging.join("runtime/python.exe").to_string_lossy()
        );
        assert_eq!(&arguments[..3], ["-I", "-B", "-c"]);
        assert!(arguments[3].contains("from faster_whisper import WhisperModel"));
        assert!(arguments[3].contains("local_files_only=True"));
        assert!(arguments[3].contains("device=\"cpu\""));
        assert!(arguments[3].contains("compute_type=\"int8\""));
        assert_eq!(
            arguments[4],
            staging
                .join("components/captions-large-v3/models/faster-whisper-large-v3")
                .to_string_lossy()
        );
        assert_eq!(
            arguments[5],
            staging
                .join("components/captions-large-v3/self-test/jfk.wav")
                .to_string_lossy()
        );
        assert!(!arguments.join(" ").contains("whisper-cli"));
        assert!(!arguments.join(" ").contains("ggml"));
        std::fs::remove_dir_all(&staging).expect("clean caption self-test root");
    }

    #[test]
    fn native_caption_self_test_falls_back_to_the_mandatory_floor_tier_when_large_v3_is_absent() {
        // The actual proof of the owner's decision at the self-test layer:
        // a station with ONLY the mandatory floor tier staged (no
        // `captions-large-v3` directory at all -- the air-gapped/USB shape)
        // must still get a REAL self-test command against the floor model,
        // never a skipped or captionless self-test.
        let staging = caption_self_test_temp_root("falls-back-to-floor");

        let (program, arguments) =
            native_caption_inference_command(&staging).expect("caption command");

        assert_eq!(
            program,
            staging.join("runtime/python.exe").to_string_lossy()
        );
        assert!(arguments[3].contains("from faster_whisper import WhisperModel"));
        assert_eq!(
            arguments[4],
            staging
                .join("packs/captions-floor/models/faster-whisper-medium")
                .to_string_lossy()
        );
        assert_eq!(
            arguments[5],
            staging
                .join("packs/captions-floor/self-test/jfk.wav")
                .to_string_lossy()
        );
        std::fs::remove_dir_all(&staging).expect("clean caption self-test root");
    }

    // ------------------------------------------------------------------
    // Field evidence regression: acquiring the optional captions_large
    // component after activation must ADD a valid addendum receipt without
    // touching anything the elevated installer already wrote, and a station
    // that later resolves large-v3 must find a receipt that validates.
    // ------------------------------------------------------------------

    fn captions_large_addendum_temp_roots(label: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!(
            "civiccast-captions-large-addendum-{label}-{}",
            std::process::id()
        ));
        let install_root = base.join("install");
        let acquisition_root = base.join("acquisition");
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&install_root).expect("create install root fixture");
        std::fs::create_dir_all(&acquisition_root).expect("create acquisition root fixture");
        (install_root, acquisition_root)
    }

    fn write_fixture_station_set(install_root: &Path, version: &str, index_sha256: &str) {
        let value = serde_json::json!({
            "schema_version": 2,
            "product": "civiccast-native",
            "product_version": version,
            "compatible_core": version,
            "distribution_index_sha256": index_sha256,
            "signing_key_id": "test-key",
            "packs": [],
            "runtime": {},
        });
        std::fs::write(
            install_root.join("station-set.json"),
            serde_json::to_vec_pretty(&value).expect("encode fixture station-set"),
        )
        .expect("write fixture station-set");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn write_captions_large_addendum_receipt_matches_the_pinned_large_v3_identity_and_this_stations_own_version(
    ) {
        let (install_root, acquisition_root) =
            captions_large_addendum_temp_roots("writer-matches-identity");
        write_fixture_station_set(&install_root, "1.0.0-rcTEST", "deadbeef".repeat(8).as_str());

        write_captions_large_addendum_receipt(&install_root, &acquisition_root)
            .expect("write addendum receipt");

        let written: serde_json::Value = serde_json::from_slice(
            &std::fs::read(captions_large_addendum_receipt_path(&acquisition_root))
                .expect("read addendum receipt"),
        )
        .expect("addendum receipt is valid JSON");

        // Every field here is checked byte-for-byte against
        // `civiccast.native.station_runtime._expected_caption_receipt`'s
        // large-v3 branch and `_validate_activation_receipt`'s identity
        // checks -- this is the exact contract the runtime enforces.
        assert_eq!(written["schema_version"], serde_json::json!(1));
        assert_eq!(written["product"], serde_json::json!("civiccast-native"));
        assert_eq!(written["product_version"], serde_json::json!("1.0.0-rcTEST"));
        assert_eq!(
            written["distribution_index_sha256"],
            serde_json::json!("deadbeef".repeat(8))
        );
        let caption = &written["caption_inference"];
        assert_eq!(caption["runtime"], serde_json::json!("faster-whisper 1.2.1"));
        assert_eq!(caption["ctranslate2"], serde_json::json!("4.8.1"));
        assert_eq!(
            caption["model"],
            serde_json::json!(
                "Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478"
            )
        );
        assert_eq!(
            caption["model_path"],
            serde_json::json!("components/captions-large-v3/models/faster-whisper-large-v3")
        );
        assert_eq!(caption["model_bin_bytes"], serde_json::json!(3_087_284_237_u64));
        assert_eq!(
            caption["model_bin_sha256"],
            serde_json::json!(
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
            )
        );
        assert_eq!(caption["device"], serde_json::json!("cpu"));
        assert_eq!(caption["compute_type"], serde_json::json!("int8"));
        assert_eq!(caption["local_files_only"], serde_json::json!(true));
        assert_eq!(caption["result"], serde_json::json!("passed"));

        std::fs::remove_dir_all(install_root.parent().expect("fixture base"))
            .expect("clean addendum fixture");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn writing_the_addendum_receipt_never_touches_install_roots_own_station_set_or_receipt_or_any_other_acquired_component(
    ) {
        // The field-evidence defect: acquiring the optional large-v3 tier
        // must ADD to the station, never remove or rewrite what the
        // elevated installer already activated. This test proves the
        // negative directly against the REAL writer, not a re-implementation
        // of it.
        let (install_root, acquisition_root) =
            captions_large_addendum_temp_roots("writer-is-additive-only");
        write_fixture_station_set(&install_root, "1.0.0-rcTEST", "cafebabe".repeat(8).as_str());
        let primary_receipt = install_root.join("activation-self-test.json");
        std::fs::write(&primary_receipt, b"{\"floor\":\"receipt\",\"untouched\":true}")
            .expect("seed primary receipt fixture");
        let other_component = install_root.join("components").join("summary-gemma4-12b");
        std::fs::create_dir_all(&other_component).expect("seed other activated component");
        std::fs::write(other_component.join("marker.bin"), b"already-activated")
            .expect("seed other activated component marker");
        let floor_component = install_root.join("packs").join("captions-floor");
        std::fs::create_dir_all(&floor_component).expect("seed floor tier fixture");
        std::fs::write(floor_component.join("marker.bin"), b"floor-tier-untouched")
            .expect("seed floor tier marker");

        write_captions_large_addendum_receipt(&install_root, &acquisition_root)
            .expect("write addendum receipt");

        assert_eq!(
            std::fs::read(&primary_receipt).expect("re-read primary receipt"),
            b"{\"floor\":\"receipt\",\"untouched\":true}",
            "acquiring an optional component must never rewrite the primary activation receipt"
        );
        assert_eq!(
            std::fs::read(other_component.join("marker.bin"))
                .expect("re-read other component marker"),
            b"already-activated",
            "acquiring an optional component must never touch an already-activated component"
        );
        assert_eq!(
            std::fs::read(floor_component.join("marker.bin"))
                .expect("re-read floor tier marker"),
            b"floor-tier-untouched",
            "acquiring an optional component must never touch the mandatory floor tier"
        );
        assert!(
            captions_large_addendum_receipt_path(&acquisition_root).exists(),
            "the addendum receipt must be written into the acquisition root"
        );

        std::fs::remove_dir_all(install_root.parent().expect("fixture base"))
            .expect("clean addendum fixture");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn addendum_already_matches_is_true_only_for_a_receipt_bound_to_this_exact_station() {
        let (install_root, acquisition_root) =
            captions_large_addendum_temp_roots("idempotency-fast-path");
        write_fixture_station_set(&install_root, "1.0.0-rcTEST", "01234567".repeat(8).as_str());

        assert!(
            !captions_large_addendum_already_matches(&install_root, &acquisition_root),
            "no receipt on disk yet must never be treated as already satisfied"
        );

        write_captions_large_addendum_receipt(&install_root, &acquisition_root)
            .expect("write addendum receipt");
        assert!(
            captions_large_addendum_already_matches(&install_root, &acquisition_root),
            "a receipt this function itself just wrote for THIS station must match"
        );

        // A station-set that moved on to a different distribution must
        // invalidate the fast path -- never trust a receipt against a
        // station identity it was not written for.
        write_fixture_station_set(&install_root, "1.0.0-rcTEST", "76543210".repeat(8).as_str());
        assert!(
            !captions_large_addendum_already_matches(&install_root, &acquisition_root),
            "a receipt bound to a different distribution_index_sha256 must not be reused"
        );

        std::fs::remove_dir_all(install_root.parent().expect("fixture base"))
            .expect("clean addendum fixture");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn verify_self_test_audio_fixture_rejects_wrong_bytes_and_accepts_the_pinned_fixture() {
        let (install_root, _acquisition_root) =
            captions_large_addendum_temp_roots("audio-fixture-verification");
        let audio_path = install_root.join("jfk.wav");

        let missing = verify_self_test_audio_fixture(&audio_path);
        assert!(missing.is_err(), "a missing audio fixture must fail closed");

        std::fs::write(&audio_path, vec![0_u8; 128]).expect("write wrong-size fixture");
        let wrong_size = verify_self_test_audio_fixture(&audio_path);
        assert!(
            wrong_size.is_err(),
            "an audio fixture with the wrong byte count must fail closed"
        );
        assert!(wrong_size.unwrap_err().contains("bytes, expected"));

        std::fs::remove_dir_all(install_root.parent().expect("fixture base"))
            .expect("clean addendum fixture");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn captions_large_addendum_model_root_and_receipt_path_use_the_pinned_layout() {
        let acquisition_root = PathBuf::from(r"C:\ProgramData\CivicCast");
        assert_eq!(
            captions_large_addendum_model_root(&acquisition_root),
            acquisition_root
                .join("components")
                .join("captions-large-v3")
                .join("models")
                .join("faster-whisper-large-v3")
        );
        assert_eq!(
            captions_large_addendum_receipt_path(&acquisition_root),
            acquisition_root.join("activation-self-test.json")
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn runtime_host_guard_waits_out_previous_owner_shutdown() {
        let incumbent = TcpListener::bind("127.0.0.1:0").expect("bind incumbent guard");
        let address = incumbent.local_addr().expect("read guard address");
        let releaser = thread::spawn(move || {
            thread::sleep(Duration::from_millis(100));
            drop(incumbent);
        });

        let replacement = acquire_runtime_host_lifetime_guard(&address.to_string());
        releaser.join().expect("release incumbent guard");

        assert!(
            replacement.is_some(),
            "a replacement runtime host must survive the repair install's previous-host shutdown race"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn runtime_host_launch_prefers_the_physical_resource_install_over_a_virtualized_exe_path() {
        let logical_executable = PathBuf::from(
            r"C:\Users\tester\AppData\Local\CivicCast Installer\civiccast-installer.exe",
        );
        let physical_resource_dir = PathBuf::from(
            r"C:\Users\tester\AppData\Local\Packages\Host.Package\LocalCache\Local\CivicCast Installer\resources",
        );

        let candidates = runtime_host_executable_candidates(
            logical_executable.clone(),
            Some(physical_resource_dir.clone()),
        );

        assert_eq!(
            candidates,
            vec![
                physical_resource_dir
                    .parent()
                    .expect("resource install parent")
                    .join("civiccast-installer.exe"),
                logical_executable,
            ],
            "a packaged parent can virtualize the installed path; the child host must launch from the physical resource directory first"
        );
    }










    // Reproduces the DESKTOP-2BR3SJR clean-machine finding: a hung wsl.exe
    // wedged the installer's status pre-check forever because the pre-check
    // spawn had no timeout. The bounded core must tree-kill a hung child and
    // report exit 124 instead of blocking.
    #[cfg(target_os = "windows")]
    #[test]
    fn run_bounded_command_kills_a_hung_child_and_reports_timeout() {
        let (exit_code, text) = run_bounded_command(
            "powershell.exe",
            "test-hang",
            &["-NoProfile", "-Command", "Start-Sleep -Seconds 30"],
            2,
        )
        .expect("spawn should succeed");
        assert_eq!(exit_code, 124);
        assert!(
            text.contains("timed out after 2 seconds"),
            "timeout note missing from: {text}"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn bounded_command_progress_heartbeats_during_a_slow_child() {
        let mut heartbeats = Vec::new();
        // The bound only guards against a hang; it must absorb PowerShell's
        // cold-start on a loaded CI runner (observed >6s), or the child gets
        // killed at the bound and the test flakes on exit_code.
        let (exit_code, _) = run_bounded_command_with_progress(
            "powershell.exe",
            "test-progress",
            &["-NoProfile", "-Command", "Start-Sleep -Seconds 4"],
            30,
            |elapsed| heartbeats.push(elapsed),
        )
        .expect("spawn should succeed");

        assert_eq!(exit_code, 0);
        assert_eq!(heartbeats.first(), Some(&0));
        assert!(
            heartbeats.iter().any(|elapsed| *elapsed >= 3),
            "a slow child must emit a later heartbeat: {heartbeats:?}"
        );
    }

    // PR #421 review fix: `classify_optional_staged_entry` /
    // `run_native_optional_verified_if_present_checks` must inspect a staged
    // optional entry WITHOUT following symlinks (the K1-1 defect used
    // `Path::is_file()`, which follows them). Covers: absent (ok, no exec
    // needed); a present regular file classifies as "proceed to verify"
    // (the exec/verify step itself is unchanged by this fix and is not
    // independently faked here -- no existing test in this codebase spins
    // up a fake staged binary for a version-string probe, and doing so would
    // require a real, valid Win32 PE claiming to be TSDuck); a present
    // regular file with garbage content fails once the real function tries
    // to run it; a directory or a symlink at the staged path fails closed
    // rather than being silently treated as "absent."
    #[cfg(target_os = "windows")]
    fn optional_check_staging_root(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "civiccast-optional-tsp-check-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let parent = root.join("packs/native-server-binaries/payload/tsduck/bin");
        std::fs::create_dir_all(&parent).expect("create staged tsp parent dir");
        root
    }

    #[cfg(target_os = "windows")]
    const OPTIONAL_TSP_RELATIVE: &str = "packs/native-server-binaries/payload/tsduck/bin/tsp.exe";

    #[cfg(target_os = "windows")]
    #[test]
    fn classify_optional_staged_entry_absent_is_ok_false() {
        let root = optional_check_staging_root("classify-absent");
        let path = root.join(OPTIONAL_TSP_RELATIVE);
        assert_eq!(
            classify_optional_staged_entry(&path, OPTIONAL_TSP_RELATIVE),
            Ok(false),
            "a genuine not-found must classify as absent-and-optional, never an error"
        );
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn classify_optional_staged_entry_a_present_regular_file_proceeds_to_verify() {
        let root = optional_check_staging_root("classify-regular-file");
        let path = root.join(OPTIONAL_TSP_RELATIVE);
        std::fs::write(&path, b"stand-in bytes, never actually executed by this test").unwrap();
        assert_eq!(
            classify_optional_staged_entry(&path, OPTIONAL_TSP_RELATIVE),
            Ok(true),
            "a real, regular, non-symlink file must classify as present-and-verifiable"
        );
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_native_optional_verified_if_present_checks_ok_when_tsp_absent() {
        // End-to-end through the real public function, not just the classifier:
        // an install with no TSDuck staged at all must activate cleanly.
        let root = optional_check_staging_root("public-absent");
        run_native_optional_verified_if_present_checks(&root)
            .expect("activation must not hard-fail when optional tsp.exe is entirely absent");
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_native_optional_verified_if_present_checks_fails_when_staged_tsp_is_a_broken_regular_file(
    ) {
        // A regular file IS staged at the path (so it must be verified, not
        // skipped as absent) but its content is not a real, runnable tsp.exe --
        // the real function must fail closed rather than silently pass.
        let root = optional_check_staging_root("public-broken-regular-file");
        let path = root.join(OPTIONAL_TSP_RELATIVE);
        std::fs::write(&path, b"not a real Win32 executable").unwrap();
        let error = run_native_optional_verified_if_present_checks(&root)
            .expect_err("a present-but-broken staged tsp.exe must fail validation");
        assert!(
            !error.is_empty(),
            "the failure must carry a message identifying the problem"
        );
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_native_optional_verified_if_present_checks_fails_when_staged_tsp_is_a_directory() {
        // The K1-1 defect class: `Path::is_file()` returns `false` for a
        // directory, which the OLD code silently treated as "absent, fine."
        // A directory at the staged path is a corrupt install state and must
        // hard-fail, never be waved through as merely-missing-and-optional.
        let root = optional_check_staging_root("public-directory-at-path");
        let path = root.join(OPTIONAL_TSP_RELATIVE);
        std::fs::create_dir_all(&path).expect("stage a directory where tsp.exe should be");
        let error = run_native_optional_verified_if_present_checks(&root)
            .expect_err("a directory staged at the optional tsp.exe path must fail validation");
        assert!(
            error.contains(OPTIONAL_TSP_RELATIVE),
            "error must name the offending staged path, got: {error}"
        );
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn run_native_optional_verified_if_present_checks_fails_when_staged_tsp_is_a_symlink() {
        // The other K1-1 defect class: `Path::is_file()` FOLLOWS symlinks, so
        // a symlink pointing at a real, working tsp.exe would previously get
        // EXECUTED -- bypassing the no-symlinks contract every other staged-
        // file check in this codebase enforces (`require_staged_files`).
        // Symlink creation on Windows needs either an elevated process or
        // Developer Mode enabled; if this test environment allows neither,
        // that is itself a real environment constraint (not something this
        // test can control), so it documents the gap via a loud eprintln and
        // exits early rather than failing the whole suite on privilege.
        let root = optional_check_staging_root("public-symlink-at-path");
        let target = root.join("real-file-elsewhere.txt");
        std::fs::write(&target, b"symlink target, never executed").unwrap();
        let path = root.join(OPTIONAL_TSP_RELATIVE);
        if let Err(error) = std::os::windows::fs::symlink_file(&target, &path) {
            eprintln!(
                "SKIPPING run_native_optional_verified_if_present_checks_fails_when_staged_tsp_is_a_symlink: \
                 could not create a Windows symlink in this test environment ({error}) -- this \
                 requires either an elevated process or Developer Mode's unprivileged symlink \
                 creation to be enabled. The directory-at-path and broken-regular-file tests \
                 above still cover the same is_file()-follows-links defect class for the cases \
                 this environment CAN exercise."
            );
            let _ = std::fs::remove_dir_all(&root);
            return;
        }
        let error = run_native_optional_verified_if_present_checks(&root)
            .expect_err("a symlink staged at the optional tsp.exe path must fail validation, \
                         even one pointing at a valid target");
        assert!(
            error.contains(OPTIONAL_TSP_RELATIVE),
            "error must name the offending staged path, got: {error}"
        );
        std::fs::remove_dir_all(&root).expect("clean temp root");
    }

    // F-RC3-1: the live-panel self-heal decision core.
    const STATE_RUNNING: &str = r#"{"schema_version":1,"current_lane_id":"runtime","status":"running","reboot_required":false}"#;
    const STATE_REBOOT_PENDING: &str = r#"{"schema_version":1,"current_lane_id":"dashboard","status":"pending_reboot","reboot_required":true}"#;
    const STATE_READY: &str = r#"{"schema_version":1,"current_lane_id":"runtime","status":"ready","reboot_required":false}"#;

    #[test]
    fn live_health_promotes_stale_state_and_revokes_stale_ready() {
        assert_eq!(
            runtime_state_transition(STATE_RUNNING, true),
            RuntimeStateTransition::MarkReady
        );
        assert_eq!(
            runtime_state_transition(STATE_READY, false),
            RuntimeStateTransition::MarkUnavailable
        );
        assert_eq!(
            runtime_state_transition(STATE_READY, true),
            RuntimeStateTransition::None
        );
        assert_eq!(
            runtime_state_transition(STATE_RUNNING, false),
            RuntimeStateTransition::None
        );
        // A leftover service must never clobber a genuine reboot requirement.
        assert_eq!(
            runtime_state_transition(STATE_REBOOT_PENDING, true),
            RuntimeStateTransition::None
        );
    }



    #[cfg(target_os = "windows")]
    #[test]
    fn windows_state_root_uses_a_non_redirected_profile_directory() {
        let user_profile = std::env::var_os("USERPROFILE").expect("USERPROFILE should resolve");
        assert_eq!(
            installer_state_root().expect("Windows state root should resolve"),
            PathBuf::from(user_profile).join(".civiccast")
        );
    }


    #[test]
    fn reconcile_preserves_the_lane_the_operator_is_watching() {
        assert_eq!(
            installer_state_string_field(STATE_RUNNING, "current_lane_id").as_deref(),
            Some("runtime")
        );
        assert_eq!(
            installer_state_string_field(STATE_REBOOT_PENDING, "current_lane_id").as_deref(),
            Some("dashboard")
        );
        assert_eq!(
            installer_state_string_field(STATE_RUNNING, "status").as_deref(),
            Some("running")
        );
        assert_eq!(installer_state_string_field(STATE_RUNNING, "nope"), None);
    }


    #[cfg(target_os = "windows")]
    #[test]
    fn run_bounded_command_returns_output_for_a_fast_child() {
        // The echoed marker includes "ubuntu" on purpose: the bounded core
        // decodes output via decode_windows_command_output, whose heuristic
        // treats ubuntu-bearing output as UTF-8 (the wsl.exe list shape) and
        // everything else as UTF-16LE. cmd.exe emits ANSI/UTF-8, so a marker
        // without "ubuntu" would be mis-decoded as UTF-16 mojibake.
        let (exit_code, text) = run_bounded_command(
            "cmd.exe",
            "test-echo",
            &["/c", "echo ubuntu-bounded-ok"],
            30,
        )
        .expect("spawn should succeed");
        assert_eq!(exit_code, 0);
        assert!(
            text.contains("ubuntu-bounded-ok"),
            "echo output missing: {text}"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn native_ollama_probe_overrides_ambient_server_for_child_only() {
        let before = std::env::var_os("OLLAMA_HOST");
        let (exit, output) = run_bounded_command(
            "cmd.exe",
            "native-ollama",
            &["/c", "echo %OLLAMA_HOST%"],
            10,
        )
        .expect("isolated probe child should run");
        assert_eq!(exit, 0);
        assert_eq!(output.trim(), "http://127.0.0.1:0");
        assert_eq!(std::env::var_os("OLLAMA_HOST"), before);
    }

    // rc10 cleanroom: the WSL health probe's UTF-8 stdout (no "ubuntu" marker) was
    // mis-decoded as UTF-16 -> log mojibake. Detect encoding by structure instead.
    #[cfg(target_os = "windows")]
    #[test]
    fn decode_windows_command_output_uses_structure_not_an_ubuntu_marker() {
        // wsl.exe UTF-16LE list output (NUL every other byte) decodes to readable text.
        assert_eq!(
            decode_windows_command_output(b"N\x00A\x00M\x00E\x00"),
            "NAME"
        );
        // a --exec'd command's UTF-8 stdout with NO "ubuntu" marker must stay UTF-8,
        // not be mis-decoded as UTF-16 (would garble under the old content heuristic).
        assert_eq!(
            decode_windows_command_output(b"python-probe 3.12 ok"),
            "python-probe 3.12 ok"
        );
    }

    // Verifier follow-up (PR #245): prove the stdout+stderr merge path is
    // safe when BOTH streams carry output — stdout must come first and both
    // must survive decoding, since wsl_ubuntu_distribution parses the merged
    // text for distro rows.
    #[cfg(target_os = "windows")]
    #[test]
    fn run_bounded_command_merges_stdout_then_stderr_when_both_are_populated() {
        let (exit_code, text) = run_bounded_command(
            "cmd.exe",
            "test-both-streams",
            &["/c", "echo ubuntu-on-stdout & echo ubuntu-on-stderr 1>&2"],
            30,
        )
        .expect("spawn should succeed");
        assert_eq!(exit_code, 0);
        let stdout_at = text
            .find("ubuntu-on-stdout")
            .unwrap_or_else(|| panic!("stdout marker missing: {text}"));
        let stderr_at = text
            .find("ubuntu-on-stderr")
            .unwrap_or_else(|| panic!("stderr marker missing: {text}"));
        assert!(
            stdout_at < stderr_at,
            "stdout must precede stderr in merged text: {text}"
        );
    }


    #[test]
    fn installer_log_candidates_cover_the_native_runtime_host_log() {
        let root = Path::new(r"C:\Users\tester\AppData\Local\CivicCast");
        assert_eq!(
            installer_log_candidates(root),
            [root.join("runtime-host.log")]
        );
    }

    // -----------------------------------------------------------------
    // Bug fix (field report 2026-08-28, candidate 9d4477b): "Open installer
    // log" was a no-op on the download screen. Root cause was TWO bugs
    // stacked: `newest_installer_log_path` only ever looked for
    // `runtime-host.log` (written by the native service, which has not
    // started yet on that screen) and never for `install-progress.log`
    // (written by the elevated NSIS installer, which HAS run by then); and
    // the command hardcoded `notepad.exe` instead of the OS default
    // handler. These tests cover the log-PATH resolution half (pure /
    // real-temp-file logic); the frontend half (visible error surfacing) is
    // covered in `cancel-retry.test.ts`.
    // -----------------------------------------------------------------

    #[test]
    fn installer_progress_log_path_from_matches_the_nsis_bootstrap_hooks_own_path() {
        // `nsis-hooks-bootstrap.nsh` opens
        // `$COMMONPROGRAMDATA\CivicCast\install-progress.log` directly; this
        // must resolve to the exact same relative path under whatever
        // `%PROGRAMDATA%` (`$COMMONPROGRAMDATA`'s Rust-visible equivalent)
        // actually is on this machine.
        let program_data = Path::new(r"C:\ProgramData");
        assert_eq!(
            installer_progress_log_path_from(program_data),
            Path::new(r"C:\ProgramData\CivicCast\install-progress.log")
        );
        // And it must share the SAME per-machine root
        // `acquisition_download_root_from` already derives for the GUI's
        // own component downloads -- one root, never a second, parallel
        // ProgramData convention.
        assert_eq!(
            installer_progress_log_path_from(program_data),
            acquisition_download_root_from(program_data).join("install-progress.log")
        );
    }

    fn installer_log_scratch_dir(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "civiccast-installer-log-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("create scratch dir");
        root
    }

    #[test]
    fn newest_existing_log_path_picks_whichever_candidate_was_modified_last() {
        let root = installer_log_scratch_dir("newest-wins");
        let older = root.join("install-progress.log");
        let newer = root.join("runtime-host.log");
        fs::write(&older, b"older transcript").expect("write older log");
        // Distinct, coarse-safe mtimes: some filesystems only carry
        // second-resolution timestamps, so a same-instant write pair would
        // make this test flaky rather than load-bearing.
        std::thread::sleep(Duration::from_millis(1100));
        fs::write(&newer, b"newer transcript").expect("write newer log");

        let picked = newest_existing_log_path(&[older.clone(), newer.clone()])
            .expect("at least one candidate exists");
        assert_eq!(picked, newer);

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn newest_existing_log_path_skips_candidates_that_do_not_exist() {
        let root = installer_log_scratch_dir("skip-missing");
        let missing = root.join("install-progress.log");
        let present = root.join("runtime-host.log");
        fs::write(&present, b"the only real file").expect("write present log");

        let picked = newest_existing_log_path(&[missing, present.clone()])
            .expect("the one real candidate must be found");
        assert_eq!(picked, present);

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn newest_existing_log_path_fails_loud_and_names_every_candidate_it_checked() {
        // The exact shape `open_installer_log` propagates to the frontend
        // as a rejected promise -- AcquisitionFlow.tsx's onClick must have
        // something legible to show, not a bare "undefined".
        let root = installer_log_scratch_dir("all-missing");
        let first = root.join("install-progress.log");
        let second = root.join("runtime-host.log");

        let error = newest_existing_log_path(&[first.clone(), second.clone()])
            .expect_err("no candidate exists yet");
        assert!(error.contains("No CivicCast installer log exists yet"));
        assert!(error.contains(&first.display().to_string()));
        assert!(error.contains(&second.display().to_string()));

        let _ = fs::remove_dir_all(&root);
    }

    // No test here mutates the real `PROGRAMDATA` env var to exercise the
    // non-`_from` `newest_installer_log_path`/`installer_progress_log_path`
    // directly (parallel test threads share the process environment -- the
    // same reason `acquisition_catalog.rs`'s
    // `components_base_url_defaults_when_the_env_override_is_unset` gives
    // for the identical restraint, and why `acquisition_download_root()`
    // itself has no dedicated test either, only `acquisition_download_root_from`
    // above). `installer_progress_log_path_from` and `newest_existing_log_path`
    // together prove the real functions "by construction": each real
    // function is a one-line composition of a tested pure half with a
    // single environment read.

    // --- K1 fix: `--civiccast-activate-station` vs `--civiccast-distribution`
    // dispatch precedence. `run_native_distribution_cli` triggers on
    // `--civiccast-acquire-channel` / `--civiccast-import-station` ALONE,
    // with no awareness of `--civiccast-activate-station` -- so an
    // activation invocation (which reuses those same acquisition flags) MUST
    // be captured by `run_native_flat_activation_cli` first, or it silently
    // stages into the wrong (versioned `app/<version>`) layout via
    // `run_native_distribution_cli` instead, defeating this whole fix. This
    // exercises the REAL dispatch precedence via both guards actually in
    // place (main()'s consult-flat-activation-first ordering AND run_native_
    // distribution_cli's own early `--civiccast-activate-station` guard),
    // not the two functions' trigger conditions in isolation. ---

    #[test]
    fn activate_station_invocation_is_never_captured_by_the_distribution_cli() {
        let args = vec![
            "--civiccast-activate-station".to_string(),
            "--civiccast-import-station".to_string(),
            r"C:\station\station-index.json".to_string(),
            "--install-root".to_string(),
            r"C:\CivicCast".to_string(),
            "--cache-root".to_string(),
            r"C:\CivicCast\packs\.station-cache".to_string(),
        ];

        // The belt-and-braces guard: run_native_distribution_cli must defer
        // (return None) even though the args also satisfy ITS OWN trigger
        // (--civiccast-import-station), because --civiccast-activate-station
        // is present.
        assert_eq!(
            run_native_distribution_cli(&args),
            None,
            "an activation invocation must never be captured by the versioned \
             distribution CLI, or it stages into app/<version> instead of flat"
        );
        // run_native_flat_activation_cli must be the one that actually
        // handles it (Some(_) -- the exact exit code is irrelevant here;
        // this args list has no real station bundle on disk, so it fails
        // during acquisition, but it must still be THIS command that fails,
        // not silence from having never run).
        assert!(
            run_native_flat_activation_cli(&args).is_some(),
            "the activation CLI must claim this invocation"
        );
    }

    #[test]
    fn detects_successful_health_response() {
        // Chain J (2026-08-02): this test exercises the REAL comparison
        // health_response_is_ok makes against the live CIVICCAST_VERSION
        // constant, so its fixtures are built FROM that constant via
        // format! rather than a re-typed literal.
        //
        // <installer-path-audit MA-07> Every fixture below now carries the
        // readiness pair as well. That is the point of the finding: this
        // probe decides whether the installer declares its lane READY, and
        // a 200 with a matching version is LIVENESS ONLY -- app.py's own
        // /health docstring says so. The old fixtures proved a station
        // ready over a body that never mentioned readiness or schema
        // currency at all.
        const READY: &str = "\"status\":\"healthy\",\"schema\":\"current\"";
        const HEAD: &str = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n";

        assert!(health_response_is_ok(
            &format!("{HEAD}{{{READY},\"version\":\"{CIVICCAST_VERSION}\"}}"),
            None,
            None,
        ));
        // The Gate A run 33681670855 body: a 200, a matching version, and a
        // station serving 500s. The pre-MA-07 probe accepted it.
        assert!(!health_response_is_ok(
            &format!(
                "{HEAD}{{\"status\":\"degraded\",\"schema\":\"behind\",\"version\":\"{CIVICCAST_VERSION}\"}}"
            ),
            None,
            None,
        ));
        // Healthy but schema-behind is still not ready.
        assert!(!health_response_is_ok(
            &format!(
                "{HEAD}{{\"status\":\"healthy\",\"schema\":\"behind\",\"version\":\"{CIVICCAST_VERSION}\"}}"
            ),
            None,
            None,
        ));
        // A body that says nothing about readiness fails closed too.
        assert!(!health_response_is_ok(
            &format!("{HEAD}{{\"version\":\"{CIVICCAST_VERSION}\"}}"),
            None,
            None,
        ));
        assert!(!health_response_is_ok(
            &format!(
                "HTTP/1.1 503 Service Unavailable\r\n\r\n{{{READY},\"version\":\"{CIVICCAST_VERSION}\"}}"
            ),
            None,
            None,
        ));
        assert!(!health_response_is_ok(
            &format!("{HEAD}{{{READY},\"version\":\"2.1.0\"}}"),
            None,
            None,
        ));
        assert!(health_response_is_ok(
            &format!(
                "{HEAD}{{{READY},\"version\":\"{CIVICCAST_VERSION}\",\"bootstrap_instance_id\":\"proof-123\"}}"
            ),
            Some("proof-123"),
            None,
        ));
        assert!(!health_response_is_ok(
            &format!(
                "{HEAD}{{{READY},\"version\":\"{CIVICCAST_VERSION}\",\"bootstrap_instance_id\":\"other\"}}"
            ),
            Some("proof-123"),
            None,
        ));
        assert!(health_response_is_ok(
            &format!(
                "{HEAD}{{{READY},\"version\":\"{CIVICCAST_VERSION}\",\"runtime_build_id\":\"build-123\"}}"
            ),
            None,
            Some("build-123"),
        ));
        assert!(!health_response_is_ok(
            &format!(
                "{HEAD}{{{READY},\"version\":\"{CIVICCAST_VERSION}\",\"runtime_build_id\":\"stale\"}}"
            ),
            None,
            Some("build-123"),
        ));
        assert!(!health_response_is_ok("", None, None));
    }

    #[test]
    fn native_runtime_status_message_never_mentions_a_windows_helper() {
        // The retired WSL product's status messages talked about "the
        // Windows helper CivicCast needs" (the WSL/Ubuntu distro). The
        // native product has no such dependency and must never say so.
        for is_healthy in [true, false] {
            let (_lane_id, _status, message) = native_runtime_status_message(is_healthy);
            assert!(!message.contains("Windows helper"));
        }
    }

    #[test]
    fn command_line_arg_detection_handles_quoted_windows_arguments() {
        let args = vec![
            "\"--help\"".to_string(),
            "\"--civiccast-runtime-host\"".to_string(),
        ];

        assert!(command_line_has_arg(&args, "--help"));
        assert!(command_line_has_arg(&args, "--civiccast-runtime-host"));
        assert!(!command_line_has_arg(&args, "--other"));
    }

    #[test]
    fn command_line_arg_detection_rejects_substring_matches() {
        let args = vec![
            "--not--civiccast-runtime-host".to_string(),
            "--civiccast-runtime-host=false".to_string(),
        ];

        assert!(!command_line_has_arg(&args, "--civiccast-runtime-host"));
    }

    #[test]
    fn installer_state_candidates_stay_in_current_user_locations() {
        let primary = PathBuf::from(r"C:\Users\tester\.civiccast\installer-state.json");
        let legacy = PathBuf::from(r"C:\Users\tester\AppData\Local\CivicCast\installer-state.json");
        let candidates = current_user_installer_state_candidate_paths(
            primary.clone(),
            Some(PathBuf::from(r"C:\Users\tester")),
        );

        assert_eq!(candidates, vec![primary, legacy]);
        assert!(!candidates.iter().any(|path| {
            path.to_string_lossy()
                .contains(r"C:\Users\other\AppData\Local\CivicCast")
        }));
    }

    #[test]
    fn primary_profile_state_outweighs_a_newer_legacy_appdata_file() {
        let primary = PathBuf::from(r"C:\Users\tester\.civiccast\installer-state.json");
        let redirected_legacy = PathBuf::from(
            r"C:\Users\tester\AppData\Local\Packages\Host.Package\LocalCache\Local\CivicCast\installer-state.json",
        );
        let selected = select_installer_state_candidate(
            primary.clone(),
            true,
            vec![(UNIX_EPOCH + Duration::from_secs(60), redirected_legacy)],
        );

        assert_eq!(selected, Some(primary));
    }

    #[test]
    fn json_escape_neutralises_quotes_and_control_characters() {
        assert_eq!(json_escape("plain"), "plain");
        assert_eq!(json_escape("a\"b"), "a\\\"b");
        assert_eq!(json_escape("a\\b"), "a\\\\b");
        assert_eq!(json_escape("l1\nl2\r\tt"), "l1\\nl2\\r\\tt");
        // A value that itself looks like JSON must not break the envelope the
        // installer writes into civiccast.env.
        assert_eq!(json_escape("\"}; evil"), "\\\"}; evil");
    }

    #[test]
    fn validate_local_console_url_allows_only_local_operator_hosts() {
        assert!(validate_local_console_url("http://127.0.0.1:8000/operator/").is_ok());
        assert!(validate_local_console_url("http://localhost:8000/operator/").is_ok());
        assert!(validate_local_console_url("http://127.0.0.1:5173/").is_ok());
        // Refuse remote hosts, other ports, https, non-http schemes, and the
        // "prefix.evil.com" trick -- the installer must never be coaxed into
        // opening an attacker URL.
        assert!(validate_local_console_url("http://evil.example.com/").is_err());
        assert!(validate_local_console_url("http://127.0.0.1:9999/").is_err());
        assert!(validate_local_console_url("https://127.0.0.1:8000/").is_err());
        assert!(validate_local_console_url("file:///etc/passwd").is_err());
        assert!(validate_local_console_url("http://127.0.0.1:8000.evil.com/").is_err());
    }



    #[test]
    fn retry_reruns_provisioning_for_every_runtime_bootstrap_lane() {
        for lane in ["runtime", "ffmpeg", "storage", "service", "dashboard"] {
            assert!(is_runtime_bootstrap_lane(lane));
        }
        assert!(!is_runtime_bootstrap_lane("unknown-lane"));
    }

    #[test]
    fn installer_state_reader_removes_a_windows_powershell_utf8_bom() {
        assert_eq!(
            normalize_installer_state_text("\u{feff}{\"status\":\"running\"}".to_string()),
            "{\"status\":\"running\"}"
        );
        assert_eq!(
            normalize_installer_state_text("{\"status\":\"ready\"}".to_string()),
            "{\"status\":\"ready\"}"
        );
    }










    #[test]
    fn blocking_installer_commands_stay_async_offloaded() {
        // TE-2 / QA-6 (gate-civiccast): the two installer commands that do
        // multi-minute or network-blocking work MUST be async so their bodies run
        // on the blocking pool (spawn_blocking), off Tauri's UI/message-pump
        // thread. These bounds are a COMPILE-TIME guard: an async fn satisfies
        // `-> impl Future`, a plain sync `fn -> Result<..>` does not, so reverting
        // either command to synchronous breaks the build here -- the "Not
        // Responding" freeze fix cannot silently regress.
        fn requires_async3<F, Fut>(_: F)
        where
            F: Fn(tauri::AppHandle, String, String) -> Fut,
            Fut: std::future::Future,
        {
        }
        fn requires_async0<F, Fut>(_: F)
        where
            F: Fn() -> Fut,
            Fut: std::future::Future,
        {
        }
        requires_async3(run_local_installer_action);
        requires_async0(read_local_installer_state);
    }

    // -----------------------------------------------------------------
    // Component acquisition (download experience): pure-logic coverage
    // only. Unlike the rest of this module, `run_single_acquisition_component`
    // / `persist_acquisition_progress` / `retry_acquisition_component_blocking`
    // are deliberately NOT exercised here -- they write the REAL
    // installer-state.json under this machine's own USERPROFILE (exactly like
    // every other write_installer_state call site in this file, none of
    // which are unit tested at that I/O boundary either). The pure decision
    // logic each of them is built from IS covered directly below; the
    // engine's own network/disk mechanics are already covered by
    // component_acquisition.rs's fixture-server test suite, and the store's
    // own JSON-shape/upsert logic is covered by acquisition_state.rs's tests.
    // -----------------------------------------------------------------

    #[test]
    fn classify_acquisition_error_maps_all_five_engine_variants() {
        use component_acquisition::AcquisitionError;

        let (kind, detail) =
            classify_acquisition_error(&AcquisitionError::NetworkFailed("dns failure".to_string()))
                .expect("a network failure is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::NetworkFailed);
        assert_eq!(detail, "dns failure");

        let (kind, detail) =
            classify_acquisition_error(&AcquisitionError::ResumeInvalid("bad content-range".to_string()))
                .expect("a resume failure is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::ResumeInvalid);
        assert_eq!(detail, "bad content-range");

        let (kind, detail) = classify_acquisition_error(&AcquisitionError::HashMismatch {
            path: PathBuf::from("artifact.bin"),
            expected: "deadbeef".to_string(),
            actual: "cafef00d".to_string(),
        })
        .expect("a hash mismatch is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::HashMismatch);
        assert_eq!(detail, "expected deadbeef, got cafef00d");

        let (kind, _detail) =
            classify_acquisition_error(&AcquisitionError::DiskFull(std::io::ErrorKind::Other))
                .expect("a disk-full is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::DiskFull);

        let (kind, detail) = classify_acquisition_error(&AcquisitionError::SourceNotFound(
            "https://example.invalid/asset.ccpack".to_string(),
        ))
        .expect("a 404 is a classified failure");

        // G011.3: the operator's own cancel is the ONE outcome that must not
        // be classified as a failure at all. Every member of the frontend's
        // AcquisitionErrorKind union names a fault with a remedy attached, so
        // any of them would put a red line with a suggested fix in front of
        // someone who simply asked CivicCast to stop.
        assert!(classify_acquisition_error(&AcquisitionError::Canceled).is_none());
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::SourceNotFound);
        assert_eq!(detail, "https://example.invalid/asset.ccpack");
    }

    #[test]
    fn expected_bytes_total_reads_pinned_bytes_and_treats_unverified_as_unknown() {
        assert_eq!(
            expected_bytes_total(&component_acquisition::ExpectedArtifact::Pinned {
                bytes: 1_500_000_000,
                sha256: "deadbeef".to_string(),
            }),
            Some(1_500_000_000)
        );
        assert_eq!(
            expected_bytes_total(&component_acquisition::ExpectedArtifact::Unverified),
            None
        );
    }

    #[test]
    fn acquisition_persist_fields_preserves_the_existing_lane_status_and_message() {
        let existing = r#"{"current_lane_id":"dashboard","status":"running","message":"Setting up.","reboot_required":true}"#;
        let (lane_id, status, message, reboot_required) = acquisition_persist_fields(Some(existing));
        assert_eq!(lane_id, "dashboard");
        assert_eq!(status, "running");
        assert_eq!(message, "Setting up.");
        assert!(reboot_required);
    }

    #[test]
    fn acquisition_persist_fields_defaults_to_a_synthetic_acquisition_lane_when_nothing_exists_yet() {
        let (lane_id, status, message, reboot_required) = acquisition_persist_fields(None);
        assert_eq!(lane_id, "acquisition");
        assert_eq!(status, "downloading");
        assert_eq!(message, "CivicCast is downloading the components it needs.");
        assert!(!reboot_required);
    }

    #[test]
    fn acquisition_success_state_distinguishes_a_real_download_from_an_offline_first_hit() {
        assert_eq!(
            acquisition_success_state(true),
            acquisition_state::AcquisitionComponentState::Complete
        );
        assert_eq!(
            acquisition_success_state(false),
            acquisition_state::AcquisitionComponentState::FoundLocally
        );
    }

    // -----------------------------------------------------------------
    // BLOCKER #54 fix: the production catalog driver (audit-lite
    // FINDING-001). `run_single_acquisition_component_with_persist` always
    // takes a test-supplied `fn()` persister so these tests never write this
    // machine's real installer-state.json (see that function's doc, and the
    // module doc above this block for why the REST of this file's
    // acquisition tests avoid real I/O the same way).
    // -----------------------------------------------------------------

    fn noop_persist_for_tests() {}

    #[test]
    fn try_start_acquisition_once_is_true_exactly_once_then_false_forever() {
        let started = std::sync::atomic::AtomicBool::new(false);
        assert!(
            try_start_acquisition_once(&started),
            "the first caller must win and be told to start the driver"
        );
        assert!(
            !try_start_acquisition_once(&started),
            "a second caller must be told the driver already started"
        );
        assert!(
            !try_start_acquisition_once(&started),
            "a third caller must still be told the driver already started"
        );
    }

    #[test]
    fn acquisition_selection_keeps_required_ids_and_omits_unselected_large() {
        let selected = selected_production_component_ids(&[
            "app_runtime".to_string(),
            "server_binaries".to_string(),
            "captions_medium".to_string(),
            "local_ai_model".to_string(),
        ])
        .expect("deliverable selections are accepted");
        assert_eq!(
            selected,
            ["app_runtime", "server_binaries", "captions_medium", "local_ai_model"]
                .map(str::to_string)
        );

        let selected_with_large = selected_production_component_ids(&[
            "app_runtime".to_string(),
            "captions_large".to_string(),
        ])
        .expect("Large is a deliverable optional component");
        assert!(selected_with_large.contains(&"captions_large".to_string()));
        assert!(selected_with_large.contains(&"captions_medium".to_string()));
        assert!(!selected.contains(&"captions_large".to_string()));
    }

    #[test]
    fn acquisition_selection_rejects_non_deliverable_or_unknown_ids() {
        assert!(selected_production_component_ids(&["media_tools".to_string()]).is_err());
        assert!(selected_production_component_ids(&["unexpected".to_string()]).is_err());
    }

    #[test]
    fn component_bytes_total_hint_sums_pinned_items_but_is_none_if_any_item_is_unverified() {
        let all_pinned = acquisition_catalog::CatalogComponent {
            id: "test-only-hint-all-pinned".to_string(),
            items: vec![
                acquisition_catalog::CatalogItem {
                    source: component_acquisition::ComponentSource::HuggingFaceFile {
                        repo: "unused/unused".to_string(),
                        revision: "0".repeat(40),
                        path: "a.bin".to_string(),
                    },
                    expected: component_acquisition::ExpectedArtifact::Pinned {
                        bytes: 100,
                        sha256: "deadbeef".to_string(),
                    },
                    destination: PathBuf::from("a.bin"),
                    staged_at: Vec::new(),
                    trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
                },
                acquisition_catalog::CatalogItem {
                    source: component_acquisition::ComponentSource::HuggingFaceFile {
                        repo: "unused/unused".to_string(),
                        revision: "0".repeat(40),
                        path: "b.bin".to_string(),
                    },
                    expected: component_acquisition::ExpectedArtifact::Pinned {
                        bytes: 250,
                        sha256: "cafef00d".to_string(),
                    },
                    destination: PathBuf::from("b.bin"),
                    staged_at: Vec::new(),
                    trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
                },
            ],
        };
        assert_eq!(component_bytes_total_hint(&all_pinned), Some(350));

        let mut mixed = all_pinned.clone();
        mixed.items.push(acquisition_catalog::CatalogItem {
            source: component_acquisition::ComponentSource::GitHubReleaseAsset {
                base_url: "https://example.invalid/download".to_string(),
                asset_name: "pack.ccpack".to_string(),
            },
            expected: component_acquisition::ExpectedArtifact::Unverified,
            destination: PathBuf::from("pack.ccpack"),
            staged_at: Vec::new(),
            trust: acquisition_catalog::AcquisitionTrust::Pack {
                trust: native_packs::PackTrust {
                    key_id: "k".to_string(),
                    public_key: ed25519_dalek::SigningKey::from_bytes(&[1_u8; 32]).verifying_key(),
                },
                expected_component: "c".to_string(),
                expected_product_version: "1".to_string(),
                expected_compatible_core: "1".to_string(),
            },
        });
        assert_eq!(
            component_bytes_total_hint(&mixed),
            None,
            "one unverified (pack) item makes the whole component's total unknown"
        );
    }

    /// Field failure, candidate #16 (4eca729): `local_ai_model` was already
    /// fully satisfied on disk (station activation's own
    /// `compose_ollama_model_store` output) but the GUI showed it "Waiting"
    /// because the strictly sequential driver
    /// (`run_acquisition_components`) had not reached it yet -- an earlier,
    /// unconditional, optional catalog entry (`captions_large` /
    /// `cuda_runtime`) was stuck on a bad connection ahead of it. This test
    /// proves the fix's core property directly: an already-satisfied
    /// component is recognized regardless of an EARLIER, unsatisfied
    /// component's state -- `locally_satisfied_progress_rows` never has to
    /// wait its turn.
    #[test]
    fn locally_satisfied_progress_rows_finds_a_satisfied_component_behind_an_unsatisfied_one() {
        let root = std::env::temp_dir().join(format!(
            "civiccast-main-prescan-order-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir scratch");

        fn sha256_hex(bytes: &[u8]) -> String {
            use sha2::{Digest, Sha256};
            let mut digest = Sha256::new();
            digest.update(bytes);
            format!("{:x}", digest.finalize())
        }

        // Stands in for `captions_large`/`cuda_runtime`: nothing on disk at
        // its destination or any staged_at candidate, so it is NOT locally
        // satisfied -- exactly the shape of an optional component the
        // offline kit never staged, which would need a real (possibly
        // stuck) network transfer to resolve.
        let stuck_ahead = acquisition_catalog::CatalogComponent {
            id: "test-only-prescan-stuck-ahead".to_string(),
            items: vec![acquisition_catalog::CatalogItem {
                source: component_acquisition::ComponentSource::HuggingFaceFile {
                    repo: "unused/unused".to_string(),
                    revision: "0".repeat(40),
                    path: "never-staged.bin".to_string(),
                },
                expected: component_acquisition::ExpectedArtifact::Pinned {
                    bytes: 12345,
                    sha256: "0".repeat(64),
                },
                destination: root.join("never-staged.bin"),
                staged_at: Vec::new(),
                trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
            }],
        };

        // Stands in for `local_ai_model`: already present at its
        // staged_at candidate (station activation's composed store),
        // matching bytes and sha256 -- fully satisfied without ever
        // touching the network.
        let body = b"already installed by station activation".to_vec();
        let staged_path = root.join("merged-store").join("already-installed.bin");
        fs::create_dir_all(staged_path.parent().unwrap()).expect("mkdir staged parent");
        fs::write(&staged_path, &body).expect("pre-stage the merged-store file");
        let already_satisfied_behind = acquisition_catalog::CatalogComponent {
            id: "test-only-prescan-already-satisfied-behind".to_string(),
            items: vec![acquisition_catalog::CatalogItem {
                source: component_acquisition::ComponentSource::HuggingFaceFile {
                    repo: "unused/unused".to_string(),
                    revision: "0".repeat(40),
                    path: "already-installed.bin".to_string(),
                },
                expected: component_acquisition::ExpectedArtifact::Pinned {
                    bytes: body.len() as u64,
                    sha256: sha256_hex(&body),
                },
                // Never written at `destination` itself -- only reachable
                // via the second `staged_at` candidate, mirroring
                // `local_ai_model_items`'s merged-store candidate.
                destination: root.join("never-written-download-dest.bin"),
                staged_at: vec![staged_path],
                trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
            }],
        };

        let components = vec![stuck_ahead, already_satisfied_behind];
        let rows = locally_satisfied_progress_rows(&components);

        assert_eq!(
            rows.len(),
            1,
            "only the already-satisfied component should produce a row, got: {rows:?}"
        );
        assert_eq!(rows[0].id, "test-only-prescan-already-satisfied-behind");
        assert_eq!(
            rows[0].state,
            acquisition_state::AcquisitionComponentState::FoundLocally
        );

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_single_acquisition_component_aggregates_multiple_pinned_items_into_one_found_locally_progress_entry(
    ) {
        let root = std::env::temp_dir().join(format!(
            "civiccast-main-acquisition-aggregate-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir scratch");

        fn sha256_hex(bytes: &[u8]) -> String {
            use sha2::{Digest, Sha256};
            let mut digest = Sha256::new();
            digest.update(bytes);
            format!("{:x}", digest.finalize())
        }

        let body_a = b"first pinned file bytes".to_vec();
        let body_b = b"second pinned file, a little longer than the first one".to_vec();
        let destination_a = root.join("a.bin");
        let destination_b = root.join("b.bin");
        fs::write(&destination_a, &body_a).expect("pre-stage file a");
        fs::write(&destination_b, &body_b).expect("pre-stage file b");

        let component_id = format!("test-only-aggregate-{}", std::process::id());
        let component = acquisition_catalog::CatalogComponent {
            id: component_id.clone(),
            items: vec![
                acquisition_catalog::CatalogItem {
                    source: component_acquisition::ComponentSource::HuggingFaceFile {
                        repo: "unused/unused".to_string(),
                        revision: "0".repeat(40),
                        path: "a.bin".to_string(),
                    },
                    expected: component_acquisition::ExpectedArtifact::Pinned {
                        bytes: body_a.len() as u64,
                        sha256: sha256_hex(&body_a),
                    },
                    destination: destination_a,
                    staged_at: Vec::new(),
                    trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
                },
                acquisition_catalog::CatalogItem {
                    source: component_acquisition::ComponentSource::HuggingFaceFile {
                        repo: "unused/unused".to_string(),
                        revision: "0".repeat(40),
                        path: "b.bin".to_string(),
                    },
                    expected: component_acquisition::ExpectedArtifact::Pinned {
                        bytes: body_b.len() as u64,
                        sha256: sha256_hex(&body_b),
                    },
                    destination: destination_b,
                    staged_at: Vec::new(),
                    trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
                },
            ],
        };

        run_single_acquisition_component_with_persist(&component, noop_persist_for_tests);

        let snapshot = acquisition_state::snapshot_json().expect("store has entries");
        let expected_total = body_a.len() + body_b.len();
        assert!(
            snapshot.contains(&format!(
                "\"id\":\"{component_id}\",\"state\":\"found_locally\",\"bytes_done\":{expected_total},\"bytes_total\":{expected_total}"
            )),
            "expected one aggregate found_locally entry summing both items, got: {snapshot}"
        );

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn run_pack_item_falls_through_to_acquire_and_verify_pack_when_no_valid_pack_exists_at_the_destination(
    ) {
        // No destination file exists, and the source cannot even resolve to
        // a URL (empty base_url) -- proving this reaches
        // component_acquisition::acquire_and_verify_pack (not merely
        // returning early on the offline-first check) without needing live
        // network, the same style component_acquisition.rs's own
        // `ensure_component_available_falls_through_to_the_download_engine_
        // when_nothing_verifies_locally` test uses.
        let root = std::env::temp_dir().join(format!(
            "civiccast-main-run-pack-item-fallthrough-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir scratch");

        let signing_key = ed25519_dalek::SigningKey::from_bytes(&[3_u8; 32]);
        let trust = native_packs::PackTrust {
            key_id: "test-key".to_string(),
            public_key: signing_key.verifying_key(),
        };
        let item = acquisition_catalog::CatalogItem {
            source: component_acquisition::ComponentSource::GitHubReleaseAsset {
                base_url: String::new(),
                asset_name: String::new(),
            },
            expected: component_acquisition::ExpectedArtifact::Unverified,
            destination: root.join("never-lands.ccpack"),
            staged_at: Vec::new(),
            trust: acquisition_catalog::AcquisitionTrust::Pack {
                trust: trust.clone(),
                expected_component: "whatever".to_string(),
                expected_product_version: "1.0.0-rc15".to_string(),
                expected_compatible_core: "1.0.0-rc15".to_string(),
            },
        };
        let error = run_pack_item(
            &item,
            &trust,
            "whatever",
            "1.0.0-rc15",
            "1.0.0-rc15",
            &component_acquisition::NoopProgress,
        )
        .expect_err("no offline-verified pack and an unresolvable source must fall through to the download engine");
        match error {
            component_acquisition::AcquisitionError::NetworkFailed(reason) => {
                assert!(reason.contains("base URL or asset name"));
            }
            other => panic!("expected NetworkFailed from resolve_url via acquire_and_verify_pack, got {other:?}"),
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn mark_production_catalog_ids_errored_marks_exactly_the_production_ids() {
        mark_production_catalog_ids_errored(
            &acquisition_catalog::PRODUCTION_CATALOG_IDS.map(str::to_string),
            "no embedded pack signing key in this test build",
        );
        let snapshot = acquisition_state::snapshot_json().expect("store has entries");
        for id in acquisition_catalog::PRODUCTION_CATALOG_IDS {
            assert!(
                snapshot.contains(&format!("\"id\":\"{id}\",\"state\":\"error\"")),
                "expected {id} to be marked error, got: {snapshot}"
            );
            assert!(
                snapshot.contains("\"kind\":\"network_failed\""),
                "expected the fail-loud reason to classify as network_failed (no sixth error kind \
                 exists on the frontend contract), got: {snapshot}"
            );
        }
    }

    #[test]
    fn acquisition_driver_smoke_test_on_a_background_thread_downloads_over_localhost_and_the_store_reports_it(
    ) {
        // The public entry points this driver calls in production
        // (component_acquisition::acquire_and_verify_pack ->
        // download_component) enforce HTTPS -- correctly, and this test
        // does not weaken that. The plain-HTTP localhost fixture server
        // below stands in for the transport step ONLY, through
        // component_acquisition's existing `#[cfg(test)]`-only seam
        // (`download_from_url_for_tests`, matching `download_from_url`'s
        // own doc comment: "so tests can drive it against a plain-HTTP
        // localhost fixture server without weakening the HTTPS-only
        // posture"). Everything after the transport -- verification
        // (`native_packs::verify_pack`, the SAME chain
        // `run_pack_item`/`acquire_and_verify_pack` use), the background
        // thread, the store aggregation, and the final reported state -- is
        // the REAL, unmodified production driver
        // (`run_single_acquisition_component_with_persist`). No live
        // internet: everything here is 127.0.0.1.
        use ed25519_dalek::{Signer, SigningKey};
        use std::io::{BufRead, BufReader};

        // Held for the whole transfer: the cancel tests below flip a
        // process-wide flag this download's read loop consults, and
        // `cargo test` runs both on parallel threads in one process.
        let _serialized = ACQUISITION_CANCEL_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());

        fn sha256_hex(bytes: &[u8]) -> String {
            use sha2::{Digest, Sha256};
            let mut digest = Sha256::new();
            digest.update(bytes);
            format!("{:x}", digest.finalize())
        }

        fn build_signed_pack(
            pack_path: &Path,
            signing_key: &SigningKey,
            component: &str,
            payload: &[(&str, &[u8])],
        ) {
            let mut files_json = Vec::new();
            let mut total_bytes = 0_u64;
            for (name, bytes) in payload {
                files_json.push(serde_json::json!({
                    "path": name,
                    "bytes": bytes.len(),
                    "sha256": sha256_hex(bytes),
                }));
                total_bytes += bytes.len() as u64;
            }
            // Source-bound components must carry the source SHA the verifier
            // enforces in production (SOURCE_BOUND_COMPONENTS in native_packs.rs).
            let test_source_sha = "a".repeat(40);
            let metadata_value = if component == "native-app-payload" {
                serde_json::json!({
                    "source_sha": test_source_sha,
                    "civiccast_source_head": test_source_sha,
                })
            } else if component == "native-server-binaries" {
                serde_json::json!({ "source_sha": test_source_sha })
            } else {
                serde_json::json!({})
            };
            let manifest_value = serde_json::json!({
                "schema_version": 1,
                "product": "civiccast-native",
                "component": component,
                "product_version": "1.0.0-rc15",
                "compatible_core": "1.0.0-rc15",
                "signing_key_id": "test-key",
                "file_count": payload.len(),
                "total_bytes": total_bytes,
                "files": files_json,
                "metadata": metadata_value,
            });
            let manifest_bytes = native_packs::canonical_json(&manifest_value)
                .expect("canonicalize test manifest")
                .into_bytes();
            let signature = signing_key.sign(&manifest_bytes);
            let signature_b64 = {
                use base64::Engine;
                base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
            };

            let file = fs::File::create(pack_path).expect("create pack file");
            let mut writer = zip::ZipWriter::new(file);
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Stored);
            writer
                .start_file("manifest.json", options)
                .expect("start manifest entry");
            writer.write_all(&manifest_bytes).expect("write manifest");
            writer
                .start_file("manifest.sig", options)
                .expect("start signature entry");
            writer
                .write_all(signature_b64.as_bytes())
                .expect("write signature");
            for (name, bytes) in payload {
                writer
                    .start_file(format!("payload/{name}"), options)
                    .expect("start payload entry");
                writer.write_all(bytes).expect("write payload bytes");
            }
            writer.finish().expect("finish pack zip");
        }

        fn serve_once(body: Vec<u8>) -> (SocketAddr, thread::JoinHandle<()>) {
            let listener = TcpListener::bind("127.0.0.1:0").expect("bind fixture server");
            let addr = listener.local_addr().expect("read fixture server addr");
            let handle = thread::spawn(move || {
                let (mut stream, _) = listener.accept().expect("accept fixture connection");
                let mut reader = BufReader::new(stream.try_clone().expect("clone fixture stream"));
                let mut line = String::new();
                loop {
                    line.clear();
                    reader.read_line(&mut line).expect("read fixture request line");
                    if line.trim().is_empty() {
                        break;
                    }
                }
                let header = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                stream.write_all(header.as_bytes()).expect("write fixture header");
                stream.write_all(&body).expect("write fixture body");
            });
            (addr, handle)
        }

        let root = std::env::temp_dir().join(format!(
            "civiccast-main-acquisition-driver-smoke-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("mkdir scratch");

        let signing_key = SigningKey::from_bytes(&[42_u8; 32]);
        let trust = native_packs::PackTrust {
            key_id: "test-key".to_string(),
            public_key: signing_key.verifying_key(),
        };
        let source_pack_path = root.join("source-pack.ccpack");
        build_signed_pack(
            &source_pack_path,
            &signing_key,
            "test-only-driver-smoke",
            &[("bin/tool.exe", b"pretend binary bytes")],
        );
        let pack_bytes = fs::read(&source_pack_path).expect("read built pack");

        let (addr, server_handle) = serve_once(pack_bytes);
        let url = format!("http://{addr}/pack.ccpack");
        let destination = root.join("landed.ccpack");
        let component_id = format!("test-only-driver-smoke-{}", std::process::id());

        let driver_handle = thread::spawn({
            let destination = destination.clone();
            let component_id = component_id.clone();
            let trust = trust.clone();
            move || {
                // Real transport, over a real TCP connection to the
                // localhost fixture server above (see this test's header
                // comment for why the HTTPS-gated public entry point is not
                // used for this one step).
                component_acquisition::download_from_url_for_tests(
                    &url,
                    &destination,
                    &component_acquisition::ExpectedArtifact::Unverified,
                    &component_acquisition::NoopProgress,
                )
                .expect("localhost fixture download succeeds");

                // From here on, the REAL, unmodified production driver: an
                // already-landed, already-verifiable pack at `destination`
                // is exactly the offline-first state `run_pack_item` checks
                // for (via `native_packs::verify_pack`, the SAME chain
                // production uses) before ever calling
                // `acquire_and_verify_pack` again.
                let component = acquisition_catalog::CatalogComponent {
                    id: component_id,
                    items: vec![acquisition_catalog::CatalogItem {
                        source: component_acquisition::ComponentSource::GitHubReleaseAsset {
                            base_url: "https://example.invalid/unused".to_string(),
                            asset_name: "unused.ccpack".to_string(),
                        },
                        expected: component_acquisition::ExpectedArtifact::Unverified,
                        destination,
                        staged_at: Vec::new(),
                        trust: acquisition_catalog::AcquisitionTrust::Pack {
                            trust,
                            expected_component: "test-only-driver-smoke".to_string(),
                            expected_product_version: "1.0.0-rc15".to_string(),
                            expected_compatible_core: "1.0.0-rc15".to_string(),
                        },
                    }],
                };
                run_single_acquisition_component_with_persist(&component, noop_persist_for_tests);
            }
        });
        driver_handle.join().expect("driver thread completes");
        server_handle.join().expect("fixture server thread completes");

        let snapshot = acquisition_state::snapshot_json().expect("store has entries after the driver ran");
        assert!(
            snapshot.contains(&format!("\"id\":\"{component_id}\",\"state\":\"found_locally\"")),
            "expected the localhost-downloaded, signature-verified pack to be reported found_locally \
             by the real driver running on a background thread, got: {snapshot}"
        );
        assert!(destination.is_file(), "the downloaded pack must remain on disk");

        let _ = fs::remove_dir_all(&root);
    }

    /// Serializes every test that touches `component_acquisition`'s
    /// process-wide cancel flag against every test that runs a real transfer.
    /// `cargo test` runs test functions on parallel threads in ONE process,
    /// and the cancel flag is deliberately process-wide (it cancels "the
    /// first-run download", not one file) -- so without this, a cancel test
    /// could stop the localhost fixture download above mid-flight.
    static ACQUISITION_CANCEL_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// G011.3. Cancel must stop the run BETWEEN components too, not only
    /// mid-transfer: a cancel that lands while a component is verifying (or
    /// in the gap before the next one starts) must not let the next
    /// multi-gigabyte download begin anyway.
    #[test]
    fn a_requested_cancel_stops_the_driver_before_the_next_component_starts() {
        let _serialized = ACQUISITION_CANCEL_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());

        let component_id = "test-only-cancel-before-next-component";
        // A source that would fail loudly (and slowly) if it were ever
        // attempted -- reaching it at all is the failure this test detects.
        let component = acquisition_catalog::CatalogComponent {
            id: component_id.to_string(),
            items: vec![acquisition_catalog::CatalogItem {
                source: component_acquisition::ComponentSource::GitHubReleaseAsset {
                    base_url: "https://example.invalid/never-reached".to_string(),
                    asset_name: "never-reached.ccpack".to_string(),
                },
                expected: component_acquisition::ExpectedArtifact::Unverified,
                destination: std::env::temp_dir().join("civiccast-cancel-test-never-written"),
                staged_at: Vec::new(),
                trust: acquisition_catalog::AcquisitionTrust::PinnedFile,
            }],
        };

        // A row already in flight, exactly as the driver would have left it.
        acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
            id: component_id.to_string(),
            state: acquisition_state::AcquisitionComponentState::Downloading,
            bytes_done: 4096,
            bytes_total: Some(500_000),
            elapsed_seconds: 2,
            error: None,
        });

        component_acquisition::request_cancel();
        run_acquisition_components(std::slice::from_ref(&component));
        component_acquisition::clear_cancel();

        let snapshot = acquisition_state::snapshot_json().expect("store has entries");
        assert!(
            snapshot.contains(&format!(
                "\"id\":\"{component_id}\",\"state\":\"canceled\",\"bytes_done\":4096"
            )),
            "expected the in-flight row to be reported canceled with its partial bytes \
             preserved (Resume continues from them), got: {snapshot}"
        );
        assert!(
            !snapshot.contains("\"id\":\"test-only-cancel-before-next-component\",\"state\":\"error\""),
            "a cancel the operator asked for must never be reported as an error, got: {snapshot}"
        );
        assert!(
            !component_acquisition::cancel_requested(),
            "the test must leave the process-wide flag clear"
        );
    }

    /// G011.3. Resuming after a cancel must clear the latch first -- otherwise
    /// the resumed transfer cancels itself again on the download loop's very
    /// first buffer-boundary check and the row is permanently wedged.
    #[test]
    fn resuming_a_canceled_component_clears_the_cancel_latch() {
        let _serialized = ACQUISITION_CANCEL_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());

        component_acquisition::request_cancel();
        assert!(component_acquisition::cancel_requested());

        // No config was ever remembered for this id, so this takes the
        // "report honestly rather than pretend" branch and performs no
        // transfer -- but it must still have cleared the latch.
        let message = retry_acquisition_component_blocking(
            "test-only-retry-clears-cancel-latch".to_string(),
        )
        .expect("retry reports a message rather than failing");
        assert!(!message.is_empty());
        assert!(
            !component_acquisition::cancel_requested(),
            "retry/resume must clear the cancel latch before running anything"
        );
    }
}

#[tauri::command(rename_all = "camelCase")]
async fn read_local_installer_state() -> Result<String, String> {
    // QA-6 (gate-civiccast): the live installer polls this every 2s through the
    // whole multi-minute first-run, and reconcile_decision runs a blocking TCP
    // /health probe (up to ~4s connect+read). A synchronous Tauri command runs on
    // the main thread and pumps the window's message loop, so a slow /health could
    // stall the UI -- the same "Not Responding" class run_local_installer_action
    // was fixed for. Offload the I/O + network work to the blocking pool.
    tauri::async_runtime::spawn_blocking(read_local_installer_state_blocking)
        .await
        .map_err(|err| format!("CivicCast could not read the installer state: {err}"))?
}

fn read_local_installer_state_blocking() -> Result<String, String> {
    let Some(path) = newest_existing_installer_state_path()? else {
        return Ok("null".to_string());
    };
    let raw = normalize_installer_state_text(
        fs::read_to_string(&path)
            .map_err(|error| format!("Could not read installer state file: {error}"))?,
    );
    // Persisted "ready" is a claim, not a lifetime guarantee. Reconcile both
    // directions against live health. Reboot-pending states are exempt
    // inside the transition core because a previous installation may still
    // answer during reboot work.
    let transition = if installer_state_reboot_required(&raw) {
        RuntimeStateTransition::None
    } else {
        let expected_build_id = headless_bundled_runtime_build_id();
        runtime_state_transition(
            &raw,
            service_health_reachable_once(None, expected_build_id.as_deref()),
        )
    };
    let lane = installer_state_string_field(&raw, "current_lane_id")
        .unwrap_or_else(|| "runtime".to_string());
    match transition {
        RuntimeStateTransition::MarkReady => {
            let _ = write_installer_state(
                &lane,
                "ready",
                "CivicCast is running and healthy on this computer.",
                false,
            );
        }
        RuntimeStateTransition::MarkUnavailable => {
            let _ = write_installer_state(
                &lane,
                "unavailable",
                "CivicCast is not responding. The background runtime host is attempting recovery.",
                false,
            );
        }
        RuntimeStateTransition::None => return Ok(raw),
    }
    if let Some(reconciled_path) = newest_existing_installer_state_path()? {
        if let Ok(reconciled) = fs::read_to_string(&reconciled_path) {
            return Ok(reconciled);
        }
    }
    Ok(raw)
}

#[tauri::command(rename_all = "camelCase")]
fn reset_local_installer_state() -> Result<String, String> {
    for path in installer_state_candidate_paths()? {
        if path.exists() {
            fs::remove_file(&path)
                .map_err(|error| format!("Could not remove installer state file: {error}"))?;
        }
    }
    Ok("CivicCast reset installer progress. Durable records were not deleted.".to_string())
}

/// Opens the newest installer log ([`newest_installer_log_path`]) in the
/// OS's own DEFAULT handler for that file type, the same way
/// [`open_operator_console`] opens a URL in the default browser -- never a
/// hardcoded application. The prior implementation spawned `notepad.exe`
/// directly, which is a second bug on top of the missing-candidate one
/// (field report 2026-08-28, candidate 9d4477b): a station without
/// `notepad.exe` on `PATH`, or with `.log` reassociated to a different
/// viewer, would have the button fail even once the log itself existed.
/// `cmd.exe /C start "" <path>` is the SAME shell-default-handler idiom
/// [`open_operator_console`] already uses for URLs; the empty `""` is the
/// `start` command's window-title placeholder, required so a path is never
/// misparsed as the title.
#[tauri::command(rename_all = "camelCase")]
fn open_installer_log() -> Result<String, String> {
    let path = newest_installer_log_path()?;
    let path_display = path.display().to_string();
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let status = Command::new("cmd.exe")
            .args(["/C", "start", "", &path_display])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map_err(|error| format!("Could not open the installer log: {error}"))?;
        if status.success() {
            return Ok(format!("Opened the CivicCast installer log: {path_display}"));
        }
        return Err(format!(
            "Windows could not open the installer log; cmd.exe exited with {status}."
        ));
    }
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg(&path)
            .status()
            .map_err(|error| format!("Could not open the installer log: {error}"))?;
        if status.success() {
            return Ok(format!("Opened the CivicCast installer log: {path_display}"));
        }
        return Err(format!(
            "macOS could not open the installer log; open exited with {status}."
        ));
    }
    #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
    {
        let status = Command::new("xdg-open")
            .arg(&path)
            .status()
            .map_err(|error| format!("Could not open the installer log: {error}"))?;
        if status.success() {
            Ok(format!("Opened the CivicCast installer log: {path_display}"))
        } else {
            Err(format!(
                "Could not open the installer log: xdg-open exited with {status}."
            ))
        }
    }
}

#[tauri::command]
async fn native_hardware_inventory() -> Result<hardware_inventory::HardwareInventory, String> {
    // Real Win32/DXGI calls can take a moment (DXGI factory/adapter
    // enumeration in particular); offload to the blocking pool so this
    // command never stalls the window's message loop, matching
    // read_local_installer_state's rationale above.
    tauri::async_runtime::spawn_blocking(hardware_inventory::collect_hardware_inventory)
        .await
        .map_err(|error| format!("CivicCast could not collect the hardware inventory: {error}"))
}

// ---------------------------------------------------------------------------
// Component acquisition (download experience) -- drives
// component_acquisition.rs's engine per configured component, writing
// progress into acquisition_state.rs's store, and persists that store into
// the polled installer-state JSON (write_installer_state, above) so the
// frontend's existing 2s/500ms poll picks it up with no new IPC channel.
// ---------------------------------------------------------------------------

fn expected_bytes_total(expected: &component_acquisition::ExpectedArtifact) -> Option<u64> {
    match expected {
        component_acquisition::ExpectedArtifact::Pinned { bytes, .. } => Some(*bytes),
        component_acquisition::ExpectedArtifact::Unverified => None,
    }
}

/// Maps the engine's five typed errors onto acquisition_state's mirror of
/// them, carrying the engine's own message through as `detail` (see that
/// field's doc comment: never shown verbatim on screen, so no attempt is
/// made here to soften it for an operator).
/// Map an engine error onto the frontend's typed error contract, or `None`
/// when the outcome is NOT a failure at all.
///
/// `None` today means exactly one thing: [`component_acquisition::
/// AcquisitionError::Canceled`], the operator's own "Stop downloading"
/// (G011.3). Returning an `Option` rather than forcing a `kind` onto it is
/// the point -- `types.ts`'s `AcquisitionErrorKind` union has seven members
/// and every one of them names a fault with a remedy attached, so any of them
/// would put a red failure line with a suggested fix in front of an operator
/// who simply asked CivicCast to stop.
fn classify_acquisition_error(
    error: &component_acquisition::AcquisitionError,
) -> Option<(acquisition_state::AcquisitionErrorKind, String)> {
    use acquisition_state::AcquisitionErrorKind;
    use component_acquisition::AcquisitionError;
    Some(match error {
        AcquisitionError::Canceled => return None,
        AcquisitionError::NetworkFailed(reason) => (AcquisitionErrorKind::NetworkFailed, reason.clone()),
        AcquisitionError::ResumeInvalid(reason) => (AcquisitionErrorKind::ResumeInvalid, reason.clone()),
        AcquisitionError::HashMismatch { expected, actual, .. } => (
            AcquisitionErrorKind::HashMismatch,
            format!("expected {expected}, got {actual}"),
        ),
        AcquisitionError::DiskFull(kind) => (AcquisitionErrorKind::DiskFull, format!("{kind:?}")),
        AcquisitionError::PermissionDenied(kind) => {
            (AcquisitionErrorKind::PermissionDenied, format!("{kind:?}"))
        }
        AcquisitionError::WriteFailed(kind) => {
            (AcquisitionErrorKind::WriteFailed, format!("{kind:?}"))
        }
        AcquisitionError::SourceNotFound(location) => {
            (AcquisitionErrorKind::SourceNotFound, location.clone())
        }
    })
}

/// The seam between the engine's Tauri-agnostic `ProgressObserver` and the
/// polled installer-state file: every byte-level callback updates the store
/// and best-effort persists it. `invoked` records whether any callback ever
/// fired, which is how the caller (see below) distinguishes an
/// offline-first "already verified locally" success (no bytes moved, no
/// callback) from a real download. `bytes_offset` is the byte count already
/// accounted for by earlier items within the SAME multi-item component
/// (`captions_medium`'s four HuggingFace files, or `local_ai_model`'s
/// manifest + config blob + layer blobs) -- see
/// `run_single_acquisition_component` -- so a component with several
/// underlying downloads still reports ONE monotonically-advancing progress
/// entry to the frontend rather than resetting to zero each file. `persist`
/// is a plain function pointer (never a capturing closure, so this stays
/// `Send` for free) letting tests observe the in-memory store without
/// writing this machine's real installer-state.json -- production always
/// passes [`persist_acquisition_progress_best_effort`].
struct AcquisitionStoreObserver {
    component_id: String,
    bytes_total_hint: Option<u64>,
    bytes_offset: u64,
    invoked: std::sync::Arc<std::sync::atomic::AtomicBool>,
    persist: fn(),
}

impl component_acquisition::ProgressObserver for AcquisitionStoreObserver {
    fn on_progress(&self, progress: component_acquisition::DownloadProgress) {
        self.invoked.store(true, std::sync::atomic::Ordering::SeqCst);
        acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
            id: self.component_id.clone(),
            state: acquisition_state::AcquisitionComponentState::Downloading,
            bytes_done: self.bytes_offset.saturating_add(progress.bytes_done),
            // The component-level total (summed across every item up front by
            // the caller) wins when known -- see this field's doc above.
            // Otherwise (a single-item signed pack, whose true size this
            // engine never has pinned ahead of time) fall back to whatever
            // this item's own transfer just learned (e.g. a resumed
            // download's Content-Range total), rather than reporting an
            // unknown total the whole time a real one is available.
            bytes_total: self.bytes_total_hint.or(progress.bytes_total),
            elapsed_seconds: progress.elapsed.as_secs(),
            error: None,
        });
        (self.persist)();
    }
}

fn persist_acquisition_progress_best_effort() {
    let _ = persist_acquisition_progress();
}

/// The pure decision behind [`persist_acquisition_progress`]: given the
/// existing installer-state.json's raw text (if any), what lane/status/
/// message/reboot fields the next write should carry so an acquisition-only
/// update never clobbers a lane transition another part of the app is mid-way
/// through. Split out so this preservation behavior is unit-testable without
/// touching this machine's real installer-state.json (see the module tests
/// above for why the rest of this I/O path deliberately is not).
fn acquisition_persist_fields(existing_raw: Option<&str>) -> (String, String, String, bool) {
    match existing_raw {
        Some(raw) => (
            installer_state_string_field(raw, "current_lane_id")
                .unwrap_or_else(|| "acquisition".to_string()),
            installer_state_string_field(raw, "status").unwrap_or_else(|| "downloading".to_string()),
            installer_state_string_field(raw, "message")
                .unwrap_or_else(|| "CivicCast is downloading the components it needs.".to_string()),
            installer_state_reboot_required(raw),
        ),
        None => (
            "acquisition".to_string(),
            "downloading".to_string(),
            "CivicCast is downloading the components it needs.".to_string(),
            false,
        ),
    }
}

/// Persists the acquisition store's current snapshot into installer-state.json
/// WITHOUT disturbing whatever lane/status/message another part of the app
/// last wrote there -- this can be called from a download in progress that
/// has no lane-transition context of its own (and, for the earliest calls in
/// a fresh install, no installer-state.json may exist yet at all).
fn persist_acquisition_progress() -> Result<(), String> {
    let existing = newest_existing_installer_state_path()?
        .map(|path| fs::read_to_string(&path))
        .transpose()
        .map_err(|error| format!("Could not read installer state file: {error}"))?
        .map(normalize_installer_state_text);
    let (lane_id, status, message, reboot_required) =
        acquisition_persist_fields(existing.as_deref());
    write_installer_state(&lane_id, &status, &message, reboot_required)
}

/// The pure decision behind [`run_single_acquisition_component`]'s success
/// branch: whether the engine actually streamed any bytes (`invoked`) tells
/// us "downloaded now" (`complete`) apart from "already present and verified"
/// (`found_locally`, the offline-first short-circuit in
/// `component_acquisition::ensure_component_available`, which never touches
/// the observer at all).
fn acquisition_success_state(invoked: bool) -> acquisition_state::AcquisitionComponentState {
    if invoked {
        acquisition_state::AcquisitionComponentState::Complete
    } else {
        acquisition_state::AcquisitionComponentState::FoundLocally
    }
}

/// The total bytes a whole (possibly multi-item) catalog component will
/// transfer, when every item's size is known up front. `None` the moment
/// ANY item's size is unknown (today: the two signed packs, downloaded as
/// `ExpectedArtifact::Unverified` -- their true size is not pinned anywhere
/// this engine can read before the transfer starts) -- reported to the
/// frontend as "size unknown" rather than a misleadingly partial total.
fn component_bytes_total_hint(component: &acquisition_catalog::CatalogComponent) -> Option<u64> {
    let mut total: u64 = 0;
    for item in &component.items {
        total = total.checked_add(expected_bytes_total(&item.expected)?)?;
    }
    Some(total)
}

/// Runs one signed-pack item: an offline-first short circuit (a file already
/// at `item.destination` that re-verifies against the SAME signed-manifest
/// expectations is accepted untouched, exactly `native_pack_staging`'s own
/// `AlreadySatisfied` posture for the separate NSIS-hook staging path, and
/// this driver's own idempotency for a second `start_acquisition` call or a
/// retry over an already-complete component), else downloads and verifies
/// via the EXISTING chain (`component_acquisition::acquire_and_verify_pack`,
/// which itself calls `native_packs::verify_pack` -- never a second,
/// parallel verifier).
/// The offline-first candidate loop [`run_pack_item`] runs before ever
/// touching the network, pulled out so a caller can run the SAME no-network
/// check on its own -- the signed-pack counterpart to
/// `component_acquisition::locally_verified_pinned_path`, used by the same
/// pre-pass (`prescan_locally_satisfied_components`). Chain H1: the download
/// destination and the installer-staged location are no longer the same
/// folder, so BOTH are offered to the SAME signed manifest verifier. The
/// staged copy is what the R7 log already reported as "Found locally --
/// verified" for app_runtime and server_binaries; moving the download root
/// must not lose that, and must not cause a re-download into the writable
/// root of something already delivered.
fn locally_verified_pack_path(
    item: &acquisition_catalog::CatalogItem,
    trust: &native_packs::PackTrust,
    expected_component: &str,
    expected_product_version: &str,
    expected_compatible_core: &str,
) -> Option<PathBuf> {
    for candidate in std::iter::once(&item.destination).chain(item.staged_at.iter()) {
        if native_packs::verify_pack(
            candidate,
            trust,
            Some(expected_component),
            Some(expected_product_version),
            Some(expected_compatible_core),
        )
        .is_ok()
        {
            return Some(candidate.clone());
        }
    }
    None
}

fn run_pack_item(
    item: &acquisition_catalog::CatalogItem,
    trust: &native_packs::PackTrust,
    expected_component: &str,
    expected_product_version: &str,
    expected_compatible_core: &str,
    observer: &dyn component_acquisition::ProgressObserver,
) -> Result<(), component_acquisition::AcquisitionError> {
    if locally_verified_pack_path(
        item,
        trust,
        expected_component,
        expected_product_version,
        expected_compatible_core,
    )
    .is_some()
    {
        return Ok(());
    }
    component_acquisition::acquire_and_verify_pack(
        &item.source,
        &item.destination,
        trust,
        expected_component,
        expected_product_version,
        expected_compatible_core,
        observer,
    )
    .map(|_verified| ())
}

/// The no-network counterpart to [`run_catalog_item`]: true when `item`
/// already resolves locally (staged by the elevated installer or by station
/// activation) under the SAME verification each trust kind's real driver
/// uses, without ever constructing a download. Used only by
/// [`prescan_locally_satisfied_components`].
fn catalog_item_locally_satisfied(item: &acquisition_catalog::CatalogItem) -> bool {
    match &item.trust {
        acquisition_catalog::AcquisitionTrust::PinnedFile => {
            component_acquisition::locally_verified_pinned_path(
                &item.destination,
                &item.staged_at,
                &item.expected,
            )
            .is_some()
        }
        acquisition_catalog::AcquisitionTrust::Pack {
            trust,
            expected_component,
            expected_product_version,
            expected_compatible_core,
        } => locally_verified_pack_path(
            item,
            trust,
            expected_component,
            expected_product_version,
            expected_compatible_core,
        )
        .is_some(),
    }
}

/// True when EVERY item in `component` is already satisfied on disk -- see
/// [`catalog_item_locally_satisfied`].
fn component_locally_satisfied(component: &acquisition_catalog::CatalogComponent) -> bool {
    !component.items.is_empty()
        && component
            .items
            .iter()
            .all(catalog_item_locally_satisfied)
}

/// Field failure, candidate #16 (4eca729): the fixed production catalog
/// contains optional, large components that may not be staged by the
/// offline kit. The UI used to pass no selection, so they always ran before
/// `local_ai_model`; on a poor connection an unselected component could wait
/// for hours before the already-staged AI model was reported.
///
/// This pass runs the exact same no-network local checks
/// (`catalog_item_locally_satisfied`) the sequential driver itself would
/// use once it reaches each selected component, over the selected list up
/// front, and persists `found_locally` immediately for anything that already
/// resolves -- so the GUI reflects real on-disk state within milliseconds of
/// the driver starting. It does not skip or weaken later verification: the
/// sequential loop below still visits every selected component in production
/// catalog order and performs its own real (network-capable) run when it gets
/// there, which re-confirms the same
/// local match at negligible cost for anything already marked here. No
/// hash or signature check is bypassed by this pass -- it only moves an
/// already-passing check's write earlier.
/// Pure half of [`prescan_locally_satisfied_components`]: the `found_locally`
/// progress rows for every already-satisfied component in `components`, in
/// order. Separated out the same way `acquisition_state::upsert_into` is
/// separated from `acquisition_state::upsert` -- so a test can exercise the
/// actual satisfaction logic without touching the process-wide
/// `ACQUISITION_STORE` other parallel `cargo test` threads also share.
fn locally_satisfied_progress_rows(
    components: &[acquisition_catalog::CatalogComponent],
) -> Vec<acquisition_state::AcquisitionComponentProgress> {
    components
        .iter()
        .filter(|component| component_locally_satisfied(component))
        .map(|component| acquisition_state::AcquisitionComponentProgress {
            id: component.id.clone(),
            state: acquisition_state::AcquisitionComponentState::FoundLocally,
            bytes_done: component_bytes_total_hint(component).unwrap_or(0),
            bytes_total: component_bytes_total_hint(component),
            elapsed_seconds: 0,
            error: None,
        })
        .collect()
}

fn prescan_locally_satisfied_components(components: &[acquisition_catalog::CatalogComponent]) {
    let rows = locally_satisfied_progress_rows(components);
    if rows.is_empty() {
        return;
    }
    for row in rows {
        acquisition_state::upsert(row);
    }
    let _ = persist_acquisition_progress();
}

/// Runs one catalog item (dispatching on its [`acquisition_catalog::AcquisitionTrust`]),
/// returning the byte count it contributed once successful -- `None` when
/// unknown (a signed pack; see [`component_bytes_total_hint`]), so the
/// caller can advance the running `bytes_offset` for the NEXT item in a
/// multi-item component.
fn run_catalog_item(
    item: &acquisition_catalog::CatalogItem,
    observer: &dyn component_acquisition::ProgressObserver,
) -> Result<(), component_acquisition::AcquisitionError> {
    match &item.trust {
        acquisition_catalog::AcquisitionTrust::PinnedFile => {
            component_acquisition::ensure_component_available(
                &item.destination,
                &item.staged_at,
                &item.source,
                &item.expected,
                observer,
            )
            .map(|_| ())
        }
        acquisition_catalog::AcquisitionTrust::Pack {
            trust,
            expected_component,
            expected_product_version,
            expected_compatible_core,
        } => run_pack_item(
            item,
            trust,
            expected_component,
            expected_product_version,
            expected_compatible_core,
            observer,
        ),
    }
}

/// Runs one catalog component through the engine: marks it `downloading`
/// (with its aggregate pinned size, when every item's size is known), runs
/// each underlying item in order (see [`run_catalog_item`]), then records
/// the outcome (`complete`, `found_locally`, or a typed `error`) as ONE
/// progress entry -- a multi-item component (`captions_medium`,
/// `local_ai_model`) is never split into several rows the frontend would
/// have to reassemble.
/// `persist` lets tests observe the in-memory store without writing this
/// machine's real installer-state.json (see [`AcquisitionStoreObserver`]'s
/// doc); production always calls [`run_single_acquisition_component`], which
/// supplies the real persister.
fn run_single_acquisition_component_with_persist(
    component: &acquisition_catalog::CatalogComponent,
    persist: fn(),
) {
    let bytes_total_hint = component_bytes_total_hint(component);
    acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
        id: component.id.clone(),
        state: acquisition_state::AcquisitionComponentState::Downloading,
        bytes_done: 0,
        bytes_total: bytes_total_hint,
        elapsed_seconds: 0,
        error: None,
    });
    persist();

    let started = std::time::Instant::now();
    let invoked = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut bytes_offset: u64 = 0;

    for item in &component.items {
        let observer = AcquisitionStoreObserver {
            component_id: component.id.clone(),
            bytes_total_hint,
            bytes_offset,
            invoked: std::sync::Arc::clone(&invoked),
            persist,
        };
        match run_catalog_item(item, &observer) {
            Ok(()) => {
                bytes_offset = bytes_offset.saturating_add(expected_bytes_total(&item.expected).unwrap_or(0));
            }
            Err(error) => {
                // G011.3: a stop the operator asked for is NOT a failure.
                // `classify_acquisition_error` returns None for it, and it
                // gets its own state, no error object, and no operator-facing
                // error copy -- and it stops the whole run, not just this
                // component, because "Stop downloading" is what was asked
                // for.
                let classified = classify_acquisition_error(&error);
                let state = match &classified {
                    Some(_) => acquisition_state::AcquisitionComponentState::Error,
                    None => acquisition_state::AcquisitionComponentState::Canceled,
                };
                acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
                    id: component.id.clone(),
                    state,
                    bytes_done: bytes_offset,
                    bytes_total: bytes_total_hint,
                    elapsed_seconds: started.elapsed().as_secs(),
                    error: classified.map(|(kind, detail)| {
                        acquisition_state::AcquisitionComponentError { kind, detail }
                    }),
                });
                if state == acquisition_state::AcquisitionComponentState::Canceled {
                    acquisition_state::mark_unfinished_canceled();
                }
                persist();
                return;
            }
        }
    }

    // The large-v3 tier's bytes are now downloaded and per-file hash
    // verified -- but a station that later STAGES this tier will only be
    // allowed to USE it once a receipt exists that
    // `station_runtime._validate_activation_receipt` accepts (see the
    // `finalize_captions_large_acquisition` doc for the full field-evidence
    // chain: without this step, acquiring this optional component silently
    // primes the runtime to crash-loop on its next start). Every other
    // catalog component's bytes are self-sufficient once verified, so this
    // finalize step is deliberately scoped to just this one id.
    if component.id == "captions_large" {
        if let Err(detail) = finalize_captions_large_acquisition(&acquisition_download_root()) {
            acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
                id: component.id.clone(),
                state: acquisition_state::AcquisitionComponentState::Error,
                bytes_done: bytes_total_hint.unwrap_or(bytes_offset),
                bytes_total: bytes_total_hint,
                elapsed_seconds: started.elapsed().as_secs(),
                error: Some(acquisition_state::AcquisitionComponentError {
                    kind: acquisition_state::AcquisitionErrorKind::WriteFailed,
                    detail,
                }),
            });
            persist();
            return;
        }
    }

    let state = acquisition_success_state(invoked.load(std::sync::atomic::Ordering::SeqCst));
    acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
        id: component.id.clone(),
        state,
        bytes_done: bytes_total_hint.unwrap_or(bytes_offset),
        bytes_total: bytes_total_hint,
        elapsed_seconds: started.elapsed().as_secs(),
        error: None,
    });
    persist();
}

fn run_single_acquisition_component(component: &acquisition_catalog::CatalogComponent) {
    run_single_acquisition_component_with_persist(component, persist_acquisition_progress_best_effort);
}

/// Config registry: remembers the last configuration each component id was
/// run with, so `retry_acquisition_component` (no persistent driver loop
/// exists beyond the one `start_acquisition` launches -- see that command's
/// doc) can redo just that one component without the caller resupplying its
/// source/hash/destination.
static ACQUISITION_CONFIGS: std::sync::Mutex<Vec<acquisition_catalog::CatalogComponent>> =
    std::sync::Mutex::new(Vec::new());

fn remember_acquisition_config(config: acquisition_catalog::CatalogComponent) {
    let mut configs = ACQUISITION_CONFIGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(existing) = configs.iter_mut().find(|candidate| candidate.id == config.id) {
        *existing = config;
    } else {
        configs.push(config);
    }
}

fn acquisition_config_for(component_id: &str) -> Option<acquisition_catalog::CatalogComponent> {
    let configs = ACQUISITION_CONFIGS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    configs
        .iter()
        .find(|candidate| candidate.id == component_id)
        .cloned()
}

/// The minimal sequential driver: remembers every component's config (so a
/// later single-component retry can find it), then runs each one in list
/// order. Invoked by [`start_acquisition`] with the real production catalog,
/// and directly by tests with fixture catalogs.
fn run_acquisition_components(components: &[acquisition_catalog::CatalogComponent]) {
    for component in components {
        remember_acquisition_config(component.clone());
    }
    // See `prescan_locally_satisfied_components`'s own doc: run before the
    // sequential, potentially network-blocking loop below so an
    // already-satisfied component is never left showing "Waiting" behind an
    // unrelated, still-in-progress one.
    prescan_locally_satisfied_components(components);
    for component in components {
        // G011.3: stop between components too, not only mid-transfer. A
        // cancel that arrives while a component is being verified (or in the
        // gap between two components) must not let the next multi-gigabyte
        // download start anyway.
        if component_acquisition::cancel_requested() {
            acquisition_state::mark_unfinished_canceled();
            let _ = persist_acquisition_progress();
            return;
        }
        run_single_acquisition_component(component);
    }
}

/// Where the GUI process resolves "the installer's own folder" from --
/// `current_exe()`'s parent, matching every other "next to the installer"
/// convention in this codebase (`native_pack_staging.rs`'s module doc: the
/// documented offline side-load remedy is a `packs` folder "next to the
/// installer"; `installer_shutdown_marker_paths`, above, resolves the same
/// way). Falls back to the current working directory only if the OS call
/// itself fails (practically never on Windows).
/// USED ONLY TO FIND WHAT IS ALREADY STAGED. Chain H1: this used to be the
/// download destination too, and on the INSTALLED GUI -- which runs
/// non-elevated from `C:\Program Files\CivicCast (Native)\CivicCast
/// Native.exe` -- that made first-run downloads target
/// `C:\Program Files\CivicCast (Native)\packs\...`, a folder the process
/// cannot write. R7 failed both required components at 0 bytes with
/// PermissionDenied. Downloads now go to [`acquisition_download_root`]; this
/// stays as the READ side, because packs the elevated installer staged here
/// legitimately live here and must still count as satisfied.
fn acquisition_installer_directory() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// The per-machine, non-elevated-writable acquisition root
/// (`<program_data>\CivicCast`).
///
/// Pure derivation so the contract is testable without touching the
/// environment. Deliberately the SAME root the rest of the product already
/// derives -- `civiccast.native.supervisor.install_layout`'s
/// `civiccast_data_root` and `civiccast.native.provision.__main__`'s
/// `resolve_provision_paths` both compute `<PROGRAMDATA>\CivicCast` -- so the
/// Rust writer and the Python consumers cannot land in different places.
fn acquisition_download_root_from(program_data_root: &Path) -> PathBuf {
    program_data_root.join("CivicCast")
}

/// [`acquisition_download_root_from`] against the real `%PROGRAMDATA%`.
///
/// ACL note (measured, not assumed): `C:\ProgramData` carries
/// `BUILTIN\Users:(OI)(CI)(RX)` **and** `BUILTIN\Users:(CI)(WD,AD,WEA,WA)`,
/// both of which inherit into `C:\ProgramData\CivicCast`. A non-elevated
/// interactive user can therefore create directories and files here, and
/// `CREATOR OWNER:(OI)(CI)(IO)(F)` gives them full control of whatever they
/// create -- so a resumed `.partial` is theirs to reopen. No elevation
/// broker is needed or invented. The two ProgramData subtrees that ARE
/// hardened to SYSTEM+Administrators (`data\pgdata`, the provisioning journal
/// state root) are credential-bearing and are not touched by acquisition.
///
/// Integrity does not rest on the directory's ACL: every acquired artifact is
/// re-verified against a pinned SHA-256 (caption weights, Ollama blobs) or an
/// ed25519 signed manifest (`.ccpack`) both at acquisition time and again at
/// consumption time (`station_runtime._validate_tier_model_root` re-hashes
/// every mandatory model file on every station start).
fn acquisition_download_root() -> PathBuf {
    let program_data = std::env::var("PROGRAMDATA")
        .ok()
        .map(PathBuf::from)
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    acquisition_download_root_from(&program_data)
}

/// Marks each selected production id as a typed, loud error with `reason`
/// as the detail -- used when the embedded
/// pack trust cannot be established at all (see [`run_production_acquisition`]).
/// Reuses [`component_acquisition::AcquisitionError::NetworkFailed`] /
/// [`classify_acquisition_error`] rather than inventing a sixth error kind
/// the frontend's five-variant contract (`types.ts`'s `AcquisitionErrorKind`)
/// has no slot for.
fn mark_production_catalog_ids_errored(ids: &[String], reason: &str) {
    let (kind, detail) = classify_acquisition_error(&component_acquisition::AcquisitionError::NetworkFailed(
        reason.to_string(),
    ))
    .expect("a NetworkFailed is always a classified failure, never a cancel");
    for id in ids {
        acquisition_state::upsert(acquisition_state::AcquisitionComponentProgress {
            id: id.clone(),
            state: acquisition_state::AcquisitionComponentState::Error,
            bytes_done: 0,
            bytes_total: None,
            elapsed_seconds: 0,
            error: Some(acquisition_state::AcquisitionComponentError {
                kind,
                detail: detail.clone(),
            }),
        });
    }
    let _ = persist_acquisition_progress();
}

/// The real production run: resolves the installer's own folder and the
/// embedded pack signing key, builds [`acquisition_catalog::production_catalog`],
/// and drives it. Called on a background OS thread by [`start_acquisition`],
/// never on the calling (Tauri command) thread -- a multi-gigabyte transfer
/// must never block that thread the way `read_local_installer_state`'s doc
/// comment already documents for other slow work. A missing embedded pack
/// key (impossible in a real release build -- see `native_packs::
/// embedded_pack_trust`'s doc -- but the normal state of a local `cargo run`)
/// fails loud into every affected component's own error state instead of
/// leaving the GUI stuck on "Waiting" forever, which is the exact defect
/// this whole module exists to close.
const REQUIRED_ACQUISITION_COMPONENT_IDS: [&str; 4] = [
    "app_runtime",
    "server_binaries",
    "captions_medium",
    "local_ai_model",
];

fn selected_production_component_ids(requested: &[String]) -> Result<Vec<String>, String> {
    for id in requested {
        if !acquisition_catalog::PRODUCTION_CATALOG_IDS.contains(&id.as_str()) {
            return Err(format!("Unknown or unavailable acquisition component: {id}"));
        }
    }
    Ok(acquisition_catalog::PRODUCTION_CATALOG_IDS
        .iter()
        .filter(|id| {
            REQUIRED_ACQUISITION_COMPONENT_IDS.contains(id)
                || requested.iter().any(|requested| requested.as_str() == **id)
        })
        .map(|id| (*id).to_string())
        .collect())
}

fn run_production_acquisition(component_ids: Vec<String>) {
    let installer_dir = acquisition_installer_directory();
    let download_root = acquisition_download_root();
    match native_packs::embedded_pack_trust() {
        Ok(trust) => {
            let catalog = acquisition_catalog::production_catalog(
                &installer_dir,
                &download_root,
                &trust,
                CIVICCAST_VERSION,
            );
            let selected = catalog
                .into_iter()
                .filter(|component| component_ids.contains(&component.id))
                .collect::<Vec<_>>();
            run_acquisition_components(&selected);
        }
        Err(reason) => mark_production_catalog_ids_errored(&component_ids, &reason),
    }
}

/// Idempotency guard for [`start_acquisition`]: `false` until the driver
/// thread is launched, `true` for the rest of this process's lifetime
/// (component-level retry after a failure goes through
/// `retry_acquisition_component`, not a second full driver run). A `Mutex`
/// would also work here, but a single `AtomicBool` swap is enough to make a
/// second call an unconditional, lock-free no-op.
static ACQUISITION_DRIVER_STARTED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Starts the component-download driver, called once by the frontend when
/// the downloading screen mounts (`AcquisitionFlow.tsx`'s
/// `useAcquisitionComponents`). Synchronous and near-instant on purpose: the
/// ONLY work done on the calling thread is the atomic idempotency check and
/// spawning a background `std::thread` to run [`run_production_acquisition`]
/// -- never `tauri::async_runtime::spawn_blocking`, which would still tie up
/// an async-runtime worker for the whole multi-gigabyte transfer. A second
/// call (a re-mount, a duplicate frontend invocation, or the user
/// navigating back and forward) is a documented no-op: the compare-and-swap
/// below only spawns the thread once per process. Never called from any
/// silent-install (`/S`) path -- see this module's doc header and
/// `nsis-hooks-native.nsh`, which drives D2/D4 entirely through this same
/// binary's headless `--civiccast-*` CLI flags and never launches the Tauri
/// GUI event loop at all, so this command can only ever be reached by an
/// interactive user actually looking at the downloading screen.
/// The exact compare-and-swap [`start_acquisition`] gates on, pulled out so
/// a test can drive it against its OWN fresh flag (never the real
/// process-wide [`ACQUISITION_DRIVER_STARTED`], which is one-way for the
/// life of the process and shared with every other test in this binary).
/// `true` means "you own this call, go start the driver"; `false` means
/// "someone already owns it, this call is a no-op".
fn try_start_acquisition_once(started: &std::sync::atomic::AtomicBool) -> bool {
    started
        .compare_exchange(
            false,
            true,
            std::sync::atomic::Ordering::SeqCst,
            std::sync::atomic::Ordering::SeqCst,
        )
        .is_ok()
}

#[tauri::command(rename_all = "camelCase")]
fn start_acquisition(component_ids: Vec<String>) -> Result<String, String> {
    let component_ids = selected_production_component_ids(&component_ids)?;
    if !try_start_acquisition_once(&ACQUISITION_DRIVER_STARTED) {
        return Ok("CivicCast is already downloading its components.".to_string());
    }
    thread::spawn(move || run_production_acquisition(component_ids));
    Ok("CivicCast started downloading its components.".to_string())
}

/// Stop the in-flight first-run download at the operator's request (G011.3).
///
/// Before this, cancel was wired to nothing at all: there was no command, no
/// button, and no canceled state, so an operator who realised mid-download
/// that they were on a metered connection could only kill the window --
/// leaving a `.partial` behind with nothing on screen acknowledging it.
///
/// Synchronous and near-instant on purpose, exactly like [`start_acquisition`]:
/// it sets a flag the download loop checks at its next 64 KiB buffer boundary
/// and marks every unfinished row canceled so the screen can say so on the
/// very next poll, without waiting for the transfer thread to unwind. Bytes
/// already written stay in the `.partial`, so Resume resumes.
#[tauri::command(rename_all = "camelCase")]
fn cancel_acquisition() -> Result<String, String> {
    component_acquisition::request_cancel();
    acquisition_state::mark_unfinished_canceled();
    let _ = persist_acquisition_progress();
    Ok("CivicCast stopped downloading. Nothing already downloaded was lost.".to_string())
}

#[tauri::command(rename_all = "camelCase")]
async fn retry_acquisition_component(component_id: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || retry_acquisition_component_blocking(component_id))
        .await
        .map_err(|error| format!("CivicCast could not retry the component download: {error}"))?
}

fn retry_acquisition_component_blocking(component_id: String) -> Result<String, String> {
    // G011.3: clear the cancel latch first. Resuming a component the operator
    // stopped must not immediately stop itself again on the download loop's
    // first buffer-boundary check.
    component_acquisition::clear_cancel();
    acquisition_state::mark_pending(&component_id);
    let _ = persist_acquisition_progress();
    match acquisition_config_for(&component_id) {
        Some(config) => {
            run_single_acquisition_component(&config);
            Ok(format!("Retrying {component_id}."))
        }
        // No known config yet (no driver has ever run for this id in this
        // process -- see run_acquisition_components's doc comment): report
        // that honestly rather than pretending the retry actually ran.
        None => Ok(
            "Retry is queued. CivicCast will pick this file back up on the next check.".to_string(),
        ),
    }
}

fn validate_local_console_url(url: &str) -> Result<(), String> {
    if url.starts_with("http://127.0.0.1:8000/")
        || url.starts_with("http://localhost:8000/")
        || url.starts_with("http://127.0.0.1:5173/")
    {
        return Ok(());
    }
    Err("CivicCast only opens local operator console URLs from the installer.".to_string())
}

#[tauri::command(rename_all = "camelCase")]
fn open_operator_console(url: String) -> Result<String, String> {
    validate_local_console_url(&url)?;
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let status = Command::new("cmd.exe")
            .args(["/C", "start", "", &url])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map_err(|error| format!("Could not open operator console: {error}"))?;
        if status.success() {
            return Ok("Opening the operator console.".to_string());
        }
        return Err(format!(
            "Windows could not open the operator console; cmd.exe exited with {status}."
        ));
    }
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg(&url)
            .status()
            .map_err(|error| format!("Could not open operator console: {error}"))?;
        if status.success() {
            return Ok("Opening the operator console.".to_string());
        }
        return Err(format!(
            "macOS could not open the operator console; open exited with {status}."
        ));
    }
    #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
    {
        let status = Command::new("xdg-open")
            .arg(&url)
            .status()
            .map_err(|error| format!("Could not open operator console: {error}"))?;
        if status.success() {
            return Ok("Opening the operator console.".to_string());
        }
        Err(format!(
            "Linux could not open the operator console; xdg-open exited with {status}."
        ))
    }
}

#[cfg(target_os = "windows")]
fn decode_windows_command_output(bytes: &[u8]) -> String {
    // wsl.exe emits its OWN output (`-l -v`, `--status`) as UTF-16LE, but a
    // `--exec`'d command's stdout passes through as its native bytes (UTF-8 from
    // Linux). Detect UTF-16LE structurally — ASCII-heavy UTF-16LE carries a NUL in
    // most odd byte positions — instead of guessing by an "ubuntu" content marker.
    // The content heuristic mis-decoded marker-less probe output (e.g. the WSL
    // health probe's Python output) as UTF-16, which is the rc10 log mojibake.
    let odd_nuls = bytes.iter().skip(1).step_by(2).filter(|&&b| b == 0).count();
    let odd_total = bytes.len() / 2;
    if odd_total > 0 && odd_nuls * 2 >= odd_total {
        let utf16: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
            .collect();
        return String::from_utf16_lossy(&utf16);
    }
    String::from_utf8_lossy(bytes).to_string()
}


















#[cfg(target_os = "windows")]
fn run_bounded_command(
    program: &str,
    name: &str,
    args: &[&str],
    timeout_secs: u64,
) -> Result<(i32, String), String> {
    run_bounded_command_with_progress(program, name, args, timeout_secs, |_| {})
}

#[cfg(target_os = "windows")]
fn run_bounded_command_with_progress<Progress>(
    program: &str,
    name: &str,
    args: &[&str],
    timeout_secs: u64,
    mut on_progress: Progress,
) -> Result<(i32, String), String>
where
    Progress: FnMut(u64),
{
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    // This is the shared hang-guarded invocation core (alongside the elevated
    // Run-Step and the headless Invoke-Logged): every wsl.exe call from the
    // installer process -- the ONLOGON re-run steps AND the UI status
    // pre-checks -- must route through it, because ANY wsl.exe spawn can hang
    // indefinitely on a machine with a missing/broken WSL runtime. Redirect to
    // temp files (not pipes) so large output can't deadlock the bounded wait,
    // and so we can still read what the child produced even if we have to
    // kill it.
    let temp = std::env::temp_dir();
    let unique = format!("{}-{}", std::process::id(), unix_timestamp());
    let out_path = temp.join(format!("civiccast-wsl-out-{unique}-{name}.log"));
    let err_path = temp.join(format!("civiccast-wsl-err-{unique}-{name}.log"));
    // RAII cleanup so the temp files are removed on EVERY return path (create
    // failure, spawn failure, try_wait error, timeout, success) -- matching the
    // PowerShell wrappers' try/finally. std::fs::File's Drop only closes the
    // OS handle, it does not delete the file.
    struct TempCleanup(PathBuf);
    impl Drop for TempCleanup {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.0);
        }
    }
    let _out_guard = TempCleanup(out_path.clone());
    let _err_guard = TempCleanup(err_path.clone());
    let out_file = fs::File::create(&out_path)
        .map_err(|error| format!("Could not create temp output for {name}: {error}"))?;
    let err_file = fs::File::create(&err_path)
        .map_err(|error| format!("Could not create temp error for {name}: {error}"))?;
    let mut command = Command::new(program);
    if name == "native-ollama" {
        // `ollama --version` asks OLLAMA_HOST for its SERVER version first.
        // Port zero cannot identify a listening server; this child must report
        // its own pinned client version via the exact no-daemon response.
        // Do not mutate the parent environment or disturb another application.
        command.env("OLLAMA_HOST", "http://127.0.0.1:0");
    }
    let mut child = command
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(out_file)
        .stderr(err_file)
        .spawn()
        .map_err(|error| format!("Could not run Windows helper setup step {name}: {error}"))?;

    let started = std::time::Instant::now();
    let deadline = started + Duration::from_secs(timeout_secs);
    let mut next_progress = started;
    let mut timed_out = false;
    let exit_code = loop {
        let now = std::time::Instant::now();
        if now >= next_progress {
            on_progress(now.duration_since(started).as_secs());
            next_progress = now + Duration::from_secs(3);
        }
        match child.try_wait() {
            Ok(Some(status)) => break status.code().unwrap_or(-1),
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    // Tree-kill the whole wsl.exe descendant tree (PS/.NET
                    // Kill() equivalents only reach the direct child), then reap.
                    let _ = Command::new("taskkill.exe")
                        .args(["/T", "/F", "/PID", &child.id().to_string()])
                        .creation_flags(CREATE_NO_WINDOW)
                        .output();
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break 124;
                }
                thread::sleep(Duration::from_millis(200));
            }
            Err(error) => {
                // Reap the still-running child on this rare error path too, so a
                // try_wait() failure can't abandon an unbounded wsl.exe tree.
                let _ = Command::new("taskkill.exe")
                    .args(["/T", "/F", "/PID", &child.id().to_string()])
                    .creation_flags(CREATE_NO_WINDOW)
                    .output();
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "Waiting on Windows helper step {name} failed: {error}"
                ));
            }
        }
    };

    let stdout_bytes = fs::read(&out_path).unwrap_or_default();
    let stderr_bytes = fs::read(&err_path).unwrap_or_default();
    let mut text = decode_windows_command_output(&stdout_bytes);
    let err_text = decode_windows_command_output(&stderr_bytes);
    if !err_text.is_empty() {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str(&err_text);
    }
    if timed_out {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str(&format!(
            "{name} timed out after {timeout_secs} seconds and was terminated."
        ));
    }
    Ok((exit_code, text))
}







fn executable_resource_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            roots.push(parent.join("resources"));
            roots.push(parent.to_path_buf());
        }
    }
    roots
}

fn resource_dir_from_roots(roots: Vec<PathBuf>, name: &str) -> Option<PathBuf> {
    roots
        .into_iter()
        .flat_map(|root| [root.join(name), root.join("resources").join(name)])
        .find(|candidate| candidate.exists())
}

fn resource_dir(app: &tauri::AppHandle, name: &str) -> Option<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    if let Ok(path) = app.path().resource_dir() {
        roots.push(path);
    }
    roots.extend(executable_resource_roots());
    resource_dir_from_roots(roots, name)
}

fn headless_resource_dir(name: &str) -> Option<PathBuf> {
    resource_dir_from_roots(executable_resource_roots(), name)
}

/// Start (or restart) the native runtime host and re-verify service health.
///
/// This is the "repair"/"retry"/"continue" recovery for the runtime-family
/// installer lanes (`runtime`/`ffmpeg`/`storage`/`service`/`dashboard`). The
/// retired WSL product's version of this function shelled out to
/// `headless-bootstrap.ps1` (a bundled resource script that provisioned the
/// runtime inside the WSL distro's `apt-get install`) -- that script no
/// longer ships, so this now calls the same native runtime-host launch
/// [`launch_startup_native_status_if_ready`] already uses at process start,
/// then re-probes `/health` with [`wait_for_service_health_after_runtime_start`]
/// before reporting the lane's outcome. The native product's actual
/// provisioning happens in the NSIS postinstall hook
/// (`nsis-hooks-bootstrap.nsh`) and the Windows service
/// (`CivicCastSupervisor`, registered by `native_service_registration.rs`);
/// this only starts/reverifies that already-installed service.
fn launch_civiccast_runtime_bootstrap(
    app: tauri::AppHandle,
    lane_id: String,
) -> Result<String, String> {
    write_installer_state(
        &lane_id,
        "running",
        "CivicCast is starting its background runtime host.",
        false,
    )?;
    std::thread::spawn(move || {
        let expected_build_id = app_bundled_runtime_build_id(&app);
        if let Err(error) = launch_runtime_host_process(&app) {
            let _ = write_installer_state(
                &lane_id,
                "error",
                &format!("Could not start the CivicCast runtime host: {error}"),
                false,
            );
            return;
        }
        match wait_for_service_health_after_runtime_start(None, expected_build_id.as_deref()) {
            Ok(()) => {
                let _ = write_installer_state(
                    &lane_id,
                    "ready",
                    "CivicCast is running and healthy on this computer.",
                    false,
                );
            }
            Err(error) => {
                let _ = write_installer_state(&lane_id, "error", &error, false);
            }
        }
    });
    Ok(
        "CivicCast is starting its background runtime host. Keep this window open while the dashboard starts."
            .to_string(),
    )
}





/// The (lane_id, status, message) triple a no-argument NATIVE launch should
/// report, given whether the native background service answered its health
/// check. Kept pure/testable so it can be asserted against directly, without
/// spawning the background thread or touching the network.
///
/// The health check reused here (`service_health_reachable_once`, TCP
/// 127.0.0.1:8000 `/health`) is not WSL-specific: `native_service_
/// registration.rs`'s module doc explicitly records that this port and
/// protocol are shared BY DESIGN between the WSL product's `SERVICE_HEALTH_
/// ADDR` and the native supervisor's `civiccast.native.supervisor.core.py`
/// `control_plane_port` default (`CONTROL_PLANE_PORT = 8000` there). Reusing
/// it here is not a fabricated capability; it is the one control-plane
/// health signal the two products already agree on.
///
/// GAP, reported rather than papered over: a no-argument launch has no
/// startup-time recovery of its own. If the native service is not
/// reachable, THIS reports that honestly -- it does not attempt to start,
/// repair, or recover the service. (The operator-initiated "repair"/"retry"
/// installer actions do have a recovery path -- see
/// `launch_civiccast_runtime_bootstrap` -- this is specifically about the
/// automatic, no-argument launch.) Whether a no-argument launch should also
/// auto-recover is a product-scope question for the owner, not something
/// this fix should invent.
fn native_runtime_status_message(is_healthy: bool) -> (&'static str, &'static str, &'static str) {
    if is_healthy {
        (
            "runtime",
            "ready",
            "CivicCast's native background service is running.",
        )
    } else {
        (
            "runtime",
            "unavailable",
            "CivicCast's native background service is not reachable yet.",
        )
    }
}

/// No-argument launch of the NATIVE product: report the real, currently
/// observed health of the native background service. The retired WSL
/// product used to have a startup branch here that could report a missing
/// Windows-helper setup message -- the native product has no such
/// dependency and must never tell an operator to set one up.
fn launch_startup_native_status_if_ready(app: tauri::AppHandle) {
    thread::spawn(move || {
        let expected_build_id = app_bundled_runtime_build_id(&app);
        let is_healthy = service_health_reachable_once(None, expected_build_id.as_deref());
        let (lane_id, status, message) = native_runtime_status_message(is_healthy);
        let _ = write_installer_state(lane_id, status, message, false);
    });
}



#[tauri::command(rename_all = "camelCase")]
async fn run_local_installer_action(
    app: tauri::AppHandle,
    lane_id: String,
    action: String,
) -> Result<String, String> {
    // Several of these actions block for minutes (WSL feature enablement, Ubuntu
    // provisioning, runtime bootstrap via run_bounded_command). A synchronous
    // Tauri command runs on the main thread, which pumps the window's message
    // loop, so the window goes "Not Responding" for the whole install. Offload
    // the blocking work to the blocking pool so the UI thread stays responsive.
    tauri::async_runtime::spawn_blocking(move || {
        // PE-05 (gate-civiccast): the blocking pool turns a panic in the closure
        // into an opaque JoinError whose Display is only "task panicked" -- the
        // real cause is lost. Catch it in-place so the actual panic message reaches
        // the operator/support logs instead of a generic string.
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run_local_installer_action_blocking(app, lane_id, action)
        }))
        .unwrap_or_else(|payload| {
            let cause = payload
                .downcast_ref::<String>()
                .map(String::as_str)
                .or_else(|| payload.downcast_ref::<&str>().copied())
                .unwrap_or("unknown internal error");
            Err(format!(
                "CivicCast hit an internal error running the installer action: {cause}"
            ))
        })
    })
    .await
    .map_err(|err| format!("CivicCast could not run the installer action: {err}"))?
}

fn run_local_installer_action_blocking(
    app: tauri::AppHandle,
    lane_id: String,
    action: String,
) -> Result<String, String> {
    if action == "cancel" {
        write_installer_state(
            &lane_id,
            "paused",
            "CivicCast paused this lane. Resume from this installer before the first public meeting.",
            false,
        )?;
        return Ok("CivicCast paused this lane. Resume from this installer before the first public meeting.".to_string());
    }
    if action == "reset" {
        return reset_local_installer_state();
    }
    if action == "repair" {
        if matches!(
            lane_id.as_str(),
            "runtime" | "ffmpeg" | "storage" | "service" | "dashboard"
        ) {
            return launch_civiccast_runtime_bootstrap(app, lane_id);
        }
        let message = "CivicCast queued a repair pass for this installer lane.";
        write_installer_state(&lane_id, "repair_queued", message, false)?;
        return Ok(message.to_string());
    }
    if action == "uninstall" {
        let message =
            "Use Windows Settings to uninstall CivicCast after backing up meeting records.";
        write_installer_state(&lane_id, "uninstall_requested", message, false)?;
        return Ok(message.to_string());
    }
    if action == "retry" && is_runtime_bootstrap_lane(&lane_id) {
        // A runtime error can mean provisioning never completed (missing or
        // inaccessible bundled resources, apt/venv failure, service cutover,
        // etc.). Starting the host alone cannot repair that state. The
        // headless bootstrap is idempotent and health-first, so Retry must
        // re-enter it; an already-healthy install remains a fast no-op.
        return launch_civiccast_runtime_bootstrap(app, lane_id);
    }
    if action == "continue"
        && matches!(
            lane_id.as_str(),
            "runtime" | "ffmpeg" | "storage" | "service" | "dashboard"
        )
    {
        return launch_civiccast_runtime_bootstrap(app, lane_id);
    }
    write_installer_state(
        &lane_id,
        "accepted",
        "CivicCast accepted the installer action.",
        false,
    )?;
    Ok("CivicCast accepted the installer action.".to_string())
}

fn command_line_has_arg(args: &[String], expected: &str) -> bool {
    args.iter()
        .map(|arg| arg.trim_matches('"').trim_matches('\''))
        .any(|arg| arg == expected)
}

fn command_line_arg_value(args: &[String], expected: &str) -> Option<String> {
    args.windows(2).find_map(|pair| {
        (pair[0].trim_matches('"').trim_matches('\'') == expected)
            .then(|| pair[1].trim_matches('"').trim_matches('\'').to_string())
    })
}

/// Like [`command_line_arg_value`] but collects EVERY occurrence, in order --
/// used for repeatable flags such as `--require-component`, where the
/// required-pack set must be an input the caller supplies, never a value
/// hard-coded in this binary.
fn command_line_arg_values(args: &[String], expected: &str) -> Vec<String> {
    args.windows(2)
        .filter(|pair| pair[0].trim_matches('"').trim_matches('\'') == expected)
        .map(|pair| pair[1].trim_matches('"').trim_matches('\'').to_string())
        .collect()
}

fn run_native_pack_cli(args: &[String]) -> Option<i32> {
    let verify_path = command_line_arg_value(args, "--civiccast-verify-pack");
    let import_path = command_line_arg_value(args, "--civiccast-import-pack");
    if verify_path.is_none() && import_path.is_none() {
        return None;
    }
    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(64);
        }
    };
    let expected_component = command_line_arg_value(args, "--expected-component");
    let result = if let Some(path) = import_path {
        let Some(destination) = command_line_arg_value(args, "--destination") else {
            eprintln!("--destination is required with --civiccast-import-pack.");
            return Some(64);
        };
        native_packs::verify_and_extract_pack(
            Path::new(&path),
            Path::new(&destination),
            &trust,
            expected_component.as_deref(),
            Some(CIVICCAST_VERSION),
            Some(CIVICCAST_VERSION),
        )
    } else {
        native_packs::verify_pack(
            Path::new(verify_path.as_deref().unwrap_or_default()),
            &trust,
            expected_component.as_deref(),
            Some(CIVICCAST_VERSION),
            Some(CIVICCAST_VERSION),
        )
    };
    match result {
        Ok(verified) => match serde_json::to_string_pretty(&verified) {
            Ok(rendered) => {
                println!("{rendered}");
                Some(0)
            }
            Err(error) => {
                eprintln!("Could not render native pack verification result: {error}");
                Some(65)
            }
        },
        Err(error) => {
            eprintln!("{error}");
            Some(66)
        }
    }
}

/// D2 install-time re-verification CLI (`spec-installer-lifecycle.md` D2;
/// wired into `NSIS_HOOK_POSTINSTALL` in `nsis-hooks-native.nsh` between the
/// defensive payload-presence gate and the D3 engine invocation). Two forms:
///
/// * `--civiccast-verify-install-tree TREE --manifest NAME` re-verifies a
///   laid tree (the app payload at `$INSTDIR\runtime`, or the media closure
///   at `$INSTDIR\native-runtime`) against its shipped manifest.
/// * `--civiccast-verify-pack-tree PACK --destination DIR` re-verifies an
///   already-extracted component-pack tree (caption/model packs) by
///   re-opening its ORIGINAL signed `.ccpack` and re-walking the extracted
///   directory against it -- reusing `native_packs::open_and_verify_pack` /
///   `verify_extracted_tree` directly.
fn run_native_install_verify_cli(args: &[String]) -> Option<i32> {
    let tree_path = command_line_arg_value(args, "--civiccast-verify-install-tree");
    let pack_path = command_line_arg_value(args, "--civiccast-verify-pack-tree");
    if tree_path.is_none() && pack_path.is_none() {
        return None;
    }
    let max_failures = command_line_arg_value(args, "--max-failures")
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(native_install_verify::DEFAULT_MAX_FAILURES);

    if let Some(tree) = tree_path {
        let Some(manifest) = command_line_arg_value(args, "--manifest") else {
            eprintln!("--manifest is required with --civiccast-verify-install-tree.");
            return Some(64);
        };
        return Some(
            match native_install_verify::verify_manifest_tree(
                Path::new(&tree),
                &manifest,
                &["SHA256SUMS", "LICENSE-BOM.md"],
                max_failures,
            ) {
                Ok(report) => match serde_json::to_string_pretty(&report) {
                    Ok(rendered) => {
                        println!("{rendered}");
                        0
                    }
                    Err(error) => {
                        eprintln!("Could not render D2 tree verification result: {error}");
                        65
                    }
                },
                Err(error) => {
                    eprintln!("{error}");
                    68
                }
            },
        );
    }

    let pack = pack_path.expect("pack_path is Some in this branch");
    let Some(destination) = command_line_arg_value(args, "--destination") else {
        eprintln!("--destination is required with --civiccast-verify-pack-tree.");
        return Some(64);
    };
    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(64);
        }
    };
    let expected_component = command_line_arg_value(args, "--expected-component");
    Some(
        match native_install_verify::verify_component_pack_tree(
            Path::new(&pack),
            Path::new(&destination),
            &trust,
            expected_component.as_deref(),
            Some(CIVICCAST_VERSION),
            Some(CIVICCAST_VERSION),
        ) {
            Ok(verified) => match serde_json::to_string_pretty(&verified) {
                Ok(rendered) => {
                    println!("{rendered}");
                    0
                }
                Err(error) => {
                    eprintln!("Could not render D2 pack-tree verification result: {error}");
                    65
                }
            },
            Err(error) => {
                eprintln!("{error}");
                69
            }
        },
    )
}

/// Offline-first / online-fallback native component-pack delivery CLI
/// (`plan-sub-300mb-bootstrap.md`; wired into `NSIS_HOOK_POSTINSTALL` in
/// `nsis-hooks-bootstrap.nsh` between the D2 install-time re-verification and
/// D4 provisioning -- `--civiccast-provision`'s `server_pack_path` default
/// must exist and verify before its own `PACK_VERIFIED` phase can pass). See
/// `native_pack_staging.rs`'s module doc for the full decision matrix.
///
/// After every required component's raw `.ccpack` is confirmed present and
/// verified, this also EXTRACTS each one's payload via `native_pack_staging::
/// ensure_pack_extracted` -- per-component destination decided by
/// `native_pack_staging::pack_extraction_destination`: the generic
/// `INSTDIR\packs\<component>\payload\` (the location `civiccast.native.
/// provision.__main__.resolve_provision_paths`'s `initdb_path` default,
/// `packs\native-server-binaries\payload\bin\initdb.exe`, expects) for every
/// component except `native-app-payload`, which is bridged to `INSTDIR\
/// runtime\` instead (the fixed interpreter path `native_service_
/// registration.rs`'s `provision_command`/`service_registration_command`
/// already hard-code). Idempotent and fail-closed: an already-extracted,
/// still-verified tree is left untouched; a missing or corrupt one is
/// rebuilt from the verified pack, never left partially written.
///
/// `--civiccast-stage-packs INSTALLER_DIR --install-root INSTDIR
/// [--require-component NAME]... [--optional-component NAME]...
/// [--channel-url URL] [--channel NAME]`
///
/// `--require-component` is repeatable; the required-pack set is always an
/// INPUT, never hard-coded here (see `native_pack_staging::
/// DEFAULT_REQUIRED_COMPONENTS`, which this CLI falls back to only when the
/// caller passes none at all -- today, `nsis-hooks-bootstrap.nsh` passes
/// none, so the effective required set is exactly
/// `["native-server-binaries", "native-app-payload"]`).
///
/// `--optional-component` is the same shape, one severity level down (see
/// `native_pack_staging::DEFAULT_OPTIONAL_COMPONENTS`'s doc for the
/// required/optional distinction): run through `native_pack_staging::
/// stage_optional_packs` AFTER required staging succeeds, an absent optional
/// component is simply recorded and never blocks setup, while a present but
/// untrusted one with no offline remedy still fails this command loud.
fn run_native_pack_staging_cli(args: &[String]) -> Option<i32> {
    let installer_dir = command_line_arg_value(args, "--civiccast-stage-packs")?;
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-stage-packs.");
        return Some(64);
    };
    let mut required_components = command_line_arg_values(args, "--require-component");
    if required_components.is_empty() {
        required_components = native_pack_staging::DEFAULT_REQUIRED_COMPONENTS
            .iter()
            .map(|component| component.to_string())
            .collect();
    }
    // OPTIONAL components (native_pack_staging::DEFAULT_OPTIONAL_COMPONENTS,
    // today just native-cuda-runtime): a separate, non-fatal pass over
    // native_pack_staging::stage_optional_packs, run only after required
    // staging succeeds. Absent is a normal outcome that never blocks setup;
    // present-but-untrusted with no offline remedy still fails loud --
    // "optional means may be absent, never may be untrusted".
    let mut optional_components = command_line_arg_values(args, "--optional-component");
    if optional_components.is_empty() {
        optional_components = native_pack_staging::DEFAULT_OPTIONAL_COMPONENTS
            .iter()
            .map(|component| component.to_string())
            .collect();
    }
    let channel_url = command_line_arg_value(args, "--channel-url");
    let channel = command_line_arg_value(args, "--channel").unwrap_or_else(|| "beta".to_string());

    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(64);
        }
    };
    let authority = native_service_registration::ServiceQuiescenceAuthority::new();

    let required_report = match native_pack_staging::stage_required_packs(
        Path::new(&installer_dir),
        Path::new(&install_root),
        &trust,
        &required_components,
        CIVICCAST_VERSION,
        CIVICCAST_VERSION,
        channel_url.as_deref(),
        &channel,
        &authority,
    ) {
        Ok(report) => report,
        Err(error) => {
            eprintln!("{error}");
            return Some(74);
        }
    };

    let optional_report = match native_pack_staging::stage_optional_packs(
        Path::new(&installer_dir),
        Path::new(&install_root),
        &trust,
        &optional_components,
        CIVICCAST_VERSION,
        CIVICCAST_VERSION,
        &authority,
    ) {
        Ok(report) => report,
        Err(error) => {
            eprintln!("{error}");
            return Some(75);
        }
    };

    // Delta review fix (2026-09-05): the FULL pretty manifest used to go
    // straight to stdout, which `nsis-hooks-bootstrap.nsh` pops into $1 via
    // `nsExec::ExecToStack` -- an NSIS string capped at NSIS_MAX_STRLEN
    // (1024 bytes; measured against real Gate A evidence, where a D2 report
    // line is cut at exactly that many bytes). `serde_json::json!`'s `Map`
    // is a `BTreeMap` (this crate never enables serde_json's
    // `preserve_order` feature -- confirmed absent from every crate in
    // Cargo.lock), so keys render ALPHABETICALLY: `"optional"` sorts before
    // `"required"`. A manifest with `payload_identity` entries for even a
    // handful of components already exceeds 1024 bytes, and truncation
    // lands inside/after the `optional` object -- silently dropping the
    // ENTIRE `required` object (including every required component's
    // `payload_identity`, `replaced_from_offline`, `satisfied_online`)
    // rather than trimming the identity data proportionally.
    //
    // Fix: write the FULL manifest (both reports, pretty) to a file under
    // `%ProgramData%\CivicCast\`, and print to stdout only a short, fixed
    // budget-tested summary (see `pack_staging_stdout_summary_for_five_components_fits_the_nsis_max_strlen_budget`)
    // naming that file's path plus one compact
    // `component=outcome staged=<8hex> incoming=<8hex>` token per
    // component. The NSIS hook logs the file's path (inside the summary
    // it already captures) so a full identity forensic trail is always
    // reachable, just not crammed through the same 1024-byte pipe.
    let manifest_path = acquisition_download_root().join(format!(
        "install-manifest-report-{}-{}.json",
        std::process::id(),
        unix_timestamp()
    ));
    let manifest_json = match serde_json::to_string_pretty(&serde_json::json!({
        "required": required_report,
        "optional": optional_report,
    })) {
        Ok(rendered) => rendered,
        Err(error) => {
            eprintln!("Could not render native pack staging report: {error}");
            return Some(65);
        }
    };
    if let Some(parent) = manifest_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Err(error) = fs::write(&manifest_path, &manifest_json) {
        eprintln!(
            "Could not write the full native pack staging manifest report to {}: {error}",
            manifest_path.display()
        );
        return Some(65);
    }

    let identities: Vec<&native_pack_staging::PackPayloadIdentity> = required_report
        .payload_identity
        .iter()
        .chain(optional_report.payload_identity.iter())
        .collect();
    println!(
        "{}",
        render_pack_staging_summary(&manifest_path, &identities)
    );
    Some(0)
}

/// Compact, single-line stdout summary of a pack-staging run -- see
/// `run_native_pack_staging_cli`'s doc for why this replaces printing the
/// full pretty manifest JSON directly. Names the file the FULL manifest was
/// written to, then one `component=outcome staged=<8hex> incoming=<8hex>`
/// token per component from `identities` (the caller passes both the
/// required and optional reports' `payload_identity` lists, concatenated).
fn render_pack_staging_summary(
    manifest_path: &Path,
    identities: &[&native_pack_staging::PackPayloadIdentity],
) -> String {
    let mut summary = format!("Full manifest report: {}.", manifest_path.display());
    for identity in identities {
        let staged = identity
            .staged_digest
            .as_deref()
            .map(digest_prefix8)
            .unwrap_or_else(|| "none".to_string());
        let incoming = identity
            .incoming_digest
            .as_deref()
            .map(digest_prefix8)
            .unwrap_or_else(|| "none".to_string());
        summary.push_str(&format!(
            " {}={} staged={staged} incoming={incoming};",
            identity.component, identity.outcome
        ));
    }
    summary
}

/// The first 8 hex characters of a pack's content digest -- enough to
/// eyeball-distinguish two kits in the compact stdout summary without
/// printing a full 64-character hash. Never used for any trust decision.
fn digest_prefix8(digest: &str) -> String {
    digest.get(..8).unwrap_or(digest).to_string()
}

/// D5 repair CLI (`spec-installer-lifecycle.md` D5: "re-verify current tree
/// against the signed manifest, re-lay corrupted files, re-register
/// service, never touch data"; `spec-native-beta-recovery.md` WP2 bullet:
/// "repair that detects and restores corruption in the installed
/// application, version, selector, runtime, dependency, and caption
/// trees"). For operator/service use OUTSIDE the interactive installer:
/// Tauri's NSIS "reinstall page" (the built-in interstitial that runs before
/// the normal install hook whenever setup detects an existing install --
/// see `nsis-hooks-bootstrap.nsh`'s own notes on what it reads to decide a
/// machine is "Already Installed") already re-runs the idempotent
/// POSTINSTALL chain in `nsis-hooks-bootstrap.nsh`
/// (pack staging -> D2 re-verify -> D4 provision -> D4 service/firewall
/// registration, every step already idempotent) whenever the installer
/// `.exe` is re-launched over an existing install -- Tauri's NSIS template
/// exposes no distinct MSI-style "repair" choice (only reinstall/uninstall),
/// so that re-run IS the D5 repair path for that entry point. This
/// subcommand gives the SAME repair semantics a standalone entry point that
/// does not require the interactive installer UI, for an operator or the
/// supervisor service to invoke directly.
///
/// `--civiccast-repair INSTDIR [--installer-dir DIR] [--require-component
/// NAME]...`. `--installer-dir` defaults to INSTDIR itself when omitted (no
/// side-loaded packs staged elsewhere); pass it to point repair at a
/// `packs\` folder holding side-load remedies (e.g. next to a freshly
/// downloaded installer). See `native_repair.rs`'s module doc for the full
/// per-component repair decision matrix. Exit code distinguishes the three
/// possible outcomes: `0` = every checked tree was already verified, `76` =
/// at least one tree/selector was repaired and everything now verifies,
/// `79` = at least one tree/selector could not be repaired locally (see the
/// JSON report's `detail` fields for the exact remedy).
fn run_native_repair_cli(args: &[String]) -> Option<i32> {
    let instdir = command_line_arg_value(args, "--civiccast-repair")?;
    let installer_dir =
        command_line_arg_value(args, "--installer-dir").unwrap_or_else(|| instdir.clone());
    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(64);
        }
    };
    let mut required_components = command_line_arg_values(args, "--require-component");
    if required_components.is_empty() {
        required_components = native_pack_staging::DEFAULT_REQUIRED_COMPONENTS
            .iter()
            .map(|component| component.to_string())
            .collect();
    }

    let authority = native_service_registration::ServiceQuiescenceAuthority::new();
    let report = native_repair::run_repair(
        Path::new(&instdir),
        Path::new(&installer_dir),
        &trust,
        &required_components,
        CIVICCAST_VERSION,
        CIVICCAST_VERSION,
        &authority,
    );
    let exit_code = match report.outcome {
        native_repair::OverallOutcome::AllVerified => 0,
        native_repair::OverallOutcome::Repaired => 76,
        native_repair::OverallOutcome::Unrepairable => 79,
    };
    match serde_json::to_string_pretty(&report) {
        Ok(rendered) => println!("{rendered}"),
        Err(error) => {
            eprintln!("Could not render D5 repair report: {error}");
            return Some(65);
        }
    }
    // <installer-path-audit MA-31, honesty half> Say what this verdict does
    // NOT cover, on stderr where an operator reads it, not only as a field in
    // the JSON. `AllVerified` / exit 0 is derived from pack hashes, pack
    // versions, the selector and service/firewall registration -- it says
    // nothing about the database schema or the station's activation
    // artifacts, so on a new-code-over-old-schema station (the exact machine
    // the installer-path audit is about) it reports "healthy" over a station
    // that cannot serve.
    for line in &report.not_checked {
        eprintln!("NOT CHECKED BY THIS REPAIR -- {line}");
    }
    // Repair rebuilds a tree only after stopping the supervisor service, and
    // nothing here starts it again -- re-registration is not a start. Say so,
    // or an operator reads exit 76 as "repaired and running" over a station
    // that is down.
    #[cfg(target_os = "windows")]
    if authority.stopped_for_rebuild() {
        eprintln!(
            "NOTE: the {} service was stopped so a corrupt tree could be rebuilt, and was NOT \
             restarted. Start it when the station should go back on air: sc.exe start {}",
            native_service_registration::SERVICE_NAME,
            native_service_registration::SERVICE_NAME
        );
    }
    Some(exit_code)
}

fn validate_native_model_response(
    body: &str,
    expected_model: &str,
    expected_response: &str,
) -> Result<(), String> {
    let response: serde_json::Value = serde_json::from_str(body)
        .map_err(|error| format!("Native AI self-test returned invalid JSON: {error}"))?;
    let object = response
        .as_object()
        .ok_or_else(|| "Native AI self-test response is not an object.".to_string())?;
    if object.get("model").and_then(serde_json::Value::as_str) != Some(expected_model) {
        return Err(format!(
            "Native AI self-test answered with the wrong model; expected {expected_model}."
        ));
    }
    if object.get("done").and_then(serde_json::Value::as_bool) != Some(true) {
        return Err(format!(
            "Native AI self-test did not complete for {expected_model}."
        ));
    }
    let generated = object
        .get("response")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("Native AI self-test returned no text for {expected_model}."))?
        .trim();
    if generated != expected_response {
        return Err(format!(
            "Native AI self-test response mismatch for {expected_model}; expected {expected_response:?}, got {generated:?}."
        ));
    }
    Ok(())
}

fn validate_native_caption_output(exit_code: i32, output: &str) -> Result<(), String> {
    let normalized = output.to_lowercase();
    if exit_code != 0 || !normalized.contains("fellow americans") || !normalized.contains("country")
    {
        return Err(format!(
            "Mandatory large-v3 caption inference self-test failed (exit {exit_code}): {}",
            output.trim()
        ));
    }
    Ok(())
}

/// How long the activation self-test waits for its private Ollama to answer
/// `/api/version` before giving up.
///
/// This was a hard 60 s, and it failed the signed beta.10 installer on a clean
/// Windows Sandbox (2026-10-02, Gate A clean lane: exit 67, "did not become
/// ready in 60 seconds"). Ollama 0.30.6 listens at once but does not answer
/// `/api/version` until its GPU discovery pass has finished; measured in that
/// VM (16 GB, install on a mapped folder, big CUDA libraries read over the
/// share) that took 61 s, one second over the old limit, so a machine only
/// slightly slower than the test VM (cold disk, antivirus scanning a fresh
/// multi-GB CUDA tree) failed activation and left no station. The three
/// inference requests that follow passed in 86 s, 75 s and 29 s against their
/// own 300 s limit, so readiness was the only budget that was too small. 300 s
/// matches that request timeout and Ollama's own `OLLAMA_LOAD_TIMEOUT` (5 m);
/// a healthy host still returns on the first poll, so only a slow start waits.
#[cfg(target_os = "windows")]
const NATIVE_AI_SELF_TEST_READY_TIMEOUT: Duration = Duration::from_secs(300);

#[cfg(target_os = "windows")]
struct NativeOllamaSelfTestServer {
    child: Child,
    host: String,
    stdout_path: PathBuf,
    stderr_path: PathBuf,
}

#[cfg(target_os = "windows")]
impl NativeOllamaSelfTestServer {
    fn start(staging: &Path) -> Result<Self, String> {
        use std::os::windows::process::CommandExt;

        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|error| format!("Could not reserve a native AI self-test port: {error}"))?;
        let address = listener
            .local_addr()
            .map_err(|error| format!("Could not inspect the native AI self-test port: {error}"))?;
        drop(listener);

        let unique = format!("{}-{}", std::process::id(), unix_timestamp());
        let stdout_path = std::env::temp_dir().join(format!(
            "civiccast-native-ollama-self-test-{unique}.stdout.log"
        ));
        let stderr_path = std::env::temp_dir().join(format!(
            "civiccast-native-ollama-self-test-{unique}.stderr.log"
        ));
        let stdout = fs::File::create(&stdout_path).map_err(|error| {
            format!("Could not create the native AI self-test output log: {error}")
        })?;
        let stderr = fs::File::create(&stderr_path).map_err(|error| {
            let _ = fs::remove_file(&stdout_path);
            format!("Could not create the native AI self-test error log: {error}")
        })?;
        let executable = staging.join("dependencies/ollama/ollama.exe");
        let models = staging.join("models/ollama");
        let host = address.to_string();
        let working_directory = executable
            .parent()
            .ok_or_else(|| "Staged native AI runtime has no working directory.".to_string())?;
        let child = Command::new(&executable)
            .arg("serve")
            .current_dir(working_directory)
            .env("OLLAMA_HOST", &host)
            .env("OLLAMA_MODELS", &models)
            .env("OLLAMA_NO_CLOUD", "1")
            .env("OLLAMA_KEEP_ALIVE", "0")
            .env("OLLAMA_MAX_LOADED_MODELS", "1")
            .env("OLLAMA_NUM_PARALLEL", "1")
            .env("NO_PROXY", "127.0.0.1,localhost")
            .stdin(Stdio::null())
            .stdout(stdout)
            .stderr(stderr)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|error| {
                let _ = fs::remove_file(&stdout_path);
                let _ = fs::remove_file(&stderr_path);
                format!("Could not start the staged native AI runtime: {error}")
            })?;
        let mut server = Self {
            child,
            host,
            stdout_path,
            stderr_path,
        };
        server.wait_until_ready()?;
        Ok(server)
    }

    fn wait_until_ready(&mut self) -> Result<(), String> {
        let client = reqwest::blocking::Client::builder()
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(5))
            .no_proxy()
            .build()
            .map_err(|error| format!("Could not create native AI self-test client: {error}"))?;
        let endpoint = format!("http://{}/api/version", self.host);
        let deadline = std::time::Instant::now() + NATIVE_AI_SELF_TEST_READY_TIMEOUT;
        loop {
            if let Some(status) = self
                .child
                .try_wait()
                .map_err(|error| format!("Could not inspect the native AI runtime: {error}"))?
            {
                return Err(format!(
                    "Staged native AI runtime exited before readiness ({status}). {}",
                    self.diagnostics()
                ));
            }
            if let Ok(response) = client.get(&endpoint).send() {
                if response.status().is_success() {
                    let body = response.text().map_err(|error| {
                        format!("Could not read native AI version response: {error}")
                    })?;
                    validate_native_ollama_version_response(&body)?;
                    return Ok(());
                }
            }
            if std::time::Instant::now() >= deadline {
                return Err(format!(
                    "Staged native AI runtime did not become ready in {} seconds. {}",
                    NATIVE_AI_SELF_TEST_READY_TIMEOUT.as_secs(),
                    self.diagnostics()
                ));
            }
            thread::sleep(Duration::from_millis(250));
        }
    }

    fn diagnostics(&self) -> String {
        fn tail(path: &Path) -> String {
            let bytes = fs::read(path).unwrap_or_default();
            let start = bytes.len().saturating_sub(8 * 1024);
            String::from_utf8_lossy(&bytes[start..]).trim().to_string()
        }
        let stdout = tail(&self.stdout_path);
        let stderr = tail(&self.stderr_path);
        format!("stdout={stdout:?}; stderr={stderr:?}")
    }
}

#[cfg(target_os = "windows")]
fn wait_for_child_exit_bounded(child: &mut Child, timeout: Duration) -> bool {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Err(_) => return false,
            Ok(None) if std::time::Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(50));
            }
            Ok(None) => return false,
        }
    }
}

#[cfg(target_os = "windows")]
fn terminate_native_ollama_process_tree(child: &mut Child) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let mut tree_killer = Command::new("taskkill.exe")
        .args(["/T", "/F", "/PID", &child.id().to_string()])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok();
    if let Some(killer) = tree_killer.as_mut() {
        if !wait_for_child_exit_bounded(killer, Duration::from_secs(5)) {
            let _ = killer.kill();
            let _ = wait_for_child_exit_bounded(killer, Duration::from_secs(2));
        }
    }
    if !wait_for_child_exit_bounded(child, Duration::from_secs(1)) {
        let _ = child.kill();
        let _ = wait_for_child_exit_bounded(child, Duration::from_secs(5));
    }
}

#[cfg(target_os = "windows")]
impl Drop for NativeOllamaSelfTestServer {
    fn drop(&mut self) {
        terminate_native_ollama_process_tree(&mut self.child);
        let _ = fs::remove_file(&self.stdout_path);
        let _ = fs::remove_file(&self.stderr_path);
    }
}

/// Whether an ACTIVATED (or staging) native station has `captions-large-v3`
/// on disk. Owner decision (2026-08-07, ratified): large-v3 is optional,
/// the caption FLOOR tier is mandatory -- so the pre-activation self-test
/// prefers large-v3 when it is actually staged and falls back to the
/// mandatory floor tier when it is not. Presence is judged the same way
/// `native_activation.rs::validate_staged_runtime_layout` and
/// `station_runtime.py::_staged_caption_tier_ids` judge it: the component's
/// staged directory entry, never a deeper walk here (a corrupt/partial
/// large-v3 directory is still "present" and fails loudly inside the real
/// inference run below, never silently reclassified as absent).
fn native_caption_self_test_uses_large_v3(staging: &Path) -> bool {
    fs::symlink_metadata(staging.join("components/captions-large-v3/models/faster-whisper-large-v3"))
        .is_ok()
}

fn native_caption_inference_command(staging: &Path) -> Result<(String, Vec<String>), String> {
    let runtime = staging.join("runtime/python.exe");
    let (model, audio) = if native_caption_self_test_uses_large_v3(staging) {
        (
            staging.join("components/captions-large-v3/models/faster-whisper-large-v3"),
            staging.join("components/captions-large-v3/self-test/jfk.wav"),
        )
    } else {
        (
            staging.join("packs/captions-floor/models/faster-whisper-medium"),
            staging.join("packs/captions-floor/self-test/jfk.wav"),
        )
    };
    let runtime_text = runtime
        .to_str()
        .ok_or_else(|| "Staged caption runtime path is not Unicode.".to_string())?;
    let model_text = model
        .to_str()
        .ok_or_else(|| "Staged caption model path is not Unicode.".to_string())?;
    let audio_text = audio
        .to_str()
        .ok_or_else(|| "Staged caption self-test audio path is not Unicode.".to_string())?;
    let script = concat!(
        "from faster_whisper import WhisperModel\n",
        "import sys\n",
        "model = WhisperModel(sys.argv[1], device=\"cpu\", ",
        "compute_type=\"int8\", local_files_only=True)\n",
        "segments, _ = model.transcribe(sys.argv[2], language=\"en\", ",
        "beam_size=5, vad_filter=False)\n",
        "print(\" \".join(segment.text.strip() for segment in segments))\n"
    );
    Ok((
        runtime_text.to_string(),
        vec![
            "-I".to_string(),
            "-B".to_string(),
            "-c".to_string(),
            script.to_string(),
            model_text.to_string(),
            audio_text.to_string(),
        ],
    ))
}

#[cfg(target_os = "windows")]
fn run_native_caption_inference_self_test(staging: &Path) -> Result<(), String> {
    let (runtime, arguments) = native_caption_inference_command(staging)?;
    let argument_refs: Vec<_> = arguments.iter().map(String::as_str).collect();
    let (exit_code, output) =
        run_bounded_command(&runtime, "native-caption-inference", &argument_refs, 300)?;
    validate_native_caption_output(exit_code, &output)
}

#[cfg(target_os = "windows")]
fn run_native_ai_inference_self_tests(staging: &Path) -> Result<(), String> {
    let server = NativeOllamaSelfTestServer::start(staging)?;
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(300))
        .no_proxy()
        .build()
        .map_err(|error| format!("Could not create native AI inference client: {error}"))?;
    let endpoint = format!("http://{}/api/generate", server.host);
    for (model, prompt, expected) in [
        (
            "gemma4:12b",
            "Reply with exactly CIVICCAST_OK and nothing else.",
            "CIVICCAST_OK",
        ),
        (
            "gemma4:e4b",
            "Reply with exactly CIVICCAST_FALLBACK_OK and nothing else.",
            "CIVICCAST_FALLBACK_OK",
        ),
        (
            "translategemma:4b",
            "Translate into Spanish. Return only the translation: The council meeting is open.",
            "La reunión del consejo está abierta.",
        ),
    ] {
        let request = serde_json::json!({
            "model": model,
            "prompt": prompt,
            "stream": false,
            "think": false,
            "keep_alive": 0,
            "options": {
                "temperature": 0,
                "num_predict": 64
            }
        });
        let mut response = client
            .post(&endpoint)
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(
                serde_json::to_vec(&request)
                    .map_err(|error| format!("Could not encode native AI self-test: {error}"))?,
            )
            .send()
            .map_err(|error| {
                format!(
                    "Native AI inference request failed for {model}: {error}. {}",
                    server.diagnostics()
                )
            })?;
        let status = response.status();
        let mut bytes = Vec::new();
        response
            .by_ref()
            .take(1024 * 1024 + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| {
                format!("Could not read native AI inference response for {model}: {error}")
            })?;
        if bytes.len() > 1024 * 1024 {
            return Err(format!(
                "Native AI inference response exceeded 1 MiB for {model}."
            ));
        }
        let body = String::from_utf8(bytes).map_err(|error| {
            format!("Native AI inference response was not UTF-8 for {model}: {error}")
        })?;
        if !status.is_success() {
            return Err(format!(
                "Native AI inference failed for {model} with HTTP {status}: {body}"
            ));
        }
        validate_native_model_response(&body, model, expected)?;
    }
    Ok(())
}

/// The `activation-self-test.json` document's content for `distribution`,
/// given the self-tests already ran successfully against `staging` -- pure
/// construction, no I/O. Extracted from [`write_native_activation_self_test_receipt`]
/// (K1 fix) so the flat-layout activation path
/// (`run_native_pre_activation_self_test_for_flat_activation`) can obtain the
/// exact same receipt VALUE `native_activation::activate_flat_station_with`
/// then writes atomically, instead of a second, parallel construction that
/// could drift from the versioned path's.
///
/// The receipt must describe whichever tier `run_native_caption_inference_self_test`
/// actually exercised -- never a hardcoded large-v3 literal that would lie
/// about a floor-only station's real self-test. Same detection as
/// `native_caption_inference_command`, so the receipt and the command it
/// documents can never drift apart.
#[cfg(target_os = "windows")]
fn native_activation_self_test_receipt_value(
    staging: &Path,
    distribution: &native_distribution::AcquiredDistribution,
) -> serde_json::Value {
    let caption_inference = if native_caption_self_test_uses_large_v3(staging) {
        serde_json::json!({
            "runtime": "faster-whisper 1.2.1",
            "ctranslate2": "4.8.1",
            "model": "Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "model_path": "components/captions-large-v3/models/faster-whisper-large-v3",
            "model_bin_bytes": 3087284237_u64,
            "model_bin_sha256":
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": true,
            "audio": "self-test/jfk.wav",
            "result": "passed"
        })
    } else {
        serde_json::json!({
            "runtime": "faster-whisper 1.2.1",
            "ctranslate2": "4.8.1",
            "model": "Systran/faster-whisper-medium@08e178d48790749d25932bbc082711ddcfdfbc4f",
            "model_path": "packs/captions-floor/models/faster-whisper-medium",
            "model_bin_bytes": 1527906378_u64,
            "model_bin_sha256":
                "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": true,
            "audio": "self-test/jfk.wav",
            "result": "passed"
        })
    };
    serde_json::json!({
        "schema_version": 1,
        "product": "civiccast-native",
        "product_version": CIVICCAST_VERSION,
        "distribution_index_sha256": distribution.index.sha256,
        "caption_inference": caption_inference,
        "ai_inference": {
            "runtime": "Ollama 0.30.6",
            "models": ["gemma4:12b", "gemma4:e4b", "translategemma:4b"],
            "offline_only": true,
            "result": "passed"
        }
    })
}

#[cfg(target_os = "windows")]
fn write_native_activation_self_test_receipt(
    staging: &Path,
    distribution: &native_distribution::AcquiredDistribution,
) -> Result<(), String> {
    let receipt = native_activation_self_test_receipt_value(staging, distribution);
    let rendered = serde_json::to_vec_pretty(&receipt)
        .map_err(|error| format!("Could not encode native activation receipt: {error}"))?;
    let path = staging.join("activation-self-test.json");
    let mut output = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&path)
        .map_err(|error| format!("Could not create native activation receipt: {error}"))?;
    output
        .write_all(&rendered)
        .and_then(|_| output.write_all(b"\n"))
        .and_then(|_| output.sync_all())
        .map_err(|error| format!("Could not persist native activation receipt: {error}"))
}

// ---------------------------------------------------------------------------
// Post-activation optional-tier receipt addendum (field evidence, candidate
// 4eca729, 2026-08-29)
// ---------------------------------------------------------------------------
//
// `activation-self-test.json` is written EXACTLY ONCE, by the elevated
// `d4-activate-station` NSIS step (`write_native_activation_self_test_receipt`
// above), and describes whichever caption tier was staged at THAT moment --
// almost always the mandatory floor tier alone, since `captions_large` is an
// OPTIONAL component the non-elevated post-install GUI downloads later, into
// the acquisition root (`acquisition_download_root`), never into
// `install_root` (chain H1: this process cannot write there at all).
// `station_runtime.py::_resolve_caption_tier` then finds large-v3 staged
// there and unconditionally PREFERS it over the floor tier (the "highest
// staged tier" policy) -- but the only receipt on disk still describes the
// floor tier, so `_validate_activation_receipt` correctly refuses it and the
// station cannot start (`NativeStationConfigurationError: ... receipt does
// not match this distribution`). The check is right -- a receipt naming the
// wrong tier IS untrustworthy -- the gap is that nothing ever produces a
// receipt for the newly-available tier.
//
// This closes that gap the same way the elevated activation flow proves a
// tier is real: by actually loading the just-downloaded large-v3 model with
// the SAME embedded interpreter (`<install_root>\runtime\python.exe` --
// read+execute only, this process never writes under `install_root`) and
// transcribing the SAME jfk.wav self-test fixture the MANDATORY floor tier
// already has staged at `<install_root>\packs\captions-floor\self-test\
// jfk.wav` (required, therefore always present on an activated station --
// re-verified below against `native_packs::CAPTION_SELF_TEST_BYTES`/
// `_SHA256` before use, never assumed). On success, it writes a receipt for
// JUST this tier -- schema-identical to
// `native_activation_self_test_receipt_value`'s large-v3 branch, built from
// the SAME `native_packs` constants, never a second hand-copied literal --
// into the acquisition root (`<acquisition_root>\activation-self-test.json`),
// the one place this non-elevated process CAN write. It never touches
// `install_root`'s own `station-set.json` or `activation-self-test.json`:
// the mandatory component set and its receipt stay exactly as the elevated
// installer left them, so every previously-activated component is left
// completely alone -- this only ADDS a second, tier-scoped receipt the
// runtime's own two-root search (`caption_tier_search_roots`) now also
// checks (see `station_runtime.load_native_station_environment`).

/// Where the just-downloaded large-v3 model directory lives under the
/// acquisition root, mirroring `caption_large_tier_destination`'s own
/// `components/captions-large-v3/<CAPTION_MODEL_ROOT>` convention.
#[cfg(target_os = "windows")]
fn captions_large_addendum_model_root(acquisition_root: &Path) -> PathBuf {
    acquisition_root
        .join("components")
        .join("captions-large-v3")
        .join(native_packs::CAPTION_MODEL_ROOT)
}

/// Where this addendum receipt is written -- the acquisition root itself,
/// the SAME root `station_runtime.py`'s `caption_tier_search_roots` already
/// searches for a GUI-downloaded tier's model files.
#[cfg(target_os = "windows")]
fn captions_large_addendum_receipt_path(acquisition_root: &Path) -> PathBuf {
    acquisition_root.join("activation-self-test.json")
}

/// Re-verifies the shared jfk.wav self-test fixture (borrowed read-only from
/// the mandatory floor tier's own staged pack) against its pinned identity
/// before it is fed to a live inference run -- never assume a file this
/// process did not itself just download is what its name claims.
#[cfg(target_os = "windows")]
fn verify_self_test_audio_fixture(audio_path: &Path) -> Result<(), String> {
    let mut file = fs::File::open(audio_path).map_err(|error| {
        format!(
            "Mandatory caption self-test audio fixture is missing at {}: {error}. A station \
             missing its REQUIRED floor caption tier cannot verify an optional large-v3 upgrade.",
            audio_path.display()
        )
    })?;
    let metadata = file
        .metadata()
        .map_err(|error| format!("Could not read self-test audio fixture metadata: {error}"))?;
    if metadata.len() != native_packs::CAPTION_SELF_TEST_BYTES {
        return Err(format!(
            "Self-test audio fixture at {} is {} bytes, expected {}",
            audio_path.display(),
            metadata.len(),
            native_packs::CAPTION_SELF_TEST_BYTES
        ));
    }
    let observed = native_packs::sha256_reader(&mut file, "caption self-test audio fixture")?;
    if observed != native_packs::CAPTION_SELF_TEST_SHA256 {
        return Err(format!(
            "Self-test audio fixture at {} does not match its pinned sha256 (expected {}, got \
             {observed})",
            audio_path.display(),
            native_packs::CAPTION_SELF_TEST_SHA256
        ));
    }
    Ok(())
}

/// Runs a REAL faster-whisper inference against the just-downloaded large-v3
/// model -- the same live proof `run_native_caption_inference_self_test`
/// gives the mandatory tiers at elevated activation time, retargeted at the
/// two different roots this non-elevated call site actually has (the
/// installed runtime, the acquisition-root-staged model).
#[cfg(target_os = "windows")]
fn run_captions_large_addendum_self_test(
    install_root: &Path,
    acquisition_root: &Path,
) -> Result<(), String> {
    let python = install_root.join("runtime").join("python.exe");
    let model = captions_large_addendum_model_root(acquisition_root);
    let audio = install_root
        .join("packs")
        .join("captions-floor")
        .join("self-test")
        .join("jfk.wav");
    verify_self_test_audio_fixture(&audio)?;
    let python_text = python
        .to_str()
        .ok_or_else(|| "Staged caption runtime path is not Unicode.".to_string())?;
    let model_text = model
        .to_str()
        .ok_or_else(|| "Downloaded large-v3 model path is not Unicode.".to_string())?;
    let audio_text = audio
        .to_str()
        .ok_or_else(|| "Self-test audio fixture path is not Unicode.".to_string())?;
    // Byte-for-byte the same self-test script `native_caption_inference_command`
    // uses -- never a second, drifting copy of the inference invocation.
    let script = concat!(
        "from faster_whisper import WhisperModel\n",
        "import sys\n",
        "model = WhisperModel(sys.argv[1], device=\"cpu\", ",
        "compute_type=\"int8\", local_files_only=True)\n",
        "segments, _ = model.transcribe(sys.argv[2], language=\"en\", ",
        "beam_size=5, vad_filter=False)\n",
        "print(\" \".join(segment.text.strip() for segment in segments))\n"
    );
    let arguments = ["-I", "-B", "-c", script, model_text, audio_text];
    let (exit_code, output) = run_bounded_command(
        python_text,
        "captions-large-addendum-self-test",
        &arguments,
        300,
    )?;
    validate_native_caption_output(exit_code, &output)
}

/// Builds and atomically writes the large-v3 addendum receipt, reusing the
/// installed station's OWN `product_version`/`distribution_index_sha256`
/// identity (read from `install_root\station-set.json`, never re-derived) so
/// `station_runtime._validate_activation_receipt`'s identity checks bind this
/// receipt to THIS station exactly as tightly as the primary one. Every
/// per-tier field comes from the SAME `native_packs` constants
/// `native_activation_self_test_receipt_value`'s large-v3 branch uses --
/// never a second, hand-copied literal that could drift from it.
#[cfg(target_os = "windows")]
fn write_captions_large_addendum_receipt(
    install_root: &Path,
    acquisition_root: &Path,
) -> Result<(), String> {
    let station_set_path = install_root.join("station-set.json");
    let station_set_bytes = fs::read(&station_set_path).map_err(|error| {
        format!(
            "Could not read native station-set to build the large-v3 addendum receipt: {error}"
        )
    })?;
    let station_set: serde_json::Value = serde_json::from_slice(&station_set_bytes)
        .map_err(|error| format!("Native station-set is not valid JSON: {error}"))?;
    let product_version = station_set
        .get("product_version")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "Native station-set is missing product_version".to_string())?;
    let distribution_index_sha256 = station_set
        .get("distribution_index_sha256")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "Native station-set is missing distribution_index_sha256".to_string())?;
    let (_, model_bin_bytes, model_bin_sha256) = native_packs::CAPTION_MODEL_FILES
        .iter()
        .find(|(name, _, _)| *name == "model.bin")
        .copied()
        .ok_or_else(|| "large-v3 model.bin identity is not pinned".to_string())?;
    let receipt = serde_json::json!({
        "schema_version": 1,
        "product": "civiccast-native",
        "product_version": product_version,
        "distribution_index_sha256": distribution_index_sha256,
        "caption_inference": {
            "runtime": "faster-whisper 1.2.1",
            "ctranslate2": "4.8.1",
            "model": format!(
                "{}@{}",
                native_packs::CAPTION_LARGE_TIER_MODEL_REPOSITORY,
                native_packs::CAPTION_LARGE_TIER_MODEL_REVISION,
            ),
            "model_path":
                format!("components/captions-large-v3/{}", native_packs::CAPTION_MODEL_ROOT),
            "model_bin_bytes": model_bin_bytes,
            "model_bin_sha256": model_bin_sha256,
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": true,
            "audio": "self-test/jfk.wav",
            "result": "passed"
        }
    });
    native_activation::write_json_atomically(
        &captions_large_addendum_receipt_path(acquisition_root),
        &receipt,
        "large-v3 acquisition addendum receipt",
    )
}

/// Idempotency fast-path: `true` only when an addendum receipt ALREADY on
/// disk is bound to this exact station (`product_version` +
/// `distribution_index_sha256`, read from `install_root\station-set.json`,
/// matched against the receipt's own claim) and names the pinned large-v3
/// `model.bin` identity -- mirroring the structural-completeness posture
/// `flat_activation_already_matches` already uses for the primary receipt.
/// Any read/parse failure (absent, corrupt, foreign content, station-set
/// unreadable) returns `false` and falls through to a full re-run -- never
/// a cache that can go stale silently. Real bar is the runtime's own
/// `_validate_activation_receipt`; this probe only avoids repeating an
/// expensive 300s live inference run when nothing has changed.
#[cfg(target_os = "windows")]
fn captions_large_addendum_already_matches(install_root: &Path, acquisition_root: &Path) -> bool {
    let Ok(station_set_bytes) = fs::read(install_root.join("station-set.json")) else {
        return false;
    };
    let Ok(station_set) = serde_json::from_slice::<serde_json::Value>(&station_set_bytes) else {
        return false;
    };
    let Some(expected_version) = station_set.get("product_version").and_then(serde_json::Value::as_str) else {
        return false;
    };
    let Some(expected_index_sha256) =
        station_set.get("distribution_index_sha256").and_then(serde_json::Value::as_str)
    else {
        return false;
    };
    let Ok(receipt_bytes) = fs::read(captions_large_addendum_receipt_path(acquisition_root))
    else {
        return false;
    };
    let Ok(receipt) = serde_json::from_slice::<serde_json::Value>(&receipt_bytes) else {
        return false;
    };
    let Some((_, _, expected_model_bin_sha256)) = native_packs::CAPTION_MODEL_FILES
        .iter()
        .find(|(name, _, _)| *name == "model.bin")
    else {
        return false;
    };
    receipt.get("schema_version") == Some(&serde_json::json!(1))
        && receipt.get("product") == Some(&serde_json::json!("civiccast-native"))
        && receipt.get("product_version") == Some(&serde_json::json!(expected_version))
        && receipt.get("distribution_index_sha256") == Some(&serde_json::json!(expected_index_sha256))
        && receipt
            .get("caption_inference")
            .and_then(|value| value.get("model_bin_sha256"))
            == Some(&serde_json::json!(expected_model_bin_sha256))
}

/// The full post-download step for the `captions_large` catalog component:
/// runs the live self-test then writes the addendum receipt. Called ONLY
/// after every item in that component has already downloaded and verified
/// (its per-file pinned SHA-256 checks) successfully -- see
/// `run_single_acquisition_component_with_persist`. A failure here means the
/// downloaded bytes are fine but the station still cannot safely use them
/// yet, so the caller reports the component as errored rather than
/// "complete", instead of leaving an operator with a green checkmark over a
/// tier that will crash the station on next start.
#[cfg(target_os = "windows")]
fn finalize_captions_large_acquisition(acquisition_root: &Path) -> Result<(), String> {
    let install_root = acquisition_installer_directory();
    if captions_large_addendum_already_matches(&install_root, acquisition_root) {
        return Ok(());
    }
    run_captions_large_addendum_self_test(&install_root, acquisition_root)?;
    write_captions_large_addendum_receipt(&install_root, acquisition_root)
}

#[cfg(not(target_os = "windows"))]
fn finalize_captions_large_acquisition(_acquisition_root: &Path) -> Result<(), String> {
    Ok(())
}

/// Check the executable version without requiring the daemon to be running yet.
/// Daemon readiness and real model inference remain separate activation checks.
fn native_ollama_cli_version_matches(exit_code: i32, output: &str) -> bool {
    if exit_code != 0 {
        return false;
    }
    let lines: Vec<&str> = output.trim().lines().map(str::trim).collect();
    let version = format!("ollama version is {NATIVE_OLLAMA_VERSION}");
    let client = format!("Warning: client version is {NATIVE_OLLAMA_VERSION}");
    lines == [version.as_str()]
        || lines
            == [
                "Warning: could not connect to a running Ollama instance",
                client.as_str(),
            ]
}

fn validate_native_ollama_version_response(body: &str) -> Result<(), String> {
    let version: serde_json::Value = serde_json::from_str(body)
        .map_err(|error| format!("Native AI version response was invalid: {error}"))?;
    if version.get("version").and_then(serde_json::Value::as_str) != Some(NATIVE_OLLAMA_VERSION) {
        return Err(format!(
            "Staged native AI runtime reported an unreviewed version: {body}"
        ));
    }
    Ok(())
}

/// The pre-activation runtime/caption/AI self-test CHECKS -- everything
/// `run_native_pre_activation_self_test` used to do except writing the
/// receipt. Extracted (K1 fix) so the flat-layout activation path can run
/// the IDENTICAL checks and then obtain the receipt VALUE instead of having
/// it written to a fixed `staging`-relative path -- never a second, forked
/// copy of these checks.
#[cfg(target_os = "windows")]
fn run_native_pre_activation_checks(staging: &Path) -> Result<(), String> {
    // K1 activation defect (found by a clean-box install proof): the postgres
    // probe pointed at a `dependencies/` path that the product never
    // stages there. It ships inside the signed `native-server-binaries`
    // pack, which `install_layout.py` (`_SERVER_PACK_SUBDIR`) resolves at
    // runtime to `<install_root>\packs\native-server-binaries\payload\bin`
    // (postgres.exe, pg_ctl.exe) -- see
    // `scripts/build_native_server_pack.py`. NATS JetStream was removed from
    // the product (owner decision 2026-08-20; see ADR 0023, which supersedes
    // ADR 0001), so the nats-server.exe probe that used to run here is gone.
    // node was BUILD-TIME ONLY
    // (`scripts/build_native_app_payload.py` invokes `which(node)` to compile
    // the React portals; the runtime serves them as static dist via Python) and
    // is never staged, so its probe could never pass -- it is removed.
    //
    // K1-2: pg_ctl.exe is checked alongside postgres.exe -- the supervisor
    // actually LAUNCHES PostgreSQL through pg_ctl.exe
    // (`native/supervisor/children.py::postgres_child_spec` builds
    // `argv=[pg_ctl_path, "start", ...]`), so the self-test now verifies the
    // binary the runtime actually invokes, not just the one pg_ctl spawns.
    //
    // K1-1: tsp.exe (TSDuck) is intentionally NOT in this hard-required
    // array. The runtime treats TSDuck as optional -- `egress/ts_relay.py`'s
    // `CIVICCAST_TS_RELAY=auto` (the default) warns and passes udp-ts egress
    // straight through when `tsp` is unavailable rather than failing the
    // channel. `run_native_optional_verified_if_present_checks` (called
    // below) mirrors that posture: verified when staged, never a hard
    // activation failure when absent.
    let checks: [(&str, &[&str], &str, &str); 5] = [
        (
            "runtime/python.exe",
            &[
                "-I",
                "-B",
                "-X",
                "utf8",
                "-c",
                "import civiccast, ctranslate2, faster_whisper; assert faster_whisper.__version__ == '1.2.1'; assert ctranslate2.__version__ == '4.8.1'; print('native-core-ok')",
            ],
            "native-python",
            "native-core-ok",
        ),
        (
            "packs/native-server-binaries/payload/bin/postgres.exe",
            &["--version"],
            "native-postgres",
            "postgres",
        ),
        (
            "packs/native-server-binaries/payload/bin/pg_ctl.exe",
            &["--version"],
            "native-pg-ctl",
            "pg_ctl",
        ),
        (
            "dependencies/ffmpeg/bin/ffmpeg.exe",
            &["-version"],
            "native-ffmpeg",
            "ffmpeg",
        ),
        (
            "dependencies/ollama/ollama.exe",
            &["--version"],
            "native-ollama",
            NATIVE_OLLAMA_VERSION,
        ),
    ];
    for (relative, arguments, name, expected) in checks {
        let program = staging.join(relative);
        let program_text = program
            .to_str()
            .ok_or_else(|| format!("Staged native runtime path is not Unicode: {relative}"))?;
        // Instrumented first load took ~52s (~33s ctranslate2); this bounds installer imports.
        // Watch/caption performance limits remain separate.
        let timeout_secs = if name == "native-python" { 120 } else { 30 };
        let (exit_code, output) = run_bounded_command(program_text, name, arguments, timeout_secs)?;
        let matches = if name == "native-ollama" {
            native_ollama_cli_version_matches(exit_code, &output)
        } else {
            exit_code == 0 && output.to_lowercase().contains(&expected.to_lowercase())
        };
        if !matches {
            return Err(format!(
                "Staged native runtime self-test failed for {relative} (exit {exit_code}): {}",
                output.trim()
            ));
        }
    }
    run_native_optional_verified_if_present_checks(staging)?;
    run_native_caption_inference_self_test(staging)?;
    run_native_ai_inference_self_tests(staging)
}

/// Classifies a staged OPTIONAL runtime entry without following symlinks --
/// isolates exactly the logic the PR #421 review flagged, so it can be unit
/// tested directly instead of only through the (execution-side-effecting)
/// caller. `Ok(true)`: a real, regular, non-symlink file is staged at
/// `path` -- the caller should proceed to verify it. `Ok(false)`: a genuine
/// not-found (`io::ErrorKind::NotFound`) -- nothing is staged, which is fine
/// for an optional entry. `Err`: anything else present that is not a plain
/// regular file (a directory, a symlink regardless of target validity, or a
/// metadata read error such as permission-denied) -- the "verified if
/// present" contract means a corrupt staged entry must hard-fail closed, not
/// be silently waved through as "absent."
///
/// PR #421 review fix: this classification previously lived inline in
/// `run_native_optional_verified_if_present_checks` and used `Path::is_file()`,
/// which FOLLOWS symlinks/reparse points: a directory or a broken symlink at
/// the staged path made `is_file()` return `false` and was silently treated
/// as "absent, that's fine, it's optional" (should have hard-failed on a
/// corrupt staged entry instead), while a symlink pointing at a real,
/// working `tsp.exe` made `is_file()` return `true` and got EXECUTED
/// (bypassing the no-symlinks contract every other staged-file check in this
/// codebase enforces -- see `require_staged_files`). This flow (the flat
/// `--civiccast-activate-station` path) never calls
/// `native_activation::validate_staged_runtime_layout`, so that function's
/// stricter regular-file/no-symlink check never runs here -- this function
/// IS the only gate for the optional entry.
#[cfg(target_os = "windows")]
fn classify_optional_staged_entry(path: &Path, relative: &str) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!(
            "Could not inspect optional staged native runtime path {relative}: {error}"
        )),
        Ok(metadata) => {
            if metadata.is_file() && !metadata.file_type().is_symlink() {
                Ok(true)
            } else {
                Err(format!(
                    "Staged native station runtime path is not a regular file: {relative}"
                ))
            }
        }
    }
}

/// K1-1: probes for runtime dependencies the SERVICE treats as optional,
/// mirroring its posture -- verified (same version-probe semantics as a hard
/// `checks` entry) when staged, but only a warning, never a hard activation
/// failure, when absent. Currently just TSDuck's `tsp.exe`
/// (`egress/ts_relay.py`'s `CIVICCAST_TS_RELAY=auto` warns-and-passes-through
/// when `tsp` is unavailable rather than failing the channel). See
/// `classify_optional_staged_entry` for the no-symlink-following inspection
/// this relies on.
#[cfg(target_os = "windows")]
fn run_native_optional_verified_if_present_checks(staging: &Path) -> Result<(), String> {
    let relative = "packs/native-server-binaries/payload/tsduck/bin/tsp.exe";
    let program = staging.join(relative);
    if !classify_optional_staged_entry(&program, relative)? {
        eprintln!(
            "Native activation: TSDuck (tsp.exe) is not staged at {relative} -- udp-ts egress \
             will run direct-from-encoder without the seamless-splice relay (#151). TSDuck is \
             optional; continuing activation."
        );
        return Ok(());
    }
    let program_text = program
        .to_str()
        .ok_or_else(|| format!("Staged native runtime path is not Unicode: {relative}"))?;
    let (exit_code, output) = run_bounded_command(program_text, "native-tsduck", &["--version"], 30)?;
    if exit_code != 0 || !output.to_lowercase().contains("tsduck") {
        return Err(format!(
            "Staged native runtime self-test failed for {relative} (exit {exit_code}): {}",
            output.trim()
        ));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn run_native_pre_activation_self_test(
    staging: &Path,
    distribution: &native_distribution::AcquiredDistribution,
) -> Result<(), String> {
    run_native_pre_activation_checks(staging)?;
    write_native_activation_self_test_receipt(staging, distribution)
}

#[cfg(not(target_os = "windows"))]
fn run_native_pre_activation_self_test(
    _staging: &Path,
    _distribution: &native_distribution::AcquiredDistribution,
) -> Result<(), String> {
    Err("Native station activation self-tests require Windows.".to_string())
}

/// K1 fix: the flat-layout counterpart of [`run_native_pre_activation_self_test`]
/// -- runs the IDENTICAL checks (never a forked copy) and returns the
/// receipt VALUE instead of writing it to a fixed `staging`-relative path,
/// so `native_activation::activate_flat_station_with` can write it
/// atomically at whatever install root it is targeting (see that function's
/// doc for why atomicity matters more for a flat root than for the
/// versioned `.staging` tree this checks/receipt logic originally shipped
/// for).
#[cfg(target_os = "windows")]
fn run_native_pre_activation_self_test_for_flat_activation(
    staging: &Path,
    distribution: &native_distribution::AcquiredDistribution,
) -> Result<serde_json::Value, String> {
    run_native_pre_activation_checks(staging)?;
    Ok(native_activation_self_test_receipt_value(staging, distribution))
}

#[cfg(not(target_os = "windows"))]
fn run_native_pre_activation_self_test_for_flat_activation(
    _staging: &Path,
    _distribution: &native_distribution::AcquiredDistribution,
) -> Result<serde_json::Value, String> {
    Err("Native station activation self-tests require Windows.".to_string())
}

fn run_native_distribution_cli(args: &[String]) -> Option<i32> {
    // Belt-and-braces: --civiccast-activate-station shares BOTH of this
    // command's own trigger flags (--civiccast-acquire-channel /
    // --civiccast-import-station) plus --install-root/--cache-root, and this
    // command's trigger check below has no way to tell the two apart on its
    // own. main()'s dispatch order already consults
    // run_native_flat_activation_cli first for exactly this reason -- this
    // guard exists so a FUTURE dispatch reorder cannot silently re-break
    // that precedence and let this command capture an activation invocation
    // (staging it into the wrong, versioned app/<version> layout instead).
    if command_line_has_arg(args, "--civiccast-activate-station") {
        return None;
    }
    let channel_url = command_line_arg_value(args, "--civiccast-acquire-channel");
    let station_path = command_line_arg_value(args, "--civiccast-import-station");
    if channel_url.is_none() && station_path.is_none() {
        return None;
    }
    if channel_url.is_some() && station_path.is_some() {
        eprintln!(
            "--civiccast-acquire-channel and --civiccast-import-station are mutually exclusive."
        );
        return Some(64);
    }
    let Some(cache_root) = command_line_arg_value(args, "--cache-root") else {
        eprintln!("--cache-root is required for native distribution acquisition.");
        return Some(64);
    };
    let channel = command_line_arg_value(args, "--channel").unwrap_or_else(|| "beta".to_string());
    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(78);
        }
    };
    let result = if let Some(url) = channel_url {
        native_distribution::acquire_online_distribution(
            &url,
            Path::new(&cache_root),
            &trust,
            &channel,
            CIVICCAST_VERSION,
            CIVICCAST_VERSION,
        )
    } else {
        native_distribution::acquire_station_distribution(
            Path::new(station_path.as_deref().unwrap_or_default()),
            Path::new(&cache_root),
            &trust,
            &channel,
            CIVICCAST_VERSION,
            CIVICCAST_VERSION,
        )
    };
    match result {
        Ok(acquired) => {
            let rendered_value =
                if let Some(install_root) = command_line_arg_value(args, "--install-root") {
                    match native_activation::stage_acquired_distribution(
                        &acquired,
                        Path::new(&install_root),
                        &trust,
                        run_native_pre_activation_self_test,
                    ) {
                        Ok(staged) => serde_json::json!({
                            "acquired": acquired,
                            "staged": staged,
                        }),
                        Err(error) => {
                            eprintln!("{error}");
                            return Some(67);
                        }
                    }
                } else {
                    match serde_json::to_value(&acquired) {
                        Ok(value) => value,
                        Err(error) => {
                            eprintln!("Could not render native distribution result: {error}");
                            return Some(65);
                        }
                    }
                };
            match serde_json::to_string_pretty(&rendered_value) {
                Ok(rendered) => {
                    println!("{rendered}");
                    Some(0)
                }
                Err(error) => {
                    eprintln!("Could not render native distribution result: {error}");
                    Some(65)
                }
            }
        }
        Err(error) => {
            eprintln!("{error}");
            Some(66)
        }
    }
}

/// K1 fix: `--civiccast-activate-station --install-root DIR --cache-root DIR
/// (--civiccast-acquire-channel URL [--channel NAME] | --civiccast-import-station PATH)`
/// -- activates a FLAT-layout native station: writes `station-set.json` and
/// `activation-self-test.json` directly at `DIR` (no `app/<version>`
/// subdirectory), the shape `native/station_runtime.py::
/// load_native_station_environment` requires for a station whose service is
/// registered against `DIR\runtime\python.exe` (see
/// `native_activation::activate_flat_station_with`'s doc for the full
/// contract, idempotency, and rollback guarantees). Mirrors
/// [`run_native_distribution_cli`]'s own argument handling and exit-code
/// conventions exactly -- `--civiccast-acquire-channel` /
/// `--civiccast-import-station` are the SAME two (mutually exclusive)
/// acquisition sources, reused verbatim rather than a third, forked
/// acquisition path.
///
/// Wired into `nsis-hooks-bootstrap.nsh`'s `NSIS_HOOK_POSTINSTALL` chain,
/// between `--civiccast-provision` and `--civiccast-register-native-service`
/// (K1 fix): the invocation there passes `--civiccast-import-station
/// "$EXEDIR\station\station-index.json"`, the SAME "packs next to the
/// installer" side-load convention `--civiccast-stage-packs` already uses
/// for component packs, extended to a full signed station bundle.
///
/// KNOWN OPERATIONAL GAP (this slice, 2026-08-16): no build step publishes a
/// station bundle to `$EXEDIR\station` yet. The elevated installer's own
/// pack-staging step (`--civiccast-stage-packs`, `native_pack_staging.rs::
/// DEFAULT_REQUIRED_COMPONENTS`) stages an entirely different, disjoint
/// component set (`native-server-binaries` / `native-app-payload` /
/// `native-ffmpeg-runtime` / `native-ollama-runtime`) than the one this
/// command's self-test needs (`captions-floor`, `summary-gemma4-12b`,
/// `summary-gemma4-e4b`, `translation-translategemma-4b`); those caption/AI
/// model weights are today fetched only AFTER the elevated installer
/// finishes, by a SEPARATE non-elevated first-run wizard
/// (`component_acquisition.rs`; see `nsis-hooks-bootstrap.nsh`'s own
/// `MUI_DIRECTORYPAGE_TEXT_TOP` string: "After Setup finishes, the CivicCast
/// setup wizard downloads additional components"). Until a station bundle
/// is actually produced and published to `$EXEDIR\station`, this step fails
/// loud (`CIVICCAST_EXIT_D4_ACTIVATION`) on every real install -- by design:
/// per this slice's instructions, a silent skip here would recreate K1 in a
/// new shape, so this fails LOUD instead of silently degrading. See this
/// slice's final report for the full evidence trail and the packaging work
/// still needed (a station-bundle publisher) before a real install passes
/// this step.
fn run_native_flat_activation_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-activate-station") {
        return None;
    }
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-activate-station.");
        return Some(64);
    };
    let channel_url = command_line_arg_value(args, "--civiccast-acquire-channel");
    let station_path = command_line_arg_value(args, "--civiccast-import-station");
    if channel_url.is_none() && station_path.is_none() {
        eprintln!(
            "--civiccast-activate-station requires --civiccast-acquire-channel or \
             --civiccast-import-station to supply the distribution to activate."
        );
        return Some(64);
    }
    if channel_url.is_some() && station_path.is_some() {
        eprintln!(
            "--civiccast-acquire-channel and --civiccast-import-station are mutually exclusive."
        );
        return Some(64);
    }
    let Some(cache_root) = command_line_arg_value(args, "--cache-root") else {
        eprintln!("--cache-root is required for native distribution acquisition.");
        return Some(64);
    };
    let channel = command_line_arg_value(args, "--channel").unwrap_or_else(|| "beta".to_string());
    let trust = match native_packs::embedded_pack_trust() {
        Ok(trust) => trust,
        Err(error) => {
            eprintln!("{error}");
            return Some(78);
        }
    };
    let result = if let Some(url) = channel_url {
        native_distribution::acquire_online_distribution(
            &url,
            Path::new(&cache_root),
            &trust,
            &channel,
            CIVICCAST_VERSION,
            CIVICCAST_VERSION,
        )
    } else {
        native_distribution::acquire_station_distribution(
            Path::new(station_path.as_deref().unwrap_or_default()),
            Path::new(&cache_root),
            &trust,
            &channel,
            CIVICCAST_VERSION,
            CIVICCAST_VERSION,
        )
    };
    let acquired = match result {
        Ok(acquired) => acquired,
        Err(error) => {
            eprintln!("{error}");
            return Some(66);
        }
    };
    match native_activation::activate_flat_station_from_acquired(
        Path::new(&install_root),
        &acquired,
        &trust,
        run_native_pre_activation_self_test_for_flat_activation,
    ) {
        Ok(activation) => match serde_json::to_string_pretty(&activation) {
            Ok(rendered) => {
                println!("{rendered}");
                Some(0)
            }
            Err(error) => {
                eprintln!("Could not render native flat activation result: {error}");
                Some(65)
            }
        },
        Err(error) => {
            eprintln!("{error}");
            Some(67)
        }
    }
}

/// D4 install-side state establishment CLI (`spec-installer-lifecycle.md` D1/
/// D4; wired into `NSIS_HOOK_POSTINSTALL` in `nsis-hooks-native.nsh` after the
/// D2 install-time re-verification passes). Registers the LocalSystem service
/// via the pywin32 seam already defined in
/// `civiccast.native.supervisor.service_host` -- see
/// `native_service_registration.rs`'s module doc for why this is a thin
/// wrapper rather than a Rust reimplementation of SCM registration.
///
/// BLOCKER (2026-08-01): registration alone left the station DOWN.
/// `--startup auto` is a next-boot instruction to the SCM, and nothing under
/// `src-tauri/src/` ever issued a start -- so a fresh install finished with a
/// registered, correctly configured, entirely STOPPED CivicCastSupervisor: no
/// postgres, no control plane on 127.0.0.1:8000, and therefore
/// nothing behind the installer's own "Open operator console" button until
/// the operator happened to reboot. This CLI now registers AND starts, awaits
/// a real RUNNING state, and fails LOUD with its own exit code
/// ([`native_service_registration::SERVICE_START_FAILED_EXIT_CODE`]) when the
/// service registers but will not run. The start is sequenced HERE rather
/// than inside `register_native_service` because that function is also D5
/// Repair's re-registration path, which must NOT restart a service it
/// deliberately stopped -- see the "Service START" section comment in
/// `native_service_registration.rs`.
fn run_native_service_registration_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-register-native-service") {
        return None;
    }
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-register-native-service.");
        return Some(64);
    };
    #[cfg(target_os = "windows")]
    {
        Some(
            match native_service_registration::register_native_service(Path::new(&install_root)) {
                Ok(()) => match native_service_registration::start_native_service() {
                    // <installer-path-audit BL-11> SCM `RUNNING` used to be
                    // the ONLY success signal the entire elevated install
                    // chain had. It means `pythonservice.exe` told the SCM it
                    // had started -- nothing about Postgres being up, the
                    // control plane binding 8000, or the schema matching the
                    // code. Gate A run 33681670855 is exactly that: service
                    // registered, SCM RUNNING, /health 200
                    // {"status":"degraded","schema":"behind"}, InstalledVersion
                    // written, exit 0, success page over a box serving 500s.
                    // Poll /health and require it to say the station can serve.
                    Ok(()) => match native_service_registration::wait_for_control_plane_ready() {
                        Ok(()) => {
                            println!(
                                "CivicCast (Native) service registered, RUNNING, and serving \
                                 (/health reports healthy with a current schema)."
                            );
                            0
                        }
                        Err(error) => {
                            eprintln!("{error}");
                            native_service_registration::SERVICE_NOT_SERVING_EXIT_CODE
                        }
                    },
                    Err(error) => {
                        eprintln!(
                            "CivicCast (Native) service was registered but could not be \
                             started: {error}"
                        );
                        native_service_registration::SERVICE_START_FAILED_EXIT_CODE
                    }
                },
                Err(error) => {
                    eprintln!("{error}");
                    70
                }
            },
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native service registration requires Windows.");
        Some(70)
    }
}

/// D4 firewall rule establishment CLI. See `native_service_registration.rs`
/// for the port/rule derivation and the fail-closed probe classification.
fn run_native_firewall_registration_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-register-native-firewall-rule") {
        return None;
    }
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-register-native-firewall-rule.");
        return Some(64);
    };
    #[cfg(target_os = "windows")]
    {
        Some(
            match native_service_registration::register_native_firewall_rule(Path::new(
                &install_root,
            )) {
                Ok(()) => {
                    println!("CivicCast (Native) firewall rule registered.");
                    0
                }
                Err(error) => {
                    eprintln!("{error}");
                    71
                }
            },
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native firewall rule registration requires Windows.");
        Some(71)
    }
}

/// D4 `DatabaseUrl` registry write CLI. Accepts an ALREADY-RESOLVED value
/// (produced elsewhere by `civiccast.native.provision.models.
/// resolve_database_url`, "the documented seam" -- see
/// `native_service_registration.rs`'s module doc's "DatabaseUrl" section for
/// why this subcommand does not itself resolve the value and is not yet
/// wired into `NSIS_HOOK_POSTINSTALL`).
fn run_native_database_url_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-write-native-database-url") {
        return None;
    }
    let Some(database_url) = command_line_arg_value(args, "--database-url") else {
        eprintln!("--database-url is required with --civiccast-write-native-database-url.");
        return Some(64);
    };
    #[cfg(target_os = "windows")]
    {
        Some(
            match native_service_registration::write_database_url(&database_url) {
                Ok(()) => {
                    println!("CivicCast (Native) DatabaseUrl written and verified.");
                    0
                }
                Err(error) => {
                    eprintln!("{error}");
                    72
                }
            },
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native DatabaseUrl registration requires Windows.");
        Some(72)
    }
}

/// D4 live provisioning-execution CLI (WP2 provision-execution wiring; wired
/// into `NSIS_HOOK_POSTINSTALL` between the D2 re-verification gates and D4
/// service/firewall registration). Shells to the journaled Python
/// provisioning engine via `native_service_registration::run_native_provision`
/// -- see that module's doc for why this is a thin wrapper rather than a
/// Rust reimplementation, and for the credential-handling contract (this
/// function never prints anything the child process produced).
fn run_native_provision_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-provision") {
        return None;
    }
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-provision.");
        return Some(64);
    };
    let Some(owner_run_id) = command_line_arg_value(args, "--owner-run-id") else {
        eprintln!("--owner-run-id is required with --civiccast-provision.");
        return Some(64);
    };
    let existing_database_url =
        command_line_arg_value(args, "--existing-database-url").unwrap_or_default();
    #[cfg(target_os = "windows")]
    {
        Some(
            match native_service_registration::run_native_provision(
                Path::new(&install_root),
                &owner_run_id,
                &existing_database_url,
            ) {
                Ok(native_service_registration::ProvisionOutcome::Provisioned) => {
                    println!(
                        "CivicCast (Native) provisioning complete; DatabaseUrl written and verified."
                    );
                    0
                }
                Ok(native_service_registration::ProvisionOutcome::NoOp) => {
                    println!(
                        "CivicCast (Native) provisioning: no-op (existing database and \
                         DatabaseUrl registry value both already present)."
                    );
                    0
                }
                // <installer-path-audit BL-13> The failure now carries its
                // own exit code: an unprovable ActiveRuntime selector
                // (SELECTOR_UNPROVABLE_EXIT_CODE) is a different operator
                // situation from a provisioning failure, and the exit code is
                // the only signal a silent install's support log carries
                // about which precondition failed.
                Err(failure) => {
                    eprintln!("{failure}");
                    failure.exit_code
                }
            },
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native provisioning requires Windows.");
        Some(native_service_registration::ProvisionFailure::GENERIC_EXIT_CODE)
    }
}

/// D4 pre-tree-rebuild service stop CLI (BLOCKER/CRITICAL fixes; wired into
/// `NSIS_HOOK_PREINSTALL` in `nsis-hooks-bootstrap.nsh`, before the existing
/// `taskkill` of the GUI exe): stops the LocalSystem supervisor service
/// (`native_service_registration::stop_native_service`) so the D3
/// install/upgrade engine and D4 pack extraction never delete/rebuild
/// `$INSTDIR\runtime`/`$INSTDIR\packs\...\payload` out from under a still-
/// running `pythonservice.exe` and its long-lived `postgres.exe`
/// child. Idempotent: a not-installed or already-stopped
/// service is success, matching the "first-ever install" case where there is
/// nothing to stop yet.
fn run_native_stop_service_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-stop-native-service") {
        return None;
    }
    #[cfg(target_os = "windows")]
    {
        Some(match native_service_registration::stop_native_service() {
            Ok(()) => {
                println!(
                    "CivicCast (Native) service is stopped (or was already stopped/not installed)."
                );
                0
            }
            Err(error) => {
                eprintln!("{error}");
                81
            }
        })
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native service stop requires Windows.");
        Some(81)
    }
}

/// D4 POSTUNINSTALL teardown CLI (BLOCKER fix; wired into
/// `NSIS_HOOK_POSTUNINSTALL` in `nsis-hooks-bootstrap.nsh`, at the START of
/// the macro, before the existing ActiveRuntime selector bookkeeping): runs
/// `native_service_registration::teardown_native_state`'s ordered, idempotent
/// steps (stop service -> remove service -> delete firewall rule -> clear the
/// credential registry value `DatabaseUrl` -> clear the install
/// markers `InstalledVersion` -> remove the now-empty `CivicCast\Native` key
/// -> clear a released Maintenance interlock blob (the last two, N-20,
/// carried) and prints one line
/// per step. Continues past every individual step's failure so one broken
/// step never blocks the others (or the selector bookkeeping that runs after
/// this in NSIS); exits 0 only when every step either succeeded or was a
/// legitimate no-op, 82 when specifically the "stop service" step could not
/// confirm the service stopped (CRITICAL fix, 2026-07-30 adversarial review
/// -- see `native_service_registration::teardown_exit_code`, which the NSIS
/// hook's recursive `RMDir /r` block gates on to avoid deleting the program
/// tree out from under a still-running service), or 80 for any other real
/// step failure.
fn run_native_teardown_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-teardown-native-state") {
        return None;
    }
    let Some(install_root) = command_line_arg_value(args, "--install-root") else {
        eprintln!("--install-root is required with --civiccast-teardown-native-state.");
        return Some(64);
    };
    #[cfg(target_os = "windows")]
    {
        let steps = native_service_registration::teardown_native_state(Path::new(&install_root));
        for step in &steps {
            if step.failed {
                eprintln!(
                    "CivicCast (Native) teardown: {} FAILED: {}",
                    step.label, step.detail
                );
            } else {
                println!(
                    "CivicCast (Native) teardown: {} -- {}",
                    step.label, step.detail
                );
            }
        }
        Some(native_service_registration::teardown_exit_code(&steps))
    }
    #[cfg(not(target_os = "windows"))]
    {
        eprintln!("Native teardown requires Windows.");
        Some(80)
    }
}

fn run_native_uninstall_policy_cli(args: &[String]) -> Option<i32> {
    let plan = native_uninstall::decide_from_cli_args(args)?;
    println!(
        "decision={} selector-mutation={}",
        native_uninstall::decision_token(plan),
        native_uninstall::selector_mutation_token(plan)
    );
    Some(if plan.decision == native_uninstall::Decision::Block {
        77
    } else {
        0
    })
}

fn run_native_uninstall_preflight_cli(args: &[String]) -> Option<i32> {
    if !command_line_has_arg(args, "--civiccast-native-uninstall-preflight") {
        return None;
    }
    let acknowledge_transfer = command_line_has_arg(args, "--acknowledge-transfer");
    let expected_len = if acknowledge_transfer { 2 } else { 1 };
    if args.len() != expected_len {
        eprintln!(
            "--civiccast-native-uninstall-preflight accepts only the optional \
             --acknowledge-transfer flag."
        );
        return Some(64);
    }
    match native_uninstall::native_uninstall_preflight(acknowledge_transfer) {
        Ok(native_uninstall::PreflightOutcome::TransferAcknowledgmentRequired) => {
            println!("transfer-acknowledgment-required");
            Some(native_uninstall::TRANSFER_ACK_REQUIRED_EXIT_CODE)
        }
        Ok(native_uninstall::PreflightOutcome::Allowed(plan)) => {
            println!(
                "decision={} selector-mutation={}",
                native_uninstall::decision_token(plan),
                native_uninstall::selector_mutation_token(plan)
            );
            Some(
                if plan.decision == native_uninstall::Decision::AllowSolePostclear {
                    native_uninstall::SOLE_POSTCLEAR_EXIT_CODE
                } else {
                    0
                },
            )
        }
        Err(error) => {
            eprintln!("{error}");
            Some(77)
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(exit_code) = run_native_uninstall_policy_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_uninstall_preflight_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_service_registration_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_firewall_registration_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_database_url_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_provision_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_stop_service_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_teardown_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_repair_cli(&args) {
        std::process::exit(exit_code);
    }
    if command_line_has_arg(&args, "--help")
        || command_line_has_arg(&args, "-h")
        || command_line_has_arg(&args, "/?")
    {
        println!("CivicCast Installer {CIVICCAST_VERSION}");
        println!(
            "  --civiccast-runtime-host          keep the installed CivicCast runtime available"
        );
        println!("  --civiccast-verify-pack PATH      verify a signed native component pack");
        println!("  --civiccast-import-pack PATH --destination DIR   verify and extract a pack");
        println!(
            "  --civiccast-acquire-channel URL --cache-root DIR [--install-root DIR] [--channel beta]   acquire and optionally stage every mandatory pack"
        );
        println!(
            "  --civiccast-import-station PATH --cache-root DIR [--install-root DIR] [--channel beta]   import and optionally stage every mandatory pack without networking"
        );
        println!(
            "  --civiccast-activate-station --install-root DIR --cache-root DIR (--civiccast-acquire-channel URL | --civiccast-import-station PATH) [--channel beta]   K1: activate a FLAT-layout station (station-set.json + activation-self-test.json written directly at --install-root, no app/<version> subdirectory); wired into nsis-hooks-bootstrap.nsh between --civiccast-provision and --civiccast-register-native-service, sourced from a side-loaded station bundle at $EXEDIR\\station\\station-index.json"
        );
        println!(
            "  --civiccast-native-uninstall-policy --product native|wsl --selector native|wsl|absent|unreadable --other-product present|absent|unknown --transfer-state not-requested|accepted-and-verified|refused|failed"
        );
        println!(
            "  --civiccast-native-uninstall-preflight [--acknowledge-transfer]   probe Native/WSL ownership before native uninstall; exit 74 means an operator-acknowledged ActiveRuntime transfer to the WSL product is required -- re-run with --acknowledge-transfer to perform the write+read-back-verified transfer before removal proceeds, or do not re-run to leave state untouched"
        );
        println!(
            "  --civiccast-verify-install-tree TREE --manifest NAME [--max-failures N]   D2 install-time re-verify a laid tree against its shipped manifest"
        );
        println!(
            "  --civiccast-verify-pack-tree PACK --destination DIR [--expected-component NAME]   D2 install-time re-verify an extracted component-pack tree against its signed pack"
        );
        println!(
            "  --civiccast-stage-packs INSTALLER_DIR --install-root DIR [--require-component NAME]... [--optional-component NAME]... [--channel-url URL] [--channel NAME]   side-load delivery of required (and, best-effort, optional) native component packs from INSTALLER_DIR\\packs into INSTDIR\\packs (--channel-url must be supplied to attempt any network acquisition; none is pinned in this build)"
        );
        println!(
            "  --civiccast-register-native-service --install-root DIR   D4 register the LocalSystem supervisor service (pywin32 install-or-update seam)"
        );
        println!(
            "  --civiccast-register-native-firewall-rule --install-root DIR   D4 create the inbound portal/API firewall rule if it does not already exist"
        );
        println!(
            "  --civiccast-write-native-database-url --database-url VALUE   D4 write + read-back verify HKLM\\SOFTWARE\\CivicCast\\Native\\DatabaseUrl"
        );
        println!(
            "  --civiccast-provision --install-root DIR --owner-run-id ID [--existing-database-url VALUE]   D4 run the journaled PostgreSQL provisioning engine and write DatabaseUrl"
        );
        println!(
            "  --civiccast-stop-native-service   stop the LocalSystem supervisor service (idempotent; not-installed/already-stopped is success), for use before a tree rebuild"
        );
        println!(
            "  --civiccast-teardown-native-state --install-root DIR   D4/D1 uninstall teardown: stop + remove the service, delete the firewall rule, clear the credential registry values (DatabaseUrl) and the install markers (InstalledVersion), remove the now-empty CivicCast\\Native key, and clear a released Maintenance interlock blob (idempotent, continues past individual absences; exit 0=all steps ok, 82=the service could not be confirmed stopped (unsafe to remove the program tree), 80=some other real failure)"
        );
        println!(
            "  --civiccast-repair INSTDIR [--installer-dir DIR] [--require-component NAME]...   D5 re-verify and repair the installed application, version, selector, runtime, dependency, and (if present) caption trees; exit 0=all-verified, 76=repaired, 79=unrepairable"
        );
        return;
    }
    // ORDER matters: --civiccast-activate-station shares its acquisition
    // flags (--civiccast-acquire-channel / --civiccast-import-station /
    // --install-root / --cache-root) with --civiccast-distribution's own
    // trigger (run_native_distribution_cli fires on --civiccast-acquire-
    // channel or --civiccast-import-station ALONE, with no knowledge of
    // --civiccast-activate-station at all). run_native_flat_activation_cli
    // MUST be consulted first, or an activation invocation is captured by
    // the versioned distribution CLI instead -- staging into app/<version>,
    // printing JSON, and exiting 0, while run_native_flat_activation_cli
    // never runs at all. See run_native_distribution_cli's own early guard
    // below for the belt-and-braces half of this fix.
    if let Some(exit_code) = run_native_flat_activation_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_distribution_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_pack_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_install_verify_cli(&args) {
        std::process::exit(exit_code);
    }
    if let Some(exit_code) = run_native_pack_staging_cli(&args) {
        std::process::exit(exit_code);
    }
    if command_line_has_arg(&args, "--civiccast-runtime-host") {
        std::process::exit(run_civiccast_runtime_host());
    }

    // <installer-path-audit MA-22> AN UNRECOGNISED `--civiccast-*` FLAG USED
    // TO LAUNCH THE GUI AND BLOCK THE INSTALLER FOREVER.
    //
    // `main()` dispatches through a chain of `if let Some(exit_code)` guards
    // and anything unmatched fell straight through to the Tauri event loop
    // below, which never exits. That matters because PREINSTALL invokes the
    // OLD, ALREADY-INSTALLED binary:
    //
    //     nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" \
    //         --civiccast-stop-native-service'
    //
    // and nothing checks the old binary's version before assuming its CLI
    // contract. If a future release renames or removes that flag, upgrading
    // FROM that version launches its GUI under nsExec with NO TIMEOUT -- the
    // installer sits alive with no children and no visible position in the
    // chain, which is precisely the run-3/run-4 hang
    // `nsis-hooks-bootstrap.nsh`'s own header was written to make
    // diagnosable.
    //
    // A no-argument launch is still the GUI, deliberately: that is what a
    // user double-clicking setup.exe gets. Only a `--civiccast-*` argument
    // this binary does not implement is fatal, and it names the flag.
    if let Some(unknown) = args
        .iter()
        .find(|argument| argument.starts_with("--civiccast-"))
    {
        eprintln!(
            "CivicCast Installer {CIVICCAST_VERSION} does not implement {unknown}. This is \
             almost always a version mismatch: an installer is invoking a command-line step on \
             a DIFFERENT CivicCast build than the one that shipped it. Refusing to open the \
             setup window instead, so an unattended install fails visibly rather than hanging \
             forever."
        );
        std::process::exit(native_service_registration::UNKNOWN_CIVICCAST_FLAG_EXIT_CODE);
    }

    // The Microsoft loader can reject stale evergreen registration while a
    // runtime already present in its standard installation folder is
    // usable. Keep this recovery local to the wizard process; don't alter
    // EdgeUpdate registration or replace an explicit operator override.
    #[cfg(target_os = "windows")]
    if std::env::var_os("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER").is_none()
        && tauri::webview_version().is_err()
    {
        let roots: Vec<PathBuf> = ["ProgramFiles(x86)", "ProgramFiles"]
            .iter()
            .filter_map(std::env::var_os)
            .map(|root| PathBuf::from(root).join("Microsoft/EdgeWebView/Application"))
            .collect();
        let selected = webview_runtime::usable_runtime_folder(&roots, |folder| {
            std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", folder);
            tauri::webview_version().is_ok()
        });
        if selected.is_none() {
            std::env::remove_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER");
        }
    }

    tauri::Builder::default()
        .setup(|app| {
            remove_stale_shutdown_markers();
            launch_shutdown_marker_watcher();
            // The native product is the only product this binary ever is --
            // see native_runtime_status_message's doc comment for what a
            // no-argument launch reports.
            launch_startup_native_status_if_ready(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            read_local_installer_state,
            open_installer_log,
            open_operator_console,
            reset_local_installer_state,
            run_local_installer_action,
            native_hardware_inventory,
            start_acquisition,
            cancel_acquisition,
            retry_acquisition_component
        ])
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                std::process::exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run CivicCast installer");
}


#[cfg(test)]
mod acquisition_destination_tests {
    use super::*;

    /// Chain H1 RED: the writable per-machine acquisition root is derived
    /// from `%PROGRAMDATA%` -- the SAME convention
    /// `civiccast.native.supervisor.install_layout.default_program_data_root`
    /// and `civiccast.native.provision.__main__.resolve_provision_paths` use,
    /// so the Rust writer and the Python consumers cannot land in different
    /// places.
    #[test]
    fn the_acquisition_download_root_is_the_program_data_civiccast_root() {
        assert_eq!(
            acquisition_download_root_from(Path::new(r"C:\ProgramData")),
            PathBuf::from(r"C:\ProgramData\CivicCast")
        );
        assert_eq!(
            acquisition_download_root_from(Path::new(r"D:\Machine State")),
            PathBuf::from(r"D:\Machine State\CivicCast")
        );
    }

    /// The installed GUI's own folder is still the place installer-staged
    /// packs are LOOKED FOR -- it just stopped being the place downloads are
    /// WRITTEN. Both roots come from the one production resolver.
    #[test]
    fn the_installed_gui_reads_staged_packs_from_its_own_folder_and_writes_nowhere_near_it() {
        let staged = acquisition_installer_directory();
        let downloads = acquisition_download_root();
        assert!(
            !downloads.starts_with(&staged),
            "downloads ({}) must not land inside the install directory ({})",
            downloads.display(),
            staged.display()
        );
    }

    /// Chain H2 RED at the wire boundary: a PermissionDenied must reach the
    /// frontend as its own kind. R7's durable installer-state recorded
    /// `{"kind":"disk_full","detail":"PermissionDenied"}` -- the detail was
    /// right and the kind, which is the ONLY thing the UI keys its copy off,
    /// was a lie.
    #[test]
    fn a_permission_denied_write_reaches_the_frontend_as_permission_denied() {
        let (kind, detail) = classify_acquisition_error(
            &component_acquisition::AcquisitionError::PermissionDenied(
                std::io::ErrorKind::PermissionDenied,
            ),
        )
        .expect("a permission denial is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::PermissionDenied);
        assert_eq!(detail, "PermissionDenied");
    }

    #[test]
    fn an_indistinguishable_write_failure_reaches_the_frontend_as_write_failed() {
        let (kind, _detail) = classify_acquisition_error(
            &component_acquisition::AcquisitionError::WriteFailed(std::io::ErrorKind::Other),
        )
        .expect("a write failure is a classified failure");
        assert_eq!(kind, acquisition_state::AcquisitionErrorKind::WriteFailed);
    }

    // ---- pack-staging stdout summary: NSIS_MAX_STRLEN fail-closed guard ----

    #[test]
    fn pack_staging_stdout_summary_names_the_manifest_path_and_every_component() {
        let manifest_path = Path::new(r"C:\ProgramData\CivicCast\install-manifest-report-1234-999.json");
        let replaced = native_pack_staging::PackPayloadIdentity {
            component: "native-server-binaries".to_string(),
            outcome: "ReplaceFromOffline".to_string(),
            staged_digest: Some("a".repeat(64)),
            incoming_digest: Some("b".repeat(64)),
        };
        let needs_online = native_pack_staging::PackPayloadIdentity {
            component: "native-app-payload".to_string(),
            outcome: "NeedsOnlineOrAbort".to_string(),
            staged_digest: None,
            incoming_digest: None,
        };
        let summary =
            render_pack_staging_summary(manifest_path, &[&replaced, &needs_online]);

        assert!(summary.contains(r"C:\ProgramData\CivicCast\install-manifest-report-1234-999.json"));
        assert!(summary.contains("native-server-binaries=ReplaceFromOffline"));
        assert!(summary.contains("staged=aaaaaaaa"));
        assert!(summary.contains("incoming=bbbbbbbb"));
        assert!(summary.contains("native-app-payload=NeedsOnlineOrAbort"));
        assert!(summary.contains("staged=none"));
        assert!(summary.contains("incoming=none"));
        // Never the raw 64-char digest -- only the 8-char eyeball prefix.
        assert!(!summary.contains(&"a".repeat(64)));
        assert!(!summary.contains(&"b".repeat(64)));
    }

    #[test]
    fn pack_staging_stdout_summary_for_five_components_fits_the_nsis_max_strlen_budget() {
        // `nsis-hooks-bootstrap.nsh`'s
        // `!insertmacro CIVICCAST_STEP "step stage-packs: manifest report: $1"`
        // concatenates this summary onto BOTH `CIVICCAST_STEP`'s own
        // "[YYYY-MM-DD HH:MM:SS] " timestamp prefix and the literal
        // "step stage-packs: manifest report: " text, all inside ONE NSIS
        // string capped at NSIS_MAX_STRLEN (1024 bytes -- measured against
        // real Gate A evidence, where a D2 report line is cut at exactly
        // that many bytes). This is the fail-closed guard: prove our own
        // worst-case summary (5 components -- the current
        // DEFAULT_REQUIRED_COMPONENTS + DEFAULT_OPTIONAL_COMPONENTS count --
        // each carrying the LONGEST `StagingAction`/`OptionalStagingAction`
        // variant name, `ReplaceCorruptFromOffline`, plus full digests)
        // leaves room for that prefix. If a future change (a longer
        // component name, another outcome variant) blows this budget, this
        // test catches it before a real install ever silently drops the
        // required object out of install-progress.log again.
        let known_prefix = "[2026-09-05 12:34:56] step stage-packs: manifest report: ";
        let budget = 1024usize.saturating_sub(known_prefix.len());

        let manifest_path = Path::new(
            r"C:\ProgramData\CivicCast\install-manifest-report-999999-1234567890123.json",
        );
        let identities: Vec<native_pack_staging::PackPayloadIdentity> = [
            "native-server-binaries",
            "native-app-payload",
            "native-ffmpeg-runtime",
            "native-ollama-runtime",
            "native-cuda-runtime",
        ]
        .iter()
        .map(|component| native_pack_staging::PackPayloadIdentity {
            component: component.to_string(),
            // The longest variant name across both StagingAction and
            // OptionalStagingAction -- the true worst case for length.
            outcome: "ReplaceCorruptFromOffline".to_string(),
            staged_digest: Some("a".repeat(64)),
            incoming_digest: Some("b".repeat(64)),
        })
        .collect();
        let refs: Vec<&native_pack_staging::PackPayloadIdentity> = identities.iter().collect();

        let summary = render_pack_staging_summary(manifest_path, &refs);
        assert!(
            summary.len() < budget,
            "summary is {} bytes, only {budget} available inside the NSIS_MAX_STRLEN budget \
             after CIVICCAST_STEP's own prefix -- got: {summary:?}",
            summary.len()
        );
    }
}
