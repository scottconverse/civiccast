// SPDX-License-Identifier: Apache-2.0
// Copyright (c) The CivicCast Authors

//! Native/WSL uninstall ownership policy and the Windows preflight adapter.
//!
//! Registry observations are direct Win32 registry calls: localized `reg.exe`
//! output is never parsed. The sole-active marker is not a selector mutation;
//! it is a product-owned proof consumed by NSIS only after the uninstaller has
//! completed its normal removal work.
//!
//! ## WSL ARP probe: elevated per-machine uninstall and other-user hives
//!
//! The WSL product's uninstall registration is a per-user (`HKEY_CURRENT_USER`)
//! ARP entry. When the Native uninstaller runs elevated for a per-machine
//! removal, `HKEY_CURRENT_USER` resolves to the *elevating admin's* hive, not
//! necessarily the hive of the user who installed WSL. To avoid misclassifying
//! a different user's still-installed WSL product as absent, the probe also
//! enumerates `HKEY_USERS` and checks every other loaded user hive.
//!
//! `HKEY_USERS` only exposes hives that are currently **loaded** — i.e. the
//! profiles of users who are logged on (or otherwise mounted) at probe time.
//! A per-user WSL install belonging to a user who is not logged in is
//! invisible to this probe and is indistinguishable from a true absence; see
//! [`WSL_ARP_PROBE_LOADED_HIVES_ONLY`]. Mounting `NTUSER.DAT` for unloaded
//! profiles, or reconciling against `C:\Users`, is a deliberate policy
//! decision reserved for the coordinator and is intentionally NOT implemented
//! here — this module only widens visibility to hives Windows has already
//! loaded, and fails closed (`Unknown`, which blocks uninstall) whenever the
//! `HKEY_USERS` enumeration itself fails or any probed view errors with
//! anything other than "not found".

#[cfg(target_os = "windows")]
use std::io;

#[cfg(target_os = "windows")]
use winreg::enums::{
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_USERS, KEY_READ, KEY_SET_VALUE, KEY_WOW64_32KEY,
    KEY_WOW64_64KEY,
};
#[cfg(target_os = "windows")]
use winreg::RegKey;

/// `HKEY_USERS` enumeration (used to catch a per-user WSL install owned by a
/// different, currently logged-on user during an elevated uninstall) only
/// observes hives Windows has already loaded. Users who are not logged in
/// remain invisible to the probe and are NOT distinguished from a true
/// absence. This constant is the named seam a future coordinator-owned
/// change (mounting `NTUSER.DAT`, or reconciling against `C:\Users`) would
/// need to address; it is intentionally left unimplemented here.
///
/// Not read at runtime by this module (it is a documentation/grep seam and
/// a unit-test pin, not a branch condition), so it is exempted from the
/// unused-item lint rather than deleted.
#[allow(dead_code)]
pub const WSL_ARP_PROBE_LOADED_HIVES_ONLY: bool = true;

const SELECTOR_KEY: &str = r"SOFTWARE\CivicCast";
const SELECTOR_VALUE: &str = "ActiveRuntime";
pub const WSL_ARP_KEY: &str =
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CivicCast Installer";
pub const SOLE_POSTCLEAR_EXIT_CODE: i32 = 73;
/// `native_uninstall_preflight` returns `TransferAcknowledgmentRequired`
/// (never touching the registry) when acknowledging a transfer, if given,
/// would flip an otherwise-`Block`ed uninstall to `AllowAfterTransfer`. The
/// CLI/NSIS layer maps that to this exit code so the interactive surfaces
/// know exactly when prompting for acknowledgment is the right response --
/// distinct from `SOLE_POSTCLEAR_EXIT_CODE` (73, a different armed-plan
/// signal) and from the generic blocked/error exit code (77) used for every
/// `Block` an acknowledgment cannot fix (unreadable selector, unknown WSL
/// presence).
pub const TRANSFER_ACK_REQUIRED_EXIT_CODE: i32 = 74;
const POSTCLEAR_MARKER: &str = "NativeUninstallPostclearPending";
const POSTCLEAR_MARKER_VALUE: &str = "civiccast-native-sole-active-v1";
/// Records that an acknowledged ActiveRuntime transfer to Wsl was performed
/// and verified, so the D1 "Cross-uninstall (active, survivor present)"
/// proof-matrix row has a durable, independently-inspectable fact to assert
/// against, distinct from (and in addition to) the ActiveRuntime value
/// itself flipping to `"wsl"`. Unlike `POSTCLEAR_MARKER`, this marker is a
/// permanent transfer-occurred record -- it is never consumed/cleared by
/// POSTUNINSTALL.
const TRANSFER_MARKER: &str = "NativeUninstallTransferCompleted";
const TRANSFER_MARKER_VALUE: &str = "civiccast-native-transfer-v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Product {
    Native,
    Wsl,
}

impl Product {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "native" => Some(Self::Native),
            "wsl" => Some(Self::Wsl),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectorState {
    Native,
    Wsl,
    Absent,
    Unreadable,
}

impl SelectorState {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "native" => Some(Self::Native),
            "wsl" => Some(Self::Wsl),
            "absent" => Some(Self::Absent),
            "unreadable" => Some(Self::Unreadable),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OtherProductState {
    Present,
    Absent,
    Unknown,
}

impl OtherProductState {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "present" => Some(Self::Present),
            "absent" => Some(Self::Absent),
            "unknown" => Some(Self::Unknown),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferState {
    NotRequested,
    AcceptedAndVerified,
    Refused,
    Failed,
}

impl TransferState {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "not-requested" => Some(Self::NotRequested),
            "accepted-and-verified" => Some(Self::AcceptedAndVerified),
            "refused" => Some(Self::Refused),
            "failed" => Some(Self::Failed),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    AllowInactive,
    AllowNoOwner,
    AllowAfterTransfer,
    AllowSolePostclear,
    Block,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectorMutation {
    None,
    ClearInPostUninstall,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UninstallPlan {
    pub decision: Decision,
    pub selector_mutation: SelectorMutation,
}

fn blocked_plan() -> UninstallPlan {
    UninstallPlan {
        decision: Decision::Block,
        selector_mutation: SelectorMutation::None,
    }
}

/// The real-world result of [`native_uninstall_preflight`] once acknowledgment
/// is factored in: either the uninstall may proceed with `plan` (which may
/// still be a no-mutation `AllowInactive`/`AllowNoOwner`/`AllowAfterTransfer`,
/// or an `AllowSolePostclear` whose marker has already been armed), or an
/// un-acknowledged transfer-eligible call needs the caller to obtain operator
/// acknowledgment before retrying. This is a CLI/NSIS-facing wrapper around
/// [`UninstallPlan`], not a change to the pure policy core's [`Decision`]
/// enum -- `decide` itself is untouched.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PreflightOutcome {
    Allowed(UninstallPlan),
    TransferAcknowledgmentRequired,
}

/// True exactly when acknowledging a transfer right now would flip `decide`'s
/// outcome from `Block` to `AllowAfterTransfer` -- i.e. this IS the D1
/// "uninstalling the ACTIVE product is blocked until ownership is
/// transferred" case. This is a CLI/NSIS-facing classification layered on
/// top of the pure policy core (it calls `decide`, never duplicates its
/// logic), not a new `Decision` variant: it lets the interactive surfaces
/// (the NSIS `MB_YESNO` prompt, the CLI's `--acknowledge-transfer` flag) know
/// WHEN prompting is the right response to a `Block`, versus a `Block` for an
/// unrelated reason (unreadable selector, unknown WSL-presence probe) that no
/// acknowledgment can fix.
pub fn transfer_would_unblock(
    product: Product,
    selector: SelectorState,
    other_product: OtherProductState,
) -> bool {
    let currently_active = matches!(
        (product, selector),
        (Product::Native, SelectorState::Native) | (Product::Wsl, SelectorState::Wsl)
    );
    currently_active
        && other_product == OtherProductState::Present
        && decide(
            product,
            selector,
            other_product,
            TransferState::NotRequested,
        )
        .decision
            == Decision::Block
}

pub fn decide(
    product: Product,
    selector: SelectorState,
    other_product: OtherProductState,
    transfer: TransferState,
) -> UninstallPlan {
    let no_mutation = |decision| UninstallPlan {
        decision,
        selector_mutation: SelectorMutation::None,
    };

    match selector {
        SelectorState::Unreadable => no_mutation(Decision::Block),
        SelectorState::Absent => no_mutation(Decision::AllowNoOwner),
        SelectorState::Native if product == Product::Wsl => no_mutation(Decision::AllowInactive),
        SelectorState::Wsl if product == Product::Native => no_mutation(Decision::AllowInactive),
        SelectorState::Native | SelectorState::Wsl => match other_product {
            OtherProductState::Present if transfer == TransferState::AcceptedAndVerified => {
                no_mutation(Decision::AllowAfterTransfer)
            }
            OtherProductState::Absent => UninstallPlan {
                decision: Decision::AllowSolePostclear,
                selector_mutation: SelectorMutation::ClearInPostUninstall,
            },
            OtherProductState::Present | OtherProductState::Unknown => no_mutation(Decision::Block),
        },
    }
}

/// Converts independently observed NSIS adapter strings into a safe plan.
///
/// Parsing is deliberately exact: missing, misspelled, or unexpected values
/// produce a blocking plan rather than defaulting to an uninstall action.
pub fn decide_from_adapter_inputs(
    product: &str,
    selector: &str,
    other_product: &str,
    transfer: &str,
) -> UninstallPlan {
    match (
        Product::parse(product),
        SelectorState::parse(selector),
        OtherProductState::parse(other_product),
        TransferState::parse(transfer),
    ) {
        (Some(product), Some(selector), Some(other_product), Some(transfer)) => {
            decide(product, selector, other_product, transfer)
        }
        _ => blocked_plan(),
    }
}

/// Parse the complete process argument vector for the uninstall-policy mode.
///
/// The policy sentinel owns the invocation whenever it is present. Every
/// missing, duplicate, or foreign argument then returns a blocking plan; a
/// generic `--help` or another CivicCast mode can never hijack the request.
pub fn decide_from_cli_args(args: &[String]) -> Option<UninstallPlan> {
    const SENTINEL: &str = "--civiccast-native-uninstall-policy";
    if !args.iter().any(|arg| arg == SENTINEL) {
        return None;
    }

    let mut sentinel_seen = false;
    let mut product: Option<&str> = None;
    let mut selector: Option<&str> = None;
    let mut other_product: Option<&str> = None;
    let mut transfer: Option<&str> = None;
    let mut index = 0;
    while index < args.len() {
        let argument = args[index].as_str();
        if argument == SENTINEL {
            if sentinel_seen {
                return Some(blocked_plan());
            }
            sentinel_seen = true;
            index += 1;
            continue;
        }

        let destination = match argument {
            "--product" => &mut product,
            "--selector" => &mut selector,
            "--other-product" => &mut other_product,
            "--transfer-state" => &mut transfer,
            _ => return Some(blocked_plan()),
        };
        if destination.is_some() || index + 1 >= args.len() {
            return Some(blocked_plan());
        }
        *destination = Some(args[index + 1].as_str());
        index += 2;
    }

    Some(match (product, selector, other_product, transfer) {
        (Some(product), Some(selector), Some(other_product), Some(transfer)) => {
            decide_from_adapter_inputs(product, selector, other_product, transfer)
        }
        _ => blocked_plan(),
    })
}

pub fn decision_token(plan: UninstallPlan) -> &'static str {
    match plan.decision {
        Decision::AllowInactive => "allow-inactive",
        Decision::AllowNoOwner => "allow-no-owner",
        Decision::AllowAfterTransfer => "allow-after-transfer",
        Decision::AllowSolePostclear => "allow-sole-postclear",
        Decision::Block => "block",
    }
}

pub fn selector_mutation_token(plan: UninstallPlan) -> &'static str {
    match plan.selector_mutation {
        SelectorMutation::None => "none",
        SelectorMutation::ClearInPostUninstall => "clear-postuninstall",
    }
}

#[cfg(target_os = "windows")]
fn classify_registry_error(error: &io::Error) -> OtherProductState {
    if error.kind() == io::ErrorKind::NotFound {
        OtherProductState::Absent
    } else {
        OtherProductState::Unknown
    }
}

#[cfg(target_os = "windows")]
pub(crate) fn probe_active_runtime_selector() -> SelectorState {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let key = match hklm.open_subkey_with_flags(SELECTOR_KEY, KEY_READ | KEY_WOW64_64KEY) {
        Ok(key) => key,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return SelectorState::Absent,
        Err(_) => return SelectorState::Unreadable,
    };
    match key.get_value::<String, _>(SELECTOR_VALUE) {
        Ok(value) => SelectorState::parse(&value).unwrap_or(SelectorState::Unreadable),
        Err(error) if error.kind() == io::ErrorKind::NotFound => SelectorState::Absent,
        Err(_) => SelectorState::Unreadable,
    }
}

/// Well-known service SIDs that are never a real user profile and never own
/// a per-user WSL install. Compared case-insensitively.
const WELL_KNOWN_SERVICE_SIDS: [&str; 3] = ["S-1-5-18", "S-1-5-19", "S-1-5-20"];

/// Decide whether an `HKEY_USERS` top-level subkey name should be skipped
/// when hunting for another user's WSL ARP registration: `.DEFAULT` is not a
/// real profile, `*_Classes` subkeys are the per-user COM/shell shadow of an
/// already-enumerated SID and never carry ARP entries, and the well-known
/// service SIDs (SYSTEM/LOCAL SERVICE/NETWORK SERVICE) are never a WSL owner.
///
/// Pure and unit-tested independent of any live registry.
fn should_skip_users_subkey(name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    if upper == ".DEFAULT" {
        return true;
    }
    if upper.ends_with("_CLASSES") {
        return true;
    }
    WELL_KNOWN_SERVICE_SIDS.iter().any(|sid| upper == *sid)
}

/// Fail-closed combination of independently observed registry-view
/// classifications: any `Present` wins outright; otherwise any `Unknown`
/// (a probe that failed for a reason other than "not found", or an
/// enumeration step that could not run at all) wins over `Absent`; only when
/// every observed state is `Absent` does the combination report `Absent`.
///
/// Pure and unit-tested independent of any live registry, mirroring the
/// existing `classify_registry_error` testing style.
fn combine_probe_results<I>(results: I) -> OtherProductState
where
    I: IntoIterator<Item = OtherProductState>,
{
    let mut saw_unknown = false;
    for state in results {
        match state {
            OtherProductState::Present => return OtherProductState::Present,
            OtherProductState::Unknown => saw_unknown = true,
            OtherProductState::Absent => {}
        }
    }
    if saw_unknown {
        OtherProductState::Unknown
    } else {
        OtherProductState::Absent
    }
}

#[cfg(target_os = "windows")]
pub(crate) fn probe_wsl_arp() -> OtherProductState {
    probe_wsl_arp_observed().0
}

/// [`probe_wsl_arp`] with every individual registry observation it made
/// kept alongside the combined verdict, so a caller that has to explain a
/// refusal (the install-time ownership claim) can name the hive, view, error
/// kind and raw error code instead of the bare word `Unknown`.
///
/// Field defect (beta.5, 2026-09-09): an elevated install on a machine with
/// uninstall history got `Unknown` back from this probe, refused to claim
/// ownership, and the only line naming WHY went to stderr -> the NSIS
/// details pane -> nowhere. A non-elevated mirror of the probe read
/// NotFound everywhere, so the failing observation was never identified.
#[cfg(target_os = "windows")]
fn probe_wsl_arp_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "user-ARP";
    let mut observations = Vec::new();

    // Same-user, un-elevated case: both HKCU ARP views must agree Absent
    // before we trust it; any denied/broken view is Unknown and blocks.
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let current_user = current_user_name();
    for (view, access) in [("64-bit", KEY_WOW64_64KEY), ("32-bit", KEY_WOW64_32KEY)] {
        observations.push(observe_registry_open(
            SOURCE,
            "HKCU".to_string(),
            view,
            &hkcu,
            WSL_ARP_KEY,
            KEY_READ | access,
            &current_user,
        ));
    }
    let hkcu_state = combine_probe_results(observations.iter().map(|o| o.state));
    if hkcu_state == OtherProductState::Present {
        return (OtherProductState::Present, observations);
    }

    // Elevated per-machine case: HKCU is the elevating admin's hive, so also
    // check every other loaded user hive via HKEY_USERS (see module
    // doc-comment for the loaded-hives-only limitation this does not solve).
    let hkey_users = RegKey::predef(HKEY_USERS);
    let names: Vec<String> = match hkey_users
        .enum_keys()
        .collect::<Result<Vec<String>, io::Error>>()
    {
        Ok(names) => names,
        // Enumeration itself failing (e.g. access denied) must fail closed:
        // it means we cannot rule out another loaded user owning WSL.
        Err(error) => {
            observations.push(ProbeObservation::from_registry_error(
                SOURCE,
                "HKU (hive enumeration)".to_string(),
                "n/a",
                &error,
            ));
            return (OtherProductState::Unknown, observations);
        }
    };
    for name in names
        .into_iter()
        .filter(|name| !should_skip_users_subkey(name))
    {
        let owner = hive_owner_name(&hkey_users, &name);
        for (view, access) in [("64-bit", KEY_WOW64_64KEY), ("32-bit", KEY_WOW64_32KEY)] {
            observations.push(observe_registry_open(
                SOURCE,
                format!("HKU\\{name}"),
                view,
                &hkey_users,
                &format!("{name}\\{WSL_ARP_KEY}"),
                KEY_READ | access,
                &owner,
            ));
        }
    }
    let state = combine_probe_results(observations.iter().map(|o| o.state));
    (state, observations)
}

// ---------------------------------------------------------------------------
// Install-time runtime-ownership evidence
// ---------------------------------------------------------------------------

/// The CivicCast WSL distro's registered `DistributionName`. MUST equal
/// `civiccast.native.runtime_guard.WSL_DISTRO_NAME`; pinned cross-language
/// by `tests/policy/test_native_installer_identity.py`.
pub const WSL_DISTRO_NAME: &str = "CivicCast-Ubuntu-24.04";
/// Per-user WSL distro registration subtree (relative to a loaded
/// `HKEY_USERS\<SID>` hive). MUST equal
/// `civiccast.native.win_probes.WSL_LXSS_KEY_PATH`.
pub const WSL_LXSS_KEY_PATH: &str = r"Software\Microsoft\Windows\CurrentVersion\Lxss";
/// Service names `sc query` is asked about, in order (modern, then legacy).
/// MUST equal `civiccast.native.win_probes.WSL_SERVICE_NAMES`.
pub const WSL_SERVICE_NAMES: [&str; 2] = ["WslService", "LxssManager"];
/// Win32 `ERROR_SERVICE_DOES_NOT_EXIST`: the ONE `sc query` exit code that
/// definitively means "no such service".
pub const ERROR_SERVICE_DOES_NOT_EXIST: i32 = 1060;
/// The WSL product's per-user autostart entry: `Run` key (relative to a user
/// hive) and value name. MUST equal
/// `civiccast.native.runtime_guard.RUN_KEY_PATH` / `RUN_VALUE_NAME`; pinned
/// cross-language by `tests/policy/test_native_installer_identity.py`.
pub const WSL_AUTOSTART_RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
pub const WSL_AUTOSTART_RUN_VALUE: &str = "CivicCast Autostart";

/// The Add/Remove Programs values read from a WSL-product uninstall key the
/// moment it is found `Present`, so the refusal (or the inert-leftover
/// warning) can name the product instead of the bare word `present`.
///
/// Field report (2026-09-09, corrected): the refused box had a real
/// `HKCU\...\Uninstall\CivicCast Installer` registration -- DisplayName
/// "CivicCast Installer", DisplayVersion 3.0.0-beta1, Publisher civiccast,
/// InstallLocation under the user's `AppData\Local`, UninstallString ending
/// in `uninstall.exe` -- and nothing that reached the log said so.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ArpProductRecord {
    /// The account the hive belongs to: `%USERNAME%` for `HKCU`, the
    /// hive's `Volatile Environment\USERNAME` for `HKU\<SID>` (the SID itself
    /// when that value is not loaded), `(per-machine)` for `HKLM`.
    pub user: String,
    pub display_name: Option<String>,
    pub display_version: Option<String>,
    pub publisher: Option<String>,
    pub install_location: Option<String>,
    pub uninstall_string: Option<String>,
}

impl ArpProductRecord {
    /// `CivicCast Installer 3.0.0-beta1` -- DisplayName then DisplayVersion,
    /// falling back to the uninstall key's own name when ARP carries none.
    pub fn title(&self) -> String {
        let name = self
            .display_name
            .as_deref()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or("CivicCast Installer");
        match self
            .display_version
            .as_deref()
            .filter(|s| !s.trim().is_empty())
        {
            Some(version) => format!("{name} {version}"),
            None => name.to_string(),
        }
    }

    /// `CivicCast Installer 3.0.0-beta1; Publisher civiccast; InstallLocation
    /// ...; UninstallString ...` -- every value ARP carried, none invented.
    pub fn summary(&self) -> String {
        let mut parts = vec![format!("user {}", self.user), self.title()];
        for (label, value) in [
            ("Publisher", &self.publisher),
            ("InstallLocation", &self.install_location),
            ("UninstallString", &self.uninstall_string),
        ] {
            if let Some(value) = value.as_deref().filter(|s| !s.trim().is_empty()) {
                parts.push(format!("{label} {value}"));
            }
        }
        parts.join("; ")
    }

    /// Reads the five ARP values from an already-open uninstall key. A
    /// missing value is `None`; a read error other than not-found is ALSO
    /// `None` here (the key opened, so `Present` already stands -- the
    /// record is descriptive, never a classifier).
    #[cfg(target_os = "windows")]
    fn read_from(key: &RegKey, user: String) -> Self {
        let read = |name: &str| key.get_value::<String, _>(name).ok();
        Self {
            user,
            display_name: read("DisplayName"),
            display_version: read("DisplayVersion"),
            publisher: read("Publisher"),
            install_location: read("InstallLocation"),
            uninstall_string: read("UninstallString"),
        }
    }
}

/// One registry or SCM observation made while establishing whether the
/// CivicCast WSL product is on this machine. Carries enough to reproduce
/// the failing read by hand: WHICH source, WHICH hive/SID (or service),
/// WHICH WOW64 view, what it classified to, and the raw error behind an
/// `Unknown`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeObservation {
    /// `user-ARP`, `machine-ARP`, `distro-scan` or `wsl-service`.
    pub source: &'static str,
    /// `HKCU`, `HKU\<SID>`, `HKLM`, `HKU (hive enumeration)`,
    /// `sc query <name>` ...
    pub scope: String,
    /// `64-bit`, `32-bit`, or `n/a` where the view does not apply.
    pub view: &'static str,
    pub state: OtherProductState,
    /// `io::ErrorKind` debug name (e.g. `PermissionDenied`) when the read
    /// errored with anything other than a clean not-found.
    pub error_kind: Option<String>,
    /// Raw OS error code (`raw_os_error`) or `sc.exe` exit code.
    pub raw_code: Option<i32>,
    /// The ARP values behind a `Present` uninstall-key observation (user-ARP
    /// and machine-ARP sources only); `None` for every other observation.
    pub product: Option<ArpProductRecord>,
}

impl ProbeObservation {
    pub fn settled(
        source: &'static str,
        scope: String,
        view: &'static str,
        state: OtherProductState,
    ) -> Self {
        Self {
            source,
            scope,
            view,
            state,
            error_kind: None,
            raw_code: None,
            product: None,
        }
    }

    /// Classifies exactly like [`classify_registry_error`] (NotFound ->
    /// Absent, anything else -> Unknown) but keeps the error's kind and raw
    /// code for the report. A NotFound carries no error detail: it is the
    /// expected, readable absence.
    #[cfg(target_os = "windows")]
    pub fn from_registry_error(
        source: &'static str,
        scope: String,
        view: &'static str,
        error: &io::Error,
    ) -> Self {
        let state = classify_registry_error(error);
        let (error_kind, raw_code) = if state == OtherProductState::Absent {
            (None, None)
        } else {
            (Some(format!("{:?}", error.kind())), error.raw_os_error())
        };
        Self {
            source,
            scope,
            view,
            state,
            error_kind,
            raw_code,
            product: None,
        }
    }
}

fn other_product_token(state: OtherProductState) -> &'static str {
    match state {
        OtherProductState::Present => "present",
        OtherProductState::Absent => "absent",
        OtherProductState::Unknown => "unknown",
    }
}

impl std::fmt::Display for ProbeObservation {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{} {}", self.source, self.scope)?;
        if self.view != "n/a" {
            write!(formatter, " ({} view)", self.view)?;
        }
        write!(formatter, ": {}", other_product_token(self.state))?;
        match (&self.error_kind, self.raw_code) {
            (Some(kind), Some(code)) => write!(formatter, " [{kind}, os error {code}]")?,
            (Some(kind), None) => write!(formatter, " [{kind}]")?,
            (None, Some(code)) => write!(formatter, " [exit {code}]")?,
            (None, None) => {}
        }
        if let Some(product) = &self.product {
            write!(formatter, " [{}]", product.summary())?;
        }
        Ok(())
    }
}

/// Everything the install-time ownership claim looked at before deciding
/// whether the CivicCast WSL product is on this machine.
///
/// `wsl_service` is NOT product evidence: `Present` there means only that a
/// WSL service (`WslService`/`LxssManager`) is registered at all -- WSL may
/// be installed for something unrelated. `Absent` there IS dispositive the
/// other way: with no WSL service on the machine, no CivicCast WSL product
/// can exist or transmit (the same rule
/// `civiccast.native.win_probes._confirm_absence_via_service` applies).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WslPresenceEvidence {
    /// The per-user ARP probe ([`probe_wsl_arp`]): HKCU plus every loaded
    /// `HKEY_USERS` hive, both WOW64 views.
    pub user_arp: OtherProductState,
    /// The WSL product's uninstall key under `HKLM`, both WOW64 views.
    pub machine_arp: OtherProductState,
    /// [`WSL_DISTRO_NAME`] registered under any loaded hive's Lxss subtree
    /// -- the LocalSystem-safe inventory the runtime guard itself trusts.
    pub distro_registration: OtherProductState,
    /// Whether ANY WSL service is registered with the SCM (see above).
    pub wsl_service: OtherProductState,
    /// The WSL product's `CivicCast Autostart` Run value
    /// ([`WSL_AUTOSTART_RUN_VALUE`]) under HKCU or any loaded `HKEY_USERS`
    /// hive. Only consulted by the inert-leftover rule
    /// ([`classify_wsl_product_for_claim`]); never product evidence on its
    /// own.
    pub autostart: OtherProductState,
    /// `HKLM\SOFTWARE\CivicCast\NativeUninstallTransferCompleted`: `Present`
    /// means a previous NATIVE install existed on this machine and its
    /// uninstall handed `ActiveRuntime` to the WSL product (the acknowledged
    /// transfer). Only consulted by the inert-leftover rule.
    pub native_transfer_marker: OtherProductState,
    pub observations: Vec<ProbeObservation>,
}

impl WslPresenceEvidence {
    /// Evidence consisting of the per-user ARP verdict alone, every
    /// corroborating source unconsulted (`Unknown`). Reproduces the
    /// pre-corroboration decision table exactly, which is what the
    /// orchestration tests pin. Test-only by construction (production
    /// always gathers the full evidence), hence the lint exemption.
    #[allow(dead_code)]
    pub fn user_arp_only(user_arp: OtherProductState) -> Self {
        Self {
            user_arp,
            machine_arp: OtherProductState::Unknown,
            distro_registration: OtherProductState::Unknown,
            wsl_service: OtherProductState::Unknown,
            autostart: OtherProductState::Unknown,
            native_transfer_marker: OtherProductState::Unknown,
            observations: Vec::new(),
        }
    }

    /// The first `Present` uninstall-key observation that carried an ARP
    /// record -- the product the refusal (or the inert-leftover warning)
    /// names. `None` when presence came only from the distro scan, or when
    /// no observation carried a record.
    pub fn present_product(&self) -> Option<(&ProbeObservation, &ArpProductRecord)> {
        self.observations
            .iter()
            .filter(|o| o.state == OtherProductState::Present)
            .find_map(|o| o.product.as_ref().map(|p| (o, p)))
    }

    /// The sentence the `Present` refusal leads with, in the exact shape
    /// the field report asked for:
    ///
    /// `Setup found another CivicCast product installed for user scott:
    /// CivicCast Installer 3.0.0-beta1 (registered at HKU\<SID>\...\Uninstall\
    /// CivicCast Installer; InstallLocation ...; UninstallString ...)`
    ///
    /// Falls back to naming the evidence when no ARP record was captured.
    /// Publisher is deliberately NOT in the lead (it is in the observation
    /// record and the recovery document): the lead has to fit the
    /// observation line's cap with a real SID and two real paths in it.
    pub fn describe_present_product(&self) -> String {
        self.describe_present_product_with(true)
    }

    /// [`describe_present_product`] built field by field: with
    /// `include_install_location` false the lead still carries the
    /// UninstallString but not the InstallLocation, so a caller with a
    /// character budget (the one-line observation) can drop a WHOLE field
    /// instead of cutting a path in half. Round 5 (delta review of round
    /// 4): the account name appears three times in the lead, and a 16-char
    /// name with a real-length SID overran the line's truncation point.
    ///
    /// [`describe_present_product`]: Self::describe_present_product
    pub fn describe_present_product_with(&self, include_install_location: bool) -> String {
        match self.present_product() {
            Some((observation, product)) => {
                let mut extras = Vec::new();
                for (label, value) in [
                    ("InstallLocation", &product.install_location),
                    ("UninstallString", &product.uninstall_string),
                ] {
                    if label == "InstallLocation" && !include_install_location {
                        continue;
                    }
                    if let Some(value) = value.as_deref().filter(|s| !s.trim().is_empty()) {
                        extras.push(format!("{label} {value}"));
                    }
                }
                let extras = if extras.is_empty() {
                    String::new()
                } else {
                    format!("; {}", extras.join("; "))
                };
                format!(
                    "Setup found another CivicCast product installed for user {}: {} \
                     (registered at {}\\...\\Uninstall\\CivicCast Installer{extras})",
                    product.user,
                    product.title(),
                    observation.scope
                )
            }
            None => format!(
                "Setup found another CivicCast product (the CivicCast WSL product) installed on \
                 this machine: {}",
                self.explain()
            ),
        }
    }

    /// One line per source, each source followed by the individual
    /// observations that were NOT a clean absence (a hive that read
    /// `absent` explains nothing; the one that read `unknown` is the whole
    /// story). Example:
    ///
    /// `user-ARP=unknown [user-ARP HKU\S-1-5-21-...-1001 (64-bit view):
    /// unknown [PermissionDenied, os error 5]], machine-ARP=absent,
    /// distro-scan=absent, wsl-service=present`
    pub fn explain(&self) -> String {
        let sources = [
            ("user-ARP", self.user_arp),
            ("machine-ARP", self.machine_arp),
            ("distro-scan", self.distro_registration),
            ("wsl-service", self.wsl_service),
            ("autostart", self.autostart),
            ("transfer-marker", self.native_transfer_marker),
        ];
        sources
            .iter()
            .map(|(source, state)| {
                let notable: Vec<String> = self
                    .observations
                    .iter()
                    .filter(|o| o.source == *source && o.state != OtherProductState::Absent)
                    .map(ToString::to_string)
                    .collect();
                if notable.is_empty() {
                    format!("{source}={}", other_product_token(*state))
                } else {
                    format!(
                        "{source}={} [{}]",
                        other_product_token(*state),
                        notable.join("; ")
                    )
                }
            })
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Round 5 (delta review of round 4): the inert-leftover conditions
    /// ([`classify_wsl_product_for_claim`]) that did NOT hold for this
    /// evidence, one sentence each, for the recovery document's "What it
    /// means" -- derived from the evidence, never asserted. Round 4 named
    /// the per-machine cause for every Present, so a station with a live
    /// distro was told its owner was signed out. Empty when every condition
    /// held (that verdict is `PresentInert`, which is not a refusal).
    ///
    /// Round 6 (delta review of round 5, MAJOR 2): the first condition --
    /// the entry was found in a LOADED user hive -- is explained from BOTH
    /// ARP reads. Round 5 gated its sentence on `user_arp != Present`
    /// alone, so a presence established by the distro scan (no ARP entry
    /// anywhere) was called "found only per-machine (HKLM)" two lines under
    /// `machine-ARP=Absent`, and a per-user probe that FAILED (`Unknown`)
    /// was called "no signed-in account's hive carries it". `Unknown` is
    /// unknown: the sentence says the probe could not read it, never that
    /// the entry is absent.
    pub fn unmet_inert_conditions(&self) -> Vec<String> {
        use OtherProductState::{Absent, Present, Unknown};
        let mut unmet = Vec::new();
        match (self.user_arp, self.machine_arp) {
            (Present, _) => {}
            (Absent, Present) => unmet.push(
                "the entry was found only per-machine (HKLM) and no signed-in account's hive \
                 carries it (user-ARP=absent), so its owner may be signed out and the distro \
                 and autostart reads below never looked at that owner's hive"
                    .into(),
            ),
            (Unknown, Present) => unmet.push(
                "the entry was found per-machine (HKLM) and no signed-in account's hive could \
                 be read (user-ARP=unknown), so setup could not look for the owner's distro or \
                 autostart state"
                    .into(),
            ),
            (Absent, Absent) => unmet.push(
                "no Add/Remove Programs entry was found in any hive (user-ARP=absent, \
                 machine-ARP=absent): the product's presence comes from the distro scan alone"
                    .into(),
            ),
            (Absent, Unknown) => unmet.push(
                "no signed-in account's hive carries the Add/Remove Programs entry \
                 (user-ARP=absent) and the per-machine read was inconclusive \
                 (machine-ARP=unknown)"
                    .into(),
            ),
            (Unknown, Absent) => unmet.push(
                "the per-user Add/Remove Programs probe could not read a hive \
                 (user-ARP=unknown) and no per-machine entry exists (machine-ARP=absent)"
                    .into(),
            ),
            (Unknown, Unknown) => unmet.push(
                "neither Add/Remove Programs probe could be read (user-ARP=unknown, \
                 machine-ARP=unknown)"
                    .into(),
            ),
        }
        match self.distro_registration {
            Absent => {}
            Present => unmet.push("a CivicCast distro is registered (distro-scan=present)".into()),
            Unknown => unmet.push("the distro scan was inconclusive (distro-scan=unknown)".into()),
        }
        match self.autostart {
            Absent => {}
            Present => {
                unmet.push("the CivicCast Autostart Run entry is set (autostart=present)".into())
            }
            Unknown => unmet.push("the autostart read was inconclusive (autostart=unknown)".into()),
        }
        match self.native_transfer_marker {
            Present => {}
            Absent => unmet.push(
                "no previous native install's hand-off marker is set (transfer-marker=absent)"
                    .into(),
            ),
            Unknown => unmet
                .push("the hand-off marker could not be read (transfer-marker=unknown)".into()),
        }
        unmet
    }

    /// Every observation, one per line, for the recovery document.
    pub fn observation_lines(&self) -> Vec<String> {
        self.observations.iter().map(ToString::to_string).collect()
    }
}

/// Fold the four evidence sources into the single `OtherProductState` the
/// claim decision ([`decide_install_selector_claim`]) consumes.
///
/// | user-ARP  | machine-ARP | distro-scan | wsl-service | verdict   |
/// |-----------|-------------|-------------|-------------|-----------|
/// | Present   | any         | any         | any         | Present   |
/// | any       | Present     | any         | any         | Present   |
/// | any       | any         | Present     | any         | Present   |
/// | Absent    | not Present | not Present | any         | Absent    |
/// | Unknown   | any         | any         | Absent      | Absent    |
/// | Unknown   | Absent      | Absent      | not Absent  | Absent    |
/// | Unknown   | otherwise                               | Unknown   |
///
/// The `Absent` user-ARP row is the pre-existing contract, unchanged: it
/// was sufficient on its own before corroboration existed, so a machine
/// where `sc.exe` happens to misbehave does not regress. The two new
/// `Unknown`-user-ARP rows are the field defect: the per-user probe could
/// not read some hive, but machine-wide evidence (no WSL service at all, or
/// no product ARP under HKLM AND no CivicCast distro registered anywhere)
/// rules the WSL product out, so the install claims ownership and logs the
/// hive it could not read. Anything else stays `Unknown` and the install
/// refuses with the observation.
pub fn corroborate_wsl_product_state(evidence: &WslPresenceEvidence) -> OtherProductState {
    if [
        evidence.user_arp,
        evidence.machine_arp,
        evidence.distro_registration,
    ]
    .contains(&OtherProductState::Present)
    {
        return OtherProductState::Present;
    }
    match evidence.user_arp {
        OtherProductState::Present => OtherProductState::Present,
        OtherProductState::Absent => OtherProductState::Absent,
        OtherProductState::Unknown => {
            if evidence.wsl_service == OtherProductState::Absent {
                return OtherProductState::Absent;
            }
            if evidence.machine_arp == OtherProductState::Absent
                && evidence.distro_registration == OtherProductState::Absent
            {
                OtherProductState::Absent
            } else {
                OtherProductState::Unknown
            }
        }
    }
}

/// The install-time verdict about the CivicCast WSL product, one step past
/// [`corroborate_wsl_product_state`]'s tri-state: `Present` splits into a
/// product that is really there and an inert Add/Remove Programs leftover.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WslProductVerdict {
    /// A CivicCast WSL product registration that setup must not take the
    /// machine away from (uninstall key AND something alive beside it, or a
    /// distro, or a leftover whose inertness could not be confirmed).
    Present,
    /// An old WSL-product uninstall registration with NOTHING alive behind
    /// it -- see [`classify_wsl_product_for_claim`] for the three conditions
    /// -- on a machine a previous native install once owned. Claimable, with
    /// a warning naming the leftover.
    PresentInert,
    Absent,
    Unknown,
}

impl WslProductVerdict {
    pub fn token(self) -> &'static str {
        match self {
            Self::Present => "present",
            Self::PresentInert => "present-inert",
            Self::Absent => "absent",
            Self::Unknown => "unknown",
        }
    }
}

/// Fold the evidence into the verdict [`decide_install_selector_claim`]
/// consumes: [`corroborate_wsl_product_state`] first, then the
/// **inert-leftover rule** on its `Present`:
///
/// | corroborated | user-ARP | distro-scan | autostart | transfer-marker | verdict      |
/// |--------------|----------|-------------|-----------|-----------------|--------------|
/// | Absent       | any      | any         | any       | any             | Absent       |
/// | Unknown      | any      | any         | any       | any             | Unknown      |
/// | Present      | Present  | Absent      | Absent    | Present         | PresentInert |
/// | Present      | otherwise                                            | Present      |
///
/// The `user-ARP = Present` column (round 4, hostile review of round 3):
/// the distro scan and the autostart read only see hives Windows has
/// LOADED. A per-user ARP entry that read `Present` was found in a loaded
/// hive, so an `Absent` distro/autostart covers the hive that owns the
/// leftover. A MACHINE-scope entry (HKLM) names no hive: its owner may be
/// logged out, in which case the two per-user absences are reads of hives
/// that are not the owner's and prove nothing about inertness. That case is
/// "inertness unknown", which is `Present` (exit 87) -- never `PresentInert`.
///
/// Field report (2026-09-09, corrected): the refused box had the old WSL-era
/// "CivicCast Installer" 3.0.0-beta1 ARP entry under HKCU (-> `Present`),
/// no `CivicCast-Ubuntu-24.04` distro in any loaded hive, no
/// `CivicCast Autostart` Run entry, and
/// `NativeUninstallTransferCompleted` set -- a native install had been there
/// and handed off, then been uninstalled. Any tester who used the WSL
/// product, then native, then uninstalled native looks exactly like that,
/// and the ARP entry alone cannot transmit. All three conditions must be
/// CONFIDENT (`Absent`/`Absent`/`Present`): an `Unknown` in any of them
/// keeps the refusal.
pub fn classify_wsl_product_for_claim(evidence: &WslPresenceEvidence) -> WslProductVerdict {
    match corroborate_wsl_product_state(evidence) {
        OtherProductState::Absent => WslProductVerdict::Absent,
        OtherProductState::Unknown => WslProductVerdict::Unknown,
        OtherProductState::Present => {
            // The owner hive is known to be loaded only when the per-user
            // probe itself found the entry there; a machine-scope-only
            // Present leaves the per-user absences uncorroborated.
            let owner_hive_loaded = evidence.user_arp == OtherProductState::Present;
            if owner_hive_loaded
                && evidence.distro_registration == OtherProductState::Absent
                && evidence.autostart == OtherProductState::Absent
                && evidence.native_transfer_marker == OtherProductState::Present
            {
                WslProductVerdict::PresentInert
            } else {
                WslProductVerdict::Present
            }
        }
    }
}

/// Open the WSL product's uninstall `subkey` under `root` and classify the
/// result as one observation; a `Present` key also carries its ARP record
/// ([`ArpProductRecord::read_from`]) attributed to `owner`.
#[cfg(target_os = "windows")]
fn observe_registry_open(
    source: &'static str,
    scope: String,
    view: &'static str,
    root: &RegKey,
    subkey: &str,
    access: u32,
    owner: &str,
) -> ProbeObservation {
    match root.open_subkey_with_flags(subkey, access) {
        Ok(key) => {
            let mut observation =
                ProbeObservation::settled(source, scope, view, OtherProductState::Present);
            observation.product = Some(ArpProductRecord::read_from(&key, owner.to_string()));
            observation
        }
        Err(error) => ProbeObservation::from_registry_error(source, scope, view, &error),
    }
}

/// Open `subkey` under `root` and read the string value `value_name`:
/// `Present` when the value exists, `Absent` when the key OR the value is
/// not found, `Unknown` (error recorded) on anything else.
#[cfg(target_os = "windows")]
fn observe_registry_value(
    source: &'static str,
    scope: String,
    view: &'static str,
    root: &RegKey,
    subkey: &str,
    value_name: &str,
    access: u32,
) -> ProbeObservation {
    let key = match root.open_subkey_with_flags(subkey, access) {
        Ok(key) => key,
        Err(error) => return ProbeObservation::from_registry_error(source, scope, view, &error),
    };
    match key.get_value::<String, _>(value_name) {
        Ok(_) => ProbeObservation::settled(source, scope, view, OtherProductState::Present),
        Err(error) => ProbeObservation::from_registry_error(source, scope, view, &error),
    }
}

/// The account name behind `HKEY_CURRENT_USER` (the elevating admin during a
/// per-machine install).
#[cfg(target_os = "windows")]
fn current_user_name() -> String {
    std::env::var("USERNAME")
        .ok()
        .filter(|name| !name.trim().is_empty())
        .unwrap_or_else(|| "(current user)".to_string())
}

/// The account name behind a loaded `HKEY_USERS\<SID>` hive, read from its
/// `Volatile Environment\USERNAME` (present for every logged-on profile);
/// the SID itself when that value is not there. A registry read, like every
/// other observation in this module -- no LookupAccountSid dependency.
#[cfg(target_os = "windows")]
fn hive_owner_name(hkey_users: &RegKey, sid: &str) -> String {
    hkey_users
        .open_subkey_with_flags(format!("{sid}\\Volatile Environment"), KEY_READ)
        .and_then(|key| key.get_value::<String, _>("USERNAME"))
        .ok()
        .filter(|name| !name.trim().is_empty())
        .unwrap_or_else(|| sid.to_string())
}

/// The WSL product's `CivicCast Autostart` Run value under HKCU and every
/// loaded `HKEY_USERS` hive (both WOW64 views, same skip list as the ARP
/// probe). Consulted only by the inert-leftover rule; an enumeration
/// failure is `Unknown`, which keeps the refusal.
#[cfg(target_os = "windows")]
fn probe_wsl_autostart_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "autostart";
    let mut observations = Vec::new();
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    for (view, access) in [("64-bit", KEY_WOW64_64KEY), ("32-bit", KEY_WOW64_32KEY)] {
        observations.push(observe_registry_value(
            SOURCE,
            "HKCU\\...\\Run".to_string(),
            view,
            &hkcu,
            WSL_AUTOSTART_RUN_KEY,
            WSL_AUTOSTART_RUN_VALUE,
            KEY_READ | access,
        ));
    }
    let hkey_users = RegKey::predef(HKEY_USERS);
    let names: Vec<String> = match hkey_users
        .enum_keys()
        .collect::<Result<Vec<String>, io::Error>>()
    {
        Ok(names) => names,
        Err(error) => {
            observations.push(ProbeObservation::from_registry_error(
                SOURCE,
                "HKU (hive enumeration)".to_string(),
                "n/a",
                &error,
            ));
            return (OtherProductState::Unknown, observations);
        }
    };
    for name in names
        .into_iter()
        .filter(|name| !should_skip_users_subkey(name))
    {
        for (view, access) in [("64-bit", KEY_WOW64_64KEY), ("32-bit", KEY_WOW64_32KEY)] {
            observations.push(observe_registry_value(
                SOURCE,
                format!("HKU\\{name}\\...\\Run"),
                view,
                &hkey_users,
                &format!("{name}\\{WSL_AUTOSTART_RUN_KEY}"),
                WSL_AUTOSTART_RUN_VALUE,
                KEY_READ | access,
            ));
        }
    }
    let state = combine_probe_results(observations.iter().map(|o| o.state));
    (state, observations)
}

/// `HKLM\SOFTWARE\CivicCast\NativeUninstallTransferCompleted` (64-bit view,
/// the view every writer in this module uses): `Present` when a previous
/// native uninstall recorded an acknowledged hand-off to the WSL product.
#[cfg(target_os = "windows")]
fn probe_native_transfer_marker_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "transfer-marker";
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let observation = observe_registry_value(
        SOURCE,
        format!("HKLM\\{SELECTOR_KEY}\\{TRANSFER_MARKER}"),
        "64-bit",
        &hklm,
        SELECTOR_KEY,
        TRANSFER_MARKER,
        KEY_READ | KEY_WOW64_64KEY,
    );
    let state = observation.state;
    (state, vec![observation])
}

/// The WSL product's uninstall key under `HKLM` (a per-machine install of
/// the WSL product, or a per-machine remnant), both WOW64 views.
#[cfg(target_os = "windows")]
fn probe_wsl_machine_arp_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "machine-ARP";
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let observations: Vec<ProbeObservation> =
        [("64-bit", KEY_WOW64_64KEY), ("32-bit", KEY_WOW64_32KEY)]
            .into_iter()
            .map(|(view, access)| {
                observe_registry_open(
                    SOURCE,
                    "HKLM".to_string(),
                    view,
                    &hklm,
                    WSL_ARP_KEY,
                    KEY_READ | access,
                    "(per-machine)",
                )
            })
            .collect();
    let state = combine_probe_results(observations.iter().map(|o| o.state));
    (state, observations)
}

/// Port of `civiccast.native.win_probes.scan_registered_distros`: is
/// [`WSL_DISTRO_NAME`] the `DistributionName` of any `{guid}` subkey under
/// any loaded hive's [`WSL_LXSS_KEY_PATH`]? Same tri-state, same rule that
/// NotFound is the ONLY readable absence (a hive with no Lxss key, or a
/// `{guid}` without a `DistributionName`, is clean); every other error
/// downgrades that hive to `Unknown` with the error recorded.
#[cfg(target_os = "windows")]
fn probe_wsl_distro_registration_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "distro-scan";
    let hkey_users = RegKey::predef(HKEY_USERS);
    let names: Vec<String> = match hkey_users
        .enum_keys()
        .collect::<Result<Vec<String>, io::Error>>()
    {
        Ok(names) => names,
        Err(error) => {
            return (
                OtherProductState::Unknown,
                vec![ProbeObservation::from_registry_error(
                    SOURCE,
                    "HKU (hive enumeration)".to_string(),
                    "n/a",
                    &error,
                )],
            );
        }
    };
    let mut observations = Vec::new();
    for name in names
        .into_iter()
        .filter(|name| !should_skip_users_subkey(name))
    {
        let scope = format!("HKU\\{name}\\...\\Lxss");
        let lxss = match hkey_users
            .open_subkey_with_flags(format!("{name}\\{WSL_LXSS_KEY_PATH}"), KEY_READ)
        {
            Ok(key) => key,
            Err(error) => {
                observations.push(ProbeObservation::from_registry_error(
                    SOURCE, scope, "n/a", &error,
                ));
                continue;
            }
        };
        observations.push(scan_lxss_hive(SOURCE, scope, &lxss));
    }
    let state = combine_probe_results(observations.iter().map(|o| o.state));
    (state, observations)
}

/// One hive's Lxss subtree, folded to a single observation.
#[cfg(target_os = "windows")]
fn scan_lxss_hive(source: &'static str, scope: String, lxss: &RegKey) -> ProbeObservation {
    for guid in lxss.enum_keys() {
        let guid = match guid {
            Ok(guid) => guid,
            Err(error) => {
                return ProbeObservation::from_registry_error(source, scope, "n/a", &error);
            }
        };
        let distro = match lxss.open_subkey_with_flags(&guid, KEY_READ) {
            Ok(key) => key,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => {
                return ProbeObservation::from_registry_error(source, scope, "n/a", &error);
            }
        };
        match distro.get_value::<String, _>("DistributionName") {
            Ok(value) if value == WSL_DISTRO_NAME => {
                return ProbeObservation::settled(
                    source,
                    format!("{scope}\\{guid}"),
                    "n/a",
                    OtherProductState::Present,
                );
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return ProbeObservation::from_registry_error(source, scope, "n/a", &error);
            }
        }
    }
    ProbeObservation::settled(source, scope, "n/a", OtherProductState::Absent)
}

/// Classify one `sc query <name>` exit status the way
/// `civiccast.native.win_probes._default_wsl_service_present` does: 0 ->
/// the service exists (any state); 1060 -> definitively does not exist;
/// anything else -> Unknown. Pure; unit-tested.
pub fn classify_sc_query_exit_code(code: Option<i32>) -> OtherProductState {
    match code {
        Some(0) => OtherProductState::Present,
        Some(ERROR_SERVICE_DOES_NOT_EXIST) => OtherProductState::Absent,
        _ => OtherProductState::Unknown,
    }
}

/// Hard deadline for one `sc query <name>` child. MUST equal the Python
/// guard's `A2_TIMEOUT_SECONDS` (`civiccast.native.runtime_guard`), which
/// `_run_probe_argv` applies to the same probe: capture-style probes have
/// deadlocked live (Sandbox runs 14/15, see `pg_ctl_exec`), so the SCM read
/// gets a watchdog rather than an open-ended `Command::output()`.
pub const SC_QUERY_TIMEOUT_SECONDS: u64 = 5;

/// `%SYSTEMROOT%\System32\sc.exe`, pinned absolute for the same
/// CWD-hijack reason as `win_probes.SC_EXE` (a bare `sc.exe` argv[0] is
/// resolved via the CreateProcess search order). Falls back to `C:\Windows`
/// exactly like the Python constant.
#[cfg(target_os = "windows")]
fn sc_exe_path() -> std::path::PathBuf {
    let system_root = std::env::var_os("SYSTEMROOT")
        .unwrap_or_else(|| std::ffi::OsString::from(r"C:\Windows"));
    std::path::PathBuf::from(system_root)
        .join("System32")
        .join("sc.exe")
}

/// Spawn `command` with every stdio handle detached (the probe classifies the
/// exit status only, and inherited pipe handles are what kept the Python
/// capture-style probes draining forever) and wait for it with a hard
/// `deadline`. On expiry the child is killed and reaped, and the call fails
/// with an `io::ErrorKind::TimedOut` error so the caller's existing
/// spawn-error branch classifies it (`Unknown`, `error_kind = "TimedOut"`).
/// Unit-tested with a prompt child and a sleeping child.
pub fn wait_with_deadline(
    command: &mut std::process::Command,
    deadline: std::time::Duration,
) -> std::io::Result<std::process::ExitStatus> {
    use std::process::Stdio;
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let started = std::time::Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if started.elapsed() >= deadline {
            // The child may exit between try_wait and kill; either way it
            // is reaped here and the read is recorded as Unknown.
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!(
                    "probe child did not exit within {} ms and was killed",
                    deadline.as_millis()
                ),
            ));
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
}

/// Port of `_default_wsl_service_present`: `Present` the instant any WSL
/// service name resolves; `Absent` only when EVERY candidate is a definite
/// 1060; `Unknown` on anything else. The SCM query is used rather than
/// winreg because the Store-packaged `WslService` does not expose a raw
/// `HKLM\SYSTEM\...\Services` key even while it is running.
/// Each query runs under [`wait_with_deadline`] with
/// [`SC_QUERY_TIMEOUT_SECONDS`]; an expired child is killed and recorded as
/// `Unknown` with `error_kind = TimedOut`, never as `Absent`.
#[cfg(target_os = "windows")]
fn probe_wsl_service_observed() -> (OtherProductState, Vec<ProbeObservation>) {
    const SOURCE: &str = "wsl-service";
    let mut observations = Vec::new();
    for name in WSL_SERVICE_NAMES {
        let scope = format!("sc query {name}");
        let observation = match wait_with_deadline(
            std::process::Command::new(sc_exe_path()).args(["query", name]),
            std::time::Duration::from_secs(SC_QUERY_TIMEOUT_SECONDS),
        ) {
            Ok(status) => {
                let code = status.code();
                let state = classify_sc_query_exit_code(code);
                let mut observation = ProbeObservation::settled(SOURCE, scope, "n/a", state);
                if state != OtherProductState::Absent {
                    observation.raw_code = code;
                }
                observation
            }
            Err(error) => ProbeObservation {
                source: SOURCE,
                scope,
                view: "n/a",
                state: OtherProductState::Unknown,
                error_kind: Some(format!("{:?}", error.kind())),
                raw_code: error.raw_os_error(),
                product: None,
            },
        };
        let state = observation.state;
        observations.push(observation);
        if state != OtherProductState::Absent {
            return (state, observations);
        }
    }
    (OtherProductState::Absent, observations)
}

/// Production evidence gathering for the install-time claim: the per-user
/// ARP probe plus the three machine-wide corroborating sources, every
/// individual observation retained.
#[cfg(target_os = "windows")]
pub(crate) fn probe_wsl_presence_evidence() -> WslPresenceEvidence {
    let (user_arp, mut observations) = probe_wsl_arp_observed();
    let (machine_arp, machine_observations) = probe_wsl_machine_arp_observed();
    let (distro_registration, distro_observations) = probe_wsl_distro_registration_observed();
    let (wsl_service, service_observations) = probe_wsl_service_observed();
    let (autostart, autostart_observations) = probe_wsl_autostart_observed();
    let (native_transfer_marker, marker_observations) = probe_native_transfer_marker_observed();
    observations.extend(machine_observations);
    observations.extend(distro_observations);
    observations.extend(service_observations);
    observations.extend(autostart_observations);
    observations.extend(marker_observations);
    WslPresenceEvidence {
        user_arp,
        machine_arp,
        distro_registration,
        wsl_service,
        autostart,
        native_transfer_marker,
        observations,
    }
}

#[cfg(target_os = "windows")]
fn clear_postclear_marker() -> Result<(), String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    match hklm.open_subkey_with_flags(SELECTOR_KEY, KEY_SET_VALUE | KEY_WOW64_64KEY) {
        Ok(key) => key
            .delete_value(POSTCLEAR_MARKER)
            .or_else(|error| {
                if error.kind() == io::ErrorKind::NotFound {
                    Ok(())
                } else {
                    Err(error)
                }
            })
            .map_err(|error| format!("Could not clear stale native uninstall marker: {error}")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!(
            "Could not open the native lifecycle key to clear a stale marker: {error}"
        )),
    }
}

#[cfg(target_os = "windows")]
fn write_postclear_marker() -> Result<(), String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let (key, _) = hklm
        .create_subkey_with_flags(SELECTOR_KEY, KEY_READ | KEY_SET_VALUE | KEY_WOW64_64KEY)
        .map_err(|error| format!("Could not create the native lifecycle marker: {error}"))?;
    key.set_value(POSTCLEAR_MARKER, &POSTCLEAR_MARKER_VALUE)
        .map_err(|error| {
            format!("Could not persist the native sole-active uninstall marker: {error}")
        })?;
    let persisted: String = key.get_value(POSTCLEAR_MARKER).map_err(|error| {
        format!("Could not verify the native sole-active uninstall marker: {error}")
    })?;
    if persisted != POSTCLEAR_MARKER_VALUE {
        return Err(
            "Native sole-active uninstall marker verification returned an unexpected value."
                .to_string(),
        );
    }
    Ok(())
}

/// Write `ActiveRuntime = "native"` and read it back to verify the write
/// landed -- the SAME write + read-back-verify convention as
/// [`write_postclear_marker`] immediately above, reusing
/// `SELECTOR_KEY`/`SELECTOR_VALUE` rather than re-declaring the registry path
/// a second time.
///
/// TWO callers, both of which decide WHEN this is safe and neither of which
/// this function second-guesses -- it only writes and verifies:
///
/// * D5 Repair's selector-repair remedy
///   (`native_repair::decide_selector_repair_action`).
/// * The install-time native ownership claim
///   ([`decide_install_selector_claim`], chain G).
///
/// Matches this module's existing untested-directly convention for real
/// registry mutation (the HARD RULE forbids unit-testing real registry
/// writes; both gating decisions are unit-tested instead).
#[cfg(target_os = "windows")]
pub(crate) fn write_selector_native() -> Result<(), String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let (key, _) = hklm
        .create_subkey_with_flags(SELECTOR_KEY, KEY_READ | KEY_SET_VALUE | KEY_WOW64_64KEY)
        .map_err(|error| {
            format!(
                "Could not create/open the native lifecycle key to repair ActiveRuntime: {error}"
            )
        })?;
    key.set_value(SELECTOR_VALUE, &"native")
        .map_err(|error| format!("Could not write ActiveRuntime: {error}"))?;
    let persisted: String = key
        .get_value(SELECTOR_VALUE)
        .map_err(|error| format!("Could not verify ActiveRuntime after the write: {error}"))?;
    if persisted != NATIVE_SELECTOR_VALUE {
        return Err(
            "ActiveRuntime write verification mismatch: wrote \"native\" but the read-back did \
             not match."
                .to_string(),
        );
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn write_selector_native() -> Result<(), String> {
    Err(
        "Writing the ActiveRuntime selector requires Windows registry access and fails closed \
         on this platform."
            .to_string(),
    )
}

// ---------------------------------------------------------------------------
// Install-time native ownership claim (chain G)
// ---------------------------------------------------------------------------

/// The EXACT `ActiveRuntime` text the native runtime's own guard accepts.
///
/// `civiccast.native.win_probes.read_selector` returns
/// `SelectorRead(ok=True, value="native")` only for a `REG_SZ` value named
/// `ActiveRuntime` under `SOFTWARE\CivicCast`, read through the 64-bit view
/// (`KEY_WOW64_64KEY`), whose text is exactly `native`. Anything else -- a
/// different type, a different spelling, the WOW6432Node shadow -- comes back
/// `ok=False` and `civiccast.native.runtime_guard.decide`'s step 2 blocks the
/// start. Stated here as a named constant so the write side and the tests
/// that pin the contract cannot drift apart.
pub const NATIVE_SELECTOR_VALUE: &str = "native";

/// What an install may do about `ActiveRuntime`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectorClaimAction {
    /// No selector exists and the WSL product is provably absent: this
    /// install is the machine's only CivicCast runtime, so it claims
    /// ownership by writing `"native"`.
    ClaimNative,
    /// The selector already says `"native"`; nothing to write.
    AlreadyNative,
    /// The WSL product owns the machine. An install NEVER steals that.
    LeaveWslOwnership,
    /// No selector, and another CivicCast product IS registered on this
    /// machine (a real `Present`, not an inert leftover). The install has no
    /// authority to decide which of two installed products transmits; the
    /// remedy is to uninstall that product or run the cutover from it. The
    /// selector is left exactly as found.
    LeaveOtherProductPresent,
    /// No selector, and the evidence is inconclusive: some read failed for a
    /// reason other than not-found (its error kind and code are in the
    /// observation). The selector is left exactly as found.
    LeaveUnprovable,
    /// The selector exists but could not be read as `native`/`wsl`. Left
    /// exactly as found; no evidence is gathered.
    LeaveUnreadable,
}

impl SelectorClaimAction {
    /// The three refusals -- every action that neither wrote nor found the
    /// selector already settled.
    pub fn is_refusal(self) -> bool {
        matches!(
            self,
            Self::LeaveOtherProductPresent | Self::LeaveUnprovable | Self::LeaveUnreadable
        )
    }
}

/// Chain G's whole decision, pure and total over `SelectorState` x
/// `OtherProductState`.
///
/// ## The defect this closes
///
/// `ActiveRuntime` is the authority basis the LocalSystem supervisor's
/// dual-runtime guard starts the control plane on
/// (`civiccast/native/runtime_guard.py::decide`). Before this, the ONLY
/// writers were the WSL lane (`cutover-to-wsl`), the operator-run
/// `civiccast-runtime cutover-to-native` verb, D5 repair, and uninstall
/// bookkeeping -- a plain native install wrote it NEVER. So a freshly
/// installed native station had `selector=absent`, and `decide`'s step 4 then
/// depends entirely on `detect_wsl_install()`, which is a deliberate
/// TRI-STATE: on a machine where `wsl.exe` is the OS inbox stub and the
/// SCM WSL-service query is ambiguous it answers `None` (unknown), and the
/// guard correctly refuses to invent an authority basis --
/// `blocked_probe_unavailable`, control plane never starts. That fail-closed
/// behavior is CORRECT and is unchanged here. What was missing is the other
/// half: a native install must produce the selector it is asking the guard to
/// honor.
///
/// ## Why the table looks like `decide_selector_repair_action` but is not it
///
/// Repair (`native_repair::decide_selector_repair_action`) treats `Absent` as
/// a legitimate settled state and never touches it, because repair has no new
/// information -- it is only cleaning up corruption. An INSTALL does have new
/// information: the native product is being installed, elevated, right now.
/// That is a genuine ownership event, so `Absent` is the one cell an install
/// may legitimately write -- and only when `probe_wsl_arp` independently
/// proves the CivicCast WSL product is not registered, so the write cannot
/// silently take a machine away from a WSL install that was there first.
/// Every other cell is left exactly as found:
///
/// | selector     | WSL product    | action                     |
/// |--------------|----------------|----------------------------|
/// | `Native`     | any            | `AlreadyNative`            |
/// | `Wsl`        | any            | `LeaveWslOwnership`        |
/// | `Absent`     | `Absent`       | `ClaimNative`              |
/// | `Absent`     | `PresentInert` | `ClaimNative` (+ warning)  |
/// | `Absent`     | `Present`      | `LeaveOtherProductPresent` |
/// | `Absent`     | `Unknown`      | `LeaveUnprovable`          |
/// | `Unreadable` | any            | `LeaveUnreadable`          |
///
/// The `WSL product` column is the [`WslProductVerdict`]
/// ([`classify_wsl_product_for_claim`] over
/// [`corroborate_wsl_product_state`]): since beta.5.1 a per-user ARP probe
/// that could not read some hive no longer decides the row on its own --
/// machine-wide ARP, the Lxss distro registration scan and the WSL service
/// presence are consulted before the row settles on `Unknown` -- and a
/// `Present` that is only an inert ARP leftover of a product this machine's
/// previous native install once handed off to is claimable with a warning.
/// The three refusals are distinct actions because their operator remedies
/// share nothing: uninstall the other product (or cutover from it), fix the
/// read that failed, or repair the selector value.
pub fn decide_install_selector_claim(
    selector: SelectorState,
    other_product: WslProductVerdict,
) -> SelectorClaimAction {
    match selector {
        SelectorState::Native => SelectorClaimAction::AlreadyNative,
        SelectorState::Wsl => SelectorClaimAction::LeaveWslOwnership,
        SelectorState::Unreadable => SelectorClaimAction::LeaveUnreadable,
        SelectorState::Absent => match other_product {
            WslProductVerdict::Absent | WslProductVerdict::PresentInert => {
                SelectorClaimAction::ClaimNative
            }
            WslProductVerdict::Present => SelectorClaimAction::LeaveOtherProductPresent,
            WslProductVerdict::Unknown => SelectorClaimAction::LeaveUnprovable,
        },
    }
}

/// The result of one install-time claim attempt.
///
/// `write_error` is `Some` ONLY when [`SelectorClaimAction::ClaimNative`] was
/// decided and the registry write (or its read-back verification) failed --
/// never for a deliberate non-write, which is a legitimate outcome rather
/// than a fault.
#[derive(Debug, Clone)]
pub struct SelectorClaimOutcome {
    pub action: SelectorClaimAction,
    /// What the selector read as.
    pub selector: SelectorState,
    /// The WSL-product evidence that was gathered -- `Some` exactly when the
    /// selector was `Absent` (the one cell where evidence can change the
    /// answer), `None` otherwise. Carries every per-hive observation.
    pub evidence: Option<WslPresenceEvidence>,
    /// [`classify_wsl_product_for_claim`] over `evidence`; `Some` exactly
    /// when `evidence` is. `PresentInert` with `ClaimNative` is the
    /// inert-leftover claim, whose warning the log must carry.
    pub verdict: Option<WslProductVerdict>,
    /// One operator-readable sentence naming what was observed and what was
    /// done about it. Printed into the install log on EVERY path, including
    /// the ones that write nothing: a station whose selector was left alone
    /// will not start, and this line is the only place that says why. On
    /// the paths that consulted evidence it embeds
    /// [`WslPresenceEvidence::explain`] -- the hive/SID, WOW64 view, error
    /// kind and raw error code of every observation that was not a clean
    /// absence.
    pub detail: String,
    pub write_error: Option<String>,
}

/// [`decide_install_selector_claim`] plus the probe/write I/O, with all three
/// sides injected so the orchestration itself is unit-testable without
/// touching a real registry (this module's HARD RULE).
pub fn claim_install_selector_with(
    probe_selector: impl Fn() -> SelectorState,
    probe_wsl_presence: impl Fn() -> WslPresenceEvidence,
    write_native: impl Fn() -> Result<(), String>,
) -> SelectorClaimOutcome {
    let selector = probe_selector();
    // Only consulted in the ONE cell where it can change the answer, so a
    // slow/ambiguous ARP enumeration cannot affect an install whose selector
    // already settles the question.
    let evidence = if selector == SelectorState::Absent {
        Some(probe_wsl_presence())
    } else {
        None
    };
    let verdict = evidence.as_ref().map(classify_wsl_product_for_claim);
    let other_product = verdict.unwrap_or(WslProductVerdict::Unknown);
    let explained = evidence
        .as_ref()
        .map(WslPresenceEvidence::explain)
        .unwrap_or_else(|| "not consulted".to_string());
    let action = decide_install_selector_claim(selector, other_product);
    let (detail, write_error) = match action {
        SelectorClaimAction::ClaimNative => {
            // The inert-leftover warning is the FIRST thing in the sentence:
            // it survives the observation line's cap and is what the log
            // must carry (the field report's box would otherwise read as a
            // clean claim).
            let basis = if other_product == WslProductVerdict::PresentInert {
                inert_leftover_warning(evidence.as_ref())
            } else {
                "ActiveRuntime was absent and the CivicCast WSL product is not on this machine"
                    .to_string()
            };
            match write_native() {
                Ok(()) => (
                    format!(
                        "{basis} ({explained}); this install claimed native ownership \
                         (ActiveRuntime = \"native\", read-back verified).{}",
                        inert_leftover_removal_hint(other_product, evidence.as_ref())
                    ),
                    None,
                ),
                Err(error) => (
                    format!(
                        "{basis} ({explained}), but claiming native ownership failed: {error}"
                    ),
                    Some(error),
                ),
            }
        }
        SelectorClaimAction::AlreadyNative => (
            "ActiveRuntime already reads \"native\"; this install left it unchanged.".to_string(),
            None,
        ),
        SelectorClaimAction::LeaveWslOwnership => (
            "ActiveRuntime reads \"wsl\", so the WSL product owns this machine; this install \
             left it unchanged. The native runtime will not start until an operator runs \
             `civiccast-runtime cutover-to-native`."
                .to_string(),
            None,
        ),
        SelectorClaimAction::LeaveOtherProductPresent => {
            let found = evidence
                .as_ref()
                .map(WslPresenceEvidence::describe_present_product)
                .unwrap_or_else(|| {
                    "Setup found another CivicCast product installed on this machine".to_string()
                });
            (
                format!(
                    "{found}. ActiveRuntime was left unchanged: setup never takes a machine \
                     away from a CivicCast product that was there first. {} Evidence: \
                     {explained}.",
                    other_product_present_remedy(evidence.as_ref())
                ),
                None,
            )
        }
        SelectorClaimAction::LeaveUnprovable => (
            format!(
                "ActiveRuntime was left unchanged: observed selector {selector:?}; WSL product \
                 evidence: {explained}; combined WSL product state {}. A read failed for a \
                 reason other than \"not found\" (its error kind and OS error code are beside \
                 it), so setup cannot establish that this machine's runtime ownership is the \
                 native product's to claim. The native runtime will not start until an \
                 operator sets HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime.",
                other_product.token()
            ),
            None,
        ),
        SelectorClaimAction::LeaveUnreadable => (
            "HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime exists but could not be read as \
             \"native\" or \"wsl\" (observed selector Unreadable; WSL product evidence: not \
             consulted); this install left it unchanged. The native runtime will not start \
             until an administrator corrects that value."
                .to_string(),
            None,
        ),
    };
    SelectorClaimOutcome {
        action,
        selector,
        evidence,
        verdict,
        detail,
        write_error,
    }
}

/// The inert-leftover warning, leading with WARNING so it survives the
/// observation line's cap and cannot be read as a clean claim.
fn inert_leftover_warning(evidence: Option<&WslPresenceEvidence>) -> String {
    let (title, user) = evidence
        .and_then(WslPresenceEvidence::present_product)
        .map(|(_, product)| (product.title(), product.user.clone()))
        .unwrap_or_else(|| ("CivicCast Installer".to_string(), "(unknown)".to_string()));
    format!(
        "WARNING: an old {title} registration remains for user {user} with no CivicCast distro \
         or autostart entry, and a previous native install had already handed this machine \
         off (NativeUninstallTransferCompleted is set); ActiveRuntime was absent"
    )
}

/// `Remove the leftover via Apps & Features (uninstall.exe at <path>).` --
/// appended to a successful inert-leftover claim; empty for a clean claim.
fn inert_leftover_removal_hint(
    verdict: WslProductVerdict,
    evidence: Option<&WslPresenceEvidence>,
) -> String {
    if verdict != WslProductVerdict::PresentInert {
        return String::new();
    }
    let at = evidence
        .and_then(WslPresenceEvidence::present_product)
        .and_then(|(_, product)| product.uninstall_string.clone())
        .filter(|s| !s.trim().is_empty())
        .map(|path| format!(" (uninstall.exe at {path})"))
        .unwrap_or_default();
    format!(" Remove the leftover via Apps & Features{at}.")
}

/// The remedy sentence for a real `Present`: uninstall that product, or
/// cut over from it. NO registry-edit instruction -- the value is not the
/// problem, the other product is.
fn other_product_present_remedy(evidence: Option<&WslPresenceEvidence>) -> String {
    let product = evidence.and_then(WslPresenceEvidence::present_product);
    let title = product
        .map(|(_, p)| p.title())
        .unwrap_or_else(|| "CivicCast Installer".to_string());
    let or_run = product
        .and_then(|(_, p)| p.uninstall_string.clone())
        .filter(|s| !s.trim().is_empty())
        .map(|command| format!(" (or run {command})"))
        .unwrap_or_default();
    format!(
        "Uninstall '{title}' from Settings > Apps{or_run}, or run `civiccast-runtime \
         cutover-to-native`, then run setup again."
    )
}

/// Production wiring of [`claim_install_selector_with`]: the real registry
/// selector probe, the real WSL-presence evidence gathering
/// ([`probe_wsl_presence_evidence`]), and the real write + read-back-verify.
#[cfg(target_os = "windows")]
pub fn claim_install_selector() -> SelectorClaimOutcome {
    claim_install_selector_with(
        probe_active_runtime_selector,
        probe_wsl_presence_evidence,
        write_selector_native,
    )
}

/// The acknowledged-transfer write: flips `ActiveRuntime` from `"native"` to
/// `"wsl"` using the SAME write + read-back-verify convention as
/// [`write_postclear_marker`] / [`repair_write_selector_native`] above, with
/// one addition the transfer's transactional contract requires: on a FAILED
/// write attempt (the `set_value` call itself erroring), this also re-reads
/// the key and confirms the pre-transfer value is still there, rather than
/// merely assuming a single `RegSetValueEx` call for one string value is
/// atomic. Callers (`native_uninstall_preflight`) treat any `Err` from this
/// function as "abort the uninstall before any removal step runs"; the
/// caller-facing contract is: `Ok(())` means `ActiveRuntime` is verified
/// `"wsl"`, `Err(_)` means `ActiveRuntime` is verified (or, in the rare case
/// the confirmation re-read itself fails, explicitly flagged as
/// indeterminate and named for manual inspection) still `"native"`.
#[cfg(target_os = "windows")]
pub(crate) fn transfer_active_runtime_to_wsl() -> Result<(), String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let key = hklm
        .create_subkey_with_flags(SELECTOR_KEY, KEY_READ | KEY_SET_VALUE | KEY_WOW64_64KEY)
        .map_err(|error| {
            format!(
                "Could not open the native lifecycle key to transfer ActiveRuntime; the \
                 original value was not touched: {error}"
            )
        })?
        .0;

    let original: Option<String> = key.get_value(SELECTOR_VALUE).ok();

    if let Err(error) = key.set_value(SELECTOR_VALUE, &"wsl") {
        return Err(match &original {
            Some(value) => match key.get_value::<String, _>(SELECTOR_VALUE) {
                Ok(reread) if &reread == value => format!(
                    "Could not write ActiveRuntime during transfer (write call failed): \
                     {error}. Verified ActiveRuntime is still {value:?} (original state intact)."
                ),
                Ok(reread) => format!(
                    "Could not write ActiveRuntime during transfer (write call failed): \
                     {error}. ActiveRuntime is now {reread:?}, which does NOT match the \
                     pre-transfer value {value:?} -- state is INDETERMINATE and needs manual \
                     inspection of HKLM\\Software\\CivicCast\\ActiveRuntime."
                ),
                Err(reread_error) => format!(
                    "Could not write ActiveRuntime during transfer (write call failed): \
                     {error}. Could not re-read ActiveRuntime afterward to confirm the \
                     original value {value:?} is intact ({reread_error}); state is \
                     INDETERMINATE and needs manual inspection of \
                     HKLM\\Software\\CivicCast\\ActiveRuntime."
                ),
            },
            None => format!(
                "Could not write ActiveRuntime during transfer (write call failed): {error}. \
                 The pre-transfer value could not be read either, so original-state \
                 intactness cannot be confirmed; inspect \
                 HKLM\\Software\\CivicCast\\ActiveRuntime manually."
            ),
        });
    }

    let persisted: String = key
        .get_value(SELECTOR_VALUE)
        .map_err(|error| format!("Could not verify ActiveRuntime after transfer write: {error}"))?;
    if persisted != "wsl" {
        return Err(format!(
            "ActiveRuntime transfer verification mismatch: wrote \"wsl\" but the read-back \
             returned {persisted:?}."
        ));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn transfer_active_runtime_to_wsl() -> Result<(), String> {
    Err(
        "ActiveRuntime transfer requires Windows registry access and fails closed on this \
         platform."
            .to_string(),
    )
}

/// Write + read-back-verify [`TRANSFER_MARKER`] (see its doc comment); called
/// only immediately after [`transfer_active_runtime_to_wsl`] has itself
/// verified the selector write. A failure here does NOT undo the transfer
/// (the selector legitimately stays `"wsl"` -- see the module-level "on any
/// post-transfer uninstall failure" note in [`native_uninstall_preflight`]);
/// it is surfaced as an error so that specific preflight call aborts before
/// any removal step runs, and a retry is safe (the next call observes
/// `ActiveRuntime = Wsl`, which `decide` resolves to `AllowInactive` with no
/// further mutation, needing no re-acknowledgment).
#[cfg(target_os = "windows")]
fn write_transfer_marker() -> Result<(), String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let (key, _) = hklm
        .create_subkey_with_flags(SELECTOR_KEY, KEY_READ | KEY_SET_VALUE | KEY_WOW64_64KEY)
        .map_err(|error| {
            format!(
                "Could not create the native lifecycle key to record the transfer marker: {error}"
            )
        })?;
    key.set_value(TRANSFER_MARKER, &TRANSFER_MARKER_VALUE)
        .map_err(|error| format!("Could not persist the native transfer marker: {error}"))?;
    let persisted: String = key
        .get_value(TRANSFER_MARKER)
        .map_err(|error| format!("Could not verify the native transfer marker: {error}"))?;
    if persisted != TRANSFER_MARKER_VALUE {
        return Err(
            "Native transfer marker verification returned an unexpected value.".to_string(),
        );
    }
    Ok(())
}

/// The WSL-product state the uninstall preflight plans with. A raw `Present`
/// from the per-user ARP probe is run through the SAME inert-leftover rule the
/// install-time claim uses ([`classify_wsl_product_for_claim`]): an old ARP
/// registration with no CivicCast distro, no autostart entry and a completed
/// earlier native hand-off cannot start or transmit, so there is no product
/// to transfer ownership to. It is planned as `Absent` and reported in the
/// returned warning. Anything short of all three confident conditions stays
/// `Present` (transfer still required); `Absent`/`Unknown` pass through and
/// gather no further evidence.
///
/// RC4 host test 2026-11-04: RC3's uninstall demanded a transfer to
/// "CivicCast Installer" 3.0.0-beta1 whose InstallLocation and uninstall.exe
/// no longer existed; accepting would have handed ActiveRuntime to nothing.
pub fn uninstall_effective_wsl_state(
    wsl_arp: OtherProductState,
    gather_evidence: impl FnOnce() -> WslPresenceEvidence,
) -> (OtherProductState, Option<String>) {
    if wsl_arp != OtherProductState::Present {
        return (wsl_arp, None);
    }
    let evidence = gather_evidence();
    if classify_wsl_product_for_claim(&evidence) != WslProductVerdict::PresentInert {
        return (OtherProductState::Present, None);
    }
    let (title, user) = evidence
        .present_product()
        .map(|(_, product)| (product.title(), product.user.clone()))
        .unwrap_or_else(|| ("CivicCast Installer".to_string(), "(unknown)".to_string()));
    (
        OtherProductState::Absent,
        Some(format!(
            "WARNING: an old {title} registration remains for user {user} with no CivicCast \
             distro or autostart entry, and a previous native install had already handed this \
             machine off (NativeUninstallTransferCompleted is set); it cannot start or transmit, \
             so this uninstall proceeds without an ownership transfer. Evidence: {}.",
            evidence.explain()
        )),
    )
}

/// Probe the authoritative Windows state and arm POSTUNINSTALL only for the
/// active-Native, no-WSL-survivor case (`AllowSolePostclear`).
///
/// When Native is active AND the WSL product remains installed (D1's
/// transfer-required case, [`transfer_would_unblock`]):
/// * `acknowledge_transfer == false` returns `TransferAcknowledgmentRequired`
///   without reading or writing anything beyond the two read-only probes
///   below -- an unacknowledged/declined/cancelled call is a true no-op (D1's
///   "transfer refused/cancelled: NOTHING removed, both products intact").
/// * `acknowledge_transfer == true` performs the write+read-back-verified
///   `ActiveRuntime` transfer to `"wsl"` ([`transfer_active_runtime_to_wsl`])
///   BEFORE removal proceeds, matching D1's "authorized transaction... before
///   removal proceeds." A transfer-write failure aborts with `Err` and the
///   original state verified intact (see that function's doc comment); a
///   verified transfer records [`TRANSFER_MARKER`]
///   ([`write_transfer_marker`]) for the proof matrix. If marker-recording
///   itself fails, this call returns `Err` (so NSIS aborts before any
///   removal step runs) -- but the selector STAYS `"wsl"`: WSL genuinely now
///   owns activation, which is correct, not a rollback case, and a retry
///   needs no re-acknowledgment (the next call observes
///   `ActiveRuntime = Wsl` for `product = Native`, which `decide` already
///   resolves to `AllowInactive` with zero further mutation).
#[cfg(target_os = "windows")]
pub fn native_uninstall_preflight(acknowledge_transfer: bool) -> Result<PreflightOutcome, String> {
    clear_postclear_marker()?;
    let selector = probe_active_runtime_selector();
    let (wsl_arp, inert_leftover) =
        uninstall_effective_wsl_state(probe_wsl_arp(), probe_wsl_presence_evidence);
    if let Some(warning) = inert_leftover {
        // Captured by the NSIS preflight call and written to the progress log.
        eprintln!("{warning}");
    }
    let needs_transfer = transfer_would_unblock(Product::Native, selector, wsl_arp);

    if needs_transfer && !acknowledge_transfer {
        return Ok(PreflightOutcome::TransferAcknowledgmentRequired);
    }

    let plan = if needs_transfer {
        // acknowledge_transfer == true here (the branch above returned
        // otherwise).
        match transfer_active_runtime_to_wsl() {
            Ok(()) => {
                write_transfer_marker()?;
                decide(
                    Product::Native,
                    selector,
                    wsl_arp,
                    TransferState::AcceptedAndVerified,
                )
            }
            Err(error) => {
                return Err(format!(
                    "CivicCast (Native) uninstall ownership transfer FAILED: {error} The \
                     uninstall was aborted before any removal step ran; nothing was removed."
                ));
            }
        }
    } else {
        decide(
            Product::Native,
            selector,
            wsl_arp,
            TransferState::NotRequested,
        )
    };

    if plan.decision == Decision::Block {
        return Err(format!(
            "CivicCast (Native) uninstall is blocked: ActiveRuntime={selector:?}, WSL ARP={wsl_arp:?}."
        ));
    }
    if plan.decision == Decision::AllowSolePostclear {
        write_postclear_marker()?;
    }
    Ok(PreflightOutcome::Allowed(plan))
}

#[cfg(not(target_os = "windows"))]
pub fn native_uninstall_preflight(_acknowledge_transfer: bool) -> Result<PreflightOutcome, String> {
    Err("Native uninstall preflight requires Windows registry access and fails closed on this platform.".to_string())
}

// ---------------------------------------------------------------------------
// D4 bidirectional state inventory
// ---------------------------------------------------------------------------
//
// `spec-installer-lifecycle.md` D4: "Exact state inventory (files, registry
// keys, service, firewall rules) is enumerated in the spec's implementation
// and asserted by the proofs -- 'everything gone' means that inventory,
// bidirectionally." This table is the ONE source of truth for every
// machine-scoped state item `native_service_registration.rs`'s
// `NSIS_HOOK_POSTINSTALL` wiring establishes, paired with the POSTUNINSTALL
// step that must remove it. `nsis-hooks-native.nsh`'s `NSIS_HOOK_POSTUNINSTALL`
// macro is still empty (a later work package, not this one); when that work
// lands it MUST consume this table rather than re-deriving its own list, so
// install-creates and uninstall-removes can never drift apart -- extending
// the same file/constants this module already tracks for the ownership
// decision (`SELECTOR_KEY`, `WSL_ARP_KEY`, `POSTCLEAR_MARKER`) rather than
// forking a second inventory concept elsewhere.

// Not consumed by any live code path yet -- the POSTUNINSTALL teardown that
// will read this table is a later work package (see the doc comment above).
// Exempted from the unused-item lint rather than deleted, the same
// documentation/grep-seam convention `WSL_ARP_PROBE_LOADED_HIVES_ONLY` above
// already uses; fully exercised by `state_inventory_tests` below in the
// meantime.
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateItemKind {
    Service,
    RegistryValue,
    FirewallRule,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Copy)]
pub struct StateInventoryItem {
    pub kind: StateItemKind,
    pub identifier: &'static str,
    pub established_by: &'static str,
    pub removed_by: &'static str,
}

#[allow(dead_code)]
pub const NATIVE_D4_STATE_INVENTORY: &[StateInventoryItem] = &[
    StateInventoryItem {
        kind: StateItemKind::Service,
        identifier: crate::native_service_registration::SERVICE_NAME,
        established_by: "python.exe -m civiccast.native.supervisor.service_host install \
            --startup auto (NSIS_HOOK_POSTINSTALL)",
        removed_by: "native_service_registration::stop_native_service (sc.exe stop, polled to \
            STOPPED) then native_service_registration::teardown_native_state's \
            \"remove service\" step (python.exe -m civiccast.native.supervisor.service_host \
            remove), driven by --civiccast-teardown-native-state \
            (NSIS_HOOK_POSTUNINSTALL, start of the macro)",
    },
    StateInventoryItem {
        kind: StateItemKind::RegistryValue,
        identifier: r"HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl",
        established_by: "winreg set_value, read-back verified (native_service_registration::\
            write_database_url -- built and tested, NOT yet wired into NSIS_HOOK_POSTINSTALL; \
            see wp2-d4-service-registration evidence STOP section)",
        removed_by: "native_service_registration::teardown_native_state's \
            \"clear credentials\" step (delete_native_credential_values), \
            driven by --civiccast-teardown-native-state \
            (NSIS_HOOK_PREUNINSTALL, which is where the teardown CLI actually \
            runs; its result is carried into NSIS_HOOK_POSTUNINSTALL). \
            SECURITY FIX F-02 (2026-08-01 sandbox newcomer re-walk), \
            REVERSING the 2026-07-30 coordinator decision that preserved this \
            value: the re-walk read this live PostgreSQL password verbatim out \
            of the registry of a machine the product had already been \
            uninstalled from. Preserving product DATA under \
            %PROGRAMDATA%\\CivicCast was never a decision to leave a live \
            SECRET behind -- data is preserved; credentials are not. \
            DISCLOSED CONSEQUENCE, deliberately not hidden: the preserved \
            PostgreSQL cluster can no longer be reused by a reinstall, because \
            the password cannot be reconstructed from anything on disk. \
            civiccast.native.provision's decision matrix classifies that as \
            FAIL_LOUD_MISSING_REGISTRY and refuses rather than regenerating a \
            password the cluster would reject -- correct fail-closed behavior, \
            but it means uninstall-then-reinstall over preserved data needs \
            operator recovery. Re-establishing a credential for a surviving, \
            product-owned cluster is a separate, still-open unit of work",
    },
    StateInventoryItem {
        kind: StateItemKind::FirewallRule,
        identifier: crate::native_service_registration::FIREWALL_RULE_NAME,
        established_by: "netsh advfirewall firewall add rule (NSIS_HOOK_POSTINSTALL)",
        removed_by: "native_service_registration::delete_native_firewall_rule (probe then \
            netsh advfirewall firewall delete rule, idempotent), driven by \
            --civiccast-teardown-native-state (NSIS_HOOK_POSTUNINSTALL, start of the macro)",
    },
    StateInventoryItem {
        kind: StateItemKind::RegistryValue,
        identifier: r"HKLM\SOFTWARE\CivicCast\Native\InstalledVersion",
        established_by: "WriteRegStr at the fully-successful end of \
            NSIS_HOOK_POSTINSTALL (the D3 fresh-install gate's prior-version \
            signal -- ARP DisplayVersion is unusable because Tauri writes it \
            before the hook runs; Sandbox matrix row 1, 2026-07-30)",
        removed_by: "native_service_registration::teardown_native_state's \
            \"clear install markers\" step \
            (delete_native_install_marker_values), driven by \
            --civiccast-teardown-native-state (NSIS_HOOK_PREUNINSTALL; result \
            carried into NSIS_HOOK_POSTUNINSTALL). F-01 uninstaller half \
            (2026-08-01 sandbox newcomer re-walk), REVERSING the 2026-07-30 \
            coordinator decision that preserved this value so a reinstall \
            would be treated as an upgrade over surviving data. The re-walk is \
            the counter-evidence: the uninstaller reported \"Uninstall was \
            completed successfully\", left InstalledVersion=1.0.0-rc15, and \
            the next install read it, did not fire the D3 fresh-install gate, \
            logged \"step d3-engine: begin (old=1.0.0-rc15)\" against a \
            product that was not installed, and rolled back. On a machine with \
            no product installed that treatment is not an upgrade, it is a \
            false one. The ROUTING side (how much the D3 gate may trust this \
            marker, and what it does when its two signals disagree) is a \
            separate unit of work; this entry is only the uninstaller's \
            obligation",
    },
];

#[cfg(test)]
mod state_inventory_tests {
    use super::*;

    #[test]
    fn inventory_has_exactly_the_four_d4_items_service_two_registry_firewall() {
        assert_eq!(NATIVE_D4_STATE_INVENTORY.len(), 4);
        assert_eq!(
            NATIVE_D4_STATE_INVENTORY
                .iter()
                .filter(|item| item.kind == StateItemKind::RegistryValue)
                .count(),
            2,
            "DatabaseUrl and InstalledVersion are the tracked registry values -- the \
             installer-handoff SetupNonce this inventory used to also track was retired \
             along with the rest of the nonce/handoff mechanism"
        );
        assert!(NATIVE_D4_STATE_INVENTORY
            .iter()
            .any(|item| item.kind == StateItemKind::Service));
        assert!(NATIVE_D4_STATE_INVENTORY
            .iter()
            .any(|item| item.kind == StateItemKind::RegistryValue));
        assert!(NATIVE_D4_STATE_INVENTORY
            .iter()
            .any(|item| item.kind == StateItemKind::FirewallRule));
    }

    #[test]
    fn every_item_has_a_non_empty_identifier_and_no_duplicates() {
        let identifiers: Vec<&str> = NATIVE_D4_STATE_INVENTORY
            .iter()
            .map(|item| item.identifier)
            .collect();
        for identifier in &identifiers {
            assert!(!identifier.trim().is_empty());
        }
        let mut sorted = identifiers.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(
            sorted.len(),
            identifiers.len(),
            "duplicate identifier in the D4 state inventory: {identifiers:?}"
        );
    }

    /// Bidirectionality: every item this table says is established somewhere
    /// also names where it is removed -- a blank `removed_by` would mean the
    /// table itself has drifted into a one-way (install-only) list, which is
    /// exactly the drift this table exists to prevent.
    #[test]
    fn every_item_names_both_an_establishing_and_a_removing_step() {
        for item in NATIVE_D4_STATE_INVENTORY {
            assert!(
                item.established_by.contains("POSTINSTALL"),
                "{:?} must name its POSTINSTALL establishing step",
                item.identifier
            );
            // Every item must name a REAL fate, and there are exactly two
            // truthful ones: it is torn down by POSTUNINSTALL, or it is
            // deliberately preserved and names what WILL remove it (the
            // typed PURGE action). "Names a keyword" is not a fate -- the
            // previous version of this assertion passed happily on the
            // literal text "POSTUNINSTALL -- not yet implemented" while
            // uninstall removed nothing at all, which is precisely how the
            // teardown BLOCKER survived undetected.
            let torn_down = item.removed_by.contains("POSTUNINSTALL");
            let deliberately_preserved = item.removed_by.contains("NOT removed by uninstall")
                && item.removed_by.contains("PURGE");
            assert!(
                torn_down || deliberately_preserved,
                "{:?} must either name its POSTUNINSTALL teardown or state it is \
                 deliberately preserved AND name the PURGE action that removes it; \
                 got: {:?}",
                item.identifier,
                item.removed_by
            );
            // The BLOCKER fix this table now documents: every removal is a REAL
            // wired mechanism (native_service_registration::teardown_native_state
            // and its steps, driven by --civiccast-teardown-native-state), never
            // the "not yet implemented" placeholder this table carried before.
            assert!(
                !item.removed_by.contains("not yet implemented"),
                "{:?}'s removal is no longer unimplemented -- update this string if it \
                 regresses",
                item.identifier
            );
        }
    }

    /// F-02 (sandbox newcomer re-walk `dd7f835f`, 2026-08-01): the live
    /// PostgreSQL password was read verbatim out of
    /// `HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl` on a machine where the
    /// product had already been uninstalled through its own uninstaller.
    /// "Uninstall deliberately preserves product data" is a decision about
    /// DATA -- it was never a decision to leave live secrets on a machine the
    /// product no longer occupies.
    ///
    /// This is the bidirectional inventory's own statement of that rule: every
    /// credential-bearing value under `CivicCast\Native` names an ordinary
    /// POSTUNINSTALL teardown step as its remover, and none of them may claim
    /// the "deliberately preserved until a future PURGE" fate that
    /// `every_item_names_both_an_establishing_and_a_removing_step` above
    /// otherwise permits.
    ///
    /// RETIRED: the installer-handoff `SetupNonce` used to be tracked here
    /// too, as a second credential beside `DatabaseUrl`. The nonce/handoff
    /// mechanism was retired in favor of the control plane admitting first
    /// setup purely by checking the request's peer IP is loopback, so there
    /// is no longer a second credential-shaped value under this key.
    #[test]
    fn every_credential_bearing_value_is_removed_by_an_ordinary_uninstall() {
        for identifier in [r"HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl"] {
            let item = NATIVE_D4_STATE_INVENTORY
                .iter()
                .find(|item| item.identifier == identifier)
                .unwrap_or_else(|| {
                    panic!(
                        "{identifier} is a credential this installer writes, so the D4 state \
                         inventory must track it and name the uninstall step that removes it"
                    )
                });
            assert!(
                !item.removed_by.contains("NOT removed by uninstall"),
                "{identifier} is a LIVE CREDENTIAL; an ordinary uninstall must delete it, not \
                 defer it to a future PURGE action. Got: {:?}",
                item.removed_by
            );
            assert!(
                item.removed_by.contains("clear credentials"),
                "{identifier} must name the teardown_native_state \"clear credentials\" step \
                 that actually removes it. Got: {:?}",
                item.removed_by
            );
        }
    }

    /// F-01, uninstaller half (sandbox newcomer re-walk `dd7f835f`,
    /// 2026-08-01): the uninstaller completed and reported success, leaving
    /// `InstalledVersion=1.0.0-rc15` behind. The very next install read it,
    /// classified a clean install as an upgrade, ran the D3 engine against a
    /// product that was not there, and rolled back. Uninstall -> reinstall was
    /// broken by state the uninstaller itself left.
    ///
    /// The ROUTING side of that (what the D3 gate does with a version marker
    /// it should not trust) is chain K's. This is the uninstaller side, stated
    /// as a rule: nothing that CLAIMS a product is installed may survive a
    /// completed uninstall.
    #[test]
    fn a_completed_uninstall_leaves_nothing_claiming_a_product_is_installed() {
        let item = NATIVE_D4_STATE_INVENTORY
            .iter()
            .find(|item| item.identifier == r"HKLM\SOFTWARE\CivicCast\Native\InstalledVersion")
            .expect("InstalledVersion must be tracked in the D4 state inventory");
        assert!(
            !item.removed_by.contains("NOT removed by uninstall"),
            "InstalledVersion is the D3 gate's 'a product is installed, and it is THIS \
             version' signal. A completed uninstall that leaves it behind makes the next \
             install misroute -- exactly what the re-walk reproduced. Got: {:?}",
            item.removed_by
        );
        assert!(
            item.removed_by.contains("clear install markers"),
            "InstalledVersion must name the teardown_native_state \"clear install markers\" \
             step that actually removes it. Got: {:?}",
            item.removed_by
        );
    }

    #[test]
    fn service_inventory_identifier_matches_the_registration_module_constant() {
        let service_item = NATIVE_D4_STATE_INVENTORY
            .iter()
            .find(|item| item.kind == StateItemKind::Service)
            .expect("a Service item must exist");
        assert_eq!(
            service_item.identifier,
            crate::native_service_registration::SERVICE_NAME
        );
    }

    #[test]
    fn firewall_inventory_identifier_matches_the_registration_module_constant() {
        let firewall_item = NATIVE_D4_STATE_INVENTORY
            .iter()
            .find(|item| item.kind == StateItemKind::FirewallRule)
            .expect("a FirewallRule item must exist");
        assert_eq!(
            firewall_item.identifier,
            crate::native_service_registration::FIREWALL_RULE_NAME
        );
    }

    #[test]
    fn registry_value_inventory_identifier_names_the_exact_hklm_path() {
        let registry_item = NATIVE_D4_STATE_INVENTORY
            .iter()
            .find(|item| item.kind == StateItemKind::RegistryValue)
            .expect("a RegistryValue item must exist");
        assert_eq!(
            registry_item.identifier,
            r"HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inactive_product_is_allowed_without_selector_mutation() {
        let plan = decide(
            Product::Native,
            SelectorState::Wsl,
            OtherProductState::Present,
            TransferState::NotRequested,
        );

        assert_eq!(plan.decision, Decision::AllowInactive);
        assert_eq!(plan.selector_mutation, SelectorMutation::None);
    }

    #[test]
    fn policy_table_covers_every_valid_input_combination_for_both_products() {
        let products = [Product::Native, Product::Wsl];
        let selectors = [
            SelectorState::Native,
            SelectorState::Wsl,
            SelectorState::Absent,
            SelectorState::Unreadable,
        ];
        let other_product_states = [
            OtherProductState::Present,
            OtherProductState::Absent,
            OtherProductState::Unknown,
        ];
        let transfers = [
            TransferState::NotRequested,
            TransferState::AcceptedAndVerified,
            TransferState::Refused,
            TransferState::Failed,
        ];

        for product in products {
            for selector in selectors {
                for other_product in other_product_states {
                    for transfer in transfers {
                        let plan = decide(product, selector, other_product, transfer);
                        let active = matches!(
                            (product, selector),
                            (Product::Native, SelectorState::Native)
                                | (Product::Wsl, SelectorState::Wsl)
                        );
                        let expected = match selector {
                            SelectorState::Unreadable => (Decision::Block, SelectorMutation::None),
                            SelectorState::Absent => {
                                (Decision::AllowNoOwner, SelectorMutation::None)
                            }
                            _ if !active => (Decision::AllowInactive, SelectorMutation::None),
                            _ if other_product == OtherProductState::Present
                                && transfer == TransferState::AcceptedAndVerified =>
                            {
                                (Decision::AllowAfterTransfer, SelectorMutation::None)
                            }
                            _ if other_product == OtherProductState::Absent => (
                                Decision::AllowSolePostclear,
                                SelectorMutation::ClearInPostUninstall,
                            ),
                            _ => (Decision::Block, SelectorMutation::None),
                        };

                        assert_eq!(
                            (plan.decision, plan.selector_mutation),
                            expected,
                            "product={product:?}, selector={selector:?}, other={other_product:?}, transfer={transfer:?}"
                        );
                    }
                }
            }
        }
    }

    // ---- transfer_would_unblock: pure, CLI/NSIS prompt-gating classifier ----

    #[test]
    fn transfer_would_unblock_true_only_for_active_product_with_wsl_survivor() {
        assert!(transfer_would_unblock(
            Product::Native,
            SelectorState::Native,
            OtherProductState::Present,
        ));
        assert!(transfer_would_unblock(
            Product::Wsl,
            SelectorState::Wsl,
            OtherProductState::Present,
        ));
    }

    #[test]
    fn transfer_would_unblock_false_when_no_other_product_present() {
        assert!(!transfer_would_unblock(
            Product::Native,
            SelectorState::Native,
            OtherProductState::Absent,
        ));
        assert!(!transfer_would_unblock(
            Product::Native,
            SelectorState::Native,
            OtherProductState::Unknown,
        ));
    }

    #[test]
    fn transfer_would_unblock_false_when_the_product_is_not_the_active_one() {
        // Uninstalling the INACTIVE product never needs a transfer -- D1's
        // "uninstalling the inactive product never touches the selector".
        assert!(!transfer_would_unblock(
            Product::Native,
            SelectorState::Wsl,
            OtherProductState::Present,
        ));
        assert!(!transfer_would_unblock(
            Product::Wsl,
            SelectorState::Native,
            OtherProductState::Present,
        ));
    }

    #[test]
    fn transfer_would_unblock_false_for_absent_or_unreadable_selector() {
        for selector in [SelectorState::Absent, SelectorState::Unreadable] {
            for other in [
                OtherProductState::Present,
                OtherProductState::Absent,
                OtherProductState::Unknown,
            ] {
                assert!(
                    !transfer_would_unblock(Product::Native, selector, other),
                    "selector={selector:?}, other={other:?}"
                );
            }
        }
    }

    #[test]
    fn transfer_would_unblock_agrees_with_decide_across_the_full_input_space() {
        // Cross-check against the exhaustive `decide` table above: every case
        // this function reports `true` for must be a case where
        // `TransferState::AcceptedAndVerified` (instead of `NotRequested`)
        // would flip `decide`'s outcome from `Block` to `AllowAfterTransfer`,
        // and every case it reports `false` for must NOT flip that way.
        let products = [Product::Native, Product::Wsl];
        let selectors = [
            SelectorState::Native,
            SelectorState::Wsl,
            SelectorState::Absent,
            SelectorState::Unreadable,
        ];
        let others = [
            OtherProductState::Present,
            OtherProductState::Absent,
            OtherProductState::Unknown,
        ];
        for product in products {
            for selector in selectors {
                for other in others {
                    let not_requested =
                        decide(product, selector, other, TransferState::NotRequested);
                    let accepted =
                        decide(product, selector, other, TransferState::AcceptedAndVerified);
                    let acknowledgment_would_flip_block_to_allow = not_requested.decision
                        == Decision::Block
                        && accepted.decision == Decision::AllowAfterTransfer;
                    assert_eq!(
                        transfer_would_unblock(product, selector, other),
                        acknowledgment_would_flip_block_to_allow,
                        "product={product:?}, selector={selector:?}, other={other:?}"
                    );
                }
            }
        }
    }

    // ---- exit code constants stay distinct ----

    #[test]
    fn transfer_ack_required_exit_code_is_distinct_from_sole_postclear_and_common_cli_codes() {
        let reserved = [SOLE_POSTCLEAR_EXIT_CODE, 0, 64, 72, 75, 76, 77, 79];
        assert!(!reserved.contains(&TRANSFER_ACK_REQUIRED_EXIT_CODE));
    }

    #[test]
    fn malformed_adapter_inputs_fail_closed() {
        for (product, selector, other_product, transfer) in [
            ("", "native", "present", "accepted-and-verified"),
            ("Native", "native", "present", "accepted-and-verified"),
            ("native", "missing", "present", "accepted-and-verified"),
            ("native", "native", "maybe", "accepted-and-verified"),
            ("native", "native", "present", "accepted"),
            ("wsl ", "wsl", "absent", "not-requested"),
        ] {
            let plan = decide_from_adapter_inputs(product, selector, other_product, transfer);
            assert_eq!(plan.decision, Decision::Block);
            assert_eq!(plan.selector_mutation, SelectorMutation::None);
        }
    }

    #[test]
    fn cli_policy_mode_rejects_help_foreign_duplicate_and_missing_arguments() {
        let malformed = [
            vec![
                "--civiccast-native-uninstall-policy",
                "--product",
                "native",
                "--selector",
                "malformed",
                "--other-product",
                "unknown",
                "--transfer-state",
                "failed",
                "--help",
            ],
            vec![
                "--civiccast-acquire-channel",
                "https://example.invalid/channel.json",
                "--civiccast-native-uninstall-policy",
                "--product",
                "native",
                "--selector",
                "wsl",
                "--other-product",
                "present",
                "--transfer-state",
                "not-requested",
            ],
            vec![
                "--civiccast-native-uninstall-policy",
                "--civiccast-native-uninstall-policy",
                "--product",
                "native",
                "--selector",
                "wsl",
                "--other-product",
                "present",
                "--transfer-state",
                "not-requested",
            ],
            vec![
                "--civiccast-native-uninstall-policy",
                "--product",
                "native",
                "--product",
                "wsl",
                "--selector",
                "wsl",
                "--other-product",
                "present",
                "--transfer-state",
                "not-requested",
            ],
            vec![
                "--civiccast-native-uninstall-policy",
                "--product",
                "native",
                "--selector",
                "wsl",
                "--other-product",
                "present",
            ],
        ];

        for args in malformed {
            let owned: Vec<String> = args.into_iter().map(str::to_string).collect();
            let plan = decide_from_cli_args(&owned).expect("sentinel must own invocation");
            assert_eq!(plan, blocked_plan(), "args={owned:?}");
        }
    }

    #[test]
    fn cli_parser_distinguishes_absent_mode_from_valid_policy_invocation() {
        assert_eq!(decide_from_cli_args(&["--help".to_string()]), None);

        let args = [
            "--civiccast-native-uninstall-policy",
            "--product",
            "native",
            "--selector",
            "wsl",
            "--other-product",
            "present",
            "--transfer-state",
            "not-requested",
        ]
        .map(str::to_string);
        let plan = decide_from_cli_args(&args).expect("policy mode");
        assert_eq!(plan.decision, Decision::AllowInactive);
        assert_eq!(plan.selector_mutation, SelectorMutation::None);
    }

    #[test]
    fn adapter_tokens_expose_only_permitted_actions() {
        assert_eq!(
            decision_token(decide_from_adapter_inputs(
                "native",
                "wsl",
                "present",
                "not-requested"
            )),
            "allow-inactive"
        );
        assert_eq!(
            selector_mutation_token(decide_from_adapter_inputs("wsl", "wsl", "absent", "failed")),
            "clear-postuninstall"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn registry_probe_classifier_only_treats_not_found_as_absent() {
        assert_eq!(
            classify_registry_error(&std::io::Error::from(std::io::ErrorKind::NotFound)),
            OtherProductState::Absent
        );
        assert_eq!(
            classify_registry_error(&std::io::Error::from(std::io::ErrorKind::PermissionDenied)),
            OtherProductState::Unknown
        );
        assert_eq!(
            classify_registry_error(&std::io::Error::from(std::io::ErrorKind::InvalidData)),
            OtherProductState::Unknown
        );
    }

    #[test]
    fn hkey_users_skip_predicate_excludes_default_classes_and_service_sids() {
        for skipped in [
            ".DEFAULT",
            ".default",
            "S-1-5-18",
            "s-1-5-18",
            "S-1-5-19",
            "S-1-5-20",
            "S-1-5-21-1111111111-2222222222-3333333333-1001_Classes",
            "s-1-5-21-1111111111-2222222222-3333333333-1001_classes",
        ] {
            assert!(
                should_skip_users_subkey(skipped),
                "expected {skipped:?} to be skipped"
            );
        }
    }

    #[test]
    fn hkey_users_skip_predicate_keeps_real_user_and_unrelated_service_sids() {
        for kept in [
            "S-1-5-21-1111111111-2222222222-3333333333-1001",
            "S-1-5-21-1111111111-2222222222-3333333333-1002",
            "S-1-5-21-4444444444-5555555555-6666666666-1001",
            // Not a well-known skip SID (e.g. NT AUTHORITY\IUSR, S-1-5-17),
            // must still be probed rather than silently excluded.
            "S-1-5-17",
        ] {
            assert!(
                !should_skip_users_subkey(kept),
                "expected {kept:?} to be probed, not skipped"
            );
        }
    }

    #[test]
    fn combine_probe_results_present_wins_over_unknown_and_absent() {
        assert_eq!(
            combine_probe_results([
                OtherProductState::Absent,
                OtherProductState::Unknown,
                OtherProductState::Present,
            ]),
            OtherProductState::Present
        );
        assert_eq!(
            combine_probe_results([OtherProductState::Present, OtherProductState::Absent]),
            OtherProductState::Present
        );
    }

    #[test]
    fn combine_probe_results_fails_closed_to_unknown_without_any_present() {
        assert_eq!(
            combine_probe_results([OtherProductState::Absent, OtherProductState::Unknown]),
            OtherProductState::Unknown
        );
        assert_eq!(
            combine_probe_results([OtherProductState::Unknown]),
            OtherProductState::Unknown
        );
    }

    #[test]
    fn combine_probe_results_is_absent_only_when_every_observed_state_is_absent() {
        assert_eq!(
            combine_probe_results([OtherProductState::Absent, OtherProductState::Absent]),
            OtherProductState::Absent
        );
        assert_eq!(
            combine_probe_results(std::iter::empty()),
            OtherProductState::Absent
        );
    }

    /// Simulates the classification truth table `probe_wsl_arp` enforces
    /// without touching a live registry: HKCU's combined view plus the
    /// HKEY_USERS enumeration's combined view, run through the same
    /// fail-closed combinator the real probe uses.
    #[test]
    fn probe_wsl_arp_truth_table_present_beats_unknown_beats_absent() {
        let cases = [
            // (hkcu_combined, hkey_users_combined, expected)
            (
                OtherProductState::Absent,
                OtherProductState::Absent,
                OtherProductState::Absent,
            ),
            (
                OtherProductState::Present,
                OtherProductState::Unknown,
                OtherProductState::Present,
            ),
            (
                OtherProductState::Unknown,
                OtherProductState::Present,
                OtherProductState::Present,
            ),
            (
                OtherProductState::Absent,
                OtherProductState::Present,
                OtherProductState::Present,
            ),
            (
                OtherProductState::Absent,
                OtherProductState::Unknown,
                OtherProductState::Unknown,
            ),
            (
                OtherProductState::Unknown,
                OtherProductState::Absent,
                OtherProductState::Unknown,
            ),
        ];
        for (hkcu, hkey_users, expected) in cases {
            assert_eq!(
                combine_probe_results([hkcu, hkey_users]),
                expected,
                "hkcu={hkcu:?}, hkey_users={hkey_users:?}"
            );
        }
    }
}

#[cfg(test)]
mod install_selector_claim_tests {
    use super::*;

    /// Chain G RED: on a fresh native install with no selector written by
    /// anyone and no CivicCast WSL product registered, the install must claim
    /// `ActiveRuntime = "native"`. Without this the LocalSystem supervisor's
    /// dual-runtime guard reads `selector=absent`, and on any machine where
    /// the WSL install-detection probe cannot answer (R7: `wsl.exe` is the
    /// inbox stub and the SCM WSL-service query is ambiguous) it returns
    /// `blocked_probe_unavailable` and never starts the control plane.
    #[test]
    fn a_fresh_native_install_with_no_selector_and_no_wsl_product_claims_native() {
        assert_eq!(
            decide_install_selector_claim(SelectorState::Absent, WslProductVerdict::Absent),
            SelectorClaimAction::ClaimNative
        );
    }

    /// Round 3 (corrected field report): an inert ARP leftover of the WSL
    /// product on a machine a previous native install once handed off is
    /// claimable -- the warning is the orchestration's job, the decision
    /// simply writes.
    #[test]
    fn an_inert_wsl_leftover_is_claimed_over() {
        assert_eq!(
            decide_install_selector_claim(SelectorState::Absent, WslProductVerdict::PresentInert),
            SelectorClaimAction::ClaimNative
        );
    }

    /// An install NEVER steals the other product's existing ownership claim --
    /// the same rule `decide_selector_repair_action` states for repair. The
    /// guard's `never_start` on `selector=wsl` is the correct product
    /// behavior; the operator runs `cutover-to-native` to change it.
    #[test]
    fn an_install_never_steals_an_existing_wsl_ownership_claim() {
        for other in [
            WslProductVerdict::Absent,
            WslProductVerdict::PresentInert,
            WslProductVerdict::Present,
            WslProductVerdict::Unknown,
        ] {
            assert_eq!(
                decide_install_selector_claim(SelectorState::Wsl, other),
                SelectorClaimAction::LeaveWslOwnership,
                "other={other:?}"
            );
        }
    }

    /// A repair/upgrade install over a station that already claimed native is
    /// a no-op, not a redundant write: the value is already exactly what the
    /// guard needs, and a write that cannot change anything should not be
    /// able to fail the install either.
    #[test]
    fn an_install_over_an_existing_native_claim_is_a_no_op() {
        for other in [
            WslProductVerdict::Absent,
            WslProductVerdict::PresentInert,
            WslProductVerdict::Present,
            WslProductVerdict::Unknown,
        ] {
            assert_eq!(
                decide_install_selector_claim(SelectorState::Native, other),
                SelectorClaimAction::AlreadyNative,
                "other={other:?}"
            );
        }
    }

    /// Fail-closed: a selector this process could not READ may already say
    /// `"wsl"`. Writing over it would silently steal ownership on exactly the
    /// evidence we do not have.
    #[test]
    fn an_unreadable_selector_is_never_overwritten_by_an_install() {
        for other in [
            WslProductVerdict::Absent,
            WslProductVerdict::PresentInert,
            WslProductVerdict::Present,
            WslProductVerdict::Unknown,
        ] {
            assert_eq!(
                decide_install_selector_claim(SelectorState::Unreadable, other),
                SelectorClaimAction::LeaveUnreadable,
                "other={other:?}"
            );
        }
    }

    /// The coexistence row: no selector, but the WSL product IS installed (or
    /// its presence cannot be proved either way). The native installer has no
    /// authority to decide which of two installed products transmits, so it
    /// leaves the selector absent and the guard's `refuse_instruct` tells the
    /// operator to run the cutover.
    #[test]
    fn an_absent_selector_with_a_wsl_product_present_or_unknown_is_left_to_the_operator() {
        for (other, expected) in [
            (
                WslProductVerdict::Present,
                SelectorClaimAction::LeaveOtherProductPresent,
            ),
            (WslProductVerdict::Unknown, SelectorClaimAction::LeaveUnprovable),
        ] {
            let action = decide_install_selector_claim(SelectorState::Absent, other);
            assert_eq!(action, expected, "other={other:?}");
            assert!(action.is_refusal());
        }
        // Round 3: the three refusals are distinct actions because their
        // remedies share nothing (uninstall the other product / fix the
        // failed read / repair the value).
        assert!(
            decide_install_selector_claim(SelectorState::Unreadable, WslProductVerdict::Absent)
                .is_refusal()
        );
        assert!(!SelectorClaimAction::ClaimNative.is_refusal());
        assert!(!SelectorClaimAction::AlreadyNative.is_refusal());
        assert!(!SelectorClaimAction::LeaveWslOwnership.is_refusal());
    }

    /// Totality over the whole 4x4 input product -- the same property
    /// `decide_selector_repair_action`'s own table carries. Exactly TWO cells
    /// write: the clean claim and the inert-leftover claim.
    #[test]
    fn the_claim_decision_is_total_and_exactly_one_cell_writes() {
        let selectors = [
            SelectorState::Native,
            SelectorState::Wsl,
            SelectorState::Absent,
            SelectorState::Unreadable,
        ];
        let others = [
            WslProductVerdict::Absent,
            WslProductVerdict::PresentInert,
            WslProductVerdict::Present,
            WslProductVerdict::Unknown,
        ];
        let mut writes = 0;
        for selector in selectors {
            for other in others {
                if decide_install_selector_claim(selector, other)
                    == SelectorClaimAction::ClaimNative
                {
                    writes += 1;
                }
            }
        }
        assert_eq!(
            writes, 2,
            "only (Absent, Absent) and (Absent, PresentInert) may write; every other cell \
             leaves the selector alone"
        );
    }

    /// The written value is the EXACT byte string the Python guard's read
    /// path accepts. `civiccast.native.win_probes.read_selector` returns
    /// `SelectorRead(ok=True, value="native")` only for a `REG_SZ` value named
    /// `ActiveRuntime` under `SOFTWARE\CivicCast` in the 64-bit view whose
    /// text is exactly `native`; anything else is `ok=False` (unreadable) and
    /// the guard blocks. This pins all three halves of that contract on the
    /// writing side.
    #[test]
    fn the_claimed_selector_matches_the_python_guards_read_contract() {
        assert_eq!(NATIVE_SELECTOR_VALUE, "native");
        assert_eq!(SELECTOR_KEY, r"SOFTWARE\CivicCast");
        assert_eq!(SELECTOR_VALUE, "ActiveRuntime");
    }
}

#[cfg(test)]
mod install_selector_claim_orchestration_tests {
    use super::*;
    use std::cell::RefCell;

    /// Behavioral RED: the claim ORCHESTRATION (probe selector, probe the WSL
    /// product, then write or deliberately not write) is exercised end to end
    /// against injected probes, so the "did an install actually write the
    /// selector" question is answered by a test rather than by reading
    /// `run_native_provision`. The real registry mutation stays out of the
    /// test per this module's HARD RULE -- only the writer is faked.
    fn run(
        selector: SelectorState,
        other: OtherProductState,
        write_result: Result<(), String>,
    ) -> (SelectorClaimOutcome, usize) {
        let writes = RefCell::new(0usize);
        let outcome = claim_install_selector_with(
            || selector,
            || WslPresenceEvidence::user_arp_only(other),
            || {
                *writes.borrow_mut() += 1;
                write_result.clone()
            },
        );
        let count = *writes.borrow();
        (outcome, count)
    }

    #[test]
    fn a_fresh_install_writes_the_selector_exactly_once_and_reports_it_claimed() {
        let (outcome, writes) = run(SelectorState::Absent, OtherProductState::Absent, Ok(()));
        assert_eq!(outcome.action, SelectorClaimAction::ClaimNative);
        assert_eq!(outcome.write_error, None);
        assert_eq!(writes, 1);
    }

    #[test]
    fn every_non_claiming_cell_never_touches_the_writer() {
        for (selector, other) in [
            (SelectorState::Native, OtherProductState::Absent),
            (SelectorState::Wsl, OtherProductState::Absent),
            (SelectorState::Unreadable, OtherProductState::Absent),
            (SelectorState::Absent, OtherProductState::Present),
            (SelectorState::Absent, OtherProductState::Unknown),
        ] {
            let (outcome, writes) = run(selector, other, Ok(()));
            assert_eq!(outcome.verdict.is_some(), selector == SelectorState::Absent);
            assert_eq!(writes, 0, "selector={selector:?}, other={other:?}");
            assert_ne!(outcome.action, SelectorClaimAction::ClaimNative);
        }
    }

    #[test]
    fn a_failed_selector_write_is_surfaced_and_never_swallowed() {
        let (outcome, writes) = run(
            SelectorState::Absent,
            OtherProductState::Absent,
            Err("access denied".to_string()),
        );
        assert_eq!(writes, 1);
        assert_eq!(outcome.action, SelectorClaimAction::ClaimNative);
        assert_eq!(outcome.write_error.as_deref(), Some("access denied"));
    }

    /// The operator-facing sentence for each outcome must name the real state,
    /// never a generic success. A station whose selector was left alone will
    /// not start, and the install log is the only place that says why.
    #[test]
    fn every_outcome_explains_itself_distinctly() {
        let mut seen: Vec<String> = Vec::new();
        for (selector, other) in [
            (SelectorState::Absent, OtherProductState::Absent),
            (SelectorState::Native, OtherProductState::Absent),
            (SelectorState::Wsl, OtherProductState::Absent),
            (SelectorState::Unreadable, OtherProductState::Absent),
            (SelectorState::Absent, OtherProductState::Present),
        ] {
            let (outcome, _) = run(selector, other, Ok(()));
            let detail = outcome.detail;
            assert!(!detail.trim().is_empty());
            assert!(
                detail.contains("ActiveRuntime"),
                "detail must name the selector: {detail}"
            );
            seen.push(detail);
        }
        seen.sort();
        seen.dedup();
        assert_eq!(
            seen.len(),
            5,
            "each observed (selector, other-product) pair explains itself distinctly"
        );
    }
}

#[cfg(test)]
mod runtime_ownership_evidence_tests {
    use super::*;
    use std::cell::RefCell;

    /// The four pre-round-3 sources; the two inert-leftover sources default
    /// to `Unknown` (unconsulted), which can never make a `Present` inert.
    fn evidence(
        user_arp: OtherProductState,
        machine_arp: OtherProductState,
        distro_registration: OtherProductState,
        wsl_service: OtherProductState,
    ) -> WslPresenceEvidence {
        WslPresenceEvidence {
            user_arp,
            machine_arp,
            distro_registration,
            wsl_service,
            autostart: OtherProductState::Unknown,
            native_transfer_marker: OtherProductState::Unknown,
            observations: Vec::new(),
        }
    }

    /// The corrected field report's box: the old WSL-era ARP entry under
    /// HKCU with its real values, no distro, no autostart, transfer marker
    /// set, WSL service present (stock Ubuntu-24.04 is installed).
    fn field_report_present_observation() -> ProbeObservation {
        let mut observation = ProbeObservation::settled(
            "user-ARP",
            "HKCU".to_string(),
            "64-bit",
            OtherProductState::Present,
        );
        observation.product = Some(ArpProductRecord {
            user: "tester".to_string(),
            display_name: Some("CivicCast Installer".to_string()),
            display_version: Some("3.0.0-beta1".to_string()),
            publisher: Some("civiccast".to_string()),
            install_location: Some(r"D:\Profiles\tester\AppData\Local\CivicCast Installer".to_string()),
            uninstall_string: Some(
                r"D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe".to_string(),
            ),
        });
        observation
    }

    fn field_report_evidence(
        autostart: OtherProductState,
        native_transfer_marker: OtherProductState,
    ) -> WslPresenceEvidence {
        WslPresenceEvidence {
            user_arp: OtherProductState::Present,
            machine_arp: OtherProductState::Absent,
            distro_registration: OtherProductState::Absent,
            wsl_service: OtherProductState::Present,
            autostart,
            native_transfer_marker,
            observations: vec![field_report_present_observation()],
        }
    }

    #[test]
    fn uninstall_treats_an_inert_wsl_arp_leftover_as_absent_but_keeps_real_products() {
        // RC4 host test 2026-11-04: RC3's uninstall demanded an ActiveRuntime
        // transfer to "CivicCast Installer" 3.0.0-beta1 whose InstallLocation
        // and uninstall.exe no longer existed -- the exact field-report box the
        // install-time inert-leftover rule already classifies as claimable.
        let never = || -> WslPresenceEvidence { panic!("evidence must not be gathered") };

        // An inert leftover: no transfer, with a warning that names it.
        let (state, note) = uninstall_effective_wsl_state(OtherProductState::Present, || {
            field_report_evidence(OtherProductState::Absent, OtherProductState::Present)
        });
        assert_eq!(state, OtherProductState::Absent);
        let note = note.expect("an inert leftover must be reported");
        assert!(note.starts_with("WARNING"), "{note}");
        assert!(note.contains("CivicCast Installer 3.0.0-beta1"), "{note}");

        // Anything short of all three confident inert conditions keeps Present.
        for (autostart, marker) in [
            (OtherProductState::Present, OtherProductState::Present),
            (OtherProductState::Unknown, OtherProductState::Present),
            (OtherProductState::Absent, OtherProductState::Absent),
            (OtherProductState::Absent, OtherProductState::Unknown),
        ] {
            let (state, note) = uninstall_effective_wsl_state(OtherProductState::Present, || {
                field_report_evidence(autostart, marker)
            });
            assert_eq!(
                state,
                OtherProductState::Present,
                "{autostart:?} {marker:?}"
            );
            assert!(note.is_none());
        }
        let mut with_distro =
            field_report_evidence(OtherProductState::Absent, OtherProductState::Present);
        with_distro.distro_registration = OtherProductState::Present;
        assert_eq!(
            uninstall_effective_wsl_state(OtherProductState::Present, || with_distro.clone()).0,
            OtherProductState::Present
        );

        // Absent and Unknown pass through unchanged and gather nothing.
        assert_eq!(
            uninstall_effective_wsl_state(OtherProductState::Absent, never),
            (OtherProductState::Absent, None)
        );
        assert_eq!(
            uninstall_effective_wsl_state(OtherProductState::Unknown, never),
            (OtherProductState::Unknown, None)
        );
    }

    fn denied_hive_observation() -> ProbeObservation {
        ProbeObservation {
            source: "user-ARP",
            scope: r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001".to_string(),
            view: "64-bit",
            state: OtherProductState::Unknown,
            error_kind: Some("PermissionDenied".to_string()),
            raw_code: Some(5),
            product: None,
        }
    }

    /// The field defect (beta.5, dirty test box with uninstall history): the
    /// per-user ARP probe came back Unknown from a hive it could not read,
    /// and on that alone the install refused to claim ownership. With every
    /// machine-wide source confidently Absent, the claim proceeds.
    #[test]
    fn unknown_user_arp_is_outweighed_by_confidently_absent_machine_wide_evidence() {
        let e = evidence(
            OtherProductState::Unknown,
            OtherProductState::Absent,
            OtherProductState::Absent,
            OtherProductState::Absent,
        );
        assert_eq!(corroborate_wsl_product_state(&e), OtherProductState::Absent);
        assert_eq!(
            decide_install_selector_claim(SelectorState::Absent, classify_wsl_product_for_claim(&e)),
            SelectorClaimAction::ClaimNative
        );
    }

    /// No WSL service registered at all means no CivicCast WSL product can
    /// exist or transmit -- dispositive on its own, the same rule the Python
    /// guard's `_confirm_absence_via_service` applies.
    #[test]
    fn an_absent_wsl_service_alone_rules_the_wsl_product_out() {
        for (machine, distro) in [
            (OtherProductState::Unknown, OtherProductState::Unknown),
            (OtherProductState::Absent, OtherProductState::Unknown),
            (OtherProductState::Unknown, OtherProductState::Absent),
        ] {
            let e = evidence(
                OtherProductState::Unknown,
                machine,
                distro,
                OtherProductState::Absent,
            );
            assert_eq!(
                corroborate_wsl_product_state(&e),
                OtherProductState::Absent,
                "machine={machine:?} distro={distro:?}"
            );
        }
    }

    /// WSL installed for something unrelated (service Present) is not
    /// evidence of the CivicCast product: with no product ARP under HKLM
    /// and no CivicCast distro registered anywhere, the claim proceeds.
    #[test]
    fn a_present_wsl_service_without_product_or_distro_still_claims_native() {
        let e = evidence(
            OtherProductState::Unknown,
            OtherProductState::Absent,
            OtherProductState::Absent,
            OtherProductState::Present,
        );
        assert_eq!(corroborate_wsl_product_state(&e), OtherProductState::Absent);
    }

    /// A genuine WSL-product observation anywhere -- per-user ARP, machine
    /// ARP or the distro registration -- still refuses, whatever the other
    /// sources say.
    #[test]
    fn any_genuine_wsl_product_observation_still_refuses() {
        let present_cases = [
            evidence(
                OtherProductState::Present,
                OtherProductState::Absent,
                OtherProductState::Absent,
                OtherProductState::Absent,
            ),
            evidence(
                OtherProductState::Unknown,
                OtherProductState::Present,
                OtherProductState::Absent,
                OtherProductState::Absent,
            ),
            evidence(
                OtherProductState::Absent,
                OtherProductState::Absent,
                OtherProductState::Present,
                OtherProductState::Unknown,
            ),
            evidence(
                OtherProductState::Unknown,
                OtherProductState::Absent,
                OtherProductState::Present,
                OtherProductState::Absent,
            ),
        ];
        for e in present_cases {
            assert_eq!(
                corroborate_wsl_product_state(&e),
                OtherProductState::Present,
                "{e:?}"
            );
            assert_eq!(
                decide_install_selector_claim(
                    SelectorState::Absent,
                    classify_wsl_product_for_claim(&e)
                ),
                SelectorClaimAction::LeaveOtherProductPresent
            );
        }
    }

    /// Unknown user-ARP with the corroborating sources themselves unable to
    /// answer stays fail-closed -- the install refuses with the observation
    /// rather than guessing.
    #[test]
    fn unknown_user_arp_without_confident_corroboration_stays_unknown() {
        for (machine, distro, service) in [
            (
                OtherProductState::Unknown,
                OtherProductState::Unknown,
                OtherProductState::Unknown,
            ),
            (
                OtherProductState::Absent,
                OtherProductState::Unknown,
                OtherProductState::Present,
            ),
            (
                OtherProductState::Unknown,
                OtherProductState::Absent,
                OtherProductState::Unknown,
            ),
        ] {
            let e = evidence(OtherProductState::Unknown, machine, distro, service);
            assert_eq!(
                corroborate_wsl_product_state(&e),
                OtherProductState::Unknown,
                "machine={machine:?} distro={distro:?} service={service:?}"
            );
        }
    }

    /// The pre-corroboration contract is preserved bit for bit: an Absent
    /// per-user ARP verdict was sufficient before, and still is, even when
    /// the new sources cannot answer (so a machine where `sc.exe` misbehaves
    /// does not regress a fresh install).
    #[test]
    fn user_arp_only_evidence_reproduces_the_original_decision_table() {
        for (user_arp, expected) in [
            (OtherProductState::Absent, OtherProductState::Absent),
            (OtherProductState::Present, OtherProductState::Present),
            (OtherProductState::Unknown, OtherProductState::Unknown),
        ] {
            assert_eq!(
                corroborate_wsl_product_state(&WslPresenceEvidence::user_arp_only(user_arp)),
                expected
            );
        }
    }

    #[test]
    fn sc_query_exit_classification_matches_the_python_guard() {
        assert_eq!(
            classify_sc_query_exit_code(Some(0)),
            OtherProductState::Present
        );
        assert_eq!(
            classify_sc_query_exit_code(Some(1060)),
            OtherProductState::Absent
        );
        assert_eq!(
            classify_sc_query_exit_code(Some(5)),
            OtherProductState::Unknown
        );
        assert_eq!(
            classify_sc_query_exit_code(Some(1)),
            OtherProductState::Unknown
        );
        assert_eq!(
            classify_sc_query_exit_code(None),
            OtherProductState::Unknown
        );
        assert_eq!(ERROR_SERVICE_DOES_NOT_EXIST, 1060);
    }

    #[test]
    fn sc_query_deadline_matches_the_python_guard() {
        assert_eq!(SC_QUERY_TIMEOUT_SECONDS, 5);
    }

    fn exit_7_command() -> std::process::Command {
        let mut command;
        if cfg!(target_os = "windows") {
            command = std::process::Command::new("cmd.exe");
            command.args(["/c", "exit 7"]);
        } else {
            command = std::process::Command::new("sh");
            command.args(["-c", "exit 7"]);
        }
        command
    }

    fn sleeping_command() -> std::process::Command {
        let mut command;
        if cfg!(target_os = "windows") {
            command = std::process::Command::new("ping.exe");
            command.args(["-n", "30", "127.0.0.1"]);
        } else {
            command = std::process::Command::new("sleep");
            command.arg("30");
        }
        command
    }

    /// A child that exits inside the deadline reports its real exit code
    /// (the value the classifier consumes).
    #[test]
    fn wait_with_deadline_returns_the_exit_code_of_a_prompt_child() {
        let status = wait_with_deadline(
            &mut exit_7_command(),
            std::time::Duration::from_secs(SC_QUERY_TIMEOUT_SECONDS),
        )
        .expect("prompt child must not time out");
        assert_eq!(status.code(), Some(7));
        assert_eq!(
            classify_sc_query_exit_code(status.code()),
            OtherProductState::Unknown
        );
    }

    /// A child that outlives the deadline is killed, reaped, and surfaces
    /// as a `TimedOut` error -- the branch that records `Unknown`, so a hung
    /// SCM never reads as "no WSL service".
    #[test]
    fn wait_with_deadline_kills_a_hung_child_and_reports_timed_out() {
        let started = std::time::Instant::now();
        let error = wait_with_deadline(
            &mut sleeping_command(),
            std::time::Duration::from_millis(300),
        )
        .expect_err("a 30 s child must expire a 300 ms deadline");
        assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
        assert_eq!(format!("{:?}", error.kind()), "TimedOut");
        assert!(error.raw_os_error().is_none());
        assert!(
            started.elapsed() < std::time::Duration::from_secs(10),
            "the child was not killed on expiry: waited {:?}",
            started.elapsed()
        );
    }

    /// An observation names its source, hive/SID, WOW64 view, classification,
    /// error kind and raw error code -- everything needed to reproduce the
    /// failing read by hand from the install log.
    #[test]
    fn an_observation_formats_hive_view_kind_and_raw_code() {
        let text = denied_hive_observation().to_string();
        assert_eq!(
            text,
            r"user-ARP HKU\S-1-5-21-1111111111-2222222222-3333333333-1001 (64-bit view): unknown [PermissionDenied, os error 5]"
        );
        let settled = ProbeObservation::settled(
            "machine-ARP",
            "HKLM".to_string(),
            "32-bit",
            OtherProductState::Absent,
        );
        assert_eq!(
            settled.to_string(),
            "machine-ARP HKLM (32-bit view): absent"
        );
        let service = ProbeObservation {
            source: "wsl-service",
            scope: "sc query WslService".to_string(),
            view: "n/a",
            state: OtherProductState::Unknown,
            error_kind: None,
            raw_code: Some(1722),
            product: None,
        };
        assert_eq!(
            service.to_string(),
            "wsl-service sc query WslService: unknown [exit 1722]"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn a_registry_observation_keeps_error_detail_only_for_non_absent_reads() {
        let denied = ProbeObservation::from_registry_error(
            "user-ARP",
            "HKCU".to_string(),
            "64-bit",
            &std::io::Error::from_raw_os_error(5),
        );
        assert_eq!(denied.state, OtherProductState::Unknown);
        assert_eq!(denied.error_kind.as_deref(), Some("PermissionDenied"));
        assert_eq!(denied.raw_code, Some(5));

        let not_found = ProbeObservation::from_registry_error(
            "user-ARP",
            "HKCU".to_string(),
            "64-bit",
            &std::io::Error::from_raw_os_error(2),
        );
        assert_eq!(not_found.state, OtherProductState::Absent);
        assert_eq!(not_found.error_kind, None);
        assert_eq!(not_found.raw_code, None);
    }

    /// `explain` names every source's verdict and, for any source that was
    /// not a clean absence, the individual observations behind it -- and
    /// stays quiet about hives that read absent (they explain nothing).
    #[test]
    fn explain_names_every_source_and_only_the_notable_observations() {
        let mut e = evidence(
            OtherProductState::Unknown,
            OtherProductState::Absent,
            OtherProductState::Absent,
            OtherProductState::Present,
        );
        e.observations = vec![
            ProbeObservation::settled(
                "user-ARP",
                "HKCU".to_string(),
                "64-bit",
                OtherProductState::Absent,
            ),
            denied_hive_observation(),
            ProbeObservation::settled(
                "machine-ARP",
                "HKLM".to_string(),
                "64-bit",
                OtherProductState::Absent,
            ),
            ProbeObservation {
                source: "wsl-service",
                scope: "sc query WslService".to_string(),
                view: "n/a",
                state: OtherProductState::Present,
                error_kind: None,
                raw_code: Some(0),
                product: None,
            },
        ];
        let text = e.explain();
        assert_eq!(
            text,
            r"user-ARP=unknown [user-ARP HKU\S-1-5-21-1111111111-2222222222-3333333333-1001 (64-bit view): unknown [PermissionDenied, os error 5]], machine-ARP=absent, distro-scan=absent, wsl-service=present [wsl-service sc query WslService: present [exit 0]], autostart=unknown, transfer-marker=unknown"
        );
        assert!(
            !text.contains("HKCU"),
            "an absent hive must not clutter the report"
        );
        assert_eq!(e.observation_lines().len(), 4);
    }

    /// End to end through the orchestration: the field defect's evidence
    /// shape writes the selector exactly once and the detail names the hive
    /// that could not be read.
    #[test]
    fn the_field_defect_evidence_claims_native_and_names_the_unreadable_hive() {
        let writes = RefCell::new(0usize);
        let mut e = evidence(
            OtherProductState::Unknown,
            OtherProductState::Absent,
            OtherProductState::Absent,
            OtherProductState::Absent,
        );
        e.observations = vec![denied_hive_observation()];
        let outcome = claim_install_selector_with(
            || SelectorState::Absent,
            || e.clone(),
            || {
                *writes.borrow_mut() += 1;
                Ok(())
            },
        );
        assert_eq!(outcome.action, SelectorClaimAction::ClaimNative);
        assert_eq!(*writes.borrow(), 1);
        assert_eq!(outcome.selector, SelectorState::Absent);
        assert_eq!(outcome.evidence.as_ref(), Some(&e));
        assert!(outcome
            .detail
            .contains("S-1-5-21-1111111111-2222222222-3333333333-1001"));
        assert!(outcome.detail.contains("PermissionDenied, os error 5"));
        assert!(outcome.detail.contains("wsl-service=absent"));
    }

    /// The refusal detail carries the same observation, so the install log
    /// and the recovery document name the exact read that failed instead of
    /// the bare word `Unknown`.
    #[test]
    fn a_refusal_detail_carries_the_per_hive_observation() {
        let mut e = evidence(
            OtherProductState::Unknown,
            OtherProductState::Unknown,
            OtherProductState::Unknown,
            OtherProductState::Present,
        );
        e.observations = vec![denied_hive_observation()];
        let outcome =
            claim_install_selector_with(|| SelectorState::Absent, || e.clone(), || Ok(()));
        assert_eq!(outcome.action, SelectorClaimAction::LeaveUnprovable);
        assert!(
            outcome.detail.contains("PermissionDenied, os error 5"),
            "{}",
            outcome.detail
        );
        assert!(
            outcome.detail.contains("machine-ARP=unknown"),
            "{}",
            outcome.detail
        );
        assert!(outcome
            .detail
            .contains("HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime"));
    }

    /// Evidence is gathered ONLY when the selector is absent; every other
    /// selector state settles the question without a single probe.
    #[test]
    fn evidence_is_not_gathered_when_the_selector_settles_the_question() {
        for selector in [
            SelectorState::Native,
            SelectorState::Wsl,
            SelectorState::Unreadable,
        ] {
            let probes = RefCell::new(0usize);
            let outcome = claim_install_selector_with(
                || selector,
                || {
                    *probes.borrow_mut() += 1;
                    WslPresenceEvidence::user_arp_only(OtherProductState::Absent)
                },
                || Ok(()),
            );
            assert_eq!(*probes.borrow(), 0, "{selector:?}");
            assert_eq!(outcome.evidence, None);
        }
    }

    /// Cross-language pins: the Rust port must look for exactly what the
    /// Python guard looks for (also asserted from the Python side by
    /// tests/policy/test_native_installer_identity.py).
    #[test]
    fn the_ported_probe_constants_match_the_python_guard() {
        assert_eq!(WSL_DISTRO_NAME, "CivicCast-Ubuntu-24.04");
        assert_eq!(
            WSL_LXSS_KEY_PATH,
            r"Software\Microsoft\Windows\CurrentVersion\Lxss"
        );
        assert_eq!(WSL_SERVICE_NAMES, ["WslService", "LxssManager"]);
        assert_eq!(
            WSL_AUTOSTART_RUN_KEY,
            r"Software\Microsoft\Windows\CurrentVersion\Run"
        );
        assert_eq!(WSL_AUTOSTART_RUN_VALUE, "CivicCast Autostart");
    }

    // -----------------------------------------------------------------
    // Round 3: the corrected field report (Present, not Unknown)
    // -----------------------------------------------------------------

    /// Pin 1: a Present ARP entry WITH the distro registered is a real
    /// product, whatever the autostart/marker say -- refuse, and refuse as
    /// `LeaveOtherProductPresent`, never as the Unknown text.
    #[test]
    fn a_present_product_with_a_registered_distro_refuses() {
        let mut e = field_report_evidence(OtherProductState::Absent, OtherProductState::Present);
        e.distro_registration = OtherProductState::Present;
        assert_eq!(classify_wsl_product_for_claim(&e), WslProductVerdict::Present);
        let outcome =
            claim_install_selector_with(|| SelectorState::Absent, || e.clone(), || Ok(()));
        assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert_eq!(outcome.verdict, Some(WslProductVerdict::Present));
        assert!(
            outcome.detail.starts_with(
                "Setup found another CivicCast product installed for user tester: CivicCast \
                 Installer 3.0.0-beta1 (registered at HKCU\\...\\Uninstall\\CivicCast Installer; \
                 InstallLocation D:\\Profiles\\tester\\AppData\\Local\\CivicCast Installer; \
                 UninstallString D:\\Profiles\\tester\\AppData\\Local\\CivicCast \
                 Installer\\uninstall.exe)"
            ),
            "{}",
            outcome.detail
        );
        assert!(outcome.detail.contains(
            "Uninstall 'CivicCast Installer 3.0.0-beta1' from Settings > Apps (or run \
             D:\\Profiles\\tester\\AppData\\Local\\CivicCast Installer\\uninstall.exe), or run \
             `civiccast-runtime cutover-to-native`, then run setup again."
        ));
        assert!(
            !outcome.detail.contains("sets HKLM"),
            "a real Present must not carry the registry-edit remedy: {}",
            outcome.detail
        );
        assert!(!outcome.detail.contains("permission"));
    }

    /// Pin 2: the field report's exact shape -- Present ARP entry, no
    /// distro, no autostart, transfer marker set -- is an inert leftover:
    /// claim native, exactly one write, and the detail leads with the
    /// WARNING naming the leftover and how to remove it.
    #[test]
    fn a_present_entry_with_no_distro_no_autostart_and_the_transfer_marker_claims_with_a_warning()
    {
        let e = field_report_evidence(OtherProductState::Absent, OtherProductState::Present);
        assert_eq!(corroborate_wsl_product_state(&e), OtherProductState::Present);
        assert_eq!(
            classify_wsl_product_for_claim(&e),
            WslProductVerdict::PresentInert
        );
        let writes = RefCell::new(0usize);
        let outcome = claim_install_selector_with(
            || SelectorState::Absent,
            || e.clone(),
            || {
                *writes.borrow_mut() += 1;
                Ok(())
            },
        );
        assert_eq!(outcome.action, SelectorClaimAction::ClaimNative);
        assert_eq!(outcome.verdict, Some(WslProductVerdict::PresentInert));
        assert_eq!(*writes.borrow(), 1);
        assert_eq!(outcome.write_error, None);
        assert!(
            outcome.detail.starts_with(
                "WARNING: an old CivicCast Installer 3.0.0-beta1 registration remains for user \
                 tester with no CivicCast distro or autostart entry, and a previous native \
                 install had already handed this machine off (NativeUninstallTransferCompleted \
                 is set); ActiveRuntime was absent ("
            ),
            "{}",
            outcome.detail
        );
        assert!(outcome
            .detail
            .contains("this install claimed native ownership (ActiveRuntime = \"native\", read-back verified)."));
        assert!(outcome.detail.ends_with(
            "Remove the leftover via Apps & Features (uninstall.exe at \
             D:\\Profiles\\tester\\AppData\\Local\\CivicCast Installer\\uninstall.exe)."
        ), "{}", outcome.detail);
        assert!(outcome.detail.contains("transfer-marker=present"));

        // A failed write on the inert path is still surfaced, still warned.
        let failed = claim_install_selector_with(
            || SelectorState::Absent,
            || e.clone(),
            || Err("access denied".to_string()),
        );
        assert_eq!(failed.write_error.as_deref(), Some("access denied"));
        assert!(failed.detail.starts_with("WARNING: an old CivicCast Installer 3.0.0-beta1"));
        assert!(failed.detail.contains("claiming native ownership failed: access denied"));
    }

    /// Pin 3: every one of the three inert conditions must be CONFIDENT.
    /// No distro but an Unknown autostart (or an Unknown/Absent marker, or
    /// an Unknown distro scan) keeps the refusal.
    #[test]
    fn a_present_entry_with_any_inert_condition_not_confident_refuses() {
        let cases = [
            ("autostart unknown", field_report_evidence(OtherProductState::Unknown, OtherProductState::Present)),
            ("autostart present", field_report_evidence(OtherProductState::Present, OtherProductState::Present)),
            ("marker unknown", field_report_evidence(OtherProductState::Absent, OtherProductState::Unknown)),
            ("marker absent", field_report_evidence(OtherProductState::Absent, OtherProductState::Absent)),
            ("distro unknown", {
                let mut e = field_report_evidence(OtherProductState::Absent, OtherProductState::Present);
                e.distro_registration = OtherProductState::Unknown;
                e
            }),
        ];
        for (label, e) in cases {
            assert_eq!(
                classify_wsl_product_for_claim(&e),
                WslProductVerdict::Present,
                "{label}"
            );
            let (outcome, writes) = {
                let writes = RefCell::new(0usize);
                let outcome = claim_install_selector_with(
                    || SelectorState::Absent,
                    || e.clone(),
                    || {
                        *writes.borrow_mut() += 1;
                        Ok(())
                    },
                );
                let count = *writes.borrow();
                (outcome, count)
            };
            assert_eq!(
                outcome.action,
                SelectorClaimAction::LeaveOtherProductPresent,
                "{label}"
            );
            assert_eq!(writes, 0, "{label}");
            assert!(
                outcome.detail.starts_with("Setup found another CivicCast product installed for user tester"),
                "{label}: {}",
                outcome.detail
            );
        }
    }

    /// The inert rule never fires on the pre-round-3 evidence shapes (the
    /// two new sources unconsulted = Unknown), so every earlier pin holds.
    #[test]
    fn the_inert_rule_needs_both_new_sources_and_never_fires_on_absent_or_unknown() {
        for user_arp in [
            OtherProductState::Absent,
            OtherProductState::Present,
            OtherProductState::Unknown,
        ] {
            let verdict =
                classify_wsl_product_for_claim(&WslPresenceEvidence::user_arp_only(user_arp));
            assert_ne!(verdict, WslProductVerdict::PresentInert, "{user_arp:?}");
            assert_eq!(
                verdict.token(),
                other_product_token(corroborate_wsl_product_state(
                    &WslPresenceEvidence::user_arp_only(user_arp)
                ))
            );
        }
    }

    /// Round 4 (hostile review of round 3): a MACHINE-scope ARP entry whose
    /// owner hive is not loaded. `distro_registration` and `autostart` are
    /// scans over LOADED user hives only, so with the HKLM entry's owner
    /// logged out both read `Absent` for a hive they never looked at. That
    /// absence proves nothing about the leftover's inertness: the verdict
    /// must be `Present` (exit 87, zero writes), never `PresentInert`.
    #[test]
    fn a_machine_arp_entry_with_no_loaded_owner_hive_is_never_inert() {
        let mut hklm = ProbeObservation::settled(
            "machine-ARP",
            "HKLM".to_string(),
            "64-bit",
            OtherProductState::Present,
        );
        hklm.product = Some(ArpProductRecord {
            user: "(per-machine)".to_string(),
            display_name: Some("CivicCast Installer".to_string()),
            display_version: Some("3.0.0-beta1".to_string()),
            ..ArpProductRecord::default()
        });
        // Exactly the field report's inert shape (no distro in any loaded
        // hive, no autostart in any loaded hive, hand-off marker set) --
        // except the entry is per-machine and no loaded user hive carries it.
        let e = WslPresenceEvidence {
            user_arp: OtherProductState::Absent,
            machine_arp: OtherProductState::Present,
            distro_registration: OtherProductState::Absent,
            wsl_service: OtherProductState::Present,
            autostart: OtherProductState::Absent,
            native_transfer_marker: OtherProductState::Present,
            observations: vec![hklm],
        };
        assert_eq!(corroborate_wsl_product_state(&e), OtherProductState::Present);
        assert_eq!(
            classify_wsl_product_for_claim(&e),
            WslProductVerdict::Present,
            "an ARP entry whose owner hive is not loaded cannot be proven inert"
        );
        let writes = RefCell::new(0usize);
        let outcome = claim_install_selector_with(
            || SelectorState::Absent,
            || e.clone(),
            || {
                *writes.borrow_mut() += 1;
                Ok(())
            },
        );
        assert_eq!(outcome.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert_eq!(outcome.verdict, Some(WslProductVerdict::Present));
        assert_eq!(*writes.borrow(), 0);
        assert!(!outcome.detail.starts_with("WARNING"), "{}", outcome.detail);

        // The same evidence with the user-ARP probe also Unknown (a hive it
        // could not read) is no more inert than Absent.
        let mut unknown_user = e.clone();
        unknown_user.user_arp = OtherProductState::Unknown;
        assert_eq!(
            classify_wsl_product_for_claim(&unknown_user),
            WslProductVerdict::Present
        );

        // Control: the owner hive IS loaded (the per-user entry was found in
        // it), so the per-user absences are real reads -> inert, as round 3.
        let mut loaded_owner = e.clone();
        loaded_owner.user_arp = OtherProductState::Present;
        loaded_owner.observations.push(field_report_present_observation());
        assert_eq!(
            classify_wsl_product_for_claim(&loaded_owner),
            WslProductVerdict::PresentInert
        );
    }

    /// A Present observation carries the ARP record in its Display, so the
    /// observation line and the recovery document name the product, its
    /// version, publisher, InstallLocation and UninstallString.
    #[test]
    fn a_present_observation_displays_the_arp_record() {
        assert_eq!(
            field_report_present_observation().to_string(),
            r"user-ARP HKCU (64-bit view): present [user tester; CivicCast Installer 3.0.0-beta1; Publisher civiccast; InstallLocation D:\Profiles\tester\AppData\Local\CivicCast Installer; UninstallString D:\Profiles\tester\AppData\Local\CivicCast Installer\uninstall.exe]"
        );
        let bare = ArpProductRecord {
            user: "S-1-5-21-1-2-3-1001".to_string(),
            ..ArpProductRecord::default()
        };
        assert_eq!(bare.title(), "CivicCast Installer");
        assert_eq!(bare.summary(), "user S-1-5-21-1-2-3-1001; CivicCast Installer");
        let e = field_report_evidence(OtherProductState::Absent, OtherProductState::Present);
        assert!(e.explain().starts_with("user-ARP=present [user-ARP HKCU (64-bit view): present [user tester; CivicCast Installer 3.0.0-beta1;"));
        assert!(e.explain().ends_with("wsl-service=present, autostart=absent, transfer-marker=present"), "{}", e.explain());
        // Presence from the distro scan alone has no record to describe.
        let mut distro_only = evidence(
            OtherProductState::Absent,
            OtherProductState::Absent,
            OtherProductState::Present,
            OtherProductState::Present,
        );
        distro_only.observations = vec![ProbeObservation::settled(
            "distro-scan",
            r"HKU\S-1-5-21-1-2-3-1001\...\Lxss\{guid}".to_string(),
            "n/a",
            OtherProductState::Present,
        )];
        assert_eq!(distro_only.present_product(), None);
        assert!(distro_only
            .describe_present_product()
            .starts_with("Setup found another CivicCast product (the CivicCast WSL product) installed on this machine: "));
    }

    /// The three refusals explain themselves with three different texts:
    /// the Present one names the product and never the registry edit; the
    /// Unknown one names the failed read and the registry value; the
    /// Unreadable one names the value and no evidence.
    #[test]
    fn the_three_refusals_have_three_different_details() {
        let present = claim_install_selector_with(
            || SelectorState::Absent,
            || field_report_evidence(OtherProductState::Present, OtherProductState::Absent),
            || Ok(()),
        );
        let unknown = claim_install_selector_with(
            || SelectorState::Absent,
            || {
                let mut e = evidence(
                    OtherProductState::Unknown,
                    OtherProductState::Unknown,
                    OtherProductState::Unknown,
                    OtherProductState::Present,
                );
                e.observations = vec![denied_hive_observation()];
                e
            },
            || Ok(()),
        );
        let unreadable = claim_install_selector_with(
            || SelectorState::Unreadable,
            || unreachable!("no evidence is gathered for an unreadable selector"),
            || Ok(()),
        );
        assert_eq!(present.action, SelectorClaimAction::LeaveOtherProductPresent);
        assert_eq!(unknown.action, SelectorClaimAction::LeaveUnprovable);
        assert_eq!(unreadable.action, SelectorClaimAction::LeaveUnreadable);
        assert!(present.detail.starts_with("Setup found another CivicCast product"));
        assert!(!present.detail.contains("sets HKLM"));
        assert!(unknown.detail.contains("A read failed for a reason other than \"not found\""));
        assert!(unknown.detail.contains("PermissionDenied, os error 5"));
        assert!(unknown.detail.contains("operator sets HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime"));
        assert!(unreadable
            .detail
            .starts_with("HKLM\\SOFTWARE\\CivicCast\\ActiveRuntime exists but could not be read"));
        assert_eq!(unreadable.evidence, None);
        assert_eq!(unreadable.verdict, None);
        for outcome in [&present, &unknown, &unreadable] {
            assert!(outcome.action.is_refusal());
            assert!(!outcome.detail.contains("permissions problem"));
        }
    }
}
