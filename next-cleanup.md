<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Next Cleanup

> Carried-forward engineering notes, not the Beta 17 release-completion list.
> Current release status and recorded checks are in the [RC6 verification record](docs/releases/v1.0.0-rc.6-verification.md); the Beta 19 record is history.

Deferred, non-blocking work stays out of `civiccast/` source per
`scripts/policy/check_no_todos.py` (no TODO/FIXME/HACK markers are allowed in
shipped source). Completed entries remain below as disposition records.

## Historical Beta 17 follow-up

Owner-deferred R9 findings are tracked in [Beta 17 follow-up](docs/ops/beta17-deferred.md).
That document preserves the original follow-up scope; Beta 16 packaging and
publication are complete. Current authorized work is tracked in
[Sprint 6](docs/ops/sprint6.md), rather than reopening those earlier release steps.

## Completed follow-up

- **WP-5 install-time payload staging + verification wiring — retired.** The
  current NSIS post-install path calls `--civiccast-stage-packs`, then invokes
  `--civiccast-verify-pack-tree` against the original signed
  `native-app-payload.ccpack` and the extracted `$INSTDIR\runtime` tree before
  calling the D3 engine with that tree as `--payload-source`. This closes the
  staging-and-reverification gap for the current signed-component-pack
  architecture. It supersedes the older embedded `runtime-manifest.json` /
  `SHA256SUMS` route; that legacy chain is not the mechanism used by the live
  bootstrap installer. Sources: `civiccast/apps/installer/src-tauri/nsis-hooks-bootstrap.nsh`,
  `civiccast/apps/installer/src-tauri/src/main.rs`, and
  `civiccast/native/upgrade/seams.py`.
