; SPDX-License-Identifier: Apache-2.0
; Copyright (c) The CivicCast Authors
;
; Small native bootstrap hooks. Large station payloads are supplied through
; signed .ccpack files and stay out of this NSIS binary; the bounded Whistle
; pack is embedded so setup-only installs can activate from a Beta 10 cache.
;
; This is the ONE LIVE native installer hook file: `tauri.native.conf.json`
; (`bundle.windows.nsis.installerHooks`) references this file, and
; `tests/policy/test_native_installer_identity.py` pins that reference on the
; EFFECTIVE (deep-merged) native config. `scripts/build_native_bootstrap.py`'s
; `validate_native_bootstrap_config` is a hard gate requiring exactly this
; file name plus a `bundle.resources` map of exactly four entries:
;   resources/vc_redist.x64.exe       -> vc_redist.x64.exe
;   resources/station/station-index.json -> station/station-index.json
;   resources/station/core.ccpack        -> station/core.ccpack
;   resources/station/captions-whistle.ccpack -> station/captions-whistle.ccpack
; The signed index and placeholder `core` pack are tiny; the mandatory Whistle
; pack has a strict <25 MB cap and must match its required, URL-free index entry.
; No other model packs are embedded; the remaining large packs come from the
; kit's station directory or per-SHA cache. The station index/core resources
; and their two-source resolution order were added 2026-09-02 (owner decision);
; the Whistle resource closes setup-only activation for Beta 10 caches.
;
; WP2 hook-migration (2026-07-30): POSTINSTALL below now carries the D2/D4
; install-time chain that previously sat unreachable in the retired
; `nsis-hooks-native.nsh` (that file's own header, and two independent same-
; day evidence trails -- `wp2-pack-delivery-reconciliation-2026-07-29.md` and
; `wp2-hook-build-wiring-2026-07-30.md` -- had disclosed it was fully built
; and unit-tested but wired into NO live build target). The coordinator
; decided this file, not `nsis-hooks-native.nsh`, is where that chain belongs
; (folding it in here rather than re-pointing the Tauri config, which two
; policy/builder tests pin against). Full migration map, adaptation
; rationale, and what was deliberately NOT migrated (and why):
; .agent-runs/native-windows/ws5-installer/evidence/wp2-hook-migration-2026-07-30.md
;
; Ordering summary (bootstrap-native: packs deliver runtime bytes, so
; verification, signed station activation, the D3 engine, and provisioning
; all run AFTER pack staging,
; not before, unlike the retired file's embedded-payload ordering):
;   stage required component packs -> D2 re-verify the extracted pack tree
;   -> activate the signed station contract -> D3 journaled install/upgrade
;   engine -> D4 PostgreSQL provisioning
;   -> D4 service registration + firewall rule. Every step fails loud on
;   failure via CIVICCAST_FAIL: a silent-safe operator report, a
;   step-identifying process exit code, and a real NSIS Abort so the wizard
;   reports failure instead of running Tauri's unconditional success page
;   (AUDIT-001).
;
; WP2 app-payload-pack gap closure (2026-07-30): the gap named below is now
; CLOSED. `scripts/build_native_app_payload_pack.py` packages the WP-6
; application-payload TREE (CPython 3.12 embeddable + civiccast + hash-pinned
; deps) as a signed `native-app-payload` component pack;
; `native_pack_staging::DEFAULT_REQUIRED_COMPONENTS` now demands it
; (`["native-server-binaries", "native-app-payload"]`), and
; `native_pack_staging::ensure_pack_extracted` special-cases its extraction
; destination to `$INSTDIR\runtime` (NOT the generic
; `packs\native-app-payload\payload\` convention every other component pack
; uses) so `$INSTDIR\runtime\python.exe` -- the interpreter both the D4
; provisioning and D4 service-registration CLI subcommands below shell out to
; internally (`native_service_registration.rs`) -- now exists after pack
; staging completes. See `wp2-app-payload-pack-2026-07-30.md` for the full
; layout-bridge rationale and build/verification evidence.
;
; WP2 D3 rehoming (2026-07-30): the app-payload-pack gap closure above is what
; made the D3 install/upgrade engine (`civiccast.native.upgrade`) invocable at
; all -- `wp2-hook-migration-2026-07-30.md` §2 explicitly left it unwired
; because `$INSTDIR\runtime\python.exe` did not exist yet. It is now wired,
; as a THIN SHELL exactly like the retired block's own invocation (direct
; `nsExec::ExecToLog` of the embedded interpreter, no Rust CLI subcommand
; wrapper, unlike the D4 steps beside it): reads the currently-installed ARP
; version, branches on the retired block's own 5-way exit-code contract
; (commit / clean rollback / halted rollback needing operator recovery /
; refused non-restorable migration / unexpected fault), and passes
; `--payload-source "$INSTDIR\runtime"`. POSITION (coordinator decision): runs
; after D2 pack-tree verification and signed station activation above, and
; BEFORE D4 provisioning/service registration below -- tree management (junction flip,
; migration, health gate, rollback) must commit on the tree before
; provisioning/service-build acts on it. See
; `wp2-d3-rehoming-2026-07-30.md` for the retired-block diff comparison, the
; exit-contract table, and the one real adaptation this position forces (the
; `--database-url` value D3 receives is now the registry's CURRENT value,
; read BEFORE D4 provisioning below can write a fresh one -- empty on a
; first-ever install, unlike the retired ordering where D4 always ran first).

; Durable install-progress breadcrumbs. DetailPrint output is invisible in
; silent installs and lost when the process dies, which made the run-3/run-4
; Sandbox hang (setup alive, no children, no observable position in the
; chain) undiagnosable from outside. Every chain step below writes a line to
; $COMMONPROGRAMDATA\CivicCast\install-progress.log BEFORE and AFTER it
; runs, so the last line always names the step in flight. This file is also
; what the fault dialogs' "see the installer log" now concretely means for
; operators. Uses $9 (saved/restored) to avoid disturbing chain registers.
; Timestamped (P3 instrumentation, 2026-07-31 run-18 forensic diagnosis): a
; breadcrumb line alone proves ORDER, not TIMING -- diagnosing a hang (this
; file's own header names the run-3/run-4 Sandbox hang this log format was
; built to make diagnosable) needs to know how long the install sat on its
; LAST logged step, which the untimestamped format could not answer without
; correlating against a process-exit timestamp from outside the file.
; ${__DATE__}/${__TIME__} are COMPILE-time constants baked into this
; installer once at build time -- every line would carry the SAME wrong
; value -- so they are explicitly not usable here. ${GetTime} (FileFunc.nsh;
; already !include'd by installer.nsi at the top of the generated script,
; strictly before this file's own !include site, and again strictly before
; any !insertmacro CIVICCAST_STEP call site, so it is always in scope) is
; the least invasive runtime option available: it is NSIS's own official
; time helper, built on the standard LOGICLIB "artificial function" calling
; convention already used elsewhere in this toolchain, and it does not
; disturb $0/$1 externally by design (that convention's own internal
; Exch-based save/restore is the entire point of it) -- unlike a raw
; `System::Call kernel32::GetLocalTime` probe, which would need hand-built
; SYSTEMTIME pointer/struct arithmetic for no accuracy benefit at this log's
; one-second granularity.
;
; Every existing CIVICCAST_STEP call site inherits this prefix automatically
; (this is the ONE place the format is produced). The prefix is a PREFIX,
; not a replacement -- every previously-logged token (`step d4-provision:
; returned 0`, `REFUSED`, `step d3-engine: begin (old=...)`, etc.) still
; appears verbatim later in the same line. Every known consumer matches with
; an unanchored substring/regex search (grep confirms:
; C:\CivicCastProof\sandbox-shared\gauntlet-run18\install_gauntlet.ps1 uses
; PowerShell `-match` with no `^`/`$` anchor at every one of its six
; breadcrumb-matching call sites -- 'REFUSED', 'step d4-provision: returned
; 0', and the regex-escaped 'step d3-engine: begin \(old=...\)' among them --
; and a repo-wide search found no OTHER consumer of install-progress.log at
; all), so a leading `[timestamp] ` prefix cannot break any of them. See
; tests/policy/test_native_installer_identity.py for the pin covering the
; prefix shape and the BEGIN line above.
;
; Registers: $2-$8 hold ${GetTime}'s seven required outputs (day, month,
; year, day-of-week [captured but unused -- this log format has never
; carried a weekday and adding one is not this fix's job], hour, minute,
; second). Chosen because those seven are used NOWHERE ELSE in this file
; (confirmed by grep) -- unlike $0, $1, $9, and $R0-$R3, which ARE live
; inside various CIVICCAST_STEP callers' own ${TEXT} argument (e.g.
; "...returned $0", "...(old=$R0)", "...$CIVICCAST_TEARDOWN_EXIT") and must
; still hold their CALLER's value, unmodified, by the time this macro's own
; FileWrite resolves that same ${TEXT} text. Still Push/Pop-saved so the
; CALLER's $2-$8 are restored after the macro -- but the Push/Pop does NOT
; make $2-$9 usable INSIDE a ${TEXT} argument: ${TEXT} expands textually at
; the FileWrite, which runs AFTER ${GetTime} has clobbered $2-$8 and after
; the file handle landed in $2 (measured 2026-07-31: a probe site logging
; "$2...$9" printed the timestamp fields and the handle, not the caller's
; values). Never reference $2-$9 in breadcrumb text.
;
; SIDE EFFECT (measured 2026-07-31): ${GetTime} routes through FileFunc's
; GetTime_, which calls ClearErrors unconditionally -- so EVERY
; CIVICCAST_STEP call clears the NSIS error flag. All current call sites
; were traced and none read the flag across a STEP, but do not place a
; STEP between a SetErrors/${GetOptions} and its ${If} ${Errors} check.
;
; ===========================================================================
; HONEST INSTALL-SIZE METADATA (F-10/F-12, 2026-08-01, FINDINGS-rewalk-
; dd7f835f.md): the wizard's "Choose Install Location" page showed "Space
; required: 237.0 MB", and Windows' Installed apps showed 37.1 MB, against a
; real ~1.19 GB on-disk install (plus up to ~12.8 GB of first-run
; downloads). Both numbers came from NSIS/Tauri machinery that can only see
; what THIS installer embeds via `File` statements (the bootstrap exe, the
; VC++ redistributable, the WebView2 offline installer) -- it has no way to
; know about the native component packs the POSTINSTALL chain below stages
; via external `nsExec` calls, which is most of the real footprint. Two
; separate, complementary fixes, because the two numbers are needed at two
; different times:
;
; 1. The pre-install "Space required" estimate (MUI_PAGE_DIRECTORY, shown
;    BEFORE anything is staged, so it cannot be measured -- it can only be
;    declared). NSIS's documented mechanism for exactly this case is
;    `AddSize` ("increases... the size used to calculate the estimated
;    space required... for actions the installer performs that increase
;    install size beyond what NSIS can auto-detect", e.g. downloads/
;    extractions done by an external process): see
;    CIVICCAST_ADDSIZE_PACKS_KB below and its use in NSIS_HOOK_PREINSTALL.
;    DERIVATION: 6,600,000 KiB covers RC1's measured 6,145,182,639 bytes
;    (run 40970229720 attempt 2), with about 10% headroom, for all four
;    raw sidecars plus their extracted trees. The candidate workflow sums the
;    actual `pack_bytes` + `payload_bytes` from every signed builder report and
;    fails before packaging if that exact build exceeds this declaration, so
;    source or manifest growth cannot silently make the wizard understate the
;    required space. The post-install EstimatedSize remains measured from the
;    real tree below.
; 2. The ARP "Installed apps" size (EstimatedSize, read by Windows AFTER
;    install completes) does not have this before/after problem -- by the
;    time NSIS_HOOK_POSTINSTALL reaches its final steps, every pack has
;    actually been staged and D2-verified, so the true footprint can be
;    MEASURED instead of estimated. See the ${GetSize} "$INSTDIR" call near
;    the end of NSIS_HOOK_POSTINSTALL, which overwrites Tauri's own (tiny,
;    File-statement-only) EstimatedSize write with the real number --
;    self-correcting on every future build, unlike a hardcoded constant.
; ===========================================================================
!define CIVICCAST_ADDSIZE_PACKS_KB "6600000"

; MUI_DIRECTORYPAGE_TEXT_TOP is MUI2's documented mechanism for adding
; explanatory prose above the directory browse box (must be !define'd
; before `!insertmacro MUI_PAGE_DIRECTORY` runs in the generated
; installer.nsi; this whole file is !include'd near the top of that script,
; strictly before that macro, so a file-scope !define here always lands in
; time). Used here, rather than a custom nsDialogs page, to state plainly
; on this SAME screen that more will download later and where its real size
; will be shown -- so "Space required" is not read as the whole story.
!define MUI_DIRECTORYPAGE_TEXT_TOP "CivicCast (Native) needs the space shown below on this drive for the program itself. After Setup finishes, the CivicCast setup wizard downloads additional components (captions and AI models) separately -- their sizes are shown individually, before anything downloads, on that wizard's own screen."

!macro CIVICCAST_STEP TEXT
  Push $9
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  ${GetTime} "" "L" $2 $3 $4 $5 $6 $7 $8
  ; $2=day $3=month $4=year $5=day-of-week (unused) $6=hour $7=minute $8=second
  StrCpy $9 "[$4-$3-$2 $6:$7:$8] "
  ; F-14 (carried, rewalk-de3aaf6f): this CreateDirectory used to run
  ; unconditionally on EVERY breadcrumb, including the last two calls in
  ; NSIS_HOOK_POSTUNINSTALL -- so a completed uninstall's final two visible
  ; detail-pane lines were "Create folder: C:\ProgramData\CivicCast" (NSIS
  ; logs that line for CreateDirectory whether or not the directory already
  ; existed), reading as the uninstaller re-creating the very data root it
  ; just finished tearing down. $COMMONPROGRAMDATA\CivicCast is preserved for
  ; the product's entire lifetime after first install (NSIS_HOOK_POSTUNINSTALL's
  ; own header: "deliberately NEVER touched by this removal, or by anything
  ; else in this macro"), so by the time any uninstall breadcrumb fires, this
  ; CreateDirectory is always redundant -- it exists ONLY to cover the very
  ; first breadcrumb of a fresh install, before the directory has ever been
  ; made. Guard it so it is a genuine no-op (and logs nothing) once the
  ; directory is already there, same FileExists-guard pattern this file
  ; already uses for the uninstall.exe/$INSTDIR reboot-delete guard above.
  ${IfNot} ${FileExists} "$COMMONPROGRAMDATA\CivicCast"
    CreateDirectory "$COMMONPROGRAMDATA\CivicCast"
  ${EndIf}
  FileOpen $2 "$COMMONPROGRAMDATA\CivicCast\install-progress.log" a
  ${If} $2 != ""
    FileSeek $2 0 END
    FileWrite $2 "$9${TEXT}$\r$\n"
    FileClose $2
  ${EndIf}
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $9
!macroend

; Silent-safe failure reporting. An NSIS MessageBox BLOCKS even under `/S`,
; waiting for a click no unattended install can give -- the installer stays
; alive with no children and no visible progress, which is exactly the
; "hang" that cost Sandbox matrix runs 3-6 (the D4 service-registration
; failure path). A full audit of this file found 21 MessageBox sites and
; ZERO silent guards. Every non-interactive failure report now routes
; through this macro: silent installs get the breadcrumb + DetailPrint and
; keep unwinding (the caller still SetErrors and Goto ...done), interactive
; installs still get the operator dialog. The ONE deliberate exception is
; the MB_YESNO ActiveRuntime ownership-transfer prompt in PREUNINSTALL:
; that asks a question silent mode cannot answer, so it keeps its dialog
; and remains an owner-facing policy question (a silent uninstall of the
; active runtime while the WSL product is present must fail loud rather
; than guess an answer). It carries /SD IDNO: without it NSIS still shows
; the dialog in silent mode and `uninstall.exe /S` blocks forever on a
; window nobody sees (RC4 host test, 2026-11-04). Declining is the
; fail-loud answer: the decline branch aborts with the uninstall-declined
; exit code and an ALERT, and nothing is removed or transferred.
!macro CIVICCAST_ALERT TEXT
  !insertmacro CIVICCAST_STEP "ALERT: ${TEXT}"
  DetailPrint "${TEXT}"
  ${IfNot} ${Silent}
    MessageBox MB_OK|MB_ICONSTOP "${TEXT}"
  ${EndIf}
!macroend

; Silent-safe OPERATOR NOTICE (CRITICAL fix, 2026-07-30 adversarial review) --
; structurally identical delivery to CIVICCAST_ALERT (breadcrumb + DetailPrint
; always; a dialog only when NOT silent) but a DISTINCT macro on purpose, for
; a DISTINCT situation: an operator-important outcome on a path that
; CONTINUES BY DESIGN and is NOT a failure (today's only caller is the D3
; clean-rollback branch in NSIS_HOOK_POSTINSTALL -- the D3 engine's own
; contract leaves the machine healthy and running on the previous version, so
; the install did not fail; what the operator needs to know is that the
; upgrade specifically did not take effect).
;
; This macro must NEVER be used to report an actual failure. It deliberately
; carries no SetErrorLevel and no Abort (see
; test_bootstrap_postinstall_every_failure_branch_actually_fails_the_install
; in tests/policy/test_native_installer_identity.py, which asserts this
; macro's body contains neither), so it is structurally incapable of
; reproducing the AUDIT-001 shape -- a report that lets a real failure keep
; going. That test also forbids POSTINSTALL from ever using a BARE
; CIVICCAST_ALERT for exactly that reason: every failure there must route
; through CIVICCAST_FAIL (which aborts); every non-failure notice routes
; through this macro instead. Use MB_ICONINFORMATION (not MB_ICONSTOP) so an
; interactive operator can tell at a glance this is not an error dialog.
!macro CIVICCAST_NOTICE TEXT
  !insertmacro CIVICCAST_STEP "NOTICE: ${TEXT}"
  DetailPrint "${TEXT}"
  ${IfNot} ${Silent}
    MessageBox MB_OK|MB_ICONINFORMATION "${TEXT}"
  ${EndIf}
!macroend

; Fail the install for real (AUDIT-001, Blocker, 2026-07-30).
;
; Before this macro, every POSTINSTALL failure branch below did SetErrors +
; alert + `Goto ...done` and nothing else. NSIS's `.onInstFailed` is documented
; to fire only on a failed extraction or an explicit `Abort` -- `SetErrors` is
; NOT a trigger -- and Tauri's generated installer.nsi defines no
; `.onInstFailed` at all while running `.onInstSuccess` unconditionally. So a
; machine whose provisioning, service registration, or pack verification had
; just failed still showed the wizard's "installation complete" page, still
; carried a healthy Add/Remove Programs entry, and still returned exit code 0
; to any deployment tool driving it silently.
;
; The shape below is not inferred from documentation; it was measured on this
; product's own makensis. Evidence:
; .agent-runs/native-windows/ws5-installer/evidence/nsis-errorlevel-probe/
;   SetErrorLevel alone   -> custom exit code, but .onInstSuccess STILL runs
;   Abort alone           -> .onInstFailed runs, but the generic exit code 2
;   SetErrorLevel + Abort -> custom exit code AND .onInstFailed  <- what we want
;
; CODE is this product's own step-identifying exit code (the table below), so
; an unattended caller can tell WHICH step failed from the process exit code
; alone -- the only signal a silent install emits.
;
; What this deliberately does NOT do: retract Tauri's Add/Remove Programs
; entry or delete the uninstaller it wrote at installer.nsi:649/670-689,
; before this hook ever runs. Both were considered and rejected on evidence:
;   * UninstallString is the ONLY way Windows' own Apps & Features UI can
;     invoke our uninstaller, and the uninstaller is now the thing that
;     actually cleans a half-installed machine (service, firewall rule,
;     recursive tree removal). Deleting the ARP entry would strand a
;     multi-gigabyte tree with no discoverable way to remove it.
;   * scripts/wait_native_uninstall_cleanup.ps1 decides an uninstall finished
;     by matching ARP DisplayName == "CivicCast (Native)" and finding none.
;     Deleting or renaming the entry on a FAILED install would make that
;     watcher report a clean machine that is not clean.
; So a failed install stays visible and removable; what changes is that it no
; longer claims to have succeeded.
; <installer-path-audit BL-02> Set to "1" by NSIS_HOOK_POSTINSTALL's first
; step. From that instant on, $INSTDIR holds the NEW payload (Tauri's own
; generated section replaced the files before this hook runs, and pack
; staging writes the new runtime into $INSTDIR\runtime a few steps later),
; while the database is still the OLD, unmigrated one until D3 commits.
; CIVICCAST_FAIL consults this to decide whether a failure needs service
; containment. Deliberately NOT set during PREINSTALL: a PREINSTALL failure
; aborts before any file is replaced, so the existing install is intact and
; its auto-start service is exactly what the operator should keep.
Var CIVICCAST_PAYLOAD_REPLACED

; <review of PR #145> "1" only when failure containment actually SUCCEEDED --
; both the service stop and the `sc config start= demand` reported 0.
;
; Containment is best-effort by design (see CIVICCAST_FAIL), so any
; operator-facing sentence that states it as fact -- "the service has been
; stopped AND set to manual start" -- is a claim the code does not check.
; That is the same shape as the exit-124 text this batch was fixing in the
; first place: true at the instant it is printed only if nothing went wrong,
; and silent about the case where something did.
Var CIVICCAST_CONTAINED

Var CIVICCAST_TRANSACTION_ARMED
Var CIVICCAST_TRANSACTION_RESULT
!define CIVICCAST_TRANSACTION_SCRIPT "${__FILEDIR__}\install-transaction.ps1"
!define CIVICCAST_EXIT_INSTALL_RECOVERY 136

!macro CIVICCAST_TRANSACTION_CALL ACTION
  Push $R8
  Push $R9
  Push $8
  System::Call 'kernel32::GetCurrentProcessId() i .r8'
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\civiccast-install-transaction.ps1" -Action ${ACTION} -InstallRoot "$INSTDIR" -UninstallKey "${UNINSTKEY}" -InstallerPid $8 -StartMenuFolder "$AppStartMenuFolder"'
  Pop $R9
  Pop $R8
  StrCpy $CIVICCAST_TRANSACTION_RESULT $R9
  !insertmacro CIVICCAST_STEP "installer recovery ${ACTION}: exit=$R9 $R8"
  Pop $8
  Pop $R9
  Pop $R8
!macroend

!macro CIVICCAST_TRANSACTION_RECOVER_PENDING
  InitPluginsDir
  File "/oname=$PLUGINSDIR\civiccast-install-transaction.ps1" "${CIVICCAST_TRANSACTION_SCRIPT}"
  !insertmacro CIVICCAST_TRANSACTION_CALL Recover
  ${If} $CIVICCAST_TRANSACTION_RESULT != 0
    !insertmacro CIVICCAST_ALERT "A previous interrupted CivicCast installation could not be restored. Setup stopped. Keep station data and recovery records; see $COMMONPROGRAMDATA\CivicCast\install-progress.log."
    SetErrorLevel ${CIVICCAST_EXIT_INSTALL_RECOVERY}
    Quit
  ${EndIf}
!macroend

!macro CIVICCAST_TRANSACTION_BEGIN
  InitPluginsDir
  File "/oname=$PLUGINSDIR\civiccast-install-transaction.ps1" "${CIVICCAST_TRANSACTION_SCRIPT}"
  !insertmacro CIVICCAST_TRANSACTION_CALL Begin
  ${If} $CIVICCAST_TRANSACTION_RESULT != 0
    !insertmacro CIVICCAST_ALERT "Setup could not prepare automatic recovery. No application files were replaced. See $COMMONPROGRAMDATA\CivicCast\install-progress.log."
    SetErrorLevel ${CIVICCAST_EXIT_INSTALL_RECOVERY}
    Abort
  ${EndIf}
  StrCpy $CIVICCAST_TRANSACTION_ARMED "1"
  ; Every child, including service registration, inherits demand startup until
  ; the final commit. A reboot must run recovery before candidate auto-start.
  System::Call 'kernel32::SetEnvironmentVariableW(w "CIVICCAST_INSTALL_TRANSACTION", w "1")'
!macroend

!macro CIVICCAST_TRANSACTION_SNAPSHOT
  !insertmacro CIVICCAST_STEP "preinstall: saving recovery snapshot (size check and copy; application files not replaced yet)"
  !insertmacro CIVICCAST_TRANSACTION_CALL Snapshot
  ${If} $CIVICCAST_TRANSACTION_RESULT != 0
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_INSTALL_RECOVERY} "Setup could not save the prior station for automatic recovery. See $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${EndIf}
  !insertmacro CIVICCAST_STEP "preinstall: recovery snapshot saved"
!macroend

!macro CIVICCAST_TRANSACTION_RESTORE
  ${If} $CIVICCAST_TRANSACTION_ARMED == "1"
    !insertmacro CIVICCAST_TRANSACTION_CALL Restore
    ${If} $CIVICCAST_TRANSACTION_RESULT == 0
      StrCpy $CIVICCAST_TRANSACTION_ARMED ""
    ${EndIf}
  ${EndIf}
!macroend

!macro CIVICCAST_TRANSACTION_COMMIT FAILURE_CODE
  !insertmacro CIVICCAST_TRANSACTION_CALL Commit
  ${If} $CIVICCAST_TRANSACTION_RESULT != 0
    !insertmacro CIVICCAST_FAIL ${FAILURE_CODE} "Setup could not finalize automatic recovery. Recovery records have been retained. See $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${EndIf}
  StrCpy $CIVICCAST_TRANSACTION_ARMED ""
  System::Call 'kernel32::SetEnvironmentVariableW(w "CIVICCAST_INSTALL_TRANSACTION", p 0)'
!macroend

; The builder expands these after Tauri has declared UNINSTKEY and its page
; variables, and enables the MUI abort callback only in that final template.
; Tauri's initial packaging pass must retain its default MUI abort behavior.
!macro CIVICCAST_TRANSACTION_CALLBACKS
Function CivicCastTransactionAbort
  !insertmacro CIVICCAST_TRANSACTION_RESTORE
FunctionEnd

Function .onInstFailed
  !insertmacro CIVICCAST_TRANSACTION_RESTORE
FunctionEnd
!macroend

!macro CIVICCAST_FAIL CODE TEXT
  SetErrors
  ; <installer-path-audit BL-02> CONTAIN THE SERVICE BEFORE ANNOUNCING THE
  ; FAILURE.
  ;
  ; The upgrade sequence is: PREINSTALL stops the service while deliberately
  ; PRESERVING its registration -> Tauri's generated section replaces
  ; $INSTDIR -> POSTINSTALL stages the packs, writing the NEW payload into
  ; $INSTDIR\runtime -> and only THEN does D3 run. So by the time any
  ; POSTINSTALL failure branch fires -- 110, 111, 112, 113, 114, 115, 116,
  ; 117, 118, 119, 121, 122, 123, 124, 125, 126, 127 -- the machine holds new
  ; code at $INSTDIR\runtime, the old unmigrated database, and
  ; CivicCastSupervisor still registered with `--startup auto`, merely
  ; stopped. This macro used to do SetErrors + alert + SetErrorLevel + Abort
  ; and nothing else, so the operator read "The service has NOT been started
  ; on the new files" -- true at that instant -- and then rebooted, at which
  ; point the SCM auto-started the supervisor on the new payload against the
  ; old schema. That is precisely the 500s state Gate A run 33681670855
  ; produced and PR #143 was written to prevent; the fix moved it from
  ; "immediately" to "next boot".
  ;
  ; Containment is best-effort by design: a containment step that could
  ; itself abort would replace an honest, specific failure message with a
  ; different one. If the product stop command fails, retry through SCM and
  ; confirm the service reaches STOPPED within a bounded interval. Each
  ; action records a breadcrumb so the log says whether containment took. A
  ; successful re-run of setup
  ; re-registers `auto` on its own (register_native_service always sets
  ; SERVICE_STARTUP_MODE), so this is not a state an operator has to undo.
  StrCpy $CIVICCAST_CONTAINED "0"
  ${If} $CIVICCAST_PAYLOAD_REPLACED == "1"
    !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT begin (new payload is on disk; the service must not auto-start onto it)"
    nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" --civiccast-stop-native-service'
    Pop $R9
    !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT service stop returned $R9"
    StrCpy $R8 $R9
    ${If} $R8 != "0"
      ; The product command normally confirms STOPPED itself. If it cannot
      ; (for example, the service keeps restarting against an old station
      ; manifest),
      ; stop through SCM directly. An accepted control is not itself proof of
      ; STOPPED, so poll the service state for at most 15 seconds.
      !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT direct SCM stop fallback begin (product stop returned $R8)"
      nsExec::ExecToLog '"$SYSDIR\sc.exe" stop CivicCastSupervisor'
      Pop $R9
      !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT direct SCM stop returned $R9"
      ${If} $R9 == "1062"
        StrCpy $R8 "0"
        !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT direct SCM stop confirms already stopped (1062)"
      ${ElseIf} $R9 == "1060"
        StrCpy $R8 "0"
        !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT direct SCM stop confirms service absent (1060)"
      ${ElseIf} $R9 == "0"
      ${OrIf} $R9 == "1061"
        nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "try { $$deadline = [DateTime]::UtcNow.AddSeconds(15); do { $$service = Get-Service -Name CivicCastSupervisor -ErrorAction Stop; $$service.Refresh(); if ($$service.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Stopped) { exit 0 }; Start-Sleep -Milliseconds 500 } while ([DateTime]::UtcNow -lt $$deadline); exit 1 } catch { exit 2 }"'
        Pop $R8
        !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT direct SCM STOPPED confirmation returned $R8"
      ${EndIf}
    ${EndIf}
    nsExec::ExecToLog '"$SYSDIR\sc.exe" config CivicCastSupervisor start= demand'
    Pop $R9
    !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT sc config start=demand returned $R9"
    ; Review of PR #145: containment is BEST-EFFORT by design (a step that
    ; could itself abort would replace an honest, specific failure message
    ; with a different one) -- so no operator-facing text may state it as
    ; fact. "1" here means BOTH the stop and the start-type change actually
    ; reported success; the exit-124 branch reads this Var and says one thing
    ; or the other, never the optimistic one unconditionally.
    ;
    ; `sc config` on an absent service (1060, ERROR_SERVICE_DOES_NOT_EXIST)
    ; IS confirmed containment: a FRESH_INSTALL failure has no registered
    ; service, so there is nothing that could auto-start onto the new payload
    ; at the next boot. (beta.5.1: this used to be logged as "NOT confirmed",
    ; which sent the operator to disarm a service that does not exist; 1060
    ; is the ONE exit code that definitively means absent -- the same rule
    ; the PREINSTALL SCM classification applies.)
    ${If} $R8 == "0"
    ${AndIf} $R9 == "0"
      StrCpy $CIVICCAST_CONTAINED "1"
      !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT confirmed (service stopped and set to manual start)"
    ${ElseIf} $R8 == "0"
    ${AndIf} $R9 == "1060"
      StrCpy $CIVICCAST_CONTAINED "1"
      !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT confirmed (no CivicCastSupervisor service is registered -- sc config returned 1060 -- so nothing can auto-start onto the new payload)"
    ${Else}
      !insertmacro CIVICCAST_STEP "postinstall: FAILURE CONTAINMENT NOT confirmed (stop=$R8 config=$R9) -- the service may still auto-start onto the new payload"
    ${EndIf}
  ${EndIf}
  !insertmacro CIVICCAST_STEP "failure reason: ${TEXT}"
  !insertmacro CIVICCAST_TRANSACTION_RESTORE
  ${If} $CIVICCAST_TRANSACTION_RESULT == 0
    !insertmacro CIVICCAST_ALERT "CivicCast setup failed (exit ${CODE}). The previous installation state was automatically restored. A station that was running before setup has been restarted and checked. See $COMMONPROGRAMDATA\CivicCast\install-progress.log for the original failure."
  ${Else}
    !insertmacro CIVICCAST_ALERT "${TEXT}"
    !insertmacro CIVICCAST_ALERT "Automatic restoration could not finish. Recovery records were retained in $COMMONPROGRAMDATA\CivicCast\install-transaction. Do not delete station data. See install-progress.log and contact support if the automatic restart recovery cannot finish."
  ${EndIf}
  !insertmacro CIVICCAST_STEP "postinstall: FAILED, aborting with exit code ${CODE}"
  SetErrorLevel ${CODE}
  Abort
!macroend

; POSTINSTALL failure exit codes. Deliberately in a band of their own: the
; product's own CLI contract codes and the D3 engine's phase codes travel
; through nsExec as $0 and must stay distinguishable from the installer
; process's own exit code.
;
; Corrected by the installer-path audit (MA-28). This comment used to say the
; CLI contract codes were "(40, 70, 73, 75, 76, 79, 81)". That set was wrong
; in both directions: it omitted thirteen live codes, and 40 is a D3 ENGINE
; phase code, not a CLI code. What is actually true, read off main.rs and its
; modules:
;   * D3 engine (the upgrade CLI this hook invokes below) phase codes:
;     0 COMPLETE, 10 ROLLED_BACK, 11 FRESH_INSTALL, 12 SAME_VERSION_NO_OP,
;     20 HALTED_RESTORE_FAILED, 30 REFUSED_NON_RESTORABLE, 40 unexpected fault.
;   * `CivicCast Native.exe` CLI codes: 0, 1, and the 64-85 band --
;     64 arguments, 65 render/serde, 66 acquisition, 67 activation/self-test,
;     68 install-tree verify, 69, 70 service registration, 71, 72 database-url
;     write, 73 uninstall preflight, 74 ActiveRuntime transfer acknowledgement
;     required AND required-pack staging failure, 75 provisioning failure AND
;     optional-pack staging failure, 76 repair-needed, 77 uninstall blocked,
;     78 embedded pack trust, 79 unrepairable, 80, 81, 82 teardown
;     service-stop unconfirmed, 83 service registered but would not start,
;     84 service running but the control plane is not serving (BL-11),
;     85 runtime ownership unprovable (BL-13: the selector is unreadable, or
;        absent while some ownership read failed for a reason other than
;        "not found"),
;     86 unknown --civiccast-* flag (MA-22),
;     87 another CivicCast product is registered on this machine (round 3 of
;        the beta.5 ownership defect: a real ARP entry, remedy = uninstall it
;        or cutover from it, never a registry edit).
; 74 and 75 each still carry two meanings; there is no live collision because
; each caller branches within one subcommand, but the duplication is recorded
; here rather than left for the next person to rediscover. 83/84/85 were
; picked from the first genuinely free numbers above that band; 86/87 follow.
!define CIVICCAST_EXIT_PACK_DELIVERY        110
!define CIVICCAST_EXIT_D2_SERVER_BINARIES   111
!define CIVICCAST_EXIT_D2_APP_PAYLOAD       112
!define CIVICCAST_EXIT_D3_HALTED            113
!define CIVICCAST_EXIT_D3_REFUSED           114
!define CIVICCAST_EXIT_D3_FAULT             115
!define CIVICCAST_EXIT_D4_PROVISION_FAILED  116
!define CIVICCAST_EXIT_D4_PROVISION_FAULT   117
!define CIVICCAST_EXIT_D4_SERVICE           118
!define CIVICCAST_EXIT_D4_FIREWALL          119

; UPGRADE QUIESCE: the sole code in this band NOT raised from POSTINSTALL.
; It fires from NSIS_HOOK_PREINSTALL before any destructive step when an
; existing-install upgrade could not prove the old native service was fully
; stopped before replacement work. Kept at the former refusal
; code so deployment tooling still sees the same distinct failure band, but
; a healthy existing install no longer triggers it.
!define CIVICCAST_EXIT_UPGRADE_QUIESCE       120

; D2 re-verification of the third required component pack,
; native-ffmpeg-runtime. Given its own code rather than folded into
; ${CIVICCAST_EXIT_D2_SERVER_BINARIES}/${CIVICCAST_EXIT_D2_APP_PAYLOAD} for
; the same reason those two are distinct: the exit code is the only signal a
; support log carries about WHICH pack failed to re-verify.
!define CIVICCAST_EXIT_D2_FFMPEG_RUNTIME     121
!define CIVICCAST_EXIT_D2_OLLAMA_RUNTIME     122

; K1 fix: flat-layout station activation (--civiccast-activate-station),
; run after D2 pack verification and before D3 starts the candidate runtime
; for its maintenance health gate. It verifies the signed distribution and
; runs app-database-independent self-tests; D3 database work and D4 setup
; remain sequenced below. Its exit code is the support log's failure signal.
!define CIVICCAST_EXIT_D4_ACTIVATION         123

; Gate A run 33681670855 fix (D3 pre-upgrade drill false-negative +
; flat-layout rollback containment): under --flat-installer-layout, D3 engine
; exit 10 (ROLLED_BACK) does NOT mean this machine is healthy on the old
; version. adapt_flat_installer_layout's read_junction/lay_tree/flip_junction
; are all no-ops over the single "$INSTDIR\runtime" tree the bootstrap already
; extracted the NEW payload into BEFORE the engine ever ran -- there is no
; separate app\<version> + junction pair for a rollback to flip back to. So a
; clean ROLLED_BACK here leaves NEW code sitting over a database the engine
; deliberately did NOT migrate. Continuing past this point (as the former
; CIVICCAST_NOTICE branch did) would let D4 register and start the service on
; that mismatched pair. This code is distinct from CIVICCAST_EXIT_D3_HALTED
; (113): 113 means the engine's OWN rollback failed and it halted itself;
; 124 means the engine's rollback succeeded exactly as designed but the flat
; layout it ran under cannot honor what a "rollback" promises, so the
; installer -- not the engine -- must fail closed instead.
!define CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT   124

; Installer-path audit BL-11: the service is RUNNING but the control plane is
; not SERVING. Until this code existed, NOTHING in the entire elevated install
; chain ever contacted /health -- SCM `RUNNING` (i.e. pythonservice.exe told
; the SCM it had started) was the only success signal the installer had, which
; says nothing about PostgreSQL being up, the control plane binding 8000, or
; the schema matching the code. Gate A run 33681670855 is the exact shape:
; service registers, SCM says RUNNING, /health returns 200
; {"status":"degraded","schema":"behind"}, the installer writes
; InstalledVersion, exits 0, and the wizard shows its success page over a box
; serving 500s. Distinct from ${CIVICCAST_EXIT_D4_SERVICE} (118, "could not
; register") and from the service-start failure below because the operator
; remedy differs in each case.
!define CIVICCAST_EXIT_D4_SERVICE_NOT_SERVING 125

; Installer-path audit MA-29: exit 83 from --civiccast-register-native-service
; means the service WAS registered and would not start. Mapping it to 118
; ("could not register the ... Windows service") pointed the operator at
; registration when the fault is startup.
!define CIVICCAST_EXIT_D4_SERVICE_NOT_STARTING 126

; Installer-path audit BL-13: --civiccast-provision could not establish which
; runtime owns this machine (HKLM\SOFTWARE\CivicCast\ActiveRuntime unreadable,
; or absent while the WSL-product probe cannot answer). The Rust side used to
; print one sentence -- which itself said "The native runtime will not start
; until an operator sets it" -- and return Ok, so setup went on to register
; and start a service whose control plane the guard blocks, and reported
; success over a station that can never serve.
!define CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP   127

; Installer-path audit BL-06: the D3 engine refused to start because a PREVIOUS
; run's terminal journal is still on disk. Distinct from every other D3 code
; because THIS run did nothing at all -- no seam ran, nothing was written --
; and the operator action is a file move, not a retry. Before it existed the
; engine returned the stale journal's own phase (usually 10), which the
; exit-10 branch turns into a fatal 124 saying "re-run setup after resolving
; the cause"; every re-run returned 10 again, forever, because nothing deletes
; that journal.
!define CIVICCAST_EXIT_D3_STALE_JOURNAL       128

; Installer-path audit MA-05: an OLDER setup.exe was run over a NEWER
; installed station. Refused by the routing decision before the interlock, the
; drain, the backup or any mutation -- so unlike every other code in this band
; it means "nothing at all happened", which is what its operator text says.
!define CIVICCAST_EXIT_D3_REFUSED_DOWNGRADE   129

; UNINSTALL refusal / incompletion codes (installer-path audit MA-34, MA-35).
;
; Three uninstall refusals -- the operator declined the ownership-transfer
; prompt, an acknowledged transfer failed, and any other nonzero preflight --
; all did SetErrors + Abort with NO SetErrorLevel, so all three returned
; NSIS's own generic script-abort code 2, indistinguishable from each other
; AND from an unrelated script abort. This file's own comment beside the
; teardown refusal explains that exact ambiguity as the reason 82 was given a
; distinct code, and then left these three at 2.
;
; And two POSTUNINSTALL paths did SetErrors + alert and then returned 0,
; because -- per this file's own measured evidence table -- `SetErrors` alone
; does not change the process exit code. An uninstall that deliberately left
; multi-gigabyte trees and a running service behind reported success to
; winget/Intune.
;
; 130-134 continues this file's own band rather than reusing the CLI's, so a
; support log can tell an uninstall refusal from an install failure by the
; number alone.
!define CIVICCAST_EXIT_UNINSTALL_DECLINED        130
!define CIVICCAST_EXIT_UNINSTALL_TRANSFER_FAILED 131
!define CIVICCAST_EXIT_UNINSTALL_BLOCKED         132
!define CIVICCAST_EXIT_UNINSTALL_TREES_RETAINED  133
!define CIVICCAST_EXIT_UNINSTALL_INCOMPLETE      134

; Round 3 of the beta.5 runtime-ownership defect (corrected field report,
; 2026-09-09): --civiccast-provision exit 87 -- the selector is absent and
; ANOTHER CivicCast product is genuinely registered on this machine (the old
; WSL-era "CivicCast Installer" ARP entry with a distro, an autostart entry,
; or no record of a previous native hand-off beside it). Its remedy shares
; nothing with 127's (uninstall that product or run the cutover from it --
; never a registry edit), so it gets its own installer code. An INERT
; leftover (no distro, no autostart, previous native hand-off recorded) is
; claimed over with a WARNING in the log and never reaches this code.
!define CIVICCAST_EXIT_D4_OTHER_PRODUCT          135

; Carries the --civiccast-teardown-native-state CLI's exit code from
; NSIS_HOOK_PREUNINSTALL (where the teardown call must run -- see that
; macro's header comment for why) to NSIS_HOOK_POSTUNINSTALL (where the
; recursive tree-removal gate this result controls must stay -- it can only
; run after Tauri's own file deletion). A file-scope Var, not a numbered
; register ($0/$R0/...), is the correct channel: registers are reused by
; Tauri's own generated uninstall-section code that runs BETWEEN these two
; macros (CheckIfAppIsRunning, the shortcut/registry cleanup, etc.), so they
; are not guaranteed to still hold the teardown's result by the time
; POSTUNINSTALL reads it. Left at its NSIS default (empty string) until
; PREUNINSTALL sets it; POSTUNINSTALL treats an empty value (PREUNINSTALL
; somehow did not run) the same as exit code 82 -- fail CLOSED, refusing to
; delete a tree a still-running service may depend on, rather than assuming
; it is safe.
Var CIVICCAST_TEARDOWN_EXIT

; <installer-path-audit MA-36> "1" once this uninstaller's own preflight
; reported that it ARMED the post-uninstall selector-clear plan (exit 73).
;
; `native_uninstall_preflight` begins with an unconditional
; `clear_postclear_marker()`, and the marker value is a fixed constant with no
; owner in it. So: uninstaller A's preflight arms the marker and returns 73; A
; proceeds into teardown and Tauri's file deletion; the operator opens Apps &
; Features again and B's preflight CLEARS A's marker. A's POSTUNINSTALL then
; reads no marker and takes the "no plan was armed" path, leaving
; HKLM\Software\CivicCast\ActiveRuntime set to "native" on a machine with no
; native product -- silently, with the uninstall still exiting 0.
;
; PREUNINSTALL and POSTUNINSTALL run in the SAME uninstaller process, so this
; Var is the one thing that can tell "no plan was ever armed" (legitimate,
; silent) apart from "this run armed a plan and something removed it"
; (MA-36's state, which must be loud). Left at its NSIS default (empty) so an
; unset value behaves exactly as before.
Var CIVICCAST_POSTCLEAR_ARMED

; The builder inserts this at the FIRST instruction of .onInit. Tauri's
; reinstall pages and WebView2 prerequisite can mutate the machine before
; PREINSTALL, so that hook alone is too late. Only installer scratch files
; are created here; no product/service/data state is changed on refusal.
!define CIVICCAST_PREFLIGHT_SCRIPT "${__FILEDIR__}\version-preflight.ps1"
!macro CIVICCAST_VERSION_PREFLIGHT
  ; Tauri sets OUTDIR to INSTDIR immediately before PREINSTALL and does not
  ; reset it before its File instructions. Extract scratch by absolute name
  ; so this guard never changes the caller's output directory/current path.
  Push $0
  Push $1
  ${If} ${Errors}
    Push "1"
  ${Else}
    Push "0"
  ${EndIf}
  ClearErrors
  InitPluginsDir
  File "/oname=$PLUGINSDIR\civiccast-version-preflight.ps1" "${CIVICCAST_PREFLIGHT_SCRIPT}"
  nsExec::ExecToStack /TIMEOUT=30000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\civiccast-version-preflight.ps1" -CandidateVersion "${VERSION}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "$1"
    ${If} $0 == 129
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONSTOP "A newer CivicCast version is installed. This older setup was refused before stopping the service or replacing application files. Keep using the installed version, or run its matching newer setup to repair it."
      ${EndIf}
      SetErrorLevel 129
    ${Else}
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONSTOP "CivicCast setup could not safely compare the installed version. Setup stopped before changing the installation. Retry from an administrator account or contact support with this message: $1"
      ${EndIf}
      SetErrorLevel 130
    ${EndIf}
    Quit
  ${EndIf}
  Pop $1
  ${If} $1 == "1"
    SetErrors
  ${Else}
    ClearErrors
  ${EndIf}
  Pop $1
  Pop $0
!macroend

; F10 (RC4 field run, 2026-11-04): another program (typically a separately
; installed Ollama) holding 127.0.0.1:11434 leaves the station without its own
; AI engine. The station still installs and runs (supervisor: "degraded AI,
; service healthy"), so this is a WARNING only -- it never refuses or quits.
; Runs after the old service and bootstrap are stopped so an upgrade does not
; flag CivicCast's own Ollama; anything under $INSTDIR also counts as ours.
!define CIVICCAST_AI_PORT_PREFLIGHT_SCRIPT "${__FILEDIR__}\ai-port-preflight.ps1"
!macro CIVICCAST_AI_PORT_PREFLIGHT
  Push $0
  Push $1
  ${If} ${Errors}
    Push "1"
  ${Else}
    Push "0"
  ${EndIf}
  ClearErrors
  InitPluginsDir
  File "/oname=$PLUGINSDIR\civiccast-ai-port-preflight.ps1" "${CIVICCAST_AI_PORT_PREFLIGHT_SCRIPT}"
  nsExec::ExecToStack /TIMEOUT=30000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\civiccast-ai-port-preflight.ps1" -Port 11434 -OwnInstallRoot "$INSTDIR"'
  Pop $0
  Pop $1
  !insertmacro CIVICCAST_STEP "preinstall: AI port check returned $0: $1"
  ${If} $0 == 10
    !insertmacro CIVICCAST_NOTICE "WARNING: another program is already using port 11434, which CivicCast's built-in AI engine needs:$\r$\n$\r$\n$1$\r$\n$\r$\nSetup will continue and the station will run normally, but AI summaries and live translation stay off until that program is stopped or moved to another port. After that, restart the CivicCast Native Supervisor service."
  ${EndIf}
  Pop $1
  ${If} $1 == "1"
    SetErrors
  ${Else}
    ClearErrors
  ${EndIf}
  Pop $1
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro CIVICCAST_VERSION_PREFLIGHT
  !insertmacro CIVICCAST_TRANSACTION_BEGIN
  ; F-10 fix: declare the space the POSTINSTALL pack-staging chain below adds
  ; beyond what NSIS's own File statements already account for. AddSize is a
  ; compile-time directive (summed into this Section's total regardless of
  ; where it appears in the section body); see CIVICCAST_ADDSIZE_PACKS_KB's
  ; own definition and derivation near the top of this file.
  AddSize ${CIVICCAST_ADDSIZE_PACKS_KB}
  ; INSTALL-OVER-EXISTING UPGRADE (finalization floor, 2026-08-31). A healthy
  ; live install is now an upgrade INPUT, not a refusal. Before Tauri's
  ; generated section or pack staging replaces any file, run the OLD
  ; bootstrap's production service-stop command. It gracefully stops the
  ; supervisor and its children while deliberately PRESERVING its service
  ; registration plus HKLM InstalledVersion and DatabaseUrl. D3 needs those
  ; exact signals to classify this as an upgrade and run its verified backup,
  ; migration, maintenance health gate, and rollback contract. The uninstall
  ; teardown command is forbidden here because it removes all three signals.
  ;
  ; Fail closed at this boundary: a nonzero service stop means replacement work
  ; cannot prove writers are gone. A service registration with no old
  ; bootstrap is also unsafe because the trusted teardown authority is
  ; missing. In either case CIVICCAST_FAIL aborts before taskkill/tree work.
  ; ProgramData markers never select the branch; they survive by design.
  !insertmacro CIVICCAST_STEP "preinstall: classify existing install for upgrade"
  nsExec::ExecToLog '"$SYSDIR\sc.exe" query CivicCastSupervisor'
  Pop $R5
  ${If} ${FileExists} "$INSTDIR\CivicCast Native.exe"
    DetailPrint "Preparing the existing CivicCast (Native) installation for a data-preserving upgrade..."
    !insertmacro CIVICCAST_STEP "preinstall: existing install found; native service stop begin"
    nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" --civiccast-stop-native-service'
    Pop $0
    !insertmacro CIVICCAST_STEP "preinstall: existing install service stop returned $0"
    ${If} $0 != 0
      !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE} "CivicCast (Native) setup found an existing installation but could not safely stop its native service (service-stop exit $0). Setup stopped before replacing application files. Its service registration, upgrade identity, recordings, database, and settings were not deleted. Retry after resolving the service error; if it persists, contact support with $COMMONPROGRAMDATA\CivicCast\install-progress.log."
    ${EndIf}
    !insertmacro CIVICCAST_STEP "preinstall: existing service confirmed stopped; upgrade identity preserved for D3"
  ${ElseIf} $R5 == 0
    !insertmacro CIVICCAST_STEP "preinstall: unsafe partial install (service registered but old bootstrap is missing)"
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE} "CivicCast (Native) setup found the CivicCastSupervisor service, but the existing CivicCast Native bootstrap is missing from $INSTDIR. Setup cannot safely prepare this partial installation for upgrade and stopped before replacing files. Do not delete $COMMONPROGRAMDATA\CivicCast; it contains the station's recordings, database, and settings. Repair or remove the broken application installation without deleting application data, then retry, or contact support with $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${ElseIf} $R5 == 1060
    !insertmacro CIVICCAST_STEP "preinstall: no existing install found; fresh install"
  ${Else}
    !insertmacro CIVICCAST_STEP "preinstall: SCM classification inconclusive (sc.exe exit $R5)"
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE} "CivicCast (Native) setup could not safely determine whether the CivicCastSupervisor service exists (service-query exit $R5). Only Windows service error 1060 definitively means the service is absent; every other query failure is treated as unsafe so setup cannot replace files beneath possible running writers. Setup stopped before replacing application files. Retry from an administrator account after resolving Windows Service Control Manager access, then contact support with $COMMONPROGRAMDATA\CivicCast\install-progress.log if the error persists."
  ${EndIf}
  ;
  ; The GUI bootstrap is not the Windows service and is outside the native
  ; service-stop above. Stop it only after service quiescence is proven, before the
  ; generated installer replaces the executable. DELTA-M-02 (2026-08-02):
  ; this step used to run
  ; taskkill.exe UNCONDITIONALLY, even on a genuinely fresh install where
  ; "$INSTDIR\CivicCast Native.exe" does not exist yet -- there is no prior
  ; bootstrap process to stop. nsExec::ExecToLog echoes a child's stdout/
  ; stderr straight into the operator-visible details pane, and taskkill's
  ; /IM lookup against a not-yet-existing target, run under this installer's
  ; execution context, surfaced as a raw "ERROR: The user name or password
  ; is incorrect." line (evidence: 016b-error-line-crop.png) immediately
  ; after "Stopping the existing CivicCast Native bootstrap before
  ; installation...", right before the install went on to complete
  ; successfully. Alarming and confusing to a newcomer reading the log --
  ; the install did not fail, but the log reads as though something did.
  ;
  ; Gated the SAME way the sc.exe-based service-stop step immediately above
  ; this one already is: only attempt the stop when a prior install
  ; actually left the exe behind. This is not a suppression of a real
  ; error -- it is skipping an operation this fresh-install case never
  ; needed to attempt in the first place, exactly like the gate above it.
  !insertmacro CIVICCAST_STEP "preinstall: stopping existing bootstrap"
  ${If} ${FileExists} "$INSTDIR\CivicCast Native.exe"
    DetailPrint "Stopping the existing CivicCast Native bootstrap before installation..."
    nsExec::ExecToLog 'taskkill.exe /IM "CivicCast Native.exe" /T /F'
    Sleep 500
  ${Else}
    DetailPrint "No prior CivicCast Native bootstrap process to stop (fresh install)."
  ${EndIf}
  !insertmacro CIVICCAST_AI_PORT_PREFLIGHT
  !insertmacro CIVICCAST_TRANSACTION_SNAPSHOT
  !insertmacro CIVICCAST_STEP "preinstall: done"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; <installer-path-audit BL-02> From here on, $INSTDIR is the NEW payload
  ; (Tauri's generated section already replaced it) while the database is
  ; still the old one until D3 commits, so every failure branch below must
  ; contain the service rather than leave it registered `--startup auto` over
  ; new code. See CIVICCAST_FAIL and this Var's own declaration.
  StrCpy $CIVICCAST_PAYLOAD_REPLACED "1"
  !insertmacro CIVICCAST_STEP "postinstall: begin"
  !insertmacro CIVICCAST_STEP "step vc-redist: begin"
  DetailPrint "Installing the offline Microsoft Visual C++ runtime prerequisite..."
  ExecWait '"$INSTDIR\vc_redist.x64.exe" /install /quiet /norestart' $0
  !insertmacro CIVICCAST_STEP "step vc-redist: returned $0"
  ${If} $0 == 0
    DetailPrint "Microsoft Visual C++ runtime installation completed."
  ${ElseIf} $0 == 3010
    DetailPrint "Microsoft Visual C++ runtime installation completed; a Windows restart is required."
    SetRebootFlag true
  ${ElseIf} $0 == 1638
    ; CRITICAL fix (2026-08-01, real-hardware run R7): 1638 is Microsoft's
    ; ERROR_PRODUCT_VERSION -- "Another version of this product is already
    ; installed. Installation of this version can't continue."
    ; (learn.microsoft.com/en-us/windows/win32/msi/error-codes). From the VC++
    ; redistributable bootstrapper it means a SAME-OR-NEWER runtime is already
    ; on the machine, i.e. the prerequisite this step exists to guarantee is
    ; already SATISFIED. That is the normal state of most real Windows
    ; machines; the pristine Sandbox images every prior proof run used never
    ; produced it, so the catch-all below hard-failed R7's install on a
    ; machine that was fine.
    ;
    ; NOT taken on trust: 1638 is only accepted after the runtime's presence
    ; is CONFIRMED in the registry. HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\
    ; VC\Runtimes\x64 "Installed" (DWORD) is the key the VC++ redistributable
    ; itself writes and the one Microsoft's own detection guidance reads. It
    ; lives in the 64-bit view, which this 32-bit NSIS installer does not see
    ; without SetRegView 64 (same explicit-view discipline the D3 block later
    ; in this macro and NSIS_HOOK_POSTUNINSTALL already follow); the view is
    ; restored immediately afterwards so nothing between here and that block
    ; inherits a changed view. A 1638 the registry does NOT confirm is
    ; genuinely abnormal and keeps the fail-loud path below.
    SetRegView 64
    StrCpy $1 ""
    ReadRegDWORD $1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
    SetRegView default
    ${If} $1 = 1
      !insertmacro CIVICCAST_STEP "step vc-redist: 1638 with the runtime confirmed present in the registry -- prerequisite satisfied"
      DetailPrint "Microsoft Visual C++ runtime already present, same or newer — prerequisite satisfied; setup did not reinstall it."
    ${Else}
      !insertmacro CIVICCAST_STEP "step vc-redist: 1638 but the runtime was NOT found in the registry"
      DetailPrint "Microsoft Visual C++ runtime installation failed with exit code $0, and no installed x64 runtime could be confirmed in the registry."
      !insertmacro CIVICCAST_FAIL $0 "CivicCast (Native) setup could not install the required Microsoft Visual C++ runtime (exit code $0), and could not confirm that a runtime is already installed on this machine. See the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log."
    ${EndIf}
  ${Else}
    DetailPrint "Microsoft Visual C++ runtime installation failed with exit code $0."
    ; Keeps the redist's own exit code (informative: 1602, 1603, ... are
    ; Microsoft's, not ours) while routing through the same fail-loud macro as
    ; every other step, so the operator gets a dialog instead of only a log
    ; line and no branch can drift back to a quiet failure. Every nonzero code
    ; other than the three branched on above still lands here, unchanged.
    !insertmacro CIVICCAST_FAIL $0 "CivicCast (Native) setup could not install the required Microsoft Visual C++ runtime (exit code $0). See the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${EndIf}
  ;
  ; ===================================================================
  ; NATIVE COMPONENT PACK DELIVERY (plan-sub-300mb-bootstrap.md; migrated
  ; from nsis-hooks-native.nsh's NSIS_HOOK_POSTINSTALL, WP2 hook-migration
  ; 2026-07-30 -- see the file header above and the migration evidence)
  ; ===================================================================
  ; The bootstrap executable carries NO station packs (see the file header);
  ; required packs are delivered by SIDE-LOAD ONLY: a verified `.ccpack` in a
  ; `packs\` folder next to THIS installer, at $EXEDIR -- NSIS's built-in
  ; "directory containing the currently running installer .exe".
  ;
  ; There is NO SECOND DELIVERY PATH TODAY. The
  ; native_distribution::acquire_online_distribution machinery is reached (not
  ; forked) but can only produce a typed NOT_AVAILABLE outcome, since no
  ; channel index URL is pinned anywhere in this codebase -- so it cannot
  ; fetch anything, on any machine, however well connected. This comment used
  ; to label that path an online FALLBACK; that label reads as working
  ; behaviour and is what the failure dialog below was written from. Until a
  ; channel index URL is
  ; pinned and proven, no operator-facing text here may offer a network or
  ; download remedy (chain F-min, 2026-08-01: a real tester hit this abort --
  ; child exit 74, hook exit 110 -- and was told to connect the machine to the
  ; network, which could not have helped).
  ;
  ; `--civiccast-stage-packs` verifies every candidate pack's signature + byte
  ; inventory BEFORE copying, re-verifies the landed copy, then extracts each
  ; required payload to its product-owned destination: server binaries under
  ; `$INSTDIR\packs`, the application under `$INSTDIR\runtime`, and FFmpeg /
  ; Ollama under `$INSTDIR\dependencies`. An already-verified extracted tree
  ; is left untouched. An unsatisfied required component is a loud abort
  ; naming the side-load remedy. No `--require-component` flag is passed, so
  ; the effective required set is exactly
  ; `native_pack_staging::DEFAULT_REQUIRED_COMPONENTS`: server binaries,
  ; application payload, FFmpeg runtime, and Ollama runtime.
  ;
  ; CAPTURED, not just streamed (chain F-min2, 2026-08-01; TESTER2
  ; request-0050 verified on real hardware that
  ; $COMMONPROGRAMDATA\CivicCast\install-progress.log carried only the
  ; CIVICCAST_STEP breadcrumbs -- step begin / returned 74 / postinstall
  ; FAILED -- and never named a single missing component, while the dialog
  ; below promised exactly that list would be there).
  ;
  ; The child DOES name them: main.rs's `--civiccast-stage-packs` arm
  ; `eprintln!`s native_pack_staging::build_pack_delivery_abort_message, which
  ; joins every still-missing component into the message. Under
  ; nsExec::ExecToLog that text goes to the wizard detail pane and NOWHERE
  ; else -- the pane is not install-progress.log, and under `/S` there is no
  ; pane at all -- so the promise could not be kept. ExecToStack captures it
  ; into $1 instead, and the failure arm below hands it to CIVICCAST_STEP,
  ; the one macro in this file that writes that log.
  ;
  ; ExecToStack costs nothing that ExecToLog was providing: native_pack_staging.rs
  ; contains ZERO print/println/eprintln calls (grepped), so the child emits
  ; nothing at all until it exits -- there is no progressive output for the
  ; pane to lose while the multi-gigabyte extraction runs. ExecToStack is
  ; already this file's established capture idiom (the uninstall-preflight
  ; calls in NSIS_HOOK_PREUNINSTALL use it the same way, exit code then
  ; output). $1 truncates at ${NSIS_MAX_STRLEN}; on the failure path the
  ; abort message is one short line well inside it.
  ;
  ; On the success path $1 is deliberately NOT the full JSON manifest report
  ; (2026-09-05 delta-review fix): measured against real Gate A evidence,
  ; NSIS_MAX_STRLEN is 1024 bytes, and a manifest carrying `payload_identity`
  ; entries for even a handful of components already exceeds that. Because
  ; `serde_json::json!`'s `Map` is a `BTreeMap` (this crate never enables the
  ; `preserve_order` feature), the top-level keys render alphabetically --
  ; `"optional"` before `"required"` -- so truncating $1 at 1024 bytes would
  ; silently drop the ENTIRE `required` object, including every required
  ; component's `payload_identity`, `replaced_from_offline`, and
  ; `satisfied_online`. main.rs's `--civiccast-stage-packs` handler now
  ; writes the full pretty JSON manifest WHOLE to its own file under
  ; $COMMONPROGRAMDATA\CivicCast\ (`install-manifest-report-<pid>-<unix
  ; time>.json`) and puts into $1 only a short, budget-tested summary line:
  ; that file's path, plus one `component=outcome staged=<8hex>
  ; incoming=<8hex>` token per component (both required and optional). The
  ; exit code in $0, never $1's shape, is what every branch decision here is
  ; made on.
  ;
  ; F-11 fix (2026-08-01, sandbox walkthrough of dd7f835f): this used to
  ; DetailPrint "$1" UNCONDITIONALLY right after popping it, so a
  ; successful install dumped that raw JSON manifest into the wizard's
  ; "Show details" pane -- an operator asking to see what the installer was
  ; doing got a JSON blob instead of a step log, and the real human-readable
  ; step history scrolled out of view underneath it. The pane must carry the
  ; human step log; full detail (including this exact JSON) belongs in
  ; install-progress.log, which the success line below now names -- the log
  ; still gets the complete report via CIVICCAST_STEP, so nothing is lost,
  ; only moved out of the operator-facing pane.
  !insertmacro CIVICCAST_STEP "step stage-packs: begin"
  DetailPrint "Staging required native component packs from the 'packs' folder next to this installer..."
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-stage-packs "$EXEDIR" --install-root "$INSTDIR"'
  Pop $0
  Pop $1
  !insertmacro CIVICCAST_STEP "step stage-packs: returned $0"
  ${If} $0 != 0
    ; This breadcrumb is what makes the dialog's "see the installer log for
    ; the exact missing component(s)" a true statement. It carries the CHILD's
    ; own words, so the component list an operator reads in the log is the
    ; authority's, never a copy this hook could let drift.
    !insertmacro CIVICCAST_STEP "step stage-packs: child reported: $1"
    DetailPrint "CivicCast (Native): required native component pack delivery FAILED (exit $0) — see the installer log above for the exact missing component(s)."
    ; Retrying a partial install is now supported: PREINSTALL invokes the old
    ; bootstrap's fail-closed native service-stop before replacing anything. Do
    ; not tell the operator to uninstall first; correct the side-load and run
    ; setup again. ProgramData remains outside every pack-delivery operation.
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_PACK_DELIVERY} "CivicCast (Native) setup could not obtain a required native component pack.$\r$\n$\r$\nThe component pack file(s) are published alongside this installer -- on the same release page, or on the same distribution medium you got setup from.$\r$\n$\r$\nTo retry:$\r$\n  1. Obtain the required .ccpack file(s) and put them in a 'packs' folder next to the installer (the same folder this setup .exe is in).$\r$\n  2. Run setup again. Setup safely prepares the partial installation before retrying.$\r$\n$\r$\nYour recordings, database, and settings in $COMMONPROGRAMDATA\CivicCast were not deleted.$\r$\n$\r$\nSee the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log for the exact missing component(s)."
  ${Else}
    ; SUCCESS: $1 is now the COMPACT summary described above (the file path
    ; of the full manifest, plus a `component=outcome staged=<8hex>
    ; incoming=<8hex>` token per component) -- log it in full (it already
    ; fits comfortably inside NSIS_MAX_STRLEN, budget-tested in main.rs) and
    ; show the operator a short, honest, human line in the pane naming both
    ; the step log and the full manifest file.
    ;
    ; This is how the 2026-09-05 install-over regression (two kits declaring
    ; the SAME product_version/compatible_core with DIFFERENT pack content)
    ; is diagnosable from install-progress.log: the summary names, per
    ; component, whether the staged pack was left unchanged or replaced from
    ; the incoming offline pack, and an 8-hex-character prefix of both
    ; packs' content digests -- enough to eyeball a mismatch without opening
    ; the full manifest file. Never a separate print call in
    ; native_pack_staging.rs itself, which would break the ZERO-print
    ; invariant this hook's ExecToStack choice depends on (see the long
    ; comment above this block) -- the summary is built and printed once by
    ; main.rs's --civiccast-stage-packs handler, the ONLY thing this child
    ; ever writes to stdout.
    !insertmacro CIVICCAST_STEP "step stage-packs: manifest report: $1"
    DetailPrint "Required native component packs staged and verified. Full detail: $COMMONPROGRAMDATA\CivicCast\install-progress.log"
  ${EndIf}
  ;
  ; ===================================================================
  ; D2 INSTALL-TIME RE-VERIFICATION OF THE EXTRACTED PACK TREE
  ; (spec-installer-lifecycle.md D2; migrated + ADAPTED from the retired
  ; nsis-hooks-native.nsh's pair of manifest-tree D2 re-verification calls)
  ; ===================================================================
  ; ADAPTATION: the retired file re-verified $INSTDIR\runtime and
  ; $INSTDIR\native-runtime against an in-tree app-payload-manifest.json /
  ; runtime-manifest.json -- the WP-6 embedded-payload convention. Neither
  ; path is ever laid down by this bootstrap build (no embedded resource
  ; carries them; see the file header), so re-running that exact check here
  ; would be an unconditional false-abort on every install, not a real
  ; defense. Runs AFTER pack staging (the tree now arrives via packs, so it
  ; cannot be verified before extraction) using the pack-tree form instead:
  ; `--civiccast-verify-pack-tree` re-opens the ORIGINAL signed .ccpack fresh
  ; and re-walks the extracted directory against it (independent of the
  ; extraction step's own internal verification), the same fail-loud "verify
  ; before trusting a laid tree" posture applied to what actually exists on
  ; disk in this architecture.
  ; F-11 fix (2026-08-02, rewalk of b1c6fe4d): this used to run under
  ; nsExec::ExecToLog, which streams 100% of the child's stdout straight into
  ; the wizard's "Show details" pane live -- there is no Pop/DetailPrint choke
  ; point to gate it at all, unlike stage-packs's ExecToStack idiom just
  ; above. On success, --civiccast-verify-pack-tree (main.rs's
  ; run_native_install_verify_cli) prints the FULL VerifiedPack via
  ; serde_json::to_string_pretty -- including its complete files: Vec<{path,
  ; bytes, sha256}>, one entry per file in the pack -- so ExecToLog dumped
  ; that entire per-file manifest into the pane. Switched to ExecToStack (the
  ; same established capture idiom stage-packs uses) so the result can be
  ; gated the same way: the pane gets one honest human line, the full report
  ; still reaches install-progress.log via CIVICCAST_STEP.
  DetailPrint "Re-verifying the extracted native-server-binaries component pack against its signed pack file (D2)..."
  !insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: begin"
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-verify-pack-tree "$INSTDIR\packs\native-server-binaries.ccpack" --destination "$INSTDIR\packs\native-server-binaries\payload" --expected-component native-server-binaries'
  Pop $0
  Pop $1
  ; F-17 fix (2026-08-01, newcomer walkthrough of dd7f835f): this step used to
  ; write only its "begin" breadcrumb -- on success, nothing else ever reached
  ; install-progress.log for it, so an operator reading the log could not tell
  ; whether verification ran, passed, or hung. Written BEFORE the branch below,
  ; exactly like vc-redist / stage-packs / d4-provision, so the result lands
  ; unconditionally on both the success and the failure path; the timestamp on
  ; this line against the timestamp on "begin" is how every other step in this
  ; log already conveys elapsed duration.
  !insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: returned $0"
  ${If} $0 != 0
    !insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: child reported: $1"
    DetailPrint "CivicCast (Native): D2 install-time verification of the extracted native-server-binaries pack FAILED (exit $0)."
    DetailPrint "The tree at $INSTDIR\packs\native-server-binaries\payload does not match its signed pack — see the installer log above for the exact mismatched path(s)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D2_SERVER_BINARIES} "CivicCast (Native) setup could not verify a required native component pack it just extracted against its signed manifest. This usually means disk corruption or an interrupted copy. Re-download the installer/pack and try again; if this persists, contact support with the installer log."
  ${Else}
    ; SUCCESS: $1 is the informational per-file VerifiedPack JSON report. Log
    ; it in full (support needs it) but show the operator a short, honest,
    ; human line in the pane instead -- naming where the rest of the detail
    ; lives.
    !insertmacro CIVICCAST_STEP "step d2-verify-server-binaries: verification report: $1"
    DetailPrint "Native-server-binaries component pack verified against its signed pack file (D2). Full detail: $COMMONPROGRAMDATA\CivicCast\install-progress.log"
  ${EndIf}
  ;
  ; The native-app-payload pack's extraction destination is BRIDGED by
  ; native_pack_staging::ensure_pack_extracted to $INSTDIR\runtime (not the
  ; generic packs\native-app-payload\payload\ convention above) -- see this
  ; file's header and wp2-app-payload-pack-2026-07-30.md. Re-verify what was
  ; actually laid down at that bridged destination.
  ; F-11 fix (2026-08-02, rewalk of b1c6fe4d): same gap as
  ; d2-verify-server-binaries above, and the one the walkthrough actually
  ; witnessed -- native-app-payload's VerifiedPack.files carries the embedded
  ; Python payload (site-packages, alembic migrations, thousands of small
  ; files), so ITS ExecToLog dump was the "Lib/site-packages/civiccast/
  ; alembic/__init__.py" manifest flood the rewalk's evidence/029-*.png
  ; screenshots show. Switched to the same guarded ExecToStack idiom.
  DetailPrint "Re-verifying the extracted native-app-payload component pack against its signed pack file (D2)..."
  !insertmacro CIVICCAST_STEP "step d2-verify-app-payload: begin"
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-verify-pack-tree "$INSTDIR\packs\native-app-payload.ccpack" --destination "$INSTDIR\runtime" --expected-component native-app-payload'
  Pop $0
  Pop $1
  ; F-17 fix (2026-08-01, newcomer walkthrough of dd7f835f): same gap as
  ; d2-verify-server-binaries above -- see that step's comment. The
  ; walkthrough's install-progress.log showed 107 seconds between this step's
  ; "begin" and the next breadcrumb with nothing logged for it in between.
  !insertmacro CIVICCAST_STEP "step d2-verify-app-payload: returned $0"
  ${If} $0 != 0
    !insertmacro CIVICCAST_STEP "step d2-verify-app-payload: child reported: $1"
    DetailPrint "CivicCast (Native): D2 install-time verification of the extracted native-app-payload pack FAILED (exit $0)."
    DetailPrint "The tree at $INSTDIR\runtime does not match its signed pack — see the installer log above for the exact mismatched path(s)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D2_APP_PAYLOAD} "CivicCast (Native) setup could not verify a required native component pack it just extracted against its signed manifest. This usually means disk corruption or an interrupted copy. Re-download the installer/pack and try again; if this persists, contact support with the installer log."
  ${Else}
    ; SUCCESS: $1 is the informational per-file VerifiedPack JSON report --
    ; this is the exact payload the rewalk observed flooding the pane. Log
    ; it in full (support needs it) but show the operator a short, honest,
    ; human line in the pane instead.
    !insertmacro CIVICCAST_STEP "step d2-verify-app-payload: verification report: $1"
    DetailPrint "Native-app-payload component pack verified against its signed pack file (D2). Full detail: $COMMONPROGRAMDATA\CivicCast\install-progress.log"
  ${EndIf}
  ;
  ; The native-ffmpeg-runtime pack's extraction destination is BRIDGED the same
  ; way, to $INSTDIR\dependencies\ffmpeg -- so that its bin\-rooted payload
  ; lands at $INSTDIR\dependencies\ffmpeg\bin\ffmpeg.exe, the exact path
  ; native_activation.rs's validate_staged_runtime_layout and the staged-runtime
  ; self-test both pin. Re-verify what was actually laid down there, never the
  ; generic packs\native-ffmpeg-runtime\payload\ path (which this component
  ; never uses).
  ;
  DetailPrint "Re-verifying the extracted native-ffmpeg-runtime component pack against its signed pack file (D2)..."
  !insertmacro CIVICCAST_STEP "step d2-verify-ffmpeg-runtime: begin"
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-verify-pack-tree "$INSTDIR\packs\native-ffmpeg-runtime.ccpack" --destination "$INSTDIR\dependencies\ffmpeg" --expected-component native-ffmpeg-runtime'
  Pop $0
  Pop $1
  ${If} $0 != 0
    !insertmacro CIVICCAST_STEP "step d2-verify-ffmpeg-runtime: child reported: $1"
    DetailPrint "CivicCast (Native): D2 install-time verification of the extracted native-ffmpeg-runtime pack FAILED (exit $0)."
    DetailPrint "The tree at $INSTDIR\dependencies\ffmpeg does not match its signed pack — see the installer log above for the exact mismatched path(s)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D2_FFMPEG_RUNTIME} "CivicCast (Native) setup could not verify a required native component pack it just extracted against its signed manifest. This usually means disk corruption or an interrupted copy. Re-download the installer/pack and try again; if this persists, contact support with the installer log."
  ${Else}
    !insertmacro CIVICCAST_STEP "step d2-verify-ffmpeg-runtime: verification report: $1"
    DetailPrint "Native-ffmpeg-runtime component pack verified against its signed pack file (D2). Full detail: $COMMONPROGRAMDATA\CivicCast\install-progress.log"
  ${EndIf}
  ;
  ; The product-owned Ollama runtime uses the same bridge pattern. Its pack is
  ; rooted at ollama.exe and lands at $INSTDIR\dependencies\ollama, the exact
  ; absolute path the LocalSystem supervisor resolves before starting AI.
  DetailPrint "Re-verifying the extracted native-ollama-runtime component pack against its signed pack file (D2)..."
  !insertmacro CIVICCAST_STEP "step d2-verify-ollama-runtime: begin"
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-verify-pack-tree "$INSTDIR\packs\native-ollama-runtime.ccpack" --destination "$INSTDIR\dependencies\ollama" --expected-component native-ollama-runtime'
  Pop $0
  Pop $1
  ${If} $0 != 0
    !insertmacro CIVICCAST_STEP "step d2-verify-ollama-runtime: child reported: $1"
    DetailPrint "CivicCast (Native): D2 install-time verification of the extracted native-ollama-runtime pack FAILED (exit $0)."
    DetailPrint "The tree at $INSTDIR\dependencies\ollama does not match its signed pack — see the installer log above for the exact mismatched path(s)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D2_OLLAMA_RUNTIME} "CivicCast (Native) setup could not verify the required local-AI runtime pack it just extracted against its signed manifest. Re-download the installer/pack and try again; if this persists, contact support with the installer log."
  ${Else}
    !insertmacro CIVICCAST_STEP "step d2-verify-ollama-runtime: verification report: $1"
    DetailPrint "Native-ollama-runtime component pack verified against its signed pack file (D2). Full detail: $COMMONPROGRAMDATA\CivicCast\install-progress.log"
  ${EndIf}
  ;
  ; A per-run id for the journal + D7a interlock owner (migrated unchanged).
  ; The installer window handle ($HWNDPARENT) is a stable, per-session unique
  ; token; the journal is keyed by state-root, so this only needs to name the
  ; interlock owner. Computed here because BOTH the D3 engine immediately
  ; below and the D4 provisioning step after it need it, for the SAME
  ; owner-id convention.
  StrCpy $R1 "nsis-$HWNDPARENT"
  ;
  ; ===================================================================
  ; D3 JOURNALED INSTALL/UPGRADE ENGINE (spec-installer-lifecycle.md D3;
  ; rehomed from the retired nsis-hooks-native.nsh's NSIS_HOOK_POSTINSTALL --
  ; left unwired by the prior hook-migration because $INSTDIR\runtime\
  ; python.exe did not exist in any bootstrap build target yet; the
  ; app-payload-pack gap closure above (bce9a3cf) bridges the native-
  ; app-payload pack's extraction to exactly that path, and the D2
  ; re-verification two blocks above re-checks that bridged tree before this
  ; call trusts it. See the file header and wp2-d3-rehoming-2026-07-30.md.
  ; ===================================================================
  ; Runs as a THIN SHELL -- all D3 logic (acquire interlock -> drain+verify
  ; quiescence -> verified pre-upgrade backup -> lay tree + flip junction ->
  ; migrate -> maintenance/read-only health gate -> commit, with the defined
  ; rollback and rollback-failure recovery paths) lives in tested Python
  ; (civiccast.native.upgrade), never in NSIS script; the engine is
  ; idempotently resumable from its own journal.
  ;
  ; POSITION (coordinator decision): runs HERE -- after D2 pack-tree
  ; verification and signed station activation, and BEFORE D4 provisioning/
  ; service registration below --
  ; because tree management (junction flip, migration, health gate, rollback)
  ; must commit on the tree BEFORE provisioning/service-build acts on it.
  ;
  ; Contract of the call (exit code -> installer branch):
  ;   0  committed        10 clean rollback (old version healthy)
  ;   11 fresh install    — no installed product; the engine is not applicable
  ;   12 same version     — nothing to migrate; the engine did not run
  ;   20 HALTED — operator recovery doc emitted (see ProgramData\CivicCast\upgrade)
  ;   30 refused (declared non-restorable migration; needs operator ack)
  ;   40 unexpected fault
  ; 0/10/20/30/40 are preserved EXACTLY from the retired file. 11 and 12 are
  ; the ROUTING outcomes added by chain K/K2 (see below); neither is a failure
  ; and both continue the install. The numeric ladder is cross-checked against
  ; civiccast/native/upgrade/__main__.py's _EXIT_CODES and _ROUTE_EXIT_CODES
  ; in BOTH directions by tests/policy/test_native_installer_identity.py, so a
  ; code the engine can emit without a branch here fails the build.
  ;
  ; $R0 = the version installed by the PREVIOUS successful run of this chain,
  ; from the product-owned InstalledVersion marker ("none" on a fresh
  ; install). NOT the ARP DisplayVersion: Tauri's generated install section
  ; writes ARP (including DisplayVersion) BEFORE NSIS_HOOK_POSTINSTALL runs,
  ; so by the time this gate executes ARP always shows the version being
  ; installed RIGHT NOW — it can neither detect a fresh install nor supply
  ; the true old version during an upgrade. Proven live in Sandbox matrix
  ; row 1, 2026-07-30: the ARP-keyed gate never fired, the engine ran with
  ; an empty --database-url and faulted (exit 1). The marker is written only
  ; at the fully-successful end of this chain (below) and, like DatabaseUrl,
  ; deliberately survives uninstall (the database cluster is preserved), so
  ; reinstall-over-existing-data correctly runs the engine as an upgrade.
  ; $R2 = the DatabaseUrl registry value AS IT STANDS RIGHT NOW. ADAPTATION
  ; from the retired block: that block read this same key AFTER D4
  ; provisioning had already run (D3 ran after D4 there), so it always saw a
  ; freshly-provisioned value. Here D3 runs BEFORE provisioning, so $R2 is
  ; empty on a first-ever install; provisioning below re-reads the same
  ; (unchanged by D3) key into $R3 for its own decision matrix, so its
  ; fresh-vs-reuse logic is unaffected. See wp2-d3-rehoming-2026-07-30.md.
  SetRegView 64
  StrCpy $R0 ""
  ReadRegStr $R0 HKLM "Software\CivicCast\Native" "InstalledVersion"
  ${If} $R0 == ""
    StrCpy $R0 "none"
  ${EndIf}
  StrCpy $R2 ""
  ReadRegStr $R2 HKLM "Software\CivicCast\Native" "DatabaseUrl"
  ; The candidate runtime's maintenance health gate validates the complete
  ; station-set contract. On upgrades from earlier releases, establish that
  ; contract from the same signed station distribution before D3 starts the
  ; candidate runtime. The activation command verifies the signed index and
  ; packs, and its self-tests do not provision, migrate, or access the
  ; application database. Failures below still go through CIVICCAST_FAIL,
  ; which contains the replaced payload before aborting setup.
  ;
  ; ===================================================================
  ; K1 FIX: FLAT-LAYOUT STATION ACTIVATION
  ; ===================================================================
  ; The ONLY writer of station-set.json / activation-self-test.json in this
  ; codebase (native_activation.rs::activate_flat_station_with, reusing the
  ; same composition/atomic-write machinery stage_distribution_with's
  ; versioned counterpart already uses) had no production caller anywhere in
  ; this hook chain (K1 audit, 2026-08-16) -- so a freshly registered,
  ; started CivicCastSupervisor service could never find station-set.json at
  ; $INSTDIR and stayed permanently in the (gracefully degraded, but never
  ; activated) NativeStationNotActivatedError state. This step closes that
  ; gap: it writes both files DIRECTLY at $INSTDIR (flat, no app/<version>
  ; subdirectory) -- the shape native/station_runtime.py::
  ; load_native_station_environment requires for the service registered
  ; below against $INSTDIR\runtime\python.exe.
  ;
  ; Sources a signed station bundle, verified through
  ; native_distribution::acquire_station_distribution -- reused verbatim via
  ; --civiccast-import-station, never a second, forked acquisition path.
  ;
  ; TWO SOURCES, resolved in this order (2026-09-02 owner decision, "make
  ; sure the installer always HAS that index, even when downloaded alone"):
  ;
  ;   (a) "$EXEDIR\station\station-index.json" -- the USB-kit side-load next
  ;       to setup.exe. The SAME "packs next to the installer" convention
  ;       --civiccast-stage-packs already uses above for component packs,
  ;       extended to a full station bundle (a signed index plus its own
  ;       packs). assemble-native-beta-kit publishes exactly this layout, so
  ;       an air-gapped station installing from the stick keeps behaving
  ;       EXACTLY as before this branch: the kit copy wins, its packs sit
  ;       beside it in the same directory, and no cache round-trip happens.
  ;
  ;   (b) "$INSTDIR\station\station-index.json" -- the EMBEDDED copy. The
  ;       signed index, placeholder `core` pack (~1.5 KB), and mandatory
  ;       `captions-whistle.ccpack` (<25 MB; pinned local model+engine) ship
  ;       inside setup.exe as Tauri bundle.resources and are laid down at
  ;       $INSTDIR\station\ before this hook runs. This makes a DOWNLOAD-ONLY
  ;       install/upgrade of setup.exe alone work with a Beta 10 cache: the
  ;       required Whistle component comes from the embedded sidecar while
  ;       the remaining ~21 GB of model packs must be satisfied from the
  ;       per-SHA cache under --cache-root (native_distribution.rs::
  ;       copy_station_pack_to_cache). The fixed resource map and signed-index
  ;       hash/size checks prevent embedding a different or oversized pack;
  ;       see scripts/build_native_bootstrap.py.
  ;
  ; Fails loud only when NEITHER exists. An unconditional silent skip here
  ; is the exact shape that produced K1 in the first place (an install that
  ; reports success while the station can never activate), so the fail-closed
  ; branch stays -- it just can no longer fire merely because the operator
  ; downloaded setup.exe on its own.
  ;
  ; Written as two literal nsExec invocations rather than one invocation
  ; over a computed path register: $R0-$R3 carry installer state across the
  ; following D3/D4 steps, and $0-$9 are unusable inside a CIVICCAST_STEP
  ; breadcrumb argument. Two literals cost a duplicated line and cannot
  ; clobber anything.
  ; Capture the activation CLI's output into $1 as well as its exit code in
  ; $0. The 67 failure dialog points operators at install-progress.log for
  ; the self-test reason, but ExecToLog only streams the child's text into
  ; the wizard detail pane (which unattended installs do not have). ExecToStack
  ; keeps the existing invocation arguments and records the bounded output
  ; prefix on every nonzero result below; NSIS's stack-string limit means this
  ; is not a promise to preserve arbitrarily long child stderr in full. The
  ; activation CLI emits only its final JSON/error, so no running progress is
  ; lost by switching from ExecToLog.
  !insertmacro CIVICCAST_STEP "step d4-activate-station: begin"
  DetailPrint "Activating the CivicCast (Native) station (K1)..."
  IfFileExists "$EXEDIR\station\station-index.json" civiccast_activate_station_from_exedir civiccast_activate_station_try_instdir
  civiccast_activate_station_from_exedir:
  !insertmacro CIVICCAST_STEP "step d4-activate-station: source EXEDIR (kit side-load $EXEDIR\station\station-index.json)"
  DetailPrint "CivicCast (Native): using the station bundle beside setup.exe ($EXEDIR\station)."
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-activate-station --install-root "$INSTDIR" --civiccast-import-station "$EXEDIR\station\station-index.json" --cache-root "$INSTDIR\packs\.station-cache"'
  Pop $0
  Pop $1
  Goto civiccast_activate_station_ran
  civiccast_activate_station_try_instdir:
  IfFileExists "$INSTDIR\station\station-index.json" civiccast_activate_station_from_instdir civiccast_activate_station_no_index
  civiccast_activate_station_from_instdir:
  !insertmacro CIVICCAST_STEP "step d4-activate-station: source INSTDIR (embedded $INSTDIR\station\station-index.json)"
  DetailPrint "CivicCast (Native): using the station index embedded in setup.exe ($INSTDIR\station)."
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-activate-station --install-root "$INSTDIR" --civiccast-import-station "$INSTDIR\station\station-index.json" --cache-root "$INSTDIR\packs\.station-cache"'
  Pop $0
  Pop $1
  Goto civiccast_activate_station_ran
  civiccast_activate_station_no_index:
  !insertmacro CIVICCAST_STEP "step d4-activate-station: no station index at $EXEDIR\station or $INSTDIR\station"
  DetailPrint "CivicCast (Native): station activation FAILED — no signed station index was found."
  !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "CivicCast (Native) setup could not activate the station: no signed station index (station-index.json) was found beside setup.exe at $EXEDIR\station, and this setup.exe does not carry the embedded copy it normally ships with. Download the CivicCast (Native) setup again from the official release page, or copy the full CivicCast kit folder (setup.exe together with its station folder) onto this machine and run setup from there. See the installer log above for details."
  civiccast_activate_station_ran:
  !insertmacro CIVICCAST_STEP "step d4-activate-station: returned $0"
  ${If} $0 != 0
    !insertmacro CIVICCAST_STEP "step d4-activate-station: child reported: $1"
  ${EndIf}
  ; Installer-path audit MA-08: run_native_flat_activation_cli emits FIVE
  ; distinct exit codes -- 64 (arguments), 65 (render), 66 (acquisition), 67
  ; (activation / self-test), 78 (embedded pack trust) -- and this branch used
  ; to collapse all of them into one fixed sentence about the station folder
  ; and the pack cache. That sentence is correct for 66-with-a-cache-miss and
  ; WRONG for the other four: 67 means the packs were fine and the station's
  ; own self-test failed; 78 means the shipped trust key is a development key
  ; without the matching opt-in, i.e. a BUILD defect; 64/65 are
  ; installer-authoring bugs. This file's own header (:374-377) states the
  ; rationale that was being discarded: "the exit code is the only signal a
  ; support log carries about WHICH step failed".
  ${If} $0 == 0
    DetailPrint "CivicCast (Native): station activation complete (or already activated; no-op)."
  ${ElseIf} $0 == 67
    DetailPrint "CivicCast (Native): station activation self-test FAILED (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "CivicCast (Native) setup laid down the station's components, but the station's own self-test did not pass, so setup stopped rather than leave you with a station that looks installed and does not work.$\r$\n$\r$\nThis is NOT a missing-files problem -- the component packs were obtained and verified. The self-test that failed is named in the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log.$\r$\n$\r$\nYour recordings, database and settings in $COMMONPROGRAMDATA\CivicCast were not deleted."
  ${ElseIf} $0 == 66
    DetailPrint "CivicCast (Native): station activation could not obtain its component packs (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "CivicCast (Native) setup could not obtain the station's component packs from the signed station index it found.$\r$\n$\r$\nIf you installed from a CivicCast kit folder, make sure its station folder was copied across whole. If you ran setup.exe on its own, the packs it needs must already be in this machine's pack cache from a previous install.$\r$\n$\r$\nSee the installer log above for the exact underlying error -- it names either the missing pack or the signature/version check that refused one."
  ${ElseIf} $0 == 78
    DetailPrint "CivicCast (Native): station activation refused this setup.exe's embedded trust key (exit $0)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "This copy of CivicCast (Native) setup is not a valid release build: its embedded signing key was refused.$\r$\n$\r$\nNothing is wrong with this machine. Download CivicCast (Native) setup again from the official release page and run that copy.$\r$\n$\r$\nNothing was deleted. The exact refusal is recorded in the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${ElseIf} $0 == 64
  ${OrIf} $0 == 65
    DetailPrint "CivicCast (Native): station activation was invoked incorrectly (exit $0)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "This copy of CivicCast (Native) setup is defective: its own station-activation step was invoked with arguments it does not accept.$\r$\n$\r$\nNothing is wrong with this machine. Download CivicCast (Native) setup again from the official release page and run that copy.$\r$\n$\r$\nNothing was deleted. The exact refusal is recorded in the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${Else}
    DetailPrint "CivicCast (Native): station activation FAILED (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_ACTIVATION} "CivicCast (Native) setup could not activate the station from the signed station index it found (exit code $0). See the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log for the exact underlying error.$\r$\n$\r$\nYour recordings, database and settings in $COMMONPROGRAMDATA\CivicCast were not deleted."
  ${EndIf}
  ;
  ; UPGRADE-VS-FRESH ROUTING (chain K/K2, real-hardware R7, 2026-08-01).
  ;
  ; This block used to hold the routing gate itself:
  ;     ${If} $R0 == "none"      ; InstalledVersion absent
  ;       ${AndIf} $R2 == ""     ; DatabaseUrl absent
  ;       ... skip the D3 engine
  ; Both of those HKLM values SURVIVE uninstall BY DESIGN (they are the
  ; credential for, and version stamp of, the PostgreSQL cluster uninstall
  ; deliberately preserves -- see native_uninstall.rs's
  ; NATIVE_D4_STATE_INVENTORY). They are DATA-REMNANT signals, not
  ; product-existence signals, so the gate could never fire on a machine that
  ; had ever held a successful install. R7 was exactly that machine: 0 ARP
  ; entries, no CivicCastSupervisor service, no install directory -- and the
  ; installer ran the UPGRADE engine anyway, "upgrading" 1.0.0-rc15 to
  ; 1.0.0-rc15, failing, and ending setup in a rollback dialog that told the
  ; operator that a version which was never installed remained in good health
  ; and running. (The exact wording is not repeated here: chain M3's test
  ; greps this file for that sentence, and a comment quoting it would trip a
  ; pin whose whole job is proving the claim is gone from the product.)
  ;
  ; The decision now lives in tested Python (civiccast/native/upgrade/
  ; routing.py), which is where this file's own contract says all D3 logic
  ; belongs -- the routing gate was the one piece that violated it, and it is
  ; the piece that broke. The engine decides BEFORE it builds any seam or
  ; touches the database, keying on whether a product actually EXISTS (SCM
  ; registration of CivicCastSupervisor -- the same signal NSIS_HOOK_PREINSTALL's
  ; upgrade classification above already uses, and the only tracked D4 item that
  ; is both created by install and removed by uninstall). It reports the route
  ; through exit codes 11/12 below.
  ;
  ; The engine is therefore invoked UNCONDITIONALLY now. That is deliberate:
  ; one decision point instead of two that can disagree, and every run --
  ; including a first-ever install -- leaves the routing decision and its
  ; reason in the durable engine log under $COMMONPROGRAMDATA\CivicCast\upgrade,
  ; which is the artifact a support case actually has. $R2 may legitimately be
  ; empty here (D3 runs before D4 provisioning); the routing decision is made
  ; before the URL is used for anything.
  !insertmacro CIVICCAST_STEP "step d3-engine: begin (old=$R0)"
  DetailPrint "Running the CivicCast (Native) install/upgrade engine (D3)..."
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -m civiccast.native.upgrade \
      --old-version "$R0" --new-version "${VERSION}" \
      --install-root "$INSTDIR" \
      --state-root  "$COMMONPROGRAMDATA\CivicCast\upgrade" \
      --database-url "$R2" --owner-run-id "$R1" \
      --payload-source "$INSTDIR\runtime" --flat-installer-layout'
  Pop $0  ; engine exit code
  ; Machine-parseable Gate A evidence. The durable log is append-only, so the
  ; harness consumes the LAST matching line and thereby binds its verdict to
  ; the current installer rather than the baseline install earlier in the run.
  ; Exit 11/12 are successful installer routes but are NOT upgrade proof.
  ${If} $0 == 11
    !insertmacro CIVICCAST_STEP "step d3-engine: evidence route=FRESH_INSTALL engine_exit=11"
  ${ElseIf} $0 == 12
    !insertmacro CIVICCAST_STEP "step d3-engine: evidence route=SAME_VERSION_NO_OP engine_exit=12"
  ${ElseIf} $0 == 13
    !insertmacro CIVICCAST_STEP "step d3-engine: evidence route=REFUSED_DOWNGRADE engine_exit=13"
  ${Else}
    !insertmacro CIVICCAST_STEP "step d3-engine: evidence route=UPGRADE engine_exit=$0"
  ${EndIf}
  ${If} $0 == 0
    DetailPrint "CivicCast (Native): install/upgrade committed."
  ${ElseIf} $0 == 11
    ; ROUTED TO FRESH INSTALL (chain K/K2). No installed product was found, so
    ; there was nothing to drain, back up, migrate, or health-gate. NOT a
    ; failure and NOT a rollback: the install continues to D4 below, which
    ; adopts any preserved PostgreSQL cluster and its credential as-is (its
    ; own decision matrix already treats an existing DatabaseUrl as reuse, not
    ; regenerate). The InstalledVersion write at the end of this macro runs
    ; normally -- this run really is installing ${VERSION}.
    ; The engine's own reason line (which names the preserved data root it is
    ; adopting) is already in this log via nsExec::ExecToLog and in
    ; $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-engine.log.
    DetailPrint "CivicCast (Native): no existing installation was found, so this is a fresh install — the install/upgrade engine was not applicable and did not run. Any CivicCast data already on this machine is preserved and adopted by this installation; nothing was deleted."
    !insertmacro CIVICCAST_STEP "step d3-engine: SKIPPED (routed to fresh install; existing data adopted, not deleted)"
  ${ElseIf} $0 == 12
    ; ROUTED TO SAME-VERSION NO-OP (chain K/K2). A real installed product is
    ; present and already at ${VERSION}. There is no migration between a
    ; version and itself, so the migration engine did not run. Honest text
    ; rather than a fabricated "upgrade committed": nothing about the database
    ; was touched. The rest of the chain (pack re-extraction, D2
    ; re-verification, D4 service/firewall re-registration) still runs and is
    ; what actually normalizes an installed tree.
    DetailPrint "CivicCast (Native): version ${VERSION} is already installed — there is no database migration to run, so the install/upgrade engine did nothing. Your data was not drained, backed up, migrated, or changed."
    !insertmacro CIVICCAST_STEP "step d3-engine: NO-OP (same version ${VERSION} already installed; no migration to run)"
  ${ElseIf} $0 == 13
    ; Defensive late refusal: preflight should already have caught this.
    ; Payload replacement and station activation have happened at this point.
    !insertmacro CIVICCAST_STEP "step d3-engine: late REFUSED_DOWNGRADE after payload replacement; newer setup repair required"
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_REFUSED_DOWNGRADE} "A newer CivicCast installation was detected after application files had already been replaced. Database downgrade was refused. Setup cannot restore the previous application files automatically, and the station may be unavailable.$\r$\n$\r$\nRun the complete signed setup kit matching the newer installed version to restore its application files and service. Do not uninstall or delete $COMMONPROGRAMDATA\CivicCast. Keep the kit available offline and contact support if repair fails. See $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${ElseIf} $0 == 10
    ; HISTORY: a 2026-07-30 adversarial-review fix stopped this branch from
    ; recording a clean rollback as a SUCCESSFUL upgrade (it used to
    ; DetailPrint only and fall through to the InstalledVersion write at the
    ; end of this macro). It then continued the install with a non-fatal
    ; CIVICCAST_NOTICE, correct ONLY for the app\<version> + junction layout,
    ; where flip_junction really does restore the old binary tree.
    ;
    ; Gate A run 33681670855 CRITICAL fix (2026-09-02) supersedes that
    ; continue-with-notice behavior. This invocation (see the nsExec::
    ; ExecToLog call above) always passes --flat-installer-layout, under
    ; which adapt_flat_installer_layout (civiccast/native/upgrade/seams.py)
    ; makes read_junction/lay_tree/flip_junction no-ops over the single
    ; "$INSTDIR\runtime" tree this bootstrap already extracted the NEW
    ; payload into BEFORE the engine ran, so under that layout a ROLLED_BACK
    ; report leaves the NEW code sitting in $INSTDIR\runtime regardless of
    ; what caused the rollback. On real hardware (Gate A run 33681670855, kit
    ; 7971815, beta.2 -> beta.3) the old DetailPrint-only branch let setup
    ; fall through to D4, which provisioned, activated, and registered/
    ; started the service on that mismatched new-code/old-schema pair, so the
    ; box ended the run serving 500s under a NOTICE-only log with exit code 0.
    ;
    ; WHAT CAUSED IT is deliberately NOT named in the operator text below.
    ; Exit 10 (ROLLED_BACK) is reached from orchestrator._drive_forward's
    ; single funnel (`except Exception as exc: return _rollback(journal,
    ; seams, reason=str(exc), attempting=attempting)`) -- ANY operational step
    ; can raise into it: drain/quiesce, the pre-upgrade backup/restore-drill
    ; (the Gate A run 33681670855 root cause, but only ONE of several
    ; possible causes), migrate, or the post-migration health gate. An
    ; earlier draft of this message named the backup check specifically;
    ; that was wrong for every other funneled cause. The real, specific
    ; reason lives in two places an operator or support engineer can read:
    ; upgrade-engine.log (which __main__.py now appends `outcome.journal.
    ; error` to, not just the bare phase name -- see that file's outcome-
    ; logging fix in the same PR) and the full journal at
    ; upgrade-journal.json beside it. The runtime-generated
    ; UPGRADE-RECOVERY.md doc (cited by exit 20/CIVICCAST_EXIT_D3_HALTED
    ; below) is NOT cited here: orchestrator.py only ever writes it from
    ; _halt, i.e. on HALTED_RESTORE_FAILED (exit 20) -- it does not exist for
    ; a plain ROLLED_BACK, and citing a file that is not there would be
    ; exactly the kind of unverified claim this codebase's own audit
    ; protocol forbids.
    ;
    ; So: fail closed here. CIVICCAST_FAIL aborts before any D4 step runs --
    ; the service is never registered or started on the new payload. The
    ; previous version's DATABASE is intact regardless of which step
    ; funneled into this rollback: a pre-mutation failure never touched it,
    ; and a post-mutation failure only reaches ROLLED_BACK (rather than
    ; HALTED_RESTORE_FAILED) once _rollback's own restore of the pre-upgrade
    ; backup has already succeeded (civiccast.native.upgrade.orchestrator.
    ; _rollback). What is NOT intact is which CODE is on disk under the flat
    ; layout, which is exactly what this abort communicates and prevents
    ; from going live.
    ; Review of PR #145: the containment sentence is CONDITIONAL on what
    ; actually succeeded. Containment is best-effort (CIVICCAST_FAIL), so
    ; asserting "stopped AND set to manual start" unconditionally would be a
    ; second instance of the exact defect BL-02 is about -- a message true
    ; only when nothing went wrong, silent when something did.
    ;
    ; CIVICCAST_FAIL runs containment BEFORE it shows this text, so
    ; $CIVICCAST_CONTAINED is already set when either branch is inserted.
    ${If} $CIVICCAST_CONTAINED == "1"
      !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT} "CivicCast (Native) setup could not complete: the upgrade engine rolled back its own work before finishing the upgrade to ${VERSION}. Your previous version's database is intact and was not left mid-migration.$\r$\n$\r$\nThe station's service has been stopped AND set to manual start, so it will not come up on the new files at the next restart either. A successful re-run of setup puts it back to automatic start.$\r$\n$\r$\nSee the engine's own reason in $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-engine.log (the full record is in upgrade-journal.json beside it) and $COMMONPROGRAMDATA\CivicCast\install-progress.log, then re-run setup after resolving the cause."
    ${Else}
      !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_ROLLED_BACK_FLAT} "CivicCast (Native) setup could not complete: the upgrade engine rolled back its own work before finishing the upgrade to ${VERSION}. Your previous version's database is intact and was not left mid-migration.$\r$\n$\r$\nIMPORTANT: setup could NOT confirm that it stopped the station's service and set it to manual start, so the service may still start automatically the next time this computer restarts -- on the NEW program files, against your previous version's database. Before restarting, stop it and set it to manual: run 'sc stop CivicCastSupervisor' and 'sc config CivicCastSupervisor start= demand' from an administrator command prompt, or use services.msc.$\r$\n$\r$\nSee the engine's own reason in $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-engine.log (the full record is in upgrade-journal.json beside it) and $COMMONPROGRAMDATA\CivicCast\install-progress.log, then re-run setup after resolving the cause."
    ${EndIf}
  ${ElseIf} $0 == 20
    ; F-03: name WHOSE rollback, for the same reason as the exit-10 branch
    ; above -- "rollback", unqualified, is what an operator reads as the
    ; install being undone. This one is the D3 upgrade engine's own rollback
    ; of its own database work, and unlike exit 10 it did NOT succeed.
    DetailPrint "CivicCast (Native): upgrade HALTED — the D3 upgrade engine's automatic rollback of its own work could not restore the database."
    DetailPrint "The service has been left STOPPED on purpose. An operator recovery document was written to"
    DetailPrint "$COMMONPROGRAMDATA\CivicCast\upgrade\UPGRADE-RECOVERY.md — follow it before restarting."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_HALTED} "CivicCast (Native) upgrade could not complete and automatic rollback could not restore the database. The service is stopped. Follow the recovery steps in:$\n$\n$COMMONPROGRAMDATA\CivicCast\upgrade\UPGRADE-RECOVERY.md"
  ${ElseIf} $0 == 30
    DetailPrint "CivicCast (Native): this release declares a non-restorable migration and needs operator acknowledgement; automatic upgrade was refused."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_REFUSED} "This CivicCast (Native) release includes a database migration that cannot be automatically rolled back. Automatic upgrade was refused. Use the manual upgrade path with operator acknowledgement."
  ${ElseIf} $0 == 31
    ; Installer-path audit BL-06. A PREVIOUS run ended in a terminal failure
    ; and left its journal behind; THIS run did nothing at all. Before this
    ; branch existed the engine returned that stale journal's own phase --
    ; usually 10 -- which the exit-10 branch above turns into a fatal 124
    ; telling the operator to "re-run setup after resolving the cause". Every
    ; re-run returned 10 again, forever, whatever was fixed, because nothing
    ; deletes the journal ($COMMONPROGRAMDATA\CivicCast is preserved by
    ; uninstall BY DESIGN, so even uninstall/reinstall did not clear it). The
    ; message names the ONE action that unwedges the machine.
    DetailPrint "CivicCast (Native): a previous upgrade attempt's record is still present; this run did nothing."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_STALE_JOURNAL} "CivicCast (Native) setup stopped before doing anything, because an EARLIER upgrade attempt on this machine ended in a failure whose record is still on disk.$\r$\n$\r$\nThat record is deliberately kept so support can read it -- but until it is moved aside, every new attempt refuses to start, so that a fresh upgrade cannot overwrite the recovery point it describes.$\r$\n$\r$\nTo continue: move (do not delete) the file$\r$\n$\r$\n    $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-journal.json$\r$\n$\r$\nsomewhere safe -- keep it for support -- and run setup again.$\r$\n$\r$\nNothing on this machine was changed by this run. Its reason is recorded in $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-engine.log."
  ${Else}
    DetailPrint "CivicCast (Native): the install/upgrade engine reported an unexpected fault (exit $0)."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D3_FAULT} "CivicCast (Native) setup hit an unexpected fault while running the install/upgrade engine (exit code $0). See the installer log."
  ${EndIf}
  civiccast_bootstrap_d3_done:
  ;
  ; ===================================================================
  ; D4 DATABASE/MESSAGING SERVER PROVISIONING (migrated unchanged from
  ; nsis-hooks-native.nsh's NSIS_HOOK_POSTINSTALL)
  ; ===================================================================
  ; Runs the journaled PostgreSQL provisioning engine
  ; (civiccast.native.provision). Reads the CURRENT DatabaseUrl registry
  ; value first (into $R3) so the CLI can tell a fresh install (no value, no
  ; cluster -- runs and generates a password) apart from a re-install over an
  ; existing cluster (existing value -> no-op, reused as-is; missing value
  ; with an existing cluster -> fail-loud, D5 repair territory, never a
  ; silent regenerate). See civiccast.native.provision.__main__'s
  ; ProvisionCliAction decision matrix and native_service_registration.rs's
  ; run_native_provision for the full contract. The generated password is
  ; NEVER printed to this installer log -- it travels only inside the
  ; Rust-captured (never echoed) subprocess stdout and the HKLM write below.
  StrCpy $R3 ""
  ReadRegStr $R3 HKLM "Software\CivicCast\Native" "DatabaseUrl"
  !insertmacro CIVICCAST_STEP "step d4-provision: begin"
  DetailPrint "Provisioning the CivicCast (Native) PostgreSQL server (D4)..."
  ; beta.5.1: clear a previous run's ownership observation first, so the
  ; read-back below can only ever report THIS run's observation (a CLI that
  ; exits before its ownership step -- e.g. a usage error -- leaves no file,
  ; and the read-back then says so instead of replaying stale text).
  Delete "$COMMONPROGRAMDATA\CivicCast\provision\ownership-observation.txt"
  nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" --civiccast-provision --install-root "$INSTDIR" --owner-run-id "$R1" --existing-database-url "$R3"'
  Pop $0
  !insertmacro CIVICCAST_STEP "step d4-provision: returned $0"
  ; beta.5.1 (runtime-ownership claim, field defect 2026-09-09): the CLI's
  ; ownership step now runs BEFORE the provisioning engine and writes its
  ; one-line observation -- which hive/SID, which WOW64 view, which error
  ; kind and OS error code -- to ownership-observation.txt on EVERY path.
  ; Read it back here so install-progress.log carries it (the CLI's stderr
  ; only reaches this details pane, which is not persisted anywhere) and so
  ; the exit-85 dialog below can show the actual observation instead of a
  ; guess. The full per-read list and the remedy are in OWNERSHIP-RECOVERY.md
  ; beside it on the 85 path.
  StrCpy $R6 "(no ownership observation file was written; see the details above)"
  ${If} ${FileExists} "$COMMONPROGRAMDATA\CivicCast\provision\ownership-observation.txt"
    ClearErrors
    FileOpen $R7 "$COMMONPROGRAMDATA\CivicCast\provision\ownership-observation.txt" r
    ${IfNot} ${Errors}
      FileRead $R7 $R6
      FileClose $R7
    ${EndIf}
  ${EndIf}
  !insertmacro CIVICCAST_STEP "step d4-provision: runtime ownership: $R6"
  ${If} $0 == 0
    DetailPrint "CivicCast (Native): database/messaging provisioning complete (or already provisioned; no-op)."
  ${ElseIf} $0 == 75
    DetailPrint "CivicCast (Native): D4 database/messaging provisioning FAILED (exit 75) — see the installer log above and $COMMONPROGRAMDATA\CivicCast\provision\PROVISION-RECOVERY.md."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_PROVISION_FAILED} "CivicCast (Native) setup could not provision the PostgreSQL server. See the installer log and $COMMONPROGRAMDATA\CivicCast\provision\PROVISION-RECOVERY.md for details."
  ${ElseIf} $0 == 87
    ; Round 3 of the beta.5 ownership defect (corrected field report): the
    ; selector is absent and ANOTHER CivicCast product is genuinely
    ; registered -- $R6 leads with "Setup found another CivicCast product
    ; installed for user <name>: <DisplayName DisplayVersion> (registered at
    ; ...; InstallLocation ...; UninstallString ...)". The remedy is that
    ; product, not the registry value, so this text carries NO
    ; ActiveRuntime instruction. Same string budget as the 85 arm below
    ; (542 static at runtime + 420 observation cap + 31 log-line overhead
    ; = 993; 558 counted as source literal + 420 + 35 = 1013 worst case,
    ; both <= 1022; the test test_the_exit_85_and_87_dialogs_fit_the_nsis_
    ; string_budget_with_the_observation MEASURES both strings, the
    ; overhead and NSIS_MAX_STRLEN on every run -- round 4 shortened this
    ; text after the review found no slack).
    DetailPrint "CivicCast (Native): D4 found another CivicCast product installed on this machine (exit 87) — see the installer log above and $COMMONPROGRAMDATA\CivicCast\provision\OWNERSHIP-RECOVERY.md."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_OTHER_PRODUCT} "CivicCast (Native) setup found another CivicCast product installed on this machine, so it will not claim the runtime. Setup stopped before provisioning: postgresql.conf, pg_hba.conf and your database credential were not touched.$\r$\n$\r$\nWhat setup observed: $R6$\r$\n$\r$\nUninstall that product from Settings > Apps (or run its UninstallString above), or run civiccast-runtime cutover-to-native from it, then run setup again. Every read: $COMMONPROGRAMDATA\CivicCast\provision\OWNERSHIP-RECOVERY.md; also logged in $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${ElseIf} $0 == 85
    ; Installer-path audit BL-13: the ActiveRuntime selector could not be
    ; established. This used to be a printed sentence and an exit 0, after
    ; which setup registered and started a service whose control plane the
    ; dual-runtime guard blocks, then reported "installation complete".
    ; Round 3: this arm is now ONLY the Unknown (a read failed for a reason
    ; other than not-found) and Unreadable-selector cases; a genuinely
    ; registered other product is exit 87 above.
    DetailPrint "CivicCast (Native): D4 could not establish this machine's runtime ownership (exit 85) — see the installer log above and $COMMONPROGRAMDATA\CivicCast\provision\OWNERSHIP-RECOVERY.md."
    ; beta.5.1: the text carries the CLI's actual observation ($R6, read back
    ; above) instead of the former "most often a permissions problem on
    ; HKEY_USERS" guess, and the claim now runs BEFORE provisioning, so the
    ; "not touched" sentence is a fact, not a hope. It names ONLY the
    ; provisioning outputs (postgresql.conf, pg_hba.conf, the credential):
    ; on the upgrade path d3-engine and the Tauri section have already
    ; replaced $INSTDIR by the time this runs, so "nothing was deleted"
    ; would be false.
    ; String budget: NSIS_MAX_STRLEN is 1024 (Tauri's NSIS 3.11, measured
    ; with makensis -HDRINFO) and CIVICCAST_ALERT wraps this text in the
    ; CIVICCAST_STEP timestamp, "ALERT: " and a trailing CRLF -- 31 chars
    ; at runtime, 35 counted as source -- in the ONE string it FileWrites
    ; to install-progress.log, so the static text here (520 chars at
    ; runtime, 540 counted as a source literal) plus the observation line
    ; (capped at OWNERSHIP_OBSERVATION_LINE_MAX_CHARS = 420 by the Rust
    ; writer, raised from 360 in round 3 so the exit-87 lead fits) must stay
    ; <= 1022: 520 + 420 + 31 = 971 at runtime, 540 + 420 + 35 = 995 as
    ; source. Round 4 shortened both arms after the review measured the
    ; previous text at 584 (source count) and found it over; round 5
    ; corrected the overhead (the old model stopped at "ALERT: " and was 2
    ; short). test_the_exit_85_and_87_dialogs_fit_the_nsis_string_budget_
    ; with_the_observation derives every number (limit, overhead, both
    ; strings, the cap) on every run and names the maximum safe cap when it
    ; fails. Do not lengthen either arm without running it.
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_RUNTIME_OWNERSHIP} "CivicCast (Native) setup could not establish which CivicCast runtime owns this machine. Setup stopped before provisioning: postgresql.conf, pg_hba.conf and your database credential were not touched.$\r$\n$\r$\nWhat setup observed: $R6$\r$\n$\r$\nIf this machine has no CivicCast WSL product, an administrator sets HKLM\SOFTWARE\CivicCast\ActiveRuntime to $\"native$\" and runs setup again. The exact command and every read: $COMMONPROGRAMDATA\CivicCast\provision\OWNERSHIP-RECOVERY.md; also logged in $COMMONPROGRAMDATA\CivicCast\install-progress.log."
  ${Else}
    DetailPrint "CivicCast (Native): D4 database/messaging provisioning reported an unexpected fault (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_PROVISION_FAULT} "CivicCast (Native) setup hit an unexpected fault while provisioning the PostgreSQL server (exit code $0). See the installer log."
  ${EndIf}
  ;
  ; ===================================================================
  ; D4 INSTALL-SIDE STATE ESTABLISHMENT (migrated unchanged from
  ; nsis-hooks-native.nsh's NSIS_HOOK_POSTINSTALL; spec-installer-lifecycle.md
  ; D4)
  ; ===================================================================
  ; Runs AFTER the D2 re-verification above and the provisioning step just
  ; above have both passed (every failure branch above aborts the install via
  ; CIVICCAST_FAIL, so this point is only reached with a verified pack tree
  ; AND a provisioned/reused database). Implementation:
  ; native_service_registration.rs; the state each step establishes is
  ; tracked bidirectionally against its POSTUNINSTALL removal in
  ; native_uninstall.rs's NATIVE_D4_STATE_INVENTORY (see the
  ; wp2-d4-service-registration evidence file for the full table), and this
  ; file's own NSIS_HOOK_PREUNINSTALL / NSIS_HOOK_POSTUNINSTALL macros below
  ; already implement the matching D1/D4 uninstall-side half.
  !insertmacro CIVICCAST_STEP "step d4-service-registration: begin"
  DetailPrint "Registering the CivicCast (Native) supervisor service (D4)..."
  ; Tauri enters POSTINSTALL with $OUTDIR set to $INSTDIR, and its remaining
  ; File statements depend on that output directory. The registration child
  ; also walks $INSTDIR to grant service-account access; inheriting $INSTDIR
  ; as its current directory keeps the target tree open and can make that
  ; walk fail with a sharing violation. Give the child the installer scratch
  ; directory as cwd, then restore Tauri's output directory before continuing.
  InitPluginsDir
  StrCpy $R9 "$OUTDIR"
  SetOutPath "$PLUGINSDIR"
  ; Capture the tool's bounded output into the progress log as well as its
  ; exit code. ExecToLog only put diagnostics in the wizard pane, which is
  ; absent during silent setup and lost when failure recovery restores files.
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-register-native-service --install-root "$INSTDIR"'
  Pop $0
  Pop $1
  SetOutPath "$R9"
  !insertmacro CIVICCAST_STEP "step d4-service-registration: returned $0"
  !insertmacro CIVICCAST_STEP "step d4-service-registration: child reported: $1"
  ; pywin32's service install MOVES pythonservice.exe out of the payload's
  ; site-packages\win32 into the payload root (its own "moving host exe"
  ; step, unconditional). The payload pack ships the exe at BOTH paths, so
  ; the service's registered binary path (the root copy) is a first-class
  ; manifest member and can never dangle -- but the site-packages member is
  ; now missing, which would make the very next D5 verification report a
  ; repair. Restore it here so the installed tree stays byte-identical to
  ; the signed manifest. Proven necessary live: Sandbox matrix run 6 left
  ; the service registered but unstartable (StartService error 2) after D5
  ; repair normalized the mutated tree.
  ; NOTE the source path: pywin32 moves the exe to sys.exec_prefix, which for
  ; this payload is $INSTDIR\runtime (the service's registered binary path is
  ; $INSTDIR\runtime\pythonservice.exe -- confirmed in Sandbox run 10's
  ; sc qc snapshot). The original restore here copied from
  ; $INSTDIR\pythonservice.exe -- a path that has never existed -- and
  ; CopyFiles /SILENT swallowed the failure on every install, which is
  ; exactly why every clean install's first D5 verify reported 76/repaired
  ; (run 10: verify names the missing member
  ; runtime\Lib\site-packages\win32\pythonservice.exe). Guarded now: a
  ; missing source writes a distinct breadcrumb instead of silently doing
  ; nothing, so this can never regress invisibly again.
  ; task #50 (Sandbox runs 12+13, row 3): this restore is NOT exclusive to
  ; the install/upgrade chain anymore. D5 Repair calls
  ; native_service_registration::register_native_service directly,
  ; in-process, with no NSIS hook in that path at all -- so a copy of this
  ; same restore now lives INSIDE register_native_service itself
  ; (restore_service_host_site_packages_member, native_service_registration.rs),
  ; run immediately after every pywin32 install/update, covering repair too.
  ; This hook-side restore stays as-is (redundant but harmless on the
  ; install/upgrade path, which already gets it from the Rust seam as well).
  IfFileExists "$INSTDIR\runtime\pythonservice.exe" civiccast_svc_host_restore civiccast_svc_host_restore_missing
  civiccast_svc_host_restore:
  CopyFiles /SILENT "$INSTDIR\runtime\pythonservice.exe" "$INSTDIR\runtime\Lib\site-packages\win32\pythonservice.exe"
  !insertmacro CIVICCAST_STEP "step d4-service-registration: restored site-packages service host member"
  Goto civiccast_svc_host_restore_done
  civiccast_svc_host_restore_missing:
  !insertmacro CIVICCAST_STEP "step d4-service-registration: WARNING source $INSTDIR\runtime\pythonservice.exe missing; site-packages member NOT restored (next D5 verify will report a repair)"
  civiccast_svc_host_restore_done:
  ; Installer-path audit MA-29 / BL-11: this used to map ANY nonzero to
  ; ${CIVICCAST_EXIT_D4_SERVICE} with one fixed "could not register the ...
  ; Windows service" string. The subcommand emits three genuinely different
  ; outcomes and the operator remedy differs for each, so each gets its own
  ; installer exit code and its own sentence. The exit code is the only
  ; signal a silent install's support log carries about WHICH step failed --
  ; this file's own header says so at :374-377.
  ${If} $0 == 84
    DetailPrint "CivicCast (Native): the service is running but the station is not serving (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_SERVICE_NOT_SERVING} "CivicCast (Native) setup started the station's Windows service, but the station did not come up ready to serve.$\r$\n$\r$\nThe most common cause is that the database schema is not the one this version needs, which would make the staff pages return errors.$\r$\n$\r$\nNothing was deleted. Your recordings, database and settings in $COMMONPROGRAMDATA\CivicCast are intact.$\r$\n$\r$\nThe exact reason the station reported is in $COMMONPROGRAMDATA\CivicCast\install-progress.log, and the upgrade engine's own record is in $COMMONPROGRAMDATA\CivicCast\upgrade\upgrade-engine.log. Resolve the cause and run setup again."
  ${ElseIf} $0 == 83
    DetailPrint "CivicCast (Native): the service was registered but would not start (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_SERVICE_NOT_STARTING} "CivicCast (Native) setup registered the station's Windows service, but Windows could not start it.$\r$\n$\r$\nThis is a startup failure, not a registration failure -- the service exists and can be inspected in services.msc.$\r$\n$\r$\nNothing was deleted. See $COMMONPROGRAMDATA\CivicCast\logs and the Windows Application event log for the exact startup error, then run setup again."
  ${ElseIf} $0 != 0
    DetailPrint "CivicCast (Native): D4 service registration FAILED (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_SERVICE} "CivicCast (Native) setup could not register the CivicCast (Native) Windows service. See the installer log for the service tool's captured output."
  ${EndIf}
  !insertmacro CIVICCAST_STEP "step d4-firewall-rule: begin"
  DetailPrint "Registering the CivicCast (Native) portal/API firewall rule (D4)..."
  nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" --civiccast-register-native-firewall-rule --install-root "$INSTDIR"'
  Pop $0
  !insertmacro CIVICCAST_STEP "step d4-firewall-rule: returned $0"
  ${If} $0 != 0
    DetailPrint "CivicCast (Native): D4 firewall rule registration FAILED (exit $0) — see the installer log above."
    !insertmacro CIVICCAST_FAIL ${CIVICCAST_EXIT_D4_FIREWALL} "CivicCast (Native) setup could not create the required inbound firewall rule. See the installer log for the exact error."
  ${EndIf}
  ;
  ; ===================================================================
  ; HONEST ARP "Installed apps" SIZE (F-12 fix, 2026-08-01): Tauri's own
  ; EstimatedSize write (installer.nsi, inside Section Install, strictly
  ; BEFORE this hook runs -- same ordering already relied on by the
  ; QuietUninstallString write below) sums only what NSIS's own File
  ; statements embed in this section (~237 MB measured against this exact
  ; build: the bootstrap exe, the VC++ redistributable, the WebView2
  ; offline installer) -- it cannot see the native component packs the D2/
  ; D3/D4 chain above just staged into $INSTDIR via external nsExec calls,
  ; which is most of the real footprint (a walkthrough of this exact build
  ; measured the true total at 1,187,975,659 bytes / 10,274 files -- see
  ; FINDINGS-rewalk-dd7f835f.md F-10/F-12 and VERDICT-rewalk-dd7f835f.md).
  ; Rather than hardcode that one-time snapshot (which drifts the moment a
  ; pack's size changes), measure $INSTDIR for real, right here, after
  ; every pack has been staged and D2-verified and D4 provisioning/service/
  ; firewall registration has all succeeded -- this is what Windows'
  ; "Installed apps" size should have said all along, and it self-corrects
  ; on every future build without touching this file again.
  ;
  ; ${GetSize} is a FileFunc "artificial function" (FileFunc.nsh, already
  ; !include'd by installer.nsi before this file's own !include site -- the
  ; same precedent CIVICCAST_STEP's own header comment already documents
  ; for ${GetTime}): it only touches the registers it is explicitly Popped
  ; into ($0 size-in-KB, $1 file count, $2 dir count here), so $R0-$R3
  ; (the D3 old-version/DatabaseUrl state still needed below) are untouched
  ; by this call.
  ;
  ; Overwrites Tauri's own write the same way the QuietUninstallString
  ; write below already does: last write to this key in this Section wins.
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD SHCTX "${UNINSTKEY}" "EstimatedSize" "$0"
  !insertmacro CIVICCAST_STEP "postinstall: EstimatedSize corrected to $0 KB (measured $INSTDIR: $1 files, $2 dirs)"
  ; Record the version this fully-successful chain just installed. This is
  ; the D3 gate's prior-version signal on the NEXT run (see the gate comment
  ; above). Written HERE — after every D2/D3/D4 step has succeeded — because
  ; every failure branch above aborts the install outright (CIVICCAST_FAIL)
  ; and must leave the marker at its previous value (a failed upgrade did not
  ; change the installed version).
  ;
  ; Gate A run 33681670855 fix (2026-09-02): a clean D3 rollback (exit 10)
  ; no longer reaches this point at all -- it now aborts via CIVICCAST_FAIL
  ; under the flat installer layout this bootstrap always uses (see the
  ; exit==10 branch above for the full reasoning), so the write below is
  ; unconditional now (the former $R4 latch that used to gate it is retired).
  ; It stays keyed off a run that reached this line at all, which -- because
  ; every failure branch above aborts outright (CIVICCAST_FAIL) -- can only
  ; be a fully successful chain.
  ;
  ; Installer-path audit BL-12: WHY THIS LINE IS SAFE NOW, and was not.
  ;
  ; InstalledVersion records WHICH INSTALLER LAST RAN, and decide_route treats
  ; it as "which schema the database is at". Those are not the same fact. An
  ; uninstall removes the CivicCastSupervisor service but preserves
  ; $COMMONPROGRAMDATA\CivicCast and its cluster BY DESIGN, so a later install
  ; of a newer version routed FRESH_INSTALL (D3 skipped, exit 11), D4
  ; provisioning took its reuse/adopt path, and this line stamped the new
  ; version over a database still at the OLD schema. Every future run then
  ; read old_version == new_version and reported SAME_VERSION_NO_OP: the
  ; machine was permanently locked out of its own upgrade engine.
  ;
  ; Two changes make reaching this line mean what it says:
  ;   * the provisioning CLI's reuse path now brings the schema to alembic
  ;     head (BL-12, civiccast/native/provision/__main__.py), so a preserved
  ;     database is migrated on the FRESH_INSTALL route rather than adopted
  ;     and left behind; and
  ;   * D4 service registration now polls /health and REFUSES to report
  ;     success unless the station is serving with a current schema (BL-11,
  ;     exit 84 -> ${CIVICCAST_EXIT_D4_SERVICE_NOT_SERVING}). That step runs
  ;     BEFORE this write and aborts the install on failure, so the marker
  ;     can no longer be stamped over a schema-behind database.
  WriteRegStr HKLM "Software\CivicCast\Native" "InstalledVersion" "${VERSION}"
  DetailPrint "CivicCast (Native): recorded InstalledVersion ${VERSION} for the next install/upgrade run."
  !insertmacro CIVICCAST_STEP "postinstall: SUCCESS (InstalledVersion ${VERSION} recorded)"
  DetailPrint "CivicCast (Native) bootstrap install complete: required component packs staged and D2-verified, D3 install/upgrade engine run, PostgreSQL provisioned, service and firewall rule registered."
  ; Reaching here means SUCCESS and nothing else. The former shared
  ; `civiccast_bootstrap_postinstall_done` unwind label is gone: it existed
  ; only so failure branches could skip the InstalledVersion write and fall
  ; out of the macro quietly, which is exactly the behavior AUDIT-001 found
  ; (a failed install continuing into Tauri's unconditional success page).
  ; See the file header and wp2-d3-rehoming-2026-07-30.md for what is
  ; deliberately NOT wired above (the WP-6 embedded-payload defensive gate,
  ; the media-runtime-closure D2 check) and why.
  ;
  ; ===================================================================
  ; QuietUninstallString registration (P1, 2026-07-31 run-18 forensic
  ; diagnosis): makes the SetErrorLevel 82 uninstall refusal in
  ; NSIS_HOOK_PREUNINSTALL (see that macro) actually REACHABLE by an
  ; unattended caller (winget, Intune, a deployment script).
  ; ===================================================================
  ; MEASURED, not assumed: Tauri's own generated installer.nsi (this file's
  ; own !include site) registers ONLY UninstallString (installer.nsi:676,
  ; `"$INSTDIR\uninstall.exe"`, no `_?=` and no QuietUninstallString) and
  ; never writes a QuietUninstallString key anywhere in that file (confirmed
  ; by reading target/release/nsis/x64/installer.nsi in full -- grep for
  ; "QuietUninstallString" there returns nothing). With no `_?=`, NSIS's own
  ; exehead behavior (confirmed live by this product's makensis probe,
  ; .agent-runs/native-windows/ws5-installer/evidence/nsis-errorlevel-probe/)
  ; is: the invoked "$INSTDIR\uninstall.exe" process respawns a copy of
  ; itself into %TEMP%, launches THAT copy to do the real work, and the
  ; ORIGINAL process -- the one any waiting caller actually holds a handle
  ; to and reads the exit code from -- returns 0 as soon as the respawn is
  ; launched. That original-process exit therefore happens BEFORE
  ; NSIS_HOOK_PREUNINSTALL, the Uninstall section body, or this file's own
  ; SetErrorLevel 82 refusal (see that macro's header) ever run in the
  ; respawned copy. A caller driving UninstallString synchronously always
  ; observes exit 0, even on a refused or failed uninstall -- the exact
  ; unreachable-refusal defect this fix closes.
  ;
  ; `_?=$INSTDIR` disables that respawn: the uninstaller runs IN-PLACE,
  ; synchronously, in the SAME process the caller is waiting on, so its real
  ; exit code (including the 82 refusal) becomes observable. This is the
  ; documented NSIS tradeoff for `_?=` (it also means the uninstaller cannot
  ; self-delete -- see the P2 fix in NSIS_HOOK_POSTUNINSTALL below, which
  ; closes that side of the same tradeoff).
  ;
  ; Registered as QuietUninstallString, NOT an overwrite of UninstallString:
  ; Windows' own Apps & Features interactive "Uninstall" button reads
  ; UninstallString and must keep the normal (self-deleting, respawning)
  ; behavior for a human-driven uninstall; QuietUninstallString is the
  ; distinct, silent-caller-specific key that modern unattended deployment
  ; tooling (winget, Intune, System Center) prefers when present, leaving
  ; the interactive path completely unchanged.
  ;
  ; Written HERE -- the LAST statement of this macro, immediately before its
  ; closing, after
  ; every D2/D3/D4 step and the InstalledVersion write above have already
  ; run, and therefore also strictly AFTER Tauri's own UNINSTKEY writes at
  ; installer.nsi:670-689 (NSIS_HOOK_POSTINSTALL is inserted at
  ; installer.nsi:703-705, itself after those writes) -- so this key is not
  ; at risk of being overwritten by anything later in the Install section,
  ; and if a future Tauri version starts writing its own
  ; QuietUninstallString, THIS write runs last and wins (see
  ; tests/policy/test_native_installer_identity.py for the pin covering
  ; this). Uses SHCTX (not a hardcoded HKLM/HKCU) and ${UNINSTKEY} to match
  ; every other UNINSTKEY write in this file and in installer.nsi, so
  ; per-machine vs per-user install mode stays consistent automatically.
  WriteRegStr SHCTX "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S _?=$INSTDIR'
  !insertmacro CIVICCAST_STEP "postinstall: QuietUninstallString registered (_?=$INSTDIR)"
  ; Tauri's installer.nsi writes InstallLocation as '"$INSTDIR"' -- WITH
  ; embedded literal quotes inside the registry VALUE. Found on the first
  ; ever inspection of an installed machine (Sandbox lifecycle attempt 5,
  ; 2026-08-07): every consumer that treats the value as a path (inventory
  ; tooling, ops scripts, the lifecycle harness itself) silently resolves a
  ; nonexistent quoted path. Windows convention is the bare path. Same
  ; write-last-wins placement rationale as QuietUninstallString above.
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  !insertmacro CIVICCAST_STEP "postinstall: InstallLocation rewritten unquoted"
  ;
  ; ===================================================================
  ; Start Menu + Desktop shortcuts to the RUNNING STATION's own web
  ; surfaces (bug fix, field report 2026-08-28, candidate 9d4477b): once
  ; this setup wizard's own window closes, an operator had NO clickable
  ; path back to the operator console or the public portal at all. The
  ; setup app's finish screen does offer "Open operator console"
  ; (App.tsx's primaryActionLabel), but that control disappears with the
  ; window, and this installer creates no shortcut of any kind pointing at
  ; either surface -- Tauri's own generated Start Menu/Desktop entries (if
  ; any) point at "$INSTDIR\${MAINBINARYNAME}.exe", which is the SETUP
  ; WIZARD, not the running service; relaunching it re-runs first-run
  ; setup, it does not open the console.
  ;
  ; URLs are the SAME fixed literals `main.rs` hardcodes
  ; (`OPERATOR_CONSOLE_URL`, `RESIDENT_PORTAL_URL`). First setup is admitted
  ; purely by the control plane checking the request's peer IP is loopback
  ; (`civiccast/installer/router.py`'s `_require_local_setup_request`), so
  ; there is no query-string handoff scheme to reconcile with
  ; installer-state.json's operatorConsoleUrl at this point in the chain --
  ; the URL these shortcuts point at is always this same fixed literal, full
  ; stop.
  ; tests/policy/test_native_installer_identity.py pins both literals against
  ; `main.rs`'s own constants so the two can never silently drift apart.
  ;
  ; Internet Shortcut (.url) files, not .lnk: a .url file is a plain
  ; INI-format text file (WriteINIStr writes it directly, creating the file
  ; and section if either is missing) that needs no icon resource and no
  ; target executable to resolve -- both matter here, because this
  ; installer embeds exactly one icon (icons/icon.ico, used only for the
  ; installer/uninstaller binaries themselves per tauri.native.conf.json)
  ; and the shortcut's target is a URL, not a file on this machine. Windows
  ; opens a .url file in the system's default browser at click time,
  ; whatever that is.
  ;
  ; SetShellVarContext all: matches this product's fixed perMachine install
  ; mode (tauri.native.conf.json's "installMode": "perMachine", not
  ; operator-selectable) so $SMPROGRAMS/$DESKTOP resolve to the ALL USERS
  ; locations -- every operator on this station sees the same two entries,
  ; not just whichever account happened to run setup. Called explicitly
  ; (never assumed already in effect) so this block's correctness does not
  ; depend on undocumented behavior of Tauri's own generated installer.nsi.
  ;
  ; Best-effort, deliberately: unlike every CIVICCAST_FAIL step above, a
  ; shortcut that could not be written does not abort or alarm the
  ; install -- the station itself is fully installed and functional either
  ; way, and the operator can still reach both surfaces via the setup
  ; wizard's own finish screen. ClearErrors/${If} ${Errors} only logs a
  ; breadcrumb so a report of "no shortcuts" is diagnosable from
  ; install-progress.log without turning a cosmetic miss into a failed
  ; install.
  ; ===================================================================
  SetShellVarContext all
  CreateDirectory "$SMPROGRAMS\${PRODUCTNAME}"
  ClearErrors
  WriteINIStr "$SMPROGRAMS\${PRODUCTNAME}\CivicCast Operator Console.url" "InternetShortcut" "URL" "http://127.0.0.1:8000/operator/"
  ${If} ${Errors}
    !insertmacro CIVICCAST_STEP "postinstall: could not write the Start Menu operator console shortcut (non-fatal)"
    ClearErrors
  ${Else}
    !insertmacro CIVICCAST_STEP "postinstall: Start Menu operator console shortcut written"
  ${EndIf}
  WriteINIStr "$SMPROGRAMS\${PRODUCTNAME}\CivicCast Public Portal.url" "InternetShortcut" "URL" "http://127.0.0.1:8000/"
  ${If} ${Errors}
    !insertmacro CIVICCAST_STEP "postinstall: could not write the Start Menu public portal shortcut (non-fatal)"
    ClearErrors
  ${Else}
    !insertmacro CIVICCAST_STEP "postinstall: Start Menu public portal shortcut written"
  ${EndIf}
  WriteINIStr "$DESKTOP\CivicCast Operator Console.url" "InternetShortcut" "URL" "http://127.0.0.1:8000/operator/"
  ${If} ${Errors}
    !insertmacro CIVICCAST_STEP "postinstall: could not write the Desktop operator console shortcut (non-fatal)"
    ClearErrors
  ${Else}
    !insertmacro CIVICCAST_STEP "postinstall: Desktop operator console shortcut written"
  ${EndIf}
  !insertmacro CIVICCAST_TRANSACTION_COMMIT ${CIVICCAST_EXIT_INSTALL_RECOVERY}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; P3 instrumentation (2026-07-31 run-18 forensic diagnosis): the very first
  ; breadcrumb this macro writes, before any probe, prompt, or teardown call
  ; below. $EXEPATH is the running uninstall.exe's own image path (proves
  ; WHICH copy is executing -- the in-place `_?=` copy vs. a stray respawned
  ; %TEMP% copy from an un-fixed caller, see the P1 QuietUninstallString fix
  ; in NSIS_HOOK_POSTINSTALL) and $CMDLINE is its full invocation (proves
  ; whether a caller passed /S, _?=, or neither) -- both are the two honest,
  ; already-available signals that answer "how was this uninstall actually
  ; invoked" without guessing from downstream behavior.
  !insertmacro CIVICCAST_STEP "preuninstall: BEGIN pid-image=$EXEPATH cmdline=$CMDLINE"
  ; WP2: Rust owns state observation. It reads 64-bit HKLM ActiveRuntime and
  ; current-user WSL ARP directly, never localized reg.exe output. Exit 73 is
  ; the sole-active plan and has armed the product-owned POST marker.
  ;
  ; WP2 transfer-transaction (2026-07-30): exit 74 means the ONLY thing
  ; blocking removal is an un-acknowledged ActiveRuntime ownership transfer to
  ; the still-installed WSL product (spec-installer-lifecycle.md D1: "the
  ; operator to run the cutover/rollback transfer, offered as an explicitly
  ; acknowledged transaction from the uninstall UI, before removal proceeds").
  ; This MessageBox MB_YESNO is the only interactive surface available here
  ; and is the sole place this macro is allowed to prompt the operator. On
  ; Yes, the SAME executable is re-invoked with --acknowledge-transfer, which
  ; performs the write+read-back-verified ActiveRuntime transfer to "wsl"
  ; BEFORE this macro proceeds to stop the process / let removal continue. On
  ; No (or dismiss), nothing further runs: the uninstall aborts and
  ; ActiveRuntime is left completely untouched (native_uninstall.rs never
  ; even attempted a probe-triggering write for an unacknowledged call).
  DetailPrint "Checking CivicCast Native runtime ownership before uninstall..."
  nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-native-uninstall-preflight'
  Pop $0
  Pop $1
  ; Record what the preflight decided (including an inert WSL leftover
  ; warning) -- silent uninstalls have no details pane.
  !insertmacro CIVICCAST_STEP "preuninstall: ownership preflight returned $0: $1"
  ${If} $0 == 74
    MessageBox MB_YESNO|MB_ICONQUESTION "CivicCast (Native) is the active runtime and the WSL product (CivicCast Installer) is still installed.$\r$\n$\r$\nTo continue uninstalling CivicCast (Native), ActiveRuntime ownership must first be transferred to the WSL product, which will then become the active runtime that starts and transmits.$\r$\n$\r$\nTransfer ownership to the WSL product now and continue uninstalling CivicCast (Native)?" /SD IDNO IDYES civiccast_native_transfer_acknowledged
    DetailPrint "CivicCast Native uninstall was declined at the ownership transfer prompt; nothing was removed and ActiveRuntime was left unchanged."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) uninstall was cancelled. ActiveRuntime ownership was NOT transferred and nothing was removed."
    SetErrors
    ; Installer-path audit MA-34: this used to Abort with NO SetErrorLevel,
    ; so it returned NSIS's own generic script-abort code 2 -- the SAME code
    ; the other two refusal branches returned, and the same code NSIS emits
    ; for an unrelated script abort. This file's own comment beside the
    ; teardown refusal explains that exact ambiguity as the reason 82 was
    ; given its own code, and then left these three at 2.
    SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_DECLINED}
    Abort
    civiccast_native_transfer_acknowledged:
    DetailPrint "Operator acknowledged the ActiveRuntime ownership transfer; transferring to the WSL product before removal proceeds..."
    nsExec::ExecToStack '"$INSTDIR\CivicCast Native.exe" --civiccast-native-uninstall-preflight --acknowledge-transfer'
    Pop $0
    Pop $1
    ${If} $0 != 0
      DetailPrint "CivicCast Native ownership transfer failed after acknowledgment: $1"
      !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not complete the ActiveRuntime ownership transfer. Nothing was removed.$\r$\n$\r$\nDetails: $1"
      SetErrors
      ; Installer-path audit MA-34: its own code, not NSIS's generic 2.
      SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_TRANSFER_FAILED}
      Abort
    ${EndIf}
    DetailPrint "CivicCast Native ActiveRuntime ownership transferred to the WSL product; proceeding with uninstall."
  ${ElseIf} $0 != 0
  ${AndIf} $0 != 73
    DetailPrint "CivicCast Native uninstall was blocked before process termination: $1"
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) cannot be uninstalled while it is the active runtime and the WSL product remains, or when lifecycle state cannot be safely read.$\r$\n$\r$\nDetails: $1"
    SetErrors
    ; Installer-path audit MA-34: its own code, not NSIS's generic 2.
    SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_BLOCKED}
    Abort
  ${EndIf}
  ; <installer-path-audit MA-36> Exit 73 is the preflight's "I armed the
  ; post-uninstall selector-clear plan" answer. Record it so POSTUNINSTALL can
  ; tell a plan that was never armed from one that was armed and then removed
  ; by a second uninstaller instance.
  ${If} $0 == 73
    StrCpy $CIVICCAST_POSTCLEAR_ARMED "1"
    !insertmacro CIVICCAST_STEP "preuninstall: post-uninstall selector-clear plan armed"
  ${EndIf}
  ;
  ; ===================================================================
  ; BLOCKER fix (2026-07-30 Sandbox audit; moved from POSTUNINSTALL): the
  ; teardown CLI invokes "$INSTDIR\CivicCast Native.exe" itself -- but
  ; Tauri's generated uninstall Section deletes that exact file
  ; (installer.nsi:756, `Delete "$INSTDIR\${MAINBINARYNAME}.exe"`) BEFORE
  ; NSIS_HOOK_POSTUNINSTALL ever runs (installer.nsi:838, the
  ; `!insertmacro NSIS_HOOK_POSTUNINSTALL` call site). A teardown call left
  ; in POSTUNINSTALL can therefore never execute the exe it depends on --
  ; nsExec::ExecToLog just fails to launch a file that no longer exists --
  ; so the service was never stopped, the firewall rule was never removed,
  ; and the registry state was never cleared, while the uninstall still
  ; reported exit 0 (Sandbox run 7 live evidence: CivicCastSupervisor still
  ; registered, the TCP 8000 firewall rule still open, 12,145 files left
  ; behind).
  ;
  ; EVIDENCE the exe still exists at THIS point: NSIS_HOOK_PREUNINSTALL is
  ; inserted at installer.nsi:748-750, strictly BEFORE the Delete at
  ; installer.nsi:756 -- and the ownership preflight call two blocks above
  ; (in this same macro, running strictly before this point) already
  ; depends on and successfully invokes that exact exe, proving it is still
  ; present here.
  ;
  ; ORDERING (deliberate): runs AFTER the ownership preflight above --
  ; including its MB_YESNO prompt and BOTH Abort paths (decline, and a
  ; failed post-acknowledgment transfer) -- because a cancelled or failed
  ; uninstall must still be able to abort the WHOLE operation before any
  ; state is torn down; tearing the service down and THEN aborting would
  ; leave a station broken by a cancelled operation. Runs BEFORE the
  ; pre-existing taskkill below, for the same reason the ownership preflight
  ; already runs before taskkill: the running process should be asked to
  ; tear itself down cleanly (stop its own service, remove its own firewall
  ; rule) before anything forces it to exit.
  ;
  ; The recursive removal of $INSTDIR\runtime / $INSTDIR\packs / $INSTDIR
  ; itself STAYS in POSTUNINSTALL (it must run after Tauri's own file
  ; deletion there, which only happens after this Section's Uninstall body
  ; runs past this hook) -- see that macro. Its exit code is carried there
  ; via the $CIVICCAST_TEARDOWN_EXIT file-scope Var declared above.
  ; ===================================================================
  !insertmacro CIVICCAST_STEP "preuninstall: teardown native state (service/firewall/registry): begin"
  DetailPrint "Removing the CivicCast (Native) supervisor service, firewall rule, and registry state..."
  nsExec::ExecToLog '"$INSTDIR\CivicCast Native.exe" --civiccast-teardown-native-state --install-root "$INSTDIR"'
  Pop $0
  StrCpy $CIVICCAST_TEARDOWN_EXIT $0
  !insertmacro CIVICCAST_STEP "preuninstall: teardown native state returned $0"
  ;
  ; ===================================================================
  ; RECOVERABLE FAIL-CLOSED (2026-07-31, gauntlet run 17): a nonzero teardown
  ; used to be recorded here and the uninstall ALLOWED TO CONTINUE, with the
  ; refusal deferred to NSIS_HOOK_POSTUNINSTALL's tree-retention gate. That
  ; ordering is unrecoverable, and run 17 proved it on a live machine:
  ;
  ;   * PREUNINSTALL records exit 82 (service stop could not be confirmed);
  ;   * Tauri's generated uninstall Section then deletes
  ;     "$INSTDIR\CivicCast Native.exe" (installer.nsi:756) and the uninstaller
  ;     itself, plus the shortcuts and the Add/Remove Programs entry;
  ;   * only THEN does POSTUNINSTALL (installer.nsi:838) refuse to remove the
  ;     trees.
  ;
  ; The machine is left with a preserved multi-gigabyte tree, a still-running
  ; service, and NO product exe and NO uninstaller -- so neither
  ; --civiccast-repair nor a second Uninstall can be run, and the former
  ; PREINSTALL policy (exit 120, which keyed on that exact exe and on the
  ; still-registered service) then refused every future install.
  ; A retention gate whose own precondition destroys the tools needed to act on
  ; it is not fail-closed; it is a dead end.
  ;
  ; Fix: refuse HERE, before anything is deleted. Same shape as this macro's
  ; ownership-preflight refusal branches above (CIVICCAST_ALERT + SetErrors +
  ; Abort) -- not CIVICCAST_FAIL, which is the POSTINSTALL failure vocabulary
  ; and carries a SetErrorLevel from the install-side code table. Aborting from
  ; PREUNINSTALL leaves the machine FULLY INTACT: exe, uninstaller, ARP entry,
  ; service, and trees all still present, so the operator can stop the service
  ; and run Uninstall again, and every code path that ever cleans this machine
  ; still exists.
  ;
  ; ALL nonzero codes abort, not just 82. POSTUNINSTALL's narrower gate (82
  ; refuses tree removal; a generic 80 does not) was correct for the question
  ; IT answers -- "is deleting the trees dangerous?" -- because a leftover
  ; firewall rule is no reason to strand gigabytes. This gate answers a
  ; different question: "did the uninstall do what it said?" A teardown that
  ; failed to remove the service or the firewall rule did not, and continuing
  ; would delete the only exe that can retry it. Nothing is stranded by
  ; refusing here, because nothing has been removed yet.
  ;
  ; POSTUNINSTALL's gate STAYS as defense in depth: it still runs on the exit-0
  ; path, still fails closed on an unset Var, and still covers a hand-edited or
  ; foreign hook file where this Abort was not reached.
  ; ===================================================================
  ${If} $CIVICCAST_TEARDOWN_EXIT != 0
    !insertmacro CIVICCAST_STEP "preuninstall: REFUSED (teardown exit $CIVICCAST_TEARDOWN_EXIT) -- aborting before anything is removed"
    DetailPrint "CivicCast (Native) uninstall aborted: the teardown step returned exit $CIVICCAST_TEARDOWN_EXIT and nothing has been removed."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not be uninstalled, so the uninstall was ABORTED and NOTHING was removed -- your installation is still complete and can be uninstalled again once the problem below is cleared.$\r$\n$\r$\nThe teardown step (stopping and removing the CivicCastSupervisor service, removing the firewall rule, clearing registry state) returned exit $CIVICCAST_TEARDOWN_EXIT.$\r$\n$\r$\nMost often this means the supervisor service did not stop. To finish: stop it manually (services.msc, or 'sc stop CivicCastSupervisor'), or reboot this machine, then run Uninstall again.$\r$\n$\r$\nSee the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log for the exact step that failed."
    SetErrors
    ; DISTINCT EXIT CODE (2026-07-31, F5a). Without this, an Abort here returns
    ; the generic NSIS script-abort code 2 -- the SAME code this macro's
    ; ownership-DECLINE aborts return, so an unattended uninstall could not tell
    ; "the operator said no" from "the teardown failed and the machine still has
    ; a live service". 82 is the teardown CLI's own "service stop could not be
    ; confirmed" code, reused here as the uninstall-refusal code: fixed, not
    ; $CIVICCAST_TEARDOWN_EXIT, because a passthrough could emit 1 or 2 (NSIS's
    ; own reserved codes) for some other teardown failure and reintroduce the
    ; ambiguity. WHICH nonzero teardown code actually fired is in the step
    ; breadcrumb + the alert text above, which is where a diagnosis belongs.
    ;
    ; SetErrorLevel-then-Abort is MEASURED, not assumed: the in-repo probe
    ; .agent-runs/native-windows/ws5-installer/evidence/nsis-errorlevel-probe/
    ; (RESULTS.md) ran this exact ordering on this product's own makensis and
    ; got exit 41 for level 41 -- the custom code survives the Abort (Abort
    ; alone returned 2). So this returns 82.
    SetErrorLevel 82
    Abort
  ${EndIf}
  ;
  DetailPrint "Stopping the CivicCast Native bootstrap after ownership preflight..."
  nsExec::ExecToLog 'taskkill.exe /IM "CivicCast Native.exe" /T /F'
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; BLOCKER fix (2026-07-30 Sandbox audit): this macro previously did ONLY the
  ; ActiveRuntime selector bookkeeping below, so a completed uninstall left
  ; the CivicCastSupervisor service registered, the portal/API firewall rule
  ; open, and the Native registry values in place -- while reporting exit 0 /
  ; success.
  ;
  ; RELOCATION (2026-07-30, second Blocker on the same fix): the teardown
  ; invocation itself was originally placed HERE, but Tauri's generated
  ; uninstall Section deletes "$INSTDIR\${MAINBINARYNAME}.exe" (installer.nsi:
  ; 756) BEFORE this macro is ever inserted (installer.nsi:838), so a
  ; teardown call here could never actually run the exe it depends on -- the
  ; entire teardown (service stop, service removal, firewall rule removal)
  ; silently never executed while the uninstall still reported exit 0. The
  ; invocation now lives in NSIS_HOOK_PREUNINSTALL (see that macro's header
  ; comment for the full evidence), which runs BEFORE installer.nsi:756 --
  ; while the exe still exists. Its exit code is carried here via the
  ; $CIVICCAST_TEARDOWN_EXIT file-scope Var (declared near the top of this
  ; file) rather than a fresh nsExec call, because the recursive removal gate
  ; below MUST stay in POSTUNINSTALL -- it can only run after Tauri's own
  ; file deletion above has completed.
  ;
  ; If $CIVICCAST_TEARDOWN_EXIT is still empty here, PREUNINSTALL somehow did
  ; not run (e.g. a hand-edited or foreign hook file); default it to "82" --
  ; the same code as "service stop could not be confirmed" -- so the
  ; recursive removal below fails CLOSED instead of assuming it is safe.
  ${If} $CIVICCAST_TEARDOWN_EXIT == ""
    StrCpy $CIVICCAST_TEARDOWN_EXIT "82"
    !insertmacro CIVICCAST_STEP "postuninstall: CIVICCAST_TEARDOWN_EXIT was unset (PREUNINSTALL did not run) -- defaulting to 82 (fail closed)"
  ${EndIf}
  !insertmacro CIVICCAST_STEP "postuninstall: teardown native state result carried from preuninstall: $CIVICCAST_TEARDOWN_EXIT"
  ; CRITICAL fix (2026-07-30 adversarial review): $R2 latches whether the
  ; recursive RMDir block below (the ONLY thing that deletes $INSTDIR\runtime
  ; / $INSTDIR\packs / $INSTDIR itself) is safe to run. Exit 82 is the ONE
  ; teardown outcome that makes it unsafe: native_service_registration::
  ; teardown_exit_code (main.rs's --civiccast-teardown-native-state) returns
  ; 82 SPECIFICALLY when the "stop service" step could not confirm the
  ; CivicCastSupervisor service actually stopped -- meaning its
  ; pythonservice.exe and long-lived postgres.exe child may
  ; still be running FROM those trees. Deleting the tree underneath them is
  ; the exact hazard the install/repair side already closed
  ; (native_pack_staging::ensure_pack_extracted requires a
  ; TreeRebuildAuthority proving the service is stopped before it may delete
  ; an extracted tree) -- this closes the same gap on the uninstall path,
  ; which used raw NSIS RMDir and bypassed that seam entirely.
  ;
  ; Deliberately narrow: teardown returns the generic 80 for a "remove
  ; service" or "delete firewall rule" failure too, and those do NOT set this
  ; latch -- the service is confirmed stopped in that case, so refusing tree
  ; removal over a leftover firewall rule would strand a multi-gigabyte tree
  ; for no safety reason. Only 82 refuses; every other nonzero code keeps the
  ; prior behavior (alert, continue, still remove the trees).
  StrCpy $R2 "0"
  ${If} $CIVICCAST_TEARDOWN_EXIT == 82
    StrCpy $R2 "1"
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) uninstall could not confirm that the CivicCastSupervisor service was fully stopped, so the program files were NOT removed -- deleting them now could corrupt data out from under a still-running service and its database/messaging processes.$\r$\n$\r$\nTo finish removing CivicCast (Native): stop the service manually (services.msc, or 'sc stop CivicCastSupervisor'), or reboot this machine, then run Uninstall again.$\r$\n$\r$\nSee the installer log at $COMMONPROGRAMDATA\CivicCast\install-progress.log for the exact error."
    SetErrors
    ; Installer-path audit MA-35: SetErrors ALONE DOES NOT CHANGE THE
    ; PROCESS EXIT CODE -- this file's own measured evidence table says so.
    ; An uninstall that deliberately left multi-gigabyte trees AND a running
    ; service behind therefore returned 0 to winget/Intune, which read it as
    ; a clean removal. Reachable today mainly through the fail-closed
    ; unset-Var default above -- which exists precisely because PREUNINSTALL
    ; might not have run at all.
    SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_TREES_RETAINED}
  ${ElseIf} $CIVICCAST_TEARDOWN_EXIT != 0
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) uninstall could not fully remove the supervisor service, firewall rule, and/or registry state (exit $CIVICCAST_TEARDOWN_EXIT). See the installer log for exactly which step failed; you may need to remove it manually (services.msc / Windows Defender Firewall / HKLM\Software\CivicCast\Native)."
    ; Installer-path audit MA-35: the same shape as the branch above -- alert,
    ; continue, delete the trees, exit 0. An uninstall that could not remove
    ; the service, the firewall rule or the registry state has NOT completed,
    ; and unattended tooling has to be able to see that.
    SetErrors
    SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_INCOMPLETE}
  ${Else}
    DetailPrint "CivicCast (Native): supervisor service, firewall rule, and registry state removed."
  ${EndIf}
  ;
  ; Never clear the selector in PREUNINSTALL. A verified, product-owned marker
  ; proves the preflight observed Native as sole active owner. Re-check the
  ; selector immediately before deletion, then remove and verify the marker.
  SetRegView 64
  ClearErrors
  ReadRegStr $R0 HKLM "Software\CivicCast" "NativeUninstallPostclearPending"
  ${If} ${Errors}
    ; <installer-path-audit MA-36> "No marker" is TWO different states, and
    ; this branch used to treat them as one.
    ;
    ; If this run's own preflight never armed a plan, leaving ActiveRuntime
    ; untouched is exactly right and silent -- unchanged.
    ;
    ; If it DID arm one (exit 73, recorded in $CIVICCAST_POSTCLEAR_ARMED) and
    ; the marker is gone now, something removed it mid-uninstall. The only
    ; thing that does is a SECOND uninstaller instance, whose preflight begins
    ; with an unconditional clear_postclear_marker(). The consequence is not
    ; cosmetic: ActiveRuntime stays "native" on a machine with no native
    ; product, which is the state the dual-runtime guard reads to decide
    ; whether anything may start. Say so, and do not report a clean uninstall.
    ${If} $CIVICCAST_POSTCLEAR_ARMED == "1"
      !insertmacro CIVICCAST_STEP "postuninstall: ARMED post-uninstall plan is MISSING (a second uninstaller instance cleared it); ActiveRuntime left unchanged"
      DetailPrint "CivicCast (Native): the post-uninstall ActiveRuntime plan armed by this uninstall is gone; ActiveRuntime was left unchanged."
      !insertmacro CIVICCAST_ALERT "CivicCast (Native) was removed, but it could not finish handing back this computer's CivicCast runtime ownership -- another CivicCast uninstall was started while this one was running and cleared the plan.$\r$\n$\r$\nThis machine's HKLM\Software\CivicCast\ActiveRuntime still reads $\"native$\" even though the native product is gone. Nothing was left running and no data was lost.$\r$\n$\r$\nAn administrator should clear or correct that value before installing CivicCast again. Do not run two CivicCast uninstalls at the same time."
      SetErrors
      SetErrorLevel ${CIVICCAST_EXIT_UNINSTALL_INCOMPLETE}
    ${EndIf}
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ${If} $R0 != "civiccast-native-sole-active-v1"
    DetailPrint "CivicCast Native post-uninstall marker is malformed; ActiveRuntime was left unchanged."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not verify its uninstall lifecycle marker. ActiveRuntime was left unchanged; inspect HKLM\Software\CivicCast before retrying."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ClearErrors
  ReadRegStr $R1 HKLM "Software\CivicCast" "ActiveRuntime"
  ${If} ${Errors}
    DetailPrint "CivicCast Native could not re-read ActiveRuntime; selector was left unchanged."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not re-read ActiveRuntime after uninstall. The selector was left unchanged; inspect HKLM\Software\CivicCast."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ${If} $R1 != "native"
    DetailPrint "ActiveRuntime changed after preflight; selector was left unchanged."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) detected that ActiveRuntime changed after uninstall preflight. The selector was left unchanged; inspect HKLM\Software\CivicCast."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ClearErrors
  DeleteRegValue HKLM "Software\CivicCast" "ActiveRuntime"
  ${If} ${Errors}
    DetailPrint "CivicCast Native could not clear ActiveRuntime; the pending marker was retained."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not clear ActiveRuntime. The pending marker was retained for diagnosis; inspect HKLM\Software\CivicCast."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ClearErrors
  ReadRegStr $R1 HKLM "Software\CivicCast" "ActiveRuntime"
  ${IfNot} ${Errors}
    DetailPrint "CivicCast Native could not verify ActiveRuntime removal; the pending marker was retained."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not verify ActiveRuntime removal. The pending marker was retained for diagnosis; inspect HKLM\Software\CivicCast."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ClearErrors
  DeleteRegValue HKLM "Software\CivicCast" "NativeUninstallPostclearPending"
  ${If} ${Errors}
    DetailPrint "CivicCast Native cleared ActiveRuntime but could not remove the pending marker."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) cleared ActiveRuntime but could not remove its pending marker; inspect HKLM\Software\CivicCast."
    SetErrors
    Goto civiccast_native_postuninstall_done
  ${EndIf}
  ClearErrors
  ReadRegStr $R0 HKLM "Software\CivicCast" "NativeUninstallPostclearPending"
  ${IfNot} ${Errors}
    DetailPrint "CivicCast Native could not verify pending-marker removal."
    !insertmacro CIVICCAST_ALERT "CivicCast (Native) could not verify pending-marker removal; inspect HKLM\Software\CivicCast."
    SetErrors
  ${EndIf}
  civiccast_native_postuninstall_done:
  ;
  ; BLOCKER fix, continued: Tauri's own generated uninstall Section only
  ; deletes the specific files it tracked from the install manifest, then
  ; calls a NON-recursive RMDir on each directory (which only succeeds if the
  ; directory is already empty) -- it has no entry for the multi-gigabyte
  ; $INSTDIR\runtime and $INSTDIR\packs trees this bootstrap extracts
  ; DYNAMICALLY from signed .ccpack files at install time (never through
  ; File/Delete instructions Tauri's uninstaller could enumerate), so those
  ; trees -- 12,145 files in the audited Sandbox run -- were silently left
  ; behind on every uninstall while it still reported exit 0/success. Runs
  ; HERE, after the selector bookkeeping above has fully completed (so a
  ; teardown/removal failure never races the selector's own re-reads of
  ; $INSTDIR-adjacent state), and recursively removes exactly the product-
  ; owned trees this bootstrap itself lays down.
  ${If} $R2 == "1"
    DetailPrint "CivicCast (Native): skipping removal of the runtime and component-pack trees -- the supervisor service could not be confirmed stopped (see the alert above)."
    !insertmacro CIVICCAST_STEP "postuninstall: recursive removal of runtime/packs/INSTDIR: SKIPPED (service stop unconfirmed, teardown exit 82)"
  ${Else}
    ; Installer-path audit MA-17 -- KNOWN LIMITATION, stated rather than
    ; silently shipped. See the CHANGELOG's "Known limitations" entry.
    ;
    ; `RMDir /r "$INSTDIR\packs"` removes `$INSTDIR\packs\.station-cache`
    ; along with everything else. That is correct for an uninstall in
    ; isolation, but it means a DOWNLOAD-ONLY reinstall on this machine
    ; (setup.exe alone, no `station\` folder) cannot activate afterwards: the
    ; signed station index is embedded in setup.exe, the ~21 GB of model packs
    ; are not, and the per-SHA cache they would have been served from is now
    ; gone -- so activation exits 66 and the installer aborts with 123. That
    ; collides with the owner's standing "download install is the floor" rule.
    ;
    ; NOT FIXED HERE, deliberately. Preserving the cache means either leaving
    ; a multi-gigabyte `$INSTDIR` behind (which contradicts this file's own
    ; "everything gone" uninstall contract and changes what a silent uninstall
    ; reports to winget/Intune) or relocating the cache to
    ; $COMMONPROGRAMDATA -- which additionally requires the activation step's
    ; `--cache-root` to search a second location (audit MA-16). Both are owner
    ; decisions about what an uninstall means, not batch-fix decisions. What
    ; is fixed here is that the operator is TOLD, on every path, instead of
    ; discovering it at the next install.
    !insertmacro CIVICCAST_NOTICE "CivicCast (Native) is being removed, including the downloaded AI model packs (about 21 GB) kept in this folder.$\r$\n$\r$\nYour recordings, database and settings in $COMMONPROGRAMDATA\CivicCast are NOT affected and are being kept.$\r$\n$\r$\nIf you reinstall later by running setup.exe on its own, it will need those model packs again and cannot download them -- reinstall from the full CivicCast kit folder (setup.exe together with its station folder), or copy $INSTDIR\packs\.station-cache somewhere safe now if you want to reuse it."
    DetailPrint "Removing the CivicCast (Native) runtime and component-pack trees..."
    !insertmacro CIVICCAST_STEP "postuninstall: recursive removal of runtime/packs/INSTDIR: begin (INCLUDING the per-SHA model pack cache -- see audit MA-17)"
    RMDir /r "$INSTDIR\runtime"
    RMDir /r "$INSTDIR\packs"
    RMDir /r "$INSTDIR"
    ;
    ; P2 fix (2026-07-31 run-18 forensic diagnosis): the QuietUninstallString
    ; registered in NSIS_HOOK_POSTINSTALL above (`_?=$INSTDIR`) is what makes
    ; the SetErrorLevel 82 refusal above reachable by a waiting caller (see
    ; that macro's header) -- but `_?=` carries a documented NSIS tradeoff:
    ; the uninstaller then runs IN-PLACE instead of a respawned %TEMP% copy,
    ; so it can never self-delete. Tauri's own generated uninstall Section
    ; already tries (installer.nsi:769 `Delete "$INSTDIR\uninstall.exe"`,
    ; installer.nsi:771 `RMDir "$INSTDIR"` non-recursive) -- both silently
    ; no-op on the `_?=` path because the running exe cannot delete itself
    ; and a non-empty directory containing it cannot be removed by a plain
    ; RMDir -- and the RMDir /r "$INSTDIR" immediately above this comment
    ; hits the exact same wall for the same reason: `_?=` keeps
    ; uninstall.exe running from inside $INSTDIR for the whole duration of
    ; this Section, including this recursive delete, so it is always the one
    ; file RMDir /r cannot touch. MEASURED, not assumed: a live `_?=` run
    ; left exactly one file behind (filesLeft=1) -- uninstall.exe itself,
    ; with an empty-but-undeletable $INSTDIR around it.
    ;
    ; Fix: schedule BOTH for deletion on next reboot. `/REBOOTOK` is NSIS's
    ; standard mechanism for exactly this case (MoveFileEx with
    ; MOVEFILE_DELAY_UNTIL_REBOOT under the hood) -- it cannot delete the
    ; running exe or its now-should-be-empty parent directory immediately,
    ; but schedules the OS to do so at the next boot, after the process has
    ; exited. GUARDED by FileExists (2026-07-31 audit findings L3 + E7):
    ; unguarded, (a) `RMDir /REBOOTOK` on an already-removed $INSTDIR can
    ; still register a PendingFileRenameOperations entry on EVERY ordinary
    ; uninstall (NSIS EW_RMDIR calls MoveFileOnReboot whenever
    ; RemoveDirectory fails, with no existence check -- fleet tooling reads
    ; that registry value as "reboot pending"), and (b) a pending
    ; delete-on-reboot for uninstall.exe outlives an uninstall->reinstall
    ; cycle and would delete the NEW uninstaller at the next boot, leaving
    ; an unremovable product. The guard makes both calls true no-ops on the
    ; ordinary (non-`_?=`) path where Tauri's own Delete/RMDir at
    ; installer.nsi:769/771 already removed both. Order matters: the file
    ; must be scheduled before its now-empty parent directory, matching
    ; `RMDir /r`'s own child-then-parent convention immediately above.
    ${If} ${FileExists} "$INSTDIR\uninstall.exe"
      Delete /REBOOTOK "$INSTDIR\uninstall.exe"
      RMDir  /REBOOTOK "$INSTDIR"
    ${EndIf}
    !insertmacro CIVICCAST_STEP "postuninstall: recursive removal of runtime/packs/INSTDIR: done"
    ;
    ; ===================================================================
    ; F-01, UNINSTALLER HALF (2026-08-01 sandbox newcomer re-walk dd7f835f):
    ; a completed uninstall must leave NOTHING claiming a product is
    ; installed. Two things make that claim. The first is
    ; HKLM\Software\CivicCast\Native\InstalledVersion, now cleared by the
    ; teardown CLI's "clear install markers" step
    ; (native_service_registration::delete_native_install_marker_values) --
    ; that one is what the re-walk actually caught, and it is what made the
    ; next install run the D3 upgrade engine against a product that was not
    ; there. The second is this one: Tauri's InstallDirRegKey.
    ;
    ; Tauri derives NSIS's ${MANUFACTURER} from the bundle identifier's
    ; second segment when no explicit publisher is configured --
    ; tauri.native.conf.json's "org.civiccast.native" -> "civiccast", which
    ; is also why this product's Publisher renders lowercase -- so the key is
    ; Software\civiccast\CivicCast (Native) and its DEFAULT VALUE is the
    ; install path. It is what Tauri's own reinstall page reads to decide a
    ; machine is "Already Installed".
    ;
    ; Tauri's generated uninstall Section does emit its own DeleteRegKey for
    ; this key, and this removal is deliberately REDUNDANT with it rather
    ; than a replacement -- DeleteRegKey on an absent key is a silent no-op,
    ; so the redundancy costs nothing. It is here because that expectation
    ; has already failed once in this product family: the retired WSL
    ; product's hook file (nsis-hooks.nsh, removed under the owner's "no
    ; linux" decision) carried an explicit "rc13 lifecycle repair" doing
    ; exactly this deletion for its own InstallDirRegKey, with the comment
    ; "Leaving it behind after uninstall produces a false 'Already Installed'
    ; page on an otherwise clean host".
    ;
    ; PLACEMENT is load-bearing, not cosmetic: inside the ${Else} arm of the
    ; $R2 tree-retention gate, so it runs ONLY on the path that actually
    ; removed $INSTDIR\runtime / $INSTDIR\packs / $INSTDIR. On the retained-
    ; tree path (service stop unconfirmed) the product IS still installed and
    ; this key must keep saying so -- erasing it there would let the next
    ; install run straight over a live tree, which is the same class of
    ; defect as the leftover this block removes, pointed the other way.
    ;
    ; /ifempty on the parent Software\civiccast is DELIBERATELY NOT DONE:
    ; the registry is case-insensitive, so that is the SAME key as
    ; HKLM\Software\CivicCast, which holds the ActiveRuntime selector and the
    ; Maintenance interlock blob this file's own postclear protocol above
    ; owns end to end. /ifempty would be a no-op while those exist and a
    ; cross-protocol surprise the moment they do not.
    ; ===================================================================
    SetRegView 64
    DeleteRegKey HKLM "Software\civiccast\CivicCast (Native)"
    !insertmacro CIVICCAST_STEP "postuninstall: removed the Tauri InstallDirRegKey (HKLM\Software\civiccast\CivicCast (Native)) so a later install cannot see this machine as Already Installed"
  ${EndIf}
  ; $COMMONPROGRAMDATA\CivicCast is deliberately NEVER touched by this
  ; removal, or by anything else in this macro: it holds product-owned data
  ; (the PostgreSQL cluster, upgrade/provision journals, and operator
  ; recovery documents) that is preserved across uninstall by design. Purge
  ; remains a separate, typed operator action in the lifecycle implementation,
  ; not something a normal uninstall ever performs.
  ;
  ; Start Menu + Desktop shortcut removal (bug fix, field report 2026-08-28,
  ; candidate 9d4477b): the counterpart to POSTINSTALL's shortcut creation
  ; above. Deliberately OUTSIDE the $R2 tree-retention gate above (runs
  ; unconditionally, on EVERY uninstall, even one that skipped removing
  ; $INSTDIR because the supervisor service could not be confirmed stopped):
  ; unlike the runtime/packs trees, a Start Menu or Desktop shortcut is
  ; state this product owns independently of $INSTDIR and of whether the
  ; service is still running -- deleting a shortcut file carries none of
  ; the "still-running process holding the tree open" hazard that gate
  ; exists to guard against, so there is no safety reason to withhold it on
  ; that path, and every reason not to: a shortcut surviving a failed
  ; uninstall would point an operator at a station this machine may no
  ; longer be running as a service at all.
  SetShellVarContext all
  Delete "$SMPROGRAMS\${PRODUCTNAME}\CivicCast Operator Console.url"
  Delete "$SMPROGRAMS\${PRODUCTNAME}\CivicCast Public Portal.url"
  RMDir "$SMPROGRAMS\${PRODUCTNAME}"
  Delete "$DESKTOP\CivicCast Operator Console.url"
  !insertmacro CIVICCAST_STEP "postuninstall: Start Menu + Desktop shortcuts removed"
!macroend
