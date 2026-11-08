# Signed public installer preparation - October 27, 2026

**Result: READY FOR OWNER TAG/PUBLISH APPROVAL.** A signed public-ready
installer was built from the merged black/silent outage fix and passed the
required isolated lifecycle/signature acceptance. Nothing was tagged,
published, released, transferred through LFS, or installed over the running
host station.

## Candidate identity

- Source: `8fdce69e4fccfba80f2984a0d034d8851b4840e0` (normal PR #273 merge;
  final PR head `1412c0060db6863f983dc381f1e51597a0930296`).
- Version: `1.0.0-beta.20` (the established source version; no version-source
  change was made for this preparation).
- Build: `native-beta-candidate-artifacts` run
  [`40616252842`](https://github.com/scottconverse/civiccast/actions/runs/40616252842),
  conclusion success. Exact-source manual, signed station resources, signed
  app/server/runtime packs, signed installer, and local kit all completed.
- Reused required source CI: run `40583199781` passed Unit, Windows WS4 and
  claims. Final PR check count was 22 passed / 3 expected skips. The optional
  randomized failure was not a gate and was not requalified.
- The earlier dispatch `40615891774` failed before any product build because
  the workflow's prune removed its local reuse path. The corrected path used
  the preserved signed station source; no product retry or extra rebuild.

## Artifact

`C:\CivicCastTester\candidates\8fdce69e4fccfba80f2984a0d034d8851b4840e0\candidate\CivicCast (Native)_1.0.0-beta.20_x64-setup.exe`

- SHA-256: `2c6d261445567c168ad9f7fa7bdbd085b5dc8f8d5bcda59e41cde86ac6388e4b`
- Authenticode: **Valid**; signer `CN=Scott Converse, O=Scott Converse, L=Longmont, S=co, C=US`;
  thumbprint `A987FBF8678961387F2CCB44A1DEDDB79E3DA879`; Microsoft timestamp.
- Installable kit:
  `C:\CivicCastTester\kit-staging\8fdce69e4fccfba80f2984a0d034d8851b4840e0`
- Candidate receipt and `SHA256SUMS.txt` bind source `8fdce69e...` and pack
  signing key `civiccast-production-2026-beta1`.
- The embedded signed station index SHA-256 is
  `751e18ab976abe7d1a81e301ee0edfe683a5ec50d35e8f1ca4edb45eff1f6272`; the
  installed fixed module SHA-256 is
  `959540cd0c5ea8db85427666d1d6f2b898d5fa5448013ef655aaf0007fd363f6`,
  byte-identical to `civiccast/native/gstreamer_runtime.py` at the candidate
  source. This is the merged per-directory DLL-registration fix.

## Required isolated acceptance

The proven F5 route was used once: Windows Sandbox with **12,288 MB**, networking
and vGPU disabled, the kit read-only, and installation on the guest's native
filesystem (not a host-mapped install directory). Sandbox API ID
`85a5964d-332c-473a-be6e-e506893164d7`; started `2026-10-28T03:04:02Z`,
finished `2026-10-28T03:30:25Z`. The sandbox is no longer running.

- Setup hash/signature verified in the guest; installer exit **0**.
- Installed launcher `CivicCast Native.exe`:
  SHA-256 `611cd261013157052f90bd347b51ec4a0024a8923e2122730153739550a5f744`,
  Authenticode **Valid**, Scott Converse, Microsoft timestamp.
- Installed `uninstall.exe`:
  SHA-256 `049878696004b2bdef7dfed2f1e336b83766c9cca964217c7e3549b9e57acc4a`,
  Authenticode **Valid**, Scott Converse, Microsoft timestamp.
- Service `CivicCastSupervisor` ran and `/health` returned healthy Beta20,
  schema `0097_agenda_minutes`, current.
- Offline activation self-test passed for Gemma 4 12B, Gemma 4 E4B,
  TranslateGemma 4B, and the faster-whisper CPU caption self-test.
- Real uninstaller exit **0**. Upload sentinel and PostgreSQL `PG_VERSION`
  hashes were preserved; runtime, packs, launcher and service were removed.
- This is isolated lifecycle/signature/preservation acceptance, not a broadcast
  quality, long-soak, or host-station proof.

## Informational Gate A note

`gate-a-station-acceptance` run `40620078397` is **informational only** under
`docs/ops/gate-a.md`'s Promotion rule and the workflow's own status comment; it
does not block merges or releases. Its automatic mapped-install lane failed at
the known host-mapped service-identity ACL migration (`d4-service-registration=70`,
setup exit `118`) and restored the prior state. The exact orphan was cleared
after identity verification. The dirty/download/soak follow-on lanes were
skipped and are not requirements for this preparation. The successful required
acceptance above used the native guest filesystem instead.

## Boundary

No host station install/upgrade/restart/probe, no reboot, no tag, no publication,
no LFS transfer, no paid service, and no new source change. Owner approval is
still required before any tag, release or publication.

Machine-readable evidence:
[signed-installer-prep-2026-10-27.json](evidence/signed-installer-prep-2026-10-27.json).
