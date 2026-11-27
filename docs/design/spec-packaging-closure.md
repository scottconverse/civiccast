# Execution spec — runtime packaging closure (`slice:ws5-packaging-closure`)

**Decision state: Proposed → revised per auditor design review SDR-008.
Not owner-approved. The OpenH264/FFmpeg licensing posture is an explicit
owner-acceptance item, evidence first.**

Charter §7 "Runtime packaging": minimal, checksum-locked, license-reviewed.

## Decisions (v2)

- **D1. Authenticated upstream inputs (SDR-008).** Every upstream artifact is
  pinned BY HASH before anything derives from it: the official GStreamer
  1.28.5 MSVC runtime installer (sha256 recorded at acquisition; upstream
  signature/hash page cited), the `gstreamer-python==1.28.5` wheel (PyPI
  hash from the lockfile), FFmpeg build (exact build + its published hash +
  its configuration string recorded — the config string is licensing
  evidence), PostgreSQL 17 zip (EDB published hash), CPython
  (python.org/astral hash). (NATS server was pinned here until the
  2026-08-20 owner decision removed NATS from the product entirely — see
  ADR 0023; `nats-server.exe` is no longer part of this closure or the
  `native-server-binaries` pack.) Closure runs
  in a CLEAN isolated environment (fresh Sandbox/VM or scrubbed-PATH,
  isolated-registry shell) so a contaminated dev box cannot leak DLLs in.
- **D2. Closure = static + dynamic + resources (SDR-008).** Static PE-import
  walk (pefile, MS-system-DLL allowlist) is the FLOOR. Added: (a) GStreamer
  non-PE resources — typelibs (`girepository`), `gio` modules, plugin data;
  (b) PostgreSQL `share\` (timezone, extensions incl. btree_gist control
  files) and `lib\` extension DLLs; (c) Python native modules in the
  wheelhouse venv; (d) a DYNAMIC trace pass: run the D6 verification suite
  under a file-access trace (procmon boot-log or equivalent) inside the
  clean environment and diff accessed-paths against the tree — anything
  loaded from outside the tree is a closure miss and fails the build.
- **D3. License policy:** SHIP LGPL core/base/good + BSD/MIT; EXCLUDE all
  GPL (x264 no-ship stands; `openh264enc` default). Per-FILE provenance →
  license mapping in `LICENSE-BOM.md` generated from the manifest, with
  required notices bundled. `gst-inspect` license metadata is an INPUT, not
  the authority — each plugin's license is confirmed against upstream source
  license files for the exact version. **OpenH264 distribution posture
  (Cisco binary vs compiled-in, patent implications) and the exact FFmpeg
  build configuration are written up as an evidence memo for OWNER
  acceptance before the beta ships — the spec does not settle them.**
  CVE policy: BOM records versions; closure script re-run is the update path.
- **D4. Conditional-encoder guard/remap with a REAL probe (SDR-008):**
  remap table (vah264enc → mfh264enc → nvh264enc → openh264enc; vah265enc →
  nvh265enc → mfh265enc → honest HEVC-unavailable error). Selection requires
  factory presence AND a successful 1-second real encode probe (caps
  negotiated on the actual adapter) at doctor time — `ElementFactory.find`
  alone is explicitly insufficient; a present-but-broken NVENC falls through
  to the next mapping with the reason logged.
- **D5. Manifest trust at install:** `runtime-manifest.json` + `SHA256SUMS`
  ship INSIDE the signed installer (chained to Authenticode; see installer
  spec D2). The verifier cross-checks file count both directions (no orphan
  files, no missing entries).
- **D6. Verification suite (packaged-tree twin of the spike proofs):**
  isolated `GST_REGISTRY` + scrubbed PATH + packaged tree only: 51-factory
  sweep, caption embed + decode-back, one `SWAPS=2` engine run, D4 probe
  matrix, pg initdb+start+SELECT 1 from the packaged binaries,
  control-plane import smoke. Green = closure holds
  for the application paths we ship, not just for plugin loading.

## Acceptance criteria

- AC1 Deterministic: two clean-environment runs from the same pinned inputs
  ⇒ identical SHA256SUMS.
- AC2 D6 suite green against the packaged tree in the clean environment.
- AC3 GPL negative control: seeding `x264enc` into the required list makes
  the build REFUSE.
- AC4 Dynamic-closure negative control: delete one typelib/gio module/pg
  share file from the tree ⇒ D6 goes red (proves the dynamic pass covers it).
- AC5 Poisoned-environment control: plant a decoy GStreamer DLL on PATH and
  a stale registry outside the tree ⇒ D6 still loads exclusively from the
  tree (trace-verified).
- AC6 Present-but-broken encoder control: force the NVENC probe to fail ⇒
  doctor reports fallback to the next mapping.
- AC7 Every shipped file: hash + BOM entry + license, cross-checked counts;
  unknown-license file count is ZERO (halt otherwise).
- AC8 Real size numbers recorded (tree + installer) — stated, not spun.

## Halt triggers

- Required factory's plugin turns out GPL-only → owner decision.
- Unknown-provenance DLL in closure → identify or exclude; never ship
  unknowns.
- Dynamic trace shows loads from outside the tree that cannot be brought
  inside (OS-version-specific media DLLs) → document as explicit OS
  dependency with version floor, owner-visible.
