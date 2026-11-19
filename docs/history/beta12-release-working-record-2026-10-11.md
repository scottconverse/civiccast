> **Historical working record, preserved October 11, 2026.** Current Beta 20 guidance is in the [release verification record](../releases/v1.0.0-beta.20-verification.md) and [project status](../../PROJECT-STATUS.md). The dated checkpoints below do not define current release status.

# Beta 12 release verification

Beta 12 was published on October 10, 2026 as a GitHub pre-release; it is not a
production release. This record separates package identity, observed upgrade
activity and incomplete post-upgrade checks. Beta 11 host observations below
remain historical evidence, not Beta 12 qualification.

## Current public release

### October 11 documentation revision

The landing page now leads with the three-channel, 48-hour Beta 11 host soak.
Obsolete Beta 10 caption-loss examples and fixed-feature warnings were removed
from customer-facing copy. The detailed historical reports remain unchanged.
The revised downloadable PDF and Word manual and regenerated built-in manual
source share manual source SHA-256
`5c1f647bcefb22e3849737d4ae2b8c0e94a5b10f9cb85bba13e1f5af6975b23a`.
This is a documentation revision; the signed installer and runtime packs retain
their original source and hashes. Already-installed built-in manual copies are
not changed by updating the website or release downloads. The original package
manual provenance remains recorded below.

The current published release is source
`a0c98cb7367dc106042ec89d35a91905470d350c`, producer run
38097350705.
Its candidate-assets job `114349148673` completed successfully; the signed
installer reports a valid Authenticode signature for Scott Converse and its
six candidate files matched the run's SHA-256 manifest. The 19-member local
offline kit is recorded at
`outputs/beta12-candidate-a0c98cb7/kit-assembly-receipt.json` (receipt SHA-256
`5be35134ca88c50875159913d5b1007706aa518dfee09ebcb8b82a527add02d4`); the
setup SHA-256 is
`2dbc982d07b66f5bc760dfc1c1a943e9a3e100d3c3c7421ace74d6a2c744ab12`.
The [GitHub release page](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.12)
publishes 12 standard assets: the signed installer, five runtime packs, four
manual assets, checksums and installer sidecar. The station proof records local
canonical Ed25519 verification and exact size/SHA-256 comparison of the six
required packs. The locally assembled 19-member kit includes station
resources; the receipt is local evidence, not a release asset.

The separate randomized-suite run `38097353468`, job `114345951910`, failed.
This recorded CI failure is distinct from the installer result; the release was
published with that evidence preserved.

The normal upgrade from the pinned Beta 11 baseline ran on October 10 from
20:07:13 to 20:25:15 MDT. Setup exited 0, the service was running, the API
reported healthy with current schema 0089, and the preservation assertion
reached and saved its marker. Initial observations showed HLS audio and video
on all three channels and primary Whistle within capacity. At 20:31:16 MDT the
post-upgrade recorder wrapper failed during its first-minute JSON save with
`Cannot convert value to type System.String.` No five-snapshot PASS was
recorded. The owner deferred the recorder issue as bookkeeping; this record
preserves the failure without claiming the snapshot check passed. Interrupted
repair and fresh-install checks have not been run, and no sustained-operation
result is claimed.

## Published download verification

Published at 2026-10-11 02:37:18 UTC (October 10, 20:37:18 MDT).
The public tag resolves to the exact producer commit `a0c98cb7367dc106042ec89d35a91905470d350c`.
All 12 public asset SHA-256 digests and sizes match the local upload files.
The public installer, PDF and Word manual were actually downloaded over HTTP;
their hashes match and the downloaded installer has a valid Authenticode
signature. The large runtime packs were checked against GitHub's public
SHA-256 digests and sizes rather than downloaded again. Local receipt:
`outputs/beta12-candidate-a0c98cb7/public-release-verification.json`.

## Separate Beta 11 host observation

The October 8–10 three-channel host run reached 48 hours. Its 635 seconds of
logged discarded caption input across 518,400 nominal station-seconds yield
99.877507716%, rounded to **99.88% live caption-input coverage**. Correct late
captions count as successful. This is a Beta 11 host observation, not Beta 12
soak or installer qualification. The [48-hour report](../ops/beta11-host-caption-performance-48-hour-2026-10-10.md)
contains the counting method, raw evidence links and measurement limits.

## Candidate identity

- Earlier audit checkpoint: `6bdec65acffd75bb6124cf94a91c5222d1b678d9`.
  The final producer is frozen at the `a0c98cb7` source recorded above; this
  audit checkpoint is not the artifact source.
- Historical partial producer: source
  `327fb3fe412853c5fcff6860eeb9f0bdf8221e19`, run `38088340484`. The owner
  canceled this producer; it is terminal and was not a producer failure. Its
  manual and station jobs and component-closure check completed successfully.
  Local canonical Ed25519 verification, six station-pack size/hash comparisons
  and all seven station checksum entries passed; the station index SHA-256 is
  `5bdd44e729d340193095fdc439081cede1600c99c5f1a09e7c8062f9efed6156`.
  The producer did not yield complete installer assets, a complete installer
  kit, an assembly receipt or a consumer installation result. These partial
  artifacts do not qualify the final candidate. The randomized CI job
  `114319492039` finished with 11,244 passed, 83 skipped and two failed
  collection-count checks. The 16 added runtime-closure cases increased the
  actual collections to 1,984 pure, 2,195 total and 2,192 Windows
  non-integration tests. The corresponding workflow floors are corrected to
  1,934 and 2,142, retaining the existing 50-test margin. The exact-count
  assertion is updated without removing any checks; both affected policy
  tests passed locally in 21.79 seconds. This CI metadata correction does not
  change application behavior or establish a frozen release source.

- Previous signed producer: source
  `486be5f917264ae73cc2333bfed78d19761eb071`, run `38086022740`.
  Manual and station jobs passed; their downloaded artifacts were verified.
  The installer-assets job `114316486913` FAILED during runtime closure:
  six checks passed and `dynamic_trace` rejected one DesktopAppInstaller
  `MergedResources-0.pri` metadata path. No installer artifact was uploaded,
  and no complete installable kit exists for this run. The failed report
  lacks per-leg/protection evidence, so its exact classification cause is
  unproved. Inspection did establish a collector defect: grouped memory maps
  were all labeled loaded modules. A bounded correction retains every mapped
  path, distinguishes known data from executable/unknown mappings, and keeps
  the existing exact canonical resource exception. It does not claim to prove
  the cause of this failed build. At that point a new signed producer remained
  necessary; the current producer is recorded above.
  The affected closure-verifier module passed 100 tests. Independent review
  passed 25 focused tests and real hardlink alias checks; executable evidence
  takes precedence over unknown evidence, which takes precedence over data.
  Malformed permissions stay strict. Repository formatting, scoped lint and
  release identity checks passed. These are source checks, not a successful
  rerun of the failed producer or a completed installation.

- Earlier assembled installer, which failed normal upgrade:
  `523d14516b08dcc639c9c2f417d69c647eccefd7`, signed prepare-only producer
  `38076855646`. All three producer jobs succeeded (manual `114287584423`,
  station `114287584610`, installer `114292522506`). The offline kit has 19
  members; all six downloaded candidate files match the producer checksums and
  sizes. Setup SHA-256 is
  `9bc8a8629d3044b8b0de23aea5dc305e68b0006b4303a46cb6541f0838cbefec`;
  local Authenticode verification is `Valid`, signer Scott Converse.
  Assembly receipt: `outputs/beta12-candidate-523d1451/kit-assembly-receipt.json`,
  SHA-256 `47f66d5572d8d2fdb97f9110993b6c533c2cbe0fcd23accf1ca2d1a5a3b29af6`.
  Local canonical Ed25519 verification and actual size/SHA-256 comparison of
  all six adjacent station packs passed. The exact-source manual passed
  verification. The normal Beta 11-to-Beta 12 upgrade ran in the existing
  CPU-only Windows Sandbox from 14:27 to 14:40 MDT on October 10 and FAILED:
  installer exit123, d4-activate-station returned67 after approximately301seconds.
  The installer contained the failure by stopping the guest service and setting
  it to manual start. One isolated activation-only diagnostic replay wrote a
  successful activation receipt after about429.7seconds; its asynchronous process
  exit code was unavailable. The original failure did not reproduce and its
  cause remains unknown. Neither this replay nor the verified kit establishes
  a successful normal upgrade for this earlier installer. Repair and fresh
  install checks for that artifact were not run. A narrow source correction
  records bounded activation child errors in the installer progress log. The
  later signed `a0c98cb7` release includes that correction; its separate normal-
  upgrade observations are recorded above. Later test-only and documentation
  commits do not change either artifact.

- The old guest's registry/payload identities became mixed after that failed
  upgrade and replay; it is not a valid working Beta 11 baseline. Its evidence
  and mapped store were preserved. A new isolated CPU-only Windows Sandbox
  (`ac8f796d-33cb-465b-9aa7-42ddee18c149`) completed the pinned Beta 11
  installation with actual setup exit 0. Its service is running and its
  API reports healthy/current Beta 11. The existing preparation check exited
  0 and recorded the account, uploaded asset, three future schedule IDs and
  caption-enabled profile. Our first checker incorrectly required a Beta 12
  health field from Beta 11; that failed attempt is preserved. After a narrow,
  independently reviewed exact-version compatibility correction, preparation
  resumed successfully without reinstalling. The baseline remains available
  for the current release; its normal-upgrade evidence is described above.
  Host stations remain untouched.

- Earlier assembled diagnostic kit: source
  `c6f0864becfb93444853c754cfc50f4226267209`, successful signed prepare-only
  producer `38031718156`. All 19 members were assembled and checked; setup
  SHA-256 `f106fc9b75be3f6e813d7d5424b1766de979de529f721f2ced4e6baad9f4b225`
  has a valid Scott Converse signature. Receipt:
  `outputs/beta12-candidate-c6f0864b/kit-assembly-receipt.json`, SHA-256
  `9e529110f39329f61cd39ed8828d108d877ff4b307b93f1e802577e2881585e9`.
  Station-index Ed25519 verification and six adjacent station-pack size/hash
  comparisons were local checks, not compiled bootstrap station-index proof.
  This earlier diagnostic kit excludes the subsequent Whistle startup, recording,
  live-preflight and Off air changes included in the current `a0c98cb7` candidate.
- Version: `v1.0.0-beta.12`.
- Development branch: `codex/beta12-reliability`.
- Earlier diagnostic kit producer: `f93beca07afc5605fb6c70a27fa16ac15d936210`,
  run `38017271666`. Its signed offline kit contains 19 members. The setup
  SHA-256 is `6eec8660fa6a6017174bed52ff1410bb51dee13dd90119272ca49018217603a4`;
  Authenticode validation passed for the Scott Converse signer. The signed
  station index SHA-256 is
  `19c92101fbf6869cb8695b7f5e787b53700fdc1837595584b834b36ceb2ea58b`.
  The assembly receipt SHA-256 is
  `fe1a1ec44f4932bdbb136ff63b239f2024c406eab8f452a5b04f2a6a0650d29d`.
  Manual source SHA-256 is
  `9d705d88bca691d3924596822d0ed314ad08508dea911a11313ee5a16f3ab3a0`, PDF
  SHA-256 is `82982cb396600d1b7aea5e5efaa635aea33bf35cec5c27050478fb0dc623962e`,
  and Word SHA-256 is
  `5b2cda545e8147d9111fccfa79074e924323a80b1e7cb3a9b7c4acf88d724bf3`.
  The exact assembly receipt and installed-consumer evidence are retained in
  `outputs/beta12-candidate-f93beca0/`.
  This kit predates the retry-progress health correction and matching manual
  update described below. Its separate post-repair caption check failed; it is
  not a qualified release package. The later `a0c98cb7` release has separate
  normal-upgrade evidence above; repair and fresh-install checks remain unrun.
- The earlier diagnostic producer `183d87ddd96857651663710527ebd00086062745`
  (run `38004934464`) assembled a 19-member kit with setup SHA-256
  `63d68d93c1cce206446b9ff6a000404a209a90817a86a8253ce9927d7482decd`.
  Its overall fresh-runtime check failed as described below. That failure is
  historical and is not the result of the corrected F93 fresh-install check.

## Source changes and checks

### October 10 remaining remote-control correction

The punch-list completion review found that the requested Off air media action
was absent from the current UI. The bounded correction sends the existing
targeted audio-mute and camera-mute commands together, preserving the guest
connection and channel source. It does not implement a native compositor:
S17 assigns that integration to a separately commissioned OBS/GStreamer path.
The existing audio command affects the guest's outgoing audio; the previous
director-only label understated its effect.

Independent inspection and a small real-provider check used the pinned local
VDO.Ninja v30.2 checkout `d5a8b566`, fake media and one headless Chromium browser
with GPU disabled. The pair changed guest audio gain from 1 to 0 and disabled
the actual video track; both tracks remained live and the peer/data-channel
connection remained open. The provider emitted two existing callback errors,
so this does not establish a provider acknowledgement. Raw observations and
command provenance are retained in
`outputs/beta12-offair-provider-proof-2026-10-10.md` in the working evidence
directory. No running host station, Sandbox or model was changed by that check.

The UI correction passed all 26 affected screen tests, scoped ESLint and
Python Ruff checks. Its new control assertion failed before implementation;
two focused regressions also passed independently. Independent diff review
reported no findings. The paired requests share one resolved guest target and
pending state; each send/save outcome is reported separately, and failure of
one request does not prevent attempting the other. Neither lifecycle tags nor
channel takeover are changed by this action.

The coordinator's TypeScript build check passed. Manual assembly reused the
existing diagrams; tracked PDF, Word and in-product HTML were regenerated.
Both renderer currentness checks passed, and release identity remains aligned
at `v1.0.0-beta.12`. The manual source SHA-256 is
`324e4c31cfc6ff4666767ad10468f2c0457f4522603c8d0e0f445ead7f3c60fe`.
These checks establish current source artifacts, not installed Help or public
download acceptance.

Producer `38073908014` remains on `688b2dc` and cannot establish inclusion of
this later application change. Final package acceptance must use a build that
includes the completed control and synchronized manual.

The candidate covers existing workflows across the application:

| Area | Implemented behavior | Verification scope |
| --- | --- | --- |
| Caption health | Worker activity, processing stalls, silence, engine/fallback state and scoped failure alerts; public readiness includes a coarse caption verdict. | Backend and screen checks, including continuous input that previously masked hung inference. No station soak claimed. |
| Raw logs | Supervisor drains and rotates control-plane and PostgreSQL output, including output after the PostgreSQL launcher exits. | Supervisor tests and an isolated real bundled PostgreSQL rollover/shutdown check. Existing oversized backups may remain until rotation ages them out. |
| Summaries and records | Pending narrative edits, stale-draft approval rejection, authenticated approval identity, approved-record history, download and verification. | Backend/store checks, independent-connection races and browser workflow checks. No real timestamp-authority claim. |
| Local administrator reset | Elevated, offline credential reset with protected backup and exclusive supervisor lock; station data preserved and old sessions/codes revoked. | Isolated credential-state, Windows ACL/mutex and console checks. The running host's credentials were not reset. Regenerate the recovery kit after signing in. |
| Control Room | Recoverable session after navigation/refresh, visible expiry, authorized release/reclaim and Panic after an ordinary expired cue is refused. | Backend state sequence and screen checks with a fake device. No hardware switcher proof. |
| Remote contribution | Supported pinned VDO.Ninja director commands, truthful unverified request state, separate confirmed channel takeover and configured-channel validation. | Two synthetic Chromium peers exercised camera mute/unmute, audio gain mute/unmute and disconnect. Independent validation checks confirmed unknown/disabled channels are rejected before audit or enqueue. Provider callback errors prevent claiming a completion acknowledgement. Guest routing into the native broadcast compositor remains absent. |
| Alerts | Console destination assignment, preserved unavailable assignments, role enforcement and missing default failure rules; dispatch follows event commit and transport/retry outcomes persist. | UI, migration and evaluator checks plus file-backed SQLite and real local HTTP firing/recovery/failure/retry checks. No real SMTP, SMS or external webhook delivery claimed. |
| Emergency presentation | Opt-in channel compositor layer and channel-specific resident warning; clearing retains ordinary graphics. A replacement-render failure preserves the accepted warning, and retry files are bounded. | Isolated actual CPU/D3D11 video, resident browser and independently reviewed failure-order checks. A Windows unlink failure after clear can leave the warning absent until cleanup succeeds; the manual documents this limitation. No live CAP-feed or cable-headend acceptance claimed. |
| Paywall | Ordinary saves preserve the signing secret; clearing and rotation are explicit; validation errors redact secret input. | Backend, screen and independent malformed-request checks. |
| Documentation | Updated affected help, API reference, source manual and rendered PDF/Word/in-app manual. | Generated API/manual artifacts regenerated after the final behavior changes; source/version currentness checks remain separate from installed Help verification. |

The integrated operator build and 155 affected screen tests passed. The public
portal build passed. The release identity/documentation suite passed 65 tests.
An initial combined backend run passed 1,024 tests and failed 48 with unexpected
401 responses. A representative failure reproduced alone: the test process
inherited a configured staff-token list that superseded its deterministic test
token. Removing that setting only within the test process produced 312 passing
alerting, summary and records checks. Production authentication was unchanged.
The subsequent combined backend run passed 1,178 tests in 114.91 seconds with
six Windows skips for POSIX FIFO contracts. It included takeover validation and
emergency failure-order checks. The existing type check passed all 690 source
files. These are affected-source checks, not a claim that every repository test
or CI job has passed.

These are development results from the working source tree. The signed package
identity is recorded above; source checks alone do not establish installed
operation or package acceptance.

## Hosted CI follow-up

The first PR source checkpoint was
`f44ca32808c51495eb88f50c471a7b895d12de5c`. Operator browser CI passed 253
tests and failed three: two session fixtures omitted the newly required open-session
lookup, and one exposed incorrect stale-draft guidance for a record-export conflict.
Independent browser review also found insufficient contrast in the Control Room
safe-state helper text. Follow-up fixes cover the session lookup/resume path,
operation-specific error recovery and accessible helper text.

Windows native CI failed two recovery permission assertions because its ACL
included `S-1-3-4` (OWNER RIGHTS), which identifies the descriptor owner. The
follow-up assertion checks the actual owner and every permission entry before
resolving that alias; unrelated principals remain rejected. Production ACL
hardening is unchanged. The original CI log did not expose the owner or entry
rights, so its descriptor cannot be certified from that log alone.

Local follow-up checks passed all 24 administrator-recovery tests without
skips, five affected browser cases, the separately rerun safe-state axe case,
the operator build and focused lint. Independent CSS inspection measured the
corrected helper contrast at 15.93:1 in the light theme and 11.49:1 in the dark
theme. The changed documentation/identity checks passed 15 tests.
Independent permission review reran the 24 tests and confirmed that the new
assertion rejects an unrelated trustee or owner, a deny entry, insufficient
rights and an inherit-only entry. The actual hosted runner descriptor remains
subject to the next Windows CI execution.

Hosted operator accessibility and Windows native checks passed on follow-up
source `875834818b73bfbd091f93ee1e3490dce3cf3388`. A subsequent real local
HTTP check of the production alert evaluator/dispatcher exposed a separate
transaction-order defect: the event was dispatched before its commit, and
delivery/retry outcomes were not committed by their separate sessions. Shared
test sessions had hidden the failure. The corrective change defers dispatch
until commit/session close and persists transport and retry outcomes; three
file-backed SQLite/loopback regressions failed before the change and passed
afterward, alongside 70 affected tests and focused static checks.
Independent review passed all 256 alerting tests. Its separate local HTTP
receiver received valid-HMAC `firing` then `resolved` payloads; fresh database
sessions confirmed the resolved event and both sent delivery records.

The affected documentation run passed 25 checks and found one tracked-download
mismatch: the candidate render was Beta 12, but `docs/USER-MANUAL.pdf` and
Word still contained Beta 11. The verified fresh candidate bytes were copied
to those tracked paths with the manifest paths updated. Currentness passed for
both render locations and the in-app manual; the formerly failing tracked-manual
check then passed. Public downloads were not changed by this branch-only fix.

The existing delivery model is optimistic: a process crash between recording
a delivery and sending it can leave a `sent` row without transport. A crash
after remote acceptance but before saving the outcome can duplicate a retry.
Payloads use the latest committed event state, including on retry. This change
does not introduce a transactional outbox or promise exactly-once delivery.

## Current source and database follow-up

At source checkpoint `4a44d8d7132deb07db0c3002bdf10b35735180ef`, the real
PostgreSQL migration chain exposed that revision `0089` added columns to
`remote_guest_sessions` without naming the `civiccast` schema. The migration
now qualifies those additions and its downgrade consistently. The focused
regression passed against an isolated bundled PostgreSQL 17 cluster, including
upgrade through head, downgrade to `0088`, confirmation that the three columns
were removed, and re-upgrade. The focused command reported 4 passed in 3.73
seconds; it did not use the development station's database.

Provisional signed build `38000097070` was cancelled before package acceptance
while source CI still had unresolved failures. The randomized detector run
`38000087246` at this checkpoint reported 104 failures; 73 came from the
PostgreSQL schema defect corrected above and one from a stale migration-head
assertion. The exact run summary was 104 failed, 11,073 passed and 83 skipped;
its raw log remains at
`C:\Users\scott\AppData\Local\Temp\beta12-randomized-suite-38000087246\pytest-random.log`.
Follow-up diagnosis accounted for all remaining failures: five
readiness tests had omitted the required enabled egress configuration, three
CG checks used an obsolete synthetic-overlay request, two native-test policy
expectations lagged fresh collections, one release-note assertion expected the
old Beta 11 label, one manual check retained a 636-entry count after the manual
grew to 638, and 18 policy checks exposed bound-blob drift in two historical
external-evidence claims. The egress/CG fixtures and workflow counts now have
targeted updates; their affected local checks passed (41 tests). The TOC count
was updated and `uv run pytest tests/docsite/test_render.py -q` passed 20 tests;
the rendered manual's `--check-current` check passed. Current-source-only
rebinding covers 14 role bindings: eight code/test bindings across four files
and six bindings to one workflow file. Direct D2 evaluation reported zero
drift, and the complete claims suite passed 125 tests in 81.86 seconds. The
historical observations and separate Session 0 pre-login hardware-proof
boundary are unchanged.

Exact-source manual rendering and currentness checks passed on candidate
`183d87ddd96857651663710527ebd00086062745` in run `38004934464`; the rendered
manual, station-embedding and station-preparation artifacts were uploaded.
The K1 station-bundle job provisioned the pinned models, built and verified the
signed bundle, and uploaded the installer-embedded resources. The final
native-beta assets job subsequently completed; the complete signed installer
and assembled offline kit are recorded under Candidate identity above.

The hosted Unit job in run `38004049724` completed its full pytest command with
11,173 passed and 82 skipped in 1,801.684 seconds. Its platform-independent
native collection had 1,959 cases: 1,957 passed and two were skipped, so the
subsequent no-skip proof verifier failed. The skipped cases were
`test_restrict_state_file_maps_explicit_owner_rights_to_verified_owner` and
`test_offline_guard_excludes_another_windows_process_owner` in
`tests/native/test_admin_recovery.py`; they were not failures of the pytest
assertions. A local marker correction classifies both as `windows_only` while
retaining their `skipif` guards. Focused policy checks passed (4 tests), and
collection checks show Linux excludes those two cases while the Windows
non-integration selection includes them. Follow-up hosted run `38008111076`
on `69ff80bd` passed: 11,173 unit tests with 82 skips, 1,957 Linux native tests
with no skips, and 2,165 Windows native tests with no skips. WS4 and the claims
verifier passed. The marker correction is therefore validated in hosted CI.

On the same source checkpoint, the Windows WS4 job passed the full native
suite with no skips. Randomized run `38004049570` passed 11,177 tests with 83
skipped at seed `4187141027`; its native-only non-Windows subset passed 1,957
with two Windows skips. Reproducibility run `38004049778` built twice in
independent Windows workspaces and confirmed byte equality. The operator and
public-portal accessibility checks passed.

The informational mutation job failed because its environment did not install
FFmpeg before a real-media test (`FfmpegNotFoundError: ffmpeg not found on
PATH`); it reported 2,429 passed, 24 skipped and one failed test before mutmut
could collect results. A local workflow-only correction adds the existing
bounded media-prerequisite setup used by the randomized and Unit jobs. YAML
parsing, the affected workflow-policy test and diff checks passed locally.
Follow-up run `38008111047` installed the prerequisites and progressed beyond
the former missing-FFmpeg failure, then exposed the separate recovery-test
assumption described below. These CI follow-ups are not part of source commit
`183d87d`.

Beta 12 is published as a GitHub pre-release. The current signed `a0c98cb7`
release and its 19-member local kit are recorded above. The normal-upgrade
setup and initial service observations are recorded, but the wrapper did not
produce five post-upgrade snapshots. Interrupted repair, fresh-install and
direct-consumer receipt checks remain incomplete.

Provisional signed builds `37996681227` and `37998508533` were cancelled.
Only their manual artifacts were downloaded and verified; these are historical
evidence. Producer run `38004934464` is the completed exact-source build for
commit `183d87d`; its manual, K1 and final native-beta assets are recorded above.
The accepted package must remain bound
to its exact producer commit. A later tag or merge commit containing only CI,
test-selection or publication metadata changes is recorded separately by the
publisher; it does not rename the artifact producer. Passing local checks or
a successful producer does not establish that all hosted checks or installation
checks pass.

The subsequent detector run `38008111047` on `69ff80bd` reported 11,176 passed,
83 skipped and one failure at randomized seed `817170042`. The valid token
request returned HTTP 200, but took 1.187 seconds against a test's 0.5-second
end-to-end limit. A test-only correction checks the actual admission contract:
four saturated guesses return 429 without invoking the bearer verifier, while
the configured valid token returns 200. A deliberate admission bypass failed
the new assertion; the three focused checks passed after restoration. This
does not establish a numeric latency guarantee or the precise Linux timing cause.

That run's mutation job installed the media prerequisites successfully, then
failed during the unmutated `Running stats` phase. Its recovery test confirmed
release of the abandoned plan but rejected a new, legitimate slate-first
handoff that completed during the same tick. The focused correction exercises
both pending and same-tick completion, verifies release of the old plan exactly
once, and checks the replacement plan's identity, worker, content and state.
Deliberately bypassing cleanup failed the release assertion; the restored
neighboring lifecycle checks passed 10 cases. This is not evidence of a killed
mutant or a successful complete mutation run. Both corrections change tests
only; those two corrections did not change application bytes.

Detector run `38014658803` on the later Beta 12 source also stopped in its Unit
and Windows WS4 baselines at
`tests/alerting/test_router.py::test_runtime_safe_to_air_exposes_active_manual_caption_worker`.
That assertion still expected `waiting` for fresh, audio-present, caught-up
processing; the intended health state is now `caught-up`. Its randomized job
reported 11,185 passed, 83 skipped and one failed test. The mutation job
reported 753 passed, 6 skipped and 8 deselected before its stats-collection
failure, so it produced no mutant-survival result. These detector outcomes are
separate from the failed candidate-assets producer documented below. The stale
expectation was reproduced with a clean test-process token environment: the
focused assertion failed once in 3.15 seconds, then passed after correction in
2.92 seconds; the full alert-router module then passed 22 tests in 11.79 seconds.

Detector run `38017271224` on f93 later failed in mutation stats collection at
`tests/egress/test_gst_engine_reload_commit_ordering.py::test_captions_av_pending_selector_switches_confirm_before_old_tail_retirement`:
the test observed reload settlement before the watchdog-completion event. A
bounded deterministic reproduction confirmed the callback-before-watchdog
ordering while the reload and cleanup assertions remained satisfied. The test
now waits up to one second for the watchdog-completion event after settlement.
The exact node passed in 1.52 seconds; its module passed 127 tests with 2
bundled-runtime checks skipped in 2.44 seconds. Mutation stats still produced no
mutant-survival result. This correction changes tests and verification prose
only; the application payload bytes are unchanged.

The same detector run's randomized job `114110224718` reported 11,184 passed,
83 skipped and two failed in 1,797.68 seconds. Both failures were stale native
collection policy metadata: the actual pure/total collection was 1,968/2,179
instead of 1,957/2,168, and the prior pure floor 1,909 sat 59 tests below the
actual collection. The Windows `not integration` collection was 2,176, leaving
the prior 2,115 floor 61 tests below. The policy now reflects the measured
counts and floors 1,918/2,126, retaining the existing 50-test margin without
changing selectors or test requirements. The two existing policy tests failed
on the old values, then passed after the correction (2 passed in 21.67
seconds); the existing claim-binding check passed (1 in 3.75 seconds) after
updating only the six workflow-role blob bindings to the exact workflow blob.
At this checkpoint the f93 hosted jobs still recorded failures; later
qualification-source results are recorded below. The correction had not yet
been checked in a new hosted run at that time.

Hosted run `38019770219` on source `03ed187` failed its Linux Unit job because
the `escape` near-miss fixture in
`tests/native/test_closure_verifier.py::test_desktop_app_installer_resource_exception_rejects_near_misses`
formed a `../../../` path beneath a package-metadata directory that it had not
created; the target existed, but the missing path prefix made the fixture's
own `resource.exists()` assertion fail before the verifier ran. The test now
creates that directory and retains the same escape-path and verifier-rejection
assertions. The claims-evidence job then failed closed because the Unit
producer result was `failure` and expected CI evidence nodes were absent; this
is downstream of the Unit failure, not a separate registry change. The test
correction has not yet passed hosted Linux CI. The separate randomized job in
run `38019770223` had 11,185 passed, 83 skipped and one error at seed
`3743501697`: Docker
Hub's Ryuk image authentication request timed out before the PostgreSQL backup
test could start. That external error is separate from the Unit fixture failure.
The corrected closure-verifier module passes locally (84 passed in 2.60 seconds);
Ruff and `git diff --check` also pass. The hosted follow-up results are recorded
below.

The current qualification-source Unit job `114125837552` passed 11,182 tests,
with 82 skipped and 5 deselected, in 2,498.85 seconds. Its Linux pure-native
collection passed 1,968 tests with no skips; Windows WS4 passed 2,176 with no
skips. The claims-evidence verifier, reproducibility job and randomized job
passed. These checks include CI/test metadata after the F93 application source;
the offline kit remains bound to producer source
`f93beca07afc5605fb6c70a27fa16ac15d936210`.

The a8fa mutation job did not complete its baseline: the unchanged pinned
manifest test could not find a committed Ollama manifest in mutmut's copied
tree. It reported 6,978 passed, 92 skipped, 8 deselected and 1 failed in
2,039.64 seconds, then failed to collect mutation statistics. The targeted
workflow prerequisite fix passed 26 local tests and is awaiting hosted
verification; there is no mutation-survival result. Raw job logs and the local
fix receipt are retained as `outputs/beta12-a8fa-*.log` and
`outputs/beta12-a8fa-mutmut-prerequisite-fix.txt`.

## Installed diagnostic package and health correction

The offline, CPU-only 9 GiB Windows Sandbox installation of producer `183d87d`
exited 0 after 1,781.608 seconds. Beta 12 was installed, its supervisor was
running, and schema revision `0089_contribution_media_control_requests` was
current. The subsequent public-channel check failed at its first one-minute
health assertion. Its original failure remains recorded as FAIL; the assertion
had not saved that instant's response body.

Two later read-only guest snapshots reproduced degraded health while Whistle
was active, its heartbeat and completed work were current, and no backlog or
inference was pending. Replaying the actual status through the source confirmed
that completed, caught-up audio became `waiting`, then `unknown`, which degraded
`/health` between chunks. This is a readiness-classification defect, not evidence
that the observed Whistle process timed out.

The correction adds the `caught-up` state only for recent completed audio that
has caught up with the latest input. Existing failure, stale-heartbeat, pending
inference and silence checks retain priority. The operator label and generated
API contracts match the new state. A regression using the actual guest status
failed before the correction; the corrected runtime suite passed 43 tests and
runtime/API-contract checks passed 52. Independent review passed 50 runtime and
health checks, 24 UI tests, and explicit stale/missing/failed/pending boundary
probes. Caught-up processing readiness does not establish decoded captions or
verified broadcast delivery. Existing manual guidance remains accurate.

An independent installed summary check in that same diagnostic guest passed
generation, editing, approval, PDF export, download and API verification using
`gemma4:e4b` on CPU. The downloaded PDF's recorded SHA-256 matched. No external
timestamp-authority call occurred. This verifies the diagnostic package's
summary workflow; it does not override the failed overall fresh run or qualify
the next producer. The guest checker now retains the health response before
asserting and captures bounded failure diagnostics.

A separate installed Help recheck on that guest passed at `2026-10-10T01:50:26Z`.
It verified the Beta 12 manual in the in-product Help shell (`/operator/#/help`,
HTTP 200), checked two operator assets and found no missing targets among 99
internal Help links. The installed manual file SHA-256 was
`754762176645f76b6adc75133717217b9389cf0e9914931e18ede32bf98a4b30`. The
administrator-reset CLI `--help` returned 0, confirming availability only; no
reset was performed, and browser clicks were not tested in the guest. The
diagnostic Sandbox was stopped at `2026-10-10T01:51:15Z`, with the host station
unchanged. This Help-only PASS does not alter the original fresh-runtime FAIL.
Evidence: `consumer/fresh-evidence/help-recheck-result.json`,
`consumer/fresh-evidence/help-inspection.json` and `consumer/fresh-stop.json`
under the retained P183 consumer evidence directory. This is historical P183
evidence and does not replace the corrected candidate check below.

## Historical F93 installed-candidate check

The exact F93 producer kit was installed in a separate, offline 9-GiB Windows
Sandbox with GPU disabled. The installer exited 0, reported Beta 12, started
the service, and returned healthy/current from `/health`. The fresh caption
check passed one public channel with Whistle-primary spoken JFK captions and
five minutes of fresh HLS containing audio and video. The retained `DONE.txt`
is `PASS`; the earlier P183 runtime failure remains recorded above and is not
silently replaced by this later run. Evidence is under
`outputs/beta12-candidate-f93beca0/consumer/fresh-evidence/` in
`fresh-install-command.log`, `fresh-caption-command.log`, and `DONE.txt`.

The same installed F93 payload passed summary generation, editing, approval,
PDF export, download and API verification using `gemma4:e4b`. The downloaded
PDF SHA-256 was
`1456a58201e691a6b439f6c92c90a047c8c0af089ed8f09ff9180b83c994bed8`. No
external TSA call occurred; this run used the local deterministic timestamp
authority and recorded no credentials. The in-product Help shell returned
HTTP 200, checked two operator assets, and found no missing target among 99
internal links. The in-product manual file SHA-256 was
`754762176645f76b6adc75133717217b9389cf0e9914931e18ede32bf98a4b30`; its
source SHA matches the manual identity above. The admin-reset CLI help was
available, but no reset was
performed and browser clicks were not tested in the guest. The Sandbox was
stopped and verified empty; the host station was not changed. Evidence is in
`summary-record-flow.json`, `help-inspection.json`, and `fresh-stop.json` under
the same `fresh-evidence/` directory. This proves the stated guest workflows,
not a target-machine installation, external TSA, or upgrade.

## Historical F93 cross-version upgrade

The pinned Beta 11 baseline installed successfully in the separate upgrade
Sandbox. The actual Beta 12 installer log records the old Beta 11 version,
`route=UPGRADE engine_exit=0`, and Beta 12 post-install success. The original
interception wrapper missed its intended partial-pack trigger and recorded
`INVALID`/`FAIL`; no process was killed. Those records remain unchanged.
The normal upgrade's success is established separately by its raw log and
the installed post-upgrade checks, without inventing an installer exit-code
record that the wrapper did not capture.

The existing `PostUpgrade` check then passed all three channels for five
minutes, from `2026-10-10T05:35:32Z` through `05:39:38Z`. All five samples were
healthy with Whistle primary and no fallback; HLS contained H.264 video and
AAC audio. Final public/government/education caption cue counts were
48/49/46, with playlist ages 0.6/0.7/1.4 seconds. The original account,
station settings, media asset and three saved schedule rows were preserved.
Root independently checked the five snapshots and matching asset/schedule
markers. This is bounded upgrade functionality, not a sustained field soak.

Evidence is under
`outputs/beta12-candidate-f93beca0/consumer/upgrade-evidence/`:
`normal-upgrade-install-progress-final.txt` has SHA-256
`ab57fb11543fc228ccf183755dde1a2dd7c3e22408412accc02eadc82b1fb454`;
`Beta12/Beta12PostUpgrade/result.json`, `preserve-marker.json` and all five
`minute-N.json` files record the actual post-upgrade result.

A separate interrupted-install repair test is still in progress. Its first
observer refused to launch setup, but omitted the failed precondition values;
the original invalid receipt is retained. A subsequent read-only snapshot
showed all preconditions satisfied. The one authorized retry now records
those values before checking them. It confirmed an actual D4 interruption at
`2026-10-10T05:52:28.9593287Z`: the exact candidate setup PID, executable hash
and signature matched; process-tree termination returned 0, the process exited,
and the captured activation step had begun without returning. The raw delta
SHA-256 is `6b2b729f2b9fb675e8833a9e3110384633f264618b24ea5090911b2ed28edf79`.
The same installer then completed repair with exit code 0 at
`2026-10-10T06:00:27Z`. Its retained progress log records activation and service
registration returning 0 and post-install success; the activation receipt binds
to the signed candidate index above. However, the verification helper exited 1
after copying the installed station and activation records and before writing
`install-state.json`. The helper did not retain its exception or immediate
service/health response, so the exact failed assertion is unproven. A separate
read-only check at `2026-10-10T06:04:10Z` found the service Running, healthy
captions and current schema 0089. This later observation does not erase the
original failed check; post-repair preservation and three-channel operation
remain unverified.
An installer exit code of 0 does not establish successful station recovery.
The original helper failure and bounded logs are retained under
`consumer/upgrade-evidence/Beta12InterruptedRepair/`.

The separate post-repair check stopped at `2026-10-10T06:05:22Z` with HTTP 409,
before any five-minute observation. Its failure remains under
`consumer/upgrade-evidence/Beta12/Beta12InterruptedRepairPostUpgrade/`.
The exact conflicting request is still being diagnosed. A distinct state
recheck at `06:06:49Z` reported degraded health with captions unknown while
schema 0089 remained current. Subsequent per-channel snapshots showed fresh
Whistle-primary workers and zero backlog; a retained log also records a
government-channel overload/pause at `06:06:08Z` followed by processing.
Those observations are insufficient to explain the public health result or
establish uninterrupted post-repair caption delivery. Diagnosis remains open.

Subsequent diagnosis established that the HTTP 409 was the check attempting to
schedule new content over its earlier 40-minute test schedule. A corrected
schedule-start attempt then failed because literal shell-redirection arguments
were passed through the launcher into PowerShell path parameters. Parameter-only
reproduction confirmed that invocation error; neither failure establishes a
product scheduling or path defect.

The corrected invocation reached all three channels ON_AIR but failed its first
caption observation at `2026-10-10T06:22:46Z`. All three channels reported
`whisper-fallback`, zero caption cues and no completed inference. The retained
log records Whistle isolated-process deadlines at `06:12:55Z`, `06:13:06Z` and
`06:13:16Z`. A later snapshot at `06:25:23Z` still reported healthy public health
despite first-pending times more than three minutes old and no completion.
Source replay reproduced a readiness defect: a new retry's inference-start
timestamp incorrectly advanced the progress anchor without completed work.
The narrow progress-timer correction now uses completed progress and the first
still-pending input; a retry start is used only when neither timestamp exists.
Sensitive regressions first failed for both absent and old completion times;
106 affected runtime, router, health and evaluator tests then passed. An initial
run with inherited staff-token configuration produced 20 authorization failures;
that contaminated result is retained separately. The successful run removed
only that inherited variable from its test child process. Independent review
passed 47 runtime-status tests and replayed the retained payload: all three
channels now classify as stalled instead of healthy processing. Existing recent
progress, startup, silence and failure behavior remains covered. No Whistle
deadline or fallback policy changed. The corrected application requires a new
signed package; no corrected candidate or successful post-repair caption
observation is established here.

These failures remain under
`consumer/upgrade-evidence/Beta12/Beta12InterruptedRepairPostUpgradeAfterInvocationFix/`
and `repair-caption-failure-final-snapshot.json`. The owned Sandbox was stopped
at `2026-10-10T06:27:42Z` after evidence capture; the host service remained
Running. Its profile disabled vGPU. Saved fallback `device=cuda` messages are
runtime self-reports, not independently verified GPU acceleration or CPU-only
fallback qualification. No primary timeout-policy change is justified by this
record alone.

The matching manual sentence now states that retries do not reset the pending
progress clock. Assembly, PDF/Word rendering and in-app generation completed;
candidate, tracked-document and in-app currentness checks passed. The focused
manual/docsite tests passed 31 cases in 94.70 seconds. The new manual source
SHA-256 is `a28c55e1d102f0d9c765a3c2a03b1ea5b0c2ec4a71dc9c3ce835c4a3923fa110`;
PDF SHA-256 is
`539b7175bf761e414ac7f7c04d299dc8167ad44070438b8aa8e2a7da9ecb0787`;
Word SHA-256 is
`d7f753f6e50835a6586cee4c1e0c9baa34794e54eac98799c68d11a73c703dea`.
These replace the earlier manual in the source tree, not in the retained F93 kit.

## Hosted candidate-assets closure failure

Prepare-only producer `4254e83f601bffde03db5e4f0fe1d9c7688fc32c` / run
`38014658717` did **not** produce a candidate package. Candidate-assets job
`114107480321` failed during “Build and verify signed component packs.” Its
manifest, CLI-consumer, factory-sweep, plugin-origin, caption-round-trip and
GPL checks passed. `dynamic_trace` rejected one outside path:

`C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_1.26.510.0_x64__8wekyb3d8bbwe\microsoft.system.package.metadata\S-1-5-21-1643835476-1616584234-1346609752-500-MergedResources-0.pri`

The verifier combines Python audit opens, sampled native file handles and
mapped modules in its failure line, so the failed log does not identify which
trace mechanism observed that file. The correction now reports this narrowly
identified Windows package resource-index access only when it is an existing,
canonical access path directly beneath the exact DesktopAppInstaller package
metadata directory and is not also listed as a mapped module. Other
WindowsApps paths, mapped modules, executable files, package publishers,
nested paths and paths resolving outside the package remain closure failures.
At that checkpoint, focused verifier tests passed but the correction had not
yet run in a hosted producer; run `38014658717` failed and did not produce a candidate. The
later F93 producer and current `a0c98cb7` producer succeeded as asset builds
and are recorded under Candidate identity above. The separate normal-upgrade
evidence and incomplete recorder result are recorded in Current public release.
The retained raw job log is
`outputs/beta12-candidate-4254e83f/candidate-artifacts/failed-build-job-114107480321.log`.

## Remaining verification limits

- The normal Beta 11-to-Beta 12 setup exit and initial service/preservation
  observations are recorded above. The failed JSON save left the five-snapshot
  post-upgrade check incomplete; do not report a five-minute PASS. Earlier F93
  checks retain their historical identity and do not qualify this release.
- D4/interrupted repair and fresh-install checks have not been run. No
  same-candidate repair, fresh-install, or sustained-operation result is
  established.
- The direct-consumer receipt has not been accepted against the `a0c98cb7` release
  and its retained Windows PowerShell receipts. The verifier binds candidate
  activation hashes to the signed station index, pins the Beta 11 baseline
  index separately, and decodes BOM-bearing JSON only after checking the raw
  file hash. The separate normal-upgrade/D4-repair contract uses actual saved
  PostUpgrade results without requiring an unrecorded command log. Final
  publisher and renderer checks passed 123 tests; independent review reran
  those 123 tests and checked the actual normal-upgrade and confirmed D4
  fragments. The actual retained BOM files parsed with their raw hashes. End-to-end receipt
  acceptance remains incomplete; no synthetic consumer PASS is claimed.
- The c6 checkpoint's unit/native/claims, randomized, reproducibility, docs,
  accessibility, lint, security, operator and installer checks passed. Its
  mutation job was cancelled and is permanently informational; no mutation
  result is claimed and it is not a release prerequisite.
- Evaluate the received MSI i7 Beta 11 field report as Beta 11 evidence. The
  owner cancelled the Ryzen/R7 report; none will arrive, and it is not a release
  requirement. Use the existing local CPU-only Sandbox evidence with its stated
  limits. The MSI report includes a successful short NVIDIA Whisper decode and
  three-channel caption output after a supported CUDA override, but its formal
  soak had not started. Do not present those observations as a sustained field
  soak or as Beta 12 candidate proof.
- The Beta 12 public release is linked above. This record does not establish
  repair, fresh-install, long-duration field, or broader production-readiness
  results.

The running development station has not been stopped, upgraded or reconfigured
for these changes. The public release page does not claim a development-station
upgrade or field-operation result.

## October 10 integrated corrections

- Whistle startup no longer loads its Whisper backup eagerly. Backup loading
  happens on actual fallback; valid primary assets remain required. Sensitive
  regressions and independent review passed 119 affected caption tests. This
  does not establish that CPU Whisper medium fits every machine.
- Recording ticks revisit prearmed jobs when their planned start arrives.
  Review then reproduced a disable-before-start failure; disabling a schedule
  now skips prearmed jobs and stops their FFmpeg capture process. Already
  recording jobs are left running. The sensitive cancellation regression and
  all 68 recording-service tests passed, with focused lint and diff checks.
- File-backed sample preflight requires the complete unchanged sample-source
  tuple, including channel, type, endpoint and absence of credentials. Editing
  any of those fields requires an actual source probe. Sensitive regressions
  and 165 affected checks passed. The missing raw MSI UI artifact prevents
  confirming the actual field row's reserved ID; the local defect is proven.
- Caption performance follows the owner decision in master specification
  section 5: 99% and 98% are reporting benchmarks, accurate late captions
  count as successful, and Scott decides release acceptability even below
  98%. Consult him before treating caption performance as a blocker.

- The Python station reference verifier now uses the current six-pack profile;
  the older five-pack builder retains its explicit legacy policy. Beta 12
  publication accepts the truthful run-bound local canonical Ed25519 and
  six-pack size/hash verification record, rather than an unperformed compiled
  station-index check. Signature tampering, missing required packs and
  same-length pack corruption are rejected by the reference verifier. The
  publisher still checks exact kit hashes and consumes the signature
  attestation; it does not independently repeat the Ed25519 check. All 140
  affected checks and 23 independent targeted checks passed, with focused lint
  and format checks. Full new-candidate consumer acceptance remains pending.

These source checks are not installed combined-candidate proof. Manual chapters,
in-app Help and generated Markdown, PDF, Word and in-app manual downloads were
synchronized to these corrections. The tracked-download regression passed, both
renderers' currentness checks passed, and the release identity check confirmed
`v1.0.0-beta.12`. The earlier broader documentation run passed 37 cases and
exposed the stale tracked render; only that failed case was rerun after rendering.

## Release-tool caption-policy follow-up

The signed application build `38073908014` continues from source
`688b2dcdc79dd001ca6cf620ea2b0ad14929e87b`. A subsequent release-tool-only
correction removes the remaining Beta 12 automatic caption-performance
rejections, including fallback, degraded caption health, empty/unavailable VTT,
repeated hashes and reference-word counts. The publisher keeps bound raw
observations and prints an advisory in console output and release notes;
Scott retains the release decision. No new approval flag or numerical floor
was added. Legacy Beta 11 validation is unchanged.

The focused publisher/release-note suites passed 130 checks, with independent
review and focused lint/compile/diff checks. Root separately reran both the
physical-host and D4 caption-degraded health cases after the final correction:
2 passed. Malformed evidence, identity/schema mismatches and actual
installation, preservation and video/audio failures remain rejected.

The existing sandbox checker and installer wrapper were aligned to this
policy, with explicit file-availability observations and run-window timestamps.
Both passed PowerShell syntax checks; those checks are not guest execution.
The downloaded four-file exact-source candidate manual verified against
source `688b2dc`, version `1.0.0-beta.12` and producer `38073908014`.
The application payload, models and bundled manual source were not changed
by this release-tool correction; no duplicate application build is required.
