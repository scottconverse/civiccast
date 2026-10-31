# Cross-machine pickup — October 31, 2026

Repository: https://github.com/scottconverse/civiccast-native
Feature branch: `feat/local-audio-description`
Feature implementation commit: `43f6080fd0dd6b48e9deb615a17b1cf99b7dbf56`.
Use the latest feature-branch HEAD for this handoff update. This branch is separate
from the release candidate; do not merge into or change the RC as part of pickup.

Scott has handed this work to the CivicCast coder on another machine. Delivery
is the feature branch only; no merge, tag, release or production activation.
The earlier local-only delivery statements below describe the prior checkpoint.

Start with `docs/ops/evidence/audio-description-film-fixes.md`, then the source
under `civiccast/audio_description` and the public portal `RecordingPlayer.tsx`.
All code, regression tests, lockfile, operational guide, manual source and generated
built-in Help are committed. Machine-local evidence paths and localhost preview
URLs below belong to the original Windows machine and will not work remotely.
Raw test media, model weights, output MP4s, detailed provider traces and local
fixture servers are not part of this source handoff. The committed evidence
summary records what was run and its limits; it is historical evidence, not a
claim that the receiving machine has run the checks.

Remaining product limitation: visual agreement is intentionally conservative and
omits substantial action, particularly Bunny. Comprehensive AD coverage and WCAG
compliance have not been established. Scott requested no further work from this
session; the receiving coder should take over from this state.

---
# Audio-description film corrections — October 31, 2026

Completed locally on `feat/local-audio-description` in the independent clone
`C:\Users\scott\Desktop\Code\civiccast-audio-description`. No RC checkout, live
station data, provider/default model, release version, installer or remote refs changed.

Fixed the five observed film issues: guarded current-frame visual reads and
uncertainty; conservative repetition consolidation; pinned offline YAMNet speech
protection with music ducking; queued pause cues that cannot be missed between
sparse browser updates; bounded Gemma lease and explicit unload on job exit.
Shortening preserves headline attribution and broadens speculative object labels.

Verified on the same three 120-second excerpts: Tears 2 in-gap/6 pause descriptions;
Sintel 1/4; Bunny 2/0. All six MP4s decode, output hashes match, original source and
excerpt hashes unchanged; no exact repeated narration or computed protected-span
overlaps. Inspected accepted text against exact sampled frames. Actual fixed-player
playback delivered all 43 original Tears pause cues, including former short misses.

33 backend regression/feature tests, five player tests, strict mypy (15 modules),
Ruff, affected frontend lint, TypeScript/Vite build, offline lock check and manual
freshness pass locally. No new GitHub CI or installed station acceptance claimed.
Fresh uncached runs took 270.899s/351.420s/240.958s. Final source-bound regeneration
reused validated model responses only, never checkpoints; changed-context requests
were recomputed. It is not a second fresh benchmark.

Important: conservative output omits substantial visual action, especially Bunny
(only two descriptions). These reproduced errors are guarded; comprehensive visual
coverage and WCAG compliance are not established. No new validation project begun.

Details: `docs/ops/evidence/audio-description-film-fixes.md`. Original integration
and earlier acceptance evidence below remain historical. Existing state and exact
candidate/process ownership:
`C:\Users\scott\Documents\Codex\2026-09-21\re\outputs\civiccast-blender-20261031\STATE.json`.
Evidence: adjacent `fix-verification\REPORT.md` and JSON/logs/review artifacts.
Final disposable preview remains on 8795; original film preview 8794 and budget
preview 8793 are untouched. Isolated inference jobs terminal and model unloaded;
model server 11439 is stopped after verification. No background inference remains.

Next boundary: finished local branch for review. No push, PR, merge, release,
production activation, additional integration or RC PDF/DOCX regeneration authorized.

---
# Audio-description integration — October 31, 2026

Local implementation is complete on `feat/local-audio-description`, in an independent
clone based on GitHub main `4fa8a4c3f4858b5c6253fd8d0299209b79bfacba`:
`C:\Users\scott\Desktop\Code\civiccast-audio-description`.

Delivered locally: actual asset job API and operator controls; local Gemma vision
and fixed-voice Pocket TTS; speech protection and measured narration placement;
explicit pause descriptions; source-bound checkpoints, resume and cancellation;
independent local-only model registry and 0099 migration; private preview grants;
resident/asset-embed playback with original HLS retained; source/publication/viewer
access checks; README, operational/API docs, manual source and built-in manual.
No new description-approval queue. No RC version bump, installer, push or release.

Evidence: `C:\Users\scott\Documents\Codex\2026-09-21\re\outputs\civiccast-ad-checks`.
- Real integrated `ad-budget-demo` job `73761da5b1a049318703e5754cfe97cc`: completed
  in 87.2 seconds; two pause descriptions, zero safe-gap descriptions in this excerpt.
- Original source hash unchanged. Actual operator preview and resident playback
  observed; video paused while local narration played, English captions showing.
- Backend feature/model run: 210 passed plus one test's missing Path import;
  corrected migration test rerun passed (1/1). All 211 affected cases now have
  passing evidence. Separate schema/head run: 29 passed.
- Public player/caption tests: 13 passed; affected operator tests: 27 passed.
  Both production frontend builds, public lint, affected operator lint, Ruff,
  strict feature mypy and in-product manual freshness checks pass.
- Migration upgrade/downgrade preserves the previous summary selection. Role,
  viewer-access revocation, publication, source/trim changes, single ownership,
  checkpoint corruption and cancellable live deferral have regression coverage.

Development runtime: loopback 8793, disposable public clip, isolated roots and
explicit test identity/access fixtures. This is a development preview, not installed
station behavior or a production auth acceptance. Original live data/config and
RC checkout were not used. Model server 11439 was stopped after generation;
no inference or worker remains. Current preview process ownership and launch
helper are recorded in `owned-processes.json` and the adjacent
`audio-description-feasibility-20261030/accept-feature-app.py`.

Next action boundary: local review only. Push/PR/merge/release, production activation,
installer provisioning and Townlight/Records integration need authorization. The
existing RC PDF/DOCX release files remain untouched; feature documentation is in
manual source, built-in manual and the operational guide. No new long soak or blind
listener validation is claimed. Automatic coverage is not legal certification.
Existing completed prototype evidence remains available; do not restart that work.

The RC2 release handoff below is preserved historical context for this branch.

---

# CURRENT — ACTIVE: RC2 R7/R9 operator corrections — October 30, 2026

Scott resumed RC2 after the complete read-only R7 results review. One candidate
on fix/rc2-r9-operator-workflows / PR285; version1.0.0-rc.2. Published RC1 stays intact.
No station/soak changes, model uploads, broad test ladder, unrelated scope or agents.

R7 source corrections implemented: model-data-only service Modify permission;
minutes+seconds in both schedule drawers; persistent actionable watch-folder and
minutes errors; private sample instead of dead RTMP camera; published manual and
recurring guide entries; planned-versus-actual as-run start; measured GStreamer
FPS/bitrate restricted to current-worker logs; visible release download bytes.
Prior R9 credential redaction, caption overlap/capacity, timing/repeat/accounting,
preparation, loudness, migration and verified download corrections are retained.

Executed: 16 Windows identity/ACL checks, scheduling UI checks, 19 watch-folder,
35 sample/live/minutes UI, 15 Commit-to-Air, 13 published-guide API, 22 as-run,
42 watchdog/health checks. New production caption tap consumed15/15 saved-audio
segments without discard; native CEA-708 decode-back recovered15/15 cues across
three isolated workers. All workers ended normally. Bounded development-host
proof, not installed tester acceptance or a sustained caption-uptime claim.
Detailed executed evidence/limits: docs/ops/evidence/rc2-r7-corrections.md.
Changed ESLint/TypeScript, Ruff, five-module mypy and current claim bindings pass.

Manual chapters, consolidated Markdown, PDF/DOCX and built-in Help regenerated;
both source-bound render consistency checks pass. Existing scanner report now
has one exact GLib2.88.4/CVE-2026-58016 application-reachability triage, with
upstream/reference/PE/source review and expiry; not an upstream-fixed claim or
blanket waiver. Saved report reconciliation passes without another broad scan.

Paused CI41072320499/41072320432 and waiter89258 are terminal. No current jobs.
Next: commit/push this reviewed batch, applicable required CI and same-run claims,
normal merge, one signed RC2 candidate reusing unchanged model packs, then the
focused failed-operator actions/three-channel caption observations. No RC2 kit,
installer, installed acceptance, tag or publication exists yet. No ETA asserted.
Human language review pending/nonblocking; historical accepted RC1 PTS remains.

## Existing RC2 implementation and earlier evidence

Scott authorized the R9 Beta20 corrections and all affected operator documentation,
manual and built-in help. Worktree `C:/Dev/cc-rc2-r9`, branch
`fix/rc2-r9-operator-workflows`, base published RC1 `6571f841`.
Published RC1 is immutable. No station/R9 soak actions, model uploads or subagents.

Implemented: safe credential validation responses; lower live Whistle overlap with
three-channel bounded capacity evidence; second-level scheduling; per-channel repeat
control and published preview; selected/encoded source boundary observations and
logical as-run accounting/window clipping; preparation status; migration explanations;
precise loudness warnings; resumable verified downloads and existing model reuse.
Branding reversion remains unconfirmed; historical tester orchestration is separate.

Executed focused evidence: auth 36 plus 9 expanded/valid-flow and 4 paywall cases;
caption 140; scheduling UI 24; repeat/store/source/cable 128 plus corrected fixture 1;
as-run/reporting 166 plus new recorder/daemon/window cases; migration 0098; loudness
30; downloader 12 (actual PowerShell range/failure fixtures); existing three screens
92. Native production worker checks observed three internal sources and a prerolled
replacement playlist; both passed with clean teardown. Native caption trials used
preserved components and read-only R9 WAVs: warm three-channel 5s arrivals processed
in 3.65/3.64s with full new input retained. Bounded proof, not a soak/installed claim.
Mypy passed seven changed modules. No broad unrelated media rerun.

Documentation now matches RC2: clean operator install/three-channel recipe, manual
chapters, quick start, built-in Help, screen copy, API reference/types and public
package pointers. Published RC1 status/limitations are documented without modifying
its assets. PDF/DOCX and packaged manual JSON are generated and current.
Source/version identity is `1.0.0-rc.2`; no RC2 tag or publication exists.

Additional focused results: 2 normal repeat/loading UI cases, preparation API plus
nonpersistent status (caught/fixed state DB serialization), source graph/roundtrip
5 cases, loudness cached measurement provenance, actual Beta20-version archive reuse,
and 13 release/version policy cases passed. Ruff/format on all 42 changed Python
files and mypy on the changed service/daemon/reporting/native graph modules pass.
Affected diff reviewed by the implementing agent; no independent reviewer claimed.
Manual PDF/DOCX consistency and packaged Help consistency pass.

F2 handoff lateness is only partly explained by original evidence; diagnostics now
separate preparation from settlement, not a claim to eliminate all timing delay.
F3 branding was not reproduced. Existing tester orchestration incidents are recorded
as separate. No new model bytes or station operations. RC1 accepted PTS limitation
remains historical/accepted; no repair loop was reopened.

PR https://github.com/scottconverse/civiccast-native/pull/285 remains OPEN at
9fa75f500637fc57858e651246c64376e66754d4, ordinarily signed/pushed October30 14:31 UTC.
The 43 previous Unit failures were addressed: current Alembic 0098 fixtures;
reviewed engine/graph/test claim tripwires with historical evidence limitations;
public RC1 architecture/support/manual pointers; explicit RC1-only acceptance
fixture; static useful validation guidance without submitted values or context.
Public manual PDF/DOCX copies come from the existing signed RC1 kit. Source manual
and packaged Help remain RC2. No models were uploaded (push transferred 2.02MiB).

Focused results: 45 passed in the first correction selection; remaining copy and
isolated child authentication environment yielded 20 passed. Two changed-claim
verifier cases pass, Ruff/format pass, mypy passes the three changed validation/model
modules. Logs work/rc2-{unit-correction-focused,validation-followup,claims-rebind-focused}.log.
Prior current-head run41018363680 is terminal/cancelled; no test producer waived.

Previous head9fa75f5 CI41072320499/optional41072320432 were cancelled at Scott's
pause; waiter89258/PID1180 is historical/terminal. Previous scanner failure and
upstream investigation are preserved in work/rc2-security-9fa and its failure log.
The top section supersedes those previous pending CI/security states. Required
new-head outcomes and the delivered candidate remain incomplete.

## Historical RC1 state (superseded assignment; evidence retained)

## RC1 current assignment (supersedes historical completion below)

Owner acceptance (Scott, 2026-10-29, direct instruction relayed by the authorized
watcher): RC1 may proceed with the specific observed immediate finite playlist
reload result: one backward audio PES PTS step, `{66: 1}`, in
`test_immediate_finite_playlist_reload_holds_rebases_and_stays_on_air`.
Evidence: [required Windows job123859869545](https://github.com/scottconverse/civiccast-native/actions/runs/40993003702/job/123859869545)
on29dcca4c:56media passed/1failed. Both incoming streams prerolled; commit,
continued output, worker survival and clean teardown assertions passed; the prior
planar-audio-tail case passed. The final codec probe after the failing PTS
assertion was not reached. This is an accepted limitation, not a test pass or
proof that timestamps are correct. Sustained/repeated stalls, readiness failures,
other PIDs/counts/assertions, skips and errors remain outside this acceptance.
No further media diagnosis, retry or rebuild was authorized to perfect this case.

CI preserves the original assertion and failed JUnit unchanged. Only that exact
class/name, source assertion and `{66: 1}` result with standard pytest exit1
are classified as failed-owner-accepted ONLY for version1.0.0-rc.1; later versions
reject this exception. CI prints an explicit warning and separate
passed/accepted counts. All other failures, skips, errors, incomplete execution
and nonstandard exits fail required CI. Governed workflow bindings are renewed
only for this documented classification; existing producer requirements remain.
Local scratch reproduction was interrupted during fixture generation on the stop
instruction (no completed tests); no owned pytest/worker remains. No product code,
station configuration or host service was changed. Retained log:
chat work/rc1-pr284-29d-native-failure.log; original JUnit downloaded to
chat work/rc1-pr284-29d-junit/junit-windows-media.xml without rewriting it.

Classification validation:4focused policy checks passed, including18negative
scenarios; actual saved JUnit classified56passed/1accepted without mutation
(SHAe63f19f4275fdffbc869202e312facf93cd2705a5c5595bca694362758826708).
Root added RC1-only version guard; both classifier tests pass3.54s including
future-version rejection. Ruff/format/diff pass; governance checks pass.
No product/media rerun. New normal push follows.

Current unit producer still running; waiter42022 remains. All other early required
checks pass; reproducibility still pending at last panel read. No new signed build.

LATEST ACTIVE CANDIDATE: PR284 head29dcca4c9af697adea4823c8bad7c372bdc9dced,
normal push completed. Required CI run40993003702, reproducibility40993003681.
One waiter session42022/PID29008/start2026-10-30T02:40:21Z, log chat
work/rc1-pr284-29dcca4c-required-wait.log; Ctrl-C cancels watcher only.
Native input transfer session10501 terminal SUCCESS02:40:52Z. Reviewed-input
transfer session73408 terminal SUCCESS02:41:14Z (run-once, no persistent
listener). Process receipt chat work/rc1-pr284-29dcca4c-processes.json.
Next handle required CI terminal results; same media readiness failure requires
focused diagnosis before further retry. Then normal merge, signed corrected build,
all outstanding isolated installed acceptance and cleanup. STOP before publication.
All earlier CI/waiter entries below are historical unless explicitly noted here.
Owner follow-up via watcher: if readiness failure repeats, correct exact incoming
boundary before another retry; no timeout/assertion weakening. Existing failure
has NO measured restart/resumed-output interval: standalone worker test launches
one process, records10s no-output watchdog and exit, no daemon recovery. Recovery
duration unavailable, NOT10s. Existing source/logs inspected only; no new probe.
New owner acceptance via watcher: roughly15–30s acceptable for one-off successful
recovery. This is an estimate/preference, NOT measured outage, required new test,
or waiver of required CI. Current audioPTS failure is a different condition.

Count correction reviewed: purefloor2095/Windowsfloor2334, exact2145/2387;
both affected policy tests PASS27.10s, narrow governance/diff checks PASS.
Six workflow blob bindings renewed to230d6303d11c2a7a2896dd5291e16c122a6c5edc;
contract/producer requirements unchanged. Normal push follows; no active waiter.

PR284 d7ca613e required run40989064082 TERMINAL FAILED02:34Z; waiter16832/
PID16204 terminal. Unit:11935PASS/2FAIL (only collection-policy counts/floor),
112skipped/5deselected as reported. New Ollama regression makes counts2145/2387
versus stale2144/2386, and purefloor2094 is51belowactual (allowed50).
Claims9violations cascade from failed producer/missing required evidence.
Bounded agenda_setup updates counts/floor/governed workflow bindings and runs
only affected policy checks. NO retry of obsolete d7 CI. New head required CI
will supply ONE justified unchanged media-path retry; repeat readiness failure
requires specific diagnosis. No new signed installer or guest launched.

Required-run waiter now session16832/PID16204/start2026-10-30T02:26:38.3058580Z. Existing all-checks waiter23196 stopped (identity verified) to exclude optional randomized waiting. Log chat work/rc1-pr284-required-run-wait.log; Ctrl-C cancels waiter only. Terminal result triggers exact failed Windows-job retry; no remote job canceled.

PR284 Windows job123847729190 FAILED:56mediaPASS/1planar-audio-tail rollover
failure; subsequent native-suite JUnit absent, no pass claimed. Bounded Sol diagnosis
compares PR283 job123796243285 verified57PASS: exact engine/test/workflow/runtime
pins unchanged. Outgoing EOS/padding completed; incoming second stream never
prerolled before existing10s watchdog. No specific stream/caps diagnosis in log.
ONE unchanged failed-job retry justified; no timeout/assertion/source change.
CLI retry refused while parent40989064082 still running unit tests; no retry started.
Retry exact failed job after parent terminal. If same cause repeats, diagnose
incoming per-pad state/caps before any further attempt. Logs retained in chat work.

PR284 current headd7ca613e: Rust compile/tests, lint, public/operator accessibility
(including WebKit), docs and security checks PASS. Required unit/native runtime/
reproducibility jobs still running; native job passed tool installation and reached
real media tests. No repository review findings. Both input-transfer listeners
terminal SUCCESS. Existing CI waiter PID23196/start01:59:50Z remains the only waiter;
its exec session handle was not retained across compaction. Log remains
chat work/rc1-pr284-d7ca613e-ci-wait.log. No new installer/guest yet.

Owner request: merged corrections, one consolidated signed RC1 candidate, isolated
installed acceptance and cleanup. STOP before RC1 tagging/publication/public asset
replacement. Beta20 publication is complete. Never probe or modify the host station.

Candidate: C:/Dev/cc-pdf-processing, fix/rc1-ollama-probe, base merged3addb10e.
Version1.0.0-rc.1. Signed3addb10e kit FAILED real clean installation and is NOT
ready. Bounded correction fixes stale installer Ollama version checks below.
Preserve unrelated Windows recovery documents; one active candidate.

Required outcomes: administrator UI agenda enablement/tenant persistence; bundled
pinned offline Tesseract/English/DLLs/licenses; automatic managed agenda backup and
safe replacement restore; shipped operator/install/recovery guidance; actual shipped
runtime dependency security coverage; competent language acceptance for Spanish,
Simplified Chinese, Filipino/Tagalog, Vietnamese and Arabic.

Root owns integration, recovery, version/docs, required CI/normal merge, one signed
build and isolated installed service/PostgreSQL/browser acceptance. Bounded Sol6.1
medium agents agenda_setup, offline_ocr and agenda_recovery own config/UI, OCR/native
inventory/security and managed recovery respectively. No DeepSeek; watcher observes/nudges only. All dependency-source builds and agent tasks are terminal; the first signed RC1 build stopped at the real installer-size guard; bounded correction follows below.

Acceptance still pending: real discovery/explicit selection, visible original/page
links, exact 30 items, zero writes before confirm, incomplete refusal, persistence
after isolated restart, bundled OCR under service identity, backup/restore/resume,
signatures/install/upgrade preservation/uninstall. PR282/283 source reviews and normal merges are complete; PR284 correction awaits required CI and merge.
Reuse unaffected retained 258-page/checkpoint/development OCR/YouTube/blackout evidence.
Owner revision relayed by the authorized watcher October29: qualified human review
for Spanish, Simplified Chinese, Filipino/Tagalog, Vietnamese and Arabic is PENDING
and explicitly NOT an RC1 readiness/completion blocker. Scott has no reviewers;
do not seek them as a prerequisite or claim human-validated translation accuracy.
Language edit/review/publication/player-selection software acceptance remains required.

Current state: PR282 normally squash-merged October29 23:01:05Z.
Merged source2be5b99d9ea402d4b0756975ae98cb70cb1e9b35, version1.0.0-rc.1.
Final PR heade05fdce431e697c03639c198c33514531f5883ea: ALL required CI passed,
including unit and same-run claims run40964478052; optional randomized also
passed. Final snapshot chat work/rc1-pr282-e05fdce4-final.json. No unresolved
repository findings; original automated review31cf34c6 had none, later affected
corrections reviewed by root (no new independent review claimed).
CI waiter4015/PID6116 terminal; both input-transfer run-once listeners terminal.

Signed build40970229720 at2be5b99d is terminal INCOMPLETE, no installer/kit.
K1 model bundle signing and final manual rendering succeeded and remain valid.
Attempt1 stopped before pack build on an existing EMPTY PyAV reuse directory;
verified scoped empty/non-reparse/no owned worker, removed nonrecursively.
Attempt2 built/verified all five signed component packs, then correctly refused
installer disk estimate: required four sidecars+extractedtrees6145182639bytes
exceeded declared5529600000bytes. This is a real package-affecting defect.
No third blind retry. Runner12243 and waiter32300 are terminal; no owned worker.
Completed component packs+five builder reports moved safely out of runner cleanup
to C:/CivicCastTester/rc1-preserved-components/2be5b99d9ea402d4b0756975ae98cb70cb1e9b35.

Follow-up PR283 normally squash-merged October30 00:29:03Z.
Final head2e3be4ceae382e861b28552d6f07f7bf5552f0d5; merged source
3addb10e0a8cdfaafd5854c4643d503721e95b1a. Identical git tree
ff8ae87370a34c4950aad08b13ecbc56587c18d0. ALL required checks passed,
including unit+claims. Optional randomized also passed. WebKit video.play timeout
resolved on ONE failed-job retry, unchanged source/assertions; trace retained.
Snapshot chat work/rc1-pr283-final.json. No review comments/findings. CI waiter
18363/PID23880 terminal. Corrected NSIS6600000KiB bound retains exact size guard;
PyAV scratch mkdir idempotent, wheelSHA unchanged. Four focused checks passed.

Corrected signed build40980003673 dispatched against exact merge3addb10e,
no public upload/release. Protected Beta20 station supplies unchanged model packs.
Headroom before dispatch11.49GiB RAM/140.09GiB disk. One hidden workflow waiter
session25858/PID36516,parent31460,start2026-10-30T00:30:08Z:
gh run watch40980003673 --exit-status --interval45 -> chat
work/rc1-build-40980003673-wait.log; Ctrl-C only cancels watcher.
K1 session31622 terminal SUCCESS00:33:48Z; exact-source manual also SUCCESS.
Owned installer-assets runner session59068 started00:34:18Z, job123818941433.
Runner process identity receipt chat work/rc1-build-40980003673-runner-assets.json.
Terminal expected after installer-assets only.
Next start separate run-once listener for OWNED queued installer-assets, then
kit assembly; do not leave persistent runner for automatic GateA jobs.
For explicit cancellation use gh run cancel40980003673, wait worker exit, then
Ctrl-C owned listener/watcher if needed; verify identities first.
Build40980003673 attempt1 TERMINAL FAILED00:42:15Z, no installer/completekit.
K1/manual succeeded. App pack built+verified: b2c67e0e6c3a816a3f2198158c23a230530ca04a2bd5dc928c88e09b7d1e2f79,
582639306bytes. Server pack refused existing cached OpenSSL libcrypto DLL:
C:/actions-runner/_work/_temp/civiccast-server-pack-cache/extracted/openssl-security-cache/openssl-3.5.9-runtime.
Exact hash guard remains intact. Failure log chat work/rc1-build-40980003673-failure.log.
Assets runner59068 and workflow waiter25858 terminal; no active owned runner.
Bounded offline_ocr agent diagnoses actual/expected hashes/cache cause before retry.
Diagnosis: DLL absent, both overlay cache leaves EMPTY (not modified DLL proof).
Trusted two archives and all17memberpins pass. Root verified no runner, exact
scope/non-reparse/empty and deleted ONLY two leaves NONRECURSIVELY. Receipts
chat work/rc1-cache-diagnosis/{empty-overlay-cache-diagnosis,empty-leaf-removal}.json.
Attempt2 same source dispatched FAILED JOBS ONLY; K1/manual retained success.
Assets runner session19199 started00:46:49Z, running correct assetsjob00:46:51Z;
one waiter session36440 -> work/rc1-build-40980003673-attempt2-wait.log.
Exact process identities in work/rc1-build-40980003673-attempt2-processes.json.
Same cancellation procedure, verify current identities. Attempt2 COMPLETE SUCCESS:
assets01:11:34Z, assembly01:15:32Z. Both runner19199/64877 and waiter36440 terminal.
Final complete kit C:/CivicCastTester/kit-staging/3addb10e0a8cdfaafd5854c4643d503721e95b1a.
Installer261527144bytes, SHA2568144e6486001c05306a59002f71dcc656c39f79a593fadffdfbee920e9942e68,
Valid ScottConverse Authenticode+Microsoft timestamp (chat work/rc1-final-installer-identity.json).
All5pack verification, sizeguard, bootstraptrust, packagedGstsmoke and signing pass.
Final security rebind chat work/rc1-packaged-security/final-source-reconciliation.json:
all dependency hashes unchanged, prior153+12-vendor scan reused, no repeat scan.
Automatically queued INFORMATIONAL GateA40984757876 cancellation requested to
avoid duplicate parallel acceptance; verify terminal once, no persistent runner.

Actual isolated installation FAILED before browser/OCR/recovery acceptance.
Guest55cc46ee-87c5-4c40-8205-9c666d315f22,hostname5FDAD1C7-5457-4,
LocalSystem,8GiB, signed setupSHA8144e648...942e68; source3addb10e.
Install exit123 after d4 activation returned67: bundled Ollama0.40.13 --version
returned0 and its actual no-daemon/client-version warning. Installer still
expected0.30.6. Subsequent daemon /api/version also had stale0.30.6; both needfix.
Failure contained: no service registered, installed runtime rolled back, no PG
marker, browser execution or backup archive; installer/lifecycle processes ended.
Guest logs+failure-preservation.json retained at
C:/CivicCastTester/rc1-installed-3addb10e-20261029/failed-install-evidence and parent.
Unused generated test secrets were not exported (no account/archive was created).
Original/checkpoint/recording fixtures remain host-side. Owned guest STOPPED and
verified ABSENT from running sandboxes, guest-terminal.json01:43:40Z. No other
VM or host station touched. Stage waiter54982 terminal. No active guest/runner.
Failed signed installer+small build reports retained and installerSHA rechecked
at chat outputs/rc1-failed-3addb10e; old fullkit may be pruned by corrected build.
Protected Beta20 complete rollback remains unchanged. Auto informational GateA
40984757876 CANCELLED/COMPLETED (force-cancel after normal request stayedqueued).

Bounded source fix prepared by agenda_recovery, root affected-diff reviewed:
- One installer NATIVE_OLLAMA_VERSION0.40.13 matches authoritative runtime lock.
- Both CLI self-test and actual daemon-version response use it. CLI accepts exact
  real no-daemon two-line output or exact normalversion only with exit0; wrong/
  suffix/partial/unrelated output and failed execution refused. Daemon HTTP and
  actual model inference checks remain intact.
- New existing-suite Python regression binds installer pin to builder+lock;
  9focused Python checks pass, Ruff/diffcheck/rustfmt syntaxpass. Three Rust
  regressions prepared for required WindowsCI, NOT locally executed. No local
  full installer build or host daemon/station probes.
PR284 OPEN: https://github.com/scottconverse/civiccast-native/pull/284
Current headd7ca613e3c9ee07ed1d2779a7410522f19fddff2, requiredCI running.
Old watch13693 terminal; one hidden waiter session26395/PID23196,parent12984,
start01:59:50Z, gh pr checks284 --watch --interval45 -> chat
work/rc1-pr284-d7ca613e-ci-wait.log. Ctrl-C stops watcher only.
Old ci-test40987844299 obsoletehead CANCELLED/COMPLETED; old optionalrandomized
had completed before cancellation request, no new optionalgate. Newrun40989064082.
New reviewed-input transfer runner28955 PASS/terminal02:01:15Z; second native
runtime-input run-once runner started only for queuedjob123847311095.
No persistent runner, active agent or guest.
First head: Windows Rust compile+new regressions PASS. Lint failed only formatter
on new Python regression; root ran pinned Ruff0.15.12 format+check, now passes.
Windows native job failed BEFORE tests: Chocolatey feed504 for FFmpeg9.0.2,
then ffmpeg command missing. No testresults/zero pass claim; no workflow change
or retry of this obsolete head. Logs chat work/rc1-pr284-{lint,native,public}-failure.log.
Public WebKit again failed transient readyState>=3 on2second clip (got2).
Bounded trace diagnosis complete: play resolved40ms, everybuffer-state poll2,
allHLS/VTT200; trace has no ended/currentTime evidence, so no claimed endcause.
Existing embed test now latches2presentedframes/media-time advance and requires
>0.1splayback advance, nullplay/mediaerrors; caption/mobile/revoke assertions
unchanged. Focused existing Chromiumtest1PASS13.5s, UIbuild/ESLint/diffpass.
Local VITE_CIVICCAST_API_PROXY_TARGET used owned503server at56200, freshpreview;
0unmockedrequests received, no8000target configured (not packetcapture claim).
All owned test helpers stopped. Local exactWebKit absent, no download; required
CI must verify WebKit. Root reviewed affecteddiff and batches with formatter.
No productfeature change, assertion weakening, optional gates or broad local loop.
Resource note before NEXT guest:8GiB cannot hold largest9,608,338,848-byte
single model blob plus Windows/runtime. Original12GiB plan is appropriate if
headroom permits; last host free13.12GiB after stoppedguest. Recheck beforelaunch,
preserve ownerapps/station, no concurrent guest/build. Do not blindly reuse8GiB.
Next: requiredCI/normalmerge, corrected signed candidate (actual package-invalidating
activation defect), then all still-pending installed acceptance/cleanup. Reuse unaffected packs/source/manual/security
proofs. Before assets job remove ONLY verifiedempty OpenSSL/security-overlay
cache leaves if runnercleanup left them (see diagnosis); archive/memberpins stay.
No tag/publication. Human language review pending NONBLOCKING as noted above.
Final3add manual receipt source37d386db...same reviewed source, hashesverified:
PDF ade428ea7dd15ab1589eb843986d227669920a581d92ddeba41be04087c7d5bb1,
DOCX d93d28539890d13828f40bc3dabada6cc6891fce5754b9e324839586d0b82744e.
Final-source dependency binding at chat work/rc1-packaged-security/final-source-reconciliation.json.

Final2be5b99d manual PDF/DOCX+receipt retained at chat outputs/rc1-final-manual-2be5b99d;
file sizes/hashes and render manifest verified. Source37d386db... equals prior
visually reviewed manual. PDFsha e6c290684e778dbe10156a16f781be4618e6f1b703ab791461201fdbcf6f464a;
DOCXsha379a2e1e25893475500d9fc467eb034337b99b10dc9b9624a903e6df7bfe7271.
Isolated acceptance inputs ready; C:/CivicCastTester/rc1-installed-2be5b99d-20261029/
acceptance.wsb is obsolete preparation only. Current8GiB configs bound to3addb10e
at C:/CivicCastTester/rc1-installed-3addb10e-20261029/acceptance.wsb and
C:/CivicCastTester/rc1-upgrade-3addb10e-20261029/acceptance.wsb. Main guest failed and was stopped; upgrade guest has not launched.
Prepared browser check now clicks Page4 (not first page), asserts #page=4 and
requires root to visually inspect actual page4 screenshot; syntax checked only.
Failed main guest stopped as recorded above; upgrade guest not launched. Station untouched; protected Beta20/rollback kits unchanged.
Recovery-secret export helpers prepared in chat work/rc1-acceptance; syntax passed.
Host RSA key generated at C:/CivicCastTester/rc1-recovery-key-20261029 with
CurrentUser-DPAPI private key and user/System-only ACL, OUTSIDE guest mappings.
Only public key copied into acceptance inputs; SHA256
c462d5ed924744f62b6de0cdb4e2358d4a858325cdf365fc03706d7a0e31ce2e.
Prepared main lifecycle no longer repeats same-version RC1 reinstall; separate
Beta20-to-RC1 upgrade is the required preservation check. Changed helper syntax
passes; main guest failed before these stages. Guest export/host retention NOT executed.
Require retained encrypted backup and
host-protected passphrase receipt before any eventual owned-guest disposal.
Latest resumed check:13.17GiB available RAM. Recheck before guest launch;
12GiB planned for actual model inference, no concurrent build/guest.
Do not reuse8GiB or stop owner applications.
Packaged security reconciliation found12 nested setuptools vendored distributions
not separately enumerated by static inventory; one focused actual-version audit
reports zero findings. Supplemental inventory/pack hash binding COMPLETE at chat work/rc1-packaged-security.
All83top-leveldependencies match;249selectivechecks/218files PASS;246GStreamer
closure entries match manifest/report. Twelve exact vendoredversions zero findings
and zero skips. Fullsignature verification reused from originalbuilder reports,
not independently repeated. No product/source changes for this; current recurring
static pipeline omits nested cohort, candidate supplement explicitly covers it.

Last fully tested head5bbf881015d39a1a5ba611075887da21e8c95db6.
Historical pre-merge failures below are resolved; signed build/installed acceptance pending.
Required unit run40955160859/job123734196810 terminal:24failed/11913passed/
112skipped/5deselected,57m27s. Claims verifier consequently failed, also reporting
stale workflow/contract bindings and missing input-transfer job inventory.
Owned CI waiter session75660 is terminal; no duplicate waiter.

Focused correction pushed in e05fdce4 (no product/runtime-byte changes):
- Installer text-stub fixtures isolate secured_postgres_sources; production
  archive/hash verification stays unchanged. Ubuntu frontend assertion now24.04.
- Native exact counts2144pure/2386total/2383Windows-not-integration; floors raised
  to2094/2333, retaining50margin. All34affected tests verified across focused runs.
- Three stale docs tests now support RC editions and distinguish RC1 source from
  still-published Beta20 manual downloads; exact artifact hashes remain checked.
  No manual/public artifact changed or rerendered for test-only corrections.
- Six workflow/contract blob bindings renewed only for reviewed archive transfer
  and raised floors. Transfer job inventoried as NONproducer; named claim code,
  tests, trust root, producer nodes/attestation unchanged. Sixteen affected
  policy regressions pass; real static inventory/needs/bindings pass. Actual
  same-run claims verification remains required on new CI run. No installed
  or hardware claim renewed. Ruff/format/diff checks passed for corrections.

Valid passing evidence at5bbf8810 retained:
- Native Windows job123734271897:2383native+35installer+57realmedia, zero
  failures/errors/skips. outputs/rc1-windows-5bbf8810. This proves fixed PNG
  graphics/board/emergency behavior under hosted Windows; no local physical GPU.
- Repro40955160743:two independent clean app builds byte-identical;
  outputs/rc1-repro-5bbf8810. Earlier failure was ambient ccache; supported
  ENABLE_CCACHE=OFF plus explicit empty launcher cache/env fixed it. OCRlock
  e44aabd55106f76a965cc64229fd04d3534cb35d97eedb9f01f60f9de9222183.
- PublicWebKit40955160895 passed unchanged after one playback-ready precondition
  failure on910cb6bf; trace retained. No weakened playback assertion.
- Security40955160860 passed; outputs/rc1-security/ci-5bbf8810.
- PNG omitted implicit decoder regression fixed by existing pinned gstpng.dll,
  explicit pngdec requirement and confirmed per-file LGPL mapping. Real pinned
  DLL staging through manifest/BOM passes; exact source/licence/failure-to-fix
  receipts docs/ops/evidence/rc1-native-security/png-decoder-regression.json.
- Optional randomized40955160865 failed35m14s; not an owner release gate and no
  extra investigation/rerun launched. Do not claim every check is green.

Next: finish this signed build, existing isolated acceptance and cleanup;
STOP before RC1 tag/publication.
No signed product build or dependency archive rebuild for these CI-only changes.
Current ead567a3 and rollback8fdce69e complete kits safely moved (same volume)
to C:/CivicCastTester/rc1-preserved-kits/<full-SHA> before any build pruning;
station-index presence/hash checked. Reuse ead.../station from this protected root.
Restore retained kits to canonical staging after build/final cleanup. No deletion.
Owned hidden runner session61380 / Runner.Listener PID4504 started2026-10-29
19:36:29Z, executable C:/actions-runner/bin/Runner.Listener.exe; two input transfer
jobs passed. Runner stopped cleanly while idle October29~21:35Z; session61380 terminal
and PID4504 absent, no Runner.Worker. All input transfers succeeded.
Use hidden run.cmd --once per authorized build job after required CI/merge.
Historical cancellation:
verify identity then Ctrl-C owned session. Single PR waiter session75660 uses
45-second gh checks watch, log coding-chat work/rc1-ci-wait.log; terminal result
triggers failure repair or merge. Cancel waiter with Ctrl-C only if superseded.

Passed source evidence: setup backend73pass/1optional skip, operator UI46pass,
types/lint; recovery combined70pass and runtime10pass; real junction escape
regressions8pass after six reproduced failures; language workflow5pass and
player selection6pass (not human accuracy review). Model reuse27pass; OCR122
focused tests and real raster recovery passed in development. Final native
integration/policy tests63pass, pip-audit policy7pass; CAP/RSS entity rejection
29pass plus actual shipped-Python proof. Changed Python Ruff and diff checks pass.
Root reviewed affected source; no independent candidate audit is claimed.

Package corrections (built/pinned/mirrored; not signed/installed acceptance):
- CPython3.12.16 cp312 archive b06074f51101293e2d81059dde190ba9325665e926e4e2a16c0e3916c49be9db;
  PyAV18.0.0/FFmpeg8.1.3 with four exact backports wheel
  5ab3fae3f85d4121919f70643e026452eb2a678b4a3f557b9ca487724c1399f0.
- Ollama0.40.13, PostgreSQL17.11-2, standalone FFmpeg N-128142-g28fda48e0b,
  Cryptography50.0.2/OpenSSL4.0.3 and minimal offline Tesseract5.5.4/English.
- GStreamer FFmpeg7.1.5-security1 overlay 40cf143eda356ea14573323c8dcfbd4c4e33c83a2510e50bbaae95912b143246;
  GLib2.88.4/libsoup3.8.1/nghttp2 1.70.0 overlay
  34da86644a17511887de4f01b7ea18708f07c15e1f92f1448dda68166eebe6b9;
  standard-ABI static-CRT OpenSSL3.5.9 overlay
  062f6d64ae51eb970e46422e6791bced241867574179aa278a82a08245f82491.
- Support-library overlay 6545e5f76472135310815f68296b427a8efc9810ba28a4c4c919241cfd04e683
  (2666766 bytes): zlib pinned security source 6be4bcda (runtime1.3.2.1-motley,
  not a stable-release claim), libpng1.6.57, libxml2 2.15.4, HarfBuzz15.0.0,
  standalone SQLite3.54.1 with automatic initialization. Earlier archive e42d...
  reused Python's OMIT_AUTOINIT SQLite and is SUPERSEDED; reproduced failure fixed.
- All19 changed DLLs pass combined import/closure checks. Scratch runtime
  compression/SQLite operations and real PostgreSQL bootstrap/XML XPath pass;
  unchanged PG DLL proof remains valid after SQLite-only correction.
- Native Grype report has129 raw matches, all individually evidenced/triaged by
  the existing checker. This is NOT129 confirmed vulnerabilities or zero matches.
  Final CI scan now passed at d3777c1, run40942012789:
  153 native components /129 individually triaged matches;83 Windows wheels/zero
  vulnerabilities. Evidence coding-chat outputs/rc1-security/ci-d3777c1. Exact evidence lives under
  docs/ops/evidence/rc1-native-security and build/native-security-final-grype.json.
  Existing pipeline now covers shipped Windows requirements and native components;
  no threshold waivers. Only the project's own editable distribution is exempt
  from PyPI scanning; third-party skipped dependencies fail.

Manual PDF/DOCX and docsite rendered and affected content visually reviewed in
coding-chat outputs/rc1-doc-review. Canonical manual SHA
37d386db5d94b0c0449985859dbc7b57e606c983767e70cd9ef3acac59f3ff50.
Final signed workflow renders companions from its final SHA. Installed browser, offline OCR, complete lifecycle and separate true Beta20-to-RC1
upgrade scripts are prepared under coding-chat work/rc1-acceptance but NOT executed.
Main lane same-RC1 reinstall is explicitly not cross-version upgrade evidence. Required CI, normal merge, signed build, installed service/PostgreSQL/
browser acceptance and cleanup remain. Source compiler jobs ended; CI runner/waiter recorded above.
Host station untouched. STOP before RC1 tagging/publication/public assets.


## Historical Beta20 delivery (complete; do not reopen)

## Compaction checkpoint

No active goal or outstanding delivery work. Await Scott's next assignment. Do not
restart monitoring, builds, tests, publication, L1/L2 or Tesseract bundling merely
because context resumes. Root is coder/coordinator; CivicCast Current Coder is
watcher/nudger only. No DeepSeek. Use the approved Sol6.1 medium routing when needed;
no automatic delegation or invented review gates.

Working checkout: C:/Dev/cc-pdf-processing, branch docs/publish-pdf-package-20261029.
Coding chat cwd: C:/Users/scott/Documents/Codex/2026-10-28/re.
CURRENT contains the local terminal receipt, intentionally not pushed as another
bookkeeping-only CI cycle. On compaction inspection, unrelated untracked
docs/ops/windows-update-recovery-2026-10-29.md and its evidence directory were present;
preserve them. They are not part of this delivered assignment.

Current complete kit:
C:/CivicCastTester/kit-staging/ead567a33614a65554a69ef186342d691c43b7dc
One rollback kit:
C:/CivicCastTester/kit-staging/8fdce69e4fccfba80f2984a0d034d8851b4840e0
Installer filename: CivicCast (Native)_1.0.0-beta.20_x64-setup.exe.
Public release: https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.20
Full artifact/acceptance report and publication cleanup receipt are in coding-chat
outputs/CivicCast-signed-installer-ead567a3.md and CivicCast-published-20261029.json.
All owned build/acceptance/upload/CI helpers ended; old PIDs/session IDs below are
historical, not authority to stop any current process. The host station was untouched.

## Final delivery receipt

PR281 normally merged2026-10-29T17:38:53Z as25c9dcc4a5202339f77d2ff165a3e534dd772d95,
head61a18829d51da0f3c768c3c83c1baee010a342d6. All applicable required checks passed,
including Unit/WS4/Claims run40913928157; no review findings. Single waiter48362/
cell17 exited0. Public release and annotated provenance tag delivered, unchanged
originalBeta20tag. Publication needs no further work; Tesseract bundled addition
deferred per Scott. Signedbytes remain exactly the prepared/accepted candidate.
Final cleanup removed five redundant upload binaries/manuals (~0.88GiB), retaining
small checksums/sidecar/manual receipts, all evidence/source, currenteadkit and ONE
rollback8fdkit. Prior watcher cleanup recovered83.84GiB separately. No station action.
Delivery+cleanup receipt: coding-chat outputs/CivicCast-published-20261029.json.
This local terminal receipt does not trigger another bookkeeping CI run.

Earlier publication chronology below is historical; remaining-work statements
are superseded by delivery above.

Standing owner directive October29 (relayed by authorized watcher): after EACH
completed delivery, remove temporary build artifacts as the final step. Preserve
the current complete signed kit, ONE complete rollback kit, all source/unrelated
work and evidence/receipts. Never touch the running station. Do not rebuild removed
candidate caches or add cleanup tests/audit loops. Watcher already removed25
redundant completed dirs (83.84GiB); current eadkit and rollback8fdkit remain.
Small reports formerly in C:/CivicCastTester/candidates were preserved in coordinator
outputs/cleanup-20261029; candidates copies are removed, do not recreate them.

Public revision DELIVERED at existing v1.0.0-beta.20 release. All27asset sizes and
GitHub digests verified against prepared replacements and retained old assets:
9replaced,18unchanged. Public unauthenticated SHA256SUMS and setup sidecar downloads
match selected bytes. Total27assets26937777054bytes including standalone reviewed
PyAV wheel. Setup SHA2568a37e2bc48d1f0d63fde088c682730f9b2b49e812e674168ddf462b5b01b9f34.
Originalrelease tag a17449189a39af6474d914137eab020169f91023 unchanged; new annotated
archive/v1.0.0-beta.20-package-revision-20261029 points at artifact sourceead567a3.
Release notes disclose exact source, accepted checks, external OCR prerequisites,
and unchanged historical/model evidence. No signing rebuild or station action.
Publication docs PR281 and required CI40913928157 are complete, as recorded above.
Historical waiter session48362/cell17 ended successfully. Its retained log is
work/publish-ead567a3/pr281-ci.log under the coding chat cwd. No waiter remains.
Public assets already verified; do not repeat verification or re-upload without
a new affected requirement or demonstrated defect.

Publication preparation history follows.

Scott explicitly ordered push/merge/tag/publish; Tesseract bundling and other
features are deferred to a future version. Existing Beta20 package-revision
convention applies: original release tag unchanged,9changed assets/18unchanged,
new annotated provenance tag archive/v1.0.0-beta.20-package-revision-20261029
pushed at exact artifact source ead567a33614a65554a69ef186342d691c43b7dc.
Publication branch docs/publish-pdf-package-20261029 based on main ead567a3.
Prepared9asset hashes/old remote metadata/checksums/notes retained in coding-chat
work/publish-ead567a3. Previous signed8fdkit remains available for recovery.
Owned upload session60781 uploads app/server packs and4manual files; next upload
setup/sidecar/checksums, verify all27remote digests and unchanged tag, edit release
notes, commit/push publication docs, required CI and normal merge. No rebuild,
station action, model-transfer, force-push or DeepSeek. Completion not yet claimed.

Prior preparation receipt follows; its no-publication boundary is superseded above.

# READY FOR SCOTT TAG/PUBLISH APPROVAL — October 29, 2026

Preparation COMPLETE. No tag/publication/asset replacement authorized or performed.
Source ead567a33614a65554a69ef186342d691c43b7dc, version1.0.0-beta.20 unchanged.
Signed build40835548156 attempt2 success. Kit:
C:/CivicCastTester/kit-staging/ead567a33614a65554a69ef186342d691c43b7dc
Setup CivicCast (Native)_1.0.0-beta.20_x64-setup.exe,261413760bytes,
SHA2568a37e2bc48d1f0d63fde088c682730f9b2b49e812e674168ddf462b5b01b9f34.
Authenticode Valid, Scott Converse, Microsoft timestamp. Full artifact paths,
companion hashes, provenance and limitations in coding-chat outputs:
C:/Users/scott/Documents/Codex/2026-10-28/re/outputs/CivicCast-signed-installer-ead567a3.md
and matching.json. This local receipt does not require a signing rebuild or CI rerun.

Required isolated lifecycle PASS08:37–09:00UTC, receipt:
C:/CivicCastTester/civiccast-ead567a3-20261029-retry/receipt.json.
Install/packaged agenda proof/uninstall exit0; setup/launcher/uninstaller signatures
Valid; offlineAI/caption activation and guest service/health/schema passed. Installed
six-module hashes match selected source, four real agenda pages yield30correctitems,
actualPDFium rendering passes; preview has no preconfirm writes, authenticated original
hash correct, confirm/reopen30items. Retained258checkpoint unchanged; no fullpacket
orOCRrepeat. Uninstall removedruntime/packs/launcher/service and preservedupload/PGmarker.
Packaged agenda check uses installed production routes in-process, not live supervisor
agendaAPI/newGUI. Prior browser evidence reused; native PDFviewer rendering unverified.
Tesseract+Englishdata+DLLs remain external prerequisites, not bundled promise.

All owned runners/waiters and Sandbox instances stopped; wsblist empty09:00UTC.
Host station never probed/changed. Historical8fdcandidate restored intact. No LFS,
spending/reboot/newsourcepatch. No remaining executable preparation work; STOP here.

Historical preparation chronology follows; pending statements below are superseded.

New owner authorization relayed by Current Coder watcher: prepare signed public
installer containing PR279/280, STOP BEFORE TAG/PUBLISH. No host station probes,
changes, restart, install or reboot. Existing pipeline/isolated lifecycle only.
Candidate source main ead567a33614a65554a69ef186342d691c43b7dc; established version
1.0.0-beta.20. Reuse source CI40828194825 and unaffected packet/resume/OCR/browser
proof. Packaging/installed changed workflow acceptance remains outstanding.

Owned build40835548156 dispatched 2026-10-29 ~07:27Z, exact SHA verified, self-hosted,
prepare_only=false, both large-upload flags=false, reviewed PyAV beta20 asset.
Reuse signed station C:/CivicCastTester/preserved-8fdce69e-20261029/kit/station.
Prior 8fd candidate-root and kit moved intact to that preservation directory before
workflow prune (original candidate and kit-staging locations); baseline148c kept.
Restore original historical locations after build pruning ends. C free~173GiB,
RAM free~6.8GiB before serial build; recheck before isolated acceptance.

Owned hidden runner session33604, ListenerPID33472 parent12788, started07:27:12Z.
Owned gh build waiter session30686, log work/installer-40835548156.log under coding
chat cwd. Expected terminal: signed candidate/kit plus successful workflow, then
isolated native-guest-filesystem install/run/uninstall and packaged agenda proof.
Cancellation: gh run cancel40835548156, verify terminal; Ctrl-C owned waiter/runner
sessions after checking process identity. Automatic informational Gate A may enqueue
after build; prevent it from expanding this bounded isolated acceptance assignment.
No publication/tag job is in the build workflow. One Sol6.1 medium document_processor
prepares bounded guest-only agenda proof; no nested workers or station access.

Attempt1 ended failure before packs/signing: reviewed-pyav-reuse scratch directory
already existed (New-Item without Force). Preserved empty stale directory under
preserved-8fdce69e-20261029/reviewed-pyav-reuse; one failed-job-only rerun requested
07:37Z. Same candidate/run, completed manual and verified station bundle reused.
Old waiter30686/cell293 terminal exit1. Current sole waiter70886, log
work/installer-40835548156-attempt2.log. Runner33604 remains owned. No source change
or weakened checks; no repeated signing build yet. Guest-only proof prepared,
not executed, under coding chat work/installer-acceptance.

Build40835548156 attempt2 SUCCESS, signed kit assembled08:09Z. Waiter70886/cell303
terminal exit0; runner33604 terminal after canceling automatic informational
GateA40840114130 (all jobs terminal; no acceptance VM launched by that run).
Old candidate/kit restored to original8fd paths after pruning ended. Signed setup
261413760bytes, SHA2568a37e2bc48d1f0d63fde088c682730f9b2b49e812e674168ddf462b5b01b9f34,
Scott Converse Authenticode Valid, Microsoft timestamp, thumbprint
FDD65C665C426DFCACF23FA544B0E1E15614CF83. App report binds clean ead567a3 source.

Owned isolated acceptance Sandbox6a5b48c7-c4a2-4223-b043-f41e3dde3287 launched
08:11:49Z,12288MB,network/vGPU disabled; kit/proof/fixtures readonly, guest native
C:/CivicCastInstall. Evidence C:/CivicCastTester/civiccast-ead567a3-20261029.
Expected terminal receipt passed after install/signature/activation/agenda/uninstall
preservation; guest shuts down. Cancel exact ID with wsb stop, verify absent.
Prelaunch free physical~13.6GiB, disk~136GiB; no other Sandbox active. Prior accidental
WindowsSandbox.exe --help empty Sandbox stopped after verified ownership and logged
in existing failure log. Never use that GUI launcher for CLI help; wsb is CLI.
Installed agenda proof is bounded240s, four fresh pages plus blank PDFium render;
retained258 checkpoint reused. No full-packet read/OCR repeat or station action.
Headless Sandbox has no ExistingLogin (0x80070520); used supported wsb exec System
on exact owned ID. Guest machine CA7D38AB-FF78-4 independently identified (host is
different); lifecycle and proof guards bind to that guest name. Lifecycle PID6340
inside guest, hidden. Setup hash/signature passed; stage installing08:14:32Z.
Sole acceptance receipt waiter73228, blocking exec cell325,140-minute bound.
On terminal receipt inspect result, ensure exact Sandbox stopped; no generic VM kill.

First guest installed/activated successfully and all packaged agenda assertions
passed (four pages, actual PDFium render, installed six-module source hashes,
30preview/confirm/reopen, no preconfirm writes,258checkpoint unchanged). Wrapper
read null Start-Process ExitCode and wrongly marked failure; guest shutdown before
uninstall. Receipt retained unchanged, waiter73228/cell325 exit1, firstVM absent.
Corrected local acceptance wrapper to retain System.Diagnostics.Process handles,
drain output asynchronously and read exit before disposal. No product/source/build
change. Fresh same-byte lifecycle guest6c268bdd-f813-4248-910a-19b9cfbd988d started
~08:36Z, evidence C:/CivicCastTester/civiccast-ead567a3-20261029-retry. Installer
and uninstaller wrappers use held process handles too; failed guest now remains
available for diagnosis/uninstall instead of unconditional shutdown. Host untouched.
Cancel only this exact Sandbox via wsb stop; no new packaging build.
Current sole receipt waiter72811/blocking cell335; stage installing08:37:26Z.

Previous source delivery receipt follows.

# DELIVERED: retained PDF processing and agenda preview — October 29, 2026

Both authorized PRs are normally merged. PR280 merged at 2026-10-29T07:16:02Z,
merge ead567a33614a65554a69ef186342d691c43b7dc, final source head
01d48cd10d09af84f3f5a68b8a1aa3928acca3b7. PR279 receipt follows below.
Final ci-test run40828194825 passed Unit tests, WS4 and Claims-evidence verifier.
All applicable CI passed; three packaging-only checks skipped. Optional network and
randomized checks also passed, without becoming gates. Public a11y passed one
failed-job rerun after diagnosis of an unchanged WebKit video.play timeout; no
claim that the intermittent media test itself was fixed. Normal review's legacy
parent-only import conflict was fixed with red/green evidence and resolved.

Final browser receipt verifies integrated source hashes, 30 correct entries from
the real 258-page packet, zero writes before confirmation, authenticated original
bytes/hash, and persistence across an isolated backend restart. Native headless PDF
viewer rendering remains unverified; Blob bytes and page links were verified.
All owned development helpers and the final CI waiter are stopped. Station/data
unchanged. No L1/L2, installer rebuild or publication. No further work authorized.
This local final receipt is not pushed as a bookkeeping-only CI restart.

Pre-merge implementation evidence follows; pending statements below are historical.

Root owns coding and delivery. Current Coder now reports Scott assigned it watching/
nudging only; it declined final candidate audit execution. Do not wait on that chat
as an auditor or claim an audit it did not perform. Normal PR review and required CI
remain applicable. Sol6.1 medium completed bounded processor/parser/UI work; no
DeepSeek. Preserve station/data, no installer/publication/L1/L2/paid OCR/LFS/reboot.

PR A DELIVERED: PR279 merged normally at 2026-10-29T05:52:48Z, merge
26c53c6a284ea4c627c48c26f1ac3249db1dbda1, final head25883c90b2a1f97e726e71c8dfbf2725cc596133.
Applicable CI passed including unit/claims, Windows/runtime, native reproducibility,
a11y, operator, lint/types/security/docs/policy. Optional randomized-suite canceled
by owner pause was not made a gate. Earlier named auditor checked757/441; root fixed
its legacy checkpoint carry-forward finding in2a304e4 with focused proof. No final
258 audit by that chat is claimed. CI run40819594549 completed successfully.

Candidate B: C:/Dev/cc-pdf-processing, feat/agenda-packet-preview, based on actual
PR279 merge26c53c6a. Supported agenda-section parsing preserves 30 ordered parents/
children on pages1–4 and excludes attachments; HTML children retained too. Durable
preview/resume/cancel and authenticated original inspection precede explicit confirm.
Resume pins the original hash across amendments; incomplete results and operator
changes refuse confirmation. Other providers retain their existing import route.

Passed draft evidence (unchanged core behavior is reused): public Longmont3382,
document17338/template15247,41,472,505 bytes,258pages,SHA256
b35f5679e9981ec5cb5dc048ae9640f187c2babf5fd1c877c628bdc9e83588a0.
Isolated development API/SQLite workflow refreshed the real download, reused the
identical completed258-page checkpoint, previewed exact30 expected entries/pages,
verified zero writes before confirm, then exact stored titles/numbers/order/links
and idempotent confirm. Receipt work/packet-acceptance/preview-workflow-proof.json
under coding chat cwd. Prior full258 read292.375s, interruptionafter12/resume and
local image-only OCR proof remain valid. No installed-station acceptance claimed.

Draft checks: parser11, HTML4, preview-store5 incl pinned-revision resume; router44
passed initially plus corrected legacy-source error case passed separately (one
unrelated optional skip). UI43 passed, TypeScript and affected ESLint passed after
fixing protected PDF viewing to use the credential-aware Blob helper. Original/hash
and semantic expected fixtures independently verified against rendered pages.
TownReporter pinned e07612de20cd5e035faaf2201d548d4d67da7be3; behavior reused in Python.

Integrated checks:119 passed,1 optional agenda-js-import skip in17.71s across the
six affected parser/preview/router/PrimeGov suites. Strict mypy passed all6 affected
source modules; Ruff lint/format clean. API schema/reference/types regenerated.
UI43 tests, TypeScript and affected ESLint passed in the identical draft source;
TypeScript was also checked with the integrated generated API types.

PR280 open; follow-up candidate includes the two reproduced corrections below.
Remaining: applicable required CI and normal merge. No extra broad suites/audit
ladder. No station processes started.

PR280 initial head13860373: normal GitHub review identified old parent-only import
order collisions. Red reproduction confirmed the17-to30-item mismerge; confirmation
now refuses numbering/order conflicts before writing and preserves matching edited
items. Actual browser acceptance also reproduced Windows AccessDenied during JSON
replacement while polling; bounded310ms sharing-error retry preserves atomic records.
Affected preview/document regressions15pass; affected strict mypy clean. Final
browser proof passed after correcting the isolated SQLite fixture to attach a real
file-backed schema (the first fixture used an in-memory attached schema). The old
failed preview stayed unchanged; no baseline rebinding. A fresh normal-UI preview
reused the same original/258-page checkpoint with zero parser workers. Before confirm:
zero items,30 exactpreview titles/pages; authenticated UI Blob41472505bytes/exactSHA,
30page links. Actual UI confirm stored30items; serverrestart preservedall30. Native
headless PDF popup reached commit/networkidle, but viewer DOMContentLoaded did not
fire; do not claim a PDF-viewer DOM assertion. Receipt/screenshots under coding-chat
work/packet-acceptance/browser-workflow-proof.json. No station changes.
Optional live-network CI received Longmont HTTP403 for both list endpoints from
GitHub runner (7others passed); no optional gate or retry added. Current Coder is
watcher/nudger only. Agent document_processor owns the bounded browser proof and
must stop its hidden development helpers after it; root owns follow-up commit/CI.

---
Historical handoff and PR278 receipt follow; preserved from prior candidate.
# CivicCast current handoff — October 27, 2026

Owner standing instruction: maintain the coordinator failure log at C:/Users/scott/Documents/Codex/2026-10-19/i-cannot-get-astra-to-finish-2/outputs/CODEX-FAILURE-LOG.md. Record future directive violations/material avoidable mistakes with evidence and impact for a possible OpenAI report; do not publish without instruction.

## CURRENT — DELIVERED: PR277 REVIEW CORRECTIONS (PR278) — October 28, 2026

DELIVERED. PR #278 https://github.com/scottconverse/civiccast-native/pull/278 merged to
`main` at 2026-10-28T23:46:47Z: merge commit `ba80846330108bf300e48bb64507b748c099b8d7`,
merged source head `065599fe4d5e0d6a7e7b1cf339e422a4a73c620d` (ordinary merge, no
admin/bypass/auto; one ordinary follow-up commit after the bounded review, no force push).
Required CI on `065599fe`: 20 checks passed, 3 packaging-only checks skipped, none failed
(Unit tests, claims-evidence verifier, Windows native guard, WS4, Two independent Windows
workspaces, reproducibility, lint/type, security, operator, a11y, docs, policy). Optional
agenda-integration and randomized suites also passed; neither is a gate. Main head verified
as `ba808463` locally and via `git ls-remote`.

This local receipt carries root's earlier local Phase 1 completion record (still below,
historical) and was not pushed as a bookkeeping-only commit, so CI was not restarted for it.

Owner ordered ONE focused correction PR for three automated-review findings reproduced
against final PR277 head `3de427a5`. DeepSeek Flash1017-cloud LOW is sole serial executor;
root coordinates. Branch `fix/pr277-review-corrections` from actual current main
`6d43e9a7` (PR277 merge). Scope is only these three defects plus their regressions and the
affected docs/types; no Phase 2, no installer/release/tag/publication, no LFS/spending, no
station API/probe/DB/restart/install action, no frozen-candidate edits.

CORRECTION 1 — failed page could still be imported as complete
(`civiccast/agenda_import/docparse.py`). Diagnosis: a page whose `extract_text()` raised was
replaced by empty text, so a document with a lost middle page reported `partial=False`/
`reason=None` whenever any other page yielded numbered items, and the caller persisted an
incomplete agenda. Fix: `PdfExtraction.pages_failed` records every failed page; a
non-zero count always ends as `partial=True` — `reason="page_failed"` when items were
recovered from other pages, `reason="unreadable"` when EVERY page failed (the existing
"nothing usable" outcome). `PrimeGovSource` refuses either before persistence
(`pdf_partial`, now naming the unreadable page count; `pages_read` counts only pages that
yielded text). Regressions: middle-page failure, first-page failure, all-pages failure
(`tests/agenda_import/test_docparse.py`), plus a PrimeGov refusal test asserting the
partial result never reaches the mapper (`test_primegov.py`).

CORRECTION 2 — text budget skipped single/final pages
(`civiccast/agenda_import/docparse.py`). Diagnosis: the overrun check ran after the page
was parsed and appended and only when another page remained, so a single-page or
oversized FINAL page bypassed the 2,000,000-character cap and imported as complete. Fix:
the remaining budget is checked BEFORE each page's text is parsed or appended, on every
page; an overrun sets `partial=True`/`text_limit` and the page is never parsed, so no
partial item list is produced. Text totalling exactly the limit is complete when every page
was covered (no off-by-one in either direction). Hard subprocess timeout, page cap and
other providers' legacy `extract_items_from_pdf` are unchanged. Regressions: single-page
overrun, final-page overrun, exactly-at-limit completion (`test_docparse.py`) and a real
subprocess single-page overrun refused before import (`test_primegov.py`, tiny budget).

CORRECTION 3 — discovered archived year could not be imported
(`primegov.py`, `router.py`, operator `AgendasScreen.tsx`). Diagnosis: `since` selects any
archived year for discovery, but the import lookup searched only the current year and the
two prior ones, so a meeting discovered with e.g. `since=2023-01-01` in 2026 returned
not-found. Fix: smallest compatible explicit year context — optional PrimeGov-only
`archive_year` field on the existing import-external body (rejected 422 for the other
vendors or for a future archive year; years 1 through the current UTC year are supported), forwarded through the
`AgendaSource.fetch_agenda` Protocol keyword (other adapters accept and ignore it), and used
by `_find_meeting` as the primary archive year (upcoming + that year + current year = at
most three finite lists). The operator console sends the selected meeting's year (else the
`since` year) for PrimeGov only, and shows a one-line hint naming the finite scope. Provider
ids, dedup, explicit operator selection and partial-failure warnings are unchanged; blank
`since` keeps the previous current-year discovery and current/current-1/current-2 import
behavior. Regression: discovery of an archived meeting three years older than the current
year is offered, is NOT findable by the pre-correction lookup, and imports with the explicit
year (`test_primegov.py`), plus router tests for forwarding/rejection (`test_router.py`) and
a UI payload test (`AgendasScreen.test.tsx`). Generated `docs/openapi.json`,
`docs/API-REFERENCE.md` and `api.generated.ts` regenerated with the repo script;
`docs/ops/agenda-import-runbook.md` updated for the bounded/page-failed and finite-year
behavior.

REVIEW FINDINGS CORRECTED (bounded independent read-only DeepSeek review of candidate
`87154b03`, coordinator outputs `deepseek-phase1-correction-review/result.json`; the reviewer
confirmed corrections 1 and 2 by reading and filed two MATERIAL outcome-3 findings, both
year-scope boundary mismatches):

- Finding 1 (high) - the upcoming list is not year-filtered, so discovery can offer a meeting
  dated in the NEXT calendar year; the console then sent that future year as `archive_year` and
  the import was refused 422. Corrected: an offered upcoming meeting needs no archive year at
  all (the import lookup always reads the upcoming list first), so the console sends the year
  only when it is a valid archived-list year (current or earlier) and sends none otherwise.
  Regression: `AgendasScreen.test.tsx` "next-year upcoming meeting sends no archive year", plus
  `test_primegov.py` "next-year upcoming meeting is importable without a year scope".
- Finding 2 (medium) - an arbitrary 1990 import floor was not shared with discovery (`since`),
  so a `since` before 1990 offered meetings import refused 422. Corrected: the invented floor is
  gone; one shared rule in `primegov.archive_year_supported()` (current UTC year or any earlier,
  floor = year 1, i.e. every year a `since` date can express) is used by BOTH the import request
  validation and (via `_archive_year`'s clamp) discovery, so the offered set and the importable
  set are identical. Regressions: `test_primegov.py` "archived year before the old floor is
  offered and importable" and `test_router.py` "a year below the old floor is accepted". No
  unbounded scan: the import still reads at most three finite archive lists, and the future-year
  rejection remains for genuinely invalid input.

EVIDENCE REUSED (not re-fetched): the recorded real Longmont eight-item agenda import
(meeting3443/template15544) and the 41,472,505-byte/258-page packet from
coordinator `outputs/deepseek-primegov-phase1/result.json` and the retained TEMP receipt.
This correction does not invalidate them: the defaults (500 pages / 2,000,000 characters),
the 258-page packet's "not partial" outcome, and the eight-item extraction are unchanged by
the budget-check reordering, and no document was downloaded again.

OWNED JOBS: none. Final-head required CI40781932797 and companion checks are terminal green; PR278 merged normally. Executor and bounded reviewer are terminal. Root independently verified merged identity and 20 successful/3 expected skipped checks. Regression receipt: coordinator outputs/deepseek-phase1-corrections/result.json; read-only review: outputs/deepseek-phase1-correction-review/result.json. No reruns or station actions.

NEXT ACTION: none owed for this correction; await owner direction. Do not start Phase 2,
packaging, or any station action automatically. No owned job remains (the only owned job was
the PR278 required-CI run on `065599fe`, which reached its terminal green state; no local
watcher process is left running).

---

## HISTORICAL — PHASE 1 DELIVERED — October 28, 2026

P1 public PrimeGov PDF retrieval, P2 bounded download/extraction, and P3 archived/upcoming discovery are implemented and merged. Phase 2 is not started.

- PR277: https://github.com/scottconverse/civiccast-native/pull/277
- Final source head: 3de427a5628c924d2a89290a61f101bbb48382be.
- Verified merge: 6d43e9a70e253586191d6ecbc9eadf22778b37f5, October 28 at 16:43:02 UTC.
- Required CI40716937602: Unit tests, Windows native guard and claims-evidence passed. All applicable lint, security, operator, accessibility, docs, policy and reproducibility checks passed. Three packaging-only checks skipped as expected. Optional agenda integration and randomized checks also passed; neither was promoted to a gate.
- Windows first attempt rejected a hosted-runner WSL AppxManifest.xml sampled handle during closure provisioning. Its one failed-job-only retry passed; successful Unit/claims evidence was reused. No product workaround or checker weakening.

Delivered behavior: null document links use templateId/compileOutputType to construct the public PDF URL; explicit links and HTML remain supported; agendas rank ahead of packets/minutes. PDFs download with a 100 MiB cap and 180-second streamed budget. Extraction has 500-page/2-million-character caps and a hard 60-second subprocess timeout that terminates a stuck parser. Unreadable/oversize/partial results return honest errors and cannot silently replace a complete agenda. Discovery merges upcoming meetings and one requested/current archive year, deduplicates IDs, shows partial-result warnings and errors when both lists fail. Redirects use the existing literal-host guard on every hop; DNS-rebinding protection is not claimed.

Actual workflow: isolated development FastAPI router and temporary SQLite database, real Longmont provider. Discovery returned 282 meetings (5 upcoming, 277 archived). Archived meeting3443/template15544 imported eight ordered agenda items with the correct source URL. Cancellation-only meeting3489 returned an explanation and stored zero items. The recorded 41,472,505-byte/258-page packet was read without truncation; its 210 extracted numbered lines prove resource-bound compatibility, not semantic validation of 210 agenda items. The changed subprocess path also returned the eight-item agenda successfully. Focused Python/UI/static checks passed; see the executor receipt for exact command-level results.

Limitations: extraction recognizes flat digit-numbered lines, not arbitrary layout/OCR or ITEM-prefixed/split-number headings. Such PDFs return pdf_no_items honestly. Import lookup automatically searches the current and previous two archive years. The real walkthrough used an isolated API/database, not an installed browser session. No installer, release, tag, LFS transfer or running-station action occurred.

Evidence: coordinator outputs/deepseek-primegov-phase1/result.json and activity.jsonl; retained temporary workflow receipt under %TEMP%/phase1-primegov/receipt.json. The owned temporary database was removed. DeepSeek Flash1017-cloud LOW performed engineering/checks; coordinator independently confirmed GitHub merge and final check conclusions. No independent review was claimed.

No owned job remains. Next action: await owner direction; do not start Phase 2 or packaging automatically. This local handoff completion update is bookkeeping only and does not justify another build or CI run.

---
Historical task records below are retained for provenance and are not the current task state.
## HISTORICAL - IN FLIGHT: workflow-policy assertion fix for required CI Unit - October 27, 2026

Owner authorized (DeepSeek LOW sole executor, root coordinates) fixing the required
CI failure introduced alongside merged PR #275 and getting required CI green, with
no extra tests, no rebuild, no LFS, no spending and no station action.

- DIAGNOSIS (from existing log, not re-run): main `346605a0` source CI run
  `40629526280`, job `Unit tests`, `11806 passed, 106 skipped, 5 deselected`.
  Single failure `tests/policy/test_native_beta_candidate_workflow.py:307`
  `assert text.count(reviewed_download) == 1` -> `0 == 1` in
  `test_native_beta_candidate_workflow_builds_signed_artifacts_without_publishing`.
  That assertion hardcoded the pre-#275 reuse command
  `gh release download $env:REVIEWED_RELEASE --repo $env:GITHUB_REPOSITORY --pattern native-app-payload.ccpack --dir $reuse`.
  PR #275 deliberately moved the reviewed PyAV wheel to its own 4 MB release asset,
  so the workflow step now reads
  `--pattern $env:REVIEWED_WHEEL_NAME --dir $reuse`. Workflow behavior is correct
  and strictly better (no mutable product pack in a CI build input, download-only);
  the stale policy assertion was the only failure. Product code, signing,
  installer bytes and published acceptance evidence are unaffected.
  The downstream claims-verifier failure is missing JUnit evidence from the failed
  producer (job never executed its JUnit-upload step), not a product/tests failure.
  Optional randomized suite failure stays NON-GATE; not run, not fixed, not waited on.
- CANDIDATE: branch `fix/policy-reuse-download-assertion` from exact main
  `346605a0409988f1f86e4845e04e82b10d970deb`. CHANGED BEHAVIOR: one policy
  assertion now expects the actual reviewed-wheel download line and additionally
  pins the step's `REVIEWED_WHEEL_NAME` to `av-18.0.0-cp311-abi3-win_amd64.whl`.
  Unchanged and still asserted: exactly one `gh release` occurrence in the
  producer (that download), no `gh api`, no publish/upload/edit verb, no
  `--allow-development-key`, signing/trust-root/artifact/checksum/reuse safeguards.
  The wheel pin itself is NOT repinned or undone (`0f9427a4...`/4,347,090 bytes).
- EVIDENCE REUSED: PR #275 merge `346605a0`; the same run's `11806` passing tests;
  WS4 and "Two independent Windows workspaces" already passed on that head;
  published installer `8fdce69e`/`2c6d2614...` correct and untouched.
- OWNED JOB: required `ci-test` on this branch's PR (Unit + applicable claims/
  security jobs), triggered by the branch push only - no manual dispatch, no
  signing build, no rebuild. Expected terminal: PR required checks green.
  Cancel method if mis-owned/wedged: `gh run cancel <run id>` for that run only.
  Exact run/PR identities are recorded at receipt below rather than in a second
  bookkeeping commit, so CI is not restarted for documentation.
- NEXT STEP: normal reviewable PR, bounded watch of the auto-triggered required
  checks, then normal merge; no admin/bypass, no tag/release/rebuild/station work.

## HISTORICAL — DELIVERED: Beta20 recovery-fix package published — October 27, 2026

Scott authorized publishing the prepared signed Beta20 (source `8fdce69e`, build
`40616252842`, setup SHA-256 `2c6d2614...388e4b`) and updating public download
links, with no rebuild, re-sign, extra test, LFS transfer, spending or
host-station action. Sole executor: DeepSeek LOW
`work/deepseek-beta20-publish.py` (activity/state in
`outputs/deepseek-beta20-publish/`); root coordinates only.

Routine decision under the delegated rule — revision convention. The
`v1.0.0-beta.20` tag and release already exist (tag target
`a17449189a39af6474d914137eab020169f91023`, PR #259 publication, revised
October 22 for documentation).
- Option A — new revision tag `v1.0.0-beta.20.1` plus a new release. Pro:
  distinct tag identity for the new bytes. Con: no existing convention for a
  patch tag on this line, a second 26-asset release (or an incomplete
  complete-kit set), a release-truth `current` switch and extra history.
- Option B — in-place asset revision of the existing published release, keeping
  the tag and its history, exactly as the October 22 documentation revision did
  (its truth note already reads "use revision source for this package" with the
  original tag target unchanged). Pro: existing convention, tags/history
  preserved, public links stay valid, complete kit stays available, no new
  release infrastructure. Con: published bytes change under one tag, mitigated
  by explicit revision-source notes in the release body, truth manifest and
  verification record. **Recommended and taken.**
- Option C — move/force the existing tag. Forbidden by policy; not considered.
No tag was created, moved or deleted, so no tag/release workflow hook could fire
a duplicate build; this repo's workflow triggers are workflow_dispatch,
PR/branch-push and workflow_run, none tag-based.

DELIVERED (public):
- Release `https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.20`
  remains published (not draft), prerelease, tag target
  `a17449189a39af6474d914137eab020169f91023`, 26 assets.
- Installer
  `https://github.com/scottconverse/civiccast-native/releases/download/v1.0.0-beta.20/setup.exe`
  SHA-256 `2c6d261445567c168ad9f7fa7bdbd085b5dc8f8d5bcda59e41cde86ac6388e4b`
  (GitHub asset digest matches the prepared bytes), revision source `8fdce69e`,
  build 40616252842, installed payload carries merged PR #273.
- Provenance tag `archive/v1.0.0-beta.20-package-revision-20261027` (annotated) points at the exact revision source
  `8fdce69e4fccfba80f2984a0d034d8851b4840e0`. It is a provenance marker only: no
  release, no assets, no version change; the published `v1.0.0-beta.20` tag stays at
  `a17449189a39af6474d914137eab020169f91023`. No workflow in this repository filters
  on `tags:`, so pushing it triggers no build.
- Nine assets replaced with the prepared bytes (setup, sidecar, app-payload and
  server-binaries packs, four manual files, SHA256SUMS.txt); the other 17,
  including all 12 station-model parts, `station-models.json`, the
  CUDA/FFmpeg/Ollama packs and the downloader, are unchanged and keep their
  original published digests. 26 assets, 26,921,298,251 bytes.
- Public download surfaces updated: release notes (revision section),
  `docs/releases/release-truth.yaml`,
  `docs/releases/v1.0.0-beta.20-verification.md`, `README.md`. Existing landing
  page/install-guide download URLs were already correct and unchanged.
- Accepted evidence reused, not repeated: isolated Sandbox lifecycle acceptance,
  signatures, source CI 40583199781, PR #273 merge. Informational Gate A
  40620078397 and the optional randomized suite remain non-gates. No long-soak
  requalification and no host-station action.
- Live station, tags/history and unrelated dirty work (soak/YouTube evidence,
  coordinator failure log, .tmp files) preserved.

PUBLICATION-INDUCED CI COUPLING - CORRECTED (no rebuild, no re-pin, no re-sign):
- Symptom: `native-app-reproducibility` / "Two independent Windows workspaces"
  (run 40628662572) failed on PR #274's head and on main pushes after publication.
- Cause: its reuse step downloaded `native-app-payload.ccpack` from release
  `v1.0.0-beta.20` and required the embedded `av-18.0.0-cp311-abi3-win_amd64.whl`
  to equal the reviewed pin 0f9427a4.../4,347,090 bytes. The revised pack carries
  the self-hosted lane's advisory-built wheel (4,346,966 bytes / f4fb5079...; same
  pinned PyAV sdist and FFmpeg commit in its own provenance file, advisory hash by
  documented policy). So a CI build input depended on a mutable product pack.
- Correction taken (root-recommended option under the delegated rule): the exact
  reviewed wheel bytes were retrieved - not rebuilt - from the retained pack
  `outputs/beta20-documentation-revision/kit/packs/native-app-payload.ccpack`,
  validated against the existing pin (4,347,090 bytes / 0f9427a4...) and published
  as their own small release asset `av-18.0.0-cp311-abi3-win_amd64.whl` on
  `v1.0.0-beta.20` (GitHub asset digest matches the pin; 4.3 MB, no pack re-upload).
  Both reuse steps now download that asset directly and keep every existing
  reviewed size/hash assertion (`EXPECTED_WHEEL_BYTES`/`EXPECTED_WHEEL_SHA256` read
  from `scripts/build_native_pyav_wheel.py`, plus the reviewed wheel name).
- The asset is CI provenance/reuse only: not part of the installable kit, never
  installer-required, and deliberately not listed in `SHA256SUMS.txt`, which stays
  the checksum authority for the 26-asset kit. Release notes, verification record,
  release truth and `docs/handoff/BUILD-ASSETS.md` now separate the two sets.
- Superseded: the earlier rebuild recommendation is withdrawn. No product rebuild,
  no wheel re-pin, no re-sign, no installer change, no new tag/release, no station
  action. Preserve this asset across future revisions instead of replacing it.

## HISTORICAL - Signed installer READY FOR OWNER TAG/PUBLISH APPROVAL - October 27, 2026 21:32 MDT

**Result: signed public-ready installer built and required isolated acceptance
passed. Nothing has been tagged, published or installed over the running host
station.**

- Exact source: `8fdce69e4fccfba80f2984a0d034d8851b4840e0` (normal PR273 merge;
  final PR head `1412c0060db6863f983dc381f1e51597a0930296`).
- Version: `1.0.0-beta.20`; established source version, no version-source edit.
- Build: `native-beta-candidate-artifacts` run `40616252842`, success.
  Required source CI `40583199781` passed Unit/Windows WS4/claims; final PR
  22 passed/3 expected skips. Optional randomized failure not a gate.
- Signed setup:
  `C:\CivicCastTester\candidates\8fdce69e4fccfba80f2984a0d034d8851b4840e0\candidate\CivicCast (Native)_1.0.0-beta.20_x64-setup.exe`
  SHA-256 `2c6d261445567c168ad9f7fa7bdbd085b5dc8f8d5bcda59e41cde86ac6388e4b`.
  Authenticode Valid, Scott Converse, thumbprint
  `A987FBF8678961387F2CCB44A1DEDDB79E3DA879`, Microsoft timestamp.
- Kit: `C:\CivicCastTester\kit-staging\8fdce69e4fccfba80f2984a0d034d8851b4840e0`.
  Embedded signed station-index SHA-256
  `751e18ab976abe7d1a81e301ee0edfe683a5ec50d35e8f1ca4edb45eff1f6272`.
- Merged outage fix is present in the packaged app payload:
  `payload/Lib/site-packages/civiccast/native/gstreamer_runtime.py` SHA-256
  `959540cd0c5ea8db85427666d1d6f2b898d5fa5448013ef655aaf0007fd363f6`,
  byte-identical to source.
- Required F5-route isolated acceptance PASSED on one 12,288 MB Windows Sandbox
  native-guest-filesystem install: sandbox `85a5964d-332c-473a-be6e-e506893164d7`,
  started `2026-10-28T03:04:02Z`, finished `2026-10-28T03:30:25Z`. Installer
  exit `0`; service Running; `/health` healthy Beta20, current schema
  `0097_agenda_minutes`; offline AI/caption activation passed.
- Installed launcher SHA-256
  `611cd261013157052f90bd347b51ec4a0024a8923e2122730153739550a5f744` and
  uninstaller SHA-256
  `049878696004b2bdef7dfed2f1e336b83766c9cca964217c7e3549b9e57acc4a`
  both Authenticode Valid (Scott Converse, Microsoft timestamp).
- Real uninstaller exit `0`; upload sentinel and PostgreSQL `PG_VERSION`
  preserved; runtime/packs/launcher/service removed.
- Informational Gate A run `40620078397` failed its mapped-folder clean lane at
  service-identity ACL migration (`d4-service-registration=70`, setup exit `118`)
  and restored prior state. Policy confirms Gate A is informational only; its
  dirty/download/soak follow-ons were skipped. Exact orphan cleared; no active
  sandbox remains. The successful required acceptance above used the native
  guest filesystem, not mapped install.
- Evidence/report: `docs/ops/signed-installer-prep-2026-10-27.md` and
  `docs/ops/evidence/signed-installer-prep-2026-10-27.json`; raw guest receipt
  remains at `C:\CivicCastTester\civiccast-8fdce69e-20261027\receipt.json`.
- Stop boundary: READY FOR OWNER TAG/PUBLISH APPROVAL. No tag, release,
  publication, LFS transfer, spending, reboot, or host-station action occurred.

## Current task — October 27, 2026: black/silent outage cause, fix, local hotfix

## DELIVERED — October27, 2026 15:56 MDT

PR273 MERGED normally (no bypass) at2026-10-27T21:56:58Z.
Final PR head1412c0060db6863f983dc381f1e51597a0930296.
Main merge8fdce69e4fccfba80f2984a0d034d8851b4840e0.
Root independently verified live GitHub MERGED state and merge SHA.
Required source CI40583199781 passed Unit tests, Windows WS4 real probes and
claims verifier. Final PR check snapshot:22passed,3expected skips; optional
randomized suite failed and was not an acceptance gate. Accessibility retry
passed. Security/lint/docs/operator checks passed. No waiver of required checks.
DeepSeek LOW performed corrections and normal merge. Local affected checks158passed.
Station hotfix remains installed; earlier600-probe/reload/all3-channel A/V
acceptance preserved. No station action, installer rebuild or extra test in this
delivery continuation. No tag/publication or95hour soak requalification.
Worker began an unnecessary additional post-merge wait to strengthen evidence;
coordinator ended that owned wait after verified authorized delivery. Main's
automatic security/pages jobs had started; their results are NOT claimed here.
Existing report: docs/ops/black-silent-hotfix-2026-10-27.md and evidence JSON.
Reason for outage/fix: repeated DLL-directory registrations exhausted Windows
loader search list, preventing reload/recovery; locked per-directory registration
cache prevents repeated registrations. Local backed-up module hotfix is active,
not a newly published signed installer. This work is delivered; historical
checkpoints below are not instructions to restart it.

## Application-upgrade checkpoint — October 27, 2026

PAUSED by Scott. Preparing for application upgrade does not authorize resumption.
No native DeepSeek runner is running (verified by process command line).
The watch civiccast-black-silent-fix-delivery is PAUSED. Latest branch CI runs
are terminal; PR273 has NOT merged. No work may resume until Scott says so.

Repository: C:/Dev/civiccast-f4-f6-f7
Branch: fix/black-silent-20261027
HEAD: 8ee3c4f16660fb076479289e00edb0a5c2375a4e
PR: https://github.com/scottconverse/civiccast-native/pull/273
Required CI run40574997367 failed Unit tests, WS4 Windows guard, and downstream
claims verifier. Optional randomized failure is not an added gate.
Unit failure: new deterministic test patched global os.name, breaking POSIX path
validation; second new real-loader test skipped where no installed runtime exists,
which the existing pure-suite gate rejects. Worker left an UNCOMMITTED correction
in tests/native/test_gstreamer_runtime.py: module-local os shim and removal of
the CI-inapplicable real-loader test; 12 focused checks passed on this Windows host.
This does not prove Linux CI or explain the separate WS4 missing-JUnit failure.
Worker stopped during native collection counts; no completed result is available.
On explicit resumption, inspect preserved diff and actual WS4 failure logs; finish
only concrete required corrections, update affected exact counts/blob bindings
together if needed, required CI, then normal merge. No unrelated checks/rebuilds.
Existing real installed600-probe/reload/A/V proof remains valid; do not repeat.
Station hotfix remains installed with backup; no station action needed for upgrade.

Execution route on resumption: ONLY DeepSeek v4.1 Flash1017-cloud via Ollama LOW.
Root coordinates. Existing runner:
C:/Users/scott/Documents/Codex/2026-10-19/i-cannot-get-astra-to-finish-2/work/deepseek-black-silent-native.py
Conversation/state/activity:
C:/Users/scott/Documents/Codex/2026-10-19/i-cannot-get-astra-to-finish-2/outputs/deepseek-black-silent-native/
result.json is stale phase1; do not use it as final delivery evidence.
Resume SAME conversation via DEEPSEEK_FOLLOWUP_FILE only after explicit owner
resumption. Last conversation ends with interrupted shell tool call: append an
honest tool response that count collection was interrupted/no result before asking
DeepSeek to continue. Never fabricate the missing result. Old PID10764 is dead;
never reuse that PID without ownership verification.

Preserve untracked soak/YouTube evidence and temporary work; no cleanup/reset/stash
or broad git add. Only CURRENT.md and the test file are currently modified tracked
files. No LFS, spending, installer rebuild, sandbox, reboot, physical hardware,
tags or publication. Hidden ConPTY/children; gh telemetry stays disabled.
No tests or code changes were made during application-upgrade preparation.


Delivery continuation: installed hotfix and all-channel A/V proof passed.
PR273 current head8ee3c4f1 (20:05UTC) renews six workflow-blob references
required by existing claims-policy CI after the collection-floor correction.
141 affected claims-policy checks and21 collection-policy checks passed locally.
Prior294050b9 CI exposed that binding drift; it did not complete green.
Sole DeepSeek LOW worker PID10764 owns final-head CI wait and normal merge.
No repeated outage proof, station samples, rebuilds or duplicate waiters.
This local state update needs no bookkeeping commit or CI restart.


The worker/session/route lines that used to open this file recorded the first,
now-superseded investigation pass; the outcome below replaces them. Historical
detail remains in the dated sections further down.

**Result: cause proven and corrected; the corrected Python module is deployed on
this TEST host and the installed station is verified airing real program A/V on
all three channels.** Cause is observed, not inferred: every content-reload
encoder probe re-ran `bootstrap_installed_gstreamer_runtime`, which called
`os.add_dll_directory` unconditionally; the Windows loader search path is a fixed
32,767-character buffer, so the 461st registration inside the long-running
control plane raised `FileNotFoundError [WinError 206] The filename or extension
is too long` (a 70-character, existing path) and every later probe failed
permanently. Reload dispatch therefore died, the channels pinned TRANSITIONING
with zero source-chain input while the mux kept emitting black/silent filler,
and every recovery retry re-entered the same poisoned probe.

- Source fix (branch `fix/black-silent-20261027`): per-directory registration
  guard in `civiccast/native/gstreamer_runtime.py`; red/green tests in
  `tests/native/test_gstreamer_runtime.py`; report
  `docs/ops/black-silent-hotfix-2026-10-27.md` with sanitized evidence JSON.
- Local test-host hotfix (NOT a signed package): installed
  `runtime\Lib\site-packages\civiccast\native\gstreamer_runtime.py` replaced
  through the elevated helper; the original is kept beside it as
  `gstreamer_runtime.py.bak-20261027-113835-local-hotfix`. sha256
  `ff8d8dc7adad05b9f0d433b4cea4432796083b97bee588869fd89bce1fb35e84` to
  `959540cd0c5ea8db85427666d1d6f2b898d5fa5448013ef655aaf0007fd363f6`.
  Launcher, uninstaller, manifest provenance, schedules, database and
  credentials are untouched.
- One bounded supervisor restart (11:39 MDT) released the exhausted loader
  state: all three channels returned ON_AIR on their scheduled programs, and an
  operator `reload` command on public was accepted through the previously fatal
  dispatch path, with zero dispatch errors and zero ERROR records afterwards.
- Installed FFmpeg observation at 11:41 MDT over three consecutive 2.5s segments
  per channel: all three 1280x720 H.264 + AAC; blackdetect reports no black
  segment; mean frame RGB about (120,108,100) public, (75,66,56) education,
  (77,73,75) government; mean_volume -9.7 to -21.0 dB and max -1.4 to -5.7 dB,
  against the outage signature of a black picture at -91.0 dB.
- Delivery: PR #273 (`fix/black-silent-20261027`) carries the source fix, tests, this
  ledger section and the sanitized evidence record; the required security gate failed
  on advisory drift published 2026-10-27T15:04Z (GHSA-j586-36cw-2gc2 / CVE-2026-114613,
  msgpack < 1.2.3), corrected minimally by `uv lock --upgrade-package msgpack`
  (1.2.1 -> 1.2.4, one lock block, no rebuild, no LFS). Local run of the CI gate over
  the exported lock scanned 128 packages with no findings and
  `scripts/check_pip_audit_allowlist.py` exited 0. No waiver, no allowlist entry.
- Owned background jobs: none. No endurance claim is made: the ~95h soak result
  and the historical filesrc11 cause are unchanged and not requalified.


## Current owner scope — YouTube live acceptance completed October 27

Scott requested setup and real YouTube validation. Installed Setup now holds the
existing authorized YouTube credentials. Installed Publish sent channel public to
unlisted event Ubr_pbAoTwc: YouTube active/good/live, CivicCast live, signed-out
Chrome visible moving video and non-silent decoded audio. CivicCast Stop passed;
YouTube complete/recorded. No owned sender/browser/helper remains. No code change
or rebuild. Report docs/ops/youtube-live-2026-10-27.md and matching JSON evidence.
This closes the previously deferred live YouTube check. One short run only, not
long-term endurance or caption acceptance. The historical station black/silent
failure remains UNRESOLVED; prior checklist/source delivery completion below does
not establish a fix for that operational failure. Scott deferred investigating it
until after this YouTube test. No tag/public release authorized by this test.


## Delivery complete — October 27, 2026, 02:57 MDT

PR272 merged normally at 08:57:42 UTC as 69c2bcaea51e573ab1fd707a42260ae14bdc6e9e,
head 677d800f16b1c67d5e849aefd6ccc9efc47e9d43. Independently verified GitHub
MERGED state and 21 successful checks / 3 expected skips. Source CI40471253320
passed, including Unit tests and Windows runtime probes. Completion receipt:
outputs/native-install-42339334/pr272-delivery.json in the coordinator workspace.
Completion process25248 is terminal. All selected roadmap outcomes are delivered
under the explicit F4 acceptance revision below: the historical filesrc11 cause
remains unknown, accepted as residual risk, never diagnosed by passing retries.
Native installed signed42339334 acceptance passed; PR272 source changes are merged
but are not packaged in those installed bytes. No tag or publication authorized.
Live YouTube transmission/playback remains deferred by Scott. No further work or
checks are required by this assignment. This completion supersedes waiting text
in the detailed execution record below.

## Current outcomes and owner authority

F1–F3, F5–F7 and N1–N8 are delivered through PR267–271; do not reopen them. F2 live
YouTube transmission/playback remains explicitly deferred, not a gate.
Scott resumed the work and explicitly directed native host installation/testing,
no sandbox, finish the goal. This supersedes the prior leave-station-untouched
restriction within that scope. Push/merge authorized; no tag/publication/release
replacement, LFS transfer/spending through October, reboot or physical hardware.

## Direct native installation: complete

The retained signed423393349f87148e7253f93691a9a70478cc120c candidate installed
on this machine at C:/Program Files/CivicCast (Native), exit0, 07:30:57–07:41:37UTC.
Normal installer recovery snapshot protected the previous app/database. Installed
manifest matched source; launcher/uninstaller signatures valid and matched prior
F5 hashes. Supervisor is running; operator/public pages HTTP200. These page and
health checks are not broadcast-health proof. PG_VERSION hash remained unchanged.
Installed worker held-supersession and never-buffering replacement/recovery both
passed, worker exits0. Receipt/report: docs/ops/native-host-install-2026-10-27.md
and docs/ops/evidence/native-host-install-2026-10-27.json (currently local).
Raw evidence: C:/CivicCastTester/f4-installed-transition-20261027 and coordinator
outputs/native-install-42339334. Installer helper96c6854002e84bc882616b8fbd01a261
and waiter10991 are terminal; no sandbox or rebuild was used.

## F4 source correction and remaining causal boundary

PR272 fix/f4-reload-state-sync-20261027: code2a880a4e corrects ignored False from
sync_state_with_parent. Incoming activation now aborts through existing build-error
cleanup; outgoing elements/pads remain intact and later incoming producers never
start. Regression red before fix, green afterward. Affected ordering129pass/2skip,
policy2pass, lint/format/governed bindings pass. This does not prove original cause.
Follow-up4d14011c pins only accessibility jobs to Ubuntu24.04 after actual26.04
Playwright installation failures; no accessibility requirement was removed.
PR272 Windows CI found raw element counts79/86 during independent caption cleanup.
Correction retains raw counts, separately records exact attached children owned by
active caption retirement threads, and preserves detection of completed leftovers
and unrelated leaks. Enumeration handles RESYNC without partial counts. Five
focused cases passed; ordering133pass/2skip before the final RESYNC-only addition;
the existing three-worker/six-rollover source scenario passed69.94s (18commits,
continuity/audio/tap/clean teardown). No repeated full native run after RESYNC.
The Linux unit image is pinned to the last passing Ubuntu24.04 baseline after a
rolling-image prerequisite delay; no tests or requirements removed. Root is
waiting on final delivery head677d800f after consolidating source/evidence and the
explicit acceptance decision below. Hidden completion waiter49111 runs coordinator
work/finish-pr272.ps1: source CI40471253320, applicable check validation, exact-head
normal merge and verification. Receipt outputs/native-install-42339334/pr272-delivery.json;
log work/pr272-merge-wait.log. Expected source CI duration can approach the last
passing run's58minutes; current Ubuntu24.04 media setup completed in17seconds.
On failure inspect actual receipt/log before another run. Cancel only this verified
owned waiter/session if Scott stops; remote jobs require separate owned cancellation.
Prior waiters29782,37137 and51242 are terminal. Do not start a competing merge.
The new source correction is not included in installed signed42339334 bytes.

Original run40165319415 at0fb01b0: incoming reload2 fourth clip filesrc11 errored
before readiness/commit. Outgoing playback continued; clean teardown. Raw Gst flow
debug was discarded and not uploaded. Later connected-tail abort fix is distinct.
Prior current/original-SHA/source-before-decoder-delay passes did not diagnose it.

One distinct installed experiment delayed after decodebin11 sync before manual
activation of seven NULL tails. It passed: GstBin async continuation independently
advanced tails to PLAYING during the0.5s delay, then reload2 committed with TS/audio
continuity and clean teardown. No incoming bus error. This rules out calling that
manual-delay observation a demonstrated defect; no downstream-first patch or sweep.
Evidence: C:/CivicCastTester/f4-installed-decoder-tail-delay-ready-20261027/receipt.json
and decoder-tail-debug-excerpt.log; raw worker.log retained locally,19MB.
Worker /root/f4_resume finished; no owned local test jobs remain.
The historical exact cause remains unknown. The following explicit acceptance
revision replaces that forensic clause; it does not claim the old cause was found.

## Adopted owner-delegated F4 decision — acceptance revision

Scott instructed the coordinator to record issue, options/pros/cons, recommendation
and reasons, then take that recommendation as his decision and continue. Root
communicated this decision in chat after the direct host installation and checks.
Issue: original raw flow diagnostics were discarded; the bounded experiments and
installed checks show safe replacement/recovery but cannot recover the old cause.
Options: continue open-ended reproduction (possible discovery, unbounded time and
contrary to Scott's no-loop directive); leave the item blocked indefinitely (keeps
old forensic wording, fails requested delivery); accept the historical unknown as
a documented residual risk after operational acceptance (finite evidence-based
delivery, but cannot promise that historical failure will never recur).
Recommend/adopt the third option using Scott's delegated decision authority.
This is a transparent change to F4 acceptance, not proof that the original exact-
cause requirement passed. Revised completion: demonstrated activation-refusal
containment, successful native installed replacement/failure recovery, corrected
required-CI accounting with leak detection retained, applicable green CI and merged
delivery. Historical cause remains documented; no additional timing sweep or new
gate. Operational checks passed; final CI and PR272 merge remain pending.

## Next action and coordination

Finish applicable PR272 CI and merge the demonstrated correction to close revised
F4 delivery. Preserve the accepted historical uncertainty explicitly. Do not turn
optional randomized CI into a new
gate, repeat passing scenarios, rebuild, or ask the same owner-acceptance question.
Scott delegated routine decisions via written options/pros/cons/recommendation;
that does not create missing historical evidence. Stay within concrete F4 actions.
Recurring watch civiccast-remaining-f4-delivery was successfully created; it uses
this state and newer instructions. Never resume old paused CivicCast chats.

## Retained assets and historical station finding

Signed build40317362877; kit C:/CivicCastTester/kit-staging/423393349f87148e7253f93691a9a70478cc120c.
F5 sandbox install/activation/signature/uninstall evidence remains valid, PR271
main379d7bc95d3d46402a7d8fccd494ac2b1cb94154; no16GB blocker remains.
Older installed2e196124 black/silent sampled HLS and false-health observation is
local in docs/ops/beta20-station-soak-2026-10-26.md and evidence JSON. It is not
new-candidate acceptance or a proved original F4 cause. Preserve those dirty files.
Full transfer snapshot remains in coordinator outputs/CIVICCAST-FULL-CONTEXT-HANDOFF-2026-10-26.md.
Everything below is historical evidence, not current execution instructions.

## Archived execution history — not current instructions

The entries below preserve earlier evidence. Their old candidate statuses, PIDs,
resource blockers, and next-action instructions are superseded by the current
outcome above.

### Historical prior candidate failure and correction receipts

- PR270 correction includes reviewed DCO906fd0a2 Tauri conditional hook fix
  and bounded manual provisioning diagnostics, plus current-source tripwire
  renewal. Remote8d64ce26 required CI40310373196 terminalFAILURE; sole15349/cell806
  consumedexit1. Unit12152974866050m10s:18stale-binding failures/11783passed,
 106skip/5deselected. All18 specific assertions nowPASS42.84s; only engine.py,
  existing native test and ci-test floor workflow role hashes renewed. Complete
  registered-input drift scan nowempty; packaging paths are not bound. Claims
  historical/current-source/installed limitations unchanged. Exact log
  %TEMP%/civiccast-pr270-unit-8d64.log. No product unit assertion failure.
- Windows121529748466SUCCESS13m30s delayedPNG/fullnative suite, integrationPASS;
  all other required checksSUCCESS/SKIP at terminalrollup. Claims failure is
  consequentmissing nativepureJUnit afterpytestfailed. Randomizedinformational.
  Next push consolidated correction, own ONE current-head requiredCI waiter.
- Candidate40310416286 terminalFAILURE;67349/cell807 consumedexit1. Reused signed
  station121529915153SUCCESS2m42s; app121531088730 builtverifiedcomponentpacks,
  compiled and initialNSISsucceeded, then normalization rejected Tauri's empty
  conditional UNINSTALLERSIGNCOMMAND hook at11:06:46UTC. Focused correction
  removes only recognized conditional block, preserves unknownfinalizer rejection,
  hidden checked callback/signingorder/failure restoration. Existing two cases
  PASS1.58s, native collection counts unchanged. Ruff/diff/actionlintPASS.
  %TEMP%/civiccast-final-candidate-build.log. No qualifying signed setup yet.
- Manual121529915032 failed silent10min apt provisioning before render; exactlog
  %TEMP%/civiccast-final-candidate-manual.log. Necessary replacement includes
  visible progress, existing30s network/60slock/retry1 options,180supdate/360sinstall
  bounds and failure holderdump; samepackages/renderchecks. No obsoletejobretry.
- AutomaticGateA40312615017 completed neutral/success with all three install and
  upgrade lanes SKIPPED; no Sandbox/stationinterruption. Listener86662/PID14068
  remains owned, no buildrunning. Last C211GBfree/RAM8978MB vs Sandbox16384MB;
  recheckbeforeacceptance and preserve unknown VM/station. Next corrected signing
  candidate reuses existing verifiedstation route, no tags/publication/LFS/spend.
  F5 installed launcher/uninstaller signatures and isolatedacceptance remain.

### Historical earlier PNG and fixture receipts

-40302184786 terminal FAILURE, sole67973/cell747 consumedexit1. Unit121502756357
 58m22s:3failed/11798passed/106skipped/5deselected. Exact native collection now
 2084pure/2326total/2323Windows(nonintegration); workflowfloors2034/2273 retain
  margin50. Schedule embed fixture now supplies explicit allowed access policy,
  matching existing VOD fixture; production fail-closed policy unchanged.
  Three exact failed testsPASS27.76s. Log %TEMP%/civiccast-pr270-unit-current.log.
- Required Windows121502756712 failed only static-PNG compositing (56other
  native-mediaPASS). Local original scenarioFAIL6.37s, retained33frames allmissing,
  decoderexit0: %TEMP%/civiccast-pr270-overlay-diagnosis/test_graphics_overlay_composit0.
  Gst1.28 VideoAggregator skips sole PTS0/40ms PNG buffer arriving after live
  deadline before repeat-after-eos can retain it. Debug probe captured40ms+EOS;
  timing changed/pass is diagnostic, not a fix. Static repeat-after-eos PNG-only
  writable pad probe now makes duration indefinite; live-layer timing unchanged.
  Existing scenario forces200ms PNG admission: without fixFAIL4.83s, with fix
  PASS4.61s (logo/banner pixels,continuity,clean teardown), settled lastframe.
  Initial fixture exclusive image_path/source_chain violation corrected; direct
  setter rejected immutable buffer, diagnosed bundled writable_object API used.
  Ruff/format/diffPASS. %TEMP%/civiccast-pr270-windows-current.log retained.
- Other current required checksPASS, including both a11y and docs retry
 121509027854 (10:01:47-10:06:13UTC). Original docs apt/TeX provisioning silent
 10min timeout occurred before render; one failed-job-only retry cleared it.
  Informational randomized121502712534 failed apt on all3mirrors beforepytest;
  no product finding, no new gate/rerun. Exact logs %TEMP%/civiccast-pr270-{docs,
  randomized}-current.log. Whole rollup18SUCCESS/3SKIP, unit/Windows and consequent
  claimsFAIL; randomizedFAIL is informational. Next one correctionpush/newwaiter.
- Candidate40303350129 at3074f2b6 CANCELLED for named artifact-affecting PNG
  defect before installer signing/kit;78803/cell757 consumedexit1. Verified signed
  station reuse121506336646 and exactmanual121506336342PASS. Both large uploads
  false; station used existing complete beta20-documentation-revision/kit/station,
  model acquisition skipped. Automatic GateA40304402239 completed neutral/skip,
  no Sandbox install. Corrected signed candidate and installed-signature acceptance
  remain; no release/tag/publication. Existing runnerlistener86662/PID14068 owned.
- GateA template requires16384MB vs free~9091MB at candidate launch. Preserve
  existing vmwp27068 (sinceOct18,parentvmcompute5428) and station. Parent requested
  app cleanup; recheck headroom before isolated acceptance, cancel only our queued
  automatic GateA before Sandbox launch if still insufficient. No VM changes.

### Historical PR270 initial candidate and typing correction


- PR270 initial lint121492497246 failed14 mypy types in8changedfiles. Exact
  log %TEMP%/civiccast-pr270-lint.log. Corrected annotations/invariant narrowing,
  typed conditional caption keyword and existing SQLAlchemy/callback type seams;
  no normal runtime behavior change. Required mypy civiccast78729 terminal0,
  no issues730files; affectedRuff passed. Next DCOcorrectionpush/newsolewaiter,
  retaining terminal receipts rather than rerunning local functional suites.
- PR270 https://github.com/scottconverse/civiccast-native/pull/270 review-ready,
  frozen head0bfcb944071017e4dfda13905b628236f661f1e6. Parent verified exacthead,
  mergeable/body/review; source frozen pending requiredCI, no additional checks.
- Sole waiter2879 owns ci-test40299157456, started~09:24UTC, Unit121492497954,
  Windows121492497573, integration121492498028. Expected fullterminal success/
  concretefailure; priorunit54m14s, jobtimeout75m. Ctrl-C2879 cancels watcher only.
  WholePRrollup at meaningful job completion/failure boundaries, ci-test alone
  does not prove allCI. Existing listener86662/PID14068 owned, build undispatched.


- Source candidate ab3df74981a82367d6726baec464c8f0ccf05b85, reviewed/DCO,
  branch fix/f5-installed-authenticode-signing; N8 103f870d, F5 signing3cd74389,
  reuseab3df749. No downstream push yet. Parent owns finalrollup/PR269merge.
- Existing CIwait23640/cell683 consumed terminal0:40289108609 attempt2 SUCCESS,
  Unit12147009249454m14s, Claims12149162566030s; Windows13m25s and integration38s
  SUCCESS retained. Public-a11y retry SUCCESS per parentrollup. No CIwaiter alive;
  Parent fullrollup20SUCCESS/3SKIP verified; PR269 merged09:22:15UTC main
  8608a4b55ae382fd21b6a8ebe5a315c0804a68ff. Integrated main without conflicts;
  valid focused evidence reused, no broad local rerun for merge alone.
- Existing runnerlistener86662/PID14068 owned onlineidle; no build dispatched.
  Finalcandidate signing/isolatedinstalledacceptance remains, no release/tag.

### Historical F5 preparation / PR269 attempt2

- F4 distinction: PR268 corrected the reproduced unsafe aborted-leg teardown /
  mux hang with fenced release before NULL; native supersede and required Windows
  checks passed. Original run40165319415 attempt1 filesrc11 stream error safely
  aborted, but its source cause remains unexplained. No existing receipt proves
  that original error shares the corrected teardown cause; original diagnostic
  provenance remains unresolved under the exact F4 plan, no green-retry claim.
- PR269 attempt2 Unit121470092494 active, sole waiter23640. Public accessibility
 121459714731 cancelled after30min apt/Playwright prerequisite install, before
  tests; failed-job-only rerun requested on fresh runner, no second watcher.
  Other terminal required checks SUCCESS; randomized informational SUCCESS.
- N8 DCO103f870d28b0b723d9451fdc420b1c07dcd1465d complete locally/reviewed.
  Active serial child fix/f5-installed-authenticode-signing. F5 source correction
  signs patched launcher before NSIS and generated uninstaller via the same pinned
  ArtifactSigning module/callback, inherited Azure credentials, fail-closed.
  Existing builder/workflow95pass6.73s; final hidden callback and fail-closed
  copy correction3pass1.68s. Ruff/diff and PowerShell parse passed. Existing
  ArtifactSigning module command contract inspected locally; actual final signed package and isolated
  installed/uninstalled signature acceptance remain, no build launched.
  Callback explicitly Hidden, module child tools NoNewWindow, inherited ConPTY.
  Python exit failure checked before any setup copy. DCO source3cd74389.
- Final-build feasibility metadata08:38UTC: C free237GB, E free1174GB,
  physical RAM free~9.6GB; no owned local build/test process, sole gh watcher23640.
  Initial blackwell-builder selfhosted sandbox-lab runner was OFFLINE/notbusy;
  existing listener connectivity subsequently verified below, no build dispatched.
  C:/CivicCastTester/runtime-artifact-mirror contains pinned FFmpeg70.8MB,
  Ollama1.46GB, PostgreSQL334MB, TSDuck38.5MB archives. Prior candidate/kit-staging
  7d61cbede contain receipts but no current setup/packs, cannot assume ready kit.
  Complete existing beta20-documentation-revision/kit under task outputs has
  app/server/runtime packs and station floor/Whistle/Gemma/translation packs.
  Reuse unchanged verified assets; rebuild changed app/server/setup once against
  final source, no LFS/spend. Signing module0.1.8 available locally.
  Existing Run-GateA.ps1 -KitDir route avoids large downloads and keeps isolation;
  prior C:/Dev/CivicCast-TestVM evidence also exists. One vmwp process27068 exists,
  ownership not established; do not interrupt. No build/VM/install launched.
  Credential/runner presence probe: Azure/pack environment values all absent,
  Azure CLI unavailable; no local signing credential access established. Existing
  C:/actions-runner/.runner identifies blackwell-builder; credential files exist
  (values not read), no .service/runner service/task/listener process. Repo secrets
  remain in Actions. Parent selected existing configured runner route: at final
  readiness start C:/actions-runner/run.cmd in owned invisible ConPTY, record
  session/PID, verify repo runner/notbusy/no competing queued candidate, then
  dispatch existing workflow using local mirror/kit and no large uploads. This
  parent superseded the wait-to-start instruction for connectivity only. Queue=[]
  before start, config expectedrepo=true. Started existing run.cmd08:44:30UTC in
  owned invisible ConPTY86662; ListenerPID14068 parent20564, version2.337.0.
  Connected/listening; GitHub08:44:42 ONLINE/idle, no Worker. Stop owned listener
  Ctrl-C86662 after verifying ownership. No build dispatch, credential copying,
  local unsigned build, VM or station changes. CIwait23640 remains sole waiter.
  Exact future dispatch: gh workflow run native-beta-candidate-artifacts.yml
  --ref main -f build_target=self-hosted -f upload_large_artifacts=false
  -f upload_candidate_binaries=false -f prepare_only=false
  -f reuse_station_dir="C:/Users/scott/Documents/Codex/2026-10-19/i-cannot-get-astra-to-finish-2/outputs/beta20-documentation-revision/kit/station";
  empty reuse_station_run_id/reuse_station_source_sha
  (hosted-only), reviewed_pyav_release_tag only if reusing an actual reviewed tag.
  Before dispatch verify main equals approved merged final source, then verify
  returned candidate run.headSha equals it; no tag/new release. GateA automatic
  workflow_run completion uses local kit; manual gate-a-station-acceptance.yml
  --ref main -f run_id=<candidateRun> -f lane=full if needed, no duplicate lane.
  Readiness constraint from existing workflow: station model/floor/Whistle caches
  deleted on previous success and absent under actual runner _work/_temp; build
  root absent. Stock selfhosted dispatch would fetch them again. Existing complete
  kit contains their packs, but no selfhosted reuse_station input exists. Resolve
  supported local asset staging before dispatch; no new download/build performed.
  Bounded F5 preparation delta: existing builder --reuse-station-dir option and
  selfhosted workflow input now verify/reuse complete signed station packs through
  verify_station_media + verify_native_pack; same-volume hardlinks, no repacking,
  default path unchanged. Three acquisition steps/raw-rootcleanup skipped only
  for explicit selfhosted reuse. Actual existing index signature verified against
  current repo public key: beta20/key civiccast-production-2026-beta1/channelbeta,
  all6 required packs named; full pack verification runs at final reuse execution.
  Existing tiny bundle/reuse/samefile/version rejection + workflowselection2pass
  1.70s, Ruff passed. Parent material review complete; no packagebuild/signcall.
  Final isolated installed launcher/uninstaller Valid timestamped expected signer
  verification plus applicable existing install/uninstall acceptance remains.
  Consolidated PR body prepared %TEMP%/civiccast-n4-n8-f5-pr-body.md; push awaits
  PR269 merge and required CI. Actual signed installed acceptance remains.

### Historical N8 finishing / PR269 infrastructure failure

- Parent owns roadmap/merge. PR269 frozen4b2dfe78; sole waiter16889 consumed
  terminal exit1, run40289108609. Unit121460108930 failed before pytest:
  existing apt prerequisite install timed out on all three mirrors (~24min).
  Claims verifier consequently missing JUnit, not product test evidence. Windows
  SUCCESS; other terminal required checks SUCCESS, public-a11y still live08:25UTC.
  Failed log %TEMP%/civiccast-pr269-correction-failed.log. Failed-job-only attempt2 requested; sole waiter23640 owns Unit121470092494
  started08:26UTC (~64min normal baseline). Ctrl-C stops watcher only. No downstream push before merge.
- N7 committed7ef5f5f5, reviewed; N4-N7 unpushed serial ancestors preserved.
  Active feat/n8-agenda-draft-minutes implements cited editable agenda sections,
  human-confirmed structured motions/votes, CAS approval and durable edit history,
  approved PDF narrative+JSON export and manual no-model recovery. Parent material
  review complete after dirty/save-pending approval block and constructor correction.
- Real migrated/store/API/cited generation/offset/history/approval/export/manual
  workflow passed4.49s; operator review+agenda37passed2.61s. Affected backend
  batch36pass/1fail: legacy PDF assertion assumed optional minutes attachment on
  every record; corrected actual-stream MIME check and minutes JSON MIME assertion.
  Typed caption fixture corrected four required fields. Exact PDF case passed;
  actual workflow4.46s passed including optional JSON MIME. TypeScript/affected
  ESLint/Ruff/diff check passed. API/manual/help generation73857 terminal0;
  PDF/DOCX8125 terminal0 (usual MiKTeX update warnings). N8 ready DCO commit;
  next F5 source-only signing seam, no installer build yet.

## Historical N7 finishing / PR269 CI - October 26, 2026

- Parent owns roadmap/merge. PR269 frozen4b2dfe786541294fa5f92a82ed0cc8e0edf823a2.
  Old ci-test40283658614 completed CANCELLED;69829 consumed exit1 (supersession).
  Sole waiter16889 owns40289108609, started08:00UTC, ~64min baseline; Ctrl-C16889
  cancels watcher only. Full rollup08:03UTC lint/operator/operator-a11y/docs/security
  SUCCESS; public-a11y/unit/Windows/repro still running, no failure then. Next
  consume at commit boundary; do not infer all CI from ci-test alone.
- N4-N6 preserved; correction merged26359142 on N6, explicit head0096. Active
  child feat/n7-selected-languages-caption-display, no downstream push until merge.
- N7 selected es,zh-Hans(Simplified Chinese),fil(Tagalog/Filipino),vi,ar; Spanish
  preserved. Existing target-aware Ollama prompt/glossary validation, station
  controls/review filters, worker review gate, HLS/flat-sidecar/CDN/repackage path
  implemented. Public selected-channel large-text display/link uses existing
  sidecar; first snapshot honest, new-change status, missing/interrupted/unchanged
  30s clear, channel-key clears old text before new request. Live translation off.
- Focused backend85pass6.81s with2 cancellation fake keyword mismatches corrected;
  those2pass1.46s. Actual-sample edit/approve/publish/repackage5pass1.79s. Operator
  review/settings34pass1.49s, native-player all-five selection6pass209ms; both
  portal typechecks, operator affected ESLint passed. Public built workflow401ms
  covers delayed new channel/text clearing,60px/link,30s/noinput/error. Public
  lint mechanical helper export corrected; final affected ESLint/types/Ruff passed.
- Actual local model five samples completed87322 exit0 (~28s), no helper live.
  Input: "The council meeting begins at 6 p.m. Public comment follows the budget
  discussion." Exact outputs retained in test_offline_caption_job.py and temporary
  civiccast-n7-translation-samples.json. Runtime translategemma:4b digest
  c49d986b0764f5881c476eb21435bb62b7abc62347aab3d4a6071e811be510a1, Ollama0.30.6.
  Bounded model-assisted/root sample review: Spanish/Vietnamese preserve meaning;
  Chinese generalizes council to municipal meeting; Arabic incorrectly says
  legislative session; Filipino order sentence ambiguous. Explicit draft edits
  for those three passed existing edit/approve→published tracks. No professional
  or native-language certification claimed; stations need qualified reviewers.
- Parent N7 material review complete. API/manual/help generated; PDF/DOCX82632
  terminal0 (usual MiKTeX update warnings only), docsite12764 terminal0. N7 ready
  for DCO commit then N8 exact scope. Full rollup08:08UTC reproducibility SUCCESS;
  public-a11y/unit/Windows still live; no failures. Sole waiter16889 retained.
  N8/F5 pending; no release/build/live-provider claim.

## PR269 correction receipts - October 26, 2026

- Parent owns roadmap/merge. PR268 is merged; F4/F6/F7 acceptance delivered.
  PR269 old head bfc4dc62 has required lint/operator/public-a11y failures;
  ci-test waiter69829 still owned (run40283658614), Windows success, unit pending.
  This is not a full CI pass. Correction now goes to the same reviewed PR.
- Fixed exact legacy translated-cue lookup by asset/language/cue identity,
  retaining source-window checks and failing closed for ambiguous matches.
  Repackage4pass4.03s, timing2pass1.48s. Server-search empty result now shows
  no-match; query-aware public fixture and both affected browser cases passed.
- Corrected staff array/auth/seed-status fixtures and changed setup/caption copy
  assertions. AppStaffLogin7pass2.10s; exact publish, pending elapsed and first-admin
  browser cases passed. First-admin fixture still asserts the issued stored token.
  No production auth change. Explicit migration assertions advance to actual0095;
  schema/migration20pass9.30s plus remaining2pass3.04s. Ruff9files formatted/check
  passed; affected portal TypeScript/ESLint and builds passed. No broad rerun.
- Parent reviewed material corrections. N4 50cbb23a, N5 7be2ab22, N6 56614c62
  are preserved on local serial descendant branches, unpushed. After correction
  push, merge into N6 and advance explicit head assertions to actual0096, then N7.
  N7 selected Spanish, Chinese (written variant explicit), Tagalog/Filipino,
  Vietnamese, Arabic. N7/N8/F5 pending; no release/install/live-transmission claim.
- Next: DCO correction commit/push, replace superseded CI ownership with one
  waiter for the new candidate and inspect full required rollup at boundaries.
  Existing waiter cancellation is Ctrl-C after verified owned session; no other
  local helper/test process is live. Named recovery stashes remain untouched.

## N4-N6 preserved implementation receipts - October 26, 2026

- Parent owns roadmap/merge. PR269 frozen bfc4dc620b3958cbe839a63450fece84f2129a66,
  required CI run40283658614, sole watcher69829 started07:08UTC (~65min prior
  baseline). Last natural-boundary state Unit and Windows running; no other CI
  watcher. Cancel watcher only Ctrl+C in69829; next consume at N5 check boundary.
- N4 reviewed/local DCO50cbb23a02de954ccf625711be0cdcb743c976a7 unpushed pending
  predecessor delivery. API/help/manual/PDF/DOCX generated; no local jobs live.
  Focused102backend13.26s,32dashboard2.21s,both portals typecheck/affected ESLint,
  Ruff, built external-origin TS/Spanish parsed cue/keyboard/375px/access403
  workflow13.3s passed. Actual YouTube transmission/account activation deferred.
- N5 reviewed/local DCO7be2ab2255a3c4c26ddbba75d4466576ccf16ccc; capture-linked
  source markers rebase through F1 while legacy/direct player times remain.
  Named recording selection, measured capture clock (bounded five-second probe),
  finalized asset linkage, hh:mm:ss correction, import basis and current public
  package/viewer access delivered. Evidence188 backend23.94s incl real UDP/media,
  44 operator3.01s, built resident HLS/keyboard corrected0.5s seek3.2s, types,
  affected ESLint/Ruff, generated API/help/manual/PDF/DOCX passed. N4+N5 unpushed.
- Active child feat/n6-daily-station-operations based N5. First product changes:
  all-channel actual output/source/audio/caption confidence with stale/missing
  readings; selected version-checked description/meeting-body edits with per-row
  results and failed-only remaining selection; read-only cleanup review joins
  existing expiry queue to current asset retention and legal-hold/readiness.
  Cleanup pages50 rows with current per-asset GETs (not limited Assets firstpage);
  only visible rows probe protection. No deletion/purge/control or station work.
- N6 material review complete after paging correction. Existing UI82 passed1.81s;
  changed direct-asset/selection3 passed890ms and paging1 passed807ms; types and
  affected ESLint passed. Updated manual/help/PDF/DOCX generation6734 exit0;
  final generated files include pagination wording. N6 ready for DCO commit.
- PR269 is NOT GREEN: parent complete-rollup found formatter
  tests/captions/test_review_persistence.py; AppStaffLogin new array mocks;
  public routing search fixture; operator first-mile Saved/publish-caption copy
  assertions. Informational randomized is not a gate, but exposed real affected
  packaging approved legacy-ID preservation and obsolete migration-head tests.
  Root logs in Temp/civiccast-pr269-{a11y,randomized}.log. Next finish/preserve
  N6 commit then isolate corrections on PR269 branch; no N7 work until repaired.
  Sole ci-test watcher69829 remains live; this watcher covers only ci-test,
  not the entire PR rollup. Last Windows121442261136 SUCCESS/unit121442261074 live.


- Parent owns roadmap/merge. PR268 merged07:03:52UTC, main5764afbcbdf757aa5b5ad6f91187283346ab2e94.
  Frozen e615fc82 CI run40277731000 SUCCESS; waiter2108 exited0. Final rollup
  21 SUCCESS/3 skipped release-only checks; all terminal. F4 abort isolation/native
  supersede+Windows, F6 honest preview withdrawal and F7 decoded boundary delivered.
  Historical original filesrc transient provenance remains unknown, not a new gate.
- Reviewed DCO commits N1 8f98d6f3, N2 59b7e77a, N3 2dc66e85 share one follow-on
  PR on feat/n3-caption-workbench, incorporating merged main and e615 diagnostics.
  No product conflict; CURRENT was the sole merge conflict, retaining both ledgers.
  Existing focused evidence below remains valid; no broad rerun for merge alone.
- N4 partial implementation preserved in named stash 'N4 embed and scheduled
  YouTube in progress before N1-N3 combined delivery', restore only on its child
  feat/n4-embed-scheduled-youtube after incorporating this combined base. No live
  local process or CI watcher. Next push combined PR, own one required CI waiter,
  then restore N4. F1-F4/F6/F7 merged; F5,N4-N8 remain.
- N2 implemented truthful first-recording progress from existing staff asset API,
  retry/refresh and direct Assets/Publish/resident links. Sources select current
  named channel profiles; stale selection falls back to a current profile.
  Accepted source/media says Saved, with no playback/publication claim.
- N2 isolated existing first-run test passed8.14s: fresh durable storage/admin/
  recovery ack, real sample generation, Package still-private, Portal-only approve,
  resident master/rendition/TS and actual FFmpeg video+audio decode. No providers,
  station interruption, downloads or installer builds. Setup UI43pass with one
  new exact-label fixture mismatch; corrected source test2pass. TypeScript and
  affected ESLint passed. No further local test expansion needed; parent material
  review complete. Docs/API/source checks remain as applicable.
- README/install guide state required components, preparation100GB/models22GB,
  progress-log and interrupted/mismatched-file recovery, preserve offline kit and
  upgrades. Optional-model feasibility: requires coordinated signed six-component
  activation/runtime contract; deferred, no acquisition implementation.
- N1 evidence retained: backend230pass24.64s, public UI9pass, added no-transcript
  workflow1pass/UI3pass, TypeScript/ESLint/Ruff. Source30->package28s correction,
  withdrawal and signed/current-grant text checks passed. Existing media/paywall
  limitation remains. Named N1 stash retained as recovery, restored on N1 only.
- N3 implemented selected batch max10 through existing serial queue, truthful
  state/cue/error progress, cancel/resume with retained review decisions, source
  cue timing edits and stored local vocabulary reaching supported Whisper paths.
  Migration0095 persists cancelled; conditional worker saves preserve cancellation.
  Checks immediately before attach/CDN stop work cancelled during translation;
  already-started publication may finish, documented in UI/help.
- N3 focused backend146pass/1external-Postgres-skip19.79s; actual settings-file ->
  worker hints + migration upgrade/downgrade/index + faster-whisper prompt3pass
  4.59s. UI50 cases covered:49pass with stale intro assertion, corrected file
  15pass; TypeScript/affected ESLint/Ruff/diff checks passed. Test fixture fixes
  for new settings vocabulary response/mutation shape and unordered same-tick jobs
  accounted for; no product failure hidden.
- Timing-only English change invalidates derived Spanish interval approval.
  Shared recorded identity includes interval; legacy target-only edits capture
  immutable English source-window association (nullable0095 review columns), so
  intended target timing and unchanged approvals survive. Existing offline suite
  70pass4.98s; correction20pass/1external-PG-skip7.21s; durable provenance restart
  plus review routes18pass8.71s. Parent correction recheck complete; live IDs unchanged.
- API/manual/help/PDF/DOCX generated; final render55017 and API53841 terminal0.
  No local jobs live. N3 DCO2dc66e85 committed after these focused checks;
  required CI now runs once for the combined reviewed N1-N3 candidate.

## Historical PR268 diagnostic preparation - October 26, 2026

- PR268 frozen at a5122f08 until this diagnostic-only commit is pushed.
  Run40276241194 Windows job121419690914 failed F7 extraction: decoded text
  empty, worker switch10.155s and clean EOS; F4 supersede passed. Exact log:
  C:\Users\scott\AppData\Local\Temp\civiccast-pr268-windows-second.log.
  Retained junit has no TS or decoder stderr. One local scenario15661 passed
  25.82s (Temp/civiccast-f7-ci-diagnosis); CI cause remains unresolved.
- Approved diagnostic change preserves decoder exit/stdout/stderr and failed
  TS/worker log in existing caption-worker-failure artifact. No product fix
  claimed. Ruff check/format passed. Next push and use one required CI waiter.
- Existing waiter20498 owns run40276241194; unit job121419690772 still live
  at last consumption. No remote cancellation. Cancel only watcher via Ctrl+C.
- N1 source is safe in named stash 'N1 source in progress while diagnosing
  PR268 caption CI', created on feat/n1-public-transcript-search. Restore only
  on that branch after diagnostic push; do not mix N1 into PR268. N1 contains
  packaged transcript search/read endpoints with current-grant signed-cookie
  access checks, timestamp links and accessible transcript; checks pending.
  Parent authorized N1 checks alongside hosted CI after headroom check (11GB free).

## Active F4/F6/F7 checkpoint — October 25, 2026

- Scott explicitly resumed this work in the parent Codex chat; the earlier chat
  remains paused and must not be messaged. Parent owns the complete roadmap;
  this directly controlled worker owns only F4/F6/F7. F1–F3 are merged; F5 and
  N1–N8 remain pending. No tags, publication, installer build or station changes.
- N7 selection for later work: Spanish, Chinese, Tagalog/Filipino, Vietnamese,
  Arabic (top five US languages besides English). Preserve existing Spanish;
  specify supported Chinese written variants honestly during N7 implementation.
- Candidate: `fix/f4-f6-f7-transitions-preview-captions` in
  `C:\Dev\civiccast-f4-f6-f7`, based on current main
  `6f1e13a0b0954d525829fe3f562a73b99ddb6f26`; PR #267 confirmed merged.
- F4 remains unresolved: first-attempt run40165319415 replacement filesrc11
  stream error aborts the replacement safely; Gst debug detail was discarded.
  Exact log: C:\Users\scott\AppData\Local\Temp\civiccast-f4-historical.log.
  Baseline native session51334 passed, as did one bounded scheduling probe;
  neither proves a fix. Pending-reload log now preserves URI-redacted Gst detail.
- F6 implemented: standalone CG HLS explicitly unavailable (503, null paths,
  truthful UI/help), layout preview and on-air graphics preserved. Focused checks
  passed in affected batch140 tests (six expected platform skips).
- F7 implemented program fencing for WAV/ASR/WebVTT/feed/embedded commands at
  actual reload switch. Native decode caught233 ms outgoing-caption carryover;
  direct cc_data erase reduced it to100 ms but still failed session49269.
  Packet capture proves erase arrives three frames late. cccombiner retains its
  scheduling FIFO across request-pad replacement (upstream gstcccombiner.c).
- Current correction disables the extra FIFO only for live appsrc captions;
  timestamp aggregation remains. Same interval proof session43974 PASSED in25.33s,
  output C:\Users\scott\AppData\Local\Temp\civiccast-f7-native5.
  No owned background process remains. Public sidecar9pass; operator13pass;
  caption/tap/feed126pass; strategy80pass6POSIX-skips; ordering/ack142pass2runtime-skips.
  Strategy initially20 gi-import failures when runtime env omitted, corrected
  by declaring the existing runtime. API/manual artifacts generated, including
  in-product/PDF/DOCX. Parent final material review found a missing daemon token passthrough; fixed
  and verified real feed-to-daemon-to-owned-strategy integration (1pass).
  Earlier failing captures native3/native4 remain beside it; final reviewed
  candidate and requiredCI correction status follow below.
- PR268 https://github.com/scottconverse/civiccast-native/pull/268 at DCO head
  3915b48c381c1308165dce268451e745c156c9d4. Parent material review completed.
  Required Windows job121413878829 FAILED: media1failed56passed434.98s.
  New F7 decode proof passed. F4 superseding scenario reload2 held only one
  stream, audio stopped before EOS, readiness timeout10s aborted, mux stalled
  and stop reported an aborted leg still retiring. Exact first-candidate log:
  C:\Users\scott\AppData\Local\Temp\civiccast-pr268-windows-first.log:781.
- Bounded diagnostic83643 passed again (no further hunt). Abort NULL/hold
  detail preserved in Temp\civiccast-f4-current-diagnosis. Structural correction:
  abort now reuses confirmed-retirement fenced request-pad release before NULL;
  removed connected-selector flush and duplicated teardown. Ordering128pass/
  2runtime-skips; same native supersede test86377 passed15.54s, output
  Temp\civiccast-f4-abort-isolation-fixed. Historical filesrc reason still unknown.
- Parent correction review complete. First-candidate unit job121413878544 was
  still running at correction push; not a pass. Parent authorized supersession
  without waiting solely to preserve old evidence. Old watch95625 ended; next
  action DCO correction push and one watcher on new candidate required CI.
  No local test remains running. No merge/tag/publication/station change by worker.
- Prior F1-F3 records below are historical; PR267 receipts remain preserved in
  C:\Dev\civiccast-f1-f3\docs\handoff\CURRENT.md.

## Active F1–F3 delivery checkpoint — October 25, 2026
- OAuth resumption: Scott authorized using civiccast1@gmail.com in Chrome and
  subsequently explicitly approved creating its YouTube channel. Created Google
  project `keen-apogee-512802-i5` (CivicCast YouTube), configured an external/testing
  OAuth app and desktop client, enabled YouTube Data API, and added only this account
  as a test user. Scott completed consent. Desktop PKCE + validated-state loopback
  callback succeeded; OAuth refresh and channel API both return HTTP 200.
  Credentials are local in an ACL-protected private directory, outside the repository;
  no secret values are recorded here or in chat. The existing Setup save function
  wrote isolated credentials; the real adapter consumed them without OAuth env vars.
  Created channel CivicCast, handle @CivicCast1, ID `UCIsN1mKqtpMNpvoh8GPSXkQ`.
  Scott completed phone verification; the correct-account verification screen
  confirms it. Refreshed Studio for this exact channel/account. Its live-access
  request is now active: requested October 25, 2026 at 9:01 PM America/Denver;
  observed countdown "Only 23:59:51 until you can stream". Expected availability
  October 26 around 9:01 PM Denver time, subject to Google's actual activation.
  OAuth refresh, channels.list and now liveBroadcasts.list return HTTP 200; the
  list response does not establish permission to start while Studio's countdown
  remains active. No event or sender created. Scott explicitly accepted F2 setup
  and implementation: real streaming is NOT required for sign-off and this delay
  is NOT a blocker. A later destination/viewer/stop rehearsal remains a follow-up,
  without touching the endurance run.
  No active watcher, callback, test sender or automatic continuation is running.
  No billing, station changes, installer, release, or merge. OAuth callback helper
  session 92399 finished successfully; earlier blocked attempt 29891 was cancelled.

- Delivered: review-ready PR267 https://github.com/scottconverse/civiccast-native/pull/267
  on branch `fix/f1-f3-caption-publish-dependencies`, head
  `29a1d373b52bf25cd6fdfd3e893308ade2c4a799`. PR is OPEN, non-draft, merge state CLEAN.
  All new commits have DCO sign-off. **Do not merge, tag, release, rebuild an installer,
  or update/restart the endurance station.** Scott revised F2 acceptance to accept
  the completed setup without a real streaming check. F1/F2/F3 are accepted; finish
  the requested setup-manual documentation and its required CI/PR delivery.
- Worktree: `C:/Dev/civiccast-f1-f3`; original checkout and its pre-existing handoff
  remain preserved. Baseline main `933dd657`; plan commit `7edc5500`. One executor;
  current-agent routing skill applied; no independent reviewer was commissioned.
- F1 VERIFIED: original review missed fixes already in main (`5c3540ec`, `5ca75521`).
  Retained package-timeline clipping/rebasing and approved-source repackaging gates.
  Added real FFmpeg fractional-trim + bilingual-caption regression: removed head/tail
  excluded, retained cues align to 0–0.167 and0.467–0.567 seconds, originals unchanged.
  Existing trim/repackage/review regressions passed; no caption engine was replaced.
- F2 ACCEPTED BY OWNER; LIVE REHEARSAL IS FOLLOW-UP: protected Setup values now feed existing
  IA/YouTube/NAS/SMTP adapters with environment fallback and explicit provider override.
  Added required YouTube refresh-token and optional SMTP fields; webhook-only setup
  remains compatible. No secrets copied into environment or returned in Setup responses.
  Publish selects a channel with fresh HLS and sends its existing audio/video through
  a separate owned FFmpeg RTMPS process to a configured/bound YouTube event. Current
  start/title/privacy/auto-start/stop metadata, viewer links, destination-confirmed
  lifecycle, two-minute startup timeout, process/destination failure, stop/shutdown,
  restart interruption, partial setup cleanup and private ingest-link redaction are
  covered. Thirty-second destination polling limits quota use; UI reads local status.
  Conditional surface writes preserve concurrent publishing; dashboard reads stay
  batched. Simulated and historical unconfirmed YouTube results remain unverified.
  Provider/output failures leave local portal publication available.
- F2 proof and acceptance: real Google OAuth refresh, channels.list and
  liveBroadcasts.list return HTTP 200; the authorized channel is @CivicCast1.
  Existing Setup save securely feeds the real adapter without OAuth environment
  variables. Scott completed phone verification. Studio's activation countdown is
  recorded above. Contract tests and the real local FFmpeg/RTMP receiver establish
  local starting/live/stopped lifecycle and transport. No actual YouTube stream or
  viewer playback is claimed. Scott explicitly removed that check as a sign-off
  requirement and said to count F2 as a win; the earlier blocked audit is superseded.
- Documentation follow-up: expanded the setup chapter with account/channel
  selection, project/API setup, OAuth app/client/test-user creation, desktop PKCE
  helper, protected three-field storage, testing-token expiry, phone verification,
  Studio account selection/activation and later rehearsal/stop. Corrected related
  obsolete provider/publishing claims. Regenerated the assembled, in-product and
  downloadable manual artifacts. Helper is an IT source-checkout tool, not a new
  installer feature; focused callback/security checks pass (4 cases).
- F3 VERIFIED: targeted package updates only; uv changes exactly four resolved versions.
  Native app lock changes only pypdf/filelock/setuptools, runtime lock only setuptools;
  existing single LGPL PyAV wheel retained and app lock hash updated. Security floors
  are scoped to captions-runtime/dev groups so global uv settings cannot contaminate
  hash-required native staging. All four current-head CI security scans pass; both npm
  audits are zero. Existing allowlisted NLTK PYSEC-2026-3740 remains unchanged/outside
  selected remediation. No suppression, threshold or workflow gate was changed.

| Dependency | Corrected advisory identities | New pin and exposure |
|---|---|---|
| pypdf | CVE-2026-115572 / GHSA-6c7f-9265-g8fw; CVE-2026-115585 / GHSA-jr96-gvgh-j3r4; CVE-2026-115580 / GHSA-gmh7-3g9r-mjh5 | 6.21.2; runtime uploaded/vendor PDF extraction, addressed first |
| filelock | CVE-2026-112940 / GHSA-v4x8-3wr5-3wq8; CVE-2026-112913 / GHSA-9vhv-jxww-xfm5; CVE-2026-112907 / GHSA-567q-5qvw-pf92 | 4.2.4; caption model-cache dependency and dev virtualenv; local/shared-lock risks, largely POSIX |
| setuptools | GHSA-xmj3-9gf7-h52w | 84.2.1; bundled app/runtime installer tooling; vulnerable wheel-install path, outside normal playout |
| virtualenv | CVE-2026-113713 / GHSA-4f8h-vr72-p32c | 21.14.13; dev/pre-commit tooling |
| Vitest / mocker | GHSA-qxg6-89mg-rqv8; GHSA-xp26-3454-mjgp | 4.1.12 family; development test server/tooling |
| Zod | GHSA-mrhx-c7xc-m3w5 | 4.6.7; frontend development ESLint tooling |
| Undici | GHSA-4qv8-2p9j-2685; GHSA-f72v-6w45-q472; GHSA-xjj6-f33q-929c | 7.30.1; public portal dev jsdom dependency |

- Final CI, current head: **23 passed,3 installer-related checks skipped,0 failed,
  0 pending**. ci-test40233336560 completed SUCCESS, including claims verifier.
  Unit cross-platform:11759 passed,105 skipped,5 deselected; Linux native subset:
  2082 passed,242 deselected; additional required captured case:1 passed, no skips.
  Windows:2321 native passed (floor2271), plus35 installer/filesystem and56 media
  cases, no skips. Security40233336615, lint/types40233336623, docs40233336572,
  Windows payload reproducibility40233336536 and a11y40233336538 all pass. Existing
  informational randomized run40233336611 also passes; it was not promoted to a gate.
  CI URL: https://github.com/scottconverse/civiccast-native/actions/runs/40233336560
- Focused evidence retained:190 provider/publish checks;39 sample/lifecycle checks;
  156 publish checks;21 legacy/query-count regressions;27 restart/setup checks;
  12 shutdown/lifecycle checks;71 native lock/closure checks;31 operator publish UI
  and69 public UI tests; operator build and both frontend linters; pinned Ruff/mypy
  (725 source files); generated OpenAPI/lock currency; no claims blob/marker drift.
  A stale public test assertion was reproduced on both old/new Vitest then corrected
  to existing wording. CI exposed two old auth/installer fixtures omitting the new
  refresh token; both were reproduced and corrected, retaining role/redaction checks.
  Required unit work finished before that test-only push. No broad manual rerun,
  unrelated repair, mutation testing or new gate was added.
- Installed environments retain current packages. Applying F3 later needs a separate
  owner-authorized installed app/runtime update from corrected locks, including
  caption-environment filelock where present. No installed package was changed here.
- All owned checks, software receiver/sender, browser helpers and CLI watchers are
  terminal. Last watcher79306 / wait179 returned0. No automatic continuation is
  promised. Scratch receipts remain under original chat `work/f1-f3-*`.
- Next: deliver this manual/helper follow-up on the same PR and complete its
  required checks. F1/F2/F3 acceptance no longer waits for YouTube activation or
  real streaming. Reuse prior passing evidence for unchanged implementation.
  A later live rehearsal is follow-up, not a blocker. No F4-F7/N-series work,
  merge, release, installer, LFS transfer, spending or installed-station changes.


## Historical restart checkpoint — October 25, 2026

Scott requested preparation for context compaction and a Codex app update/restart.
This checkpoint is a local documentation change only; it has not been committed
or pushed, and must not automatically trigger another PR or CI cycle.

- Checkout: `C:\Dev\civiccast-native`, branch `fix/brotli-agenda-advisory`,
  HEAD `0fb01b0bc51bfa648dac2fc419890fd3d18d15f2`.
- Fetched `origin/main`: `933dd6571ab57ee5891f76c1148b79cb9c77bbc0`.
  PR265 (checker/handoff) and PR266 (targeted Brotli) are merged. Both authorized
  delivery tasks are complete; there is no pending merge, build or release.
- **Leave the endurance station running.** Scott explicitly said it is a test
  station to see how long it runs without issues. Do not update its installed
  packages, restart its processes/services, reinstall, reboot, stress-test, or
  interrupt it. At this checkpoint `CivicCastSupervisor` is Running, PID12084.
- No owned CI watcher/helper remains running. Wait sessions78744,94426,19473
  are terminal; the final watcher confirmed the PR266 merge. A process-name/
  command-line probe found no visible Python checker/Brotli wait helper.
- No installer rebuild, new release, LFS transfer, or installed-station package
  update was performed. The Codex app update/restart itself was not initiated.
- Latest requested review is complete: specs, records, current source, open
  issues/PRs, and five related repositories were inspected read-only. Scott has
  **not authorized implementing the resulting backlog**. Resume by reading the
  report and the next owner instruction, not by starting repairs automatically.
- Full review and source references:
  `C:\Users\scott\Documents\Codex\2026-10-24\us\outputs\civiccast-unfinished-work-review-2026-10-25.md`.
  Brotli investigation/merge receipts:
  `C:\Users\scott\Documents\Codex\2026-10-24\us\outputs\civiccast-brotli-investigation.md`.
- Main findings: other dependency advisories; trimmed-recording caption timeline;
  fixed/nonexistent CG preview segments; installed native launcher unsigned
  (read-only signature check); intermittent replacement-program test failure;
  native-app audience beacons missing; incomplete field workflow evidence; five
  unreconciled open PRs; stale current-status records. Distinguish confirmed
  defects, static source gaps, missing proof and owner-deferred work.
- TownLight integration is owner-deferred to2.0. Historical WSL/macOS/audit-role
  obligations do not revive retired work or superseded workflow gates. No new
  release requirements or automatic audit/delegation were authorized.
- Scott requested the `codex-model-routing-1024` skill for this session:
  `C:\Users\scott\.codex\skills\codex-model-routing-1024\SKILL.md`.
  Keep the suitable current agent; no automatic delegation/model change. Keep
  Windows helpers hidden and gh inside invisible ConPTY (`tty=true`,
  `login=false`), preserve disabled telemetry, no host reboot, no LFS spending.

The following entries retain their original preparation/history scope. This
restart checkpoint and the confirmed merge receipts supersede pending wording.

## Completed follow-up — targeted Brotli correction

The checker integration below is complete: PR265 merged into main at
`f9d380bebb15489762424f4f1293f4e6f050211a` after current-head unit tests,
Windows probes and claims verification passed (test run `40161873024`).
Earlier pending/failure notes below describe preparation, not the final result.

Scott subsequently authorized the Brotli dependency fix through a PR and merge
after required CI, with no installer rebuild or new release. Branch
`fix/brotli-agenda-advisory` adds `brotli>=1.2.1` to `agenda-js-import` and updates
the locked decoder from 1.2.0 to 1.2.1. Only the Brotli package record and root
optional-dependency metadata change in `uv.lock`; crawl4ai remains 0.9.2 and all
other package records are identical. Native payload requirements are unchanged.

Passed local checks: `uv lock --check`; semantic comparison of all 200 locked
package records; Python 3.12 Windows x64 wheel availability in the lock; 45
`test_js_portal.py` tests (two real-crawl4ai checks skipped because that extra is
absent locally); isolated Brotli 1.2.1/HTTPX 0.28.1 compressed-response decoding;
and a focused `pip-audit --no-deps --disable-pip` scan of `brotli==1.2.1` reporting
no known vulnerabilities. No waiver or unrelated dependency upgrade is included.

PR266 merged at `2026-10-25T06:32:40Z` as
`933dd6571ab57ee5891f76c1148b79cb9c77bbc0`. Applicable CI passed, including
unit tests, claims verification, optional agenda integration, virtual headend,
publish soak, veraPDF and app-payload reproducibility. The Windows media job
initially failed on an unchanged GStreamer replacement-leg stream error, then
passed on one job-only retry without code/assertion changes. GitHub itself
returned HTTP403 for retry while the workflow was still running; that waiting
restriction was not a repository-configured rule. Test workflow40165319415,
payload reproducibility40165319416. Security40165319459 installed Brotli1.2.1
and no longer reported its advisory; independent Python/npm findings remain
failed and are not waived. Informational randomized run40165319528 was
cancelled, not passed. No independent agent review was required or performed.
No installer or release workflow was dispatched. Existing signed artifacts and
the running station are untouched.
Installed optional-extra environments need a separate package update and process
restart; merging this source fix does not patch running environments. Earlier
Brotli investigation and other security findings below remain historical evidence.

Read this file and the root `AGENTS.md` / `CLAUDE.md` before continuing. This is
the current handoff, replacing dependence on the previous coordinator's local
chat and drive. Historical release records retain their original evidence scope.

## Get the complete handoff

Repository: <https://github.com/scottconverse/civiccast-native>

This handoff and the checker fix are on **`fix/beta20-portable-handoff`**, based on
published main commit `121465506a7314f02ad7572337b46a83fa3f6e51`. They are being
delivered as a branch, not represented as already merged into `main`.
Do not start from the Beta20 tag expecting the latest instructions or package source.

PowerShell, on the destination machine (choose a short writable source directory):

```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
$env:GIT_LFS_SKIP_PUSH = '1'
git clone --single-branch --branch fix/beta20-portable-handoff https://github.com/scottconverse/civiccast-native.git civiccast-native
Set-Location civiccast-native
git status --short --branch
git log -1 --oneline
```

No `git lfs pull`, `fetch`, or `push`; no LFS transfers or spending through
October 2026. The current tree has no LFS-tracked files; historical revisions may.
The handoff branch deliberately has no PR yet: creating a PR launches broad
application CI. Scott requested a push and portable handoff, not another CI cycle
or merge. Check current remote state before later integration.

## Current result and exact identities

- **Beta20 is already public:** [release and assets](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.20).
  GitHub labels it a prerelease because it is a beta; it is not a draft.
- Current installer is the documentation revision built from
  `2e19612467fda894d63e499580e4d7b5f029f5f2`, signed
  [build 39810481727](https://github.com/scottconverse/civiccast-native/actions/runs/39810481727).
  Setup SHA-256: `6c0aa52d773a4c5c0a0643e00d860a83560551d497c5f1efb05cba5beadc3e5d`.
  Existing evidence records a valid Scott Converse signature and installed Help/manual acceptance.
  See [Beta20 verification](../releases/v1.0.0-beta.20-verification.md).
- Original release tag remains at `a17449189a39af6474d914137eab020169f91023`.
  The tag's automatic source ZIP is **not** the documentation-revision source.
  Do not move the tag or rebuild an installer to reconcile these identities.
- [PR263](https://github.com/scottconverse/civiccast-native/pull/263) merged the final
  prior CI corrections at `086f1b967766aaea31a5a4296ce6f96771e2d72f`.
  Its recorded rollup was 21 successes / 3 inapplicable skips / 0 failures.
  That historical pass does not claim later CI is green.
- The approved station-manager landing page and Scott's biography are live at
  <https://scottconverse.github.io/civiccast-native/>.
  [PR264](https://github.com/scottconverse/civiccast-native/pull/264) merged at
  `121465506a7314f02ad7572337b46a83fa3f6e51`;
  [Pages run 39989225060](https://github.com/scottconverse/civiccast-native/actions/runs/39989225060)
  succeeded. Live HTTP 200, headline, biography/LinkedIn, loaded images and working
  walkthrough were checked, with no browser JavaScript errors. Six local tabs and
  390/768/1440-width layouts passed. The walkthrough is explicitly an illustration,
  not a real station demo. No customer testimonial or production-station claim was invented.

## Checker correction included in this branch

Only `scripts/verify_native_runtime_closure.py` and
`tests/native/test_closure_verifier.py` change checker behavior.

The last passing Windows run used GitHub image `20261007.264.1`; the failing run
used `20261021.278.1`. Product runtime, dependency lock, checker and workflow were
unchanged between those runs. The rejected file was Windows Terminal's root
`resources.pri`, observed as a known non-executable mapping. Six other closure
checks passed, including plugin origin and the caption embed/decode round trip.
This is a data-resource classification false positive, not evidence of a missing
media DLL. The precise Windows API that mapped the file was not captured.

The correction recognizes the stable Microsoft Windows Terminal x64 package
identity (numeric version, publisher `8wekyb3d8bbwe`) and **only root
`resources.pri`**. The path stays visible in the report. Executable and unknown
protection mappings, DLLs, wrong publishers, different packages and subdirectories
still fail. Canonical-path and file-identity safeguards are retained.

Existing verification, October 23, reused without rerunning unchanged code:

```text
.venv\Scripts\python.exe -m pytest tests/native/test_closure_verifier.py -q --no-cov
153 passed in 6.38s
ruff check: passed
ruff format: applied; changed files reviewed
git diff --check: passed
```

This is local checker-logic evidence, not a new hosted Windows pass or installed
product test. No installer rebuild is required for this change.

Historical Windows runs:
[passing 39923178286](https://github.com/scottconverse/civiccast-native/actions/runs/39923178286)
and [failure 39987938776](https://github.com/scottconverse/civiccast-native/actions/runs/39987938776).
The failed prerequisite caused later Windows suites to skip; missing JUnit reports
are consequences, not separate product defects. Other broad PR264 runs were cancelled
to avoid unchanged full-suite/manual work; cancellation is not a pass.

## Remaining work, not authorized as part of this handoff push

1. Integrate this branch through the repository's normal PR process when requested.
   The corrected checker has not been exercised on the updated hosted Windows image.
   Use the existing affected Windows job when integration requires it, not repeated
   full suites or a new installer. Do not claim repository-wide CI is green.
2. The Python audit reported `brotli 1.2.0 / PYSEC-2026-4322`, pulled in through
   `crawl4ai 0.9.2`. See [run 39987938274](https://github.com/scottconverse/civiccast-native/actions/runs/39987938274)
   and main security run `39989225752`. Severity, reachability and inclusion in the
   released installer were **not established** in this session. The finding has not
   been fixed or waived. Inspect the advisory and actual dependency use before a
   bounded dependency correction; do not invent an allowlist exception.

The next agent should take Scott's current assignment. Reading this handoff is not
permission to restart old nine/twelve-goal lists, redesign the product, create a
new release, or repair unrelated findings.

## Accepted behavior and boundaries to preserve

- Scott accepted the observed ~8.763-second caption sample delay and brief
  previous-program carryover for Beta20. It is not a universal latency bound and
  not a new release blocker.
- Summary evidence covers a short transcript and cited narrative; no long-meeting
  benchmark is claimed. Local AI model RAM requirements still apply.
- Roku acceptance uses the repository's software/API mocks and BrightScript
  validation. Scott explicitly prohibits physical-hardware testing requirements.
- The previous host was a test server, with owner-authorized reset/stop/start
  operations. Do not infer that a new machine is a test host without context.
- Mutation testing was removed at Scott's request. Never restore it as a CI or
  release requirement, or promote informational results into mandatory gates.
- Serial work, one justified candidate, no automatic subagents or repeated audits.
  Every additional check must address changed behavior, an unresolved requirement,
  a concrete defect or required CI. Reuse unaffected evidence and artifacts.
- No LFS transfers, new spending, host reboot or installer rebuild for this handoff.
  Keep windows/helpers hidden. No automatic monitor or continuation is required.

## Build, runtime and machine state

See [BUILD-ASSETS.md](BUILD-ASSETS.md) for exact source locations, asset sizes,
existing release downloads, the FFmpeg mirror and PyAV reuse recipe.
Source checkout and checker development do not require copying the old machine's
station data, models, installer, virtual machines, `.venv`, or agent tooling.

The old untracked `.codex/` directory is deliberately not imported. It contains
machine-specific Desktop paths, historical authorization/evidence files and
wake/completion hooks for a previous assignment. These are not product build inputs.
Current owner rules are portable in `AGENTS.md` and `CLAUDE.md`; do not recreate
old hooks or depend on `C:/Dev/codex-hook-kit` to develop CivicCast.

The previous workspace has older candidate/worktree directories. This handoff is
for the current released native product and this branch; it does not certify or
publish abandoned experiments from every historical worktree. The coordinator's
incident drafts, token-accounting reports and browser mockup scratch files are
not prerequisites. Relevant product status and evidence are linked above.

No fresh-machine build was run for this handoff. Required binary sources are
identified; availability of every third-party download and access to private
signing credentials are not implied by a successful Git clone.


## Integration and advisory investigation — October 24, 2026

Scott authorized [PR265](https://github.com/scottconverse/civiccast-native/pull/265)
and merge after applicable CI passes, followed by a Brotli investigation/report.
No installer rebuild or new release is authorized. At preparation of this update,
integration awaits corrected-head CI; PR265 records the subsequent merge result.
Do not infer repository-wide green CI from a merge: informational scans have findings.

- Initial PR head: `50b4e11d15f36aa50ba2bf02f9bd417d1e1ddf60`.
  Windows closure/native job `121056228553` passed in run `40155601318`.
  App-payload reproducibility run `40155601291` passed byte equality.
- Informational randomized run `40155601353` exposed five deterministic stale
  policy/doc assertions (seed `824704267`): native collection counts/floors,
  removed historical coder-seat wording, and two old landing-page assumptions.
  All five reproduced locally. The still-running unit workflow `40155601318`
  was cancelled before pushing corrections; cancelled is not passed.
- The integration correction updates collection expectations to 2082 pure /
  2324 total / 2321 Windows non-integration tests, and raises JUnit floors to
  2032 / 2271, preserving the existing 50-test margin. Role checks protect
  current owner assignment; landing-page checks protect current release links,
  reject withdrawn releases, retain prohibited-claim checks and require historical
  citations if historical measurements return. Product/page content is unchanged.
- Reuse the passed payload byte-equality proof for these test/doc/floor-only
  corrections; they do not alter packaging. Await applicable CI on the corrected
  PR head, including the downstream claims-evidence verifier, before merging.
  Main rules require a PR but returned no named required checks or approving
  review requirement. The security workflow and randomized suite are explicitly
  non-required/informational; report their findings without making them new gates.
- Focused local verification passed: 50 documentation/role tests and 21 native
  workflow-policy tests (including live collection/floor checks), plus ruff lint,
  formatting and git diff --check. The checker code remains unchanged; reuse its
  153-test evidence and the hosted Windows proof above.
- Previous local watchers ended. Next action at preparation: push the corrected
  head, wait for applicable CI, and merge the exact authorized candidate.

Brotli findings (dependency correction is not implemented):

- PYSEC-2026-4322 / CVE-2026-111497 / GHSA-9v8h-qqgm-4m35 is one upstream
  decoder memory-corruption issue, rated critical (CVSS 4.0 9.2), fixed in 1.2.1.
  See [PyPA advisory](https://github.com/pypa/advisory-database/blob/main/vulns/brotli/PYSEC-2026-4322.yaml)
  and [upstream fix release](https://github.com/google/brotli/releases/tag/v1.2.1).
- Optional `agenda-js-import` -> crawl4ai 0.9.2 (`brotli>=1.1.0`) locks 1.2.0.
  Installing that extra enables HTTPX's Brotli decoder automatically. CivicCast's
  `js_portal` robots.txt fetch provides a concrete remote-response input path.
  This is static reachability evidence, not an executed exploit or compromise.
- Fix needed: require `brotli>=1.2.1` in that optional extra and refresh only its
  affected lock/hashes; verify the selected version, advisory result and focused
  agenda/HTTP tests. Crawl4ai 0.9.2 accepts 1.2.1, and a Python 3.12 Windows x64
  wheel exists. Installed affected environments also need their decoder updated
  and processes restarted. Do not add an allowlist waiver.
- Exact released source `2e19612467fda894d63e499580e4d7b5f029f5f2` excludes Brotli
  and crawl4ai from the native Python locks; packaging uses `--no-deps`.
  Bounded HTTP ranges inspected the published app pack's ZIP directory:
  1,152,522 bytes read, 9963 entries / 83 wheels, no Brotli/crawl4ai paths.
  Full artifact checksum/signature verification and installed-environment
  inspection were not performed; other embedded decoders are not ruled out.
- Security run `40155601323` also reports filelock, pypdf, setuptools, virtualenv,
  Zod, Vitest/mocker and Undici findings. Their reachability and artifact scope
  were not investigated here. No dependency edits, waiver, installer rebuild,
  release or LFS transfer was performed.

### Claims-binding follow-up during integration

Corrected head `8b3c90174aa584238e6ccc8aa9f58ab895502913` passed its Windows
job in run `40158115701`. Its unit job reported 16 failures / 11712 passes:
all failures derived from six workflow blob bindings left stale by the JUnit
floor correction. Local checks reproduced those six mismatches. The downstream
claims verifier rejected the failed producer; this is not green CI.

The six registry workflow references are renewed after reviewing the floor/comment
only delta; see the October 24 section of the existing current-source review
note. No producer, registered node, control or enforcement is weakened, and no
historical acceptance is renewed. All 125 claims-evidence policy tests passed locally after rebinding (67.49s).
The next candidate must complete same-run CI.
The previous finite merge watcher exited on these required failures without
merging. No merge is currently armed while this follow-up is prepared.


### October 27, 2026 -- October 26 station black/silent outage: proven cause, correction

Working branch `fix/black-silent-20261027` from current tree (head `677d800f`, merge
`69c2bcae`). Nothing committed, pushed or merged; no installer rebuild, no service
configuration change, no station interruption.

Observed cause (log, not inference). Retained control-plane log
`C:\ProgramData\CivicCast\logs\control_plane-app.log.6` (now `.4`) line 5832-5859:

- `2026-10-25 07:49:55,675` content-reload *source preparation succeeded* (`took 5.4s
  for 1 segment(s)`). Only the dispatch step then failed: `07:49:55,679 ERROR
  civiccast.egress.daemon: Content-reload dispatch failed for public; restarting
  encoder.` followed by
  `FileNotFoundError: [WinError 206] The filename or extension is too long:
  'C:\Program Files\CivicCast (Native)\runtime\dependencies\gstreamer\bin'`
  raised from `gstreamer_runtime.py:137 _DLL_HANDLES.append(os.add_dll_directory(bin_dir))`,
  reached via `daemon.py:6045 _reload_steps` -> `strategy.py:1538 reload_content` ->
  `strategy.py:1253 _resolve_encoder_override` -> `encoder_probe.py:140/57/30/43`.
- The named path exists and is 70 characters long, so this was NOT a missing path or
  an over-long name. `os.add_dll_directory` appends to a process-wide loader search
  path whose buffer is a fixed 32,767 characters; `bootstrap_installed_gstreamer_runtime`
  had no per-directory guard and is re-entered by every reload encoder probe, so the
  buffer was exhausted. Reproduced on this host with the installed bin directory in a
  fresh CPython 3.12 process: 460 registrations succeed, the next raises exactly this
  exception and every later call fails permanently.
- That single process-wide counter explains the otherwise puzzling evidence: three
  independent channels all went TRANSITIONING within ~21s of one another (public
  07:49:55.724, education 07:50:16.9xx) and never returned to ON_AIR, while 171
  dispatch errors (57/channel) and 168 transition-stall errors accumulated over the
  following 26h. Worker PIDs never changed, and the retained worker tails show the
  mux still encoding (26247449 buffers, +26247444 since PLAYING) with
  `chain-in 5.0s: in video=+0 audio=+0` -- zero source-chain input into the selector
  while 150 video / 234 audio buffers per 5s of black/silent filler reached the mux.
- Failed recovery has the same cause, not a second one: `_request_reload` catches the
  exception, logs "restarting encoder" and returns False, which arms a `_pending_reloads`
  pin (wait for worker plan EOS). Recovery then re-enters the SAME probe -- both the
  reload-stall watchdog re-issue and the restart's `strategy.start()` path call
  `_resolve_encoder_override` -- so every retry raised the same WinError 206 in that
  process. Nothing but a process restart could have cleared it. This is distinct from
  the historical filesrc11 CI failure and from the HLS relay log-cap warnings.

Correction (smallest sufficient behavior): `bootstrap_installed_gstreamer_runtime` now
records each registered directory (normcase key) in `_REGISTERED_DLL_DIRECTORIES` and
calls `os.add_dll_directory` at most once per directory per process, appending the
handle (keeping it referenced) only after a successful call. This mirrors the existing
`civiccast.captions.runtime._CUDA_DLL_DIRECTORY_HANDLES` pattern. The unguarded call
also exists in installed `2e196124` and in the signed `42339334` build, so the running
station is expected to hit the same wall ~460 reload probes after each control-plane
start unless this is delivered.

Focused red/green (same scenarios, same runtime, retained receipts in
`.agent-runs/black-silent-20261027/`):

- RED, production-exact: installed `runtime\python.exe` + installed `civiccast`
  2e196124 running the probe loop -> `RESULT probe #460 raised` with the byte-identical
  06:49 traceback lines and WinError 206. GREEN, fixed repo source on the same
  interpreter/runtime -> `RESULT all 600 probes succeeded`, one loader directory.
- `tests/native/test_gstreamer_runtime.py` adds
  `test_repeated_reload_probes_register_the_dll_directory_only_once` (deterministic
  loader-budget stub) and `test_real_loader_survives_repeated_reload_probes` (real
  loader, 600 probes). Both FAIL before the correction with the production exception
  and pass after (`2 failed` -> `32 passed` with the encoder-probe/decision tests).
- Actual native A/V after the previously fatal count: one process performs 600 real
  `probe_hardware_encoder` calls, then runs a real production-shape worker with a real
  content reload; `CTRL reload committed` is present, and the emitted 640x360 h264 +
  AAC 48k stereo TS decodes to mean RGB (249,0,3) solid red program (ffmpeg
  blackdetect reports no black segment) with mean_volume -20.0 dB, max -1.3 dB.

Limits: this proves the mechanism, the correction and the reload A/V path; it does not
prove that a ~95h soak now stays clean, and no new installer was built, so the
installed station still carries the unguarded call. Remaining executable action: root
review of the bounded diff, then commit/PR and a delivered build if the station is to
carry the fix.
