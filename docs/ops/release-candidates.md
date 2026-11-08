# Release Candidates: Build -> Consumer Evidence -> Publish

Runbook for cutting and publishing a native-Windows beta-candidate release.
Owner decision 2026-09-02: the coordinating agent cuts these releases going
forward, because the beta tester (Sergio, "LPM") checks
`scottconverse/civiccast`'s GitHub Releases page daily for new
versions. This document is the checklist that agent follows -- and the
checklist Scott follows to withdraw a bad one.

Nothing in this runbook authorizes an agent to merge to `main` or to publish
without the checks below all passing. `scripts/release/publish_beta_candidate.py`
is fail-closed at every step (see its module docstring) -- it refuses rather
than proceeds past any failed check.

Current published package: [Beta 20](../releases/v1.0.0-beta.20-verification.md). Its release assets, generated manual/Help, checksums and package results are bound to the signed source named in that record. Prior package receipts remain historical. Documentation-only corrections do not require rebuilding an existing signed installer.

## 1. Build

Dispatch or let `push` trigger `.github/workflows/native-beta-candidate-artifacts.yml`
on the release branch. It produces, per candidate commit (`<sha>`):

- `setup.exe` -- the Authenticode-signed native installer.
- `packs\*.ccpack` -- signed runtime component packs (app payload, server
  binaries, FFmpeg, Ollama runtime, optional CUDA runtime). Each is well
  under GitHub's 2 GB/file release-asset cap.
- `station\` -- the complete signed AI-model bundle. The publisher
  streams the six packs, station index and checksum file directly into a
  standard ZIP64 STORE archive split into parts below GitHub's per-file cap,
  without writing a full-size intermediate ZIP. `station-models.json` records
  each part's name, size and hash plus the archive hash and all eight unpacked
  file hashes. The parts and manifest are included in release checksums and
  assets.
- `manual\` in the assembled kit -- `USER-MANUAL.pdf`, `USER-MANUAL.docx`,
  the render manifest, and an exact-source/run receipt. The candidate's four
  manual files are included as release assets and in `SHA256SUMS.txt`.

Confirm the build run's conclusion is `success` and note its run id
(`--build-run-id` below).

### Hosted preparation mode (not an installable kit)

To prepare a candidate without assembling an installable kit, use
the manual workflow with `build_target: hosted` and `prepare_only: true`.
This keeps packaging and signing work off the station desktop. The workflow
still builds, signs, and verifies the complete candidate and station bundle
on `windows-latest`, then retains only these short-lived artifacts:

- `native-beta-candidate-<sha>`: candidate reports, signing receipt, and
  installer-pack checksums.
- `native-beta-candidate-binaries-<sha>`: signed installer and runtime packs
  (about 3.4 GB before the Whistle runtime addition). The setup embeds the
  signed station index, `core.ccpack`, and `captions-whistle.ccpack` under
  `station\` for setup-only upgrade activation.
- `native-station-embed-<sha>`: the same signed station index and the embedded
  `core.ccpack` plus `captions-whistle.ccpack` used to build those installer
  resources.
- `native-station-prepare-<sha>`: signed `captions-whistle.ccpack`, the exact
  station index, the station bundle `SHA256SUMS.txt`, and its build report.
- `native-beta-manual-<sha>`: the exact-SHA PDF, DOCX, render manifest, and
  receipt bound to the candidate version and workflow run. It is rendered
  with the same Pandoc/TeX and currentness checks as `ci-docs.yml`, has
  one-day retention, and is copied into a full kit by the normal assembly job.
  `prepare_only` produces this small manual artifact too, but still skips kit
  assembly; it does not make a preparation run Gate A eligible.

The Beta 11 preparation measured a Whistle pack payload of 18,422,127 bytes
(about 17.6 MiB); use the current build report for current sizes. It is included
in the installer and repeated in both small station artifacts, so budget for
about 3.5 GB total, plus small indexes and reports; the run will confirm actual
upload sizes. Each artifact has one-day retention, keeping this below the
shared 10 GB Actions artifact budget. The run does not upload the full model
bundle or assemble `native-beta-kit-<sha>`. These artifacts are not a complete
first-install kit and cannot be used for Gate A or sent to a station. An
existing setup-only upgrade can use the embedded Whistle pack and retain its
local cached large models; the signed station index has no download URLs.
The automatic `workflow_run` preflight sees the marker immediately; do not
manually dispatch Gate A against this run after the marker expires, because a
missing expired artifact cannot distinguish preparation-only from a full
self-hosted build. If the candidate binaries expire before later kit assembly,
rerun the candidate workflow at the same source SHA and use the fresh run id.

The station build report and checksum list record the full signed bundle's
identity, but the large model packs are not retained by this run. A later
full-kit build must reconstruct those packs from the pinned, verified local
model cache for the same source SHA, compare the rebuilt station index and
pack hashes with the preparation artifacts, assemble the kit, and run Gate A.
If those identities differ, the preparation run does not establish the full
kit's identity.

Gate A automatically receives a `workflow_run` event when this preparation
workflow completes. After the preflight change is on the default branch, its
hosted preflight sees `native-station-prepare-<sha>` on that exact run and
skips all Windows Sandbox lanes. A pre-merge run still uses the default-
branch Gate A definition, so that guard is not active yet; keep the local
`blackwell-builder` runner offline and cancel any auto-queued Gate A run for
the preparation candidate. Gate A has no separate registered Windows runner
today. Do not bring the desktop runner online or start Windows Sandbox while
the owner continues the station and monitor. Clean-machine Gate A remains
outstanding until that work has ended and the owner schedules it.

## 2. Gate A: three required lanes (workflow-backed route)

`.github/workflows/gate-a-station-acceptance.yml` runs automatically after a
successful build (`workflow_run`), or can be dispatched manually against a
specific build run id. It produces three jobs, each with its own
`gate-a-verdict.json`:

| job | lane name in the verdict JSON | artifact name |
| --- | --- | --- |
| `station-acceptance` | (no `lane` field -- implicitly `clean`) | `gate-a-verdict-<run_id>` |
| `station-acceptance-dirty` | `dirty` | `gate-a-dirty-verdict-<run_id>` |
| `station-acceptance-download-only` | `download-only` | `gate-a-download-only-verdict-<run_id>` |

All three are **required** when using the workflow-backed route (owner decision
2026-09-02 made the download-only lane required alongside clean and dirty).
Each verdict JSON must report `"verdict": "PASS"` and the same producer
`source_sha` as the candidate artifacts. Note the Gate A run id
(`--gate-a-run-id` below) -- this is the
`gate-a-station-acceptance` workflow run id that produced all three jobs,
not any one job's own id.

If any lane is missing, not `PASS`, or reports a different `source_sha`, do
not publish. Re-run Gate A (or the specific failing lane via
`workflow_dispatch` with `lane: cross-version-only` /
`lane: download-only-only`) and fix the underlying defect first.

### Direct Sandbox consumer evidence route

The publisher also accepts direct consumer receipts instead of the Gate A
workflow. Select exactly one route: pass `--gate-a-run-id` for the
workflow-backed route, or `--consumer-evidence-receipt <file>` for direct
evidence. Direct mode also requires an explicit `--artifact-source-sha`; it
never downloads Gate A artifacts or manufactures lane verdicts.

The Beta 12 direct Sandbox receipt uses the version-1 JSON kind
`civiccast-native-beta-direct-consumer-evidence`, binds the exact
`source_sha`, `build_run_id`, and assembly receipt, and declares
`consumer_contract: "beta11-to-beta12-interrupted-upgrade-v1"`. Its `evidence`
object contains exactly these groups:

- `fresh_install`: the candidate installer run, final install state, and
  activation self-test.
- `beta11_baseline_install`: the pinned Beta 11 installer run, healthy state,
  activation self-test, and a `PrepareBaseline` marker containing the existing
  asset ID and three schedule IDs.
- `beta11_to_beta12_interrupted_upgrade`: the exact candidate installer run,
  state and activation self-test after an observed nonempty, incomplete
  `.ccpack.partial` during `stage-packs`; the interrupted file and recovery
  result must bind the candidate source, workflow run, and installer hash.
  The same installer must then repair the interrupted install, remove partial
  packs, and pass post-upgrade verification.
- `verify_after_upgrade`: the `PostUpgrade` marker and command log proving that
  the Beta 11 asset and all three baseline schedules were preserved.
- `three_channel_runtime`: the five-minute result, preservation marker, and
  all five snapshots for public, government, and education.

The separate contract
`consumer_contract: "beta11-to-beta12-upgrade-and-d4-repair-v1"` proves a
normal cross-version upgrade followed by an independently interrupted repair.
It retains `fresh_install` and `beta11_baseline_install`, and uses:

- `beta11_to_beta12_upgrade`: the bound launch receipt and actual installer
  progress log showing the Beta 11 upgrade route and Beta 12 completion. It
  does not manufacture an installer exit-code receipt that was not captured.
- `verify_after_upgrade`: the actual `PostUpgrade` result and preservation
  marker. Its result reference must be the same one used by
  `three_channel_runtime`, which binds all five three-channel snapshots.
- `beta12_interrupted_install_repair`: an observed, incomplete D4 activation
  interrupted by terminating only the verified candidate setup process tree;
  the same candidate's subsequent installer, state and activation receipts.
  The raw interruption record retains its guest paths. A separate hash-bound
  `progress_delta` reference locates the mapped evidence file on the publisher
  machine without rewriting that record or relaxing path containment.
- `verify_after_repair` and `repair_three_channel_runtime`: the same baseline
  data and a second five-minute, three-channel observation after repair.

The interruption record binds source, producer run, installer hash/signature,
healthy installed-before state, the exact live executable/PID, successful
process-tree termination, and a captured D4 begin with no return. Repair output
must follow the repair installation; earlier upgrade snapshots cannot serve
as post-repair evidence. Neither contract turns an invalid interception
attempt into a successful recovery test.

Beta 12 direct Sandbox evidence requires the three-channel runtime scope; a
one-channel install-smoke or the older Beta 10/failed-fresh-repair receipt
cannot stand in for the cross-version upgrade and interrupted-install repair
evidence required by either Beta 12 contract above.
Legacy Sandbox receipt validation remains available for Beta 11 receipts:
it uses the older `failed_install_repair`, `beta10_baseline_install`, and
`beta10_to_beta11_upgrade` groups, and supports the historical one-channel
install-smoke scope. That legacy shape is not accepted for Beta 12.

An explicit `consumer_mode: "physical-host"` is a separate, bounded in-place
update route. It binds the exact signed setup to a running pre-install host,
successful candidate install, verified installed app manifest, and retained
service-loop/schema state. Its runtime group contains at least two
time-ordered snapshots covering public, government, and education, spanning
at least 30 seconds, with HLS no older than 30 seconds, advancing
playlists/segments and H.264/AAC. Beta 11 also requires changing nonempty
caption output; Beta 12 records caption observations under the owner policy
below, including caption-only degraded health. It proves
those sampled outputs on that host; it does not claim a clean Sandbox install,
failed-install repair, cross-version Sandbox upgrade, or simultaneous
capacity.

For the Beta 11 legacy one-channel scope, the top-level
`runtime_proof_scope` is `"one-channel-install-smoke"` and the proof group is
`one_channel_install_smoke` for exactly `["public"]`. Unknown scopes, mixed
runtime groups, and missing or different channels are rejected; missing
channels never silently reduce the requested scope. Account, asset, and three
saved-schedule preservation checks remain required for that legacy receipt.

The runtime snapshots require a time-ordered sample span of at least three
minutes, HLS playlist age no greater than 30 seconds, and advancing playlist
timestamps and newest segment names for each channel. They also require
audio/video HLS. Legacy Beta 11 requires at least three distinct VTT snapshots
and at least two accumulated JFK reference words per channel across the run;
Beta 12 reports those observations without a caption-performance gate. The bounded VTT
text sample remains in the hash-bound evidence for human review; the publisher
does not compare it to a transcript. Historical Beta 11 one-channel evidence
shows installed caption functionality only, not three-channel capacity, and
cannot satisfy the Beta 12 direct Sandbox route.

**Beta 12 caption reporting:** 99% and 98% are the owner's reporting
benchmarks, not release minimums. Accurate late captions count as successful.
Fallback, degraded caption readiness, missing or empty captions, repeated
VTT hashes and reference-word observations are recorded in the bound snapshots
and summarized separately in console output and release notes. Scott decides
release acceptability, including below 98%; consult him before treating any
caption-performance observation as a blocker. No extra approval flag or
override receipt is required.

Beta 12 observations explicitly record `vtt_available` and
`caption_runtime_available`. An unavailable file has null file/status data
and a nonempty unavailable reason, rather than an invented digest or healthy
state. VTT samples record the bounded text and its exact UTF-8 byte count;
reference-word fields distinguish observed text from unavailable text.
Missing, malformed, contradictory or incorrectly hash-bound evidence remains
invalid. Installation/signature/asset/activation checks, current database
schema, version identity, service operation, preserved station data and actual
video/audio output retain their existing checks. Caption-only degraded health
does not make those installation checks fail.

Every referenced file is a `{ "path": ..., "sha256": ... }` object. The
publisher verifies each file hash and the semantics above, then checks all 19
kit files against the build's `kit-assembly-receipt.json` hashes and sizes.
The receipt binds `artifact.source_sha` and the assembly workflow run to
`--artifact-source-sha` and `--build-run-id`. The tested installer is named
`CivicCast (Native)_1.0.0-beta.N_x64-setup.exe`; direct mode stages a
byte-identical `setup.exe` release asset without changing the assembled kit.

`--source-sha` always names the commit targeted by the release tag.
`--artifact-source-sha` names the commit that produced the signed package and
consumer evidence; the values may differ, for example when publishing the
already-built package from a later documentation-only commit. Release notes
show both identities. Direct evidence does not establish Gate A's
download-only network route; the notes state that the route was not tested.
File hashes bind the receipt to its evidence files but do not turn the local
receipt into a signed attestation.

## 3. Publish: `publish_beta_candidate.py`

```powershell
uv run python scripts/release/publish_beta_candidate.py `
  --kit-dir C:\CivicCastTester\kit-staging\<sha> `
  --source-sha <sha> `
  --build-run-id <native-beta-candidate-artifacts run id> `
  --gate-a-run-id <gate-a-station-acceptance run id> `
  --tag v1.0.0-beta.N `
  --truth-status staging `
  --dry-run
```

Always run with `--dry-run` first and read what it prints and what it wrote
to `artifacts\release\<tag>\` (`RELEASE-NOTES.md`, the sidecar JSON,
`SHA256SUMS.txt`) before dropping `--dry-run`. The dry run touches no GitHub
or git-remote state at all.

For a direct-evidence run, use the same command but replace
`--gate-a-run-id` with the exact receipt and producer source SHA:

```powershell
uv run python scripts/release/publish_beta_candidate.py `
  --kit-dir C:\CivicCastTester\kit-staging\<artifact-source-sha> `
  --source-sha <tag-target-sha> `
  --artifact-source-sha <artifact-source-sha> `
  --build-run-id <native-beta-candidate-artifacts run id> `
  --consumer-evidence-receipt C:\CivicCastTester\evidence\direct-consumer-evidence.json `
  --tag v1.0.0-beta.N `
  --truth-status staging `
  --dry-run
```

What it checks, in order, refusing (exit nonzero, no further action) on the
first failure:

1. **Layout** -- workflow mode requires `setup.exe`, `packs\*.ccpack`
   (>=1), and `station\`; direct mode resolves the signed installer path
   from the hash-bound assembly receipt. Both modes require the Beta 11
   `manual\` PDF, DOCX, render manifest, and candidate-manual receipt.
2. **Version identity** -- the signed installer's `VersionInfo.ProductVersion`
   (via PowerShell), `civiccast._native_version.__version__` (source tree),
   and `--tag` with its leading `v` stripped must all agree.
3. **Candidate manual** -- Beta 11 and later must carry the four files from
   `native-beta-manual-<sha>`. The publisher checks the receipt's source SHA,
   candidate version, and artifact producer build-run id, then verifies the
   current Markdown source, render-manifest hash, and PDF/DOCX hashes and
   sizes before checking the selected consumer-evidence route.
4. **Authenticode signature** -- `Get-AuthenticodeSignature` on `setup.exe`
   must report `Status: Valid` (see `CODE_SIGNING_POLICY.md`).
   The native candidate builder now uses `--sign-installed-executables`: it
   signs the launcher after the Tauri bundle-type patch, then signs NSIS's final
   generated uninstaller through `!uninstfinalize` before embedding it. Both use
   the existing ArtifactSigning 0.1.8 module and Azure profile; the outer setup
   is signed afterward by the existing action. Callback failure aborts packaging.
   Credentials are inherited environment values, never generated NSIS arguments.
   On the self-hosted lane, `reuse_station_dir` may point to an existing complete
   same-version signed `station/` directory. This skips model/floor/Whistle
   acquisition and repacking. The existing index and pack validators check the
   trusted key, channel, product/stable-model identities, payload hashes and
   pinned contracts before same-volume hardlink staging; mismatches fail closed.
   The default provisioning path remains unchanged. Keep the original kit intact
   outside the candidate prune roots. No large artifact upload is needed when
   both self-hosted upload flags are false.
   Signing the outer setup alone does not prove its installed contents. On the
   final authorized candidate, verify the installed launcher and uninstaller
   each retain a Valid, timestamped Scott Converse signature, then complete the
   applicable isolated install/uninstall acceptance. Source checks do not supply
   that installed-file evidence.
5. **Consumer evidence** -- workflow mode downloads all three Gate A lane
   verdicts and requires them to PASS for `--artifact-source-sha` (which
   defaults to `--source-sha`). Direct mode verifies every bound Sandbox
   receipt and all 19 assembled-kit member hashes/sizes. Direct mode does not
   claim Gate A lanes or the network download-only route.
6. **Hashing + manifest** -- SHA-256 of `setup.exe`, every
   `packs\*.ccpack`, and all four manual files, written to `SHA256SUMS.txt` and a
   `<setup.exe>.sidecar.json` shaped to match
   `scripts/policy/check_sidecar_attestation_integrity.py`'s contract
   (`sha256`, `attestation: null`, `install_manifest.signed`) and what
   `scripts/download_windows_release_artifacts.ps1` already reads.
7. **Release notes** -- rendered via
   `scripts/render_release_notes.render_native_beta_candidate_notes`. They
   identify the artifact producer and tag target, link the build run, and
   show either the three Gate A PASS lanes or the direct Sandbox scenarios
   actually checked, plus the `[Unreleased]` CHANGELOG section, an asset table
   with size + SHA-256, plain-English install/upgrade instructions, the
   SmartScreen note, and the beta-candidate boundary statement.
8. **Pre-flight the asset set** -- every asset (setup.exe, each pack, each
   manual file,
   SHA256SUMS.txt, the sidecar) must be under GitHub's documented 2 GiB
   per-file release-asset cap. The complete set is printed with sizes.
   Anything at or over the cap refuses BEFORE any remote mutation. (Also a
   pre-flight, first of all: `gh auth status` must succeed.)
9. **Publish or dry-run** -- without `--dry-run`, in an order that can never
   leave an orphan tag (no `git tag`/`git push` is ever run by hand):
   1. `gh release create <tag> --draft --target <source-sha> --prerelease
      --title ... --notes-file ... <every asset>` -- a **draft** creates no
      tag. If this fails, the (possibly partial) draft is deleted
      best-effort and the run refuses.
   2. `gh release view <tag> --json assets,isDraft` -- every expected asset
      must be present with a size matching the local file, and the release
      must still be a draft. On ANY mismatch the draft is deleted
      (`gh release delete <tag> --yes`) and the run refuses: nothing is left
      behind, because a draft has no tag.
   3. `gh release edit <tag> --draft=false` -- the single step that creates
      the public tag, atomically with its verified release. If this fails
      the draft is deliberately NOT deleted (un-draft may have partially
      applied server-side); the run refuses loudly and you inspect the
      release on GitHub and publish or delete it by hand.
   4. Only then: update `docs/releases/release-truth.yaml` (adding the new
      entry at the given `--truth-status`, flipping the previous `current`
      entry to `superseded` when the new one becomes `current`) and print a
      summary of the edit.

   GitHub's un-draft creates a lightweight tag at `--source-sha`. No policy
   check in this repository requires an annotated tag, so none is made.

### Failure and rollback map

| failed step | what exists afterwards | what the script does | what you do |
| --- | --- | --- | --- |
| gh auth / layout / version / signature / Gate A / pre-flight | nothing remote | refuses | fix the cause, re-run |
| `gh release create --draft` | maybe a partial draft, no tag | best-effort `gh release delete`, refuses | confirm no draft remains on GitHub, re-run |
| draft verification (missing asset / size mismatch / not a draft) | draft, no tag | `gh release delete`, refuses | confirm no draft remains, investigate the asset, re-run |
| `gh release edit --draft=false` | verified draft, tag state uncertain | refuses, does NOT delete | first run `gh release view <tag> -R scottconverse/civiccast --json isDraft,url` to learn whether the release actually went public (a failed un-draft may have partially applied), then publish it by hand if it is intact and still a draft, or delete it |
| `release-truth.yaml` update | public prerelease + tag, manifest not updated | refuses | edit `release-truth.yaml` by hand to match what is live |

`--truth-status staging` for a first publish under review; flip a later
publish to `--truth-status current` once you're ready for it to be the
recommended install target.

## 4. What Sergio (LPM) sees on GitHub

The exact tag appears on the `scottconverse/civiccast` Releases page
as a **prerelease**, not a full Latest release. Its assets include:

- `setup.exe`, all five runtime packs and the four candidate manual files.
- `SHA256SUMS.txt`, `setup.exe.sidecar.json`, and the
  `download_windows_release_artifacts.ps1` helper.
- `station-models.json` and each multipart station archive asset. Every part
  is below the 2 GiB per-file limit; the manifest binds the ordered parts,
  complete archive hash, signed station-index hash and hashes of the eight
  files extracted into `station\`.

The helper's `-Install` option downloads and verifies the full kit and runs
the signed installer with `/S`, elevating when required. It also supports
`-IncludePacks` to download and verify the full kit without installing.
Release notes should state the exact source SHA, build and consumer-evidence
route, asset sizes and hashes, and the SmartScreen verification instructions.
Do not describe availability of the complete package as first-install,
upgrade-preservation, repair or sustained-operation acceptance. The October 11
R9 failure applies to the earlier incomplete public asset set; no completed
fresh-install result for the repaired Beta 12 asset set is recorded here.

## 5. Withdrawing a bad candidate

If a published beta candidate turns out to be broken:

1. Unpublish it on GitHub:
   ```powershell
   gh release edit <tag> --draft -R scottconverse/civiccast
   ```
   This pulls it off the public Releases page immediately (a draft release
   is not visible to Sergio or anyone else without direct owner access).
2. Mark it in `docs/releases/release-truth.yaml`: change its `status` to
   `withdrawn` and add `superseded_by: <the tag that replaces it>` (required
   by `scripts/policy/check_release_truth.py`'s `REQUIRES_SUCCESSOR` rule --
   a `withdrawn` entry with no `superseded_by` fails that check). If nothing
   replaces it yet, do not mark it `withdrawn` until a replacement tag
   exists to name; in the meantime describe the problem in its `notes` field
   and leave `status` as `staging` (never silently leave a known-bad
   `current`).
3. If the withdrawn tag was `current`, promote the last known-good tag back
   to `current` in the same edit (exactly one entry may carry
   `status: current`).
4. Tell Sergio directly (do not rely on him re-checking GitHub on his own
   schedule) that the tag is withdrawn and what to do instead -- reinstall
   the previous good candidate, or wait for the replacement.
5. Never delete the git tag itself (`git tag -d` / `git push origin
   :refs/tags/<tag>`) as part of a withdrawal -- the tag stays as history;
   only its GitHub Release visibility and its `release-truth.yaml` status
   change.

## Related

- `scripts/release/publish_beta_candidate.py` -- the publisher this runbook
  describes.
- `tests/release/test_publish_beta_candidate.py` -- its test suite.
- `docs/releases/release-truth.yaml` -- the sole authored source for release
  state; `scripts/policy/check_release_truth.py` checks docs against it.
- `docs/ops/gate-a.md` -- the full Gate A verdict-criteria table, including
  the "Download-only lane" and "Promotion rule" sections.
- `CODE_SIGNING_POLICY.md` -- what a `Valid` Authenticode signature means
  here and how to verify one yourself.
- `docs/install/windows-release-trust.md` -- the operator-facing trust and
  verification page this runbook's published assets must stay compatible
  with.
