# CivicCast project status

> **Taking over coding? Start with [HANDOFF.md](HANDOFF.md)** for the
> current continuation state. The [October 2 coder handoff](docs/handoff/CODER-HANDOFF-2026-10-02.md)
> remains a dated layout and history reference, not current status.
>
> Current operator Help serves the bundled manual generated from
> [`docs/manual/src/`](docs/manual/src/) and [`docs/USER-MANUAL.md`](docs/USER-MANUAL.md).
> The 62 per-screen sheets indexed in [`docs/in-app-help/README.md`](docs/in-app-help/README.md)
> are historical Beta 10 audit records; the linked Beta 11 inventory is a dated
> source audit, not the current defect list.

## Current release: RC9

v1.0.0-rc.9 is the current published native Windows package, published
2026-11-28. It stops the cable output crashing on a future-dated closing frame,
prefers the Media Foundation H.264 encoder, lets the stall watchdog see a cable
feed with audio but no video, adds the station logo control, and carries the
rc.8 fixes forward. The minimum machine is 32 GB of RAM.
Its signed package and scoped checks are in the [RC9 record](docs/releases/v1.0.0-rc.9-verification.md); the [RC9 release notes](docs/releases/v1.0.0-rc.9-release-notes.md) list known issues. RC8, RC7, RC6, RC5, Beta 20 and Beta 19 remain historical.

## Historical Beta 17 release

Beta 17 was a published package, with its scoped installation results
in the [Beta 17 record](docs/releases/v1.0.0-beta.17-verification.md). It adds
subscriber delivery, Podcast publication, and underwriting daypart selection.
Protected paywall media stayed outside Sprint 5; the later Beta 18 and 19
releases completed its remaining workflows.

The R9 field findings are assigned to [Beta 17](docs/ops/beta17-deferred.md).

## Historical Beta 11 package and evidence

The historical Beta 11 installer and bundled manual were built from
`400cff08f72a4555f9de57e790a298ecefdb0cce` in signed build `37857705750`.
The original published tag remains at
`79e6431dfea248b11b079cff69408192ef22816a`; the release page discloses the
replacement package source. There are 12 public release assets. Use the
complete USB/LAN kit, including the runtime packs and signed `station\`
model bundle, for an offline installation.

That exact package passed a fresh, offline, CPU-only Windows Sandbox install,
installed Help verification, and a five-minute single-channel Whistle output
check. Public installer/manual downloads and release asset digests were
verified. Upgrade from Beta 10, repair, GPU operation, and sustained
three-channel capacity remain unverified for that package. See
[Beta 11 verification](docs/releases/v1.0.0-beta.11-verification.md).

The accepted 24-hour and 36-hour three-channel caption milestones came from
the development overlay. They are useful runtime evidence for that overlay,
not installation or capacity proof for the corrected release package. See
[soak results and limits](docs/ops/beta11-dev7-24-hour-caption-soak-2026-10-07.md).

A 72-hour observation of the Beta 11 host recorded 1,695 discarded
caption-input seconds out of 777,600 nominal channel-seconds across Public,
Government, and Education: 99.782021605% (**99.78% rounded**) live
caption-input coverage. Per-channel discarded totals were Public 575,
Government 555, and Education 565 seconds. See the [72-hour host report](docs/ops/beta11-host-caption-performance-72-hour-2026-10-11.md)
for measurement and method.

## Beta 12 release and upgrade observation

The owner authorized unattended-operation hardening and completion of existing
operator workflows: upgrade preservation and recovery; caption health and
notifications; bounded logs; summary approval/export; local administrator
recovery; Control Room session recovery; remote contribution media controls;
alert routing and emergency presentation; paywall secret preservation;
target-machine operation; and documentation/release identity consistency.
The Beta 12 published release was source
`a0c98cb7367dc106042ec89d35a91905470d350c` from producer run `38097350705`.
Its asset job and 19-member local kit completed with exact-run checksums,
signature and station-index evidence. The release's separate randomized-suite
run `38097353468` failed; that recorded CI failure is distinct from the actual
installer outcome. The normal Beta 11-to-Beta 12 setup ran from 20:07:13 to
20:25:15 MDT, exited 0, and left the service running and healthy at schema
0089. The preservation assertion reached and saved its marker. Initial checks
observed HLS audio and video on all three channels and primary Whistle within
capacity. At 20:31:16 MDT, the post-upgrade recorder wrapper failed during its
first-minute JSON save with `Cannot convert value to type System.String.` No
five-snapshot PASS was recorded. The owner deferred that wrapper issue as
bookkeeping; interrupted repair and sustained-operation checks have no result.
The pre-repair R9 attempt exited 123. The repaired public kit was subsequently
downloaded and verified in a fresh offline Sandbox run: setup exited 0, the
service was `Running`, station activation returned 0, and offline Ollama plus
CPU faster-whisper self-tests passed. The bounded first-admin/three-channel
check then failed; only `Traceback (most recent call last):` was captured from
Python stderr, so the cause is unknown and complete first-admin/channel
validation is not established. No repair, upgrade retest, or soak was run. See
the [captured result](docs/ops/evidence/beta12-public-fresh-install-2026-10-11.json).
Actual
synthetic VDO peers previously exercised camera mute, audio gain mute and
disconnect; this does not establish guest routing into the
native broadcast compositor. The [verification record](docs/releases/v1.0.0-beta.12-verification.md)
links the published release, manual assets and exact evidence limits.

## Where the history went

The earlier dated status entries and handoff notes (beta.5 through beta.8
preparation, kit-server commands, local paths, the old task list and standing
rules from that period) are kept for the record, and are not current guidance:

- [`docs/history/PROJECT-STATUS-2026-09.md`](docs/history/PROJECT-STATUS-2026-09.md)
- [`docs/history/HANDOFF-2026-09.md`](docs/history/HANDOFF-2026-09.md)

Release state of record: [`docs/releases/release-truth.yaml`](docs/releases/release-truth.yaml).
Deferred work: [`next-cleanup.md`](next-cleanup.md).
