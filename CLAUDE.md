# CivicCast - Project Instructions for Codex and Claude Code

## Current owner workflow - 2026-10-19

Scott authorized simplifying these instructions to end repeated audit/build/status
loops. This replaces the mandatory five-lens audit before every push, automatic
cross-agent review ladder, zero-findings-at-HEAD rule, and ceremonial role reports.
Older process documents and historical handoffs do not restore those generic rules.
Product requirements, explicit task acceptance, required CI, branch protections,
security/data safeguards, and release recovery requirements remain in force.

Beta20 was publicly released; the October 19 pause is historical, not current state.
Read `docs/handoff/CURRENT.md` for the portable handoff and outstanding work.
Scott authorized publishing the checker fix, portable instructions, and handoff on
October 24. This is not an installer or product-release assignment. Do not launch
builds, test suites, or release workflows merely because a new agent opens the repo.

## Completion contract (owner-approved 2026-10-19)

- Complete the requested acceptance outcomes and authorized delivery. Do not stop merely to give a progress update when executable authorized work remains. Stop when the requested outcome is delivered; do not invent the next project.
- Treat "continue", "finish", and "keep going" as continuation of the current authorized scope. Continuous development requires an explicit request for ongoing work with no bounded terminal. Do not silently narrow an existing assignment.
- Keep one concise current list of outcomes, passed evidence, remaining work, blockers, candidate identity, and next action when work spans sessions or external waits. Reuse an existing list; do not create parallel ledgers. Before handoff or compaction, preserve that state and relevant decisions.
- Default to serial execution and one current candidate. No automatic delegation. Use a bounded independent reviewer only when explicitly authorized or required by the current assignment; otherwise disclose the absence of independent review rather than claiming it occurred.
- Inspect relevant files, make the smallest sufficient change, and run the narrowest meaningful checks plus required CI and acceptance checks. Reproduce a bug before fixing when practical. Do not manufacture tests for prose or mechanical changes.
- Every additional check, review, or rebuild must address changed behavior, a named defect, an unresolved required outcome, or required CI. Reuse valid unaffected evidence. Test-only and bookkeeping changes do not invalidate unchanged signed artifacts.
- After two failed attempts for the same cause, diagnose before another attempt. Change the approach based on evidence; if genuinely blocked, report the concrete blocker and continue independent authorized work. Do not blind-retry or abandon the whole task merely because a repair is difficult.
- Update documentation where behavior or a relied-upon claim changed. Do not force every change through every document, a five-lens report, multiple review gates, or a new harness. Optional improvements go outside the current assignment.
- Preserve required CI, review requirements enforced by the repository, data preservation, signing, backup/recovery, and actual installed-behavior acceptance. Do not waive required outcomes or fake receipts to finish faster. Existing authorization remains valid; do not ask again at every stage.
- For background work, record the owned process/job identity, start time, expected terminal result, next action, and cancellation method in the existing task state. Verify resource headroom before expensive or concurrent operations. A watcher proves only what it actually performs; do not promise automatic continuation without a working wakeup/continuation mechanism.
- An explicit stop takes priority: stop launching work, cancel owned agents/jobs/helpers/remote runs as authorized, verify terminal state, and report anything still running or inaccessible. Pausing a goal or ending a launcher is not OS/CI cancellation. Never act on stale PIDs without verifying ownership. A paused task requires explicit resumption.
- Query live state for progress claims; distinguish prepared, executed, verified, and delivered. A documented blocker means incomplete, not complete. Final reports state delivered results, checks actually run, and remaining limitations. Effort, a deadline, or a polished summary is not completion evidence.
- Mutation testing was removed by Scott on October 22. Do not restore it or turn informational checks into completion gates. No repeated broad testing, audits, or rebuilds without an affected requirement or concrete unresolved defect.
- No Git LFS transfers or spending through October 2026. Keep LFS smudging and push transfers disabled. Source work and this handoff do not need installers or model downloads.
- Keep Windows terminals/helpers invisible; use ConPTY (`tty=true`, `login=false`) and Hidden/CreateNoWindow for background children. Preserve disabled GitHub CLI telemetry and existing hidden launchers. Never reboot the host.
- Never pause an active goal without a current owner request. A newer explicit resume or physical goal-start action supersedes an earlier pause. Do not mark a goal complete with required work remaining.
- Use documented software/API simulators for hardware-facing integrations, including Roku. Do not make physical-device testing an acceptance gate. The prior host was a test server; that does not automatically authorize changes to a different machine's live workloads.

## Native-Windows product and authority

CivicCast is a native Windows civic broadcast application; `main` is the single
product line. WSL/Linux deployment was retired. Keep version identities consistent
with `scripts/policy/check_release_identity.py`; do not reopen that architecture.
Scott's current assignment determines the coder, reviewer, and authority. Old
model-role assignments and prior beta authorizations are historical, not permission
to resume a stopped task. Existing authorization for a current task remains valid.

Use DCO sign-off and PRs to `main`; merge only when authorized and required CI and
review requirements pass. Publish, tag, or cut over production only within actual
owner authorization. Do not bypass branch protection or move published tags.

## Sources of truth and working path

- Source: `https://github.com/scottconverse/civiccast`; use the current checkout root, not a fixed drive path.
- Current portable state: `docs/handoff/CURRENT.md`; build inputs: `docs/handoff/BUILD-ASSETS.md`. Update this existing handoff when work resumes, rather than creating parallel ledgers.
- Product spec: `docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md`.
- Branch policy: `BRANCHES.md`. Architectural decisions: `docs/adr/`.
- Read relevant product sections before changing behavior; do not reload the entire
  spec for bookkeeping. Resolve material product ambiguity with Scott; routine
  implementation choices remain the engineer's responsibility.
- Work on the assigned branch. Never edit frozen release-candidate worktrees merely
  to update these process instructions.

## Verification and release

Inspect the affected path, implement the smallest correct change, run meaningful
affected checks and required CI, and review the resulting diff. The optional
checklist at `docs/process/5-lens-self-audit.md` is guidance, not a push gate.
Independent review is bounded and used when the assignment or repository requires
it; it is not automatically launched for every push or documentation correction.

Keep claims-evidence CI for the registered governed set (`docs/claims/claims.yaml`,
`scripts/policy/check_claims_evidence.py`). Do not weaken it or sweep unrelated files.
For a release, follow its applicable executable requirements in
`docs/ops/release-candidates.md` and the current acceptance contract: signed exact
candidate, required CI, isolated install/preservation, actual requested workflows,
and applicable lifecycle checks. A new soak is not automatic. Source/health checks
do not establish installed acceptance. Reuse unaffected evidence at its actual scope.

Before production cutover, verify a usable recovery backup and recovery procedure,
then perform only the authorized upgrade and actual post-upgrade acceptance. Honor
the no-host-reboot constraint. Never synthesize an acceptance receipt or lower a
resource/security requirement to get a pass. A blocker remains open until resolved.

## Closed architectural decisions

These are decided. Do not reopen them without surfacing to the human director first.

- **Messaging substrate: in-process broker (`civiccast.platform.broker.InProcessBrokerClient`).** ADR 0001 originally chose NATS JetStream: Apache 2.0 license, single-binary install, sub-millisecond latency, persistent streams with consumer-group fan-out, rejecting Redis Streams for license posture (SSPL/RSAL fork situation creates a procurement smell municipal evaluators will flag) and Postgres LISTEN/NOTIFY for capability (8KB payload limit, no durable replay, no consumer groups). The owner reopened and reversed that decision on 2026-08-20: NATS never did real production work in this codebase, and it has been cut entirely — the supervised child process, the config file, packaging, and every health/readiness gate that required it. ADR 0023 records the reversal and supersedes ADR 0001. The in-process broker is now the sole event-bus implementation for every deployment mode; there is no separate "production" broker mode. Postgres LISTEN/NOTIFY is still used for low-volume "tell the UI a row changed" purposes — not as the broadcast event bus.

- **Caption runtimes:** Whistle is the CPU live-caption primary in Beta20; faster-whisper (CTranslate2) supplies per-channel fallback, with supported NVIDIA acceleration applying to Whisper. ADR 0002 describes the Whisper choice; current source and the release manual describe the later Whistle integration.

- **Cable broadcast scope: deferred to optional `civiccast-cable` add-on.** Phase 3+ in the spec. D1 (Rust vs Go for cable-grade playout) and D14 (full loudness preset library) are closed at the spec level — both moved to the cable add-on doc. Streaming-core code does not depend on cable artifacts.

- **License: Apache 2.0 for code, CC BY 4.0 for documentation.** No relicensing without the multi-layer approval defined in spec §14.8.

- **Repository layout:** the native product is in `scottconverse/civiccast`, with Python modules under `civiccast/`, apps under `civiccast/apps/`, build tools under `scripts/`, and documentation under `docs/`. Follow actual module boundaries and documented APIs.

- **Schema: `civiccast.*` PostgreSQL namespace.** Mode A: only schema in DB. Mode B: lives alongside `civiccore.*`, `civicclerk.*`, etc. CivicCast never reads or writes outside its own schema except through documented APIs.

## Open decisions and where they resolve

Current open decisions live in the canonical spec's own section 13, "Open
decisions for Scott" (`docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md`).
That section is dated and tracks which items are resolved (with the
resolving note inline) and which remain open — read it directly rather than
trusting a decision list mirrored here, since mirrored lists go stale.
Native-line decisions specific to the native Windows program (D-numbers
scoped to `.agent-runs/native-windows/specs/*.md`, e.g. the
claims-evidence rule, the dual-runtime guard, the migration contract) are a
separate numbering namespace from the spec's own D-numbers — don't conflate
the two when citing a "D#".

If you reach a decision point that depends on something genuinely
unresolved, surface it to the human director and write the resulting ADR.
Don't pick silently.

## Tooling

- **Python 3.12+.** Type hints throughout.
- **Linter / formatter: ruff** (replaces black + isort + flake8). Configuration in `pyproject.toml`.
- **Type checker: mypy** in strict mode for service modules. `--ignore-missing-imports` is permitted at module boundaries with a documented reason.
- **Tests: pytest + hypothesis.** Coverage targets: 80% service modules, 90% platform substrate, 95% streaming origin and syndication module (where bugs cause channel outages).
- **Database: PostgreSQL 17 + pgvector.** Migrations: Alembic, one Python file per migration, both `upgrade` and `downgrade` implemented and tested.
- **Broker:** in-process broker, as described in the closed decision above; do not provision a separate Redis/NATS broker from historical instructions.
- **AI runtime:** Ollama for LLMs; Whistle primary and faster-whisper fallback for live captions. Product models are separate from an agent's coding-model choice.
- **Pre-commit hooks:** ruff, mypy, trailing-whitespace, end-of-file-fixer, conventional-commit-message check.
- **Frontend:** use each app's `package.json` and `package-lock.json` for current tool versions and commands; do not install historical versions copied from a spec.
- **Documentation:** the public landing page is `docs/index.html` with `docs/assets/web/landing.css`, deployed by GitHub Pages. Follow the existing documentation workflow for full manual rendering; a prose-only handoff does not require rerendering manuals.
- **License header on every source file:** `# SPDX-License-Identifier: Apache-2.0` on the first or second line. Use `# Copyright (c) The CivicCast Authors` rather than per-contributor copyright lines (matches the DCO no-CLA contribution model in spec §14.6).
- **Commit message convention: Conventional Commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). Sign off every commit with `Signed-off-by: Name <email>` per the DCO.

## Language boundaries

Each language has one job and one home. Do not add a language or a second
home without the human director's OK. Reason: one toolchain per language
keeps builds, CI and reviews simple.

| Path | Language | Owns |
|---|---|---|
| `civiccast/` (not `civiccast/apps/`), `tests/`, `alembic/` | Python | The product: services, CLI, database, streaming, captions, AI runtime |
| `civiccast/apps/*` | TypeScript | The web portals and the installer UI |
| `civiccast/apps/installer/src-tauri/` | Rust | The Tauri shell of the installer. Keep it thin. |
| `tools/ndi-ffmpeg-sender/` | Rust | The NDI sender tool |
| `civiccast/apps/ott-native/` | Kotlin, Swift, BrightScript | The TV and device apps. Each platform requires its language. |

Tool versions and commands: see "Tooling" above, and each app's `package.json`.

### How the parts talk
- TypeScript apps reach Python only through the documented HTTP APIs. No cross-language imports.
- Python reaches a Rust tool only as a separate program. Rust is not a linked library in Python.

### Existing exception
`civiccast/control_room/tsr_service/` is a Node sidecar in the Python tree (ADR 0019). The Python control plane is its only caller, on 127.0.0.1. Leave it there. Do not add another one.

### Not covered
Scripts and config files (PowerShell, shell, SQL, YAML, JSON, TOML, NSIS) can live where they are used.

### Before you write a new source file
1. Find the row whose "Owns" column fits the task. Use that path and that language.
2. Do not copy the language of a nearby file if the job belongs to a different row.
3. If no row fits, or two rows fit, stop and ask. Do not write code first.

### Ask first
- A new Rust crate (there are two: `src-tauri` and `ndi-ffmpeg-sender`).
- A new Node package outside `civiccast/apps/`.
- A second Python project (there is one `pyproject.toml`, at the repo root).
- A language that is not in the table.

## Non-negotiables (pointer)

The spec's §4 contains the project's non-negotiables. They are the floor, not aspirations. Re-read §4 before any work that touches:

- User-facing surfaces (UX non-negotiables, §4.1)
- AI artifacts (AI principles, §4.2 — especially operator-approval-before-publish and refusal-on-uncertainty)
- Anything in the prohibited-uses list (§4.3 — voice cloning, sentiment scoring of named individuals, biometric ID, predictive scoring of residents, retention of resident audio for AI training, covert recording, selling subscriber data)
- Documentation artifacts (§4.4 — every release ships with the full doc artifact set)
- Test gates (§4.5)
- Archival behavior (§4.6 — every public-record meeting publishes to portal + IA + local NAS before being marked archive-complete)

A pull request that violates a non-negotiable is closed without review. The maintainer-level enforcement is real, not aspirational.

## Three-tier publish (load-bearing principle)

Every public-record meeting recording must reach three tiers. The spec's §2.6 codifies this.

- **Tier 1 — Portal (canonical).** Self-hosted HLS origin + branded VOD page. The URL residents, press, and CivicClerk records cite. The system of record for legal and public-records purposes.
- **Tier 2 — Internet Archive (permanence).** Required publish target. Peer to portal, not fallback. Local NAS archive is a required peer to IA so the station retains the asset bit-for-bit even if IA suffers a Hachette-class event.
- **Tier 3 — Syndication (reach + capacity insurance).** YouTube Live as primary; Facebook Live, PeerTube/Owncast, X/other as optional. YouTube Live is functionally required because it is the only ingest tier that scales infinitely under high-stakes-meeting load.

Surfaces fan out independently and complete asynchronously. The portal goes public as soon as portal publish succeeds; IA, syndication, podcast, signed transcript, and subscriber notifications complete on their own timelines. The publish dashboard reports per-surface state. A failed reach surface (e.g., YouTube) does not block the public-record availability of a recording whose portal and archive surfaces succeeded.

## When to ask the human director

Surface to the human, do not improvise, when:

- A change requires reopening a closed architectural decision.
- A change looks like it belongs to the retired WSL/Linux lane rather than this product.
- A non-negotiable conflicts with what's being asked.
- An open decision (spec §13, or a native-line D-decision) needs to resolve and the resolution would change the spec's text.
- A real-world action is required (creating an account, registering a domain, ordering hardware, signing an agreement, contacting a third party).
- The verification gate cannot be passed and the gap is not a known short-term limitation.
- The spec and another source-of-truth document (an ADR, BRANCHES.md) appear to disagree.
- A test or measurement reveals a fact that contradicts the spec.

When you ask, name (1) the branch/line you're on, (2) the section of the spec that's relevant, (3) the question, and (4) the option you'd recommend if you had to pick. The human is your director; they answer the question and you proceed.

## Git workflow

- Branch from `main`; there is no second line to choose between.
- Conventional commits with DCO sign-off on every commit.
- Use pull requests to `main`; merge on green CI under the owner's assignment.
- This native Windows product uses `v1.0.0-beta.N` release tags. Tag and publish only within current owner authorization. Historical beta.5 authorization does not resume a stopped Beta20 task.
- Release notes and PR descriptions state the resulting behavior, relevant verification, limitations, and affected ADRs. No fixed audit report is required.

## Engineering posture

Apply engineering, UX, and QA judgment to the affected behavior. Trace relevant
callers and runtime data; consider accessibility and error states when UI changes.
Verify actual behavior at the layer claimed. Do not force unrelated changes through
every UI state or every document. A passing suite proves only what it exercises.
Use a focused review, fix material defects, and defer unrelated improvements.

## What you never do

- Choose the quickest implementation over the correct one.
- Build a UI you wouldn't want to use yourself.
- Declare work complete without satisfying the requested acceptance and authorized delivery.
- Omit relevant rendered states when changing a user workflow.
- Ignore accessibility.
- Ignore relevant browser errors when verifying a changed browser workflow.
- Write tests that only cover the happy path.
- Treat a passing test suite as proof the product is ready.
- Assume a displayed value reads from the source you think it reads from without tracing the actual runtime path.
- Fix something without checking what it might have broken adjacent to it.
- Execute a requirement you believe is wrong without first flagging it and proposing an alternative.
- Ship changed behavior with misleading affected documentation or undocumented breaking changes.
- Reopen a closed architectural decision without surfacing it to the human director first.
- Merge with failing required checks or publish without the owner's authorization and candidate evidence.
- Pick an open decision (spec §13 or a native-line D-decision) silently — write the ADR.

---

*Read the relevant spec section when the task affects product behavior or architecture.*
