# Plan: CivicCast native bootstrap under 300 MB

**Decision state:** Owner-approved D2 architecture amendment (2026-07-29).

**Size gate:** the final NSIS bootstrap executable must be strictly smaller
than **300,000,000 bytes**. The gate fails at 300,000,000 bytes or above.

## Why the complete native payload is much larger

The inherited 61.8 MB native executable did not contain the completed native
station. Local sizing measurements from the inherited staging tree show
approximately:

- 402,000,097 bytes of PostgreSQL, NATS, TSDuck, and FFmpeg dependencies;
- 3,087,284,237 bytes for the pinned `faster-whisper-large-v3` model alone;
- approximately 3.57 GB for the staged application tree including that model.

The measured staging manifest records source `35a4ab4e39a19e632aa6a25c3492cae6c4489283`
with a dirty build state. These numbers establish the order of magnitude and
physical incompatibility with a 300 MB all-in-one executable; they are not
candidate-bound reproducibility evidence. The final plan gate must remeasure
the clean candidate artifacts.

The legacy WSL installer can remain comparatively small because Windows-side
setup can rely on a separately provisioned Linux environment and fetched
packages. A complete offline native station carries Windows runtimes, servers,
media tools, application dependencies, and legally required offline caption
weights. Those bytes cannot fit inside a sub-300 MB executable without removing
required capability.

The solution is a small installer executable plus separately verifiable
required component packs, not silent feature or model removal.

## Artifact layout

> **Amended 2026-08-06 per owner decision D-E (two-pack public contract);
> supersedes the five-pack distribution text.** The public beta release carries
> exactly TWO component packs; AI model components are acquired and
> cryptographically verified during the installer's acquisition walkthrough
> (AcquisitionFlow), not shipped as public release packs. The <300 MB bootstrap
> gate is unchanged and binding.

> **Amended 2026-08-07 per owner decision (Scott Converse): `large-v3` is not
> mandatory for a default station; it is an optional quality add-on. The
> caption floor tier is the mandatory requirement for a default station.**
> Reason given by the owner: the team tested this across multiple machines
> and found the floor tier sufficient for the legally required captions, and
> the legal caption requirement is not realtime — captions may be generated
> offline, after hours, once the recording is saved. This decision was
> originally made weeks before 2026-08-07, was lost in a Codex crash, and is
> re-recorded here so it is not rediscovered as a "contradiction" and
> silently reverted a third time. Captions themselves remain a legal
> non-negotiable and are unaffected: every default station still ships,
> installs, and proves a working, mandatory caption floor tier. Only the
> *model tier* requirement changes; the Required acceptance evidence section
> below reflects this.

The public release asset set is exactly:

1. `CivicCast (Native)_<version>_x64-setup.exe`
   - signed NSIS/Tauri bootstrap;
   - embedded silent WebView2 offline installer so setup never requires a
     network connection;
   - embedded, hash-pinned Microsoft Visual C++ x64 runtime prerequisite for
     the packaged PostgreSQL and TSDuck binaries;
   - minimal installer UI, pack acquisition/import, verifier, lifecycle
     coordinator, diagnostics, and trust root;
   - no multi-gigabyte application or model payload;
   - hard release gate: byte length `< 300000000`.
2. `native-app-payload.ccpack`
   - application, CPython, frontends, and the embedded GStreamer runtime
     closure (no separate GStreamer pack), manifests, BOMs, and licenses;
   - signed; `metadata.source_sha` and `metadata.civiccast_source_head` bind
     it to the exact candidate commit.
3. `native-server-binaries.ccpack`
   - PostgreSQL, TSDuck, FFmpeg, and server-side runtime binaries (NATS was
     removed from this pack -- owner decision 2026-08-20, see ADR 0023);
   - signed; `metadata.source_sha` binds it to the exact candidate commit.
4. `SHA256SUMS.txt`
5. `candidate-receipt.json`

AI model components — the caption tiers (floor mandatory, large-v3 optional
quality), both adaptive Summary models, and Translation — are required for a
complete default station, and are acquired during the installer's acquisition
walkthrough as individually verified downloads. The Station-Pack/USB offline
path carries them as verified side-load inputs for air-gapped installation;
they are not public release assets. Additional alternate local models and
consent-based cloud providers may use optional acquisitions or network
configuration, but they must never displace, rename, or masquerade as any
required component.

## Trust chain and D2 amendment

The current owner-approved D2 contract places payload checksums inside the
signed executable. Sidecar packs therefore require an explicit owner-approved
D2 amendment before implementation.

Proposed equivalent trust chain:

`Authenticode-signed bootstrap -> embedded pack-signing public key -> signed
canonical pack manifest -> path, size, and SHA-256 verification of every file`

Each manifest contains:

- product and component identity;
- product version and compatibility range;
- caption model repository, revision, and known file identities where
  applicable;
- canonical relative path, byte length, and SHA-256 for every file;
- exact file count and total bytes;
- manifest format version and signing-key identifier;
- detached signature.

The bootstrap must reject before execution:

- missing, extra, duplicate, or renamed files;
- path traversal, absolute paths, alternate data streams, or reparse points;
- byte-length or hash mismatch;
- a pack or manifest from another version/product;
- an unknown, revoked, malformed, or invalid signature;
- substitution of a smaller caption model under the required name.

Verification occurs before extraction, again against the staged tree, and again
after laydown. Activation is atomic only after a compatible required pack set
and its local self-tests pass. Caption-pack or caption-self-test failure always
blocks broadcast readiness. Missing Summary or Translation packs keep the
installation explicitly incomplete and the affected features unavailable; they
are never silently omitted or reclassified as optional.

## Installation modes

### Online

1. Bootstrap obtains a signed channel index over TLS.
2. It verifies the index signature using its embedded trust root.
3. It downloads the two public packs (Core and Captions) into a bounded
   temporary staging directory with resume support. AI model components
   (caption tiers, both Summary models, Translation) come from the installer's
   separate acquisition walkthrough, not as public release packs (amendment
   above).
4. It verifies, extracts, re-verifies, provisions, and performs the offline
   caption, summary, and translation proofs.
5. Only then does it enable normal service start and station readiness.

### Air-gapped

1. Operator selects a Station Pack on USB or a local share.
2. Bootstrap makes no network request in air-gapped mode.
3. It verifies every required pack using the same signatures, manifests,
   compatibility rules, and file hashes.
4. It installs and runs the same offline caption, summary, and translation
   proofs.
5. Source media remains untouched; temporary local staging is cleaned after a
   successful install.

If the caption pack is absent or corrupt, the installation may retain a
diagnostic/maintenance shell but the station remains visibly **not broadcast
ready**. The operator can retry online or import a verified offline pack.
There is no model downgrade or caption-disabled success path.

If either Summary pack or the Translation pack is absent or corrupt, the same
diagnostic shell may remain, but the station is visibly **incomplete** and the
affected local feature is unavailable. Repair must obtain or import the exact
verified pack; there is no complete-station success path that quietly drops
summary or translation.

## Repair, update, rollback, and uninstall

- Repair verifies every active file in the Core, Captions, both Summary, and
  Translation packs against the signed manifests and restores only
  product-owned corrupt/missing files. It preserves all ProgramData,
  configuration, databases, recordings, and station content.
- Update stages a complete compatible required pack set side by side, runs D3
  migration plus caption, summary, and translation proofs, then atomically
  switches. The previous complete set remains available for rollback.
- Failed health, migration, model self-test, signature, or hash checks restore
  the prior complete set or enter the specified halted recovery state.
- Default uninstall removes product-owned Program Files, service, firewall,
  registry, ARP, and shortcut state while preserving ProgramData.
- Typed purge is the only path that removes the exact ProgramData inventory.
- External USB or share packs are never deleted.

## Required acceptance evidence

1. The actual bootstrap byte count is `<= 299999999`, with SHA-256 recorded.
2. A one-byte size overrun fails the build/release gate.
3. Add/delete/mutate/reparse/path-escape/signature/version-swap negative
   controls fail before provisioning or code execution for every required pack.
4. A pristine online venue installs the exact Core, Captions floor tier (the
   mandatory `medium` model), both Summary, and Translation packs and passes
   socket-denied ASR, active WebVTT, CEA-708 insertion, decode-back, local
   summary, and local translation proofs. `large-v3` is an optional quality
   add-on, not required for this proof; when it is additionally installed,
   the same caption proofs also pass against it.
5. A pristine air-gapped venue passes the same proof with outbound networking
   blocked.
6. Missing/corrupt captions keep the product not-ready; verified import/repair
   restores readiness without changing ProgramData.
7. Missing/corrupt Summary or Translation packs keep the product incomplete;
   verified import/repair restores the affected features without changing
   ProgramData.
8. Repair detects corruption in application, runtime, dependency, selector, and
   every required model tree.
9. Update and rollback operate on complete compatible pack sets through real
   service/database lifecycle proof.
10. Default and purge uninstall satisfy the exact bidirectional inventory.
11. The complete D7 Windows Sandbox matrix and any focused persistent-VM gap
    rows pass at the final candidate SHA.

This plan reduces the download/launch artifact seen by a new user. It does not
claim that the entire offline station is under 300 MB, and it does not reduce
the required product.
