# Final package preparation checkpoint

Package producer: `31298ce8492826d9987aff55ebe3658f8ff54559`, version `1.0.0-beta.11`, hosted run 37671057029, conclusion success. Tests/documentation-only follow-up does not change this artifact provenance or assert that a different source produced these bytes.

Local kit: `C:\Dev\Claude\civiccast-beta11-whistle\outputs\beta11-candidate-31298ce8\kit`. Full inventory/hash report: `C:\Dev\Claude\civiccast-beta11-whistle\outputs\beta11-candidate-31298ce8\kit-verification.md`. The kit contains 19 files totaling 26,913,523,987 bytes. Installer Authenticode is Valid, signer Scott Converse, version beta.11, SHA-256 `20afb093b5b7206be7589a400f185360a21b93425d2a9d94d92ede2372821db1`.

All five runtime packs and six station packs passed Ed25519 signature, every signed member size/hash, and outer size/hash verification. Runtime payload trees match their build reports. Station index SHA-256 is `1afdbdee1a42a1e6b7822ee1d4468b2f798aa2ddce3d6dabc70dbb25ff8b9446`; signature and station-specific Rust required-component contract match. The legacy Python reference distribution verifier has a different documented component set; it was not used to claim station acceptance. Whistle model and engine match their pinned upstream identities. Four cached model packs match final index entries exactly and were copied without modifying the production cache. All 19 destination hashes, the station checksum manifest, copied installer signature and exact-run manual receipt passed.

Verification receipts: `C:\Users\scott\Documents\Codex\2026-10-04\rea\outputs\beta11-candidate-31298ce8\cache-verification`. Exact-run manual currentness and `candidate_manual.py verify` passed locally. Kit plus separate extracted binaries occupy 31,801,155,393 bytes intentionally; this is release staging, not live-caption archive growth.

## Focused test corrections after full CI

Unit run 37670930362 attempt 1 timed out during Ubuntu package installation before pytest. The one retry passed prerequisites and produced 129 failed, 10,942 passed, 78 skipped and five deselected tests in 2,748.06 seconds. Four Beta 11 overlaps were reproduced/diagnosed and corrected only in tests: stub the Windows cleanup API for cross-platform concurrency testing; expect WebP inline manual images; select Whisper explicitly when asserting Whisper settings; make the empty-ASR fixture return no hypotheses rather than valid `um` text.

The four specific regressions passed in 5.56 seconds. Full Whistle-runtime, docsite-router and factory test files passed 34 tests in 28.48 seconds. The broader four-file run reported 61 passed/five failed: diagnostic assertions expect timing symbols absent in both PR base and package source. Ruff check/format and diff checks passed. These focused results do not establish full CI green or classify all other failures as inherited. No packaged runtime code changed for these corrections.

Independent follow-up review reran the four regressions (four passed in 5.67 seconds), three affected modules (34 passed in 18.15 seconds), and native-default Whistle selection (one passed in 1.16 seconds), plus Ruff check/format. It confirmed no production-file diff and accurate artifact/open-check documentation. Receipt: `C:\Dev\Claude\civiccast-beta11-whistle\outputs\beta11-candidate-31298ce8\beta11-test-diff-review.md`. The broader 61/five result was not independently rerun.

## Open release proof

Clean-install, upgrade and download-only activation tests remain unrun. Preserve the running station; the existing 16 GiB Sandbox gate needs a separate suitable host. Its intentional regenerated Beta 5 baseline pin `ff7c10a698116a66b292f76493daebfed8618244631859d0d5bfc95a4d945b16` does not match the older local copies. Historical approved baseline artifacts have expired; recover that exact baseline or establish a replacement with explicit provenance. Do not silently change the pin to local bytes. Full CI findings and stale egress claims bindings remain open. No merge, tag, publication, station install, engine load, restart or Sandbox execution occurred.

Fresh station check October 7 at 14:23 MDT: all three channels healthy, captions fresh, same supervisor PID 28760. The independent 20-minute observer continues; re-query before reporting fresh status. Owner-excluded gaming measurements remain excluded.

## Five-lens review of follow-up

- Engineering: source/run/receipt paths and hashes match retained artifacts; follow-up changes only tests and documentation.
- UX: no new product flow; handoff distinguishes a verified kit from release readiness.
- Tests: four observed failures have focused passing corrections; full-suite failure and remaining diagnostic failures remain visible.
- Docs: handoff, verification record and this receipt identify the package producer separately from subsequent test/documentation changes; public beta.10 remains the published release.
- QA: kit file count/size and staging growth reconcile; installer validation is not represented as clean installation or a new package soak.
- Artifact-state: no release-ready claim, no local station modification, no opaque producer SHA; historical evidence stays identified as historical. Legacy untracked oversight evidence is excluded from the commit.

proved: hosted build success; installer signature; 11 pack signatures/payloads; 19 copied hashes; exact-run manuals; four focused regressions | lane: Critical
