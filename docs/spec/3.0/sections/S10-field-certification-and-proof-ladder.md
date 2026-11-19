# S10 — Field Certification and Proof Ladder

> **Amendment 2026-08-21 (owner-approved):** Field certification for the
> native-Windows product line is proven by the **machine gates**, not by the
> rung-runner pipeline this section originally specified:
>
> - **Gate A** — the automated clean-Windows-Sandbox station-acceptance gate
>   (`docs/ops/gate-a.md`, `scripts/gate_a_verdict.py`): install → K1
>   activation → both UIs render → the clerk loop (upload → publish →
>   captions) → the GStreamer egress engine verified with TSDuck → a bounded
>   soak, judged by code, fail-closed, never from prose.
> - **Gate B** — the real-hardware 24h reboot/kill/restart unattended soak
>   that Gate A explicitly does not attempt (Windows Sandbox has no
>   hardware pass-through and cannot own multi-hour timing).
>
> The **rung-runner pipeline this section describes below (§3's `ProofRung`
> enum/`CapabilityProof` model, the `civiccast/proof/` package, the
> `/api/v1/proof/*` endpoints, the `doctor --proof` CLI surface, and the
> System Health → Proof Status screen) was never built and is not being
> built.** The legacy pre-Gate-A rung-numbered release-gate pipeline that
> did exist was removed in PR #12
> (`chore: remove the legacy pre-Gate-A rung-numbered release-gate
> pipeline`, commit `ef27958`). Do not resurrect it.
>
> The rest of this section is kept intact below as a historical design
> record — its vocabulary (contract/lab/machine/sdi/headend/field) is still
> useful shorthand in prose and in `CAPABILITIES.md`, but no code, API, or
> UI implements it as a tracked entity. Where this section's "DONE
> criteria" (§9) call for `civiccast/proof/models.py`, CAPABILITIES
> generation from that model, or the Proof Status UI screen, treat those as
> **superseded by Gate A / Gate B** — the machine verdict is the proof
> surface now, not a hand-maintained rung ladder.

**Status:** Spec section for Scott's review — implementation readiness TBD.
Superseded in part by the 2026-08-21 amendment above: field certification
proof now flows through Gate A / Gate B, not the rung-runner machinery
this section specifies.

**Purpose:** Formalize the master §5 unified 6-rung proof ladder as the single project vocabulary, governing how every capability states its evidence and when claims may be made to the public, app stores, hardware vendors, legal/FCC bodies, managed-service operators, and live devices. Define the hard public claim boundary and per-tier release gates. Specify the certified-integrator program. Ensure proof tracking is transparent to operators via CAPABILITIES surface + UI.

---

## 1. Goal & PEG automation rationale

**incumbent PEG platform (the referenced third-party vendor)** is closed-source appliance software that customers treat as a monolithic black box. It ships with a hardware package (VIO Lite/2/4/OMNI/Stream), annual support contracts, and per-minute cloud AI services. When incumbent PEG platform claims "supports captions," the claim is backed by a vendor whose reputation is on the line and whose customers can sue for breach of warranty. incumbent PEG platform never documents how many hours of testing prove "captions work" — it simply asserts confidence.

CivicCast is open-source software on commodity hardware, running unattended in a room by a 3-person PEG staff. The only way a station — or an app store, a cable headend, a municipality, or the public — can trust a CivicCast capability is to have **evidence openly staged and honestly communicated**. This section governs that system.

The gap we close: today the codebase has four overlapping proof vocabularies (`parity-evidence-matrix.json` status enum, `ProviderProofStatus` enum in `proof.py`, `_FIELD_PROOF_BOUNDARY` markers, and `not_claimed` fields scattered across `caption_embed.py`, `headend.py`, and elsewhere). This creates confusion and risk of accidental overclaiming. §5 of the master spec defines a single vocabulary; this section operationalizes it.

**Functional target:** CivicCast does not pretend to match the incumbent PEG platform's business model (per-minute metering, managed cloud, vendor-backed warranty). Instead, we match the incumbent PEG platform's honest functional threshold: an unattended appliance that can demonstrate, via transparent evidence, that each capability works as claimed. The proof ladder exists so that threshold is reachable for open-source software.

---

## 2. Current state (file:line grounding)

### Proof vocabulary today (four overlapping systems)

1. **parity-evidence-matrix.json (v2.0 release checkpoint)**
   - File: `docs/spec/2.0/parity-evidence-matrix.json:1–100` et seq.
   - Status enum: `complete`, `complete_with_external_dependency`, `human_blocked`
   - Scope: v2.0 release capability gaps (OTT apps, router hardware, caption appliances)
   - Mapping: `complete` ≈ rung 1+ (lab-proven); `complete_with_external_dependency` ≈ rung 2–3 gated by hardware/device

2. **ProviderProofStatus enum**
   - File: `civiccast/publish/proof.py:11–17`
   - Status enum: `not_configured`, `needs_live_proof`, `proof_passed`, `proof_failed_redaction`, `skipped_optional`
   - Scope: external provider integrations (Internet Archive, YouTube, NAS rsync, email, webhooks)
   - Mapping: `proof_passed` ≈ rung 4 (headend-accepted); others = rungs 0–2

3. **_FIELD_PROOF_BOUNDARY markers**
   - Files: `civiccast/egress/headend.py:35–38`, `civiccast/egress/caption_embed.py:16,84–88,117–121,284–287`
   - Pattern: `proof_boundary` string + `not_claimed` list document gaps openly
   - Scope: cable headend profiles, caption embedding
   - Mapping: boundaries = rung 1 (lab-proven); not_claimed items explain unproven aspects

### Proof-rung-tracking surface (missing / incomplete)

- **CAPABILITIES doc/API (missing):** no single place lists all capabilities with current rung + proof boundary
- **UI health disclosure (partial):** SystemHealthScreen shows status but does not distinguish "proven" vs "declared"
- **Release-gate checklist (missing):** no automated per-rung release gate beyond v1.1 AI metric floors

### Net-new work in S10

1. Unify vocabulary: one proof rung enum + schema across all entities
2. CAPABILITIES.md: single authoritative doc listing all capabilities + rung + proof boundary
3. Proof rung API: read-only endpoint exposing CAPABILITIES
4. Certified-integrator program: formal spec + contract language
5. Per-section proof tiers: all 27 section specs state current rung + advancement path
6. Release gates (per-tier): contract → lab → machine → SDI → headend → field

---

## 3. Entities / data model & migrations

### Proof rung enumeration (unified)

**Location:** new `civiccast/proof/models.py`

```python
from typing import Literal

ProofRung = Literal[
    "contract",      # 0 — code + unit/API/UI tests; no live egress
    "lab",           # 1 — runtime proof against loopback/synthetic
    "machine",       # 2 — Windows clean install + 24/72h soak + reboot
    "sdi",           # 3 — physical SDI captured from real DeckLink card
    "headend",       # 4 — accepted by real cable headend + station
    "field",         # 5 — unattended in production for agreed duration
]
```

### Capability tracking entity

**Location:** `civiccast/proof/models.py`

```python
@dataclass(frozen=True)
class ProofBoundary:
    reason: str
    affects_claim: str
    next_step: str | None

class CapabilityProof(BaseModel):
    capability_id: str
    label: str
    category: str
    current_rung: ProofRung
    evidence_reference: str | None
    reached_date: str | None
    proof_boundaries: list[ProofBoundary]
    operator_action: str
    next_rung: ProofRung | None
    advancement_path: str
```

### Mapping existing entities

| Old enum / marker | Maps to | Implementation |
|---|---|---|
| parity-evidence-matrix.json status | current_rung + evidence_reference | migrate to CAPABILITIES.json |
| ProviderProofStatus | CapabilityProof (external provider category) | add to CAPABILITIES |
| headend.py _FIELD_PROOF_BOUNDARY | proof_boundaries[].reason | wrap in ProofBoundary |
| caption_embed.py not_claimed | proof_boundaries[].affects_claim | fold into ProofBoundary list |
| HeadendProfile.not_claimed | CapabilityProof.proof_boundaries | 1:1 field migration |

### Migration strategy (no breaking changes)

1. Phase 1: define civiccast/proof/models.py with new entities
2. Phase 2: build CAPABILITIES.md + CAPABILITIES.json from code audit
3. Phase 3: each section spec updates entities to reference new proof model
4. Phase 4: deprecation notes to old enums; no removal until v3.1

---

## 4. API surface

### Proof-rung read-only endpoints

**Auth:** require_any_role(["support_admin", "meeting_operator", "records_clerk", "publish_operator", "setup_admin"]) — read-only diagnostic surface; support_admin sufficient

#### GET `/api/v1/proof/capabilities`

Returns full CAPABILITIES list with version and timestamp.

#### GET `/api/v1/proof/capabilities/{capability_id}`

Returns one capability's full proof state.

#### GET `/api/v1/proof/release-gates`

Returns per-tier release-gate readiness for current build.

### Proof-rung surface in operator CLI

**Command:** `civiccast doctor --proof`

Outputs current proof state for all capabilities, grouped by rung.

---

## 5. Operator UI surface

### New screen: System Health > Proof Status

**Location:** OperatorConsole → System Health → Proof Status tab

**Content:**
- Table of all capabilities + current rung
- Filter by category (Egress, Captioning, AI, Reliability, etc.)
- Drill-down to see proof_boundaries + advancement_path
- Live gate status per tier
- Release readiness checklist
- Operator-action summary

**Phone-first UX:**
- Expandable category sections
- One capability per card
- Blockage reasons in red with next_step CTA
- Share button exports CAPABILITIES snapshot for support bundle

### CG / Playout screens: proof status indicator

Show proof-tier badge:
- `[PROVEN]` for rung 3+ (SDI+)
- `[MACHINE]` for rung 2 (clean install + soak)
- `[LAB]` for rung 1 (loopback/synthetic)
- `[NOT YET]` for rung 0

---

## 6. Behavior / algorithms

### Proof-tier advancement (per-section)

Every section spec states:
1. Current rung (master §3 baseline or §4 gap)
2. How to reach next rung (tests, soak parameters, hardware, field criteria)
3. Honest boundary (what is NOT claimed today)

### Release-gate enforcement (per tier)

| Tier | Gate | Automated | Manual |
|---|---|---|---|
| Contract | All unit/API/UI tests pass | pytest + playwright | Code review |
| Lab | Runtime proof against loopback/synthetic at declared proof_boundary | loopback/synthetic runtime harness | Audit logs |
| Machine | Clean Windows install + 24/72h unattended soak incl. reboot; zero off-air | install script + CLI e2e + soak script | Manual walkthrough |
| SDI | Physical SDI captured from DeckLink | ffprobe decode-back | Hardware sign-off |
| Headend | Real cable headend accepts TS | Live station test | Engineer sign-off |
| Field | Unattended 24h–7d; failure recovery | Health event log | Operator attestation |

### Preventing accidental overclaiming

**Mechanism:** Every data model enforces proof constraint:

```python
def validate_proof_claim(self):
    if self.current_rung == "contract" and "field" in self.operator_action.lower():
        raise ValueError("cannot claim field-proof when rung is contract")
```

---

## 7. Proof tier: current rung + how to advance it

### CivicCast 3.0 proof landscape (per master §3)

| Capability | Current rung | Evidence | Next |
|---|---|---|---|
| Egress automation (24/7) | Machine (rung 2) | 24h soak in flight | Field proof at first station |
| Program log (72h rolling) | Lab (rung 1) | Unit + integration tests | rung 1, advancing to 2 |
| Playout states | Lab (rung 1) | Production use | rung 1, advancing to 2 |
| 6 headend profiles | Lab (rung 1) | Synthetic headend test | Field (first station) |
| UDP/SPTS CBR sink | Lab (rung 1), advancing to 2 | Soak in flight | Field |
| TSDuck compliance probe | Lab (rung 1) | Synthetic test @ 8 Mbps | Field |
| SDI relay (DeckLink) | Contract (rung 0) | Code only | SDI proof (hardware) |
| NDI relay | Contract (rung 0) | Code only | Machine proof |
| Caption embed + decode-back | Lab (rung 1) | Sidecar tested; CEA-708 not | rung 1, advancing to 2 |
| Loudness normalization | Lab (rung 1) | Unit test | Lab full-path |
| Health telemetry | Lab (rung 1) | Pull-only API | Field (alerting) |
| Station identity | Lab (rung 1) | Installer proof | Field |
| Auth (5 roles) | Lab (rung 1) | Unit + API tests | Field |
| Installer wizard (11 screens) | Lab (rung 1) | Playwright walkthrough | Field |
| Live ingest/recording | Lab (rung 1) | Unit + e2e tests | Field |
| Captions (faster-whisper) | Lab (rung 1) | WER floor test | Field |
| Translation (translategemma:4b) | Lab (rung 1) | BLEU floor test | Field |
| Summary (gemma4:e4b) | Lab (rung 1) | Refusal test | Field |
| Facility router planning | Lab (rung 1) | Unit tests | Field |

### Path to rung 3 (SDI-proven, master §10 step 2)

1. Hardware: procure DeckLink card (e.g., DeckLink Mini Recorder) + BYO ffmpeg
2. Capture: run playout loop, capture physical SDI output to file
3. Validation: ffprobe decode-back + TR 101 290 P1 probe
4. Evidence: signed photo + soak log + decoded TS hash → S1-sdi-proof-2026-Q3.md

### Path to rung 4 (Headend-proven, master §10 step 11)

1. Station: deploy to first-station beta (e.g., North Tonawanda PEG, target July 2026)
2. Acceptance: live schedule + commit-to-air + 48h unattended soak
3. Operator proof: signs off; captures off-air monitor + playout logs
4. Evidence: headend-proof-north-tonawanda-2026-07.md (in release notes)

### Path to rung 5 (Field-proven)

1. Duration: 24h–7d unattended operation; no operator intervention
2. Failure recovery: reboot, clock drift, encoder crash, failed input — box recovers
3. Operator attestation: signs off on uptime + recovery
4. Evidence: field-proof-attestation-{station}-{date-range}.md + health log

---

## 8. Test plan

### Unit / API / E2E (contract tier)

**What:** every rung-0 capability has tests proving the code works + correct Pydantic models.

**Tests added in S10:**
- tests/proof/test_models.py — proof rung model validation
- tests/api/test_proof_endpoints.py — GET /api/v1/proof/* endpoints
- tests/cli/test_doctor_proof.py — civiccast doctor --proof output
- tests/ui/test_proof_status_screen.py — Proof Status screen

**Audit expectation:** 0 failing tests; 0 overclaimed capability rungs.

### Lab soak (machine tier)

**What:** 24h unattended soak; advances automation + headend profiles to rung 2.

**Soak parameters:**
- Three-channel playout
- One channel with Comcast MTD-HD headend profile
- Synthetic live EAS + caption events every 6h
- Reboot at 12h; unclean-restart relay reap test at 20h
- Health event log collected; TSDuck compliance probe every 2h
- Headless operation

**Pass criteria:**
- Zero off-air events
- Relay reap succeeds on unclean restart (S9 ENG-003)
- All scheduled playout items run
- Headend TS passes TR 101 290 P1
- No orphaned processes

**Evidence:** soak-{date}-24h.md with log excerpts.

### SDI hardware gate (rung 3)

**What:** capture physical SDI from DeckLink; decode and validate.

**Hardware:** DeckLink Mini Recorder (under $400) + test playout loop.

**Steps:**
1. Start playout (10min loop; includes caption cues)
2. Connect SDI output to DeckLink input; ffmpeg captures to capture.ts
3. Stop after 2 loops (20min recorded)
4. ffprobe -show_data + tsduck --analyze on capture.ts
5. Compare captions: decode capture.ts → verify CEA-708 cues match
6. Verify audio/video bitrate, GOP timing, no packet loss

**Evidence:** photo + capture.ts hash + probe output → S1-sdi-proof-{date}.md.

### Headend acceptance gate (rung 4)

**What:** deploy to real PEG station; operator schedules; live playout runs on real headend.

**Pre-deployment checklist:**
- Headend profile selected
- Headend address + port configured
- TS generation test passes (TSDuck probe)
- Operator trained on: schedule commit, emergency takeover, alert acknowledge
- Backup restore tested

**During deployment:**
- Operator runs playout schedule
- CivicCast calls for help if off-air (S8 alerting)
- Operator does one emergency takeover + returns
- Headend engineer monitors ingest; confirms no TR 101 290 violations

**Evidence:** operator + engineer sign-off → headend-proof-{station}-{date}.md.

### Field deployment gate (rung 5)

**What:** station runs unattended 24h–7d; recovers from failures without manual work.

**Failure injection:**
- Graceful reboot
- Network interruption (headend unreachable 5min)
- Full disk scenario
- Encoder crash (ffmpeg kill)
- Clock drift (NTP off)

**Box must:**
- Stay on-air through all failures
- Alert operator via email/SMS/webhook (S8)
- Log events with timestamps + recovery action
- Present "safe-to-air" status in console

**Evidence:** health log + operator attestation.

### Audit expectation

**Result:** 0/0/0/0/0.

- 0 overclaimed rungs (proof >= stated rung)
- 0 missing evidence links (every rung > 0 has evidence_reference)
- 0 ambiguous not_claimed notes
- 0 contradictions (CAPABILITIES.json matches code)
- 0 app-store / legal / hardware / cloud claims without rung 3+ evidence

---

## 9. DONE criteria (what "shipped" means for S10)

1. **CAPABILITIES.md + CAPABILITIES.json** exist:
   - All ~50 capabilities listed with current_rung + evidence_reference + proof_boundaries
   - Evidence links are correct
   - Can be generated from code + master spec

2. **civiccast/proof/models.py** defines:
   - ProofRung enum (contract / lab / machine / sdi / headend / field)
   - ProofBoundary dataclass
   - CapabilityProof Pydantic model
   - Validation: no overclaiming

3. **API endpoints live:**
   - GET `/api/v1/proof/capabilities` (full list + release gates)
   - GET `/api/v1/proof/capabilities/{id}` (one capability)
   - GET `/api/v1/proof/release-gates` (per-tier gate status)
   - Auth: read-only diagnostic surface; support_admin sufficient

4. **CLI surface:**
   - `civiccast doctor --proof` outputs per-category proof status
   - Correct formatting + human-readable output

5. **UI screen:**
   - SystemHealth > Proof Status tab renders CAPABILITIES
   - Filters by category, current_rung
   - Click-through to proof_boundaries + advancement_path
   - Phone-friendly (collapsible sections)

6. **Release gate checklist (automated):**
   - Contract: all tests pass
   - Lab: 24h soak complete + reboot success
   - Machine: clean Windows install + wizard completes
   - SDI/Headend/Field: manual gates with blocking_reason + unblock_step

7. **All 27 section specs** include:
   - Current rung statement
   - Advancement path
   - Honest boundary (not_claimed items)
   - Evidence links

8. **Documentation:**
   - CAPABILITIES.md linked from README.md
   - Release notes cite rung level (e.g., "egress-headend-comcast-mtd-hd [LAB]")
   - Certified-integrator marketing never claims rungs 0–1
   - No app-store / hardware / legal claims without rung 3+ evidence

9. **Tests pass:**
   - tests/proof/test_models.py
   - tests/api/test_proof_endpoints.py
   - tests/cli/test_doctor_proof.py
   - tests/ui/test_proof_status_screen.py
   - Audit: 0/0/0/0/0

---

## 10. Dependencies & cross-refs to other sections

### Hard dependencies (gates S10)

- S1 (Reference station): defines doctor output; S10 uses doctor as proof-status source
- S9 (Reliability): ENG-003 (relay reap) + process identity part of soak gate
- S3 (Commissioning wizard): headend/SDI proof steps added

### Soft dependencies (S10 used by)

- S2 (Headend): each HeadendProfile gets proof_rung field → CapabilityProof entry
- S4 (Playout core): commit-to-air gate is rung-1 feature
- S5 (Force Matrix): takeover = rung 0 (coded); wiring + audit = rung 1
- S6 (CG): render-only = rung 0; persistence = rung 0–1 after S6
- S8 (Alerting): alerting system is rung-1 gated; S10 surfaces in health
- S11 (Captions/EAS): CEA-708 decode-back = rung-1 gate; EAS = rung 0 (informational)
- S12 (OTT): app shells = rung 0 (contracts); store publication = rung 3+ (device lab)
- S13 (AI models): model selection = rung 0 (coded); UI surface = rung 1

### Open decisions for Scott

1. **DeckLink hardware procurement (S1, gate for rung 3):** confirm model + procurement path.
   *(Recommend: DeckLink Mini Recorder ~$400; Amazon / B&H within 1 week.)*

2. **First-station beta location:** North Tonawanda Public Access (tentative, July 2026)?
   Confirm station commitment + headend engineer contact.

3. **Certified-integrator contract (master §13):** separate legal doc or inline in CAPABILITIES.md?
   *(Recommend: separate certified-integrator-terms.md + link from CAPABILITIES index.)*

4. **Per-rung release gates — automation vs. manual:** should we add CI step that blocks merge unless automated gates pass?
   *(Recommend: yes, pre-merge check in GitHub Actions.)*

5. **CAPABILITIES regeneration:** hand-curated JSON or generated from code metadata?
   *(Recommend: semi-automated — model.py + section specs generate .json; human review for evidence links.)*

---

*S10 is the final operational spec document. Implementation does not begin until Scott approves all 27 section specs together. This section governs how every other section states its proof; it is not independent work.*
