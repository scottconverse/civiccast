# S8 — Health, Alerting, Support & Updates: The Operational Alerting Hub

> **Scope:** The push-based operational alerting layer for an unattended PEG box — alert rules, alert channels (email / SMS / webhook to the *operator*), alert events with dedupe and rate limiting, the runtime "safe-to-air" status, the QA-004 sink-health fix, a consolidated system-health dashboard, support-bundle export, and the update check/install/rollback/release-notes + daily/weekly self-test surfaces. **S8 is the routing hub** every other section hands operational conditions to.
>
> **Status: NOT STARTED — this is the NEXT build (master §10 step 4).** Every "what exists" claim is grounded to code; the alert-dispatch + runtime safe-to-air gate is what S8 builds. The S9 **restart-escalation proof event** (`egress-encoder-restart-escalation-*`, daemon `_append_restart_escalation_event`) is the upstream hook S8 turns into an operator alert.
>
> **Canonical-decision conformance (RECONCILIATION.md):** five real auth roles only (D1); single global alembic chain (D11); S8 owns the QA-004 `sink_connected`/`egress_state` semantics on `egress_health_samples`; S9 already shipped `schema_version`/`proof_events_appended` on that table in **`0038_reliability_fields`** (the current head — built first). **Migration-numbering reality (2026-06-14):** the planned `0042`/`0043` numbers are superseded by as-built assignment — S8's migration takes the **next free number after `0038`** (e.g. `0039_alerting_and_sinkhealth`), NOT `0042`. Proof ladder per master §5 (D7).

---

## 1. Goal & PEG automation rationale

An unattended single-PC PEG station has one non-negotiable obligation the prior build does not meet: **when the box goes off-air with nobody watching, it must call for help.** Today CivicCast collects rich health telemetry but exposes it **pull-only** — an operator has to open the console and look. A station that runs 24/7 from a closet cannot be babysat. The master spec lists this as gap #2 (master §4 item 2) and a station-acceptance gate ("the box calls for help when it goes off-air unattended", master §12).

**What incumbent PEG platform does (sourced, master §2.1):** an incumbent PEG automation platform surfaces health/status and emails operators on failures; REFLECT+ adds cloud telemetry/Audience Measurement. The functional bar S8 must clear is **proactive push notification of operational failure to a human**, plus a single consolidated health view, a support/diagnostic export, and a safe update path. the incumbent PEG platform is appliance hardware, so its health story is largely "the box is the box"; ours is software on a commodity PC, so S8 also owns the **system-resource** view (CPU/RAM/GPU/disk/clock/db/service) that an appliance vendor handles in firmware.

**The five things this section delivers:**
1. **Push operational alerting** — email / SMS / webhook to the **operator** (sharply distinct from `subscribe/`, which notifies *residents* of new VOD; see §1.1). Net-new.
2. **Runtime "safe-to-air" status** — promote the install-time `SafeToBroadcastContract` (a pre-meeting readiness gate, `installer/service.py:541`) into a **continuous** runtime signal driven by live egress state + the QA-004 fix.
3. **The QA-004 fix** — `build_default_sink_health` reports `sink_connected=false` on a perfectly healthy UDP sink that is idling on slate, because slate encoders emit no parseable fps/bitrate. We require encoder progress **only when `state == ON_AIR`**; idling-on-slate is healthy.
4. **System-health dashboard + support bundle** — consolidate egress health, continuity, and net-new system-resource/self-test samples into one operator view, and export a redacted support bundle (extend `create_diagnostic_bundle`, `installer/service.py:2139`).
5. **Updates + self-test** — update check/install/rollback/release-notes (extend the existing `UpdateRollbackStatus` machinery, `installer/service.py:1005`) plus a daily self-test and a weekly deeper validation.

### 1.1 CONTRAST: `subscribe/` is residents, S8 is operators (not a dependency, an explicit non-overlap)

`civiccast/subscribe/` is a **resident VOD notification** system: double-opt-in email + webhook subscriptions to a channel or meeting body, dispatched when a recording publishes (`subscribe/models.py:100` `NotificationPayload` → asset_id/title/portal_url/summary). It encrypts subscriber PII (`subscribe/crypto.py`), rate-limits public signup (`subscribe/rate_limit.py`), and retries failed webhooks with bounded exponential backoff + dead-letter (`subscribe/retry_worker.py:143` `WebhookRetryWorker`, `backoff_seconds=120` doubling).

**S8 alerting is the operator-facing mirror, and it is a separate stack:**

| Axis | `subscribe/` (residents) | S8 alerting (operators) |
|---|---|---|
| Audience | Public residents | Station staff / on-call operator |
| Trigger | A recording **publishes** | An **operational failure** (off-air, encoder death, …) |
| Recipients | Self-service double-opt-in, encrypted PII | Operator-configured channels, no double-opt-in (staff-owned) |
| Auth to manage | Resident links (tokens) | `setup_admin` / `support_admin` (D1) |
| Payload | VOD metadata (title, portal URL, summary) | Severity + condition + channel + affected resource + next step |
| Dedupe | Per-subscription | **Per (rule, resource) with notify-on-first-failure** (§6.3) |

S8 **reuses the delivery *patterns*** proven in `subscribe/` (signed webhook, bounded-backoff retry, dead-letter, PII-careful storage) but does **not** route operator alerts through resident subscriptions, and does not import resident subscription tables. The webhook signing + retry helpers are the one piece of shared lineage; S8 implements its own channel store so an operator alert can never leak onto a resident feed and vice versa. **Open decision OD-7** asks whether to physically share the webhook-signing helper module or copy it.

---

## 2. Current state (file:line)

| Capability | Where | Status (master §5 rung) | Gap |
|---|---|---|---|
| Egress health telemetry (event-driven) | `egress/health.py`; `EgressHealthSample` `egress/models.py:359`; DB row `egress/models.py:238` | rung 1 (lab; shipped) | **Pull-only — no push.** |
| `build_default_sink_health` (QA-004 bug) | `egress/health.py:64-91` | rung 1 | **`sink_connected=false` on healthy idling UDP sink** — `metrics_available` is False on slate, so UDP defaults to `udp_ok=True`, BUT any stale fps=0/bitrate=0 line makes `metrics_available=True` and `encoder_has_progress` returns False, flipping a clean sink to "disconnected." Not state-aware. |
| `encoder_has_progress` | `egress/health.py:94-102` | rung 1 | Correct primitive; just applied unconditionally instead of only when `ON_AIR`. |
| FileSink/SRT continuity proof | `egress/continuity.py:93,198` | rung 1 (boundary-declared) | Evidence source for the dashboard; not surfaced as a continuous signal. |
| Egress soak evidence contract | `egress/soak.py:94,141` | rung 1→2 (24h soak in flight) | Soak produces `EgressSoakResult.checks`; S8 self-test reuses the harness shape, doesn't weaken the 6h gate. |
| `EgressState` enum (8 states) | `egress/models.py:217` (`STOPPED…ERROR`) | shipped | The state machine S8 watches for off-air/encoder-death/server-crash. |
| `ChannelAutomationRollup` (CA-4) | `egress/models.py` (`automated/on_air/on_slate/dark`) | shipped | The dashboard's at-a-glance channel rollup; also an alert input (`dark` ⇒ off-air). |
| Install-time `SafeToBroadcastContract` | `installer/service.py:541-656`; models `installer/models.py:362-388` | rung 1 (shipped, pre-meeting) | **Install-time only.** Promote to a continuous runtime status. |
| `build_system_health_report` | `installer/service.py:2233-2317` | rung 1 | Readiness checks (setup, storage, backup, live source, portal, policy, providers, channel automation, headend). **No system-resource, no alert state, no continuity, no self-test.** |
| `/system-health`, `/safe-to-broadcast` endpoints | `installer/router.py:225-269` | shipped | Pull-only; staff-gated. Extend with runtime + resource + alert state. |
| Support bundle (`create_diagnostic_bundle`) | `installer/service.py:2139-2209`; `/support-bundle` `installer/router.py:581` | rung 1 | Redacted JSON (version, platform, env presence, setup, storage, backup/restore/update, providers, source, system_health). **Add: recent alert events, egress health window, proof-event window, self-test history, system-resource samples.** |
| Update / rollback machinery | `installer/service.py:1005-1505+` (`build_update_rollback_status`, preflight, maintenance window, rollback artifact, rollback rehearsal, failed-update rehearsal) | rung 1 | **Strong already.** S8 adds: scheduled hourly **check**, **release-notes** surface, and wires update events into alerting. |
| Resident notification delivery (CONTRAST) | `subscribe/delivery.py`, `subscribe/webhook.py`, `subscribe/smtp.py`, `subscribe/retry_worker.py` | rung 1 | Pattern source only (§1.1). |
| **AlertRule / AlertChannel / AlertEvent / AlertEventDelivery** | — | **net-new** | The whole push layer. |
| **RuntimeSafeToAirStatus** | — | **net-new** | Continuous runtime signal. |
| **ChannelRuntimeStatus** | — | **net-new** | Per-channel runtime snapshot feeding the dashboard + alert evaluator. |
| **SystemResourceSample** | — | **net-new** | CPU/RAM/GPU/disk/clock host metrics. |
| **SystemSelfTest** | — | **net-new** | Daily + weekly self-test records. |

---

## 3. Entities / data model & migrations

All net-new entities are pydantic contracts plus SQLAlchemy rows. Persisted tables are added by **`0039_alerting_and_sinkhealth`** (single global chain; it follows `0038_reliability_fields`, as the canonical-decision note above records). RuntimeSafeToAirStatus and ChannelRuntimeStatus are **computed/ephemeral** (returned by API, optionally snapshotted into a sample table — see §3.7).

### 3.1 Enums

```python
AlertSeverity = Literal["critical", "warning", "info"]
# critical = the box is off-air or will be imminently; page someone now.
# warning  = degraded / a guardrail tripped; needs attention this shift.
# info      = an event worth a record (update applied, self-test passed).

AlertConditionKind = Literal[
    "off-air",                 # S8 (egress state) — a channel that should be ON_AIR is not
    "encoder-death",           # S8 (egress) — encoder process exited / ERROR with no clean stop
    "server-crash",            # S8 (self/boot) — app restarted without a clean shutdown marker
    "schema-drift",            # S9 — running code schema_version != persisted (S9 0043 field)
    "relay-blocked",           # S9 — SDI/NDI relay stuck in backoff (ENG-003 orphan card hold)
    "compliance-probe-fail",   # S2 — TSDuck TR 101 290 priority-1 drift on a cable sink
    "missing-media",           # S7 — a scheduled item's media is absent < 5 min before air
    "commit-failure",          # S4 — commit-to-air validation/dispatch failed
    "takeover-stuck-2h",       # S5 — a live takeover has held the channel > 2h without handback
    "ai-runtime-down",         # S13 — Ollama / model runtime unreachable for a needed feature
]

AlertChannelKind = Literal["email", "sms", "webhook"]
AlertDeliveryStatus = Literal["sent", "failed", "suppressed", "dead_letter"]
AlertEventState = Literal["firing", "resolved"]
SelfTestKind = Literal["daily", "weekly"]
SelfTestStatus = Literal["pass", "warn", "fail"]
SafeToAirColor = Literal["green", "yellow", "red"]  # reuses the SafeToBroadcastColor vocabulary
```

`AlertConditionKind` is the **canonical registry** of every operational condition any section may route to S8. Adding a new condition is a one-line enum change + a default row in the rule seed (§6.2); **sections do not invent their own alert plumbing** — they call `record_alert_condition(kind=…, resource=…, detail=…)` (§4) and S8 owns rule match, dedupe, severity, and delivery.

### 3.2 `AlertRule`

Operator-tunable policy mapping a condition to a severity + a channel + a dedupe window. Seeded with the defaults in §6.2; editable.

```python
class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    condition: AlertConditionKind
    enabled: bool = True
    severity: AlertSeverity
    channel_ids: Annotated[list[str], Field(min_length=0)]   # AlertChannel.channel_id refs
    # Dedupe / rate-limit: notify on FIRST failure, then suppress repeats for
    # this resource until either it RESOLVES or re_alert_after elapses.
    dedupe_window_seconds: Annotated[int, Field(ge=0, le=86_400)] = 900
    re_alert_after_seconds: Annotated[int, Field(ge=0, le=604_800)] = 3600
    # Optional scope: limit a rule to one channel_id; None = all channels.
    scope_channel_id: Annotated[str | None, Field(default=None, max_length=80)] = None
    notify_on_resolve: bool = True
    updated_at: datetime
    updated_by: Annotated[str, Field(min_length=1, max_length=120)]
```

### 3.3 `AlertChannel`

A push destination for the **operator**. Secret material (SMTP creds, SMS API key, webhook secret) is **not** stored in this contract — it is referenced by handle and kept in the same local credential store the installer uses (`_PROVIDER_CREDENTIAL_FIELDS` pattern, `installer/service.py:171`); the contract carries only redacted metadata, exactly like `ProviderCredentialSetupResponse`.

```python
class AlertChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: Annotated[str, Field(min_length=1, max_length=120)]   # NB: alert-channel id, NOT egress channel
    kind: AlertChannelKind
    label: Annotated[str, Field(min_length=1, max_length=120)]
    enabled: bool = True
    # email: comma-free single recipient or distribution list address(es), redacted in reads
    # sms:   E.164 number(s), redacted to last 4 in reads
    # webhook: HTTPS URL (https only; loopback allowed for tests), secret by handle
    target_redacted: Annotated[str, Field(min_length=1, max_length=200)]
    credential_handle: Annotated[str | None, Field(default=None, max_length=200)] = None
    # quiet hours: critical alerts ALWAYS send; warning/info hold until window closes (OD-2)
    quiet_hours_start_utc: Annotated[str | None, Field(default=None, max_length=5)] = None  # "22:00" UTC
    quiet_hours_end_utc: Annotated[str | None, Field(default=None, max_length=5)] = None    # "07:00" UTC
    last_delivery_status: AlertDeliveryStatus | None = None
    last_delivery_at: datetime | None = None
    created_at: datetime
```

### 3.4 `AlertEvent`

One fired alert for one (rule, resource). Append-only; carries firing→resolved lifecycle and the dedupe key.

```python
class AlertEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Annotated[str, Field(min_length=1, max_length=120)]
    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    condition: AlertConditionKind
    severity: AlertSeverity
    state: AlertEventState                       # firing | resolved
    # dedupe_key = f"{condition}:{resource_ref}" — the unit of notify-on-first-failure
    resource_ref: Annotated[str, Field(min_length=1, max_length=200)]  # e.g. egress channel_id, sink label
    summary: Annotated[str, Field(min_length=1, max_length=300)]
    detail: Annotated[str, Field(default="", max_length=2000)]
    source_section: Annotated[str, Field(min_length=2, max_length=8)]  # "S2".."S13" or "S8"
    first_observed_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None = None
    occurrence_count: Annotated[int, Field(ge=1)] = 1   # suppressed repeats counted here
    acknowledged_at: datetime | None = None
    acknowledged_by: Annotated[str | None, Field(default=None, max_length=120)] = None
```

### 3.5 `AlertEventDelivery`

Proof that S8 attempted to notify a human, mirroring `subscribe/`'s `NotificationDelivery` (`subscribe/models.py:113`).

```python
class AlertEventDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_id: Annotated[str, Field(min_length=1, max_length=120)]
    event_id: Annotated[str, Field(min_length=1, max_length=120)]
    alert_channel_id: Annotated[str, Field(min_length=1, max_length=120)]
    kind: AlertChannelKind
    status: AlertDeliveryStatus                  # sent | failed | suppressed | dead_letter
    attempts: Annotated[int, Field(ge=0)] = 0
    next_attempt_at: datetime | None = None      # bounded exp backoff like WebhookRetryWorker
    last_error: Annotated[str, Field(default="", max_length=1000)] = ""
    signature: Annotated[str | None, Field(default=None, max_length=200)] = None  # webhook HMAC
    dispatched_at: datetime
```

### 3.6 `RuntimeSafeToAirStatus` (computed, continuous)

The install-time `SafeToBroadcastContract` answers "can I start a meeting?"; this answers **"is the box on-air and healthy right now?"** every few seconds.

```python
class RuntimeSafeToAirStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: datetime
    color: SafeToAirColor                        # green=on-air & clean, yellow=degraded, red=off-air
    label: Annotated[str, Field(min_length=1, max_length=80)]
    operator_message: Annotated[str, Field(min_length=1)]
    channels: list[ChannelRuntimeStatus]
    active_critical_alerts: Annotated[int, Field(ge=0)]
    active_warning_alerts: Annotated[int, Field(ge=0)]
    # green only if EVERY auto_start channel that should be ON_AIR is ON_AIR (or
    # cleanly on healthy slate) AND no critical alert is firing.
```

### 3.7 `ChannelRuntimeStatus` (computed, per-channel)

```python
class ChannelRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: Annotated[str, Field(min_length=1, max_length=80)]
    egress_state: EgressState                    # from EgressStateRow
    sink_health: dict[str, bool]                 # POST-QA-004 (state-aware, §6.1)
    on_air: bool
    on_healthy_slate: bool                       # idling on slate is healthy, not off-air
    encoder_fps: float | None = None
    encoder_bitrate_kbps: float | None = None
    last_loudness_lufs: float | None = None
    seconds_in_state: Annotated[int, Field(ge=0)] = 0
    last_proof_event_id: Annotated[str | None, Field(default=None, max_length=120)] = None
    color: SafeToAirColor
```

### 3.8 `SystemResourceSample` (persisted)

Host metrics an appliance vendor hides in firmware; we own them.

```python
class SystemResourceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: Annotated[int | None, Field(default=None, ge=1)] = None
    sampled_at: datetime
    cpu_percent: Annotated[float | None, Field(default=None, ge=0, le=100)] = None
    ram_used_gb: Annotated[float | None, Field(default=None, ge=0)] = None
    ram_total_gb: Annotated[float | None, Field(default=None, ge=0)] = None
    gpu_percent: Annotated[float | None, Field(default=None, ge=0, le=100)] = None
    gpu_vram_used_gb: Annotated[float | None, Field(default=None, ge=0)] = None
    media_volume_free_gb: Annotated[float | None, Field(default=None, ge=0)] = None
    backup_volume_free_gb: Annotated[float | None, Field(default=None, ge=0)] = None
    db_reachable: bool = True
    backup_volume_writable: bool = True
    service_running: bool = True                 # the egress daemon / service unit is up
    clock_skew_seconds: Annotated[float | None, Field(default=None)] = None  # NTP/host drift
```

### 3.9 `SystemSelfTest` (persisted)

```python
class SystemSelfTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    self_test_id: Annotated[str, Field(min_length=1, max_length=120)]
    kind: SelfTestKind                           # daily | weekly
    started_at: datetime
    finished_at: datetime | None = None
    status: SelfTestStatus                       # pass | warn | fail
    checks: dict[str, bool]                      # named subcheck → pass
    summary: Annotated[str, Field(min_length=1, max_length=600)]
    evidence_path: Annotated[str | None, Field(default=None, max_length=500)] = None
```

### 3.10 The QA-004 health-sample fix (co-edit of `egress_health_samples`)

`EgressHealthSample` (`egress/models.py:359`) already has `sink_connected: dict[str,bool]` and `state: EgressState`. The DB row (`egress/models.py:238`) stores `sink_connected_json` + `state`. **S8 owns the *semantics*** of these two fields (state-aware sink health, §6.1); the columns already exist, so `0042` does **not** add `sink_connected`/`egress_state` columns to that table — it formalizes their meaning and adds whatever is needed for the alert-event tables. The historical phrasing "QA-004 adds `sink_connected`/`egress_state` on `egress_health_samples`" (RECONCILIATION D11 / migration table) is reconciled here: the fields exist; `0042` carries the **fix** (the corrected write path + any backfill/index) and the alerting tables; S9's `0043` adds `schema_version`/`proof_events_appended` to the *same* table and **must be sequenced after `0042`** to avoid a chain conflict.

> **CO-EDIT NOTE (binding, D11):** `0042` (S8) and `0043` (S9) both touch `egress_health_samples`. `0042` lands first (alert tables + QA-004 write-path correction); `0043` adds `schema_version`/`proof_events_appended` and **depends on `0042`** as its `down_revision`. Neither migration may be authored as a per-package head — single global chain, head pin in `tests/live/test_real_postgres.py` advances to `0043` after both merge. S9's spec carries the matching note.

### 3.11 `0042_alerting_and_sinkhealth` — what it adds

- `alert_rules` (rule_id PK, condition, severity, channel_ids JSON, dedupe/re-alert windows, scope_channel_id, enabled, notify_on_resolve, updated_at/by).
- `alert_channels` (channel_id PK, kind, label, target_redacted, credential_handle, quiet-hours, last_delivery_*, created_at). **No secret values in-row.**
- `alert_events` (event_id PK, rule_id, condition, severity, state, resource_ref, dedupe key indexed `(condition, resource_ref, state)`, summary/detail, source_section, first/last_observed_at, resolved_at, occurrence_count, acknowledged_*).
- `alert_event_deliveries` (delivery_id PK, event_id FK, alert_channel_id, kind, status, attempts, next_attempt_at, last_error, signature, dispatched_at).
- `system_resource_samples` (sample_id PK autoincrement, sampled_at indexed, all metric columns nullable).
- `system_self_tests` (self_test_id PK, kind, started/finished_at, status, checks JSON, summary, evidence_path).
- **QA-004:** no schema change to `egress_health_samples` columns; migration includes the corrected sink-health computation shipping with the code and (optional) a one-time note row — see §6.1. The migration is **idempotent** and additive; existing rows untouched.

---

## 4. API surface

All staff endpoints use `require_any_role(...)` (`auth/roles.py:60`) with the **five real roles only** (D1). Alert configuration is sensitive (it controls who gets paged) ⇒ `setup_admin`; read-only diagnostics ⇒ `support_admin`; the runtime safe-to-air read is broad (any operator who runs a meeting needs it).

### 4.1 Public / runtime read (broad operator read)

- `GET /api/staff/runtime-safe-to-air` → `RuntimeSafeToAirStatus`. Roles: any of the five. Cached server-side ~3–5s (OD-3) so a dashboard polling at 1Hz never stampedes the egress store. *No public/unauthenticated variant* — runtime channel state is operational, not resident-facing.

### 4.2 System health & resources (read-only diagnostics)

- `GET /api/staff/system-health` → extended `SystemHealthReport` (now includes runtime safe-to-air, system-resource summary, recent alert counts, last self-test). Roles: `support_admin`, `setup_admin`, `meeting_operator`. (Extends `installer/router.py:225`.)
- `GET /api/staff/system-resources?window_minutes=` → `list[SystemResourceSample]`. Roles: `support_admin`, `setup_admin`.
- `GET /api/staff/egress-health?channel_id=&window_minutes=` → `list[EgressHealthSample]` (reuses existing health store; surfaced here for the dashboard). Roles: `support_admin`, `setup_admin`, `meeting_operator`.

### 4.3 Alert configuration (sensitive)

- `GET /api/staff/alert-rules` → `list[AlertRule]`. Roles: `setup_admin`, `support_admin`.
- `PUT /api/staff/alert-rules/{rule_id}` (severity, enabled, channel_ids, dedupe/re-alert, scope, notify_on_resolve). Roles: `setup_admin`.
- `POST /api/staff/alert-channels` / `GET /api/staff/alert-channels` / `PUT /api/staff/alert-channels/{channel_id}` / `DELETE …`. Secrets supplied to the credential store, **never returned**; reads are redacted. Roles: `setup_admin`.
- `POST /api/staff/alert-channels/{channel_id}/test` → sends a synthetic `info` alert to that channel and returns the `AlertEventDelivery`. Roles: `setup_admin`. (The "does my pager actually work" button.)

### 4.4 Alert events (read + acknowledge)

- `GET /api/staff/alert-events?state=&severity=&since=` → `list[AlertEvent]`. Roles: `support_admin`, `setup_admin`, `meeting_operator`.
- `GET /api/staff/alert-events/{event_id}/deliveries` → `list[AlertEventDelivery]`. Roles: `support_admin`, `setup_admin`.
- `POST /api/staff/alert-events/{event_id}/acknowledge` → marks `acknowledged_*`; **does not** resolve (the condition resolves itself, §6.3). Roles: `meeting_operator`, `support_admin`, `setup_admin`.

### 4.5 Internal condition-ingest (in-process, not a public route)

S8 exposes a single **in-process** entry point other sections call (no HTTP — it is a service function, the same way `build_system_health_report` is called from the router):

```python
def record_alert_condition(
    *, kind: AlertConditionKind, resource_ref: str, source_section: str,
    summary: str, detail: str = "", observed_at: datetime | None = None,
    resolved: bool = False,
) -> AlertEvent: ...
```

S2/S4/S5/S7/S9/S13 import and call this; they do not build alert rows themselves. `resolved=True` is how a section reports a condition cleared (e.g. relay un-blocked) so S8 can fire the resolve notification.

### 4.6 Support bundle & updates (extend existing)

- `POST /api/staff/support-bundle` → extended `DiagnosticBundleResponse` (now also bundles recent alert events/deliveries, an egress-health window, a proof-event window, self-test history, and system-resource samples). Roles: `support_admin`, `setup_admin`. (Extends `installer/router.py:581`.)
- `GET /api/staff/update-status` → `UpdateRollbackStatus` (existing). + `GET /api/staff/release-notes?version=` → release-notes text/markdown for the available version. Roles: `support_admin`, `setup_admin`.
- Existing update endpoints (preflight / maintenance window / rollback artifact / rollback rehearsal / failed-update rehearsal) keep their `setup_admin` gate; S8 adds the **hourly check scheduler** + emits an `info` alert when an update becomes available and a `critical`/`warning` alert if an applied update fails its post-update proof (§6.6).
- `GET /api/staff/self-tests?kind=&since=` → `list[SystemSelfTest]`; `POST /api/staff/self-tests/run?kind=daily|weekly` → run on demand. Roles: `support_admin`, `setup_admin`.

---

## 5. Operator UI surface

One **System Health & Alerting** console area (extends the existing System Health screen, master §0 `SystemHealthScreen`). Phone-first (master §0 "phone-first operation").

1. **Top banner — Runtime Safe-to-Air.** Big green/yellow/red from `RuntimeSafeToAirStatus`. Green: "On air — all channels healthy." Red: "OFF AIR — government channel down 4m12s." One tap → the offending channel's runtime card.
2. **Channel runtime cards.** One per channel: `EgressState`, on-air/on-slate, fps/bitrate/LUFS, sink-health chips (post-QA-004 — a slate-idling UDP sink shows **green "idle on slate"**, not red "disconnected"), seconds-in-state, last proof event link.
3. **Active alerts list.** Firing alerts first, sorted by severity; each row: condition, resource, age, occurrence_count ("+37 suppressed"), source section, Acknowledge button, and a deliveries drawer (which channels got it, sent/failed/dead-letter).
4. **System resources.** CPU/RAM/GPU/VRAM, media + backup free space, db reachable, service running, clock skew — sparkline over the selected window from `SystemResourceSample`.
5. **Alert configuration (gear, `setup_admin`).** Rules table (condition → severity → channels → dedupe window, toggle); channels table (add email/SMS/webhook, quiet hours, **Send test** button); the test button is the trust-builder.
6. **Self-test panel.** Last daily + last weekly result with pass/warn/fail and a "Run now" button; expandable subcheck list.
7. **Updates panel.** Current vs available version, release-notes link, the existing preflight → maintenance-window → apply → post-update-proof flow, and a visible rollback button with rollback-proof state.
8. **Support bundle.** "Export support bundle" → generates the redacted JSON, shows the path + sha256 + what's included/excluded (reuses `DiagnosticBundleResponse.contains`/`excludes` copy).

Every red/yellow item carries an **operator-actionable `next_step`** string (the codebase-wide pattern — `SystemHealthCheck.next_step`, `installer/models.py:374`).

---

## 6. Behavior / algorithms

### 6.1 The QA-004 fix — state-aware sink health (the headline correctness fix)

**Bug (today, `egress/health.py:64-91`):** for UDP/SPTS sinks, `build_default_sink_health` computes `udp_ok = encoder_has_progress(metrics) if metrics_available else True`. The intent was right (don't claim a failure you can't observe), but it is **not state-aware**: a channel idling on slate while `ON_AIR=false` (state `FALLBACK_SLATE` or a slate-only `STOPPED`-adjacent run) can still emit a stale `fps=0`/`bitrate=0` progress line, which makes `metrics_available=True` and `encoder_has_progress=False`, flipping a perfectly healthy fire-and-forget UDP sink to `sink_connected=false`. On a TSDuck-clean sink that reads false **for hours** (the QA-004 finding).

**Fix:** require encoder progress **only when `state == ON_AIR`**. Idling on a slate is a *healthy* state for a UDP sink, not a disconnection. New signature threads the state in:

```python
def build_default_sink_health(
    *, config: EgressConfig, metrics: EgressEncoderMetrics, state: EgressState,
) -> dict[str, bool]:
    require_progress = state == "ON_AIR"
    metrics_available = (
        metrics.encoder_fps is not None or metrics.encoder_bitrate_kbps is not None
    )
    if require_progress:
        # On air: a UDP sink is "connected" iff the encoder is actually moving media.
        udp_ok = encoder_has_progress(metrics) if metrics_available else False
    else:
        # Idling on slate / not on air: a fire-and-forget UDP sink has no far-end
        # to disprove. Healthy by default — we cannot and must not claim a failure.
        udp_ok = True
    health: dict[str, bool] = {}
    for sink in config.sinks:
        if sink.kind == "file":
            health[sink.label] = True; continue
        if sink.kind == "local-ts" and urlsplit(sink.uri).scheme.lower() == "file":
            health[sink.label] = True; continue
        # srt/rtmp/sdi still require a provider ack ⇒ conservative False (unchanged).
        health[sink.label] = False if sink.kind in {"srt", "rtmp", "sdi"} else udp_ok
    return health
```

The `ON_AIR`-with-no-metrics case now reads `False` (we *expect* progress and don't see it — that is a real, observable problem worth surfacing), while the **idle-on-slate** case reads `True`. Net effect: the dashboard chip is green "idle on slate" when slating and only red when an on-air channel genuinely stalls. The caller (`daemon.py` health sampler) passes `state=` from the live `EgressStateRow`. `encoder_has_progress` (`egress/health.py:94`) is unchanged.

### 6.2 Default alert-rule seed (severity / default channel / dedupe)

`0042` seeds one rule per condition. Defaults (operator-tunable):

| Condition | Source | Default severity | Default channel | Dedupe / re-alert | Notes |
|---|---|---|---|---|---|
| `off-air` | S8 (egress) | **critical** | SMS + email | first-failure; re-alert 1h | A should-be-ON_AIR channel not ON_AIR and not healthy-slate. The flagship "box went dark" page. |
| `encoder-death` | S8 (egress) | **critical** | SMS + email | first-failure; re-alert 1h | Encoder exited / state ERROR without a clean stop. |
| `server-crash` | S8 (self/boot) | **critical** | SMS + email | first-failure; re-alert 0 (one-shot per boot) | App started without a clean-shutdown marker. |
| `relay-blocked` | **S9** | **critical** | SMS + email | first-failure; re-alert 30m | SDI/NDI relay stuck in 5/15/60s backoff holding the card (ENG-003). |
| `missing-media` (<5min) | **S7** | **critical** | SMS + email | first-failure per item; re-alert 0 | Scheduled item's media absent <5 min before air — can't be fixed by re-paging, page once. |
| `commit-failure` | **S4** | **warning** | email | first-failure; re-alert 30m | Commit-to-air validation/dispatch failed; operator-driven, daytime. |
| `compliance-probe-fail` | **S2** | **warning** | email | first-failure; re-alert 1h | TSDuck TR 101 290 priority-1 drift on a cable sink. |
| `schema-drift` | **S9** | **warning** | email | first-failure; re-alert 6h | Running schema_version ≠ persisted; data-corruption risk. |
| `takeover-stuck-2h` | **S5** | **warning** | email + SMS | fires once at 2h; re-alert 2h | Live takeover held >2h without handback. |
| `ai-runtime-down` | **S13** | **info** | email | first-failure; re-alert 6h | Model runtime unreachable; captions/summary degrade but air continues. Lowest because it never takes the channel off-air. |

"Default channel" is a *kind* recommendation; the seed wires each rule to whatever channels of that kind the operator has configured (a rule with no live channel of its default kind logs a `suppressed` delivery so the gap is visible, never a silent drop).

### 6.3 Alert evaluation, dedupe, and rate-limit (notify-on-first-failure)

A lightweight **alert evaluator** runs inside the existing egress daemon loop (it already samples health every few seconds — `daemon.py` writes `EgressHealthSample`s) plus a periodic tick for system resources / self-test / update-check. Sections push other conditions via `record_alert_condition` (§4.5). Per evaluation:

1. **Derive conditions.** S8 self-derives `off-air`/`encoder-death`/`server-crash` from `EgressStateRow` + the post-QA-004 sink health + a clean-shutdown marker. Other conditions arrive via `record_alert_condition`.
2. **Match rules.** For each (condition, resource_ref), find enabled rules whose `scope_channel_id` matches (or is None).
3. **Dedupe — notify on FIRST failure, not every sample.** Compute `dedupe_key = f"{condition}:{resource_ref}"`. If a `firing` `AlertEvent` already exists for that key:
   - bump `occurrence_count`, update `last_observed_at`, **suppress** the notification (record an `AlertEventDelivery` with `status="suppressed"` only if you want the audit; otherwise just bump) **unless** `re_alert_after_seconds` has elapsed since the last *sent* delivery — then send a re-alert and reset the timer.
   - This is the explicit guard against "every sample fires an alert." A flapping source that writes ~900 proof events/hr (the ENG-007 churn shape S9 caps) produces **one** page, then a count, then at most one re-alert per `re_alert_after` window.
   If no firing event exists, create one (`state="firing"`, `occurrence_count=1`) and **send**.
4. **Resolve.** When the condition clears (S8 observes ON_AIR-and-healthy again, or a section calls `record_alert_condition(..., resolved=True)`), set `state="resolved"`, `resolved_at`, and if the rule's `notify_on_resolve`, send a single resolve notification ("government channel back on air after 6m"). A resolved key is eligible to fire fresh next time.
5. **Quiet hours.** `critical` ignores quiet hours (off-air always pages). `warning`/`info` during a channel's quiet window are **held** (queued, `next_attempt_at` = window end), then sent once when the window closes — never dropped (OD-2).

### 6.4 Delivery (reuse `subscribe/` patterns, separate stack)

- **email:** SMTP send (mirror `subscribe/smtp.py`); subject `[CivicCast {severity}] {condition} — {resource}`.
- **sms:** pluggable provider adapter (Twilio-shaped HTTP POST) keyed by `credential_handle`; body ≤140 chars with the next-step.
- **webhook:** HTTPS POST, **HMAC-signed** like `subscribe/webhook.py` (`AlertEventDelivery.signature`); JSON body = the `AlertEvent` plus station identity.
- **Retry:** bounded exponential backoff + dead-letter, mirroring `WebhookRetryWorker` (`subscribe/retry_worker.py:143`, `backoff_seconds=120`, doubling, `dead_letter` terminal). A `dead_letter` delivery is itself surfaced as a `warning` on the dashboard ("could not reach pager X") — failure to alert must not be silent.
- **No PII leakage across stacks:** operator alert tables never reference resident subscription rows; resident dispatch never reads alert channels.

### 6.5 Runtime safe-to-air computation (§3.6/§3.7)

Every read (cached ~3–5s): for each `auto_start` channel, build `ChannelRuntimeStatus` from `EgressStateRow` + post-QA-004 sink health + latest `EgressHealthSample`. `on_healthy_slate` = state is a slate state and UDP sinks read healthy. A channel is **red** if it should be ON_AIR and is neither ON_AIR nor on healthy slate; **yellow** if degraded (e.g. dropped frames climbing, loudness out of tolerance, one of several sinks down); **green** otherwise. Overall `color` = worst channel color, escalated to **red** if any `critical` alert is firing. This reuses the `ChannelAutomationRollup` `dark` list (`egress/models.py`) as the off-air seed and the existing `SafeToBroadcastColor` vocabulary so the UI shares the install-time color semantics.

### 6.6 System resources, self-test, updates

- **Resource sampling:** a periodic tick (e.g. every 60s) writes a `SystemResourceSample` via `psutil`-style probes already used by `installer/platform.py` (CPU/RAM/GPU/VRAM), `installer/storage.py` `durable_storage_status` (free space, writability), a db-reachability ping, a service-up check (the egress service unit, `egress/service_unit.py`), and an NTP/host clock-skew read. Threshold breaches (disk <X GB, clock skew >Ys, db unreachable, service down) feed `record_alert_condition` (mapped to `server-crash`/dedicated resource conditions; resource thresholds default `warning`, db-down/service-down `critical`).
- **Daily self-test (default 02:00 local, OD-4):** runs the install-time readiness path (`build_system_health_report`), a short FileSink continuity proof (`egress/continuity.py:run_filesink_continuity_proof`) against a synthetic plan at the declared boundary, a backup write/read/delete probe (existing `build_backup_status`), and a model-runtime ping. Writes a `SystemSelfTest(kind="daily")`. A `fail` raises a `warning` alert.
- **Weekly deeper validation (default Sun 03:00 local, OD-5):** the daily set plus a restore rehearsal (`run_restore_rehearsal`), an SRT receiver continuity proof (`run_srt_receiver_continuity_proof`), a TSDuck compliance probe on cable sinks (S2), and an alert-channel **test send** to confirm the pager still works. Writes `SystemSelfTest(kind="weekly")`.
- **Updates:** an **hourly check** reads the available version (existing `build_update_rollback_status`, env/`CIVICCAST_AVAILABLE_VERSION`); when one appears, emit an `info` alert + surface release-notes (`GET …/release-notes`). The existing preflight → maintenance-window → apply → post-update-proof → rollback machinery (`installer/service.py:1005-1505+`) is unchanged; S8 wires a **failed post-update proof** to a `critical` alert and the failed-update rollback rehearsal result to the dashboard. **No silent auto-install** (OD-6) — staff opens the maintenance window.

### 6.7 Server-crash / clean-shutdown marker

On graceful stop the service writes a clean-shutdown marker (timestamp + version) to the ops-state file (`installer/station_state.py` lineage). On boot, if the marker is absent or stale while channels were `auto_start`, S8 fires a one-shot `server-crash` `critical` alert ("CivicCast restarted unexpectedly; channels are recovering"). This is the "the box rebooted at 3am and you should know" signal; it pairs with S9's relay-reap-on-boot (a `server-crash` + a `relay-blocked` together tell the operator the unclean-restart path is exercising).

---

## 7. Proof tier: current rung + how to advance it

Per master §5 (D7). S8 is **net-new**, so it starts at **rung 0 (contract-tested)** and advances with the box's own soak.

- **Rung 0 — Contract-tested:** all entities, the rule/channel/event store, the evaluator (dedupe/rate-limit/resolve), the QA-004 fix, the runtime safe-to-air computation, the resource/self-test samplers, and the API are covered by unit/API/UI tests with no real egress. The QA-004 fix has a dedicated regression: a slate-idling UDP sink with a stale `fps=0` line reads **healthy**, and an `ON_AIR` sink with no progress reads **unhealthy**.
- **Rung 1 — Lab-proven:** real email/SMS/webhook **delivery** proven against loopback / a local SMTP sink / a localhost webhook receiver at a declared `proof_boundary` (`civiccast-operator-alert-delivery-boundary`); the daily self-test runs a real FileSink continuity proof. A real fired alert from an induced off-air is delivered and acknowledged.
- **Rung 2 — Machine-proven:** the **existing 24h three-channel soak** (in flight, master §0) is extended to **induce** off-air / encoder-death / relay-blocked / unclean-restart and assert that (a) the correct alert fires exactly once with the right severity, (b) repeats are deduped, (c) resolve fires on recovery, (d) the QA-004 chip never false-flags during the slate stretches, and (e) a `SystemSelfTest(daily)` is recorded during the run. This is how S8 reaches the same rung as automation.
- Rungs 3–5 (SDI / headend / field) are not S8-specific; S8 rides the box's overall rung and adds the "did it page on real failure" evidence at each.

**Claim boundary:** S8 never claims an alert was *received and read* — only that delivery was *attempted/sent* (`AlertEventDelivery.status`), exactly as `subscribe/`'s `NotificationDelivery` is "proof a notification was attempted." No "guaranteed paging."

---

## 8. Test plan (unit/API/e2e + soak gate) and the 0/0/0/0/0 audit

**Unit**
- QA-004 truth table: `{state ∈ {ON_AIR, FALLBACK_SLATE, STOPPED, ERROR}} × {metrics: none, fps0/br0, fps30/br8000}` → expected sink-health per kind (file/local-ts/udp/srt/rtmp/sdi). Lock the regression that today flips healthy→false.
- Evaluator dedupe: N consecutive failing samples ⇒ **one** firing event, `occurrence_count==N`, **one** sent delivery (until `re_alert_after`); re-alert fires exactly once per window.
- Resolve path: condition clears ⇒ `state=resolved`, one resolve delivery iff `notify_on_resolve`; next failure fires fresh.
- Quiet hours: `critical` sends inside the window; `warning`/`info` held and sent at window close, never dropped.
- Severity/channel mapping from the §6.2 seed; rule edit changes routing.
- Runtime safe-to-air color: worst-channel + critical-escalation logic; idle-on-slate ⇒ green not red.
- Delivery retry/backoff/dead-letter mirrors `WebhookRetryWorker` semantics; dead-letter surfaces a `warning`.
- Resource thresholds map to the right condition + severity; clock-skew/db-down/service-down.
- Self-test pass/warn/fail classification (daily vs weekly subcheck sets).

**API**
- Every endpoint enforces its exact role set (D1); negative tests for each (e.g. `meeting_operator` cannot edit alert rules; `support_admin` can read but not configure channels).
- Alert-channel reads are **redacted** (no secret value, SMS last-4 only); secrets go to the credential store.
- `…/alert-channels/{id}/test` produces a real `AlertEventDelivery`.
- Support bundle includes the new sections and still **excludes** tokens/passwords/keys/provider-creds/subscriber-data/raw-logs (assert on `excludes`).

**E2E / Playwright** (master §12 walkthrough): configure a webhook channel → induce off-air → see the red banner → see the firing alert with a `sent` delivery in the drawer → acknowledge → recover → see resolve. Run the daily self-test from the panel. Export a support bundle and verify the path/sha256/contents card.

**Soak gate** (rung 2, §7): the extended 24h soak asserts fire-once / dedupe / resolve / no QA-004 false-flag / a recorded daily self-test, reusing `egress/soak.py` evidence shape **without weakening the six-hour gate** (`evaluate_egress_soak_observation`).

**0/0/0/0/0 audit (Blocker/Major/Minor/Nit/Test-gap all zero — MEMORY: fix-all-severities):** every audit on this section reaches **0/0/0/0/0**. No correctness bug (the QA-004 regression must stay green), no lint, no perf regression (the evaluator must not add measurable per-sample cost — it runs inside the existing health-sample loop and the safe-to-air read is cached), no skipped test, no missing assertion. Blockers-of-note to assert against: (a) an alert that fires every sample instead of once; (b) the QA-004 chip false-flagging on slate; (c) a `critical` suppressed by quiet hours; (d) a secret value appearing in any read or in the support bundle; (e) an operator alert leaking onto a resident subscription feed.

---

## 9. DONE criteria (shipped state)

1. `0042_alerting_and_sinkhealth` lands on the single global chain after `0041`, sequenced **before** S9's `0043`; head pin in `tests/live/test_real_postgres.py` advances; co-edit note honored.
2. QA-004 fixed: `build_default_sink_health` is state-aware; the regression test proves idle-on-slate UDP reads healthy and on-air-no-progress reads unhealthy; the dashboard chip text reflects "idle on slate."
3. All ten `AlertConditionKind`s are wired: S8 self-derives off-air/encoder-death/server-crash; S2/S4/S5/S7/S9/S13 each call `record_alert_condition` (the cross-section dependency is *consumed*, with each section's spec referencing this hub).
4. Email + SMS + webhook channels deliver to the **operator**, with dedupe/rate-limit (notify-on-first-failure), resolve notifications, quiet hours, and bounded-backoff retry + dead-letter; the "Send test" button works.
5. `RuntimeSafeToAirStatus` + `ChannelRuntimeStatus` drive a continuous runtime banner; the install-time `SafeToBroadcastContract` remains the pre-meeting gate (the two coexist, do not merge).
6. System-health dashboard consolidates egress health + continuity + system-resource + self-test + active alerts; support bundle export includes them and stays redacted.
7. Update check (hourly) + release-notes + the existing preflight/maintenance/apply/rollback flow are wired to alerting (info on available, critical on failed post-update proof); no silent auto-install.
8. Daily self-test (02:00) and weekly deeper validation (Sun 03:00) record `SystemSelfTest`s; a `fail` raises an alert.
9. Five real roles only on every endpoint (D1); secrets never returned; reads redacted.
10. Rung 2 reached via the extended soak; **0/0/0/0/0** audit.

---

## 10. Dependencies & cross-refs to other sections; open decisions for Scott

### 10.1 Cross-refs — S8 is the hub these sections route to

| Section | Hands S8 | S8 returns / owns |
|---|---|---|
| **S2** (headend) | `compliance-probe-fail` (TSDuck TR 101 290 drift) | warning alert; dashboard surfaces probe state |
| **S4** (commit-to-air) | `commit-failure` | warning alert |
| **S5** (force matrix) | `takeover-stuck-2h` (live takeover held >2h, no handback) | warning+SMS alert |
| **S7** (media lifecycle) | `missing-media` (<5 min before air) | critical alert (page once) |
| **S9** (reliability) | `schema-drift`, `relay-blocked` (ENG-003) | warning / critical alerts; **co-edits `egress_health_samples`** (D11) — `0043` sequenced after `0042` |
| **S13** (AI) | `ai-runtime-down` | info alert; air continues, captions/summary degrade |
| **S1/installer** | `StationBoxProfile`, `durable_storage_status`, `platform` probes | feed `SystemResourceSample` |
| **subscribe/** | nothing (CONTRAST only, §1.1) | strictly separate stack |
| **S21** (scheduled recording) | `source-fail` / `disk-full` conditions on a scheduled `RecordingJob` (via `AlertSinkProtocol` in `civiccast/recording/service.py`) | warning/critical alert; **the seam:** S21's `RecordingService` calls into S8 through `AlertSinkProtocol` (alert(kind, resource, summary, detail) → None); S8 owns rule match + dedupe + delivery. S21 unit tests inject a stub sink; the production implementation is `record_alert_condition` (§4.5). |

S8 **depends on** the egress state/health machinery (`egress/health.py`, `egress/models.py`, the daemon health loop) and the installer update/backup/health machinery (`installer/service.py`). It **provides** `record_alert_condition` to every section above and the runtime safe-to-air read to the operator console.

### 10.2 Open decisions for Scott

- **OD-1 — SMS provider.** Twilio-shaped adapter as the reference (operator supplies creds), with the adapter pluggable for a carrier email-to-SMS gateway fallback? *Recommend:* ship the Twilio-shaped HTTP adapter + document the email-to-SMS fallback (zero extra cost path), default channel = whichever the operator configures.
- **OD-2 — Quiet hours vs critical.** Confirm `critical` (off-air/encoder-death/server-crash/relay-blocked/missing-media) **always** ignores quiet hours and only `warning`/`info` are held. *Recommend:* yes — an off-air box at 3am must page.
- **OD-3 — Runtime safe-to-air cache TTL.** 3s vs 5s server-side cache for the 1Hz dashboard poll. *Recommend:* 5s (the egress health loop already samples on that order; avoids store stampede).
- **OD-4 / OD-5 — Self-test times.** Daily 02:00 local, weekly Sun 03:00 local — confirm, and confirm both are operator-overridable. *Recommend:* yes, defaults as stated, override in Alert/Health settings.
- **OD-6 — Auto-install.** Keep update **install** manual (maintenance-window gated), never auto-applied? *Recommend:* yes — a station must never reboot itself off-air unattended; only the **check** is automatic.
- **OD-7 — Webhook-signing shared module.** Physically share `subscribe/webhook.py`'s HMAC signer with S8, or copy it to keep the operator/resident stacks fully decoupled? *Recommend:* extract a tiny `civiccast/common/webhook_sign.py` used by both, with separate secret stores — shares the crypto, not the routing.
- **OD-8 — Acknowledge expiry.** Does an acknowledged-but-still-firing alert re-page after `re_alert_after`, or stay silent until resolve? *Recommend:* re-page for `critical` (an ack is not a fix), stay silent for `warning`/`info`.
- **OD-9 — Resource conditions in the enum.** The §6.6 resource breaches (disk-low, clock-skew, db-down, service-down) currently map onto `server-crash`/ad-hoc; should they become first-class `AlertConditionKind` members in `0042`, or stay derived? *Recommend:* add `disk-low`, `clock-skew`, `db-unreachable`, `service-down` as explicit kinds in `0042` so the rule table is self-documenting — flag the small scope add for approval.

---

## 11. Build order note (within S8, after S9 ENG-003 and SDI-proof per master §10 step 3)

1. **Week 1:** `0042` schema + entities + stores; QA-004 fix + regression test (it is the load-bearing correctness item and unblocks honest sink health for every other section).
2. **Week 2:** evaluator (dedupe/rate-limit/resolve) + the §6.2 seed + `record_alert_condition`; wire S8's own off-air/encoder-death/server-crash derivation.
3. **Week 3:** email/SMS/webhook delivery + retry/dead-letter + quiet hours + the test-send button; runtime safe-to-air + channel runtime cards.
4. **Week 4:** system-resource sampler, daily/weekly self-test, update check + release-notes wiring, support-bundle extension; consume S2/S4/S5/S7/S9/S13 conditions as those sections land their `record_alert_condition` calls.
5. **Soak:** extend the 24h soak to induce failures and assert fire-once/dedupe/resolve/no-QA-004-false-flag → rung 2; **0/0/0/0/0** audit.
