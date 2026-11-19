# S9 — Reliability and Process Identity: Unattended-Survival Spine

> **Scope:** GStreamer **pipeline lifecycle supervision** (watchdog, bus-error handling, clean restart to a known state), the process-identity primitive applied to **optional co-processes** (CasparCG, OBS, VDO.Ninja, the NDI runtime, any relay) so a dead one is detected/reaped and doesn't hold a device, uniform pacing latches, schema-currency health surface, and proof-event retention caps that gate unattended operation.
>
> **Engine alignment (2026-06-13):** The playout/compositor/output engine is now a **persistent GStreamer pipeline** (see S15), not the per-segment ffmpeg-relay. This **largely dissolves** the historical per-segment teardown/orphan-reap class (ENG-001/003/009, #151): the engine no longer spawns/kills ffmpeg per segment, so the mux stays in PLAYING and the orphaned-encoder-on-segment-boundary failure mode cannot recur by design. Reliability work re-targets to (a) supervising the persistent pipeline and (b) supervising the **optional** co-processes that *do* still spawn as separate OS processes and *can* hold hardware (the CasparCG/SDI co-process holding a DeckLink card is the surviving instance of the device-lock concern).
>
> **Status: BUILT — master §10 step 3, code-complete + machine-verified (2026-06-14, branch `work/3.0-gstreamer-engine`).** Shipped S9-1…S9-6 (`90deaf3..0b04153`): `UniformPacingLatch` + TOCTOU-safe `verify_and_kill_process`; schema-currency surface + proof-event churn cap (10k/channel) + migration `0038_reliability_fields`; boot co-process reap → durable proof event (IP-only rescope — durable co-process pid tracking deferred to step 7/CasparCG); engine **output-stall watchdog** (quits on a silent output flatline → daemon relaunch on the committed source); **latch-gated crash-relaunch back-off** + the **S8 escalation hook seam**; **schema-drift health badge** + NDI readiness-probe TTL cache. Verified: egress suite 443 passed; WSL live-engine harness 11 passed. Report: `Desktop\Code\civiccast-stage3-s9-report.md`. The build-spec text below is retained for reference; code references ground all "what exists" claims.

---

## 1. Goal & PEG automation rationale

Unattended cable operation requires the software to:
- **Supervise the persistent GStreamer pipeline.** The engine (S15) is one persistent pipeline per channel whose output half stays in PLAYING continuously while sources hot-swap upstream. Reliability here means a **watchdog on that pipeline** (detect a stalled/EOS'd output, an element that errored on the GStreamer **bus**, a posted `GST_MESSAGE_ERROR`), and a **clean restart to a known state** when an element fails — re-establishing the pipeline (and its `interpipesrc`/`interpipesink` topology) without leaving the channel dark. This replaces the old "reap the ffmpeg the daemon spawned for this segment" model entirely, because there is no per-segment ffmpeg.
- **Survive restarts cleanly — for the optional co-processes.** GStreamer runs in-process (PyGObject, S15 §7), so there is no separate engine process to orphan. But the **optional co-processes** still spawn as separate OS processes and **can hold hardware**: after a kill -9 or power loss, the next boot must detect and reap any co-process the dead server left behind — especially the **CasparCG/SDI co-process holding a DeckLink card** (S15 §5/§8 premium-CG tier) or the NDI runtime holding an NDI name — before starting fresh. Today: encoder orphans get reaped (daemon.py:581–586); the relay scan exists (automation.py:352–385) but is never wired on boot, and co-process identities are tracked in-memory only, so after an unclean restart the dead co-process holds the device and the new one backoff-loops 5/15/60 seconds forever (the surviving form of ENG-003).
- **Never kill the wrong process.** PID reuse is a TOCTOU race: probe says "pid 1234 is the CasparCG co-process," by the time we kill it, pid 1234 may be running sshd (ENG-001). Today: mitigated via `created_at` timestamp in `OrphanInfo` (daemon.py:69–79) — we re-verify create time at kill time (daemon.py:800–803). The same primitive now applies to co-processes.
- **Pace starts and reloads.** Transient failures (network hiccup, pipeline-element crash) retry immediately; persistent failures (missing media, bad feed) go into a 30s cooldown to avoid churn and obvious flapping (automation.py:131–134 + 318–317 for starts/reloads, ENG-002). No uniform latch for co-process probing or pipeline-readiness checks yet.
- **Report live schema-currency.** An operator watching /health must see whether the running code matches the persisted schema version. Today: no visibility; a skew can silently corrupt data.
- **Cap proof-event churn.** A broken source makes a churn loop write ~900 proof events/hr; unbounded growth eats disk and makes the proof log unreadable (ENG-007). No retention policy yet.

**PEG automation coverage:** the incumbent PEG platform is appliance hardware; it does not face these software-restart and process-lifetime problems. We build them because we're software on a general-purpose PC. The "parity" here is *reliability* — make the box call for help, not vanish.

---

## 2. Current state (file:line references)

| Capability | Where | Status | Gap |
|---|---|---|---|
| **Process-identity primitive** | daemon.py:69–79 (`OrphanInfo`) | Implemented; probe + term both use it | Reusable as-is for co-processes |
| **GStreamer pipeline watchdog** | (engine is net-new — S15) | Not yet built | **NET-NEW: bus-error/`GST_MESSAGE_ERROR` handler + stall watchdog + clean restart to known state on element failure** |
| Encoder orphan reap on boot | daemon.py:569–609 (`_check_prior_encoder`) | Implemented for the legacy ffmpeg-relay; **largely moot** under the GStreamer engine (no per-segment ffmpeg) | Retain as the pattern; re-point at co-processes |
| Process-identity reap contract | daemon.py:775–813 (`_default_orphan_probe` / `_terminator`) | Implemented | Reuse for co-process reap |
| **Co-process orphan reap** | automation.py:352–385 (`reap_predecessor_relays`) | **Coded but never called on boot** | **LOAD-BEARING: must wire on startup; now scopes to the optional co-processes (CasparCG/SDI, NDI runtime, OBS, VDO.Ninja, any relay)** |
| Co-process identity (in-memory) | sdi_relay.py:90–100, ndi_relay.py:71–82 | Runtime-only dicts (`_RELAY_STATUSES`); no durability | **NET-NEW: persist co-process pids + create_time to state** |
| **Start retry cooldown** | automation.py:131 (`_start_retry_at`) | Implemented with latch | Works as-is |
| **Reload pacing cooldown** | automation.py:134 (`_replan_retry_at`) | Implemented with latch | Works as-is |
| Co-process / pipeline readiness probing | (no central place yet) | Distributed; SDI readiness is pull-only (sdi_relay.py:79–88) | **NET-NEW: uniform latch for readiness checks (pipeline + co-process)** |
| **Schema-currency on /health** | router.py:607–620 (`get_recent_health`) | Returns `EgressHealthSample` (360–373); no schema field | **NET-NEW: add schema_version to health sample & check endpoint** |
| **Proof-event retention** | store.py:132–147 (`append_proof_event` in-memory; 364–375 Postgres) | Unbounded append; no trim policy | **NET-NEW: cap per-channel proof events at ~10k; trim old ones on append** |
| Proof-event rate visibility | daemon.py:593–609 (appends proof events) | Events have `observed_at`; stored in `EgressProofEvent` (models.py:376–389) | **NET-NEW: add rate metrics to health** |

---

## 3. Entities / data model & migrations

### 3.1 Extend `EgressStateRow` (daemon.py reuse)

The state row already persists encoder pid. **Extend it to include optional-co-process identities** (per cable-sprint audit watchlist comment in master §4). Under the GStreamer engine (S15) the engine itself runs in-process (no engine pid to track here beyond the daemon's own), so the durable identities are for the **optional co-processes** that hold devices — the CasparCG/SDI co-process and the NDI runtime being the two that lock hardware. The fields are named generically (`coproc_*`) so OBS/VDO.Ninja/any relay can reuse them; `kind` distinguishes them:

```python
class EgressStateRow(BaseModel):
    """Last-known daemon state for one channel."""
    channel_id: str
    state: EgressState
    current_source_label: str | None = None
    current_proof_event_id: str | None = None
    updated_at: datetime
    pid: int | None = None  # encoder pid (legacy ffmpeg-relay; unused under the GStreamer engine)
    last_error: str | None = None
    
    # NET-NEW (S9): durable identity of an optional device-holding co-process (S15 §5/§8)
    sdi_coproc_pid: int | None = None  # pid of the CasparCG/SDI co-process holding the DeckLink card (if running)
    sdi_coproc_created_at: float | None = None  # process.create_time() at spawn
    ndi_coproc_pid: int | None = None  # pid of the NDI-runtime co-process holding the NDI name (if running)
    ndi_coproc_created_at: float | None = None  # process.create_time() at spawn
```

**Migration:** `0038_reliability_fields` (S9-owned, the as-built number). Alembic is a **single global chain** — one head; 3.0 migrations take a single monotonic sequence and this is **not** a per-package migration. This step adds four nullable integer/float columns to the `egress_state_rows` table. Existing rows default to NULL. Idempotent schema update. (See §3.2 — it also touches `egress_health_samples`; S8's alerting migration follows as `0039`.)

### 3.2 Extend `EgressHealthSample` (models.py reuse)

Add schema-currency and proof-event rate visibility:

```python
class EgressHealthSample(BaseModel):
    """Periodic health sample for operator System Health and proof review."""
    channel_id: str
    sampled_at: datetime
    state: EgressState
    sink_connected: dict[str, bool] = Field(default_factory=dict)
    encoder_fps: float | None = None
    encoder_bitrate_kbps: float | None = None
    dropped_frames: int = 0
    seconds_on_air: int = 0
    last_loudness_lufs: float | None = None
    caption_status: CaptionStatus = "not-verified"
    
    # NET-NEW (S9): schema and proof churn visibility
    schema_version: int  # Semver patch bumped on breaking entity changes
    proof_events_appended_since_last_sample: int = 0  # count, for rate/churn detection
```

**Migration:** `0043_reliability_fields` (same S9-owned migration as §3.1, single global chain). Adds two columns to `egress_health_samples` (`schema_version`, `proof_events_appended`). **Co-edit note:** S8's `0042_alerting_and_sinkhealth` also touches `egress_health_samples` (QA-004 `sink_connected`/`egress_state`), so `0043` is sequenced **after** `0042` in the global chain. Existing rows: `schema_version = 1` (sentinel); `proof_events_appended = 0`.

### 3.3 NET-NEW: `CoprocessIdentity` helper (egress/coprocess_identity.py)

Unify optional-co-process identity management (mirrors `OrphanInfo`). This applies to the device-holding co-processes (CasparCG/SDI, NDI runtime) and any other optional spawned process (OBS, VDO.Ninja, a legacy relay), **not** to the GStreamer engine itself (which is in-process):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CoprocessIdentity:
    """Identity of an optional co-process for TOCTOU-safe reaping.
    
    Same pattern as daemon.OrphanInfo: created_at must re-verify at kill time
    to guard against pid reuse. Used for co-processes that can hold a device
    (CasparCG/SDI → DeckLink card; NDI runtime → NDI name).
    """
    pid: int
    kind: Literal["sdi", "ndi", "obs", "vdoninja", "relay"]  # which co-process type
    created_at: float  # process.create_time() from probe
    name: str  # process.name() for validation
```

### 3.4 NET-NEW: Per-channel proof-event trim policy

In-memory and Postgres stores: keep the most recent **10,000 proof events per channel**. When appending would exceed the limit, trim oldest 1,000 before the new one goes in. This is GC-style; no explicit operator action.

---

## 4. API surface

### 4.0 GStreamer pipeline supervision (NET-NEW — engine watchdog)

The persistent pipeline (S15 §3) is the primary thing to keep alive. The supervisor attaches to the pipeline's **bus** and runs a stall watchdog:

1. **Bus-error handling:** subscribe to `GST_MESSAGE_ERROR` / `GST_MESSAGE_WARNING` / `GST_MESSAGE_EOS` on the output pipeline's bus (via PyGObject; callbacks marshalled back with `GLib.idle_add()` per S15 §7). A posted error from any element (encoder, mux, sink, `interpipesrc`) records the failing element + `GError`, appends a proof event (`proof_boundary="civiccast-egress-pipeline-lifecycle"`), and triggers a clean restart.
2. **Stall watchdog:** track output progress (running-time / buffers at the sink). If it does not advance for `PIPELINE_STALL_TIMEOUT` (default 10s) while the channel is supposed to be on-air, treat it as a stall and restart.
3. **Clean restart to a known state:** set the pipeline to `GST_STATE_NULL`, rebuild the `interpipesrc`/`interpipesink` topology, return to `PLAYING` with the last-committed source via `listen-to` (S15 §3). The restart is **latch-gated** (§6.3) so a flapping element cannot churn. On repeated restarts, escalate to S8 alerting (off-air/pipeline-down rule).

   No per-segment ffmpeg is spawned or killed in any of this — the restart re-establishes the in-process pipeline, it does not reap an OS process.

### 4.1 Co-process reap on boot (automation.py wiring)

**Before:** `reap_predecessor_relays(boot_epoch=time.time())` is called but hidden in a try/except block (automation.py:490). Co-process pids are found but never persisted.

**After:**
1. `reap_predecessor_relays()` stays as-is; returns `list[int]` of reaped pids (now scoped to the optional device-holding co-processes — CasparCG/SDI, NDI runtime — and any other spawned co-process).
2. On boot, before starting fresh co-processes, iterate the reaped pids and **log** them with proof events (one event per reaped co-process):
   ```python
   for pid in reaped_coproc_pids:
       store.append_proof_event(EgressProofEvent(
           event_id=f"egress-coproc-reap-sdi-{pid}",  # or "ndi"
           channel_id=channel_id,
           state="STARTING",
           source_label="(co-process reap)",
           source_path=f"coproc-pid-{pid}",
           proof_boundary="civiccast-egress-coprocess-lifecycle",
           machine_summary=f"Reaped predecessor CasparCG/SDI co-process (pid {pid}) before startup; DeckLink card released."
       ))
   ```

### 4.2 Co-process pid durability (daemon / supervisor lifecycle)

When a device-holding co-process starts (e.g. the CasparCG/SDI co-process, or the NDI runtime):
```python
# In the co-process supervisor (sdi/ndi co-process launcher)
def _spawn_coprocess(...) -> CoprocessResult:
    # ... start the co-process subprocess (CasparCG, NDI runtime, etc.) ...
    process = subprocess.Popen([...])
    coproc_identity = CoprocessIdentity(
        pid=process.pid,
        kind="sdi",  # or "ndi", "obs", ...
        created_at=psutil.Process(process.pid).create_time(),
        name=process_name,  # e.g. "casparcg", "ndi-runtime"
    )
    # Persist to state row
    state = store.read_state(channel_id)
    state.sdi_coproc_pid = coproc_identity.pid
    state.sdi_coproc_created_at = coproc_identity.created_at
    store.write_state(state)
    return ...
```

When a co-process stops (normal or crash):
```python
state.sdi_coproc_pid = None
state.sdi_coproc_created_at = None
store.write_state(state)
```

### 4.3 Readiness latch (uniform pacing — pipeline + co-process)

**NET-NEW:** A uniform `readiness_latch` dict in the automation loop, keyed by channel_id. It paces the pipeline-health poll (bus drain / stall check) and the optional-co-process readiness probes:

```python
class ChannelAutomation:
    def __init__(self, ...):
        # ... existing ...
        # Audit ENG-002: probe readiness (pipeline health, SDI/NDI co-process, headend) at most every 30s per channel
        self._readiness_probe_at: dict[str, float] = {}
    
    def run_pass(self, ...):
        # Before calling pipeline-health, _check_coprocess_readiness, or headend readiness:
        now = time.time()
        if now < self._readiness_probe_at.get(channel_id, 0):
            return  # Still in cooldown
        self._readiness_probe_at[channel_id] = now + 30.0
        # ... do the probes ...
```

Same latch pattern already used for starts (automation.py:131) and reloads (automation.py:134). Reuse that class/pattern for readiness. (The pipeline bus-error handler in §4.0 is event-driven, not polled; the latch here gates the *active* health poll and the co-process probes only.)

### 4.4 Schema-currency on `/health` endpoint

**Before:** `get_recent_health()` (router.py:607–620) returns raw samples.

**After:**
1. Build a module `egress/schema_currency.py`:
   ```python
   EGRESS_SCHEMA_VERSION = 1  # Bumped on breaking entity changes
   
   def current_schema_version() -> int:
       return EGRESS_SCHEMA_VERSION
   
   def is_schema_current(sample: EgressHealthSample) -> bool:
       return sample.schema_version == current_schema_version()
   ```

2. When appending health in daemon._append_health:
   ```python
   sample = EgressHealthSample(
       ...,
       schema_version=current_schema_version(),
       proof_events_appended_since_last_sample=...,
   )
   store.append_health(sample)
   ```

3. On the operator's SystemHealthScreen (S8 ties to it), show a warning badge if any channel's latest health has `schema_version != EGRESS_SCHEMA_VERSION`.

---

## 5. Operator UI surface

The operator UI integrates schema-currency visibility (phone-first, consistent with master §1 appliance posture):

### 5.1 SystemHealthScreen amendment (ChannelOpsScreen / FacilityRouterScreen)

**Add:** A schema-currency badge on the top of the health card:
- 🟢 **Schema OK** if `latest_health.schema_version == current_schema_version()`.
- 🔴 **Schema Drift!** (red box) if they differ, with text "Please restart the egress daemon to reload schema." Operator taps "Learn More" → short doc on what this means.

**Add:** A "Proof churn rate" line showing `latest_health.proof_events_appended_since_last_sample` (e.g., "23 proof events since last sample"). If > 100 in a 60s sample, flash a warning ("High proof churn; investigate the source").

### 5.2 Health chart drill-down (staff API + console)

When the operator taps "/channels/{channel_id}/health" in the console:
- Return the 20 most recent health samples (router.py already does this).
- Include schema_version and proof_events fields in each.
- Graph proof_events over time to detect churn loops.

---

## 6. Behavior / algorithms

### 6.1 Pipeline supervision (the primary reliability loop)

**Sequence (engine, in-process — no OS process to reap):**

1. **Attach to the bus** when the pipeline goes to PLAYING: register handlers for `GST_MESSAGE_ERROR`, `GST_MESSAGE_WARNING`, `GST_MESSAGE_EOS`. Callbacks marshal back via `GLib.idle_add()` (S15 §7).
2. **On a posted error/unexpected EOS:** record the failing element + `GError`, append an `EgressProofEvent` (`proof_boundary="civiccast-egress-pipeline-lifecycle"`, state="STOPPED"), and request a clean restart (latch-gated, §6.3).
3. **Stall watchdog (latch-gated poll, §4.3):** if output running-time has not advanced for `PIPELINE_STALL_TIMEOUT` (default 10s) while on-air, treat as a stall → clean restart.
4. **Clean restart to a known state:** `→ NULL`, rebuild the `interpipesrc`/`interpipesink` topology, `→ PLAYING` with the last-committed source via `listen-to`. On repeated restarts within a window, escalate to S8 (off-air/pipeline-down alert). The mux/PCR continuity guarantee that *prevents* #151 lives in the engine (S15 §3); S9 only supervises and recovers the pipeline.

### 6.2 Optional co-process lifecycle (durable pid tracking)

Applies to the device-holding co-processes (CasparCG/SDI, NDI runtime) and any other optional spawned co-process. **Sequence:**

1. **Boot**: Automation loop calls `reap_predecessor_relays(boot_epoch=...)` (now scoped to co-processes). For each reaped co-process:
   - Append an `EgressProofEvent` with `proof_boundary="civiccast-egress-coprocess-lifecycle"`.
   - Scan the config to find which channel was trying to use it; update that state row to clear the stale pid/created_at (freeing the DeckLink card / NDI name for the new co-process).

2. **Co-process spawn** (sdi/ndi co-process launcher):
   - Start the subprocess.
   - Capture its pid and `psutil.Process(pid).create_time()`.
   - Write to `EgressStateRow.sdi_coproc_pid` + `sdi_coproc_created_at`.
   - **Also** maintain the in-memory status dict (existing; `_RELAY_STATUSES`, sdi_relay.py 90–100) for live status; sync them.

3. **Co-process polling** (automation loop, `_check_coprocess_readiness`):
   - Every 30s per channel (latch-gated).
   - If the co-process is running, check health (readiness probe, restart count, etc.).
   - If it has crashed: log warning, append proof event (state="STOPPED"), clear the state-row pid/created_at, schedule a restart.

4. **Co-process stop/cleanup** (graceful or forced):
   - Clear the state-row pid/created_at.
   - Clear the in-memory status dict.

5. **Unclean restart detection**:
   - On the next boot, if `EgressStateRow.sdi_coproc_pid` is non-null and a live process with that pid exists, re-verify its `create_time()` matches `sdi_coproc_created_at` (TOCTOU guard).
   - If they match and the process is still the expected co-process (or is still running, regardless), reap it and log as a predecessor co-process orphan.
   - If create_time differs by > 1 second, skip (it's a recycled pid, not ours).

### 6.3 Process-identity TOCTOU guard (uniform across any spawned co-process)

Extract `_verify_and_kill_process(pid, created_at, tolerance_seconds=1.0)` as a shared utility:

```python
def _verify_and_kill_process(
    pid: int, 
    created_at: float, 
    tolerance_seconds: float = 1.0
) -> bool:
    """Kill a process only if its create_time matches the recorded one.
    
    Returns True if killed, False if not (pid missing, recycled, or access denied).
    On access denied, logs a warning and returns False (best effort).
    """
    import psutil
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - created_at) > tolerance_seconds:
            # PID was recycled; it's not ours.
            return False
        process.terminate()
        process.wait(timeout=10)
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.TimeoutExpired:
        process.kill()
        return True
    except psutil.AccessDenied:
        _LOG.warning(f"Access denied reaping pid {pid}; if it is the CasparCG/SDI co-process, the DeckLink card may remain locked.")
        return False
```

Used by both `_default_orphan_terminator` (daemon.py) and the co-process terminator (automation.py or the co-process module).

### 6.4 Uniform pacing latch

Centralize in a helper class (egress/pacing.py):

```python
class UniformPacingLatch:
    """Cooldown latch for operations that must not churn.
    
    Each operation (key) has a next-allowed time. Query returns whether
    the operation should run now; if yes, updates the next-allowed time.
    """
    def __init__(self, default_cooldown_seconds: float = 30.0):
        self._next_allowed: dict[str, float] = {}
        self._default_cooldown = default_cooldown_seconds
    
    def should_run_now(self, key: str, now: float | None = None) -> bool:
        """Return True if the operation may run; also advances the latch."""
        now = now or time.time()
        if now >= self._next_allowed.get(key, 0):
            self._next_allowed[key] = now + self._default_cooldown
            return True
        return False
    
    def force_reset(self, key: str) -> None:
        """Clear the cooldown (used after success to allow immediate retry)."""
        self._next_allowed.pop(key, None)
```

Used for:
- Start retries (existing, migrated from `_start_retry_at`).
- Reload pacing (existing, migrated from `_replan_retry_at`).
- **Pipeline restart** (new — gates the clean-restart loop in §6.1 so a flapping element cannot churn).
- Pipeline-health / co-process readiness probes (new).
- Headend readiness probes (new, part of S2 integration).

### 6.5 Proof-event retention and rate capping

In both `InMemoryEgressStore` and `PostgresEgressStore`:

```python
MAX_PROOF_EVENTS_PER_CHANNEL = 10_000
TRIM_BATCH_SIZE = 1_000

def append_proof_event(self, event: EgressProofEvent) -> None:
    # Append (duplicate check already exists).
    # Then trim if needed.
    self._do_append_proof_event_impl(event)
    
    # Count per-channel; trim if over limit.
    channel_count = self._count_proof_events_for_channel(event.channel_id)
    if channel_count > MAX_PROOF_EVENTS_PER_CHANNEL:
        self._trim_oldest_proof_events_for_channel(
            event.channel_id, 
            delete_count=TRIM_BATCH_SIZE
        )
```

**Rationale:** Proof events are audit/debug logs, not operational data. A 10k limit per channel is ~10 hours of churn-loop activity at 900 events/hr, providing forensic window without unbounded growth.

### 6.6 Health sample schema-currency tracking

In daemon._append_health (daemon.py ~440–460):

```python
def _append_health(self, channel_id: str, state: str, **kwargs) -> None:
    # Count new proof events since last health sample.
    last_health = self._store.recent_health(channel_id, 1)
    last_count = last_health[0].proof_events_appended_since_last_sample if last_health else 0
    new_count = self._store.count_proof_events_since(
        channel_id, 
        since_time=last_health[0].sampled_at if last_health else None
    )
    
    sample = EgressHealthSample(
        channel_id=channel_id,
        sampled_at=datetime.now(UTC),
        state=state,
        sink_connected=kwargs.get("sink_connected", {}),
        ...,
        schema_version=current_schema_version(),
        proof_events_appended_since_last_sample=new_count,
    )
    self._store.append_health(sample)
```

---

## 7. Proof tier: current rung + how to advance it

**Current rung: CONTRACT (unit/API tests only; no runtime egress).**

**What exists:**
- `OrphanInfo` + `_default_orphan_probe/_terminator` (daemon.py): tested, used in the legacy encoder reap; TOCTOU guard is verified. Reusable as-is for co-processes.
- `reap_predecessor_relays()` (automation.py): scanned and callable, but not wired on boot yet.
- Start/reload pacing latches (automation.py): proven in 24h soak.
- Schema version concept: trivial; no external dependencies.
- Proof-event trimming: no runtime dependencies; pure data structure.

**What is net-new (S9):**
1. **GStreamer pipeline supervision** — bus-error handler + stall watchdog + clean-restart-to-known-state, latch-gated. Depends on the S15 engine existing.
2. Co-process pid durability in `EgressStateRow` — schema migration + write logic.
3. Co-process reap wiring on boot — calling `reap_predecessor_relays()` in automation startup.
4. Pipeline/co-process readiness latch — new polling loop integration.
5. Schema-currency in health samples — two columns, one module, router amendment.
6. Proof-event trimming logic — store backend change.

**Advance to MACHINE (rung 2):**
- All unit tests for trim logic, pacing latch, schema-currency field passing.
- **Pipeline-supervision test:** force an element error on the persistent pipeline (e.g. kill a sink's downstream); confirm the bus handler fires, a proof event is logged, and the pipeline restarts to PLAYING with the committed source.
- Integration test: boot a channel with a state-row that has a stale co-process pid; confirm it gets reaped and a proof event is logged.
- **24/72-hour soak** (ongoing — this is also the #151 re-test, coordinated with S15's persistent-pipeline soak):
  - Inject pipeline element failures on a running channel; verify clean restart each time, channel never goes dark longer than the restart window, and TS continuity (PCR/CC) is unbroken (the #151 guarantee comes from S15's persistent mux).
  - With a CasparCG/SDI co-process enabled, kill and restart the egress service; verify no orphan co-process is left holding the DeckLink card.
  - Verify proof-event count stays bounded (~10k max per channel).
  - Verify health samples include schema_version and proof_events_appended.
  - Verify no TOCTOU-induced false kills (i.e., a pid is recycled to sshd; we don't kill it).

**Advance to SDI-PROVEN (rung 3):**
- Run soak with the CasparCG/SDI co-process (DeckLink card) enabled; kill the co-process and let reap detect + cleanup; verify the card is available to the new co-process on restart. (Base-tier SDI is a GStreamer `decklinkvideosink` inside the engine, S15 §4 — its failure recovers via pipeline restart, not co-process reap; only the *optional premium CasparCG/SDI* path is a separate device-holding process.)

**Honest boundary:** This spec does not claim to prevent *all* failures. The persistent GStreamer pipeline dissolves the per-segment-teardown orphan class by design (S15), and S9 recovers a failed pipeline via supervised restart. For the optional co-processes it detects and reaps those whose pid predates the current boot epoch; a co-process can still crash after boot (not an orphan) and is recovered via the polling loop. PID reuse is mitigated but could theoretically race; the 1-second create_time tolerance is conservative but not ironclad (see master §10 gate language).

---

## 8. Test plan (0/0/0/0/0 audit expectation)

### 8.1 Unit tests (egress/test_*)

- `test_orphan_info_creation`: Verify `OrphanInfo` captures name and created_at correctly.
- `test_verify_and_kill_process_matches_create_time`: Mock psutil; verify kill succeeds when create_time is within tolerance, skips when it differs by >1s.
- `test_verify_and_kill_process_recycled_pid`: Pid is reused for a different process; confirm we skip it (don't kill).
- `test_coprocess_identity`: Same as OrphanInfo but for the optional co-process (CasparCG/SDI, NDI runtime).
- `test_pipeline_bus_error_triggers_restart`: Mock the GStreamer bus; post a `GST_MESSAGE_ERROR`; verify the supervisor records the failing element, appends a pipeline-lifecycle proof event, and requests a (latch-gated) restart.
- `test_pipeline_stall_watchdog`: Output running-time does not advance for > `PIPELINE_STALL_TIMEOUT`; verify a restart is requested; advancing time clears it.
- `test_pacing_latch_cooldown`: Start two tasks; first runs, second blocked until latch expires; third runs after reset.
- `test_pacing_latch_force_reset`: After success, latch resets; next task runs immediately.
- `test_pipeline_restart_latch`: Repeated element errors do not churn — restarts are gated by the pacing latch.
- `test_proof_event_trim_per_channel`: Append 12,000 events; verify only 10,000 remain after trim; verify other channels unaffected.
- `test_health_sample_schema_version`: Schema version matches current; different after version bump.
- `test_health_sample_proof_events_count`: Count increments as events are appended.
- `test_coprocess_state_row_durability`: Create a state row with co-process pid/created_at; read back; verify values persist.

### 8.2 API/integration tests (egress/test_integration_*)

- `test_coprocess_reap_on_boot`: Start a mock co-process, record its pid in state, simulate boot, call `reap_predecessor_relays()`, verify it's killed and a proof event is logged.
- `test_coprocess_reap_skips_live_process`: Start a co-process before boot epoch; reap skips it (it's ours).
- `test_pipeline_restart_recovers_committed_source`: Drive a pipeline to PLAYING with a committed source, force an element failure, verify the supervised restart returns to PLAYING on the same `listen-to` source.
- `test_readiness_latch_gates_probes`: Call the readiness check (pipeline-health + co-process) twice in quick succession; second is blocked; after 30s both run.
- `test_schema_currency_on_health_endpoint`: POST a health sample with schema_version; GET /health returns it; verify schema warning logic.
- `test_proof_event_rate_visible_in_health`: Append 50 proof events between samples; health sample shows proof_events_appended_since_last_sample=50.

### 8.3 E2E / soak tests

**Run on the ongoing 24/72-hour soak (master §12):**

1. **Pipeline supervision (the primary loop):**
   - During the soak, inject element failures on the persistent pipeline (kill a downstream sink target, force an encoder error).
   - Verify each time: the bus handler fires, a `civiccast-egress-pipeline-lifecycle` proof event is logged, the pipeline restarts to PLAYING on the committed source, and TSDuck shows TS continuity (PCR/CC) unbroken across the supervised restart (the #151 guarantee from S15's persistent mux).
   - **Blocker if:** the channel goes dark beyond the restart window, the pipeline does not recover, or TS continuity breaks.

2. **Co-process orphan reap (surviving ENG-003 closure):**
   - Start the soak with channels configured for the optional CasparCG/SDI and/or NDI co-process.
   - At hour 12: kill the egress daemon with SIGKILL (unclean restart).
   - Verify the next boot:
     - `reap_predecessor_relays()` is called and logs the reaped co-process pids.
     - Proof events are appended for each reaped co-process.
     - No "backoff 5/15/60" errors in the co-process logs (the DeckLink card / NDI name is available immediately).
   - **Blocker if:** the co-process backoff loop appears in logs (card/name still held).

3. **Proof-event retention:**
   - After 24h soak, query recent_proof_events for a channel; verify count ≤ 10,000.
   - Graph proof_event timestamps; verify no unbounded growth.
   - **Blocker if:** event count exceeds 15,000 (giving 50% headroom).

4. **Schema-currency visibility:**
   - Sample /health every 5 minutes; verify schema_version matches current.
   - Trigger a version bump (update EGRESS_SCHEMA_VERSION); verify next health sample shows new version.
   - **Blocker if:** schema_version is ever missing or mismatched.

5. **Pacing latch (no churn):**
   - Monitor automation loop logs; verify pipeline-health/co-process readiness probes are gated to ~1 per 30s per channel (not every ~2s loop iteration), and that repeated pipeline-element failures do not churn the restart loop.
   - **Blocker if:** readiness probes spam the logs, or pipeline restarts churn faster than the latch.

6. **TOCTOU safety:**
   - Manually spawn a process with pid X; record its create_time; then kill it and spawn a different process with the same pid X.
   - Call `_verify_and_kill_process(X, old_create_time)`; verify it doesn't kill the new process.
   - **Blocker if:** false positive kill.

### 8.4 Audit expectation

**0/0/0/0/0:** No correctness bugs, no lint, no perf issues, no test skips, no missing assertions.

- Correctness: the pipeline is supervised and restarts cleanly to a known state; co-process orphans are reaped; TOCTOU is guarded; proof events are trimmed; health schema is consistent.
- Lint: Code follows CivicCast style; no unused imports; docstrings on public functions.
- Perf: Trim logic is O(1) amortized; pacing latch is O(1); no N² loops.
- Tests: All happy paths and error cases covered; no `.skip()` or `#TODO`.
- Assertions: Health samples always have schema_version; state rows always have consistent pid/created_at pairs or both null.

---

## 9. DONE criteria (shipped state)

The section is "done" and can be marked machine-proven (rung 2) when:

1. **Schema migration is applied:** A single S9-owned migration `0043_reliability_fields` on the global Alembic chain (head `0037` → … → `0042` → `0043`) adds four new columns on `egress_state_rows` (co-process pids/timestamps) and two on `egress_health_samples` (schema_version, proof_events_appended), sequenced after S8's `0042` (co-edit on `egress_health_samples`). Tested on fresh and existing databases.
2. **Pipeline supervision works:** the persistent GStreamer pipeline (S15) has a bus-error handler + stall watchdog; an element failure triggers a clean restart to a known state on the committed source, latch-gated, with a proof event and S8 escalation on repeated restarts. Verified under injected failures with unbroken TS continuity.
3. **Co-process reap is wired on boot:** `reap_predecessor_relays()` is called before co-process startup in the automation startup sequence; proof events are logged for each reaped co-process (DeckLink card / NDI name freed).
4. **Co-process pid durability:** When a device-holding co-process starts or stops, `EgressStateRow` is updated immediately (not async). Tested in isolation and in the soak.
5. **Uniform pacing latch:** `UniformPacingLatch` class is used for start, reload, pipeline restart, and readiness probes. All use the same gates; no separate cooldowns.
6. **Schema-currency on /health:** `EgressHealthSample.schema_version` is populated on every sample; `/health` endpoint includes it; UI shows a warning badge if it differs from current.
7. **Proof-event trimming:** Per-channel limit is 10,000; trim happens on append; no manual operator action. Tested with a burst of events.
8. **0/0/0/0/0 audit:** All unit/API/e2e tests pass; no lint warnings; no perf regressions.
9. **Soak proof:** 72-hour run with pipeline-failure injection + recovery, co-process reap, proof trimming, and schema-currency observed; no blocker results.
10. **Documentation:** Runbook section added for "Pipeline recovery and co-process lockup" with examples of proof events and health diagnostics.

---

## 10. Dependencies & cross-refs; open decisions

### 10.1 Dependencies

- **S15 (Playout Engine — GStreamer):** the engine S9 supervises. The persistent pipeline is what S9's watchdog/bus-error handler/clean-restart loop operates on, and its in-process design (PyGObject, S15 §7) is *why* the per-segment ffmpeg-reap class is dissolved. The optional co-processes S9 reaps are the CasparCG/SDI and NDI-runtime processes defined in S15 §5/§8. S9's pipeline supervision is the realization of S15's test-plan item (4) (recovery / watchdog).
- **S1 (Reference Station):** S9 process-identity primitive is foundational; S1 will reference it for `StationBoxProfile` startup checks (e.g., "Is the egress daemon running cleanly?").
- **S8 (Alerting):** S9 pipeline-down/restart events, proof-event rate, and schema-drift feed into S8 alerting rules (e.g., "Alert on repeated pipeline restarts," "proof_events_appended > 100/sample," "schema_version mismatch").
- **Master §5 (proof ladder):** S9 stays contract-rung until soak data (master §12 gate) is complete; then advances to machine.
- **Master §10 (build order):** S9 is step 1, before SDI-proven (step 2). Unattended operation is load-bearing — and now presupposes the S15 engine exists for the pipeline-supervision half.

### 10.2 Cross-section references

| Section | Reference |
|---|---|
| S15 | The GStreamer engine S9 supervises; source of the optional co-processes S9 reaps; S9 realizes S15 test-plan item (4) (recovery/watchdog). |
| S1 | Reference-station process health checks; StationBoxProfile startup validation. |
| S8 | Alerting rules: pipeline-down/restart, proof-event churn, schema drift, co-process lockup. |
| S10 | Formal proof-ladder mapping; S9 is contract→machine. |
| S11 | EAS alerting may feed proof events; proof retention applies. |
| S12 | OTT app health checks may consume /health endpoint (schema_version). |

### 10.3 Open decisions for Scott

1. **TOCTOU tolerance (create_time matching):** Currently set to 1.0 second (daemon.py:802). On heavily loaded systems, could a legitimate process' create_time drift > 1s between probe and kill? Recommend keeping 1.0s (conservative); escalate to 5.0s only if false positives appear in soak.

2. **Proof-event trim threshold:** 10,000 per channel is a guess based on ~900 events/hr churn. Should we make this configurable in `StationBoxProfile` (e.g., based on disk space)? Recommend fixed for 3.0; make configurable in 3.1 if needed.

3. **Schema-version bumping discipline:** Who decides when to bump `EGRESS_SCHEMA_VERSION`? Recommend: bump on any breaking entity change (new required field, removed field, enum rename). Document in the code module docstring.

4. **Windows Job Objects / POSIX process groups:** Master §4 mentions "Consider Windows Job Objects / POSIX process groups for lifetime coupling." This is a future optimization (rung 3+) to automatically reap optional co-process children (e.g. the CasparCG/SDI co-process) if the parent dies. Defer to 3.1 unless soak reveals uncleaned children. (Less pressing now that the engine is in-process and has no per-segment child to leak.)

---

## 11. Implementation order (build within S9)

1. **Week 1:** Schema migrations, TOCTOU utilities, `UniformPacingLatch` class, unit tests.
2. **Week 2:** GStreamer pipeline supervision (bus-error handler + stall watchdog + clean restart), co-process durability in state rows, co-process reap wiring on boot, pipeline/co-process readiness latch, proof-event trimming.
3. **Week 3:** Schema-currency in health samples, UI warning badge, API tests, soak integration.
4. **Week 4:** Soak runs (pipeline-failure injection + recovery, co-process reap); blockers resolved; 0/0/0/0/0 audit.

---

*Final: Write to C:/CivicCastTester/civiccast/docs/spec/3.0/sections/S9-reliability-and-process-identity.md*

*Author: Claude Code | Date: 2026-06-13*