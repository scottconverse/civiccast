# CivicCast native-Windows takeover prompt (AI-agnostic)

You are taking over the CivicCast native-Windows beta work as the primary implementing AI agent.

Before taking actions, read these files end to end in this order:

1. `C:\cc-nb\docs\process\CODEX-NATIVE-BETA-HANDOFF-2026-07-24.md`
2. `C:\cc-nb\docs\process\CODEX-NATIVE-BETA-CODER-HANDOFF-2026-07-27.md`
3. `C:\cc-nb\docs\process\CODEX-NATIVE-BETA-HANDOFF-RECEIPT-2026-07-27.md`
4. `C:\cc-nb\.agent-runs\native-windows\specs\spec-native-beta-recovery.md`
5. `C:\cc-nb\.agent-runs\native-windows\specs\spec-installer-lifecycle.md`
6. `C:\cc-nb\.agent-runs\native-windows\specs\plan-sub-300mb-bootstrap.md`
7. `C:\cc-nb\docs\spec\3.3-to-4.0-sprint-list-and-implementation-plan.md`

### Owner rules

- Scott Converse is the owner.
  Only Scott may authorize merge, tag, release signing, publication, shipment, gate bypass, force-push, or branch-protection changes.
- Scope changes may proceed only as bounded, validated packages; no silent scope trimming.
- Captions are legally required and cannot be downgraded.
- **Superseded for the shipped station:** the later adaptive-tier decision and the Whistle
  live-caption primary replace this fixed contract (see
  `docs/design/spec-native-beta-recovery.md` and `docs/design/plan-sub-300mb-bootstrap.md`).
- Accepted caption contract at the time is exact: `faster-whisper` large-v3 with model
  revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`, `faster-whisper==1.2.1`,
  `ctranslate2==4.8.1`, `cuda`, `int8_float16`, local-only loading.
- Do not substitute smaller/quantized runtime to satisfy size or speed gates.
- Bootstrap target is `< 300 MB` only for the installer executable; mandatory packs remain separate and verified.

### Environment-agnostic startup checklist

Run equivalent commands in your environment:

1. Confirm repo identity and remote state (`fetch`, branch, `HEAD`, remote branch, divergence, status, tree id).
2. Confirm working tree context for this handoff (or equivalent) and snapshot artifact integrity.
3. Confirm no CivicCast build/test/installer/audit/VM/Vault process is actively running before changing anything.
4. Read the entire handoff and linked specs before editing.
5. Identify staged vs unstaged vs untracked changes and freeze a package boundary.

### Working cadence

Use one active package at a time:

1. WP1: native payload + captions
2. WP2: installer/provisioning/repair/lifecycle
3. WP3: Stage 2 operator workflow
4. WP4: documentation truth pass
5. WP5: exact-candidate gates and owner handoff

For each package:
- freeze candidate SHA,
- run package-appropriate checks,
- close required findings,
- commit with provenance,
- push checkpoint (when permitted),
- then continue only after that package is complete and verified.

### Verification expectations

Always regenerate proofs from the frozen SHA you are proposing:
- focused runtime/pipeline tests,
- pollution tests and randomized checks,
- lint/type/deps checks,
- reproducible build artifacts with manifest/hash proof,
- offline caption proof,
- lifecycle/rehearsal matrix,
- fresh adversarial audit with zero required findings,
- clean release-control verdict for exact candidate SHA.

### First operational action sequence (today)

1. Baseline status and confirm the active tree identity.
2. Re-run focused checks and baseline proof suite.
3. Close cross-process install/acquisition locking defects first in WP1.
4. Rebuild artifacts from the frozen tree and record deterministic hashes.
5. Re-run adversarial audit cycle on the exact tree and report `AUDIT_PASS`-equivalent result or explicit fail reasons.
6. Send Scott one plain-English five-line status: current state, exact SHA, evidence rerun, active package, owner decision required.

### Documentation and evidence rule

Keep this handoff and this prompt as local inheritance continuity only.
Do not treat them as current status unless verified by fresh checks against the exact tree in your session.

### Hard stop rules

- If a required finding cannot be closed, pause and escalate.
- If a tool/command/process is unavailable in your environment, map it to the closest equivalent and document the substitution in status.
- Never call a draft plan “done” until it is installed, proven, and gate-closed.
