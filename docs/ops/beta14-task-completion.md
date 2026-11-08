# Beta 14 field fixes and delivery

## Scope and completion basis

The owner authorized all 28 goals in `C:\Users\scott\Desktop\Goals\CIVICCAST GOALS B14.md`, an exact-candidate Windows Sandbox installation, and a final installation on the development host. Each goal is evidenced by a focused behavior check or artifact inspection appropriate to its done criterion; the goal set does not require a full installed-UI walkthrough. This record distinguishes focused product evidence from candidate installation, runtime, historical and publication evidence.

The signed application candidate is PR 240, source `44525c14265d21ae461c2435d4d5a9a6acd58af3`, hosted build `38217950172`, version `1.0.0-beta.14`. Its installer SHA-256 is `e22d1cb9ae064841c45bed64a37e8ce9bd1e5176e0ff80945edccc362447082f` and Authenticode signature is valid. The later merged commit is `ed2f5069004abb6c3c6b4375ac82a07fcabe9a7b`; its release-tool, test, workflow and documentation changes do not alter the candidate's `civiccast/` product source. No candidate rebuild was needed for those follow-up changes.

## Goal evidence

| Goal | Focused evidence for the requested behavior |
| --- | --- |
| 1. Installer selections | Native Rust acquisition-selection checks and installer-flow UI checks verify that the selected model tier controls the downloaded assets. |
| 2. Whistle asset resolution | Runtime resolver checks distinguish Whistle from Whisper assets. All five Sandbox snapshots and both final host snapshots per channel used `whistle-primary`. |
| 3. Saved and live model selection | Runtime/API/UI checks cover the saved choice, service-start choice, and verified model path/tier crossing the native child-process boundary; the live boundary regression was observed failing before correction and passing after. |
| 4. CPU Whisper fallback | Focused timeout tests cover the configurable 120-second CPU fallback and containment of a stalled request, separately from Whistle's primary deadline and recovery behavior. |
| 5. Caption verification clocks | A captured, decoded offset MPEG-TS fixture accepts matching text with a 0.000311-second comparison delta and rejects incorrect text. |
| 6. Caption capacity status | Capacity checks exercise within-capacity, delayed, sustained overload and recovery. Sandbox samples show delayed/one overloaded observation while HLS and VTT continue; final host samples report within capacity. |
| 7. Summary resource protection | Focused Python and AI Models UI checks cover headroom deferral, retry visibility, serialization, unloading and the resource message (`install-model-receipt.md`). |
| 8. Actual live-source preflight | Source-probe wiring and isolated live-router regressions verify that probes use the configured source and that a rehearsal sample cannot satisfy the required live-source check (`live-recording-receipt.md`). |
| 9. Session and egress state | Router regression verifies that a retained ON AIR session does not mask stopped egress; the public-current fixtures now establish genuine ON AIR egress before testing the live URL behavior. |
| 10. Recording duration and continuity | Thirty-eight focused recording boundary, reconnect, timeout and drain checks pass. The 60-second request that ran 95.934 seconds was caused by capture beginning during the 30-second arming lead plus scheduler-tick finalization; capture now starts at the scheduled time and respects the remaining window. The separate 46-second wall / 11.73-second media observation lacks its original segment evidence, so its exact cause remains unknown. |
| 11. Recording to package to publish | Packaging and operator-Assets checks exercise the UI-only package action and subsequent publish workflow without requiring the raw recording API. |
| 12. Recording minimum and errors | Operator form checks exercise the 60-second minimum and friendly validation; API/recording checks cover accepted requests and readable failures. |
| 13. Saved ticker in public output | Focused checks follow saved ticker configuration through the board resolver, public snapshot and compositor output. |
| 14. Bulletin continuity | Filler/compositor checks verify that image-render failure is contained and the established safe-slate behavior remains available. |
| 15. Signed-summary PDF wrapping | The long-narrative records-router regression verifies pagination and signed-record integrity. |
| 16. Summary transcript playback | Source-grant/range and SummaryReview checks cover citation seeking, trimmed-package offsets and partial-coverage explanation; independent review reran the UI cases. |
| 17. Manual PDF clipping | Canonical and exact-candidate manuals each have 499 pages and zero characters outside page bounds. The exact candidate scan and representative visual checks are recorded in `evidence/beta14-candidate-manual-boundary-scan-2026-10-12.json`. |
| 18. Help navigation | Focused Help navigation checks pass. Exact-candidate Help inspection reports HTTP 200, all 100 internal targets present, and served assets available. |
| 19. Chapter-to-agenda sync | Agenda service/router/factory checks pass; a production app-factory regression with real `StaffAssetRow.chapters: list[Chapter]` went RED before model validation was corrected and GREEN after. |
| 20. Publish count and filter | Operator dashboard checks exercise the shared `needs_operator_action` predicate used by both the displayed count and filter. |
| 21. Resident archive action | Public portal checks verify the idle archive action resolves to published recordings. |
| 22. Schedule and playback transitions | Focused polling/HLS checks and a real MPEG-TS Chromium check cover last-good refresh, channel transition behavior, and title alignment to the buffered-media clock/source transition. |
| 23. Translation activity | Focused persistence/API and AI Models UI checks verify saved Spanish review-cue activity after reload (`install-model-receipt.md`). |
| 24. Packaged-media trim preview | Four TrimEditor checks cover packaged-HLS offsets and media-error retry on the same URL. Playwright decodes a real MPEG-TS fixture and maps its playhead using the 0.5-second source offset; separate backend checks cover staff grant, package containment and range delivery. |
| 25. Recorded-caption review audio | Review-media checks cover full-cue clip boundaries and explicit incomplete-source-audio reporting. |
| 26. Continuous scheduling and time accounting | Two schedule-plan checks cover published-sequence rotation and gap preservation. Reports checks distinguish scheduled UTC from engine-observed actual UTC and explain VOD preroll. Supported setup is `CIVICCAST_SCHEDULE_LOOP=1` with contiguous published items; the default remains finite. |
| 27. Backup readiness and output proof | Twenty-three readiness/commissioning checks distinguish configured backup, storage/probe outcomes and output-proof prerequisites; the hosted Egress virtual-headend gate passed. Local TSDuck 3.44-4676 parsed a finalized public HLS segment (1,837 packets, zero invalid syncs, zero transport errors). This does not prove UDP/headend/cable output. The R7 report says local HLS without actual cable output blocked cable/UDP proof. R9 reports a 15-second TSDuck proof failure but gives no check-level reason; its original cause remains unknown. |
| 28. Resolved install and storage paths | Focused API/runtime-path and Setup/StationProfile checks pass. Sandbox and host resolve install root `C:\Program Files\CivicCast (Native)` and database path `C:\ProgramData\CivicCast\data\pgdata`. |

## Candidate and installation evidence

The exact-candidate Sandbox fresh-install receipt passed: installer exit 0, Beta 14 service running, current schema and revision. Its five-snapshot, three-channel runtime check kept public, government and education ON AIR with H.264/AAC, growing VTTs and Whistle primary. Cue counts grew 6/6/7 to 56/56/57. Some observations were delayed and one was overloaded while captions continued; these are reported, and caption performance is advisory under the owner's policy. Exact-candidate Help inspection passed with 100 internal targets and no missing links. Receipts are under `../../outputs/beta14-work/candidate-beta14/consumer/fresh-evidence/Beta14/`.

The exact signed installer ran once on the development host and exited 0. The post-install continuation passed: service Running, Beta 14 healthy, schema current at `0089_contribution_media_control_requests`, schedule loop enabled, and candidate paths intact. The first observer stopped because it classified 227 separately manifested GStreamer paths as app-payload orphans; continuation verified the app manifest and the GStreamer component separately, including its 224 manifested files and running-service resolution. The initial observer result remains recorded as a verifier-scope failure, not an installer failure.

The final host runtime consumer receipt passed for all three channels in two public-health/HLS/ffprobe/file snapshots 35.291 seconds apart. HLS output was H.264/AAC, VTT files were available and changed, and all three channels reported healthy within-capacity caption state. The publisher accepted the bound receipt and reported no caption-performance findings. Host receipts are under `../../outputs/beta14-work/host-runtime-final/`.

The host preservation continuation attests that the original control flow completed comparisons for service/schema, install/data paths, data presence and schedule-loop state. Raw pre-install values were not persisted, and account or schedule rows were not compared. Do not interpret the continuation as an archived raw baseline.

Hosted follow-up recorded an initial consolidated Python run of 1,087 passed and five stale-fixture failures. The affected fixtures were corrected; narrow selections passed, and the later hosted full Unit tests and randomized-suite jobs passed. The Egress virtual-headend, claims-evidence verifier, independent Windows workspaces, security, installer, accessibility and lint jobs also passed. The saved current-checks snapshot retains the informational mutation-report as in progress and three native-beta jobs as skipped; these statuses are not represented as completed passes. The first Egress attempt only encountered a truncated prerequisite download and ran no tests; its retry passed.

The historical 72-hour, three-channel, 99.78% caption-input coverage result is Beta 11 evidence, not a Beta 14 measurement. It remains identified by its original release in `evidence/beta11-host-caption-performance-72-hour-2026-10-11.json`.

## Publication

Beta 14 was published as a public prerelease on 2026-10-12 at 10:25:01 UTC. The 26 release assets passed name and size verification. The published release is [v1.0.0-beta.14](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.14). All 26 GitHub asset SHA-256 digests match the publication files; six actual public downloads passed SHA-256 verification, and the downloaded installer has a Valid Scott Converse Authenticode signature. The final 19-member D: kit and both Beta 14 installation prompts are verified. See [public-download evidence](evidence/beta14-public-download-verification-2026-10-12.json) and the [release verification record](../releases/v1.0.0-beta.14-verification.md).
