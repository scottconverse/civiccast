# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Native installer product identity (spec D1 / SDR-004).

CivicCast (Native) is the only installer product this repository ships. The
WSL2 lane ("CivicCast Installer", the base tauri.conf.json built WITHOUT the
native overlay) was retired under the owner's "no linux" decision
(2026-08-19); its hook file, nsis-hooks.nsh, is deleted, and the base
config no longer declares any installerHooks of its own.

The native product is still built as a Tauri v2 config OVERLAY in the same
app (``tauri.native.conf.json`` + ``nsis-hooks-bootstrap.nsh``), merged over
the base ``tauri.conf.json`` by ``tauri build --config``, rather than a
forked app directory -- D1's "two products, one codebase" decision predates
the WSL lane's retirement, and the overlay mechanism itself did not change,
only the number of real products that use it.

These tests assert:

1. the native NSIS hook set contains ZERO WSL-touching steps -- a permanent
   regression guard, not a two-product disjointness check: if wsl.exe or
   distro-lifecycle code ever reappeared in the shipped hook set, this
   fails;
2. the EFFECTIVE (deep-merged) native config carries the native product's
   own identity, install mode, and hook file, computed the same way Tauri's
   CLI computes it, so an overlay that silently dropped an override would
   be caught; and
3. the native product installs perMachine (with elevation), per D1.

The base tauri.conf.json is kept only because Tauri's CLI always reads it as
the file a ``--config`` overlay merges on top of -- nothing in this
repository's build scripts or CI workflows ever builds it directly (see
``scripts/build_native_installer.py``'s own ``run_tauri_build()``, which
always passes ``--config``).
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "civiccast" / "apps" / "installer" / "src-tauri"
BASE_CONFIG = INSTALLER / "tauri.conf.json"
NATIVE_CONFIG = INSTALLER / "tauri.native.conf.json"
NATIVE_HOOKS = INSTALLER / "nsis-hooks-bootstrap.nsh"
NATIVE_VERSION_FILE = ROOT / "civiccast" / "_native_version.py"
INSTALLER_MAIN_RS = INSTALLER / "src" / "main.rs"

# Tokens that only ever belonged to the retired WSL product's lifecycle. Any
# of these appearing in the native hook set would mean WSL distro-lifecycle
# code (terminating/unregistering the distro, deleting its autostart) had
# come back -- the SDR-004 hazard this test cluster exists to catch.
WSL_ONLY_TOKENS = (
    "wsl.exe",
    "wsl --",
    "--unregister",
    "--terminate",
    "CivicCast-Ubuntu",
    "Sysnative",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Replicate Tauri v2's ``--config`` deep merge: objects merge recursively,
    scalars and arrays are replaced by the overlay. Used to compute the EFFECTIVE
    native config so an override that is *missing* from the overlay (and would
    therefore inherit the base config's value) is caught rather than silently
    trusted."""
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _nsis(config: dict) -> dict:
    return config.get("bundle", {}).get("windows", {}).get("nsis", {})


def _binary_name(config: dict) -> str:
    """The installed executable base name Tauri derives: mainBinaryName if set,
    else the productName."""
    return config.get("mainBinaryName") or config.get("productName", "")


def test_native_overlay_and_hook_file_exist() -> None:
    assert NATIVE_CONFIG.is_file(), f"missing native Tauri overlay: {NATIVE_CONFIG}"
    assert NATIVE_HOOKS.is_file(), f"missing native NSIS hook set: {NATIVE_HOOKS}"
    assert NATIVE_HOOKS.read_text(encoding="utf-8").strip(), "native hook file is empty"


def test_native_hook_set_contains_zero_wsl_touching_steps() -> None:
    hooks = NATIVE_HOOKS.read_text(encoding="utf-8")
    lowered = hooks.lower()
    offenders = [token for token in WSL_ONLY_TOKENS if token.lower() in lowered]
    assert not offenders, (
        f"native NSIS hook set must contain ZERO WSL-touching steps (SDR-004); found: {offenders}"
    )
    # It must also not stop the WSL product's process by name.
    assert "civiccast-installer.exe" not in lowered, (
        "native hooks must not target the WSL product's executable"
    )


def test_effective_native_config_declares_permachine_elevation() -> None:
    effective = _deep_merge(_load(BASE_CONFIG), _load(NATIVE_CONFIG))
    assert _nsis(effective).get("installMode") == "perMachine", (
        "native product installs perMachine with elevation (D1); "
        "an overlay that omits installMode would inherit the base config's default"
    )


def test_effective_native_config_uses_its_own_hook_set() -> None:
    """The SDR-004 inheritance footgun: Tauri deep-merges the overlay OVER the
    base config, so a missing installerHooks override would silently keep
    whatever (if anything) the base config declares. Assert the EFFECTIVE
    hook path is the native file explicitly."""
    effective = _deep_merge(_load(BASE_CONFIG), _load(NATIVE_CONFIG))
    assert _nsis(effective).get("installerHooks") == "nsis-hooks-bootstrap.nsh"


def test_base_config_declares_no_installer_hooks_of_its_own() -> None:
    """The base tauri.conf.json is never built directly by anything in this
    repository (see module docstring) -- it exists only as the file Tauri's
    CLI always merges a ``--config`` overlay on top of. It used to point
    installerHooks at the retired WSL product's nsis-hooks.nsh; that file
    and the reference to it are both gone, so a bare ``tauri build`` (no
    ``--config``, which nothing here runs) now produces an installer with
    no custom hooks at all rather than the retired WSL lane's."""
    base = _load(BASE_CONFIG)
    assert "installerHooks" not in _nsis(base), (
        "the base tauri.conf.json must not declare installerHooks -- the file it "
        "used to point at (nsis-hooks.nsh) is deleted"
    )
    assert not (INSTALLER / "nsis-hooks.nsh").exists(), (
        "the retired WSL product's nsis-hooks.nsh must stay deleted"
    )


def test_native_uninstall_preflight_is_the_only_gate_before_native_taskkill() -> None:
    """WP2: probe in Rust, fail closed, and defer any selector clear to POST."""
    hooks = NATIVE_HOOKS.read_text(encoding="utf-8")
    preuninstall = hooks.split("!macro NSIS_HOOK_PREUNINSTALL", 1)[1].split("!macroend", 1)[0]
    postuninstall = hooks.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[0]

    assert "--civiccast-native-uninstall-preflight" in preuninstall
    assert preuninstall.index("--civiccast-native-uninstall-preflight") < preuninstall.index(
        "taskkill.exe"
    )
    assert 'DeleteRegValue HKLM "Software\\CivicCast" "ActiveRuntime"' not in preuninstall
    marker = "NativeUninstallPostclearPending"
    selector_delete = 'DeleteRegValue HKLM "Software\\CivicCast" "ActiveRuntime"'
    marker_delete = 'DeleteRegValue HKLM "Software\\CivicCast" "NativeUninstallPostclearPending"'
    assert marker in postuninstall
    assert selector_delete in postuninstall
    assert marker_delete in postuninstall
    assert postuninstall.index(
        'ReadRegStr $R1 HKLM "Software\\CivicCast" "ActiveRuntime"'
    ) < postuninstall.index(selector_delete)
    assert postuninstall.index(selector_delete) < postuninstall.index(marker_delete)


def test_native_uninstall_preflight_uses_64bit_registry_probes_and_fail_closed_tokens() -> None:
    cargo = (INSTALLER / "Cargo.toml").read_text(encoding="utf-8")
    source = (INSTALLER / "src" / "native_uninstall.rs").read_text(encoding="utf-8")
    main = (INSTALLER / "src" / "main.rs").read_text(encoding="utf-8")

    assert 'winreg = "0.55"' in cargo
    for token in (
        "KEY_WOW64_64KEY",
        "ActiveRuntime",
        "CivicCast Installer",
        "NativeUninstallPostclearPending",
        "allow-sole-postclear",
        "unreadable",
        "unknown",
    ):
        assert token in source
    assert "--civiccast-native-uninstall-preflight" in main

    # A bare `assert "73" in main` would pass on ANY coincidental occurrence
    # of those two characters anywhere in the file -- and main.rs has one:
    # the WSL bootstrap helper's `exit 73` for an unrelated "setup already
    # running" mutex check (a completely different subsystem), plus main.rs
    # never even spells the preflight's real exit code as the bare digits --
    # it maps through the named constant `native_uninstall::SOLE_POSTCLEAR_
    # EXIT_CODE`. Pin the SYMBOLIC reference inside the actual preflight
    # branch instead, and pin that symbol's numeric value against its one
    # definition in native_uninstall.rs (`source`, read above), so moving or
    # renaming the real usage -- or drifting the two apart -- fails this
    # test even though a bare "73" substring would still be present.
    preflight_branch = main.split(
        "fn run_native_uninstall_preflight_cli(args: &[String]) -> Option<i32> {",
        1,
    )[1].split("\nfn main()", 1)[0]
    assert "native_uninstall::SOLE_POSTCLEAR_EXIT_CODE" in preflight_branch, (
        "the AllowSolePostclear branch of run_native_uninstall_preflight_cli "
        "must map through the named constant, not a bare exit-code literal"
    )
    constant_definition = re.search(
        r"pub const SOLE_POSTCLEAR_EXIT_CODE:\s*i32\s*=\s*(\d+);", source
    )
    assert constant_definition is not None, (
        "SOLE_POSTCLEAR_EXIT_CODE definition not found in native_uninstall.rs"
    )
    assert constant_definition.group(1) == "73", (
        "SOLE_POSTCLEAR_EXIT_CODE must stay pinned at 73 (D1 exit-code contract)"
    )


def test_native_uninstall_preflight_probes_other_users_via_hkey_users() -> None:
    """WP2 elevated-uninstall fix: an elevated per-machine uninstall's HKCU
    resolves to the elevating admin's hive, so a per-user WSL install owned
    by a DIFFERENT (currently logged-on) user must still be found via
    HKEY_USERS, and the probe must document/skip the hives that are not real
    user profiles while staying fail-closed (never parsing reg.exe output)."""
    source = (INSTALLER / "src" / "native_uninstall.rs").read_text(encoding="utf-8")

    for token in (
        "HKEY_USERS",
        "S-1-5-18",
        "S-1-5-19",
        "S-1-5-20",
        ".DEFAULT",
        "_CLASSES",
        "KEY_WOW64_32KEY",
        "loaded",
    ):
        assert token in source, f"expected {token!r} in native_uninstall.rs"

    # Still direct winreg calls only: RegKey::predef(HKEY_USERS) is the new
    # enumeration root, alongside the pre-existing HKEY_CURRENT_USER probe.
    assert "RegKey::predef(HKEY_USERS)" in source
    assert "RegKey::predef(HKEY_CURRENT_USER)" in source


def test_ownership_recovery_document_installer_exit_codes_are_pinned_to_the_nsis_defines() -> None:
    """Round 4 (hostile review of round 3): OWNERSHIP-RECOVERY.md said
    "installer exit 127" for BOTH refusals, but the exit-87 arm aborts the
    installer with CIVICCAST_EXIT_D4_OTHER_PRODUCT (135). The document is
    generated by Rust from constants; those constants must equal the NSIS
    defines, the CLI->installer mapping must be the one the hook's arms
    actually implement, and the generator must not hardcode a number."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    registration = (INSTALLER / "src" / "native_service_registration.rs").read_text(
        encoding="utf-8"
    )

    def nsis_define(name: str) -> int:
        match = re.search(rf"^!define {name}\s+(\d+)$", hooks_text, re.MULTILINE)
        assert match is not None, f"{name} not defined in nsis-hooks-bootstrap.nsh"
        return int(match.group(1))

    def rust_const(name: str) -> int:
        match = re.search(rf"^pub const {name}: i32 = (\d+);$", registration, re.MULTILINE)
        assert match is not None, f"{name} not found in native_service_registration.rs"
        return int(match.group(1))

    # CLI code -> the NSIS define its arm aborts with, read from the hook.
    step = _slice_macro(hooks_text, "NSIS_HOOK_POSTINSTALL")
    arm_87 = step.split("${ElseIf} $0 == 87", 1)[1].split("${ElseIf} $0 == 85", 1)[0]
    arm_85 = step.split("${ElseIf} $0 == 85", 1)[1].split("${Else}", 1)[0]
    assert "CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_OTHER_PRODUCT}" in arm_87
    assert "CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP}" in arm_85

    assert rust_const("SELECTOR_UNPROVABLE_EXIT_CODE") == 85
    assert rust_const("OTHER_PRODUCT_PRESENT_EXIT_CODE") == 87
    assert rust_const("SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE") == nsis_define(
        "CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP"
    )
    assert rust_const("OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE") == nsis_define(
        "CIVICCAST_EXIT_D4_OTHER_PRODUCT"
    )
    assert rust_const("OTHER_PRODUCT_PRESENT_INSTALLER_EXIT_CODE") != rust_const(
        "SELECTOR_UNPROVABLE_INSTALLER_EXIT_CODE"
    )

    # The generator interpolates the mapped constant; a literal in the
    # format string is exactly the drift this test exists to stop.
    generator = registration.split("pub fn ownership_recovery_document(", 1)[1].split("\n}\n", 1)[0]
    assert "installer exit {installer_exit_code}" in generator
    assert not re.search(r"installer exit \d", generator), (
        "OWNERSHIP-RECOVERY.md must not hardcode an installer exit code"
    )


def test_install_ownership_claim_ports_the_python_guards_wsl_probes_exactly() -> None:
    """beta.5.1: when the per-user WSL ARP probe cannot read a hive, the
    install-time ownership claim corroborates with machine-wide ARP, the
    Lxss distro registration scan and the WSL service presence -- the SAME
    checks the Python runtime guard (`civiccast.native.win_probes`) trusts.
    The Rust port must look for exactly what Python looks for, or the two
    sides can disagree about the same machine."""
    from civiccast.native import runtime_guard, win_probes

    source = (INSTALLER / "src" / "native_uninstall.rs").read_text(encoding="utf-8")

    def rust_str(name: str) -> str:
        match = re.search(rf'pub const {name}: &str = r?"([^"]+)";', source)
        assert match is not None, f"{name} not found in native_uninstall.rs"
        return match.group(1)

    assert rust_str("WSL_DISTRO_NAME") == runtime_guard.WSL_DISTRO_NAME
    assert rust_str("WSL_LXSS_KEY_PATH") == win_probes.WSL_LXSS_KEY_PATH
    services = re.search(r"pub const WSL_SERVICE_NAMES: \[&str; 2\] = \[([^\]]+)\];", source)
    assert services is not None
    assert tuple(token.strip().strip('"') for token in services.group(1).split(",")) == tuple(
        win_probes.WSL_SERVICE_NAMES
    )
    assert "pub const ERROR_SERVICE_DOES_NOT_EXIST: i32 = 1060;" in source
    assert win_probes._ERROR_SERVICE_DOES_NOT_EXIST == 1060
    # Round 3: the inert-leftover rule probes the WSL product's autostart
    # Run value -- the SAME key and value name the Python guard owns.
    assert rust_str("WSL_AUTOSTART_RUN_KEY") == runtime_guard.RUN_KEY_PATH
    assert rust_str("WSL_AUTOSTART_RUN_VALUE") == runtime_guard.RUN_VALUE_NAME
    assert "fn probe_wsl_autostart_observed" in source
    assert "fn probe_native_transfer_marker_observed" in source

    # Machine-wide ARP: HKLM, both WOW64 views, direct winreg -- no reg.exe.
    assert "fn probe_wsl_machine_arp_observed" in source
    assert "RegKey::predef(HKEY_LOCAL_MACHINE)" in source
    # The corroboration is a pure, unit-tested fold consumed by the claim.
    assert "pub fn corroborate_wsl_product_state" in source
    assert "probe_wsl_presence_evidence" in source
    assert "reg.exe" not in source.replace("localized `reg.exe`", "")

    # The SCM probe runs under the same hard deadline as the Python guard's
    # `_run_probe_argv` (A2_TIMEOUT_SECONDS), from the absolute System32 path
    # win_probes.SC_EXE pins, and never through an open-ended `.output()`.
    deadline = re.search(r"pub const SC_QUERY_TIMEOUT_SECONDS: u64 = (\d+);", source)
    assert deadline is not None
    assert float(deadline.group(1)) == runtime_guard.A2_TIMEOUT_SECONDS
    assert "fn wait_with_deadline(" in source
    assert "Command::new(sc_exe_path())" in source
    assert 'std::process::Command::new("sc.exe")' not in source

    # Deliberate divergence, pinned: when enumerating HKEY_USERS the Rust
    # port skips `.DEFAULT` and the well-known service SIDs (SYSTEM
    # S-1-5-18, LOCAL SERVICE S-1-5-19, NETWORK SERVICE S-1-5-20) in
    # addition to the `*_Classes` shadows Python's scan_registered_distros
    # skips. None of those is a real user profile or a possible per-user WSL
    # owner, so skipping them cannot hide a WSL install; it only removes
    # reads that would otherwise fail and poison the fold with Unknown.
    assert "fn should_skip_users_subkey(name: &str) -> bool" in source
    assert (
        'const WELL_KNOWN_SERVICE_SIDS: [&str; 3] = ["S-1-5-18", "S-1-5-19", "S-1-5-20"];' in source
    )
    skip_body = source.split("fn should_skip_users_subkey(name: &str) -> bool {", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert 'upper == ".DEFAULT"' in skip_body
    assert 'upper.ends_with("_CLASSES")' in skip_body
    assert "WELL_KNOWN_SERVICE_SIDS.iter().any(|sid| upper == *sid)" in skip_body


# ---------------------------------------------------------------------------
# WP2 hook-migration (2026-07-30): nsis-hooks-native.nsh's POSTINSTALL D2/D4/
# pack-delivery chain was folded into nsis-hooks-bootstrap.nsh (the ONE live
# native hook file per test_effective_native_config_uses_its_own_hook_set_
# not_the_wsl_hooks above) and nsis-hooks-native.nsh was retired. These tests
# pin the resulting bootstrap-ordered chain and the retirement itself.
# ---------------------------------------------------------------------------

NATIVE_NATIVE_HOOKS_RETIRED = INSTALLER / "nsis-hooks-native.nsh"


def test_nsis_hooks_native_is_retired() -> None:
    """The stranded WP-6-era hook file must be gone, not merely unreferenced --
    its functional POSTINSTALL content now lives in nsis-hooks-bootstrap.nsh."""
    assert not NATIVE_NATIVE_HOOKS_RETIRED.exists(), (
        "nsis-hooks-native.nsh should have been retired (deleted) once its "
        "POSTINSTALL chain was migrated into nsis-hooks-bootstrap.nsh"
    )


def _postinstall_block(hooks_text: str) -> str:
    return hooks_text.split("!macro NSIS_HOOK_POSTINSTALL", 1)[1].split("!macroend", 1)[0]


def _preinstall_block(hooks_text: str) -> str:
    return hooks_text.split("!macro NSIS_HOOK_PREINSTALL", 1)[1].split("!macroend", 1)[0]


def _slice_macro(hooks_text: str, macro_name: str) -> str:
    """The body of one `!macro <name> ... !macroend` block.

    <installer-path-audit MA-34/MA-35> The uninstall macros needed the same
    treatment as the install ones once uninstall refusals gained their own
    exit codes.
    """
    return hooks_text.split(f"!macro {macro_name}", 1)[1].split("!macroend", 1)[0]


def _slice(text: str, start: str, end: str) -> str:
    """Return the text between two ordered markers, excluding the markers."""
    start_at = text.index(start) + len(start)
    end_at = text.index(end, start_at)
    return text[start_at:end_at]


def test_bootstrap_postinstall_chain_orders_pack_verification_activation_d3_and_d4() -> None:
    """Pins the migrated bootstrap-native ordering: packs deliver the runtime
    bytes, so D2 re-verification precedes signed station activation, which
    establishes the strict contract before D3 health checks the candidate
    runtime; D3 still precedes D4 provisioning and service/firewall
    registration."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    stage_packs = "--civiccast-stage-packs"
    verify_pack_tree = "--civiccast-verify-pack-tree"
    provision = "--civiccast-provision"
    activate_station = "--civiccast-activate-station"
    register_service = "--civiccast-register-native-service"
    register_firewall = "--civiccast-register-native-firewall-rule"

    for token in (
        stage_packs,
        verify_pack_tree,
        activate_station,
        provision,
        register_service,
        register_firewall,
    ):
        assert token in postinstall, f"expected {token!r} in nsis-hooks-bootstrap.nsh POSTINSTALL"

    assert postinstall.index(stage_packs) < postinstall.index(verify_pack_tree)
    verify_last = postinstall.rindex(verify_pack_tree)
    activation_step = '!insertmacro CIVICCAST_STEP "step d4-activate-station: begin"'
    d3_step = '!insertmacro CIVICCAST_STEP "step d3-engine: begin'
    assert verify_last < postinstall.index(activation_step)
    assert postinstall.index(activation_step) < postinstall.index(d3_step)
    assert postinstall.index(d3_step) < postinstall.index(provision)
    # K1 activation writes station-set.json and activation-self-test.json at
    # $INSTDIR before D3's maintenance health check loads the candidate runtime.
    assert postinstall.index(activate_station) < postinstall.index(d3_step)
    assert postinstall.index(provision) < postinstall.index(register_service)
    assert postinstall.index(register_service) < postinstall.index(register_firewall)


def test_bootstrap_postinstall_activates_the_flat_station_and_fails_loud_on_error() -> None:
    """K1 fix, "wired not just defined" pattern: the new
    `--civiccast-activate-station` subcommand (main.rs::
    run_native_flat_activation_cli) must actually be INVOKED from the
    POSTINSTALL chain via nsExec -- not merely mentioned in a comment or a
    `--help` string -- and its failure branch must route through
    CIVICCAST_FAIL (abort), never a bare CIVICCAST_ALERT that lets the chain
    continue. See test_bootstrap_postinstall_every_failure_branch_actually_
    fails_the_install for why a bare alert here would reintroduce AUDIT-001's
    "install reports success on a broken machine" shape."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    executable = "\n".join(
        line for line in postinstall.splitlines() if not line.strip().startswith(";")
    )

    assert (
        'nsExec::ExecToStack \'"$INSTDIR\\CivicCast Native.exe" --civiccast-activate-station'
        in (executable)
    ), "the activation CLI output must be captured for the durable failure breadcrumb"
    assert (
        '--install-root "$INSTDIR"'
        in executable.split("--civiccast-activate-station", 1)[1].split("\n", 1)[0]
    ), "the activation invocation must target $INSTDIR (flat layout), matching its neighbors"

    activation_block = _slice(
        postinstall,
        '!insertmacro CIVICCAST_STEP "step d4-activate-station: begin"',
        "  ; UPGRADE-VS-FRESH ROUTING",
    )
    invocations = [
        line for line in executable.splitlines() if "--civiccast-activate-station" in line
    ]
    assert len(invocations) == 2, "both kit and embedded activation routes must remain"
    assert all("nsExec::ExecToStack" in line for line in invocations), (
        "both activation routes must capture their bounded child output"
    )
    assert activation_block.count("Pop $0\n  Pop $1") == 2, (
        "each activation call must pop its exit code and captured child output"
    )
    nonzero_arm = activation_block.split("${If} $0 != 0", 1)[1].split("${EndIf}", 1)[0]
    assert (
        '!insertmacro CIVICCAST_STEP "step d4-activate-station: child reported: $1"' in nonzero_arm
    ), "activation child detail must reach install-progress.log before failure routing"
    assert "${CIVICCAST_EXIT_D4_ACTIVATION}" in activation_block
    assert "!insertmacro CIVICCAST_FAIL" in activation_block, (
        "a failed station activation must abort the install through CIVICCAST_FAIL, "
        "never a silent skip -- a silent skip is the exact shape that produced K1"
    )
    assert "!insertmacro CIVICCAST_ALERT" not in activation_block, (
        "no bare CIVICCAST_ALERT on the activation failure path -- it must abort, not just notify"
    )


def test_bootstrap_postinstall_resolves_the_station_index_kit_first_then_embedded() -> None:
    """2026-09-02 owner decision: a DOWNLOAD-ONLY install/upgrade of setup.exe
    alone must still find a signed station index, because the installer now
    EMBEDS one (bundle.resources -> $INSTDIR\\station\\station-index.json, pinned
    by test_native_bootstrap_embeds_only_the_two_tiny_station_resources below).

    The resolution order is load-bearing in BOTH directions:

      (a) $EXEDIR\\station\\station-index.json must be tried FIRST, so an
          air-gapped station installing from the USB kit
          (assemble-native-beta-kit's layout) behaves exactly as it did before
          this change -- the kit's index wins, and its component packs sit
          beside it in the same media directory that
          native_distribution.rs::acquire_station_distribution derives from the
          index path;
      (b) $INSTDIR\\station\\station-index.json is the fallback, never the
          preference -- preferring it would silently ignore the bundle an
          operator deliberately carried to the machine on a stick.

    And the fail-closed branch must survive: only when NEITHER path exists.
    A silent skip here is the exact shape that produced K1."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    executable = "\n".join(
        line for line in postinstall.splitlines() if not line.strip().startswith(";")
    )

    exedir_import = '--civiccast-import-station "$EXEDIR\\station\\station-index.json"'
    instdir_import = '--civiccast-import-station "$INSTDIR\\station\\station-index.json"'
    assert exedir_import in executable, "the USB-kit side-load path must still be imported"
    assert instdir_import in executable, (
        "the embedded copy at $INSTDIR\\station must be a real import path, not just a comment"
    )
    assert executable.index(exedir_import) < executable.index(instdir_import), (
        "the kit's own station bundle must win over the embedded index -- an "
        "air-gapped station that carried a bundle to the machine must keep "
        "using it"
    )

    # The existence checks that actually implement the order, not just two
    # invocations sitting near each other.
    exedir_probe = 'IfFileExists "$EXEDIR\\station\\station-index.json"'
    instdir_probe = 'IfFileExists "$INSTDIR\\station\\station-index.json"'
    assert exedir_probe in executable
    assert instdir_probe in executable
    assert executable.index(exedir_probe) < executable.index(instdir_probe)

    # Both sources must be observable in install-progress.log -- the whole
    # point of naming which one was used is post-mortem diagnosis of an
    # install nobody watched.
    assert '!insertmacro CIVICCAST_STEP "step d4-activate-station: source EXEDIR' in executable
    assert '!insertmacro CIVICCAST_STEP "step d4-activate-station: source INSTDIR' in executable

    activation_block = _slice(
        postinstall,
        '!insertmacro CIVICCAST_STEP "step d4-activate-station: begin"',
        "  ; UPGRADE-VS-FRESH ROUTING",
    )
    # <installer-path-audit MA-08> The count moved from 2 to 6, deliberately.
    # `run_native_flat_activation_cli` emits FIVE distinct exit codes -- 64
    # (arguments), 65 (render), 66 (acquisition), 67 (activation/self-test),
    # 78 (embedded pack trust) -- and this block used to collapse all of them
    # into ONE sentence about the station folder and the pack cache. Correct
    # for 66-with-a-cache-miss; wrong for a failed self-test (the packs were
    # fine), for 78 (a BUILD defect, not a machine problem), and for 64/65 (an
    # installer-authoring bug). So: neither-index-present, plus one arm each
    # for 67 / 66 / 78 / 64+65, plus the catch-all.
    assert activation_block.count("!insertmacro CIVICCAST_FAIL") == 6, (
        "every activation exit code with its own operator remedy needs its own "
        "fail-closed branch: neither index present, 67, 66, 78, 64/65, and the "
        "catch-all"
    )
    for code in ("67", "66", "78", "64"):
        assert f"$0 == {code}" in activation_block, (
            f"activation exit {code} must have its own branch -- this file's own header "
            "says the exit code is the only signal a support log carries about WHICH "
            "step failed"
        )
    assert "${CIVICCAST_EXIT_D4_ACTIVATION}" in activation_block
    assert "!insertmacro CIVICCAST_ALERT" not in activation_block


def test_native_bootstrap_embeds_only_the_bounded_whistle_station_pack() -> None:
    """The embedded Whistle pack must not become a hole in the no-large-model
    payload rule. Exactly four resources are allowed; the three station
    entries must land under `station/` so the hook's $INSTDIR\\station\\...
    probe finds them (Tauri maps resource VALUES relative to the install root).
    The builder separately verifies the Whistle pack's signed-index identity
    and strict 25 MB size ceiling."""
    resources = _deep_merge(_load(BASE_CONFIG), _load(NATIVE_CONFIG))["bundle"]["resources"]

    assert resources == {
        "resources/vc_redist.x64.exe": "vc_redist.x64.exe",
        "resources/station/station-index.json": "station/station-index.json",
        "resources/station/core.ccpack": "station/core.ccpack",
        "resources/station/captions-whistle.ccpack": "station/captions-whistle.ccpack",
    }, "bundle.resources must carry the VC++ prerequisite plus the exact station embed allowlist"

    # Only the tiny placeholder `core` pack and bounded local Whistle pack may
    # be embedded. Other component packs remain in the kit or per-SHA cache.
    embedded_packs = [value for value in resources.values() if value.endswith(".ccpack")]
    assert (
        set(embedded_packs)
        == {
            "station/core.ccpack",
            "station/captions-whistle.ccpack",
        }
        and len(embedded_packs) == 2
    ), (
        f"only the placeholder `core` and bounded Whistle packs may be embedded, found {embedded_packs}"
    )


def test_bootstrap_postinstall_verifies_the_extracted_pack_tree_not_the_retired_embedded_runtime_path() -> (
    None
):
    """ADAPTATION pin: the retired file's D2 checks re-verified the WP-6
    embedded $INSTDIR\\runtime / $INSTDIR\\native-runtime trees against an
    in-tree manifest file. Neither path is ever laid down by the bootstrap
    build (bundle.resources carries only station activation resources and the
    VC++ prerequisite), so that exact
    check must NOT reappear here -- it would unconditionally fail-abort every
    install. The adapted check re-verifies the pack-derived tree instead."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "--civiccast-verify-install-tree" not in postinstall, (
        "the WP-6 embedded-runtime D2 check must not be migrated into the "
        "bootstrap hook -- it can never pass in this architecture"
    )
    # NOTE (WP2 D3 rehoming, 2026-07-30): the bare substring
    # `"$INSTDIR\runtime\python.exe"` is now legitimately present -- the D3
    # engine invocation below shells out to it as the bridged interpreter
    # (native-app-payload pack extraction now lays $INSTDIR\runtime down, see
    # the file header). What must never reappear is specifically the WP-6
    # ${FileExists} defensive-presence GATE around that path, not the bare
    # path string, which the D3 invocation legitimately needs.
    assert '${IfNot} ${FileExists} "$INSTDIR\\runtime\\python.exe"' not in postinstall, (
        "the WP-6 defensive payload-presence gate (the FileExists check) "
        "must not be migrated -- it was WP-6-embedded-only and D3 already "
        "fails loud on a missing interpreter via its own nsExec exit code"
    )
    assert "$INSTDIR\\packs\\native-server-binaries.ccpack" in postinstall
    assert "$INSTDIR\\packs\\native-server-binaries\\payload" in postinstall


def test_bootstrap_postinstall_every_failure_branch_actually_fails_the_install() -> None:
    """AUDIT-001 (Blocker): the previous version of this test asserted the OLD
    convention -- SetErrors + alert + `Goto civiccast_bootstrap_postinstall_done`
    -- and passed happily while a failed install still reported SUCCESS. NSIS
    fires `.onInstFailed` only on a failed extraction or an explicit `Abort`;
    `SetErrors` is not a trigger, and Tauri's generated installer.nsi runs
    `.onInstSuccess` unconditionally. So the old convention was the defect, and
    a test pinning it was pinning the defect.

    Every failure branch now routes through CIVICCAST_FAIL, which sets a
    step-identifying error level and aborts. The unwind label is gone: a
    branch that merely jumps to a shared label is exactly the shape that let a
    broken machine finish on the wizard's completion page.

    Measured behavior this pins (see
    .agent-runs/native-windows/ws5-installer/evidence/nsis-errorlevel-probe/):
    SetErrorLevel alone still runs .onInstSuccess; Abort alone gives the
    generic exit code 2; the two together give the custom code AND
    .onInstFailed.

    SHARPER RULE (CRITICAL fix, 2026-07-30 adversarial review, D3
    clean-rollback): the D3 exit==10 branch needs to tell the operator an
    upgrade did not take effect on a path that deliberately CONTINUES (the
    machine is healthy, just still on the old version -- not a failure), so
    a bare CIVICCAST_ALERT there would be indistinguishable from the
    AUDIT-001 shape this test exists to catch. Rather than weaken this
    invariant with a carve-out, the fix gives that case its own macro,
    CIVICCAST_NOTICE -- structurally identical delivery to CIVICCAST_ALERT
    but with no SetErrorLevel/Abort of its own, so it is incapable of masking
    a failure. The rule stays absolute: POSTINSTALL may contain NO bare
    CIVICCAST_ALERT anywhere. Failures route through CIVICCAST_FAIL; non-
    failure notices route through CIVICCAST_NOTICE."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(hooks_text)

    assert "!macro CIVICCAST_FAIL CODE TEXT" in hooks_text
    fail_macro = hooks_text.split("!macro CIVICCAST_FAIL CODE TEXT", 1)[1].split("!macroend", 1)[0]
    assert "SetErrorLevel ${CODE}" in fail_macro
    assert "Abort" in fail_macro
    assert "!insertmacro CIVICCAST_ALERT" in fail_macro

    assert postinstall.count("!insertmacro CIVICCAST_FAIL") >= 9, (
        "every POSTINSTALL failure branch must fail loud through CIVICCAST_FAIL"
    )
    # Prose may still discuss the retired label; executable lines may not use
    # it. NSIS comments start with ';'.
    executable = "\n".join(
        line for line in postinstall.splitlines() if not line.strip().startswith(";")
    )
    assert "civiccast_bootstrap_postinstall_done" not in executable, (
        "the shared unwind label is the AUDIT-001 shape: a failure branch that "
        "jumps past the InstalledVersion write and lets the install finish"
    )

    # CIVICCAST_NOTICE must be structurally incapable of being a failure
    # path: no SetErrorLevel, no Abort. Otherwise it would just be
    # CIVICCAST_ALERT under a different name and this whole invariant would
    # be trivially defeatable by routing a real failure through it instead.
    assert "!macro CIVICCAST_NOTICE TEXT" in hooks_text, (
        "expected a dedicated CIVICCAST_NOTICE macro for non-failure "
        "operator notices on a path that continues by design (D3 clean "
        "rollback)"
    )
    notice_macro = hooks_text.split("!macro CIVICCAST_NOTICE TEXT", 1)[1].split("!macroend", 1)[0]
    assert "SetErrorLevel" not in notice_macro, (
        "CIVICCAST_NOTICE must never set an error level -- that would make "
        "it capable of the AUDIT-001 shape it exists to avoid"
    )
    assert "Abort" not in notice_macro, (
        "CIVICCAST_NOTICE must never abort -- it is for a path that continues by design"
    )

    # The invariants that actually close the hole. An adversarial pass
    # against an earlier version of this test rewrote ONE branch back to
    # `SetErrorLevel <code>` + a bare alert -- no Abort -- and every assertion
    # here still passed, because a >= count survives losing one branch and the
    # reintroduced shape used SetErrorLevel rather than SetErrors. A silent
    # install with that branch live exits nonzero but still runs
    # .onInstSuccess, which is the Blocker wearing a different hat.
    #
    # 1. POSTINSTALL reports failures ONLY through CIVICCAST_FAIL. A bare
    #    CIVICCAST_ALERT here means a branch that reports and keeps going --
    #    the rule is absolute, with NO carve-out (the D3 clean-rollback
    #    notice now routes through CIVICCAST_NOTICE instead, checked above
    #    and again below).
    for line in executable.splitlines():
        assert "!insertmacro CIVICCAST_ALERT" not in line, (
            "POSTINSTALL must report failures through CIVICCAST_FAIL (which aborts) "
            "or non-failure notices through CIVICCAST_NOTICE, "
            f"never a bare CIVICCAST_ALERT that lets the chain continue: {line.strip()!r}"
        )
    # 2. Any error level set in POSTINSTALL is immediately followed by an
    #    Abort. Setting a code without aborting is the exact measured shape
    #    that still runs .onInstSuccess.
    postinstall_lines = [line.strip() for line in executable.splitlines() if line.strip()]
    for index, line in enumerate(postinstall_lines):
        if line.startswith("SetErrorLevel"):
            following = postinstall_lines[index + 1] if index + 1 < len(postinstall_lines) else ""
            assert following.startswith("Abort"), (
                "SetErrorLevel without an immediately following Abort still runs "
                f".onInstSuccess (measured); found {line!r} followed by {following!r}"
            )
    # 3. CIVICCAST_NOTICE must not be reachable anywhere in POSTINSTALL.
    #
    #    RETARGETED (Gate A run 33681670855, 2026-09-02). Chain M3 (F-03) had
    #    given the D3 clean-rollback path (exit 10) its own non-failure
    #    CIVICCAST_NOTICE dialog, raised at the end of the macro from read
    #    machine state, because that path deliberately CONTINUED past the
    #    engine's rollback -- the D3 engine's own database work reverted
    #    cleanly, but setup itself kept going and finished the install.
    #
    #    Gate A run 33681670855 (kit 7971815, beta.2 -> beta.3, real hardware)
    #    proved that "continue" is unsafe under the flat installer layout this
    #    bootstrap always uses (--flat-installer-layout, see the D3 engine
    #    invocation above): adapt_flat_installer_layout
    #    (civiccast/native/upgrade/seams.py) makes the engine's
    #    read_junction/lay_tree/flip_junction seams no-ops over the single
    #    "$INSTDIR\\runtime" tree the bootstrap already extracted the NEW
    #    payload into before D3 ran, so a clean engine-level rollback leaves
    #    NEW code on disk over an UNMIGRATED database. Continuing into D4 let
    #    that mismatched pair get a registered, started service. The fix
    #    retires the notice-and-continue path entirely: exit 10 now fails the
    #    whole install via CIVICCAST_FAIL (${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT}),
    #    the same as every other D3 failure code, and CIVICCAST_NOTICE has no
    #    remaining caller in POSTINSTALL.
    assert "${ElseIf} $0 == 10" in executable
    assert "${ElseIf} $0 == 20" in executable
    assert "${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT}" in executable, (
        "D3 exit 10 must fail the install under a distinct exit code -- the flat "
        "installer layout this bootstrap always uses cannot honor what a rollback "
        "promises (see the Gate A run 33681670855 comment above the exit==10 branch)"
    )
    d3_arm = executable.split("${ElseIf} $0 == 10", 1)[1].split("${ElseIf} $0 == 20", 1)[0]
    assert "!insertmacro CIVICCAST_FAIL" in d3_arm, (
        "exit 10 must abort through CIVICCAST_FAIL, exactly like every other D3 failure "
        "code -- there is no more non-failure continuation path for a clean rollback "
        "under the flat installer layout"
    )
    notice_uses = executable.count("!insertmacro CIVICCAST_NOTICE")
    assert notice_uses == 0, (
        "CIVICCAST_NOTICE must have no remaining caller in POSTINSTALL -- the D3 "
        "clean-rollback path it existed for now fails closed via CIVICCAST_FAIL instead "
        "(Gate A run 33681670855); a reintroduced use needs its own justification"
    )
    assert '$R4 == "1"' not in executable, (
        "the retired $R4 clean-rollback latch/report arm must not reappear -- the "
        "InstalledVersion write at the end of POSTINSTALL is unconditional now, reachable "
        "only by a fully successful chain since every failure branch above it aborts"
    )


def test_bootstrap_postinstall_failure_exit_codes_are_distinct_and_reserved() -> None:
    """Each failure branch must carry its OWN process exit code: in a silent
    install there is no dialog, so the exit code is the only signal a
    deployment tool receives, and "something failed" is not actionable. The
    codes also must not collide with the product CLI contract codes that
    travel through nsExec as $0 (40, 70, 73, 75, 76, 79, 81) or with the D3
    engine's phase codes (0/10/20/30), or a reader cannot tell whose code they
    are looking at.

    UPDATE (install-only-refusal WP, 2026-07-30): the reserved band this test
    pins is no longer POSTINSTALL-exclusive -- CIVICCAST_EXIT_INSTALL_OVER_EXISTING
    (120) is raised from NSIS_HOOK_PREINSTALL (the refusal must fire before any
    destructive PREINSTALL step, so it can never live in POSTINSTALL, which
    only ever runs once install-time construction has already happened). The
    "no orphan definition" check below now accepts a code used in EITHER
    chain; the distinctness/reservation checks are unchanged and still cover
    every CIVICCAST_EXIT_* definition in the file."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")

    codes = {
        name: int(value)
        for name, value in re.findall(
            r"^!define (CIVICCAST_EXIT_[A-Z0-9_]+)\s+(\d+)$",
            hooks_text,
            re.MULTILINE,
        )
    }

    assert len(codes) >= 10, f"expected a code per failure branch, found {sorted(codes)}"
    assert len(set(codes.values())) == len(codes), f"duplicate installer exit codes: {codes}"

    reserved = {0, 10, 20, 30, 40, 70, 73, 75, 76, 79, 81}
    collisions = {name: code for name, code in codes.items() if code in reserved}
    assert not collisions, f"installer exit codes collide with contract codes: {collisions}"

    postinstall = _postinstall_block(hooks_text)
    preinstall = _preinstall_block(hooks_text)
    # <installer-path-audit MA-34/MA-35> The band is no longer install-only
    # either. Five uninstall codes were added for refusals and incompletions
    # that previously returned NSIS's own generic script-abort code 2 (the
    # three PREUNINSTALL refusals) or 0 (the two POSTUNINSTALL paths that
    # alert and continue) -- an uninstall that deliberately left
    # multi-gigabyte trees and a running service behind reported SUCCESS to
    # winget/Intune. Those live in the uninstall macros by necessity, so the
    # orphan check accepts any of the four hook chains. Distinctness and
    # reservation above are unchanged and still cover every definition.
    postuninstall = _slice_macro(hooks_text, "NSIS_HOOK_POSTUNINSTALL")
    preuninstall = _slice_macro(hooks_text, "NSIS_HOOK_PREUNINSTALL")
    for name in codes:
        used = any(
            f"${{{name}}}" in block
            for block in (postinstall, preinstall, postuninstall, preuninstall)
        )
        assert used, f"{name} is defined but never used in any NSIS_HOOK_* macro"


# ---------------------------------------------------------------------------
# WP2 app-payload-pack gap closure (2026-07-30): a signed native-app-payload
# pack now exists (scripts/build_native_app_payload_pack.py), joins the
# bootstrap's required-component set, and its extracted tree is bridged to
# $INSTDIR\runtime -- closing the gap the prior migration explicitly
# disclosed and left open (wp2-hook-migration-2026-07-30.md §2). These tests
# pin the resulting D2 verification block and the cross-language component
# identity every layer of this bridge must agree on byte-for-byte.
# ---------------------------------------------------------------------------

NATIVE_PACK_STAGING_RS = INSTALLER / "src" / "native_pack_staging.rs"
APP_PAYLOAD_PY = ROOT / "civiccast" / "native" / "app_payload.py"
APP_PAYLOAD_PACK_BUILDER = ROOT / "scripts" / "build_native_app_payload_pack.py"


def test_native_app_payload_pack_builder_exists() -> None:
    assert APP_PAYLOAD_PACK_BUILDER.is_file(), (
        f"missing the native-app-payload pack builder: {APP_PAYLOAD_PACK_BUILDER}"
    )


def test_app_payload_component_identity_matches_across_python_and_rust() -> None:
    """The exact string 'native-app-payload' must agree across the Python
    policy module, the Rust required-set/bridge, and the NSIS D2 hook --
    a drift on any one side would silently produce a pack the other layers
    do not recognize (unauthorized-component abort, or a bridge that never
    fires)."""
    app_payload_source = APP_PAYLOAD_PY.read_text(encoding="utf-8")
    rust_source = NATIVE_PACK_STAGING_RS.read_text(encoding="utf-8")
    hooks_source = NATIVE_HOOKS.read_text(encoding="utf-8")

    assert 'APP_PAYLOAD_COMPONENT: Final[str] = "native-app-payload"' in app_payload_source
    assert 'pub const APP_PAYLOAD_COMPONENT: &str = "native-app-payload";' in rust_source
    assert "FFMPEG_RUNTIME_COMPONENT," in rust_source
    assert "OLLAMA_RUNTIME_COMPONENT," in rust_source
    assert "--civiccast-verify-pack-tree" in hooks_source
    assert "native-app-payload.ccpack" in hooks_source
    assert "--expected-component native-app-payload" in hooks_source


def test_bootstrap_postinstall_verifies_the_bridged_app_payload_runtime_destination() -> None:
    """The app-payload pack's D2 re-verification must target its BRIDGED
    extraction destination ($INSTDIR\\runtime), not the generic
    packs\\native-app-payload\\payload\\ convention every other component
    pack uses -- and must run after pack staging, before D4 provisioning,
    same as the native-server-binaries D2 check it sits beside."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert '--destination "$INSTDIR\\runtime"' in postinstall
    assert "--expected-component native-app-payload" in postinstall
    assert "$INSTDIR\\packs\\native-app-payload\\payload" not in postinstall, (
        "the app payload must NOT be verified at the generic packs\\<component>\\payload\\ path"
    )

    stage_packs = "--civiccast-stage-packs"
    app_payload_verify = '"$INSTDIR\\packs\\native-app-payload.ccpack"'
    provision = "--civiccast-provision"
    assert postinstall.index(stage_packs) < postinstall.index(app_payload_verify)
    assert postinstall.index(app_payload_verify) < postinstall.index(provision)


# ---------------------------------------------------------------------------
# FFmpeg-pack gap closure: a signed native-ffmpeg-runtime pack now exists
# (scripts/build_native_ffmpeg_pack.py) and its extracted tree is bridged to
# $INSTDIR\dependencies\ffmpeg -- closing the gap that left a native install
# with NO ffmpeg.exe/ffprobe.exe anywhere, while native_activation.rs's
# validate_staged_runtime_layout and main.rs's staged-runtime self-test both
# pin dependencies/ffmpeg/bin/ffmpeg.exe literally. The private candidate now
# carries and requires the sidecar even though the public GUI acquisition
# catalog still does not offer a separately downloadable media-tools row.
# ---------------------------------------------------------------------------

FFMPEG_PACK_BUILDER = ROOT / "scripts" / "build_native_ffmpeg_pack.py"


def test_native_ffmpeg_pack_builder_exists() -> None:
    assert FFMPEG_PACK_BUILDER.is_file(), (
        f"missing the native-ffmpeg-runtime pack builder: {FFMPEG_PACK_BUILDER}"
    )


def test_ffmpeg_runtime_component_identity_matches_across_python_and_rust() -> None:
    """The exact string 'native-ffmpeg-runtime' must agree across the Python
    builder, the Rust required-set/bridge, and the NSIS D2 hook -- a drift on
    any one side would silently produce a pack the other layers do not
    recognize (unauthorized-component abort, or a bridge that never fires and
    leaves ffmpeg.exe off the activation-pinned path)."""
    builder_source = FFMPEG_PACK_BUILDER.read_text(encoding="utf-8")
    rust_source = NATIVE_PACK_STAGING_RS.read_text(encoding="utf-8")
    hooks_source = NATIVE_HOOKS.read_text(encoding="utf-8")

    assert 'FFMPEG_RUNTIME_COMPONENT: Final[str] = "native-ffmpeg-runtime"' in builder_source
    assert 'pub const FFMPEG_RUNTIME_COMPONENT: &str = "native-ffmpeg-runtime";' in rust_source
    assert "native-ffmpeg-runtime.ccpack" in hooks_source
    assert "--expected-component native-ffmpeg-runtime" in hooks_source


def test_bootstrap_postinstall_verifies_the_bridged_ffmpeg_dependencies_destination() -> None:
    """The ffmpeg pack's D2 re-verification must target its BRIDGED extraction
    destination ($INSTDIR\\dependencies\\ffmpeg) -- the path that composes with
    the pack's own bin\\-rooted payload to produce
    $INSTDIR\\dependencies\\ffmpeg\\bin\\ffmpeg.exe -- and never the generic
    packs\\<component>\\payload\\ convention. Runs after pack staging and
    before D4 provisioning, same as the two D2 checks it sits beside."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert '--destination "$INSTDIR\\dependencies\\ffmpeg"' in postinstall
    assert "--expected-component native-ffmpeg-runtime" in postinstall
    assert "$INSTDIR\\packs\\native-ffmpeg-runtime\\payload" not in postinstall, (
        "the ffmpeg runtime must NOT be verified at the generic packs\\<component>\\payload\\ path"
    )

    stage_packs = "--civiccast-stage-packs"
    ffmpeg_verify = '"$INSTDIR\\packs\\native-ffmpeg-runtime.ccpack"'
    provision = "--civiccast-provision"
    assert postinstall.index(stage_packs) < postinstall.index(ffmpeg_verify)
    assert postinstall.index(ffmpeg_verify) < postinstall.index(provision)


def test_ffmpeg_runtime_component_is_required_and_reverified_for_private_candidate() -> None:
    rust_source = NATIVE_PACK_STAGING_RS.read_text(encoding="utf-8")
    hooks_source = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(hooks_source)

    assert "FFMPEG_RUNTIME_COMPONENT," in rust_source
    guard = '${If} ${FileExists} "$INSTDIR\\packs\\native-ffmpeg-runtime.ccpack"'
    ffmpeg_verify_call = (
        '--civiccast-verify-pack-tree "$INSTDIR\\packs\\native-ffmpeg-runtime.ccpack"'
    )
    assert guard not in postinstall
    assert ffmpeg_verify_call in postinstall


OLLAMA_PACK_BUILDER = ROOT / "scripts" / "build_native_ollama_pack.py"


def test_ollama_runtime_component_is_required_and_bridged_to_supervisor_path() -> None:
    builder_source = OLLAMA_PACK_BUILDER.read_text(encoding="utf-8")
    rust_source = NATIVE_PACK_STAGING_RS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert 'OLLAMA_RUNTIME_COMPONENT: Final[str] = "native-ollama-runtime"' in builder_source
    assert 'pub const OLLAMA_RUNTIME_COMPONENT: &str = "native-ollama-runtime";' in rust_source
    assert "OLLAMA_RUNTIME_COMPONENT," in rust_source
    assert '"$INSTDIR\\packs\\native-ollama-runtime.ccpack"' in postinstall
    assert '--destination "$INSTDIR\\dependencies\\ollama"' in postinstall
    assert "--expected-component native-ollama-runtime" in postinstall


def test_ffmpeg_pack_payload_root_composes_onto_the_activation_pinned_path() -> None:
    """The two halves of the bridge, pinned together: the builder must root its
    payload at ``bin/`` and the activation validator must pin
    ``dependencies/ffmpeg/bin/ffmpeg.exe``. Either half changing alone silently
    breaks the composition -- the pack would extract successfully to a path
    activation then refuses."""
    builder_source = FFMPEG_PACK_BUILDER.read_text(encoding="utf-8")
    activation_source = (INSTALLER / "src" / "native_activation.rs").read_text(encoding="utf-8")

    assert 'sources[f"bin/{filename}"] = path' in builder_source
    assert '"dependencies/ffmpeg/bin/ffmpeg.exe"' in activation_source


# ---------------------------------------------------------------------------
# WP2 D3 rehoming (2026-07-30): the journaled install/upgrade engine
# (civiccast.native.upgrade, spec D3) was the one retired block the prior
# hook-migration explicitly left unwired (wp2-hook-migration-2026-07-30.md
# §1/§2), because $INSTDIR\runtime\python.exe did not exist in any bootstrap
# build target yet. bce9a3cf closed that gap (the native-app-payload pack now
# bridges to $INSTDIR\runtime). The coordinator decided D3 re-homes into
# POSTINSTALL after D2 pack-tree verification and signed station activation,
# and BEFORE D4 provisioning/service registration -- tree management (junction flip,
# migration, health gate, rollback) must commit on the tree before
# provisioning/service-build acts on it. These tests pin that exact position
# and the preserved exit-code contract.
# ---------------------------------------------------------------------------


def test_bootstrap_postinstall_chain_places_d3_upgrade_engine_between_verify_and_provision() -> (
    None
):
    """Pins the D3 rehoming position: the journaled install/upgrade engine
    invocation must run after BOTH D2 pack-tree verification calls (so
    $INSTDIR\\runtime\\python.exe is verified-present) and signed station
    activation, but before D4 provisioning -- never before verification,
    never after provisioning or service registration."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    verify_pack_tree = "--civiccast-verify-pack-tree"
    d3_invocation = "-m civiccast.native.upgrade"
    provision = "--civiccast-provision"
    register_service = "--civiccast-register-native-service"

    assert d3_invocation in postinstall, (
        f"expected {d3_invocation!r} (the D3 engine invocation) in "
        "nsis-hooks-bootstrap.nsh POSTINSTALL"
    )

    # Both D2 pack-tree verification calls (native-server-binaries and the
    # bridged native-app-payload) must precede D3.
    last_verify = postinstall.rindex(verify_pack_tree)
    activation_step = '!insertmacro CIVICCAST_STEP "step d4-activate-station: begin"'
    activation_index = postinstall.index(activation_step)
    d3_index = postinstall.index(d3_invocation)
    assert last_verify < activation_index < d3_index, (
        "signed station activation must follow BOTH D2 verification calls and establish "
        "the strict runtime contract before D3 starts the candidate runtime for its "
        "maintenance health gate"
    )
    assert d3_index < postinstall.index(provision), (
        "D3 must run before D4 provisioning (tree management commits before "
        "provisioning/service-build acts on that tree)"
    )
    assert postinstall.index(provision) < postinstall.index(register_service)


def test_bootstrap_postinstall_d3_invocation_uses_the_bridged_runtime_interpreter() -> None:
    """The D3 engine must be invoked under the bridged embedded interpreter at
    $INSTDIR\\runtime\\python.exe -- never a host Python -- matching the
    retired block's own invocation shape (direct nsExec::ExecToLog call, no
    Rust CLI subcommand wrapper, unlike the D4 steps beside it)."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert '"$INSTDIR\\runtime\\python.exe" -m civiccast.native.upgrade' in postinstall
    assert "--payload-source" in postinstall
    payload_source_idx = postinstall.index("--payload-source")
    assert '"$INSTDIR\\runtime"' in postinstall[payload_source_idx : payload_source_idx + 60]
    assert "--flat-installer-layout" in postinstall[payload_source_idx : payload_source_idx + 100]


def test_bootstrap_d3_writes_machine_parseable_route_and_engine_evidence() -> None:
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "step d3-engine: evidence route=UPGRADE engine_exit=$0" in postinstall
    assert "step d3-engine: evidence route=FRESH_INSTALL engine_exit=11" in postinstall
    assert "step d3-engine: evidence route=SAME_VERSION_NO_OP engine_exit=12" in postinstall


def test_bootstrap_postinstall_d3_exit_code_contract_is_preserved_exactly() -> None:
    """The D3 engine's 5-way exit-code contract (0 / 10 / 20 / 30 / unexpected)
    must be branched on distinctly in operator-facing messages -- this is the
    load-bearing safety contract (commit vs rollback-containment vs halt vs
    refuse) and must not drift silently.

    RETARGETED (Gate A run 33681670855, 2026-09-02): exit 10's own behavior
    changed from chain M3's "continue, notify" to "fail closed" (see the
    exit==10 branch's header comment for the real-hardware failure this
    closes), so what this test pins for exit 10 changed with it -- it now
    asserts the branch fails through CIVICCAST_FAIL under its own distinct
    exit code, the same shape every other D3 failure branch already had."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "install/upgrade committed" in postinstall
    assert "upgrade HALTED" in postinstall
    assert "UPGRADE-RECOVERY.md" in postinstall
    assert "non-restorable migration" in postinstall
    assert "unexpected fault" in postinstall

    # The four erroring branches (10-under-flat-layout, 20, 30, unexpected)
    # must each fail the install outright, with a code that says WHICH of
    # them happened -- a rollback-containment abort, a halt needing manual
    # database restore, and a refused non-restorable migration call for
    # different operator action, so one shared "setup failed" code would be
    # a downgrade (AUDIT-001).
    # Sliced to the D3 block's OWN end label rather than a fixed character
    # window: a 4000-char window silently excluded the CIVICCAST_FAIL
    # branches the moment the exit==10 branch grew a second wording, which
    # made this assertion report a contract regression that had not happened.
    d3_start = postinstall.index("-m civiccast.native.upgrade")
    d3_region = postinstall[d3_start : postinstall.index("civiccast_bootstrap_d3_done:", d3_start)]
    assert d3_region.count("!insertmacro CIVICCAST_FAIL") >= 4
    for code_name in (
        "CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT",
        "CIVICCAST_EXIT_D3_HALTED",
        "CIVICCAST_EXIT_D3_REFUSED",
        "CIVICCAST_EXIT_D3_FAULT",
    ):
        assert f"${{{code_name}}}" in d3_region, f"the D3 region must fail with {code_name}"


# ---------------------------------------------------------------------------
# AUDIT-006 (still open): the D3 phase-to-exit-code contract is defined
# independently in Python (_EXIT_CODES in civiccast/native/upgrade/__main__.py)
# and in NSIS (the ${If}/${ElseIf} $0 == N ladder wired above). Nothing before
# this test read BOTH files and compared the NUMBERS: the Python unit test
# checks the dict against the enum it was built from (self-consistent, proves
# nothing about the hook); the hook policy test above
# (test_bootstrap_postinstall_d3_exit_code_contract_is_preserved_exactly)
# greps for message substrings but never cross-reads the Python source. This
# test closes that gap.
# ---------------------------------------------------------------------------

UPGRADE_MAIN_PY = ROOT / "civiccast" / "native" / "upgrade" / "__main__.py"


def _python_exit_code_dict(dict_name: str) -> dict[str, int]:
    """Parse a ``<dict_name>: dict[<Enum>, int] = {...}`` literal out of
    __main__.py with ``ast`` -- NOT by importing the module. Importing
    civiccast.native.upgrade pulls in the orchestrator, service_control, and
    (transitively) SQLAlchemy for what is meant to stay a cheap policy check
    (see the sturdier-ast-parse precedent already used for a similar
    read-the-literal-without-importing need in
    scripts/provision_native_build_toolchain.py's ``_app_build_toolchain_policy``).
    Returns {enum member name: exit code}."""
    tree = ast.parse(UPGRADE_MAIN_PY.read_text(encoding="utf-8"), filename=str(UPGRADE_MAIN_PY))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == dict_name
            and isinstance(node.value, ast.Dict)
        ):
            codes: dict[str, int] = {}
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                assert isinstance(key, ast.Attribute), (
                    f"expected {dict_name} keys shaped like <Enum>.<MEMBER>, found {ast.dump(key)}"
                )
                codes[key.attr] = ast.literal_eval(value)
            return codes
    raise AssertionError(f"could not locate the {dict_name} dict literal in {UPGRADE_MAIN_PY}")


def _python_d3_exit_codes() -> dict[str, int]:
    """Every nonzero exit code the D3 CLI can emit, from BOTH of its contracts.

    ``_EXIT_CODES`` maps terminal journal phases; ``_ROUTE_EXIT_CODES`` (chain
    K/K2) maps the routing outcomes that never enter the D3 sequence at all.
    Both travel through the same ``$0`` ladder in the hook, so both must be
    cross-checked against it -- reading only the first is how a routing code
    could be added in Python with no hook branch and fall through to the
    unexpected-fault ${Else}, failing an install that actually succeeded."""

    merged = dict(_python_exit_code_dict("_EXIT_CODES"))
    for name, code in _python_exit_code_dict("_ROUTE_EXIT_CODES").items():
        assert name not in merged, f"{name} is defined in both D3 exit-code dicts"
        assert code not in merged.values(), (
            f"routing code {code} ({name}) collides with a phase exit code; the hook "
            "branches on the number alone and could not tell them apart"
        )
        merged[name] = code
    return merged


def _d3_hook_region(hooks_text: str) -> str:
    """The hook text from the D3 engine invocation up to its own done-label,
    i.e. just the branching ladder this test cross-checks (never the D4 steps
    that reuse similar-looking $0 checks right below it)."""
    d3_start = hooks_text.index("-m civiccast.native.upgrade")
    d3_end = hooks_text.index("civiccast_bootstrap_d3_done:", d3_start)
    return hooks_text[d3_start:d3_end]


def _d3_branch_bodies(region: str) -> dict[object, str]:
    """Map each ``${If}``/``${ElseIf} $0 == N`` branch to its own body text (up
    to the next branch or ``${EndIf}``), plus the catch-all ``${Else}`` body
    under the key ``"else"``. Lets the test assert not just WHICH numbers the
    hook branches on, but which OUTCOME each number reaches."""
    bodies: dict[object, list[str]] = {}
    current: object = None
    header = re.compile(r"^\s*\$\{(If|ElseIf|Else)\}(?:\s*\$0\s*==\s*(\d+))?\s*$")
    end = re.compile(r"^\s*\$\{EndIf\}\s*$")
    for line in region.splitlines():
        match = header.match(line)
        if match:
            kind, code = match.groups()
            current = "else" if kind == "Else" else int(code)
            bodies[current] = []
            continue
        if end.match(line):
            current = None
            continue
        if current is not None:
            bodies[current].append(line)
    return {key: "\n".join(lines) for key, lines in bodies.items()}


def test_d3_exit_code_contract_cross_checked_between_python_engine_and_nsis_hook() -> None:
    """Without this test, the D3 phase-to-exit-code contract can drift silently:
    renumbering a phase in Python's _EXIT_CODES (e.g. HALTED_RESTORE_FAILED
    20 -> 21) is invisible to the Python unit test (it only checks the dict
    against its own enum) and invisible to the existing hook policy test above
    (it only greps for message substrings, never reads the Python source) --
    but it silently falls the hook's numeric ladder through to its
    unexpected-fault ${Else} branch. An operator who just had a HALTED
    upgrade -- service stopped on purpose, database restore required -- would
    then be told "unexpected fault, see the installer log" instead of being
    pointed at UPGRADE-RECOVERY.md.

    Worse than a missing branch is a CROSSED one: if 20's and 30's branch
    bodies were ever swapped, a HALT (needing manual database restore) would
    be reported through the non-restorable-migration message and the
    CIVICCAST_EXIT_D3_REFUSED code -- telling the operator to "use the manual
    upgrade path with operator acknowledgement" when what actually happened
    is a failed automatic rollback. That is the exact wrong-instructions
    defect AUDIT-006 flags, and the two assertions below on branch BODIES
    (not just branch numbers) are what would catch it.
    """
    python_codes = _python_d3_exit_codes()
    nonzero_python_codes = {name: code for name, code in python_codes.items() if code != 0}
    assert nonzero_python_codes, (
        f"expected at least one nonzero UpgradePhase exit code, found {python_codes}"
    )

    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    region = _d3_hook_region(hooks_text)
    branch_bodies = _d3_branch_bodies(region)
    hook_numeric_codes = {key for key in branch_bodies if isinstance(key, int) and key != 0}

    # Bidirectional pin: every nonzero phase the Python engine can emit must
    # be branched on by the hook, AND the hook must not branch on a number
    # the engine cannot emit (a phantom/stale code left over from a prior
    # renumbering that can never actually fire).
    assert set(nonzero_python_codes.values()) == hook_numeric_codes, (
        "the D3 phase-to-exit-code contracts disagree: Python's nonzero "
        f"UpgradePhase codes are {nonzero_python_codes} (from {UPGRADE_MAIN_PY}) "
        f"but the NSIS hook's numeric $0 == N branches are {sorted(hook_numeric_codes)} "
        f"(from the D3 region of {NATIVE_HOOKS})"
    )

    # Each specific code must route to the RIGHT operator outcome. Getting 20
    # (HALTED_RESTORE_FAILED) and 30 (REFUSED_NON_RESTORABLE) crossed is the
    # exact defect this test exists to catch.
    halted_body = branch_bodies[20]
    assert "UPGRADE-RECOVERY.md" in halted_body, (
        "exit code 20 (HALTED_RESTORE_FAILED) must route to the recovery-doc message"
    )
    assert "${CIVICCAST_EXIT_D3_HALTED}" in halted_body, (
        "exit code 20 must fail through CIVICCAST_EXIT_D3_HALTED"
    )
    assert "${CIVICCAST_EXIT_D3_REFUSED}" not in halted_body, (
        "exit code 20 must NOT reach the non-restorable-migration failure code"
    )

    refused_body = branch_bodies[30]
    assert "non-restorable migration" in refused_body, (
        "exit code 30 (REFUSED_NON_RESTORABLE) must route to the non-restorable-migration message"
    )
    assert "${CIVICCAST_EXIT_D3_REFUSED}" in refused_body, (
        "exit code 30 must fail through CIVICCAST_EXIT_D3_REFUSED"
    )
    assert "${CIVICCAST_EXIT_D3_HALTED}" not in refused_body, (
        "exit code 30 must NOT reach the halted-restore-failed failure code"
    )

    # Chain K/K2: the two ROUTING outcomes. Neither is a failure -- R7's whole
    # damage was a non-failure being reported as a failed upgrade with a
    # rollback dialog -- so neither may reach CIVICCAST_FAIL, and each must
    # carry text that is true about what actually happened.
    fresh_body = branch_bodies[python_codes["FRESH_INSTALL"]]
    assert "!insertmacro CIVICCAST_FAIL" not in fresh_body, (
        "routing to a fresh install is not a failure and must never abort the install"
    )
    assert "preserved and adopted" in fresh_body, (
        "the fresh-install route must state that existing CivicCast data is adopted, "
        "not deleted -- the preserve-on-uninstall design is invisible to the "
        "operator unless the install says so"
    )
    assert "nothing was deleted" in fresh_body.lower()

    no_op_body = branch_bodies[python_codes["SAME_VERSION_NO_OP"]]
    assert "!insertmacro CIVICCAST_FAIL" not in no_op_body, (
        "a same-version no-op is not a failure and must never abort the install"
    )
    assert "already installed" in no_op_body
    assert "did nothing" in no_op_body or "no database migration to run" in no_op_body, (
        "the same-version route's text must be honest that no migration ran"
    )


# ---------------------------------------------------------------------------
# WP2 transfer-transaction (2026-07-30): the acknowledged ActiveRuntime
# ownership transfer (Native -> Wsl) named by D1's "the operator to run the
# cutover/rollback transfer, offered as an explicitly acknowledged
# transaction from the uninstall UI, before removal proceeds" and left
# unimplemented by the earlier WP2 preflight slice ("transfer acknowledgement
# is not implemented in this WP2 slice"). These tests pin: the MB_YESNO
# prompt is the only interactive surface used, gated on the Rust-computed
# exit code 74 (never a fresh NSIS registry read); acknowledging re-invokes
# the SAME executable with --acknowledge-transfer BEFORE taskkill / any
# removal step; declining aborts with SetErrors + Abort and touches no
# registry value directly from NSIS; and a post-acknowledgment failure also
# aborts before taskkill.
# ---------------------------------------------------------------------------


def _preuninstall_block(hooks_text: str) -> str:
    return hooks_text.split("!macro NSIS_HOOK_PREUNINSTALL", 1)[1].split("!macroend", 1)[0]


def test_preuninstall_prompts_for_acknowledged_transfer_on_exit_74_before_any_removal_step() -> (
    None
):
    preuninstall = _preuninstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "$0 == 74" in preuninstall, (
        "the transfer-eligible outcome must be gated on the distinct Rust-computed "
        "exit code (74), not a fresh NSIS-side registry read"
    )
    assert "MB_YESNO" in preuninstall, (
        "the acknowledged transfer must be offered via the one interactive surface "
        "available in the uninstall UI (NSIS MessageBox MB_YESNO)"
    )
    assert "--acknowledge-transfer" in preuninstall

    # Use CODE-only markers for the ordering check below (the explanatory
    # comment above the macro legitimately mentions "MB_YESNO" and
    # "--acknowledge-transfer" in prose ahead of the real statements; a bare
    # substring search would find those first and give a false ordering
    # signal). "MB_YESNO|MB_ICONQUESTION" is the actual MessageBox flag
    # combination (never written that way in prose); the trailing "'" on
    # "--acknowledge-transfer'" is the closing quote of the real nsExec
    # command line (prose never follows the flag with a quote).
    first_probe = preuninstall.index("--civiccast-native-uninstall-preflight")
    exit_74_check = preuninstall.index("$0 == 74")
    prompt = preuninstall.index("MB_YESNO|MB_ICONQUESTION")
    ack_flag = preuninstall.index("--acknowledge-transfer'")
    taskkill = preuninstall.index("taskkill.exe")

    assert first_probe < exit_74_check < prompt < ack_flag < taskkill, (
        "expected: initial (unacknowledged) preflight probe -> exit-74 gate -> "
        "MB_YESNO prompt -> --acknowledge-transfer re-invocation -> taskkill, in "
        "that order"
    )


def test_preuninstall_declining_the_transfer_prompt_aborts_with_state_untouched() -> None:
    preuninstall = _preuninstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    # No path in this macro may write the registry directly -- Rust owns
    # every mutation (write+read-back-verify), matching the module's
    # documented "Rust owns state observation" convention. A decline must
    # therefore be achievable by simply not re-invoking the executable, never
    # by NSIS undoing a mutation it never should have started.
    assert 'WriteRegStr HKLM "Software\\CivicCast"' not in preuninstall
    assert 'DeleteRegValue HKLM "Software\\CivicCast" "ActiveRuntime"' not in preuninstall

    # The IDYES jump target must be the ONLY way execution reaches the
    # acknowledged re-invocation; falling through (No/Cancel) must hit an
    # abort before that point. Start from the CODE MessageBox line (see the
    # comment in the sibling ordering test above for why a bare "MB_YESNO"
    # substring search would instead match the explanatory prose comment).
    prompt_idx = preuninstall.index("MB_YESNO|MB_ICONQUESTION")
    ack_label_idx = preuninstall.index("civiccast_native_transfer_acknowledged:")
    decline_region = preuninstall[prompt_idx:ack_label_idx]

    assert "IDYES civiccast_native_transfer_acknowledged" in decline_region
    assert "SetErrors" in decline_region
    assert "Abort" in decline_region
    assert "--acknowledge-transfer" not in decline_region, (
        "the decline path itself must never mention the acknowledgment flag -- "
        "it must fall through to SetErrors/Abort without re-invoking the executable"
    )


def test_preuninstall_transfer_prompt_declines_by_default_in_silent_mode() -> None:
    # RC4 host test 2026-11-04: `uninstall.exe /S` hung 30 minutes on this
    # prompt, which NSIS still shows in silent mode unless /SD is given. A
    # silent uninstall must fail loud by DECLINING (the decline branch aborts
    # with the uninstall-declined exit code and an ALERT, nothing removed),
    # never block on a window nobody can see, and never guess "yes".
    preuninstall = _preuninstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    prompt_line = next(
        line for line in preuninstall.splitlines() if "MB_YESNO|MB_ICONQUESTION" in line
    )
    assert "/SD IDNO" in prompt_line
    assert "/SD IDYES" not in prompt_line
    assert prompt_line.index("/SD IDNO") < prompt_line.index(
        "IDYES civiccast_native_transfer_acknowledged"
    )


def test_preuninstall_transfer_failure_after_acknowledgment_aborts_before_taskkill() -> None:
    preuninstall = _preuninstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    ack_label_idx = preuninstall.index("civiccast_native_transfer_acknowledged:")
    taskkill_idx = preuninstall.index("taskkill.exe")
    post_ack_region = preuninstall[ack_label_idx:taskkill_idx]

    assert "--acknowledge-transfer" in post_ack_region
    assert "$0 != 0" in post_ack_region, (
        "a nonzero exit from the acknowledged re-invocation (the transfer write "
        "failed) must be checked before falling through to taskkill/removal"
    )
    assert "SetErrors" in post_ack_region
    assert "Abort" in post_ack_region
    assert "!insertmacro CIVICCAST_ALERT" in post_ack_region


def test_preuninstall_still_blocks_unacknowledgeable_reasons_exactly_as_before() -> None:
    """Regression guard: an unreadable selector or unknown WSL-ARP state (no
    acknowledgment can fix either) must still hard-block via the pre-existing
    generic path, unaffected by the new exit-74 branch."""
    preuninstall = _preuninstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "$0 != 0" in preuninstall
    assert "$0 != 73" in preuninstall
    assert preuninstall.count("!insertmacro CIVICCAST_ALERT") >= 2
    assert preuninstall.count("SetErrors") >= 2
    assert preuninstall.count("Abort") >= 2


def test_bootstrap_postinstall_old_version_never_comes_from_arp_display_version() -> None:
    """Matrix row 1 (Sandbox, 2026-07-30) live fault: Tauri's generated
    installer section writes the ARP DisplayVersion BEFORE
    NSIS_HOOK_POSTINSTALL runs (installer.nsi WriteRegStr precedes the hook
    insertion), so by hook time ARP always holds the version being installed
    RIGHT NOW -- it can neither detect a fresh install nor supply a true
    --old-version. The chain must read the product-owned InstalledVersion
    marker instead, which only THIS hook writes (at the end of a fully
    successful postinstall chain).

    Since chain K/K2 that marker is ONLY the old-version signal; it no longer
    selects the route (see the test below)."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert "CurrentVersion\\Uninstall" not in postinstall, (
        "the POSTINSTALL chain must not read ARP uninstall registry state -- "
        "Tauri writes it before the hook runs, so it cannot distinguish a "
        "fresh install and reports the NEW version during upgrades"
    )
    marker_read = 'ReadRegStr $R0 HKLM "Software\\CivicCast\\Native" "InstalledVersion"'
    assert marker_read in postinstall, (
        "the D3 chain must read the product-owned InstalledVersion marker for --old-version"
    )


def test_d3_engine_invocation_is_never_gated_on_preserved_registry_remnants() -> None:
    """Chain K/K2, real hardware R7 (2026-08-01).

    The old gate skipped the D3 engine only when BOTH ``InstalledVersion`` and
    ``DatabaseUrl`` were absent. Both values survive uninstall BY DESIGN (they
    are the credential for, and version stamp of, the preserved PostgreSQL
    cluster -- ``native_uninstall.rs``'s ``NATIVE_D4_STATE_INVENTORY``), so on
    R7 -- 0 ARP entries, no CivicCastSupervisor service, no install directory,
    only preserved data -- the gate could not fire and the UPGRADE engine ran,
    "upgrading" 1.0.0-rc15 to 1.0.0-rc15 and ending setup in a rollback dialog.

    The routing decision now lives in tested Python
    (``civiccast/native/upgrade/routing.py``) and keys on whether a product
    actually EXISTS. The prior-version sentinel is normalized before station
    activation; the routing block then reaches D3 without an NSIS gate on
    preserved $R0/$R2 data. Station activation has its own exit-code branches
    before this region and must not be mistaken for an upgrade-routing gate.
    """
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(hooks_text)

    invocation = "-m civiccast.native.upgrade"
    marker_read = 'ReadRegStr $R0 HKLM "Software\\CivicCast\\Native" "InstalledVersion"'
    marker_at = postinstall.index(marker_read)
    sentinel_end = postinstall.index("${EndIf}", marker_at) + len("${EndIf}")
    marker_normalization = postinstall[marker_at:sentinel_end]

    # The one marker-read conditional only normalizes an absent version to the
    # literal "none" consumed by the engine's --old-version argument.
    sentinel_default = '${If} $R0 == ""'
    conditionals = [
        line.strip()
        for line in marker_normalization.splitlines()
        if not line.strip().startswith(";")
        and ("${If}" in line or "${AndIf}" in line or "${ElseIf}" in line)
    ]
    assert conditionals == [sentinel_default], (
        "the D3 engine invocation must not be gated in NSIS -- the routing decision "
        "belongs to civiccast.native.upgrade.routing, which keys on whether a "
        f"product EXISTS rather than on preserved data. Found: {conditionals}"
    )
    assert "$R2" not in marker_normalization, (
        "the sentinel default must not read the preserved DatabaseUrl value -- that "
        "pairing IS the gate R7 tripped"
    )

    # Station activation runs before D3 and has its own checked failure arms.
    # Inspect only the post-activation routing section to ensure none of those
    # independent $0 branches are confused with a D3 remnant-keyed gate.
    route_marker = "UPGRADE-VS-FRESH ROUTING"
    route_at = postinstall.index(route_marker)
    invocation_at = postinstall.index(invocation, route_at)
    routing_preamble = postinstall[route_at:invocation_at]
    route_conditionals = [
        line.strip()
        for line in routing_preamble.splitlines()
        if not line.strip().startswith(";")
        and ("${If}" in line or "${AndIf}" in line or "${ElseIf}" in line)
    ]
    assert route_conditionals == [], (
        "the post-activation D3 routing section must invoke the engine without "
        f"an NSIS gate; found: {route_conditionals}"
    )

    # And the engine must be reached, not jumped over.
    assert "Goto civiccast_bootstrap_d3_done" not in routing_preamble, (
        "nothing between reading the version marker and invoking the engine may "
        "skip the engine -- that skip WAS the gate"
    )


def test_bootstrap_postinstall_writes_installed_version_marker_only_after_full_success() -> None:
    """The InstalledVersion marker is the D3 gate's prior-version signal, so
    it must be written exactly once, only after D4 firewall registration --
    the last step of the chain. Every failure branch aborts before reaching
    it, so a failed install leaves the marker at its previous value (a failed
    upgrade did not change the installed version). Like DatabaseUrl, the
    marker deliberately survives uninstall (the database cluster is
    preserved), so a reinstall over existing data runs the engine as a true
    upgrade with the real previous version."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(hooks_text)

    marker_write = 'WriteRegStr HKLM "Software\\CivicCast\\Native" "InstalledVersion" "${VERSION}"'
    assert postinstall.count(marker_write) == 1, (
        "expected exactly one InstalledVersion marker write in POSTINSTALL"
    )
    register_firewall = "--civiccast-register-native-firewall-rule"
    assert postinstall.index(register_firewall) < postinstall.index(marker_write)
    # Nothing may fail-and-abort AFTER the marker is written: an abort past
    # this point would leave a machine advertising a version whose install
    # chain did not finish.
    assert "!insertmacro CIVICCAST_FAIL" not in postinstall[postinstall.index(marker_write) :], (
        "the InstalledVersion marker must be the last thing the chain does"
    )


def test_non_interactive_failure_reports_never_block_a_silent_install() -> None:
    """An NSIS MessageBox blocks even under /S, waiting for a click no
    unattended install can give -- the installer stays alive with no
    children and no visible progress. That is exactly the 'hang' that cost
    Sandbox matrix runs 3-6 (the D4 service-registration failure path), and
    an audit found 21 MessageBox sites with ZERO silent guards. Every
    non-interactive failure report must route through CIVICCAST_ALERT,
    which logs + DetailPrints always and shows a dialog only when NOT
    silent. The version-preflight macro also has two MB_OK dialogs, each
    explicitly gated on non-silent mode. The ONE unguarded exception is the
    MB_YESNO ActiveRuntime ownership-transfer prompt: it asks a question
    silent mode cannot answer, so it must fail loud rather than guess.

    CIVICCAST_NOTICE (CRITICAL fix, 2026-07-30 adversarial review, D3
    clean-rollback) is silent-safe by the same construction -- breadcrumb +
    DetailPrint always, a dialog only when NOT silent -- so its macro body is
    excluded from the stray-dialog scan below exactly like CIVICCAST_ALERT's,
    and is checked for the same non-silent-mode gate. The separate
    CIVICCAST_VERSION_PREFLIGHT macro is also excluded only after the test
    verifies both of its MB_OK dialogs are inside `${IfNot} ${Silent}`."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")

    macro_body_start = hooks_text.index("!macro CIVICCAST_ALERT")
    macro_body_end = hooks_text.index("!macroend", macro_body_start)
    macro_body = hooks_text[macro_body_start:macro_body_end]

    notice_body_start = hooks_text.index("!macro CIVICCAST_NOTICE TEXT")
    notice_body_end = hooks_text.index("!macroend", notice_body_start)
    notice_body = hooks_text[notice_body_start:notice_body_end]

    preflight_body_start = hooks_text.index("!macro CIVICCAST_VERSION_PREFLIGHT")
    preflight_body_end = hooks_text.index("!macroend", preflight_body_start)
    preflight_body = hooks_text[preflight_body_start:preflight_body_end]

    # Version refusal and comparison-error dialogs are safe in /S because
    # each is nested under the non-silent guard, while its DetailPrint remains
    # available to unattended logs.
    preflight_dialogs = []
    guard_stack = []
    for line in preflight_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("${If"):
            guard_stack.append(stripped)
        if "MessageBox" in line and not stripped.startswith(";"):
            assert "MB_OK" in line, f"unexpected preflight dialog: {stripped}"
            assert "${IfNot} ${Silent}" in guard_stack, (
                f"every version-preflight dialog must be suppressed in silent mode: {stripped}"
            )
            preflight_dialogs.append(stripped)
        if stripped.startswith("${EndIf}"):
            assert guard_stack, "unmatched ${EndIf} in version-preflight macro"
            guard_stack.pop()
    assert not guard_stack, f"unclosed NSIS condition in version-preflight macro: {guard_stack}"
    assert len(preflight_dialogs) == 2, (
        f"expected exactly two silent-gated version-preflight dialogs, found {preflight_dialogs}"
    )

    outside_macro = (
        hooks_text[:macro_body_start]
        + hooks_text[macro_body_end:notice_body_start]
        + hooks_text[notice_body_end:]
    )
    # Exclude the version-preflight macro only after validating every dialog
    # in it above; the only remaining unguarded dialog must be the deliberate
    # ownership-transfer question.
    preflight_start_after_notices = outside_macro.index("!macro CIVICCAST_VERSION_PREFLIGHT")
    preflight_end_after_notices = outside_macro.index("!macroend", preflight_start_after_notices)
    outside_macro = (
        outside_macro[:preflight_start_after_notices] + outside_macro[preflight_end_after_notices:]
    )

    # The reporting macros own their plain MB_OK dialogs and gate them on
    # non-silent mode; the preflight macro's two dialogs were checked above.
    assert "MessageBox MB_OK" in macro_body
    assert "${IfNot} ${Silent}" in macro_body, (
        "CIVICCAST_ALERT must gate its dialog on non-silent mode"
    )
    assert "MessageBox MB_OK" in notice_body
    assert "${IfNot} ${Silent}" in notice_body, (
        "CIVICCAST_NOTICE must gate its dialog on non-silent mode"
    )

    stray_dialogs = [
        line.strip()
        for line in outside_macro.splitlines()
        if "MessageBox" in line and not line.strip().startswith(";")
    ]
    assert len(stray_dialogs) == 1, (
        "exactly one MessageBox may live outside CIVICCAST_ALERT/CIVICCAST_NOTICE "
        f"(the MB_YESNO ownership-transfer prompt); found: {stray_dialogs}"
    )
    assert "MB_YESNO" in stray_dialogs[0], (
        "the only permitted un-macro'd dialog is the interactive "
        f"ownership-transfer question; found: {stray_dialogs[0]}"
    )
    # Every failure branch must still report through a silent-safe path.
    # CIVICCAST_FAIL wraps CIVICCAST_ALERT (adding the error level and the
    # abort AUDIT-001 required), so a POSTINSTALL branch now shows up as a
    # FAIL site rather than a bare ALERT site; both count. CIVICCAST_NOTICE
    # is also silent-safe but is deliberately NOT counted here -- it is not a
    # failure report (see test_bootstrap_postinstall_every_failure_branch_
    # actually_fails_the_install, which pins it is used exactly once and only
    # by the D3 clean-rollback branch).
    silent_safe_reports = hooks_text.count("!insertmacro CIVICCAST_ALERT") + hooks_text.count(
        "!insertmacro CIVICCAST_FAIL"
    )
    assert silent_safe_reports >= 20, (
        f"expected every failure report to route through a silent-safe macro, found {silent_safe_reports}"
    )
    fail_macro_body = hooks_text.split("!macro CIVICCAST_FAIL CODE TEXT", 1)[1].split(
        "!macroend", 1
    )[0]
    assert "MessageBox" not in fail_macro_body, (
        "CIVICCAST_FAIL must report through CIVICCAST_ALERT, never its own dialog"
    )


def test_service_host_member_is_restored_after_service_registration() -> None:
    """pywin32's service install MOVES pythonservice.exe out of the
    payload's site-packages/win32 into the payload root. The pack ships it
    at BOTH paths so the service's registered binary path is a manifest
    member, and the chain restores the site-packages member immediately
    after registration so the installed tree stays byte-identical to the
    signed manifest (Sandbox run 6: without this, D5 repair normalized the
    mutated tree and the service could not start -- StartService error 2).

    PIN CORRECTED 2026-07-30 (Sandbox run 10): this test previously pinned
    the restore's source as $INSTDIR\\pythonservice.exe -- a path that has
    never existed, so the pinned CopyFiles silently failed on every install
    and the first D5 verify after a CLEAN install reported 76/repaired
    (run 10 verify output names the missing site-packages member). The pin
    was asserting what the code DID, not what is true: pywin32 moves the
    exe to sys.exec_prefix, which is $INSTDIR\\runtime (run 10's sc qc
    snapshot shows the registered binary path runtime\\pythonservice.exe).
    The pin now requires the CORRECT source and the IfFileExists guard so
    a missing source can never again be silent."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    register_service = "--civiccast-register-native-service"
    restore = r'CopyFiles /SILENT "$INSTDIR\runtime\pythonservice.exe"'
    assert restore in postinstall, (
        "the chain must restore the site-packages service-host member after registration, "
        "copying from the payload root (runtime\\) where pywin32 actually moves it"
    )
    stale_restore = r'CopyFiles /SILENT "$INSTDIR\pythonservice.exe"'
    assert stale_restore not in postinstall, (
        "the restore must never regress to the phantom $INSTDIR\\pythonservice.exe source "
        "(never existed; silently failed on every install until run 10 caught it)"
    )
    assert r'IfFileExists "$INSTDIR\runtime\pythonservice.exe"' in postinstall, (
        "the restore source must be existence-guarded with a breadcrumb so a missing "
        "source is visible, not silent"
    )
    assert postinstall.index(register_service) < postinstall.index(restore), (
        "the restore must run AFTER service registration performs the move"
    )


def test_d3_clean_rollback_does_not_record_new_installed_version() -> None:
    """History: a 2026-07-30 adversarial-review fix stopped D3's exit==10
    branch (clean rollback) from falling through to the InstalledVersion
    write on a machine the engine's own contract had just left on the OLD
    version, using a `$R4` latch register gating that write.

    SUPERSEDED (Gate A run 33681670855, 2026-09-02): the whole
    continue-and-report shape that latch protected is gone. Under the flat
    installer layout this bootstrap always uses, a D3 engine ROLLED_BACK
    report does not mean "the machine is on the old version, still healthy" --
    adapt_flat_installer_layout's read_junction/lay_tree/flip_junction are
    no-ops over the single "$INSTDIR\\runtime" tree already holding the NEW
    payload before D3 ever ran (see the exit==10 branch's own header comment
    for the full reasoning and the real-hardware failure this closes).
    Continuing past it -- which is exactly what the old $R4-gated path did --
    let D4 register and start a service on that mismatched pair. The fix
    retires the $R4 latch and the notice-and-continue path entirely: exit 10
    now fails the WHOLE install via CIVICCAST_FAIL
    (${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT}), before the InstalledVersion write
    is ever reached, exactly like every other D3 failure branch. This test now
    pins THAT mechanism instead: the write is unconditional and unreachable
    from exit==10 because CIVICCAST_FAIL aborts first."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    marker_write = 'WriteRegStr HKLM "Software\\CivicCast\\Native" "InstalledVersion" "${VERSION}"'

    executable = "\n".join(
        line for line in postinstall.splitlines() if not line.lstrip().startswith(";")
    )
    assert "$R4" not in executable, (
        "the retired InstalledVersion-write latch ($R4) must not reappear in executable "
        "NSIS code -- exit 10 fails closed now, so there is nothing left for a latch to "
        "gate (prose may still discuss the retired mechanism historically)"
    )

    d3_start = postinstall.index("-m civiccast.native.upgrade")
    assert "${ElseIf} $0 == 10" in postinstall
    assert "${ElseIf} $0 == 20" in postinstall
    rollback_branch = postinstall.split("${ElseIf} $0 == 10", 1)[1].split("${ElseIf} $0 == 20", 1)[
        0
    ]
    assert "!insertmacro CIVICCAST_FAIL" in rollback_branch, (
        "the D3 exit==10 (clean rollback under the flat installer layout) branch must "
        "abort via CIVICCAST_FAIL -- see the Gate A run 33681670855 fix comment above "
        "the branch for why a bare continue is unsafe under this layout"
    )
    assert "${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT}" in rollback_branch
    assert "!insertmacro CIVICCAST_ALERT" not in rollback_branch, (
        "the D3 exit==10 path must not use a bare CIVICCAST_ALERT -- it must go "
        "through CIVICCAST_FAIL, which wraps CIVICCAST_ALERT with SetErrorLevel + Abort"
    )
    assert "!insertmacro CIVICCAST_NOTICE" not in postinstall, (
        "CIVICCAST_NOTICE must have no remaining caller in POSTINSTALL -- the D3 "
        "clean-rollback continuation path it existed for is retired"
    )
    # The actual defect this fix closes, restated for the new mechanism: the
    # rollback branch's own text must never reach the InstalledVersion write
    # -- CIVICCAST_FAIL's Abort makes that structurally true, and the write
    # itself is now unconditional (unreachable except via a fully successful
    # chain, since every failure branch above it aborts outright).
    assert marker_write not in rollback_branch, (
        "the D3 exit==10 (clean rollback) branch must not reach the "
        "InstalledVersion write -- CIVICCAST_FAIL aborts before it"
    )
    assert d3_start < postinstall.index(marker_write), (
        "sanity: the InstalledVersion write still lives after the D3 engine call"
    )


def test_native_teardown_invocation_lives_in_preuninstall_not_postuninstall() -> None:
    """BLOCKER (2026-07-30, live Sandbox run 7): --civiccast-teardown-native-state
    was originally invoked from NSIS_HOOK_POSTUNINSTALL. But Tauri's OWN
    generated uninstall Section deletes the exact executable that CLI call
    needs, BEFORE POSTUNINSTALL ever runs:

        target/release/nsis/x64/installer.nsi:756
            Delete "$INSTDIR\\${MAINBINARYNAME}.exe"
        target/release/nsis/x64/installer.nsi:838
            !insertmacro NSIS_HOOK_POSTUNINSTALL

    (line 756 is inside `Section Uninstall`, strictly before line 838 in the
    same linear script -- there is no jump between them). So a teardown call
    living in POSTUNINSTALL can never actually run: nsExec::ExecToLog just
    fails to launch a file NSIS's own uninstall Section had already deleted a
    few lines earlier in the SAME generated script. The entire teardown --
    stop the service, remove the service, delete the firewall rule -- never
    ran, while the uninstall still reported exit 0. This precisely explains
    the observed Sandbox run 7 evidence: uninstall exited 0 while leaving the
    CivicCastSupervisor service registered, the TCP 8000 firewall rule open,
    and 12,145 files behind.

    NSIS_HOOK_PREUNINSTALL is inserted BEFORE that Delete:

        target/release/nsis/x64/installer.nsi:748-750
            !ifmacrodef NSIS_HOOK_PREUNINSTALL
              !insertmacro NSIS_HOOK_PREUNINSTALL
            !endif

    -- i.e. at line 748, strictly before the line-756 Delete -- so the exe
    still exists there. This is independently provable from the hook file
    itself, without needing the generated installer.nsi at all: PREUNINSTALL's
    pre-existing ActiveRuntime ownership preflight (the ORIGINAL, unmoved
    code, not part of this fix) already depends on and successfully invokes
    that exact same executable at this same point in the chain -- proving the
    exe is present here, or that preflight could never have worked either.

    Fix: move the teardown invocation itself into PREUNINSTALL (after the
    ownership preflight gate, before the pre-existing taskkill), and carry its
    exit code to POSTUNINSTALL via a file-scope Var, since the recursive tree
    removal that gate controls must stay in POSTUNINSTALL (it can only run
    after Tauri's own file deletion has completed)."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    preuninstall = _preuninstall_block(hooks_text)
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    teardown_call = "--civiccast-teardown-native-state"
    assert teardown_call in preuninstall, (
        "the teardown CLI must be invoked from PREUNINSTALL, where the "
        "product executable it needs still exists (see this test's docstring)"
    )
    # Prose (comment lines, starting with ';') may still legitimately discuss
    # --civiccast-teardown-native-state (e.g. naming the Rust function that
    # returns its exit code) -- only an EXECUTABLE nsExec call is forbidden.
    postuninstall_executable = "\n".join(
        line for line in postuninstall.splitlines() if not line.strip().startswith(";")
    )
    assert teardown_call not in postuninstall_executable, (
        "the teardown invocation must NOT be in POSTUNINSTALL: Tauri's "
        "generated installer.nsi deletes $INSTDIR\\${MAINBINARYNAME}.exe at "
        "installer.nsi:756, strictly BEFORE NSIS_HOOK_POSTUNINSTALL is "
        "inserted at installer.nsi:838, so a teardown call there can never "
        "execute the exe it depends on"
    )

    # The ordering that makes this safe: the pre-existing ownership preflight
    # gate (with its MB_YESNO prompt and BOTH Abort paths) must run BEFORE
    # the teardown call, so a cancelled/failed uninstall aborts before any
    # state is torn down -- tearing down the service and then aborting would
    # leave a station broken by a cancelled operation.
    preflight_call = "--civiccast-native-uninstall-preflight"
    assert preuninstall.index(preflight_call) < preuninstall.index(teardown_call), (
        "the ownership preflight (with its abort paths) must run before the teardown invocation"
    )
    # And the teardown call must run before the pre-existing taskkill: the
    # running process should be asked to tear itself down (stop its own
    # service) before anything forces it to exit.
    assert preuninstall.index(teardown_call) < preuninstall.index("taskkill.exe"), (
        "the teardown invocation must run before taskkill"
    )

    # The carrying channel: a file-scope Var (not a numbered register), since
    # registers are reused by Tauri's own generated uninstall-section code
    # that runs BETWEEN PREUNINSTALL and POSTUNINSTALL.
    assert "Var CIVICCAST_TEARDOWN_EXIT" in hooks_text, (
        "expected a file-scope Var declared to carry the teardown result "
        "across the PREUNINSTALL/POSTUNINSTALL macro boundary"
    )
    assert "StrCpy $CIVICCAST_TEARDOWN_EXIT" in preuninstall, (
        "PREUNINSTALL must set the carrying Var from the teardown call's exit code"
    )


def test_preuninstall_aborts_before_anything_is_removed_on_a_nonzero_teardown() -> None:
    """BLOCKER (2026-07-31, gauntlet run 17): a nonzero teardown was recorded in
    PREUNINSTALL and the uninstall was ALLOWED TO CONTINUE, deferring the
    refusal to POSTUNINSTALL's tree-retention gate. By the time that gate runs,
    Tauri's generated uninstall Section has already deleted
    "$INSTDIR\\CivicCast Native.exe" (installer.nsi:756), the uninstaller, the
    shortcuts, and the Add/Remove Programs entry -- so the "preserved" tree is
    left with NO product exe (no --civiccast-repair, no re-teardown) and NO
    uninstaller, while NSIS_HOOK_PREINSTALL's install-only gate then refuses
    every future install (120) because that same exe/service is still there.

    A retention gate whose precondition destroys the tools needed to act on it
    is a dead end, not a fail-closed state. The refusal must therefore happen in
    PREUNINSTALL -- before Tauri deletes anything -- so the machine stays fully
    intact and RE-UNINSTALLABLE.

    Pinned structurally (SetErrors + Abort, in that branch, before taskkill),
    the same shape this macro's ownership-preflight refusals already use."""

    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    preuninstall = _preuninstall_block(hooks_text)

    guard = "${If} $CIVICCAST_TEARDOWN_EXIT != 0"
    assert guard in preuninstall, (
        "PREUNINSTALL must branch on a NONZERO carried teardown exit and refuse "
        "there, not defer the refusal to POSTUNINSTALL (run-17 dead end)"
    )
    guard_index = preuninstall.index(guard)
    refusal_branch = preuninstall.split(guard, 1)[1].split("${EndIf}", 1)[0]

    assert "SetErrors" in refusal_branch and "Abort" in refusal_branch, (
        "the nonzero-teardown branch must SetErrors + Abort (the same refusal "
        "shape the ownership-preflight branches above use); recording the code "
        "and continuing is exactly the run-17 defect"
    )

    # F5a (2026-07-31): the refusal must carry a DISTINCT exit code. A bare
    # Abort returns the generic NSIS script-abort code 2 -- the same code the
    # ownership-DECLINE aborts in this macro return -- so an unattended
    # uninstall could not tell "the operator declined" from "the teardown
    # failed and this machine still has a live service". SetErrorLevel-then-
    # Abort preserves the custom code (MEASURED on this product's own makensis:
    # .agent-runs/native-windows/ws5-installer/evidence/nsis-errorlevel-probe/
    # RESULTS.md -- level 41 then Abort exited 41, where Abort alone exited 2).
    # Comment lines are stripped first: this is an ORDERING claim about executed
    # instructions, and NSIS comments mentioning "Abort" must not satisfy (or
    # break) it.
    refusal_code = [
        line.strip()
        for line in refusal_branch.splitlines()
        if line.strip() and not line.strip().startswith(";")
    ]
    assert "SetErrorLevel 82" in refusal_code, (
        "the uninstall-refusal branch must SetErrorLevel 82 so its exit code is "
        "distinguishable from the generic script-abort 2 the ownership-decline "
        f"aborts return; branch instructions were {refusal_code}"
    )
    assert refusal_code.index("SetErrorLevel 82") < refusal_code.index("Abort"), (
        "SetErrorLevel must come BEFORE the Abort -- that ordering is what the "
        "in-repo makensis probe proved preserves the custom code (Abort alone "
        "returns the generic 2)"
    )
    assert "CIVICCAST_ALERT" in refusal_branch, (
        "the refusal must be surfaced to the operator, not silent"
    )
    # The user-facing text must say the uninstall was ABORTED and that nothing
    # was removed -- the old wording described a partial removal, which is now
    # the opposite of what happened.
    assert "ABORTED" in refusal_branch and "NOTHING was removed" in refusal_branch, (
        "the alert text must tell the operator the uninstall was aborted and "
        "that nothing was removed, so they know the machine is still intact"
    )
    # ...and it must name the recovery, which is what makes this RECOVERABLE
    # fail-closed rather than merely fail-closed.
    assert "sc stop CivicCastSupervisor" in refusal_branch, (
        "the alert must name the concrete recovery step (stop the service, then "
        "run Uninstall again)"
    )

    # ORDERING: the refusal must come AFTER the teardown call it reads (it has
    # nothing to judge otherwise) and BEFORE the taskkill -- and, being an
    # Abort, before every removal step in the generated Section that follows
    # this macro.
    assert preuninstall.index("--civiccast-teardown-native-state") < guard_index, (
        "the refusal gate must read a teardown result that has actually been produced"
    )
    assert guard_index < preuninstall.index("taskkill.exe"), (
        "the refusal must abort before the taskkill, so a refused uninstall does "
        "not even force the running product to exit"
    )

    # And the POSTUNINSTALL retention gate must SURVIVE as defense in depth:
    # it still covers the unset-Var case (a hand-edited or foreign hook file
    # where this Abort was never reached).
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]
    assert "${If} $CIVICCAST_TEARDOWN_EXIT == 82" in postuninstall, (
        "the PREUNINSTALL abort must ADD a gate, not replace POSTUNINSTALL's tree-retention gate"
    )


def test_postuninstall_recursive_removal_is_gated_on_carried_teardown_result() -> None:
    """CRITICAL fix (2026-07-30 adversarial review), REHOMED (2026-07-30
    Blocker fix, see test_native_teardown_invocation_lives_in_preuninstall_
    not_postuninstall for why): NSIS_HOOK_POSTUNINSTALL's recursive RMDir /r
    block must not run when the teardown's "stop service" step could not be
    confirmed -- deleting the tree out from under a possibly still-running
    LocalSystem service (and its long-lived postgres.exe/nats-server.exe
    children) would corrupt data.

    native_service_registration::teardown_exit_code (Rust) returns a
    DISTINCT exit code (82) specifically for that case, separate from the
    generic 80 used for any OTHER step's failure (firewall rule, registry
    values) -- which must NOT block tree removal, or a leftover firewall
    rule would strand gigabytes of data for no safety reason.

    Now that the teardown CLI is invoked in PREUNINSTALL (not here -- see the
    sibling test above), this gate can no longer read the exit code directly
    from a `Pop $0` in this macro; it must read the value PREUNINSTALL
    carried across in the $CIVICCAST_TEARDOWN_EXIT file-scope Var instead.
    This pins that the gate branches on exactly that Var == 82 (never a bare
    $0, which is not guaranteed to still hold the teardown result by the
    time POSTUNINSTALL runs), and that an unset Var (PREUNINSTALL somehow did
    not run) defaults to the same fail-closed 82 behavior."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    # Prose (comment lines, starting with ';') may still legitimately discuss
    # --civiccast-teardown-native-state -- only an EXECUTABLE nsExec call is
    # forbidden here.
    postuninstall_executable = "\n".join(
        line for line in postuninstall.splitlines() if not line.strip().startswith(";")
    )
    assert "--civiccast-teardown-native-state" not in postuninstall_executable, (
        "the teardown CLI must not be invoked (again) in POSTUNINSTALL"
    )

    # Safe default: an empty (never-set) carrying Var must be defaulted to
    # "82" -- the same fail-closed behavior as an explicit unconfirmed stop --
    # BEFORE the gate below reads it.
    default_check = '${If} $CIVICCAST_TEARDOWN_EXIT == ""'
    assert default_check in postuninstall, (
        "expected POSTUNINSTALL to default an unset CIVICCAST_TEARDOWN_EXIT "
        "(PREUNINSTALL did not run) to a safe (fail-closed) value"
    )
    default_index = postuninstall.index(default_check)
    default_branch = postuninstall.split(default_check, 1)[1].split("${EndIf}", 1)[0]
    assert 'StrCpy $CIVICCAST_TEARDOWN_EXIT "82"' in default_branch, (
        "an unset CIVICCAST_TEARDOWN_EXIT must default to 82 (service-stop-"
        "unconfirmed), not 0 (proceed) or any other value that would let "
        "tree removal proceed without confirmation"
    )

    guard = "${If} $CIVICCAST_TEARDOWN_EXIT == 82"
    assert guard in postuninstall, (
        "expected NSIS_HOOK_POSTUNINSTALL to branch on the carried "
        "CIVICCAST_TEARDOWN_EXIT Var, not a fresh nsExec call or a bare $0"
    )
    guard_index = postuninstall.index(guard)
    assert default_index < guard_index, (
        "the safe-default check must run before the gate that reads the Var"
    )

    rmdir_call = 'RMDir /r "$INSTDIR\\runtime"'
    assert rmdir_call in postuninstall
    rmdir_index = postuninstall.index(rmdir_call)
    assert guard_index < rmdir_index, (
        "the exit==82 branch must be evaluated before the recursive RMDir block it is meant to gate"
    )

    # The exit==82 branch must set a latch register to "1"; the SAME latch
    # must gate the RMDir block behind an ${Else} (i.e. RMDir is skipped
    # exactly when exit==82 fired).
    branch_82 = postuninstall.split(guard, 1)[1].split("${ElseIf}", 1)[0]
    match = re.search(r'StrCpy\s+(\$R\d)\s+"1"', branch_82)
    assert match, 'expected the exit==82 branch to set a latch register to "1"'
    latch_register = match[1]

    rmdir_guard = f'${{If}} {latch_register} == "1"'
    between = postuninstall[guard_index:rmdir_index]
    assert rmdir_guard in between, (
        f"expected the recursive RMDir block to be preceded by an ${{If}} "
        f'{latch_register} == "1" check gating it off'
    )
    rmdir_guard_index = postuninstall.index(rmdir_guard, guard_index)
    else_index = postuninstall.index("${Else}", rmdir_guard_index)
    assert rmdir_guard_index < else_index < rmdir_index, (
        "RMDir /r must live in the ${Else} branch of the service-stop-"
        "confirmed latch, i.e. skipped when the service could not be "
        "confirmed stopped"
    )

    # Over-refusal guard: any OTHER real teardown failure (the generic 80
    # path) must NOT set the same latch, or a leftover firewall rule would
    # also strand the whole program tree.
    other_failure_guard = "${ElseIf} $CIVICCAST_TEARDOWN_EXIT != 0"
    assert other_failure_guard in postuninstall
    other_failure_branch = postuninstall.split(other_failure_guard, 1)[1].split("${Else}", 1)[0]
    assert f'StrCpy {latch_register} "1"' not in other_failure_branch, (
        "a generic (non-82) teardown failure must NOT set the tree-removal "
        "latch -- only an unconfirmed service stop may refuse tree removal"
    )


def test_no_postuninstall_macro_invokes_the_deleted_product_executable() -> None:
    """The GENERAL rule this Blocker violated (see
    test_native_teardown_invocation_lives_in_preuninstall_not_postuninstall
    for the specific defect): Tauri's generated uninstall Section deletes
    $INSTDIR\\${MAINBINARYNAME}.exe (installer.nsi:756) BEFORE
    NSIS_HOOK_POSTUNINSTALL is ever inserted (installer.nsi:838). ANY future
    NSIS_HOOK_POSTUNINSTALL step that invokes "$INSTDIR\\CivicCast Native.exe"
    -- regardless of which CLI subcommand -- would repeat this exact class of
    defect: a call to an executable that no longer exists at that point in
    the chain, silently failing to launch while the uninstall still reports
    success. This test pins the rule generally, independent of the specific
    --civiccast-teardown-native-state call the Blocker was about, so a future
    regression (e.g. someone adding a NEW POSTUNINSTALL step that shells out
    to the product exe) is caught even if it is not a teardown call."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    assert '"$INSTDIR\\CivicCast Native.exe"' not in postuninstall, (
        "POSTUNINSTALL must never invoke the product executable by path -- "
        "Tauri's generated uninstall Section deletes it (installer.nsi:756) "
        "before POSTUNINSTALL runs (installer.nsi:838), so any such call can "
        "never actually execute"
    )


# ---------------------------------------------------------------------------
# Caption floor-tier binding cross-check (2026-07-30): the owner's BINDING
# ruling (OWNER-DECISION-caption-adaptive-tier.md) named `medium` as the
# mandatory CPU-only floor tier. That binding was landed in
# civiccast/native/caption_tiers.py (the Python source of truth) and
# hand-mirrored into civiccast/apps/installer/src-tauri/src/native_packs.rs's
# own caption_tier_registry() -- the THIRD instance on this project of the
# same contract independently hand-written in Python and Rust with nothing
# comparing them (the first two: the app-payload component identity check
# above, and the D3 exit-code contract check above that). Both of those were
# closed by a test that reads BOTH files and cross-checks; this is that test
# for the floor tier binding.
#
# Without this test, a drift is invisible to either side alone: the Python
# unit test (tests/native/test_caption_tiers.py) only checks the registry
# against ITSELF, and the Rust unit tests
# (native_packs::tests::caption_tier_registry_floor_entry_is_bound_to_the_
# owner_ruled_medium_model et al.) only check Rust's OWN transcription
# against itself. A pack built from the real Python registry with a byte
# that disagrees with Rust's transcription would verify fine in Python and
# then be silently REJECTED (or worse, silently ACCEPTED with the wrong
# bytes) by the installer at install time -- exactly the failure mode this
# whole WP1 caption-tier effort exists to prevent, just moved one binding
# later.
# ---------------------------------------------------------------------------

NATIVE_PACKS_RS = INSTALLER / "src" / "native_packs.rs"


def _rust_str_const(source: str, name: str) -> str:
    # Visibility prefix optional: the component-acquisition engine (83bc7cf6)
    # widened these constants to pub(crate) so it can reuse the one pinned
    # mirror instead of re-transcribing it. The PINNED VALUES are unchanged;
    # only the declaration syntax grew a visibility modifier.
    # \s* after `=`: rustfmt wraps long values onto the next line.
    match = re.search(
        rf'(?:pub(?:\(crate\))?\s+)?const {re.escape(name)}: &str =\s*"([^"]*)";', source
    )
    assert match is not None, f"could not find `const {name}: &str = ...;` in {NATIVE_PACKS_RS}"
    return match[1]


def _rust_floor_tier_files(source: str) -> dict[str, tuple[int, str]]:
    """Parse the ``CAPTION_FLOOR_TIER_MODEL_FILES`` array literal out of the
    Rust source with a regex (mirrors the ast-without-import precedent used
    for the D3 exit-code cross-check above): each entry is
    ``("name", bytes, "sha256")``, bytes may carry `_` digit separators."""

    array_match = re.search(
        r"const CAPTION_FLOOR_TIER_MODEL_FILES:\s*\[\(&str, u64, &str\);\s*\d+\]\s*=\s*\[(.*?)\];",
        source,
        re.DOTALL,
    )
    assert array_match is not None, (
        f"could not find the CAPTION_FLOOR_TIER_MODEL_FILES array literal in {NATIVE_PACKS_RS}"
    )
    entries: dict[str, tuple[int, str]] = {}
    entry_pattern = re.compile(
        r'\(\s*"([^"]+)"\s*,\s*([0-9_]+)\s*,\s*"([0-9a-f]{64})"\s*,?\s*\)', re.DOTALL
    )
    for name, bytes_literal, sha256 in entry_pattern.findall(array_match[1]):
        entries[name] = (int(bytes_literal.replace("_", "")), sha256)
    assert entries, (
        f"parsed zero file entries out of CAPTION_FLOOR_TIER_MODEL_FILES in {NATIVE_PACKS_RS}"
    )
    return entries


def test_caption_floor_tier_binding_matches_across_python_and_rust() -> None:
    from civiccast.native.caption_tiers import CAPTION_TIER_REGISTRY, FLOOR_TIER_ID

    python_spec = CAPTION_TIER_REGISTRY[FLOOR_TIER_ID].require_bound()

    rust_source = NATIVE_PACKS_RS.read_text(encoding="utf-8")
    rust_model_root = _rust_str_const(rust_source, "CAPTION_FLOOR_TIER_MODEL_ROOT")
    rust_repository = _rust_str_const(rust_source, "CAPTION_FLOOR_TIER_MODEL_REPOSITORY")
    rust_revision = _rust_str_const(rust_source, "CAPTION_FLOOR_TIER_MODEL_REVISION")
    rust_files = _rust_floor_tier_files(rust_source)

    # Model directory: `models/<model_directory>` on both sides.
    assert rust_model_root == f"models/{python_spec.model_directory}", (
        f"Rust CAPTION_FLOOR_TIER_MODEL_ROOT ({rust_model_root!r}) disagrees with Python's "
        f"CAPTION_TIER_REGISTRY[{FLOOR_TIER_ID!r}].model_directory "
        f"({python_spec.model_directory!r}) in {NATIVE_PACKS_RS}"
    )
    assert rust_repository == python_spec.model_repository, (
        f"Rust CAPTION_FLOOR_TIER_MODEL_REPOSITORY ({rust_repository!r}) disagrees with "
        f"Python's model_repository ({python_spec.model_repository!r})"
    )
    assert rust_revision == python_spec.model_revision, (
        f"Rust CAPTION_FLOOR_TIER_MODEL_REVISION ({rust_revision!r}) disagrees with "
        f"Python's model_revision ({python_spec.model_revision!r})"
    )

    # Exact file set, and each file's own size and sha256 -- in BOTH
    # directions: a file Rust has that Python does not (or vice versa) is
    # just as much a drift as a mismatched size or hash.
    assert set(rust_files) == set(python_spec.files), (
        f"floor tier file sets disagree: Rust has {sorted(rust_files)}, "
        f"Python has {sorted(python_spec.files)}"
    )
    for name, (python_bytes, python_sha256) in python_spec.files.items():
        rust_bytes, rust_sha256 = rust_files[name]
        assert (rust_bytes, rust_sha256) == (python_bytes, python_sha256), (
            f"floor tier file {name!r} disagrees between Python "
            f"({python_bytes}, {python_sha256}) and Rust ({rust_bytes}, {rust_sha256})"
        )


# ---------------------------------------------------------------------------
# INSTALL-OVER-EXISTING UPGRADE (2026-08-31 finalization floor): setup must
# accept a healthy live install, invoke the OLD bootstrap's already-proven
# native service-stop before Tauri or pack staging replaces anything, and only
# continue after the service is confirmed stopped. The quiesce operation must
# preserve service registration plus InstalledVersion and DatabaseUrl so D3
# still routes the run through backup/migration/rollback as a real upgrade.
# A service registration with no bootstrap, or any nonzero stop result,
# remains a fail-closed condition carrying a distinct installer exit code.
# ---------------------------------------------------------------------------


def test_preinstall_quiesces_a_live_install_without_erasing_upgrade_identity() -> None:
    """A normal existing install is an upgrade input, not an automatic refusal.

    The OLD bootstrap must run its production service-stop command before
    taskkill or any later tree work. It must not run uninstall teardown: that
    command deletes InstalledVersion and DatabaseUrl and unregisters the
    service, causing D3 to misroute this live upgrade as a fresh install.
    """
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    preinstall = _preinstall_block(hooks_text)

    assert re.search(r"^!define CIVICCAST_EXIT_UPGRADE_QUIESCE\s+120$", hooks_text, re.MULTILINE), (
        "expected CIVICCAST_EXIT_UPGRADE_QUIESCE to be reserved at exactly 120"
    )
    assert "CIVICCAST_EXIT_INSTALL_OVER_EXISTING" not in hooks_text

    classify_idx = preinstall.index("preinstall: classify existing install for upgrade")
    stop_idx = preinstall.index("--civiccast-stop-native-service")
    taskkill_idx = preinstall.index("taskkill.exe")
    assert classify_idx < stop_idx < taskkill_idx, (
        "the old bootstrap must quiesce registered native state before the GUI stop "
        "or any generated/postinstall tree replacement can begin"
    )
    assert "--civiccast-teardown-native-state" not in preinstall
    assert '${FileExists} "$INSTDIR\\CivicCast Native.exe"' in preinstall
    assert "preinstall: existing install service stop returned $0" in preinstall


def test_preinstall_quiesce_failure_aborts_instead_of_continuing_into_tree_work() -> None:
    """A failed or ambiguous old-service stop cannot be advisory.

    Continuing would let the generated installer and pack stage overwrite
    binaries beneath running writers. The nonzero branch must fail through
    CIVICCAST_FAIL before taskkill/tree work.
    """
    preinstall = _preinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    quiesce_region = preinstall.split("--civiccast-stop-native-service", 1)[1].split(
        "taskkill.exe", 1
    )[0]

    assert "${If} $0 != 0" in quiesce_region
    assert "!insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE}" in quiesce_region
    assert "continuing anyway" not in quiesce_region.lower(), (
        "a nonzero native service-stop result must never be logged and ignored"
    )


def test_preinstall_fails_closed_on_registered_service_without_old_bootstrap() -> None:
    """The service-only partial-install shape cannot be safely auto-upgraded.

    With no old bootstrap there is no trusted teardown command to invoke, so
    setup must refuse before replacement work and name the repair condition.
    """
    preinstall = _preinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert (
        "sc.exe" in preinstall and "query" in preinstall and "CivicCastSupervisor" in preinstall
    ), "expected preinstall classification to query the real SCM registration"
    unsafe_region = preinstall.split("${ElseIf} $R5 == 0", 1)[1].split("${Else}", 1)[0]
    assert "bootstrap is missing" in unsafe_region.lower()
    assert "!insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE}" in unsafe_region


def test_preinstall_treats_only_scm_1060_as_a_definitive_fresh_install() -> None:
    """An unreadable SCM result cannot be downgraded to "service absent".

    Access denied, process-launch failure, plug-in errors, and every other
    ambiguous result must stop before replacement work. Only Windows service
    error 1060 is authoritative evidence that the service does not exist.
    """
    preinstall = _preinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    no_bootstrap = preinstall.split("${ElseIf} $R5 == 0", 1)[1]

    fresh_branch, ambiguous_branch = no_bootstrap.split("${ElseIf} $R5 == 1060", 1)[1].split(
        "${Else}", 1
    )
    assert "fresh install" in fresh_branch.lower()
    assert "CIVICCAST_FAIL" not in fresh_branch
    assert "classification inconclusive" in ambiguous_branch.lower()
    assert "!insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE}" in ambiguous_branch


def test_preinstall_normal_upgrade_has_no_uninstall_first_or_install_only_message() -> None:
    preinstall = _preinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    executable = "\n".join(
        line for line in preinstall.splitlines() if not line.strip().startswith(";")
    ).lower()

    assert "install-only" not in executable
    assert "does not support installing over" not in executable
    assert "uninstall civiccast (native) first" not in executable


def test_upgrade_quiesce_is_covered_by_the_reserved_exit_code_invariants() -> None:
    """The fail-closed quiesce path keeps code 120 distinct and actionable."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    codes = {
        name: int(value)
        for name, value in re.findall(
            r"^!define (CIVICCAST_EXIT_[A-Z0-9_]+)\s+(\d+)$",
            hooks_text,
            re.MULTILINE,
        )
    }
    assert codes.get("CIVICCAST_EXIT_UPGRADE_QUIESCE") == 120
    reserved = {0, 10, 20, 30, 40, 70, 73, 75, 76, 79, 81}
    other_codes = {
        name: code for name, code in codes.items() if name != "CIVICCAST_EXIT_UPGRADE_QUIESCE"
    }
    assert 120 not in other_codes.values(), "120 collides with an existing installer exit code"
    assert 120 not in reserved, "120 collides with a reserved product/D3 contract code"


# ---------------------------------------------------------------------------
# Run-18 forensic diagnosis (2026-07-31): P1 QuietUninstallString reachability,
# P2 the resulting `_?=` self-delete tradeoff, P3 timestamped breadcrumbs.
# ---------------------------------------------------------------------------


def test_postinstall_registers_quietuninstallstring_with_inplace_uninstall_flag() -> None:
    """P1 fix: Tauri's generated installer.nsi registers ONLY UninstallString
    (installer.nsi:676, `"$INSTDIR\\uninstall.exe"`, no `_?=`) and never
    writes a QuietUninstallString anywhere (confirmed by reading the full
    generated file -- it contains zero occurrences of that key). With no
    `_?=`, NSIS's own exehead behavior (measured on this product's own
    makensis: .agent-runs/native-windows/ws5-installer/evidence/
    nsis-errorlevel-probe/) is that any caller invoking UninstallString
    respawns a %TEMP% copy of the uninstaller and the ORIGINAL process --
    the one a waiting caller such as winget/Intune/a deployment script
    actually holds a handle to -- exits 0 as soon as that respawn launches,
    before NSIS_HOOK_PREUNINSTALL's own SetErrorLevel 82 refusal (see that
    macro) ever runs in the respawned copy. A caller relying on
    UninstallString's exit code can therefore never observe that refusal.

    `_?=$INSTDIR` disables the respawn: the uninstaller then runs in-place,
    synchronously, in the caller's own process, so its real exit code
    becomes observable. Registered as QuietUninstallString (not an
    overwrite of UninstallString, which Windows' own interactive Apps &
    Features "Uninstall" button reads and must keep respawning/self-
    deleting for a human-driven uninstall) -- QuietUninstallString is the
    distinct key modern unattended deployment tooling (winget, Intune)
    prefers when present."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = hooks_text.split("!macro NSIS_HOOK_POSTINSTALL", 1)[1].split("!macroend", 1)[0]

    write_lines = [
        line.strip()
        for line in postinstall.splitlines()
        if "WriteRegStr" in line and "QuietUninstallString" in line
    ]
    assert write_lines, (
        "expected NSIS_HOOK_POSTINSTALL to register a QuietUninstallString "
        "value under UNINSTKEY -- without it, an unattended caller invoking "
        "UninstallString can never observe the PREUNINSTALL SetErrorLevel 82 "
        "refusal (NSIS respawns a %TEMP% copy and the caller's process exits "
        "0 before the refusal ever runs)"
    )
    write_line = write_lines[0]

    assert write_line.startswith('WriteRegStr SHCTX "${UNINSTKEY}" "QuietUninstallString"'), (
        "the QuietUninstallString write must use the same SHCTX/UNINSTKEY "
        f"convention as every other uninstall registry write; got: {write_line!r}"
    )
    assert '"$INSTDIR\\uninstall.exe"' in write_line, (
        "the quiet uninstall command must invoke the same uninstall.exe "
        f"Tauri's own UninstallString points at; got: {write_line!r}"
    )
    assert "/S" in write_line, (
        f"the quiet uninstall command must pass /S (silent); got: {write_line!r}"
    )
    assert "_?=$INSTDIR" in write_line, (
        "the quiet uninstall command must carry `_?=$INSTDIR` so the "
        "uninstaller runs in-place instead of respawning a %TEMP% copy that "
        "exits the caller's process with 0 before the real work -- and its "
        f"exit code -- ever happens; got: {write_line!r}"
    )

    # Must run at the END of POSTINSTALL, after Tauri's own UNINSTKEY writes
    # (installer.nsi:670-689, which themselves run BEFORE NSIS_HOOK_POSTINSTALL
    # is inserted at installer.nsi:703-705) -- so nothing in this file's own
    # POSTINSTALL chain can still overwrite it afterward, and if a future
    # Tauri version starts writing its own QuietUninstallString, this write
    # (running last) wins.
    write_index = postinstall.index(write_line)
    installed_version_write = (
        'WriteRegStr HKLM "Software\\CivicCast\\Native" "InstalledVersion" "${VERSION}"'
    )
    assert installed_version_write in postinstall
    assert postinstall.index(installed_version_write) < write_index, (
        "the QuietUninstallString registration must come after the "
        "InstalledVersion write, i.e. at the very end of the successful "
        "POSTINSTALL chain"
    )


def test_postuninstall_reboots_ok_deletes_uninstaller_and_instdir_after_inplace_run() -> None:
    """P2 fix: the P1 QuietUninstallString fix's `_?=$INSTDIR` flag means the
    uninstaller runs in-place and can never self-delete -- MEASURED live: a
    `_?=` run left exactly one file behind (uninstall.exe, filesLeft=1)
    inside an otherwise-empty $INSTDIR that the existing `RMDir /r
    "$INSTDIR"` could not remove for the same reason (the running exe is
    always the one member RMDir /r cannot touch). `/REBOOTOK` schedules both
    for deletion at the next boot -- a no-op on the ordinary (non-`_?=`)
    uninstall path, where Tauri's own Delete/RMDir (installer.nsi:769/771)
    already removed both. Pinned: both calls exist, in child-then-parent
    order, and live INSIDE the same service-stop-confirmed `${Else}` branch
    as the pre-existing recursive RMDir /r block -- i.e. they must be
    skipped exactly when tree removal itself is skipped (teardown exit 82),
    never run unconditionally."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    delete_call = 'Delete /REBOOTOK "$INSTDIR\\uninstall.exe"'
    rmdir_call = 'RMDir  /REBOOTOK "$INSTDIR"'
    assert delete_call in postuninstall, (
        "expected a /REBOOTOK Delete of $INSTDIR\\uninstall.exe so the "
        "uninstaller (which cannot self-delete on the `_?=` in-place path) "
        "is cleaned up at the next reboot"
    )
    assert rmdir_call in postuninstall, (
        "expected a /REBOOTOK RMDir of $INSTDIR so the now-empty install "
        "directory (which cannot be removed while uninstall.exe still "
        "occupies it) is cleaned up at the next reboot"
    )
    delete_index = postuninstall.index(delete_call)
    reboot_rmdir_index = postuninstall.index(rmdir_call)
    assert delete_index < reboot_rmdir_index, (
        "the uninstaller file must be scheduled for deletion BEFORE its "
        "now-empty parent directory, matching RMDir /r's own child-then-"
        "parent convention immediately above"
    )

    # Both calls must live in the SAME ${Else} branch as the pre-existing
    # recursive RMDir /r block (the service-stop-confirmed path), i.e. gated
    # off exactly when tree removal itself is skipped ($R2 == "1", teardown
    # exit 82) -- never run unconditionally, which would attempt to schedule
    # deletion of a tree this same run just refused to touch.
    rmdir_guard = '${If} $R2 == "1"'
    assert rmdir_guard in postuninstall
    rmdir_guard_index = postuninstall.index(rmdir_guard)
    else_index = postuninstall.index("${Else}", rmdir_guard_index)
    endif_index = postuninstall.index("${EndIf}", else_index)
    final_rmdir_r = 'RMDir /r "$INSTDIR"'
    # Disambiguate from the runtime/packs RMDir /r calls above it: the one
    # this fix appends after is the LAST RMDir /r before the reboot-ok Delete.
    rmdir_r_index = postuninstall.rindex(final_rmdir_r, else_index, delete_index)

    assert else_index < rmdir_r_index < delete_index < reboot_rmdir_index < endif_index, (
        "the /REBOOTOK cleanup must run AFTER the existing recursive "
        'RMDir /r "$INSTDIR" and BEFORE the ${EndIf} closing the service-'
        "stop-confirmed ${Else} branch, so it is skipped exactly when tree "
        "removal itself is skipped"
    )


def test_preuninstall_begins_with_a_pid_image_and_cmdline_breadcrumb() -> None:
    """P3 instrumentation: the very first breadcrumb NSIS_HOOK_PREUNINSTALL
    writes, before any probe, prompt, or teardown call, names the running
    uninstaller's own image path ($EXEPATH -- proves which copy is
    executing: the P1 in-place `_?=` copy vs. a stray respawned %TEMP% copy
    from an un-fixed caller) and its full invocation ($CMDLINE -- proves
    whether a caller passed /S, _?=, or neither). Pinned as the first
    executable statement in the macro, ahead of the ownership-preflight
    probe."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    preuninstall = _preuninstall_block(hooks_text)

    begin_step = (
        '!insertmacro CIVICCAST_STEP "preuninstall: BEGIN pid-image=$EXEPATH cmdline=$CMDLINE"'
    )
    assert begin_step in preuninstall, (
        "expected NSIS_HOOK_PREUNINSTALL to open with a breadcrumb naming "
        "$EXEPATH and $CMDLINE, so a diagnosis can tell which copy of the "
        "uninstaller ran and how it was invoked"
    )

    begin_index = preuninstall.index(begin_step)
    first_probe = "nsExec::ExecToStack '\"$INSTDIR\\CivicCast Native.exe\" --civiccast-native-uninstall-preflight'"
    assert first_probe in preuninstall
    assert begin_index < preuninstall.index(first_probe), (
        "the BEGIN breadcrumb must be the first executable statement, "
        "before the ownership-preflight probe"
    )


def test_civiccast_step_prefixes_every_breadcrumb_with_a_runtime_timestamp() -> None:
    """P3 instrumentation: a breadcrumb line alone proves ORDER, not TIMING --
    diagnosing a hang (this file's own header names the run-3/run-4 Sandbox
    hang this log format exists to make diagnosable) needs to know how long
    the install sat on its last logged step. ${__DATE__}/${__TIME__} are
    COMPILE-time constants baked into the installer once at build time
    (every line would carry the identical wrong value) and are explicitly
    forbidden here; the macro must use a RUNTIME time source instead.
    ${GetTime} (FileFunc.nsh, already available by the time this macro is
    ever inserted) is pinned as that source. Every existing breadcrumb call
    site inherits the prefix automatically -- this is the one place the
    format is produced -- and the PREFIX must not consume or reorder any
    existing token, so every known consumer (the PowerShell gauntlet harness,
    which matches with unanchored `-match` and no `^`/`$` anchors at every
    breadcrumb call site) keeps matching."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    step_macro = hooks_text.split("!macro CIVICCAST_STEP TEXT", 1)[1].split("!macroend", 1)[0]

    assert "${__DATE__}" not in step_macro and "${__TIME__}" not in step_macro, (
        "CIVICCAST_STEP must not use the compile-time ${__DATE__}/${__TIME__} "
        "tokens -- every logged line would carry the same wrong (build-time) "
        "value, not the time the step actually ran"
    )
    assert "${GetTime}" in step_macro, (
        "expected CIVICCAST_STEP to call the runtime ${GetTime} macro "
        "(FileFunc.nsh) to compute a per-call timestamp"
    )

    # The FileWrite must write a value built from ${GetTime}'s own output
    # register, immediately followed by the original ${TEXT} parameter
    # UNCHANGED -- i.e. a prefix, not a replacement or reordering.
    filewrite_lines = [
        line.strip() for line in step_macro.splitlines() if line.strip().startswith("FileWrite")
    ]
    assert len(filewrite_lines) == 1, (
        f"expected exactly one FileWrite in CIVICCAST_STEP; got {filewrite_lines}"
    )
    filewrite_line = filewrite_lines[0]
    assert filewrite_line.endswith('${TEXT}$\\r$\\n"'), (
        "the FileWrite must end with the original ${TEXT} parameter "
        f"unchanged, followed by the CRLF terminator; got: {filewrite_line!r}"
    )
    match = re.search(r'FileWrite\s+\$\w+\s+"(\$\w+)\$\{TEXT\}', filewrite_line)
    assert match, (
        "expected the FileWrite to write a single variable holding the "
        f"timestamp prefix immediately before ${{TEXT}}; got: {filewrite_line!r}"
    )
    prefix_var = match[1]

    # That prefix variable must actually be built from ${GetTime}'s output,
    # not a stray unrelated register.
    gettime_call_index = step_macro.index("${GetTime}")
    strcpy_pattern = re.compile(rf'StrCpy\s+{re.escape(prefix_var)}\s+"\[.*\]\s*"')
    strcpy_match = strcpy_pattern.search(step_macro)
    assert strcpy_match, (
        f"expected a StrCpy building {prefix_var} into a bracketed timestamp "
        f"string; step_macro was: {step_macro!r}"
    )
    assert gettime_call_index < step_macro.index(strcpy_match.group(0)), (
        "the timestamp prefix must be built AFTER the ${GetTime} call that supplies its values"
    )

    # Every register CIVICCAST_STEP touches for the timestamp must be
    # Push'd and Pop'd (matching this macro's pre-existing $9 discipline),
    # so it stays neutral for any future caller whose ${TEXT} references one
    # of them.
    pushed = re.findall(r"^\s*Push \$(\w+)", step_macro, re.MULTILINE)
    popped = re.findall(r"^\s*Pop \$(\w+)", step_macro, re.MULTILINE)
    assert pushed, "expected CIVICCAST_STEP to Push every register it uses as scratch"
    assert sorted(pushed) == sorted(popped), (
        "every register CIVICCAST_STEP pushes must be popped (in some order) "
        f"before the macro ends; pushed={sorted(pushed)} popped={sorted(popped)}"
    )
    # LIFO discipline: the pop order must be the exact reverse of the push order.
    assert popped == list(reversed(pushed)), (
        "registers must be popped in exactly the reverse order they were "
        f"pushed (LIFO); pushed={pushed} popped={popped}"
    )


def test_repository_install_progress_matchers_accept_timestamp_prefix() -> None:
    """Protect today's repository-owned parser, not the external July gauntlet.

    The old run18 harness parsed d4-provision; the maintained sandbox reporter
    parses d3 routing, d4 activation, postinstall outcomes and alerts. Exercise
    its actual regexes with both plain and timestamp-prefixed breadcrumbs.
    These patterns use syntax shared by Python and PowerShell's .NET regexes;
    this check does not claim to execute the sandbox reporting workflow.
    """
    source = (ROOT / "sandbox-lab/scripts/In-Sandbox-Report.ps1").read_text(encoding="utf-8")
    capture = source.split("function Invoke-InstallProgressCapture {", 1)[1].split(
        "# <gate-a-audit-MA-25> The clean lane", 1
    )[0]
    samples = (
        ("d3Match", "step d3-engine: evidence route=UPGRADE engine_exit=0", ("UPGRADE", "0")),
        ("d4Match", "step d4-activate-station: returned 0", ("0",)),
        ("poMatch", "postinstall: SUCCESS", ("SUCCESS",)),
        ("stepMatch", "step d4-activate-station: returned 66", ("d4-activate-station", "66")),
    )
    for variable, breadcrumb, expected_groups in samples:
        definition = re.search(rf"\${variable} = \[regex\]::Match\(\$line, '([^']+)'\)", capture)
        assert definition, f"missing repository breadcrumb matcher: {variable}"
        for prefix in ("", "[2026-10-17 09:00:00] "):
            matched = re.search(definition.group(1), prefix + breadcrumb)
            assert matched, f"{variable} rejected breadcrumb prefix {prefix!r}"
            assert matched.groups() == expected_groups
    alert = re.search(r"\$line -match '([^']+)'", capture)
    assert alert, "missing repository alert matcher"
    assert re.search(alert.group(1), "[2026-10-17 09:00:00] ALERT: setup failed")


# ---------------------------------------------------------------------------
# Installer text/metadata honesty (2026-08-01, chain O -- FINDINGS-rewalk-
# dd7f835f.md F-10/F-11/F-12/F-18/F-19/F-20/F-21/F-23). Each group below pins
# one finding's fix directly against the artifact the walkthrough actually
# observed, so a future edit that reintroduces the dishonest text fails here
# before it ever reaches a Sandbox run again.
# ---------------------------------------------------------------------------


def test_stage_packs_success_path_does_not_dump_the_raw_json_manifest_to_the_details_pane() -> None:
    """F-11: 'Show details' dumped a raw JSON manifest at the operator and
    flushed the real step log out of view. Root cause: main.rs's
    --civiccast-stage-packs handler prints the pack-staging report via
    serde_json::to_string_pretty on SUCCESS (println!) and a short human
    message on FAILURE (eprintln!) -- both land in nsExec::ExecToStack's $1,
    and the old hook did an UNCONDITIONAL `DetailPrint "$1"` right after
    popping it, so a successful install showed the pretty-printed JSON blob
    in the wizard's details pane. The pane must carry a human step log; full
    detail belongs in install-progress.log, which the pane must name."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    stage_packs_start = postinstall.index("--civiccast-stage-packs")
    stage_packs_region = postinstall[
        stage_packs_start : postinstall.index("D2 INSTALL-TIME RE-VERIFICATION", stage_packs_start)
    ]

    # No bare, unconditional `DetailPrint "$1"` -- on success that line IS
    # the raw JSON manifest report (native_pack_staging.rs's own success
    # payload). Any DetailPrint of $1 must be wrapped in more context (a
    # step breadcrumb, a labeled failure message), never printed alone.
    for line in stage_packs_region.splitlines():
        assert line.strip() != 'DetailPrint "$1"', (
            "stage-packs must not unconditionally DetailPrint the raw child "
            f"output to the pane -- on success that is the JSON manifest "
            f"report: {line!r}"
        )

    # install-progress.log must still capture the full report (support needs
    # it), and the pane must tell the operator where to find it.
    assert (
        '!insertmacro CIVICCAST_STEP "step stage-packs: manifest report: $1"' in stage_packs_region
    ), "the full manifest report must still be written to install-progress.log via CIVICCAST_STEP"
    assert "install-progress.log" in stage_packs_region, (
        "the pane's success message must name install-progress.log as where the full detail lives"
    )


def test_d2_verify_pack_tree_success_path_does_not_stream_the_raw_json_manifest_to_the_details_pane() -> (
    None
):
    """F-11, test-vs-product gap closure (rewalk of b1c6fe4d): the chain-O fix
    above only touched the STAGE-PACKS step's `DetailPrint "$1"`. The re-walk
    of b1c6fe4d still found the raw JSON manifest flooding the pane -- because
    the actual "Show details" control renders EVERY DetailPrint call NSIS
    fires while POSTINSTALL runs, and the D2 re-verification steps that run
    right after stage-packs (native-server-binaries, native-app-payload,
    native-ffmpeg-runtime, native-ollama-runtime) invoke
    `--civiccast-verify-pack-tree` via
    `nsExec::ExecToLog`, not `ExecToStack` -- a DIFFERENT NSIS plugin call
    that streams 100% of the child's stdout straight into the pane live, with
    no `Pop`/`DetailPrint` choke point to guard at all.

    On success, `run_native_install_verify_cli` (main.rs) serializes the
    FULL `VerifiedPack` -- including its complete `files: Vec<VerifiedPackFile
    { path, bytes, sha256 }>`, one entry per file in the pack (thousands for
    native-app-payload, which bridges to $INSTDIR\\runtime and carries the
    embedded Python payload: `Lib/site-packages/civiccast/alembic/__init__.py`
    is exactly this shape) -- via `serde_json::to_string_pretty` +
    `println!`. ExecToLog has no equivalent of ExecToStack's $1: there is no
    variable to inspect or gate before the pane renders it, so this is a
    STRICTLY WORSE version of the exact bug chain O fixed one step earlier in
    the same macro.

    This is why chain O's own test above could not have caught it: its
    `stage_packs_region` slice explicitly ENDS at the
    "D2 INSTALL-TIME RE-VERIFICATION" marker
    (`postinstall.index("D2 INSTALL-TIME RE-VERIFICATION", stage_packs_start)`),
    which excludes these four ExecToLog calls by construction. This test
    scans the FULL, unsliced postinstall block instead -- the same text that
    becomes the compiled installer.nsi that NSIS's own "Show details"
    listbox renders line-for-line -- so there is no seam left for a fixed
    step to hide a still-broken one beside it.

    Witnessed RED against the unfixed hook file (fails on the original three
    ExecToLog call sites) before the fix; GREEN once each D2 verify-pack-tree
    call is switched to the same guarded ExecToStack pattern stage-packs
    already uses."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    verify_pack_tree = "--civiccast-verify-pack-tree"
    # Only actual nsExec call lines matter here -- the surrounding prose
    # comments also name the flag (documenting what it does), and a comment
    # count is not a behavioral fact. `nsExec::Exec` is the load-bearing
    # substring that turns a mention into an invocation.
    lines = postinstall.splitlines()
    verify_call_lines = [
        line for line in lines if verify_pack_tree in line and "nsExec::Exec" in line
    ]
    occurrences = len(verify_call_lines)
    assert occurrences == 4, (
        f"expected exactly 4 D2 verify-pack-tree call sites (native-server-binaries, "
        f"native-app-payload, native-ffmpeg-runtime, native-ollama-runtime); found {occurrences} -- update this "
        f"test if a component pack was added or removed"
    )

    for line in verify_call_lines:
        assert "nsExec::ExecToStack" in line, (
            "a D2 verify-pack-tree call must use nsExec::ExecToStack (captures output into "
            "$1 behind a Pop, so the pane can be gated) rather than nsExec::ExecToLog "
            f"(streams 100% of the child's stdout -- the full VerifiedPack JSON manifest, "
            f"on success -- directly into the operator-facing details pane, unconditionally "
            f"and un-gateable): {line.strip()!r}"
        )
        assert "nsExec::ExecToLog" not in line, (
            f"D2 verify-pack-tree call still uses ExecToLog, which is exactly how the "
            f"re-walk observed the raw JSON manifest flooding 'Show details': {line.strip()!r}"
        )

    # Each of the four call sites must be immediately followed by a `Pop $1`
    # (mirroring stage-packs) and a success branch that names
    # install-progress.log rather than dumping $1 into the pane bare.
    for component, destination in (
        ("native-server-binaries", '"$INSTDIR\\packs\\native-server-binaries\\payload"'),
        ("native-app-payload", '"$INSTDIR\\runtime"'),
        ("native-ffmpeg-runtime", '"$INSTDIR\\dependencies\\ffmpeg"'),
        ("native-ollama-runtime", '"$INSTDIR\\dependencies\\ollama"'),
    ):
        call_marker = f"--destination {destination} --expected-component {component}"
        assert call_marker in postinstall, (
            f"missing D2 verify-pack-tree call for {component} targeting {destination}"
        )
        call_idx = postinstall.index(call_marker)
        # The next ~1400 chars after the call cover Pop $0 / Pop $1 / the
        # success-vs-failure branch for this component (each block is short;
        # the ffmpeg-runtime block needs the most room for its extra
        # FileExists-guard indentation).
        window = postinstall[call_idx : call_idx + 1400]
        assert "Pop $1" in window, (
            f"{component} D2 verify-pack-tree call must capture output into $1 "
            f"(ExecToStack's Pop $0 / Pop $1 pair) so the success branch can gate it"
        )
        for line in window.splitlines():
            assert line.strip() != 'DetailPrint "$1"', (
                f"{component} D2 verification must not unconditionally DetailPrint the raw "
                f"child output -- on success that is the full VerifiedPack JSON manifest "
                f"(every file's path/bytes/sha256): {line!r}"
            )
        assert "install-progress.log" in window, (
            f"{component} D2 verification's success line must name install-progress.log as "
            f"where the full per-file manifest report lives, matching the stage-packs pattern"
        )


# ---------------------------------------------------------------------------
# F-19: the uninstaller's "Delete the application data" checkbox never said
# what or where -- an operator could reasonably read "application data" as
# their meeting recordings. Fixed via Tauri's documented customLanguageFiles
# override (NsisConfig::custom_language_files in tauri-utils), never by
# patching Tauri's own generated installer.nsi.
# ---------------------------------------------------------------------------

NATIVE_LANG_FILE = INSTALLER / "nsis-lang-native-english.nsh"

# The complete stock LangString key set Tauri's own bundled English.nsh
# defines (github.com/tauri-apps/tauri/blob/dev/crates/tauri-bundler/src/
# bundle/windows/nsis/languages/English.nsh, fetched 2026-08-01).
# customLanguageFiles REPLACES the language file wholesale -- a custom file
# missing any of these keys would leave Tauri's generated installer.nsi
# referencing an undefined LangString and fail to compile.
STOCK_ENGLISH_LANGSTRING_KEYS = frozenset(
    {
        "addOrReinstall",
        "alreadyInstalled",
        "alreadyInstalledLong",
        "appRunning",
        "appRunningOkKill",
        "chooseMaintenanceOption",
        "choowHowToInstall",
        "createDesktop",
        "dontUninstall",
        "dontUninstallDowngrade",
        "failedToKillApp",
        "installingWebview2",
        "newerVersionInstalled",
        "older",
        "olderOrUnknownVersionInstalled",
        "silentDowngrades",
        "unableToUninstall",
        "uninstallApp",
        "uninstallBeforeInstalling",
        "unknown",
        "webview2AbortError",
        "webview2DownloadError",
        "webview2DownloadSuccess",
        "webview2Downloading",
        "webview2InstallError",
        "webview2InstallSuccess",
        "deleteAppData",
    }
)


def _effective_native_nsis_config() -> dict:
    effective = _deep_merge(_load(BASE_CONFIG), _load(NATIVE_CONFIG))
    return _nsis(effective)


def test_native_config_overrides_delete_app_data_via_documented_custom_language_file() -> None:
    """The native overlay must wire Tauri's documented customLanguageFiles
    mechanism (not a hand-patch of Tauri's generated installer.nsi) to
    override the stock deleteAppData string, and must declare English in
    languages (customLanguageFiles' key must be a language present in that
    array, per NsisConfig::custom_language_files's own doc note)."""
    nsis = _effective_native_nsis_config()
    assert nsis.get("languages") == ["English"], (
        "customLanguageFiles' key must be present in the declared languages array"
    )
    assert nsis.get("customLanguageFiles") == {"English": "nsis-lang-native-english.nsh"}


def test_native_custom_language_file_exists_and_defines_every_stock_key() -> None:
    """A custom language file that is missing a stock key would leave
    Tauri's generated installer.nsi referencing an undefined LangString and
    fail to compile -- this must never regress silently."""
    assert NATIVE_LANG_FILE.is_file(), f"missing native custom language file: {NATIVE_LANG_FILE}"
    text = NATIVE_LANG_FILE.read_text(encoding="utf-8")
    defined_keys = set(re.findall(r"^LangString (\w+) \$\{LANG_ENGLISH\}", text, re.MULTILINE))
    missing = STOCK_ENGLISH_LANGSTRING_KEYS - defined_keys
    assert not missing, (
        f"native custom language file is missing stock LangString keys: {sorted(missing)}"
    )
    # Every OTHER key must be byte-identical to Tauri's own stock text -- this
    # file exists to change exactly one string, not to silently drift the
    # rest of the installer's copy.
    unexpected = defined_keys - STOCK_ENGLISH_LANGSTRING_KEYS
    assert not unexpected, f"unexpected LangString keys not in the stock set: {sorted(unexpected)}"


def test_delete_app_data_text_names_the_real_location_and_is_not_the_stock_dishonest_string() -> (
    None
):
    """F-19 fix: the rewritten string must name the true preserved-data
    location (C:\\ProgramData\\CivicCast, matching $COMMONPROGRAMDATA -- see
    NSIS_HOOK_POSTUNINSTALL's own closing comment) and must no longer be
    Tauri's bare stock string, which named neither what nor where."""
    text = NATIVE_LANG_FILE.read_text(encoding="utf-8")
    match = re.search(r'LangString deleteAppData \$\{LANG_ENGLISH\} "([^"]*)"', text)
    assert match is not None, "deleteAppData LangString not found"
    rewritten = match.group(1)
    assert rewritten != "Delete the application data", (
        "deleteAppData must no longer be Tauri's bare stock string -- it named neither what nor where"
    )
    assert "C:\\ProgramData\\CivicCast" in rewritten, (
        "deleteAppData must name the real preserved-data location"
    )


def test_delete_app_data_text_matches_what_the_uninstaller_actually_leaves_untouched() -> None:
    """Cross-check against the code, not just prose: NSIS_HOOK_POSTUNINSTALL's
    own closing comment states, as deliberate product design, that
    $COMMONPROGRAMDATA\\CivicCast is never touched by uninstall regardless of
    this checkbox. The rewritten checkbox text must agree with that same
    claim, so the two cannot silently drift apart."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]
    assert "$COMMONPROGRAMDATA\\CivicCast is deliberately NEVER touched" in postuninstall, (
        "expected NSIS_HOOK_POSTUNINSTALL's own comment confirming the preserve-data design "
        "this checkbox text relies on"
    )

    lang_text = NATIVE_LANG_FILE.read_text(encoding="utf-8")
    match = re.search(r'LangString deleteAppData \$\{LANG_ENGLISH\} "([^"]*)"', lang_text)
    assert match is not None
    rewritten = match.group(1)
    assert any(phrase in rewritten for phrase in ("always stay", "always kept", "never remove")), (
        "the checkbox text must affirmatively state that CivicCast data is preserved, "
        "matching the uninstaller's actual preserve-data behavior"
    )


# ---------------------------------------------------------------------------
# F-18 / F-21: Publisher showed as the lowercase "civiccast" (Tauri's own
# documented default when bundle.publisher is unset: "the second element in
# the identifier string" -- org.civiccast.native's second element), and every
# page showed the stock "Nullsoft Install System v3.11" branding line because
# bundle.copyright was unset, so Tauri's generated `BrandingText
# "${COPYRIGHT}"` compiled to an empty string and NSIS fell back to its own
# default. Both are fixed via Tauri's own documented BundleConfig fields
# (confirmed against tauri-utils 2.9.2's config.rs), never by hand-patching
# Tauri's generated installer.nsi.
# ---------------------------------------------------------------------------


def test_native_config_sets_an_honest_publisher_not_the_lowercase_identifier_fragment() -> None:
    nsis_bundle = _load(NATIVE_CONFIG)["bundle"]
    publisher = nsis_bundle.get("publisher")
    assert publisher, "bundle.publisher must be set explicitly"
    assert publisher != "civiccast", (
        "publisher must not be left to Tauri's default derivation (the lowercase "
        "second segment of the bundle identifier) -- that is the F-21 defect"
    )
    # Must match branding actually used elsewhere in the product (the window
    # title bar and every DetailPrint line say "CivicCast", never "civiccast").
    assert publisher == "CivicCast"


def test_native_config_sets_a_copyright_string_so_brandingtext_is_never_empty() -> None:
    """Tauri's generated installer.nsi does `BrandingText "${COPYRIGHT}"`
    unconditionally (confirmed by reading a real generated installer.nsi);
    an empty COPYRIGHT compiles to an empty BrandingText argument, and NSIS
    falls back to its own "Nullsoft Install System vX.XX" default (F-18).
    Setting bundle.copyright is the documented Tauri mechanism to supply
    real branding text instead -- NOT a hand-edit of the generated script."""
    nsis_bundle = _load(NATIVE_CONFIG)["bundle"]
    copyright_text = nsis_bundle.get("copyright")
    assert copyright_text, "bundle.copyright must be set so BrandingText is never empty"
    assert "CivicCast" in copyright_text


def test_native_config_uses_the_product_icon_for_the_installer_and_uninstaller_exe() -> None:
    """F-18's 'stock NSIS artwork' -- at minimum, the setup.exe/uninstall.exe
    file icon (visible in Explorer, the taskbar, and the UAC prompt) should be
    the product's own icon, which already exists in this repo, rather than
    NSIS's generic default. Full header/sidebar wizard-page bitmaps are a
    separate design-asset investment (no such art exists in this repo) and are
    deliberately out of scope for this text/metadata honesty pass -- see the
    commit message."""
    nsis = _effective_native_nsis_config()
    assert nsis.get("installerIcon") == "icons/icon.ico"
    assert nsis.get("uninstallerIcon") == "icons/icon.ico"
    icon_path = INSTALLER / "icons" / "icon.ico"
    assert icon_path.is_file(), f"configured installer icon does not exist: {icon_path}"


def test_native_bootstrap_resource_gate_still_passes_after_identity_fixes() -> None:
    """The publisher/copyright/icon additions above must not widen
    bundle.resources -- scripts/build_native_bootstrap.py's
    validate_native_bootstrap_config() is a hard gate against embedding any
    multi-gigabyte payload in the bootstrap; this proves the gate still
    passes after this commit's config edits."""
    import sys

    scripts_dir = str(ROOT / "scripts")
    added = scripts_dir not in sys.path
    if added:
        sys.path.insert(0, scripts_dir)
    try:
        from build_native_bootstrap import validate_native_bootstrap_config

        validate_native_bootstrap_config()
    finally:
        if added:
            sys.path.remove(scripts_dir)


# ---------------------------------------------------------------------------
# F-10 / F-12: the wizard's pre-install "Space required" estimate (237.0 MB)
# and the ARP "Installed apps" size (37.1 MB) both undercounted the real
# ~1.19 GB on-disk install by roughly 30x, because neither number could see
# past NSIS's own File-statement bookkeeping into the native component packs
# this bootstrap stages via external nsExec calls. Two complementary fixes:
# a compile-time AddSize declaration (derived from the four packs a fresh
# install actually stages) for the pre-install estimate, and a runtime
# ${GetSize} measurement of $INSTDIR (self-correcting, no guessing) for the
# post-install ARP value.
# ---------------------------------------------------------------------------


def test_addsize_declares_the_candidate_enforced_sidecar_budget() -> None:
    """The workflow separately checks actual reports against this budget."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")

    match = re.search(r'!define CIVICCAST_ADDSIZE_PACKS_KB "(\d+)"', hooks_text)
    assert match is not None, "CIVICCAST_ADDSIZE_PACKS_KB define not found"
    addsize_kb = int(match.group(1))

    # Actual required sidecars + extracted trees from RC1 build40970229720.
    # The old5,400,000KiB declaration understated this measured requirement.
    assert addsize_kb * 1024 >= 6_145_182_639


def test_addsize_is_declared_before_the_directory_page_and_used_in_preinstall() -> None:
    """AddSize is a compile-time directive; it must be invoked inside a
    Section (NSIS_HOOK_PREINSTALL is inserted inside Tauri's generated
    `Section Install`). The KB constant itself must be !define'd at file
    scope, textually before any hook macro body, so it is defined the
    moment this file is !include'd -- well before installer.nsi's own
    `!insertmacro MUI_PAGE_DIRECTORY` runs."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    define_index = hooks_text.index("!define CIVICCAST_ADDSIZE_PACKS_KB")
    first_macro_index = hooks_text.index("!macro NSIS_HOOK_PREINSTALL")
    assert define_index < first_macro_index, (
        "CIVICCAST_ADDSIZE_PACKS_KB must be defined at file scope, before any "
        "hook macro body, so it is available at !include time"
    )
    preinstall = _preinstall_block(hooks_text)
    assert "AddSize ${CIVICCAST_ADDSIZE_PACKS_KB}" in preinstall


def test_directory_page_top_text_states_more_will_download_later() -> None:
    """F-10: 'Space required' must not read as the whole install size. The
    MUI_DIRECTORYPAGE_TEXT_TOP override must be defined at file scope
    (before installer.nsi's MUI_PAGE_DIRECTORY insertion point) and must
    plainly say more will be downloaded later, without asserting a
    specific total that could go stale -- the download-plan screen already
    shows real, measured per-component sizes (VERDICT-rewalk-dd7f835f.md
    praised that screen as accurate) and is the honest place for the
    number, not a static wizard-page string."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    define_index = hooks_text.index("!define MUI_DIRECTORYPAGE_TEXT_TOP")
    first_macro_index = hooks_text.index("!macro NSIS_HOOK_PREINSTALL")
    assert define_index < first_macro_index, (
        "MUI_DIRECTORYPAGE_TEXT_TOP must be defined at file scope, before any hook macro body"
    )
    match = re.search(r'!define MUI_DIRECTORYPAGE_TEXT_TOP "([^"]*)"', hooks_text)
    assert match is not None
    text = match.group(1)
    assert "download" in text.lower()
    assert "after" in text.lower(), "must make clear this happens AFTER Setup, not as part of it"


def test_estimated_size_is_measured_from_the_real_installed_tree_not_hardcoded() -> None:
    """F-12: the ARP EstimatedSize write must come from a runtime ${GetSize}
    measurement of $INSTDIR (self-correcting on every future build), placed
    after D4 firewall registration (every pack has been staged and
    verified by then) and before the InstalledVersion write, overwriting
    Tauri's own earlier write the same way the QuietUninstallString write
    below it already does."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))

    assert '${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2' in postinstall
    assert 'WriteRegDWORD SHCTX "${UNINSTKEY}" "EstimatedSize" "$0"' in postinstall

    firewall_check = "step d4-firewall-rule: returned $0"
    get_size = '${GetSize} "$INSTDIR"'
    installed_version_write = 'WriteRegStr HKLM "Software\\CivicCast\\Native" "InstalledVersion"'
    assert postinstall.index(firewall_check) < postinstall.index(get_size), (
        "EstimatedSize must be measured after D4 firewall registration, "
        "once every pack has actually been staged"
    )
    assert postinstall.index(get_size) < postinstall.index(installed_version_write), (
        "EstimatedSize must be measured before the InstalledVersion write "
        "(ordering is not load-bearing here, but keeps the success-path "
        "steps in a single readable block)"
    )


# ---------------------------------------------------------------------------
# Chain J (2026-08-02): the native product reported the bare version string
# "1.0.0-rc15" -- byte-identical to the older WSL line's own release string.
# Two different rc15 installers existed in the wild; it confused the project
# owner personally. The fix gave the native line its OWN version source
# (civiccast/_native_version.py), deliberately separate from
# civiccast/_version.py (the WSL line's own identity) for as long as the WSL
# line shipped alongside the native one.
#
# The owner retired the WSL/Linux lane on 2026-08-19 and, on 2026-08-31,
# retired the vestigial WSL *version* machinery too: there is one product and
# one version now, and civiccast/_version.py and civiccast/_native_version.py
# are REQUIRED to agree (scripts/policy/check_release_identity.py enforces
# it). The two files are kept separate only because a dozen-plus pre-existing
# surfaces still import civiccast/_native_version.py by name -- collapsing
# them is tracked as future cleanup, not a correctness requirement.
#
# main.rs's CIVICCAST_VERSION and tauri.native.conf.json's "version" both
# track civiccast/_native_version.py; civiccast.native.station_runtime.
# native_reported_version_environment is the matching runtime wire that makes
# a native-hosted backend's own /health agree with that constant. Any of
# these tests would fail if a surface reverted to a bare, stale "1.0.0-rc15".
# ---------------------------------------------------------------------------


def _native_source_version() -> str:
    match = re.search(
        r'__version__\s*=\s*"([^"]+)"', NATIVE_VERSION_FILE.read_text(encoding="utf-8")
    )
    assert match is not None, f"could not read __version__ from {NATIVE_VERSION_FILE}"
    return match.group(1)


def test_native_overlay_version_matches_the_native_python_source_of_truth() -> None:
    """tauri.native.conf.json's own "version" field drives the native
    product's ARP DisplayVersion, InstalledVersion registry write, and D3
    upgrade-engine --new-version (all via Tauri's ${VERSION} NSIS macro --
    see nsis-hooks-bootstrap.nsh). It must equal
    civiccast._native_version.__version__, the same value main.rs's
    CIVICCAST_VERSION constant is required to carry (test below) -- a drift
    here would not fail loudly anywhere except a real installer run failing
    its own post-install health verification."""
    version = _native_source_version()
    native_config = _load(NATIVE_CONFIG)

    assert native_config.get("version") == version, (
        f"{NATIVE_CONFIG} reports version {native_config.get('version')!r}, "
        f"expected {version!r} (civiccast/_native_version.py)"
    )


def test_native_and_base_tauri_configs_report_the_single_product_version() -> None:
    """The retired chain-J regression this test used to guard against (two
    DIFFERENT rc15 installers under one version string) required the native
    and base Tauri configs to stay disjoint. With the WSL/Linux lane retired
    (2026-08-19) and its separate version identity retired with it
    (2026-08-31), there is one product line and one version -- the base
    config no longer builds a shipped product of its own, and both configs
    are now REQUIRED to report the identical single-source version. Drift
    between them is the regression to catch today."""
    native_config = _load(NATIVE_CONFIG)
    base_config = _load(BASE_CONFIG)

    assert native_config.get("version") == base_config.get("version"), (
        "native and base Tauri configs report different versions -- "
        f"{native_config.get('version')!r} vs {base_config.get('version')!r} -- "
        "there is one product line now and both must agree"
    )


def test_installer_rust_version_constant_matches_the_native_python_source_of_truth() -> None:
    """main.rs's CIVICCAST_VERSION is the REAL runtime source for the
    installer's post-install health verification (string-matches the running
    service's self-reported /health JSON, made to agree by
    civiccast.native.station_runtime.native_reported_version_environment) and
    for the expected_product_version/expected_compatible_core it passes into
    real pack verification (native_packs::verify_native_pack via
    run_production_acquisition). It must equal
    civiccast._native_version.__version__ -- NOT the WSL
    civiccast._version.__version__, which is a separate identity chain J
    deliberately left untouched (see evidence/chainJ-analysis.md)."""
    version = _native_source_version()
    source = INSTALLER_MAIN_RS.read_text(encoding="utf-8")

    assert f'const CIVICCAST_VERSION: &str = "{version}";' in source, (
        f"main.rs's CIVICCAST_VERSION constant does not carry {version!r}"
    )


def test_no_native_identity_surface_still_says_bare_rc15() -> None:
    """Direct pin against regression to the exact defect this chain fixes:
    none of the native product's own identity surfaces may carry the WSL
    line's literal "1.0.0-rc15" string. (Cargo.toml, the WSL product's own
    tauri.conf.json, and its WSL-only headless-bootstrap.ps1 are explicitly
    NOT checked here -- they are SUPPOSED to still say 1.0.0-rc15, being
    genuinely WSL-line surfaces; see
    test_wsl_product_identity_files_are_unchanged_by_the_native_work above
    and evidence/chainJ-analysis.md for why.)"""
    stale = "1.0.0-rc15"

    assert _load(NATIVE_CONFIG).get("version") != stale
    assert NATIVE_VERSION_FILE.read_text(encoding="utf-8").count(f'__version__ = "{stale}"') == 0
    assert f'CIVICCAST_VERSION: &str = "{stale}"' not in INSTALLER_MAIN_RS.read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# F-14 (carried, rewalk-de3aaf6f DELTA-M-03): the uninstaller's final two
# logged detail-pane lines were "Create folder: C:\ProgramData\CivicCast"
# (x2), immediately after "Remove folder: C:\Program Files\CivicCast
# (Native)\" -- reading, to an operator watching the log, as the uninstaller
# re-creating the data root it just finished tearing down.
#
# Root cause: CIVICCAST_STEP (the shared breadcrumb macro every DetailPrint-
# adjacent step in this file routes through, including the LAST two calls in
# NSIS_HOOK_POSTUNINSTALL) unconditionally runs `CreateDirectory
# "$COMMONPROGRAMDATA\CivicCast"` before every single log line it writes.
# NSIS's CreateDirectory instruction always emits a "Create folder: <path>"
# DetailPrint line, whether or not the directory already existed. Since
# $COMMONPROGRAMDATA\CivicCast is product-owned data that this same file's
# NSIS_HOOK_POSTUNINSTALL header explicitly preserves ("deliberately NEVER
# touched by this removal, or by anything else in this macro") for the
# product's entire lifetime after first install, every CIVICCAST_STEP call
# during an uninstall is creating a directory that is already there -- the
# two log lines are pure, misleading noise, and because they happen to be
# the LAST two breadcrumbs in NSIS_HOOK_POSTUNINSTALL, they land as the
# uninstaller's final visible action.
# ---------------------------------------------------------------------------


def _civiccast_step_macro_body() -> str:
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    return hooks_text.split("!macro CIVICCAST_STEP TEXT", 1)[1].split("!macroend", 1)[0]


def test_civiccast_step_does_not_unconditionally_recreate_programdata() -> None:
    """The breadcrumb macro must only create $COMMONPROGRAMDATA\\CivicCast
    when it is not already there -- never as a bare, unguarded
    CreateDirectory that fires (and logs "Create folder:") on every single
    breadcrumb, including the last ones in an uninstall."""
    body = _civiccast_step_macro_body()

    bare_create = 'CreateDirectory "$COMMONPROGRAMDATA\\CivicCast"'
    assert bare_create in body, (
        "expected CIVICCAST_STEP to still ensure the log directory exists "
        "somewhere -- if this literal changed, update this test's search string"
    )

    # The CreateDirectory call must sit inside an existence guard, not run
    # unconditionally before every FileOpen.
    guard_open = '${IfNot} ${FileExists} "$COMMONPROGRAMDATA\\CivicCast"'
    assert guard_open in body, (
        "expected CIVICCAST_STEP's CreateDirectory to be guarded by "
        '${IfNot} ${FileExists} "$COMMONPROGRAMDATA\\CivicCast" so it is a '
        "no-op (and logs nothing) once the directory already exists -- which "
        "is the whole lifetime of an installed product, uninstall included"
    )
    guard_index = body.index(guard_open)
    create_index = body.index(bare_create)
    assert guard_index < create_index, (
        "the FileExists guard must wrap the CreateDirectory call, not follow it"
    )
    guard_body = body[guard_index:].split("${EndIf}", 1)[0]
    assert bare_create in guard_body, (
        "CreateDirectory must be INSIDE the guard's ${IfNot}...${EndIf} block, "
        "not merely appear somewhere after the guard opens"
    )


def test_postuninstall_final_breadcrumbs_do_not_log_a_programdata_create_folder() -> None:
    """End-to-end pin on the actual symptom: simulate CIVICCAST_STEP's own
    CreateDirectory guard against a machine where the uninstall never
    deletes $COMMONPROGRAMDATA\\CivicCast (true for every real uninstall --
    that tree is preserved by design, per this same macro's header comment)
    and assert the LAST two breadcrumbs NSIS_HOOK_POSTUNINSTALL emits --
    "...recursive removal of runtime/packs/INSTDIR: done" and "...removed
    the Tauri InstallDirRegKey..." -- would not have re-triggered
    CreateDirectory's "Create folder:" log line under the fixed guard."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    last_two_steps = [
        "postuninstall: recursive removal of runtime/packs/INSTDIR: done",
        "postuninstall: removed the Tauri InstallDirRegKey",
    ]
    positions = [postuninstall.index(step) for step in last_two_steps]
    assert positions == sorted(positions), "expected these two breadcrumbs in this order"

    step_messages = re.findall(r'!insertmacro CIVICCAST_STEP "([^"]*)"', postuninstall)
    # Updated 2026-08-28 (candidate 9d4477b shortcut-removal fix): the
    # InstallDirRegKey breadcrumb was the tail this test originally pinned,
    # but Start Menu/Desktop shortcut removal now runs unconditionally AFTER
    # it (see that block's own doc comment for why it must not be gated
    # behind the $R2 tree-retention check InstallDirRegKey's removal sits
    # inside) and logs its own breadcrumb, which is now the real tail.
    assert step_messages[-1] == "postuninstall: Start Menu + Desktop shortcuts removed", (
        "expected the shortcut-removal breadcrumb to be the LAST "
        "!insertmacro CIVICCAST_STEP call in POSTUNINSTALL -- it must run "
        "unconditionally, after every other step including the gated "
        "InstallDirRegKey removal this test used to pin as the tail"
    )
    assert step_messages[-2].startswith("postuninstall: removed the Tauri InstallDirRegKey"), (
        "the InstallDirRegKey breadcrumb (this test's ORIGINAL tail pin, "
        "the exact tail the operator log showed) must still be the "
        "second-to-last breadcrumb, immediately before shortcut removal"
    )

    # The guard from the sibling test, re-verified against the shared macro
    # body actually used by these two call sites.
    body = _civiccast_step_macro_body()
    assert '${IfNot} ${FileExists} "$COMMONPROGRAMDATA\\CivicCast"' in body, (
        "the last two POSTUNINSTALL breadcrumbs route through CIVICCAST_STEP, "
        "which must guard its CreateDirectory so these tail lines never log "
        "another spurious 'Create folder: C:\\ProgramData\\CivicCast'"
    )


def test_postinstall_rewrites_installlocation_without_embedded_quotes() -> None:
    """Sandbox lifecycle attempt 5 (2026-08-07), first-ever inspection of an
    installed machine: Tauri's installer.nsi writes ARP InstallLocation as
    '"$INSTDIR"' -- literal quotes INSIDE the registry value -- so every
    consumer treating it as a path (inventory tooling, ops scripts, the
    lifecycle harness) resolves a nonexistent quoted path. POSTINSTALL must
    rewrite it as the bare path, last-wins, same SHCTX/UNINSTKEY convention
    as the QuietUninstallString write."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = hooks_text.split("!macro NSIS_HOOK_POSTINSTALL", 1)[1].split("!macroend", 1)[0]

    rewrite = 'WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$INSTDIR"'
    assert rewrite in postinstall, (
        "POSTINSTALL must rewrite InstallLocation as the bare unquoted "
        "$INSTDIR, overwriting Tauri's quoted value"
    )
    assert "'\"$INSTDIR\"'" not in postinstall.split(rewrite, 1)[1], (
        "nothing after the unquoted rewrite may re-introduce a quoted InstallLocation value"
    )
    quiet = 'WriteRegStr SHCTX "${UNINSTKEY}" "QuietUninstallString"'
    assert postinstall.index(quiet) < postinstall.index(rewrite), (
        "the InstallLocation rewrite belongs with the end-of-chain last-wins "
        "registry writes, after QuietUninstallString"
    )


def test_postinstall_creates_start_menu_and_desktop_shortcuts_to_the_running_station() -> None:
    """Field report 2026-08-28 (candidate 9d4477b): once the setup wizard's
    own window closes, an operator had NO clickable path back to the
    operator console or the public portal at all -- this installer created
    no shortcut of any kind pointing at either surface. Fix: a Start Menu
    folder ("CivicCast (Native)") carrying both console and portal
    Internet Shortcuts, plus a Desktop shortcut to the console alone, all
    written at the very end of POSTINSTALL (after every other end-of-chain
    write, so they only appear once the install has actually succeeded)."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postinstall = _postinstall_block(hooks_text)

    assert 'CreateDirectory "$SMPROGRAMS\\${PRODUCTNAME}"' in postinstall, (
        "POSTINSTALL must create a per-product Start Menu folder before writing shortcuts into it"
    )
    for expected in (
        'WriteINIStr "$SMPROGRAMS\\${PRODUCTNAME}\\CivicCast Operator Console.url" '
        '"InternetShortcut" "URL" "http://127.0.0.1:8000/operator/"',
        'WriteINIStr "$SMPROGRAMS\\${PRODUCTNAME}\\CivicCast Public Portal.url" '
        '"InternetShortcut" "URL" "http://127.0.0.1:8000/"',
        'WriteINIStr "$DESKTOP\\CivicCast Operator Console.url" '
        '"InternetShortcut" "URL" "http://127.0.0.1:8000/operator/"',
    ):
        assert expected in postinstall, (
            f"expected {expected!r} in nsis-hooks-bootstrap.nsh POSTINSTALL"
        )

    # Placement: strictly after the InstallLocation rewrite, which is the
    # documented last-wins end-of-chain write -- shortcuts must not be
    # created before the install itself is known to have succeeded.
    rewrite = 'WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$INSTDIR"'
    shortcuts_begin = postinstall.index('CreateDirectory "$SMPROGRAMS\\${PRODUCTNAME}"')
    assert postinstall.index(rewrite) < shortcuts_begin, (
        "shortcut creation must run after the InstallLocation rewrite, "
        "at the very end of a successful POSTINSTALL"
    )

    # Silent-install safety: no dialog may accompany shortcut creation --
    # a missed/failed shortcut is cosmetic, never install-blocking.
    shortcuts_block = postinstall[shortcuts_begin:]
    assert "MessageBox" not in shortcuts_block, (
        "shortcut creation must stay silent-safe -- no MessageBox, even on failure"
    )
    assert "CIVICCAST_FAIL" not in shortcuts_block, (
        "a shortcut write failing must never abort the install "
        "(CIVICCAST_FAIL is the install-blocking vocabulary)"
    )
    assert "CIVICCAST_ALERT" not in shortcuts_block, (
        "shortcut creation must not raise an operator-facing alert on failure "
        "(non-fatal, breadcrumb-only per the block's own doc comment)"
    )


def test_postinstall_shortcut_urls_match_main_rs_constants() -> None:
    """Drift guard: the two URLs hardcoded into the NSIS shortcuts above must
    stay byte-identical to `main.rs`'s own `OPERATOR_CONSOLE_URL` /
    `RESIDENT_PORTAL_URL` constants -- the same values the setup wizard's own
    finish screen uses for "Open operator console" -- so the two can never
    silently drift apart."""
    postinstall = _postinstall_block(NATIVE_HOOKS.read_text(encoding="utf-8"))
    main_rs = INSTALLER_MAIN_RS.read_text(encoding="utf-8")

    operator_console_match = re.search(r'const OPERATOR_CONSOLE_URL: &str = "([^"]+)";', main_rs)
    portal_match = re.search(r'const RESIDENT_PORTAL_URL: &str = "([^"]+)";', main_rs)
    assert operator_console_match, "main.rs must still declare OPERATOR_CONSOLE_URL"
    assert portal_match, "main.rs must still declare RESIDENT_PORTAL_URL"

    operator_console_url = operator_console_match.group(1)
    portal_url = portal_match.group(1)

    assert postinstall.count(f'"URL" "{operator_console_url}"') == 2, (
        f"expected the operator console URL {operator_console_url!r} in exactly "
        "two shortcuts (Start Menu + Desktop)"
    )
    assert postinstall.count(f'"URL" "{portal_url}"') == 1, (
        f"expected the public portal URL {portal_url!r} in exactly one shortcut (Start Menu)"
    )


def test_postuninstall_removes_the_start_menu_and_desktop_shortcuts() -> None:
    """The counterpart to the POSTINSTALL creation test above: an uninstall
    must remove both Start Menu shortcuts and the folder, plus the Desktop
    shortcut, and must do so UNCONDITIONALLY -- unlike the $INSTDIR
    runtime/packs trees, shortcut removal is not gated behind confirming the
    supervisor service stopped (see the block's own doc comment for why:
    deleting a shortcut carries none of the still-running-process hazard
    that gate exists to guard against)."""
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    postuninstall = hooks_text.split("!macro NSIS_HOOK_POSTUNINSTALL", 1)[1].split("!macroend", 1)[
        0
    ]

    for expected in (
        'Delete "$SMPROGRAMS\\${PRODUCTNAME}\\CivicCast Operator Console.url"',
        'Delete "$SMPROGRAMS\\${PRODUCTNAME}\\CivicCast Public Portal.url"',
        'RMDir "$SMPROGRAMS\\${PRODUCTNAME}"',
        'Delete "$DESKTOP\\CivicCast Operator Console.url"',
    ):
        assert expected in postuninstall, (
            f"expected {expected!r} in nsis-hooks-bootstrap.nsh POSTUNINSTALL"
        )

    # Placement: strictly AFTER the $R2 tree-retention gate's closing
    # ${EndIf} (the DeleteRegKey line just inside it is the last statement
    # of that gated arm), proving shortcut removal is not nested inside --
    # and therefore not skipped by -- the "service stop unconfirmed" branch.
    gated_marker = 'DeleteRegKey HKLM "Software\\civiccast\\CivicCast (Native)"'
    shortcuts_marker = 'Delete "$SMPROGRAMS\\${PRODUCTNAME}\\CivicCast Operator Console.url"'
    assert postuninstall.index(gated_marker) < postuninstall.index(shortcuts_marker), (
        "shortcut removal must be placed after the gated InstallDirRegKey removal"
    )
    between = postuninstall[
        postuninstall.index(gated_marker) : postuninstall.index(shortcuts_marker)
    ]
    assert between.count("${EndIf}") >= 1, (
        "shortcut removal must sit OUTSIDE (after the closing ${EndIf} of) the "
        "$R2 service-stop-confirmed gate, so it always runs"
    )


def test_pack_delivery_failure_tells_operator_to_correct_the_kit_and_retry() -> None:
    """A partial install is now a supported retry/repair input.

    PREINSTALL safely tears down any registered native state before another
    attempt, so telling the operator to uninstall first would reintroduce an
    unnecessary destructive-looking step and contradict install-over-existing.
    The message must instead name the bad side-load, direct retry, and the
    ProgramData preservation boundary.
    """
    hooks_text = NATIVE_HOOKS.read_text(encoding="utf-8")
    fail_line = next(
        line
        for line in hooks_text.splitlines()
        if "CIVICCAST_FAIL ${CIVICCAST_EXIT_PACK_DELIVERY}" in line
    )

    assert "put them in a 'packs' folder next to the installer" in fail_line
    assert "Run setup again" in fail_line
    assert "recordings, database, and settings" in fail_line
    assert "were not deleted" in fail_line
    assert "Uninstall CivicCast (Native)" not in fail_line
    assert "refuse to install over" not in fail_line
