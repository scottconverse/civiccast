"""Contract for the operator-facing behaviour of the NSIS bootstrap hooks.

`nsis-hooks-bootstrap.nsh` is the ONE live native installer hook file (see its
own header, and `tests/policy/test_native_installer_identity.py` for the pin on
that fact). It is NSIS script, not Python or Rust, so -- following the
source-shape convention `test_native_service_start.py` and
`test_tauri_windows_manifest.py` already use in this directory -- the contract
pinned here is the SHAPE of the script text: which exit codes each step
branches on, which branches route to the fail-loud `CIVICCAST_FAIL` macro, and
what the operator-visible remedy text actually says.

Both defects covered here were found on real hardware on 2026-08-01, not in the
pristine Windows Sandbox that every prior proof run used:

  * chain E -- the vc-redist step hard-failed the whole install on MSI exit
    1638 (ERROR_PRODUCT_VERSION, "another version of this product is already
    installed"), which for the VC++ redistributable bootstrapper is the NORMAL
    state of a machine that already carries a same-or-newer runtime. The
    prerequisite is satisfied; the install aborted anyway.

  * chain F -- the stage-packs failure dialog offered a remedy the product does
    not implement ("connect this machine to the network so setup can download
    them"). The hook's own comment says the online path is a typed
    NOT_AVAILABLE outcome today because no channel index URL is pinned anywhere
    in this codebase.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.build_native_bootstrap import add_nsis_version_preflight

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_HOOKS_NSH = (
    ROOT / "civiccast" / "apps" / "installer" / "src-tauri" / "nsis-hooks-bootstrap.nsh"
)

VC_REDIST_BEGIN = '!insertmacro CIVICCAST_STEP "step vc-redist: begin"'
STAGE_PACKS_BEGIN = '!insertmacro CIVICCAST_STEP "step stage-packs: begin"'


def _hooks_source() -> str:
    return BOOTSTRAP_HOOKS_NSH.read_text(encoding="utf-8")


def test_failed_install_restores_prior_station_before_aborting() -> None:
    source = _hooks_source()
    fail = _slice(source, "!macro CIVICCAST_FAIL CODE TEXT", "!macroend")
    assert "CIVICCAST_TRANSACTION_RESTORE" in fail, (
        "failed setup currently contains the new payload but never restores the prior station"
    )
    pre = _slice(source, "!macro NSIS_HOOK_PREINSTALL", "!macroend")
    assert pre.index("CIVICCAST_TRANSACTION_BEGIN") < pre.index('sc.exe" query')
    assert "CIVICCAST_TRANSACTION_SNAPSHOT" in pre
    post = _slice(source, "!macro NSIS_HOOK_POSTINSTALL", "!macroend")
    assert post.index("CIVICCAST_TRANSACTION_COMMIT") > post.index(
        '"InstalledVersion" "${VERSION}"'
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows NSIS recovery callback compilation")
@pytest.mark.parametrize("final_template", [False, True], ids=["initial-tauri", "final-patched"])
def test_transaction_macros_compile_with_tauri_early_hook_include(
    tmp_path: Path, final_template: bool
) -> None:
    makensis = Path.home() / "AppData/Local/tauri/NSIS/Bin/makensis.exe"
    if not makensis.is_file():
        pytest.skip("Tauri NSIS compiler unavailable")
    source = tmp_path / "recovery.nsi"
    template = (
        # Tauri emits a UTF-8 BOM; prepending directives must not move it mid-file.
        "\ufeffUnicode true\n!include MUI2.nsh\n!include LogicLib.nsh\n!include FileFunc.nsh\n"
        # Tauri includes hooks before defining product constants/page variables.
        f'!include "{BOOTSTRAP_HOOKS_NSH}"\n'
        '!define VERSION "999.0.0"\n!define UNINSTKEY "Software\\CivicCastRecoveryProbe"\n'
        'Var AppStartMenuFolder\nName "Recovery compile probe"\nRequestExecutionLevel user\n'
        f'OutFile "{tmp_path / "recovery.exe"}"\n'
        "!insertmacro MUI_PAGE_INSTFILES\n!insertmacro MUI_LANGUAGE English\n"
        "Section\n!insertmacro CIVICCAST_TRANSACTION_RECOVER_PENDING\n"
        "!insertmacro CIVICCAST_TRANSACTION_BEGIN\n!insertmacro CIVICCAST_TRANSACTION_SNAPSHOT\n"
        "!insertmacro CIVICCAST_TRANSACTION_COMMIT ${CIVICCAST_EXIT_INSTALL_RECOVERY}\nSectionEnd\n"
        "Function .onInit\nFunctionEnd\n"
    )
    if final_template:
        template = add_nsis_version_preflight(template)
        assert "!define MUI_CUSTOMFUNCTION_ABORT" in template
        assert template.index("!define MUI_CUSTOMFUNCTION_ABORT") < template.index(
            "!insertmacro MUI_PAGE_INSTFILES"
        )
        assert template.count("!insertmacro CIVICCAST_TRANSACTION_CALLBACKS") == 1
        assert add_nsis_version_preflight(template) == template
    source.write_text(template, encoding="utf-8")
    compiled = subprocess.run(
        [str(makensis), "/V2", str(source)], capture_output=True, text=True, timeout=60
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    # Do not execute: this probe checks wiring without touching host state.


def _slice(source: str, start_marker: str, end_marker: str) -> str:
    start = source.find(start_marker)
    assert start != -1, f"{start_marker!r} not found in {BOOTSTRAP_HOOKS_NSH}"
    end = source.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after {start_marker!r}"
    return source[start:end]


def _vc_redist_block(source: str) -> str:
    """The whole vc-redist step, from its breadcrumb to the next step's."""
    return _slice(source, VC_REDIST_BEGIN, STAGE_PACKS_BEGIN)


def _vc_redist_1638_branch(block: str) -> str:
    """The 1638 arm only, up to the chain's outer catch-all `${Else}`.

    The outer `${If}/${ElseIf}/${Else}` chain sits at two-space indentation, so
    a newline followed by exactly two spaces and `${Else}` closes the 1638 arm;
    any `${Else}` nested INSIDE the arm is indented deeper than that.
    """
    marker = "$0 == 1638"
    start = block.find(marker)
    assert start != -1, (
        "the vc-redist step has no branch for MSI exit 1638 (ERROR_PRODUCT_VERSION). "
        "1638 from the VC++ redistributable bootstrapper means a same-or-newer runtime "
        "is already installed -- the prerequisite is SATISFIED -- yet it currently "
        "falls into the catch-all that aborts the install. Real machine R7 failed "
        "install on exactly this on 2026-08-01."
    )
    end = block.find("\n  ${Else}", start)
    assert end != -1, "the vc-redist branch chain has no outer catch-all ${Else}"
    return block[start:end]


def test_e_the_1638_branch_verifies_the_runtime_is_really_present_in_the_registry() -> None:
    branch = _vc_redist_1638_branch(_vc_redist_block(_hooks_source()))

    assert "SetRegView 64" in branch, (
        "the 1638 branch must read the 64-bit registry view explicitly; the VC++ x64 "
        "runtime key lives in the 64-bit view and a 32-bit NSIS installer does not see "
        "it by default"
    )
    assert "ReadRegDWORD" in branch, (
        "the 1638 branch must READ the runtime's presence, not assume it; 'Installed' is a DWORD"
    )
    assert r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" in branch, (
        "the 1638 branch does not read Microsoft's documented VC++ x64 runtime presence key"
    )
    assert '"Installed"' in branch, (
        "the 1638 branch does not read the 'Installed' DWORD value that proves the "
        "runtime is genuinely present"
    )


def test_e_a_confirmed_present_runtime_is_a_success_and_an_unconfirmed_one_still_fails() -> None:
    branch = _vc_redist_1638_branch(_vc_redist_block(_hooks_source()))

    split = branch.find("\n    ${Else}")
    assert split != -1, (
        "the 1638 branch does not split on the registry answer; it must have a "
        "confirmed-present arm AND an unconfirmed arm"
    )
    confirmed_present, unconfirmed = branch[:split], branch[split:]

    assert "CIVICCAST_FAIL" not in confirmed_present, (
        "a 1638 whose runtime presence IS confirmed in the registry still routes to "
        "CIVICCAST_FAIL and aborts the install; the prerequisite is satisfied and the "
        "install must continue"
    )
    assert "DetailPrint" in confirmed_present, (
        "the confirmed-present case must say so honestly in the installer log"
    )
    assert "CIVICCAST_FAIL $0" in unconfirmed, (
        "a 1638 that the registry does NOT confirm is genuinely abnormal and must keep "
        "failing loud with the real MSI exit code"
    )


def test_e_every_other_nonzero_vc_redist_exit_code_still_fails_loud() -> None:
    block = _vc_redist_block(_hooks_source())

    handled = set(re.findall(r"\$0 == (\d+)", block))
    assert handled == {"0", "3010", "1638"}, (
        "the vc-redist step must special-case exactly success (0), success-with-reboot "
        "(3010) and already-present (1638); anything else is a real prerequisite "
        f"failure. Found: {sorted(handled)}"
    )

    catch_all = block[block.find("\n  ${Else}") :]
    assert "CIVICCAST_FAIL $0" in catch_all, (
        "the catch-all arm must keep routing every other nonzero exit code through the "
        "fail-loud macro with the redist's own exit code"
    )


# --------------------------------------------------------------------------
# chain F-min: the stage-packs failure dialog must only name remedies that exist
# --------------------------------------------------------------------------

PACK_DELIVERY_COMMENT_BEGIN = "NATIVE COMPONENT PACK DELIVERY"
D2_VERIFY_BEGIN = "D2 INSTALL-TIME RE-VERIFICATION"

# Remedies the product does not implement. There is no channel index URL pinned
# anywhere in this codebase, so `acquire_online_distribution` can only return a
# typed NOT_AVAILABLE -- telling an operator to plug in a network cable sends
# them to fix something that would not help.
FALSE_REMEDY_PATTERNS = (
    r"connect\b[^\"]{0,60}\bnetwork",
    r"\bdownload\b",
    r"\bonline[- ]fallback\b",
    r"\binternet\b",
)


def _stage_packs_step(source: str) -> str:
    """The stage-packs step itself: breadcrumb, DetailPrints and the abort."""
    return _slice(source, STAGE_PACKS_BEGIN, D2_VERIFY_BEGIN)


def _stage_packs_comment(source: str) -> str:
    """The pack-delivery design comment that sits above the step."""
    return _slice(source, PACK_DELIVERY_COMMENT_BEGIN, STAGE_PACKS_BEGIN)


def _stage_packs_fail_message(source: str) -> str:
    step = _stage_packs_step(source)
    marker = "!insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_PACK_DELIVERY}"
    start = step.find(marker)
    assert start != -1, (
        "the stage-packs step no longer aborts through CIVICCAST_FAIL with "
        "CIVICCAST_EXIT_PACK_DELIVERY; the exit code and the fail-loud routing must not "
        "change"
    )
    return step[start + len(marker) : step.find("\n", start)]


def test_f_the_stage_packs_failure_never_offers_a_remedy_the_product_cannot_perform() -> None:
    step = _stage_packs_step(_hooks_source())

    operator_text = "\n".join(
        line for line in step.splitlines() if "DetailPrint" in line or "CIVICCAST_FAIL" in line
    )
    for pattern in FALSE_REMEDY_PATTERNS:
        found = re.search(pattern, operator_text, re.IGNORECASE)
        assert found is None, (
            f"the stage-packs step tells the operator {found.group(0)!r}, but no online "
            "pack acquisition exists: no channel index URL is pinned anywhere in this "
            "codebase, so acquire_online_distribution can only return a typed "
            "NOT_AVAILABLE. A real tester hit this on 2026-08-01 (child exit 74, hook "
            "exit 110) and was given instructions for a capability the product does not "
            "have."
        )


def test_f_the_stage_packs_failure_names_the_remedy_that_does_exist() -> None:
    message = _stage_packs_fail_message(_hooks_source())

    assert "'packs' folder" in message, (
        "the dialog must name the side-load folder by its exact name; that is the only "
        "path that actually delivers a pack"
    )
    assert "next to the installer" in message, (
        "the dialog must say WHERE the 'packs' folder goes -- $EXEDIR, beside the installer .exe"
    )
    assert re.search(r"release page|distribution medium", message, re.IGNORECASE), (
        "the dialog must tell the operator where to obtain the pack file(s); 'put the "
        "file somewhere' is not a remedy if they do not know where the file comes from"
    )
    # Intent: name the retry step; the dialog's numbered step correctly capitalizes it.
    assert "run setup again" in message.lower(), "the dialog must name the retry step"
    assert "installer log" in message, (
        "the dialog must keep pointing at the installer log for the exact missing component list"
    )


def test_f_the_design_comment_stops_describing_the_missing_path_as_a_working_fallback() -> None:
    comment = _stage_packs_comment(_hooks_source())

    assert "online-fallback" not in comment, (
        "the comment labels the nonexistent path 'online-fallback', which reads as "
        "working behaviour; the label is what the false operator remedy was written from"
    )
    assert "NOT_AVAILABLE" in comment, (
        "the accurate description -- a typed NOT_AVAILABLE outcome, because no channel "
        "index URL is pinned anywhere in this codebase -- must stay"
    )


# --------------------------------------------------------------------------
# chain F-min2: the dialog promises the installer log names the missing
# component(s). TESTER2 (request-0050) verified on real hardware that
# C:\ProgramData\CivicCast\install-progress.log carries only the CIVICCAST_STEP
# breadcrumbs and never the component names, because the staging child's output
# went to the wizard detail pane (nsExec::ExecToLog) and nowhere else.
# --------------------------------------------------------------------------

PACK_STAGING_RS = (
    ROOT / "civiccast" / "apps" / "installer" / "src-tauri" / "src" / "native_pack_staging.rs"
)


def _stage_packs_failure_arm(step: str) -> str:
    start = step.find("${If} $0 != 0")
    assert start != -1, "the stage-packs step no longer branches on a nonzero child exit"
    return step[start:]


def test_f2_the_staging_child_output_is_captured_rather_than_only_streamed_to_the_pane() -> None:
    step = _stage_packs_step(_hooks_source())

    assert "nsExec::ExecToStack" in step, (
        "the staging child runs under nsExec::ExecToLog, which pipes its output into the "
        "wizard detail pane and NOWHERE else -- so the missing-component names the child "
        "prints can never reach install-progress.log, which is exactly what the failure "
        "dialog promises the operator will find there (TESTER2, request-0050)"
    )
    assert re.search(r"Pop \$0\s*;?[^\n]*\n\s*Pop \$1", step), (
        "ExecToStack pushes the exit code and then the captured output; both must be "
        "popped, or the output is discarded and the stack is left unbalanced"
    )
    # Chain F-min2 originally pinned an unconditional `DetailPrint "$1"` here,
    # because ExecToLog had put the child's output in the wizard pane and the
    # move to ExecToStack must not silently take it away. Chain O (finding
    # F-11, from the 2026-08-01 newcomer walkthrough) showed that on the
    # SUCCESS path that output is the child's raw JSON manifest report, and
    # dumping it into the pane both bewildered the operator and flushed the
    # real step log out of view. The requirement F-min2 was actually
    # protecting is that the child's words REACH THE OPERATOR'S LOG -- which
    # is what the failure dialog promises -- not that they are pasted into
    # the pane. Both paths must therefore carry $1 into install-progress.log
    # via the breadcrumb macro (the only writer of that file), and the
    # failure path is the one the dialog's promise depends on.
    assert re.search(r'CIVICCAST_STEP "step stage-packs: child reported: \$1"', step), (
        "the failure path must put the child's own words -- the missing component "
        "names -- into install-progress.log, or the dialog's 'see the installer log "
        "for the exact missing component(s)' is a promise the log cannot keep "
        "(TESTER2, request-0050)"
    )
    assert re.search(r'CIVICCAST_STEP "step stage-packs: manifest report: \$1"', step), (
        "the success path must still preserve the child's report in the installer "
        "log; it is the record of what was staged and verified"
    )
    assert 'DetailPrint "$1"' not in step, (
        "the raw child output must not be dumped into the wizard detail pane: on "
        "success it is a JSON manifest that means nothing to an operator and "
        "flushes the human step log out of view (walkthrough finding F-11)"
    )


def test_f2_the_captured_output_reaches_the_installer_log_on_failure() -> None:
    failure_arm = _stage_packs_failure_arm(_stage_packs_step(_hooks_source()))

    breadcrumbs_with_output = [
        line for line in failure_arm.splitlines() if "CIVICCAST_STEP" in line and "$1" in line
    ]
    assert breadcrumbs_with_output, (
        "nothing writes the child's captured output into install-progress.log on the "
        "failure path. CIVICCAST_STEP is the only thing in this file that writes to that "
        "file, so without it the dialog's 'see the installer log for the exact missing "
        "component(s)' is a promise the log cannot keep"
    )


def test_f2_the_rust_abort_message_names_the_components_without_a_download_remedy() -> None:
    source = PACK_STAGING_RS.read_text(encoding="utf-8")
    marker = "pub fn build_pack_delivery_abort_message"
    start = source.find(marker)
    assert start != -1, f"{marker} not found in {PACK_STAGING_RS}"
    body = source[start : source.find("\n}", start)]

    assert "missing_components.join" in body, (
        "the abort message must keep naming every missing component; that list is the "
        "whole reason the hook now persists this string into install-progress.log"
    )
    for pattern in FALSE_REMEDY_PATTERNS:
        found = re.search(pattern, body, re.IGNORECASE)
        assert found is None, (
            f"the staging child's own abort message tells the operator {found.group(0)!r}. "
            "The hook now persists this string verbatim into install-progress.log and the "
            "detail pane, so the false remedy chain F-min removed from the NSIS dialog "
            "would simply reappear in the operator's log through the child"
        )


# --------------------------------------------------------------------------
# chain M2: F-01, uninstaller half. The sandbox newcomer re-walk (dd7f835f,
# 2026-08-01) uninstalled through the product's own uninstaller -- which
# reported "Uninstall was completed successfully" -- and the post-uninstall
# sweep found HKLM\SOFTWARE\CivicCast\Native\InstalledVersion=1.0.0-rc15 still
# there. The next install read it, misclassified a clean install as an upgrade,
# ran the D3 engine and rolled back.
#
# Chain K owns the ROUTING side (what the D3 gate does with a version marker it
# should not trust). This pins the UNINSTALLER side: after a completed
# uninstall, nothing may be left claiming a product is installed. Two things
# make that claim -- InstalledVersion (cleared by the teardown CLI, pinned in
# native_service_registration.rs / native_uninstall.rs) and Tauri's
# InstallDirRegKey, whose DEFAULT VALUE is the install path and which is what
# Tauri's own reinstall page reads to decide a machine is "Already Installed".
# --------------------------------------------------------------------------

POSTUNINSTALL_MACRO_BEGIN = "!macro NSIS_HOOK_POSTUNINSTALL"
PREUNINSTALL_TEARDOWN_STEP = (
    '!insertmacro CIVICCAST_STEP "preuninstall: teardown native state '
    '(service/firewall/registry): begin"'
)

# Tauri derives NSIS's ${MANUFACTURER} from the bundle identifier's second
# segment when no explicit publisher is configured: tauri.native.conf.json's
# "org.civiccast.native" -> "civiccast" (which is also why the product's
# Publisher renders lowercase). ${PRODUCTNAME} is "CivicCast (Native)". So
# ${MANUPRODUCTKEY} is Software\civiccast\CivicCast (Native).
TAURI_INSTALL_DIR_REG_KEY = r"Software\civiccast\CivicCast (Native)"


def _postuninstall_macro(source: str) -> str:
    return _slice(source, POSTUNINSTALL_MACRO_BEGIN, "\n!macroend")


def _preuninstall_teardown_call(source: str) -> str:
    return _slice(source, PREUNINSTALL_TEARDOWN_STEP, "!macroend")


def test_m2_a_completed_uninstall_removes_the_tauri_install_dir_registry_key() -> None:
    macro = _postuninstall_macro(_hooks_source())

    assert f'DeleteRegKey HKLM "{TAURI_INSTALL_DIR_REG_KEY}"' in macro, (
        "NSIS_HOOK_POSTUNINSTALL never removes Tauri's InstallDirRegKey "
        f"(HKLM\\{TAURI_INSTALL_DIR_REG_KEY}), whose default value is the install path. "
        "Left behind, it is what makes a fresh install of this product show Tauri's "
        '"Already Installed" reinstall page on an otherwise clean host -- the same '
        "lifecycle defect the retired WSL product's nsis-hooks.nsh already carried an "
        "explicit repair for (its rc13 'orphaned uninstall registration' block), proving "
        "Tauri's own generated uninstall Section cannot be relied on to have done it"
    )


def test_m2_the_install_dir_key_is_only_removed_when_the_uninstall_really_completed() -> None:
    macro = _postuninstall_macro(_hooks_source())

    delete_at = macro.find(f'DeleteRegKey HKLM "{TAURI_INSTALL_DIR_REG_KEY}"')
    assert delete_at != -1, "no InstallDirRegKey removal to place"

    # $R2 == "1" is this macro's own latch for "the supervisor service could
    # not be confirmed stopped, so the program trees were NOT removed". A
    # machine whose trees are still there IS still installed; erasing the key
    # that says so would let the next install run straight over it.
    retention_gate = macro.find('${If} $R2 == "1"')
    assert retention_gate != -1, "the tree-retention gate ($R2) is gone"
    assert delete_at > retention_gate, (
        "the InstallDirRegKey removal must sit inside/after the tree-retention gate, not "
        "before it: on the retained-tree path the product is still installed and the key "
        "must keep saying so"
    )
    tail = macro[retention_gate:]
    skip_arm, _, remove_arm = tail.partition("${Else}")
    assert f'DeleteRegKey HKLM "{TAURI_INSTALL_DIR_REG_KEY}"' not in skip_arm, (
        "the InstallDirRegKey is removed on the SKIPPED arm, where the runtime/packs "
        "trees were deliberately left in place because the service could not be confirmed "
        "stopped"
    )
    assert f'DeleteRegKey HKLM "{TAURI_INSTALL_DIR_REG_KEY}"' in remove_arm, (
        "the InstallDirRegKey removal must be on the arm that actually removed the trees"
    )


def test_m2_the_teardown_result_is_still_what_gates_everything_downstream() -> None:
    """Guard against 'fix the leftover by ignoring the refusal'."""
    macro = _postuninstall_macro(_hooks_source())

    assert "$CIVICCAST_TEARDOWN_EXIT" in macro, (
        "the teardown exit code carried from PREUNINSTALL must still gate this macro"
    )
    assert 'StrCpy $CIVICCAST_TEARDOWN_EXIT "82"' in macro, (
        "the fail-closed default for an unset teardown result must stay"
    )


# --------------------------------------------------------------------------
# chain M3: F-03. The sandbox newcomer re-walk (dd7f835f, 2026-08-01) ended in
# a dialog that was false three ways and a log line that contradicted itself:
#
#   [2026-08-01 18:17:40] postinstall: SUCCESS (D3 clean rollback;
#                         InstalledVersion left at 1.0.0-rc15, NOT 1.0.0-rc15)
#
# and, on screen, "could not complete the upgrade to 1.0.0-rc15 and
# automatically rolled back. The previously installed version is healthy and
# still running -- no data was lost." while the machine actually held a
# complete 1.19 GB install, a RUNNING CivicCastSupervisor, and a live API
# answering /health 200 "healthy" on :8000 -- and had had NO prior install at
# all (the "previous version" was a leftover registry value, F-01).
#
# The rule these pin: every operator-facing claim on this path is either a
# fact the installer just verified, or an attributed report from the component
# that produced it. Never an assumption, never SUCCESS for a run that did not
# succeed, and never a sentence that asserts a thing and its negation.
# --------------------------------------------------------------------------

POSTINSTALL_MACRO_BEGIN = "!macro NSIS_HOOK_POSTINSTALL"


def _postinstall_macro(source: str) -> str:
    return _slice(source, POSTINSTALL_MACRO_BEGIN, "\n!macroend")


def _code(block: str) -> str:
    """NSIS code only. A `;` comment discussing a macro is not a call to it, and
    these assertions are about what the installer DOES, not what it explains."""
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith(";"))


def _breadcrumbs(block: str) -> list[str]:
    return re.findall(r'!insertmacro CIVICCAST_STEP "([^"]*)"', _code(block))


def _operator_claims(block: str) -> list[str]:
    """Everything the operator can actually read: dialogs and the detail pane."""
    return re.findall(
        r'!insertmacro CIVICCAST_(?:NOTICE|ALERT) "([^"]*)"|DetailPrint "([^"]*)"', _code(block)
    )


def test_m3_a_rolled_back_install_is_never_logged_as_success() -> None:
    for crumb in _breadcrumbs(_postinstall_macro(_hooks_source())):
        if "rollback" in crumb.lower() or "rolled back" in crumb.lower():
            assert "SUCCESS" not in crumb, (
                "an install whose upgrade engine rolled back is logged as SUCCESS: "
                f"{crumb!r}. SUCCESS is the word an operator, a support engineer and a "
                "fleet log scraper all key on; a run that did not succeed must not carry it"
            )


def test_m3_no_claim_asserts_a_thing_and_its_negation() -> None:
    """`InstalledVersion left at $R0, NOT ${VERSION}` renders as
    "left at 1.0.0-rc15, NOT 1.0.0-rc15" whenever the leftover marker happens
    to equal the version being installed -- which is exactly the case the
    re-walk hit, because the leftover came from installing this same build."""
    macro = _postinstall_macro(_hooks_source())
    claims = _breadcrumbs(macro) + [
        text for pair in _operator_claims(macro) for text in pair if text
    ]
    for claim in claims:
        if "$R0" in claim and "${VERSION}" in claim:
            assert not re.search(r",\s*NOT\s*\$\{VERSION\}", claim), (
                f"{claim!r} contrasts $R0 against ${{VERSION}}, but the two are equal "
                "whenever a leftover marker records the same build being installed -- the "
                'string then reads "left at X, NOT X". A claim must be true for every '
                "value its own variables can take"
            )


def test_m3_the_rollback_path_verifies_the_service_state_instead_of_asserting_it() -> None:
    """SUPERSEDED (Gate A run 33681670855, 2026-09-02): the M3-era rollback
    path used to CONTINUE past exit 10 and describe machine state at the end
    of the macro, so it had to read the service control manager rather than
    assert. That path is gone -- exit 10 under the flat installer layout
    (the only layout this bootstrap ever invokes) now fails the install
    outright via CIVICCAST_FAIL before D4 provisioning/service registration
    ever runs, so there is no live service to query and nothing here needs to
    verify a running-service claim it no longer makes."""
    macro = _postinstall_macro(_hooks_source())

    assert "healthy and still running" not in macro, (
        'the rollback path must not tell the operator an installation "is healthy and '
        'still running" -- exit 10 now fails the install before any service is registered '
        "or started, so no such claim can ever be true here"
    )
    d3_arm = _slice(macro, "${ElseIf} $0 == 10", "${ElseIf} $0 == 20")
    assert "!insertmacro CIVICCAST_FAIL" in _code(d3_arm), (
        "exit 10 (D3 clean rollback) under the flat installer layout must fail the "
        "install via CIVICCAST_FAIL -- see the Gate A run 33681670855 fix comment above "
        "the branch for why a bare continue-with-notice is unsafe under this layout"
    )
    assert "${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT}" in d3_arm


def test_m3_the_rollback_path_never_claims_the_install_itself_was_undone() -> None:
    """D3 exit 10 means the UPGRADE ENGINE reverted its OWN work. It does not
    mean setup was undone: D4 provisioning, service registration and the
    firewall rule all still run after it, and Tauri laid the program files down
    before this macro was ever entered."""
    macro = _postinstall_macro(_hooks_source())
    claims = [text for pair in _operator_claims(macro) for text in pair if text]

    for claim in claims:
        if not re.search(r"roll(ed|ing|s)? ?back|rollback|reverted", claim, re.IGNORECASE):
            continue
        assert re.search(r"engine|its own work", claim, re.IGNORECASE), (
            f"{claim!r} tells the operator something was rolled back without naming WHAT. "
            "The re-walk operator read it as the install being undone and found a "
            "complete 1.19 GB install, a running service and a live API instead. The "
            "rollback is the D3 upgrade engine reverting its own work; say that"
        )
        assert "no data was lost" not in claim.lower(), (
            f"{claim!r} claims no data was lost. Nothing here verified that, and on a "
            "machine with no prior install there was no data to speak about"
        )


def test_m3_the_rollback_arm_is_retired_in_favor_of_an_unconditional_write() -> None:
    """SUPERSEDED (Gate A run 33681670855, 2026-09-02): the `$R4 == "1"`
    rollback-report arm this test used to pin (three-way $R0-vs-${VERSION}
    wording) existed to describe a D3 clean-rollback state that CONTINUED
    past exit 10. That state is no longer reachable -- exit 10 now aborts via
    CIVICCAST_FAIL before this point in the macro, so $R4 is never set to
    "1" and the InstalledVersion write at the end of POSTINSTALL is
    unconditional (reachable only by a fully successful chain, since every
    failure branch above it aborts outright)."""
    macro = _postinstall_macro(_hooks_source())
    executable = _code(macro)

    assert '$R4 == "1"' not in executable, (
        "the retired rollback-report arm must not reappear -- exit 10 fails closed now, "
        "so there is nothing left for a $R4 latch to gate"
    )
    assert 'WriteRegStr HKLM "Software\\CivicCast\\Native" "InstalledVersion" "${VERSION}"' in (
        executable
    ), "the InstalledVersion write must still happen, unconditionally, at the end of POSTINSTALL"


# --------------------------------------------------------------------------
# chain F-17: the D2 install-time re-verification steps write a `begin`
# breadcrumb and then, on success, NOTHING else -- the newcomer walkthrough of
# build dd7f835f (evidence/install-progress.log, evidence/FINDINGS-rewalk-
# dd7f835f.md) shows exactly this:
#
#   step d2-verify-server-binaries: begin
#   step d2-verify-app-payload: begin
#   step d3-engine: begin (old=1.0.0-rc15)
#
# -- with 107 seconds between the app-payload begin and the next breadcrumb,
# and no result line for either D2 step anywhere in between. An operator
# reading the log cannot tell whether either verification ran, passed, or
# hung. Every other instrumented step in this file (vc-redist, stage-packs,
# d4-provision, d4-service-registration, d4-firewall-rule) pops its child's
# exit code and immediately writes a "step X: returned $0" breadcrumb BEFORE
# branching on it, so the log carries a result unconditionally, on both the
# success and the failure path (the timestamp on that line, against the
# timestamp on "begin", is how every other step in this log already conveys
# elapsed duration -- there is no separate duration field anywhere in this
# file). The two D2 steps must follow the same contract.
# --------------------------------------------------------------------------

D2_SERVER_BINARIES_BEGIN = '!insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: begin"'
D2_APP_PAYLOAD_BEGIN = '!insertmacro CIVICCAST_STEP "step d2-verify-app-payload: begin"'
D2_APP_PAYLOAD_DEST_COMMENT = "The native-app-payload pack's extraction destination is BRIDGED by"
D2_FFMPEG_DEST_COMMENT = (
    "The native-ffmpeg-runtime pack's extraction destination is BRIDGED the same"
)


def _d2_server_binaries_step(source: str) -> str:
    """The whole d2-verify-server-binaries step, begin through its own branch."""
    return _slice(source, D2_SERVER_BINARIES_BEGIN, D2_APP_PAYLOAD_DEST_COMMENT)


def _d2_app_payload_step(source: str) -> str:
    """The whole d2-verify-app-payload step, begin through its own branch."""
    return _slice(source, D2_APP_PAYLOAD_BEGIN, D2_FFMPEG_DEST_COMMENT)


def test_f17_d2_server_binaries_verification_logs_a_result_before_branching_on_it() -> None:
    step = _d2_server_binaries_step(_hooks_source())

    pop_at = step.find("Pop $0")
    assert pop_at != -1, "the d2-verify-server-binaries step no longer pops the child's exit code"
    branch_at = step.find("${If} $0 != 0")
    assert branch_at != -1, "the d2-verify-server-binaries step no longer branches on the exit code"

    result_marker = '!insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: returned $0"'
    result_at = step.find(result_marker)
    assert result_at != -1, (
        "the d2-verify-server-binaries step writes a 'begin' breadcrumb and then, on "
        "success, nothing else to install-progress.log. The newcomer walkthrough of "
        "dd7f835f (evidence/install-progress.log, finding F-17) shows exactly this -- "
        "'step d2-verify-server-binaries: begin' with no matching result line -- so an "
        "operator reading the log cannot tell whether verification ran, passed, or hung"
    )
    assert pop_at < result_at < branch_at, (
        "the result breadcrumb must be written immediately after popping the exit code "
        "and BEFORE the success/failure branch, exactly like vc-redist / stage-packs / "
        "d4-provision -- so the log carries a result unconditionally on both the success "
        "and the failure path, not only when CIVICCAST_FAIL happens to fire"
    )


def test_f17_d2_app_payload_verification_logs_a_result_before_branching_on_it() -> None:
    step = _d2_app_payload_step(_hooks_source())

    pop_at = step.find("Pop $0")
    assert pop_at != -1, "the d2-verify-app-payload step no longer pops the child's exit code"
    branch_at = step.find("${If} $0 != 0")
    assert branch_at != -1, "the d2-verify-app-payload step no longer branches on the exit code"

    result_marker = '!insertmacro CIVICCAST_STEP "step d2-verify-app-payload: returned $0"'
    result_at = step.find(result_marker)
    assert result_at != -1, (
        "the d2-verify-app-payload step writes a 'begin' breadcrumb and then, on success, "
        "nothing else to install-progress.log. The newcomer walkthrough of dd7f835f "
        "(evidence/install-progress.log, finding F-17) shows 107 seconds between this "
        "step's begin and the next breadcrumb with no result line in between -- an "
        "operator reading the log cannot tell whether verification ran, passed, or hung"
    )
    assert pop_at < result_at < branch_at, (
        "the result breadcrumb must be written immediately after popping the exit code "
        "and BEFORE the success/failure branch, exactly like vc-redist / stage-packs / "
        "d4-provision -- so the log carries a result unconditionally on both the success "
        "and the failure path"
    )


def test_m3_no_claim_about_machine_state_is_made_before_the_state_exists() -> None:
    """D3 runs BEFORE D4 provisioning, service registration and the firewall
    rule. Anything the D3 branch says about what is running on this machine is
    a claim about a state that has not happened yet."""
    macro = _postinstall_macro(_hooks_source())
    d3_arm = _slice(macro, "${ElseIf} $0 == 10", "${ElseIf} $0 == 20")
    claims = [text for pair in _operator_claims(d3_arm) for text in pair if text]

    for claim in claims:
        assert not re.search(r"still running|is healthy|no data was lost", claim, re.IGNORECASE), (
            f"{claim!r} describes machine state from inside the D3 branch, which runs "
            "before D4 provisioning, service registration and the firewall rule have "
            "happened. That claim belongs at the end of the macro, where the state exists "
            "and can be read"
        )
    assert not any("CIVICCAST_NOTICE" in line for line in _code(d3_arm).splitlines()), (
        "the D3 rollback branch raises the operator dialog before setup has finished. "
        "The dialog must be raised at the end, from verified final state"
    )


# --------------------------------------------------------------------------
# DELTA-M-02 (rewalk-de3aaf6f, 2026-08-02): a fresh install must not print a
# raw "ERROR:" line while stopping a bootstrap process that was never there
# --------------------------------------------------------------------------

BOOTSTRAP_STOP_BEGIN = '!insertmacro CIVICCAST_STEP "preinstall: stopping existing bootstrap"'
PREINSTALL_DONE = '!insertmacro CIVICCAST_STEP "preinstall: done"'


def _bootstrap_stop_step(source: str) -> str:
    """The whole 'stopping existing bootstrap' preinstall step."""
    return _slice(source, BOOTSTRAP_STOP_BEGIN, PREINSTALL_DONE)


def test_delta_m02_the_bootstrap_taskkill_never_runs_on_a_fresh_install() -> None:
    """Evidence 016b-error-line-crop.png (rewalk-de3aaf6f): a genuinely fresh
    install -- no "$INSTDIR\\CivicCast Native.exe" left behind by a prior
    install -- ran `taskkill.exe /IM "CivicCast Native.exe" /T /F`
    unconditionally anyway. Under this installer's execution context that
    surfaced as a raw "ERROR: The user name or password is incorrect." line
    in the operator-visible details pane (nsExec::ExecToLog echoes the
    child's stdout/stderr verbatim), immediately after "Stopping the existing
    CivicCast Native bootstrap before installation...", right before the
    install went on to complete successfully -- alarming and confusing.

    The taskkill call must be gated behind the exact same existence check the
    sc.exe-based service-stop step immediately above it already uses -- there
    is nothing to stop on a fresh install, so nothing should be attempted."""
    step = _bootstrap_stop_step(_hooks_source())

    gate_at = step.find('${If} ${FileExists} "$INSTDIR\\CivicCast Native.exe"')
    taskkill_at = step.find("taskkill.exe")
    assert gate_at != -1, (
        "the 'stopping existing bootstrap' step no longer opens with a "
        '${If} ${FileExists} "$INSTDIR\\CivicCast Native.exe" gate -- taskkill must never '
        "run against a target that provably does not exist yet"
    )
    assert taskkill_at != -1, "the step no longer stops the bootstrap process at all"
    assert gate_at < taskkill_at, (
        "the FileExists gate must come BEFORE the taskkill call, not after -- taskkill "
        "must be inside the gated arm, never reached unconditionally"
    )

    else_at = step.find("${Else}")
    endif_at = step.find("${EndIf}")
    assert else_at != -1 and endif_at != -1, (
        "the step must have both a gated arm (exe present: stop it) and an ${Else} arm "
        "(exe absent: nothing to stop) closed by ${EndIf}"
    )
    assert gate_at < taskkill_at < else_at < endif_at, (
        "taskkill must sit strictly inside the ${If}...${Else} arm -- if it runs after "
        "${Else} or after ${EndIf} it is unconditional again"
    )


def test_delta_m02_the_fresh_install_arm_is_silent_not_a_raw_child_invocation() -> None:
    """The safety property: the ${Else} arm (fresh install, nothing to stop)
    must say so in plain English and must never itself invoke nsExec/taskkill
    -- otherwise the same raw-ERROR hazard just moves to the other branch."""
    step = _bootstrap_stop_step(_hooks_source())

    else_at = step.find("${Else}")
    endif_at = step.find("${EndIf}")
    assert else_at != -1 and endif_at != -1
    fresh_install_arm = step[else_at:endif_at]

    assert "nsExec" not in fresh_install_arm, (
        "the fresh-install arm must not run any child process at all -- there is nothing "
        "to stop, so nothing should be executed"
    )
    assert "taskkill" not in fresh_install_arm, (
        "the fresh-install arm must not invoke taskkill -- that is exactly the "
        "unconditional call this fix removes from the fresh-install path"
    )
    assert "DetailPrint" in fresh_install_arm, (
        "the fresh-install arm must say something informational in the details pane -- "
        "silence there would be as confusing as the raw ERROR line it replaces"
    )


def test_delta_m02_a_genuine_taskkill_failure_still_reaches_the_details_pane() -> None:
    """Not fixed by hiding real errors: when a prior install DID leave the exe
    behind, the taskkill call and its output must still be logged exactly as
    before -- only the fresh-install case (nothing to stop) is now silent
    about the raw child output."""
    step = _bootstrap_stop_step(_hooks_source())

    gate_at = step.find('${If} ${FileExists} "$INSTDIR\\CivicCast Native.exe"')
    else_at = step.find("${Else}")
    assert gate_at != -1 and else_at != -1
    prior_install_arm = step[gate_at:else_at]

    assert "nsExec::ExecToLog" in prior_install_arm, (
        "when a prior install left the exe behind, taskkill's output must still be "
        "captured through nsExec::ExecToLog exactly as before -- this fix must not "
        "suppress a genuine stop attempt or its real output"
    )
    assert 'taskkill.exe /IM "CivicCast Native.exe" /T /F' in prior_install_arm, (
        "the real stop command itself must be unchanged"
    )


# --------------------------------------------------------------------------
# 2026-09-02 (owner decision): the installer now EMBEDS the signed station
# index and the tiny `core` pack as Tauri bundle.resources, so a
# download-only install/upgrade of setup.exe ALONE can activate. Before this,
# d4-activate-station imported only "$EXEDIR\station\station-index.json" and
# failed the install outright when it was absent -- and its dialog offered a
# remedy ("publish one alongside the installer") that is release-engineering
# work, not something the operator standing at the machine can perform.
#
# Same class of defect as chain F above: a fail-loud dialog naming a remedy
# outside the operator's reach. Pinned here, next to chain F, for that reason.
# --------------------------------------------------------------------------

ACTIVATE_STATION_BEGIN = '!insertmacro CIVICCAST_STEP "step d4-activate-station: begin"'
ACTIVATE_STATION_END = "  ; UPGRADE-VS-FRESH ROUTING"


def _activate_station_step(source: str) -> str:
    return _slice(source, ACTIVATE_STATION_BEGIN, ACTIVATE_STATION_END)


def _activate_station_fail_messages(step: str) -> tuple[str, ...]:
    messages = re.findall(
        r"!insertmacro CIVICCAST_FAIL \$\{CIVICCAST_EXIT_D4_ACTIVATION\} \"(.*?)\"\n",
        step,
        re.DOTALL,
    )
    assert messages, "the activation step no longer fails loud through CIVICCAST_FAIL"
    return tuple(messages)


def test_station_activation_tries_the_kit_bundle_before_the_embedded_index() -> None:
    """Order is the whole contract. An air-gapped station that carried a full
    signed bundle to the machine on a stick must keep using THAT bundle --
    its component packs live in the same media directory the index sits in
    (native_distribution.rs::acquire_station_distribution derives media_root
    from the index's parent). Preferring the embedded copy would silently
    send that station down the pack-cache path instead, on a machine whose
    cache is empty."""
    step = _activate_station_step(_hooks_source())

    exedir = r'IfFileExists "$EXEDIR\station\station-index.json"'
    instdir = r'IfFileExists "$INSTDIR\station\station-index.json"'
    assert exedir in step, "the USB-kit index must still be probed"
    assert instdir in step, "the embedded index must be probed as a fallback"
    assert step.index(exedir) < step.index(instdir), (
        "the kit's own bundle must be preferred over the embedded index"
    )


def test_station_activation_only_fails_closed_when_neither_index_exists() -> None:
    """The K1 invariant survives: a missing index still aborts the install
    (never a silent success that leaves a station which can never activate).
    What changed is only WHEN -- both sources must be exhausted first."""
    step = _activate_station_step(_hooks_source())

    no_index_arm = step.split("civiccast_activate_station_no_index:", 1)
    assert len(no_index_arm) == 2, (
        "there must be a distinct branch for 'no index at either location' -- "
        "otherwise the two sources are not really both tried"
    )
    arm = no_index_arm[1].split("civiccast_activate_station_ran:", 1)[0]
    assert "!insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION}" in arm, (
        "no index anywhere must still abort the install through CIVICCAST_FAIL"
    )
    assert "!insertmacro CIVICCAST_ALERT" not in arm


def test_station_activation_failure_names_a_remedy_the_operator_can_perform() -> None:
    """chain-F rule, applied to this dialog: 'publish one alongside the
    installer' is release-engineering work, not an action available to the
    volunteer standing at the station. The replacement must name things the
    operator can actually do."""
    messages = _activate_station_fail_messages(_activate_station_step(_hooks_source()))
    combined = "\n".join(messages)

    assert "publish one alongside the installer" not in combined, (
        "the old dialog told the operator to publish a station bundle -- that is a "
        "build-pipeline action, not an operator action"
    )
    assert re.search(r"release page", combined, re.IGNORECASE), (
        "the operator must be told where a good setup.exe comes from"
    )
    assert re.search(r"\bkit\b", combined, re.IGNORECASE), (
        "the operator must be told the other real remedy: copy the full CivicCast kit "
        "folder (setup.exe together with its station folder) onto this machine"
    )
    for message in messages:
        assert "installer log" in message, (
            "every activation failure dialog must keep pointing at the installer log"
        )


def test_station_activation_logs_which_index_source_it_used() -> None:
    r"""An install nobody watched is diagnosed from
    $COMMONPROGRAMDATA\CivicCast\install-progress.log alone. With two possible
    sources, 'activation failed' is not diagnosable unless the log says which
    index was imported."""
    step = _activate_station_step(_hooks_source())

    assert "step d4-activate-station: source EXEDIR" in step
    assert "step d4-activate-station: source INSTDIR" in step
    assert "step d4-activate-station: no station index at" in step, (
        "the neither-present case must also name itself in the breadcrumb log"
    )


# ---------------------------------------------------------------------------
# Installer-path audit batch (2026-09-03). Source-shape contracts, in this
# file's established convention -- the .nsh is NSIS script, and no test in
# this repository executes an installer route.
# ---------------------------------------------------------------------------


def test_bl02_a_failed_postinstall_disarms_the_service_before_aborting() -> None:
    r"""BL-02. The upgrade sequence is: PREINSTALL stops the service while
    PRESERVING its registration -> Tauri replaces $INSTDIR -> POSTINSTALL
    stages the NEW payload into $INSTDIR\runtime -> and only THEN does D3 run.
    So by the time ANY POSTINSTALL failure branch fires, the machine holds new
    code, the old unmigrated database, and CivicCastSupervisor still
    registered `--startup auto`, merely stopped. The operator reads "the
    service has NOT been started on the new files" -- true at that instant --
    reboots, and the SCM starts the supervisor on the new payload against the
    old schema. PR #143's fix moved that from "immediately" to "next boot".
    """
    source = _hooks_source()
    fail_macro = _slice(source, "!macro CIVICCAST_FAIL CODE TEXT", "!macroend")

    assert "--civiccast-stop-native-service" in fail_macro, (
        "CIVICCAST_FAIL must stop the service before aborting"
    )
    product_stop = fail_macro.index("--civiccast-stop-native-service")
    fallback_gate = fail_macro.index('${If} $R8 != "0"', product_stop)
    direct_stop = fail_macro.index('"$SYSDIR\\sc.exe" stop CivicCastSupervisor')
    demand_start = fail_macro.index('"$SYSDIR\\sc.exe" config CivicCastSupervisor start= demand')
    assert product_stop < fallback_gate < direct_stop < demand_start, (
        "the direct SCM stop must be conditional on a failed product stop and retain "
        "the manual-start containment step"
    )
    assert "StrCpy $R8 $R9" in fail_macro
    assert '${If} $R9 == "1062"' in fail_macro
    assert "AddSeconds(15)" in fail_macro
    assert "Get-Service -Name CivicCastSupervisor" in fail_macro
    assert "ServiceControllerStatus]::Stopped" in fail_macro, (
        "when the product stop CLI fails, CIVICCAST_FAIL must issue a direct SCM stop, "
        "accept ERROR_SERVICE_NOT_ACTIVE (1062), and otherwise poll a real STOPPED "
        "state for a bounded interval"
    )
    assert "sc.exe" in fail_macro and "start= demand" in fail_macro, (
        "CIVICCAST_FAIL must set the service to manual start so it does not "
        "auto-start onto the new payload at the next reboot"
    )
    assert "$CIVICCAST_PAYLOAD_REPLACED" in fail_macro, (
        "containment must be gated on the payload having been replaced -- a "
        "PREINSTALL failure aborts before any file changes and must leave the "
        "existing install's auto-start service exactly as it was"
    )
    assert "Var CIVICCAST_PAYLOAD_REPLACED" in source
    assert 'StrCpy $CIVICCAST_PAYLOAD_REPLACED "1"' in source

    # The flag is armed at the top of POSTINSTALL, before any failure branch.
    postinstall = _postinstall_macro(source)
    armed_at = postinstall.index('StrCpy $CIVICCAST_PAYLOAD_REPLACED "1"')
    first_fail_at = postinstall.index("!insertmacro CIVICCAST_FAIL")
    assert armed_at < first_fail_at


def test_bl02_the_exit_124_message_is_conditional_on_what_containment_achieved() -> None:
    """The operator text must describe the state the machine is ACTUALLY left
    in -- and containment is best-effort, so it cannot be stated as fact.

    Review of PR #145: the first pass asserted "the service has been stopped
    AND set to manual start" unconditionally, while `CIVICCAST_FAIL` runs
    those two steps best-effort by design (a containment step that could
    itself abort would replace an honest, specific failure message with a
    different one). That is the same defect BL-02 is about, one level up: a
    sentence true only when nothing went wrong, and silent when something did.
    """
    source = _hooks_source()
    assert "Var CIVICCAST_CONTAINED" in source

    fail_macro = _slice(source, "!macro CIVICCAST_FAIL CODE TEXT", "!macroend")
    assert 'StrCpy $CIVICCAST_CONTAINED "0"' in fail_macro, (
        "containment must be assumed FAILED until both steps report success"
    )
    assert 'StrCpy $CIVICCAST_CONTAINED "1"' in fail_macro
    # ...and it is set from BOTH results, not just the last one.
    assert "${If} $R8 ==" in fail_macro and "${AndIf} $R9 ==" in fail_macro

    branch = _slice(source, '${If} $CIVICCAST_CONTAINED == "1"', "civiccast_bootstrap_d3_done")
    confirmed, unconfirmed = branch.split("${Else}", 1)
    assert "stopped AND set to manual start" in confirmed
    assert "could NOT confirm" in unconfirmed, (
        "the unconfirmed branch must say so plainly, not repeat the optimistic claim"
    )
    assert "sc config CivicCastSupervisor start= demand" in unconfirmed, (
        "and it must give the operator the exact commands to contain it themselves"
    )
    assert "The service has NOT been started on the new files." not in branch, (
        "the ORIGINAL sentence was true only at that instant and is what BL-02 is about"
    )


def test_containment_treats_sc_config_1060_as_confirmed_not_unconfirmed() -> None:
    """Defect 5 of the beta.5.1 batch. `sc config CivicCastSupervisor
    start= demand` returning 1060 (ERROR_SERVICE_DOES_NOT_EXIST) means no
    service is registered at all -- a FRESH_INSTALL failure -- so nothing can
    auto-start onto the new payload. That IS containment. It used to be
    logged as "NOT confirmed ... the service may still auto-start", sending
    the operator to disarm a service that does not exist."""
    source = _hooks_source()
    fail_macro = _code(_slice(source, "!macro CIVICCAST_FAIL CODE TEXT", "!macroend"))
    containment = _slice(fail_macro, '${If} $R8 == "0"', "${Else}")
    assert '${AndIf} $R9 == "0"' in containment
    assert '${ElseIf} $R8 == "0"' in containment and '${AndIf} $R9 == "1060"' in containment
    assert containment.count('StrCpy $CIVICCAST_CONTAINED "1"') == 2, (
        "both the stopped+demand case and the no-service (1060) case must set CONTAINED"
    )
    assert "sc config returned 1060" in containment
    unconfirmed = _slice(fail_macro, "${Else}", "${EndIf}")
    assert "NOT confirmed" in unconfirmed, (
        "every OTHER combination must still be reported as unconfirmed"
    )


def test_ma17_the_uninstall_warns_that_it_destroys_the_pack_cache() -> None:
    r"""<installer-path-audit MA-17> KNOWN LIMITATION, stated not hidden.

    `RMDir /r "$INSTDIR\packs"` removes `$INSTDIR\packs\.station-cache` with
    everything else, so a download-only reinstall (setup.exe alone, no
    `station\` folder) cannot activate afterwards -- the signed index is
    embedded in setup.exe, the ~21 GB of model packs are not, and the cache
    they would have been served from is gone. That collides with the standing
    "download install is the floor" rule.

    NOT fixed in this batch: preserving the cache means either leaving a
    multi-gigabyte `$INSTDIR` behind (which contradicts this file's own
    "everything gone" uninstall contract and changes what a silent uninstall
    reports to winget/Intune) or relocating it and teaching `--cache-root` a
    second search location (audit MA-16) -- both owner decisions about what an
    uninstall means. What IS fixed: the operator is told, on every path,
    instead of discovering it at the next install.
    """
    source = _hooks_source()
    notice_at = source.index("CivicCast (Native) is being removed, including the downloaded AI")
    # Anchored on the EXECUTABLE removal, not the first textual match -- the
    # design comment above it necessarily quotes the statement it explains.
    removal_at = source.index('\n    RMDir /r "$INSTDIR\\packs"')
    assert notice_at < removal_at, "the warning must come BEFORE the removal"

    # Silent-safe by construction: CIVICCAST_NOTICE always breadcrumbs and
    # DetailPrints, and only shows a dialog when NOT /S -- so an unattended
    # uninstall records it in install-progress.log rather than hanging on a
    # dialog nobody can dismiss.
    assert "CIVICCAST_NOTICE" in source[notice_at - 200 : notice_at]

    notice_end = source.index('"\n', notice_at)
    notice = source[notice_at:notice_end]
    assert "are NOT affected" in notice, (
        "the operator must be told what is KEPT, or the warning reads as data loss"
    )
    assert "full CivicCast kit folder" in notice, (
        "and given the remedy that actually works for a later reinstall"
    )
    assert ".station-cache" in notice, "naming the directory is what makes it actionable"

    breadcrumb = source[removal_at - 400 : removal_at]
    assert "see audit MA-17" in breadcrumb, (
        "the breadcrumb must name the finding, so an install log says why the cache went"
    )


def test_bl11_a_running_but_not_serving_service_gets_its_own_code_and_message() -> None:
    """BL-11. Nothing in the elevated install chain ever contacted /health;
    SCM `RUNNING` was the only success signal the installer had."""
    source = _hooks_source()
    assert "!define CIVICCAST_EXIT_D4_SERVICE_NOT_SERVING 125" in source
    assert "!define CIVICCAST_EXIT_D4_SERVICE_NOT_STARTING 126" in source

    step = _slice(
        source,
        '!insertmacro CIVICCAST_STEP "step d4-service-registration: begin"',
        '!insertmacro CIVICCAST_STEP "step d4-firewall-rule: begin"',
    )
    assert "${If} $0 == 84" in step, "exit 84 (running, not serving) needs its own branch"
    assert "${ElseIf} $0 == 83" in step, (
        "exit 83 (registered, would not start) needs its own branch"
    )
    assert "${CIVICCAST_EXIT_D4_SERVICE_NOT_SERVING}" in step
    assert "${CIVICCAST_EXIT_D4_SERVICE_NOT_STARTING}" in step
    # MA-29: 83 must no longer be described as a registration failure.
    not_starting_arm = _slice(step, "${ElseIf} $0 == 83", "${ElseIf} $0 != 0")
    assert "startup failure, not a registration failure" in not_starting_arm


def test_bl13_an_unprovable_runtime_selector_aborts_the_install() -> None:
    """BL-13. The Rust side printed one sentence -- which itself said "The
    native runtime will not start until an operator sets it" -- and returned
    Ok, after which setup registered and started a service whose control
    plane the guard blocks, and reported success."""
    source = _hooks_source()
    assert "!define CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP   127" in source
    step = _d4_provision_step(source)
    assert "${ElseIf} $0 == 85" in step
    assert "${CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP}" in step
    arm = _slice(step, "${ElseIf} $0 == 85", "${Else}")
    assert "ActiveRuntime" in arm, "the message must name the value an administrator has to set"


@pytest.mark.skipif(
    os.name != "nt", reason="executes the real NSIS service-registration cwd handoff"
)
@pytest.mark.parametrize("exit_code", [0, 70], ids=["success", "registration-failure"])
def test_service_registration_child_does_not_inherit_install_root_as_its_cwd(
    tmp_path: Path, exit_code: int
) -> None:
    """The registration child applies ACLs to $INSTDIR and can fail when its
    inherited current directory is that same tree. Execute the production
    OUTDIR handoff in a small installer and prove both child isolation and
    restoration of NSIS's output directory.
    """
    makensis = Path.home() / "AppData/Local/tauri/NSIS/Bin/makensis.exe"
    if not makensis.is_file():
        pytest.skip("Tauri NSIS compiler is unavailable")

    source = _hooks_source()
    service_step = _slice(
        source,
        '!insertmacro CIVICCAST_STEP "step d4-service-registration: begin"',
        '!insertmacro CIVICCAST_STEP "step d4-firewall-rule: begin"',
    )
    executable_step = "\n".join(
        line
        for line in service_step.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    )
    wrapper = re.search(
        r"(?:InitPluginsDir\s*\n\s*)?"
        r"StrCpy \$R9 \"\$OUTDIR\"\s*\n\s*"
        r"SetOutPath \"\$PLUGINSDIR\"\s*\n\s*"
        r"(?P<execute>nsExec::ExecToStack [^\n]+)\s*\n\s*"
        r"Pop \$0\s*\n\s*Pop \$1\s*\n\s*"
        r"SetOutPath \"\$R9\"",
        executable_step,
    )

    child_cwd_file = tmp_path / "registration-child-cwd.txt"
    child_command = f'cmd.exe /d /c cd > "$EXEDIR/{child_cwd_file.name}"'
    if exit_code:
        child_command += f" & exit /b {exit_code}"
    diagnostic_exec = f"nsExec::ExecToStack '{child_command}'"
    if wrapper is None:
        # Baseline behavior: run the same child from the install output
        # directory. The assertion below must fail on this path, proving the
        # regression test observes the inherited-cwd defect itself.
        registration_body = f"{diagnostic_exec}\nPop $0\nPop $1"
    else:
        registration_body = re.sub(
            r"nsExec::ExecToStack [^\n]+", diagnostic_exec, wrapper.group(0), count=1
        )

    install_root = tmp_path / "install-root"
    payload = tmp_path / "restore-sentinel.txt"
    payload.write_text("output directory restored", encoding="utf-8")
    exit_code_file = tmp_path / "registration-exit-code.txt"
    output = tmp_path / "registration-cwd.exe"
    script = tmp_path / "registration-cwd.nsi"
    script.write_text(
        "Unicode true\n!include LogicLib.nsh\nSilentInstall silent\n"
        "RequestExecutionLevel user\n"
        f'OutFile "{output}"\n'
        "Section\n"
        f'SetOutPath "{install_root}"\n'
        f"{registration_body}\n"
        f'FileOpen $R8 "$EXEDIR\\{exit_code_file.name}" w\n'
        'FileWrite $R8 "$0"\nFileClose $R8\n'
        f'File /oname=restored-output.txt "{payload}"\n'
        "SectionEnd\n",
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [str(makensis), "/V2", str(script)], capture_output=True, text=True, timeout=60
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    ran = subprocess.run([str(output), "/S"], capture_output=True, timeout=60)
    assert ran.returncode == 0, f"NSIS cwd probe exited {ran.returncode}"

    child_cwd = Path(child_cwd_file.read_text(encoding="utf-8").strip()).resolve()
    assert child_cwd != install_root.resolve(), (
        "the registration child inherited the install-root cwd while its ACL "
        f"walk targets that tree: {child_cwd}"
    )
    assert (install_root / "restored-output.txt").read_text(encoding="utf-8") == (
        "output directory restored"
    ), "NSIS must restore the install output directory after the child returns"
    assert exit_code_file.read_text(encoding="utf-8") == str(exit_code), (
        "restoring OUTDIR and recording the child output must preserve its exit code"
    )

    assert wrapper is not None, "service registration must isolate its inherited cwd"
    assert "--civiccast-register-native-service" in wrapper.group("execute")
    restore_at = service_step.index('SetOutPath "$R9"')
    returned_at = service_step.index(
        '!insertmacro CIVICCAST_STEP "step d4-service-registration: returned $0"'
    )
    branch_at = service_step.index("${If} $0 == 84")
    report_at = service_step.index(
        '!insertmacro CIVICCAST_STEP "step d4-service-registration: child reported: $1"'
    )
    assert restore_at < returned_at < report_at < branch_at, (
        "restore OUTDIR before recording the captured child output or branching on its exit code"
    )
    step_logger = _slice(source, "!macro CIVICCAST_STEP TEXT", "!macroend")
    assert "$0" not in step_logger and "$1" not in step_logger, (
        "CIVICCAST_STEP must preserve the registration exit code and captured output"
    )


OWNERSHIP_OBSERVATION_FILE = "$COMMONPROGRAMDATA\\CivicCast\\provision\\ownership-observation.txt"
OWNERSHIP_RECOVERY_DOC = "$COMMONPROGRAMDATA\\CivicCast\\provision\\OWNERSHIP-RECOVERY.md"


def _d4_provision_step(source: str) -> str:
    """Return only D4 provisioning; station activation now precedes D3/D4."""
    return _slice(
        source,
        '!insertmacro CIVICCAST_STEP "step d4-provision: begin"',
        '!insertmacro CIVICCAST_STEP "step d4-service-registration: begin"',
    )


def test_ownership_claim_observation_is_read_back_into_the_progress_log() -> None:
    r"""beta.5.1 field defect (2026-09-09, dirty test box with uninstall
    history): the Rust CLI refused the ownership claim on an `Unknown` it
    never named -- its only detail line went to stderr, i.e. the NSIS details
    pane, which is not persisted anywhere. The CLI now writes a one-line
    observation file and the d4 step MUST read it back into
    install-progress.log via CIVICCAST_STEP, on every path, so the log
    carries the hive/SID, view, error kind and OS error code."""
    step = _code(_d4_provision_step(_hooks_source()))
    read_at = step.index(f'FileOpen $R7 "{OWNERSHIP_OBSERVATION_FILE}" r')
    assert f'${{If}} ${{FileExists}} "{OWNERSHIP_OBSERVATION_FILE}"' in step
    assert "FileRead $R7 $R6" in step
    assert "FileClose $R7" in step
    logged_at = step.index(
        '!insertmacro CIVICCAST_STEP "step d4-provision: runtime ownership: $R6"'
    )
    returned_at = step.index('!insertmacro CIVICCAST_STEP "step d4-provision: returned $0"')
    cleared_at = step.index(f'Delete "{OWNERSHIP_OBSERVATION_FILE}"')
    assert cleared_at < returned_at, (
        "a previous run's observation must be deleted before the CLI runs, so the read-back "
        "can only report THIS run's observation"
    )
    first_branch_at = step.index("${If} $0 == 0")
    assert returned_at < read_at < logged_at < first_branch_at, (
        "the observation must be read and logged after the CLI returns and BEFORE any "
        "exit-code branch, so every path (including the corroborated claim) is logged"
    )
    # A missing file must not leave $R6 empty: the dialog embeds it.
    assert 'StrCpy $R6 "(no ownership observation file was written' in step
    # No TrimNewLines: Tauri's NSIS 3.11 ships FileFunc without it, so the
    # Rust writer emits the line WITHOUT a trailing newline instead.
    assert "TrimNewLines" not in step


def test_the_exit_85_dialog_carries_the_actual_observation_not_a_guess() -> None:
    """Defect 4 of the beta.5.1 batch: the dialog used to say the cause was
    "most often a permissions problem on HKEY_USERS" -- a guess that was
    wrong on the box it was measured on. It must now embed the observation
    the CLI recorded ($R6), say that the log and the recovery document carry
    it (both must exist: the read-back above and the Rust writer), and state
    the untouched-database fact the new step ORDER makes true."""
    source = _hooks_source()
    arm = _slice(_d4_provision_step(source), "${ElseIf} $0 == 85", "${Else}")
    assert "permissions problem on HKEY_USERS" not in arm
    assert "What setup observed: $R6" in arm
    assert OWNERSHIP_RECOVERY_DOC in arm
    assert "$COMMONPROGRAMDATA\\CivicCast\\install-progress.log" in arm
    assert "Setup stopped before provisioning" in arm
    # Narrowed (PR #209 review): only the provisioning outputs are promised
    # untouched -- on the upgrade path $INSTDIR was already replaced by
    # d3-engine/the Tauri section, so "nothing was deleted" would be false.
    assert "postgresql.conf, pg_hba.conf and your database credential were not touched" in arm
    assert "Nothing was deleted" not in arm
    assert "ActiveRuntime" in arm and r"$\"native$\"" in arm

    # The Rust side is the writer of both files the dialog cites.
    registration = (
        BOOTSTRAP_HOOKS_NSH.parent / "src" / "native_service_registration.rs"
    ).read_text(encoding="utf-8")
    assert (
        'pub const OWNERSHIP_OBSERVATION_FILE_NAME: &str = "ownership-observation.txt";'
        in registration
    )
    assert (
        'pub const OWNERSHIP_RECOVERY_DOCUMENT_FILE_NAME: &str = "OWNERSHIP-RECOVERY.md";'
        in registration
    )


def test_the_exit_87_dialog_names_the_other_product_and_never_the_registry_edit() -> None:
    """Round 3 of the beta.5 ownership defect (corrected field report,
    2026-09-09): the refused box had a REAL `HKCU\\...\\Uninstall\\CivicCast
    Installer` registration (3.0.0-beta1), not an unreadable hive, and the
    decision table sent it through the exit-85 arm whose text told the
    operator to set ActiveRuntime by hand. A genuinely registered other
    product is now CLI exit 87 -> its own installer code, its own dialog:
    the observation ($R6) leads with the product, and the remedy is to
    uninstall it (or cutover from it) -- no registry instruction."""
    source = _hooks_source()
    assert "!define CIVICCAST_EXIT_D4_OTHER_PRODUCT          135" in source
    step = _d4_provision_step(source)
    assert "${ElseIf} $0 == 87" in step
    arm = _slice(step, "${ElseIf} $0 == 87", "${ElseIf} $0 == 85")
    assert "${CIVICCAST_EXIT_D4_OTHER_PRODUCT}" in arm
    assert "What setup observed: $R6" in arm
    assert "found another CivicCast product installed on this machine" in arm
    assert "Settings > Apps" in arm
    assert "cutover-to-native" in arm
    assert OWNERSHIP_RECOVERY_DOC in arm
    assert "$COMMONPROGRAMDATA\\CivicCast\\install-progress.log" in arm
    assert "postgresql.conf, pg_hba.conf and your database credential were not touched" in arm
    code_only = _code(arm)
    assert "ActiveRuntime" not in code_only, (
        "a real other-product refusal must not tell the operator to edit ActiveRuntime"
    )
    assert "permissions" not in code_only

    # The 85 arm keeps the registry remedy but no longer says "could not
    # determine" as if the cause were unknowable, and never guesses at
    # permissions.
    arm_85 = _code(_slice(step, "${ElseIf} $0 == 85", "${Else}"))
    assert "permissions problem" not in arm_85
    assert "could not establish which CivicCast runtime owns this machine" in arm_85
    assert r"$\"native$\"" in arm_85
    assert "Settings > Apps" not in arm_85

    # The Rust side: exit 87 exists, is what a real Present maps to, and the
    # inert-leftover claim (no distro, no autostart, previous native
    # hand-off recorded) is a ClaimNative with a warning, not a refusal.
    registration = (
        BOOTSTRAP_HOOKS_NSH.parent / "src" / "native_service_registration.rs"
    ).read_text(encoding="utf-8")
    assert "pub const OTHER_PRODUCT_PRESENT_EXIT_CODE: i32 = 87;" in registration
    assert (
        "SelectorClaimAction::LeaveOtherProductPresent => Some(OTHER_PRODUCT_PRESENT_EXIT_CODE)"
        in registration
    )
    uninstall = (BOOTSTRAP_HOOKS_NSH.parent / "src" / "native_uninstall.rs").read_text(
        encoding="utf-8"
    )
    assert "pub fn classify_wsl_product_for_claim" in uninstall
    assert "WslProductVerdict::PresentInert" in uninstall
    assert "WslProductVerdict::Absent | WslProductVerdict::PresentInert => {" in uninstall


def _nsis_max_strlen() -> int:
    """NSIS_MAX_STRLEN, MEASURED from the toolchain (`makensis -HDRINFO`)
    whenever a makensis is reachable -- Tauri's own copy under
    %LOCALAPPDATA%\\tauri\\NSIS first, then PATH. Only when neither exists
    does this fall back to what that measurement gave for Tauri's NSIS 3.11
    on 2026-09-09 (1024); a toolchain built with a different limit is then
    caught by whoever has it installed, not by a number in this file."""
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "tauri" / "NSIS" / "Bin" / "makensis.exe")
    on_path = shutil.which("makensis")
    if on_path:
        candidates.append(Path(on_path))
    for exe in candidates:
        if not exe.is_file():
            continue
        try:
            header = subprocess.run(
                [str(exe), "-HDRINFO"], capture_output=True, text=True, timeout=60, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        measured = re.search(r"NSIS_MAX_STRLEN=(\d+)", header)
        if measured is not None:
            return int(measured.group(1))
    return 1024


def _alert_log_line_overhead(source: str) -> tuple[int, int]:
    """The characters CIVICCAST_ALERT puts AROUND ${TEXT} in the one string
    NSIS builds for `FileWrite $2 "$9${TEXT}$\\r$\\n"`: CIVICCAST_STEP's `$9`
    timestamp (`[YYYY-MM-DD HH:MM:SS] `), the `ALERT: ` prefix, and the
    trailing CRLF -- which is in the same buffer, so it counts (round 5:
    the old model stopped at "ALERT: " and was 2 short). Returned under the
    same two conventions the static text is counted in: runtime (`$\\r$\\n`
    is two characters) and pessimistic source-literal (six)."""
    step = _slice(source, "!macro CIVICCAST_STEP TEXT", "!macroend")
    stamp_match = re.search(r'StrCpy \$9 "([^"]*)"', step)
    assert stamp_match is not None, "CIVICCAST_STEP no longer builds its timestamp in $9"
    # ${GetTime} "L": $2 day, $3 month, $4 year, $6 hour, $7 minute,
    # $8 second -- four digits for the year, two for everything else.
    stamp = stamp_match.group(1).replace("$4", "2026")
    stamp = re.sub(r"\$[2-8]", "09", stamp)
    assert "$" not in stamp, stamp
    write_match = re.search(r'FileWrite \$2 "\$9\$\{TEXT\}([^"]*)"', step)
    assert write_match is not None, "CIVICCAST_STEP no longer writes $9${TEXT} in one FileWrite"
    trailer = write_match.group(1)
    alert = _slice(source, "!macro CIVICCAST_ALERT TEXT", "!macroend")
    prefix_match = re.search(r'!insertmacro CIVICCAST_STEP "([^"]*)\$\{TEXT\}"', alert)
    assert prefix_match is not None, (
        "CIVICCAST_ALERT no longer prefixes ${TEXT} through CIVICCAST_STEP"
    )
    prefix = prefix_match.group(1)
    runtime = len(stamp) + len(prefix) + len(trailer.replace("$\\r$\\n", "\r\n"))
    pessimistic = len(stamp) + len(prefix) + len(trailer)
    return runtime, pessimistic


def test_the_exit_85_and_87_dialogs_fit_the_nsis_string_budget_with_the_observation() -> None:
    """The exit-85 and exit-87 dialog strings embed the observation line
    ($R6, capped at OWNERSHIP_OBSERVATION_LINE_MAX_CHARS by the Rust writer)
    and CIVICCAST_ALERT wraps the result in a timestamp, "ALERT: " and a CRLF
    before ONE FileWrite; the whole string must fit NSIS_MAX_STRLEN or the
    log line (and the dialog) silently truncate -- for BOTH ownership arms
    (round 3 added 87). Round 5 (delta review of round 4): every number here
    is derived -- NSIS_MAX_STRLEN from makensis when present, the log-line
    overhead from the CIVICCAST_STEP/CIVICCAST_ALERT macros, the two static
    strings from the arms, the cap from the Rust constant -- so none can
    drift; the floor on the cap is owned by the Rust lead tests, not by a
    magic number duplicated here."""
    source = _hooks_source()
    step = _d4_provision_step(source)
    registration = (
        BOOTSTRAP_HOOKS_NSH.parent / "src" / "native_service_registration.rs"
    ).read_text(encoding="utf-8")
    cap = re.search(r"pub const OWNERSHIP_OBSERVATION_LINE_MAX_CHARS: usize = (\d+);", registration)
    assert cap is not None
    runtime_overhead, pessimistic_overhead = _alert_log_line_overhead(source)
    assert 0 < runtime_overhead <= pessimistic_overhead
    max_strlen = _nsis_max_strlen()
    # NSIS_MAX_STRLEN counts the terminating NUL, so max_strlen - 1
    # characters fit; keep one more in hand (the `< 1023` this test has
    # asserted since round 2, now relative to the measured limit).
    usable = max_strlen - 2
    arms = {
        "85": (
            _slice(step, "${ElseIf} $0 == 85", "${Else}"),
            "CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP",
        ),
        "87": (
            _slice(step, "${ElseIf} $0 == 87", "${ElseIf} $0 == 85"),
            "CIVICCAST_EXIT_D4_OTHER_PRODUCT",
        ),
    }
    # Round 4 (hostile review of round 3): the budget is MEASURED from the
    # real static strings on every run, under BOTH ways of counting them --
    # the runtime expansion (`$\r$\n` -> CRLF, `$\"` -> `"`,
    # `$COMMONPROGRAMDATA` -> C:\ProgramData, `$R6` -> the observation) and
    # the pessimistic source-literal count (every `$\r$\n` at its six
    # source characters, `$\"` at three), which is what a reviewer counting
    # the .nsh by hand gets. The cap must fit under the LARGER of the two,
    # so no hardcoded "564" can drift again. Whoever lengthens either dialog
    # gets the exact numbers in the failure message.
    static_lengths: dict[str, tuple[int, int]] = {}
    for code, (arm, define) in arms.items():
        text = re.search(rf'CIVICCAST_FAIL \$\{{{define}\}} "(.*)"', arm)
        assert text is not None, code
        literal = text.group(1)
        runtime = (
            literal.replace("$\\r$\\n", "\r\n")
            .replace('$\\"', '"')
            .replace("$COMMONPROGRAMDATA", "C:\\ProgramData")
            .replace("$R6", "")
        )
        pessimistic = literal.replace("$COMMONPROGRAMDATA", "C:\\ProgramData").replace("$R6", "")
        static_lengths[code] = (len(runtime), len(pessimistic))
    line_cap = int(cap.group(1))
    # Each convention carries its own overhead: the CRLF trailer is two
    # characters at runtime and six in the source literal, like every other
    # `$\\r$\\n` in the string.
    conventions = (
        ("runtime", 0, runtime_overhead),
        ("pessimistic", 1, pessimistic_overhead),
    )
    max_fixed = max(
        lengths[index] + overhead
        for lengths in static_lengths.values()
        for _label, index, overhead in conventions
    )
    max_safe_cap = usable - max_fixed
    for code, lengths in static_lengths.items():
        for label, index, overhead in conventions:
            static = lengths[index]
            total = static + line_cap + overhead
            assert total <= usable, (
                f"exit-{code} dialog ({label} count {static}) + observation cap {line_cap} "
                f"+ log-line overhead {overhead} = {total} chars > {usable} "
                f"(NSIS_MAX_STRLEN {max_strlen} - 2); the maximum safe "
                f"OWNERSHIP_OBSERVATION_LINE_MAX_CHARS for the current text is {max_safe_cap}"
            )
    assert line_cap <= max_safe_cap, (line_cap, max_safe_cap, static_lengths)
    # The FLOOR on the cap -- the exit-87 lead (user, product, version, an
    # HKU\<SID> key, UninstallString) must survive it -- is owned by the Rust
    # tests that build that lead at the constant's real value and assert it
    # is whole. This is only a tripwire that those pins still exist; it
    # duplicates no number.
    for pin in (
        "fn a_present_refusal_line_leads_with_the_product_and_maps_to_exit_87",
        "fn a_long_username_never_cuts_the_exit_87_lead_mid_path",
        "lead_end <= OWNERSHIP_OBSERVATION_LINE_MAX_CHARS - 64",
    ):
        assert pin in registration, f"the Rust floor pin {pin!r} is gone"


def test_ownership_claim_runs_before_the_provisioning_engine_mutates_state() -> None:
    """Defect 3: the claim used to run AFTER the Python engine had rewritten
    postgresql.conf/pg_hba.conf and completed its journal, so a refused
    install (exit 85) left the database configuration changed on a machine
    setup then declared unowned. In run_native_provision the claim must now
    precede the subprocess spawn."""
    registration = (
        BOOTSTRAP_HOOKS_NSH.parent / "src" / "native_service_registration.rs"
    ).read_text(encoding="utf-8")
    body = registration.split("pub fn run_native_provision(", 1)[1].split("\n}\n", 1)[0]
    claim_at = body.index("crate::native_uninstall::claim_install_selector()")
    spawn_at = body.index("std::process::Command::new(&python_exe)")
    # Round 3: every refusal (85 unknown/unreadable, 87 other product) goes
    # through refusal_exit_code before the spawn.
    refusal_at = body.index("if let Some(exit_code) = refusal_exit_code(selector_claim.action)")
    assert claim_at < refusal_at < spawn_at, (
        "the ownership claim and its exit-85/87 refusal must both precede the provisioning "
        "subprocess so a refused install leaves postgresql.conf/pg_hba.conf untouched"
    )


def test_ma08_activation_exit_codes_do_not_collapse_to_one_message() -> None:
    """MA-08. Five distinct activation exit codes shared one sentence about
    the station folder and the pack cache -- correct for 66-with-a-cache-miss
    and wrong for 67 (self-test), 78 (a build defect), and 64/65."""
    step = _activate_station_step(_hooks_source())
    for code in ("67", "66", "78", "64"):
        assert f"$0 == {code}" in step, f"activation exit {code} has no branch of its own"

    self_test_arm = _slice(step, "${ElseIf} $0 == 67", "${ElseIf} $0 == 66")
    assert "self-test" in self_test_arm
    assert "pack cache" not in self_test_arm, (
        "a self-test failure must not tell the operator their packs are missing"
    )

    trust_arm = _slice(step, "${ElseIf} $0 == 78", "${ElseIf} $0 == 64")
    assert "not a valid release build" in trust_arm
    assert "Nothing is wrong with this machine" in trust_arm


def test_ma28_the_cli_contract_comment_names_the_codes_that_actually_exist() -> None:
    """MA-28. The comment above the exit-code band used to say the product's
    CLI codes were "(40, 70, 73, 75, 76, 79, 81)" -- omitting thirteen live
    codes, and listing 40, which is a D3 ENGINE phase code."""
    source = _hooks_source()
    band_comment = _slice(
        source, "; POSTINSTALL failure exit codes.", "!define CIVICCAST_EXIT_PACK_DELIVERY"
    )
    # The old set is still quoted, deliberately -- but only inside the
    # sentence that RETRACTS it. Asserting the retraction is the real
    # contract; asserting the string's absence would just invite someone to
    # delete the correction along with the error.
    assert '"(40, 70, 73, 75, 76, 79, 81)". That set was wrong' in band_comment, (
        "the corrected comment must name what it is correcting, or the next "
        "person picking an exit code learns nothing from it"
    )
    for code in ("83", "84", "85", "86", "87"):
        assert code in band_comment, f"the new CLI code {code} must be recorded in the band comment"
    assert "40 unexpected fault" in band_comment, (
        "40 must be named as what it is -- a D3 engine phase code, not a CLI code"
    )


@pytest.mark.skipif(os.name != "nt", reason="executes the real NSIS preflight macro")
@pytest.mark.parametrize("incoming_errors", [False, True])
def test_version_preflight_preserves_extraction_destination_and_caller_state(
    tmp_path: Path, incoming_errors: bool
) -> None:
    makensis = Path.home() / "AppData/Local/tauri/NSIS/Bin/makensis.exe"
    if not makensis.is_file():
        pytest.skip("Tauri NSIS compiler is unavailable")
    macro = _slice(_hooks_source(), "!macro CIVICCAST_VERSION_PREFLIGHT", "!macroend") + "!macroend"
    # Supply identity through the script's existing input seam: the fixture
    # runs without elevation and must not read the station's protected registry.
    macro = macro.replace(
        '-CandidateVersion "${VERSION}"',
        '-CandidateVersion "${VERSION}" -InstalledVersion "1.0.0-beta.14"',
    )
    # Preserve the real subprocess result on refusal. NSIS DetailPrint is not
    # captured by subprocess stdout; exit130 alone loses the distinction between
    # PowerShell failure and nsExec startup/timeout. Fixture identity is synthetic.
    macro = macro.replace(
        "  Pop $0\n  Pop $1\n",
        '  Pop $0\n  Pop $1\nFileOpen $R8 "$EXEDIR\\preflight-diagnostic.txt" w\n'
        'FileWrite $R8 "nsExec exit=$0 output=$1"\nFileClose $R8\n',
        1,
    )
    payload = tmp_path / "fixture.txt"
    payload.write_text("must land in installation directory", encoding="utf-8")
    output = tmp_path / "preflight-state.exe"
    script = tmp_path / "preflight-state.nsi"
    script.write_text(
        "Unicode true\n!include LogicLib.nsh\nSilentInstall silent\nRequestExecutionLevel user\n"
        f'OutFile "{output}"\n!define VERSION "999.0.0"\n'
        f'!define CIVICCAST_PREFLIGHT_SCRIPT "{BOOTSTRAP_HOOKS_NSH.parent / "version-preflight.ps1"}"\n'
        + macro
        + '\nSection\nSetOutPath "$EXEDIR\\payload"\n'
        'StrCpy $0 "caller-zero"\nStrCpy $1 "caller-one"\n'
        + ("SetErrors\n" if incoming_errors else "ClearErrors\n")
        + "!insertmacro CIVICCAST_VERSION_PREFLIGHT\n"
        '${If} $OUTDIR != "$EXEDIR\\payload"\nSetErrorLevel 72\nQuit\n${EndIf}\n'
        '${If} $0 != "caller-zero"\nSetErrorLevel 73\nQuit\n${EndIf}\n'
        '${If} $1 != "caller-one"\nSetErrorLevel 74\nQuit\n${EndIf}\n'
        + ("${IfNot} ${Errors}\n" if incoming_errors else "${If} ${Errors}\n")
        + "SetErrorLevel 75\nQuit\n${EndIf}\n"
        + f'File /oname=sentinel.txt "{payload}"\n'
        "SetErrorLevel 0\nSectionEnd\n",
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [str(makensis), "/V2", str(script)], capture_output=True, text=True, timeout=60
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    ran = subprocess.run([str(output), "/S"], capture_output=True, timeout=60)
    assert ran.returncode == 0, (
        f"NSIS state regression exit {ran.returncode}: 72=OUTDIR, 73/74=registers, 75=Errors; "
        + (tmp_path / "preflight-diagnostic.txt").read_text(encoding="utf-8", errors="replace")
    )
    assert (tmp_path / "payload/sentinel.txt").read_text(encoding="utf-8") == payload.read_text(
        encoding="utf-8"
    )


def test_snapshot_phase_replaces_stopping_bootstrap_before_blocking_copy() -> None:
    source = _hooks_source()
    snapshot = source.split("!macro CIVICCAST_TRANSACTION_SNAPSHOT\n", 1)[1].split("!macroend", 1)[
        0
    ]
    assert snapshot.index("preinstall: saving recovery snapshot") < snapshot.index(
        "CIVICCAST_TRANSACTION_CALL Snapshot"
    )
    assert snapshot.index("preinstall: recovery snapshot saved") > snapshot.index(
        "CIVICCAST_TRANSACTION_CALL Snapshot"
    )
